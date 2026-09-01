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
from sensors_dcs.viz import VizHub, create_viz_app


class Orchestrator:
    """MVP runtime: start listed agents + viz publisher."""

    def __init__(self, cfg: DcsConfig) -> None:
        self.cfg = cfg
        dry = cfg.dry_run
        self.manager = SensorManager.from_yaml(cfg.sensors_config, dry_run=dry)
        self.agents: dict[str, BaseAgent] = {}
        for acfg in cfg.agents:
            sensor = self.manager.get(acfg.sensor_id)
            self.agents[acfg.id] = build_agent(acfg, sensor)
        self.hub = VizHub()
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
        if self._viz_thread and self._viz_thread.is_alive():
            self._viz_thread.join(timeout=2.0)
        if self._console_thread and self._console_thread.is_alive():
            self._console_thread.join(timeout=2.0)
        for agent in self.agents.values():
            agent.stop()
        # close any sensors that agents did not own exclusively
        self.manager.close_all()

    def _viz_payload(self) -> dict[str, Any]:
        frames = []
        agent_rates: dict[str, Any] = {}
        for aid, agent in self.agents.items():
            fr = agent.ring.latest.get()
            if fr is not None:
                frames.append(fr.to_dict())
            agent_rates[aid] = {
                "hz_target": agent.hz,
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
        }

    def _viz_loop(self) -> None:
        period = 1.0 / max(0.1, self.cfg.runtime.viz_hz)
        while not self._stop.is_set():
            self.hub.publish_threadsafe(self._viz_payload())
            self._stop.wait(period)

    def _console_loop(self) -> None:
        period = 1.0 / max(0.1, self.cfg.runtime.console_hz)
        while not self._stop.is_set():
            parts = []
            for aid, agent in self.agents.items():
                fr = agent.ring.latest.get()
                if fr is None:
                    parts.append(f"{aid}: (no frame)")
                    continue
                joints = fr.payload.get("joints_rad")
                if joints is None:
                    parts.append(f"{aid}: seq={fr.seq} err={fr.error}")
                else:
                    jtxt = ",".join(f"{x:+.3f}" for x in joints)
                    tag = "synth" if fr.payload.get("synth") else "live"
                    parts.append(f"{aid}[{tag}] seq={fr.seq} q=[{jtxt}]")
            print(" | ".join(parts), flush=True)
            self._stop.wait(period)

    def serve(self) -> None:
        """Blocking: start agents + uvicorn viz server until SIGINT."""
        rt = self.cfg.runtime
        app = create_viz_app(self.hub, self.status)
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
            f"viz=http://{rt.viz_host}:{rt.viz_port}/",
            flush=True,
        )
        try:
            self._server.run()
        finally:
            self.stop()
            print("[sensors-dcs] stopped", flush=True)
