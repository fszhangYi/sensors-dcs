"""Multi-station session registry (联机会话清单).

Written only when a master barrier starts with active slaves. Single-machine
collect never creates ``sessions/``. Schema: ``dcs_session_v1``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SCHEMA = "dcs_session_v1"
SESSIONS_DIRNAME = "sessions"


def episode_relpath(episode_index: int) -> str:
    return f"episode_{int(episode_index):05d}"


def sessions_root(save_dir: str | Path) -> Path:
    return Path(save_dir).expanduser().resolve() / SESSIONS_DIRNAME


def session_dir(save_dir: str | Path, session_id: str) -> Path:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    return sessions_root(save_dir) / sid


def session_json_path(save_dir: str | Path, session_id: str) -> Path:
    return session_dir(save_dir, session_id) / "session.json"


def build_episode_uri(
    *,
    station_id: str,
    advertise_ip: str,
    save_dir: str,
    episode_rel: str,
    share_root: str = "",
    share_station_subdir: str = "",
) -> dict[str, Any]:
    """Describe where an episode lives for later discovery / copy layout."""
    sid = str(station_id or "").strip() or "unknown"
    ep_rel = str(episode_rel or "").strip()
    local_root = str(Path(str(save_dir)).expanduser())
    local_ep = str(Path(local_root) / ep_rel) if ep_rel else local_root
    share = str(share_root or "").strip()
    if share:
        sub = str(share_station_subdir or "").strip() or sid
        shared_ep = str(Path(share) / sub / ep_rel) if ep_rel else str(Path(share) / sub)
        return {
            "kind": "share",
            "uri": shared_ep,
            "local_path": local_ep,
            "reachability": "share_root",
        }
    host = str(advertise_ip or "").strip() or "local"
    return {
        "kind": "file",
        "uri": f"file:{host}:{local_ep}",
        "local_path": local_ep,
        "reachability": "local_or_manual_copy",
    }


def build_participant(
    *,
    station_id: str,
    role: str,
    advertise_ip: str = "",
    viz_port: int | None = None,
    save_dir: str = "",
    episode_index: int | None = None,
    episode_path: str | None = None,
    t_start: float | None = None,
    agents: list[dict[str, Any]] | None = None,
    share_root: str = "",
    share_station_subdir: str = "",
) -> dict[str, Any]:
    sid = str(station_id or "").strip()
    if not sid:
        raise ValueError("participant station_id is required")
    role_n = "slave" if str(role).strip().lower() == "slave" else "master"
    ep_idx = int(episode_index) if episode_index is not None else None
    ep_rel = episode_relpath(ep_idx) if ep_idx is not None else ""
    save = str(save_dir or "").strip()
    if episode_path:
        ep_path = str(Path(str(episode_path)).expanduser())
        if not ep_rel:
            ep_rel = Path(ep_path).name
    elif save and ep_rel:
        ep_path = str(Path(save).expanduser() / ep_rel)
    else:
        ep_path = ""
    uri = build_episode_uri(
        station_id=sid,
        advertise_ip=advertise_ip,
        save_dir=save or (str(Path(ep_path).parent) if ep_path else ""),
        episode_rel=ep_rel,
        share_root=share_root,
        share_station_subdir=share_station_subdir,
    )
    agents_summary: list[dict[str, Any]] = []
    for a in agents or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or a.get("agent_id") or "").strip()
        if not aid:
            continue
        agents_summary.append(
            {
                "id": aid,
                "kind": a.get("kind") or a.get("type"),
                "sensor_id": a.get("sensor_id"),
            }
        )
    out: dict[str, Any] = {
        "station_id": sid,
        "role": role_n,
        "advertise_ip": str(advertise_ip or "").strip(),
        "viz_port": int(viz_port) if viz_port is not None else None,
        "save_dir": save,
        "episode_index": ep_idx,
        "episode_relpath": ep_rel or None,
        "episode_path": ep_path or None,
        "episode_uri": uri,
        "t_start": float(t_start) if t_start is not None else None,
        "agents_summary": agents_summary,
    }
    return out


def validate_unique_station_ids(participants: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for p in participants:
        sid = str((p or {}).get("station_id") or "").strip()
        if not sid:
            raise ValueError("participant missing station_id")
        if sid in seen:
            raise ValueError(f"duplicate station_id in session: {sid}")
        seen.add(sid)


def build_session_doc(
    *,
    session_id: str,
    episode_index: int | None,
    mode: str,
    master_station_id: str,
    participants: list[dict[str, Any]],
    created_at: float | None = None,
) -> dict[str, Any]:
    validate_unique_station_ids(participants)
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    mid = str(master_station_id or "").strip()
    if not mid:
        raise ValueError("master_station_id is required")
    if not any(str(p.get("station_id")) == mid for p in participants):
        raise ValueError(f"master_station_id {mid!r} not in participants")
    return {
        "schema": SCHEMA,
        "session_id": sid,
        "episode_index": int(episode_index) if episode_index is not None else None,
        "mode": "infer" if str(mode).strip().lower() == "infer" else "collect",
        "created_at": float(created_at if created_at is not None else time.time()),
        "master_station_id": mid,
        "participants": list(participants),
    }


def write_session_registry(
    save_dir: str | Path,
    doc: dict[str, Any],
) -> Path:
    """Write ``{save_dir}/sessions/{session_id}/session.json``. Returns path."""
    sid = str(doc.get("session_id") or "").strip()
    if not sid:
        raise ValueError("doc.session_id is required")
    validate_unique_station_ids(list(doc.get("participants") or []))
    path = session_json_path(save_dir, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_session_registry(
    save_dir: str | Path,
    session_id: str,
) -> dict[str, Any] | None:
    path = session_json_path(save_dir, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_session_docs(save_dir: str | Path | None) -> list[dict[str, Any]]:
    """Newest-first session summaries under ``save_dir/sessions/``."""
    if not save_dir:
        return []
    root = sessions_root(save_dir)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        man = d / "session.json"
        if not man.is_file():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        participants = data.get("participants") if isinstance(data.get("participants"), list) else []
        rows.append(
            {
                "session_id": data.get("session_id") or d.name,
                "episode_index": data.get("episode_index"),
                "mode": data.get("mode"),
                "created_at": data.get("created_at"),
                "master_station_id": data.get("master_station_id"),
                "participant_count": len(participants),
                "path": str(man),
                "dir": str(d),
                "has_merged": (d / "merged").is_dir(),
            }
        )
    return rows


def convention_episode_candidates(
    *,
    save_dir: str | Path | None,
    session_id: str | None,
    station_id: str,
    episode_index: int | None = None,
    role: str | None = None,
) -> list[Path]:
    """Recommended / relocated layouts under the analysis-machine save_dir.

    ```text
    {save_dir}/episode_XXXXX/                                 # master native
    {save_dir}/sessions/{session_id}/{station_id}/episode_XXXXX/
    {save_dir}/sessions/{session_id}/{station_id}/
    ```
    """
    if not save_dir:
        return []
    root_save = Path(save_dir).expanduser()
    try:
        root_save = root_save.resolve()
    except OSError:
        pass
    out: list[Path] = []
    ep_rel: str | None = None
    if episode_index is not None:
        try:
            ep_rel = episode_relpath(int(episode_index))
        except (TypeError, ValueError):
            ep_rel = None
    role_n = str(role or "").strip().lower()
    # Root save_dir/episode_* is the native master layout only — never for slaves
    # (otherwise every slave resolves to the master's episode after a zip relocate).
    if role_n in ("", "master") and ep_rel:
        out.append(root_save / ep_rel)
    if session_id and station_id:
        root = session_dir(root_save, session_id) / str(station_id).strip()
        if ep_rel:
            out.append(root / ep_rel)
        out.append(root)
    return out


def resolve_participant_episode_path(
    participant: dict[str, Any],
    *,
    remaps: dict[str, str] | None = None,
    save_dir: str | Path | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Pick a readable episode directory for one participant.

    Order: remap → relocated conventions under ``save_dir`` (incl. root
    ``episode_*`` for master) → registered absolute paths / URIs (may be stale).
    """
    sid = str(participant.get("station_id") or "").strip()
    remaps = remaps or {}
    candidates: list[str] = []
    if sid and remaps.get(sid):
        candidates.append(str(remaps[sid]))
    sess = str(session_id or "").strip() or None
    ep_idx = participant.get("episode_index")
    ep_rel = str(participant.get("episode_relpath") or "").strip() or None
    if ep_idx is None and ep_rel:
        name = Path(ep_rel).name
        if name.startswith("episode_"):
            try:
                ep_idx = int(name.split("_", 1)[1])
            except ValueError:
                ep_idx = None
    if ep_rel is None and ep_idx is not None:
        try:
            ep_rel = episode_relpath(int(ep_idx))
        except (TypeError, ValueError):
            ep_rel = None
    for c in convention_episode_candidates(
        save_dir=save_dir,
        session_id=sess,
        station_id=sid,
        episode_index=int(ep_idx) if ep_idx is not None else None,
        role=str(participant.get("role") or ""),
    ):
        candidates.append(str(c))
    role_n = str(participant.get("role") or "").strip().lower()
    # Only master may fall back to save_dir/<episode_basename>.
    if role_n in ("", "master") and save_dir and ep_rel:
        candidates.append(str(Path(save_dir).expanduser() / Path(ep_rel).name))
    if participant.get("episode_path"):
        candidates.append(str(participant["episode_path"]))
        if role_n in ("", "master") and save_dir:
            candidates.append(
                str(Path(save_dir).expanduser() / Path(str(participant["episode_path"])).name)
            )
    uri = participant.get("episode_uri")
    if isinstance(uri, dict):
        if uri.get("local_path"):
            candidates.append(str(uri["local_path"]))
            if role_n in ("", "master") and save_dir:
                candidates.append(
                    str(Path(save_dir).expanduser() / Path(str(uri["local_path"])).name)
                )
        if uri.get("kind") == "share" and uri.get("uri"):
            candidates.append(str(uri["uri"]))
    seen: set[str] = set()
    for raw in candidates:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        p = Path(key).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if p.is_dir() and (p / "manifest.json").is_file():
            return p
    return None


