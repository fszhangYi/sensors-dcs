"""Tests for pack_hik_datasets batch packing."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.export.pack_hik_datasets import pack_hik_datasets, parse_episode_index


def _write_episode(
    root: Path,
    name: str,
    *,
    valid: bool = True,
    with_hik: bool = True,
    with_grid: bool = True,
    with_camera_map: bool = True,
) -> Path:
    ep = root / name
    ep.mkdir(parents=True)
    (ep / "manifest.json").write_text(
        json.dumps({"valid": valid, "episode_index": parse_episode_index(name)}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    if with_hik:
        hik = ep / "export" / "hik_dataset"
        hik.mkdir(parents=True)
        (hik / "metadata.json").write_text('{"ok": true}\n', encoding="utf-8")
        (hik / "steps.json").write_text("[]\n", encoding="utf-8")
        (hik / "rgb_demo_0.jpg").write_bytes(b"fake-jpeg")
        if with_camera_map:
            (hik / "camera_map.yaml").write_text("cameras: {}\n", encoding="utf-8")
        if with_grid:
            (hik / "episode_grid.mp4").write_bytes(b"fake-mp4")
    return ep


def test_parse_episode_index() -> None:
    assert parse_episode_index("episode_00016") == 16
    assert parse_episode_index("episode_0") == 0
    assert parse_episode_index("episode_abc") is None
    assert parse_episode_index("nope") is None


def test_pack_copies_excludes_and_videos(tmp_path: Path) -> None:
    src = tmp_path / "data_new"
    dst = tmp_path / "hik_pack"
    src.mkdir()
    _write_episode(src, "episode_00000")
    _write_episode(src, "episode_00016", with_grid=False)
    _write_episode(src, "episode_00003", with_hik=False)
    _write_episode(src, "episode_00007", valid=False)

    out = pack_hik_datasets(src, dst)
    assert out["ok"] is True
    assert out["copied"] == 2
    assert out["skipped"] == 2
    assert out["warnings"] == 1  # 16 missing grid
    assert out["errors"] == 0

    assert (dst / "0" / "metadata.json").is_file()
    assert (dst / "0" / "rgb_demo_0.jpg").is_file()
    assert not (dst / "0" / "camera_map.yaml").exists()
    assert not (dst / "0" / "episode_grid.mp4").exists()
    assert (dst / "video" / "0.mp4").is_file()
    assert (dst / "video" / "0.mp4").read_bytes() == b"fake-mp4"

    assert (dst / "16" / "steps.json").is_file()
    assert not (dst / "video" / "16.mp4").exists()
    assert not (dst / "3").exists()
    assert not (dst / "7").exists()

    # Source untouched
    assert (src / "episode_00000" / "export" / "hik_dataset" / "camera_map.yaml").is_file()
    assert (src / "episode_00000" / "export" / "hik_dataset" / "episode_grid.mp4").is_file()


def test_pack_overwrite_and_allow_invalid(tmp_path: Path) -> None:
    src = tmp_path / "in"
    dst = tmp_path / "out"
    src.mkdir()
    _write_episode(src, "episode_00001")
    first = pack_hik_datasets(src, dst)
    assert first["copied"] == 1
    (dst / "1" / "stale.txt").write_text("old\n", encoding="utf-8")

    # Refresh source content and re-pack
    (src / "episode_00001" / "export" / "hik_dataset" / "metadata.json").write_text(
        '{"v": 2}\n', encoding="utf-8"
    )
    second = pack_hik_datasets(src, dst)
    assert second["copied"] == 1
    assert not (dst / "1" / "stale.txt").exists()
    assert '"v": 2' in (dst / "1" / "metadata.json").read_text(encoding="utf-8")

    _write_episode(src, "episode_00002", valid=False)
    skip = pack_hik_datasets(src, dst, skip_invalid=True)
    assert skip["copied"] == 1
    assert any(i.get("reason") == "invalid_episode" for i in skip["items"])

    keep = pack_hik_datasets(src, dst, skip_invalid=False)
    assert keep["copied"] == 2
    assert (dst / "2" / "metadata.json").is_file()


def test_pack_rejects_same_or_nested_roots(tmp_path: Path) -> None:
    src = tmp_path / "data"
    src.mkdir()
    bad = pack_hik_datasets(src, src)
    assert bad["ok"] is False
    nested = pack_hik_datasets(src, src / "out")
    assert nested["ok"] is False
