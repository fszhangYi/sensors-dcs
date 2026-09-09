#!/usr/bin/env python3
"""Read-only wall-clock sync QC for one episode → JSON.

Week1 D4 / Gap G03. Field names are frozen for W2 wall-vs-hw compare — do not rename
``cam_dt_p50``, ``cam_dt_p95``, ``cam_gap_count``, ``state_cam_abs_dt_p50``,
``state_cam_abs_dt_p95``, ``missing_agents``, ``hw_ts_present``, ``hw_ts_coverage``.

Example::

    python scripts/qc_episode_sync.py \\
      --episode docs/portfolio/baseline/ep_good \\
      --camera-primary cam-middle \\
      --out docs/portfolio/baseline/reports/sync_wall_good.json
"""

from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left
from pathlib import Path
from typing import Any

SCHEMA = "qc_episode_sync.v1"
ALIGN_CLOCK = "wall"

# Manifest kinds treated as primary state for state↔cam |Δt|.
PRIMARY_STATE_KINDS = frozenset({"gello", "arm", "arm_read", "arm_write"})
STATE_KINDS = frozenset(
    {"gello", "arm", "arm_read", "arm_write", "gripper_read", "gripper", "gripper_write"}
)


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return float(ys[0])
    k = (len(ys) - 1) * (p / 100.0)
    f = int(math.floor(k))
    c = min(f + 1, len(ys) - 1)
    return float(ys[f] + (ys[c] - ys[f]) * (k - f))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _load_manifest(ep: Path) -> dict[str, Any] | None:
    path = ep / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _nearest_abs_dt(query_ts: list[float], ref_ts: list[float]) -> list[float]:
    if not query_ts or not ref_ts:
        return []
    ref = sorted(ref_ts)
    out: list[float] = []
    for t in query_ts:
        i = bisect_left(ref, t)
        cands: list[float] = []
        if i < len(ref):
            cands.append(abs(ref[i] - t))
        if i > 0:
            cands.append(abs(ref[i - 1] - t))
        out.append(min(cands))
    return out


def _is_primary_state(agent_id: str, kind: str) -> bool:
    if kind in PRIMARY_STATE_KINDS:
        return True
    return agent_id.startswith(("gello", "arm"))


