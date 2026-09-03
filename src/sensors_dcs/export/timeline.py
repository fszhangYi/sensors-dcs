from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sensors_dcs.export.parquet_io import require_pandas, write_parquet

AlignMode = Literal["asof", "nearest", "grid", "union"]
ExportFormat = Literal["parquet", "csv", "both"]


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
            out.append(
                Sample(
                    t_wall=float(row["t_wall"]),
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
                )
            )
    return out


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
) -> list[float]:
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
    times = [s.t_wall for s in master_samples]
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
):
    pd = require_pandas()
    if not samples:
        return pd.DataFrame()

    t_start = _t_start(manifest, samples)
    by_agent: dict[str, list[Sample]] = {}
    for s in samples:
        by_agent.setdefault(s.agent_id, []).append(s)

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

    return base.sort_values("t_wall", kind="mergesort").reset_index(drop=True)


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
    if align is not None:
        aligned = build_aligned_frame(
            manifest, samples, master=master, mode=align, hz=hz, master_hz=master_hz
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
