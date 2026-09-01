from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Frame:
    """One timed sample from an agent."""

    sensor_id: str
    agent_id: str
    kind: str
    t_wall: float
    t_mono: float
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "agent_id": self.agent_id,
            "kind": self.kind,
            "t_wall": self.t_wall,
            "t_mono": self.t_mono,
            "seq": self.seq,
            "error": self.error,
            "payload": self.payload,
        }
