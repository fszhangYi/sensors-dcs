from __future__ import annotations

import threading
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class GripperWriteAgent(BaseAgent):
    """Command gripper via ``sensor.write()`` (DH AG95 / 慧灵); no Modbus poll loop.

    Share the same ``sensor_id`` as ``gripper_read``. UI may post
    ``position_norm`` / ``position_raw`` (DH) or ``position_mm`` (慧灵);
    this agent holds the last command for viz and calls write once per request.
    """

    kind = "gripper_write"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 5.0,
        buffer_frames: int = 8,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self._cmd_lock = threading.Lock()
        self._last_cmd: dict[str, Any] = {
            "position_norm": None,
            "position_raw": None,
            "ok": None,
            "error": None,
            "t_wall": None,
            "result": None,
        }

    def stop(self, *, join_timeout: float = 2.0, close_sensor: bool = True) -> None:
        """Stop loop only — do not close shared gripper sensor (read agent owns lifecycle)."""
        del close_sensor
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.05, float(join_timeout)))
        self._thread = None

    def command(
        self,
        *,
        position_norm: float | None = None,
        position_raw: int | None = None,
        position_mm: float | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        """Initialize and/or send one absolute gripper target.

        DH AG95: prefer ``position_norm`` (0..~0.637). 慧灵: prefer ``position_mm``
        (0..65) or ``position_norm`` (driver maps to mm).
        """
        if initialize:
            body: dict[str, Any] = {"initialize": True}
        elif position_norm is None and position_raw is None and position_mm is None:
            return {
                "ok": False,
                "error": "position_norm, position_raw, position_mm, or initialize required",
            }
        else:
            body = {}
            if position_norm is not None:
                body["position_norm"] = float(position_norm)
            if position_raw is not None:
                body["position_raw"] = int(position_raw)
            if position_mm is not None:
                body["position_mm"] = float(position_mm)
        try:
            result = dict(self.sensor.write(body))
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)}
        with self._cmd_lock:
            self._last_cmd = {
                "position_norm": body.get("position_norm", result.get("position_norm")),
                "position_raw": body.get("position_raw", result.get("position_raw")),
                "position_mm": body.get("position_mm", result.get("position_mm")),
                "initialized": result.get("initialized"),
                "init_code": result.get("init_code"),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "t_wall": time.time(),
                "result": result,
            }
            snap = dict(self._last_cmd)
        # Push a frame immediately so viz updates without waiting for hz.
        try:
            self.ring.push(self._frame_from_cmd(snap))
        except Exception:  # noqa: BLE001
            pass
        out = {
            "ok": bool(snap.get("ok")),
            **{k: snap[k] for k in snap if k != "result"},
            "result": result,
        }
        # Promote driver fields so the UI can show init_code without digging into result.
        if result.get("init_code") is not None:
            out["init_code"] = result.get("init_code")
        if result.get("init_ms") is not None:
            out["init_ms"] = result.get("init_ms")
        return out

    def read_frame(self) -> Frame:
        with self._cmd_lock:
            snap = dict(self._last_cmd)
        return self._frame_from_cmd(snap)

    def _frame_from_cmd(self, snap: dict[str, Any]) -> Frame:
        t_wall = float(snap.get("t_wall") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1
        payload: dict[str, Any] = {
            "command_position_norm": snap.get("position_norm"),
            "command_position_raw": snap.get("position_raw"),
            "command_position_mm": snap.get("position_mm"),
            "initialized": snap.get("initialized"),
            "init_code": snap.get("init_code"),
            "last_ok": snap.get("ok"),
            "last_error": snap.get("error"),
            "last_result": snap.get("result"),
            "port": getattr(self.sensor, "port", None),
            "dry_run": bool(getattr(getattr(self.sensor, "ctx", None), "dry_run", False)),
        }
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            error=str(snap["error"]) if snap.get("error") else None,
        )
