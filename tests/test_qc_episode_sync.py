"""Tests for scripts/qc_episode_sync.py (W1 D4 stable JSON fields)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "qc_episode_sync.py"

# Frozen for W2 wall-vs-hw compare — rename = break contract.
REQUIRED_KEYS = (
    "cam_dt_p50",
    "cam_dt_p95",
    "cam_gap_count",
    "state_cam_abs_dt_p50",
    "state_cam_abs_dt_p95",
    "missing_agents",
    "hw_ts_present",
    "hw_ts_coverage",
    "align_clock",
    "schema",
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("qc_episode_sync", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _make_episode(tmp: Path) -> Path:
    ep = tmp / "episode_qc_demo"
    (ep / "cameras" / "cam-middle").mkdir(parents=True)
    (ep / "states").mkdir(parents=True)
    t0 = 1000.0
    dt = 1 / 30
    cam_rows = []
    for i in range(60):
        # one intentional gap that survives HW sort/dedup: shift all later frames
        tw = t0 + i * dt + (0.2 if i >= 30 else 0.0)
        cam_rows.append(
            {
                "agent_id": "cam-middle",
                "t_wall": tw,
                "file": f"{i:08d}.jpg",
                # ms HW stamps coherent with wall (RealSense-like)
                "color_timestamp": tw * 1000.0 + 3.0,
                "color_timestamp_domain": "timestamp_domain.system_time",
            }
        )
        (ep / "cameras" / "cam-middle" / f"{i:08d}.jpg").write_bytes(b"x")
    _write_jsonl(ep / "cameras" / "cam-middle" / "index.jsonl", cam_rows)

    state_rows = []
    for i in range(100):
        state_rows.append(
            {
                "agent_id": "gello",
                "t_wall": t0 + i * 0.02 + 0.005,
                "payload": {},
            }
        )
    _write_jsonl(ep / "states" / "gello.jsonl", state_rows)

    man = {
        "valid": True,
        "written": 200,
        "dropped": 10,
        "duration_s": 2.0,
        "agents": [
            {"agent_id": "cam-middle", "kind": "realsense"},
            {"agent_id": "gello", "kind": "gello"},
            {"agent_id": "cam-ghost", "kind": "realsense"},  # missing
        ],
    }
    (ep / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return ep


def test_analyze_stable_fields(tmp_path: Path):
    mod = _load_mod()
    ep = _make_episode(tmp_path)
    r = mod.analyze_episode(ep, camera_primary="cam-middle", gap_factor=2.5)
    for k in REQUIRED_KEYS:
        assert k in r, k
    assert r["align_clock"] == "wall"
    assert r["schema"] == "qc_episode_sync.v1"
    assert r["hw_ts_present"] is True
    assert r["hw_ts_coverage"] == 1.0
    assert r["cam_dt_p50"] is not None
    assert r["cam_gap_count"] >= 1
    assert r["state_cam_abs_dt_p95"] is not None
    assert "cam-ghost" in r["missing_agents"]
    assert r["error"] is None


def test_cli_writes_json(tmp_path: Path):
    mod = _load_mod()
    ep = _make_episode(tmp_path)
    out = tmp_path / "sync_wall_demo.json"
    rc = mod.main(["--episode", str(ep), "--camera-primary", "cam-middle", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    for k in REQUIRED_KEYS:
        assert k in data


def test_analyze_hw_ts_mvp(tmp_path: Path):
    mod = _load_mod()
    ep = _make_episode(tmp_path)
    r = mod.analyze_episode(
        ep, camera_primary="cam-middle", align_clock="hw_ts", gap_factor=2.5
    )
    assert r["align_clock"] == "hw_ts"
    assert r["align_fallback"] is False
    assert r["mvp_limitation"]
    assert r["cam_dt_p50"] is not None
    assert r["state_cam_abs_dt_p95"] is not None
    # HW grid should still see the injected gap.
    assert r["cam_gap_count"] >= 1


def test_analyze_hw_ts_fallback_without_timestamps(tmp_path: Path):
    mod = _load_mod()
    ep = _make_episode(tmp_path)
    # Strip HW fields
    idx = ep / "cameras" / "cam-middle" / "index.jsonl"
    rows = []
    for line in idx.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row.pop("color_timestamp", None)
        row.pop("color_timestamp_domain", None)
        rows.append(row)
    idx.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    r = mod.analyze_episode(ep, camera_primary="cam-middle", align_clock="hw_ts")
    assert r["align_fallback"] is True
    assert r["align_clock"] == "wall"
    assert r["align_clock_requested"] == "hw_ts"
