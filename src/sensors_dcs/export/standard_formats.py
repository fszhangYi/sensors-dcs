"""Step 4: convert Step-2 ``timeline_filtered`` into mainstream training layouts.

Step-2 **primary** artifact is ``export/timeline_filtered.parquet`` (or ``.csv``).
Images are read from the **original** episode tree via ``{cam}.image_relpath``.
``export/filtered/`` is **not** used (optional materialize leftover from Step 2).

Formats:

- ``lerobot_v21`` / ``lerobot_v30`` / ``hdf5``

Single: one ``episode_*``. Batch: collect root of ``episode_*``. Always force-overwrite.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

FORMAT_LEROBOT_V21 = "lerobot_v21"
FORMAT_LEROBOT_V30 = "lerobot_v30"
FORMAT_HDF5 = "hdf5"

STANDARD_FORMATS: tuple[str, ...] = (
    FORMAT_LEROBOT_V21,
    FORMAT_LEROBOT_V30,
    FORMAT_HDF5,
)

_FORMAT_META: dict[str, dict[str, str]] = {
    FORMAT_LEROBOT_V21: {
        "id": FORMAT_LEROBOT_V21,
        "label": "LeRobot v2.1",
        "out_subdir": "lerobot_v21",
        "blurb": "meta/ + data/chunk-*/episode_*.parquet + videos/…/episode_*.mp4",
    },
    FORMAT_LEROBOT_V30: {
        "id": FORMAT_LEROBOT_V30,
        "label": "LeRobot v3.0",
        "out_subdir": "lerobot_v30",
        "blurb": "meta/info.json + data|videos chunk files (single-episode pack)",
    },
    FORMAT_HDF5: {
        "id": FORMAT_HDF5,
        "label": "HDF5",
        "out_subdir": "standard_hdf5",
        "blurb": "episode.hdf5 with observations/actions (+ optional images)",
    },
}


@dataclass
class _StdCam:
    agent_id: str
    path: Path
    serial: str | None = None


@dataclass
class _StdStep:
    seq: int
    t_wall: float
    joints: list[float]
    gripper: float
    cameras: dict[str, _StdCam]


def list_standard_formats() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for k in STANDARD_FORMATS:
        row = dict(_FORMAT_META[k])
        if k == FORMAT_HDF5:
            try:
                import h5py  # noqa: F401
            except ImportError:
                row["available"] = "false"
                row["blurb"] = row["blurb"] + " (pip install h5py)"
            else:
                row["available"] = "true"
        else:
            row["available"] = "true"
        out.append(row)
    return out


def normalize_formats(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw]
    else:
        raise ValueError(f"formats must be str|list, got {type(raw).__name__}")
    out: list[str] = []
    unknown: list[str] = []
    for p in parts:
        if not p:
            continue
        key = p.lower().replace("-", "_")
        aliases = {
            "lerobot": FORMAT_LEROBOT_V21,
            "lerobot_v2": FORMAT_LEROBOT_V21,
            "lerobot_v2.1": FORMAT_LEROBOT_V21,
            "lerobot21": FORMAT_LEROBOT_V21,
            "lerobot_v3": FORMAT_LEROBOT_V30,
            "lerobot_v3.0": FORMAT_LEROBOT_V30,
            "lerobot30": FORMAT_LEROBOT_V30,
            "h5": FORMAT_HDF5,
            "hdf5": FORMAT_HDF5,
        }
        key = aliases.get(key, key)
        if key not in STANDARD_FORMATS:
            unknown.append(p)
            continue
        if key not in out:
            out.append(key)
    if unknown:
        raise ValueError(
            f"unknown standard format(s): {unknown}; "
            f"choose from {list(STANDARD_FORMATS)}"
        )
    if not out:
        raise ValueError(f"formats required; choose from {list(STANDARD_FORMATS)}")
    return out


def _safe_cam_key(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]+", "_", str(name).strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "cam"


def find_timeline_filtered(export_dir: str | Path) -> Path | None:
    root = Path(export_dir)
    for name in ("timeline_filtered.parquet", "timeline_filtered.csv"):
        p = root / name
        if p.is_file():
            return p
    return None


def _read_timeline_filtered(path: Path):
    from sensors_dcs.export.parquet_io import require_pandas

    pd = require_pandas()
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    from sensors_dcs.export.parquet_io import read_parquet

    return read_parquet(path)


def check_step2_artifacts(episode: str | Path) -> dict[str, Any]:
    """Verify Step-2 wide table under ``<episode>/export/``.

    Required: non-empty ``timeline_filtered.parquet`` (or ``.csv``).
    ``export/filtered/`` is irrelevant to Step 4.
    """
    ep = Path(episode)
    export_dir = ep / "export"
    filter_meta_path = export_dir / "filter_meta.json"
    reasons: list[str] = []
    ok = True
    rows: int | None = None
    rows_out_meta: int | None = None
    table_path: Path | None = None

    if not ep.is_dir():
        return {
            "ok": False,
            "episode": str(ep),
            "export_dir": str(export_dir),
            "timeline_filtered": None,
            "rows": None,
            "reasons": [f"episode directory missing: {ep}"],
        }
    if not export_dir.is_dir():
        ok = False
        reasons.append("missing export/ (run Step 1–2 first)")
    else:
        table_path = find_timeline_filtered(export_dir)
        if table_path is None:
            ok = False
            reasons.append(
                "missing export/timeline_filtered.parquet "
                "(or .csv) — run Step 2 filter-timeline"
            )
        else:
            try:
                df = _read_timeline_filtered(table_path)
                rows = int(len(df))
            except Exception as e:  # noqa: BLE001
                ok = False
                reasons.append(f"cannot read {table_path.name}: {type(e).__name__}: {e}")
            else:
                if rows <= 0:
                    ok = False
                    reasons.append(
                        "timeline_filtered has 0 rows (Step 2 kept nothing); "
                        "loosen --require / --max-match-dt or fix missing images, "
                        "then re-run Step 2"
                    )

    if filter_meta_path.is_file():
        try:
            filter_meta = json.loads(filter_meta_path.read_text(encoding="utf-8"))
            if isinstance(filter_meta.get("rows_out"), int):
                rows_out_meta = int(filter_meta["rows_out"])
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    return {
        "ok": ok,
        "episode": str(ep.resolve() if ep.exists() else ep),
        "export_dir": str(export_dir),
        "timeline_filtered": str(table_path) if table_path else None,
        "has_timeline_filtered": table_path is not None,
        "has_filter_meta": filter_meta_path.is_file(),
        "rows": rows,
        "rows_out_meta": rows_out_meta,
        "reasons": reasons,
    }


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _present(val: Any) -> bool:
    if val is None:
        return False
    try:
        if isinstance(val, float) and math.isnan(val):
            return False
    except (TypeError, ValueError):
        pass
    try:
        import pandas as pd

        if pd.isna(val):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _load_camera_name_map(
    *,
    episode: Path,
    camera_map_yaml: str | Path | None,
) -> tuple[dict[str, str], str | None]:
    """serial → display name; empty map means fall back to agent_id."""
    from sensors_dcs.export.hik_dataset import load_camera_map_yaml

    candidates: list[Path] = []
    if camera_map_yaml:
        candidates.append(Path(camera_map_yaml))
    candidates.extend(
        [
            episode / "export" / "hik_camera_map.yaml",
            episode / "export" / "camera_map.yaml",
        ]
    )
    for p in candidates:
        try:
            if p.is_file():
                return load_camera_map_yaml(p), str(p.resolve())
        except Exception:  # noqa: BLE001
            continue
    return {}, None


def _pick_arm_gripper(
    columns: list[str],
    kind_hint: Mapping[str, str],
) -> tuple[str | None, str | None]:
    from sensors_dcs.export.filter import _agent_kind, _agents_in_frame

    agents = _agents_in_frame(columns)
    kinds = {aid: _agent_kind(columns, aid, kind_hint=dict(kind_hint)) for aid in agents}
    arm_id = None
    for prefer in ("arm_read", "gello"):
        for aid, kind in kinds.items():
            if kind == prefer:
                arm_id = aid
                break
        if arm_id:
            break
    if arm_id is None:
        for aid, kind in kinds.items():
            if kind in {"arm_read", "gello", "arm_write"}:
                arm_id = aid
                break
    grip_id = None
    for aid, kind in kinds.items():
        if kind == "gripper_read":
            grip_id = aid
            break
    return arm_id, grip_id


def _pad6(joints: list[float] | None) -> list[float]:
    j = [float(x) for x in (joints or [])]
    if len(j) < 6:
        j = j + [0.0] * (6 - len(j))
    return j[:6]


def load_aligned_from_timeline(
    episode: str | Path,
    *,
    camera_map_yaml: str | Path | None = None,
) -> tuple[dict[str, Any], list[_StdStep], dict[str, str], dict[str, Any]]:
    """Build steps from ``timeline_filtered`` + original episode image files."""
    from sensors_dcs.export.filter import (
        _agent_kind,
        _agents_in_frame,
        _joints_from_row,
        _kind_hints_from_manifest,
    )

    ep = Path(episode)
    table_path = find_timeline_filtered(ep / "export")
    if table_path is None:
        raise FileNotFoundError(
            f"missing timeline_filtered under {ep / 'export'}"
        )
    df = _read_timeline_filtered(table_path)
    if len(df) <= 0:
        raise ValueError("timeline_filtered has 0 rows")

    manifest: dict[str, Any] = {}
    man_path = ep / "manifest.json"
    if man_path.is_file():
        try:
            manifest = json.loads(man_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}

    columns = list(df.columns)
    kind_hint = _kind_hints_from_manifest(manifest)
    arm_id, grip_id = _pick_arm_gripper(columns, kind_hint)
    if not arm_id:
        from sensors_dcs.export.filter import _agents_in_frame

        agents = _agents_in_frame(columns)
        j_like = [
            c
            for c in columns
            if any(
                tok in c
                for tok in (".j0", ".j1", ".feedback_j0", ".j_raw0")
            )
        ]
        raise ValueError(
            "no arm/gello joint columns in timeline_filtered "
            "(need {agent}.j0 … for arm_read/gello). "
            f"agents_in_table={agents}; joint_like_cols={j_like[:20] or '[]'}. "
            "Camera-only / slave episodes cannot export state/action — "
            "use the master episode or a federated merged episode that includes arm/gello."
        )

    name_map, map_src = _load_camera_name_map(
        episode=ep, camera_map_yaml=camera_map_yaml
    )
    agents = _agents_in_frame(columns)
    cam_agents = [
        aid
        for aid in agents
        if _agent_kind(columns, aid, kind_hint=kind_hint) == "realsense"
    ]

    aligned: list[_StdStep] = []
    cam_to_agent: dict[str, str] = {}

    for _, row in df.iterrows():
        step_i = int(row["step"]) if "step" in columns and _present(row.get("step")) else len(aligned)
        t_wall = float(row["t_wall"]) if _present(row.get("t_wall")) else float(step_i)
        joints = _joints_from_row(row, arm_id, columns)
        if joints is None:
            raise ValueError(f"missing joints for {arm_id!r} at step {step_i}")
        joints = _pad6(joints)
        gripper = 0.0
        if grip_id:
            gcol = f"{grip_id}.position_norm"
            if gcol in columns and _present(row.get(gcol)):
                gripper = float(row.get(gcol))

        cameras: dict[str, _StdCam] = {}
        for aid in cam_agents:
            path_col = f"{aid}.image_relpath"
            file_col = f"{aid}.file"
            rel = row.get(path_col) if path_col in columns else None
            if not _present(rel) and file_col in columns and _present(row.get(file_col)):
                rel = f"cameras/{aid}/{row.get(file_col)}"
            if not _present(rel):
                raise FileNotFoundError(
                    f"missing image path for camera {aid!r} at step {step_i}"
                )
            src = ep / str(rel)
            if not src.is_file():
                raise FileNotFoundError(f"missing episode image: {src}")
            serial = None
            scol = f"{aid}.serial"
            if scol in columns and _present(row.get(scol)):
                serial = str(row.get(scol)).strip() or None
            if serial and serial in name_map:
                cam_name = name_map[serial]
            else:
                cam_name = _safe_cam_key(aid)
            cameras[cam_name] = _StdCam(agent_id=aid, path=src, serial=serial)
            cam_to_agent.setdefault(cam_name, aid)

        aligned.append(
            _StdStep(
                seq=step_i,
                t_wall=t_wall,
                joints=joints,
                gripper=gripper,
                cameras=cameras,
            )
        )

    meta = {
        "timeline_filtered": str(table_path),
        "camera_map": map_src,
        "arm_agent": arm_id,
        "gripper_agent": grip_id,
        "rows": len(aligned),
    }
    return manifest, aligned, cam_to_agent, meta


def _task_text(manifest: Mapping[str, Any]) -> str:
    tags = manifest.get("tags") if isinstance(manifest.get("tags"), dict) else {}
    task_id = str((tags or {}).get("task_id") or "").strip()
    if task_id:
        return task_id
    to = manifest.get("task_outcome") if isinstance(manifest.get("task_outcome"), dict) else {}
    note = str((to or {}).get("note") or "").strip()
    if note:
        return note
    nl = str(manifest.get("natural_language") or "").strip()
    return nl or "task"


def _episode_success(manifest: Mapping[str, Any]) -> bool | None:
    to = manifest.get("task_outcome") if isinstance(manifest.get("task_outcome"), dict) else None
    if not to:
        return None
    outcome = str(to.get("outcome") or "").strip().lower()
    if outcome == "success":
        return True
    if outcome in {"fail", "partial", "void"}:
        return False
    return None


def _state_vec(joints: Sequence[float], gripper: float) -> list[float]:
    return [float(x) for x in joints] + [float(gripper)]


def _action_vecs(aligned: Sequence[_StdStep]) -> list[list[float]]:
    out: list[list[float]] = []
    n = len(aligned)
    for i, step in enumerate(aligned):
        nxt = aligned[i + 1] if i + 1 < n else step
        out.append(_state_vec(nxt.joints, nxt.gripper))
    return out


def _estimate_fps(aligned: Sequence[_StdStep]) -> float:
    if len(aligned) < 2:
        return 5.0
    dts = [
        float(aligned[i + 1].t_wall) - float(aligned[i].t_wall)
        for i in range(len(aligned) - 1)
    ]
    dts = [d for d in dts if d > 1e-4]
    if not dts:
        return 5.0
    med = float(np.median(np.asarray(dts, dtype=np.float64)))
    if med <= 1e-4:
        return 5.0
    return float(max(1.0, min(60.0, round(1.0 / med, 3))))


def _cam_order(aligned: Sequence[_StdStep]) -> list[str]:
    names = sorted({name for s in aligned for name in s.cameras})
    if aligned:
        first = list(aligned[0].cameras.keys())
        names = first + [c for c in names if c not in first]
    return names


def _frame_paths(aligned: Sequence[_StdStep], cam: str) -> list[Path]:
    paths: list[Path] = []
    for step in aligned:
        info = step.cameras.get(cam)
        if info is None:
            raise FileNotFoundError(f"camera {cam!r} missing on a step")
        if not info.path.is_file():
            raise FileNotFoundError(f"missing image: {info.path}")
        paths.append(info.path)
    return paths


def _write_mp4(frames: list[Path], out_path: Path, *, fps: float) -> tuple[int, int]:
    import cv2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        raise ValueError("no frames for video")
    first = np.fromfile(str(frames[0]), dtype=np.uint8)
    img0 = cv2.imdecode(first, cv2.IMREAD_COLOR)
    if img0 is None:
        raise ValueError(f"cannot decode frame: {frames[0]}")
    h, w = img0.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, float(fps), (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"VideoWriter failed: {out_path}")
    try:
        for fp in frames:
            raw = np.fromfile(str(fp), dtype=np.uint8)
            img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if img is None:
                img = np.zeros((h, w, 3), dtype=np.uint8)
            elif img.shape[0] != h or img.shape[1] != w:
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            writer.write(img)
    finally:
        writer.release()
    return h, w


def _require_pandas():
    from sensors_dcs.export.parquet_io import require_pandas

    return require_pandas()


def _export_lerobot_v21(
    *,
    episode: Path,
    manifest: Mapping[str, Any],
    aligned: Sequence[_StdStep],
    cam_to_agent: Mapping[str, str],
    out_dir: Path,
    fps: float,
) -> dict[str, Any]:
    pd = _require_pandas()
    _clear_dir(out_dir)

    ep_index = int(manifest.get("episode_index") or 0)
    task = _task_text(manifest)
    states = [_state_vec(s.joints, s.gripper) for s in aligned]
    actions = _action_vecs(aligned)
    n = len(aligned)
    state_dim = len(states[0]) if states else 0
    action_dim = len(actions[0]) if actions else 0
    cam_names = _cam_order(aligned)

    video_shapes: dict[str, tuple[int, int, int]] = {}
    for cam in cam_names:
        key = _safe_cam_key(cam)
        rel = (
            Path("videos")
            / "chunk-000"
            / f"observation.images.{key}"
            / f"episode_{ep_index:06d}.mp4"
        )
        h, w = _write_mp4(_frame_paths(aligned, cam), out_dir / rel, fps=fps)
        video_shapes[key] = (h, w, 3)

    t0 = float(aligned[0].t_wall) if aligned else 0.0
    rows: dict[str, list[Any]] = {
        "observation.state": states,
        "action": actions,
        "timestamp": [float(s.t_wall) - t0 for s in aligned],
        "frame_index": list(range(n)),
        "episode_index": [ep_index] * n,
        "index": list(range(n)),
        "task_index": [0] * n,
    }
    success = _episode_success(manifest)
    if success is not None:
        rows["next.done"] = ([False] * (n - 1) + [True]) if n else []
        rows["complementary_info.success"] = [bool(success)] * n

    df = pd.DataFrame(rows)
    data_path = out_dir / "data" / "chunk-000" / f"episode_{ep_index:06d}.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_path, index=False)

    features: dict[str, Any] = {
        "observation.state": {
            "dtype": "float32",
            "shape": [state_dim],
            "names": [f"q{i}" for i in range(max(0, state_dim - 1))]
            + (["gripper"] if state_dim else []),
        },
        "action": {
            "dtype": "float32",
            "shape": [action_dim],
            "names": [f"q{i}" for i in range(max(0, action_dim - 1))]
            + (["gripper"] if action_dim else []),
        },
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    }
    for key, shape in video_shapes.items():
        features[f"observation.images.{key}"] = {
            "dtype": "video",
            "shape": list(shape),
            "names": ["height", "width", "channels"],
            "info": {
                "video.fps": float(fps),
                "video.codec": "mp4v",
                "video.is_depth_map": False,
                "video.channels": 3,
            },
        }
    if success is not None:
        features["complementary_info.success"] = {
            "dtype": "bool",
            "shape": [1],
            "names": None,
        }
        features["next.done"] = {"dtype": "bool", "shape": [1], "names": None}

    info = {
        "codebase_version": "v2.1",
        "robot_type": str(manifest.get("robot") or "elite"),
        "total_episodes": 1,
        "total_frames": n,
        "total_tasks": 1,
        "total_videos": len(video_shapes),
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": float(fps),
        "splits": {"train": "0:1"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": (
            "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
        ),
        "features": features,
        "source": "sensors-dcs",
        "source_format": "dcs_timeline_filtered_v1",
        "source_episode": episode.name,
        "camera_agent_map": dict(cam_to_agent),
    }
    meta = out_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (meta / "tasks.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"task_index": 0, "task": task}, ensure_ascii=False) + "\n")
    with (meta / "episodes.jsonl").open("w", encoding="utf-8") as f:
        ep_row: dict[str, Any] = {
            "episode_index": ep_index,
            "tasks": [task],
            "length": n,
        }
        if success is not None:
            ep_row["success"] = bool(success)
        f.write(json.dumps(ep_row, ensure_ascii=False) + "\n")
    (meta / "episodes_stats.jsonl").write_text(
        json.dumps({"episode_index": ep_index, "stats": {}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "format": FORMAT_LEROBOT_V21,
        "out_dir": str(out_dir),
        "frames": n,
        "cameras": list(video_shapes.keys()),
        "fps": float(fps),
        "task": task,
        "episode_index": ep_index,
    }


def _export_lerobot_v30(
    *,
    episode: Path,
    manifest: Mapping[str, Any],
    aligned: Sequence[_StdStep],
    cam_to_agent: Mapping[str, str],
    out_dir: Path,
    fps: float,
) -> dict[str, Any]:
    pd = _require_pandas()
    _clear_dir(out_dir)

    ep_index = int(manifest.get("episode_index") or 0)
    task = _task_text(manifest)
    states = [_state_vec(s.joints, s.gripper) for s in aligned]
    actions = _action_vecs(aligned)
    n = len(aligned)
    state_dim = len(states[0]) if states else 0
    action_dim = len(actions[0]) if actions else 0
    cam_names = _cam_order(aligned)

    video_shapes: dict[str, tuple[int, int, int]] = {}
    for cam in cam_names:
        key = _safe_cam_key(cam)
        rel = Path("videos") / key / "chunk-000" / "file-000.mp4"
        h, w = _write_mp4(_frame_paths(aligned, cam), out_dir / rel, fps=fps)
        video_shapes[key] = (h, w, 3)

    t0 = float(aligned[0].t_wall) if aligned else 0.0
    df = pd.DataFrame(
        {
            "observation.state": states,
            "action": actions,
            "timestamp": [float(s.t_wall) - t0 for s in aligned],
            "frame_index": list(range(n)),
            "episode_index": [ep_index] * n,
            "index": list(range(n)),
            "task_index": [0] * n,
        }
    )
    data_path = out_dir / "data" / "chunk-000" / "file-000.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_path, index=False)

    features: dict[str, Any] = {
        "observation.state": {"dtype": "float32", "shape": [state_dim], "names": None},
        "action": {"dtype": "float32", "shape": [action_dim], "names": None},
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    }
    for key, shape in video_shapes.items():
        features[f"observation.images.{key}"] = {
            "dtype": "video",
            "shape": list(shape),
            "names": ["height", "width", "channels"],
            "info": {"video.fps": float(fps), "video.codec": "mp4v"},
        }

    info = {
        "codebase_version": "v3.0",
        "robot_type": str(manifest.get("robot") or "elite"),
        "total_episodes": 1,
        "total_frames": n,
        "total_tasks": 1,
        "fps": float(fps),
        "splits": {"train": "0:1"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": features,
        "source": "sensors-dcs",
        "source_format": "dcs_timeline_filtered_v1",
        "source_episode": episode.name,
        "camera_agent_map": dict(cam_to_agent),
    }
    meta = out_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pd.DataFrame([{"task_index": 0, "task": task}]).to_parquet(
        meta / "tasks.parquet", index=False
    )
    ep_df = pd.DataFrame(
        [
            {
                "episode_index": ep_index,
                "tasks": [task],
                "length": n,
                "data_chunk_index": 0,
                "data_file_index": 0,
                **{f"videos/{k}/chunk_index": 0 for k in video_shapes},
                **{f"videos/{k}/file_index": 0 for k in video_shapes},
            }
        ]
    )
    ep_meta_dir = meta / "episodes" / "chunk-000"
    ep_meta_dir.mkdir(parents=True, exist_ok=True)
    ep_df.to_parquet(ep_meta_dir / "file-000.parquet", index=False)
    (meta / "stats.json").write_text("{}\n", encoding="utf-8")
    return {
        "format": FORMAT_LEROBOT_V30,
        "out_dir": str(out_dir),
        "frames": n,
        "cameras": list(video_shapes.keys()),
        "fps": float(fps),
        "task": task,
        "episode_index": ep_index,
    }


def _export_hdf5(
    *,
    episode: Path,
    manifest: Mapping[str, Any],
    aligned: Sequence[_StdStep],
    cam_to_agent: Mapping[str, str],
    out_dir: Path,
) -> dict[str, Any]:
    try:
        import h5py
    except ImportError as e:  # pragma: no cover
        raise ImportError("hdf5 export requires h5py; pip install h5py") from e

    import cv2

    _clear_dir(out_dir)
    path = out_dir / "episode.hdf5"
    states = np.asarray(
        [_state_vec(s.joints, s.gripper) for s in aligned], dtype=np.float32
    )
    actions = np.asarray(_action_vecs(aligned), dtype=np.float32)
    t_wall = np.asarray([float(s.t_wall) for s in aligned], dtype=np.float64)
    cam_names = _cam_order(aligned)

    with h5py.File(path, "w") as f:
        f.attrs["source"] = "sensors-dcs"
        f.attrs["source_format"] = "dcs_timeline_filtered_v1"
        f.attrs["source_episode"] = episode.name
        f.attrs["task"] = _task_text(manifest)
        success = _episode_success(manifest)
        if success is not None:
            f.attrs["success"] = bool(success)
        f.create_dataset("observations/state", data=states, compression="gzip")
        f.create_dataset("actions", data=actions, compression="gzip")
        f.create_dataset("t_wall", data=t_wall, compression="gzip")
        g = f.create_group("observations/images")
        for cam in cam_names:
            key = _safe_cam_key(cam)
            imgs: list[np.ndarray] = []
            for step in aligned:
                src = step.cameras[cam].path
                raw = np.fromfile(str(src), dtype=np.uint8)
                img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError(f"cannot decode {src}")
                imgs.append(img[:, :, ::-1])
            g.create_dataset(key, data=np.stack(imgs, axis=0), compression="gzip")
            agent = cam_to_agent.get(cam)
            if agent:
                g[key].attrs["agent_id"] = agent

    return {
        "format": FORMAT_HDF5,
        "out_dir": str(out_dir),
        "path": str(path),
        "frames": len(aligned),
        "cameras": [_safe_cam_key(c) for c in cam_names],
        "task": _task_text(manifest),
        "episode_index": int(manifest.get("episode_index") or 0),
    }


def export_standard_episode(
    episode: str | Path,
    *,
    formats: str | Sequence[str],
    camera_map_yaml: str | Path | None = None,
    allow_invalid: bool = False,
    fps: float | None = None,
) -> dict[str, Any]:
    """Export one episode from Step-2 ``timeline_filtered`` (force overwrite)."""
    from sensors_dcs.export.timeline import (
        ensure_episode_exportable,
        resolve_episode_dir,
    )

    ep = resolve_episode_dir(episode)
    check = check_step2_artifacts(ep)
    if not check["ok"]:
        return {
            "ok": False,
            "episode": str(ep),
            "error": "; ".join(check["reasons"]) or "step2 artifacts missing",
            "step2": check,
            "results": [],
        }

    wanted = normalize_formats(formats)
    man_path = ep / "manifest.json"
    if man_path.is_file():
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
            ensure_episode_exportable(
                man, allow_invalid=allow_invalid, episode_label=ep.name
            )
        except json.JSONDecodeError:
            pass

    try:
        manifest, aligned, cam_to_agent, load_meta = load_aligned_from_timeline(
            ep, camera_map_yaml=camera_map_yaml
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "episode": str(ep),
            "error": f"{type(e).__name__}: {e}",
            "step2": check,
            "results": [],
        }
    if not aligned:
        return {
            "ok": False,
            "episode": str(ep),
            "error": "timeline_filtered produced 0 aligned steps",
            "step2": check,
            "results": [],
        }

    use_fps = float(fps) if fps is not None and float(fps) > 0 else _estimate_fps(aligned)
    results: list[dict[str, Any]] = []
    log_lines = [
        f"==> standard-export episode={ep.name} formats={wanted} overwrite=true",
        f"step2 table={check.get('timeline_filtered')} rows={check.get('rows')}",
        f"source=timeline_filtered+episode_cameras arm={load_meta.get('arm_agent')}",
    ]
    if load_meta.get("camera_map"):
        log_lines.append(f"camera_map={load_meta['camera_map']}")

    for fmt in wanted:
        meta = _FORMAT_META[fmt]
        out_dir = ep / "export" / meta["out_subdir"]
        log_lines.append(f"==> {fmt} → {out_dir} (force overwrite)")
        try:
            if fmt == FORMAT_LEROBOT_V21:
                info = _export_lerobot_v21(
                    episode=ep,
                    manifest=manifest,
                    aligned=aligned,
                    cam_to_agent=cam_to_agent,
                    out_dir=out_dir,
                    fps=use_fps,
                )
            elif fmt == FORMAT_LEROBOT_V30:
                info = _export_lerobot_v30(
                    episode=ep,
                    manifest=manifest,
                    aligned=aligned,
                    cam_to_agent=cam_to_agent,
                    out_dir=out_dir,
                    fps=use_fps,
                )
            else:
                info = _export_hdf5(
                    episode=ep,
                    manifest=manifest,
                    aligned=aligned,
                    cam_to_agent=cam_to_agent,
                    out_dir=out_dir,
                )
            results.append({"format": fmt, "ok": True, "meta": info})
            log_lines.append(f"ok {fmt}")
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            results.append({"format": fmt, "ok": False, "error": err})
            log_lines.append(f"FAIL {fmt}: {err}")
            return {
                "ok": False,
                "episode": str(ep),
                "error": err,
                "step2": check,
                "results": results,
                "log": "\n".join(log_lines),
                "overwrite": True,
            }

    summary = {
        "ok": True,
        "episode": str(ep),
        "formats": wanted,
        "overwrite": True,
        "step2": check,
        "load": load_meta,
        "results": results,
        "log": "\n".join(log_lines),
    }
    meta_path = ep / "export" / "standard_export_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "episode": ep.name,
                "formats": wanted,
                "overwrite": True,
                "source": "timeline_filtered",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    summary["meta_path"] = str(meta_path)
    return summary


def iter_episode_dirs(root: str | Path) -> list[Path]:
    root_p = Path(root)
    if not root_p.is_dir():
        raise FileNotFoundError(f"dataset root not found: {root_p}")
    return sorted(
        p for p in root_p.iterdir() if p.is_dir() and p.name.startswith("episode_")
    )


def export_standard_root(
    input_root: str | Path,
    *,
    formats: str | Sequence[str],
    camera_map_yaml: str | Path | None = None,
    allow_invalid: bool = False,
    fps: float | None = None,
) -> dict[str, Any]:
    """Batch Step-4 over every ``episode_*``; always force-overwrite per format."""
    from sensors_dcs.export.timeline import ensure_episode_exportable

    root = Path(input_root).expanduser().resolve()
    wanted = normalize_formats(formats)
    eps = iter_episode_dirs(root)
    log_lines = [
        f"==> standard-export-root input={root} formats={wanted} overwrite=true "
        f"episodes={len(eps)}"
    ]
    rows: list[dict[str, Any]] = []
    ok_n = fail_n = skip_n = 0

    for ep in eps:
        man_path = ep / "manifest.json"
        if man_path.is_file():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
                ensure_episode_exportable(
                    man, allow_invalid=allow_invalid, episode_label=ep.name
                )
            except Exception as e:  # noqa: BLE001
                skip_n += 1
                rows.append(
                    {
                        "episode": str(ep),
                        "ok": False,
                        "skipped": True,
                        "error": str(e),
                    }
                )
                log_lines.append(f"SKIP {ep.name}: {e}")
                continue
        check = check_step2_artifacts(ep)
        if not check["ok"]:
            fail_n += 1
            err = "; ".join(check["reasons"]) or "step2 missing"
            rows.append(
                {
                    "episode": str(ep),
                    "ok": False,
                    "skipped": False,
                    "error": err,
                    "step2": check,
                }
            )
            log_lines.append(f"FAIL {ep.name}: {err}")
            continue
        one = export_standard_episode(
            ep,
            formats=wanted,
            camera_map_yaml=camera_map_yaml,
            allow_invalid=allow_invalid,
            fps=fps,
        )
        if one.get("ok"):
            ok_n += 1
            log_lines.append(f"ok {ep.name}")
        else:
            fail_n += 1
            log_lines.append(f"FAIL {ep.name}: {one.get('error')}")
        rows.append(one)

    return {
        "ok": fail_n == 0,
        "input_root": str(root),
        "formats": wanted,
        "overwrite": True,
        "ok_count": ok_n,
        "failed_count": fail_n,
        "skipped_count": skip_n,
        "episodes": rows,
        "log": "\n".join(log_lines),
        "error": None if fail_n == 0 else f"{fail_n} episode(s) failed",
    }
