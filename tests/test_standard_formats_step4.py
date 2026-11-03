"""Step 4: timeline_filtered + raw cameras (no export/filtered)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sensors_dcs.export.standard_formats import (
    check_step2_artifacts,
    export_standard_episode,
    export_standard_root,
    list_standard_formats,
    normalize_formats,
)


def _write_jpeg(path: Path, *, color: tuple[int, int, int] = (40, 80, 120)) -> None:
    import cv2

    img = np.zeros((48, 64, 3), dtype=np.uint8)
    img[:] = color
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.tobytes())


def _make_episode_with_step2_table(root: Path, name: str = "episode_00009") -> Path:
    ep = root / name
    serial = "111122223333"
    agents = [
        {"agent_id": "arm", "kind": "arm_read"},
        {"agent_id": "cam-left", "kind": "realsense", "role": "left"},
        {"agent_id": "gripper-read", "kind": "gripper_read"},
    ]
    manifest = {
        "format": "dcs_episode_v1",
        "valid": True,
        "episode_index": int(name.rsplit("_", 1)[-1]),
        "agents": agents,
        "tags": {"task_id": "pick_cube"},
        "task_outcome": {"outcome": "success"},
        "cameras": {"cam-left": {"serial": serial}},
    }
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ep / "cameras" / "cam-left").mkdir(parents=True, exist_ok=True)

    from sensors_dcs.export.parquet_io import require_pandas

    pd = require_pandas()
    rows = []
    t0 = 1000.0
    for i in range(3):
        fname = f"{i:08d}.jpg"
        rel = f"cameras/cam-left/{fname}"
        _write_jpeg(ep / "cameras" / "cam-left" / fname)
        rows.append(
            {
                "step": i,
                "t_wall": t0 + i * 0.2,
                "t_rel": i * 0.2,
                "arm.seq": i,
                "arm.j0": 0.1 * i,
                "arm.j1": 0.0,
                "arm.j2": 0.0,
                "arm.j3": 0.0,
                "arm.j4": 0.0,
                "arm.j5": 0.0,
                "arm.match_dt": 0.0,
                "gripper-read.seq": i,
                "gripper-read.position_norm": 0.1 * i,
                "gripper-read.match_dt": 0.0,
                "cam-left.seq": i,
                "cam-left.file": fname,
                "cam-left.image_relpath": rel,
                "cam-left.role": "left",
                "cam-left.serial": serial,
                "cam-left.file_missing": False,
                "cam-left.match_dt": 0.0,
            }
        )
    out = ep / "export" / "timeline_filtered.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out, index=False)
    (ep / "export" / "filter_meta.json").write_text(
        json.dumps({"rows_out": 3, "materialize": False}, indent=2) + "\n",
        encoding="utf-8",
    )
    (ep / "export" / "hik_camera_map.yaml").write_text(
        f'cameras:\n  "{serial}": front\n', encoding="utf-8"
    )
    # Ensure no filtered tree is required / present.
    assert not (ep / "export" / "filtered").exists()
    return ep


def test_normalize_and_list_formats() -> None:
    assert normalize_formats("lerobot,hdf5") == ["lerobot_v21", "hdf5"]
    assert {f["id"] for f in list_standard_formats()} >= {
        "lerobot_v21",
        "lerobot_v30",
        "hdf5",
    }


def test_check_step2_is_timeline_only(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00001"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps({"valid": True, "episode_index": 1}) + "\n", encoding="utf-8"
    )
    (ep / "export").mkdir()
    chk = check_step2_artifacts(ep)
    assert chk["ok"] is False
    assert "filtered_dir" not in chk
    assert any("timeline_filtered" in r for r in chk["reasons"])

    import pandas as pd

    pd.DataFrame({"step": [], "t_wall": []}).to_parquet(
        ep / "export" / "timeline_filtered.parquet", index=False
    )
    chk2 = check_step2_artifacts(ep)
    assert chk2["ok"] is False
    assert chk2["rows"] == 0


def test_export_from_timeline_without_filtered_dir(tmp_path: Path) -> None:
    ep = _make_episode_with_step2_table(tmp_path)
    assert not (ep / "export" / "filtered").exists()
    out = export_standard_episode(
        ep,
        formats=["lerobot_v21"],
        camera_map_yaml=ep / "export" / "hik_camera_map.yaml",
    )
    assert out["ok"] is True, out
    assert "filtered" not in (out.get("log") or "").lower() or "filtered_dir" not in out
    info = json.loads(
        (ep / "export" / "lerobot_v21" / "meta" / "info.json").read_text(encoding="utf-8")
    )
    assert info["total_frames"] == 3
    assert info["source_format"] == "dcs_timeline_filtered_v1"


def test_export_force_overwrite(tmp_path: Path) -> None:
    ep = _make_episode_with_step2_table(tmp_path, "episode_00020")
    cam_map = ep / "export" / "hik_camera_map.yaml"
    assert export_standard_episode(ep, formats=["lerobot_v21"], camera_map_yaml=cam_map)["ok"]
    marker = ep / "export" / "lerobot_v21" / "_probe.txt"
    marker.write_text("old", encoding="utf-8")
    assert export_standard_episode(ep, formats=["lerobot_v21"], camera_map_yaml=cam_map)["ok"]
    assert not marker.exists()


def test_export_standard_root_batch(tmp_path: Path) -> None:
    _make_episode_with_step2_table(tmp_path, "episode_00009")
    _make_episode_with_step2_table(tmp_path, "episode_00010")
    bad = tmp_path / "episode_00011"
    bad.mkdir()
    (bad / "manifest.json").write_text(
        json.dumps({"valid": True, "episode_index": 11}) + "\n", encoding="utf-8"
    )
    (bad / "export").mkdir()
    cam_map = tmp_path / "episode_00009" / "export" / "hik_camera_map.yaml"
    result = export_standard_root(
        tmp_path, formats=["lerobot_v21"], camera_map_yaml=cam_map
    )
    assert result["ok"] is False
    assert result["ok_count"] == 2
    assert result["failed_count"] == 1


def test_export_hdf5_optional(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    ep = _make_episode_with_step2_table(tmp_path, "episode_00012")
    out = export_standard_episode(
        ep,
        formats=["hdf5"],
        camera_map_yaml=ep / "export" / "hik_camera_map.yaml",
    )
    assert out["ok"] is True, out
    assert (ep / "export" / "standard_hdf5" / "episode.hdf5").is_file()
