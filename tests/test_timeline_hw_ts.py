"""W2 D9: HW timestamp normalize + primary-camera grid."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.export.timeline import (
    Sample,
    build_aligned_frame,
    build_aligned_frame_with_meta,
    collect_primary_camera_anchors,
    export_episode_timeline,
    hw_grid_times,
    load_episode,
    normalize_hw_timestamp,
)


def test_normalize_hw_timestamp_ms_vs_wall() -> None:
    t_wall = 1788752504.3331985
    raw_ms = 1788752504337.1316
    t_hw, unit = normalize_hw_timestamp(raw_ms, t_wall_hint=t_wall)
    assert unit == "ms"
    assert t_hw == pytest.approx(raw_ms / 1000.0)
    assert abs(t_hw - t_wall) < 0.01


def test_normalize_hw_timestamp_already_seconds() -> None:
    t_wall = 1000.05
    raw = 1000.051
    t_hw, unit = normalize_hw_timestamp(raw, t_wall_hint=t_wall)
    assert unit == "s"
    assert t_hw == pytest.approx(raw)


def _episode_with_hw(tmp_path: Path, *, with_hw: bool = True) -> Path:
    ep = tmp_path / "episode_00099"
    (ep / "states").mkdir(parents=True)
    (ep / "cameras" / "cam-middle").mkdir(parents=True)
    t0 = 2000.0
    # HW in ms; slight offset from wall so grid order can differ if shuffled.
    hw0_ms = t0 * 1000.0 + 5.0
    manifest = {
        "site": "test",
        "episode_index": 99,
        "t_start": t0,
        "t_end": t0 + 0.2,
        "written": 6,
        "dropped": 0,
        "valid": True,
        "agents": [
            {"agent_id": "gello", "kind": "gello", "hz_target": 50.0},
            {"agent_id": "cam-middle", "kind": "realsense", "hz_target": 30.0},
        ],
    }
    (ep / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with (ep / "states" / "gello.jsonl").open("w", encoding="utf-8") as f:
        for i in range(3):
            f.write(
                json.dumps(
                    {
                        "agent_id": "gello",
                        "sensor_id": "gello-main",
                        "kind": "gello",
                        "seq": i,
                        "t_wall": t0 + i * 0.02,
                        "t_mono": 1.0 + i * 0.02,
                        "payload": {"joints_rad": [0.1 * i, 0.2, 0.3]},
                    }
                )
                + "\n"
            )

    # Deliberately write camera rows out of HW order in file; grid must sort by HW.
    cam_specs = [
        (1, t0 + 0.10, hw0_ms + 100.0),  # second in HW time
        (0, t0 + 0.00, hw0_ms + 0.0),  # first
        (2, t0 + 0.20, hw0_ms + 200.0),  # third
        (3, t0 + 0.20, hw0_ms + 200.0),  # dup HW → dedup
    ]
    with (ep / "cameras" / "cam-middle" / "index.jsonl").open("w", encoding="utf-8") as f:
        for seq, tw, hw_ms in cam_specs:
            row = {
                "agent_id": "cam-middle",
                "sensor_id": "rs-middle",
                "kind": "realsense",
                "seq": seq,
                "t_wall": tw,
                "t_mono": tw,
                "file": f"{seq:08d}.jpg",
                "role": "middle",
            }
            if with_hw:
                row["color_timestamp"] = hw_ms
                row["color_timestamp_domain"] = "timestamp_domain.system_time"
            f.write(json.dumps(row) + "\n")
            (ep / "cameras" / "cam-middle" / f"{seq:08d}.jpg").write_bytes(b"x")
    return ep


def test_hw_grid_length_and_endpoints(tmp_path: Path) -> None:
    ep = _episode_with_hw(tmp_path, with_hw=True)
    _manifest, samples = load_episode(ep)
    anchors, info = collect_primary_camera_anchors(
        samples, primary_camera="cam-middle"
    )
    assert info["align_fallback"] is False
    assert info["hw_raw_unit_detected"] == "ms"
    times = hw_grid_times(anchors)
    # 4 rows but one duplicate HW → 3 unique
    assert len(times) == 3
    assert times[0] == pytest.approx(2000.005)
    assert times[-1] == pytest.approx(2000.205)
    assert times == sorted(times)


def test_hw_aligned_export_uses_primary_camera_rows(tmp_path: Path) -> None:
    ep = _episode_with_hw(tmp_path, with_hw=True)
    meta = export_episode_timeline(
        ep,
        align="nearest",
        align_clock="hw_ts",
        primary_camera="cam-middle",
        fmt="csv",
    )
    assert meta["align_clock"] == "hw_ts"
    assert meta["align_fallback"] is False
    assert meta["primary_camera"] == "cam-middle"
    assert meta["rows"]["aligned"] == 3
    assert meta["align"]["master"] == "cam-middle"
    out = Path(meta["output_dir"])
    import pandas as pd

    df = pd.read_csv(out / "timeline_aligned.csv")
    assert "t_hw" in df.columns
    assert list(df["t_hw"]) == sorted(df["t_hw"].tolist())


def test_no_hw_falls_back_to_wall_behavior(tmp_path: Path) -> None:
    ep = _episode_with_hw(tmp_path, with_hw=False)
    manifest, samples = load_episode(ep)
    wall_df = build_aligned_frame(manifest, samples, master="gello", mode="asof")
    with pytest.warns(UserWarning, match="hw_field_absent"):
        hw_df, clock_meta = build_aligned_frame_with_meta(
            manifest,
            samples,
            master="gello",
            mode="asof",
            align_clock="hw_ts",
            primary_camera="cam-middle",
        )
    assert clock_meta["align_fallback"] is True
    assert clock_meta["align_fallback_reason"] == "hw_field_absent"
    assert clock_meta["align_clock"] == "wall"
    assert len(hw_df) == len(wall_df)
    assert list(hw_df["t_wall"]) == list(wall_df["t_wall"])


def test_wall_default_unchanged_without_hw_flag(tmp_path: Path) -> None:
    """Regression: default export path still masters on gello."""
    ep = _episode_with_hw(tmp_path, with_hw=True)
    meta = export_episode_timeline(ep, align="asof", master="gello", fmt="csv")
    assert meta.get("align_clock", "wall") == "wall"
    assert meta["align"]["master"] == "gello"
    assert meta["align_fallback"] is False
    assert meta["rows"]["aligned"] == 3  # gello samples


def _gello_only_episode(tmp_path: Path) -> Path:
    """Episode with state only — no cameras (missing primary)."""
    ep = tmp_path / "episode_no_cam"
    (ep / "states").mkdir(parents=True)
    t0 = 3000.0
    manifest = {
        "site": "test",
        "episode_index": 1,
        "t_start": t0,
        "t_end": t0 + 0.1,
        "written": 3,
        "dropped": 0,
        "valid": True,
        "agents": [{"agent_id": "gello", "kind": "gello", "hz_target": 50.0}],
    }
    (ep / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with (ep / "states" / "gello.jsonl").open("w", encoding="utf-8") as f:
        for i in range(3):
            f.write(
                json.dumps(
                    {
                        "agent_id": "gello",
                        "sensor_id": "gello-main",
                        "kind": "gello",
                        "seq": i,
                        "t_wall": t0 + i * 0.02,
                        "t_mono": 1.0 + i * 0.02,
                        "payload": {"joints_rad": [0.1, 0.2, 0.3]},
                    }
                )
                + "\n"
            )
    return ep


def test_missing_primary_camera_falls_back(tmp_path: Path) -> None:
    ep = _gello_only_episode(tmp_path)
    with pytest.warns(UserWarning, match="missing_primary_camera"):
        meta = export_episode_timeline(
            ep,
            align="asof",
            master="gello",
            align_clock="hw_ts",
            primary_camera="cam-middle",
            fmt="csv",
        )
    assert meta["align_fallback"] is True
    assert meta["align_fallback_reason"] == "missing_primary_camera"
    assert meta["align_clock"] == "wall"
    assert meta["align"]["master"] == "gello"
    assert meta["rows"]["aligned"] == 3


def test_hw_coverage_low_falls_back(tmp_path: Path) -> None:
    """Partial color_timestamp coverage below HW_COVERAGE_MIN → wall."""
    ep = tmp_path / "episode_low_cov"
    (ep / "states").mkdir(parents=True)
    cam_dir = ep / "cameras" / "cam-middle"
    cam_dir.mkdir(parents=True)
    t0 = 4000.0
    manifest = {
        "site": "test",
        "episode_index": 2,
        "t_start": t0,
        "t_end": t0 + 1.0,
        "written": 20,
        "dropped": 0,
        "valid": True,
        "agents": [
            {"agent_id": "gello", "kind": "gello", "hz_target": 50.0},
            {"agent_id": "cam-middle", "kind": "realsense", "hz_target": 30.0},
        ],
    }
    (ep / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with (ep / "states" / "gello.jsonl").open("w", encoding="utf-8") as f:
        for i in range(5):
            f.write(
                json.dumps(
                    {
                        "agent_id": "gello",
                        "sensor_id": "gello-main",
                        "kind": "gello",
                        "seq": i,
                        "t_wall": t0 + i * 0.02,
                        "t_mono": 1.0 + i * 0.02,
                        "payload": {"joints_rad": [0.0, 0.0, 0.0]},
                    }
                )
                + "\n"
            )
    # 10 camera frames; only first 5 have HW → coverage 0.5 < 0.95
    with (cam_dir / "index.jsonl").open("w", encoding="utf-8") as f:
        for i in range(10):
            row = {
                "agent_id": "cam-middle",
                "sensor_id": "rs-middle",
                "kind": "realsense",
                "seq": i,
                "t_wall": t0 + i * 0.033,
                "t_mono": t0 + i * 0.033,
                "file": f"{i:08d}.jpg",
                "role": "middle",
            }
            if i < 5:
                row["color_timestamp"] = (t0 + i * 0.033) * 1000.0
                row["color_timestamp_domain"] = "timestamp_domain.system_time"
            f.write(json.dumps(row) + "\n")
            (cam_dir / f"{i:08d}.jpg").write_bytes(b"x")

    _manifest, samples = load_episode(ep)
    anchors, info = collect_primary_camera_anchors(
        samples, primary_camera="cam-middle"
    )
    assert anchors == []
    assert info["align_fallback"] is True
    assert info["align_fallback_reason"] == "hw_coverage_low"
    assert info["hw_coverage"] == pytest.approx(0.5)

    with pytest.warns(UserWarning, match="hw_coverage_low"):
        meta = export_episode_timeline(
            ep,
            align="asof",
            master="gello",
            align_clock="hw_ts",
            primary_camera="cam-middle",
            fmt="csv",
        )
    assert meta["align_fallback"] is True
    assert meta["align_fallback_reason"] == "hw_coverage_low"
    assert meta["align_clock"] == "wall"
    assert meta["rows"]["aligned"] == 5  # gello master wall path


def test_hw_grid_mode_unsupported_falls_back(tmp_path: Path) -> None:
    ep = _episode_with_hw(tmp_path, with_hw=True)
    with pytest.warns(UserWarning, match="hw_grid_mode_unsupported"):
        meta = export_episode_timeline(
            ep,
            align="grid",
            hz=10.0,
            align_clock="hw_ts",
            primary_camera="cam-middle",
            fmt="csv",
        )
    assert meta["align_fallback"] is True
    assert meta["align_fallback_reason"] == "hw_grid_mode_unsupported"
    assert meta["align_clock"] == "wall"
