"""Desktop entry: local uvicorn viz + system browser / pywebview."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def _pick_port(preferred: int = 7011) -> int:
    env = os.environ.get("SENSORS_DCS_PORT") or os.environ.get("EOAT_PORT")
    if env:
        return int(env)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _start_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[sensors-dcs] pywebview unavailable ({exc}); falling back to browser")
        return False

    from sensors_dcs.paths import APP_TITLE, user_data_dir

    storage = user_data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window_kwargs: dict = {
        "title": APP_TITLE,
        "url": url,
        "width": 1100,
        "height": 720,
        "background_color": "#0f1419",
        "text_select": True,
    }
    start_kwargs: dict = {
        "private_mode": False,
        "storage_path": str(storage),
    }
    gui = (os.environ.get("SENSORS_DCS_WEBVIEW_GUI") or "").strip().lower()
    if not gui and sys.platform == "win32":
        gui = "edgechromium"
    if gui:
        start_kwargs["gui"] = gui
    try:
        webview.create_window(**window_kwargs)
        webview.start(**start_kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[sensors-dcs] webview start failed ({exc}); falling back to browser")
        return False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sensors-dcs", description="sensors-dcs desktop")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="DCS YAML path (must contain sensors_config + agents). "
        "Overrides SENSORS_DCS_CONFIG / AppData default.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
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

    # Apply -c before env seeding so default_dcs_config() sees it.
    if args.config:
        os.environ["SENSORS_DCS_CONFIG"] = str(args.config)

    from sensors_dcs.paths import default_dcs_config, ensure_runtime_env

    ensure_runtime_env(desktop=True)

    import uvicorn

    from sensors_dcs.config import load_dcs_config
    from sensors_dcs.runtime import Orchestrator
    from sensors_dcs.viz import create_viz_app

    cfg_path = default_dcs_config()
    print(f"[sensors-dcs] config={cfg_path}", flush=True)
    if not cfg_path.is_file():
        print(f"[sensors-dcs] config not found: {cfg_path}", file=sys.stderr)
        raise SystemExit(1)

    try:
        cfg = load_dcs_config(cfg_path)
    except Exception as e:
        print(f"[sensors-dcs] {e}", file=sys.stderr)
        raise SystemExit(1) from e

    # Desktop defaults to loopback; port may be remapped if busy.
    host = "127.0.0.1"
    port = _pick_port(int(cfg.runtime.viz_port or 7011))
    cfg.runtime.viz_host = host
    cfg.runtime.viz_port = port

    force_dry = (os.environ.get("SENSORS_DCS_DRY_RUN") or "").strip().lower()
    if force_dry in {"1", "true", "yes"}:
        cfg.dry_run = True
    elif force_dry in {"0", "false", "no"}:
        cfg.dry_run = False

    orch = Orchestrator(cfg)
    app = create_viz_app(orch.hub, orch.status)
    url = f"http://{host}:{port}/"
    ready = f"http://{host}:{port}/api/status"

    def _serve() -> None:
        uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)

    thread = threading.Thread(target=_serve, name="sensors-dcs-uvicorn", daemon=True)
    thread.start()
    orch.start()

    if not _wait_ready(ready):
        print(f"[sensors-dcs] status check failed: {ready}", file=sys.stderr)
        orch.stop()
        raise SystemExit(1)

    print(
        f"[sensors-dcs] site={cfg.site} dry_run={orch.manager.ctx.dry_run} open {url}",
        flush=True,
    )
    force_browser = (os.environ.get("SENSORS_DCS_BROWSER") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "browser",
    }
    opened = False
    if not force_browser:
        opened = _start_webview(url)
    if not opened:
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    orch.stop()


if __name__ == "__main__":
    main()
