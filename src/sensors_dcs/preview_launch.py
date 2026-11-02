"""Ensure webrtc-preview backend is up, then hand a browser URL to the UI.

DCS「打开预览软件」previously only ``window.open``'d the frontend URL. If the
preview FastAPI process was not running, the tab failed. This module:

1. Resolves a configurable preview backend base URL
2. Probes ``GET {base}/api/health`` (proxy-free; never hold the spawn lock)
3. If down, spawns the backend (cmd configurable / auto-discovered)
4. Waits briefly for health (aborts early if the child exits)
"""

from __future__ import annotations

import http.client
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

_LOCK = threading.Lock()
_CHILD: subprocess.Popen[Any] | None = None
_CHILD_BASE: str | None = None
_CHILD_LOG: Path | None = None

DEFAULT_PREVIEW_APP_URL = "http://127.0.0.1:7050/"
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_WAIT_S = 12.0


def normalize_preview_app_url(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        return DEFAULT_PREVIEW_APP_URL
    if "://" not in s:
        s = "http://" + s
    parsed = urlparse(s)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not parsed.netloc and parsed.path and "/" not in parsed.path:
        netloc = parsed.path
    if not netloc:
        return DEFAULT_PREVIEW_APP_URL
    # Drop any path/query — base is origin only.
    return urlunparse((scheme, netloc, "/", "", "", ""))


def resolve_preview_app_url(
    override: str | None = None,
    *,
    runtime_url: str | None = None,
) -> str:
    """Priority: call override → env → runtime YAML → default."""
    for cand in (
        override,
        os.environ.get("WEBRTC_PREVIEW_URL"),
        os.environ.get("SENSORS_DCS_PREVIEW_APP_URL"),
        runtime_url,
    ):
        if cand and str(cand).strip():
            return normalize_preview_app_url(str(cand))
    return DEFAULT_PREVIEW_APP_URL


def parse_preview_listen(base_url: str) -> tuple[str, int]:
    """Host/port used for health checks and browser open (loopback-friendly)."""
    p = urlparse(normalize_preview_app_url(base_url))
    host = p.hostname or "127.0.0.1"
    port = int(p.port or (443 if p.scheme == "https" else 80))
    return host, port


def health_url(base_url: str) -> str:
    base = normalize_preview_app_url(base_url).rstrip("/")
    return f"{base}/api/health"


def probe_preview_health(base_url: str, *, timeout_s: float = 0.45) -> bool:
    """Direct HTTP GET /api/health — no system proxy (avoids Windows hang)."""
    host, port = parse_preview_listen(base_url)
    # Prefer loopback for 0.0.0.0-style open URLs (should not appear after normalize).
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
        try:
            conn.request("GET", "/api/health", headers={"Connection": "close"})
            resp = conn.getresponse()
            resp.read()
            return int(resp.status) == 200
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ValueError, TimeoutError):
        return False


def _port_open(host: str, port: int, *, timeout_s: float = 0.2) -> bool:
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _workspace_root() -> Path:
    # sensors-dcs/src/sensors_dcs/preview_launch.py → repo root (autodl-tmp)
    return Path(__file__).resolve().parents[3]


def _webrtc_preview_src() -> Path | None:
    cand = _workspace_root() / "webrtc-preview" / "src"
    if (cand / "webrtc_preview_app").is_dir():
        return cand
    sibling = Path(__file__).resolve().parents[2].parent / "webrtc-preview" / "src"
    if (sibling / "webrtc_preview_app").is_dir():
        return sibling
    return None


