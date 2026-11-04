"""HTTP / UI helpers for offline postprocess (Steps 1–3 + Step 4 standard export)."""

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
# Thin M2: optional; never included in DEFAULT_STEPS / quick-collect.
OPTIONAL_STEPS = ("traj-quality",)
ALL_STEPS = DEFAULT_STEPS + OPTIONAL_STEPS
# Steps that read raw episode data and must still run after a mid-pipeline failure
# (e.g. filter keep=0 → hik fails, but traj-quality should still write sidecar).
INDEPENDENT_STEPS = frozenset({"traj-quality"})
DEFAULT_RATE_POLICY_NAME = "fixed_5hz.json"


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


def resolve_camera_map_path(camera_map: str | Path | None) -> str | None:
    """Return an existing camera-map YAML path, or the best default candidate.

    Packaged-desktop localStorage often keeps a stale absolute path under another
    machine's ``Downloads\\...\\_internal\\configs\\hik_camera_map.yaml``. When
    that file is missing, fall back to ``default_camera_map_candidates()``.
    """
    raw = str(camera_map or "").strip()
    if raw:
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if p.is_file():
            return str(p)
    cands = default_camera_map_candidates()
    return cands[0] if cands else None


def default_rate_policy_candidates() -> list[str]:
    """Prefer launch-pwd / repo rate-policy JSONs."""
    from sensors_dcs.paths import capture_launch_cwd

    capture_launch_cwd()
    cwd = launch_cwd()
    out: list[str] = []
    seen: set[str] = set()
    dirs = (
        cwd / "configs" / "rate_policies",
        project_root() / "configs" / "rate_policies",
        user_data_dir() / "configs" / "rate_policies",
    )
    for d in dirs:
        try:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.json")):
                if not p.is_file():
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                out.append(key)
        except OSError:
            continue
    return out


def default_rate_policy_path() -> str:
    """Prefer ``fixed_5hz.json`` among candidates (else first / empty)."""
    cands = default_rate_policy_candidates()
    for p in cands:
        if Path(p).name == DEFAULT_RATE_POLICY_NAME:
            return p
    return cands[0] if cands else ""


