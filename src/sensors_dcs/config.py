from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from sensors_dcs.paths import project_root


class AgentConfig(BaseModel):
    id: str
    type: Literal[
        "gello",
        "arm",
        "arm_write",
        "gripper_read",
        "gripper_write",
        "realsense",
        "pi05",
        "gello_reinforce",
    ] = "gello"
    sensor_id: str | None = None
    hz: float = 50.0
    buffer_frames: int = 64
    # pi05 client fields (ignored for other types)
    host: str | None = None
    port: int | None = None
    prompt: str | None = None
    # Infer UI/LOOP: treat term_flag as terminate when term_flag > term_threshold
    term_threshold: float = 0.5
    camera_map: dict[str, str] | None = None
    arm_agent_id: str | None = None
    gripper_agent_id: str | None = None
    # gello_reinforce: peer gello agent id (optional; default = sole gello)
    gello_agent_id: str | None = None

    @field_validator("hz")
    @classmethod
    def _hz_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("hz must be > 0")
        return v

    @field_validator("term_threshold")
    @classmethod
    def _term_threshold_finite(cls, v: float) -> float:
        if not math.isfinite(float(v)):
            raise ValueError("term_threshold must be finite")
        return float(v)

    @model_validator(mode="after")
    def _sensor_id_unless_pi05(self) -> AgentConfig:
        if self.type not in ("pi05", "gello_reinforce") and not (self.sensor_id or "").strip():
            raise ValueError("sensor_id is required unless type is pi05 or gello_reinforce")
        return self


class RuntimeConfig(BaseModel):
    control_host: str = "0.0.0.0"
    control_port: int = 7010
    viz_host: str = "0.0.0.0"
    viz_port: int = 7011
    viz_hz: float = 15.0
    console_hz: float = 5.0


class GelloArmSyncConfig(BaseModel):
    """One-shot gello→arm alignment (see docs/gello-arm-sync.md). Not teleop."""

    align_max_rad: float = 0.8
    ramp_duration_s: float = 20.0
    ramp_hz: float = 5.0
    sync_done_eps_rad: float = 0.03
    max_ramp_rounds: int = 3


