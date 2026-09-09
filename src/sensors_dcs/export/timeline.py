from __future__ import annotations

import json
import logging
import warnings
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sensors_dcs.export.parquet_io import require_pandas, write_parquet

_LOG = logging.getLogger(__name__)

AlignMode = Literal["asof", "nearest", "grid", "union"]
ExportFormat = Literal["parquet", "csv", "both"]
AlignClock = Literal["wall", "hw_ts"]

# Primary-camera HW coverage below this → fall back to wall (see hw-timeline-align.md).
HW_COVERAGE_MIN = 0.95
# |t_hw_s - t_wall| median above this → unit/coherence failure → fallback.
HW_WALL_INCOHERENT_S = 1.0


@dataclass
class Sample:
    t_wall: float
    t_mono: float
    agent_id: str
    sensor_id: str
    kind: str
    seq: int
    fields: dict[str, Any] = field(default_factory=dict)
    image_relpath: str | None = None
    role: str | None = None
    serial: str | None = None
    dry_run: bool | None = None
    file_missing: bool = False
    t_hw: float | None = None  # normalized hardware time in seconds (cameras only)
    hw_domain: str | None = None
    hw_raw_unit: str | None = None


@dataclass(frozen=True)
class FrameAnchor:
    """Primary-camera frame used as HW grid node (MVP pairs wall↔hw)."""

    seq: int
    t_wall: float
    t_hw: float
    agent_id: str
    file: str | None = None
    hw_domain: str | None = None
    hw_raw_unit: str | None = None


def normalize_hw_timestamp(
    raw: float,
    *,
    t_wall_hint: float | None = None,
) -> tuple[float, str]:
    """Convert device ``color_timestamp`` to seconds.

    Returns ``(t_hw_seconds, raw_unit_detected)`` where unit is
    ``s`` / ``ms`` / ``us`` / ``unknown``.
    """
    raw_f = float(raw)
    if t_wall_hint is None or t_wall_hint == 0:
        # No hint: treat large magnitudes as ms (RealSense baseline pattern).
        if abs(raw_f) >= 1e12:
            return raw_f / 1e6, "us"
        if abs(raw_f) >= 1e10:
            return raw_f / 1e3, "ms"
        return raw_f, "s"

    hint = float(t_wall_hint)

    def _near(a: float, b: float) -> bool:
        if b == 0:
            return abs(a) < 1.0
        ratio = abs(a / b) if b else float("inf")
        return 0.5 <= ratio <= 2.0

    if _near(raw_f, hint):
        return raw_f, "s"
    if _near(raw_f / 1e3, hint):
        return raw_f / 1e3, "ms"
    if _near(raw_f / 1e6, hint):
        return raw_f / 1e6, "us"
    return raw_f, "unknown"


def sample_grid_time(sample: Sample, clock: AlignClock) -> float:
    """Pick the timeline key for one sample under ``align_clock``."""
    if clock == "hw_ts":
        if sample.t_hw is None:
            raise ValueError(f"sample {sample.agent_id}#{sample.seq} has no t_hw")
        return float(sample.t_hw)
    return float(sample.t_wall)


def resolve_episode_dir(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"episode directory not found: {p}")
    if not p.name.startswith("episode_"):
        raise ValueError(f"not an episode directory (expected episode_*): {p}")
    return p


