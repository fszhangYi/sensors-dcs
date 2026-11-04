"""Thin M2: arm.jsonl dynamics → export/quality/traj_quality.json + aggregate."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.export.traj_quality import analyze_arm_samples, run_traj_quality
from sensors_dcs.metrics.aggregate import aggregate_metrics


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_arm_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_analyze_good_and_warn() -> None:
    good = [
        {"t": 0.0, "joints": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "xyz": [0.0, 0.0, 0.0]},
        {"t": 0.1, "joints": [0.05, 0.0, 0.0, 0.0, 0.0, 0.0], "xyz": [0.001, 0.0, 0.0]},
        {"t": 0.2, "joints": [0.10, 0.0, 0.0, 0.0, 0.0, 0.0], "xyz": [0.002, 0.0, 0.0]},
    ]
    m = analyze_arm_samples(good, omega_max_rad_s=2.0, jump_rad=0.35)
    assert m["grade"] == "good"
    assert m["overspeed_count"] == 0

    bad = [
        {"t": 0.0, "joints": [0.0] * 6, "xyz": [0.0, 0.0, 0.0]},
        {"t": 0.05, "joints": [1.0] + [0.0] * 5, "xyz": [0.5, 0.0, 0.0]},
    ]
    m2 = analyze_arm_samples(bad, omega_max_rad_s=2.0, jump_rad=0.35)
    assert m2["grade"] == "warn"
    assert m2["overspeed_count"] >= 1
    assert m2["jump_count"] >= 1


def test_run_writes_sidecar(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00042"
    _write_json(ep / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    rows = []
    for i in range(5):
        rows.append(
            {
                "t_wall": i * 0.1,
                "agent_id": "arm",
                "payload": {
                    "q": [0.02 * i, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "cartesian_xyzrpy": [0.001 * i, 0.0, 0.2, 0.0, 0.0, 0.0],
                },
            }
        )
    _write_arm_jsonl(ep / "states" / "arm.jsonl", rows)
    out = run_traj_quality(ep, write=True)
    assert out["ok"] is True
    assert out["grade"] == "good"
    side = ep / "export" / "quality" / "traj_quality.json"
    assert side.is_file()
    loaded = json.loads(side.read_text(encoding="utf-8"))
    assert loaded["schema"] == "dcs_traj_quality_v1"
    assert loaded["kind"] == "thin"


def test_missing_arm_fails(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    _write_json(ep / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    out = run_traj_quality(ep, write=True)
    assert out["ok"] is False
    assert out["grade"] == "missing"


def test_aggregate_quality_block(tmp_path: Path) -> None:
    ep0 = tmp_path / "episode_00000"
    _write_json(ep0 / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    ep1 = tmp_path / "episode_00001"
    _write_json(ep1 / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    _write_json(
        ep1 / "export" / "quality" / "traj_quality.json",
        {
            "schema": "dcs_traj_quality_v1",
            "kind": "thin",
            "grade": "warn",
            "overspeed_count": 3,
            "jump_count": 1,
            "tcp_overspeed_count": 0,
            "agent_id": "arm",
        },
    )
    out = aggregate_metrics(tmp_path)
    assert out["ok"] is True
    q = out["quality"]
    assert q["schema"] == "dcs_traj_quality_summary_v1"
    assert q["summary"]["with_traj_quality"] == 1
    assert q["summary"]["missing"] == 1
    assert q["summary"]["by_grade"]["warn"] == 1
    assert q["summary"]["overspeed_episodes"] == 1
