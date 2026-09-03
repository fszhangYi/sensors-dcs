"""Tests for RealSense depth PNG encode and episode write."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np

from sensors_dcs.agents.realsense_agent import _depth_png_b64
from sensors_dcs.config import RecordConfig
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController


def test_depth_png_roundtrip() -> None:
    depth = np.arange(12, dtype=np.uint16).reshape(3, 4)
    b64 = _depth_png_b64(depth)
    raw = base64.b64decode(b64)
    import cv2

    arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert arr is not None
    assert arr.dtype == np.uint16
    assert arr.shape == (3, 4)
    assert int(arr[0, 1]) == 1


def test_record_writes_depth_png(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8)
    rc = RecordController(cfg, agents={}, site="lab")
    ep = tmp_path / "episode_00000"
    ep.mkdir()

    depth = np.full((4, 4), 1000, dtype=np.uint16)
    depth_b64 = _depth_png_b64(depth)
    # Minimal valid JPEG via numpy zeros encoded through jpeg helper
    from sensors_dcs.agents.realsense_agent import _jpeg_b64

    color = np.zeros((4, 4, 3), dtype=np.uint8)
    jpeg_b64 = _jpeg_b64(color, quality=80, max_width=None)

    fr = Frame(
        sensor_id="rs",
        agent_id="cam",
        kind="realsense",
        t_wall=1.0,
        t_mono=1.0,
        seq=7,
        payload={
            "jpeg_b64": jpeg_b64,
            "enable_depth": True,
            "depth_png_b64": depth_b64,
            "depth_shape": [4, 4],
            "serial": "s1",
            "role": "middle",
            "color_timestamp": 12345.6,
            "depth_timestamp": 12346.0,
            "color_timestamp_domain": "timestamp_domain.hardware_clock",
            "depth_timestamp_domain": "timestamp_domain.hardware_clock",
        },
    )

    cam_fps: dict[str, object] = {}

    def state_fp(_aid: str):
        raise AssertionError("state not expected")

    def cam_paths(aid: str):
        d = ep / "cameras" / aid
        d.mkdir(parents=True, exist_ok=True)
        if aid not in cam_fps:
            cam_fps[aid] = (d / "index.jsonl").open("a", encoding="utf-8")
        return d, cam_fps[aid]

    try:
        rc._write_frame(fr, state_fp, cam_paths)
    finally:
        for f in cam_fps.values():
            f.close()  # type: ignore[union-attr]

    jpg = ep / "cameras" / "cam" / "00000007.jpg"
    png = ep / "cameras" / "cam" / "00000007_depth.png"
    assert jpg.is_file()
    assert png.is_file()
    idx = json.loads((ep / "cameras" / "cam" / "index.jsonl").read_text(encoding="utf-8").strip())
    assert idx["file"] == "00000007.jpg"
    assert idx["depth_file"] == "00000007_depth.png"
    assert idx["enable_depth"] is True
    assert idx["color_timestamp"] == 12345.6
    assert idx["depth_timestamp"] == 12346.0
    assert idx["color_timestamp_domain"] == "timestamp_domain.hardware_clock"
    assert idx["depth_timestamp_domain"] == "timestamp_domain.hardware_clock"
