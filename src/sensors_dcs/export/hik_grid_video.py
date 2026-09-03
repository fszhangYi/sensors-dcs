"""Compose one episode preview video from hik_dataset rgb frames + sensor text.

Layout: camera cells first (stable hik camera order), last cell = non-camera
sensors (joints / gripper / cartesian) drawn as text for that step index.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


def _grid_shape(n_cells: int) -> tuple[int, int]:
    if n_cells <= 0:
        return 1, 1
    cols = int(math.ceil(math.sqrt(n_cells)))
    rows = int(math.ceil(n_cells / cols))
    return rows, cols


def _load_bgr(path: Path, size: tuple[int, int]) -> np.ndarray:
    import cv2

    w, h = int(size[0]), int(size[1])
    blank = np.zeros((h, w, 3), dtype=np.uint8)
    if not path.is_file():
        return blank
    raw = np.fromfile(str(path), dtype=np.uint8)
    if raw.size == 0:
        return blank
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        return blank
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def _fmt_vec(xs: Sequence[float] | None, *, nd: int = 3) -> str:
    if not xs:
        return "-"
    return " ".join(f"{float(x):.{nd}f}" for x in xs)


def _draw_sensor_panel(
    size: tuple[int, int],
    *,
    step_i: int,
    n_steps: int,
    joints: Sequence[float] | None,
    gripper: float | None,
    cart_obs: Sequence[float] | None,
    cart_act: Sequence[float] | None,
) -> np.ndarray:
    import cv2

    w, h = int(size[0]), int(size[1])
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (32, 32, 36)
    lines = [
        f"sensors  step {step_i + 1}/{n_steps}",
        f"joint  {_fmt_vec(joints)}",
        f"grip   {float(gripper):.4f}" if gripper is not None else "grip   -",
        f"cart   {_fmt_vec(cart_obs)}",
        f"dcart  {_fmt_vec(cart_act)}",
    ]
    y0 = 28
    scale = max(0.35, min(0.55, w / 900.0))
    thickness = 1
    for i, line in enumerate(lines):
        y = y0 + i * int(22 + scale * 18)
        cv2.putText(
            img,
            line[:120],
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (220, 220, 220),
            thickness,
            cv2.LINE_AA,
        )
    cv2.putText(
        img,
        "other sensors",
        (12, h - 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (140, 140, 150),
        1,
        cv2.LINE_AA,
    )
    return img


def _label_cell(img: np.ndarray, title: str) -> np.ndarray:
    import cv2

    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 22), (0, 0, 0), thickness=-1)
    cv2.putText(
        out,
        title[:48],
        (6, 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return out


def estimate_fps_from_walls(t_walls: Sequence[float], *, default: float = 5.0) -> float:
    if len(t_walls) < 2:
        return float(default)
    dt = float(t_walls[-1]) - float(t_walls[0])
    if dt <= 1e-6:
        return float(default)
    return float(max(1.0, min(30.0, (len(t_walls) - 1) / dt)))


def write_episode_grid_video(
    hik_dir: str | Path,
    *,
    camera_names: Sequence[str],
    steps: Mapping[str, Any],
    n_steps: int,
    out_name: str = "episode_grid.mp4",
    cell_wh: tuple[int, int] = (480, 360),
    fps: float = 5.0,
    t_walls: Sequence[float] | None = None,
) -> Path:
    """Write a grid MP4 under ``hik_dir``; cameras first, last cell = sensors."""
    import cv2

    hik_dir = Path(hik_dir)
    if n_steps <= 0:
        raise ValueError("n_steps must be > 0")
    cams = [str(c) for c in camera_names]
    n_cells = len(cams) + 1
    rows, cols = _grid_shape(n_cells)
    cw, ch = int(cell_wh[0]), int(cell_wh[1])
    frame_w, frame_h = cols * cw, rows * ch

    obs = steps.get("observations") or {}
    act = steps.get("actions") or {}
    joints_all = list(obs.get("joint_position") or [])
    cart_obs_all = list(obs.get("cartesian_position") or [])
    cart_act_all = list(act.get("cartesian_position") or [])
    grip_raw = obs.get("gripper_position")
    if isinstance(grip_raw, list) and grip_raw and isinstance(grip_raw[0], list):
        grip_all = list(grip_raw[0])
    elif isinstance(grip_raw, list):
        grip_all = list(grip_raw)
    else:
        grip_all = []

    use_fps = float(fps)
    if t_walls is not None:
        use_fps = estimate_fps_from_walls(t_walls, default=use_fps)

    out_path = hik_dir / out_name
    if out_path.exists():
        out_path.unlink()

    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        use_fps,
        (frame_w, frame_h),
    )
    if not writer.isOpened():
        raise RuntimeError(f"failed to open VideoWriter for {out_path}")

    try:
        for i in range(n_steps):
            tiles: list[np.ndarray] = []
            for name in cams:
                path = hik_dir / f"rgb_{name}_{i}.jpg"
                cell = _load_bgr(path, (cw, ch))
                tiles.append(_label_cell(cell, name))
            joints = joints_all[i] if i < len(joints_all) else None
            grip = float(grip_all[i]) if i < len(grip_all) else None
            cart_o = cart_obs_all[i] if i < len(cart_obs_all) else None
            cart_a = cart_act_all[i] if i < len(cart_act_all) else None
            panel = _draw_sensor_panel(
                (cw, ch),
                step_i=i,
                n_steps=n_steps,
                joints=joints,
                gripper=grip,
                cart_obs=cart_o,
                cart_act=cart_a,
            )
            tiles.append(_label_cell(panel, "sensors"))

            while len(tiles) < rows * cols:
                tiles.append(np.zeros((ch, cw, 3), dtype=np.uint8))

            row_imgs = []
            for r in range(rows):
                row_imgs.append(np.hstack(tiles[r * cols : (r + 1) * cols]))
            frame = np.vstack(row_imgs)
            writer.write(frame)
    finally:
        writer.release()

    if not out_path.is_file() or out_path.stat().st_size <= 0:
        raise RuntimeError(f"grid video not written: {out_path}")
    return out_path
