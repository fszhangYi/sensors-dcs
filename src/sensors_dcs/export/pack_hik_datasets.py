"""Batch-pack episode ``export/hik_dataset`` trees into a flat training layout.

For each ``episode_XXXXX`` under an input root that has ``export/hik_dataset``:

* copy into ``{output}/{i}/`` with ``i`` from the episode folder index
* exclude ``camera_map.yaml`` and ``episode_grid.mp4`` from the training copy
* copy ``episode_grid.mp4`` → ``{output}/video/{i}.mp4`` when present

Source episodes are never modified.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

_EP_RE = re.compile(r"^episode_(\d+)$", re.IGNORECASE)
_IGNORE_NAMES = frozenset({"camera_map.yaml", "episode_grid.mp4"})


def parse_episode_index(name: str) -> int | None:
    """``episode_00016`` → ``16``; invalid names → ``None``."""
    m = _EP_RE.match(str(name).strip())
    if not m:
        return None
    try:
        return int(m.group(1), 10)
    except ValueError:
        return None


def _manifest_valid(ep_dir: Path) -> bool | None:
    man = ep_dir / "manifest.json"
    if not man.is_file():
        return None
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if "valid" not in data:
        return None
    return bool(data["valid"])


def _ignore_pack(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _IGNORE_NAMES}


def _rm_dst(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file() or path.is_symlink():
        path.unlink()


def pack_hik_datasets(
    input_root: str | Path,
    output_root: str | Path,
    *,
    skip_invalid: bool = True,
) -> dict[str, Any]:
    """Scan ``input_root/episode_*`` and pack hik_dataset trees into ``output_root``.

    Returns a summary dict with ``ok``, counts, and per-episode ``items``.
    """
    try:
        src_root = Path(input_root).expanduser().resolve()
    except OSError as e:
        return {"ok": False, "error": f"invalid input_root: {e}"}
    try:
        dst_root = Path(output_root).expanduser().resolve()
    except OSError as e:
        return {"ok": False, "error": f"invalid output_root: {e}"}

    if not src_root.is_dir():
        return {"ok": False, "error": f"input_root is not a directory: {src_root}"}
    if src_root == dst_root:
        return {"ok": False, "error": "output_root must differ from input_root"}
    try:
        dst_root.relative_to(src_root)
        return {
            "ok": False,
            "error": "output_root must not be inside input_root",
        }
    except ValueError:
        pass
    try:
        src_root.relative_to(dst_root)
        return {
            "ok": False,
            "error": "input_root must not be inside output_root",
        }
    except ValueError:
        pass

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
        video_dir = dst_root / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"cannot create output_root: {e}"}

    items: list[dict[str, Any]] = []
    copied = 0
    skipped = 0
    errors = 0
    warnings = 0

    episodes = sorted(
        (p for p in src_root.glob("episode_*") if p.is_dir()),
        key=lambda p: p.name,
    )
    for ep_dir in episodes:
        name = ep_dir.name
        idx = parse_episode_index(name)
        row: dict[str, Any] = {
            "episode": name,
            "path": str(ep_dir),
            "index": idx,
            "status": "pending",
        }
        if idx is None:
            row["status"] = "skipped"
            row["reason"] = "bad_episode_name"
            skipped += 1
            items.append(row)
            continue

        hik = ep_dir / "export" / "hik_dataset"
        if not hik.is_dir():
            row["status"] = "skipped"
            row["reason"] = "missing_hik_dataset"
            skipped += 1
            items.append(row)
            continue

        valid = _manifest_valid(ep_dir)
        row["valid"] = valid
        if skip_invalid and valid is False:
            row["status"] = "skipped"
            row["reason"] = "invalid_episode"
            skipped += 1
            items.append(row)
            continue

        dst_ep = dst_root / str(idx)
        grid_src = hik / "episode_grid.mp4"
        grid_dst = video_dir / f"{idx}.mp4"
        try:
            _rm_dst(dst_ep)
            shutil.copytree(hik, dst_ep, ignore=_ignore_pack)
            # Defensive: remove if ignore missed nested copies.
            for banned in _IGNORE_NAMES:
                leftover = dst_ep / banned
                if leftover.exists():
                    _rm_dst(leftover)
            row["dataset_dir"] = str(dst_ep)
            if grid_src.is_file():
                if grid_dst.exists():
                    _rm_dst(grid_dst)
                shutil.copy2(grid_src, grid_dst)
                row["video"] = str(grid_dst)
            else:
                row["video"] = None
                row["warning"] = "missing_episode_grid"
                warnings += 1
            row["status"] = "copied"
            copied += 1
        except OSError as e:
            row["status"] = "error"
            row["error"] = str(e)
            errors += 1
            try:
                if dst_ep.exists():
                    _rm_dst(dst_ep)
            except OSError:
                pass
        items.append(row)

    return {
        "ok": errors == 0,
        "input_root": str(src_root),
        "output_root": str(dst_root),
        "skip_invalid": bool(skip_invalid),
        "copied": copied,
        "skipped": skipped,
        "errors": errors,
        "warnings": warnings,
        "items": items,
        "error": None if errors == 0 else f"{errors} episode(s) failed to pack",
    }
