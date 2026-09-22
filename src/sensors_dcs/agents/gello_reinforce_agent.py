"""Gello frame joint-delta → cumulative bias added on each absolute-ramp write."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class _ReinforceNullSensor:
    """Placeholder so BaseAgent.sensor_id works without a hik-sensors device."""

    id = "gello-reinforce"

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> dict[str, Any]:
        return {}


class GelloReinforceAgent(BaseAgent):
    """Sample peer Gello joints; fold frame-to-frame joint deltas into the cumulative bias."""

    kind = "gello_reinforce"

    def __init__(
        self,
        *,
        agent_id: str,
        hz: float = 20.0,
        buffer_frames: int = 8,
        gello_agent_id: str | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=_ReinforceNullSensor(),  # type: ignore[arg-type]
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self._gello_agent_id = gello_agent_id
        self._peers: dict[str, BaseAgent] = {}
        self._sample_fn: Callable[[list[float]], dict[str, Any]] | None = None
        self._lock = threading.Lock()
        self._last_ok: bool | None = None
        self._last_error: str | None = None
        self._last_offset: list[float] | None = None
        self._last_skipped: bool | None = None
        self._last_t_wall: float | None = None

    def bind_peers(self, agents: dict[str, BaseAgent]) -> None:
        self._peers = agents

    def bind_orchestrator(
        self,
        *,
        note_sample: Callable[[list[float]], dict[str, Any]],
    ) -> None:
        self._sample_fn = note_sample

    def _resolve_gello(self):
        from sensors_dcs.agents.gello_agent import GelloAgent

        if self._gello_agent_id:
            ag = self._peers.get(self._gello_agent_id)
            if isinstance(ag, GelloAgent):
                return ag
            return None
        found = [a for a in self._peers.values() if isinstance(a, GelloAgent)]
        return found[0] if len(found) == 1 else None

    def _read_gello7(self) -> list[float] | None:
        gello = self._resolve_gello()
        if gello is None:
            return None
        fr = gello.ring.latest.get()
        if fr is None:
            return None
        joints = (fr.payload or {}).get("joints_rad")
        if not isinstance(joints, (list, tuple)) or len(joints) < 7:
            return None
        try:
            out = [float(joints[i]) for i in range(7)]
        except (TypeError, ValueError):
            return None
        if any(not (x == x) or abs(x) == float("inf") for x in out):
            return None
        return out

    def read_frame(self) -> Frame:
        t_wall = time.time()
        t_mono = time.perf_counter()
        self._seq += 1
        err: str | None = None
        result: dict[str, Any] | None = None
        joints = self._read_gello7()
        if joints is None:
            err = "no live gello joints_rad[7]"
            with self._lock:
                self._last_ok = False
                self._last_error = err
                self._last_t_wall = t_wall
        elif self._sample_fn is None:
            err = "orchestrator sample hook not bound"
            with self._lock:
                self._last_ok = False
                self._last_error = err
                self._last_t_wall = t_wall
        else:
            try:
                result = dict(self._sample_fn(joints))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                result = {"ok": False, "error": err}
            with self._lock:
                self._last_ok = bool(result.get("ok"))
                self._last_error = result.get("error")
                self._last_skipped = bool(result.get("skipped"))
                off = result.get("offset")
                self._last_offset = list(off) if isinstance(off, (list, tuple)) else None
                self._last_t_wall = t_wall
                if not self._last_ok:
                    err = str(self._last_error or err or "sample failed")

        with self._lock:
            payload = {
                "gello_agent_id": self._gello_agent_id
                or (self._resolve_gello().agent_id if self._resolve_gello() else None),
                "last_ok": self._last_ok,
                "last_error": self._last_error,
                "last_skipped": self._last_skipped,
                "offset": list(self._last_offset) if self._last_offset else None,
            }
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            error=err,
        )
