from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.buffer import FrameRing
from sensors_dcs.frame import Frame


class BaseAgent(ABC):
    """Background reader that wraps a hik-sensors Sensor."""

    kind: str = "base"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float,
        buffer_frames: int = 64,
    ) -> None:
        self.agent_id = agent_id
        self.sensor = sensor
        self.hz = float(hz)
        self.ring = FrameRing(maxlen=buffer_frames)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._error_count = 0
        self._last_error: str | None = None
        self._ok_count = 0
        self._last_ok_mono: float | None = None
        self._hz_ema: float | None = None

    @property
    def sensor_id(self) -> str:
        return self.sensor.id

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.sensor.open()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"agent-{self.agent_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        try:
            self.sensor.close()
        except Exception:  # noqa: BLE001
            pass

    def stats(self) -> dict[str, Any]:
        latest = self.ring.latest.get()
        return {
            "agent_id": self.agent_id,
            "sensor_id": self.sensor_id,
            "kind": self.kind,
            "hz_target": self.hz,
            "hz_meas": self._hz_ema,
            "ok_count": self._ok_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "latest_seq": latest.seq if latest else None,
            "latest_t_wall": latest.t_wall if latest else None,
        }

    def _loop(self) -> None:
        period = 1.0 / self.hz
        next_t = time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            if now < next_t:
                time.sleep(min(0.002, next_t - now))
                continue
            next_t = max(next_t + period, time.perf_counter())
            try:
                frame = self.read_frame()
                self._ok_count += 1
                self._last_error = None
                now_ok = time.perf_counter()
                if self._last_ok_mono is not None:
                    dt = now_ok - self._last_ok_mono
                    if dt > 1e-4:
                        inst = 1.0 / dt
                        self._hz_ema = (
                            inst if self._hz_ema is None else self._hz_ema * 0.8 + inst * 0.2
                        )
                self._last_ok_mono = now_ok
            except Exception as e:  # noqa: BLE001
                self._error_count += 1
                self._last_error = str(e)
                self._seq += 1
                frame = Frame(
                    sensor_id=self.sensor_id,
                    agent_id=self.agent_id,
                    kind=self.kind,
                    t_wall=time.time(),
                    t_mono=time.perf_counter(),
                    payload={},
                    seq=self._seq,
                    error=str(e),
                )
            self.ring.push(frame)

    @abstractmethod
    def read_frame(self) -> Frame:
        ...
