from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sensors_dcs.export.align_plot import plot_filter_alignment
from sensors_dcs.export.filter import filter_episode_timeline
from sensors_dcs.export.timeline import export_episode_timeline


pytest.importorskip("cv2")


@pytest.fixture
def episode_with_aligned(tmp_path: Path) -> Path:
    ep = tmp_path / "episode_00000"
    (ep / "states").mkdir(parents=True)
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
    export_episode_timeline(ep, align="asof", master="gello", fmt="parquet")
    return ep


def test_plot_filter_alignment_writes_png(tmp_path: Path) -> None:
    n = 80
    t = np.linspace(0.0, 2.0, n)
    df = pd.DataFrame(
        {
            "t_wall": 1000.0 + t,
            "t_rel": t,
            "cam-middle.match_dt": np.zeros(n),
            "cam-left.match_dt": np.clip(0.005 + 0.02 * np.sin(t * 4), 0, None),
            "gello.match_dt": np.clip(0.002 + 0.01 * np.cos(t * 3), 0, None),
            "cam-middle.image_relpath": [f"{i:08d}.jpg" for i in range(n)],
            "cam-left.image_relpath": [f"{i:08d}.jpg" for i in range(n)],
            "gello.j0": np.linspace(0.0, 1.0, n),
        }
    )
    mask = np.ones(n, dtype=bool)
    mask[:5] = False
    mask[-5:] = False
    mask[40] = False
    out = tmp_path / "filter_align.png"
    info = plot_filter_alignment(
        df,
        mask=mask,
        out_path=out,
        master="cam-middle",
        require=["cam-middle", "cam-left", "gello"],
        default_max_dt=0.033,
        trim_start_idx=5,
        trim_end_idx=n - 6,
        title="unit test align",
    )
    assert info["ok"] is True
    assert out.is_file()
    assert out.stat().st_size > 1000
    assert info["agents"] == ["cam-middle", "cam-left", "gello"]


def test_filter_writes_align_plot(episode_with_aligned: Path) -> None:
    meta = filter_episode_timeline(episode_with_aligned, require="gello", trim="none")
    assert meta.get("align_plot") == "export/filter_align.png"
    png = episode_with_aligned / "export" / "filter_align.png"
    assert png.is_file()
    assert meta.get("align_plot_info", {}).get("ok") is True
