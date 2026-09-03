from __future__ import annotations

import base64
import time
from typing import Any

import numpy as np

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


def _jpeg_b64(
    bgr: np.ndarray,
    *,
    quality: int = 70,
    max_width: int | None = 640,
) -> str:
    """Encode BGR image to JPEG base64.

    ``max_width=None`` keeps native resolution (for episode write).
    A finite ``max_width`` downscales for WebSocket preview only.
    """
    import cv2

    img = bgr
    h, w = img.shape[:2]
    if max_width is not None and w > max_width:
        scale = max_width / float(w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _depth_png_b64(depth: np.ndarray) -> str:
    """Encode uint16 (or convertible) depth map to PNG base64."""
    import cv2

    arr = np.asarray(depth)
    if arr.dtype != np.uint16:
        arr = np.clip(arr, 0, 65535).astype(np.uint16)
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise RuntimeError("depth png encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


class RealSenseAgent(BaseAgent):
    """Agent for hik-sensors ``kind=realsense`` — color (+ optional depth) via JPEG/PNG."""

    kind = "realsense"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 15.0,
        buffer_frames: int = 2,
        dry_run_synth: bool = True,
        jpeg_quality: int = 90,
        viz_jpeg_quality: int = 70,
        viz_max_width: int = 427,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self.dry_run_synth = dry_run_synth
        self.jpeg_quality = jpeg_quality
        self.viz_jpeg_quality = viz_jpeg_quality
        self.viz_max_width = viz_max_width

    def camera_infos_dict(self) -> dict[str, Any] | None:
        """Color/depth intrinsics captured when the RealSense pipeline opened."""
        sensor = self.sensor
        if hasattr(sensor, "get_camera_infos"):
            info = sensor.get_camera_infos()
            if info:
                return dict(info)
        infos = getattr(sensor, "camera_infos", None)
        return dict(infos) if isinstance(infos, dict) else None

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        color = sample.get("color")
        dry = bool(sample.get("dry_run")) or color is None
        if dry and self.dry_run_synth:
            color = self._synth_color(t_wall)
            sample = {
                **sample,
                "color": color,
                "depth": None,
                "dry_run": True,
                "synth": True,
            }

        jpeg_b64 = None
        jpeg_b64_preview = None
        depth_png_b64 = None
        shape = None
        depth_shape = None
        if color is not None:
            arr = np.asarray(color)
            shape = list(arr.shape)
            # Full-resolution JPEG for episode write (matches sensor width/height).
            jpeg_b64 = _jpeg_b64(
                arr,
                quality=self.jpeg_quality,
                max_width=None,
            )
            # Downscaled copy only for WebSocket viz (keeps WS light).
            jpeg_b64_preview = _jpeg_b64(
                arr,
                quality=self.viz_jpeg_quality,
                max_width=self.viz_max_width,
            )

        depth = sample.get("depth")
        enable_depth = bool(
            sample.get("enable_depth")
            if sample.get("enable_depth") is not None
            else getattr(self.sensor, "enable_depth", False)
        )
        if enable_depth and depth is not None and not dry:
            try:
                darr = np.asarray(depth)
                depth_shape = list(darr.shape)
                depth_png_b64 = _depth_png_b64(darr)
            except Exception:  # noqa: BLE001
                depth_png_b64 = None
                depth_shape = None

        payload: dict[str, Any] = {
            "serial": sample.get("serial") or getattr(self.sensor, "serial", None),
            "role": sample.get("role") or getattr(self.sensor, "role", None),
            "width": sample.get("width") or (shape[1] if shape and len(shape) >= 2 else None),
            "height": sample.get("height") or (shape[0] if shape else None),
            "fps": sample.get("fps"),
            "color_shape": shape,
            "jpeg_b64": jpeg_b64,
            "jpeg_b64_preview": jpeg_b64_preview,
            "enable_depth": enable_depth,
            "depth_shape": depth_shape,
            "depth_png_b64": depth_png_b64,
            "dry_run": dry,
            "synth": bool(sample.get("synth")),
            # Persisted for audit / future align; timeline still keys on t_wall.
            "color_timestamp": sample.get("color_timestamp"),
            "depth_timestamp": sample.get("depth_timestamp"),
            "color_timestamp_domain": sample.get("color_timestamp_domain"),
            "depth_timestamp_domain": sample.get("depth_timestamp_domain"),
        }
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
        )

    def _synth_color(self, t_wall: float) -> np.ndarray:
        """Moving color bars so dry-run viz looks alive without a camera."""
        w = int(getattr(self.sensor, "width", 640) or 640)
        h = int(getattr(self.sensor, "height", 480) or 480)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        sn = str(getattr(self.sensor, "serial", "") or "")
        # Offset phase by serial so multi-camera dry-run previews look distinct
        phase = int((t_wall * 40 + (hash(sn) % 360)) % max(w, 1))
        for x in range(w):
            v = int(40 + 180 * abs(((x + phase) % w) / max(w - 1, 1) - 0.5) * 2)
            img[:, x, 0] = v // 3
            img[:, x, 1] = v
            img[:, x, 2] = 255 - v // 2
        try:
            import cv2

            role = str(getattr(self.sensor, "role", "") or "")
            label = f"RS {role} {sn or 'dry-run'} {self.hz:.0f}Hz".strip()
            cv2.putText(
                img,
                label[:48],
                (12, 36),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (240, 240, 240),
                2,
                cv2.LINE_AA,
            )
        except Exception:  # noqa: BLE001
            pass
        return img
