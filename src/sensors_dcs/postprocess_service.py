"""HTTP / UI helpers for the three offline postprocess CLI steps."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from sensors_dcs.paths import launch_cwd, project_root, user_data_dir

# Defaults mirror the operator's usual Windows workflow (episode_00016 example).
DEFAULT_ALIGN = "asof"
DEFAULT_MASTER = "cam-left"
DEFAULT_MASTER_HZ = 5.0
DEFAULT_REQUIRE = "arm,cam-left,cam-right,cam-middle,gripper-read"
DEFAULT_MAX_MATCH_DT = "0.033"
DEFAULT_TRIM = "both"
DEFAULT_STEPS = ("export-timeline", "filter-timeline", "export-hik-dataset")


def default_camera_map_candidates() -> list[str]:
    """Prefer launch-pwd / repo map; user-data seed is last (often stale)."""
    from sensors_dcs.paths import capture_launch_cwd

    capture_launch_cwd()
    cwd = launch_cwd()
    out: list[str] = []
    seen: set[str] = set()
    for p in (
        cwd / "configs" / "hik_camera_map.yaml",
        cwd / "hik_camera_map.yaml",
        project_root() / "configs" / "hik_camera_map.yaml",
        user_data_dir() / "configs" / "hik_camera_map.yaml",
    ):
        try:
            if not p.is_file():
                continue
            key = str(p.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def postprocess_defaults(*, save_dir: str | Path | None = None) -> dict[str, Any]:
    maps = default_camera_map_candidates()
    return {
        "align": DEFAULT_ALIGN,
        "master": DEFAULT_MASTER,
        "master_hz": DEFAULT_MASTER_HZ,
        "require": DEFAULT_REQUIRE,
        "max_match_dt": DEFAULT_MAX_MATCH_DT,
        "trim": DEFAULT_TRIM,
        "materialize": True,
        "camera_map": maps[0] if maps else "",
        "camera_map_candidates": maps,
        "allow_invalid": False,
        "steps": list(DEFAULT_STEPS),
        "episodes": list_episodes(save_dir) if save_dir else [],
        "save_dir": str(Path(save_dir).expanduser().resolve()) if save_dir else None,
    }


def list_episodes(save_dir: str | Path | None) -> list[dict[str, Any]]:
    if not save_dir:
        return []
    root = Path(save_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(root.glob("episode_*"), key=lambda x: x.name):
        if not p.is_dir():
            continue
        man = p / "manifest.json"
        valid = None
        if man.is_file():
            try:
                import json

                data = json.loads(man.read_text(encoding="utf-8"))
                if "valid" in data:
                    valid = bool(data["valid"])
            except Exception:  # noqa: BLE001
                pass
        rows.append(
            {
                "name": p.name,
                "path": str(p),
                "valid": valid,
                "has_export": (p / "export").is_dir(),
                "has_hik": (p / "export" / "hik_dataset").is_dir(),
            }
        )
    rows.reverse()  # newest first
    return rows


def _master_candidates_from_manifest(manifest: dict[str, Any]) -> list[str]:
    """Agent ids suitable as export-timeline ``--master`` (manifest order, unique)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in manifest.get("agents") or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("agent_id") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
    # Camera keys are agent ids when agents[] omitted / incomplete.
    cameras = manifest.get("cameras")
    if isinstance(cameras, dict):
        for aid in cameras:
            key = str(aid).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _suggest_master(manifest: dict[str, Any], candidates: list[str]) -> str | None:
    if not candidates:
        return None
    kind_by_id: dict[str, str] = {}
    for item in manifest.get("agents") or []:
        if isinstance(item, dict) and item.get("agent_id"):
            kind_by_id[str(item["agent_id"])] = str(item.get("kind") or "")
    for prefer in ("gello", "arm", "arm_read", "realsense"):
        for aid in candidates:
            if kind_by_id.get(aid) == prefer or (
                prefer == "realsense" and aid.startswith("cam-")
            ):
                return aid
    for aid in candidates:
        if aid.startswith("cam-"):
            return aid
    return candidates[0]