def postprocess_defaults(
    *,
    save_dir: str | Path | None = None,
    tcp_xyz: list[float] | tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    from sensors_dcs.arm_pose import get_tcp_xyz, normalize_tcp_xyz

    maps = default_camera_map_candidates()
    rate_cands = default_rate_policy_candidates()
    rate_default = default_rate_policy_path()
    try:
        tcp = list(normalize_tcp_xyz(tcp_xyz if tcp_xyz is not None else get_tcp_xyz()))
    except ValueError:
        tcp = list(get_tcp_xyz())
    return {
        "align": DEFAULT_ALIGN,
        "master": DEFAULT_MASTER,
        "master_hz": DEFAULT_MASTER_HZ,
        "rate_policy": rate_default,
        "rate_policy_candidates": rate_cands,
        "align_clock": DEFAULT_ALIGN_CLOCK,
        "primary_camera": DEFAULT_PRIMARY_CAMERA,
        "require": DEFAULT_REQUIRE,
        "max_match_dt": DEFAULT_MAX_MATCH_DT,
        "trim": DEFAULT_TRIM,
        "materialize": True,
        "camera_map": maps[0] if maps else "",
        "camera_map_candidates": maps,
        "allow_invalid": False,
        "tcp_xyz": tcp,
        "steps": list(DEFAULT_STEPS),
        "optional_steps": list(OPTIONAL_STEPS),
        "standard_formats": _standard_format_defaults(),
        "episodes": list_episodes(save_dir) if save_dir else [],
        "sessions": list_sessions(save_dir) if save_dir else [],
        "save_dir": str(Path(save_dir).expanduser().resolve()) if save_dir else None,
    }


def _standard_format_defaults() -> dict[str, Any]:
    from sensors_dcs.export.standard_formats import list_standard_formats

    formats = list_standard_formats()
    # Prefer LeRobot v2.1 checked by default when available.
    selected = [
        f["id"]
        for f in formats
        if f.get("id") == "lerobot_v21" and f.get("available") != "false"
    ]
    if not selected and formats:
        selected = [formats[0]["id"]]
    return {"choices": formats, "selected": selected}


def run_standard_export(
    *,
    episode: str | Path,
    formats: list[str] | str | None = None,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    fps: float | None = None,
) -> dict[str, Any]:
    """Step 4 single-episode export (force overwrite)."""
    from sensors_dcs.export.standard_formats import export_standard_episode

    cam = resolve_camera_map_path(camera_map)
    return export_standard_episode(
        episode,
        formats=formats or ["lerobot_v21"],
        camera_map_yaml=cam,
        allow_invalid=allow_invalid,
        fps=fps,
    )


def run_standard_export_root(
    *,
    input_root: str | Path,
    formats: list[str] | str | None = None,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    fps: float | None = None,
) -> dict[str, Any]:
    """Step 4 batch export over ``episode_*`` (force overwrite)."""
    from sensors_dcs.export.standard_formats import export_standard_root

    cam = resolve_camera_map_path(camera_map)
    return export_standard_root(
        input_root,
        formats=formats or ["lerobot_v21"],
        camera_map_yaml=cam,
        allow_invalid=allow_invalid,
        fps=fps,
    )


def list_sessions(save_dir: str | Path | None) -> list[dict[str, Any]]:
    """List multi-station session registries under ``save_dir/sessions/``."""
    from sensors_dcs.session_registry import list_session_docs

    return list_session_docs(save_dir)


def inspect_session(
    *,
    save_dir: str | Path | None,
    session_id: str,
    remaps: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load session.json and check whether each participant episode is readable."""
    from sensors_dcs.session_registry import (
        inspect_session_paths,
        read_session_registry,
    )

    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "empty session_id", "session_id": None}
    if not save_dir:
        return {"ok": False, "error": "empty save_dir", "session_id": sid}
    root = str(Path(save_dir).expanduser().resolve())
    doc = read_session_registry(root, sid)
    if not doc:
        return {
            "ok": False,
            "error": f"session registry not found: {sid}",
            "session_id": sid,
            "save_dir": root,
        }
    health = inspect_session_paths(
        doc, remaps=remaps, save_dir=root, session_id=sid
    )
    return {
        "ok": True,
        "session_id": sid,
        "save_dir": root,
        "doc": doc,
        "health": health,
        "all_present": bool(health.get("all_present")),
    }


def run_postprocess_session(
    *,
    save_dir: str | Path,
    session_id: str,
    remaps: dict[str, str] | None = None,
    master_station_id: str | None = None,
    master_agent: str | None = None,
    max_match_dt: float | None = 0.033,
    also_per_station: bool = False,
    write_tau_table: bool = False,
    steps: list[str] | None = None,
    align: str = DEFAULT_ALIGN,
    master: str = DEFAULT_MASTER,
    master_hz: float | None = DEFAULT_MASTER_HZ,
    rate_policy: str | None = None,
    align_clock: str = DEFAULT_ALIGN_CLOCK,
    primary_camera: str = DEFAULT_PRIMARY_CAMERA,
    require: str = DEFAULT_REQUIRE,
    trim: str = DEFAULT_TRIM,
    materialize: bool = True,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    tcp_xyz: list[float] | tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """Federate multi-station episodes then run the same 3-step postprocess.

    1. Build ``sessions/<id>/merged/episode_XXXXX/`` (τ→common ``t_wall``, slave
       agents prefixed ``{station_id}__…``).
    2. Run ``run_postprocess`` on that episode → ``export/`` + hik_dataset.
    3. Optional: also_per_station / write_tau_table.
    """
    from sensors_dcs.export.session_federate import build_federated_episode
    from sensors_dcs.session_registry import resolve_participant_episode_path

    root = str(Path(save_dir).expanduser().resolve())
    insp = inspect_session(save_dir=root, session_id=session_id, remaps=remaps)
    if not insp.get("ok"):
        return {**insp, "results": []}
    if not insp.get("all_present"):
        return {
            "ok": False,
            "session_id": session_id,
            "save_dir": root,
            "error": "missing participant episode dirs",
            "health": insp.get("health"),
            "results": [],
        }

    federate = build_federated_episode(
        save_dir=root,
        session_id=session_id,
        remaps=remaps,
        master_station_id=master_station_id,
    )
    if not federate.get("ok"):
        return {
            "ok": False,
            "session_id": session_id,
            "save_dir": root,
            "federate": federate,
            "error": federate.get("error"),
            "health": insp.get("health"),
            "results": [],
        }

    # Cross-station HW clocks are not aligned — prefer wall on federated episode.
    clock = (align_clock or DEFAULT_ALIGN_CLOCK).strip().lower()
    if clock == "hw_ts":
        clock = "wall"

    fed_ep = str(federate["episode_path"])
    try:
        post = run_postprocess(
            episode=fed_ep,
            steps=steps,
            align=align,
            master=master_agent or master,
            master_hz=master_hz,
            rate_policy=rate_policy,
            align_clock=clock,
            primary_camera=primary_camera,
            require=require,
            max_match_dt=str(max_match_dt)
            if max_match_dt is not None
            else DEFAULT_MAX_MATCH_DT,
            trim=trim,
            materialize=materialize,
            camera_map=camera_map,
            allow_invalid=allow_invalid,
            tcp_xyz=tcp_xyz,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        return {
            "ok": False,
            "session_id": session_id,
            "save_dir": root,
            "federate": {
                "ok": True,
                "episode_path": fed_ep,
                "meta_path": federate.get("meta_path"),
                "written_states": federate.get("written_states"),
                "written_frames": federate.get("written_frames"),
            },
            "episode": fed_ep,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
            "health": insp.get("health"),
            "results": [],
        }

    per_station: list[dict[str, Any]] = []
    if also_per_station:
        doc = insp.get("doc") or {}
        for p in doc.get("participants") or []:
            if not isinstance(p, dict):
                continue
            ep = resolve_participant_episode_path(
                p, remaps=remaps, save_dir=root, session_id=session_id
            )
            if ep is None:
                per_station.append(
                    {
                        "station_id": p.get("station_id"),
                        "ok": False,
                        "error": "episode path not found",
                    }
                )
                continue
            r = run_postprocess(
                episode=ep,
                steps=steps,
                align=align,
                master=master,
                master_hz=master_hz,
                rate_policy=rate_policy,
                align_clock=align_clock,
                primary_camera=primary_camera,
                require=require,
                max_match_dt=str(max_match_dt)
                if max_match_dt is not None
                else DEFAULT_MAX_MATCH_DT,
                trim=trim,
                materialize=materialize,
                camera_map=camera_map,
                allow_invalid=allow_invalid,
                tcp_xyz=tcp_xyz,
            )
            per_station.append(
                {
                    "station_id": p.get("station_id"),
                    "episode": str(ep),
                    **r,
                }
            )

    tau_merge: dict[str, Any] | None = None
    if write_tau_table:
        from sensors_dcs.export.session_merge import merge_session_tau

        tau_merge = merge_session_tau(
            save_dir=root,
            session_id=session_id,
            remaps=remaps,
            master_station_id=master_station_id,
            master_agent=master_agent or master,
            max_match_dt=max_match_dt,
        )

    return {
        "ok": bool(post.get("ok")),
        "session_id": session_id,
        "save_dir": root,
        "federate": {
            "ok": True,
            "episode_path": fed_ep,
            "meta_path": federate.get("meta_path"),
            "written_states": federate.get("written_states"),
            "written_frames": federate.get("written_frames"),
        },
        "postprocess": post,
        "episode": fed_ep,
        "results": post.get("results") or [],
        "per_station": per_station,
        "tau_merge": tau_merge,
        "error": None if post.get("ok") else post.get("error"),
        "health": insp.get("health"),
    }


def resolve_export_tcp_xyz(
    tcp_xyz: list[float] | tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """UI/CLI override, else active DCS YAML ``tcp_xyz`` (via arm_pose)."""
    from sensors_dcs.arm_pose import get_tcp_xyz, normalize_tcp_xyz

    if tcp_xyz is None:
        return get_tcp_xyz()
    return normalize_tcp_xyz(tcp_xyz)


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
    from sensors_dcs.export.agent_ids import suggest_master_agent

    suggested = suggest_master_agent(manifest)
    if suggested and suggested in candidates:
        return suggested
    kind_by_id: dict[str, str] = {}
    for item in manifest.get("agents") or []:
        if isinstance(item, dict) and item.get("agent_id"):
            kind_by_id[str(item["agent_id"])] = str(item.get("kind") or "")
    for prefer in ("realsense", "gello", "arm", "arm_read"):
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
        "suggested_require": None,
        "suggested_primary_camera": None,
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
    try:
        from sensors_dcs.export.agent_ids import (
            suggest_primary_camera,
            suggest_require_agents,
        )

        req = suggest_require_agents(manifest)
        base["suggested_require"] = ",".join(req) if req else None
        base["suggested_primary_camera"] = suggest_primary_camera(manifest)
    except Exception:  # noqa: BLE001
        base["suggested_require"] = None
        base["suggested_primary_camera"] = None

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


GRIPPER_SERIES_MAX_POINTS = 800
_DEFAULT_GRIPPER_KINDS = ("gripper_read", "gripper_write")
_GRIPPER_FIELD_BY_KIND = {
    "gripper_read": "position_norm",
    "gripper_write": "command_position_norm",
}


def _downsample_progress_points(
    points: list[dict[str, float]],
    *,
    max_points: int = GRIPPER_SERIES_MAX_POINTS,
) -> list[dict[str, float]]:
    """Keep first/last and evenly sample by index when over ``max_points``."""
    n = len(points)
    if n <= max_points or max_points < 2:
        return points
    out: list[dict[str, float]] = []
    last_idx = -1
    for i in range(max_points):
        idx = int(round(i * (n - 1) / (max_points - 1)))
        if idx == last_idx:
            continue
        out.append(points[idx])
        last_idx = idx
    return out


def load_episode_gripper_series(
    episode: str | Path,
    *,
    kinds: list[str] | tuple[str, ...] | None = None,
    max_points: int = GRIPPER_SERIES_MAX_POINTS,
) -> dict[str, Any]:
    """Load gripper read/write curves vs episode time-progress percent.

    Progress uses wall span of **all** ``states/*.jsonl`` samples (not gripper-only).
    Does not scan cameras. Returns ``{ok, episode, episode_label, series, error}``.
    """
    from sensors_dcs.export.timeline import resolve_episode_dir

    wanted = tuple(kinds) if kinds is not None else _DEFAULT_GRIPPER_KINDS
    kind_set = {str(k).strip() for k in wanted if str(k).strip()}
    kind_set &= set(_GRIPPER_FIELD_BY_KIND)
    raw = str(episode or "").strip()
    empty: dict[str, Any] = {
        "ok": False,
        "episode": raw or None,
        "episode_label": None,
        "series": [],
        "error": None,
    }
    if not raw:
        empty["error"] = "empty episode path"
        return empty
    try:
        ep = resolve_episode_dir(raw)
    except Exception as exc:  # noqa: BLE001
        empty["error"] = f"{type(exc).__name__}: {exc}"
        return empty
    empty["episode"] = str(ep)
    empty["episode_label"] = ep.name
    if not ep.is_dir():
        empty["error"] = f"episode directory not found: {ep}"
        return empty

    states_dir = ep / "states"
    if not states_dir.is_dir():
        return {
            "ok": True,
            "episode": str(ep),
            "episode_label": ep.name,
            "series": [],
            "error": None,
        }

    # Lightweight parse: only need t_wall + gripper payloads (no camera load).
    from sensors_dcs.export.timeline import _flatten_state_row, _read_jsonl

    samples = []
    for path in sorted(states_dir.glob("*.jsonl")):
        try:
            for row in _read_jsonl(path):
                samples.append(_flatten_state_row(row))
        except Exception as exc:  # noqa: BLE001
            empty["error"] = f"unreadable {path.name}: {exc}"
            return empty

    if not samples:
        return {
            "ok": True,
            "episode": str(ep),
            "episode_label": ep.name,
            "series": [],
            "error": None,
        }

    t0 = min(s.t_wall for s in samples)
    t_end = max(s.t_wall for s in samples)
    span = t_end - t0

    # Group gripper samples by (agent_id, kind).
    buckets: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for s in samples:
        if s.kind not in kind_set:
            continue
        field = _GRIPPER_FIELD_BY_KIND[s.kind]
        raw_v = s.fields.get(field)
        if raw_v is None:
            continue
        try:
            v = float(raw_v)
        except (TypeError, ValueError):
            continue
        if not (v == v):  # NaN
            continue
        key = (s.agent_id, s.kind)
        buckets.setdefault(key, []).append((float(s.t_wall), v))

    series_out: list[dict[str, Any]] = []
    for (agent_id, kind), pts in sorted(buckets.items(), key=lambda x: (x[0][0], x[0][1])):
        pts.sort(key=lambda p: p[0])
        field = _GRIPPER_FIELD_BY_KIND[kind]
        if span <= 1e-12:
            points = [{"pct": 0.0, "v": pts[0][1]}] if pts else []
        else:
            points = [
                {"pct": max(0.0, min(100.0, (t - t0) / span * 100.0)), "v": v}
                for t, v in pts
            ]
        points = _downsample_progress_points(points, max_points=max_points)
        series_out.append(
            {
                "episode": str(ep),
                "episode_label": ep.name,
                "agent_id": agent_id,
                "kind": kind,
                "field": field,
                "t0": float(t0),
                "t_end": float(t_end),
                "points": points,
            }
        )

    return {
        "ok": True,
        "episode": str(ep),
        "episode_label": ep.name,
        "series": series_out,
        "error": None,
    }


def load_gripper_series_batch(
    paths: list[str] | None,
    *,
    kinds: list[str] | tuple[str, ...] | None = None,
    max_points: int = GRIPPER_SERIES_MAX_POINTS,
) -> dict[str, Any]:
    """Load gripper series for multiple episodes; per-path errors collected."""
    series: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for raw in paths or []:
        p = str(raw or "").strip()
        if not p:
            errors.append({"path": "", "error": "empty episode path"})
            continue
        one = load_episode_gripper_series(p, kinds=kinds, max_points=max_points)
        if not one.get("ok"):
            errors.append({"path": p, "error": str(one.get("error") or "load failed")})
            continue
        series.extend(one.get("series") or [])
    return {"ok": True, "series": series, "errors": errors}


def run_postprocess(
    *,
    episode: str | Path,
    steps: list[str] | None = None,
    align: str = DEFAULT_ALIGN,
    master: str = DEFAULT_MASTER,
    master_hz: float | None = DEFAULT_MASTER_HZ,
    rate_policy: str | None = None,
    align_clock: str = DEFAULT_ALIGN_CLOCK,
    primary_camera: str = DEFAULT_PRIMARY_CAMERA,
    require: str = DEFAULT_REQUIRE,
    max_match_dt: str = DEFAULT_MAX_MATCH_DT,
    trim: str = DEFAULT_TRIM,
    materialize: bool = True,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    tcp_xyz: list[float] | tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """Run selected postprocess steps sequentially.

    Dependent steps are skipped after the first failure. ``INDEPENDENT_STEPS``
    (e.g. ``traj-quality``) still run so raw-state sidecars are written even when
    filter keep=0 causes hik to fail.
    """
    from sensors_dcs.export.filter import filter_episode_timeline
    from sensors_dcs.export.hik_dataset import export_hik_dataset
    from sensors_dcs.export.timeline import (
        export_episode_timeline,
        resolve_episode_dir,
        wait_episode_manifest_ready,
    )

    ep = resolve_episode_dir(episode)
    wanted = list(steps or DEFAULT_STEPS)
    unknown = [s for s in wanted if s not in ALL_STEPS]
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
    require_s = (require or DEFAULT_REQUIRE).strip() or DEFAULT_REQUIRE
    master_s = (master or DEFAULT_MASTER).strip() or DEFAULT_MASTER

    # Resolve role/kind aliases against this episode's manifest (multi-station safe).
    try:
        import json as _json

        from sensors_dcs.export.agent_ids import (
            AgentResolveError,
            agents_index_from_manifest,
            resolve_agent_token,
            resolve_require_list,
            suggest_master_agent,
            suggest_primary_camera,
            suggest_require_agents,
        )

        man_path = ep / "manifest.json"
        man: dict[str, Any] | None = None
        if man_path.is_file():
            man = _json.loads(man_path.read_text(encoding="utf-8"))
        if isinstance(man, dict):
            agents = agents_index_from_manifest(man)
            sug = suggest_require_agents(man)
            federated = bool(man.get("federated"))
            slave_ids = [a["id"] for a in agents if "__" in str(a.get("id") or "")]
            # Default / empty → suggest from this episode (federated = all stations' cams).
            # Federated + require that covers no slave agent → treat as stale single-station
            # list (UI often overwrites #ppRequire when inspecting master episode_*).
            use_suggested = bool(sug) and (
                require_s == DEFAULT_REQUIRE
                or not require_s.strip()
            )
            if federated and sug and slave_ids and not use_suggested:
                tokens = {x.strip() for x in require_s.split(",") if x.strip()}
                covers_slave = any(
                    t in slave_ids
                    or any(sid == t or sid.endswith("__" + t) for sid in slave_ids)
                    for t in tokens
                )
                if not covers_slave:
                    use_suggested = True
            if use_suggested:
                require_s = ",".join(sug)
            else:
                resolved, _ = resolve_require_list(require_s, manifest=man)
                require_s = ",".join(resolved)
            if master_s == DEFAULT_MASTER:
                master_s = suggest_master_agent(man) or master_s
            else:
                master_s = resolve_agent_token(master_s, agents=agents)
            if primary == DEFAULT_PRIMARY_CAMERA:
                primary = suggest_primary_camera(man) or primary
            else:
                primary = resolve_agent_token(primary, agents=agents)
    except AgentResolveError as e:
        return {
            "ok": False,
            "error": f"agent resolve failed: {e}",
            "episode": str(ep),
            "results": [],
        }
    except Exception:  # noqa: BLE001
        pass

    cam_map_requested = (camera_map or "").strip() or None
    cam_map = resolve_camera_map_path(cam_map_requested)
    rate_path = (rate_policy or "").strip() or None
    try:
        export_tcp = resolve_export_tcp_xyz(tcp_xyz)
    except ValueError as e:
        return {"ok": False, "error": str(e), "episode": str(ep), "results": []}
    # Prefer rate-policy JSON; fall back to legacy uniform master_hz.
    use_master_hz = None if rate_path else (
        float(master_hz) if master_hz is not None else None
    )
    results: list[dict[str, Any]] = []
    log_lines: list[str] = []
    pipeline_failed = False
    pipeline_error: str | None = None
    if cam_map_requested and cam_map and cam_map != cam_map_requested:
        log_lines.append(
            f"camera_map missing ({cam_map_requested}); using fallback {cam_map}"
        )
    elif cam_map_requested and not cam_map:
        log_lines.append(f"camera_map missing and no fallback found: {cam_map_requested}")

    for step in wanted:
        if pipeline_failed and step not in INDEPENDENT_STEPS:
            results.append(
                {
                    "step": step,
                    "ok": False,
                    "skipped": True,
                    "error": f"skipped after prior failure: {pipeline_error}",
                }
            )
            log_lines.append(f"SKIP {step}: prior failure")
            continue
        log_lines.append(f"==> {step}")
        try:
            if step == "export-timeline":
                meta = export_episode_timeline(
                    ep,
                    align=align,  # type: ignore[arg-type]
                    master=master_s or None,
                    master_hz=use_master_hz,
                    rate_policy=rate_path,
                    allow_invalid=allow_invalid,
                    align_clock=clock,  # type: ignore[arg-type]
                    primary_camera=primary,
                )
            elif step == "filter-timeline":
                meta = filter_episode_timeline(
                    ep,
                    require=require_s or None,
                    master=master_s or None,
                    max_match_dt=max_match_dt or None,
                    trim=trim,  # type: ignore[arg-type]
                    materialize=bool(materialize),
                    allow_invalid=allow_invalid,
                )
            elif step == "traj-quality":
                from sensors_dcs.export.traj_quality import run_traj_quality

                meta = run_traj_quality(ep, write=True)
                if not meta.get("ok"):
                    raise RuntimeError(meta.get("error") or "traj-quality failed")
                log_lines.append(
                    f"traj_quality grade={meta.get('grade')} "
                    f"overspeed={meta.get('overspeed_count')} jump={meta.get('jump_count')}"
                )
            elif step == "export-hik-dataset":
                if not cam_map:
                    raise ValueError(
                        "camera_map is required for export-hik-dataset "
                        "(path to hik_camera_map.yaml). Saved UI path was missing; "
                        "place configs/hik_camera_map.yaml under the app cwd or "
                        "paste a valid path in Step 3."
                    )
                meta = export_hik_dataset(
                    ep,
                    camera_map_yaml=cam_map,
                    allow_invalid=allow_invalid,
                    tcp_xyz=export_tcp,
                )
                log_lines.append(f"tcp_xyz={list(export_tcp)}")
            else:
                raise ValueError(f"unsupported step: {step}")
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
                # Empty keep is not a hard filter exception, but hik will usually fail.
                # Surface it early so logs show why the dependent chain breaks.
                try:
                    rows_out = int(meta.get("rows_out") or 0)
                except (TypeError, ValueError):
                    rows_out = 0
                if rows_out <= 0:
                    log_lines.append(
                        "filter keep=0 (all rows discarded); downstream hik will likely fail; "
                        "independent steps (traj-quality) still run if requested"
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
            if step in INDEPENDENT_STEPS:
                # Independent step failure does not block other independent steps,
                # but still marks the overall run failed.
                pipeline_failed = True
                if pipeline_error is None:
                    pipeline_error = err
                continue
            pipeline_failed = True
            pipeline_error = err
            continue

    if pipeline_failed:
        return {
            "ok": False,
            "error": pipeline_error,
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
    rate_policy: str | None = None,
    align_clock: str = DEFAULT_ALIGN_CLOCK,
    primary_camera: str = DEFAULT_PRIMARY_CAMERA,
    require: str = DEFAULT_REQUIRE,
    max_match_dt: str = DEFAULT_MAX_MATCH_DT,
    trim: str = DEFAULT_TRIM,
    materialize: bool = True,
    camera_map: str | None = None,
    allow_invalid: bool = False,
    overwrite: bool = True,
    tcp_xyz: list[float] | tuple[float, float, float] | None = None,
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
    unknown = [s for s in wanted if s not in ALL_STEPS]
    if unknown:
        return {
            "ok": False,
            "error": f"unknown steps: {unknown}",
            "input_root": str(root),
            "ok_count": 0,
            "skipped": 0,
            "failed": 0,
            "items": [],
            "log": "\n".join(log_lines + [f"unknown steps: {unknown}"]),
        }
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
                rate_policy=rate_policy,
                align_clock=align_clock,
                primary_camera=primary_camera,
                require=require,
                max_match_dt=max_match_dt,
                trim=trim,
                materialize=materialize,
                camera_map=camera_map,
                allow_invalid=allow_invalid,
                tcp_xyz=tcp_xyz,
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
