from __future__ import annotations

import math
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class GripperReadAgent(BaseAgent):
    """Read-only agent for hik-sensors gripper kinds (DH AG95 / 慧灵 Z-EFG-C65).

    Control-layer separation: this agent only calls ``sensor.read()`` and never
    ``write()``. Share the same ``sensor_id`` with a write agent — do not open a
    second Serial on the same port.
    """

    kind = "gripper_read"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 50.0,
        buffer_frames: int = 64,
        dry_run_synth: bool = True,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self.dry_run_synth = dry_run_synth
        self._last_good: dict[str, Any] | None = None

    def read_frame(self) -> Frame:
        t_wall = time.time()
        t_mono = time.perf_counter()
        self._seq += 1
        sample: dict[str, Any]
        bus_busy = False
        # If init/write holds the Modbus lock, skip this tick (do not block for seconds).
        # Hold the lock across read() so init cannot sneak in after a probe-release
        # and park this thread for the whole initialize() timeout.
        lock = getattr(self.sensor, "_io_lock", None)
        if lock is not None and hasattr(lock, "acquire"):
            got = False
            try:
                got = bool(lock.acquire(blocking=False))
            except Exception:  # noqa: BLE001
                got = False
            if got:
                try:
                    sample = dict(self.sensor.read())
                    t_wall = float(sample.get("ts") or t_wall)
                finally:
                    try:
                        lock.release()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                bus_busy = True
                sample = {"ok": False, "error": "bus busy (init/write in progress)", "dry_run": False}
        else:
            sample = dict(self.sensor.read())
            t_wall = float(sample.get("ts") or t_wall)

        pos = sample.get("position_norm")
        raw = sample.get("raw_value")
        if raw is None:
            raw = sample.get("position_raw")
        ctx_dry = bool(getattr(getattr(self.sensor, "ctx", None), "dry_run", False))
        # Fake mode may return null until first command — do not synth over that.
        if sample.get("fake") and sample.get("position_source") == "last_command":
            dry = bool(sample.get("dry_run")) or ctx_dry
        else:
            # Live Modbus miss must NOT be labeled dry_run (was: pos is None → synth).
            dry = bool(sample.get("dry_run")) or ctx_dry
        synth = False
        stale = False
        if dry and self.dry_run_synth and not sample.get("fake"):
            pos = self._synth_position(t_wall)
            # Inverse of (1000 - raw) * 0.000637 for viz-only synth
            raw = int(round(1000.0 - float(pos) / 0.000637))
            sample = {
                **sample,
                "position_norm": pos,
                "position_raw": raw,
                "raw_value": raw,
                "dry_run": True,
                "synth": True,
            }
            synth = True
        elif not dry and pos is not None:
            self._last_good = {
                "position_norm": pos,
                "raw_value": raw,
                "position_mm": sample.get("position_mm"),
                "speed_mm_s": sample.get("speed_mm_s"),
                "current_a": sample.get("current_a"),
                "init_code": sample.get("init_code"),
                "grip_code": sample.get("grip_code"),
                "init_text": sample.get("init_text"),
                "grip_text": sample.get("grip_text"),
                "initialized": sample.get("initialized"),
            }
        elif not dry and pos is None and self._last_good is not None:
            # Serial contention / bus busy: keep last good sample.
            g = self._last_good
            pos = g.get("position_norm")
            raw = g.get("raw_value")
            for k, v in g.items():
                sample.setdefault(k, v)
            stale = True
            if not sample.get("error"):
                sample["error"] = (
                    "bus busy (holding last good)"
                    if bus_busy
                    else "status read failed (holding last good)"
                )

        payload: dict[str, Any] = {
            "position_norm": pos,
            "raw_value": raw,
            "position_raw": raw,  # alias of raw_value (Modbus ticks before norm)
            # 慧灵 Z-EFG-C65 (kind=gripper_huiling) also exposes mm / status codes
            "position_mm": sample.get("position_mm"),
            "speed_mm_s": sample.get("speed_mm_s"),
            "current_a": sample.get("current_a"),
            "init_code": sample.get("init_code"),
            "grip_code": sample.get("grip_code"),
            "init_text": sample.get("init_text"),
            "grip_text": sample.get("grip_text"),
            "initialized": sample.get("initialized"),
            "init_state": sample.get("init_state"),
            "fault": sample.get("fault"),
            "read_ms": sample.get("read_ms"),
            "port": getattr(self.sensor, "port", None) or sample.get("port"),
            "dry_run": dry,
            "synth": synth or bool(sample.get("synth")),
            "stale": stale,
            "fake": bool(sample.get("fake")),
            "position_source": sample.get("position_source"),
            "command_t_wall": sample.get("command_t_wall"),
            "error": sample.get("error"),
        }
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            error=str(sample["error"]) if sample.get("error") and not dry else None,
        )

    def _synth_position(self, t_wall: float) -> float:
        """Oscillate in DH AG95 norm range (~0 .. 0.637) for dry-run viz."""
        return 0.3185 * (1.0 + math.sin(t_wall * 0.9))
