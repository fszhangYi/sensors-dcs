"""Cross-station τ-domain merge for multi-machine sessions.

Each station episode keeps its own ``t_start``; events are mapped to
``τ = t_wall - t_start`` then asof-joined onto the master station's timeline.
Absolute ``t_wall`` is never used as the cross-station join key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sensors_dcs.session_registry import (
    inspect_session_paths,
    read_session_registry,
    resolve_participant_episode_path,
    session_dir,
)


def _prefix_agent(station_id: str, agent_id: str) -> str:
    return f"{station_id}::{agent_id}"


def _load_station_frames(
    ep_dir: Path,
    *,
    station_id: str,
) -> tuple[dict[str, Any], Any, float]:
    """Return (manifest, long events DataFrame with tau, t0)."""
    from sensors_dcs.export.parquet_io import require_pandas
    from sensors_dcs.export.timeline import build_events_frame, load_episode

    pd = require_pandas()
    manifest, samples = load_episode(ep_dir)
    t0 = float(manifest["t_start"]) if manifest.get("t_start") is not None else (
        min((s.t_wall for s in samples), default=0.0)
    )
    events = build_events_frame(manifest, samples)
    if events is None or getattr(events, "empty", True):
        empty = pd.DataFrame(columns=["t_wall", "t_rel", "tau", "agent_id", "kind"])
        return manifest, empty, t0
    df = events.copy()
    df["tau"] = df["t_wall"].astype(float) - float(t0)
    df["station_id"] = station_id
    df["agent_id"] = df["agent_id"].map(lambda a: _prefix_agent(station_id, str(a)))
    return manifest, df, t0


def _agent_tau_frame(events, agent_id: str):
    from sensors_dcs.export.parquet_io import require_pandas

    pd = require_pandas()
    sub = events[events["agent_id"] == agent_id]
    if sub.empty:
        return pd.DataFrame(columns=["tau"])
    # Keep tau + scalar payload columns (drop join metadata duplicates).
    drop = {"t_wall", "t_rel", "station_id", "agent_id", "kind", "seq", "path"}
    keep = ["tau"] + [c for c in sub.columns if c not in drop and c != "tau"]
    out = sub[keep].sort_values("tau", kind="mergesort").reset_index(drop=True)
    # Prefix value columns with agent id (tau stays).
    rename = {c: f"{agent_id}.{c}" for c in out.columns if c != "tau"}
    # Avoid double-prefix if already agent-scoped somehow.
    rename = {k: v for k, v in rename.items() if not str(k).startswith(f"{agent_id}.")}
    out = out.rename(columns=rename)
    src = f"{agent_id}.tau_src"
    out[src] = out["tau"]
    return out


def merge_session_tau(
    *,
    save_dir: str | Path,
    session_id: str,
    remaps: dict[str, str] | None = None,
    master_station_id: str | None = None,
    master_agent: str | None = None,
    max_match_dt: float | None = 0.033,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Merge all reachable participants of a session in τ domain.

    Writes ``sessions/{session_id}/merged/timeline_aligned_tau.parquet`` (+ json meta).
    """
    from sensors_dcs.export.parquet_io import require_pandas

    pd = require_pandas()
    doc = read_session_registry(save_dir, session_id)
    if not doc:
        return {
            "ok": False,
            "error": f"session registry not found: {session_id}",
            "session_id": session_id,
        }
    health = inspect_session_paths(
        doc, remaps=remaps, save_dir=save_dir, session_id=session_id
    )
    if not health.get("all_present"):
        missing = [
            r["station_id"] for r in health.get("participants") or [] if not r.get("ok")
        ]
        return {
            "ok": False,
            "error": f"missing participant episode dirs: {missing}",
            "session_id": session_id,
            "health": health,
        }

    mid = str(master_station_id or doc.get("master_station_id") or "").strip()
    participants = [p for p in (doc.get("participants") or []) if isinstance(p, dict)]
    if not any(str(p.get("station_id")) == mid for p in participants):
        return {
            "ok": False,
            "error": f"master_station_id not in participants: {mid}",
            "session_id": session_id,
        }

    station_events: dict[str, Any] = {}
    station_meta: dict[str, Any] = {}
    for p in participants:
        sid = str(p.get("station_id") or "")
        ep = resolve_participant_episode_path(
            p, remaps=remaps, save_dir=save_dir, session_id=session_id
        )
        assert ep is not None
        man, events, t0 = _load_station_frames(ep, station_id=sid)
        station_events[sid] = events
        station_meta[sid] = {
            "station_id": sid,
            "role": p.get("role"),
            "episode_path": str(ep),
            "t_start": t0,
            "manifest_t_start": man.get("t_start"),
            "agents": [
                str(a.get("agent_id"))
                for a in (man.get("agents") or [])
                if isinstance(a, dict) and a.get("agent_id")
            ],
        }

    master_events = station_events[mid]
    if master_events.empty:
        return {
            "ok": False,
            "error": f"master station has no events: {mid}",
            "session_id": session_id,
        }

    # Choose master agent (unprefixed within station, then prefix).
    agents_on_master = sorted(set(master_events["agent_id"].astype(str)))
    master_agent_pref = None
    if master_agent:
        raw = str(master_agent).strip()
        cand = raw if raw.startswith(f"{mid}::") else _prefix_agent(mid, raw)
        if cand in agents_on_master:
            master_agent_pref = cand
    if master_agent_pref is None:
        # Prefer camera-like agent on master station.
        for a in agents_on_master:
            short = a.split("::", 1)[-1]
            if short.startswith("cam-") or "realsense" in short:
                master_agent_pref = a
                break
        if master_agent_pref is None:
            master_agent_pref = agents_on_master[0]

    base_times = (
        master_events[master_events["agent_id"] == master_agent_pref]["tau"]
        .astype(float)
        .sort_values()
        .tolist()
    )
    if not base_times:
        return {
            "ok": False,
            "error": f"master agent has no samples: {master_agent_pref}",
            "session_id": session_id,
        }

    base = pd.DataFrame({"tau": base_times})
    all_agents = sorted(
        {str(a) for ev in station_events.values() for a in ev["agent_id"].astype(str).unique()}
    )
    match_stats: dict[str, Any] = {}
    for aid in all_agents:
        # Find which station owns this agent.
        owner = aid.split("::", 1)[0] if "::" in aid else mid
        ev = station_events.get(owner)
        if ev is None or ev.empty:
            continue
        right = _agent_tau_frame(ev, aid)
        if right.empty:
            base[f"{aid}.match_dt"] = float("nan")
            continue
        src_col = f"{aid}.tau_src"
        merged = pd.merge_asof(
            base.sort_values("tau"),
            right.sort_values("tau"),
            on="tau",
            direction="backward",
        )
        if src_col in merged.columns:
            merged[f"{aid}.match_dt"] = merged["tau"] - merged[src_col]
            series = merged[f"{aid}.match_dt"].dropna()
            if len(series):
                match_stats[f"{aid}.match_dt"] = {
                    "mean": float(series.mean()),
                    "max": float(series.max()),
                    "missing_rate": float(merged[f"{aid}.match_dt"].isna().mean()),
                }
            if max_match_dt is not None and max_match_dt > 0:
                bad = merged[f"{aid}.match_dt"].abs() > float(max_match_dt)
                # Keep rows but null out payload cols for this agent when too far.
                payload_cols = [
                    c
                    for c in merged.columns
                    if c.startswith(f"{aid}.") and c not in {f"{aid}.match_dt", src_col}
                ]
                for c in payload_cols:
                    merged.loc[bad, c] = pd.NA
            merged = merged.drop(columns=[src_col], errors="ignore")
        base = merged

    out_root = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else session_dir(save_dir, session_id) / "merged"
    )
    out_root.mkdir(parents=True, exist_ok=True)
    aligned_path = out_root / "timeline_aligned_tau.parquet"
    try:
        base.to_parquet(aligned_path, index=False)
    except Exception:
        # Fallback when pyarrow/fastparquet missing.
        csv_path = out_root / "timeline_aligned_tau.csv"
        base.to_csv(csv_path, index=False)
        aligned_path = csv_path

    meta = {
        "schema": "dcs_session_merge_v1",
        "session_id": session_id,
        "episode_index": doc.get("episode_index"),
        "master_station_id": mid,
        "master_agent": master_agent_pref,
        "max_match_dt": max_match_dt,
        "rows": int(len(base)),
        "stations": station_meta,
        "match_stats": match_stats,
        "aligned_path": str(aligned_path),
        "domain": "tau",
        "note": "τ = t_wall - station t_start; cross-station join on tau only",
    }
    meta_path = out_root / "merge_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "session_id": session_id,
        "output_dir": str(out_root),
        "aligned_path": str(aligned_path),
        "meta_path": str(meta_path),
        "rows": int(len(base)),
        "master_station_id": mid,
        "master_agent": master_agent_pref,
        "health": health,
        "match_stats": match_stats,
    }
