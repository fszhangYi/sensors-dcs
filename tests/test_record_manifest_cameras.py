from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from sensors_dcs.config import RecordConfig
from sensors_dcs.record import RecordController


class _FakeRsAgent:
    kind = "realsense"
    agent_id = "cam-left"
    sensor_id = "rs-left"
    hz = 15.0

    def camera_infos_dict(self) -> dict[str, Any]:
        return {
            "serial": "336222075436",
            "role": "middle",
            "intrinsic_matrix": [[1.0, 0.0, 2.0], [0.0, 3.0, 4.0], [0.0, 0.0, 1.0]],
            "color_intrinsics": [1.0, 3.0, 2.0, 4.0],
        }


def test_write_manifest_includes_cameras(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8)
    rc = RecordController(cfg, agents={"cam-left": _FakeRsAgent()}, site="lab")  # type: ignore[arg-type]
    ep_dir = tmp_path / "episode_00000"
    ep_dir.mkdir()
    man = rc._write_manifest(
        ep_dir,
        ep=0,
        t_start=1.0,
        t_end=None,
        written=0,
        dropped=0,
        provisional=True,
    )
    assert man["provisional"] is True
    assert man["cameras"]["cam-left"]["serial"] == "336222075436"
    assert man["cameras"]["cam-left"]["intrinsic_matrix"][0][0] == 1.0
    disk = json.loads((ep_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "cameras" in disk

    # final write merges stats and keeps cameras even if agent returns None later
    rc.agents["cam-left"].camera_infos_dict = MagicMock(return_value=None)  # type: ignore[method-assign]
    man2 = rc._write_manifest(
        ep_dir,
        ep=0,
        t_start=1.0,
        t_end=2.5,
        written=10,
        dropped=1,
        provisional=False,
    )
    assert man2["valid"] is True
    assert man2["written"] == 10
    assert man2["cameras"]["cam-left"]["intrinsic_matrix"][1][1] == 3.0
    assert "provisional" not in man2

    man3 = rc._write_manifest(
        ep_dir,
        ep=0,
        t_start=1.0,
        t_end=2.5,
        written=10,
        dropped=1,
        provisional=False,
        valid=False,
    )
    assert man3["valid"] is False
    assert "作废" in (man3.get("note") or "") or man3.get("valid") is False
    assert json.loads((ep_dir / "manifest.json").read_text(encoding="utf-8"))["valid"] is False


def test_provisional_manifest_valid_false(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8)
    rc = RecordController(cfg, agents={}, site="lab")
    ep_dir = tmp_path / "episode_00000"
    ep_dir.mkdir()
    man = rc._write_manifest(
        ep_dir,
        ep=0,
        t_start=1.0,
        t_end=None,
        written=0,
        dropped=0,
        provisional=True,
        valid=True,  # ignored while provisional
    )
    assert man["valid"] is False
    assert man["provisional"] is True
