"""Export refuses discarded episodes unless allow_invalid=True."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.export.timeline import ensure_episode_exportable, export_episode_timeline


def _episode_with_manifest(tmp_path: Path, *, valid: bool) -> Path:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    (ep / "states").mkdir()
    (ep / "cameras").mkdir()
    man = {
        "site": "lab",
        "episode_index": 0,
        "t_start": 1.0,
        "t_end": 2.0,
        "written": 0,
        "dropped": 0,
        "agents": [],
        "cameras": {},
        "valid": valid,
        "format": "dcs_episode_v1",
    }
    (ep / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return ep


def test_ensure_episode_exportable_blocks_invalid() -> None:
    with pytest.raises(ValueError, match="valid=false"):
        ensure_episode_exportable({"valid": False}, episode_label="episode_00000")
    ensure_episode_exportable({"valid": False}, allow_invalid=True)
    ensure_episode_exportable({"valid": True})
    ensure_episode_exportable({})  # legacy / missing field → allow


def test_export_timeline_skips_discarded(tmp_path: Path) -> None:
    ep = _episode_with_manifest(tmp_path, valid=False)
    with pytest.raises(ValueError, match="valid=false"):
        export_episode_timeline(ep)
    meta = export_episode_timeline(ep, allow_invalid=True)
    assert meta["source_episode"] == "episode_00000"
