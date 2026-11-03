"""M3: sensor/timeline coverage extraction and aggregate block."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.metrics.aggregate import aggregate_metrics
from sensors_dcs.metrics.coverage import extract_episode_coverage


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_unprocessed_episode(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    _write_json(ep / "manifest.json", {"format": "dcs_episode_v1", "valid": True, "written": 100, "dropped": 5})
    row = extract_episode_coverage(ep)
    assert row["status"] == "unprocessed"
    assert row["filter_keep_rate"] is None
    assert "unprocessed" in row["alerts"]
    assert abs(row["drop_rate"] - (5 / 105)) < 1e-9


def test_filtered_with_missing_and_low_keep(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00001"
    _write_json(
        ep / "manifest.json",
        {"format": "dcs_episode_v1", "valid": True, "written": 1000, "dropped": 0},
    )
    _write_json(
        ep / "export" / "export_meta.json",
        {
            "agents": ["arm", "cam-middle"],
            "align_clock": "hw_ts",
            "hw_coverage": 1.0,
            "manifest_written": 1000,
            "manifest_dropped": 0,
            "match_dt_stats": {
                "arm.match_dt": {"missing_rate": 0.0},
                "cam-middle.match_dt": {"missing_rate": 0.0},
            },
        },
    )
    _write_json(
        ep / "export" / "filter_meta.json",
        {
            "require": ["arm", "cam-middle", "cam-left"],
            "require_unmatched_columns": ["cam-left"],
            "rows_in": 100,
            "rows_out": 40,
            "drop_reasons": {"missing_data": 60},
            "align_quality": {
                "score": 55.0,
                "grade": "fair",
                "components": {"keep_rate": 0.4, "sync": 0.5, "budget": 0.5},
                "rows_in": 100,
                "rows_out": 40,
            },
        },
    )
    row = extract_episode_coverage(ep)
    assert row["status"] == "filtered"
    assert abs(row["filter_keep_rate"] - 0.4) < 1e-9
    assert row["missing_agents"] == ["cam-left"]
    assert "low_filter_keep_rate" in row["alerts"]
    assert "missing_agents" in row["alerts"]


def test_aggregate_includes_coverage(tmp_path: Path) -> None:
    ep0 = tmp_path / "episode_00000"
    _write_json(ep0 / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    ep1 = tmp_path / "episode_00001"
    _write_json(ep1 / "manifest.json", {"format": "dcs_episode_v1", "valid": True, "written": 10, "dropped": 0})
    _write_json(
        ep1 / "export" / "filter_meta.json",
        {
            "require": ["arm"],
            "rows_in": 10,
            "rows_out": 10,
            "align_quality": {
                "score": 90.0,
                "grade": "excellent",
                "components": {"keep_rate": 1.0},
                "rows_in": 10,
                "rows_out": 10,
            },
        },
    )
    out = aggregate_metrics(tmp_path)
    assert out["ok"] is True
    cov = out["coverage"]
    assert cov["schema"] == "dcs_sensor_coverage_v1"
    assert cov["summary"]["episodes"] == 2
    assert cov["summary"]["unprocessed"] == 1
    assert cov["summary"]["filtered"] == 1
    statuses = {r["episode"]: r["status"] for r in cov["episodes"]}
    assert statuses["episode_00000"] == "unprocessed"
    assert statuses["episode_00001"] == "filtered"
