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
DEFAULT_ALIGN_CLOCK = "wall"
DEFAULT_PRIMARY_CAMERA = "cam-middle"
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
        "align_clock": DEFAULT_ALIGN_CLOCK,
        "primary_camera": DEFAULT_PRIMARY_CAMERA,
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
    align_clock: str = DEFAULT_ALIGN_CLOCK,
    primary_camera: str = DEFAULT_PRIMARY_CAMERA,
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
    from sensors_dcs.export.timeline import (
        export_episode_timeline,
        resolve_episode_dir,
        wait_episode_manifest_ready,
    )

    ep = resolve_episode_dir(episode)
    wanted = list(steps or DEFAULT_STEPS)
    unknown = [s for s in wanted if s not in DEFAULT_STEPS]
    if unknown:
        return {"ok": False, "error": f"unknown steps: {unknown}", "episode": str(ep), "results": []}

    # Async flush may still be rewriting the start-time provisional manifest
    # (valid=false). Wait so quick-collect does not race the final rewrite.
    wait_episode_manifest_ready(ep, timeout_s=120.0, poll_s=0.1)

    clock = (align_clock or DEFAULT_ALIGN_CLOCK).strip().lower()
    if clock not in {"wall", "hw_ts"}:
        return {
            "ok": False,
            "error": f"align_clock must be wall|hw_ts, got {align_clock!r}",
            "episode": str(ep),
            "results": [],
        }
    primary = (primary_camera or DEFAULT_PRIMARY_CAMERA).strip() or DEFAULT_PRIMARY_CAMERA

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
                    align_clock=clock,  # type: ignore[arg-type]
                    primary_camera=primary,
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
            if step == "filter-timeline" and isinstance(meta, dict):
                ap = meta.get("align_plot")
                api = meta.get("align_plot_info") if isinstance(meta.get("align_plot_info"), dict) else {}
                if ap:
                    log_lines.append(f"align_plot {ap}")
                else:
                    err = (api or {}).get("error") or "no align_plot"
                    log_lines.append(f"align_plot FAIL: {err}")
                aq = meta.get("align_quality") if isinstance(meta.get("align_quality"), dict) else {}
                if aq and aq.get("score") is not None:
                    log_lines.append(
                        f"align_quality score={aq.get('score')} grade={aq.get('grade')} "
                        f"keep={((aq.get('components') or {}).get('keep_rate'))} "
                        f"sync={((aq.get('components') or {}).get('sync'))} "
                        f"budget={((aq.get('components') or {}).get('budget'))}"
                    )
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


def clear_episode_export(episode: str | Path) -> dict[str, Any]:
    """Remove ``<episode>/export`` entirely (used by batch overwrite)."""
    import shutil

    ep = Path(episode)
    export_dir = ep / "export"
    if not export_dir.exists():
        return {"cleared": False, "path": str(export_dir), "reason": "missing"}
    if not export_dir.is_dir():
        return {"cleared": False, "path": str(export_dir), "reason": "not_a_directory"}
    shutil.rmtree(export_dir)
    return {"cleared": True, "path": str(export_dir)}


def _pick_master_for_episode(
    insp: dict[str, Any],
    *,
    preferred: str | None,
) -> str | None:
    candidates = list(insp.get("master_candidates") or [])
    pref = (preferred or "").strip()
    if pref and pref in candidates:
        return pref
    suggested = insp.get("suggested_master")
    if suggested and str(suggested) in candidates:
        return str(suggested)
    if len(candidates) == 1:
        return str(candidates[0])
    return pref or (str(suggested) if suggested else None) or None


