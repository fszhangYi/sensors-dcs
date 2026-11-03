"""Session gather path resolve, dest URI, resume copy, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.gather_sync import join_publish_uri, resume_copy_tree, sync_directory
from sensors_dcs.session_gather import (
    build_gather_dest,
    convention_slave_episode_dir,
    resolve_session_for_gather,
    validate_gathered_episode,
)
from sensors_dcs.session_registry import (
    build_participant,
    build_session_doc,
    write_session_registry,
)


def _write_session(root: Path, *, sid: str = "sess-g1", ep_i: int = 7) -> dict:
    parts = [
        build_participant(
            station_id="master-a",
            role="master",
            advertise_ip="192.168.1.10",
            viz_port=7011,
            save_dir=str(root),
            episode_index=ep_i,
            t_start=100.0,
        ),
        build_participant(
            station_id="slave-right",
            role="slave",
            advertise_ip="192.168.1.21",
            viz_port=7012,
            save_dir=str(root / "slave_local"),
            episode_index=ep_i,
            t_start=100.05,
        ),
    ]
    doc = build_session_doc(
        session_id=sid,
        episode_index=ep_i,
        mode="collect",
        master_station_id="master-a",
        participants=parts,
        created_at=1.0,
    )
    write_session_registry(root, doc)
    return doc


def test_join_publish_uri_unc_and_posix() -> None:
    unc = join_publish_uri(r"\\nas\dcs", "sessions", "sid", "slave-right", "episode_00007")
    assert unc.replace("/", "\\").startswith(r"\\nas\dcs\sessions")
    assert unc.endswith(r"episode_00007") or unc.endswith("/episode_00007")

    posix = join_publish_uri("/mnt/share", "sessions", "sid", "s1", "episode_00001")
    assert posix.replace("\\", "/").endswith("sessions/sid/s1/episode_00001")

    smb = join_publish_uri("smb://nas/dcs", "sessions", "a", "b", "episode_00002")
    assert smb.startswith("smb://")
    assert smb.endswith("sessions/a/b/episode_00002")


def test_resolve_from_session_and_episode_manifest(tmp_path: Path) -> None:
    _write_session(tmp_path, sid="sess-g1", ep_i=7)
    resolved = resolve_session_for_gather(save_dir=tmp_path, session_id="sess-g1")
    assert resolved["session_id"] == "sess-g1"
    assert resolved["episode_index"] == 7
    assert resolved["episode_relpath"] == "episode_00007"

    ep = tmp_path / "episode_00007"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps({"session_id": "sess-g1", "episode_index": 7}),
        encoding="utf-8",
    )
    resolved2 = resolve_session_for_gather(save_dir=tmp_path, episode_path=ep)
    assert resolved2["session_id"] == "sess-g1"
    assert resolved2["episode_index"] == 7


def test_build_gather_dest_and_validate(tmp_path: Path) -> None:
    dest = build_gather_dest(
        save_dir=tmp_path,
        session_id="sess-g1",
        station_id="slave-right",
        episode_index=7,
        publish_uri=r"\\nas\dcs",
    )
    local = Path(dest["local_path"])
    assert local == convention_slave_episode_dir(tmp_path, "sess-g1", "slave-right", 7)
    assert local.is_dir()
    assert "slave-right" in str(dest["dest_uri"])
    assert "episode_00007" in str(dest["dest_uri"]).replace("\\", "/")

    bad = validate_gathered_episode(local, session_id="sess-g1", episode_index=7)
    assert bad["ok"] is False

    (local / "manifest.json").write_text(
        json.dumps({"session_id": "sess-g1", "episode_index": 7}),
        encoding="utf-8",
    )
    ok = validate_gathered_episode(local, session_id="sess-g1", episode_index=7)
    assert ok["ok"] is True


def test_resume_copy_skips_unchanged(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "cameras").mkdir(parents=True)
    f = src / "cameras" / "a.jpg"
    f.write_bytes(b"jpeg-bytes-1")
    (src / "manifest.json").write_text("{}", encoding="utf-8")

    r1 = resume_copy_tree(src, dst)
    assert r1["ok"] and r1["copied"] >= 2 and r1["skipped"] == 0
    assert (dst / "cameras" / "a.jpg").read_bytes() == b"jpeg-bytes-1"

    r2 = resume_copy_tree(src, dst)
    assert r2["ok"] and r2["copied"] == 0 and r2["skipped"] >= 2

    f.write_bytes(b"jpeg-bytes-2-longer")
    r3 = resume_copy_tree(src, dst)
    assert r3["ok"] and r3["copied"] >= 1
    assert (dst / "cameras" / "a.jpg").read_bytes() == b"jpeg-bytes-2-longer"


def test_sync_directory_python_fallback(tmp_path: Path) -> None:
    src = tmp_path / "ep"
    dst = tmp_path / "out"
    src.mkdir()
    (src / "manifest.json").write_text('{"episode_index":1}', encoding="utf-8")
    (src / "states").mkdir()
    (src / "states" / "arm.jsonl").write_text("{}", encoding="utf-8")
    out = sync_directory(src, str(dst), backend="python")
    assert out["ok"]
    assert (dst / "manifest.json").is_file()
    assert (dst / "states" / "arm.jsonl").is_file()


def test_resolve_requires_session(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00001"
    ep.mkdir()
    (ep / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="session_id"):
        resolve_session_for_gather(save_dir=tmp_path, episode_path=ep)
