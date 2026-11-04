"""Thick M2 DCS adapter (sensors.traj_safety)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from sensors_dcs.export.traj_safety_thick import run_traj_safety_thick


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_thick_writes_sidecars(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00099"
    _write_json(ep / "manifest.json", {"format": "dcs_episode_v1", "valid": True})
    q_deg = [46.889, -62.737, 120.028, -150.651, 90.667, 40.356]
    q = [math.radians(x) for x in q_deg]
    rows = []
    for i in range(4):
        rows.append(
            {
                "t_wall": i * 0.1,
                "agent_id": "arm-cs66",
                "payload": {"q": q, "cartesian_xyzrpy": [0.0, 0.0, 0.3, 0, 0, 0]},
            }
        )
    states = ep / "states" / "arm-cs66.jsonl"
    states.parent.mkdir(parents=True, exist_ok=True)
    states.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )

    out = run_traj_safety_thick(ep, write=True, also_thin=True, profile_id="elite_cs66")
    assert out["ok"] is True
    assert out["kind"] == "thick"
    assert (ep / "export" / "quality" / "traj_quality.json").is_file()
    assert (ep / "export" / "quality" / "near_miss.json").is_file()
    assert out["grade"] in {"good", "warn", "bad"}
