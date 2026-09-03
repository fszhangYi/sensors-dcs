from __future__ import annotations

import base64
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.config import RecordConfig
from sensors_dcs.frame import Frame


def _episode_dir(save_dir: Path, episode: int) -> Path:
    return save_dir / f"episode_{episode:05d}"


def discover_next_episode(save_dir: Path, configured: int) -> int:
    """Next free episode index from config, NEXT_EPISODE hint, and existing dirs."""
    save_dir.mkdir(parents=True, exist_ok=True)
    candidates = [max(0, int(configured))]
    hint = save_dir / "NEXT_EPISODE"
    if hint.is_file():
        try:
            candidates.append(max(0, int(hint.read_text(encoding="utf-8").strip())))
        except ValueError:
            pass
    for p in save_dir.glob("episode_*"):
        if not p.is_dir():
            continue
        try:
            n = int(p.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        candidates.append(n + 1)
    return max(candidates)


@dataclass
class _EpisodeSession:
    """One episode's in-memory write pipeline (sampler → queue → writer)."""

    ep: int
    ep_dir: Path
    t_start: float
    q: queue.Queue
    stop_event: threading.Event = field(default_factory=threading.Event)
    accepting: bool = True
    written: int = 0
    dropped: int = 0
    last_error: str | None = None
    writer: threading.Thread | None = None
    sampler: threading.Thread | None = None


class RecordController:
    """Sensors stay on; this only starts/stops a disk write consumer.

    Sync flush (default): stop() blocks until the episode queue is drained
    (``state=flushing``), then returns to idle — next Start waits.

    Async flush: stop(async_flush=True) detaches the episode job, bumps
    ``episode_index``, returns to idle immediately so the next episode can
    record while the previous job finishes writing (and optional quick-collect
    postprocess can run in parallel).
    """

    def __init__(
        self,
        cfg: RecordConfig,
        *,
        agents: dict[str, BaseAgent],
        site: str = "",
        on_status: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.agents = agents
        self.site = site
        self._on_status = on_status
        self._lock = threading.Lock()
        self.save_dir = Path(cfg.save_dir).expanduser().resolve()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.episode_index = discover_next_episode(self.save_dir, int(cfg.episode_index))
        self.state = "idle"  # idle | recording | flushing (sync only)
        self._session: _EpisodeSession | None = None
        self._flush_jobs: dict[int, dict[str, Any]] = {}
        self._last_error: str | None = None

    def _flush_jobs_unlocked(self) -> list[dict[str, Any]]:
        return [dict(v) for v in self._flush_jobs.values()]

    def _status_unlocked(self) -> dict[str, Any]:
        sess = self._session
        return {
            "state": self.state,
            "save_dir": str(self.save_dir),
            "episode_index": self.episode_index,
            "episode_path": str(sess.ep_dir) if sess is not None else None,
            "accepting": bool(sess.accepting) if sess is not None else False,
            "written": int(sess.written) if sess is not None else 0,
            "dropped": int(sess.dropped) if sess is not None else 0,
            "queue_size": sess.q.qsize() if sess is not None else 0,
            "last_error": (sess.last_error if sess is not None else None) or self._last_error,
            "flushing_count": len(self._flush_jobs),
            "flushing_jobs": self._flush_jobs_unlocked(),
        }

    def set_save_dir(self, raw: str | None) -> dict[str, Any]:
        """Change save root while idle (background flushes may still run)."""
        with self._lock:
            if self.state == "recording" or self.state == "flushing":
                return {
                    "ok": False,
                    "error": f"cannot change save_dir while state={self.state}",
                    **self._status_unlocked(),
                }
            if raw is None or not str(raw).strip():
                return {"ok": True, **self._status_unlocked()}
            path = Path(str(raw).strip()).expanduser().resolve()
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return {
                    "ok": False,
                    "error": f"cannot create save_dir: {e}",
                    **self._status_unlocked(),
                }
            self.save_dir = path
            self.cfg = self.cfg.model_copy(update={"save_dir": str(path)})
            self.episode_index = discover_next_episode(self.save_dir, int(self.cfg.episode_index))
        self._notify()
        return {"ok": True, **self.status()}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state != "idle":
                return {
                    "ok": False,
                    "error": f"cannot start while state={self.state}",
                    **self._status_unlocked(),
                }
            ep = self.episode_index
            ep_dir = _episode_dir(self.save_dir, ep)
            if ep_dir.exists():
                return {
                    "ok": False,
                    "error": f"episode dir already exists: {ep_dir}",
                    **self._status_unlocked(),
                }
            ep_dir.mkdir(parents=True, exist_ok=False)
            (ep_dir / "states").mkdir()
            (ep_dir / "cameras").mkdir()
            sess = _EpisodeSession(
                ep=ep,
                ep_dir=ep_dir,
                t_start=time.time(),
                q=queue.Queue(maxsize=max(8, int(self.cfg.queue_maxsize))),
            )
            sess.writer = threading.Thread(
                target=self._writer_loop,
                args=(sess,),
                name=f"record-writer-ep{ep}",
                daemon=True,
            )
            sess.sampler = threading.Thread(
                target=self._sampler_loop,
                args=(sess,),
                name=f"record-sampler-ep{ep}",
                daemon=True,
            )
            self._session = sess
            self.state = "recording"
            self._last_error = None
            sess.writer.start()
            sess.sampler.start()
            try:
                self._write_manifest(
                    ep_dir,
                    ep=ep,
                    t_start=sess.t_start,
                    t_end=None,
                    written=0,
                    dropped=0,
                    provisional=True,
                    valid=False,
                )
            except Exception as e:  # noqa: BLE001
                sess.last_error = f"initial manifest: {e}"
                self._last_error = sess.last_error
        self._notify()
        return {"ok": True, **self.status()}

    def stop(
        self,
        *,
        timeout: float = 120.0,
        valid: bool = True,
        async_flush: bool = False,
    ) -> dict[str, Any]:
        """Stop accepting frames and flush the episode to disk.

        ``async_flush=True``: return to idle immediately and finish the flush
        (manifest + queue drain) on a background thread so the next episode
        can start without waiting.
        """
        with self._lock:
            if self.state != "recording" or self._session is None:
                return {
                    "ok": False,
                    "error": f"cannot stop while state={self.state}",
                    **self._status_unlocked(),
                }
            sess = self._session
            sess.accepting = False
            ep = sess.ep
            ep_dir = sess.ep_dir
            if async_flush:
                self._session = None
                self.state = "idle"
                self.episode_index = ep + 1
                (self.save_dir / "NEXT_EPISODE").write_text(
                    str(self.episode_index) + "\n", encoding="utf-8"
                )
                self._flush_jobs[ep] = {
                    "episode_index": ep,
                    "episode_path": str(ep_dir),
                    "state": "flushing",
                    "valid": bool(valid),
                    "error": None,
                }
            else:
                self.state = "flushing"

        self._notify()

        if async_flush:
            threading.Thread(
                target=self._finalize_session,
                kwargs={
                    "sess": sess,
                    "valid": bool(valid),
                    "timeout": timeout,
                    "async_mode": True,
                },
                name=f"record-flush-ep{ep}",
                daemon=True,
            ).start()
            return {
                "ok": True,
                "async_flush": True,
                "manifest": None,
                "finished_episode_index": ep,
                "finished_episode_path": str(ep_dir),
                "valid": bool(valid),
                **self.status(),
            }

        manifest = self._finalize_session(
            sess=sess, valid=bool(valid), timeout=timeout, async_mode=False
        )
        return {
            "ok": True,
            "async_flush": False,
            "manifest": manifest,
            "finished_episode_index": ep,
            "finished_episode_path": str(ep_dir),
            "valid": bool(valid),
            **self.status(),
        }

    def _finalize_session(
        self,
        *,
        sess: _EpisodeSession,
        valid: bool,
        timeout: float,
        async_mode: bool,
    ) -> dict[str, Any]:
        sess.stop_event.set()
        if sess.sampler is not None:
            sess.sampler.join(timeout=5.0)
        try:
            sess.q.put(None, timeout=2.0)
        except queue.Full:
            try:
                sess.q.get_nowait()
            except queue.Empty:
                pass
            try:
                sess.q.put_nowait(None)
            except queue.Full:
                pass
        if sess.writer is not None:
            sess.writer.join(timeout=timeout)

        t1 = time.time()
        try:
            manifest = self._write_manifest(
                sess.ep_dir,
                ep=sess.ep,
                t_start=sess.t_start,
                t_end=t1,
                written=sess.written,
                dropped=sess.dropped,
                provisional=False,
                valid=bool(valid),
            )
            err = None
        except Exception as e:  # noqa: BLE001
            err = f"final manifest: {e}"
            manifest = {"error": str(e)}
            self._last_error = err

        with self._lock:
            if async_mode:
                job = self._flush_jobs.get(sess.ep)
                if job is not None:
                    job["state"] = "error" if err else "done"
                    job["error"] = err
                    job["written"] = sess.written
                    job["dropped"] = sess.dropped
                    # Keep briefly for UI, then drop done jobs after status read
                    if not err:
                        # retain done entry until next status poll cleans older ones
                        pass
            else:
                self.state = "idle"
                self._session = None
                self.episode_index = sess.ep + 1
                (self.save_dir / "NEXT_EPISODE").write_text(
                    str(self.episode_index) + "\n", encoding="utf-8"
                )
                if err:
                    self._last_error = err
            # Prune finished async jobs (keep last few for UI)
            done_eps = [
                k
                for k, v in self._flush_jobs.items()
                if v.get("state") in {"done", "error"} and k != sess.ep
            ]
            for k in done_eps[:-2]:
                self._flush_jobs.pop(k, None)
        self._notify()
        return manifest

    def _collect_cameras(self) -> dict[str, Any]:
        """Per-agent RealSense intrinsics captured at sensor open()."""
        cameras: dict[str, Any] = {}
        for a in self.agents.values():
            if a.kind != "realsense":
                continue
            info = None
            if hasattr(a, "camera_infos_dict"):
                info = a.camera_infos_dict()
            if not info:
                continue
            cameras[a.agent_id] = info
        return cameras

    def _write_manifest(
        self,
        ep_dir: Path,
        *,
        ep: int,
        t_start: float | None,
        t_end: float | None,
        written: int,
        dropped: int,
        provisional: bool,
        valid: bool = True,
    ) -> dict[str, Any]:
        cameras = self._collect_cameras()
        prev_path = ep_dir / "manifest.json"
        if prev_path.is_file():
            try:
                prev = json.loads(prev_path.read_text(encoding="utf-8"))
                for aid, info in (prev.get("cameras") or {}).items():
                    cameras.setdefault(aid, info)
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        agents_meta: list[dict[str, Any]] = []
        for a in self.agents.values():
            item: dict[str, Any] = {
                "agent_id": a.agent_id,
                "sensor_id": a.sensor_id,
                "kind": a.kind,
                "hz_target": a.hz,
            }
            if a.kind == "gello" and hasattr(a, "calib_dict"):
                item["gello_calib"] = a.calib_dict()
            agents_meta.append(item)

        duration = None
        if t_start is not None and t_end is not None:
            duration = float(t_end) - float(t_start)

        manifest_valid = False if provisional else bool(valid)

        manifest: dict[str, Any] = {
            "site": self.site,
            "episode_index": ep,
            "t_start": t_start,
            "t_end": t_end,
            "duration_s": duration,
            "written": written,
            "dropped": dropped,
            "agents": agents_meta,
            "cameras": cameras,
            "valid": manifest_valid,
            "format": "dcs_episode_v1",
        }
        if provisional:
            manifest["provisional"] = True
            manifest["note"] = (
                "Written at record start; cameras.* intrinsics from RealSense open()."
            )
        elif not manifest_valid:
            manifest["note"] = "Stopped via discard (作废); data kept on disk with valid=false."

        prev_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest

    def _notify(self) -> None:
        if self._on_status:
            try:
                self._on_status()
            except Exception:  # noqa: BLE001
                pass

    def _sampler_loop(self, sess: _EpisodeSession) -> None:
        last_seq: dict[str, int] = {}
        while not sess.stop_event.is_set():
            if not sess.accepting:
                break
            for aid, agent in self.agents.items():
                fr = agent.ring.latest.get()
                if fr is None or fr.error:
                    continue
                prev = last_seq.get(aid)
                if prev is not None and fr.seq <= prev:
                    continue
                last_seq[aid] = fr.seq
                try:
                    sess.q.put_nowait(fr)
                except queue.Full:
                    try:
                        sess.q.get_nowait()
                        sess.dropped += 1
                    except queue.Empty:
                        pass
                    try:
                        sess.q.put_nowait(fr)
                    except queue.Full:
                        sess.dropped += 1
            time.sleep(0.002)

    def _writer_loop(self, sess: _EpisodeSession) -> None:
        ep_dir = sess.ep_dir
        state_files: dict[str, Any] = {}
        cam_dirs: dict[str, Path] = {}
        cam_index: dict[str, Any] = {}

        def state_fp(aid: str):
            if aid not in state_files:
                p = ep_dir / "states" / f"{aid}.jsonl"
                state_files[aid] = p.open("a", encoding="utf-8")
            return state_files[aid]

        def cam_paths(aid: str) -> tuple[Path, Any]:
            if aid not in cam_dirs:
                d = ep_dir / "cameras" / aid
                d.mkdir(parents=True, exist_ok=True)
                cam_dirs[aid] = d
                cam_index[aid] = (d / "index.jsonl").open("a", encoding="utf-8")
            return cam_dirs[aid], cam_index[aid]

        try:
            while True:
                item = sess.q.get()
                if item is None:
                    break
                try:
                    self._write_frame(item, state_fp, cam_paths)
                    sess.written += 1
                except Exception as e:  # noqa: BLE001
                    sess.last_error = str(e)
        finally:
            for f in state_files.values():
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass
            for f in cam_index.values():
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass

    def _write_frame(
        self,
        fr: Frame,
        state_fp: Callable[[str], Any],
        cam_paths: Callable[[str], tuple[Path, Any]],
    ) -> None:
        if fr.kind == "realsense":
            jpeg_b64 = (fr.payload or {}).get("jpeg_b64")
            if not jpeg_b64:
                return
            cam_dir, idx_fp = cam_paths(fr.agent_id)
            name = f"{fr.seq:08d}.jpg"
            path = cam_dir / name
            path.write_bytes(base64.b64decode(jpeg_b64))
            depth_name = None
            depth_b64 = (fr.payload or {}).get("depth_png_b64")
            if depth_b64 and (fr.payload or {}).get("enable_depth"):
                depth_name = f"{fr.seq:08d}_depth.png"
                (cam_dir / depth_name).write_bytes(base64.b64decode(depth_b64))
            pl = fr.payload or {}
            rec = {
                "agent_id": fr.agent_id,
                "sensor_id": fr.sensor_id,
                "kind": fr.kind,
                "seq": fr.seq,
                "t_wall": fr.t_wall,
                "t_mono": fr.t_mono,
                "file": name,
                "depth_file": depth_name,
                "serial": pl.get("serial"),
                "role": pl.get("role"),
                "dry_run": pl.get("dry_run"),
                "width": pl.get("width"),
                "height": pl.get("height"),
                "color_shape": pl.get("color_shape"),
                "depth_shape": pl.get("depth_shape"),
                "enable_depth": pl.get("enable_depth"),
                "color_timestamp": pl.get("color_timestamp"),
                "depth_timestamp": pl.get("depth_timestamp"),
                "color_timestamp_domain": pl.get("color_timestamp_domain"),
                "depth_timestamp_domain": pl.get("depth_timestamp_domain"),
            }
            idx_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            idx_fp.flush()
            return

        payload = dict(fr.payload or {})
        payload.pop("jpeg_b64", None)
        payload.pop("jpeg_b64_preview", None)
        payload.pop("depth_png_b64", None)
        if isinstance(payload.get("last_result"), dict):
            lr = dict(payload["last_result"])
            payload["last_result"] = {
                k: lr[k]
                for k in (
                    "ok",
                    "error",
                    "armed",
                    "initialized",
                    "joints_rad",
                    "position_norm",
                    "position_raw",
                )
                if k in lr
            }
        rec = {
            "agent_id": fr.agent_id,
            "sensor_id": fr.sensor_id,
            "kind": fr.kind,
            "seq": fr.seq,
            "t_wall": fr.t_wall,
            "t_mono": fr.t_mono,
            "payload": payload,
        }
        fp = state_fp(fr.agent_id)
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fp.flush()
