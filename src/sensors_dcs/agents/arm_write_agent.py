from __future__ import annotations

import math
import threading
import time
from typing import Any, Sequence

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class ArmWriteAgent(BaseAgent):
    """Command Elite arm via ``sensor.write()`` (gated arm/disarm + TT jog).

    Mirrors ``GripperWriteAgent``: the agent loop only republishes last command
    status; motion is request-driven from the API / UI (± delta).
    """

    kind = "arm_write"

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
            "joints_rad": None,
            "jog_joint": None,
            "delta_rad": None,
            "armed": False,
            "ok": None,
            "error": None,
            "t_wall": None,
            "result": None,
        }

    def stop(self) -> None:
        """Stop loop; attempt disarm if sensor supports it."""
        try:
            if hasattr(self.sensor, "disarm"):
                self.sensor.disarm(stop=True)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def command(
        self,
        *,
        arm: bool = False,
        disarm: bool = False,
        stop: bool = False,
        joints_rad: Sequence[float] | None = None,
        reference_joints_rad: Sequence[float] | None = None,
        jog_joint: int | None = None,
        delta_rad: float | None = None,
        delta_deg: float | None = None,
    ) -> dict[str, Any]:
        """Arm/disarm/stop or send absolute / jog command through the sensor."""
        body: dict[str, Any] = {}
        if arm:
            body["arm"] = True
        elif disarm or stop:
            body["disarm"] = True
            body["stop"] = True if stop or disarm else False
        elif joints_rad is not None:
            body["joints_rad"] = [float(x) for x in joints_rad]
            if reference_joints_rad is not None:
                body["reference_joints_rad"] = [float(x) for x in reference_joints_rad]
        elif jog_joint is not None:
            # Prefer orchestrator converting jog→absolute from arm_read; keep for tests.
            body["jog_joint"] = int(jog_joint)
            if delta_rad is not None:
                body["delta_rad"] = float(delta_rad)
            elif delta_deg is not None:
                body["delta_rad"] = math.radians(float(delta_deg))
            else:
                return {"ok": False, "error": "delta_rad or delta_deg required for jog"}
        else:
            return {
                "ok": False,
                "error": "arm, disarm/stop, joints_rad, or jog_joint required",
            }

        try:
            result = dict(self.sensor.write(body))
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)}

        with self._cmd_lock:
            self._last_cmd = {
                "joints_rad": result.get("joints_rad", body.get("joints_rad")),
                "jog_joint": result.get("jog_joint", body.get("jog_joint")),
                "delta_rad": result.get("delta_rad", body.get("delta_rad")),
                "armed": bool(result.get("armed", getattr(self.sensor, "armed", False))),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "t_wall": time.time(),
                "result": result,
            }
            snap = dict(self._last_cmd)
        try:
            self.ring.push(self._frame_from_cmd(snap))
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": bool(snap.get("ok")),
            **{k: snap[k] for k in snap if k != "result"},
            "result": result,
        }

    def read_frame(self) -> Frame:
        # Prefer live sample for feedback bars when sensor can read.
        sample: dict[str, Any] = {}
        try:
            sample = dict(self.sensor.read())
        except Exception:  # noqa: BLE001
            sample = {}
        with self._cmd_lock:
            snap = dict(self._last_cmd)
        if sample.get("armed") is not None:
            snap["armed"] = bool(sample.get("armed"))
        snap["feedback_joints_rad"] = sample.get("joints_rad")
        snap["dry_run"] = bool(sample.get("dry_run"))
        snap["max_delta_deg"] = sample.get("max_delta_deg")
        return self._frame_from_cmd(snap)

    def _frame_from_cmd(self, snap: dict[str, Any]) -> Frame:
        t_wall = float(snap.get("t_wall") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1
        payload: dict[str, Any] = {
            "command_joints_rad": snap.get("joints_rad"),
            "feedback_joints_rad": snap.get("feedback_joints_rad"),
            "jog_joint": snap.get("jog_joint"),
            "delta_rad": snap.get("delta_rad"),
            "armed": bool(snap.get("armed")),
            "last_ok": snap.get("ok"),
            "last_error": snap.get("error"),
            "last_result": snap.get("result"),
            "max_delta_deg": snap.get("max_delta_deg"),
            "num_joints": int(getattr(self.sensor, "num_joints", 6) or 6),
            "robot_ip": getattr(self.sensor, "robot_ip", None),
            "dry_run": bool(
                snap.get("dry_run")
                if snap.get("dry_run") is not None
                else getattr(getattr(self.sensor, "ctx", None), "dry_run", False)
            ),
            "mode": "write_gated",
        }
        # Pose from measured feedback when available; else last commanded joints.
        pose_joints = snap.get("feedback_joints_rad") or snap.get("joints_rad")
        try:
            from sensors_dcs.arm_pose import cartesian_payload

            payload.update(
                cartesian_payload(pose_joints if isinstance(pose_joints, list) else None)
            )
        except Exception:  # noqa: BLE001
            payload["cartesian_xyzrpy"] = None
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
