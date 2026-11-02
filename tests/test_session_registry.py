"""Session registry uniqueness + discover/inspect helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.session_registry import (
    build_participant,
    build_session_doc,
    inspect_session_paths,
    list_session_docs,
    read_session_registry,
    validate_unique_station_ids,
    write_session_registry,
)


def test_validate_unique_station_ids() -> None:
    validate_unique_station_ids(
        [
            {"station_id": "m"},
            {"station_id": "s1"},
        ]
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_unique_station_ids(
            [
                {"station_id": "m"},
                {"station_id": "m"},
            ]
        )


def test_write_and_list_session(tmp_path: Path) -> None:
    parts = [
        build_participant(
            station_id="master-a",
            role="master",
            advertise_ip="127.0.0.1",
            viz_port=7011,
            save_dir=str(tmp_path / "m"),
            episode_index=3,
            t_start=100.0,
        ),
        build_participant(
            station_id="slave-b",
            role="slave",
            advertise_ip="127.0.0.1",
            viz_port=7012,
            save_dir=str(tmp_path / "s"),
            episode_index=3,
            t_start=100.05,
            share_root=str(tmp_path / "share"),
        ),
    ]
    doc = build_session_doc(
        session_id="sess-abc",
        episode_index=3,
        mode="collect",
        master_station_id="master-a",
        participants=parts,
        created_at=1.0,
    )
    path = write_session_registry(tmp_path / "m", doc)
    assert path.is_file()
    assert path.name == "session.json"
    loaded = read_session_registry(tmp_path / "m", "sess-abc")
    assert loaded is not None
    assert loaded["schema"] == "dcs_session_v1"
    assert loaded["session_id"] == "sess-abc"
    assert len(loaded["participants"]) == 2
    assert loaded["participants"][1]["episode_uri"]["kind"] == "share"

    rows = list_session_docs(tmp_path / "m")
    assert len(rows) == 1
    assert rows[0]["session_id"] == "sess-abc"
    assert rows[0]["participant_count"] == 2


def test_inspect_session_paths_missing(tmp_path: Path) -> None:
    parts = [
        build_participant(
            station_id="master-a",
            role="master",
            save_dir=str(tmp_path / "m"),
            episode_index=0,
        ),
        build_participant(
            station_id="slave-b",
            role="slave",
            save_dir=str(tmp_path / "s"),
            episode_index=0,
        ),
    ]
    # Create only master episode with a manifest.
    ep_m = tmp_path / "m" / "episode_00000"
    ep_m.mkdir(parents=True)
    (ep_m / "manifest.json").write_text(
        json.dumps({"valid": True, "t_start": 1.0, "agents": []}), encoding="utf-8"
    )
    parts[0]["episode_path"] = str(ep_m)
    doc = build_session_doc(
        session_id="sess-x",
        episode_index=0,
        mode="collect",
        master_station_id="master-a",
        participants=parts,
    )
    health = inspect_session_paths(doc)
    assert health["missing_count"] == 1
    assert health["all_present"] is False
    by_id = {r["station_id"]: r for r in health["participants"]}
    assert by_id["master-a"]["ok"] is True
    assert by_id["slave-b"]["ok"] is False


def test_share_root_uri(tmp_path: Path) -> None:
    p = build_participant(
        station_id="cell-right",
        role="slave",
        save_dir=str(tmp_path / "local"),
        episode_index=1,
        share_root=r"\\nas\dcs",
        share_station_subdir="",
    )
    assert p["episode_uri"]["kind"] == "share"
    assert "cell-right" in p["episode_uri"]["uri"]
    assert "episode_00001" in p["episode_uri"]["uri"]


def test_relocated_save_dir_master_vs_slave(tmp_path: Path) -> None:
    """Zip/analysis copy: master at save_dir/episode_*; slaves under sessions/."""
    from sensors_dcs.session_registry import resolve_participant_episode_path

    save = tmp_path / "relocated"
    master_ep = save / "episode_00011"
    master_ep.mkdir(parents=True)
    (master_ep / "manifest.json").write_text("{}", encoding="utf-8")
    slave_ep = save / "sessions" / "sess-1" / "slave-left" / "episode_00011"
    slave_ep.mkdir(parents=True)
    (slave_ep / "manifest.json").write_text("{}", encoding="utf-8")

    master = {
        "station_id": "master-a",
        "role": "master",
        "episode_index": 11,
        "episode_path": str(tmp_path / "old_machine" / "episode_00011"),
    }
    slave = {
        "station_id": "slave-left",
        "role": "slave",
        "episode_index": 11,
        "episode_path": str(tmp_path / "old_slave" / "episode_00011"),
    }
    m = resolve_participant_episode_path(
        master, save_dir=save, session_id="sess-1"
    )
    s = resolve_participant_episode_path(
        slave, save_dir=save, session_id="sess-1"
    )
    assert m == master_ep.resolve()
    assert s == slave_ep.resolve()
    assert m != s
