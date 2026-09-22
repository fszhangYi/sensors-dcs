"""Compose a multi-camera preview MP4 from raw episode JPEG frames.

Reads ``episode/cameras/<agent_id>/{index.jsonl, *.jpg}`` directly — no
export-timeline / filter / hik_dataset dependency. Layout matches
``hik_grid_video`` (sqrt grid of camera cells), without the sensor text panel.

Frames are taken by per-camera index (no ``t_wall`` nearest-neighbor sync).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sensors_dcs.export.hik_grid_video import (
    _grid_shape,
    _label_cell,
    _load_bgr,
    estimate_fps_from_walls,
)


@dataclass(frozen=True)
class _CamFrame:
    t_wall: float | None
    path: Path
    seq: int


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def load_raw_camera_frames(ep_dir: str | Path) -> dict[str, list[_CamFrame]]:
    """Load per-camera frame lists sorted by ``seq`` (existing JPEG only)."""
    root = Path(ep_dir)
    cameras = root / "cameras"
    out: dict[str, list[_CamFrame]] = {}
    if not cameras.is_dir():
        return out
    for agent_dir in sorted(p for p in cameras.iterdir() if p.is_dir()):
        index_path = agent_dir / "index.jsonl"
        if not index_path.is_file():
            continue
        frames: list[_CamFrame] = []
        for row in _read_jsonl(index_path):
            file_name = str(row.get("file") or "").strip()
            if not file_name:
                continue
            img_path = agent_dir / file_name
            if not img_path.is_file():
                continue
            t_wall: float | None
            try:
                t_wall = float(row.get("t_wall"))
            except (TypeError, ValueError):
                t_wall = None
            try:
                seq = int(row.get("seq") if row.get("seq") is not None else len(frames))
            except (TypeError, ValueError):
                seq = len(frames)
            frames.append(_CamFrame(t_wall=t_wall, path=img_path, seq=seq))
        # Index order: seq first; stable for equal seq by original append order.
        frames.sort(key=lambda f: (f.seq, f.t_wall if f.t_wall is not None else 0.0))
        if frames:
            out[agent_dir.name] = frames
    return out


def _pick_primary(cams: dict[str, list[_CamFrame]], preferred: str | None = None) -> str:
    """Prefer named camera for cell order / FPS estimate (not for time sync)."""
    if not cams:
        raise ValueError("no camera frames")
    prefer = (preferred or "").strip()
    if prefer and prefer in cams:
        return prefer
    for name in ("cam-middle", "cam-left", "cam-right", "camera"):
        if name in cams:
            return name
    # Longest stream wins (richest preview).
    return max(cams.keys(), key=lambda k: (len(cams[k]), k))


def write_raw_episode_grid_video(
    episode_dir: str | Path,
    *,
    out_name: str = "raw_grid.mp4",
    cell_wh: tuple[int, int] = (480, 360),
    fps: float | None = None,
    master_camera: str | None = None,
) -> Path:
    """Write a camera-grid MP4 under the episode root from raw JPEGs.

    Each output step ``i`` takes frame ``i`` from every camera (clamp to last
    if shorter). No ``t_wall`` nearest-neighbor alignment. ``master_camera``
    only affects cell order and FPS estimation. Returns the output path.
    """
    import cv2

    ep = Path(episode_dir)
    if not ep.is_dir():
        raise FileNotFoundError(f"episode directory not found: {ep}")

    cams = load_raw_camera_frames(ep)
    if not cams:
        raise ValueError(f"no raw camera JPEG frames under {ep / 'cameras'}")

    primary_id = _pick_primary(cams, master_camera)
    cam_names = sorted(cams.keys())
    # Put primary first for stable left-to-right reading, then the rest.
    ordered = [primary_id] + [c for c in cam_names if c != primary_id]

    n_steps = max(len(frames) for frames in cams.values())
    n_cells = len(ordered)
    rows, cols = _grid_shape(n_cells)
    cw, ch = int(cell_wh[0]), int(cell_wh[1])
    frame_w, frame_h = cols * cw, rows * ch

    t_walls = [f.t_wall for f in cams[primary_id] if f.t_wall is not None]
    use_fps = float(fps) if fps is not None and float(fps) > 0 else estimate_fps_from_walls(
        t_walls, default=5.0
    )

    out_path = ep / out_name
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
            for name in ordered:
                frames = cams[name]
                idx = i if i < len(frames) else len(frames) - 1
                cell = _load_bgr(frames[idx].path, (cw, ch))
                tiles.append(_label_cell(cell, name))

            while len(tiles) < rows * cols:
                tiles.append(np.zeros((ch, cw, 3), dtype=np.uint8))

            row_imgs = []
            for r in range(rows):
                row_imgs.append(np.hstack(tiles[r * cols : (r + 1) * cols]))
            writer.write(np.vstack(row_imgs))
    finally:
        writer.release()

    if not out_path.is_file() or out_path.stat().st_size <= 0:
        raise RuntimeError(f"raw grid video not written: {out_path}")
    return out_path
