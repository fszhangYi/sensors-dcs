from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from sensors_dcs.export.timeline import load_episode, resolve_episode_dir

from sensors_dcs.export.parquet_io import read_parquet, require_pandas, write_parquet

TrimMode = Literal["none", "start", "end", "both"]
FilterFormat = Literal["parquet", "csv"]


def parse_max_match_dt(spec: str | None, *, default: float = 0.033) -> tuple[float, dict[str, float]]:
    """Parse global or per-agent max match_dt in seconds."""
    per_agent: dict[str, float] = {}
    if not spec or not str(spec).strip():
        return default, per_agent
    text = str(spec).strip()
    if ":" not in text and "," not in text:
        return float(text), per_agent
    global_default = default
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            aid, val = part.split(":", 1)
            per_agent[aid.strip()] = float(val.strip())
        else:
            global_default = float(part)
    return global_default, per_agent


def parse_require_list(spec: str | None, df_columns: list[str]) -> list[str]:
    if spec and str(spec).strip():
        return [x.strip() for x in str(spec).split(",") if x.strip()]
    # infer from match_dt columns
    agents = []
    for col in df_columns:
        if col.endswith(".match_dt"):
            agents.append(col[: -len(".match_dt")])
    return sorted(set(agents))


def _read_export_meta(ep_dir: Path) -> dict[str, Any]:
    path = ep_dir / "export" / "export_meta.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def resolve_master(df, export_meta: dict[str, Any], explicit: str | None) -> str | None:
    if explicit:
        return explicit
    align = export_meta.get("align") or {}
    if align.get("master"):
        return str(align["master"])
    for col in df.columns:
        if col.endswith(".match_dt"):
            aid = col[: -len(".match_dt")]
            series = df[col].dropna()
            if len(series) and (series.abs() < 1e-12).all():
                return aid
    return None


def _agent_kind(
    df_columns: list[str],
    agent_id: str,
    *,
    kind_hint: dict[str, str] | None = None,
) -> str:
    if kind_hint and agent_id in kind_hint:
        return str(kind_hint[agent_id])
    if f"{agent_id}.image_relpath" in df_columns or f"{agent_id}.file" in df_columns:
        return "realsense"
    if f"{agent_id}.position_norm" in df_columns:
        return "gripper_read"
    if any(
        c.startswith(f"{agent_id}.j") and c[len(agent_id) + 1 :].isdigit()
        for c in df_columns
    ):
        # Joint-like columns: prefer arm_read when agent_id looks like robot/arm.
        aid = agent_id.lower()
        if "arm" in aid or "robot" in aid or "elite" in aid:
            return "arm_read"
        return "gello"
    return "unknown"


