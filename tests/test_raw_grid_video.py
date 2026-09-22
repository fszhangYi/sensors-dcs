"""Raw episode camera grid video (independent of hik_dataset steps)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


def _write_jpg(path: Path, *, tint: int) -> None:
    import cv2

    arr = np.zeros((48, 64, 3), dtype=np.uint8)
    arr[:, :, 0] = tint
    arr[:, :, 1] = 80
    arr[:, :, 2] = 120
    ok, buf = cv2.imencode(".jpg", arr)
    assert ok
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.tobytes())


def _write_raw_episode(ep: Path, *, cams: dict[str, int], t0: float = 1000.0) -> None:
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "manifest.json").write_text(
        json.dumps({"valid": True, "episode_index": 0, "cameras": {}}) + "\n",
        encoding="utf-8",
    )
    for aid, n in cams.items():
        cam_dir = ep / "cameras" / aid
        cam_dir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for i in range(n):
            seq = 1000 + i
            name = f"{seq:08d}.jpg"
            _write_jpg(cam_dir / name, tint=40 + i * 15)
            lines.append(
                json.dumps(
                    {
                        "agent_id": aid,
                        "seq": seq,
                        "t_wall": t0 + i * 0.2,
                        "file": name,
                    }
                )
            )
        (cam_dir / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_write_raw_episode_grid_video(tmp_path: Path) -> None:
    import cv2

    from sensors_dcs.export.raw_grid_video import write_raw_episode_grid_video

    ep = tmp_path / "episode_00000"
    _write_raw_episode(ep, cams={"cam-left": 4, "cam-middle": 5, "cam-right": 3})
    out = write_raw_episode_grid_video(ep)
    assert out == ep / "raw_grid.mp4"
    assert out.is_file() and out.stat().st_size > 0

    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    ok, frame = cap.read()
    n = 0
    while ok:
        n += 1
        ok, frame2 = cap.read()
        if ok:
            frame = frame2
    cap.release()
    # by-index: n_steps = max(frame counts) → 5
    assert n == 5
    # 3 cams → 2x2 grid of 480x360
    assert frame is not None
    assert frame.shape[0] == 720
    assert frame.shape[1] == 960


def test_compose_raw_video_service(tmp_path: Path) -> None:
    from sensors_dcs.postprocess_service import compose_raw_video

    ep = tmp_path / "episode_00001"
    _write_raw_episode(ep, cams={"cam-left": 2, "cam-middle": 2})
    res = compose_raw_video(episode=ep)
    assert res["ok"] is True
    assert Path(res["meta"]["video"]).is_file()
    assert res["meta"]["video_name"] == "raw_grid.mp4"
    assert res["meta"]["cameras"]["cam-left"] == 2


def test_compose_raw_video_no_cameras(tmp_path: Path) -> None:
    from sensors_dcs.postprocess_service import compose_raw_video

    ep = tmp_path / "episode_00002"
    ep.mkdir()
    (ep / "manifest.json").write_text(json.dumps({"valid": True}) + "\n", encoding="utf-8")
    res = compose_raw_video(episode=ep)
    assert res["ok"] is False
    assert "no raw camera" in (res.get("error") or "").lower() or "JPEG" in (
        res.get("error") or ""
    )


def test_by_index_no_timestamp_align(tmp_path: Path) -> None:
    """Skewed t_wall must not change frame pairing — index i with index i."""
    import cv2

    from sensors_dcs.export.raw_grid_video import write_raw_episode_grid_video

    ep = tmp_path / "episode_00003"
    ep.mkdir(parents=True)
    (ep / "manifest.json").write_text(
        json.dumps({"valid": True, "episode_index": 0}) + "\n", encoding="utf-8"
    )
    # cam-left: 3 frames at t=0,1,2; cam-middle: 3 frames at t=100,101,102 (far apart)
    for aid, t0, tint0 in (("cam-left", 0.0, 10), ("cam-middle", 100.0, 200)):
        cam_dir = ep / "cameras" / aid
        cam_dir.mkdir(parents=True)
        lines: list[str] = []
        for i in range(3):
            seq = 1000 + i
            name = f"{seq:08d}.jpg"
            _write_jpg(cam_dir / name, tint=tint0 + i * 20)
            lines.append(
                json.dumps(
                    {
                        "agent_id": aid,
                        "seq": seq,
                        "t_wall": t0 + i,
                        "file": name,
                    }
                )
            )
        (cam_dir / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = write_raw_episode_grid_video(ep)
    cap = cv2.VideoCapture(str(out))
    n = 0
    ok, _ = cap.read()
    while ok:
        n += 1
        ok, _ = cap.read()
    cap.release()
    assert n == 3


def test_primary_camera_order_only(tmp_path: Path) -> None:
    """master_camera only affects cell order / FPS, not frame count (max length)."""
    import cv2

    from sensors_dcs.export.raw_grid_video import write_raw_episode_grid_video

    ep = tmp_path / "episode_00004"
    _write_raw_episode(ep, cams={"cam-left": 3, "cam-middle": 8})
    out = write_raw_episode_grid_video(ep, master_camera="cam-left")
    cap = cv2.VideoCapture(str(out))
    n = 0
    ok, _ = cap.read()
    while ok:
        n += 1
        ok, _ = cap.read()
    cap.release()
    assert n == 8
