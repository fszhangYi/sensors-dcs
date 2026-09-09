"""Postprocess service defaults and pipeline wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.postprocess_service import list_episodes, postprocess_defaults, run_postprocess


def test_postprocess_defaults_include_camera_map() -> None:
    d = postprocess_defaults(save_dir=".")
    assert d["align"] == "asof"
    assert d["master"] == "cam-left"
    assert d["master_hz"] == 5.0
    assert d["align_clock"] == "wall"
    assert d["primary_camera"] == "cam-middle"
    assert "cam-left" in d["require"]
    assert d["max_match_dt"] == "0.033"
    assert d["materialize"] is True
    assert isinstance(d["camera_map_candidates"], list)


def test_camera_map_prefers_launch_pwd(tmp_path: Path, monkeypatch) -> None:
    import sensors_dcs.paths as paths
    from sensors_dcs.postprocess_service import default_camera_map_candidates

    repo = Path("/root/autodl-tmp/sensors-dcs")
    repo_map = repo / "configs" / "hik_camera_map.yaml"
    assert repo_map.is_file()

    paths._LAUNCH_CWD = None
    monkeypatch.chdir(repo)
    paths.capture_launch_cwd()
    # Even if cwd later moves to user-data, launch pwd wins.
    monkeypatch.chdir(tmp_path)
    cands = default_camera_map_candidates()
    assert cands, "expected at least repo camera map"
    assert Path(cands[0]).resolve() == repo_map.resolve()


def test_list_episodes(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00003"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps({"valid": True, "episode_index": 3}) + "\n", encoding="utf-8"
    )
    rows = list_episodes(tmp_path)
    assert len(rows) == 1
    assert rows[0]["name"] == "episode_00003"
    assert rows[0]["valid"] is True


def test_run_postprocess_missing_episode(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        run_postprocess(episode=tmp_path / "episode_99999")


def test_stop_returns_finished_episode_path(tmp_path: Path) -> None:
    from sensors_dcs.config import RecordConfig
    from sensors_dcs.record import RecordController

    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8)
    rc = RecordController(cfg, agents={}, site="lab")
    start = rc.start()
    assert start["ok"]
    ep_path = start["episode_path"]
    assert ep_path and Path(ep_path).is_dir()
    stop = rc.stop(valid=True)
    assert stop["ok"]
    assert stop["finished_episode_path"] == ep_path
    assert stop["finished_episode_index"] == 0
    assert stop["valid"] is True
    assert stop["episode_path"] is None
    assert stop["episode_index"] == 1

    start2 = rc.start()
    stop2 = rc.stop(valid=False)
    assert stop2["valid"] is False
    assert Path(stop2["finished_episode_path"]).joinpath("manifest.json").is_file()
    man = json.loads(
        Path(stop2["finished_episode_path"]).joinpath("manifest.json").read_text(encoding="utf-8")
    )
    assert man["valid"] is False
