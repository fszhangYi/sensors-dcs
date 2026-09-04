"""Packaged static assets (fonts, favicons, etc.) for the embedded viz/login shells."""

from __future__ import annotations

from pathlib import Path


def static_root() -> Path:
    return Path(__file__).resolve().parent / "static"


def fonts_dir() -> Path:
    return static_root() / "fonts"


def favicon_path(name: str = "favicon.ico") -> Path:
    """Resolve a favicon under ``static/`` (``favicon.svg`` / ``.png`` / ``.ico``)."""
    return static_root() / name
