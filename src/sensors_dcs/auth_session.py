"""Cookie + opaque session auth for sensors-dcs FastAPI viz (Embody pattern)."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from typing import Any

from sensors_dcs.users_store import (
    auth_bootstrap_path,
    bootstrap_admin,
    init_users,
    list_users_public,
    verify_login,
)

COOKIE_NAME = "sensors_dcs_session"
SESSION_TTL_SEC = 7 * 24 * 3600

_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}
_state: dict[str, Any] = {
    "enabled": False,
    "username": "sensors",
    "bootstrapped": False,
}

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}

PUBLIC_PATH_PREFIXES = (
    "/login",
    "/assets/",
)

PUBLIC_EXACT = {
    "/login",
    "/favicon.ico",
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _write_bootstrap_file(username: str, password: str) -> None:
    path = auth_bootstrap_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from sensors_dcs.users_store import hash_password

    payload = {
        "username": username,
        "password_hash": hash_password(password),
        "password": password,
        "note": "First-run bootstrap only. Prefer SENSORS_DCS_AUTH_PASSWORD. Do not commit.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def init_auth() -> dict[str, Any]:
    """Load or bootstrap credentials. Call once at process start."""
    with _lock:
        if _env_truthy("SENSORS_DCS_AUTH_DISABLED"):
            _state.update(enabled=False, username="", bootstrapped=True)
            return {"enabled": False, "username": None, "source": "disabled"}

        user = (os.environ.get("SENSORS_DCS_AUTH_USER") or "").strip() or "sensors"
        password = (os.environ.get("SENSORS_DCS_AUTH_PASSWORD") or "").strip()
        source = "env"

        info = init_users()
        if int(info.get("count") or 0) == 0:
            if not password:
                password = secrets.token_urlsafe(12)
                source = "bootstrap"
            bootstrap_admin(user, password)
            if source == "bootstrap":
                _write_bootstrap_file(user, password)
                print(
                    f"[auth] created admin in {info.get('path')} "
                    f"(user={user} password={password})",
                    flush=True,
                )
                print(
                    "[auth] set SENSORS_DCS_AUTH_PASSWORD or edit configs/users.json; "
                    "SENSORS_DCS_AUTH_DISABLED=1 to turn auth off",
                    flush=True,
                )
            else:
                print(f"[auth] enabled via env (user={user})", flush=True)
        elif password:
            bootstrap_admin(user, password)
            print(f"[auth] upserted admin via env (user={user})", flush=True)

        admins = list_users_public()
        primary = next(
            (u for u in admins if u.get("role") == "admin"),
            admins[0] if admins else None,
        )
        primary_name = str(primary.get("username") if primary else user)
        _state.update(enabled=True, username=primary_name, bootstrapped=True)
        return {"enabled": True, "username": primary_name, "source": info.get("source", source)}


def auth_enabled() -> bool:
    return bool(_state.get("enabled"))


def public_status() -> dict[str, Any]:
    return {
        "ok": True,
        "authRequired": auth_enabled(),
        "usernameHint": _state.get("username") if auth_enabled() else None,
        "loginPath": "/login",
    }


def parse_session_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    if not morsel:
        return None
    token = (morsel.value or "").strip()
    return token or None


def _purge_expired() -> None:
    now = time.time()
    dead = [k for k, v in _sessions.items() if float(v.get("expires", 0)) < now]
    for k in dead:
        _sessions.pop(k, None)


def session_profile(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with _lock:
        _purge_expired()
        row = _sessions.get(token)
        if not row:
            return None
        if float(row.get("expires", 0)) < time.time():
            _sessions.pop(token, None)
            return None
        row["expires"] = time.time() + SESSION_TTL_SEC
        return {
            "username": str(row.get("username") or ""),
            "role": str(row.get("role") or "guest"),
        }


def create_session(username: str, role: str = "guest") -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = {
            "username": username,
            "role": role or "guest",
            "expires": time.time() + SESSION_TTL_SEC,
            "created": time.time(),
        }
    return token


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def try_login(username: str, password: str) -> dict[str, Any]:
    if not auth_enabled():
        return {"ok": True, "authRequired": False, "user": None, "token": None}
    u = (username or "").strip()
    p = password or ""
    if not u or not p:
        return {"ok": False, "error": "empty_credentials"}
    prof = verify_login(u, p)
    if not prof:
        return {"ok": False, "error": "invalid_credentials"}
    token = create_session(prof["username"], str(prof.get("role") or "guest"))
    return {
        "ok": True,
        "authRequired": True,
        "user": {"username": prof["username"], "role": prof.get("role", "guest")},
        "token": token,
    }


def cookie_header_set(token: str) -> str:
    return (
        f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SEC}"
    )


def cookie_header_clear() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def request_profile(cookie_header: str | None) -> dict[str, Any] | None:
    if not auth_enabled():
        return None
    return session_profile(parse_session_cookie(cookie_header))


def is_authenticated(cookie_header: str | None) -> bool:
    if not auth_enabled():
        return True
    return request_profile(cookie_header) is not None


def path_requires_auth(path: str) -> bool:
    if not auth_enabled():
        return False
    if path in PUBLIC_EXACT or path in PUBLIC_API_PATHS:
        return False
    for prefix in PUBLIC_PATH_PREFIXES:
        if path.startswith(prefix):
            return False
    return True


def sanitize_from(raw: str | None) -> str:
    value = (raw or "/").strip() or "/"
    if not value.startswith("/") or value.startswith("//") or value.startswith("/login"):
        return "/"
    return value
