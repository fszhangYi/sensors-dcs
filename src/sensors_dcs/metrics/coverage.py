"""M3: sensor / timeline coverage rows (not E4 scene-tag coverage).

Reads ``export/filter_meta.json``, ``export/export_meta.json``, and ``manifest.json``.
Episodes without filter_meta are marked ``unprocessed``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "dcs_sensor_coverage_v1"
DEFAULT_KEEP_RATE_MIN = 0.9
DEFAULT_DROP_RATE_MAX = 0.05


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or math.isinf(x):
        return None
    return x


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    p = min(100.0, max(0.0, float(p)))
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def _histogram(values: list[float], edges: list[float]) -> list[dict[str, Any]]:
    """Inclusive left, exclusive right except last bucket closed on the right."""
    if not edges or len(edges) < 2:
        return []
    counts = [0] * (len(edges) - 1)
    for v in values:
        placed = False
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if i == len(edges) - 2:
                if lo <= v <= hi:
                    counts[i] += 1
                    placed = True
                    break
            elif lo <= v < hi:
                counts[i] += 1
                placed = True
                break
        if not placed:
            # Out of range: clamp into ends
            if v < edges[0]:
                counts[0] += 1
            else:
                counts[-1] += 1
    return [
        {"lo": edges[i], "hi": edges[i + 1], "n": counts[i]}
        for i in range(len(edges) - 1)
    ]


def _present_agent_ids(export_meta: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(export_meta, dict):
        return out
    for a in export_meta.get("agents") or []:
        s = str(a).strip()
        if s:
            out.add(s)
    stats = export_meta.get("match_dt_stats")
    if isinstance(stats, dict):
        for key in stats:
            k = str(key)
            if k.endswith(".match_dt"):
                out.add(k[: -len(".match_dt")])
    return out


def _soft_present(req: str, present: set[str]) -> bool:
    if req in present:
        return True
    for a in present:
        if req in a or a.endswith(req) or a.endswith("-" + req):
            return True
    return False


def _missing_agents(
    filter_meta: dict[str, Any],
    export_meta: dict[str, Any] | None,
) -> list[str]:
    unmatched = filter_meta.get("require_unmatched_columns")
    if isinstance(unmatched, list) and unmatched:
        return [str(x).strip() for x in unmatched if str(x).strip()]
    require = filter_meta.get("require") or []
    if not isinstance(require, list):
        return []
    present = _present_agent_ids(export_meta)
    if not present:
        # No export meta: cannot decide beyond unmatched list
        return []
    missing: list[str] = []
    for raw in require:
        r = str(raw).strip()
        if not r:
            continue
        if not _soft_present(r, present):
            missing.append(r)
    return missing


def _filter_keep_rate(filter_meta: dict[str, Any]) -> float | None:
    aq = filter_meta.get("align_quality")
    if isinstance(aq, dict):
        comp = aq.get("components") if isinstance(aq.get("components"), dict) else {}
        kr = _safe_float(comp.get("keep_rate"))
        if kr is not None:
            return kr
        rows_in = _safe_int(aq.get("rows_in"))
        rows_out = _safe_int(aq.get("rows_out"))
        if rows_in and rows_in > 0 and rows_out is not None:
            return float(rows_out) / float(rows_in)
    rows_in = _safe_int(filter_meta.get("rows_in"))
    rows_out = _safe_int(filter_meta.get("rows_out"))
    if rows_in and rows_in > 0 and rows_out is not None:
        return float(rows_out) / float(rows_in)
    return None


def extract_episode_coverage(
    episode_dir: str | Path,
    *,
    keep_rate_min: float = DEFAULT_KEEP_RATE_MIN,
    drop_rate_max: float = DEFAULT_DROP_RATE_MAX,
) -> dict[str, Any]:
    """Build one coverage row for an episode directory."""
    ep = Path(episode_dir).expanduser().resolve()
    man = _read_json(ep / "manifest.json") or {}
    filter_meta = _read_json(ep / "export" / "filter_meta.json")
    export_meta = _read_json(ep / "export" / "export_meta.json")

    written = _safe_int(man.get("written"))
    dropped = _safe_int(man.get("dropped"))
    if written is None and isinstance(export_meta, dict):
        written = _safe_int(export_meta.get("manifest_written"))
    if dropped is None and isinstance(export_meta, dict):
        dropped = _safe_int(export_meta.get("manifest_dropped"))
    drop_rate = None
    if written is not None and dropped is not None:
        denom = written + dropped
        if denom > 0:
            drop_rate = float(dropped) / float(denom)

    hw_coverage = _safe_float(export_meta.get("hw_coverage")) if isinstance(export_meta, dict) else None
    align_clock = None
    if isinstance(export_meta, dict):
        align_clock = str(export_meta.get("align_clock") or "").strip() or None

    row: dict[str, Any] = {
        "episode": ep.name,
        "path": str(ep),
        "status": "unprocessed",
        "filter_keep_rate": None,
        "rows_in": None,
        "rows_out": None,
        "align_score": None,
        "align_grade": None,
        "require": [],
        "missing_agents": [],
        "drop_reasons": {},
        "written": written,
        "dropped": dropped,
        "drop_rate": drop_rate,
        "hw_coverage": hw_coverage,
        "align_clock": align_clock,
        "diagnose": None,
        "alerts": [],
    }

    if filter_meta is None:
        row["alerts"] = ["unprocessed"]
        return row

    row["status"] = "filtered"
    row["require"] = [str(x) for x in (filter_meta.get("require") or []) if str(x).strip()]
    row["rows_in"] = _safe_int(filter_meta.get("rows_in"))
    row["rows_out"] = _safe_int(filter_meta.get("rows_out"))
    row["filter_keep_rate"] = _filter_keep_rate(filter_meta)
    aq = filter_meta.get("align_quality") if isinstance(filter_meta.get("align_quality"), dict) else {}
    row["align_score"] = _safe_float(aq.get("score"))
    grade = aq.get("grade")
    row["align_grade"] = str(grade).strip() if grade else None
    dr = filter_meta.get("drop_reasons")
    row["drop_reasons"] = dict(dr) if isinstance(dr, dict) else {}
    row["missing_agents"] = _missing_agents(filter_meta, export_meta)
    diag = filter_meta.get("diagnose")
    row["diagnose"] = str(diag).strip() if diag else None

    alerts: list[str] = []
    kr = row["filter_keep_rate"]
    if kr is not None and kr < float(keep_rate_min):
        alerts.append("low_filter_keep_rate")
    if row["missing_agents"]:
        alerts.append("missing_agents")
    if drop_rate is not None and drop_rate > float(drop_rate_max):
        alerts.append("high_record_drop_rate")
    if hw_coverage is not None and hw_coverage < 1.0 and align_clock == "hw_ts":
        alerts.append("incomplete_hw_coverage")
    row["alerts"] = alerts
    return row


def summarize_coverage(
    rows: list[dict[str, Any]],
    *,
    keep_rate_min: float = DEFAULT_KEEP_RATE_MIN,
    drop_rate_max: float = DEFAULT_DROP_RATE_MAX,
) -> dict[str, Any]:
    """Dataset-level histogram / counts over coverage rows."""
    n = len(rows)
    n_filtered = sum(1 for r in rows if r.get("status") == "filtered")
    n_unprocessed = sum(1 for r in rows if r.get("status") == "unprocessed")
    n_alert = sum(1 for r in rows if r.get("alerts"))

    keep_vals = [
        float(r["filter_keep_rate"])
        for r in rows
        if r.get("filter_keep_rate") is not None
    ]
    keep_vals_sorted = sorted(keep_vals)
    drop_vals = [float(r["drop_rate"]) for r in rows if r.get("drop_rate") is not None]
    drop_vals_sorted = sorted(drop_vals)
    hw_vals = [float(r["hw_coverage"]) for r in rows if r.get("hw_coverage") is not None]
    hw_vals_sorted = sorted(hw_vals)

    missing_freq: dict[str, int] = {}
    for r in rows:
        for aid in r.get("missing_agents") or []:
            missing_freq[str(aid)] = int(missing_freq.get(str(aid), 0)) + 1

    alert_freq: dict[str, int] = {}
    for r in rows:
        for a in r.get("alerts") or []:
            alert_freq[str(a)] = int(alert_freq.get(str(a), 0)) + 1

    return {
        "episodes": n,
        "filtered": n_filtered,
        "unprocessed": n_unprocessed,
        "alert_count": n_alert,
        "threshold": {
            "filter_keep_rate_min": float(keep_rate_min),
            "drop_rate_max": float(drop_rate_max),
        },
        "filter_keep_rate": {
            "n": len(keep_vals_sorted),
            "mean": (sum(keep_vals_sorted) / len(keep_vals_sorted)) if keep_vals_sorted else None,
            "p50": _percentile(keep_vals_sorted, 50),
            "histogram": _histogram(keep_vals_sorted, [0.0, 0.5, 0.9, 0.99, 1.0]),
        },
        "drop_rate": {
            "n": len(drop_vals_sorted),
            "mean": (sum(drop_vals_sorted) / len(drop_vals_sorted)) if drop_vals_sorted else None,
            "p50": _percentile(drop_vals_sorted, 50),
        },
        "hw_coverage": {
            "n": len(hw_vals_sorted),
            "mean": (sum(hw_vals_sorted) / len(hw_vals_sorted)) if hw_vals_sorted else None,
            "p50": _percentile(hw_vals_sorted, 50),
        },
        "missing_agent_freq": dict(sorted(missing_freq.items(), key=lambda kv: (-kv[1], kv[0]))),
        "alert_freq": dict(sorted(alert_freq.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def build_coverage_block(
    episode_dirs: list[Path],
    *,
    keep_rate_min: float = DEFAULT_KEEP_RATE_MIN,
    drop_rate_max: float = DEFAULT_DROP_RATE_MAX,
) -> dict[str, Any]:
    rows = [
        extract_episode_coverage(
            ep,
            keep_rate_min=keep_rate_min,
            drop_rate_max=drop_rate_max,
        )
        for ep in episode_dirs
    ]
    return {
        "schema": SCHEMA,
        "kind": "sensor_timeline",
        "note": (
            "Sensor/timeline coverage (filter keep_rate, missing require agents, "
            "record drop_rate, hw_coverage). Not E4 scene/object tag coverage."
        ),
        "summary": summarize_coverage(
            rows,
            keep_rate_min=keep_rate_min,
            drop_rate_max=drop_rate_max,
        ),
        "episodes": rows,
    }