def inspect_session_paths(
    doc: dict[str, Any],
    *,
    remaps: dict[str, str] | None = None,
    save_dir: str | Path | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Health-check each participant's episode directory."""
    participants = doc.get("participants") if isinstance(doc.get("participants"), list) else []
    sid_doc = str(session_id or doc.get("session_id") or "").strip() or None
    ep_idx = doc.get("episode_index")
    rows: list[dict[str, Any]] = []
    missing = 0
    for p in participants:
        if not isinstance(p, dict):
            continue
        sid = str(p.get("station_id") or "")
        # Ensure episode_index available for convention lookup.
        p_use = dict(p)
        if p_use.get("episode_index") is None and ep_idx is not None:
            p_use["episode_index"] = ep_idx
        resolved = resolve_participant_episode_path(
            p_use, remaps=remaps, save_dir=save_dir, session_id=sid_doc
        )
        ok = resolved is not None
        if not ok:
            missing += 1
        conv = []
        if save_dir and sid:
            conv = [
                str(c)
                for c in convention_episode_candidates(
                    save_dir=save_dir,
                    session_id=sid_doc,
                    station_id=sid,
                    episode_index=int(ep_idx) if ep_idx is not None else None,
                    role=str(p.get("role") or ""),
                )
            ]
        rows.append(
            {
                "station_id": sid,
                "role": p.get("role"),
                "ok": ok,
                "path": str(resolved) if resolved else None,
                "expected_episode_path": p.get("episode_path"),
                "convention_paths": conv,
                "episode_uri": p.get("episode_uri"),
                "t_start": p.get("t_start"),
            }
        )
    return {
        "session_id": doc.get("session_id"),
        "episode_index": doc.get("episode_index"),
        "master_station_id": doc.get("master_station_id"),
        "participants": rows,
        "missing_count": missing,
        "all_present": missing == 0 and bool(rows),
    }
