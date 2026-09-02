"""Desktop entry: local uvicorn viz + system browser / pywebview.

Also dispatches CLI subcommands (export-timeline, filter-timeline, …) so the
frozen ``sensors-dcs.exe`` can run the same offline tools as ``sensors-dcs``.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Any

# Subcommands owned by sensors_dcs.cli — not desktop collect/viz flags.
_CLI_COMMANDS = frozenset(
    {"run", "show-config", "export-timeline", "filter-timeline", "export-hik-dataset"}
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sensors-dcs", description="sensors-dcs desktop")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="DCS YAML path (must contain sensors_config + agents). "
        "Overrides SENSORS_DCS_CONFIG / AppData default.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        default=False,
        help="Open viz UI (pywebview window, or system browser as fallback). "
        "Default: backend only; visit http://127.0.0.1:<port>/ manually.",
    )
    return parser.parse_args(argv)


def _resolve_open_ui(args: argparse.Namespace) -> bool:
    if args.ui:
        return True
    env = (os.environ.get("SENSORS_DCS_UI") or "").strip().lower()
    return env in {"1", "true", "yes", "ui", "open"}


def _dispatch_cli_if_needed(argv: list[str] | None) -> int | None:
    """If argv starts with a CLI subcommand, run cli.main and return its exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return None
    # Only the first positional token is treated as a subcommand
    # (leading flags like --help stay on the desktop parser).
    for token in args:
        if token.startswith("-"):
            continue
        if token in _CLI_COMMANDS:
            from sensors_dcs.cli import main as cli_main

            return int(cli_main(args))
        break
    return None


def main(argv: list[str] | None = None) -> None:
    cli_code = _dispatch_cli_if_needed(argv)
    if cli_code is not None:
        raise SystemExit(cli_code)
    args = _parse_args(argv)

    if getattr(sys, "frozen", False):
        try:
            import numpy as _np  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            meipass = getattr(sys, "_MEIPASS", "?")
            print(
                "[sensors-dcs] numpy import failed in frozen bundle.\n"
                f"  _MEIPASS={meipass}\n"
                f"  cwd={os.getcwd()}\n"
                f"  cause={exc!r}",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc

    if args.config:
        os.environ["SENSORS_DCS_CONFIG"] = str(args.config)

    from sensors_dcs.paths import default_dcs_config, ensure_runtime_env
    from sensors_dcs.ui_serve import _local_url, pick_port, serve_app_blocking
    from sensors_dcs.viz import create_error_app, create_viz_app

    ensure_runtime_env(desktop=True)

    cfg_path = default_dcs_config()
    print(f"[sensors-dcs] config={cfg_path}", flush=True)

    # Default local-only; YAML ``runtime.viz_host`` (often 0.0.0.0) wins when present.
    # Override: SENSORS_DCS_VIZ_HOST=0.0.0.0 for LAN without editing seeded YAML.
    host = (os.environ.get("SENSORS_DCS_VIZ_HOST") or "").strip() or "127.0.0.1"
    preferred_port = 7011
    boot_error: str | None = None
    orch = None

    if not cfg_path.is_file():
        boot_error = f"config not found: {cfg_path}"
    else:
        try:
            from sensors_dcs.config import load_dcs_config
            from sensors_dcs.runtime import Orchestrator

            cfg = load_dcs_config(cfg_path)
            preferred_port = int(cfg.runtime.viz_port or 7011)
            env_host = (os.environ.get("SENSORS_DCS_VIZ_HOST") or "").strip()
            if env_host:
                host = env_host
            else:
                host = str(cfg.runtime.viz_host or "127.0.0.1").strip() or "127.0.0.1"
            force_dry = (os.environ.get("SENSORS_DCS_DRY_RUN") or "").strip().lower()
            if force_dry in {"1", "true", "yes"}:
                cfg.dry_run = True
            elif force_dry in {"0", "false", "no"}:
                cfg.dry_run = False
            cfg.runtime.viz_host = host
            orch = Orchestrator(cfg)
        except Exception as e:  # noqa: BLE001
            boot_error = f"{e}\n\n{traceback.format_exc()}"
            print(f"[sensors-dcs] boot error (UI will show details):\n{e}", flush=True)

    port = pick_port(preferred_port)
    open_ui = _resolve_open_ui(args)
    url = _local_url(host, port, "/")

    if boot_error is not None:
        app = create_error_app(error=boot_error, config_path=str(cfg_path))
        if not open_ui:
            print(f"[sensors-dcs] boot error (headless): {boot_error.splitlines()[0]}", flush=True)
            print(f"[sensors-dcs] details at {url} — run with --ui to open automatically", flush=True)
        serve_app_blocking(app, host=host, port=port, open_ui=open_ui)
        return

    assert orch is not None
    shutdown_box: dict[str, Any] = {"server": None}

    def _shutdown() -> dict[str, Any]:
        result = orch.request_shutdown()
        server = shutdown_box.get("server")
        if server is not None:
            server.should_exit = True
        return result

    orch._uvicorn_exit = lambda: setattr(shutdown_box.get("server"), "should_exit", True) if shutdown_box.get("server") is not None else None

    app = create_viz_app(
        orch.hub,
        orch.status,
        recorder=orch.recorder,
        gripper_command=orch.gripper_command,
        gripper_gello_sync=orch.set_gripper_gello_sync,
        gripper_gello_sync_status=orch.gripper_gello_sync_status,
        arm_command=orch.arm_command,
        shutdown=_shutdown,
    )
    orch.cfg.runtime.viz_port = port
    try:
        orch.start()
    except Exception as e:  # noqa: BLE001
        boot_error = f"{e}\n\n{traceback.format_exc()}"
        print(f"[sensors-dcs] start error (UI will show details):\n{e}", flush=True)
        app = create_error_app(error=boot_error, config_path=str(cfg_path))
        if not open_ui:
            print(f"[sensors-dcs] start error (headless): {boot_error.splitlines()[0]}", flush=True)
            print(f"[sensors-dcs] details at {url} — run with --ui to open automatically", flush=True)
        serve_app_blocking(app, host=host, port=port, open_ui=open_ui)
        return

    print(
        f"[sensors-dcs] site={orch.cfg.site} dry_run={orch.manager.ctx.dry_run}",
        flush=True,
    )
    if not open_ui:
        print(f"[sensors-dcs] headless backend at {url}", flush=True)
        print("[sensors-dcs] open the URL in a browser, or restart with --ui", flush=True)
    try:
        serve_app_blocking(
            app,
            host=host,
            port=port,
            open_ui=open_ui,
            attach_server=lambda s: shutdown_box.__setitem__("server", s),
        )
    finally:
        if not orch._stop.is_set():
            orch.stop()


if __name__ == "__main__":
    main()