def inspect_episode(episode: str | Path) -> dict[str, Any]:
    """Validate episode ``manifest.json`` and list master candidates.

    Unqualified: missing/unreadable manifest, ``valid is False``, or no agents.
    """
    import json

    raw = str(episode or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": "empty episode path",
            "path": None,
            "valid": None,
            "master_candidates": [],
            "suggested_master": None,
        }
    ep = Path(raw).expanduser()
    try:
        ep = ep.resolve()
    except OSError:
        pass
    man_path = ep / "manifest.json"
    base: dict[str, Any] = {
        "ok": False,
        "path": str(ep),
        "manifest_path": str(man_path),
        "valid": None,
        "provisional": None,
        "note": None,
        "agents": [],
        "master_candidates": [],
        "suggested_master": None,
        "error": None,
    }
    if not ep.is_dir():
        base["error"] = f"episode directory not found: {ep}"
        return base
    if not man_path.is_file():
        base["error"] = f"manifest.json not found under {ep}"
        return base
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"manifest.json unreadable: {exc}"
        return base
    if not isinstance(manifest, dict):
        base["error"] = "manifest.json root must be a mapping"
        return base

    valid = manifest.get("valid")
    if "valid" in manifest:
        base["valid"] = bool(valid)
    base["provisional"] = bool(manifest.get("provisional")) if "provisional" in manifest else None
    base["note"] = manifest.get("note")
    candidates = _master_candidates_from_manifest(manifest)
    base["master_candidates"] = candidates
    base["agents"] = [
        {
            "agent_id": str(a.get("agent_id")),
            "kind": a.get("kind"),
            "hz_target": a.get("hz_target"),
            "sensor_id": a.get("sensor_id"),
        }
        for a in (manifest.get("agents") or [])
        if isinstance(a, dict) and a.get("agent_id")
    ]
    base["suggested_master"] = _suggest_master(manifest, candidates)

    if base["valid"] is False:
        note = base.get("note") or "manifest.valid=false"
        base["error"] = f"episode marked invalid ({note})"
        return base
    if not candidates:
        base["error"] = "manifest has no agent ids for master"
        return base

    base["ok"] = True
    base["error"] = None
    return base


def run_postprocess(
    *,
    episode: str | Path,
    steps: list[str] | None = None,
    align: str = DEFAULT_ALIGN,
    master: str = DEFAULT_MASTER,
    master_hz: float | None = DEFAULT_MASTER_HZ,
    require: str = DEFAULT_REQUIRE,
    max_match_dt: str = DEFAULT_MAX_MATCH_DT,
    trim: str = DEFAULT_TRIM,
    materialize: bool = True,
    camera_map: str | None = None,
    allow_invalid: bool = False,
) -> dict[str, Any]:
    """Run selected postprocess steps sequentially. Stops on first failure."""
    from sensors_dcs.export.filter import filter_episode_timeline
    from sensors_dcs.export.hik_dataset import export_hik_dataset
    from sensors_dcs.export.timeline import export_episode_timeline, resolve_episode_dir

    ep = resolve_episode_dir(episode)
    wanted = list(steps or DEFAULT_STEPS)
    unknown = [s for s in wanted if s not in DEFAULT_STEPS]
    if unknown:
        return {"ok": False, "error": f"unknown steps: {unknown}", "episode": str(ep), "results": []}

    cam_map = (camera_map or "").strip() or None
    results: list[dict[str, Any]] = []
    log_lines: list[str] = []

    for step in wanted:
        log_lines.append(f"==> {step}")
        try:
            if step == "export-timeline":
                meta = export_episode_timeline(
                    ep,
                    align=align,  # type: ignore[arg-type]
                    master=master or None,
                    master_hz=float(master_hz) if master_hz is not None else None,
                    allow_invalid=allow_invalid,
                )
            elif step == "filter-timeline":
                meta = filter_episode_timeline(
                    ep,
                    require=require or None,
                    max_match_dt=max_match_dt or None,
                    trim=trim,  # type: ignore[arg-type]
                    materialize=bool(materialize),
                    allow_invalid=allow_invalid,
                )
            else:  # export-hik-dataset
                if not cam_map:
                    raise ValueError(
                        "camera_map is required for export-hik-dataset "
                        "(path to hik_camera_map.yaml)"
                    )
                meta = export_hik_dataset(
                    ep,
                    camera_map_yaml=cam_map,
                    allow_invalid=allow_invalid,
                )
            results.append({"step": step, "ok": True, "meta": meta})
            log_lines.append(f"ok {step}")
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            results.append(
                {
                    "step": step,
                    "ok": False,
                    "error": err,
                    "traceback": traceback.format_exc(),
                }
            )
            log_lines.append(f"FAIL {step}: {err}")
            return {
                "ok": False,
                "error": err,
                "episode": str(ep),
                "results": results,
                "log": "\n".join(log_lines),
            }

    return {
        "ok": True,
        "episode": str(ep),
        "results": results,
        "log": "\n".join(log_lines),
    }
