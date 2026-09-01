from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from sensors_dcs.paths import project_root


class AgentConfig(BaseModel):
    id: str
    type: Literal["gello"] = "gello"
    sensor_id: str
    hz: float = 50.0
    buffer_frames: int = 64

    @field_validator("hz")
    @classmethod
    def _hz_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("hz must be > 0")
        return v


class RuntimeConfig(BaseModel):
    control_host: str = "0.0.0.0"
    control_port: int = 7010
    viz_host: str = "0.0.0.0"
    viz_port: int = 7011
    viz_hz: float = 15.0
    console_hz: float = 5.0


class DcsConfig(BaseModel):
    version: int = 1
    site: str = "default"
    sensors_config: str
    dry_run: bool | None = None
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    agents: list[AgentConfig] = Field(default_factory=list)

    @field_validator("agents")
    @classmethod
    def _need_agents(cls, v: list[AgentConfig]) -> list[AgentConfig]:
        if not v:
            raise ValueError("agents must not be empty")
        return v


def resolve_sensors_config(raw: str | Path, *, config_file: Path) -> Path:
    """Resolve sensors YAML: absolute, next to DCS YAML, or under project ``sensors/``."""
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()

    beside = (config_file.parent / p).resolve()
    if beside.is_file():
        return beside

    under_root = (project_root() / p).resolve()
    if under_root.is_file():
        return under_root

    # Prefer project-root style error when path looks like sensors/...
    if str(p).startswith("sensors/") or str(p).startswith("sensors\\"):
        return under_root
    return beside


def load_dcs_config(path: str | Path) -> DcsConfig:
    root = Path(path).resolve()
    with root.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {root}")

    cfg = DcsConfig.model_validate(data)
    sensors_path = resolve_sensors_config(cfg.sensors_config, config_file=root)
    if not sensors_path.is_file():
        raise FileNotFoundError(f"sensors_config not found: {sensors_path}")
    return cfg.model_copy(update={"sensors_config": str(sensors_path)})


def config_summary(cfg: DcsConfig) -> dict[str, Any]:
    return {
        "site": cfg.site,
        "version": cfg.version,
        "dry_run": cfg.dry_run,
        "sensors_config": cfg.sensors_config,
        "runtime": cfg.runtime.model_dump(),
        "agents": [a.model_dump() for a in cfg.agents],
    }
