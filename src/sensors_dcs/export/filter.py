from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from sensors_dcs.export.timeline import load_episode, resolve_episode_dir

TrimMode = Literal["none", "start", "end", "both"]
FilterFormat = Literal["parquet", "csv"]


def _require_pandas():
    try:
        import pandas as pd  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "filter-timeline requires pandas. "
            "Dev: pip install -e '.[export]'. "
            "Desktop exe: rebuild with requirements-desktop.txt (includes pandas/pyarrow)."
        ) from exc
    return pd


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


def _agent_kind(df_columns: list[str], agent_id: str) -> str:
    if f"{agent_id}.image_relpath" in df_columns or f"{agent_id}.file" in df_columns:
        return "realsense"
    if any(c.startswith(f"{agent_id}.j") and c[len(agent_id) + 1 :].isdigit() for c in df_columns):
        return "gello"
    if f"{agent_id}.position_norm" in df_columns:
        return "gripper_read"
    return "unknown"


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
    elif kind == "gello":
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
) -> tuple[list[bool], dict[str, int]]:
    columns = list(df.columns)
    reasons: dict[str, int] = {}
    mask: list[bool] = []
    for _, row in df.iterrows():
        ok = True
        for aid in require:
            kind = _agent_kind(columns, aid)
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
    pd = _require_pandas()
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _write_frame(df, path: Path, fmt: FilterFormat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        df.to_parquet(path, index=False)


def materialize_images(
    df,
    *,
    ep_dir: Path,
    out_dir: Path,
    require: list[str],
    columns: list[str],
) -> None:
    camera_agents = [aid for aid in require if _agent_kind(columns, aid) == "realsense"]
    if not camera_agents:
        camera_agents = sorted(
            {
                col.split(".", 1)[0]
                for col in columns
                if col.endswith(".image_relpath") or col.endswith(".file")
            }
        )
    for aid in camera_agents:
        path_col = f"{aid}.image_relpath"
        file_col = f"{aid}.file"
        filt_col = f"{aid}.filtered_file"
        dest_root = out_dir / "filtered" / "images" / aid
        dest_root.mkdir(parents=True, exist_ok=True)
        filtered_paths: list[str | None] = []
        for _, row in df.iterrows():
            rel = row.get(path_col) if path_col in columns else None
            if not _value_present(rel) and file_col in columns:
                fname = row.get(file_col)
                if _value_present(fname):
                    rel = f"cameras/{aid}/{fname}"
            if not _value_present(rel):
                filtered_paths.append(None)
                continue
            src = ep_dir / str(rel)
            step = int(row["step"])
            name = f"{step:08d}.jpg"
            dst = dest_root / name
            if src.is_file():
                shutil.copy2(src, dst)
                filtered_paths.append(f"filtered/images/{aid}/{name}")
            else:
                filtered_paths.append(None)
        df[filt_col] = filtered_paths


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
    if materialize and len(filtered) > 0:
        materialize_images(
            filtered,
            ep_dir=root,
            out_dir=out_dir,
            require=require_agents,
            columns=list(filtered.columns),
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
    }
    meta_path = out_dir / "filter_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta["output_dir"] = str(out_dir)
    meta["filter_meta"] = str(meta_path)
    return meta
