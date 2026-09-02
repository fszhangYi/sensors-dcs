from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from sensors_dcs.export.hik_dataset import (
    build_steps,
    export_hik_dataset,
    load_camera_map_yaml,
    resolve_hik_camera_name,
    load_aligned_from_filtered,
    _AlignedStep,
)


def _write_map(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "cameras": {
                    "317222074437": "rear_left_1",
                    "336222075436": "top",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_filtered(ep: Path, *, n: int = 3, with_serial: bool = True) -> Path:
    filtered = ep / "export" / "filtered"
    (filtered / "states").mkdir(parents=True)
    (filtered / "cameras" / "cam-left").mkdir(parents=True)
    (filtered / "cameras" / "cam-middle").mkdir(parents=True)
    (ep / "cameras" / "cam-left").mkdir(parents=True, exist_ok=True)
    (ep / "cameras" / "cam-middle").mkdir(parents=True, exist_ok=True)

    manifest = {
        "site": "test",
        "episode_index": 0,
        "agents": [
            {"agent_id": "arm", "kind": "arm_read", "hz_target": 50.0},
            {"agent_id": "gripper-read", "kind": "gripper_read", "hz_target": 50.0},
            {"agent_id": "cam-left", "kind": "realsense", "hz_target": 15.0},
            {"agent_id": "cam-middle", "kind": "realsense", "hz_target": 15.0},
        ],
        "format": "dcs_episode_v1",
        "rows": n,
    }
    (filtered / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with (filtered / "states" / "arm.jsonl").open("w", encoding="utf-8") as fa, (
        filtered / "states" / "gripper-read.jsonl"
    ).open("w", encoding="utf-8") as fg:
        for i in range(n):
            fa.write(
                json.dumps(
                    {
                        "agent_id": "arm",
                        "kind": "arm_read",
                        "seq": i,
                        "t_wall": 1000.0 + i * 0.2,
                        "payload": {"joints_rad": [0.1 * i, 0.2, 0.3, 0.4, 0.5, 0.6]},
                    }
                )
                + "\n"
            )
            fg.write(
                json.dumps(
                    {
                        "agent_id": "gripper-read",
                        "kind": "gripper_read",
                        "seq": i,
                        "t_wall": 1000.0 + i * 0.2,
                        "payload": {"position_norm": 0.1 * i},
                    }
                )
                + "\n"
            )

    cams = (
        ("cam-left", "left", "317222074437"),
        ("cam-middle", "middle", "336222075436"),
    )
    for aid, role, serial in cams:
        with (filtered / "cameras" / aid / "index.jsonl").open("w", encoding="utf-8") as fc:
            for i in range(n):
                name = f"{i:08d}.jpg"
                (filtered / "cameras" / aid / name).write_bytes(b"JPEG" + bytes([i]))
                rec = {
                    "agent_id": aid,
                    "kind": "realsense",
                    "seq": i,
                    "t_wall": 1000.0 + i * 0.2,
                    "file": name,
                    "role": role,
                }
                if with_serial:
                    rec["serial"] = serial
                fc.write(json.dumps(rec) + "\n")
        (ep / "cameras" / aid / "index.jsonl").write_text(
            json.dumps(
                {
                    "agent_id": aid,
                    "kind": "realsense",
                    "seq": 0,
                    "t_wall": 1000.0,
                    "file": "00000000.jpg",
                    "role": role,
                    "serial": serial,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return filtered


def test_load_camera_map_yaml(tmp_path: Path) -> None:
    p = _write_map(tmp_path / "map.yaml")
    m = load_camera_map_yaml(p)
    assert m["317222074437"] == "rear_left_1"
    assert m["336222075436"] == "top"


def test_resolve_requires_serial_in_map() -> None:
    m = {"336222075436": "top", "317222074437": "rear_left_1"}
    assert (
        resolve_hik_camera_name(agent_id="cam-x", serial="336222075436", name_map=m)
        == "top"
    )
    with pytest.raises(ValueError, match="not in camera map"):
        resolve_hik_camera_name(agent_id="cam-x", serial="000000000000", name_map=m)
    with pytest.raises(ValueError, match="has no serial"):
        resolve_hik_camera_name(agent_id="cam-x", serial=None, name_map=m)


def test_build_steps_without_fk() -> None:
    aligned = [
        _AlignedStep(0, 1.0, [0.1] * 6, 0.0, {}),
        _AlignedStep(1, 1.2, [0.2] * 6, 0.5, {}),
    ]
    steps = build_steps(aligned, fk=None)
    assert steps["observations"]["gripper_position"][0] == [0.0, 0.5]


def test_build_steps_with_fk() -> None:
    def fk(joints: list[float]) -> np.ndarray:
        m = np.eye(4)
        m[0, 3] = float(joints[0])
        return m

    aligned = [
        _AlignedStep(0, 1.0, [0.0] * 6, 0.0, {}),
        _AlignedStep(1, 1.2, [0.1, 0, 0, 0, 0, 0], 1.0, {}),
    ]
    steps = build_steps(aligned, fk=fk, tcp_xyz=(0.0, 0.0, 0.0))
    assert abs(steps["actions"]["cartesian_position"][0][0] - 0.1) < 1e-9


def test_export_requires_camera_map(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    _write_filtered(ep, n=2)
    with pytest.raises(ValueError, match="camera serial"):
        export_hik_dataset(ep)


def test_export_hik_dataset_uses_yaml_map(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    _write_filtered(ep, n=3)
    cmap = _write_map(tmp_path / "hik_camera_map.yaml")

    meta = export_hik_dataset(ep, camera_map_yaml=cmap, robot_name="elite")
    out = Path(meta["out_dir"])
    assert (out / "rgb_rear_left_1_0.jpg").is_file()
    assert (out / "rgb_top_2.jpg").is_file()
    assert (out / "camera_map.yaml").is_file()
    assert (ep / "export" / "filtered" / "camera_map.yaml").is_file()

    md = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert "rgb_top" in md["sensor_list"]


def test_export_reuses_bundled_map(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    _write_filtered(ep, n=2)
    cmap = _write_map(tmp_path / "hik_camera_map.yaml")
    export_hik_dataset(ep, camera_map_yaml=cmap)
    # second call without --camera-map uses filtered/camera_map.yaml
    meta = export_hik_dataset(ep, output_dir="export/hik_dataset2")
    assert Path(meta["out_dir"]).is_dir()


def test_serial_fallback_from_source_episode(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    filtered = _write_filtered(ep, n=2, with_serial=False)
    m = load_camera_map_yaml(_write_map(tmp_path / "m.yaml"))
    _, aligned, hik_to_agent = load_aligned_from_filtered(
        filtered, name_map=m, source_episode=ep
    )
    assert "rear_left_1" in hik_to_agent
    assert "top" in aligned[0].cameras