def _kind_hints_from_manifest(manifest: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not manifest:
        return out
    for item in manifest.get("agents") or []:
        aid = item.get("agent_id")
        kind = item.get("kind")
        if aid and kind:
            out[str(aid)] = str(kind)
    return out


def _agents_in_frame(columns: list[str]) -> list[str]:
    skip = {"step", "t_wall", "t_rel", "t_mono"}
    agents: set[str] = set()
    for col in columns:
        if col in skip or "." not in col:
            continue
        agents.add(col.split(".", 1)[0])
    return sorted(agents)


def _joints_from_row(row: Any, agent_id: str, columns: list[str]) -> list[float] | None:
    joints: list[tuple[int, float]] = []
    prefix = f"{agent_id}.j"
    for col in columns:
        if not col.startswith(prefix):
            continue
        suffix = col[len(prefix) :]
        if not suffix.isdigit():
            continue
        val = row.get(col)
        if not _value_present(val):
            return None
        joints.append((int(suffix), float(val)))
    if not joints:
        return None
    joints.sort(key=lambda x: x[0])
    return [v for _, v in joints]


def _value_present(val: Any) -> bool:
    if val is None:
        return False
    try:
        import math

        if isinstance(val, float) and math.isnan(val):
            return False
    except TypeError:
        pass
    if isinstance(val, str) and not val.strip():
        return False
    return True


def row_passes(
    row: Any,
    *,
    agent_id: str,
    kind: str,
    is_master: bool,
    max_dt: float,
    columns: list[str],
) -> tuple[bool, str | None]:
    prefix = f"{agent_id}."
    if kind == "realsense":
        path_col = f"{prefix}image_relpath"
        file_col = f"{prefix}file"
        val = row.get(path_col) if path_col in columns else row.get(file_col)
        if not _value_present(val):
            return False, "missing_image"
        miss_col = f"{prefix}file_missing"
        if miss_col in columns and row.get(miss_col) is True:
            return False, "file_missing"
    elif kind in {"gello", "arm_read"}:
        j0 = f"{prefix}j0"
        if j0 in columns and not _value_present(row.get(j0)):
            return False, "missing_joints"
        elif j0 not in columns:
            any_j = any(c.startswith(prefix) for c in columns)
            if not any_j:
                return False, "missing_joints"
    elif kind == "gripper_read":
        col = f"{prefix}position_norm"
        if col in columns and not _value_present(row.get(col)):
            return False, "missing_gripper"
    else:
        any_col = any(c.startswith(prefix) for c in columns if c != f"{prefix}match_dt")
        if not any_col:
            return False, "missing_data"

    dt_col = f"{prefix}match_dt"
    if not is_master and dt_col in columns:
        dt = row.get(dt_col)
        if not _value_present(dt):
            return False, "missing_match_dt"
        if float(dt) > max_dt:
            return False, "match_dt_exceeded"
    return True, None


def compute_row_mask(
    df,
    *,
    require: list[str],
    master: str | None,
    default_max_dt: float,
    per_agent_max_dt: dict[str, float],
    kind_hint: dict[str, str] | None = None,
) -> tuple[list[bool], dict[str, int]]:
    columns = list(df.columns)
    reasons: dict[str, int] = {}
    mask: list[bool] = []
    for _, row in df.iterrows():
        ok = True
        for aid in require:
            kind = _agent_kind(columns, aid, kind_hint=kind_hint)
            is_master = aid == master
            max_dt = per_agent_max_dt.get(aid, default_max_dt)
            passed, reason = row_passes(
                row,
                agent_id=aid,
                kind=kind,
                is_master=is_master,
                max_dt=max_dt,
                columns=columns,
            )
            if not passed:
                ok = False
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                break
        mask.append(ok)
    return mask, reasons


def apply_trim_indices(mask: list[bool], trim: TrimMode) -> tuple[int, int, int, int]:
    n = len(mask)
    if not n or not any(mask):
        return 0, -1, 0, 0
    first_valid = next(i for i, m in enumerate(mask) if m)
    last_valid = max(i for i, m in enumerate(mask) if m)
    start = first_valid if trim in {"start", "both"} else 0
    end = last_valid if trim in {"end", "both"} else n - 1
    trimmed_start = start if trim in {"start", "both"} else 0
    trimmed_end = (n - 1 - end) if trim in {"end", "both"} else 0
    return start, end, trimmed_start, trimmed_end


def dedupe_consecutive_rows(df, columns: list[str]):
    """Drop consecutive rows where all listed columns are identical (e.g. repeated camera frame)."""
    if df.empty or not columns:
        return df
    cols = [c for c in columns if c in df.columns]
    if not cols:
        return df
    keep = [True]
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        row = df.iloc[i]
        same = all(prev[c] == row[c] for c in cols)
        keep.append(not same)
    return df.iloc[[i for i, k in enumerate(keep) if k]].reset_index(drop=True)


def expand_dedupe_columns(spec: str | None, require: list[str], columns: list[str]) -> list[str]:
    if not spec or not str(spec).strip():
        return []
    out: list[str] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if part.endswith(".*"):
            prefix = part[:-1]
            out.extend(c for c in columns if c.startswith(prefix))
        else:
            out.append(part)
    return out


def tail_after_master(ep_dir: Path, last_t_wall: float) -> dict[str, int]:
    try:
        _, samples = load_episode(ep_dir)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for s in samples:
        if s.t_wall > last_t_wall:
            out[s.agent_id] = out.get(s.agent_id, 0) + 1
    return out


def _resolve_input_path(ep_dir: Path, input_path: str | Path | None) -> Path:
    if input_path:
        p = Path(input_path).expanduser()
        if not p.is_absolute():
            p = ep_dir / p
        return p.resolve()
    export = ep_dir / "export"
    for name in ("timeline_aligned.parquet", "timeline_aligned.csv"):
        cand = export / name
        if cand.is_file():
            return cand.resolve()
    raise FileNotFoundError(
        f"aligned timeline not found under {export}; run export-timeline --align first"
    )


def _load_frame(path: Path):
    if path.suffix.lower() == ".csv":
        pd = require_pandas()
        return pd.read_csv(path)
    return read_parquet(path)


def _write_frame(df, path: Path, fmt: FilterFormat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        write_parquet(df, path, index=False)


def materialize_filtered_episode(
    df,
    *,
    ep_dir: Path,
    filtered_root: Path,
    columns: list[str],
    master: str | None,
    source_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write episode-shaped tree under ``filtered_root`` (states / cameras / manifest)."""
    if filtered_root.exists():
        shutil.rmtree(filtered_root)
    (filtered_root / "states").mkdir(parents=True)
    (filtered_root / "cameras").mkdir(parents=True)

    kind_hints = _kind_hints_from_manifest(source_manifest)
    agents = _agents_in_frame(columns)
    agent_kinds = {aid: _agent_kind(columns, aid, kind_hint=kind_hints) for aid in agents}
    camera_agents = [aid for aid, kind in agent_kinds.items() if kind == "realsense"]
    filtered_file_cols: dict[str, list[str | None]] = {aid: [] for aid in camera_agents}

    state_fps: dict[str, Any] = {}
    cam_index_fps: dict[str, Any] = {}
    written = 0

    def state_fp(aid: str):
        if aid not in state_fps:
            path = filtered_root / "states" / f"{aid}.jsonl"
            state_fps[aid] = path.open("w", encoding="utf-8")
        return state_fps[aid]

    def cam_index_fp(aid: str):
        if aid not in cam_index_fps:
            d = filtered_root / "cameras" / aid
            d.mkdir(parents=True, exist_ok=True)
            cam_index_fps[aid] = (d / "index.jsonl").open("w", encoding="utf-8")
        return cam_index_fps[aid]

    try:
        for _, row in df.iterrows():
            step = int(row["step"])
            t_wall = float(row["t_wall"])
            t_rel = (
                float(row["t_rel"])
                if "t_rel" in columns and _value_present(row.get("t_rel"))
                else None
            )
            name = f"{step:08d}.jpg"

            for aid in agents:
                kind = agent_kinds[aid]
                src_seq = row.get(f"{aid}.seq") if f"{aid}.seq" in columns else None
                src_t = row.get(f"{aid}.t_wall_src") if f"{aid}.t_wall_src" in columns else None
                match_dt = row.get(f"{aid}.match_dt") if f"{aid}.match_dt" in columns else None

                if kind == "realsense":
                    path_col = f"{aid}.image_relpath"
                    file_col = f"{aid}.file"
                    rel = row.get(path_col) if path_col in columns else None
                    if not _value_present(rel) and file_col in columns:
                        fname = row.get(file_col)
                        if _value_present(fname):
                            rel = f"cameras/{aid}/{fname}"
                    dest_rel = f"cameras/{aid}/{name}"
                    if not _value_present(rel):
                        filtered_file_cols[aid].append(None)
                        continue
                    src = ep_dir / str(rel)
                    dst = filtered_root / "cameras" / aid / name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not src.is_file():
                        filtered_file_cols[aid].append(None)
                        continue
                    shutil.copy2(src, dst)
                    filtered_file_cols[aid].append(f"filtered/{dest_rel}")

                    rec = {
                        "agent_id": aid,
                        "sensor_id": aid,
                        "kind": "realsense",
                        "seq": step,
                        "t_wall": t_wall,
                        "t_mono": 0.0,
                        "file": name,
                        "role": row.get(f"{aid}.role") if f"{aid}.role" in columns else None,
                        "serial": row.get(f"{aid}.serial") if f"{aid}.serial" in columns else None,
                        "dry_run": None,
                        "src_seq": int(src_seq) if _value_present(src_seq) else None,
                        "src_t_wall": float(src_t) if _value_present(src_t) else None,
                        "match_dt": float(match_dt) if _value_present(match_dt) else None,
                    }
                    cam_index_fp(aid).write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    continue

                if kind in {"gello", "arm_read"}:
                    joints = _joints_from_row(row, aid, columns)
                    if joints is None:
                        continue
                    payload: dict[str, Any] = {"joints_rad": joints, "dry_run": None}
                elif kind == "gripper_read":
                    pos = (
                        row.get(f"{aid}.position_norm")
                        if f"{aid}.position_norm" in columns
                        else None
                    )
                    if not _value_present(pos):
                        continue
                    raw = (
                        row.get(f"{aid}.position_raw")
                        if f"{aid}.position_raw" in columns
                        else None
                    )
                    payload = {
                        "position_norm": float(pos),
                        "position_raw": float(raw) if _value_present(raw) else None,
                        "dry_run": None,
                    }
                else:
                    continue

                if _value_present(src_seq):
                    payload["src_seq"] = int(src_seq)
                if _value_present(src_t):
                    payload["src_t_wall"] = float(src_t)
                if _value_present(match_dt):
                    payload["match_dt"] = float(match_dt)
                if t_rel is not None:
                    payload["t_rel"] = t_rel

                rec = {
                    "agent_id": aid,
                    "sensor_id": aid,
                    "kind": kind,
                    "seq": step,
                    "t_wall": t_wall,
                    "t_mono": 0.0,
                    "payload": payload,
                }
                state_fp(aid).write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    finally:
        for fp in state_fps.values():
            try:
                fp.close()
            except Exception:  # noqa: BLE001
                pass
        for fp in cam_index_fps.values():
            try:
                fp.close()
            except Exception:  # noqa: BLE001
                pass

    for aid, paths in filtered_file_cols.items():
        df[f"{aid}.filtered_file"] = paths

    t_start = float(df["t_wall"].iloc[0]) if len(df) else None
    t_end = float(df["t_wall"].iloc[-1]) if len(df) else None
    agents_meta: list[dict[str, Any]] = []
    for aid in agents:
        item: dict[str, Any] = {
            "agent_id": aid,
            "kind": agent_kinds[aid],
            "hz_target": None,
        }
        if source_manifest:
            for src in source_manifest.get("agents") or []:
                if src.get("agent_id") == aid:
                    item["sensor_id"] = src.get("sensor_id")
                    item["hz_target"] = src.get("hz_target")
                    break
        agents_meta.append(item)

    manifest = {
        "site": (source_manifest or {}).get("site"),
        "episode_index": (source_manifest or {}).get("episode_index"),
        "t_start": t_start,
        "t_end": t_end,
        "duration_s": (t_end - t_start) if t_start is not None and t_end is not None else None,
        "written": written,
        "dropped": 0,
        "agents": agents_meta,
        "valid": True,
        "format": "dcs_episode_v1",
        "derived_from": ep_dir.name,
        "align_master": master,
        "rows": len(df),
        "note": "Filtered / time-aligned materialization of source episode (step == seq).",
    }
    (filtered_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "filtered_root": str(filtered_root.relative_to(ep_dir))
        if filtered_root.is_relative_to(ep_dir)
        else str(filtered_root),
        "agents": agent_kinds,
        "written": written,
        "rows": len(df),
    }


# Back-compat alias used by older tests / callers
def materialize_images(
    df,
    *,
    ep_dir: Path,
    out_dir: Path,
    require: list[str],
    columns: list[str],
) -> None:
    del require
    filtered_root = out_dir / "filtered"
    source_manifest = None
    try:
        source_manifest, _ = load_episode(ep_dir)
    except Exception:  # noqa: BLE001
        pass
    materialize_filtered_episode(
        df,
        ep_dir=ep_dir,
        filtered_root=filtered_root,
        columns=columns,
        master=None,
        source_manifest=source_manifest,
    )


def filter_episode_timeline(
    ep_dir: str | Path,
    *,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    require: str | None = None,
    master: str | None = None,
    max_match_dt: str | None = None,
    trim: TrimMode = "both",
    materialize: bool = False,
    dedupe: str | None = None,
    fmt: FilterFormat = "parquet",
) -> dict[str, Any]:
    """Filter aligned wide table; assign step 0..N-1."""
    root = resolve_episode_dir(ep_dir)
    in_path = _resolve_input_path(root, input_path)
    export_meta = _read_export_meta(root)
    source_manifest: dict[str, Any] | None = None
    try:
        source_manifest, _ = load_episode(root)
    except Exception:  # noqa: BLE001
        pass
    kind_hint = _kind_hints_from_manifest(source_manifest)
    df = _load_frame(in_path)
    if df.empty:
        raise ValueError("aligned timeline is empty")

    default_dt, per_agent_dt = parse_max_match_dt(max_match_dt)
    require_agents = parse_require_list(require, list(df.columns))
    if not require_agents:
        raise ValueError("no agents to require; pass --require or use aligned table with match_dt columns")

    master_id = resolve_master(df, export_meta, master)
    mask, drop_reasons = compute_row_mask(
        df,
        require=require_agents,
        master=master_id,
        default_max_dt=default_dt,
        per_agent_max_dt=per_agent_dt,
        kind_hint=kind_hint,
    )
    rows_in = len(df)
    start, end, trimmed_start, trimmed_end = apply_trim_indices(mask, trim)
    if end < start:
        filtered = df.iloc[0:0].copy()
    else:
        sub = df.iloc[start : end + 1].reset_index(drop=True)
        sub_mask = mask[start : end + 1]
        filtered = sub.iloc[[i for i, m in enumerate(sub_mask) if m]].reset_index(drop=True)

    dedupe_cols = expand_dedupe_columns(dedupe, require_agents, list(filtered.columns))
    deduped_rows = 0
    if dedupe_cols and len(filtered) > 1:
        before = len(filtered)
        filtered = dedupe_consecutive_rows(filtered, dedupe_cols)
        deduped_rows = before - len(filtered)

    filtered = filtered.copy()
    filtered.insert(0, "step", range(len(filtered)))

    out_path = (
        Path(output_path).expanduser().resolve()
        if output_path
        else (root / "export" / f"timeline_filtered.{fmt if fmt != 'parquet' else 'parquet'}")
    )
    if not output_path and fmt == "csv":
        out_path = root / "export" / "timeline_filtered.csv"

    out_dir = out_path.parent
    materialize_info = None
    if materialize and len(filtered) > 0:
        materialize_info = materialize_filtered_episode(
            filtered,
            ep_dir=root,
            filtered_root=out_dir / "filtered",
            columns=list(filtered.columns),
            master=master_id,
            source_manifest=source_manifest,
        )

    _write_frame(filtered, out_path, fmt)

    last_t = float(filtered["t_wall"].max()) if len(filtered) else float("nan")
    tail = tail_after_master(root, last_t) if len(filtered) else {}

    meta = {
        "source_episode": root.name,
        "source_path": str(root),
        "input": str(in_path.relative_to(root)) if in_path.is_relative_to(root) else str(in_path),
        "output": str(out_path.relative_to(root)) if out_path.is_relative_to(root) else str(out_path),
        "master": master_id,
        "require": require_agents,
        "max_match_dt": {"default": default_dt, **per_agent_dt},
        "trim": trim,
        "dedupe": dedupe_cols or None,
        "deduped_rows": deduped_rows,
        "rows_in": rows_in,
        "rows_out": len(filtered),
        "step_range": [0, len(filtered) - 1] if len(filtered) else None,
        "trimmed_start": trimmed_start,
        "trimmed_end": trimmed_end,
        "last_master_t_wall": last_t if len(filtered) else None,
        "tail_after_master": tail,
        "drop_reasons": drop_reasons,
        "materialize": materialize,
        "materialize_info": materialize_info,
    }
    meta_path = out_dir / "filter_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta["output_dir"] = str(out_dir)
    meta["filter_meta"] = str(meta_path)
    return meta
