"""Safe filesystem listing for path picker (scoped roots + optional rootPath)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sensors_dcs.paths import project_root, user_data_dir


def default_roots() -> dict[str, Path]:
    """Browse sandboxes for the path picker.

    Primary ``workspace`` is the **parent of the project root** so sibling
    trees (other repos / shared configs) are visible one level up.
    """
    proj = project_root().resolve()
    parent = proj.parent.resolve()
    roots: dict[str, Path] = {
        "workspace": parent,
        "configs": (proj / "configs").resolve(),
        "user": (user_data_dir() / "configs").resolve(),
    }
    home = Path.home().resolve()
    if home != parent:
        roots["home"] = home
    return roots


ROOTS: dict[str, Path] = default_roots()


def refresh_roots() -> dict[str, Path]:
    global ROOTS
    ROOTS = default_roots()
    return ROOTS


def _resolve_under(root: Path, path: str) -> Path:
    root = root.resolve()
    if not path or path in (".", "/"):
        return root
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PermissionError(f"path outside root: {path}") from e
    return target


def list_children(root_key: str, path: str = "", root_path: str | None = None) -> dict[str, Any]:
    refresh_roots()
    if root_path:
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        effective_key = "custom"
    elif root_key in ROOTS:
        root = ROOTS[root_key].resolve()
        effective_key = root_key
    else:
        raise ValueError(f"unknown root: {root_key}")
    target = _resolve_under(root, path)
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    entries: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(target), key=lambda s: s.lower())
    except OSError as e:
        raise PermissionError(str(e)) from e

    for name in names:
        if name.startswith(".") or name == "__pycache__":
            continue
        child = target / name
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "path": str(child.resolve()),
                "isDir": is_dir,
            }
        )

    entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
    return {
        "ok": True,
        "rootKey": effective_key,
        "root": str(root),
        "path": str(target),
        "entries": entries,
    }


def browse_roots() -> dict[str, str]:
    refresh_roots()
    return {k: str(v) for k, v in ROOTS.items() if v.exists()}
