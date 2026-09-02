from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sensors_dcs.export.filter import (
    apply_trim_indices,
    compute_row_mask,
    filter_episode_timeline,
    parse_max_match_dt,
)
from sensors_dcs.export.timeline import export_episode_timeline


@pytest.fixture
def episode_with_aligned(tmp_path: Path) -> Path:
    ep = tmp_path / "episode_00000"
    (ep / "states").mkdir(parents=True)
    (ep / "cameras" / "camera").mkdir(parents=True)
    manifest = {
        "t_start": 1000.0,
        "t_end": 1000.1,
        "agents": [{"agent_id": "gello", "kind": "gello", "hz_target": 50.0}],
    }
    (ep / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    gello = {
        "agent_id": "gello",
        "sensor_id": "g",
        "kind": "gello",
        "seq": 1,
        "t_wall": 1000.05,
        "payload": {"joints_rad": [0.1]},
    }
    (ep / "states" / "gello.jsonl").write_text(json.dumps(gello) + "\n", encoding="utf-8")
    export_episode_timeline(
        ep,
        align="asof",
        master="gello",
        fmt="parquet",
    )
    return ep


def test_parse_max_match_dt_per_agent() -> None:
    default, per = parse_max_match_dt("cam-left:0.05,gello:0.02")
    assert default == 0.033
    assert per["cam-left"] == 0.05
    assert per["gello"] == 0.02


def test_trim_indices_both() -> None:
    mask = [False, False, True, True, False, True, False]
    start, end, ts, te = apply_trim_indices(mask, "both")
    assert start == 2
    assert end == 5
    assert ts == 2
    assert te == 1


def test_filter_drops_warmup_and_reindexes(episode_with_aligned: Path) -> None:
    aligned_path = episode_with_aligned / "export" / "timeline_aligned.parquet"
    df = pd.read_parquet(aligned_path)
    # simulate warmup: first row camera missing
    if "camera.image_relpath" in df.columns:
        df.loc[0, "camera.image_relpath"] = None
        df.loc[0, "camera.match_dt"] = None
    df.to_parquet(aligned_path, index=False)

    meta = filter_episode_timeline(
        episode_with_aligned,
        require="gello,camera" if "camera.match_dt" in df.columns else "gello",
        trim="both",
        max_match_dt="0.05",
    )
    out = pd.read_parquet(episode_with_aligned / "export" / "timeline_filtered.parquet")
    assert meta["rows_out"] == len(out)
    if len(out):
        assert list(out["step"]) == list(range(len(out)))


def test_filter_meta_written(episode_with_aligned: Path) -> None:
    filter_episode_timeline(episode_with_aligned, require="gello", trim="none")
    meta_path = episode_with_aligned / "export" / "filter_meta.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "rows_in" in meta
    assert "tail_after_master" in meta
