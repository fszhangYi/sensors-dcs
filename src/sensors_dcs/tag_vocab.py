"""E4: optional controlled vocab for episode tags (task / env / object).

Load order (first existing wins unless ``explicit`` is set):

1. ``explicit`` path argument
2. ``SENSORS_DCS_TAG_VOCAB`` env
3. ``runtime.tag_vocab`` from the active DCS YAML (if readable)
4. ``tag_vocab.yaml`` next to the active DCS YAML
5. ``configs/tag_vocab.yaml`` under the project / bundle root
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from sensors_dcs.paths import project_root

SCHEMA = "dcs_tag_vocab_v1"


def _as_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for x in raw:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out
    return []


def _empty(path: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": SCHEMA,
        "path": path,
        "task_ids": [],
        "env_ids": [],
        "object_ids": [],
        "source": "empty",
    }


def parse_tag_vocab(data: Any, *, path: str | None = None) -> dict[str, Any]:
    if data is None:
        return _empty(path)
    if not isinstance(data, dict):
        return {
            "ok": False,
            "schema": SCHEMA,
            "path": path,
            "error": "tag vocab root must be a mapping",
            "task_ids": [],
            "env_ids": [],
            "object_ids": [],
            "source": "invalid",
        }
    # Allow nested under ``tags:`` or flat keys.
    block = data.get("tags") if isinstance(data.get("tags"), dict) else data
    return {
        "ok": True,
        "schema": SCHEMA,
        "path": path,
        "task_ids": _as_str_list(block.get("task_ids") or block.get("tasks")),
        "env_ids": _as_str_list(block.get("env_ids") or block.get("envs")),
        "object_ids": _as_str_list(block.get("object_ids") or block.get("objects")),
        "source": "file",
    }


def load_tag_vocab_file(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    if not p.is_file():
        return {
            "ok": False,
            "schema": SCHEMA,
            "path": str(p),
            "error": f"tag vocab not found: {p}",
            "task_ids": [],
            "env_ids": [],
            "object_ids": [],
            "source": "missing",
        }
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return {
            "ok": False,
            "schema": SCHEMA,
            "path": str(p),
            "error": f"{type(e).__name__}: {e}",
            "task_ids": [],
            "env_ids": [],
            "object_ids": [],
            "source": "error",
        }
    return parse_tag_vocab(raw, path=str(p))


def _runtime_tag_vocab_path(config_path: str | Path | None) -> str | None:
    if not config_path:
        return None
    cfg = Path(config_path).expanduser()
    if not cfg.is_file():
        return None
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    rt = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    raw = str(rt.get("tag_vocab") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (cfg.parent / p).resolve()
    return str(p)


def resolve_tag_vocab_candidates(
    *,
    explicit: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.expanduser().resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        out.append(Path(key))

    if explicit:
        _add(Path(explicit))
    env = (os.environ.get("SENSORS_DCS_TAG_VOCAB") or "").strip()
    if env:
        _add(Path(env))
    rt = _runtime_tag_vocab_path(config_path)
    if rt:
        _add(Path(rt))
    if config_path:
        cfg = Path(config_path).expanduser()
        if cfg.is_file():
            _add(cfg.parent / "tag_vocab.yaml")
            _add(cfg.parent / "configs" / "tag_vocab.yaml")
    _add(project_root() / "configs" / "tag_vocab.yaml")
    return out


def load_tag_vocab(
    *,
    explicit: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return vocab dict; missing files → empty lists with ok=True."""
    for cand in resolve_tag_vocab_candidates(explicit=explicit, config_path=config_path):
        if not cand.is_file():
            continue
        loaded = load_tag_vocab_file(cand)
        if loaded.get("ok"):
            return loaded
        # Unreadable file: keep searching unless explicit was that path.
        if explicit and Path(explicit).expanduser().resolve() == cand:
            return loaded
    empty = _empty(None)
    empty["source"] = "default_empty"
    return empty
