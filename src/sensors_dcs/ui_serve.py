"""Shared local UI server helpers (desktop + CLI)."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
import urllib.request
import webbrowser
from typing import Any

import uvicorn
from fastapi import FastAPI


def pick_port(preferred: int = 7011) -> int:
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


def wait_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def start_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[sensors-dcs] pywebview unavailable ({exc}); falling back to browser")
        return False

    import sys

    from sensors_dcs.paths import APP_TITLE, user_data_dir

    storage = user_data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window_kwargs: dict[str, Any] = {
        "title": APP_TITLE,
        "url": url,
        "width": 734,
        "height": 480,
        "background_color": "#0f1419",
        "text_select": True,
    }
    start_kwargs: dict[str, Any] = {
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


def serve_app_blocking(
    app: FastAPI,
    *,
    host: str = "127.0.0.1",
    port: int = 7011,
    open_ui: bool = True,
) -> None:
    """Run uvicorn until SIGINT/SIGTERM; optionally open webview/browser."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)

    def _handle_sig(*_args: object) -> None:
        server.should_exit = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    url = f"http://{host}:{port}/"
    ready = f"http://{host}:{port}/api/status"

    thread = threading.Thread(target=server.run, name="sensors-dcs-uvicorn", daemon=True)
    thread.start()

    if not wait_ready(ready):
        print(f"[sensors-dcs] status check failed: {ready}", flush=True)
        server.should_exit = True
        thread.join(timeout=2.0)
        return

    print(f"[sensors-dcs] open {url}", flush=True)
    if not open_ui:
        print("[sensors-dcs] headless — UI not opened (use --ui or SENSORS_DCS_UI=1)", flush=True)
    if open_ui:
        force_browser = (os.environ.get("SENSORS_DCS_BROWSER") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "browser",
        }
        opened = False
        if not force_browser:
            opened = start_webview(url)
        if not opened:
            webbrowser.open(url)
            try:
                while thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                server.should_exit = True
        else:
            server.should_exit = True
    else:
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True

    thread.join(timeout=3.0)
