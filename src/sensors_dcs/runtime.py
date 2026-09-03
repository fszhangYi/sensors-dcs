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

    def status(self) -> dict[str, Any]:
        return {
            "config": config_summary(self.cfg),
            "agents": {aid: a.stats() for aid, a in self.agents.items()},
            "sensors_site": self.manager.bundle.site,
            "sensors_dry_run": self.manager.ctx.dry_run,
            "record": self.recorder.status(),
            "gripper_gello_sync": self.gripper_gello_sync_status(),
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
            return agent.command(disarm=bool(disarm or stop), stop=True)

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
