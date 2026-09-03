from __future__ import annotations

import signal
import threading
import time
from typing import Any

import uvicorn

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors import SensorManager  # noqa: E402

from sensors_dcs.agents import build_agent
from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.config import DcsConfig, config_summary
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController
from sensors_dcs.viz import VizHub, create_viz_app


class Orchestrator:
    """MVP runtime: start listed agents + viz publisher + record consumer."""

    def __init__(self, cfg: DcsConfig) -> None:
        self.cfg = cfg
        dry = cfg.dry_run
        self.manager = SensorManager.from_yaml(cfg.sensors_config, dry_run=dry)
        self.agents: dict[str, BaseAgent] = {}
        known = self.manager.ids()
        for acfg in cfg.agents:
            try:
                sensor = self.manager.get(acfg.sensor_id)
            except KeyError as e:
                raise KeyError(
                    f"agent {acfg.id!r} sensor_id={acfg.sensor_id!r} not in "
                    f"sensors_config={cfg.sensors_config!r}; known devices: {known}. "
                    f"For robot_write, both YAMLs must use id arm-elite "
                    f"(old seeds used arm-elite-write — re-copy "
                    f"configs/robot_write.yaml + sensors_robot_write.yaml)."
                ) from e
            self.agents[acfg.id] = build_agent(acfg, sensor)
        self.hub = VizHub()
        self.recorder = RecordController(
            cfg.record,
            agents=self.agents,
            site=cfg.site,
        )
        self._stop = threading.Event()
        self._viz_thread: threading.Thread | None = None
        self._console_thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        # Server-side gello j6 (cal) → gripper write; UI only toggles on/off.
        self._sync_lock = threading.Lock()
        self._sync_stop = threading.Event()
        self._sync_thread: threading.Thread | None = None
        self._sync_enabled = False
        self._sync_gello_id: str | None = None
        self._sync_gripper_id: str | None = None
        self._sync_joint_index = 6
        self._sync_hz = 15.0
        self._sync_last_norm: float | None = None
        self._sync_last_ok: bool | None = None
        self._sync_last_error: str | None = None
        self._sync_last_t_wall: float | None = None
        self._sync_write_count = 0
        # One-shot gello→arm alignment (docs/gello-arm-sync.md); not teleop.
        self._arm_sync_lock = threading.Lock()
        self._arm_sync_stop = threading.Event()
        self._arm_sync_thread: threading.Thread | None = None
        self._arm_sync_enabled = False
        self._arm_sync_gello_id: str | None = None
        self._arm_sync_arm_id: str | None = None
        self._arm_sync_writer_id: str | None = None
        self._arm_sync_phase = "idle"
        self._arm_sync_ramp_index = 0
        self._arm_sync_ramp_n = 0
        self._arm_sync_round = 0
        self._arm_sync_delta_max: float | None = None
        self._arm_sync_worst_joint: int | None = None
        self._arm_sync_last_ok: bool | None = None
        self._arm_sync_last_error: str | None = None
        self._arm_sync_last_message: str | None = None
        self._arm_sync_write_count = 0
        self._arm_sync_last_t_wall: float | None = None
        self._arm_sync_target_joints: list[float] | None = None
        # Live gello→arm teleop (docs/gello-arm-teleop.md); default off.
        self._arm_teleop_lock = threading.Lock()
        self._arm_teleop_stop = threading.Event()
        self._arm_teleop_thread: threading.Thread | None = None
        self._arm_teleop_enabled = False
        self._arm_teleop_gello_id: str | None = None
        self._arm_teleop_arm_id: str | None = None
        self._arm_teleop_writer_id: str | None = None
        self._arm_teleop_phase = "idle"
        self._arm_teleop_delta_max: float | None = None
        self._arm_teleop_worst_joint: int | None = None
        self._arm_teleop_last_ok: bool | None = None
        self._arm_teleop_last_error: str | None = None
        self._arm_teleop_last_message: str | None = None
        self._arm_teleop_write_count = 0
        self._arm_teleop_last_t_wall: float | None = None
        self._arm_teleop_rate_limited = False
        self._arm_teleop_hz = 50.0

    def status(self) -> dict[str, Any]:
        return {
            "config": config_summary(self.cfg),
            "agents": {aid: a.stats() for aid, a in self.agents.items()},
            "sensors_site": self.manager.bundle.site,
            "sensors_dry_run": self.manager.ctx.dry_run,
            "record": self.recorder.status(),
            "gripper_gello_sync": self.gripper_gello_sync_status(),
            "gello_arm_sync": self.gello_arm_sync_status(),
            "gello_arm_teleop": self.gello_arm_teleop_status(),
        }

    def start(self) -> None:
        for agent in self.agents.values():
            agent.start()
        self._stop.clear()
        self._viz_thread = threading.Thread(target=self._viz_loop, name="viz-sampler", daemon=True)
        self._viz_thread.start()
        self._console_thread = threading.Thread(
            target=self._console_loop, name="console", daemon=True
        )
        self._console_thread.start()

    def stop(self) -> None:
        self.set_gello_arm_teleop(enabled=False)
        self.set_gello_arm_sync(enabled=False)
        self.set_gripper_gello_sync(enabled=False)
        self._stop.set()
        # Finish any open episode before tearing down agents
        if self.recorder.status().get("state") == "recording":
            try:
                self.recorder.stop(timeout=30.0)
            except Exception:  # noqa: BLE001
                pass
        if self._viz_thread and self._viz_thread.is_alive():
            self._viz_thread.join(timeout=2.0)
        if self._console_thread and self._console_thread.is_alive():
            self._console_thread.join(timeout=2.0)
        # Stop writers before readers so shared sensors disarm then close once.
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent

        writers = [
            a
            for a in self.agents.values()
            if isinstance(a, (ArmWriteAgent, GripperWriteAgent))
        ]
        readers = [a for a in self.agents.values() if a not in writers]
        for agent in writers + readers:
            agent.stop()
        self.manager.close_all()

    def request_shutdown(self) -> dict[str, Any]:
        """UI/API safe exit: stop agents (close sensors) then signal uvicorn."""
        try:
            self.stop()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        hook = getattr(self, "_uvicorn_exit", None)
        if callable(hook):
            try:
                hook()
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"stopped agents but exit failed: {e}"}
        return {"ok": True, "message": "shutdown requested"}

    def _viz_payload(self) -> dict[str, Any]:
        frames = []
        agent_rates: dict[str, Any] = {}
        for aid, agent in self.agents.items():
            fr = agent.ring.latest.get()
            if fr is not None:
                frames.append(self._frame_for_viz(fr))
            st = agent.stats()
            agent_rates[aid] = {
                "hz_target": agent.hz,
                "hz_meas": st.get("hz_meas"),
                "seq": fr.seq if fr is not None else None,
                "t_wall": fr.t_wall if fr is not None else None,
            }
        return {
            "schema": "sensors_dcs.viz.v1",
            "site": self.cfg.site,
            "t_wall": time.time(),
            "rates": {
                "viz_hz_target": self.cfg.runtime.viz_hz,
                "agents": agent_rates,
            },
            "frames": frames,
            "record": self.recorder.status(),
            "gripper_gello_sync": self.gripper_gello_sync_status(),
            "gello_arm_sync": self.gello_arm_sync_status(),
            "gello_arm_teleop": self.gello_arm_teleop_status(),
        }

    @staticmethod
    def _frame_for_viz(fr: Frame) -> dict[str, Any]:
        """Serialize frame for WebSocket: keep preview JPEG, drop full-res disk JPEG."""
        data = fr.to_dict()
        payload = dict(data.get("payload") or {})
        preview = payload.get("jpeg_b64_preview") or payload.get("jpeg_b64")
        payload.pop("jpeg_b64", None)
        payload.pop("jpeg_b64_preview", None)
        if preview is not None:
            payload["jpeg_b64_preview"] = preview
        payload.pop("depth_png_b64", None)
        data["payload"] = payload
        return data

    def _viz_loop(self) -> None:
        period = 1.0 / max(0.1, self.cfg.runtime.viz_hz)
        while not self._stop.is_set():
            self.hub.publish_threadsafe(self._viz_payload())
            self._stop.wait(period)

    def _console_loop(self) -> None:
        period = 1.0 / max(0.1, self.cfg.runtime.console_hz)
        while not self._stop.is_set():
            parts = []
            rs = self.recorder.status()
            parts.append(f"rec={rs.get('state')} ep={rs.get('episode_index')}")
            for aid, agent in self.agents.items():
                fr = agent.ring.latest.get()
                if fr is None:
                    parts.append(f"{aid}: (no frame)")
                    continue
                tag = "synth" if fr.payload.get("synth") else "live"
                if fr.error:
                    parts.append(f"{aid}: seq={fr.seq} err={fr.error}")
                elif fr.kind == "gripper_read":
                    pos = fr.payload.get("position_norm")
                    ptxt = "—" if pos is None else f"{float(pos):+.3f}"
                    parts.append(f"{aid}[{tag}] seq={fr.seq} pos={ptxt}")
                elif fr.kind == "arm_write":
                    armed = "armed" if fr.payload.get("armed") else "idle"
                    q = fr.payload.get("command_joints_rad") or fr.payload.get("feedback_joints_rad")
                    if q is None:
                        parts.append(f"{aid}[{tag}/{armed}] seq={fr.seq}")
                    else:
                        jtxt = ",".join(f"{x:+.3f}" for x in q[:6])
                        parts.append(f"{aid}[{tag}/{armed}] seq={fr.seq} q=[{jtxt}]")
                elif fr.kind == "realsense":
                    sn = fr.payload.get("serial") or "—"
                    shape = fr.payload.get("color_shape")
                    stxt = "x".join(str(x) for x in shape) if shape else "—"
                    parts.append(f"{aid}[{tag}] seq={fr.seq} sn={sn} rgb={stxt}")
                else:
                    joints = fr.payload.get("joints_rad")
                    if joints is None:
                        parts.append(f"{aid}[{tag}] seq={fr.seq}")
                    else:
                        jtxt = ",".join(f"{x:+.3f}" for x in joints)
                        parts.append(f"{aid}[{tag}] seq={fr.seq} q=[{jtxt}]")
            print(" | ".join(parts), flush=True)
            self._stop.wait(period)

    def gripper_gello_sync_status(self) -> dict[str, Any]:
        with self._sync_lock:
            return {
                "enabled": self._sync_enabled,
                "gello_agent_id": self._sync_gello_id,
                "gripper_agent_id": self._sync_gripper_id,
                "joint_index": self._sync_joint_index,
                "hz": self._sync_hz,
                "last_norm": self._sync_last_norm,
                "last_ok": self._sync_last_ok,
                "last_error": self._sync_last_error,
                "last_t_wall": self._sync_last_t_wall,
                "write_count": self._sync_write_count,
            }

    def set_gripper_gello_sync(
        self,
        *,
        enabled: bool,
        gello_agent_id: str | None = None,
        gripper_agent_id: str | None = None,
        joint_index: int = 6,
        hz: float | None = None,
    ) -> dict[str, Any]:
        """Start/stop server-side gello cal[j] → gripper write loop (UI toggle only)."""
        from sensors_dcs.agents.gello_agent import GelloAgent
        from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent

        if not enabled:
            self._sync_stop.set()
            th = self._sync_thread
            if th is not None and th.is_alive():
                th.join(timeout=2.0)
            with self._sync_lock:
                self._sync_enabled = False
                self._sync_thread = None
            return {"ok": True, **self.gripper_gello_sync_status()}

        gellos = [a for a in self.agents.values() if isinstance(a, GelloAgent)]
        writers = [a for a in self.agents.values() if isinstance(a, GripperWriteAgent)]
        if not gellos:
            return {"ok": False, "error": "no gello agent in config"}
        if not writers:
            return {"ok": False, "error": "no gripper_write agent in config"}

        if gello_agent_id:
            gello = self.agents.get(gello_agent_id)
            if not isinstance(gello, GelloAgent):
                return {"ok": False, "error": f"agent {gello_agent_id!r} is not gello"}
        else:
            gello = gellos[0]

        if gripper_agent_id:
            writer = self.agents.get(gripper_agent_id)
            if not isinstance(writer, GripperWriteAgent):
                return {"ok": False, "error": f"agent {gripper_agent_id!r} is not gripper_write"}
        else:
            writer = writers[0]

        idx = int(joint_index)
        if idx < 0:
            return {"ok": False, "error": "joint_index must be >= 0"}
        rate = float(hz) if hz is not None else float(getattr(writer, "hz", 5.0) or 5.0)
        rate = max(1.0, min(rate, 50.0))

        # Restart cleanly if already running.
        self._sync_stop.set()
        th_old = self._sync_thread
        if th_old is not None and th_old.is_alive():
            th_old.join(timeout=2.0)

        with self._sync_lock:
            self._sync_enabled = True
            self._sync_gello_id = gello.agent_id
            self._sync_gripper_id = writer.agent_id
            self._sync_joint_index = idx
            self._sync_hz = rate
            self._sync_last_error = None

        self._sync_stop.clear()
        self._sync_thread = threading.Thread(
            target=self._gello_gripper_sync_loop,
            name="gello-gripper-sync",
            daemon=True,
        )
        self._sync_thread.start()
        return {"ok": True, **self.gripper_gello_sync_status()}

    def _gello_gripper_sync_loop(self) -> None:
        """Pull latest gello joints_rad[j] and write gripper — never via the browser.

        Always command at ``_sync_hz`` (no change-gate): AG95 needs repeated
        position writes while tracking; skipping on tiny Δ leaves the gripper
        stuck after a fast gello move until the leader moves again.
        """
        from sensors_dcs.agents.gello_agent import GelloAgent
        from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent

        period = 1.0 / max(1.0, self._sync_hz)
        while not self._sync_stop.is_set() and not self._stop.is_set():
            with self._sync_lock:
                gello_id = self._sync_gello_id
                grip_id = self._sync_gripper_id
                idx = self._sync_joint_index
            gello = self.agents.get(gello_id or "")
            writer = self.agents.get(grip_id or "")
            if not isinstance(gello, GelloAgent) or not isinstance(writer, GripperWriteAgent):
                with self._sync_lock:
                    self._sync_last_ok = False
                    self._sync_last_error = "sync agents missing"
                    self._sync_last_t_wall = time.time()
                self._sync_stop.wait(period)
                continue

            fr = gello.ring.latest.get()
            joints = (fr.payload.get("joints_rad") if fr is not None else None) or []
            if not isinstance(joints, list) or len(joints) <= idx:
                with self._sync_lock:
                    self._sync_last_ok = False
                    self._sync_last_error = f"gello joints_rad missing index {idx}"
                    self._sync_last_t_wall = time.time()
                self._sync_stop.wait(period)
                continue

            try:
                norm = float(joints[idx])
            except (TypeError, ValueError):
                with self._sync_lock:
                    self._sync_last_ok = False
                    self._sync_last_error = f"invalid joints_rad[{idx}]"
                    self._sync_last_t_wall = time.time()
                self._sync_stop.wait(period)
                continue

            # Clamp to AG95 position_norm range used elsewhere in UI.
            norm = max(0.0, min(0.637, norm))
            result = writer.command(position_norm=norm)
            with self._sync_lock:
                self._sync_last_norm = norm
                self._sync_last_ok = bool(result.get("ok"))
                self._sync_last_error = result.get("error")
                self._sync_last_t_wall = time.time()
                self._sync_write_count += 1
            self._sync_stop.wait(period)

        with self._sync_lock:
            self._sync_enabled = False

    def gello_arm_sync_status(self) -> dict[str, Any]:
        with self._arm_sync_lock:
            return {
                "enabled": self._arm_sync_enabled,
                "phase": self._arm_sync_phase,
                "gello_agent_id": self._arm_sync_gello_id,
                "arm_agent_id": self._arm_sync_arm_id,
                "arm_write_agent_id": self._arm_sync_writer_id,
                "ramp_index": self._arm_sync_ramp_index,
                "ramp_n": self._arm_sync_ramp_n,
                "round": self._arm_sync_round,
                "delta_max": self._arm_sync_delta_max,
                "worst_joint": self._arm_sync_worst_joint,
                "last_ok": self._arm_sync_last_ok,
                "last_error": self._arm_sync_last_error,
                "message": self._arm_sync_last_message,
                "write_count": self._arm_sync_write_count,
                "last_t_wall": self._arm_sync_last_t_wall,
                "target_joints_rad": (
                    list(self._arm_sync_target_joints)
                    if self._arm_sync_target_joints is not None
                    else None
                ),
                "params": self.cfg.gello_arm_sync.model_dump(),
            }

    @staticmethod
    def _joints6_from_ring(agent: BaseAgent, *, prefer_key: str = "joints_rad") -> list[float] | None:
        fr = agent.ring.latest.get()
        if fr is None:
            return None
        raw = fr.payload.get(prefer_key)
        if not isinstance(raw, list) or len(raw) < 6:
            # arm_write feedback fallback
            raw = fr.payload.get("feedback_joints_rad") or fr.payload.get("joints_rad")
        if not isinstance(raw, list) or len(raw) < 6:
            return None
        try:
            return [float(raw[i]) for i in range(6)]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _delta_max_joint(qa: list[float], qg: list[float]) -> tuple[float, int]:
        best_i = 0
        best_d = 0.0
        for i in range(6):
            d = abs(float(qg[i]) - float(qa[i]))
            if d > best_d:
                best_d = d
                best_i = i
        return best_d, best_i

    @staticmethod
    def _interp_path(qa: list[float], qg: list[float], n: int) -> list[list[float]]:
        """Linear path q_a → q_g with N points (k=1..N); last == q_g."""
        n = max(1, int(n))
        out: list[list[float]] = []
        for k in range(1, n + 1):
            a = k / n
            out.append([float(qa[i]) + a * (float(qg[i]) - float(qa[i])) for i in range(6)])
        return out

    def _resolve_gello_arm_agents(
        self,
        *,
        gello_agent_id: str | None = None,
        arm_agent_id: str | None = None,
        arm_write_agent_id: str | None = None,
    ) -> tuple[Any, Any, Any] | dict[str, Any]:
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.agents.gello_agent import GelloAgent

        gellos = [a for a in self.agents.values() if isinstance(a, GelloAgent)]
        readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
        writers = [a for a in self.agents.values() if isinstance(a, ArmWriteAgent)]
        if not gellos:
            return {"ok": False, "error": "no gello agent in config", "gate_failed": True}
        if not readers:
            return {"ok": False, "error": "no arm (read) agent in config", "gate_failed": True}
        if not writers:
            return {"ok": False, "error": "no arm_write agent in config", "gate_failed": True}

        if gello_agent_id:
            gello = self.agents.get(gello_agent_id)
            if not isinstance(gello, GelloAgent):
                return {"ok": False, "error": f"agent {gello_agent_id!r} is not gello", "gate_failed": True}
        else:
            gello = gellos[0]

        if arm_write_agent_id:
            writer = self.agents.get(arm_write_agent_id)
            if not isinstance(writer, ArmWriteAgent):
                return {
                    "ok": False,
                    "error": f"agent {arm_write_agent_id!r} is not arm_write",
                    "gate_failed": True,
                }
        else:
            writer = writers[0]

        if arm_agent_id:
            reader = self.agents.get(arm_agent_id)
            if not isinstance(reader, ArmAgent):
                return {"ok": False, "error": f"agent {arm_agent_id!r} is not arm", "gate_failed": True}
        else:
            reader = readers[0]
            for cand in readers:
                if cand.sensor_id == writer.sensor_id:
                    reader = cand
                    break
        return gello, reader, writer

    def _gello_arm_gate(
        self,
        gello: BaseAgent,
        reader: BaseAgent,
        writer: BaseAgent,
    ) -> dict[str, Any]:
        params = self.cfg.gello_arm_sync
        armed = bool(getattr(writer.sensor, "armed", False))
        if not armed:
            fr = writer.ring.latest.get()
            if fr is not None:
                armed = bool(fr.payload.get("armed"))
        if not armed:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "arm_write not armed; click Arm before sync",
            }
        qg = self._joints6_from_ring(gello)
        qa = self._joints6_from_ring(reader)
        if qg is None:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "no live gello joints_rad[0:6]; wait for gello read",
            }
        if qa is None:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "no live arm read joints; wait for robot · Read",
            }
        dmax, ji = self._delta_max_joint(qa, qg)
        if dmax > float(params.align_max_rad) + 1e-12:
            return {
                "ok": False,
                "gate_failed": True,
                "error": (
                    f"max |Δq|={dmax:.4f} rad (joint {ji}) exceeds align_max_rad="
                    f"{params.align_max_rad}. Please manually move the arm (or gello) "
                    f"closer, then retry sync."
                ),
                "delta_max": dmax,
                "worst_joint": ji,
                "q_gello": qg,
                "q_arm": qa,
            }
        # Ensure each ramp step stays under driver max_delta when possible.
        n = max(1, int(round(float(params.ramp_duration_s) * float(params.ramp_hz))))
        step = dmax / n
        max_delta = float(getattr(writer.sensor, "max_delta_rad", 0.0) or 0.0)
        if max_delta > 0 and step > max_delta + 1e-12:
            return {
                "ok": False,
                "gate_failed": True,
                "error": (
                    f"ramp step ≈{step:.4f} rad exceeds driver max_delta={max_delta:.4f} rad; "
                    f"reduce align gap or increase ramp duration/hz"
                ),
                "delta_max": dmax,
                "worst_joint": ji,
            }
        return {"ok": True, "q_gello": qg, "q_arm": qa, "delta_max": dmax, "worst_joint": ji, "ramp_n": n}

    def set_gello_arm_sync(
        self,
        *,
        enabled: bool,
        gello_agent_id: str | None = None,
        arm_agent_id: str | None = None,
        arm_write_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Start/stop one-shot gello→arm alignment. After finish, gello does not command arm."""
        if enabled:
            # Sync wins: stop teleop before starting alignment.
            self.set_gello_arm_teleop(enabled=False)
        if not enabled:
            self._arm_sync_stop.set()
            th = self._arm_sync_thread
            if th is not None and th.is_alive():
                th.join(timeout=3.0)
            with self._arm_sync_lock:
                self._arm_sync_enabled = False
                self._arm_sync_thread = None
                if self._arm_sync_phase in {"ramping", "verifying"}:
                    self._arm_sync_phase = "idle"
                    self._arm_sync_last_message = "同步已取消；gello 未控制机械臂"
                    self._arm_sync_last_ok = True
                    self._arm_sync_last_error = None
            return {"ok": True, **self.gello_arm_sync_status()}

        resolved = self._resolve_gello_arm_agents(
            gello_agent_id=gello_agent_id,
            arm_agent_id=arm_agent_id,
            arm_write_agent_id=arm_write_agent_id,
        )
        if isinstance(resolved, dict):
            return {**resolved, **self.gello_arm_sync_status()}
        gello, reader, writer = resolved

        gate = self._gello_arm_gate(gello, reader, writer)
        if not gate.get("ok"):
            with self._arm_sync_lock:
                self._arm_sync_phase = "error"
                self._arm_sync_last_ok = False
                self._arm_sync_last_error = gate.get("error")
                self._arm_sync_last_message = gate.get("error")
                self._arm_sync_delta_max = gate.get("delta_max")
                self._arm_sync_worst_joint = gate.get("worst_joint")
                self._arm_sync_last_t_wall = time.time()
            return {**gate, **self.gello_arm_sync_status()}

        # Restart cleanly if a previous run is still marked.
        self._arm_sync_stop.set()
        th_old = self._arm_sync_thread
        if th_old is not None and th_old.is_alive():
            th_old.join(timeout=3.0)

        params = self.cfg.gello_arm_sync
        n = int(gate["ramp_n"])
        with self._arm_sync_lock:
            self._arm_sync_enabled = True
            self._arm_sync_gello_id = gello.agent_id
            self._arm_sync_arm_id = reader.agent_id
            self._arm_sync_writer_id = writer.agent_id
            self._arm_sync_phase = "ramping"
            self._arm_sync_ramp_index = 0
            self._arm_sync_ramp_n = n
            self._arm_sync_round = 0
            self._arm_sync_delta_max = gate.get("delta_max")
            self._arm_sync_worst_joint = gate.get("worst_joint")
            self._arm_sync_last_ok = None
            self._arm_sync_last_error = None
            self._arm_sync_last_message = (
                f"同步开始：目标为命令时刻 gello 姿态（一次性）；{params.ramp_duration_s:g}s @ "
                f"{params.ramp_hz:g}Hz × {n} 点"
            )
            self._arm_sync_write_count = 0
            self._arm_sync_last_t_wall = time.time()
            self._arm_sync_target_joints = list(gate["q_gello"])

        self._arm_sync_stop.clear()
        self._arm_sync_thread = threading.Thread(
            target=self._gello_arm_sync_loop,
            name="gello-arm-sync",
            daemon=True,
            args=(
                gello.agent_id,
                reader.agent_id,
                writer.agent_id,
                list(gate["q_gello"]),
                list(gate["q_arm"]),
                int(gate["ramp_n"]),
                float(gate["delta_max"]),
                int(gate["worst_joint"]),
            ),
        )
        self._arm_sync_thread.start()
        return {"ok": True, **self.gello_arm_sync_status()}

    def _gello_arm_sync_loop(
        self,
        gello_id: str,
        arm_id: str,
        writer_id: str,
        first_qg: list[float],
        first_qa: list[float],
        first_n: int,
        first_dmax: float,
        first_ji: int,
    ) -> None:
        """Frozen-target ramp @ ramp_hz; verify; retry rounds; then force-disable (no teleop)."""
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.agents.gello_agent import GelloAgent

        params = self.cfg.gello_arm_sync
        period = 1.0 / max(0.1, float(params.ramp_hz))
        max_rounds = max(1, int(params.max_ramp_rounds))
        eps = float(params.sync_done_eps_rad)
        completed = False
        final_error: str | None = None
        final_message: str | None = None

        try:
            for round_i in range(1, max_rounds + 1):
                if self._arm_sync_stop.is_set() or self._stop.is_set():
                    final_message = "同步已取消；gello 未控制机械臂"
                    break

                gello = self.agents.get(gello_id)
                reader = self.agents.get(arm_id)
                writer = self.agents.get(writer_id)
                if not isinstance(gello, GelloAgent) or not isinstance(reader, ArmAgent):
                    final_error = "sync agents missing"
                    break
                if not isinstance(writer, ArmWriteAgent):
                    final_error = "arm_write agent missing"
                    break

                if round_i == 1:
                    # Command-time snapshot from set_gello_arm_sync (do not re-read gello).
                    qg_star = list(first_qg)
                    qa0 = list(first_qa)
                    n = max(1, int(first_n))
                    dmax0 = float(first_dmax)
                    ji0 = int(first_ji)
                else:
                    # New round = new command-time sample (once per round).
                    gate = self._gello_arm_gate(gello, reader, writer)
                    if not gate.get("ok"):
                        final_error = str(gate.get("error") or "gate failed")
                        with self._arm_sync_lock:
                            self._arm_sync_delta_max = gate.get("delta_max")
                            self._arm_sync_worst_joint = gate.get("worst_joint")
                        break
                    qg_star = list(gate["q_gello"])
                    qa0 = list(gate["q_arm"])
                    n = int(gate["ramp_n"])
                    dmax0 = float(gate["delta_max"])
                    ji0 = int(gate["worst_joint"])

                path = self._interp_path(qa0, qg_star, n)
                with self._arm_sync_lock:
                    self._arm_sync_round = round_i
                    self._arm_sync_phase = "ramping"
                    self._arm_sync_ramp_n = n
                    self._arm_sync_ramp_index = 0
                    self._arm_sync_delta_max = dmax0
                    self._arm_sync_worst_joint = ji0
                    self._arm_sync_target_joints = list(qg_star)
                    self._arm_sync_last_message = (
                        f"ramping round {round_i}/{max_rounds}: frozen gello target; "
                        f"gello motion during ramp is ignored"
                    )
                    self._arm_sync_last_t_wall = time.time()

                aborted = False
                for k, qk in enumerate(path, start=1):
                    if self._arm_sync_stop.is_set() or self._stop.is_set():
                        aborted = True
                        break
                    ref = self._joints6_from_ring(reader) or (
                        path[k - 2] if k >= 2 else qa0
                    )
                    result = writer.command(joints_rad=list(qk), reference_joints_rad=list(ref))
                    with self._arm_sync_lock:
                        self._arm_sync_ramp_index = k
                        self._arm_sync_write_count += 1
                        self._arm_sync_last_ok = bool(result.get("ok"))
                        self._arm_sync_last_error = result.get("error")
                        self._arm_sync_last_t_wall = time.time()
                        if not result.get("ok"):
                            self._arm_sync_last_message = (
                                f"ramp write failed at {k}/{n}: {result.get('error')}"
                            )
                    if not result.get("ok"):
                        final_error = str(result.get("error") or "ramp write failed")
                        aborted = True
                        break
                    self._arm_sync_stop.wait(period)

                if aborted:
                    if self._arm_sync_stop.is_set() or self._stop.is_set():
                        final_message = "同步已取消；gello 未控制机械臂"
                    break

                with self._arm_sync_lock:
                    self._arm_sync_phase = "verifying"
                    self._arm_sync_last_message = "校验中（不写臂）…"
                    self._arm_sync_last_t_wall = time.time()

                qg_now = self._joints6_from_ring(gello)
                qa_now = self._joints6_from_ring(reader)
                if qg_now is None or qa_now is None:
                    final_error = "verify failed: missing live joints"
                    break
                dmax, ji = self._delta_max_joint(qa_now, qg_now)
                with self._arm_sync_lock:
                    self._arm_sync_delta_max = dmax
                    self._arm_sync_worst_joint = ji
                    self._arm_sync_last_t_wall = time.time()

                if dmax <= eps + 1e-12:
                    completed = True
                    final_message = "同步完成；gello 已不再控制机械臂"
                    break

                if round_i >= max_rounds:
                    final_error = (
                        f"after {max_rounds} ramp rounds still |Δq|={dmax:.4f} rad "
                        f"(joint {ji}) > eps={eps}; move closer manually and retry"
                    )
                    break
        finally:
            with self._arm_sync_lock:
                self._arm_sync_enabled = False
                self._arm_sync_thread = None
                if completed:
                    self._arm_sync_phase = "completed"
                    self._arm_sync_last_ok = True
                    self._arm_sync_last_error = None
                    self._arm_sync_last_message = final_message
                elif final_error:
                    self._arm_sync_phase = "error"
                    self._arm_sync_last_ok = False
                    self._arm_sync_last_error = final_error
                    self._arm_sync_last_message = final_error
                else:
                    self._arm_sync_phase = "idle"
                    self._arm_sync_last_ok = True if final_message else self._arm_sync_last_ok
                    self._arm_sync_last_message = final_message or self._arm_sync_last_message
                self._arm_sync_last_t_wall = time.time()

    def gello_arm_teleop_status(self) -> dict[str, Any]:
        with self._arm_teleop_lock:
            return {
                "enabled": self._arm_teleop_enabled,
                "phase": self._arm_teleop_phase,
                "gello_agent_id": self._arm_teleop_gello_id,
                "arm_agent_id": self._arm_teleop_arm_id,
                "arm_write_agent_id": self._arm_teleop_writer_id,
                "delta_max": self._arm_teleop_delta_max,
                "worst_joint": self._arm_teleop_worst_joint,
                "last_ok": self._arm_teleop_last_ok,
                "last_error": self._arm_teleop_last_error,
                "message": self._arm_teleop_last_message,
                "write_count": self._arm_teleop_write_count,
                "last_t_wall": self._arm_teleop_last_t_wall,
                "rate_limited": self._arm_teleop_rate_limited,
                "hz": self._arm_teleop_hz,
                "params": self.cfg.gello_arm_teleop.model_dump(),
            }

    @staticmethod
    def _rate_limit_step(
        q_prev: list[float], q_target: list[float], step_max: float
    ) -> list[float]:
        """Move from q_prev toward q_target with max |Δ| per joint capped by step_max on the vector."""
        dmax, _ = Orchestrator._delta_max_joint(q_prev, q_target)
        if dmax <= step_max + 1e-12:
            return [float(x) for x in q_target]
        if dmax <= 1e-15:
            return [float(x) for x in q_prev]
        scale = float(step_max) / dmax
        return [
            float(q_prev[i]) + scale * (float(q_target[i]) - float(q_prev[i]))
            for i in range(6)
        ]

    def _writer_armed(self, writer: BaseAgent) -> bool:
        armed = bool(getattr(writer.sensor, "armed", False))
        if not armed:
            fr = writer.ring.latest.get()
            if fr is not None:
                armed = bool(fr.payload.get("armed"))
        return armed

    def _gello_arm_teleop_gate(
        self,
        gello: BaseAgent,
        reader: BaseAgent,
        writer: BaseAgent,
    ) -> dict[str, Any]:
        params = self.cfg.gello_arm_teleop
        if self._arm_sync_enabled:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "gello→arm sync active; cancel sync before teleop",
            }
        if not self._writer_armed(writer):
            return {
                "ok": False,
                "gate_failed": True,
                "error": "请先点 Arm，再开摇操。",
            }
        qg = self._joints6_from_ring(gello)
        qa = self._joints6_from_ring(reader)
        if qg is None:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "no live gello joints_rad[0:6]; wait for gello read",
            }
        if qa is None:
            return {
                "ok": False,
                "gate_failed": True,
                "error": "no live arm read joints; wait for robot · Read",
            }
        dmax, ji = self._delta_max_joint(qa, qg)
        enter = float(params.teleop_enter_max_rad)
        if dmax > enter + 1e-12:
            return {
                "ok": False,
                "gate_failed": True,
                "error": (
                    f"当前最大关节差 Δ={dmax:.4f} rad（第 {ji} 轴）超过 "
                    f"teleop_enter_max_rad={enter}。请先点「同步」对齐后再摇操。"
                ),
                "delta_max": dmax,
                "worst_joint": ji,
                "q_gello": qg,
                "q_arm": qa,
            }
        return {
            "ok": True,
            "q_gello": qg,
            "q_arm": qa,
            "delta_max": dmax,
            "worst_joint": ji,
        }

    def set_gello_arm_teleop(
        self,
        *,
        enabled: bool,
        gello_agent_id: str | None = None,
        arm_agent_id: str | None = None,
        arm_write_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Start/stop live gello→arm teleop. Jump/stale auto-disables; keeps armed."""
        if not enabled:
            self._arm_teleop_stop.set()
            th = self._arm_teleop_thread
            if th is not None and th.is_alive():
                th.join(timeout=3.0)
            with self._arm_teleop_lock:
                was = self._arm_teleop_enabled or self._arm_teleop_phase == "teleop"
                self._arm_teleop_enabled = False
                self._arm_teleop_thread = None
                self._arm_teleop_rate_limited = False
                if was and self._arm_teleop_phase == "teleop":
                    self._arm_teleop_phase = "idle"
                    self._arm_teleop_last_message = "已解除摇操；gello 未控制机械臂"
                    self._arm_teleop_last_ok = True
                    self._arm_teleop_last_error = None
                self._arm_teleop_last_t_wall = time.time()
            return {"ok": True, **self.gello_arm_teleop_status()}

        resolved = self._resolve_gello_arm_agents(
            gello_agent_id=gello_agent_id,
            arm_agent_id=arm_agent_id,
            arm_write_agent_id=arm_write_agent_id,
        )
        if isinstance(resolved, dict):
            return {**resolved, **self.gello_arm_teleop_status()}
        gello, reader, writer = resolved

        gate = self._gello_arm_teleop_gate(gello, reader, writer)
        if not gate.get("ok"):
            with self._arm_teleop_lock:
                self._arm_teleop_phase = "error"
                self._arm_teleop_last_ok = False
                self._arm_teleop_last_error = gate.get("error")
                self._arm_teleop_last_message = gate.get("error")
                self._arm_teleop_delta_max = gate.get("delta_max")
                self._arm_teleop_worst_joint = gate.get("worst_joint")
                self._arm_teleop_last_t_wall = time.time()
            return {**gate, **self.gello_arm_teleop_status()}

        self._arm_teleop_stop.set()
        th_old = self._arm_teleop_thread
        if th_old is not None and th_old.is_alive():
            th_old.join(timeout=3.0)

        params = self.cfg.gello_arm_teleop
        hz = max(1.0, min(100.0, float(params.teleop_hz)))
        with self._arm_teleop_lock:
            self._arm_teleop_enabled = True
            self._arm_teleop_gello_id = gello.agent_id
            self._arm_teleop_arm_id = reader.agent_id
            self._arm_teleop_writer_id = writer.agent_id
            self._arm_teleop_phase = "teleop"
            self._arm_teleop_delta_max = gate.get("delta_max")
            self._arm_teleop_worst_joint = gate.get("worst_joint")
            self._arm_teleop_last_ok = None
            self._arm_teleop_last_error = None
            self._arm_teleop_last_message = f"摇操中 · {hz:g} Hz"
            self._arm_teleop_write_count = 0
            self._arm_teleop_rate_limited = False
            self._arm_teleop_hz = hz
            self._arm_teleop_last_t_wall = time.time()

        self._arm_teleop_stop.clear()
        self._arm_teleop_thread = threading.Thread(
            target=self._gello_arm_teleop_loop,
            name="gello-arm-teleop",
            daemon=True,
            args=(gello.agent_id, reader.agent_id, writer.agent_id),
        )
        self._arm_teleop_thread.start()
        return {"ok": True, **self.gello_arm_teleop_status()}

    def _gello_arm_teleop_loop(self, gello_id: str, arm_id: str, writer_id: str) -> None:
        """Live follow with per-step rate limit and jump/stale abort (keeps armed)."""
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.agents.gello_agent import GelloAgent

        params = self.cfg.gello_arm_teleop
        hz = max(1.0, min(100.0, float(params.teleop_hz)))
        period = 1.0 / hz
        step_max = float(params.teleop_step_max_rad)
        jump_abort = float(params.teleop_jump_abort_rad)
        stale_max = max(1, int(params.teleop_stale_max_ticks))
        fail_max = max(1, int(params.teleop_write_fail_max))

        q_cmd_prev: list[float] | None = None
        stale_ticks = 0
        fail_streak = 0
        final_error: str | None = None
        final_message: str | None = None
        abort_phase = "idle"

        try:
            while not self._arm_teleop_stop.is_set() and not self._stop.is_set():
                gello = self.agents.get(gello_id)
                reader = self.agents.get(arm_id)
                writer = self.agents.get(writer_id)
                if not isinstance(gello, GelloAgent) or not isinstance(reader, ArmAgent):
                    final_error = "teleop agents missing"
                    abort_phase = "error"
                    break
                if not isinstance(writer, ArmWriteAgent):
                    final_error = "arm_write agent missing"
                    abort_phase = "error"
                    break
                if not self._writer_armed(writer):
                    final_error = "Arm 已断开，已解除摇操"
                    abort_phase = "error"
                    break

                q_g = self._joints6_from_ring(gello)
                q_a = self._joints6_from_ring(reader)
                if q_g is None:
                    stale_ticks += 1
                    if stale_ticks > stale_max:
                        final_error = "gello 读数中断，已解除摇操"
                        abort_phase = "error"
                        break
                    with self._arm_teleop_lock:
                        self._arm_teleop_last_message = (
                            f"摇操中 · 等待 gello（stale {stale_ticks}/{stale_max}）"
                        )
                        self._arm_teleop_last_t_wall = time.time()
                    self._arm_teleop_stop.wait(period)
                    continue
                stale_ticks = 0

                rate_limited = False
                if q_cmd_prev is None:
                    q_cmd = list(q_g)
                    dmax, ji = self._delta_max_joint(q_a or q_cmd, q_g)
                else:
                    dmax, ji = self._delta_max_joint(q_cmd_prev, q_g)
                    if dmax > jump_abort + 1e-12:
                        final_error = (
                            f"检测到 gello 关节跳变（Δ={dmax:.4f} rad，轴 {ji}），"
                            f"已自动解除摇操。请检查主手后先「同步」，再开摇操。"
                        )
                        abort_phase = "error"
                        break
                    if dmax > step_max + 1e-12:
                        q_cmd = self._rate_limit_step(q_cmd_prev, q_g, step_max)
                        rate_limited = True
                    else:
                        q_cmd = list(q_g)

                ref = q_a if q_a is not None else (q_cmd_prev or q_cmd)
                result = writer.command(joints_rad=list(q_cmd), reference_joints_rad=list(ref))
                ok = bool(result.get("ok"))
                if ok:
                    fail_streak = 0
                    q_cmd_prev = list(q_cmd)
                else:
                    fail_streak += 1
                    if fail_streak >= fail_max:
                        final_error = (
                            "摇操写臂连续失败："
                            + str(result.get("error") or "write rejected")
                            + "；已解除摇操"
                        )
                        abort_phase = "error"
                        break

                with self._arm_teleop_lock:
                    self._arm_teleop_delta_max = dmax
                    self._arm_teleop_worst_joint = ji
                    self._arm_teleop_rate_limited = rate_limited
                    self._arm_teleop_last_ok = ok
                    self._arm_teleop_last_error = result.get("error")
                    self._arm_teleop_write_count += 1 if ok else 0
                    self._arm_teleop_last_t_wall = time.time()
                    if rate_limited:
                        self._arm_teleop_last_message = (
                            f"限速中 · {hz:g} Hz · 已写 {self._arm_teleop_write_count}"
                        )
                    else:
                        self._arm_teleop_last_message = (
                            f"摇操中 · {hz:g} Hz · 已写 {self._arm_teleop_write_count}"
                        )

                self._arm_teleop_stop.wait(period)

            if final_error is None and (
                self._arm_teleop_stop.is_set() or self._stop.is_set()
            ):
                final_message = "已解除摇操；gello 未控制机械臂"
                abort_phase = "idle"
        finally:
            with self._arm_teleop_lock:
                self._arm_teleop_enabled = False
                self._arm_teleop_thread = None
                self._arm_teleop_rate_limited = False
                self._arm_teleop_phase = abort_phase
                if final_error:
                    self._arm_teleop_last_ok = False
                    self._arm_teleop_last_error = final_error
                    self._arm_teleop_last_message = final_error
                else:
                    self._arm_teleop_last_ok = True
                    self._arm_teleop_last_error = None
                    self._arm_teleop_last_message = (
                        final_message or "已解除摇操；gello 未控制机械臂"
                    )
                self._arm_teleop_last_t_wall = time.time()

    def gripper_command(
        self,
        *,
        agent_id: str | None = None,
        position_norm: float | None = None,
        position_raw: int | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        """Dispatch init / absolute gripper target to a ``gripper_write`` agent."""
        from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent

        if self._sync_enabled and not initialize:
            return {
                "ok": False,
                "error": "gello sync active; cancel sync before manual command",
                **self.gripper_gello_sync_status(),
            }

        writers = [a for a in self.agents.values() if isinstance(a, GripperWriteAgent)]
        if not writers:
            return {"ok": False, "error": "no gripper_write agent in config"}
        agent: GripperWriteAgent
        if agent_id:
            found = self.agents.get(agent_id)
            if not isinstance(found, GripperWriteAgent):
                return {"ok": False, "error": f"agent {agent_id!r} is not gripper_write"}
            agent = found
        else:
            agent = writers[0]
        return agent.command(
            position_norm=position_norm,
            position_raw=position_raw,
            initialize=initialize,
        )

    def arm_command(
        self,
        *,
        agent_id: str | None = None,
        arm: bool = False,
        disarm: bool = False,
        stop: bool = False,
        joints_rad: list[float] | None = None,
        jog_joint: int | None = None,
        delta_rad: float | None = None,
        delta_deg: float | None = None,
    ) -> dict[str, Any]:
        """Dispatch arm/disarm/jog to ``arm_write``; jog is always relative to ``arm`` read."""
        import math

        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
        writers = [a for a in self.agents.values() if isinstance(a, ArmWriteAgent)]
        if not writers:
            return {"ok": False, "error": "no arm_write agent in config"}
        if not readers:
            return {
                "ok": False,
                "error": "arm_write requires a paired arm (read) agent; refuse motion without live joints",
            }

        agent: ArmWriteAgent
        if agent_id:
            found = self.agents.get(agent_id)
            if not isinstance(found, ArmWriteAgent):
                return {"ok": False, "error": f"agent {agent_id!r} is not arm_write"}
            agent = found
        else:
            agent = writers[0]

        reader = readers[0]
        # Prefer read agent that shares the same sensor_id as the writer.
        for cand in readers:
            if cand.sensor_id == agent.sensor_id:
                reader = cand
                break

        def _read_joints() -> list[float] | None:
            fr = reader.ring.latest.get()
            if fr is None:
                return None
            raw = fr.payload.get("joints_rad")
            if not isinstance(raw, list) or not raw:
                return None
            try:
                return [float(x) for x in raw]
            except (TypeError, ValueError):
                return None

        if arm:
            base = _read_joints()
            if base is None:
                return {
                    "ok": False,
                    "error": "no live arm read joints yet; wait for robot · Read before Arm",
                }
            return agent.command(arm=True)

        if disarm or stop:
            # Always kill teleop + alignment write loops before disarm/estop.
            self.set_gello_arm_teleop(enabled=False)
            self.set_gello_arm_sync(enabled=False)
            return agent.command(disarm=bool(disarm or stop), stop=True)

        if self._arm_teleop_enabled:
            return {
                "ok": False,
                "error": "gello→arm teleop active; 解除摇操 before jog",
                **self.gello_arm_teleop_status(),
            }

        if self._arm_sync_enabled:
            return {
                "ok": False,
                "error": "gello→arm sync active; cancel sync before jog",
                **self.gello_arm_sync_status(),
            }

        if jog_joint is not None:
            base = _read_joints()
            if base is None:
                return {
                    "ok": False,
                    "error": "no live arm read joints; refuse jog without current pose",
                }
            if delta_rad is not None:
                d = float(delta_rad)
            elif delta_deg is not None:
                d = math.radians(float(delta_deg))
            else:
                return {"ok": False, "error": "delta_rad or delta_deg required for jog"}
            idx = int(jog_joint)
            if idx < 0 or idx >= len(base):
                return {"ok": False, "error": f"jog_joint out of range 0..{len(base)-1}"}
            target = list(base)
            target[idx] = float(base[idx]) + d
            # Absolute write anchored to read pose (guards still apply max Δq vs reference).
            return agent.command(joints_rad=target, reference_joints_rad=base)

        if joints_rad is not None:
            base = _read_joints()
            if base is None:
                return {
                    "ok": False,
                    "error": "no live arm read joints; refuse absolute command without current pose",
                }
            return agent.command(joints_rad=list(joints_rad), reference_joints_rad=base)

        return {"ok": False, "error": "arm, disarm/stop, joints_rad, or jog_joint required"}

    def serve(self) -> None:
        """Blocking: start agents + uvicorn viz server until SIGINT."""
        rt = self.cfg.runtime
        app = create_viz_app(
            self.hub,
            self.status,
            recorder=self.recorder,
            gripper_command=self.gripper_command,
            gripper_gello_sync=self.set_gripper_gello_sync,
            gripper_gello_sync_status=self.gripper_gello_sync_status,
            arm_command=self.arm_command,
            gello_arm_sync=self.set_gello_arm_sync,
            gello_arm_sync_status=self.gello_arm_sync_status,
            gello_arm_teleop=self.set_gello_arm_teleop,
            gello_arm_teleop_status=self.gello_arm_teleop_status,
            shutdown=self.request_shutdown,
        )
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=rt.viz_host,
                port=rt.viz_port,
                log_level="info",
                access_log=False,
            )
        )
        self._uvicorn_exit = lambda: setattr(self._server, "should_exit", True)

        def _handle_sig(*_args: object) -> None:
            self._stop.set()
            if self._server is not None:
                self._server.should_exit = True

        signal.signal(signal.SIGINT, _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        self.start()
        print(
            f"[sensors-dcs] site={self.cfg.site} dry_run={self.manager.ctx.dry_run} "
            f"viz=http://{rt.viz_host}:{rt.viz_port}/ "
            f"save_dir={self.recorder.save_dir} episode={self.recorder.episode_index}",
            flush=True,
        )
        try:
            self._server.run()
        finally:
            self.stop()
            print("[sensors-dcs] stopped", flush=True)
