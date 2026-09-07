"""Tests for scripts/select_baseline_episodes.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select_baseline_episodes.py"


def _load_mod():
    import sys

    spec = importlib.util.spec_from_file_location("select_baseline_episodes", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses + from __future__ annotations needs the module registered first
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _make_episode(
    root: Path,
    name: str,
    *,
    valid: bool = True,
    duration_s: float = 40.0,
    n_cam: int = 30,
    cam_dt: float = 1 / 15,
    gap_every: int | None = None,
    hw_ts: bool = True,
    state_offset: float = 0.005,
    dropped: int = 0,
    multi_cam: bool = True,
) -> Path:
    ep = root / name
    (ep / "cameras" / "cam-middle").mkdir(parents=True)
    if multi_cam:
        (ep / "cameras" / "cam-left").mkdir(parents=True)
    (ep / "states").mkdir(parents=True)

    t0 = 1_700_000_000.0
    cam_rows = []
    for i in range(n_cam):
        tw = t0 + i * cam_dt
        if gap_every and i > 0 and i % gap_every == 0:
            tw += cam_dt * 5  # inject gap
            # rebase subsequent? keep simple absolute
        fname = f"{i:08d}.jpg"
        (ep / "cameras" / "cam-middle" / fname).write_bytes(b"x")
        row = {
            "agent_id": "cam-middle",
            "kind": "realsense",
            "seq": i,
            "t_wall": tw,
            "t_mono": float(i) * cam_dt,
            "file": fname,
        }
        if hw_ts:
            row["color_timestamp"] = tw * 1000.0
            row["color_timestamp_domain"] = "global_time"
        cam_rows.append(row)
    # Fix gap injection properly: rebuild times monotonically with occasional long dt
    if gap_every:
        cam_rows = []
        t = t0
        for i in range(n_cam):
            if i > 0:
                t += cam_dt * (6 if i % gap_every == 0 else 1)
            else:
                t = t0
            fname = f"{i:08d}.jpg"
            (ep / "cameras" / "cam-middle" / fname).write_bytes(b"x")
            row = {
                "agent_id": "cam-middle",
                "kind": "realsense",
                "seq": i,
                "t_wall": t,
                "t_mono": t - t0,
                "file": fname,
            }
            if hw_ts:
                row["color_timestamp"] = t * 1000.0
            cam_rows.append(row)

    _write_jsonl(ep / "cameras" / "cam-middle" / "index.jsonl", cam_rows)
    if multi_cam:
        left = []
        for i, r in enumerate(cam_rows):
            fname = f"{i:08d}.jpg"
            (ep / "cameras" / "cam-left" / fname).write_bytes(b"x")
            lr = dict(r)
            lr["agent_id"] = "cam-left"
            lr["file"] = fname
            left.append(lr)
        _write_jsonl(ep / "cameras" / "cam-left" / "index.jsonl", left)

    state_rows = []
    for i, r in enumerate(cam_rows):
        # ~3 state samples per camera frame
        for k in range(3):
            state_rows.append(
                {
                    "agent_id": "gello",
                    "kind": "gello",
                    "seq": i * 3 + k,
                    "t_wall": float(r["t_wall"]) + state_offset + k * 0.001,
                    "t_mono": float(r["t_mono"]) + state_offset,
                    "payload": {"joints_rad": [0.1] * 7},
                }
            )
    _write_jsonl(ep / "states" / "gello.jsonl", state_rows)

    agents = [
        {"agent_id": "cam-middle", "sensor_id": "rs0", "kind": "realsense", "hz_target": 15},
        {"agent_id": "gello", "sensor_id": "gello0", "kind": "gello", "hz_target": 50},
    ]
    if multi_cam:
        agents.insert(1, {"agent_id": "cam-left", "sensor_id": "rs1", "kind": "realsense", "hz_target": 15})

    man = {
        "site": "lab",
        "episode_index": int(name.split("_")[-1]),
        "record_mode": "collect",
        "t_start": t0,
        "t_end": t0 + duration_s,
        "duration_s": duration_s,
        "written": n_cam * 4,
        "dropped": dropped,
        "agents": agents,
        "cameras": {"cam-middle": {}},
        "valid": valid,
        "format": "dcs_episode_v1",
    }
    (ep / "manifest.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return ep


@pytest.fixture(scope="module")
def mod():
    return _load_mod()


def test_grades_and_readme(tmp_path: Path, mod) -> None:
    data = tmp_path / "recs"
    data.mkdir()
    _make_episode(data, "episode_00001", hw_ts=True, gap_every=None, state_offset=0.003)
    _make_episode(
        data,
        "episode_00002",
        hw_ts=True,
        gap_every=8,
        state_offset=0.040,
        duration_s=35.0,
    )
    _make_episode(
        data,
        "episode_00003",
        hw_ts=False,
        gap_every=4,
        state_offset=0.200,
        valid=False,
        duration_s=20.0,
        dropped=50,
    )

    out = tmp_path / "baseline"
    result = mod.run(
        data_root=data,
        out_baseline=out,
        link_mode="symlink",
        dry_run=False,
    )
    assert result["n_episodes"] == 3
    assert (out / "README.md").is_file()
    assert (out / "ep_good").is_symlink()
    assert (out / "ep_mid").is_symlink()
    assert (out / "ep_bad").is_symlink()
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "ep_good" in readme and "候选池排名" in readme
    assert result["chosen"]["good"]["name"] == "episode_00001"
    assert result["chosen"]["bad"]["name"] == "episode_00003"


def test_dry_run_prints_without_links(tmp_path: Path, mod) -> None:
    data = tmp_path / "recs"
    data.mkdir()
    _make_episode(data, "episode_00000")
    out = tmp_path / "baseline"
    result = mod.run(data_root=data, out_baseline=out, dry_run=True)
    assert not out.exists()
    assert "Baseline episodes" in result["readme"]
