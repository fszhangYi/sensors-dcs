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


def _jpeg_b64(bgr: np.ndarray, *, quality: int = 70, max_width: int = 640) -> str:
    """Encode BGR image to JPEG base64 for WebSocket viz (no raw ndarray in JSON)."""
    import cv2

    img = bgr
    h, w = img.shape[:2]
    if w > max_width:
        scale = max_width / float(w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


class RealSenseAgent(BaseAgent):
    """Agent for hik-sensors ``kind=realsense`` — color preview via JPEG."""

    kind = "realsense"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 15.0,
        buffer_frames: int = 2,
        dry_run_synth: bool = True,
        jpeg_quality: int = 70,
        viz_max_width: int = 640,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self.dry_run_synth = dry_run_synth
        self.jpeg_quality = jpeg_quality
        self.viz_max_width = viz_max_width

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
        shape = None
        if color is not None:
            arr = np.asarray(color)
            shape = list(arr.shape)
            jpeg_b64 = _jpeg_b64(
                arr,
                quality=self.jpeg_quality,
                max_width=self.viz_max_width,
            )

        payload: dict[str, Any] = {
            "serial": sample.get("serial") or getattr(self.sensor, "serial", None),
            "role": sample.get("role") or getattr(self.sensor, "role", None),
            "width": sample.get("width"),
            "height": sample.get("height"),
            "fps": sample.get("fps"),
            "color_shape": shape,
            "jpeg_b64": jpeg_b64,
            "dry_run": dry,
            "synth": bool(sample.get("synth")),
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
        w = min(w, 640)
        h = min(h, 480)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        sn = str(getattr(self.sensor, "serial", "") or "")
        # Offset phase by serial so multi-camera dry-run previews look distinct
        phase = int((t_wall * 40 + (hash(sn) % 360)) % w)
        for x in range(w):
            v = int(40 + 180 * abs(((x + phase) % w) / max(w - 1, 1) - 0.5) * 2)
            img[:, x, 0] = v // 3
            img[:, x, 1] = v
            img[:, x, 2] = 255 - v // 2
        try:
            import cv2

            role = str(getattr(self.sensor, "role", "") or "")
            label = f"RS {role} {sn or 'dry-run'}".strip()
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
