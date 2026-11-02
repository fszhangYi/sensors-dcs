"""Build a single-station-shaped episode from a multi-station session.

Each participant keeps its own ``t_start``. Samples are rewritten onto a common
wall clock:

    t_wall' = master_t0 + (t_wall - station_t0)   # ≡ master_t0 + τ

Master-station agent ids stay unchanged (hik / require defaults keep working).
Slave agents are prefixed ``{station_id}__{agent_id}`` so paths never collide.

Camera JPEG/PNG files are hardlinked when possible (symlink / copy fallback).
The result under ``sessions/<id>/merged/episode_XXXXX/`` is a normal
``dcs_episode_v1`` directory that ``run_postprocess`` can consume unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from sensors_dcs.session_registry import (
    episode_relpath,
    inspect_session_paths,
    read_session_registry,
    resolve_participant_episode_path,
    session_dir,
)

# re-export for callers / tests
__all__ = ["build_federated_episode", "federated_agent_id"]


def federated_agent_id(station_id: str, agent_id: str, *, is_master: bool) -> str:
    aid = str(agent_id or "").strip()
    if is_master or not aid:
        return aid
    sid = str(station_id or "").strip()
    if not sid:
        return aid
    return f"{sid}__{aid}"


def _link_or_copy(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        return "exists"
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return "symlink"
    except OSError:
        pass
    shutil.copy2(src, dst)
    return "copy"


def _station_t0(manifest: dict[str, Any], ep_dir: Path) -> float:
    if manifest.get("t_start") is not None:
        return float(manifest["t_start"])
    # Fallback: earliest sample wall time from any state/camera index.
    times: list[float] = []
    states = ep_dir / "states"
    if states.is_dir():
        for path in states.glob("*.jsonl"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        if row.get("t_wall") is not None:
                            times.append(float(row["t_wall"]))
                            break
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
    if times:
        return min(times)
    return 0.0


def _rewrite_state_jsonl(
    src: Path,
    dst: Path,
    *,
    station_t0: float,
    master_t0: float,
    out_agent_id: str | None,
) -> int:
    """Rewrite one states/*.jsonl onto the federated wall clock. Returns lines written."""
    n = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            try:
                tw = float(row["t_wall"])
            except (KeyError, TypeError, ValueError):
                continue
            row["t_wall"] = float(master_t0) + (tw - float(station_t0))
            if row.get("t_mono") is not None:
                try:
                    # Keep mono delta relative to station start mapped onto master domain.
                    tm = float(row["t_mono"])
                    row["t_mono"] = float(master_t0) + (tm - float(station_t0))
                except (TypeError, ValueError):
                    pass
            if out_agent_id:
                row["agent_id"] = out_agent_id
            row["session_tau"] = tw - float(station_t0)
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def _rewrite_camera_agent(
    src_agent_dir: Path,
    dst_agent_dir: Path,
    *,
    station_t0: float,
    master_t0: float,
    out_agent_id: str,
) -> dict[str, Any]:
    """Link media files and rewrite index.jsonl timestamps / agent_id."""
    index_src = src_agent_dir / "index.jsonl"
    if not index_src.is_file():
        return {"ok": False, "error": "index.jsonl missing", "frames": 0, "links": {}}
    dst_agent_dir.mkdir(parents=True, exist_ok=True)
    links: dict[str, int] = {"hardlink": 0, "symlink": 0, "copy": 0, "exists": 0}
    frames = 0
    with index_src.open("r", encoding="utf-8") as fin, (
        dst_agent_dir / "index.jsonl"
    ).open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            try:
                tw = float(row["t_wall"])
            except (KeyError, TypeError, ValueError):
                continue
            row["t_wall"] = float(master_t0) + (tw - float(station_t0))
            if row.get("t_mono") is not None:
                try:
                    tm = float(row["t_mono"])
                    row["t_mono"] = float(master_t0) + (tm - float(station_t0))
                except (TypeError, ValueError):
                    pass
            row["agent_id"] = out_agent_id
            row["session_tau"] = tw - float(station_t0)
            for key in ("file", "depth_file"):
                name = row.get(key)
                if not name:
                    continue
                src_f = src_agent_dir / str(name)
                if src_f.is_file():
                    kind = _link_or_copy(src_f, dst_agent_dir / str(name))
                    links[kind] = links.get(kind, 0) + 1
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            frames += 1
    return {"ok": True, "frames": frames, "links": links}


def build_federated_episode(
    *,
    save_dir: str | Path,
    session_id: str,
    remaps: dict[str, str] | None = None,
    master_station_id: str | None = None,
    output_dir: str | Path | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Materialize ``sessions/<id>/merged/episode_XXXXX/`` from all participants."""
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

    master_p = next(p for p in participants if str(p.get("station_id")) == mid)
    master_ep = resolve_participant_episode_path(
        master_p, remaps=remaps, save_dir=save_dir, session_id=session_id
    )
    assert master_ep is not None
    master_man = json.loads((master_ep / "manifest.json").read_text(encoding="utf-8"))
    master_t0 = _station_t0(master_man, master_ep)

    ep_idx = doc.get("episode_index")
    if ep_idx is None:
        ep_idx = master_man.get("episode_index")
    try:
        ep_name = episode_relpath(int(ep_idx)) if ep_idx is not None else "episode_00000"
    except (TypeError, ValueError):
        ep_name = "episode_00000"

    out_ep = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else session_dir(save_dir, session_id) / "merged" / ep_name
    )
    if out_ep.exists() and overwrite:
        shutil.rmtree(out_ep)
    out_ep.mkdir(parents=True, exist_ok=True)
    (out_ep / "states").mkdir(exist_ok=True)
    (out_ep / "cameras").mkdir(exist_ok=True)

    agents_out: list[dict[str, Any]] = []
    cameras_out: dict[str, Any] = {}
    station_reports: list[dict[str, Any]] = []
    written_states = 0
    written_frames = 0
    t_end = master_t0

    for p in participants:
        sid = str(p.get("station_id") or "")
        is_master = sid == mid
        ep = resolve_participant_episode_path(
            p, remaps=remaps, save_dir=save_dir, session_id=session_id
        )
        assert ep is not None
        man = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
        t0 = _station_t0(man, ep)
        if man.get("t_end") is not None:
            try:
                local_end = float(man["t_end"])
                t_end = max(t_end, master_t0 + (local_end - t0))
            except (TypeError, ValueError):
                pass

        report: dict[str, Any] = {
            "station_id": sid,
            "role": p.get("role"),
            "episode_path": str(ep),
            "t_start": t0,
            "agents": [],
            "cameras": [],
        }

        # --- states ---
        states_dir = ep / "states"
        if states_dir.is_dir():
            for src in sorted(states_dir.glob("*.jsonl")):
                raw_aid = src.stem
                # Prefer agent_id inside first row if present.
                try:
                    with src.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            row0 = json.loads(line)
                            if isinstance(row0, dict) and row0.get("agent_id"):
                                raw_aid = str(row0["agent_id"])
                            break
                except (OSError, json.JSONDecodeError):
                    pass
                out_aid = federated_agent_id(sid, raw_aid, is_master=is_master)
                dst = out_ep / "states" / f"{out_aid}.jsonl"
                # Append if collision (shouldn't for unique prefixes).
                mode_append = dst.is_file()
                if mode_append:
                    tmp = dst.with_suffix(".jsonl.part")
                    n = _rewrite_state_jsonl(
                        src,
                        tmp,
                        station_t0=t0,
                        master_t0=master_t0,
                        out_agent_id=out_aid,
                    )
                    with dst.open("a", encoding="utf-8") as fout, tmp.open(
                        "r", encoding="utf-8"
                    ) as fin:
                        fout.write(fin.read())
                    tmp.unlink(missing_ok=True)
                else:
                    n = _rewrite_state_jsonl(
                        src,
                        dst,
                        station_t0=t0,
                        master_t0=master_t0,
                        out_agent_id=out_aid,
                    )
                written_states += n
                report["agents"].append({"agent_id": out_aid, "src": raw_aid, "rows": n})

        # --- cameras ---
        cams_dir = ep / "cameras"
        cam_meta_src = man.get("cameras") if isinstance(man.get("cameras"), dict) else {}
        if cams_dir.is_dir():
            for agent_dir in sorted(p for p in cams_dir.iterdir() if p.is_dir()):
                raw_aid = agent_dir.name
                out_aid = federated_agent_id(sid, raw_aid, is_master=is_master)
                cam_res = _rewrite_camera_agent(
                    agent_dir,
                    out_ep / "cameras" / out_aid,
                    station_t0=t0,
                    master_t0=master_t0,
                    out_agent_id=out_aid,
                )
                written_frames += int(cam_res.get("frames") or 0)
                report["cameras"].append(
                    {"agent_id": out_aid, "src": raw_aid, **cam_res}
                )
                src_info = cam_meta_src.get(raw_aid)
                if isinstance(src_info, dict):
                    cameras_out[out_aid] = dict(src_info)
                else:
                    cameras_out[out_aid] = {"agent_id": out_aid}

        # --- agents meta ---
        for item in man.get("agents") or []:
            if not isinstance(item, dict) or not item.get("agent_id"):
                continue
            raw_aid = str(item["agent_id"])
            out_aid = federated_agent_id(sid, raw_aid, is_master=is_master)
            entry = dict(item)
            entry["agent_id"] = out_aid
            entry["station_id"] = sid
            entry["source_agent_id"] = raw_aid
            agents_out.append(entry)

        station_reports.append(report)

    # Deduplicate agents_out by agent_id (keep first).
    seen_a: set[str] = set()
    agents_unique: list[dict[str, Any]] = []
    for a in agents_out:
        aid = str(a.get("agent_id") or "")
        if not aid or aid in seen_a:
            continue
        seen_a.add(aid)
        agents_unique.append(a)

    fed_manifest: dict[str, Any] = {
        "format": "dcs_episode_v1",
        "site": master_man.get("site"),
        "episode_index": doc.get("episode_index"),
        "record_mode": doc.get("mode") or master_man.get("record_mode") or "collect",
        "t_start": master_t0,
        "t_end": t_end,
        "duration_s": float(t_end) - float(master_t0) if t_end is not None else None,
        "written": int(written_states + written_frames),
        "dropped": 0,
        "agents": agents_unique,
        "cameras": cameras_out,
        "valid": True,
        "session_id": session_id,
        "station_id": mid,
        "station_role": "federated",
        "federated": True,
        "federated_from": {
            "master_station_id": mid,
            "participants": [
                {
                    "station_id": r["station_id"],
                    "episode_path": r["episode_path"],
                    "t_start": r["t_start"],
                }
                for r in station_reports
            ],
        },
        "note": (
            "Federated multi-station episode; "
            "t_wall' = master_t0 + (t_wall - station_t0). "
            "Slave agents prefixed {station_id}__{agent_id}."
        ),
    }
    (out_ep / "manifest.json").write_text(
        json.dumps(fed_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    meta_path = out_ep.parent / "federate_meta.json"
    meta = {
        "schema": "dcs_session_federate_v1",
        "session_id": session_id,
        "episode_path": str(out_ep),
        "master_station_id": mid,
        "master_t0": master_t0,
        "stations": station_reports,
        "written_states": written_states,
        "written_frames": written_frames,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "session_id": session_id,
        "episode_path": str(out_ep),
        "meta_path": str(meta_path),
        "master_station_id": mid,
        "master_t0": master_t0,
        "written_states": written_states,
        "written_frames": written_frames,
        "health": health,
        "manifest": fed_manifest,
    }
