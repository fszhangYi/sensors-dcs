from __future__ import annotations

import base64
import json
import queue
import threading
import time
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


class RecordController:
    """Sensors stay on; this only starts/stops a disk write consumer."""

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
        self.state = "idle"  # idle | recording | flushing
        self._accepting = False
        self._q: queue.Queue[Frame | None] | None = None
        self._writer: threading.Thread | None = None
        self._sampler: threading.Thread | None = None
        self._episode_path: Path | None = None
        self._t_start: float | None = None
        self._written = 0
        self._dropped = 0
        self._last_error: str | None = None
        self._stop_event = threading.Event()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "save_dir": str(self.save_dir),
                "episode_index": self.episode_index,
                "episode_path": str(self._episode_path) if self._episode_path else None,
                "accepting": self._accepting,
                "written": self._written,
                "dropped": self._dropped,
                "queue_size": self._q.qsize() if self._q is not None else 0,
                "last_error": self._last_error,
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state != "idle":
                return {"ok": False, "error": f"cannot start while state={self.state}", **self.status()}
            ep = self.episode_index
            ep_dir = _episode_dir(self.save_dir, ep)
            if ep_dir.exists():
                return {"ok": False, "error": f"episode dir already exists: {ep_dir}", **self.status()}
            ep_dir.mkdir(parents=True, exist_ok=False)
            (ep_dir / "states").mkdir()
            (ep_dir / "cameras").mkdir()
            self._episode_path = ep_dir
            self._t_start = time.time()
            self._written = 0
            self._dropped = 0
            self._last_error = None
            self._q = queue.Queue(maxsize=max(8, int(self.cfg.queue_maxsize)))
            self._accepting = True
            self.state = "recording"
            self._stop_event.clear()
            self._writer = threading.Thread(
                target=self._writer_loop,
                name=f"record-writer-ep{ep}",
                daemon=True,
            )
            self._sampler = threading.Thread(
                target=self._sampler_loop,
                name=f"record-sampler-ep{ep}",
                daemon=True,
            )
            self._writer.start()
            self._sampler.start()
        self._notify()
        return {"ok": True, **self.status()}

    def stop(self, *, timeout: float = 120.0) -> dict[str, Any]:
        """Stop accepting new frames, drain queue to disk, then bump episode."""
        with self._lock:
            if self.state != "recording":
                return {"ok": False, "error": f"cannot stop while state={self.state}", **self.status()}
            self._accepting = False
            self.state = "flushing"
            q = self._q
            writer = self._writer
            sampler = self._sampler
            ep_dir = self._episode_path
            ep = self.episode_index
            t0 = self._t_start
        self._notify()

        self._stop_event.set()
        if sampler is not None:
            sampler.join(timeout=5.0)
        if q is not None:
            try:
                q.put(None, timeout=2.0)
            except queue.Full:
                # force room for sentinel
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
        if writer is not None:
            writer.join(timeout=timeout)

        t1 = time.time()
        manifest = {
            "site": self.site,
            "episode_index": ep,
            "t_start": t0,
            "t_end": t1,
            "duration_s": (t1 - t0) if t0 else None,
            "written": self._written,
            "dropped": self._dropped,
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "sensor_id": a.sensor_id,
                    "kind": a.kind,
                    "hz_target": a.hz,
                }
                for a in self.agents.values()
            ],
            "valid": True,
            "format": "dcs_episode_v1",
        }
        if ep_dir is not None:
            (ep_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        with self._lock:
            self.state = "idle"
            self._q = None
            self._writer = None
            self._sampler = None
            self._episode_path = None
            self._t_start = None
            self.episode_index = ep + 1
            # persist next index hint
            (self.save_dir / "NEXT_EPISODE").write_text(
                str(self.episode_index) + "\n", encoding="utf-8"
            )
        self._notify()
        return {"ok": True, "manifest": manifest, **self.status()}

    def _notify(self) -> None:
        if self._on_status:
            try:
                self._on_status()
            except Exception:  # noqa: BLE001
                pass

    def _sampler_loop(self) -> None:
        last_seq: dict[str, int] = {}
        while not self._stop_event.is_set():
            if not self._accepting:
                break
            q = self._q
            if q is None:
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
                    q.put_nowait(fr)
                except queue.Full:
                    # drop_oldest then retry once
                    try:
                        q.get_nowait()
                        self._dropped += 1
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(fr)
                    except queue.Full:
                        self._dropped += 1
            time.sleep(0.002)

    def _writer_loop(self) -> None:
        assert self._q is not None and self._episode_path is not None
        ep_dir = self._episode_path
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
                item = self._q.get()
                if item is None:
                    break
                try:
                    self._write_frame(item, state_fp, cam_paths)
                    self._written += 1
                except Exception as e:  # noqa: BLE001
                    self._last_error = str(e)
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
            rec = {
                "agent_id": fr.agent_id,
                "sensor_id": fr.sensor_id,
                "kind": fr.kind,
                "seq": fr.seq,
                "t_wall": fr.t_wall,
                "t_mono": fr.t_mono,
                "file": name,
                "serial": (fr.payload or {}).get("serial"),
                "role": (fr.payload or {}).get("role"),
                "dry_run": (fr.payload or {}).get("dry_run"),
            }
            idx_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            idx_fp.flush()
            return

        # gello / gripper_read / other state
        payload = dict(fr.payload or {})
        # strip heavy fields if any
        payload.pop("jpeg_b64", None)
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
