"""Frozen / desktop path helpers for sensors-dcs (Scheme A compat)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ID = "sensors-dcs"
APP_TITLE = "sensors-dcs"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def meipass() -> Path | None:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return None


def project_root() -> Path:
    """Repo root (dev) or PyInstaller bundle root (_MEIPASS)."""
    mp = meipass()
    if mp is not None:
        return mp
    return Path(__file__).resolve().parents[2]


def sensors_root() -> Path:
    """Checkout / bundled root behind the ``sensors`` tree."""
    return project_root() / "sensors"


def sensors_src_dir() -> Path:
    """Directory that contains the ``sensors`` Python package."""
    root = project_root()
    for candidate in (
        root / "sensors_src",
        root / "sensors" / "src",
        root.parent / "sensors" / "src",
    ):
        if (candidate / "sensors").is_dir():
            return candidate
    return root / "sensors" / "src"


def ensure_sensors_import() -> Path:
    """Prefer local / bundled sensors on ``sys.path`` over site-packages."""
    src = sensors_src_dir()
    if src.is_dir():
        s = str(src)
        if s in sys.path:
            sys.path.remove(s)
        sys.path.insert(0, s)
    return src


def resolve_frontend_dist() -> Path:
    """Scheme A checklist path; sensors-dcs UI is embedded in viz FastAPI."""
    root = project_root()
    for candidate in (
        Path(os.environ["FRONTEND_DIST"]) if os.environ.get("FRONTEND_DIST") else None,
        root / "frontend-dist",
        root / "packaging" / "frontend-dist-stub",
    ):
        if candidate is None:
            continue
        if candidate.is_dir():
            return candidate
    return root / "frontend-dist"


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def default_dcs_config() -> Path:
    env = (os.environ.get("SENSORS_DCS_CONFIG") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    data = user_data_dir() / "configs" / "gello_gripper.yaml"
    if data.is_file():
        return data
    # fallback older single-agent config
    legacy = user_data_dir() / "configs" / "gello_only.yaml"
    if legacy.is_file():
        return legacy
    bundled = project_root() / "configs" / "gello_gripper.yaml"
    if bundled.is_file():
        return bundled
    # Dev layout: repo configs next to src/
    if not is_frozen():
        repo = Path(__file__).resolve().parents[2] / "configs" / "gello_gripper.yaml"
        if repo.is_file():
            return repo
    return data


def _seed_file(src: Path, dst: Path) -> None:
    if dst.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _seed_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    if not dst.exists():
        shutil.copytree(src, dst)


def ensure_runtime_env(*, desktop: bool = False) -> Path:
    """Prepare writable user data + sys.path for desktop / frozen runs."""
    data = user_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "configs").mkdir(parents=True, exist_ok=True)

    root = project_root()
    # Seed DCS + sensors YAML into user data (editable after first run)
    for name in (
        "gello_only.yaml",
        "sensors_gello.yaml",
        "gello_gripper.yaml",
        "sensors_gello_gripper.yaml",
        "camera-only.yaml",
        "sensors_camera.yaml",
        "camera-multi.yaml",
        "sensors_cameras.yaml",
        "full_cell.yaml",
        "sensors_full_cell.yaml",
        "robot_only.yaml",
        "sensors_robot.yaml",
    ):
        _seed_file(root / "configs" / name, data / "configs" / name)

    bundled_sensors_cfg = root / "sensors" / "configs"
    if not bundled_sensors_cfg.is_dir():
        # frozen layout may place hik-sensors configs under sensors/configs
        alt = root / "sensors_configs"
        if alt.is_dir():
            bundled_sensors_cfg = alt
    _seed_tree(bundled_sensors_cfg, data / "sensors" / "configs")

    # Rewrite seeded DCS YAMLs to point at sibling sensors_*.yaml in user data
    for dcs_name, sensors_name in (
        ("gello_only.yaml", "sensors_gello.yaml"),
        ("gello_gripper.yaml", "sensors_gello_gripper.yaml"),
        ("camera-only.yaml", "sensors_camera.yaml"),
        ("camera-multi.yaml", "sensors_cameras.yaml"),
        ("full_cell.yaml", "sensors_full_cell.yaml"),
        ("robot_only.yaml", "sensors_robot.yaml"),
    ):
        seeded = data / "configs" / dcs_name
        sensors_local = data / "configs" / sensors_name
        if seeded.is_file() and sensors_local.is_file():
            text = seeded.read_text(encoding="utf-8")
            if "sensors_config:" in text:
                lines = []
                for line in text.splitlines():
                    if line.strip().startswith("sensors_config:"):
                        lines.append(f"sensors_config: ./{sensors_name}")
                    else:
                        lines.append(line)
                seeded.write_text("\n".join(lines) + "\n", encoding="utf-8")

    frontend = resolve_frontend_dist()
    os.environ.setdefault("FRONTEND_DIST", str(frontend.resolve()))
    os.environ.setdefault("SENSORS_DCS_CONFIG", str(default_dcs_config()))
    os.environ.setdefault("SENSORS_DCS_USER_DATA", str(data.resolve()))

    ensure_sensors_import()

    if desktop or is_frozen():
        work = data / "run"
        work.mkdir(parents=True, exist_ok=True)
        os.chdir(work)
    return data
