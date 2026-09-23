"""Postprocess service defaults and pipeline wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.postprocess_service import (
    clear_episode_export,
    list_episodes,
    postprocess_defaults,
    run_postprocess,
    run_postprocess_root,
)


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


def _write_ep(root: Path, name: str, *, valid: bool = True, with_export: bool = False) -> Path:
    ep = root / name
    ep.mkdir(parents=True)
    (ep / "manifest.json").write_text(
        json.dumps(
            {
                "valid": valid,
                "episode_index": int(name.rsplit("_", 1)[-1]),
                "agents": [
                    {"agent_id": "cam-left", "kind": "realsense"},
                    {"agent_id": "gello", "kind": "gello"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if with_export:
        export = ep / "export"
        export.mkdir()
        (export / "marker.txt").write_text("old\n", encoding="utf-8")
    return ep


def test_clear_episode_export(tmp_path: Path) -> None:
    ep = _write_ep(tmp_path, "episode_00001", with_export=True)
    assert (ep / "export" / "marker.txt").is_file()
    info = clear_episode_export(ep)
    assert info["cleared"] is True
    assert not (ep / "export").exists()
    assert clear_episode_export(ep)["cleared"] is False


def test_run_postprocess_root_overwrite_and_skip(tmp_path: Path, monkeypatch) -> None:
    ep_ok = _write_ep(tmp_path, "episode_00001", with_export=True)
    ep_skip = _write_ep(tmp_path, "episode_00002", with_export=True)
    ep_bad = _write_ep(tmp_path, "episode_00003", valid=False)
    called: list[str] = []

    def _fake_run(**kwargs):
        ep = Path(kwargs["episode"])
        called.append(ep.name)
        # overwrite should have removed export before call
        assert not (ep / "export").exists()
        (ep / "export").mkdir()
        (ep / "export" / "done.txt").write_text("1\n", encoding="utf-8")
        return {"ok": True, "episode": str(ep), "results": [], "log": "ok"}

    monkeypatch.setattr(
        "sensors_dcs.postprocess_service.run_postprocess",
        _fake_run,
    )

    # overwrite=false → skip both that have export/
    out_skip = run_postprocess_root(
        input_root=tmp_path,
        camera_map=str(tmp_path / "map.yaml"),
        overwrite=False,
        allow_invalid=False,
    )
    assert out_skip["ok"] is True
    assert out_skip["ok_count"] == 0
    assert out_skip["skipped"] >= 2
    assert called == []
    assert (ep_ok / "export" / "marker.txt").is_file()

    out = run_postprocess_root(
        input_root=tmp_path,
        camera_map=str(tmp_path / "map.yaml"),
        overwrite=True,
        allow_invalid=False,
        master="cam-left",
    )
    assert out["failed"] == 0
    assert out["ok_count"] == 2
    assert "episode_00001" in called and "episode_00002" in called
    assert "episode_00003" not in called  # invalid skipped
    assert not (ep_ok / "export" / "marker.txt").exists()
    assert (ep_ok / "export" / "done.txt").is_file()
    assert (ep_skip / "export" / "done.txt").is_file()
    assert ep_bad.is_dir()