def _find_packaged_exe() -> Path | None:
    env = (os.environ.get("WEBRTC_PREVIEW_EXE") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    roots = [
        _workspace_root() / "webrtc-preview" / "dist",
        Path(os.environ.get("LOCALAPPDATA") or "") / "webrtc-preview",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        exes = sorted(root.glob("WebRTCPreview*.exe"), key=lambda x: x.stat().st_mtime, reverse=True)
        if exes:
            return exes[0]
        plain = root / "WebRTCPreview.exe"
        if plain.is_file():
            return plain
    return None


def _spawn_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "sensors-dcs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "preview-launch.log"


def build_launch_argv(
    *,
    base_url: str,
    bind_host: str | None = None,
    cmd: str | None = None,
    catalog: str | None = None,
) -> tuple[list[str], dict[str, str], Path | None]:
    """Return (argv, env_updates, cwd)."""
    _host, port = parse_preview_listen(base_url)
    bind = (bind_host or os.environ.get("WEBRTC_PREVIEW_HOST") or DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST
    env_updates: dict[str, str] = {
        "WEBRTC_PREVIEW_HOST": bind,
        "WEBRTC_PREVIEW_PORT": str(port),
        # Avoid child inheriting a broken HTTP(S)_PROXY that breaks its own probes.
        "NO_PROXY": "127.0.0.1,localhost," + os.environ.get("NO_PROXY", ""),
        "no_proxy": "127.0.0.1,localhost," + os.environ.get("no_proxy", ""),
    }
    if catalog:
        env_updates["WEBRTC_PREVIEW_CATALOG"] = str(catalog)

    raw_cmd = (
        cmd
        or os.environ.get("WEBRTC_PREVIEW_LAUNCH")
        or os.environ.get("SENSORS_DCS_PREVIEW_LAUNCH")
        or ""
    ).strip()
    if raw_cmd:
        if sys.platform == "win32":
            argv = shlex.split(raw_cmd, posix=False)
        else:
            argv = shlex.split(raw_cmd)
        joined = " ".join(argv).lower()
        if "--port" not in joined:
            argv.extend(["--port", str(port)])
        if "--host" not in joined:
            argv.extend(["--host", bind])
        if catalog and "--catalog" not in joined:
            argv.extend(["--catalog", catalog])
        return argv, env_updates, None

    exe = _find_packaged_exe()
    if exe is not None:
        argv = [str(exe), "web", "--host", bind, "--port", str(port)]
        if catalog:
            argv.extend(["--catalog", catalog])
        return argv, env_updates, exe.parent

    argv = [
        sys.executable,
        "-m",
        "webrtc_preview_app",
        "web",
        "--host",
        bind,
        "--port",
        str(port),
    ]
    if catalog:
        argv.extend(["--catalog", catalog])
    src = _webrtc_preview_src()
    cwd = src.parent if src is not None else None
    if src is not None:
        env_updates["PYTHONPATH"] = (
            str(src) + os.pathsep + os.environ.get("PYTHONPATH", "")
        ).rstrip(os.pathsep)
    return argv, env_updates, cwd


def _read_log_tail(path: Path | None, *, max_chars: int = 1200) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    data = data.strip()
    if len(data) <= max_chars:
        return data
    return data[-max_chars:]


def _spawn_backend(
    *,
    base_url: str,
    bind_host: str | None = None,
    cmd: str | None = None,
    catalog: str | None = None,
) -> dict[str, Any]:
    global _CHILD, _CHILD_BASE, _CHILD_LOG
    argv, env_updates, cwd = build_launch_argv(
        base_url=base_url, bind_host=bind_host, cmd=cmd, catalog=catalog
    )
    env = os.environ.copy()
    env.update(env_updates)
    # Drop proxies for the child HTTP stack entirely when probing local DCS later.
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)

    log_path = _spawn_log_path()
    try:
        log_f = open(log_path, "a", encoding="utf-8")
        log_f.write(
            f"\n--- spawn {time.strftime('%Y-%m-%d %H:%M:%S')} argv={argv!r} cwd={cwd!r} ---\n"
        )
        log_f.flush()
    except OSError:
        log_f = subprocess.DEVNULL  # type: ignore[assignment]
        log_path = None

    popen_kwargs: dict[str, Any] = {
        "args": argv,
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_f if log_f is not subprocess.DEVNULL else subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if log_f is not subprocess.DEVNULL else subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # New process group + hidden console. Avoid DETACHED_PROCESS — it can
        # leave the child unable to finish stdio setup and look "stuck".
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(**popen_kwargs)  # noqa: S603 — intentional local launcher
    except OSError as e:
        if hasattr(log_f, "close") and log_f is not subprocess.DEVNULL:
            try:
                log_f.close()
            except Exception:  # noqa: BLE001
                pass
        return {
            "ok": False,
            "error": f"failed to spawn preview backend: {e}",
            "argv": argv,
            "log": str(log_path) if log_path else None,
        }

    _CHILD = proc
    _CHILD_BASE = normalize_preview_app_url(base_url)
    _CHILD_LOG = log_path
    return {
        "ok": True,
        "spawned": True,
        "pid": proc.pid,
        "argv": argv,
        "cwd": str(cwd) if cwd else None,
        "log": str(log_path) if log_path else None,
    }


def wait_preview_healthy(
    base_url: str,
    *,
    timeout_s: float = DEFAULT_WAIT_S,
    interval_s: float = 0.2,
    child: subprocess.Popen[Any] | None = None,
) -> tuple[bool, str]:
    """Return (healthy, detail). Aborts early if ``child`` exits."""
    host, port = parse_preview_listen(base_url)
    deadline = time.time() + max(0.5, float(timeout_s))
    while time.time() < deadline:
        if child is not None:
            rc = child.poll()
            if rc is not None:
                tail = _read_log_tail(_CHILD_LOG)
                detail = f"preview process exited early (code={rc})"
                if tail:
                    detail += f"\n--- log ---\n{tail}"
                return False, detail
        if probe_preview_health(base_url):
            return True, "healthy"
        # Port up but health not ready yet — keep waiting a bit.
        _port_open(host, port)
        time.sleep(interval_s)
    tail = _read_log_tail(_CHILD_LOG)
    detail = f"timeout after {timeout_s:.0f}s waiting for {health_url(base_url)}"
    if child is not None and child.poll() is not None:
        detail += f" (process exit={child.poll()})"
    if tail:
        detail += f"\n--- log ---\n{tail}"
    return False, detail


def ensure_preview_backend(
    *,
    base_url: str,
    bind_host: str | None = None,
    cmd: str | None = None,
    catalog: str | None = None,
    timeout_s: float = DEFAULT_WAIT_S,
) -> dict[str, Any]:
    """Idempotent: if healthy, no-op; else spawn and wait (short, lock only around spawn)."""
    base = normalize_preview_app_url(base_url)
    out: dict[str, Any] = {
        "ok": False,
        "preview_app_base": base,
        "health_url": health_url(base),
        "already_running": False,
        "spawned": False,
    }

    # Network I/O must NOT run under _LOCK (proxy hangs used to freeze all opens).
    if probe_preview_health(base):
        out["ok"] = True
        out["already_running"] = True
        return out

    child: subprocess.Popen[Any] | None = None
    with _LOCK:
        # Re-check after acquiring lock (another request may have spawned).
        if probe_preview_health(base):
            out["ok"] = True
            out["already_running"] = True
            return out
        global _CHILD, _CHILD_BASE
        if _CHILD is not None and _CHILD.poll() is None and _CHILD_BASE == base:
            spawned = {
                "ok": True,
                "spawned": False,
                "pid": _CHILD.pid,
                "reused": True,
                "argv": None,
                "cwd": None,
                "log": str(_CHILD_LOG) if _CHILD_LOG else None,
            }
            child = _CHILD
        else:
            spawned = _spawn_backend(
                base_url=base, bind_host=bind_host, cmd=cmd, catalog=catalog
            )
            if not spawned.get("ok"):
                out["error"] = spawned.get("error") or "spawn failed"
                out["argv"] = spawned.get("argv")
                out["log"] = spawned.get("log")
                return out
            child = _CHILD
        out["spawned"] = bool(spawned.get("spawned"))
        out["pid"] = spawned.get("pid")
        out["argv"] = spawned.get("argv")
        out["cwd"] = spawned.get("cwd")
        out["reused"] = bool(spawned.get("reused"))
        out["log"] = spawned.get("log")

    ok, detail = wait_preview_healthy(base, timeout_s=timeout_s, child=child)
    if ok:
        out["ok"] = True
        return out
    out["error"] = detail
    return out


def build_preview_open_url(base_url: str, catalog_url: str | None) -> str:
    base = normalize_preview_app_url(base_url)
    if not catalog_url:
        return base
    return f"{base}?catalog={quote(str(catalog_url), safe='')}"