def run_postprocess_root(
    *,
    input_root: str | Path,
    steps: list[str] | None = None,
    align: str = DEFAULT_ALIGN,
    master: str = DEFAULT_MASTER,
    master_hz: float | None = DEFAULT_MASTER_HZ,
    align_clock: str = DEFAULT_ALIGN_CLOCK,
    primary_camera: str = DEFAULT_PRIMARY_CAMERA,
    require: str = DEFAULT_REQUIRE,
    max_match_dt: str = DEFAULT_MAX_MATCH_DT,
    trim: str = DEFAULT_TRIM,
    materialize: bool = True,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Run the three-step postprocess on every ``episode_*`` under ``input_root``.

    ``overwrite=True`` (default): delete each episode's ``export/`` before running.
    ``overwrite=False``: skip episodes that already have an ``export/`` directory.
    Per-episode failures are recorded; the batch continues.
    """
    root = Path(input_root).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    log_lines: list[str] = [
        f"==> run-postprocess-root input={root} overwrite={bool(overwrite)} "
        f"allow_invalid={bool(allow_invalid)}"
    ]
    if not root.is_dir():
        err = f"input_root is not a directory: {root}"
        log_lines.append(f"FAIL: {err}")
        return {
            "ok": False,
            "error": err,
            "input_root": str(root),
            "ok_count": 0,
            "skipped": 0,
            "failed": 0,
            "items": [],
            "log": "\n".join(log_lines),
        }

    wanted = list(steps or DEFAULT_STEPS)
    episodes = sorted(
        (p for p in root.glob("episode_*") if p.is_dir()),
        key=lambda p: p.name,
    )
    log_lines.append(f"found {len(episodes)} episode_* under {root}")

    items: list[dict[str, Any]] = []
    ok_count = 0
    skipped = 0
    failed = 0

    for ep in episodes:
        item: dict[str, Any] = {
            "episode": str(ep),
            "name": ep.name,
            "status": "pending",
        }
        export_dir = ep / "export"
        insp = inspect_episode(ep)

        if insp.get("provisional") is True:
            item["status"] = "skipped"
            item["reason"] = "provisional"
            item["error"] = insp.get("error") or "manifest still provisional"
            skipped += 1
            log_lines.append(f"SKIP {ep.name}: provisional")
            items.append(item)
            continue

        if insp.get("valid") is False and not allow_invalid:
            item["status"] = "skipped"
            item["reason"] = "invalid"
            item["error"] = insp.get("error") or "manifest.valid=false"
            skipped += 1
            log_lines.append(f"SKIP {ep.name}: invalid")
            items.append(item)
            continue

        if export_dir.exists() and not overwrite:
            item["status"] = "skipped"
            item["reason"] = "export_exists"
            skipped += 1
            log_lines.append(f"SKIP {ep.name}: export/ exists (overwrite=false)")
            items.append(item)
            continue

        ep_master = _pick_master_for_episode(insp, preferred=master)
        if not ep_master and (align_clock or "").strip().lower() != "hw_ts":
            # wall align still needs a master agent id
            item["status"] = "failed"
            item["reason"] = "no_master"
            item["error"] = "no master candidate for episode"
            failed += 1
            log_lines.append(f"FAIL {ep.name}: no master")
            items.append(item)
            continue

        if overwrite and export_dir.exists():
            try:
                cleared = clear_episode_export(ep)
                item["cleared_export"] = cleared
                log_lines.append(f"CLEAR {ep.name}/export cleared={cleared.get('cleared')}")
            except Exception as e:  # noqa: BLE001
                item["status"] = "failed"
                item["reason"] = "clear_export_failed"
                item["error"] = f"{type(e).__name__}: {e}"
                failed += 1
                log_lines.append(f"FAIL {ep.name}: clear export: {item['error']}")
                items.append(item)
                continue

        log_lines.append(f"==> {ep.name} master={ep_master}")
        try:
            result = run_postprocess(
                episode=ep,
                steps=wanted,
                align=align,
                master=ep_master or master,
                master_hz=master_hz,
                align_clock=align_clock,
                primary_camera=primary_camera,
                require=require,
                max_match_dt=max_match_dt,
                trim=trim,
                materialize=materialize,
                camera_map=camera_map,
                allow_invalid=allow_invalid,
            )
        except Exception as e:  # noqa: BLE001
            item["status"] = "failed"
            item["reason"] = "exception"
            item["error"] = f"{type(e).__name__}: {e}"
            item["traceback"] = traceback.format_exc()
            failed += 1
            log_lines.append(f"FAIL {ep.name}: {item['error']}")
            items.append(item)
            continue

        item["result"] = {
            "ok": result.get("ok"),
            "error": result.get("error"),
            "steps": [
                {"step": r.get("step"), "ok": r.get("ok"), "error": r.get("error")}
                for r in (result.get("results") or [])
                if isinstance(r, dict)
            ],
        }
        if result.get("ok"):
            item["status"] = "ok"
            ok_count += 1
            log_lines.append(f"ok {ep.name}")
        else:
            item["status"] = "failed"
            item["reason"] = "postprocess_failed"
            item["error"] = result.get("error")
            failed += 1
            log_lines.append(f"FAIL {ep.name}: {result.get('error')}")
            if result.get("log"):
                log_lines.append(str(result["log"]))
        items.append(item)

    summary_ok = failed == 0
    out = {
        "ok": summary_ok,
        "input_root": str(root),
        "overwrite": bool(overwrite),
        "allow_invalid": bool(allow_invalid),
        "steps": wanted,
        "ok_count": ok_count,
        "skipped": skipped,
        "failed": failed,
        "total": len(episodes),
        "items": items,
        "log": "\n".join(log_lines),
    }
    if not summary_ok:
        out["error"] = f"{failed} episode(s) failed"
    return out


def pack_hik_dataset_root(
    *,
    input_root: str | Path,
    output_root: str | Path,
    skip_invalid: bool = True,
) -> dict[str, Any]:
    """UI/CLI wrapper: batch-pack ``episode_*/export/hik_dataset`` trees."""
    from sensors_dcs.export.pack_hik_datasets import pack_hik_datasets

    log_lines = [
        f"==> pack-hik-datasets input={input_root} output={output_root} "
        f"skip_invalid={bool(skip_invalid)}"
    ]
    try:
        result = pack_hik_datasets(
            input_root,
            output_root,
            skip_invalid=bool(skip_invalid),
        )
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log_lines.append(f"FAIL: {err}")
        return {
            "ok": False,
            "error": err,
            "traceback": traceback.format_exc(),
            "log": "\n".join(log_lines),
        }

    log_lines.append(
        f"copied={result.get('copied')} skipped={result.get('skipped')} "
        f"errors={result.get('errors')} warnings={result.get('warnings')}"
    )
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        st = item.get("status")
        ep = item.get("episode")
        if st == "copied":
            extra = ""
            if item.get("warning"):
                extra = f" warn={item.get('warning')}"
            log_lines.append(
                f"OK {ep} -> {item.get('index')}{extra}"
            )
        elif st == "skipped":
            log_lines.append(f"SKIP {ep}: {item.get('reason')}")
        elif st == "error":
            log_lines.append(f"FAIL {ep}: {item.get('error')}")

    out = dict(result)
    out["log"] = "\n".join(log_lines)
    if not out.get("ok") and not out.get("error"):
        out["error"] = "pack failed"
    return out


def compose_raw_video(
    *,
    episode: str | Path,
    master_camera: str | None = None,
    fps: float | None = None,
    out_name: str = "raw_grid.mp4",
) -> dict[str, Any]:
    """Compose a camera-grid MP4 from raw episode JPEGs (independent of Step 1–3)."""
    from sensors_dcs.export.raw_grid_video import (
        load_raw_camera_frames,
        write_raw_episode_grid_video,
    )
    from sensors_dcs.export.timeline import resolve_episode_dir

    try:
        ep = resolve_episode_dir(episode)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "episode": str(episode)}

    log_lines = [f"==> raw-grid-video episode={ep}"]
    try:
        cams = load_raw_camera_frames(ep)
        if not cams:
            raise ValueError(f"no raw camera JPEG frames under {ep / 'cameras'}")
        cam_counts = {k: len(v) for k, v in cams.items()}
        log_lines.append(f"cameras={cam_counts}")
        out = write_raw_episode_grid_video(
            ep,
            out_name=out_name,
            fps=fps,
            master_camera=master_camera,
        )
        meta = {
            "video": str(out),
            "video_name": out.name,
            "cameras": cam_counts,
            "size_bytes": out.stat().st_size,
        }
        log_lines.append(f"ok wrote {out}")
        return {
            "ok": True,
            "episode": str(ep),
            "meta": meta,
            "log": "\n".join(log_lines),
        }
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log_lines.append(f"FAIL: {err}")
        return {
            "ok": False,
            "error": err,
            "episode": str(ep),
            "traceback": traceback.format_exc(),
            "log": "\n".join(log_lines),
        }
