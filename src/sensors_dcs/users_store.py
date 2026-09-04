"""Multi-user PBKDF2 store for sensors-dcs (Embody cookie-session pattern)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from sensors_dcs.paths import user_data_dir

PBKDF2_ITERATIONS = 120_000
VALID_ROLES = frozenset({"admin", "operator", "guest"})

_lock = threading.RLock()
_users: list[dict[str, Any]] = []
_users_path: Path | None = None


def users_config_path() -> Path:
    return user_data_dir() / "configs" / "users.json"


def auth_bootstrap_path() -> Path:
    return user_data_dir() / "configs" / ".auth.json"


def _pbkdf2_hash(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def hash_password(password: str) -> str:
    return _pbkdf2_hash(password)


def verify_password(password: str, stored: str) -> bool:
    stored = (stored or "").strip()
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters_s, salt_hex, digest_hex = stored.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iters_s),
            )
            return secrets.compare_digest(digest.hex(), digest_hex)
        except Exception:
            return False
    return secrets.compare_digest(password, stored)


def _load_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("users"), list):
        return [u for u in raw["users"] if isinstance(u, dict)]
    if isinstance(raw, list):
        return [u for u in raw if isinstance(u, dict)]
    raise RuntimeError(f"{path} must contain a users array")


def _save_file(path: Path, users: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "users": users}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def init_users() -> dict[str, Any]:
    global _users, _users_path
    path = users_config_path()
    with _lock:
        _users_path = path
        try:
            _users = _load_file(path)
        except Exception as e:
            raise RuntimeError(f"invalid users store {path}: {e}") from e
        return {"count": len(_users), "path": str(path), "source": "file" if path.is_file() else "empty"}


def bootstrap_admin(username: str, password: str) -> dict[str, Any]:
    """Create or upsert an admin account."""
    global _users
    u = (username or "").strip() or "sensors"
    ph = _pbkdf2_hash(password)
    with _lock:
        path = _users_path or users_config_path()
        found = False
        for row in _users:
            if str(row.get("username") or "") == u:
                row["password_hash"] = ph
                row["role"] = "admin"
                row["enabled"] = True
                found = True
                break
        if not found:
            _users.append(
                {
                    "username": u,
                    "password_hash": ph,
                    "role": "admin",
                    "enabled": True,
                }
            )
        _save_file(path, _users)
        return {"username": u, "role": "admin", "enabled": True}


def list_users_public() -> list[dict[str, Any]]:
    with _lock:
        return [
            {
                "username": str(u.get("username") or ""),
                "role": str(u.get("role") or "guest"),
                "enabled": bool(u.get("enabled", True)),
            }
            for u in _users
            if u.get("username")
        ]


def find_user(username: str) -> dict[str, Any] | None:
    u = (username or "").strip()
    with _lock:
        for row in _users:
            if str(row.get("username") or "") == u:
                return {
                    "username": u,
                    "role": str(row.get("role") or "guest"),
                    "enabled": bool(row.get("enabled", True)),
                }
    return None


def _normalize_role(role: str) -> str:
    r = (role or "guest").strip().lower()
    return r if r in VALID_ROLES else "guest"


def verify_login(username: str, password: str) -> dict[str, Any] | None:
    u = (username or "").strip()
    with _lock:
        for row in _users:
            if str(row.get("username") or "") != u:
                continue
            if not bool(row.get("enabled", True)):
                return None
            if verify_password(password or "", str(row.get("password_hash") or "")):
                return {
                    "username": u,
                    "role": str(row.get("role") or "guest"),
                }
    return None


def create_user(username: str, password: str, role: str = "operator") -> dict[str, Any]:
    u = (username or "").strip()
    if not u:
        raise ValueError("username required")
    if not password:
        raise ValueError("password required")
    if len(u) > 64:
        raise ValueError("username too long")
    with _lock:
        if any(str(row.get("username") or "") == u for row in _users):
            raise ValueError("username already exists")
        row = {
            "username": u,
            "role": _normalize_role(role),
            "enabled": True,
            "password_hash": _pbkdf2_hash(password),
        }
        _users.append(row)
        path = _users_path or users_config_path()
        _save_file(path, _users)
        return {"username": u, "role": row["role"], "enabled": True}


def update_user(
    username: str,
    *,
    role: str | None = None,
    enabled: bool | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    u = (username or "").strip()
    with _lock:
        for i, row in enumerate(_users):
            if str(row.get("username") or "") != u:
                continue
            if role is not None:
                row["role"] = _normalize_role(role)
            if enabled is not None:
                row["enabled"] = bool(enabled)
            if password:
                row["password_hash"] = _pbkdf2_hash(password)
            _users[i] = row
            path = _users_path or users_config_path()
            _save_file(path, _users)
            return {
                "username": u,
                "role": str(row.get("role") or "guest"),
                "enabled": bool(row.get("enabled", True)),
            }
    raise ValueError("user not found")


def delete_user(username: str) -> None:
    u = (username or "").strip()
    with _lock:
        before = len(_users)
        _users[:] = [row for row in _users if str(row.get("username") or "") != u]
        if len(_users) == before:
            raise ValueError("user not found")
        if not _users:
            raise ValueError("cannot delete last user")
        admins = [row for row in _users if str(row.get("role") or "") == "admin"]
        if not admins:
            raise ValueError("cannot delete last admin")
        path = _users_path or users_config_path()
        _save_file(path, _users)


def is_admin(username: str | None) -> bool:
    if not username:
        return False
    with _lock:
        for row in _users:
            if str(row.get("username") or "") == username:
                return bool(row.get("enabled", True)) and str(row.get("role") or "") == "admin"
    return False
