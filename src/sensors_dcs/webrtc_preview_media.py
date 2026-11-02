"""aiortc media plane for ``WebrtcPreviewAgent`` (Phase B + E).

Pulls latest JPEG from a camera agent ring, decodes with OpenCV, feeds a
``VideoStreamTrack``. Optionally opens a best-effort DataChannel
(``sensors-preview``) with latest-wins telemetry for all peer sensors.
Signaling stays on FastAPI; this module owns PeerConnections.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sensors_dcs.agents.webrtc_preview_agent import WebrtcPreviewAgent

log = logging.getLogger(__name__)

_PCS: set[Any] = set()
_PC_LOCK = asyncio.Lock()
_TELEMETRY_TASKS: dict[Any, asyncio.Task[Any]] = {}


def aiortc_available() -> bool:
    try:
        import aiortc  # noqa: F401
        import av  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _decode_jpeg_b64(b64: str) -> np.ndarray | None:
    if not b64:
        return None
    try:
        import cv2

        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:  # noqa: BLE001
        return None


def _latest_bgr(agent: WebrtcPreviewAgent, camera_agent_id: str) -> np.ndarray | None:
    cam = agent._peers.get(camera_agent_id)
    if cam is None:
        return None
    fr = cam.ring.latest.get()
    if fr is None:
        return None
    pl = fr.payload or {}
    b64 = pl.get("jpeg_b64") or pl.get("jpeg_b64_preview")
    if not isinstance(b64, str):
        return None
    return _decode_jpeg_b64(b64)


def _build_track(agent: WebrtcPreviewAgent, camera_agent_id: str, *, fps: float = 15.0):
    from aiortc import VideoStreamTrack
    from aiortc.mediastreams import VIDEO_CLOCK_RATE, VIDEO_TIME_BASE
    import av

    class CameraJpegTrack(VideoStreamTrack):
        kind = "video"

        def __init__(self) -> None:
            super().__init__()
            self._fps = max(1.0, float(fps))
            self._timestamp = 0
            self._placeholder = np.zeros((240, 320, 3), dtype=np.uint8)

        async def recv(self):  # noqa: ANN201
            img = await asyncio.to_thread(_latest_bgr, agent, camera_agent_id)
            if img is None:
                img = self._placeholder
            else:
                agent.note_encode_frame()
            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            self._timestamp += int(VIDEO_CLOCK_RATE / self._fps)
            frame.pts = self._timestamp
            frame.time_base = VIDEO_TIME_BASE
            await asyncio.sleep(1.0 / self._fps)
            return frame

    return CameraJpegTrack()


async def _telemetry_send_loop(
    pc: Any,
    channel: Any,
    agent: WebrtcPreviewAgent,
    *,
    agent_ids: list[str] | None = None,
) -> None:
    hz = max(0.5, float(getattr(agent, "_telemetry_hz", 15.0) or 15.0))
    period = 1.0 / hz
    ids = [str(x).strip() for x in (agent_ids or []) if str(x).strip()] or None
    try:
        while pc in _PCS and getattr(channel, "readyState", "") != "closed":
            if getattr(channel, "readyState", "") == "open":
                try:
                    snap = await asyncio.to_thread(
                        lambda: agent.build_telemetry_snapshot(agent_ids=ids)
                    )
                    raw = json.dumps(snap, ensure_ascii=True, default=str, separators=(",", ":"))
                    channel.send(raw)
                except Exception:  # noqa: BLE001
                    log.debug("telemetry send failed", exc_info=True)
            await asyncio.sleep(period)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.debug("telemetry loop ended", exc_info=True)


def _start_telemetry_task(
    pc: Any,
    channel: Any,
    agent: WebrtcPreviewAgent,
    *,
    agent_ids: list[str] | None = None,
) -> None:
    prev = _TELEMETRY_TASKS.pop(pc, None)
    if prev is not None and not prev.done():
        prev.cancel()
    _TELEMETRY_TASKS[pc] = asyncio.create_task(
        _telemetry_send_loop(pc, channel, agent, agent_ids=agent_ids),
        name="webrtc-preview-telemetry",
    )


async def answer_offer(
    agent: WebrtcPreviewAgent,
    *,
    sdp: str,
    camera_agent_id: str | None = None,
    telemetry_only: bool = False,
    telemetry_agent_id: str | None = None,
) -> dict[str, Any]:
    """Answer a browser offer.

    - Camera: VideoTrack for ``camera_agent_id``.
    - Sensor: ``telemetry_only`` + ``telemetry_agent_id`` → one DataChannel peer
      for that agent only (equal catalog stream to cameras).
    """
    if not aiortc_available():
        return {"ok": False, "error": "aiortc not installed", "backend": "missing"}

    from aiortc import RTCPeerConnection, RTCSessionDescription
    from sensors_dcs.agents.webrtc_preview_agent import TELEMETRY_DC_LABEL

    telemetry_on = bool(getattr(agent, "_telemetry_enabled", True))
    tel_aid = (telemetry_agent_id or "").strip() or None
    if telemetry_only and not telemetry_on:
        return {"ok": False, "error": "telemetry_enabled is false", "backend": "aiortc"}
    if telemetry_only and not tel_aid:
        return {
            "ok": False,
            "error": "telemetry_agent_id required for telemetry_only offer",
            "backend": "aiortc",
        }
    if telemetry_only and tel_aid:
        allowed = set(agent._resolve_telemetry_ids())
        # Allow explicit id even if not yet in auto list (peer may exist).
        if tel_aid not in allowed and tel_aid not in agent._peers:
            return {
                "ok": False,
                "error": f"telemetry agent {tel_aid!r} not found",
                "backend": "aiortc",
            }

    cam: str | None = None
    if not telemetry_only:
        cams = agent._resolve_camera_ids()
        cam = (camera_agent_id or "").strip() or None
        if not cam:
            return {"ok": False, "error": "camera_agent_id required unless telemetry_only"}
        if cam not in cams and agent._camera_agent_ids:
            return {"ok": False, "error": f"camera {cam!r} not subscribed"}

    pc = RTCPeerConnection()
    async with _PC_LOCK:
        _PCS.add(pc)
        agent.set_peer_count(len(_PCS))

    @pc.on("connectionstatechange")
    async def _on_state() -> None:
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await _discard_pc(pc, agent)

    if cam is not None:
        pc.addTrack(_build_track(agent, cam, fps=15.0))

    # Per-sensor DC only (not bolted onto camera peers).
    if telemetry_only and tel_aid:

        @pc.on("datachannel")
        def _on_datachannel(channel: Any) -> None:
            label = str(getattr(channel, "label", "") or "")
            if label and label != TELEMETRY_DC_LABEL:
                return
            log.info(
                "telemetry DataChannel accepted agent=%s label=%s",
                tel_aid,
                label or TELEMETRY_DC_LABEL,
            )
            _start_telemetry_task(pc, channel, agent, agent_ids=[tel_aid])

    offer = RTCSessionDescription(sdp=sdp, type="offer")
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    local = pc.localDescription
    if local is None:
        await _discard_pc(pc, agent)
        return {"ok": False, "error": "no local description", "backend": "aiortc"}

    return {
        "ok": True,
        "backend": "aiortc",
        "camera_agent_id": cam,
        "telemetry_only": bool(telemetry_only),
        "telemetry_agent_id": tel_aid if telemetry_only else None,
        "type": local.type,
        "sdp": local.sdp,
        "peer_count": len(_PCS),
        "telemetry_enabled": telemetry_on,
        "telemetry_data_channel": TELEMETRY_DC_LABEL if telemetry_only else None,
        "telemetry_hz": float(getattr(agent, "_telemetry_hz", 15.0) or 15.0),
    }


async def _discard_pc(pc: Any, agent: WebrtcPreviewAgent) -> None:
    task = _TELEMETRY_TASKS.pop(pc, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    async with _PC_LOCK:
        _PCS.discard(pc)
        agent.set_peer_count(len(_PCS))
    try:
        await pc.close()
    except Exception:  # noqa: BLE001
        log.debug("pc close failed", exc_info=True)


async def close_all(agent: WebrtcPreviewAgent | None = None) -> None:
    async with _PC_LOCK:
        pcs = list(_PCS)
        _PCS.clear()
        if agent is not None:
            agent.set_peer_count(0)
    for pc in pcs:
        task = _TELEMETRY_TASKS.pop(pc, None)
        if task is not None and not task.done():
            task.cancel()
        try:
            await pc.close()
        except Exception:  # noqa: BLE001
            pass
