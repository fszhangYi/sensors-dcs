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
from sensors_dcs.agents.gello_reinforce_agent import GelloReinforceAgent
from sensors_dcs.agents.pi05_agent import Pi05ClientAgent
from sensors_dcs.config import (
    DcsConfig,
    config_summary,
    parse_home_duration_s,
    parse_home_joints,
    upsert_home_yaml,
)
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController
from sensors_dcs.viz import VizHub, create_viz_app

# Fold Gello joint deltas into the cumulative bias only after this much motion.
# Smaller residuals stay in pending so encoder noise does not drift the arm.
_OFFSET_FOLD_JOINT_RAD = 1e-3
_OFFSET_FOLD_GRIP = 2e-3


def _offset7(vals: list[float] | None) -> list[float]:
    raw = list(vals or [])
    if len(raw) < 7:
        raw = raw + [0.0] * 7
    return [float(raw[i]) for i in range(7)]


def _pending_ready(pending: list[float]) -> bool:
    if any(abs(pending[i]) >= _OFFSET_FOLD_JOINT_RAD for i in range(6)):
        return True
    return abs(pending[6]) >= _OFFSET_FOLD_GRIP


class Orchestrator:
    """MVP runtime: start listed agents + viz publisher + record consumer."""

    def __init__(self, cfg: DcsConfig) -> None:
        self.cfg = cfg
        from sensors_dcs.arm_pose import set_active_kinematics, set_tcp_xyz
        from sensors_dcs.arm_viz import resolve_arm_viz_model

        # Online FK/IK flange→TCP offset (YAML ``tcp_xyz``; default 0,0,0.18).
        self._tcp_xyz = list(set_tcp_xyz(getattr(cfg, "tcp_xyz", None)))
        # Infer URDF + FK/IK pack from sensors devices linked by arm_write agents.
        arm_viz = resolve_arm_viz_model(
            sensors_config=cfg.sensors_config,
            agents=cfg.agents,
        )
        kin_pkg, kin_prof = set_active_kinematics(
            arm_viz.get("kinematics"),
            arm_viz.get("kinematics_profile"),
        )
        # Warm IK early so Fake/TCP paths don't hit a sticky failed cache mid-run.
        try:
            from sensors_dcs.arm_pose import _get_ik_backend, ik_backend_error

            ik_b = _get_ik_backend(retry=True)
            if ik_b is False:
                print(
                    f"[sensors-dcs] IK warm failed ({kin_pkg}/{kin_prof}): "
                    f"{ik_backend_error()}",
                    flush=True,
                )
            else:
                print(
                    f"[sensors-dcs] IK warm ok ({kin_pkg}/{kin_prof})",
                    flush=True,
                )
        except Exception as e:  # noqa: BLE001
            print(f"[sensors-dcs] IK warm skipped: {e}", flush=True)
        dry = cfg.dry_run
        self.manager = SensorManager.from_yaml(cfg.sensors_config, dry_run=dry)
        self.agents: dict[str, BaseAgent] = {}
        known = self.manager.ids()
        for acfg in cfg.agents:
            if acfg.type in ("pi05", "gello_reinforce"):
                self.agents[acfg.id] = build_agent(acfg)
                continue
            try:
                sensor = self.manager.get(acfg.sensor_id or "")
            except KeyError as e:
                raise KeyError(
                    f"agent {acfg.id!r} sensor_id={acfg.sensor_id!r} not in "
                    f"sensors_config={cfg.sensors_config!r}; known devices: {known}. "
                    f"For robot_write, both YAMLs must use id arm-elite "
                    f"(old seeds used arm-elite-write — re-copy "
                    f"configs/robot_write.yaml + sensors_robot_write.yaml)."
                ) from e
            self.agents[acfg.id] = build_agent(acfg, sensor)
        for agent in self.agents.values():
            if isinstance(agent, Pi05ClientAgent):
                agent.bind_peers(self.agents)
            if isinstance(agent, GelloReinforceAgent):
                agent.bind_peers(self.agents)
                agent.bind_orchestrator(note_sample=self.note_gello_reinforce_sample)
        self.hub = VizHub()
        self.recorder = RecordController(
            cfg.record,
            agents=self.agents,
            site=cfg.site,
        )
        from sensors_dcs.station_peer import StationPeer

        self.peer = StationPeer(
            cfg,
            agents_summary_fn=self._peer_agents_summary,
            sensors_summary_fn=self._peer_sensors_summary,
        )
        self.peer.bind_record_handlers(
            on_prepare=self._peer_on_prepare,
            on_commit=self._peer_on_commit,
            on_abort=self._peer_on_abort,
            on_stop=self._peer_on_stop,
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
        # Cumulative Gello reinforce offset (docs/gello-reinforce.md).
        self._delta_pose_offset_lock = threading.Lock()
        self._delta_pose_offset_configured = any(
            isinstance(a, GelloReinforceAgent) for a in self.agents.values()
        )
        self._delta_pose_offset_enabled = False
        self._delta_pose_offset: list[float] = [0.0] * 7
        self._delta_pose_offset_pending: list[float] = [0.0] * 7
        self._delta_pose_offset_prev_gello: list[float] | None = None
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
        # B.1-style: setpoint poller (latest-wins) + fixed-Hz write thread.
        self._arm_teleop_lock = threading.Lock()
        self._arm_teleop_stop = threading.Event()
        self._arm_teleop_thread: threading.Thread | None = None  # write thread
        self._arm_teleop_setpoint_thread: threading.Thread | None = None
        # Teleop gello queue: list[{"t": float, "q": list[float]}]; pick/commit consume it.
        self._arm_teleop_setpoint: list[dict[str, Any]] = []
        self._arm_teleop_last_sent: list[float] | None = None
        self._arm_teleop_pending: dict[str, Any] | None = None
        self._arm_teleop_pick_mode = str(
            getattr(self.cfg.gello_arm_teleop, "pick_mode", "latest") or "latest"
        )
        self._arm_teleop_lag_path_rad: float | None = None
        self._arm_teleop_lag_age_s: float | None = None
        self._arm_teleop_last_step_rad: float | None = None
        self._arm_teleop_catch_up = False
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
        self._arm_teleop_rate_limited = False  # always False; step rate-limit removed
        self._arm_teleop_hz = 100.0
        self._arm_teleop_write_path: str | None = None
        # Fake teleop: dense Cartesian path → IK joints (diagnose arm vs gello stutter).
        self._fake_teleop_lock = threading.Lock()
        self._fake_teleop_stop = threading.Event()
        self._fake_teleop_thread: threading.Thread | None = None
        self._fake_teleop_joints: list[list[float]] = []
        self._fake_teleop_ready = False
        self._fake_teleop_phase = "idle"  # idle|ready|running|error
        self._fake_teleop_last_ok: bool | None = None
        self._fake_teleop_last_error: str | None = None
        self._fake_teleop_last_message: str | None = None
        self._fake_teleop_write_count = 0
        self._fake_teleop_index = 0
        self._fake_teleop_n = 0
        self._fake_teleop_hz = 100.0
        self._fake_teleop_meta: dict[str, Any] = {}
        self._fake_teleop_writer_id: str | None = None
        # Timed absolute joints ramp (UI: duration slider 1–30s @ ramp_hz).
        self._abs_ramp_lock = threading.Lock()
        self._abs_ramp_stop = threading.Event()
        self._abs_ramp_thread: threading.Thread | None = None
        self._abs_ramp_enabled = False
        self._abs_ramp_phase = "idle"
        self._abs_ramp_index = 0
        self._abs_ramp_n = 0
        self._abs_ramp_duration_s = 0.0
        self._abs_ramp_hz = 5.0
        self._abs_ramp_arm_id: str | None = None
        self._abs_ramp_writer_id: str | None = None
        self._abs_ramp_last_ok: bool | None = None
        self._abs_ramp_last_error: str | None = None
        self._abs_ramp_last_message: str | None = None
        self._abs_ramp_write_count = 0
        self._abs_ramp_last_t_wall: float | None = None
        self._abs_ramp_target_joints: list[float] | None = None
        self._abs_ramp_timing: str | None = None
        self._abs_ramp_delta_max: float | None = None
        self._abs_ramp_gripper: float | None = None
        # Home pose (rad); seeded from YAML, updatable at runtime / via settings save.
        self._home_lock = threading.Lock()
        hj, _herr = parse_home_joints(getattr(cfg, "home_joints_rad", None))
        self._home_joints_rad: list[float] | None = hj
        self._home_duration_s: float = parse_home_duration_s(
            getattr(cfg, "home_duration_s", None), default=20.0
        )
        self._home_source: str = "yaml" if hj is not None else "unset"
        self._sync_home_to_arm_agents()

    def _sync_home_to_arm_agents(self) -> None:
        """Push home into ArmAgent dry-run synth and seed shared arm_write last_cmd."""
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        with self._home_lock:
            hj = list(self._home_joints_rad) if self._home_joints_rad is not None else None
        for agent in self.agents.values():
            if isinstance(agent, ArmAgent):
                agent.set_home_joints_rad(hj)
        if not bool(getattr(self.manager.ctx, "dry_run", False)) or hj is None:
            return
        # Shared arm_write device: seed last command so Read returns home (not zeros).
        for agent in self.agents.values():
            if not isinstance(agent, ArmWriteAgent):
                continue
            sens = agent.sensor
            last = getattr(sens, "_last_cmd_rad", None)
            if last is None and hasattr(sens, "_last_cmd_rad"):
                try:
                    sens._last_cmd_rad = [float(x) for x in hj[: int(getattr(sens, "num_joints", 6) or 6)]]
                except Exception:  # noqa: BLE001
                    pass

    def _peer_agents_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for aid, agent in self.agents.items():
            out.append(
                {
                    "id": aid,
                    "kind": getattr(agent, "kind", None) or type(agent).__name__,
                    "sensor_id": getattr(agent, "sensor_id", None),
                }
            )
        return out

    def _peer_sensors_summary(self) -> dict[str, Any]:
        try:
            ids = list(self.manager.ids())
        except Exception:  # noqa: BLE001
            ids = []
        return {
            "site": getattr(self.manager.bundle, "site", None),
            "dry_run": bool(getattr(self.manager.ctx, "dry_run", False)),
            "device_ids": ids,
        }

    def _peer_on_prepare(
        self, mode: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        st = self.recorder.status()
        if st.get("state") != "idle":
            return {"ok": False, "error": f"recorder busy state={st.get('state')}"}
        body = body or {}
        if body.get("episode_index") is not None:
            try:
                ep = int(body["episode_index"])
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": f"invalid episode_index: {body.get('episode_index')!r}",
                }
            from sensors_dcs.record import _episode_dir

            ep_dir = _episode_dir(self.recorder.save_dir, ep)
            if ep_dir.exists():
                return {
                    "ok": False,
                    "error": f"episode dir already exists: {ep_dir}",
                }
        return {"ok": True, "mode": mode}

    def _peer_on_commit(self, mode: str, session_meta: dict[str, Any]) -> dict[str, Any]:
        return self.recorder.start(mode=mode, session_meta=session_meta)

    def _peer_on_abort(self, session_id: str, reason: str) -> None:
        st = self.recorder.status()
        if st.get("state") == "recording":
            try:
                self.recorder.stop(valid=False, async_flush=False, keep_files=False)
            except Exception as e:  # noqa: BLE001
                print(f"[sensors-dcs] peer abort stop failed ({session_id}): {e}", flush=True)
        print(f"[sensors-dcs] peer abort session={session_id}: {reason}", flush=True)

    def _peer_on_stop(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.recorder.stop(
            valid=bool(body.get("valid", True)),
            async_flush=bool(body.get("async_flush", False)),
            keep_files=bool(body.get("keep_files", True)),
        )

    def coordinated_record_start(self, *, mode: str = "collect") -> dict[str, Any]:
        """Master with slaves → barrier; otherwise legacy RecordController.start."""
        if self.peer.has_active_slaves():
            return self.peer.barrier_start(
                mode=mode,
                local_start=self.recorder.start,
                episode_index=int(self.recorder.episode_index),
            )
        return self.recorder.start(mode=mode)

    def coordinated_record_stop(
        self,
        *,
        valid: bool = True,
        async_flush: bool = False,
        keep_files: bool = True,
    ) -> dict[str, Any]:
        if self.peer.has_active_slaves():
            return self.peer.barrier_stop(
                local_stop=self.recorder.stop,
                valid=valid,
                async_flush=async_flush,
                keep_files=keep_files,
            )
        return self.recorder.stop(
            valid=valid, async_flush=async_flush, keep_files=keep_files
        )

    def status(self) -> dict[str, Any]:
        from sensors_dcs.config import station_summary

        return {
            "config": config_summary(self.cfg),
            "station": station_summary(self.cfg),
            "peer": self.peer.status(),
            "agents": {aid: a.stats() for aid, a in self.agents.items()},
            "sensors_site": self.manager.bundle.site,
            "sensors_dry_run": self.manager.ctx.dry_run,
            "record": self.recorder.status(),
            "gripper_gello_sync": self.gripper_gello_sync_status(),
            "gello_arm_sync": self.gello_arm_sync_status(),
            "gello_arm_teleop": self.gello_arm_teleop_status(),
            "gello_trace": self.gello_trace_status(),
            "fake_teleop": self.fake_teleop_status(),
            "arm_abs_ramp": self.arm_abs_ramp_status(),
            "arm_home": self.arm_home_status(),
            "pi05": self.pi05_status(),
            "delta_pose_offset": self.delta_pose_offset_status(),
        }

    def arm_home_status(self) -> dict[str, Any]:
        with self._home_lock:
            joints = list(self._home_joints_rad) if self._home_joints_rad is not None else None
            source = self._home_source
            duration_s = float(self._home_duration_s)
        ok_joints, err = parse_home_joints(joints)
        cart = None
        if ok_joints is not None:
            try:
                from sensors_dcs.arm_pose import joints_rad_to_xyzrpy

                cart = joints_rad_to_xyzrpy(ok_joints)
            except Exception:  # noqa: BLE001
                cart = None
        return {
            "ok": err is None,
            "home_joints_rad": ok_joints,
            "home_duration_s": duration_s,
            "home_cartesian_xyzrpy": cart,
            "source": source,
            "error": err,
            "configured": ok_joints is not None,
        }

    def set_arm_home(
        self,
        *,
        joints_rad: list[float] | None = None,
        from_live: bool = False,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        """Update in-memory home (joints and/or Home ramp duration)."""
        updates: dict[str, Any] = {}
        if from_live or joints_rad is not None:
            if from_live:
                from sensors_dcs.agents.arm_agent import ArmAgent

                readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
                if not readers:
                    return {"ok": False, "error": "no arm_read agent", **self.arm_home_status()}
                live = self._joints6_from_ring(readers[0])
                if live is None:
                    return {
                        "ok": False,
                        "error": "no live arm joints; wait for robot · Read",
                        **self.arm_home_status(),
                    }
                joints_rad = live
            parsed, err = parse_home_joints(joints_rad)
            if parsed is None:
                return {
                    "ok": False,
                    "error": err or "invalid home_joints_rad",
                    **self.arm_home_status(),
                }
            with self._home_lock:
                self._home_joints_rad = list(parsed)
                self._home_source = "live" if from_live else "set"
            updates["home_joints_rad"] = list(parsed)
            self._sync_home_to_arm_agents()
        if duration_s is not None:
            dur = parse_home_duration_s(duration_s)
            with self._home_lock:
                self._home_duration_s = dur
            updates["home_duration_s"] = dur
        if not updates:
            return {"ok": False, "error": "nothing to update", **self.arm_home_status()}
        self.cfg = self.cfg.model_copy(update=updates)
        return {"ok": True, **self.arm_home_status()}

    def save_arm_home_to_yaml(self, *, path: str | None = None) -> dict[str, Any]:
        """Persist current in-memory home joints into the selected/active DCS YAML."""
        from sensors_dcs.paths import default_dcs_config
        from sensors_dcs.reexec import validate_dcs_config_file

        st = self.arm_home_status()
        if not st.get("configured"):
            return {"ok": False, "error": st.get("error") or "home 未配置", **st}
        try:
            target = validate_dcs_config_file(path or str(default_dcs_config()))
            upsert_home_yaml(
                target,
                joints=list(st["home_joints_rad"]),
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), **self.arm_home_status()}
        with self._home_lock:
            self._home_source = "yaml"
        return {
            "ok": True,
            "path": str(target),
            "message": f"已写入 {target}",
            **self.arm_home_status(),
        }

    def go_arm_home(
        self,
        *,
        duration_s: float | None = None,
        agent_id: str | None = None,
        t_min_s: float | None = None,
        t_max_s: float | None = None,
        v_norm_rad_s: float | None = None,
        jerk_seg_frac: float | None = None,
        accel_seg_frac: float | None = None,
    ) -> dict[str, Any]:
        """Sugar for abs-send to configured ``home_joints_rad``.

        Same joint-space ramp as Infer/Collect「下发」:
        ``T=clamp(d/v_norm, t_min, t_max)`` + configured profile (default 7-seg jerk).
        ``duration_s`` is ignored (kept for API compatibility).
        """
        del duration_s  # Home no longer uses a dedicated fixed duration.
        st = self.arm_home_status()
        if not st.get("configured"):
            return {
                "ok": False,
                "error": st.get("error") or "home_joints_rad 未配置或格式错误",
                **st,
            }
        if getattr(self, "_delta_pose_offset_configured", False):
            self.clear_delta_pose_offset()
        out = self.arm_command(
            agent_id=agent_id,
            joints_rad=list(st["home_joints_rad"]),
            timing="scale_by_d",
            t_min_s=t_min_s,
            t_max_s=t_max_s,
            v_norm_rad_s=v_norm_rad_s,
            jerk_seg_frac=jerk_seg_frac,
            accel_seg_frac=accel_seg_frac,
            apply_gello_bias=False,
        )
        out = dict(out)
        out["home"] = self.arm_home_status()
        return out

    def _pi05_agent(self, agent_id: str | None = None) -> Pi05ClientAgent | None:
        if agent_id:
            a = self.agents.get(agent_id)
            return a if isinstance(a, Pi05ClientAgent) else None
        for a in self.agents.values():
            if isinstance(a, Pi05ClientAgent):
                return a
        return None

    def pi05_status(self, agent_id: str | None = None) -> dict[str, Any]:
        agent = self._pi05_agent(agent_id)
        if agent is None:
            return {
                "ok": False,
                "configured": False,
                "error": "no pi05 agent in config",
                "connected": False,
                "running": False,
            }
        return {"configured": True, **agent.status_payload(), "ok": True}

    def pi05_connect(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        agent = self._pi05_agent(agent_id)
        if agent is None:
            return {"ok": False, "configured": False, "error": "no pi05 agent in config"}
        out = agent.connect(host=host, port=port)
        if out.get("ok"):
            print(
                f"[sensors-dcs] pi05 connected → {out.get('host')}:{out.get('port')}",
                flush=True,
            )
        else:
            print(
                f"[sensors-dcs] pi05 connect failed: {out.get('error')}",
                flush=True,
            )
        return out

    def pi05_disconnect(self, *, agent_id: str | None = None) -> dict[str, Any]:
        agent = self._pi05_agent(agent_id)
        if agent is None:
            return {"ok": False, "configured": False, "error": "no pi05 agent in config"}
        out = agent.disconnect()
        print("[sensors-dcs] pi05 disconnected", flush=True)
        return out

    def pi05_set_prompt(self, prompt: str, *, agent_id: str | None = None) -> dict[str, Any]:
        agent = self._pi05_agent(agent_id)
        if agent is None:
            return {"ok": False, "configured": False, "error": "no pi05 agent in config"}
        return agent.set_prompt(prompt)

    def pi05_step(
        self,
        *,
        agent_id: str | None = None,
        prompt: str | None = None,
        robot_state_format: str | None = None,
        next_state_format: str | None = None,
        tcp_clip: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from sensors_dcs.arm_pose import (
            DEFAULT_RECV_STATE_FORMAT,
            DEFAULT_SEND_STATE_FORMAT,
            default_tcp_clip,
            normalize_recv_state_format,
            normalize_send_state_format,
            normalize_tcp_clip,
        )

        agent = self._pi05_agent(agent_id)
        if agent is None:
            return {"ok": False, "configured": False, "error": "no pi05 agent in config"}
        try:
            send_fmt = normalize_send_state_format(
                robot_state_format or DEFAULT_SEND_STATE_FORMAT
            )
            recv_fmt = normalize_recv_state_format(
                next_state_format or DEFAULT_RECV_STATE_FORMAT
            )
            clip_bounds = normalize_tcp_clip(tcp_clip)
        except ValueError as e:
            return {
                "ok": False,
                "configured": True,
                "error": str(e),
                "robot_state_format": robot_state_format,
                "next_state_format": next_state_format,
                "next_joints_rad": None,
                "ik_ok": False,
                "ik_error": str(e),
                "goal_xyzrpy": None,
                "goal_xyzrpy_raw": None,
                "tcp_clip": default_tcp_clip() if tcp_clip is None else tcp_clip,
                "tcp_clip_applied": False,
                "next_grip": None,
                "grip_ok": False,
                "grip_error": str(e),
            }
        if prompt is not None:
            agent.set_prompt(prompt)
        print(
            f"[sensors-dcs] pi05 step → gathering sensors / querying serve…  "
            f"send={send_fmt} recv={recv_fmt}",
            flush=True,
        )
        out = agent.step(robot_state_format=send_fmt)
        out["robot_state_format"] = send_fmt
        out["next_state_format"] = recv_fmt
        out["tcp_clip"] = clip_bounds
        if out.get("ok"):
            ns = out.get("next_state")
            lat = out.get("latency_ms")
            print(
                f"[sensors-dcs] pi05 step ok  latency_ms={lat}  "
                f"next_state={ns}  term={out.get('term_flag')} reject={out.get('reject_flag')}",
                flush=True,
            )
            decoded = self._decode_next_state(ns, recv_fmt, tcp_clip=clip_bounds)
            out["next_joints_rad"] = decoded.get("joints_rad")
            out["ik_ok"] = bool(decoded.get("ok"))
            out["ik_error"] = decoded.get("error")
            out["goal_xyzrpy"] = decoded.get("goal_xyzrpy")
            out["goal_xyzrpy_raw"] = decoded.get("goal_xyzrpy_raw")
            out["tcp_clip_applied"] = bool(decoded.get("tcp_clip_applied"))
            agent.note_wire_meta(
                goal_xyzrpy=decoded.get("goal_xyzrpy"),
                robot_state_format=send_fmt,
                next_state_format=recv_fmt,
            )
            try:
                agent._push_status_frame()
            except Exception:  # noqa: BLE001
                pass
            if decoded.get("ok"):
                print(
                    f"[sensors-dcs] pi05 decode ok ({recv_fmt})  "
                    f"next_joints_rad={decoded.get('joints_rad')}",
                    flush=True,
                )
            else:
                print(
                    f"[sensors-dcs] pi05 decode failed ({recv_fmt}): {decoded.get('error')}",
                    flush=True,
                )
            # Dim 7 = gripper position_norm. Not sent on step: the abs ramp
            # writes it, and adds the live Gello grip bias whenever that bias moves.
            grip = self._next_state_grip(ns)
            out["next_grip"] = grip
            if grip is None:
                out["grip_ok"] = False
                out["grip_deferred"] = False
                out["grip_error"] = "next_state missing grip (need len>=7)"
            else:
                out["grip_ok"] = True
                out["grip_deferred"] = True
                out["grip_error"] = None
                print(
                    f"[sensors-dcs] pi05 grip deferred  position_norm={grip} "
                    f"(abs-ramp, plus live gello grip bias)",
                    flush=True,
                )
        else:
            print(f"[sensors-dcs] pi05 step failed: {out.get('error')}", flush=True)
            out.setdefault("next_joints_rad", None)
            out.setdefault("ik_ok", False)
            out.setdefault("ik_error", out.get("error"))
            out.setdefault("goal_xyzrpy", None)
            out.setdefault("goal_xyzrpy_raw", None)
            out.setdefault("tcp_clip_applied", False)
            out.setdefault("next_grip", None)
            out.setdefault("grip_ok", False)
            out.setdefault("grip_deferred", False)
            out.setdefault("grip_error", out.get("error"))
        return out

    def _arm_read_agent(self):
        from sensors_dcs.agents.arm_agent import ArmAgent

        readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
        return readers[0] if readers else None

    def _current_tcp_xyzrpy(self) -> list[float] | None:
        reader = self._arm_read_agent()
        if reader is None:
            return None
        fr = reader.ring.latest.get()
        if fr is None:
            return None
        xyzrpy = (fr.payload or {}).get("cartesian_xyzrpy")
        if not isinstance(xyzrpy, (list, tuple)) or len(xyzrpy) < 6:
            return None
        try:
            out = [float(xyzrpy[i]) for i in range(6)]
        except (TypeError, ValueError):
            return None
        if any(x != x or abs(x) == float("inf") for x in out):
            return None
        return out

    def _decode_next_state(
        self,
        next_state: Any,
        recv_fmt: str,
        *,
        tcp_clip: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Decode serve ``next_state`` → joints + absolute TCP goal for HUD."""
        from sensors_dcs.arm_pose import (
            compose_delta_xyzrpy,
            default_tcp_clip,
            joints_rad_to_xyzrpy,
            normalize_recv_state_format,
        )

        clip_bounds = tcp_clip if isinstance(tcp_clip, dict) else default_tcp_clip()
        fmt = normalize_recv_state_format(recv_fmt)
        if not isinstance(next_state, (list, tuple)) or len(next_state) < 6:
            return {
                "ok": False,
                "error": "next_state missing 6-d core",
                "joints_rad": None,
                "goal_xyzrpy": None,
                "goal_xyzrpy_raw": None,
                "tcp_clip_applied": False,
            }

        if fmt == "joints":
            try:
                joints = [float(next_state[i]) for i in range(6)]
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "next_state joints not numeric",
                    "joints_rad": None,
                    "goal_xyzrpy": None,
                    "goal_xyzrpy_raw": None,
                    "tcp_clip_applied": False,
                }
            if any(x != x or abs(x) == float("inf") for x in joints):
                return {
                    "ok": False,
                    "error": "next_state joints non-finite",
                    "joints_rad": None,
                    "goal_xyzrpy": None,
                    "goal_xyzrpy_raw": None,
                    "tcp_clip_applied": False,
                }
            goal = joints_rad_to_xyzrpy(joints)
            decoded = {
                "ok": True,
                "error": None,
                "joints_rad": joints,
                "goal_xyzrpy": goal,
            }
            return self._finish_decoded_goal(decoded, recv_fmt=fmt, tcp_clip=clip_bounds)

        if fmt == "delta_pose":
            cur = self._current_tcp_xyzrpy()
            if cur is None:
                return {
                    "ok": False,
                    "error": "no live TCP pose to compose delta_pose",
                    "joints_rad": None,
                    "goal_xyzrpy": None,
                    "goal_xyzrpy_raw": None,
                    "tcp_clip_applied": False,
                }
            try:
                abs_pose = compose_delta_xyzrpy(cur, list(next_state)[:6])
            except Exception as e:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": f"delta_pose compose failed: {e}",
                    "joints_rad": None,
                    "goal_xyzrpy": None,
                    "goal_xyzrpy_raw": None,
                    "tcp_clip_applied": False,
                }
            return self._finish_decoded_goal(
                {
                    "ok": True,
                    "error": None,
                    "joints_rad": None,
                    "goal_xyzrpy": [float(x) for x in abs_pose[:6]],
                },
                recv_fmt=fmt,
                tcp_clip=clip_bounds,
            )

        # pose (absolute TCP): no IK until offset is composed (identity if zero).
        try:
            goal = [float(next_state[i]) for i in range(6)]
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "next_state pose not numeric",
                "joints_rad": None,
                "goal_xyzrpy": None,
                "goal_xyzrpy_raw": None,
                "tcp_clip_applied": False,
            }
        if any(x != x or abs(x) == float("inf") for x in goal):
            return {
                "ok": False,
                "error": "next_state pose non-finite",
                "joints_rad": None,
                "goal_xyzrpy": None,
                "goal_xyzrpy_raw": None,
                "tcp_clip_applied": False,
            }
        return self._finish_decoded_goal(
            {
                "ok": True,
                "error": None,
                "joints_rad": None,
                "goal_xyzrpy": goal,
            },
            recv_fmt=fmt,
            tcp_clip=clip_bounds,
        )

    def _finish_decoded_goal(
        self,
        decoded: dict[str, Any],
        *,
        recv_fmt: str,
        tcp_clip: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Clip absolute TCP xyz (optional), then IK. Gello bias is not applied here."""
        from sensors_dcs.arm_pose import (
            clip_tcp_xyzrpy,
            default_tcp_clip,
            joints_rad_to_xyzrpy,
            tcp_clip_is_active,
        )

        if not decoded.get("ok"):
            decoded.setdefault("goal_xyzrpy_raw", None)
            decoded.setdefault("tcp_clip_applied", False)
            return decoded

        bounds = tcp_clip if isinstance(tcp_clip, dict) else default_tcp_clip()
        active = tcp_clip_is_active(bounds)

        # joints passthrough only when workspace clip is fully off.
        if (
            recv_fmt == "joints"
            and decoded.get("joints_rad") is not None
            and not active
        ):
            goal = decoded.get("goal_xyzrpy")
            if goal is None:
                joints = decoded.get("joints_rad")
                if isinstance(joints, (list, tuple)) and len(joints) >= 6:
                    goal = joints_rad_to_xyzrpy(list(joints)[:6])
                    decoded["goal_xyzrpy"] = goal
            raw = [float(x) for x in list(goal)[:6]] if goal is not None else None
            return {
                **decoded,
                "goal_xyzrpy_raw": raw,
                "tcp_clip_applied": False,
            }

        goal = decoded.get("goal_xyzrpy")
        if goal is None and recv_fmt == "joints":
            joints = decoded.get("joints_rad")
            if isinstance(joints, (list, tuple)) and len(joints) >= 6:
                goal = joints_rad_to_xyzrpy(list(joints)[:6])
        if goal is None:
            return {
                **decoded,
                "ok": False,
                "error": decoded.get("error") or "cannot IK without abs TCP goal",
                "joints_rad": None,
                "goal_xyzrpy_raw": None,
                "tcp_clip_applied": False,
            }

        clipped = clip_tcp_xyzrpy(goal, bounds)
        if not clipped.get("ok"):
            return {
                **decoded,
                "ok": False,
                "error": clipped.get("error") or "tcp clip failed",
                "joints_rad": None,
                "goal_xyzrpy": None,
                "goal_xyzrpy_raw": clipped.get("goal_xyzrpy_raw"),
                "tcp_clip_applied": False,
            }
        goal_clipped = [float(x) for x in list(clipped["goal_xyzrpy"])[:6]]
        raw = [float(x) for x in list(clipped["goal_xyzrpy_raw"])[:6]]
        applied = bool(clipped.get("applied"))

        ik = self._xyzrpy_to_joints(goal_clipped)
        ik["goal_xyzrpy"] = goal_clipped
        ik["goal_xyzrpy_raw"] = raw
        ik["tcp_clip_applied"] = applied
        return ik

    def joints_plus_gello_bias(self, joints6: list[float]) -> list[float]:
        """Add the live cumulative joint bias. Used at each absolute-ramp write."""
        bias = self.effective_delta_pose_offset()
        return [float(joints6[i]) + float(bias[i]) for i in range(6)]

    def delta_pose_offset_status(self) -> dict[str, Any]:
        with getattr(self, "_delta_pose_offset_lock", threading.Lock()):
            configured = bool(getattr(self, "_delta_pose_offset_configured", False))
            enabled = bool(getattr(self, "_delta_pose_offset_enabled", False)) and configured
            offset = _offset7(getattr(self, "_delta_pose_offset", None))
            pending = _offset7(getattr(self, "_delta_pose_offset_pending", None))
            prev = getattr(self, "_delta_pose_offset_prev_gello", None)
            effective = list(offset) if enabled else [0.0] * 7
            return {
                "configured": configured,
                "enabled": enabled,
                "offset": offset,
                "pending": pending,
                "effective": effective,
                "prev_set": prev is not None,
            }

    def effective_delta_pose_offset(self) -> list[float]:
        with getattr(self, "_delta_pose_offset_lock", threading.Lock()):
            configured = bool(getattr(self, "_delta_pose_offset_configured", False))
            enabled = bool(getattr(self, "_delta_pose_offset_enabled", False))
            if not configured or not enabled:
                return [0.0] * 7
            return _offset7(getattr(self, "_delta_pose_offset", None))

    def _reset_delta_pose_offset_locked(self) -> None:
        """Drop the cumulative offset and treat the current Gello pose as zero."""
        self._delta_pose_offset = [0.0] * 7
        self._delta_pose_offset_pending = [0.0] * 7
        self._delta_pose_offset_prev_gello = self._live_gello_joints7()

    def clear_delta_pose_offset(self) -> dict[str, Any]:
        """Zero the cumulative offset. Next absolute targets no longer include it."""
        if not getattr(self, "_delta_pose_offset_configured", False):
            return {
                "ok": False,
                "error": "gello_reinforce not configured",
                **self.delta_pose_offset_status(),
            }
        with self._delta_pose_offset_lock:
            self._reset_delta_pose_offset_locked()
        return {"ok": True, **self.delta_pose_offset_status()}

    def set_delta_pose_offset(self, offset: list[float] | None) -> dict[str, Any]:
        if not getattr(self, "_delta_pose_offset_configured", False):
            return {
                "ok": False,
                "error": "gello_reinforce not configured",
                **self.delta_pose_offset_status(),
            }
        if offset is None:
            return {"ok": False, "error": "offset required", **self.delta_pose_offset_status()}
        try:
            vals = [float(x) for x in list(offset)[:7]]
        except (TypeError, ValueError):
            return {"ok": False, "error": "offset must be 7 floats", **self.delta_pose_offset_status()}
        if len(vals) < 7 or any(not (v == v) or abs(v) == float("inf") for v in vals):
            return {
                "ok": False,
                "error": "offset must be 7 finite floats",
                **self.delta_pose_offset_status(),
            }
        with self._delta_pose_offset_lock:
            self._delta_pose_offset = vals
            self._delta_pose_offset_pending = [0.0] * 7
            self._delta_pose_offset_prev_gello = self._live_gello_joints7()
        return {"ok": True, **self.delta_pose_offset_status()}

    def set_delta_pose_offset_enabled(self, enabled: bool) -> dict[str, Any]:
        if not getattr(self, "_delta_pose_offset_configured", False):
            return {
                "ok": False,
                "error": "gello_reinforce not configured",
                **self.delta_pose_offset_status(),
            }
        with self._delta_pose_offset_lock:
            self._delta_pose_offset_enabled = bool(enabled)
            # Both edges start from zero. Disable must not leave a stale offset
            # to be reapplied the next time the box is checked.
            self._reset_delta_pose_offset_locked()
        return {"ok": True, **self.delta_pose_offset_status()}

    def _api_set_delta_pose_offset(
        self,
        *,
        enabled: bool | None = None,
        offset: list[float] | None = None,
        clear: bool | None = None,
    ) -> dict[str, Any]:
        """Combined POST handler for Infer UI / HTTP API."""
        if enabled is not None:
            out = self.set_delta_pose_offset_enabled(bool(enabled))
            if not out.get("ok"):
                return out
        if clear:
            out = self.clear_delta_pose_offset()
            if not out.get("ok"):
                return out
        elif offset is not None:
            out = self.set_delta_pose_offset(offset)
            if not out.get("ok"):
                return out
        if enabled is None and offset is None and not clear:
            return {
                "ok": False,
                "error": "enabled, offset, or clear required",
                **self.delta_pose_offset_status(),
            }
        return {"ok": True, **self.delta_pose_offset_status()}

    def _live_gello_joints7(self) -> list[float] | None:
        from sensors_dcs.agents.gello_agent import GelloAgent

        for agent in self.agents.values():
            if not isinstance(agent, GelloAgent):
                continue
            fr = agent.ring.latest.get()
            if fr is None:
                continue
            joints = (fr.payload or {}).get("joints_rad")
            if not isinstance(joints, (list, tuple)) or len(joints) < 7:
                continue
            try:
                out = [float(joints[i]) for i in range(7)]
            except (TypeError, ValueError):
                continue
            if any(not (x == x) or abs(x) == float("inf") for x in out):
                continue
            return out
        return None

    def note_gello_reinforce_sample(self, joints7: list[float]) -> dict[str, Any]:
        """Fold a Gello frame joint delta into the cumulative bias."""
        try:
            cur = [float(joints7[i]) for i in range(7)]
        except (TypeError, ValueError, IndexError):
            return {"ok": False, "error": "joints7 must be 7 floats", "skipped": True}
        if any(not (x == x) or abs(x) == float("inf") for x in cur):
            return {"ok": False, "error": "joints7 non-finite", "skipped": True}

        with self._delta_pose_offset_lock:
            if not self._delta_pose_offset_configured:
                return {
                    "ok": False,
                    "error": "gello_reinforce not configured",
                    "skipped": True,
                }
            if not self._delta_pose_offset_enabled:
                return {
                    "ok": True,
                    "skipped": True,
                    "offset": list(self._delta_pose_offset),
                }
            prev = self._delta_pose_offset_prev_gello
            if prev is None:
                self._delta_pose_offset_prev_gello = list(cur)
                return {
                    "ok": True,
                    "skipped": False,
                    "armed_prev": True,
                    "offset": list(self._delta_pose_offset),
                }

            pending = _offset7(self._delta_pose_offset_pending)
            for i in range(7):
                pending[i] += float(cur[i]) - float(prev[i])
            folded = False
            if _pending_ready(pending):
                off = _offset7(self._delta_pose_offset)
                self._delta_pose_offset = [
                    round(float(off[i]) + float(pending[i]), 6) for i in range(7)
                ]
                self._delta_pose_offset_pending = [0.0] * 7
                folded = True
            else:
                self._delta_pose_offset_pending = pending
            self._delta_pose_offset_prev_gello = list(cur)
            return {
                "ok": True,
                "skipped": False,
                "folded": folded,
                "offset": list(self._delta_pose_offset),
                "pending": list(self._delta_pose_offset_pending),
            }

    def _next_state_to_joints(self, next_state: Any) -> dict[str, Any]:
        """IK TCP pose from pi05 ``next_state`` → arm ``joints_rad`` (pose recv)."""
        if not isinstance(next_state, (list, tuple)) or len(next_state) < 6:
            return {"ok": False, "error": "next_state missing xyzrpy[6]", "joints_rad": None}
        return self._xyzrpy_to_joints(list(next_state)[:6])

    def _ik_seed_joints(self) -> list[float] | None:
        """Live arm_read joints, else configured home — dry_run always has synth live joints."""
        import math

        reader = self._arm_read_agent()
        if reader is not None:
            seed = self._joints6_from_ring(reader)
            if seed is not None:
                return seed
        home = getattr(self, "_home_joints_rad", None)
        if isinstance(home, (list, tuple)) and len(home) >= 6:
            try:
                out = [float(home[i]) for i in range(6)]
            except (TypeError, ValueError):
                return None
            if all(math.isfinite(x) for x in out):
                return out
        return None

    def _xyzrpy_to_joints(
        self,
        xyzrpy: list[float],
        *,
        interactive: bool = False,
    ) -> dict[str, Any]:
        from sensors_dcs.arm_pose import xyzrpy_to_joints_rad

        if self._arm_read_agent() is None:
            return {"ok": False, "error": "no arm_read agent for IK seed", "joints_rad": None}
        seed = self._ik_seed_joints()
        if seed is None:
            return {
                "ok": False,
                "error": "no live arm joints for IK seed",
                "joints_rad": None,
            }
        if interactive:
            # Amber-ball drag (esp. CS66): LM only — soft TRF made drag feel dead.
            return xyzrpy_to_joints_rad(
                xyzrpy,
                q_seed_rad=seed,
                interactive=True,
                max_nfev=80,
                soft_limit_retry=False,
                enforce_teach_soft_limits=False,
                solver_tol=1e-6,
            )
        return xyzrpy_to_joints_rad(xyzrpy, q_seed_rad=seed)

    def arm_ik(self, xyzrpy: list[float] | None) -> dict[str, Any]:
        """Public IK for TCP drag preview (no arm write). Always interactive LM."""
        if not isinstance(xyzrpy, (list, tuple)) or len(xyzrpy) < 6:
            return {"ok": False, "error": "xyzrpy must have 6 floats", "joints_rad": None}
        try:
            vals = [float(x) for x in list(xyzrpy)[:6]]
        except (TypeError, ValueError):
            return {"ok": False, "error": "xyzrpy must be 6 floats", "joints_rad": None}
        if any(not (v == v) or v in (float("inf"), float("-inf")) for v in vals):
            return {"ok": False, "error": "xyzrpy has non-finite values", "joints_rad": None}
        return self._xyzrpy_to_joints(vals, interactive=True)

    @staticmethod
    def _next_state_grip(next_state: Any) -> float | None:
        """Extract gripper ``position_norm`` from serve ``next_state[6]``."""
        if not isinstance(next_state, (list, tuple)) or len(next_state) < 7:
            return None
        try:
            g = float(next_state[6])
        except (TypeError, ValueError):
            return None
        if g != g or g in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return g

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

    def stop(self, *, for_shutdown: bool = False) -> None:
        # Stop viz/console first so the terminal stops "pushing" immediately.
        self._stop.set()
        try:
            self.peer.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[sensors-dcs] peer stop: {e}", flush=True)
        self._arm_teleop_stop.set()
        self._fake_teleop_stop.set()
        self._arm_sync_stop.set()
        self._sync_stop.set()
        self._abs_ramp_stop.set()

        for a in self.agents.values():
            if isinstance(a, Pi05ClientAgent):
                try:
                    a.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        if for_shutdown:
            # Do not join teleop/sync threads or take their locks — a stuck
            # sensor.write holding a lock used to block forever here, so
            # os._exit never ran and agents kept sampling.
            if self.recorder.status().get("state") == "recording":
                try:
                    self.recorder.stop(timeout=1.0, async_flush=True)
                except Exception:  # noqa: BLE001
                    pass
            join_t = 0.25
            for agent in list(self.agents.values()):
                try:
                    agent.stop(join_timeout=join_t, close_sensor=False)
                except TypeError:
                    try:
                        agent.stop()
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass
            print("[sensors-dcs] agents signaled stop (shutdown)", flush=True)
            return

        self.set_gello_arm_teleop(enabled=False)
        self.set_gello_arm_sync(enabled=False)
        self.cancel_arm_abs_ramp()
        self.set_gripper_gello_sync(enabled=False)
        # Finish any open episode before tearing down agents
        if self.recorder.status().get("state") == "recording":
            try:
                self.recorder.stop(timeout=30.0)
            except Exception:  # noqa: BLE001
                pass
        join_t = 2.0
        if self._viz_thread and self._viz_thread.is_alive():
            self._viz_thread.join(timeout=join_t)
        if self._console_thread and self._console_thread.is_alive():
            self._console_thread.join(timeout=join_t)
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
            try:
                agent.stop(join_timeout=join_t, close_sensor=True)
            except TypeError:
                # Older agents without kwargs.
                agent.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.manager.close_all()
        except Exception:  # noqa: BLE001
            pass

    def request_shutdown(self) -> dict[str, Any]:
        """UI/API safe exit: stop publishing immediately, then force process exit.

        Sensor ``close()`` / teleop locks can hang under bad HW/SDK; never block
        the HTTP handler on that. ``os._exit`` is scheduled independently of
        teardown so a stuck ``stop()`` cannot keep the process alive.
        """
        import os

        if getattr(self, "_shutdown_requested", False):
            return {"ok": True, "message": "shutdown already requested"}
        self._shutdown_requested = True

        # Kill console/viz output immediately (before any join/lock).
        self._stop.set()
        self._arm_teleop_stop.set()
        self._arm_sync_stop.set()
        self._sync_stop.set()
        self._abs_ramp_stop.set()
        print("[sensors-dcs] shutdown requested — stopping publishers…", flush=True)

        hook = getattr(self, "_uvicorn_exit", None)
        if callable(hook):
            try:
                hook()
            except Exception as e:  # noqa: BLE001
                print(f"[sensors-dcs] uvicorn exit signal failed: {e}", flush=True)

        def _force_exit() -> None:
            time.sleep(1.0)
            print("[sensors-dcs] exiting", flush=True)
            os._exit(0)

        def _cleanup() -> None:
            try:
                self.stop(for_shutdown=True)
            except Exception as e:  # noqa: BLE001
                print(f"[sensors-dcs] shutdown cleanup error: {e}", flush=True)
            print("[sensors-dcs] cleanup done — exiting", flush=True)
            os._exit(0)

        threading.Thread(target=_force_exit, name="sensors-dcs-force-exit", daemon=True).start()
        threading.Thread(target=_cleanup, name="sensors-dcs-shutdown", daemon=True).start()
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
            "gello_trace": self.gello_trace_status(),
            "fake_teleop": self.fake_teleop_status(),
            "arm_abs_ramp": self.arm_abs_ramp_status(),
            "arm_home": self.arm_home_status(),
            "delta_pose_offset": self.delta_pose_offset_status(),
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
                "params": self.cfg.gripper_gello_sync.model_dump(),
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
                val = float(joints[idx])
            except (TypeError, ValueError):
                with self._sync_lock:
                    self._sync_last_ok = False
                    self._sync_last_error = f"invalid joints_rad[{idx}]"
                    self._sync_last_t_wall = time.time()
                self._sync_stop.wait(period)
                continue

            sync_cfg = self.cfg.gripper_gello_sync
            lo, hi = sync_cfg.clamp_min, sync_cfg.clamp_max
            if lo is not None:
                val = max(float(lo), val)
            if hi is not None:
                val = min(float(hi), val)
            if sync_cfg.command_as == "position_mm":
                result = writer.command(position_mm=val)
            else:
                result = writer.command(position_norm=val)
            with self._sync_lock:
                self._sync_last_norm = val
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
    def _sensor_kind(sensor: Any) -> str:
        kind_v = getattr(getattr(sensor, "kind", None), "value", None)
        return str(kind_v if kind_v is not None else getattr(sensor, "kind", "") or "")

    @classmethod
    def _is_cs_arm_write(cls, sensor: Any) -> bool:
        """Elite CS66/ES66 write path (EliteDriver servoj) — not EC TT."""
        return cls._sensor_kind(sensor) == "arm_cs_write"

    @staticmethod
    def _joints6_from_ring(agent: BaseAgent, *, prefer_key: str = "joints_rad") -> list[float] | None:
        import math

        fr = agent.ring.latest.get()
        if fr is None:
            return None
        raw = fr.payload.get(prefer_key)
        if not isinstance(raw, (list, tuple)) or len(raw) < 6:
            # arm_write feedback fallback
            raw = fr.payload.get("feedback_joints_rad") or fr.payload.get("joints_rad")
        if not isinstance(raw, (list, tuple)) or len(raw) < 6:
            return None
        try:
            out = [float(raw[i]) for i in range(6)]
        except (TypeError, ValueError):
            return None
        if any(not math.isfinite(x) for x in out):
            return None
        return out

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
    def _s_curve_alpha(u: float) -> float:
        """Cosine ease-in-out in [0, 1] (S-curve approximation)."""
        import math

        u = max(0.0, min(1.0, float(u)))
        return 0.5 * (1.0 - math.cos(math.pi * u))

    @staticmethod
    def _clamp_seven_segment_fracs(
        jerk_seg_frac: float,
        accel_seg_frac: float,
    ) -> tuple[float, float, float]:
        """Return (Tj, Ta, Tv) with Tv = 1 - 4*Tj - 2*Ta >= 0."""
        tj = max(0.02, min(0.22, float(jerk_seg_frac)))
        ta = max(0.0, min(0.30, float(accel_seg_frac)))
        need = 4.0 * tj + 2.0 * ta
        if need > 1.0 - 1e-9:
            scale = (1.0 - 1e-6) / need
            tj *= scale
            ta *= scale
        tv = max(0.0, 1.0 - 4.0 * tj - 2.0 * ta)
        return tj, ta, tv

    @staticmethod
    def _seven_segment_alpha(
        u: float,
        *,
        jerk_seg_frac: float = 0.10,
        accel_seg_frac: float = 0.15,
    ) -> float:
        """Symmetric 7-segment jerk-limited α(u)∈[0,1]; α̇=α̈=0 at ends.

        Time layout (normalized): +J Tj | 0 Ta | -J Tj | 0 Tv | -J Tj | 0 Ta | +J Tj.
        """
        u = max(0.0, min(1.0, float(u)))
        if u <= 0.0:
            return 0.0
        if u >= 1.0:
            return 1.0
        tj, ta, tv = Orchestrator._clamp_seven_segment_fracs(
            jerk_seg_frac, accel_seg_frac
        )
        # Jerk square-wave with |J|=1; normalize position by final raw value.
        segs: list[tuple[float, float]] = [
            (tj, 1.0),
            (ta, 0.0),
            (tj, -1.0),
            (tv, 0.0),
            (tj, -1.0),
            (ta, 0.0),
            (tj, 1.0),
        ]

        def _integrate(t_query: float) -> float:
            a = 0.0
            v = 0.0
            p = 0.0
            t_left = float(t_query)
            for dur, j in segs:
                if dur <= 0.0:
                    continue
                if t_left <= 0.0:
                    break
                dt = dur if t_left >= dur else t_left
                # a(t)=a0+j*t; v=v0+a0*t+0.5*j*t^2; p=p0+v0*t+0.5*a0*t^2+(1/6)*j*t^3
                p = p + v * dt + 0.5 * a * dt * dt + (1.0 / 6.0) * j * dt * dt * dt
                v = v + a * dt + 0.5 * j * dt * dt
                a = a + j * dt
                t_left -= dt
            return p

        p_end = _integrate(1.0)
        if abs(p_end) < 1e-18:
            return u  # degenerate → linear fallback
        return max(0.0, min(1.0, _integrate(u) / p_end))

    @staticmethod
    def _path_alpha(
        u: float,
        *,
        profile: str = "linear",
        jerk_seg_frac: float = 0.10,
        accel_seg_frac: float = 0.15,
    ) -> float:
        """Shared path progress α(u)∈[0,1] for joints + gripper.

        ``seven_segment`` / ``cosine``: S-shaped — Δα small at both ends, larger mid-path.
        ``linear``: uniform α=u.
        """
        u = max(0.0, min(1.0, float(u)))
        prof = str(profile or "linear").strip().lower()
        if prof == "seven_segment":
            return Orchestrator._seven_segment_alpha(
                u,
                jerk_seg_frac=jerk_seg_frac,
                accel_seg_frac=accel_seg_frac,
            )
        if prof == "cosine":
            return Orchestrator._s_curve_alpha(u)
        return u

    @staticmethod
    def _interp_path(
        qa: list[float],
        qg: list[float],
        n: int,
        *,
        profile: str = "linear",
        jerk_seg_frac: float = 0.10,
        accel_seg_frac: float = 0.15,
    ) -> list[list[float]]:
        """Path q_a → q_g with N points (k=1..N); last == q_g.

        ``profile=linear``: uniform α=k/N (gello sync).
        ``profile=cosine``: shared cosine S-curve α (legacy abs).
        ``profile=seven_segment``: shared jerk-limited 7-segment α (abs default).
        """
        n = max(1, int(n))
        out: list[list[float]] = []
        for k in range(1, n + 1):
            a = Orchestrator._path_alpha(
                k / n,
                profile=profile,
                jerk_seg_frac=jerk_seg_frac,
                accel_seg_frac=accel_seg_frac,
            )
            out.append(
                [float(qa[i]) + a * (float(qg[i]) - float(qa[i])) for i in range(6)]
            )
        return out

    def _live_gripper_norm(self) -> float | None:
        """Latest gripper_read ``position_norm``, or None if unavailable."""
        from sensors_dcs.agents.gripper_read_agent import GripperReadAgent

        for agent in self.agents.values():
            if not isinstance(agent, GripperReadAgent):
                continue
            fr = agent.ring.latest.get()
            if fr is None:
                continue
            raw = (fr.payload or {}).get("position_norm")
            try:
                g = float(raw)
            except (TypeError, ValueError):
                continue
            if g != g or g in (float("inf"), float("-inf")):
                continue
            return g
        return None

    @staticmethod
    def _path_peak_step(qa: list[float], path: list[list[float]]) -> float:
        """Max ∞-norm joint step along path (including first step from qa)."""
        prev = qa
        peak = 0.0
        for qk in path:
            step = max(abs(float(qk[i]) - float(prev[i])) for i in range(6))
            if step > peak:
                peak = step
            prev = qk
        return peak

    @staticmethod
    def _abs_move_duration_s(
        *,
        d_rad: float,
        v_norm_rad_s: float,
        t_min_s: float,
        t_max_s: float,
    ) -> float:
        """T = clamp(d/v_norm, t_min, t_max); tiny d → t_min."""
        t_min = max(0.1, min(30.0, float(t_min_s)))
        t_max = max(0.1, min(30.0, float(t_max_s)))
        if t_min > t_max:
            t_min, t_max = t_max, t_min
        v = float(v_norm_rad_s)
        if not (v == v) or v <= 0:
            v = 0.02
        d = max(0.0, float(d_rad))
        if d < 1e-9:
            return t_min
        return max(t_min, min(t_max, d / v))

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
            # Sync wins: stop teleop + abs ramp before starting alignment.
            self.set_gello_arm_teleop(enabled=False)
            self.cancel_arm_abs_ramp(message="因同步开始取消绝对下发")
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
                cs_dense = self._is_cs_arm_write(writer.sensor)
                prev_cmd = list(qa0)
                for k, qk in enumerate(path, start=1):
                    if self._arm_sync_stop.is_set() or self._stop.is_set():
                        aborted = True
                        break
                    if cs_dense:
                        result = writer.command(
                            joints_rad=list(qk),
                            reference_joints_rad=list(prev_cmd),
                            servoj_once=True,
                            relative_to_last=True,
                            idle_after=False,
                        )
                    else:
                        ref = self._joints6_from_ring(reader) or prev_cmd
                        result = writer.command(
                            joints_rad=list(qk), reference_joints_rad=list(ref)
                        )
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
                    prev_cmd = list(qk)
                    self._arm_sync_stop.wait(period)
                if cs_dense and not aborted and final_error is None:
                    try:
                        writer.command(
                            joints_rad=list(prev_cmd),
                            reference_joints_rad=list(prev_cmd),
                            servoj_once=True,
                            idle_after=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass

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
                "rate_limited": False,  # step rate-limit removed; kept for UI compat
                "has_setpoint": bool(self._arm_teleop_setpoint),
                "setpoint_len": len(self._arm_teleop_setpoint),
                "pick_mode": self._arm_teleop_pick_mode,
                "pick_mode_locked": bool(
                    self._arm_teleop_enabled or self._arm_teleop_phase == "teleop"
                ),
                "lag_path_rad": self._arm_teleop_lag_path_rad,
                "lag_age_s": self._arm_teleop_lag_age_s,
                "last_step_rad": self._arm_teleop_last_step_rad,
                "catch_up": self._arm_teleop_catch_up,
                "pending": self._arm_teleop_pending is not None,
                "hz": self._arm_teleop_hz,
                "write_path": self._arm_teleop_write_path,
                "params": self.cfg.gello_arm_teleop.model_dump(),
            }

    def set_gello_arm_teleop_pick_mode(self, pick_mode: str) -> dict[str, Any]:
        """Switch pick policy. Allowed only while teleop is not running."""
        from sensors_dcs.config import GelloArmTeleopConfig

        mode = GelloArmTeleopConfig.model_validate({"pick_mode": pick_mode}).pick_mode
        with self._arm_teleop_lock:
            busy = self._arm_teleop_enabled or self._arm_teleop_phase == "teleop"
            if not busy:
                self._arm_teleop_pick_mode = mode
                self._arm_teleop_last_message = (
                    f"抽点模式：{mode}（使能前可切换；解除摇操后仍保留）"
                )
                self._arm_teleop_last_t_wall = time.time()
        if busy:
            return {
                "ok": False,
                "error": "摇操进行中，不能切换抽点模式；请先解除摇操",
                **self.gello_arm_teleop_status(),
            }
        return {"ok": True, **self.gello_arm_teleop_status()}

    def _arm_teleop_reset_session_locked(self) -> None:
        """Clear queue / pending / last_sent. Caller holds ``_arm_teleop_lock``."""
        self._arm_teleop_setpoint = []
        self._arm_teleop_last_sent = None
        self._arm_teleop_pending = None
        self._arm_teleop_lag_path_rad = None
        self._arm_teleop_lag_age_s = None
        self._arm_teleop_last_step_rad = None
        self._arm_teleop_catch_up = False

    @staticmethod
    def _arm_teleop_path_lag_linf(
        q0: list[float], samples: list[dict[str, Any]]
    ) -> float:
        """L-inf polyline length from ``q0`` through sample joints."""
        lag = 0.0
        prev = q0
        for sample in samples:
            q = sample.get("q")
            if not isinstance(q, list) or len(q) < 6:
                continue
            dmax, _ = Orchestrator._delta_max_joint(prev, q)
            lag += float(dmax)
            prev = q
        return lag

    @staticmethod
    def _arm_teleop_advance_path(
        q0: list[float],
        samples: list[dict[str, Any]],
        *,
        budget: float,
    ) -> tuple[list[float], list[dict[str, Any]], float]:
        """Walk L-inf polyline up to ``budget``; return (q_cmd, remaining_buf, step)."""
        if budget <= 1e-15:
            return list(q0), list(samples), 0.0
        remaining = list(samples)
        cur = list(q0)
        used = 0.0
        while remaining and used < budget - 1e-15:
            nxt = remaining[0].get("q")
            if not isinstance(nxt, list) or len(nxt) < 6:
                remaining.pop(0)
                continue
            dmax, _ = Orchestrator._delta_max_joint(cur, nxt)
            room = budget - used
            if dmax <= room + 1e-15:
                cur = list(nxt)
                used += float(dmax)
                remaining.pop(0)
                continue
            if dmax <= 1e-15:
                remaining.pop(0)
                continue
            frac = room / dmax
            cur = [
                float(cur[i]) + frac * (float(nxt[i]) - float(cur[i])) for i in range(6)
            ]
            used += room
            head_t = float(remaining[0].get("t") or time.time())
            remaining[0] = {"t": head_t, "q": list(cur)}
            break
        return cur, remaining, used

    def _resolve_gello_agent(self, gello_agent_id: str | None = None) -> Any | dict[str, Any]:
        from sensors_dcs.agents.gello_agent import GelloAgent

        gellos = [a for a in self.agents.values() if isinstance(a, GelloAgent)]
        if not gellos:
            return {"ok": False, "error": "no gello agent in config"}
        if gello_agent_id:
            gello = self.agents.get(gello_agent_id)
            if not isinstance(gello, GelloAgent):
                return {"ok": False, "error": f"agent {gello_agent_id!r} is not gello"}
            return gello
        return gellos[0]

    def gello_trace_status(self, gello_agent_id: str | None = None) -> dict[str, Any]:
        gello = self._resolve_gello_agent(gello_agent_id)
        if isinstance(gello, dict):
            return {**gello, "recording": False, "sample_count": 0}
        return {"ok": True, **gello.trace_status()}

    def set_gello_trace(
        self, *, enabled: bool, gello_agent_id: str | None = None
    ) -> dict[str, Any]:
        """Start/stop in-memory capture of gello joints (teleop feed space)."""
        gello = self._resolve_gello_agent(gello_agent_id)
        if isinstance(gello, dict):
            return gello
        return gello.set_trace_recording(enabled=bool(enabled))

    def export_gello_trace(self, gello_agent_id: str | None = None) -> dict[str, Any]:
        gello = self._resolve_gello_agent(gello_agent_id)
        if isinstance(gello, dict):
            return gello
        return gello.export_trace()

    def fake_teleop_status(self) -> dict[str, Any]:
        with self._fake_teleop_lock:
            return {
                "ready": self._fake_teleop_ready and bool(self._fake_teleop_joints),
                "phase": self._fake_teleop_phase,
                "n": self._fake_teleop_n,
                "index": self._fake_teleop_index,
                "hz": self._fake_teleop_hz,
                "write_count": self._fake_teleop_write_count,
                "last_ok": self._fake_teleop_last_ok,
                "last_error": self._fake_teleop_last_error,
                "message": self._fake_teleop_last_message,
                "meta": dict(self._fake_teleop_meta),
                "can_execute": (
                    self._fake_teleop_ready
                    and bool(self._fake_teleop_joints)
                    and self._fake_teleop_phase != "running"
                ),
            }

    @staticmethod
    def _lerp_xyzrpy(
        a: list[float], b: list[float], n: int
    ) -> list[list[float]]:
        """Inclusive dense line from a→b with about n segments (n+1 poses)."""
        n = max(1, int(n))
        out: list[list[float]] = []
        for k in range(n + 1):
            u = k / float(n)
            out.append([float(a[i]) + u * (float(b[i]) - float(a[i])) for i in range(6)])
        return out

    @staticmethod
    def _build_fake_teleop_xyzrpy(
        p0: list[float],
        *,
        dx_m: float = 0.05,
        step_m: float = 0.002,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        """p0 → A(+X) → full circle around p0 in XY back to A → p0. Flange xyzrpy."""
        import math

        r = abs(float(dx_m))
        step = max(5e-4, float(step_m))
        p0 = [float(x) for x in p0[:6]]
        a = [p0[0] + r, p0[1], p0[2], p0[3], p0[4], p0[5]]
        n_line = max(1, int(math.ceil(r / step)))
        n_circ = max(8, int(math.ceil((2.0 * math.pi * r) / step)))
        poses: list[list[float]] = []
        # 1) p0 → A
        poses.extend(Orchestrator._lerp_xyzrpy(p0, a, n_line))
        # 2) circle about p0 in XY, start/end at A (θ=0 … 2π)
        # skip θ=0 (already at A from segment 1)
        for k in range(1, n_circ + 1):
            th = (2.0 * math.pi) * (k / float(n_circ))
            poses.append(
                [
                    p0[0] + r * math.cos(th),
                    p0[1] + r * math.sin(th),
                    p0[2],
                    p0[3],
                    p0[4],
                    p0[5],
                ]
            )
        # 3) A → p0 (skip first = A duplicate)
        back = Orchestrator._lerp_xyzrpy(a, p0, n_line)
        poses.extend(back[1:])
        meta = {
            "frame": "flange",
            "dx_m": r,
            "step_m": step,
            "n_line": n_line,
            "n_circle": n_circ,
            "n_poses": len(poses),
            "p0": p0,
            "A": a,
            "path": "p0→A(+X) → circle_XY(r=|p0A|) → A→p0",
        }
        return poses, meta

    def fake_teleop_generate(
        self,
        *,
        arm_write_agent_id: str | None = None,
        dx_m: float = 0.05,
        step_m: float = 0.002,
    ) -> dict[str, Any]:
        """Plan flange path, dense IK → memory (does not move the arm)."""
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.arm_pose import joints_rad_to_xyzrpy, xyzrpy_to_joints_rad

        if self._arm_teleop_enabled:
            return {
                "ok": False,
                "error": "gello 摇操进行中；请先解除摇操再生成 Fake 路径",
            }
        with self._fake_teleop_lock:
            if self._fake_teleop_phase == "running":
                return {"ok": False, "error": "Fake 遥操执行中；请等待结束再生成"}

        resolved = self._resolve_gello_arm_agents(arm_write_agent_id=arm_write_agent_id)
        if isinstance(resolved, dict):
            # Still need reader/writer without gello — resolve manually.
            readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
            writers = [a for a in self.agents.values() if isinstance(a, ArmWriteAgent)]
            if not readers or not writers:
                return {
                    "ok": False,
                    "error": resolved.get("error") or "need arm read + write agents",
                }
            reader, writer = readers[0], writers[0]
            if arm_write_agent_id:
                w = self.agents.get(arm_write_agent_id)
                if isinstance(w, ArmWriteAgent):
                    writer = w
        else:
            _gello, reader, writer = resolved

        q0 = self._joints6_from_ring(reader)
        if q0 is None:
            return {"ok": False, "error": "无臂关节读数；请先连接并等待 RTSI/monitor"}

        # Flange center pose (tcp offset zero).
        flange0 = joints_rad_to_xyzrpy(q0, tcp_xyz=(0.0, 0.0, 0.0))
        if flange0 is None:
            return {"ok": False, "error": "法兰 FK 失败"}

        poses, meta = self._build_fake_teleop_xyzrpy(
            flange0, dx_m=float(dx_m), step_m=float(step_m)
        )
        joints_out: list[list[float]] = []
        seed = list(q0)
        # Ensure CS66 IK backend is loaded before the dense loop (clear sticky fail).
        from sensors_dcs.arm_pose import clear_ik_backend_cache, _get_ik_backend, ik_backend_error

        clear_ik_backend_cache()
        warm = _get_ik_backend(retry=True)
        if warm is False:
            err = (
                "IK 不可用，无法生成 Fake 路径："
                + str(ik_backend_error() or "cs66_kin_g26562 backend missing")
            )
            with self._fake_teleop_lock:
                self._fake_teleop_ready = False
                self._fake_teleop_joints = []
                self._fake_teleop_n = 0
                self._fake_teleop_phase = "error"
                self._fake_teleop_last_ok = False
                self._fake_teleop_last_error = err
                self._fake_teleop_last_message = err
                self._fake_teleop_meta = {**meta, "ik_fail_index": -1}
            return {"ok": False, "error": err, **self.fake_teleop_status()}

        for i, pose in enumerate(poses):
            ik = xyzrpy_to_joints_rad(
                pose,
                q_seed_rad=seed,
                tcp_xyz=(0.0, 0.0, 0.0),
                interactive=True,
                max_nfev=120,
                soft_limit_retry=False,
                enforce_teach_soft_limits=False,
                solver_tol=1e-6,
            )
            if not ik.get("ok") or not isinstance(ik.get("joints_rad"), list):
                with self._fake_teleop_lock:
                    self._fake_teleop_ready = False
                    self._fake_teleop_joints = []
                    self._fake_teleop_n = 0
                    self._fake_teleop_phase = "error"
                    self._fake_teleop_last_ok = False
                    self._fake_teleop_last_error = (
                        f"IK 失败 @ pose {i}/{len(poses)}: "
                        + str(ik.get("error") or "no joints")
                    )
                    self._fake_teleop_last_message = self._fake_teleop_last_error
                    self._fake_teleop_meta = {**meta, "ik_fail_index": i}
                return {
                    "ok": False,
                    "error": self._fake_teleop_last_error,
                    **self.fake_teleop_status(),
                }
            q = [float(x) for x in ik["joints_rad"][:6]]
            joints_out.append(q)
            seed = q

        with self._fake_teleop_lock:
            self._fake_teleop_joints = joints_out
            self._fake_teleop_ready = True
            self._fake_teleop_n = len(joints_out)
            self._fake_teleop_index = 0
            self._fake_teleop_phase = "ready"
            self._fake_teleop_last_ok = True
            self._fake_teleop_last_error = None
            self._fake_teleop_last_message = (
                f"已生成 Fake 路径 · {len(joints_out)} 点 · 法兰 p0→A(+X 5cm)→圆→回 p0"
            )
            self._fake_teleop_meta = {
                **meta,
                "q0": list(q0),
                "writer_id": writer.agent_id,
                "reader_id": reader.agent_id,
            }
            self._fake_teleop_writer_id = writer.agent_id
        return {"ok": True, **self.fake_teleop_status()}

    def fake_teleop_stop(self, *, message: str | None = None) -> dict[str, Any]:
        self._fake_teleop_stop.set()
        th = self._fake_teleop_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=3.0)
        with self._fake_teleop_lock:
            was_running = self._fake_teleop_phase == "running"
            if was_running:
                self._fake_teleop_phase = (
                    "ready" if self._fake_teleop_ready and self._fake_teleop_joints else "idle"
                )
            if message:
                self._fake_teleop_last_message = message
            self._fake_teleop_thread = None
        return {"ok": True, **self.fake_teleop_status()}

    def fake_teleop_execute(
        self,
        *,
        arm_write_agent_id: str | None = None,
        hz: float | None = None,
    ) -> dict[str, Any]:
        """Play stored Fake joints at fixed Hz (same CS dense servoj_once as teleop)."""
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        if self._arm_teleop_enabled:
            return {
                "ok": False,
                "error": "gello 摇操进行中；请先解除再执行 Fake 路径",
                **self.fake_teleop_status(),
            }
        with self._fake_teleop_lock:
            if not self._fake_teleop_ready or not self._fake_teleop_joints:
                return {
                    "ok": False,
                    "error": "尚无可用 Fake 点；请先点「Fake遥操点生成」",
                    **self.fake_teleop_status(),
                }
            if self._fake_teleop_phase == "running":
                return {"ok": False, "error": "Fake 遥操已在执行", **self.fake_teleop_status()}
            joints = [list(q) for q in self._fake_teleop_joints]
            wid = arm_write_agent_id or self._fake_teleop_writer_id

        writer = self.agents.get(wid) if wid else None
        if not isinstance(writer, ArmWriteAgent):
            writers = [a for a in self.agents.values() if isinstance(a, ArmWriteAgent)]
            writer = writers[0] if writers else None
        if not isinstance(writer, ArmWriteAgent):
            return {"ok": False, "error": "no arm_write agent", **self.fake_teleop_status()}
        if not self._writer_armed(writer):
            return {
                "ok": False,
                "error": "请先 Arm 再执行 Fake 遥操",
                **self.fake_teleop_status(),
            }

        # Fake playback wins over abs-ramp; stop gello teleop if somehow still on.
        self.cancel_arm_abs_ramp(message="因 Fake 遥操取消绝对下发")
        write_hz = max(1.0, min(125.0, float(hz if hz is not None else self._fake_teleop_hz)))

        self._fake_teleop_stop.set()
        th_old = self._fake_teleop_thread
        if th_old is not None and th_old.is_alive():
            th_old.join(timeout=2.0)

        with self._fake_teleop_lock:
            self._fake_teleop_phase = "running"
            self._fake_teleop_index = 0
            self._fake_teleop_n = len(joints)
            self._fake_teleop_write_count = 0
            self._fake_teleop_hz = write_hz
            self._fake_teleop_last_ok = None
            self._fake_teleop_last_error = None
            self._fake_teleop_last_message = (
                f"Fake 遥操执行中 · 0/{len(joints)} · {write_hz:g} Hz"
            )
            self._fake_teleop_writer_id = writer.agent_id

        self._fake_teleop_stop.clear()
        self._fake_teleop_thread = threading.Thread(
            target=self._fake_teleop_loop,
            name="fake-teleop",
            daemon=True,
            args=(writer.agent_id, joints, write_hz),
        )
        self._fake_teleop_thread.start()
        return {"ok": True, **self.fake_teleop_status()}

    def _fake_teleop_loop(
        self, writer_id: str, joints: list[list[float]], hz: float
    ) -> None:
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        period = 1.0 / max(1.0, float(hz))
        fail_streak = 0
        fail_max = 5
        final_error: str | None = None
        final_message: str | None = None
        try:
            for i, q in enumerate(joints):
                if self._fake_teleop_stop.is_set() or self._stop.is_set():
                    final_message = f"Fake 遥操已停止 · {i}/{len(joints)}"
                    break
                writer = self.agents.get(writer_id)
                if not isinstance(writer, ArmWriteAgent) or not self._writer_armed(writer):
                    final_error = "Arm 断开，Fake 遥操中止"
                    break
                if self._is_cs_arm_write(writer.sensor):
                    result = writer.command(
                        joints_rad=list(q),
                        servoj_once=True,
                        relative_to_last=True,
                        idle_after=False,
                    )
                else:
                    result = writer.command(joints_rad=list(q))
                ok = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
                with self._fake_teleop_lock:
                    self._fake_teleop_index = i + 1
                    self._fake_teleop_last_ok = ok
                    if ok:
                        fail_streak = 0
                        self._fake_teleop_write_count += 1
                        self._fake_teleop_last_error = None
                        self._fake_teleop_last_message = (
                            f"Fake 遥操执行中 · {i + 1}/{len(joints)} · {hz:g} Hz"
                        )
                    else:
                        fail_streak += 1
                        self._fake_teleop_last_error = (
                            result.get("error")
                            if isinstance(result, dict)
                            else "write rejected"
                        )
                        if fail_streak >= fail_max:
                            final_error = (
                                "Fake 写臂连续失败："
                                + str(self._fake_teleop_last_error)
                                + "；已停止"
                            )
                            break
                self._fake_teleop_stop.wait(period)
            else:
                final_message = f"Fake 遥操完成 · {len(joints)} 点"
        finally:
            with self._fake_teleop_lock:
                self._fake_teleop_thread = None
                if final_error:
                    self._fake_teleop_phase = "error"
                    self._fake_teleop_last_ok = False
                    self._fake_teleop_last_error = final_error
                    self._fake_teleop_last_message = final_error
                    # Keep joints so user can retry execute after fixing arm.
                    self._fake_teleop_ready = bool(self._fake_teleop_joints)
                else:
                    self._fake_teleop_phase = (
                        "ready" if self._fake_teleop_joints else "idle"
                    )
                    self._fake_teleop_last_ok = True
                    self._fake_teleop_last_error = None
                    self._fake_teleop_last_message = (
                        final_message or "Fake 遥操结束"
                    )

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

    def _join_gello_arm_teleop_threads(self, *, timeout: float = 3.0) -> None:
        """Stop signal already set; join setpoint + write threads."""
        th_sp = self._arm_teleop_setpoint_thread
        th_wr = self._arm_teleop_thread
        for th in (th_sp, th_wr):
            if th is not None and th.is_alive() and th is not threading.current_thread():
                th.join(timeout=timeout)
        with self._arm_teleop_lock:
            self._arm_teleop_setpoint_thread = None
            self._arm_teleop_thread = None

    def set_gello_arm_teleop(
        self,
        *,
        enabled: bool,
        gello_agent_id: str | None = None,
        arm_agent_id: str | None = None,
        arm_write_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Start/stop live gello→arm teleop. Jump/stale auto-disables; keeps armed.

        Architecture (aligned with sensors-arm B.1):
        - setpoint poller: append each gello sample into ``_arm_teleop_setpoint``
        - write thread: fixed ``teleop_hz`` → pick → CS ``servoj_once`` (not stream)
          / EC TT; commit queue only on write success
        """
        if not enabled:
            self._arm_teleop_stop.set()
            self._join_gello_arm_teleop_threads()
            with self._arm_teleop_lock:
                was = self._arm_teleop_enabled or self._arm_teleop_phase == "teleop"
                self._arm_teleop_enabled = False
                self._arm_teleop_reset_session_locked()
                self._arm_teleop_rate_limited = False
                if was and self._arm_teleop_phase == "teleop":
                    self._arm_teleop_phase = "idle"
                    self._arm_teleop_last_message = (
                        f"已解除摇操；gello 未控制机械臂 · pick={self._arm_teleop_pick_mode}"
                    )
                    self._arm_teleop_last_ok = True
                    self._arm_teleop_last_error = None
                self._arm_teleop_last_t_wall = time.time()
            return {"ok": True, **self.gello_arm_teleop_status()}

        # Teleop wins over absolute ramp / Fake playback.
        self.cancel_arm_abs_ramp(message="因摇操开始取消绝对下发")
        self.fake_teleop_stop(message="因摇操开始停止 Fake 遥操")

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
        self._join_gello_arm_teleop_threads()

        params = self.cfg.gello_arm_teleop
        hz = max(1.0, min(100.0, float(params.teleop_hz)))
        write_path = (
            "cs_servoj_once" if self._is_cs_arm_write(writer.sensor) else "ec_tt"
        )
        q0 = self._joints6_from_ring(gello)
        t0 = time.time()
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
            mode = self._arm_teleop_pick_mode
            self._arm_teleop_last_message = (
                f"摇操中 · {hz:g} Hz · {mode} · {write_path}"
            )
            self._arm_teleop_write_count = 0
            self._arm_teleop_rate_limited = False
            self._arm_teleop_hz = hz
            self._arm_teleop_write_path = write_path
            self._arm_teleop_reset_session_locked()
            if q0 is not None:
                self._arm_teleop_setpoint = [{"t": t0, "q": list(q0)}]
                self._arm_teleop_last_sent = list(q0)
            self._arm_teleop_last_t_wall = t0

        self._arm_teleop_stop.clear()
        self._arm_teleop_setpoint_thread = threading.Thread(
            target=self._gello_arm_teleop_setpoint_loop,
            name="gello-arm-teleop-setpoint",
            daemon=True,
            args=(gello.agent_id,),
        )
        self._arm_teleop_thread = threading.Thread(
            target=self._gello_arm_teleop_write_loop,
            name="gello-arm-teleop-write",
            daemon=True,
            args=(gello.agent_id, reader.agent_id, writer.agent_id),
        )
        self._arm_teleop_setpoint_thread.start()
        self._arm_teleop_thread.start()
        return {"ok": True, **self.gello_arm_teleop_status()}

    def _arm_teleop_pick_setpoint(self) -> list[float] | None:
        """Plan next joint command from the gello queue (two-phase: commit later).

        Caller must hold ``_arm_teleop_lock``. On write failure the same pending
        command is retried; queue mutation happens only in ``_arm_teleop_commit_setpoint``.
        """
        if self._arm_teleop_pending is not None:
            q = self._arm_teleop_pending.get("q")
            return list(q) if isinstance(q, list) else None

        buf = self._arm_teleop_setpoint
        if not buf:
            return None

        mode = self._arm_teleop_pick_mode
        now = time.time()
        params = self.cfg.gello_arm_teleop
        hz = max(1.0, float(self._arm_teleop_hz or params.teleop_hz or 100.0))

        if mode != "eat_stream":
            tip = buf[-1]
            q_tip = tip.get("q")
            if not isinstance(q_tip, list) or len(q_tip) < 6:
                return None
            tip_t = float(tip.get("t") or now)
            lag_path = 0.0
            if self._arm_teleop_last_sent is not None:
                lag_path = self._arm_teleop_path_lag_linf(
                    self._arm_teleop_last_sent, buf
                )
            lag_age = max(0.0, now - float(buf[0].get("t") or now))
            self._arm_teleop_pending = {
                "mode": "latest",
                "q": list(q_tip),
                "tip_t": tip_t,
                "lag_path_rad": lag_path,
                "lag_age_s": lag_age,
                "step_rad": (
                    self._delta_max_joint(self._arm_teleop_last_sent, q_tip)[0]
                    if self._arm_teleop_last_sent is not None
                    else 0.0
                ),
                "catch_up": False,
            }
            self._arm_teleop_lag_path_rad = lag_path
            self._arm_teleop_lag_age_s = lag_age
            self._arm_teleop_last_step_rad = self._arm_teleop_pending["step_rad"]
            self._arm_teleop_catch_up = False
            return list(q_tip)

        # eat_stream: optional drop stale heads, then path-follow with catch-up A.
        if bool(params.eat_drop_stale):
            age_max = max(0.0, float(params.eat_lag_age_max_s))
            while len(buf) > 1:
                age = now - float(buf[0].get("t") or now)
                if age <= age_max + 1e-15:
                    break
                buf.pop(0)

        q_ref = self._arm_teleop_last_sent
        if q_ref is None:
            q0 = buf[0].get("q")
            if not isinstance(q0, list) or len(q0) < 6:
                return None
            q_ref = list(q0)

        lag_path = self._arm_teleop_path_lag_linf(q_ref, buf)
        lag_age = max(0.0, now - float(buf[0].get("t") or now))
        catch_up = lag_path > float(params.eat_lag_path_max_rad) + 1e-15 or (
            lag_age > float(params.eat_lag_age_max_s) + 1e-15
        )
        rate = float(
            params.eat_catch_up_rad_s if catch_up else params.eat_max_rad_s
        )
        budget = max(0.0, rate) / hz
        q_cmd, remaining, step = self._arm_teleop_advance_path(
            q_ref, buf, budget=budget
        )
        self._arm_teleop_pending = {
            "mode": "eat_stream",
            "q": list(q_cmd),
            "remaining": remaining,
            "lag_path_rad": lag_path,
            "lag_age_s": lag_age,
            "step_rad": step,
            "catch_up": catch_up,
        }
        self._arm_teleop_lag_path_rad = lag_path
        self._arm_teleop_lag_age_s = lag_age
        self._arm_teleop_last_step_rad = step
        self._arm_teleop_catch_up = catch_up
        return list(q_cmd)

    def _arm_teleop_commit_setpoint(self, *, ok: bool) -> None:
        """Apply queue mutation after a write attempt. Caller holds the lock."""
        pending = self._arm_teleop_pending
        if pending is None:
            return
        if not ok:
            # Keep pending so the next tick retries the same command.
            return
        mode = pending.get("mode")
        q_cmd = pending.get("q")
        if isinstance(q_cmd, list) and len(q_cmd) >= 6:
            self._arm_teleop_last_sent = list(q_cmd)
        if mode == "latest":
            tip_t = float(pending.get("tip_t") or 0.0)
            self._arm_teleop_setpoint = [
                s
                for s in self._arm_teleop_setpoint
                if float(s.get("t") or 0.0) > tip_t + 1e-15
            ]
        elif mode == "eat_stream":
            remaining = pending.get("remaining")
            if isinstance(remaining, list):
                self._arm_teleop_setpoint = list(remaining)
        self._arm_teleop_pending = None

    def _gello_arm_teleop_setpoint_loop(self, gello_id: str) -> None:
        """Poll gello ring → append samples; write loop picks via ``_arm_teleop_pick_setpoint``."""
        from sensors_dcs.agents.gello_agent import GelloAgent

        params = self.cfg.gello_arm_teleop
        hz = max(1.0, min(100.0, float(params.teleop_hz)))
        # Poll at least as fast as write Hz; up to 200 Hz so gello updates are not coalesced early.
        poll_hz = max(hz, min(200.0, max(hz, 100.0)))
        period = 1.0 / poll_hz
        jump_abort = float(params.teleop_jump_abort_rad)
        stale_max = max(1, int(params.teleop_stale_max_ticks))

        q_prev: list[float] | None = None
        with self._arm_teleop_lock:
            if self._arm_teleop_setpoint:
                q_prev = list(self._arm_teleop_setpoint[-1]["q"])
            elif self._arm_teleop_last_sent is not None:
                q_prev = list(self._arm_teleop_last_sent)
        stale_ticks = 0
        final_error: str | None = None

        try:
            while not self._arm_teleop_stop.is_set() and not self._stop.is_set():
                gello = self.agents.get(gello_id)
                if not isinstance(gello, GelloAgent):
                    final_error = "teleop agents missing"
                    break

                q_g = self._joints6_from_ring(gello)
                if q_g is None:
                    stale_ticks += 1
                    if stale_ticks > stale_max:
                        final_error = "gello 读数中断，已解除摇操"
                        break
                    with self._arm_teleop_lock:
                        self._arm_teleop_last_message = (
                            f"摇操中 · 等待 gello（stale {stale_ticks}/{stale_max}）"
                        )
                        self._arm_teleop_last_t_wall = time.time()
                    self._arm_teleop_stop.wait(period)
                    continue
                stale_ticks = 0

                if q_prev is not None:
                    dmax, ji = self._delta_max_joint(q_prev, q_g)
                    with self._arm_teleop_lock:
                        self._arm_teleop_delta_max = dmax
                        self._arm_teleop_worst_joint = ji
                    if dmax > jump_abort + 1e-12:
                        final_error = (
                            f"检测到 gello 关节跳变（Δ={dmax:.4f} rad，轴 {ji}），"
                            f"已自动解除摇操。请检查主手后先「同步」，再开摇操。"
                        )
                        break
                else:
                    dmax, ji = 0.0, 0
                    with self._arm_teleop_lock:
                        self._arm_teleop_delta_max = dmax
                        self._arm_teleop_worst_joint = ji

                with self._arm_teleop_lock:
                    self._arm_teleop_setpoint.append(
                        {"t": time.time(), "q": list(q_g)}
                    )
                    self._arm_teleop_last_t_wall = time.time()
                q_prev = list(q_g)
                self._arm_teleop_stop.wait(period)
        finally:
            if final_error is not None:
                self._arm_teleop_stop.set()
                with self._arm_teleop_lock:
                    self._arm_teleop_enabled = False
                    self._arm_teleop_phase = "error"
                    self._arm_teleop_last_ok = False
                    self._arm_teleop_last_error = final_error
                    self._arm_teleop_last_message = final_error
                    self._arm_teleop_last_t_wall = time.time()
            with self._arm_teleop_lock:
                self._arm_teleop_setpoint_thread = None

    def _gello_arm_teleop_write_loop(
        self, gello_id: str, arm_id: str, writer_id: str
    ) -> None:
        """Fixed-Hz write of latest setpoint; write RTT does not stall gello sampling."""
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
        from sensors_dcs.agents.gello_agent import GelloAgent

        params = self.cfg.gello_arm_teleop
        hz = max(1.0, min(100.0, float(params.teleop_hz)))
        period = 1.0 / hz
        fail_max = max(1, int(params.teleop_write_fail_max))

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

                with self._arm_teleop_lock:
                    q_cmd = self._arm_teleop_pick_setpoint()
                    write_path = self._arm_teleop_write_path
                    mode = self._arm_teleop_pick_mode
                    catch_up = self._arm_teleop_catch_up

                if q_cmd is None:
                    self._arm_teleop_stop.wait(period)
                    continue

                # CS66: dense servoj_once (not _servoj_stream). EC: TT single add.
                if write_path == "cs_servoj_once" or self._is_cs_arm_write(writer.sensor):
                    result = writer.command(
                        joints_rad=list(q_cmd),
                        servoj_once=True,
                        relative_to_last=True,
                        idle_after=False,
                    )
                else:
                    q_a = self._joints6_from_ring(reader)
                    ref = q_a if q_a is not None else q_cmd
                    result = writer.command(
                        joints_rad=list(q_cmd), reference_joints_rad=list(ref)
                    )
                ok = bool(result.get("ok"))
                if ok:
                    fail_streak = 0
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
                    self._arm_teleop_commit_setpoint(ok=ok)
                    self._arm_teleop_last_ok = ok
                    self._arm_teleop_last_error = result.get("error")
                    self._arm_teleop_write_count += 1 if ok else 0
                    self._arm_teleop_last_t_wall = time.time()
                    cu = " · catch-up" if catch_up and mode == "eat_stream" else ""
                    self._arm_teleop_last_message = (
                        f"摇操中 · {hz:g} Hz · {mode}{cu} · 已写 "
                        f"{self._arm_teleop_write_count}"
                    )

                if ok:
                    try:
                        gello.note_teleop_command(
                            list(q_cmd),
                            t_wall=time.time(),
                            rate_limited=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass

                self._arm_teleop_stop.wait(period)

            if final_error is None and (
                self._arm_teleop_stop.is_set() or self._stop.is_set()
            ):
                final_message = "已解除摇操；gello 未控制机械臂"
                abort_phase = "idle"
        finally:
            self._arm_teleop_stop.set()
            with self._arm_teleop_lock:
                self._arm_teleop_enabled = False
                self._arm_teleop_thread = None
                self._arm_teleop_rate_limited = False
                # Prefer setpoint-loop error if it already set phase=error.
                if self._arm_teleop_phase == "error" and self._arm_teleop_last_error:
                    pass
                else:
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
                self._arm_teleop_reset_session_locked()
                self._arm_teleop_last_t_wall = time.time()

    def gripper_command(
        self,
        *,
        agent_id: str | None = None,
        position_norm: float | None = None,
        position_raw: int | None = None,
        position_mm: float | None = None,
        initialize: bool = False,
        allow_during_sync: bool = False,
    ) -> dict[str, Any]:
        """Dispatch init / absolute gripper target to a ``gripper_write`` agent."""
        from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent

        if self._sync_enabled and not initialize and not allow_during_sync:
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
            position_mm=position_mm,
            initialize=initialize,
        )

    def arm_abs_ramp_status(self) -> dict[str, Any]:
        cfg = getattr(self.cfg, "arm_abs_ramp", None)
        defaults = {
            "t_min_s": float(getattr(cfg, "t_min_s", 0.1) if cfg else 0.1),
            "t_max_s": float(getattr(cfg, "t_max_s", 30.0) if cfg else 30.0),
            "v_norm_rad_s": float(getattr(cfg, "v_norm_rad_s", 0.02) if cfg else 0.02),
            "profile": str(
                getattr(cfg, "profile", "seven_segment") if cfg else "seven_segment"
            ),
            "jerk_seg_frac": float(getattr(cfg, "jerk_seg_frac", 0.10) if cfg else 0.10),
            "accel_seg_frac": float(
                getattr(cfg, "accel_seg_frac", 0.15) if cfg else 0.15
            ),
            "ramp_hz": float(getattr(cfg, "ramp_hz", 20.0) if cfg else 20.0),
            "gripper_ramp_hz": float(
                getattr(cfg, "gripper_ramp_hz", 5.0) if cfg else 5.0
            ),
        }
        with self._abs_ramp_lock:
            return {
                "enabled": self._abs_ramp_enabled,
                "phase": self._abs_ramp_phase,
                "ramp_index": self._abs_ramp_index,
                "ramp_n": self._abs_ramp_n,
                "duration_s": self._abs_ramp_duration_s,
                "hz": self._abs_ramp_hz,
                "arm_agent_id": self._abs_ramp_arm_id,
                "arm_write_agent_id": self._abs_ramp_writer_id,
                "last_ok": self._abs_ramp_last_ok,
                "last_error": self._abs_ramp_last_error,
                "message": self._abs_ramp_last_message,
                "write_count": self._abs_ramp_write_count,
                "last_t_wall": self._abs_ramp_last_t_wall,
                "target_joints": (
                    list(self._abs_ramp_target_joints)
                    if self._abs_ramp_target_joints is not None
                    else None
                ),
                "timing": getattr(self, "_abs_ramp_timing", None),
                "delta_max": getattr(self, "_abs_ramp_delta_max", None),
                "gripper_position_norm": getattr(self, "_abs_ramp_gripper", None),
                "params": defaults,
            }

    def cancel_arm_abs_ramp(self, *, message: str | None = None) -> dict[str, Any]:
        """Stop a running absolute timed ramp (no-op if idle)."""
        self._abs_ramp_stop.set()
        th = self._abs_ramp_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)
        with self._abs_ramp_lock:
            was = self._abs_ramp_enabled or self._abs_ramp_phase == "ramping"
            self._abs_ramp_enabled = False
            self._abs_ramp_thread = None
            if was or self._abs_ramp_phase == "ramping":
                self._abs_ramp_phase = "idle"
                self._abs_ramp_last_ok = True
                self._abs_ramp_last_error = None
                self._abs_ramp_last_message = message or "绝对下发已取消"
                self._abs_ramp_last_t_wall = time.time()
        return {"ok": True, **self.arm_abs_ramp_status()}

    def start_arm_abs_ramp(
        self,
        *,
        joints_rad: list[float],
        duration_s: float | None = None,
        agent_id: str | None = None,
        timing: str = "scale_by_d",
        t_min_s: float | None = None,
        t_max_s: float | None = None,
        v_norm_rad_s: float | None = None,
        jerk_seg_frac: float | None = None,
        accel_seg_frac: float | None = None,
        gripper_position_norm: float | None = None,
        apply_gello_bias: bool = True,
    ) -> dict[str, Any]:
        """Timed **joint-space** ramp from live arm read → target joints.

        - ``timing=scale_by_d`` (abs-send / Home): ``T=clamp(d/v_norm, t_min, t_max)`` + profile.
        - ``timing=fixed``: use ``duration_s`` as total T + same profile
          (legacy; gello sync / callers that pass only duration_s).
        Path is linear in q with shared α(t). Not Cartesian+IK.
        Optional ``gripper_position_norm`` is the policy grip. It is written
        off the joint timing path (Modbus must not stretch periods). When
        ``apply_gello_bias`` is set, later waypoints re-send
        ``clamp(policy + bias[6])`` whenever that target moves, so a Gello
        grip nudge during the ramp reaches the gripper before the ramp ends.
        """
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        if self._arm_teleop_enabled:
            return {
                "ok": False,
                "error": "gello→arm teleop active; 解除摇操 before absolute ramp",
                **self.gello_arm_teleop_status(),
            }
        if self._arm_sync_enabled:
            return {
                "ok": False,
                "error": "gello→arm sync active; cancel sync before absolute ramp",
                **self.gello_arm_sync_status(),
            }

        readers = [a for a in self.agents.values() if isinstance(a, ArmAgent)]
        writers = [a for a in self.agents.values() if isinstance(a, ArmWriteAgent)]
        if not writers:
            return {"ok": False, "error": "no arm_write agent in config"}
        if not readers:
            return {
                "ok": False,
                "error": "arm_write requires a paired arm (read) agent",
            }

        writer: ArmWriteAgent
        if agent_id:
            found = self.agents.get(agent_id)
            if not isinstance(found, ArmWriteAgent):
                return {"ok": False, "error": f"agent {agent_id!r} is not arm_write"}
            writer = found
        else:
            writer = writers[0]

        reader = readers[0]
        for cand in readers:
            if cand.sensor_id == writer.sensor_id:
                reader = cand
                break

        armed = bool(getattr(writer.sensor, "armed", False))
        if not armed:
            fr = writer.ring.latest.get()
            if fr is not None:
                armed = bool(fr.payload.get("armed"))
        if not armed:
            return {"ok": False, "error": "arm_write not armed; click Arm before absolute ramp"}

        try:
            q_star = [float(x) for x in joints_rad]
        except (TypeError, ValueError):
            return {"ok": False, "error": "joints_rad must be 6 floats"}
        if len(q_star) != 6 or any(not (x == x) for x in q_star):  # NaN check
            return {"ok": False, "error": "joints_rad must be exactly 6 finite values"}

        qa0 = self._joints6_from_ring(reader)
        if qa0 is None:
            return {
                "ok": False,
                "error": "no live arm read joints; refuse absolute ramp without current pose",
            }

        cfg = getattr(self.cfg, "arm_abs_ramp", None)
        t_min = float(t_min_s if t_min_s is not None else getattr(cfg, "t_min_s", 0.1))
        t_max = float(t_max_s if t_max_s is not None else getattr(cfg, "t_max_s", 30.0))
        v_norm = float(
            v_norm_rad_s
            if v_norm_rad_s is not None
            else getattr(cfg, "v_norm_rad_s", 0.02)
        )
        profile = str(getattr(cfg, "profile", "seven_segment") or "seven_segment")
        tj_in = jerk_seg_frac if jerk_seg_frac is not None else getattr(cfg, "jerk_seg_frac", 0.10)
        ta_in = accel_seg_frac if accel_seg_frac is not None else getattr(cfg, "accel_seg_frac", 0.15)
        jerk_frac, accel_frac, _tv = self._clamp_seven_segment_fracs(tj_in, ta_in)
        mode = str(timing or "scale_by_d").strip().lower()
        if mode not in ("scale_by_d", "fixed"):
            mode = "scale_by_d"

        dmax, ji = self._delta_max_joint(qa0, q_star)
        if mode == "fixed":
            dur = 10.0 if duration_s is None else float(duration_s)
            if not (dur == dur):
                dur = 10.0
            dur = max(0.1, min(30.0, dur))
        else:
            dur = self._abs_move_duration_s(
                d_rad=dmax,
                v_norm_rad_s=v_norm,
                t_min_s=t_min,
                t_max_s=t_max,
            )

        abs_hz = getattr(cfg, "ramp_hz", None) if cfg is not None else None
        if abs_hz is not None:
            hz = float(abs_hz)
        else:
            hz = float(getattr(self.cfg.gello_arm_sync, "ramp_hz", 5.0) or 5.0)
        # CS66 dense servoj_once: denser ticks → smaller steps → smoother motion.
        cs_dense = self._is_cs_arm_write(writer.sensor)
        if cs_dense:
            cs_hz = float(getattr(cfg, "cs_ramp_hz", 50.0) if cfg is not None else 50.0)
            hz = max(hz, cs_hz)
        hz = max(0.1, hz)
        n = max(1, int(round(dur * hz)))

        path = self._interp_path(
            qa0,
            q_star,
            n,
            profile=profile,
            jerk_seg_frac=jerk_frac,
            accel_seg_frac=accel_frac,
        )
        peak = self._path_peak_step(qa0, path)
        max_delta = float(getattr(writer.sensor, "max_delta_rad", 0.0) or 0.0)
        if max_delta > 0 and peak > max_delta + 1e-12:
            need_n = max(1, int((dmax / max_delta) + 0.999999))
            scale = peak / max(dmax / n, 1e-12)
            need_n_s = max(need_n, int((scale * dmax / max_delta) + 0.999999))
            need_s = need_n_s / hz
            return {
                "ok": False,
                "error": (
                    f"ramp peak step ≈{peak:.4f} rad (joint {ji}) exceeds driver max_delta="
                    f"{max_delta:.4f} rad at {dur:g}s×{hz:g}Hz ({profile}); "
                    f"increase t_max / lower v_norm or use duration ≥{need_s:.1f}s (max 30)"
                ),
                "delta_max": dmax,
                "peak_step": peak,
                "worst_joint": ji,
                "ramp_n": n,
                "duration_s": dur,
                "t_min_s": t_min,
                "t_max_s": t_max,
                "v_norm_rad_s": v_norm,
                "timing": mode,
            }

        grip_end: float | None = None
        if gripper_position_norm is not None:
            try:
                gv = float(gripper_position_norm)
            except (TypeError, ValueError):
                return {"ok": False, "error": "gripper_position_norm must be a float"}
            if not (gv == gv):
                return {"ok": False, "error": "gripper_position_norm must be finite"}
            # AG95 useful range is ~0..0.637; keep a soft ceiling at 1.0 for API compat.
            grip_end = max(0.0, min(1.0, gv))

        # Replace any in-flight abs ramp.
        self._abs_ramp_stop.set()
        th_old = self._abs_ramp_thread
        if th_old is not None and th_old.is_alive():
            th_old.join(timeout=2.0)

        with self._abs_ramp_lock:
            self._abs_ramp_enabled = True
            self._abs_ramp_phase = "ramping"
            self._abs_ramp_index = 0
            self._abs_ramp_n = n
            self._abs_ramp_duration_s = dur
            self._abs_ramp_hz = hz
            self._abs_ramp_arm_id = reader.agent_id
            self._abs_ramp_writer_id = writer.agent_id
            self._abs_ramp_last_ok = None
            self._abs_ramp_last_error = None
            if grip_end is not None:
                grip_msg = f"；夹爪一次下发 {float(grip_end):g}（与关节斜坡解耦）"
            else:
                grip_msg = ""
            dens = " · CS dense servoj" if cs_dense else ""
            self._abs_ramp_last_message = (
                f"关节斜坡开始：{dur:g}s @ {hz:g}Hz × {n} 点 ({mode}, {profile})"
                + dens
                + grip_msg
            )
            self._abs_ramp_write_count = 0
            self._abs_ramp_last_t_wall = time.time()
            self._abs_ramp_target_joints = list(q_star)
            self._abs_ramp_timing = mode
            self._abs_ramp_delta_max = dmax
            self._abs_ramp_gripper = grip_end

        self._abs_ramp_stop.clear()
        self._abs_ramp_thread = threading.Thread(
            target=self._arm_abs_ramp_loop,
            name="arm-abs-ramp",
            args=(
                reader.agent_id,
                writer.agent_id,
                list(qa0),
                list(q_star),
                n,
                hz,
                profile,
                jerk_frac,
                accel_frac,
                grip_end,
                bool(apply_gello_bias),
                bool(cs_dense),
            ),
            daemon=True,
        )
        self._abs_ramp_thread.start()
        return {
            "ok": True,
            "t_min_s": t_min,
            "t_max_s": t_max,
            "v_norm_rad_s": v_norm,
            "jerk_seg_frac": jerk_frac,
            "accel_seg_frac": accel_frac,
            "timing": mode,
            "delta_max": dmax,
            "peak_step": peak,
            **self.arm_abs_ramp_status(),
        }

    def _arm_abs_ramp_loop(
        self,
        arm_id: str,
        writer_id: str,
        qa0: list[float],
        q_star: list[float],
        n: int,
        hz: float,
        profile: str = "seven_segment",
        jerk_seg_frac: float = 0.10,
        accel_seg_frac: float = 0.15,
        gripper_position_norm: float | None = None,
        apply_gello_bias: bool = True,
        cs_dense: bool = False,
    ) -> None:
        from sensors_dcs.agents.arm_agent import ArmAgent
        from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

        period = 1.0 / max(0.1, float(hz))
        path = self._interp_path(
            qa0,
            q_star,
            n,
            profile=profile,
            jerk_seg_frac=jerk_seg_frac,
            accel_seg_frac=accel_seg_frac,
        )
        final_error: str | None = None
        final_message: str | None = None
        completed = False
        grip_soft_error: str | None = None
        grip_ok: bool | None = None
        last_g_sent: float | None = None
        grip_threads: list[threading.Thread] = []
        # Re-send only when the commanded norm actually moves. A still bias
        # stays one Modbus write; a Gello nudge mid-ramp gets another.
        grip_eps = 1e-3

        def _biased_grip() -> float | None:
            if gripper_position_norm is None:
                return None
            g = float(gripper_position_norm)
            if apply_gello_bias:
                g += float(self.effective_delta_pose_offset()[6])
            return max(0.0, min(1.0, g))

        def _kick_grip(g_target: float) -> None:
            def _run() -> None:
                nonlocal grip_ok, grip_soft_error
                gcmd = self.gripper_command(
                    position_norm=g_target,
                    allow_during_sync=True,
                )
                grip_ok = bool(gcmd.get("ok"))
                if not grip_ok:
                    grip_soft_error = str(
                        gcmd.get("error") or "gripper write failed"
                    )
                else:
                    grip_soft_error = None

            th = threading.Thread(target=_run, name="abs-ramp-grip", daemon=True)
            th.start()
            grip_threads.append(th)

        def _maybe_kick_grip() -> None:
            nonlocal last_g_sent
            g_now = _biased_grip()
            if g_now is None:
                return
            if last_g_sent is not None and abs(g_now - last_g_sent) < grip_eps:
                return
            last_g_sent = g_now
            _kick_grip(g_now)

        try:
            reader = self.agents.get(arm_id)
            writer = self.agents.get(writer_id)
            if not isinstance(reader, ArmAgent) or not isinstance(writer, ArmWriteAgent):
                final_error = "abs ramp agents missing"
            else:
                next_t = time.perf_counter()
                # Guard max_delta vs previous *command* (not lagged RTSI) on CS.
                prev_cmd = list(qa0)
                for k, qk in enumerate(path, start=1):
                    if self._abs_ramp_stop.is_set() or self._stop.is_set():
                        final_message = "绝对下发已取消"
                        break
                    q_cmd = list(qk)
                    if apply_gello_bias:
                        q_cmd = self.joints_plus_gello_bias(q_cmd)
                    # Grip tracks bias on the same ticks as joints, but the
                    # Modbus write itself stays off this deadline.
                    _maybe_kick_grip()
                    if cs_dense:
                        result = writer.command(
                            joints_rad=q_cmd,
                            reference_joints_rad=list(prev_cmd),
                            servoj_once=True,
                            relative_to_last=True,
                            idle_after=False,
                        )
                    else:
                        ref = self._joints6_from_ring(reader) or prev_cmd
                        result = writer.command(
                            joints_rad=q_cmd, reference_joints_rad=list(ref)
                        )
                    with self._abs_ramp_lock:
                        self._abs_ramp_index = k
                        self._abs_ramp_write_count += 1
                        self._abs_ramp_last_ok = bool(result.get("ok"))
                        self._abs_ramp_last_error = result.get("error")
                        self._abs_ramp_last_t_wall = time.time()
                        if not result.get("ok"):
                            self._abs_ramp_last_message = (
                                f"ramp write failed at {k}/{n}: {result.get('error')}"
                            )
                        else:
                            self._abs_ramp_last_message = f"关节斜坡 {k}/{n}"
                    if not result.get("ok"):
                        final_error = str(result.get("error") or "ramp write failed")
                        break
                    prev_cmd = list(q_cmd)
                    # Deadline schedule: arm write time must not add onto period.
                    next_t += period
                    delay = next_t - time.perf_counter()
                    if delay > 0:
                        self._abs_ramp_stop.wait(delay)
                    else:
                        # Behind schedule — catch up without sleeping; reset base.
                        next_t = time.perf_counter()
                else:
                    completed = True
                    dens = " · CS dense" if cs_dense else ""
                    final_message = (
                        f"绝对下发完成：{n} 点已写入（关节空间/{profile}{dens}）"
                    )
                    # Hold reverse socket open after dense CS ramp (no per-point idle).
                    if cs_dense:
                        try:
                            writer.command(
                                joints_rad=list(prev_cmd),
                                reference_joints_rad=list(prev_cmd),
                                servoj_once=True,
                                idle_after=True,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    if gripper_position_norm is not None:
                        deadline = time.perf_counter() + 0.25
                        for th in grip_threads:
                            remain = deadline - time.perf_counter()
                            if remain > 0:
                                th.join(timeout=remain)
                        shown = (
                            last_g_sent
                            if last_g_sent is not None
                            else float(gripper_position_norm)
                        )
                        if grip_ok:
                            final_message += f"；夹爪下发 {shown:g}"
                        elif grip_soft_error:
                            final_message += f"（夹爪软失败: {grip_soft_error}）"
                        else:
                            final_message += f"；夹爪已异步下发 {shown:g}"
                    # This interpolated command is done. Drop the bias so the
                    # next absolute target is not shifted by the same delta again.
                    if (
                        apply_gello_bias
                        and getattr(self, "_delta_pose_offset_configured", False)
                    ):
                        self.clear_delta_pose_offset()
        except Exception as e:  # noqa: BLE001 — surface to status
            final_error = f"abs ramp exception: {e}"

        with self._abs_ramp_lock:
            self._abs_ramp_enabled = False
            self._abs_ramp_thread = None
            if completed:
                self._abs_ramp_phase = "completed"
                self._abs_ramp_last_ok = True
                self._abs_ramp_last_error = None
                self._abs_ramp_last_message = final_message
            elif final_error:
                self._abs_ramp_phase = "error"
                self._abs_ramp_last_ok = False
                self._abs_ramp_last_error = final_error
                self._abs_ramp_last_message = final_error
            else:
                self._abs_ramp_phase = "idle"
                self._abs_ramp_last_ok = True
                self._abs_ramp_last_error = None
                self._abs_ramp_last_message = final_message or "绝对下发已取消"
            self._abs_ramp_last_t_wall = time.time()

    def arm_command(
        self,
        *,
        agent_id: str | None = None,
        arm: bool = False,
        disarm: bool = False,
        stop: bool = False,
        joints_rad: list[float] | None = None,
        duration_s: float | None = None,
        cancel_abs_ramp: bool = False,
        jog_joint: int | None = None,
        delta_rad: float | None = None,
        delta_deg: float | None = None,
        timing: str | None = None,
        t_min_s: float | None = None,
        t_max_s: float | None = None,
        v_norm_rad_s: float | None = None,
        jerk_seg_frac: float | None = None,
        accel_seg_frac: float | None = None,
        gripper_position_norm: float | None = None,
        apply_gello_bias: bool = True,
    ) -> dict[str, Any]:
        """Dispatch arm/disarm/jog to ``arm_write``; absolute joints use timed ramp."""
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

        if cancel_abs_ramp:
            return self.cancel_arm_abs_ramp()

        if arm:
            base = _read_joints()
            if base is None:
                return {
                    "ok": False,
                    "error": "no live arm read joints yet; wait for robot · Read before Arm",
                }
            return agent.command(arm=True)

        if disarm or stop:
            # Always kill teleop + alignment + abs ramp before disarm/estop.
            self.set_gello_arm_teleop(enabled=False)
            self.set_gello_arm_sync(enabled=False)
            self.fake_teleop_stop(message="因 Disarm/Estop 停止 Fake 遥操")
            self.cancel_arm_abs_ramp(message="因 Disarm/Estop 取消绝对下发")
            if getattr(self, "_delta_pose_offset_configured", False):
                self.clear_delta_pose_offset()
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

        if self._abs_ramp_enabled and joints_rad is None:
            # Jog/arm/misc blocked while ramping. A new joints_rad abs-send is
            # allowed through — start_arm_abs_ramp cancels and replaces the old path
            # (needed for TCP-drag live streaming).
            return {
                "ok": False,
                "error": "absolute ramp active; cancel or wait before jog",
                **self.arm_abs_ramp_status(),
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
            # CS66: pass jog to the driver so it streams from continuous last_cmd
            # (pre-baking target from lagged RTSI caused no-move / rewind shake).
            # EC TT: still expand to absolute joints from measured read.
            if self._is_cs_arm_write(agent.sensor):
                return agent.command(jog_joint=idx, delta_rad=d)
            target = list(base)
            target[idx] = float(base[idx]) + d
            return agent.command(joints_rad=target, reference_joints_rad=base)

        if joints_rad is not None:
            mode = timing
            if mode is None:
                if (
                    duration_s is not None
                    and t_min_s is None
                    and t_max_s is None
                    and v_norm_rad_s is None
                ):
                    mode = "fixed"
                else:
                    mode = "scale_by_d"
            mode_l = str(mode or "").strip().lower()
            # TCP-drag / teleop scrub: one setpoint per call (no jerk abs-ramp).
            # CS66: servoj_once + relative_to_last (never nested _servoj_stream).
            if mode_l in ("direct", "immediate", "step", "teleop"):
                if self._abs_ramp_enabled:
                    self.cancel_arm_abs_ramp(message="因直接下发取消斜坡")
                armed = bool(getattr(agent.sensor, "armed", False))
                if not armed:
                    fr = agent.ring.latest.get()
                    if fr is not None:
                        armed = bool(fr.payload.get("armed"))
                if not armed:
                    return {
                        "ok": False,
                        "error": "arm_write not armed; click Arm before direct write",
                    }
                try:
                    q_star = [float(x) for x in joints_rad]
                except (TypeError, ValueError):
                    return {"ok": False, "error": "joints_rad must be 6 floats"}
                if len(q_star) != 6 or any(not (x == x) for x in q_star):
                    return {"ok": False, "error": "joints_rad must be exactly 6 finite values"}
                base = _read_joints()
                if base is None:
                    return {
                        "ok": False,
                        "error": "no live arm read joints; refuse direct write without current pose",
                    }
                cs_dense = self._is_cs_arm_write(agent.sensor)
                if cs_dense:
                    wr = agent.command(
                        joints_rad=q_star,
                        servoj_once=True,
                        relative_to_last=True,
                        idle_after=False,
                    )
                else:
                    wr = agent.command(
                        joints_rad=q_star,
                        reference_joints_rad=list(base),
                    )
                wr = dict(wr) if isinstance(wr, dict) else {"ok": bool(wr)}
                wr["timing"] = mode_l if mode_l != "teleop" else "teleop"
                wr["write_path"] = "cs_servoj_once" if cs_dense else "ec_tt"
                if cs_dense:
                    wr["servoj_once"] = True
                # Direct write has no interp path — apply grip immediately if provided.
                if (
                    wr.get("ok")
                    and gripper_position_norm is not None
                ):
                    gcmd = self.gripper_command(
                        position_norm=float(gripper_position_norm),
                        allow_during_sync=True,
                    )
                    wr = {
                        **wr,
                        "grip_ok": bool(gcmd.get("ok")),
                        "grip_error": gcmd.get("error"),
                        "gripper_position_norm": float(gripper_position_norm),
                    }
                    if not gcmd.get("ok"):
                        wr["ok"] = False
                        wr["error"] = gcmd.get("error") or "gripper failed"
                return wr
            return self.start_arm_abs_ramp(
                joints_rad=list(joints_rad),
                duration_s=duration_s,
                agent_id=agent.agent_id,
                timing=mode,
                t_min_s=t_min_s,
                t_max_s=t_max_s,
                v_norm_rad_s=v_norm_rad_s,
                jerk_seg_frac=jerk_seg_frac,
                accel_seg_frac=accel_seg_frac,
                gripper_position_norm=gripper_position_norm,
                apply_gello_bias=apply_gello_bias,
            )

        return {"ok": False, "error": "arm, disarm/stop, joints_rad, or jog_joint required"}

    def serve(self) -> None:
        """Blocking: bring viz UI up first, then open agents; SIGINT stops both.

        Agent ``open()`` failures (common with ``dry_run: false`` / missing HW SDK)
        must not kill the process — postprocess UI stays up and Collect stays locked
        via mutable ``boot_box``.
        """
        import traceback

        from sensors_dcs.paths import default_dcs_config

        rt = self.cfg.runtime
        # Locked until agents start successfully (or permanently on open failure).
        boot_box: dict[str, Any] = {"error": "agents starting…"}
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
            gello_arm_teleop_pick_mode=self.set_gello_arm_teleop_pick_mode,
            gello_trace_set=self.set_gello_trace,
            gello_trace_status=self.gello_trace_status,
            gello_trace_export=self.export_gello_trace,
            fake_teleop_status=self.fake_teleop_status,
            fake_teleop_generate=self.fake_teleop_generate,
            fake_teleop_execute=self.fake_teleop_execute,
            fake_teleop_stop=self.fake_teleop_stop,
            pi05_connect=self.pi05_connect,
            pi05_disconnect=self.pi05_disconnect,
            pi05_status=self.pi05_status,
            pi05_set_prompt=self.pi05_set_prompt,
            pi05_step=self.pi05_step,
            delta_pose_offset_status=self.delta_pose_offset_status,
            delta_pose_offset_set=self._api_set_delta_pose_offset,
            arm_home_status=self.arm_home_status,
            arm_home_set=self.set_arm_home,
            arm_home_save=self.save_arm_home_to_yaml,
            arm_home_go=self.go_arm_home,
            arm_ik=self.arm_ik,
            shutdown=self.request_shutdown,
            boot_box=boot_box,
            postprocess_save_dir=str(self.recorder.save_dir),
            config_path=str(default_dcs_config()),
            station_peer_connect=self.peer.connect_to_master,
            station_peer_disconnect=self.peer.disconnect,
            station_peer_kick=self.peer.kick_slave,
            station_peer_ping=self.peer.ping_slave,
            station_peer_probe=self.peer.probe_slave,
            record_start_fn=self.coordinated_record_start,
            record_stop_fn=self.coordinated_record_stop,
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
            print("[sensors-dcs] signal — requesting shutdown…", flush=True)
            self.request_shutdown()

        try:
            signal.signal(signal.SIGINT, _handle_sig)
            signal.signal(signal.SIGTERM, _handle_sig)
        except ValueError:
            # signal handlers only work in the main thread (e.g. unit tests)
            pass

        server_thread = threading.Thread(
            target=self._server.run, name="sensors-dcs-uvicorn", daemon=True
        )
        server_thread.start()
        deadline = time.time() + 20.0
        while not getattr(self._server, "started", False) and time.time() < deadline:
            if self._stop.is_set() or self._server.should_exit:
                break
            time.sleep(0.05)

        print(
            f"[sensors-dcs] site={self.cfg.site} dry_run={self.manager.ctx.dry_run} "
            f"viz=http://{rt.viz_host}:{rt.viz_port}/ "
            f"save_dir={self.recorder.save_dir} episode={self.recorder.episode_index}",
            flush=True,
        )

        try:
            self.peer.start()
        except Exception as e:  # noqa: BLE001
            print(f"[sensors-dcs] peer start failed (local collect still ok): {e}", flush=True)

        try:
            self.start()
            boot_box["error"] = None
        except Exception as e:  # noqa: BLE001
            boot_box["error"] = f"{e}\n\n{traceback.format_exc()}"
            print(
                f"[sensors-dcs] agent boot failed (UI stays up; Collect locked):\n{e}",
                flush=True,
            )

        try:
            while server_thread.is_alive() and not self._stop.is_set():
                if self._server.should_exit:
                    break
                time.sleep(0.2)
        finally:
            if not self._stop.is_set():
                try:
                    self.stop(for_shutdown=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[sensors-dcs] final stop: {e}", flush=True)
            if self._server is not None:
                self._server.should_exit = True
            server_thread.join(timeout=2.0)
            print("[sensors-dcs] stopped", flush=True)
