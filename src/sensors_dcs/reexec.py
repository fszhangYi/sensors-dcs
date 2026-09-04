"""Persist active DCS YAML path and re-exec the process after config change."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from sensors_dcs.paths import user_data_dir


def active_config_pointer() -> Path:
    return user_data_dir() / "configs" / "active_config.path"


def read_active_config_path() -> Path | None:
    ptr = active_config_pointer()
    if not ptr.is_file():
        return None
    try:
        raw = ptr.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None
    return p if p.is_file() else None


def write_active_config_path(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    ptr = active_config_pointer()
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(str(p) + "\n", encoding="utf-8")
    try:
        os.chmod(ptr, 0o600)
    except OSError:
        pass
    return p


def validate_dcs_config_file(path: str | Path) -> Path:
    """Raise ValueError if path is not a usable DCS launch YAML."""
    from sensors_dcs.config import load_dcs_config

    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"config not found: {p}")
    # Full parse — catches missing sensors_config / empty agents, etc.
    load_dcs_config(p)
    return p


def build_reexec_argv(config_path: Path) -> list[str]:
    """Build argv to restart the current entrypoint with ``-c <config>``."""
    cfg = str(config_path)
    if getattr(sys, "frozen", False):
        exe = sys.executable
        # Drop prior -c / --config pairs from frozen argv.
        out = [exe]
        args = list(sys.argv[1:])
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-c", "--config") and i + 1 < len(args):
                i += 2
                continue
            if a.startswith("--config="):
                i += 1
                continue
            out.append(a)
            i += 1
        out.extend(["-c", cfg])
        return out

    # Dev: prefer desktop_main so viz + port env stay consistent.
    return [sys.executable, "-m", "sensors_dcs.desktop_main", "-c", cfg]


def schedule_reexec(
    config_path: Path,
    *,
    shutdown: Callable[[], Any] | None = None,
    delay_s: float = 0.45,
) -> None:
    """Stop current runtime then replace this process with a fresh start."""

    def _run() -> None:
        time.sleep(max(0.05, float(delay_s)))
        if callable(shutdown):
            try:
                shutdown()
            except Exception:  # noqa: BLE001
                pass
        os.environ["SENSORS_DCS_CONFIG"] = str(config_path)
        argv = build_reexec_argv(config_path)
        try:
            os.execv(argv[0], argv)
        except Exception as e:  # noqa: BLE001
            print(f"[sensors-dcs] reexec failed: {e}", flush=True)
            os._exit(1)

    threading.Thread(target=_run, name="sensors-dcs-reexec", daemon=True).start()
