from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sensors_dcs.export.timeline import (
    build_aligned_frame,
    build_events_frame,
    export_episode_timeline,
    load_episode,
    pick_default_master,
    subsample_times,
)


@pytest.fixture
def episode_dir(tmp_path: Path) -> Path:
    ep = tmp_path / "episode_00000"
    (ep / "states").mkdir(parents=True)
    (ep / "cameras" / "camera").mkdir(parents=True)

    manifest = {
        "site": "test",
        "episode_index": 0,
        "t_start": 1000.0,
        "t_end": 1000.2,
        "written": 5,
        "dropped": 0,
        "agents": [
            {"agent_id": "gello", "kind": "gello", "hz_target": 50.0},
            {"agent_id": "camera", "kind": "realsense", "hz_target": 15.0},
        ],
        "valid": True,
        "format": "dcs_episode_v1",
    }
    (ep / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    gello_lines = [
        {
            "agent_id": "gello",
            "sensor_id": "gello-main",
            "kind": "gello",
            "seq": 10,
            "t_wall": 1000.00,
            "t_mono": 1.0,
            "payload": {"joints_rad": [0.1, 0.2, 0.3], "dry_run": True},
        },
        {
            "agent_id": "gello",
            "sensor_id": "gello-main",
            "kind": "gello",
            "seq": 11,
            "t_wall": 1000.02,
            "t_mono": 1.02,
            "payload": {"joints_rad": [0.2, 0.3, 0.4], "dry_run": True},
        },
    ]
    with (ep / "states" / "gello.jsonl").open("w", encoding="utf-8") as f:
        for row in gello_lines:
            f.write(json.dumps(row) + "\n")

    cam_lines = [
        {
            "agent_id": "camera",
            "sensor_id": "rs-main",
            "kind": "realsense",
            "seq": 5,
            "t_wall": 1000.01,
            "t_mono": 1.01,
            "file": "00000005.jpg",
            "role": "middle",
            "dry_run": True,
        },
        {
            "agent_id": "camera",
            "sensor_id": "rs-main",
            "kind": "realsense",
            "seq": 6,
            "t_wall": 1000.03,
            "t_mono": 1.03,
            "file": "00000006.jpg",
            "role": "middle",
            "dry_run": True,
        },
    ]
    with (ep / "cameras" / "camera" / "index.jsonl").open("w", encoding="utf-8") as f:
        for row in cam_lines:
            f.write(json.dumps(row) + "\n")
    (ep / "cameras" / "camera" / "00000005.jpg").write_bytes(b"fakejpeg")
    return ep


def test_load_episode_counts(episode_dir: Path) -> None:
    manifest, samples = load_episode(episode_dir)
    assert manifest["episode_index"] == 0
    assert len(samples) == 4
    assert pick_default_master(manifest, samples) == "gello"


def test_events_frame_sorted(episode_dir: Path) -> None:
    manifest, samples = load_episode(episode_dir)
    df = build_events_frame(manifest, samples)
    assert len(df) == 4
    assert list(df["agent_id"]) == ["gello", "camera", "gello", "camera"]
    assert df.iloc[0]["t_rel"] == pytest.approx(0.0)
    cam_row = df[df["kind"] == "realsense"].iloc[0]
    assert cam_row["image_relpath"] == "cameras/camera/00000005.jpg"
    assert cam_row["file"] == "00000005.jpg"


def test_aligned_asof_master_match_dt(episode_dir: Path) -> None:
    manifest, samples = load_episode(episode_dir)
    df = build_aligned_frame(manifest, samples, master="gello", mode="asof")
    assert len(df) == 2
    assert (df["gello.match_dt"] == 0.0).all()
    # as-of backward: at t=1000.00 no camera sample yet
    assert pd.isna(df.iloc[0]["camera.file"])
    second = df.iloc[1]
    assert second["camera.file"] == "00000005.jpg"
    assert second["camera.match_dt"] == pytest.approx(0.01)


def test_export_episode_timeline_writes_files(episode_dir: Path) -> None:
    meta = export_episode_timeline(episode_dir, align="asof", master="gello")
    out = Path(meta["output_dir"])
    assert (out / "timeline_events.parquet").is_file()
    assert (out / "timeline_aligned.parquet").is_file()
    assert (out / "export_meta.json").is_file()
    assert meta["rows"]["events"] == 4
    assert meta["rows"]["aligned"] == 2


def test_subsample_times_reduces_count() -> None:
    times = [1000.0 + i * 0.02 for i in range(10)]  # 50Hz over 0.18s
    out = subsample_times(times, 15.0)
    assert len(out) < len(times)
    assert out[0] == pytest.approx(1000.0)
    assert all(out[i] <= out[i + 1] for i in range(len(out) - 1))


def test_aligned_master_hz_downsamples(episode_dir: Path) -> None:
    manifest, samples = load_episode(episode_dir)
    full = build_aligned_frame(manifest, samples, master="gello", mode="asof")
    down = build_aligned_frame(
        manifest, samples, master="gello", mode="asof", master_hz=15.0
    )
    assert len(down) <= len(full)
    assert len(down) >= 1


def test_export_master_hz_in_meta(episode_dir: Path) -> None:
    meta = export_episode_timeline(
        episode_dir, align="asof", master="gello", master_hz=15.0
    )
    assert meta["align"]["master_hz"] == 15.0


def test_real_episode_if_present() -> None:
    ep = Path("configs/data/episode_00000")
    if not ep.is_dir():
        pytest.skip("no recorded episode fixture on disk")
    manifest, samples = load_episode(ep)
    assert len(samples) >= 1
    meta = export_episode_timeline(ep, align="asof")
    assert meta["rows"]["events"] == len(samples)
