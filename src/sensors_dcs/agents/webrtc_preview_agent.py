"""Optional WebRTC preview publisher — config presence enables the media plane.

Consumes RealSense (or other camera) agent rings. Does **not** feed episode
recording with media. Status/metrics frames go to ``ring`` for viz/console only.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Mapping

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class _WebrtcNullSensor:
    id = "webrtc-preview"

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> dict[str, Any]:
        return {}


class _RateMeter:
    """Simple EMA of events/sec from monotonic timestamps."""

    def __init__(self, *, alpha: float = 0.2) -> None:
        self._alpha = float(alpha)
        self._last_t: float | None = None
        self._ema: float | None = None
        self._count = 0

    def note(self, t_mono: float | None = None) -> None:
        now = time.perf_counter() if t_mono is None else float(t_mono)
        self._count += 1
        if self._last_t is not None:
            dt = now - self._last_t
            if dt > 1e-4:
                inst = 1.0 / dt
                self._ema = (
                    inst
                    if self._ema is None
                    else self._ema * (1.0 - self._alpha) + inst * self._alpha
                )
        self._last_t = now

    @property
    def hz(self) -> float | None:
        return self._ema

    @property
    def count(self) -> int:
        return self._count

    def reset(self) -> None:
        self._last_t = None
        self._ema = None
        self._count = 0


TELEMETRY_SCHEMA = "sensors-dcs.preview_telemetry.v1"
TELEMETRY_DC_LABEL = "sensors-preview"

_TELEMETRY_STRIP_KEYS = frozenset(
    {
        "jpeg_b64",
        "jpeg_b64_preview",
        "jpeg",
        "image",
        "rgb",
        "bgr",
        "color",
        "color_image",
        "depth",
        "depth_raw",
        "depth_frame",
        "depth_image",
        "pointcloud",
        "points",
        "vertices",
        "mesh",
        "raw",
        "bytes",
        "buffer",
        "frame",
        "frames",
    }
)
_TELEMETRY_MAX_STR = 256
_TELEMETRY_MAX_LIST = 128
_TELEMETRY_MAX_DEPTH = 5


def _sanitize_telemetry_value(value: Any, *, depth: int = 0) -> Any:
    """Drop bulky media blobs; keep scalars / short lists for preview HUD."""
    if depth > _TELEMETRY_MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _TELEMETRY_MAX_STR:
            return f"<str:{len(value)}>"
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<bytes:{len(value)}>"
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            if value.size > _TELEMETRY_MAX_LIST:
                return {"dtype": str(value.dtype), "shape": list(value.shape)}
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except Exception:  # noqa: BLE001
        pass
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key.lower() in _TELEMETRY_STRIP_KEYS:
                continue
            if key.lower().endswith(("_b64", "_base64", "_jpg", "_png")):
                continue
            cleaned = _sanitize_telemetry_value(v, depth=depth + 1)
            if cleaned is not None:
                out[key] = cleaned
        return out
    if isinstance(value, (list, tuple)):
        if len(value) > _TELEMETRY_MAX_LIST:
            head = [
                _sanitize_telemetry_value(v, depth=depth + 1)
                for v in value[:_TELEMETRY_MAX_LIST]
            ]
            return {"_truncated": True, "len": len(value), "head": head}
        return [_sanitize_telemetry_value(v, depth=depth + 1) for v in value]
    try:
        return str(value)[:_TELEMETRY_MAX_STR]
    except Exception:  # noqa: BLE001
        return None


class WebrtcPreviewAgent(BaseAgent):
    """YAML ``type: webrtc_preview`` — start preview WebRTC when listed in agents."""

    kind = "webrtc_preview"

    def __init__(
        self,
        *,
        agent_id: str,
        hz: float = 2.0,
        buffer_frames: int = 8,
        camera_agent_ids: list[str] | None = None,
        suppress_jpeg_ws: bool = True,
        max_bitrate_kbps: int = 2500,
        preferred_codec: str = "H264",
        backend: str = "auto",
        telemetry_enabled: bool = True,
        telemetry_hz: float = 15.0,
        telemetry_agent_ids: list[str] | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=_WebrtcNullSensor(),  # type: ignore[arg-type]
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self._camera_agent_ids = [str(x).strip() for x in (camera_agent_ids or []) if str(x).strip()]
        self._suppress_jpeg_ws = bool(suppress_jpeg_ws)
        self._max_bitrate_kbps = max(100, int(max_bitrate_kbps))
        self._preferred_codec = str(preferred_codec or "H264").strip() or "H264"
        backend_n = str(backend or "auto").strip().lower()
        if backend_n not in ("auto", "stub", "aiortc"):
            backend_n = "auto"
        self._backend_pref = backend_n
        self._telemetry_enabled = bool(telemetry_enabled)
        self._telemetry_hz = max(0.5, float(telemetry_hz or 15.0))
        self._telemetry_agent_ids = [
            str(x).strip() for x in (telemetry_agent_ids or []) if str(x).strip()
        ]

        self._peers: dict[str, BaseAgent] = {}
        self._lock = threading.RLock()
        self._resolved_backend = "stub"
        self._aiortc_available = False
        self._peer_count = 0
        self._bitrate_kbps_est: float | None = None
        self._last_error: str | None = None
        self._cam_seq: dict[str, int] = {}
        self._source_meter = _RateMeter()
        self._encode_meter = _RateMeter()

        # Browser-reported hooks (A/B)
        self._client_preview_fps: float | None = None
        self._client_glass_ms: float | None = None
        self._client_control_rtt_ms: float | None = None
        self._client_mode_label: str | None = None
        self._client_t_wall: float | None = None

        self._bench: dict[str, Any] | None = None

        self._detect_backend()

    def _detect_backend(self) -> None:
        has = False
        try:
            import aiortc  # noqa: F401

            has = True
        except Exception:  # noqa: BLE001
            has = False
        self._aiortc_available = has
        if self._backend_pref == "stub":
            self._resolved_backend = "stub"
        elif self._backend_pref == "aiortc":
            self._resolved_backend = "aiortc" if has else "missing"
            if not has:
                self._last_error = "aiortc not installed (pip install aiortc)"
        else:
            self._resolved_backend = "aiortc" if has else "stub"

    def bind_peers(self, agents: dict[str, BaseAgent]) -> None:
        self._peers = agents

    @property
    def suppress_jpeg_ws(self) -> bool:
        return self._suppress_jpeg_ws

    def camera_ids_for_suppress(self) -> set[str]:
        """Camera ids to strip from WS JPEG — only while a WebRTC peer is live.

        Avoids empty cam tiles when ``suppress_jpeg_ws`` is true but nobody has
        clicked「连接预览」yet.
        """
        if not self._suppress_jpeg_ws:
            return set()
        with self._lock:
            if self._peer_count <= 0:
                return set()
        return set(self._resolve_camera_ids())

    def _resolve_camera_ids(self) -> list[str]:
        if self._camera_agent_ids:
            return list(self._camera_agent_ids)
        out: list[str] = []
        for aid, ag in self._peers.items():
            if getattr(ag, "kind", None) == "realsense":
                out.append(aid)
        return out

    def _resolve_telemetry_ids(self) -> list[str]:
        """Non-camera sensor agents eligible for per-sensor DataChannel streams."""
        if self._telemetry_agent_ids:
            return list(self._telemetry_agent_ids)
        cam_set = set(self._resolve_camera_ids())
        out: list[str] = []
        for aid, ag in self._peers.items():
            if aid == self.agent_id or aid in cam_set:
                continue
            kind = str(getattr(ag, "kind", "") or "").strip().lower()
            if kind == "webrtc_preview":
                continue
            if kind in ("realsense", "camera", "rgb", "usb_cam", "usbcam", "opencv_cam"):
                continue
            if "realsense" in kind:
                continue
            out.append(aid)
        return out

    def build_telemetry_snapshot(
        self,
        *,
        agent_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Latest-wins preview telemetry for one or more agents (not for recording)."""
        ids = [str(x).strip() for x in (agent_ids or self._resolve_telemetry_ids()) if str(x).strip()]
        agents_out: dict[str, Any] = {}
        for aid in ids:
            ag = self._peers.get(aid)
            if ag is None:
                continue
            fr = None
            ring = getattr(ag, "ring", None)
            if ring is not None:
                latest = getattr(ring, "latest", None)
                if latest is not None and hasattr(latest, "get"):
                    fr = latest.get()
            if fr is None:
                continue
            payload = getattr(fr, "payload", None) or {}
            agents_out[aid] = {
                "kind": str(getattr(fr, "kind", None) or getattr(ag, "kind", "") or ""),
                "seq": int(getattr(fr, "seq", 0) or 0),
                "t_wall": float(getattr(fr, "t_wall", 0.0) or 0.0),
                "t_mono": float(getattr(fr, "t_mono", 0.0) or 0.0),
                "error": getattr(fr, "error", None),
                "payload": _sanitize_telemetry_value(payload),
            }
        primary = ids[0] if len(ids) == 1 else None
        return {
            "schema": TELEMETRY_SCHEMA,
            "t_wall": time.time(),
            "agent_id": primary,
            "agents": agents_out,
        }

    def note_encode_frame(self) -> None:
        """Call from media track when a frame is pushed to the encoder."""
        with self._lock:
            self._encode_meter.note()

    def set_peer_count(self, n: int) -> None:
        with self._lock:
            self._peer_count = max(0, int(n))

    def set_bitrate_kbps_est(self, kbps: float | None) -> None:
        with self._lock:
            self._bitrate_kbps_est = None if kbps is None else float(kbps)

    def note_client_metrics(
        self,
        *,
        preview_fps: float | None = None,
        glass_ms: float | None = None,
        control_rtt_ms: float | None = None,
        mode_label: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if preview_fps is not None:
                self._client_preview_fps = float(preview_fps)
            if glass_ms is not None:
                self._client_glass_ms = float(glass_ms)
            if control_rtt_ms is not None:
                self._client_control_rtt_ms = float(control_rtt_ms)
            if mode_label is not None:
                self._client_mode_label = str(mode_label)
            self._client_t_wall = time.time()
            if self._bench is not None and self._bench.get("active"):
                samples = self._bench.setdefault("client_samples", [])
                samples.append(
                    {
                        "t_wall": self._client_t_wall,
                        "preview_fps": self._client_preview_fps,
                        "glass_ms": self._client_glass_ms,
                        "control_rtt_ms": self._client_control_rtt_ms,
                        "mode_label": self._client_mode_label,
                    }
                )
            return {"ok": True, **self._hooks_locked()}

    def benchmark_start(
        self,
        *,
        duration_s: float = 30.0,
        mode_label: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._source_meter.reset()
            self._encode_meter.reset()
            label = mode_label or self._client_mode_label or (
                "webrtc" if self._resolved_backend == "aiortc" else "stub"
            )
            self._bench = {
                "active": True,
                "mode_label": label,
                "t_start": time.time(),
                "duration_s": float(duration_s),
                "client_samples": [],
                "hooks_start": self._hooks_locked(),
            }
            return {"ok": True, "benchmark": dict(self._bench)}

    def benchmark_stop(self) -> dict[str, Any]:
        with self._lock:
            if self._bench is None:
                return {"ok": False, "error": "no active benchmark"}
            self._bench["active"] = False
            self._bench["t_end"] = time.time()
            self._bench["hooks_end"] = self._hooks_locked()
            snap = dict(self._bench)
            self._bench = None
            return {"ok": True, "benchmark": snap}

    def _hooks_locked(self) -> dict[str, Any]:
        return {
            "backend": self._resolved_backend,
            "aiortc_available": self._aiortc_available,
            "suppress_jpeg_ws": self._suppress_jpeg_ws,
            "camera_agent_ids": self._resolve_camera_ids(),
            "telemetry_enabled": self._telemetry_enabled,
            "telemetry_hz": self._telemetry_hz,
            "telemetry_agent_ids": self._resolve_telemetry_ids(),
            "telemetry_data_channel": TELEMETRY_DC_LABEL,
            "source_fps": self._source_meter.hz,
            "encode_fps": self._encode_meter.hz,
            "peer_count": self._peer_count,
            "bitrate_kbps_est": self._bitrate_kbps_est,
            "max_bitrate_kbps": self._max_bitrate_kbps,
            "preferred_codec": self._preferred_codec,
            "client_preview_fps": self._client_preview_fps,
            "client_glass_ms": self._client_glass_ms,
            "client_control_rtt_ms": self._client_control_rtt_ms,
            "client_mode_label": self._client_mode_label,
            "client_t_wall": self._client_t_wall,
            "benchmark_active": bool(self._bench and self._bench.get("active")),
            "last_error": self._last_error,
        }

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": self._resolved_backend != "missing",
                "agent_id": self.agent_id,
                "kind": self.kind,
                "configured": True,
                **self._hooks_locked(),
            }

    async def handle_offer(
        self,
        *,
        sdp: str,
        camera_agent_id: str | None = None,
        telemetry_only: bool = False,
        telemetry_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """SDP offer → answer (aiortc) or structured stub error.

        Camera stream: ``camera_agent_id`` + VideoTrack.
        Sensor stream: ``telemetry_only`` + ``telemetry_agent_id`` + DataChannel.
        """
        cam_hint = (camera_agent_id or "").strip()
        tel_hint = (telemetry_agent_id or "").strip()
        if cam_hint.lower() in ("_telemetry", "telemetry"):
            telemetry_only = True
            cam_hint = ""
        if tel_hint and not telemetry_only:
            # Catalog sensor stream without explicit flag.
            if tel_hint not in set(self._resolve_camera_ids()):
                telemetry_only = True

        cams = self._resolve_camera_ids()
        cam: str | None = None
        tel_aid: str | None = tel_hint or None
        if telemetry_only:
            if not self._telemetry_enabled:
                return {"ok": False, "error": "telemetry_enabled is false"}
            if not tel_aid:
                # Fall back: treat camera_agent_id slot as sensor id (catalog agent_id).
                tel_aid = cam_hint or None
            if not tel_aid:
                return {"ok": False, "error": "telemetry_agent_id required"}
        else:
            cam = cam_hint or (cams[0] if cams else None)
            if not cam:
                return {"ok": False, "error": "no camera_agent_id and no realsense peers"}
            if cam not in cams and self._camera_agent_ids:
                return {"ok": False, "error": f"camera {cam!r} not in camera_agent_ids"}

        with self._lock:
            backend = self._resolved_backend
            err = self._last_error
        if backend == "missing":
            return {
                "ok": False,
                "error": err or "aiortc missing",
                "backend": backend,
            }
        if backend == "stub":
            return {
                "ok": False,
                "error": (
                    "webrtc backend=stub. Install aiortc "
                    '(pip install -e ".[webrtc]") and set backend: aiortc|auto.'
                ),
                "backend": backend,
                "camera_agent_id": cam,
                "telemetry_only": bool(telemetry_only),
                "telemetry_agent_id": tel_aid,
                "hooks": self.status_payload(),
            }
        from sensors_dcs.webrtc_preview_media import answer_offer

        try:
            return await answer_offer(
                self,
                sdp=sdp,
                camera_agent_id=cam,
                telemetry_only=bool(telemetry_only),
                telemetry_agent_id=tel_aid,
            )
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._last_error = str(e)
            return {
                "ok": False,
                "error": f"aiortc offer failed: {e}",
                "backend": "aiortc",
                "camera_agent_id": cam,
                "telemetry_only": bool(telemetry_only),
                "telemetry_agent_id": tel_aid,
            }

    def stop(self, *, join_timeout: float = 2.0, close_sensor: bool = True) -> None:
        try:
            from sensors_dcs.webrtc_preview_media import close_all

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(close_all(self), loop)
                try:
                    fut.result(timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass
            else:
                asyncio.run(close_all(self))
        except Exception:  # noqa: BLE001
            pass
        super().stop(join_timeout=join_timeout, close_sensor=close_sensor)

    def _poll_sources(self) -> None:
        for aid in self._resolve_camera_ids():
            ag = self._peers.get(aid)
            if ag is None:
                continue
            fr = ag.ring.latest.get()
            if fr is None:
                continue
            seq = int(fr.seq)
            prev = self._cam_seq.get(aid)
            if prev is None or seq > prev:
                self._cam_seq[aid] = seq
                self._source_meter.note()

    def read_frame(self) -> Frame:
        self._poll_sources()
        t_wall = time.time()
        t_mono = time.perf_counter()
        self._seq += 1
        with self._lock:
            payload = {
                "webrtc_preview": True,
                **self._hooks_locked(),
            }
            err = self._last_error if self._resolved_backend == "missing" else None
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            error=err,
        )