class GripperGelloSyncConfig(BaseModel):
    """Server-side gello joint → gripper write (UI toggles on/off).

    Defaults match DH AG95 ``position_norm`` (~0..0.637). For 慧灵 with J7 in mm,
    set ``command_as: position_mm`` and ``clamp_max: 65`` (see configs/g26562.yaml).
    ``clamp_min`` / ``clamp_max`` null = no bound on that side.
    """

    clamp_min: float | None = 0.0
    clamp_max: float | None = 0.637
    command_as: Literal["position_norm", "position_mm"] = "position_norm"

    @field_validator("clamp_min", "clamp_max", mode="before")
    @classmethod
    def _optional_float(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("clamp_min/clamp_max must be finite or null")
        return f

    @field_validator("command_as", mode="before")
    @classmethod
    def _command_as(cls, v: Any) -> str:
        s = str(v if v is not None else "position_norm").strip().lower()
        if s in ("norm", "position"):
            s = "position_norm"
        if s in ("mm", "position_mm_s"):
            s = "position_mm"
        if s not in ("position_norm", "position_mm"):
            raise ValueError(
                "gripper_gello_sync.command_as must be position_norm or position_mm"
            )
        return s

    @model_validator(mode="after")
    def _min_le_max(self) -> GripperGelloSyncConfig:
        lo, hi = self.clamp_min, self.clamp_max
        if lo is not None and hi is not None and float(lo) > float(hi):
            raise ValueError("gripper_gello_sync.clamp_min must be <= clamp_max")
        return self


class GelloArmTeleopConfig(BaseModel):
    """Live gello→arm teleop (see docs/gello-arm-teleop.md). Default off.

    ``teleop_step_max_rad`` was removed (2026-10-31): ``latest`` mode is
    passthrough; ``eat_stream`` uses ``eat_*`` path-follow budgets instead.
    Only ``teleop_jump_abort_rad`` still gates abnormal gello jumps on enqueue.
    Extra YAML keys (including legacy ``teleop_step_max_rad``) are ignored.
    """

    model_config = {"extra": "ignore"}

    teleop_hz: float = 100.0
    teleop_enter_max_rad: float = 0.05
    teleop_jump_abort_rad: float = 0.35
    teleop_stale_max_ticks: int = 3
    teleop_write_fail_max: int = 5
    # Pick policy: latest = tip only; eat_stream = path-follow consume (CS soft-TT).
    pick_mode: str = "latest"
    # eat_stream budgets (L-inf joint path). Recommended defaults = catch-up A.
    eat_max_rad_s: float = 1.5
    eat_catch_up_rad_s: float = 3.0
    eat_lag_path_max_rad: float = 0.25
    eat_lag_age_max_s: float = 0.15
    eat_drop_stale: bool = False

    @field_validator("pick_mode", mode="before")
    @classmethod
    def _normalize_pick_mode(cls, v: Any) -> str:
        s = str(v or "latest").strip().lower().replace("-", "_")
        if s in ("eat", "eat_stream", "stream", "tt", "tt_eat"):
            return "eat_stream"
        return "latest"


class ArmAbsRampConfig(BaseModel):
    """Joint-space abs send: T=clamp(d/v_norm, t_min, t_max) + profile (see docs/jerk-seven-segment.md)."""

    t_min_s: float = 0.1
    t_max_s: float = 30.0
    # Max-joint-rad speed used for T≈d/v_norm (rad/s). UI label: v_norm.
    v_norm_rad_s: float = 0.02
    # seven_segment = jerk-limited (default); cosine = legacy; linear for opt-in.
    profile: str = "seven_segment"
    # Seven-segment time fractions of total T (see docs/jerk-seven-segment.md).
    jerk_seg_frac: float = 0.10
    accel_seg_frac: float = 0.15
    # Abs command rate; None → fall back to gello_arm_sync.ramp_hz.
    ramp_hz: float | None = 20.0
    # CS66 dense servoj floor (servoj_once ticks); ignored for EC TT.
    cs_ramp_hz: float = 50.0
    # Deprecated / ignored: grip is one-shot at abs-ramp start (not in joint loop).
    gripper_ramp_hz: float = 5.0

    @field_validator("t_min_s", "t_max_s", mode="before")
    @classmethod
    def _t_bounds(cls, v: Any) -> float:
        if v is None or v == "":
            raise ValueError("t_min_s/t_max_s required")
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("t_min_s/t_max_s must be finite")
        return max(0.1, min(30.0, f))

    @field_validator("v_norm_rad_s", mode="before")
    @classmethod
    def _v_norm(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.02
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            raise ValueError("v_norm_rad_s must be a positive finite float")
        return f

    @field_validator("profile", mode="before")
    @classmethod
    def _profile(cls, v: Any) -> str:
        s = str(v or "seven_segment").strip().lower()
        if s in ("seven", "jerk", "7seg", "7-segment"):
            s = "seven_segment"
        if s not in ("seven_segment", "cosine", "linear"):
            raise ValueError(
                "arm_abs_ramp.profile must be seven_segment, cosine, or linear"
            )
        return s

    @field_validator("jerk_seg_frac", mode="before")
    @classmethod
    def _jerk_frac(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.10
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("jerk_seg_frac must be finite")
        return max(0.02, min(0.22, f))

    @field_validator("accel_seg_frac", mode="before")
    @classmethod
    def _accel_frac(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.15
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("accel_seg_frac must be finite")
        return max(0.0, min(0.30, f))

    @field_validator("ramp_hz", mode="before")
    @classmethod
    def _ramp_hz(cls, v: Any) -> float | None:
        if v is None or v == "":
            return 20.0
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            raise ValueError("arm_abs_ramp.ramp_hz must be a positive finite float")
        return max(0.1, min(200.0, f))

    @field_validator("cs_ramp_hz", mode="before")
    @classmethod
    def _cs_ramp_hz(cls, v: Any) -> float:
        if v is None or v == "":
            return 50.0
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            raise ValueError("arm_abs_ramp.cs_ramp_hz must be a positive finite float")
        return max(1.0, min(200.0, f))

    @field_validator("gripper_ramp_hz", mode="before")
    @classmethod
    def _gripper_ramp_hz(cls, v: Any) -> float:
        if v is None or v == "":
            return 5.0
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            raise ValueError("arm_abs_ramp.gripper_ramp_hz must be a positive finite float")
        return max(0.1, min(50.0, f))

    @model_validator(mode="after")
    def _t_min_le_t_max(self) -> ArmAbsRampConfig:
        if self.t_min_s > self.t_max_s:
            raise ValueError("arm_abs_ramp.t_min_s must be <= t_max_s")
        need = 4.0 * float(self.jerk_seg_frac) + 2.0 * float(self.accel_seg_frac)
        if need > 1.0 - 1e-9:
            scale = (1.0 - 1e-6) / need
            object.__setattr__(self, "jerk_seg_frac", float(self.jerk_seg_frac) * scale)
            object.__setattr__(self, "accel_seg_frac", float(self.accel_seg_frac) * scale)
        return self


class RecordConfig(BaseModel):
    save_dir: str | None = "./data"
    episode_index: int = 0
    queue_maxsize: int = 512


class StationConfig(BaseModel):
    """LAN master/slave identity (YAML-only; UI read-only). See docs/station-multi-machine.md.

    Omitting the whole ``station:`` block ≡ ``role=master`` with idle peer (single-machine).
    """

    role: Literal["master", "slave"] = "master"
    # Stable machine id; empty → status falls back to DcsConfig.site.
    id: str = ""
    # Address shown in UI / used by peers to reach this host (not the bind address).
    advertise_ip: str = ""
    # Peer listen/connect port; None → runtime.control_port.
    peer_port: int | None = None
    # Slave-only: master endpoint to dial.
    master_ip: str = ""
    master_port: int | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v: Any) -> str:
        s = str(v if v is not None else "master").strip().lower()
        if s in ("primary", "main"):
            s = "master"
        if s in ("secondary", "follower"):
            s = "slave"
        if s not in ("master", "slave"):
            raise ValueError("station.role must be master or slave")
        return s

    @field_validator("id", "advertise_ip", "master_ip", mode="before")
    @classmethod
    def _strip_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("peer_port", "master_port", mode="before")
    @classmethod
    def _port(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            p = int(v)
        except (TypeError, ValueError) as e:
            raise ValueError("station peer/master port must be an int") from e
        if p < 1 or p > 65535:
            raise ValueError("station peer/master port must be 1..65535")
        return p

    @model_validator(mode="after")
    def _slave_needs_master(self) -> StationConfig:
        if self.role == "slave" and not (self.master_ip or "").strip():
            raise ValueError("station.master_ip is required when role=slave")
        return self


class DcsConfig(BaseModel):
    version: int = 1
    site: str = "default"
    sensors_config: str
    dry_run: bool | None = None
    # Absolute home joints for Collect/Infer Home button (rad). Optional.
    home_joints_rad: list[float] | None = None
    # Deprecated: ignored for Home motion (Home uses arm_abs_ramp t_min/t_max/v_norm).
    home_duration_s: float = 20.0
    # Flange→TCP translation (m). None → (0, 0, 0.18). Online FK/IK use this.
    tcp_xyz: list[float] | None = None
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    record: RecordConfig = Field(default_factory=RecordConfig)
    station: StationConfig = Field(default_factory=StationConfig)
    gello_arm_sync: GelloArmSyncConfig = Field(default_factory=GelloArmSyncConfig)
    gripper_gello_sync: GripperGelloSyncConfig = Field(
        default_factory=GripperGelloSyncConfig
    )
    gello_arm_teleop: GelloArmTeleopConfig = Field(default_factory=GelloArmTeleopConfig)
    arm_abs_ramp: ArmAbsRampConfig = Field(default_factory=ArmAbsRampConfig)
    agents: list[AgentConfig] = Field(default_factory=list)

    @field_validator("home_joints_rad", mode="before")
    @classmethod
    def _home_joints(cls, v: Any) -> list[float] | None:
        if v is None or v == "":
            return None
        if not isinstance(v, (list, tuple)):
            raise ValueError("home_joints_rad must be a list of 6 floats (rad)")
        if len(v) != 6:
            raise ValueError("home_joints_rad must have exactly 6 values")
        out: list[float] = []
        for i, x in enumerate(v):
            try:
                f = float(x)
            except (TypeError, ValueError) as e:
                raise ValueError(f"home_joints_rad[{i}] is not a float") from e
            if f != f:  # NaN
                raise ValueError(f"home_joints_rad[{i}] is not finite")
            out.append(f)
        return out

    @field_validator("home_duration_s", mode="before")
    @classmethod
    def _home_duration(cls, v: Any) -> float:
        if v is None or v == "":
            return 20.0
        try:
            f = float(v)
        except (TypeError, ValueError) as e:
            raise ValueError("home_duration_s must be a float (seconds)") from e
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("home_duration_s must be finite")
        return max(0.1, min(30.0, f))

    @field_validator("tcp_xyz", mode="before")
    @classmethod
    def _tcp_xyz(cls, v: Any) -> list[float] | None:
        if v is None or v == "":
            return None
        if not isinstance(v, (list, tuple)):
            raise ValueError("tcp_xyz must be a list of 3 floats (meters)")
        if len(v) != 3:
            raise ValueError("tcp_xyz must have exactly 3 values")
        out: list[float] = []
        for i, x in enumerate(v):
            try:
                f = float(x)
            except (TypeError, ValueError) as e:
                raise ValueError(f"tcp_xyz[{i}] is not a float") from e
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError(f"tcp_xyz[{i}] is not finite")
            out.append(f)
        return out

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


def resolve_save_dir(raw: str | Path, *, config_file: Path) -> Path:
    """Resolve record.save_dir: absolute, next to DCS YAML, or CWD."""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    beside = (config_file.parent / p).resolve()
    return beside


def load_dcs_config(path: str | Path) -> DcsConfig:
    root = Path(path).resolve()
    with root.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {root}")

    # Common mix-up: pointing at hik-sensors device YAML (has devices, no sensors_config).
    if "sensors_config" not in data and "devices" in data:
        raise ValueError(
            f"Not a DCS launch YAML (missing sensors_config): {root}\n"
            "  This looks like a hik-sensors device list (sensors_*.yaml).\n"
            "  Use a DCS file instead, e.g.:\n"
            "    %APPDATA%\\sensors-dcs\\configs\\default.yaml\n"
            "    %APPDATA%\\sensors-dcs\\configs\\gello_gripper.yaml\n"
            "    %APPDATA%\\sensors-dcs\\configs\\camera-only.yaml\n"
            "    %APPDATA%\\sensors-dcs\\configs\\camera-multi.yaml\n"
            "    %APPDATA%\\sensors-dcs\\configs\\robot_only.yaml\n"
            "  Or: sensors-dcs.exe -c <path-to-default.yaml>"
        )

    try:
        cfg = DcsConfig.model_validate(data)
    except Exception as e:
        raise ValueError(f"Invalid DCS config {root}: {e}") from e

    sensors_path = resolve_sensors_config(cfg.sensors_config, config_file=root)
    if not sensors_path.is_file():
        raise FileNotFoundError(
            f"sensors_config not found: {sensors_path}\n"
            f"  (referenced from DCS config {root})"
        )
    save_dir = resolve_save_dir(cfg.record.save_dir or "./data", config_file=root)
    record = cfg.record.model_copy(update={"save_dir": str(save_dir)})
    return cfg.model_copy(update={"sensors_config": str(sensors_path), "record": record})


def resolve_peer_port(cfg: DcsConfig) -> int:
    """Peer listen/dial port: station.peer_port, else runtime.control_port."""
    st = cfg.station
    if st.peer_port is not None:
        return int(st.peer_port)
    return int(cfg.runtime.control_port)


def resolve_master_port(cfg: DcsConfig) -> int:
    """Slave→master dial port: station.master_port, else peer port resolution."""
    st = cfg.station
    if st.master_port is not None:
        return int(st.master_port)
    return resolve_peer_port(cfg)


def station_summary(cfg: DcsConfig) -> dict[str, Any]:
    """Read-only station identity for /api/status and UI (no peer session state)."""
    st = cfg.station
    sid = (st.id or "").strip() or str(cfg.site or "default")
    return {
        "role": st.role,
        "id": sid,
        "advertise_ip": (st.advertise_ip or "").strip(),
        "peer_port": resolve_peer_port(cfg),
        "master_ip": (st.master_ip or "").strip(),
        "master_port": resolve_master_port(cfg) if st.role == "slave" else None,
        "is_master": st.role == "master",
        "is_slave": st.role == "slave",
    }


def config_summary(cfg: DcsConfig) -> dict[str, Any]:
    from sensors_dcs.arm_pose import DEFAULT_TCP_XYZ, normalize_tcp_xyz
    from sensors_dcs.arm_viz import resolve_arm_viz_model

    tcp = list(normalize_tcp_xyz(cfg.tcp_xyz))
    arm_viz = resolve_arm_viz_model(
        sensors_config=cfg.sensors_config,
        agents=cfg.agents,
    )
    return {
        "site": cfg.site,
        "version": cfg.version,
        "dry_run": cfg.dry_run,
        "home_joints_rad": list(cfg.home_joints_rad) if cfg.home_joints_rad else None,
        "home_duration_s": float(cfg.home_duration_s),
        "tcp_xyz": tcp,
        "tcp_xyz_default": list(DEFAULT_TCP_XYZ),
        "arm_viz": arm_viz,
        "sensors_config": cfg.sensors_config,
        "runtime": cfg.runtime.model_dump(),
        "record": cfg.record.model_dump(),
        "station": station_summary(cfg),
        "gello_arm_sync": cfg.gello_arm_sync.model_dump(),
        "gripper_gello_sync": cfg.gripper_gello_sync.model_dump(),
        "gello_arm_teleop": cfg.gello_arm_teleop.model_dump(),
        "arm_abs_ramp": cfg.arm_abs_ramp.model_dump(),
        "agents": [a.model_dump() for a in cfg.agents],
    }


def parse_home_joints(raw: Any) -> tuple[list[float] | None, str | None]:
    """Validate home joints for UI/API. Returns ``(joints, error)``."""
    if raw is None or raw == "":
        return None, "home_joints_rad 未配置"
    if isinstance(raw, str):
        parts = [p for p in raw.replace(";", ",").split(",") if p.strip()]
        raw = parts
    if not isinstance(raw, (list, tuple)):
        return None, "home_joints_rad 格式错误：需要 6 个浮点数"
    if len(raw) != 6:
        return None, f"home_joints_rad 需要恰好 6 个数，当前 {len(raw)}"
    out: list[float] = []
    for i, x in enumerate(raw):
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None, f"home_joints_rad[{i}] 不是有效数字"
        if f != f or f in (float("inf"), float("-inf")):
            return None, f"home_joints_rad[{i}] 不是有限数"
        out.append(f)
    return out, None


def parse_home_duration_s(raw: Any, *, default: float = 20.0) -> float:
    """Clamp Home ramp duration to 0.1–30s (matches abs ramp backend)."""
    if raw is None or raw == "":
        return float(default)
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if f != f or f in (float("inf"), float("-inf")):
        return float(default)
    return max(0.1, min(30.0, f))


def upsert_home_yaml(
    path: str | Path,
    *,
    joints: list[float] | None = None,
    duration_s: float | None = None,
) -> Path:
    """Insert or replace top-level home_* keys in a DCS YAML (preserves most text)."""
    import re

    root = Path(path).resolve()
    if not root.is_file():
        raise FileNotFoundError(f"config not found: {root}")
    text = root.read_text(encoding="utf-8")

    def _upsert(key: str, line: str, blob: str) -> str:
        if re.search(rf"^{re.escape(key)}\s*:", blob, flags=re.M):
            return re.sub(rf"^{re.escape(key)}\s*:.*$", line, blob, count=1, flags=re.M)
        if re.search(r"^agents\s*:", blob, flags=re.M):
            return re.sub(r"^(agents\s*:)", line + "\n\n\\1", blob, count=1, flags=re.M)
        return blob.rstrip() + "\n\n" + line + "\n"

    if joints is not None:
        joints6, err = parse_home_joints(joints)
        if joints6 is None:
            raise ValueError(err or "invalid home_joints_rad")
        jline = "home_joints_rad: [" + ", ".join(f"{x:.6g}" for x in joints6) + "]"
        text = _upsert("home_joints_rad", jline, text)
    if duration_s is not None:
        dur = parse_home_duration_s(duration_s)
        dline = f"home_duration_s: {dur:g}"
        text = _upsert("home_duration_s", dline, text)
    root.write_text(text, encoding="utf-8")
    return root


# Back-compat alias
def upsert_home_joints_yaml(path: str | Path, joints: list[float]) -> Path:
    return upsert_home_yaml(path, joints=joints)