def analyze_episode(
    episode: Path,
    *,
    camera_primary: str | None = None,
    gap_factor: float = 2.5,
    state_sample_cap: int = 5000,
) -> dict[str, Any]:
    """Analyze one episode on wall-clock axis. Returns stable-keyed dict."""
    ep = episode.expanduser().resolve()
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "align_clock": ALIGN_CLOCK,
        "episode": ep.name,
        "episode_path": str(ep),
        "camera_primary": None,
        "gap_factor": float(gap_factor),
        "cam_dt_p50": None,
        "cam_dt_p95": None,
        "cam_gap_count": 0,
        "state_cam_abs_dt_p50": None,
        "state_cam_abs_dt_p95": None,
        "missing_agents": [],
        "hw_ts_present": False,
        "hw_ts_coverage": 0.0,
        "n_cam_frames": 0,
        "n_state_samples": 0,
        "cameras": [],
        "states": [],
        "valid": None,
        "written": None,
        "dropped": None,
        "drop_rate": None,
        "duration_s": None,
        "error": None,
    }

    man = _load_manifest(ep)
    if man is None:
        out["error"] = "missing or unreadable manifest.json"
        return out

    if "valid" in man:
        out["valid"] = bool(man.get("valid"))
    if man.get("written") is not None:
        out["written"] = int(man["written"])
    if man.get("dropped") is not None:
        out["dropped"] = int(man["dropped"])
    w = float(out["written"] or 0)
    d = float(out["dropped"] or 0)
    if w + d > 0:
        out["drop_rate"] = d / (w + d)
    if man.get("duration_s") is not None:
        try:
            out["duration_s"] = float(man["duration_s"])
        except (TypeError, ValueError):
            pass
    if out["duration_s"] is None and man.get("t_start") is not None and man.get("t_end") is not None:
        try:
            out["duration_s"] = float(man["t_end"]) - float(man["t_start"])
        except (TypeError, ValueError):
            pass

    kind_by_id: dict[str, str] = {}
    declared: list[str] = []
    for item in man.get("agents") or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("agent_id") or "").strip()
        if not aid:
            continue
        declared.append(aid)
        kind_by_id[aid] = str(item.get("kind") or "")

    cam_dir = ep / "cameras"
    state_dir = ep / "states"
    cameras = sorted(p.name for p in cam_dir.iterdir() if p.is_dir()) if cam_dir.is_dir() else []
    states = sorted(p.stem for p in state_dir.glob("*.jsonl")) if state_dir.is_dir() else []
    out["cameras"] = cameras
    out["states"] = states

    missing: list[str] = []
    for aid in declared:
        kind = kind_by_id.get(aid, "")
        if kind == "realsense" or aid.startswith("cam-"):
            if aid not in cameras:
                missing.append(aid)
        elif kind in STATE_KINDS:
            if aid not in states:
                missing.append(aid)
        elif not kind and aid not in states and aid not in cameras:
            missing.append(aid)
    out["missing_agents"] = missing

    if not cameras:
        out["error"] = "no cameras/*/index.jsonl"
        return out

    # Resolve primary camera.
    if camera_primary and camera_primary in cameras:
        prim = camera_primary
    elif "cam-middle" in cameras:
        prim = "cam-middle"
    else:
        # Prefer camera with most index rows.
        prim = max(
            cameras,
            key=lambda c: len(_read_jsonl(ep / "cameras" / c / "index.jsonl")),
        )
    out["camera_primary"] = prim

    cam_rows = _read_jsonl(ep / "cameras" / prim / "index.jsonl")
    out["n_cam_frames"] = len(cam_rows)

    t_walls: list[float] = []
    hw_ok = 0
    for row in cam_rows:
        tw = row.get("t_wall")
        if isinstance(tw, (int, float)):
            t_walls.append(float(tw))
        cts = row.get("color_timestamp")
        if cts is not None and cts != "":
            hw_ok += 1

    n = max(len(cam_rows), 1)
    coverage = hw_ok / n if cam_rows else 0.0
    out["hw_ts_coverage"] = float(coverage)
    out["hw_ts_present"] = coverage > 0.0

    dts: list[float] = []
    for i in range(1, len(t_walls)):
        dlt = t_walls[i] - t_walls[i - 1]
        if dlt >= 0:
            dts.append(dlt)
    out["cam_dt_p50"] = _pct(dts, 50)
    out["cam_dt_p95"] = _pct(dts, 95)
    gap_count = 0
    if dts and out["cam_dt_p50"] and out["cam_dt_p50"] > 0:
        thr = gap_factor * float(out["cam_dt_p50"])
        gap_count = sum(1 for dlt in dts if dlt > thr)
    out["cam_gap_count"] = int(gap_count)

    # State ↔ primary camera nearest |Δt| on t_wall.
    state_ts: list[float] = []
    for sid in states:
        kind = kind_by_id.get(sid, "")
        if not _is_primary_state(sid, kind):
            continue
        for row in _read_jsonl(ep / "states" / f"{sid}.jsonl"):
            tw = row.get("t_wall")
            if isinstance(tw, (int, float)):
                state_ts.append(float(tw))
    if len(state_ts) > state_sample_cap:
        step = max(1, len(state_ts) // state_sample_cap)
        state_ts = state_ts[::step]
    out["n_state_samples"] = len(state_ts)
    abs_dts = _nearest_abs_dt(state_ts, t_walls)
    out["state_cam_abs_dt_p50"] = _pct(abs_dts, 50)
    out["state_cam_abs_dt_p95"] = _pct(abs_dts, 95)

    return out


def collect_wall_series(
    episode: Path,
    *,
    camera_primary: str | None = None,
    gap_factor: float = 2.5,
    state_sample_cap: int = 5000,
) -> dict[str, Any]:
    """Raw wall-clock series for histograms (D5). Same rules as ``analyze_episode``."""
    report = analyze_episode(
        episode,
        camera_primary=camera_primary,
        gap_factor=gap_factor,
        state_sample_cap=state_sample_cap,
    )
    ep = episode.expanduser().resolve()
    prim = report.get("camera_primary")
    cam_dts: list[float] = []
    abs_dts: list[float] = []
    gap_threshold: float | None = None

    if prim and not report.get("error"):
        cam_rows = _read_jsonl(ep / "cameras" / str(prim) / "index.jsonl")
        t_walls = [
            float(r["t_wall"])
            for r in cam_rows
            if isinstance(r.get("t_wall"), (int, float))
        ]
        for i in range(1, len(t_walls)):
            dlt = t_walls[i] - t_walls[i - 1]
            if dlt >= 0:
                cam_dts.append(dlt)
        if cam_dts and report.get("cam_dt_p50"):
            gap_threshold = float(gap_factor) * float(report["cam_dt_p50"])

        man = _load_manifest(ep) or {}
        kind_by_id: dict[str, str] = {}
        for item in man.get("agents") or []:
            if isinstance(item, dict) and item.get("agent_id"):
                kind_by_id[str(item["agent_id"])] = str(item.get("kind") or "")
        state_dir = ep / "states"
        states = (
            sorted(p.stem for p in state_dir.glob("*.jsonl")) if state_dir.is_dir() else []
        )
        state_ts: list[float] = []
        for sid in states:
            if not _is_primary_state(sid, kind_by_id.get(sid, "")):
                continue
            for row in _read_jsonl(ep / "states" / f"{sid}.jsonl"):
                tw = row.get("t_wall")
                if isinstance(tw, (int, float)):
                    state_ts.append(float(tw))
        if len(state_ts) > state_sample_cap:
            step = max(1, len(state_ts) // state_sample_cap)
            state_ts = state_ts[::step]
        abs_dts = _nearest_abs_dt(state_ts, t_walls)

    return {
        "report": report,
        "cam_dts": cam_dts,
        "state_cam_abs_dts": abs_dts,
        "gap_threshold": gap_threshold,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Wall-clock sync QC for one episode (JSON). Stable fields for W2."
    )
    p.add_argument("--episode", "-e", type=Path, required=True, help="episode directory")
    p.add_argument(
        "--camera-primary",
        default=None,
        help="primary camera agent_id (default: cam-middle or densest)",
    )
    p.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="write JSON to this path (also printed to stdout)",
    )
    p.add_argument(
        "--gap-factor",
        type=float,
        default=2.5,
        help="gap if cam dt > factor * cam_dt_p50 (default: 2.5)",
    )
    p.add_argument(
        "--state-sample-cap",
        type=int,
        default=5000,
        help="max state timestamps for |Δt| (default: 5000)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.episode.exists():
        print(f"error: episode not found: {args.episode}", flush=True)
        return 2
    report = analyze_episode(
        args.episode,
        camera_primary=args.camera_primary,
        gap_factor=args.gap_factor,
        state_sample_cap=args.state_sample_cap,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        out = args.out.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", flush=True)
        # Avoid dumping full JSON twice when writing a file (D7 path UX).
        summary = {
            "episode": report.get("episode"),
            "cam_gap_count": report.get("cam_gap_count"),
            "state_cam_abs_dt_p95": report.get("state_cam_abs_dt_p95"),
            "hw_ts_coverage": report.get("hw_ts_coverage"),
            "drop_rate": report.get("drop_rate"),
            "error": report.get("error"),
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    else:
        print(text, end="")
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
