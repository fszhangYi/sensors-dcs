"""Session gather orchestration: resolve paths, validate, slave push / master wait."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sensors_dcs.gather_sync import join_publish_uri, sync_directory
from sensors_dcs.session_registry import (
    episode_relpath,
    read_session_registry,
    session_dir,
)


def convention_slave_episode_dir(
    save_dir: str | Path,
    session_id: str,
    station_id: str,
    episode_index: int,
) -> Path:
    return (
        Path(save_dir).expanduser().resolve()
        / "sessions"
        / str(session_id).strip()
        / str(station_id).strip()
        / episode_relpath(int(episode_index))
    )


def resolve_session_for_gather(
    *,
    save_dir: str | Path,
    session_id: str | None = None,
    episode_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve session.json + episode_index from session_id and/or episode manifest."""
    root = Path(save_dir).expanduser().resolve()
    sid = str(session_id or "").strip()
    ep_path = Path(episode_path).expanduser() if episode_path else None
    ep_index: int | None = None
    man: dict[str, Any] = {}

    if ep_path is not None:
        if not ep_path.is_dir():
            raise FileNotFoundError(f"episode not found: {ep_path}")
        mp = ep_path / "manifest.json"
        if mp.is_file():
            man = json.loads(mp.read_text(encoding="utf-8"))
            if not sid:
                sid = str(man.get("session_id") or "").strip()
            if man.get("episode_index") is not None:
                ep_index = int(man["episode_index"])
        if ep_index is None and ep_path.name.startswith("episode_"):
            try:
                ep_index = int(ep_path.name.split("_", 1)[1])
            except ValueError:
                ep_index = None

    if not sid:
        raise ValueError(
            "session_id required (select a linked-session episode or pass session_id)"
        )

    doc = read_session_registry(root, sid)
    if not doc:
        raise FileNotFoundError(
            f"session.json not found under {session_dir(root, sid)}"
        )
    if ep_index is None and doc.get("episode_index") is not None:
        ep_index = int(doc["episode_index"])
    if ep_index is None:
        raise ValueError("episode_index unknown — pick episode or session with index")

    return {
        "ok": True,
        "save_dir": str(root),
        "session_id": sid,
        "episode_index": int(ep_index),
        "episode_relpath": episode_relpath(int(ep_index)),
        "doc": doc,
        "episode_path": str(ep_path) if ep_path else None,
        "manifest": man,
    }


def build_gather_dest(
    *,
    save_dir: str | Path,
    session_id: str,
    station_id: str,
    episode_index: int,
    publish_uri: str = "",
) -> dict[str, Any]:
    """Local master path + optional slave-reachable publish URI."""
    local = convention_slave_episode_dir(
        save_dir, session_id, station_id, episode_index
    )
    local.mkdir(parents=True, exist_ok=True)
    pub = str(publish_uri or "").strip()
    dest_uri = None
    if pub:
        dest_uri = join_publish_uri(
            pub,
            "sessions",
            session_id,
            station_id,
            episode_relpath(int(episode_index)),
        )
    return {
        "local_path": str(local),
        "dest_uri": dest_uri,
        "publish_uri": pub or None,
    }


def validate_gathered_episode(dest: str | Path, *, session_id: str, episode_index: int) -> dict[str, Any]:
    p = Path(dest)
    if not p.is_dir():
        return {"ok": False, "error": f"dest missing: {p}"}
    man_path = p / "manifest.json"
    if not man_path.is_file():
        return {"ok": False, "error": f"manifest.json missing under {p}"}
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"manifest unreadable: {e}"}
    sid = str(man.get("session_id") or "").strip()
    if sid and sid != str(session_id).strip():
        return {
            "ok": False,
            "error": f"manifest.session_id={sid!r} != {session_id!r}",
        }
    if man.get("episode_index") is not None and int(man["episode_index"]) != int(episode_index):
        return {
            "ok": False,
            "error": (
                f"manifest.episode_index={man.get('episode_index')} "
                f"!= {episode_index}"
            ),
        }
    return {"ok": True, "path": str(p), "manifest": man}


def slave_source_episode_dir(save_dir: str | Path, episode_index: int) -> Path:
    return Path(save_dir).expanduser().resolve() / episode_relpath(int(episode_index))


def run_slave_gather_push(
    *,
    source_episode: str | Path,
    dest_uri: str,
    backend: str = "auto",
    force: bool = False,
    timeout_s: float | None = 3600.0,
) -> dict[str, Any]:
    """Slave-side: push local episode tree to master dest (resume-capable sync)."""
    src = Path(source_episode)
    if not src.is_dir() or not (src / "manifest.json").is_file():
        return {
            "ok": False,
            "error": f"local episode incomplete: {src}",
            "src": str(src),
            "dest": dest_uri,
        }
    try:
        result = sync_directory(
            src,
            dest_uri,
            backend=backend,
            force=force,
            timeout_s=timeout_s,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "src": str(src),
            "dest": dest_uri,
        }
    return result


class GatherJobRegistry:
    """In-process gather job status (master + slave)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, job_id: str, meta: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "state": "running",
                "t_start": time.time(),
                **meta,
            }

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            row = self._jobs.get(job_id)
            if not row:
                return
            row.update(fields)

    def finish(self, job_id: str, *, ok: bool, **fields: Any) -> None:
        with self._lock:
            row = self._jobs.get(job_id) or {"job_id": job_id}
            row.update(fields)
            row["state"] = "ok" if ok else "error"
            row["ok"] = ok
            row["t_end"] = time.time()
            self._jobs[job_id] = row

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._jobs.get(job_id)
            return dict(row) if row else None

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._jobs:
                return None
            job_id = max(self._jobs.items(), key=lambda kv: float(kv[1].get("t_start") or 0))[0]
            return dict(self._jobs[job_id])


GATHER_JOBS = GatherJobRegistry()


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]
