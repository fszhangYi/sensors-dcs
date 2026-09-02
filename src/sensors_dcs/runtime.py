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
        for acfg in cfg.agents:
            sensor = self.manager.get(acfg.sensor_id)
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

    def status(self) -> dict[str, Any]:
        return {
            "config": config_summary(self.cfg),
            "agents": {aid: a.stats() for aid, a in self.agents.items()},
            "sensors_site": self.manager.bundle.site,
            "sensors_dry_run": self.manager.ctx.dry_run,
            "record": self.recorder.status(),
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
        for agent in self.agents.values():
            agent.stop()
        self.manager.close_all()

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

    def serve(self) -> None:
        """Blocking: start agents + uvicorn viz server until SIGINT."""
        rt = self.cfg.runtime
        app = create_viz_app(
            self.hub,
            self.status,
            recorder=self.recorder,
            gripper_command=self.gripper_command,
        )
        config = uvicorn.Config(
            app,
            host=rt.viz_host,
            port=rt.viz_port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

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