def ensure_episode_exportable(
    manifest: dict[str, Any],
    *,
    allow_invalid: bool = False,
    episode_label: str | None = None,
) -> None:
    """Refuse discarded episodes (``manifest.valid is False``) unless overridden.

    Start-time provisional manifests also have ``valid=false``; export should only
    run after a finished stop. Pass ``allow_invalid=True`` / CLI ``--allow-invalid``
    to force export of 作废 episodes.
    """
    if allow_invalid:
        return
    if manifest.get("valid") is False:
        label = episode_label or str(manifest.get("episode_index", "?"))
        raise ValueError(
            f"episode {label} has manifest.valid=false (作废/provisional); "
            "refusing export. Pass allow_invalid=True / --allow-invalid to override."
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _flatten_state_row(row: dict[str, Any]) -> Sample:
    payload = dict(row.get("payload") or {})
    kind = str(row.get("kind") or "")
    fields: dict[str, Any] = {}
    if kind == "gello" or kind == "arm_read":
        joints = payload.get("joints_rad")
        if joints is not None:
            fields["joints_rad"] = list(joints)
        joints_raw = payload.get("joints_rad_raw")
        if joints_raw is not None:
            fields["joints_rad_raw"] = list(joints_raw)
        if payload.get("joint_offsets") is not None:
            fields["joint_offsets"] = list(payload["joint_offsets"])
        if payload.get("joint_signs") is not None:
            fields["joint_signs"] = list(payload["joint_signs"])
    elif kind == "arm_write":
        # Command joints are the write-agent primary track (distinct agent_id from arm_read).
        joints = payload.get("command_joints_rad")
        if joints is None:
            joints = payload.get("joints_rad")
        if joints is not None:
            fields["joints_rad"] = list(joints)
        fb = payload.get("feedback_joints_rad")
        if fb is not None:
            fields["feedback_joints_rad"] = list(fb)
        fields["armed"] = payload.get("armed")
        fields["last_ok"] = payload.get("last_ok")
        fields["last_error"] = payload.get("last_error")
    elif kind == "gripper_read":
        fields["position_norm"] = payload.get("position_norm")
        raw = payload.get("raw_value")
        if raw is None:
            raw = payload.get("position_raw")
        fields["position_raw"] = raw
        fields["raw_value"] = raw
    elif kind == "gripper_write":
        fields["command_position_norm"] = payload.get("command_position_norm")
        fields["command_position_raw"] = payload.get("command_position_raw")
        fields["last_ok"] = payload.get("last_ok")
        fields["last_error"] = payload.get("last_error")
        fields["initialized"] = payload.get("initialized")
    else:
        fields.update(payload)
    return Sample(
        t_wall=float(row["t_wall"]),
        t_mono=float(row.get("t_mono") or 0.0),
        agent_id=str(row["agent_id"]),
        sensor_id=str(row.get("sensor_id") or ""),
        kind=kind,
        seq=int(row.get("seq") or 0),
        fields=fields,
        dry_run=payload.get("dry_run"),
    )


def _load_states(ep_dir: Path) -> list[Sample]:
    states_dir = ep_dir / "states"
    if not states_dir.is_dir():
        return []
    out: list[Sample] = []
    for path in sorted(states_dir.glob("*.jsonl")):
        for row in _read_jsonl(path):
            out.append(_flatten_state_row(row))
    return out


def _load_cameras(ep_dir: Path) -> list[Sample]:
    cameras_dir = ep_dir / "cameras"
    if not cameras_dir.is_dir():
        return []
    out: list[Sample] = []
    for agent_dir in sorted(p for p in cameras_dir.iterdir() if p.is_dir()):
        index_path = agent_dir / "index.jsonl"
        if not index_path.is_file():
            continue
        agent_id = agent_dir.name
        for row in _read_jsonl(index_path):
            file_name = str(row.get("file") or "")
            rel = f"cameras/{agent_id}/{file_name}" if file_name else None
            missing = bool(rel and not (ep_dir / rel).is_file())
            depth_name = row.get("depth_file")
            depth_name = str(depth_name) if depth_name else None
            if depth_name:
                drel = f"cameras/{agent_id}/{depth_name}"
                if not (ep_dir / drel).is_file():
                    # Keep depth_file name but mark color missing semantics separately;
                    # depth absence does not fail color export.
                    pass
            fields: dict[str, Any] = {"file": file_name}
            if depth_name:
                fields["depth_file"] = depth_name
            t_wall = float(row["t_wall"])
            t_hw: float | None = None
            hw_domain = row.get("color_timestamp_domain")
            hw_domain_s = str(hw_domain) if hw_domain not in (None, "") else None
            hw_raw_unit: str | None = None
            raw_cts = row.get("color_timestamp")
            if raw_cts is not None and raw_cts != "":
                try:
                    t_hw_s, hw_raw_unit = normalize_hw_timestamp(
                        float(raw_cts), t_wall_hint=t_wall
                    )
                    fields["color_timestamp"] = float(raw_cts)
                    if hw_domain_s:
                        fields["color_timestamp_domain"] = hw_domain_s
                    if hw_raw_unit != "unknown":
                        t_hw = t_hw_s
                except (TypeError, ValueError):
                    t_hw = None
                    hw_raw_unit = None
            out.append(
                Sample(
                    t_wall=t_wall,
                    t_mono=float(row.get("t_mono") or 0.0),
                    agent_id=str(row.get("agent_id") or agent_id),
                    sensor_id=str(row.get("sensor_id") or ""),
                    kind=str(row.get("kind") or "realsense"),
                    seq=int(row.get("seq") or 0),
                    fields=fields,
                    image_relpath=rel,
                    role=row.get("role"),
                    serial=str(row["serial"]) if row.get("serial") else None,
                    dry_run=row.get("dry_run"),
                    file_missing=missing,
                    t_hw=t_hw,
                    hw_domain=hw_domain_s,
                    hw_raw_unit=hw_raw_unit,
                )
            )
    return out


def collect_primary_camera_anchors(
    samples: list[Sample],
    *,
    primary_camera: str,
    coverage_min: float = HW_COVERAGE_MIN,
) -> tuple[list[FrameAnchor], dict[str, Any]]:
    """Build HW grid anchors from primary camera; report fallback metadata."""
    cam = [s for s in samples if s.agent_id == primary_camera]
    info: dict[str, Any] = {
        "primary_camera": primary_camera,
        "n_frames": len(cam),
        "hw_coverage": 0.0,
        "hw_raw_unit_detected": None,
        "hw_domains": [],
        "align_fallback": False,
        "align_fallback_reason": None,
        "hw_backsteps": 0,
    }
    if not cam:
        info["align_fallback"] = True
        info["align_fallback_reason"] = "missing_primary_camera"
        return [], info

    with_hw = [s for s in cam if s.t_hw is not None]
    info["hw_coverage"] = len(with_hw) / max(len(cam), 1)
    domains = sorted({s.hw_domain for s in with_hw if s.hw_domain})
    info["hw_domains"] = domains
    units = {s.hw_raw_unit for s in with_hw if s.hw_raw_unit}
    info["hw_raw_unit_detected"] = next(iter(units)) if len(units) == 1 else (
        "mixed" if units else None
    )

    any_raw = any(s.fields.get("color_timestamp") is not None for s in cam)
    if not with_hw:
        info["align_fallback"] = True
        if any_raw:
            # Present but failed unit detection / nulls.
            info["align_fallback_reason"] = "hw_unit_unknown"
        else:
            info["align_fallback_reason"] = "hw_field_absent"
        return [], info
    if info["hw_coverage"] < coverage_min:
        info["align_fallback"] = True
        info["align_fallback_reason"] = "hw_coverage_low"
        return [], info

    deltas = [abs(float(s.t_hw) - s.t_wall) for s in with_hw]
    deltas.sort()
    med = deltas[len(deltas) // 2]
    if med > HW_WALL_INCOHERENT_S:
        info["align_fallback"] = True
        info["align_fallback_reason"] = "hw_wall_incoherent"
        return [], info

    # Sort by HW time; count backsteps on original camera order.
    ordered_wall = sorted(cam, key=lambda s: (s.t_wall, s.seq))
    backsteps = 0
    prev_hw: float | None = None
    for s in ordered_wall:
        if s.t_hw is None:
            continue
        if prev_hw is not None and float(s.t_hw) < prev_hw:
            backsteps += 1
        prev_hw = float(s.t_hw)
    info["hw_backsteps"] = backsteps
    if backsteps / max(len(with_hw), 1) > 0.05:
        info["align_fallback"] = True
        info["align_fallback_reason"] = "hw_non_monotonic"
        return [], info

    # Dedup identical t_hw: keep first seq.
    by_hw = sorted(with_hw, key=lambda s: (float(s.t_hw), s.seq))  # type: ignore[arg-type]
    anchors: list[FrameAnchor] = []
    seen: set[float] = set()
    for s in by_hw:
        th = float(s.t_hw)  # type: ignore[arg-type]
        if th in seen:
            continue
        seen.add(th)
        anchors.append(
            FrameAnchor(
                seq=s.seq,
                t_wall=s.t_wall,
                t_hw=th,
                agent_id=s.agent_id,
                file=str(s.fields.get("file") or "") or None,
                hw_domain=s.hw_domain,
                hw_raw_unit=s.hw_raw_unit,
            )
        )
    if len(domains) > 1:
        # Mixed domains: still usable in MVP; flag in info (no hard fallback).
        info["hw_domain_mixed"] = True
    return anchors, info


def hw_grid_times(anchors: list[FrameAnchor]) -> list[float]:
    """Sorted unique HW seconds for primary-camera grid."""
    return [a.t_hw for a in anchors]


def load_episode(ep_dir: str | Path) -> tuple[dict[str, Any], list[Sample]]:
    """Load manifest and all samples from an episode directory."""
    root = resolve_episode_dir(ep_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.json not found under {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = _load_states(root) + _load_cameras(root)
    samples.sort(key=lambda s: (s.t_wall, s.agent_id, s.seq))
    return manifest, samples


def _t_start(manifest: dict[str, Any], samples: list[Sample]) -> float:
    if manifest.get("t_start") is not None:
        return float(manifest["t_start"])
    if samples:
        return min(s.t_wall for s in samples)
    return 0.0


def _agent_hz(manifest: dict[str, Any], agent_id: str) -> float:
    for item in manifest.get("agents") or []:
        if str(item.get("agent_id")) == agent_id:
            return float(item.get("hz_target") or 0.0)
    return 0.0


def pick_default_master(manifest: dict[str, Any], samples: list[Sample]) -> str:
    agents = {s.agent_id: s.kind for s in samples}
    gello_ids = sorted(aid for aid, kind in agents.items() if kind == "gello")
    if gello_ids:
        return gello_ids[0]
    arm_ids = sorted(aid for aid, kind in agents.items() if kind == "arm_read")
    if arm_ids:
        return arm_ids[0]
    state_ids = sorted(aid for aid, kind in agents.items() if kind != "realsense")
    if state_ids:
        return max(state_ids, key=lambda aid: _agent_hz(manifest, aid))
    if agents:
        return sorted(agents)[0]
    raise ValueError("episode has no samples")


def collect_gello_calib(
    manifest: dict[str, Any], samples: list[Sample]
) -> dict[str, dict[str, Any]]:
    """Per-agent gello affine params from payload and/or episode manifest."""
    out: dict[str, dict[str, Any]] = {}
    for item in manifest.get("agents") or []:
        if item.get("kind") != "gello":
            continue
        aid = str(item.get("agent_id") or "")
        if aid and isinstance(item.get("gello_calib"), dict):
            out[aid] = dict(item["gello_calib"])
    for s in samples:
        if s.kind != "gello" or s.agent_id in out:
            continue
        if s.fields.get("joint_offsets") is None and s.fields.get("joint_signs") is None:
            continue
        out[s.agent_id] = {
            "joint_offsets": list(s.fields.get("joint_offsets") or []),
            "joint_signs": list(s.fields.get("joint_signs") or []),
            "affine": "q=(q_raw-offsets)*signs",
        }
    return out


def _sample_event_row(sample: Sample, t_start: float) -> dict[str, Any]:
    row: dict[str, Any] = {
        "t_wall": sample.t_wall,
        "t_mono": sample.t_mono,
        "t_rel": sample.t_wall - t_start,
        "agent_id": sample.agent_id,
        "kind": sample.kind,
        "seq": sample.seq,
        "sensor_id": sample.sensor_id,
        "image_relpath": sample.image_relpath,
        "role": sample.role,
        "serial": sample.serial,
        "dry_run": sample.dry_run,
        "file_missing": sample.file_missing,
    }
    if sample.kind in {"gello", "arm_read", "arm_write"}:
        for i, val in enumerate(sample.fields.get("joints_rad") or []):
            row[f"j{i}"] = float(val)
        # Raw (pre-affine) joints — gello only; arm_read usually has no separate raw
        for i, val in enumerate(sample.fields.get("joints_rad_raw") or []):
            row[f"j_raw{i}"] = float(val)
        if sample.kind == "arm_write":
            for i, val in enumerate(sample.fields.get("feedback_joints_rad") or []):
                row[f"feedback_j{i}"] = float(val)
            row["armed"] = sample.fields.get("armed")
            row["last_ok"] = sample.fields.get("last_ok")
    elif sample.kind == "gripper_read":
        row["position_norm"] = sample.fields.get("position_norm")
        row["position_raw"] = sample.fields.get("position_raw")
        row["raw_value"] = sample.fields.get("raw_value")
    elif sample.kind == "gripper_write":
        row["command_position_norm"] = sample.fields.get("command_position_norm")
        row["command_position_raw"] = sample.fields.get("command_position_raw")
        row["last_ok"] = sample.fields.get("last_ok")
    elif sample.image_relpath:
        row["file"] = sample.fields.get("file")
        if sample.fields.get("depth_file"):
            row["depth_file"] = sample.fields.get("depth_file")
    return row


def build_events_frame(manifest: dict[str, Any], samples: list[Sample]):
    pd = require_pandas()
    t_start = _t_start(manifest, samples)
    rows = [_sample_event_row(s, t_start) for s in samples]
    if not rows:
        return pd.DataFrame(
            columns=[
                "t_wall",
                "t_mono",
                "t_rel",
                "agent_id",
                "kind",
                "seq",
                "sensor_id",
                "image_relpath",
            ]
        )
    return (
        pd.DataFrame(rows)
        .sort_values(["t_wall", "agent_id", "seq"], kind="mergesort")
        .reset_index(drop=True)
    )


def _agent_value_columns(agent_id: str, sample: Sample) -> dict[str, Any]:
    cols: dict[str, Any] = {f"{agent_id}.seq": sample.seq, f"{agent_id}.t_wall_src": sample.t_wall}
    if sample.kind in {"gello", "arm_read", "arm_write"}:
        for i, val in enumerate(sample.fields.get("joints_rad") or []):
            cols[f"{agent_id}.j{i}"] = float(val)
        for i, val in enumerate(sample.fields.get("joints_rad_raw") or []):
            cols[f"{agent_id}.j_raw{i}"] = float(val)
        if sample.kind == "arm_write":
            for i, val in enumerate(sample.fields.get("feedback_joints_rad") or []):
                cols[f"{agent_id}.feedback_j{i}"] = float(val)
            cols[f"{agent_id}.armed"] = sample.fields.get("armed")
            cols[f"{agent_id}.last_ok"] = sample.fields.get("last_ok")
    elif sample.kind == "gripper_read":
        cols[f"{agent_id}.position_norm"] = sample.fields.get("position_norm")
        cols[f"{agent_id}.position_raw"] = sample.fields.get("position_raw")
        cols[f"{agent_id}.raw_value"] = sample.fields.get("raw_value")
    elif sample.kind == "gripper_write":
        cols[f"{agent_id}.command_position_norm"] = sample.fields.get("command_position_norm")
        cols[f"{agent_id}.command_position_raw"] = sample.fields.get("command_position_raw")
        cols[f"{agent_id}.last_ok"] = sample.fields.get("last_ok")
    else:
        cols[f"{agent_id}.file"] = sample.fields.get("file")
        cols[f"{agent_id}.image_relpath"] = sample.image_relpath
        cols[f"{agent_id}.role"] = sample.role
        cols[f"{agent_id}.serial"] = sample.serial
        cols[f"{agent_id}.file_missing"] = sample.file_missing
        if sample.fields.get("depth_file"):
            cols[f"{agent_id}.depth_file"] = sample.fields.get("depth_file")
    return cols


def _agent_frame(samples: list[Sample], agent_id: str, t_start: float):
    pd = require_pandas()
    rows: list[dict[str, Any]] = []
    for s in samples:
        row = {"t_wall": s.t_wall, "t_rel": s.t_wall - t_start}
        row.update(_agent_value_columns(agent_id, s))
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["t_wall", "t_rel"])
    return (
        pd.DataFrame(rows).sort_values("t_wall", kind="mergesort").reset_index(drop=True)
    )


def _asof_join(base, agent_df, agent_id: str, *, direction: str = "backward"):
    pd = require_pandas()
    if agent_df.empty:
        base[f"{agent_id}.match_dt"] = float("nan")
        return base
    src_col = f"{agent_id}.t_wall_src"
    right = agent_df.sort_values("t_wall").drop(columns=["t_rel"], errors="ignore")
    merged = pd.merge_asof(
        base.sort_values("t_wall"),
        right,
        on="t_wall",
        direction=direction,
    )
    merged[f"{agent_id}.match_dt"] = merged["t_wall"] - merged[src_col]
    return merged.drop(columns=[src_col], errors="ignore")


def _nearest_join(base_times: list[float], agent_df, agent_id: str):
    pd = require_pandas()
    if agent_df.empty:
        return pd.DataFrame({"t_wall": base_times})
    src_times = agent_df["t_wall"].tolist()
    src_col = f"{agent_id}.t_wall_src"
    value_cols = [c for c in agent_df.columns if c not in {"t_wall", "t_rel", src_col}]
    rows: list[dict[str, Any]] = []
    for t in base_times:
        pos = bisect_left(src_times, t)
        candidates: list[int] = []
        if pos < len(src_times):
            candidates.append(pos)
        if pos > 0:
            candidates.append(pos - 1)
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs(src_times[i] - t))
        picked = agent_df.iloc[best]
        row: dict[str, Any] = {"t_wall": t}
        for col in value_cols:
            row[col] = picked[col]
        row[f"{agent_id}.match_dt"] = t - float(picked["t_wall"])
        rows.append(row)
    return pd.DataFrame(rows)


def _build_base_times(
    manifest: dict[str, Any],
    samples: list[Sample],
    *,
    mode: AlignMode,
    master_id: str,
    by_agent: dict[str, list[Sample]],
    t_start: float,
    hz: float | None,
    master_hz: float | None = None,
    align_clock: AlignClock = "wall",
    hw_anchors: list[FrameAnchor] | None = None,
) -> list[float]:
    """Select master timeline timestamps (seconds) for the wide table.

    ``align_clock=hw_ts`` uses primary-camera HW anchors for asof/nearest.
    ``grid`` / ``union`` stay on wall axis (HW grid for those modes is D10+/later).
    """
    if align_clock == "hw_ts" and mode in {"asof", "nearest"} and hw_anchors:
        times = hw_grid_times(hw_anchors)
        if master_hz is not None and master_hz > 0:
            return subsample_times(times, master_hz)
        return times

    if mode == "grid":
        t_end = float(manifest.get("t_end") or max(s.t_wall for s in samples))
        if hz is None or hz <= 0:
            raise ValueError("grid align requires --hz > 0")
        dt = 1.0 / hz
        count = max(1, int((t_end - t_start) / dt) + 1)
        return [t_start + i * dt for i in range(count)]
    if mode == "union":
        return sorted({s.t_wall for s in samples})
    master_samples = by_agent[master_id]
    times = [sample_grid_time(s, "wall") for s in master_samples]
    if master_hz is not None and master_hz > 0:
        return subsample_times(times, master_hz)
    return times


def subsample_times(times: list[float], hz: float) -> list[float]:
    """Pick master timestamps at most `hz`, keeping first sample at/after each grid point."""
    if not times or hz <= 0:
        return times
    dt = 1.0 / hz
    out: list[float] = []
    target = times[0]
    idx = 0
    end = times[-1]
    while target <= end + 1e-9:
        while idx + 1 < len(times) and times[idx + 1] < target + 1e-9:
            idx += 1
        out.append(times[idx])
        target += dt
    return out


def build_aligned_frame(
    manifest: dict[str, Any],
    samples: list[Sample],
    *,
    master: str | None = None,
    mode: AlignMode = "asof",
    hz: float | None = None,
    master_hz: float | None = None,
    align_clock: AlignClock = "wall",
    primary_camera: str = "cam-middle",
):
    """Build wide aligned table (DataFrame). See ``build_aligned_frame_with_meta``."""
    df, _meta = build_aligned_frame_with_meta(
        manifest,
        samples,
        master=master,
        mode=mode,
        hz=hz,
        master_hz=master_hz,
        align_clock=align_clock,
        primary_camera=primary_camera,
    )
    return df


def build_aligned_frame_with_meta(
    manifest: dict[str, Any],
    samples: list[Sample],
    *,
    master: str | None = None,
    mode: AlignMode = "asof",
    hz: float | None = None,
    master_hz: float | None = None,
    align_clock: AlignClock = "wall",
    primary_camera: str = "cam-middle",
) -> tuple[Any, dict[str, Any]]:
    """Build wide aligned table.

    Returns ``(dataframe, clock_meta)``. For ``align_clock=hw_ts`` (asof/nearest),
    the row index follows primary-camera HW order; joins still use each anchor's
    ``t_wall`` (MVP — states have no HW clock).
    """
    pd = require_pandas()
    clock_meta: dict[str, Any] = {
        "align_clock": "wall",
        "primary_camera": None,
        "align_fallback": False,
        "align_fallback_reason": None,
        "hw_unit": "seconds",
        "hw_raw_unit_detected": None,
    }
    if not samples:
        return pd.DataFrame(), clock_meta

    t_start = _t_start(manifest, samples)
    by_agent: dict[str, list[Sample]] = {}
    for s in samples:
        by_agent.setdefault(s.agent_id, []).append(s)

    effective_clock: AlignClock = "wall"
    hw_anchors: list[FrameAnchor] | None = None
    if align_clock == "hw_ts":
        if mode in {"grid", "union"}:
            clock_meta["align_fallback"] = True
            clock_meta["align_fallback_reason"] = "hw_grid_mode_unsupported"
            clock_meta["align_clock"] = "wall"
        else:
            anchors, ainfo = collect_primary_camera_anchors(
                samples, primary_camera=primary_camera
            )
            clock_meta.update(
                {
                    "primary_camera": ainfo.get("primary_camera"),
                    "hw_raw_unit_detected": ainfo.get("hw_raw_unit_detected"),
                    "hw_domains": ainfo.get("hw_domains"),
                    "hw_coverage": ainfo.get("hw_coverage"),
                    "hw_backsteps": ainfo.get("hw_backsteps"),
                }
            )
            if ainfo.get("align_fallback"):
                clock_meta["align_fallback"] = True
                clock_meta["align_fallback_reason"] = ainfo.get("align_fallback_reason")
                clock_meta["align_clock"] = "wall"
            else:
                effective_clock = "hw_ts"
                hw_anchors = anchors
                clock_meta["align_clock"] = "hw_ts"
                clock_meta["align_fallback"] = False
                clock_meta["align_fallback_reason"] = None

        if clock_meta.get("align_fallback"):
            reason = clock_meta.get("align_fallback_reason") or "unknown"
            msg = (
                f"align_clock=hw_ts fell back to wall "
                f"(reason={reason}, primary_camera={primary_camera})"
            )
            _LOG.warning(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)

    if effective_clock == "hw_ts" and hw_anchors is not None:
        # Master is the primary camera; base rows keyed by anchor.t_wall for joins.
        master_id = primary_camera
        if master_id not in by_agent:
            raise ValueError(f"primary camera not found in episode: {master_id}")
        # Optionally downsample HW anchors by master_hz on t_hw axis.
        anchors_use = hw_anchors
        if master_hz is not None and master_hz > 0:
            keep_hw = set(subsample_times(hw_grid_times(hw_anchors), master_hz))
            anchors_use = [a for a in hw_anchors if a.t_hw in keep_hw]
        base = pd.DataFrame(
            {
                "t_wall": [a.t_wall for a in anchors_use],
                "t_hw": [a.t_hw for a in anchors_use],
                "t_rel": [a.t_wall - t_start for a in anchors_use],
                f"{master_id}.seq": [a.seq for a in anchors_use],
            }
        )
        # Attach primary-camera columns by seq (t_wall may collide after HW dedup).
        master_rows: list[dict[str, Any]] = []
        by_seq = {s.seq: s for s in by_agent[master_id]}
        for a in anchors_use:
            s = by_seq.get(a.seq)
            if s is None:
                master_rows.append({f"{master_id}.seq": a.seq})
                continue
            row = _agent_value_columns(master_id, s)
            master_rows.append(row)
        master_df = pd.DataFrame(master_rows)
        base = base.merge(master_df, on=f"{master_id}.seq", how="left")
        base[f"{master_id}.match_dt"] = 0.0
        agent_ids = [aid for aid in sorted(by_agent) if aid != master_id]
        join_direction = "backward" if mode == "asof" else "nearest"
        for aid in agent_ids:
            agent_df = _agent_frame(by_agent[aid], aid, t_start)
            if join_direction == "nearest":
                part = _nearest_join(base["t_wall"].tolist(), agent_df, aid)
                base = base.merge(part, on="t_wall", how="left")
            else:
                base = _asof_join(base, agent_df, aid, direction="backward")
        # Sort by HW grid order for readability.
        return (
            base.sort_values("t_hw", kind="mergesort").reset_index(drop=True),
            clock_meta,
        )

    # ---- wall path (default / fallback) ----
    master_id = master or pick_default_master(manifest, samples)
    if master_id not in by_agent:
        raise ValueError(f"master agent not found in episode: {master_id}")

    base_times = _build_base_times(
        manifest,
        samples,
        mode="asof" if mode == "nearest" else mode,
        master_id=master_id,
        by_agent=by_agent,
        t_start=t_start,
        hz=hz,
        master_hz=master_hz,
        align_clock="wall",
        hw_anchors=None,
    )
    base = pd.DataFrame({"t_wall": base_times, "t_rel": [t - t_start for t in base_times]})

    join_direction = "backward" if mode in {"asof", "grid", "union"} else "nearest"

    if mode in {"asof", "nearest"}:
        master_df = _agent_frame(by_agent[master_id], master_id, t_start)
        keep = [c for c in master_df.columns if c != "t_rel"]
        base = base.merge(master_df[keep], on="t_wall", how="left")
        base[f"{master_id}.match_dt"] = 0.0
        agent_ids = [aid for aid in sorted(by_agent) if aid != master_id]
    else:
        agent_ids = sorted(by_agent)

    for aid in agent_ids:
        agent_df = _agent_frame(by_agent[aid], aid, t_start)
        if join_direction == "nearest":
            part = _nearest_join(base["t_wall"].tolist(), agent_df, aid)
            base = base.merge(part, on="t_wall", how="left")
        else:
            base = _asof_join(base, agent_df, aid, direction="backward")

    return base.sort_values("t_wall", kind="mergesort").reset_index(drop=True), clock_meta


def _write_frame(df, path: Path, fmt: ExportFormat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        write_parquet(df, path, index=False)


def export_episode_timeline(
    ep_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    align: AlignMode | None = None,
    master: str | None = None,
    hz: float | None = None,
    master_hz: float | None = None,
    fmt: ExportFormat = "parquet",
    allow_invalid: bool = False,
    align_clock: AlignClock = "wall",
    primary_camera: str = "cam-middle",
) -> dict[str, Any]:
    """Export long and optional aligned timeline tables for one episode."""
    root = resolve_episode_dir(ep_dir)
    manifest, samples = load_episode(root)
    ensure_episode_exportable(
        manifest, allow_invalid=allow_invalid, episode_label=root.name
    )
    out_dir = Path(output_dir).expanduser().resolve() if output_dir else root / "export"
    out_dir.mkdir(parents=True, exist_ok=True)

    events = build_events_frame(manifest, samples)
    aligned = None
    clock_meta: dict[str, Any] = {
        "align_clock": align_clock if align is not None else "wall",
        "primary_camera": primary_camera if align_clock == "hw_ts" else None,
        "align_fallback": False,
        "align_fallback_reason": None,
        "hw_unit": "seconds",
        "hw_raw_unit_detected": None,
    }
    if align is not None:
        aligned, clock_meta = build_aligned_frame_with_meta(
            manifest,
            samples,
            master=master,
            mode=align,
            hz=hz,
            master_hz=master_hz,
            align_clock=align_clock,
            primary_camera=primary_camera,
        )

    if fmt == "both":
        _write_frame(events, out_dir / "timeline_events.parquet", "parquet")
        _write_frame(events, out_dir / "timeline_events.csv", "csv")
    else:
        ext = "csv" if fmt == "csv" else "parquet"
        _write_frame(events, out_dir / f"timeline_events.{ext}", fmt)

    aligned_rows = 0
    match_stats: dict[str, Any] = {}
    if aligned is not None:
        aligned_rows = len(aligned)
        for col in aligned.columns:
            if col.endswith(".match_dt"):
                series = aligned[col].dropna()
                if len(series):
                    match_stats[col] = {
                        "mean": float(series.mean()),
                        "max": float(series.max()),
                        "missing_rate": float(aligned[col].isna().mean()),
                    }
        if fmt == "both":
            _write_frame(aligned, out_dir / "timeline_aligned.parquet", "parquet")
            _write_frame(aligned, out_dir / "timeline_aligned.csv", "csv")
        else:
            ext = "csv" if fmt == "csv" else "parquet"
            _write_frame(aligned, out_dir / f"timeline_aligned.{ext}", fmt)

    t_start = _t_start(manifest, samples)
    t_end = float(manifest.get("t_end") or max((s.t_wall for s in samples), default=t_start))
    if clock_meta.get("align_clock") == "hw_ts":
        master_id = primary_camera
    else:
        master_id = master or (pick_default_master(manifest, samples) if samples else None)
    meta = {
        "source_episode": root.name,
        "source_path": str(root),
        "export_version": 1,
        "align": None
        if align is None
        else {
            "mode": "asof_backward" if align == "asof" else align,
            "master": master_id,
            "hz": hz,
            "master_hz": master_hz,
        },
        "align_clock": clock_meta.get("align_clock", "wall"),
        "primary_camera": clock_meta.get("primary_camera"),
        "align_fallback": bool(clock_meta.get("align_fallback")),
        "align_fallback_reason": clock_meta.get("align_fallback_reason"),
        "hw_unit": clock_meta.get("hw_unit", "seconds"),
        "hw_raw_unit_detected": clock_meta.get("hw_raw_unit_detected"),
        "hw_coverage": clock_meta.get("hw_coverage"),
        "time_range": {"t_start": t_start, "t_end": t_end},
        "rows": {"events": len(events), "aligned": aligned_rows},
        "agents": sorted({s.agent_id for s in samples}),
        "manifest_written": manifest.get("written"),
        "manifest_dropped": manifest.get("dropped"),
        "match_dt_stats": match_stats or None,
        "gello_calib": collect_gello_calib(manifest, samples) or None,
        "joint_columns": {
            "calibrated": "agent.j{i} / events j{i} = joints_rad after affine (follow/dataset)",
            "raw": "agent.j_raw{i} / events j_raw{i} = joints_rad_raw before affine",
        },
        "outputs": sorted(p.name for p in out_dir.iterdir() if p.is_file()),
    }
    meta_path = out_dir / "export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta["output_dir"] = str(out_dir)
    return meta
