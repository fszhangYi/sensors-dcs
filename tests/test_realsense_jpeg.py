from __future__ import annotations

import base64

import numpy as np

from sensors_dcs.agents.realsense_agent import _hconcat_color_depth, _jpeg_b64


def test_jpeg_full_res_keeps_native_size() -> None:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    img[:, :, 1] = 80
    b64 = _jpeg_b64(img, quality=90, max_width=None)
    raw = base64.b64decode(b64)
    # JPEG SOFn marker has height/width at offset after marker; use cv2 if available.
    import cv2

    arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert arr is not None
    assert arr.shape[0] == 720
    assert arr.shape[1] == 1280


def test_jpeg_preview_downscales() -> None:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    b64 = _jpeg_b64(img, quality=70, max_width=427)
    raw = base64.b64decode(b64)
    import cv2

    arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert arr is not None
    assert arr.shape[1] == 427
    assert arr.shape[0] == 240  # 720 * 427/1280


def test_hconcat_color_depth_side_by_side() -> None:
    color = np.zeros((48, 64, 3), dtype=np.uint8)
    color[:, :, 2] = 200
    depth = np.zeros((48, 64, 3), dtype=np.uint8)
    depth[:, :, 0] = 200
    out = _hconcat_color_depth(color, depth)
    assert out.shape[0] == 48
    assert out.shape[1] == 128
    assert out.shape[2] == 3
