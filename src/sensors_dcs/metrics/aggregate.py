"""E3: scan a collect root for dataset KPIs (read-only aggregation).

Missing E1/E2/align fields degrade to null / zero — never raise for absent data.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from sensors_dcs.task_outcome import normalize_outcome

SCHEMA = "dcs_metrics_summary_v1"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


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


def _align_score(ep: Path) -> float | None:
    meta = _read_json(ep / "export" / "filter_meta.json")
    if not meta:
        return None
    aq = meta.get("align_quality")
    if not isinstance(aq, dict):
        # Some writers nest under filter_meta key; tolerate flat score too.
        if meta.get("score") is not None and meta.get("grade") is not None:
            aq = meta
        else:
            return None
    try:
        s = float(aq.get("score"))
    except (TypeError, ValueError):
        return None
    if s != s:  # NaN
        return None
    return s


def _list_top_episodes(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.glob("episode_*") if p.is_dir() and p.name.startswith("episode_")
    )


def _count_sessions(root: Path) -> int:
    sess = root / "sessions"
    if not sess.is_dir():
        return 0
    n = 0
    for d in sess.iterdir():
        if d.is_dir() and (d / "session.json").is_file():
            n += 1
    return n


def _count_merged_episodes(root: Path) -> int:
    sess = root / "sessions"
    if not sess.is_dir():
        return 0
    n = 0
    for d in sess.glob("*/merged/episode_*"):
        if d.is_dir():
            n += 1
    return n


def _new_pivot_row() -> dict[str, Any]:
    return {
        "episodes": 0,
        "success": 0,
        "fail": 0,
        "partial": 0,
        "void": 0,
        "unknown": 0,
        "unlabeled": 0,
        "completion_denom": 0,
        "completion_rate": None,
    }


def _pivot_touch(store: dict[str, dict[str, Any]], key: str, outcome: str | None) -> None:
    k = str(key or "").strip()
    if not k:
        return
    row = store.get(k)
    if row is None:
        row = _new_pivot_row()
        store[k] = row
    row["episodes"] = int(row["episodes"]) + 1
    if outcome is None:
        row["unlabeled"] = int(row["unlabeled"]) + 1
        return
    if outcome in ("success", "fail", "partial", "void", "unknown"):
        row[outcome] = int(row[outcome]) + 1
    else:
        row["unknown"] = int(row["unknown"]) + 1


def _finalize_pivots(store: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(store.keys()):
        row = dict(store[key])
        denom = int(row["success"]) + int(row["fail"]) + int(row["partial"])
        row["completion_denom"] = denom
        row["completion_rate"] = (int(row["success"]) / denom) if denom else None
        out[key] = row
    return out


def aggregate_metrics(root: str | Path, *, write: bool = False) -> dict[str, Any]:
    """Aggregate KPIs under a collect / dataset root.

    Primary cohort = ``root/episode_*`` (station-local finalized episodes).
    Nested session copies are counted separately so rates are not double-counted.
    """
    base = Path(root).expanduser().resolve()
    out: dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA,
        "root": str(base),
        "t_wall": float(time.time()),
        "error": None,
    }
    if not base.is_dir():
        out["ok"] = False
        out["error"] = f"root not found: {base}"
        return out

    episodes = _list_top_episodes(base)
    n_final = 0
    n_valid = 0
    n_invalid = 0
    n_with_outcome = 0
    n_unlabeled = 0
    n_success = 0
    n_fail = 0
    n_partial = 0
    n_void = 0
    n_unknown = 0
    n_intervene = 0
    by_mode = {"collect": 0, "infer": 0, "other": 0}
    durations: list[float] = []
    align_scores: list[float] = []
    by_task: dict[str, dict[str, Any]] = {}
    by_env: dict[str, dict[str, Any]] = {}
    by_object: dict[str, dict[str, Any]] = {}
    n_with_task = 0
    n_with_env = 0
    n_with_object = 0
    n_untagged = 0
    finalized_eps: list[Path] = []

    for ep in episodes:
        man = _read_json(ep / "manifest.json")
        if man is None:
            continue
        if man.get("provisional"):
            continue
        n_final += 1
        finalized_eps.append(ep)
        if man.get("valid") is True:
            n_valid += 1
        elif man.get("valid") is False:
            n_invalid += 1

        mode = str(man.get("record_mode") or "collect").strip().lower()
        if mode in by_mode:
            by_mode[mode] += 1
        else:
            by_mode["other"] += 1

        try:
            if man.get("duration_s") is not None:
                d = float(man["duration_s"])
                if d == d:
                    durations.append(d)
        except (TypeError, ValueError):
            pass

        to = man.get("task_outcome")
        outcome_key: str | None
        if not isinstance(to, dict) or not to.get("outcome"):
            n_unlabeled += 1
            outcome_key = None
        else:
            n_with_outcome += 1
            oc = normalize_outcome(to.get("outcome"))
            outcome_key = oc
            if oc == "success":
                n_success += 1
            elif oc == "fail":
                n_fail += 1
            elif oc == "partial":
                n_partial += 1
            elif oc == "void":
                n_void += 1
            else:
                n_unknown += 1

        tags = man.get("tags") if isinstance(man.get("tags"), dict) else {}
        tid = str(tags.get("task_id") or "").strip()
        eid = str(tags.get("env_id") or "").strip()
        objs_raw = tags.get("object_ids") or []
        if isinstance(objs_raw, str):
            objs = [x.strip() for x in objs_raw.replace(";", ",").split(",") if x.strip()]
        elif isinstance(objs_raw, (list, tuple)):
            objs = [str(x).strip() for x in objs_raw if str(x).strip()]
        else:
            objs = []
        if tid:
            n_with_task += 1
            _pivot_touch(by_task, tid, outcome_key)
        if eid:
            n_with_env += 1
            _pivot_touch(by_env, eid, outcome_key)
        if objs:
            n_with_object += 1
            for oid in objs:
                _pivot_touch(by_object, oid, outcome_key)
        if not tid and not eid and not objs:
            n_untagged += 1

        iv = man.get("intervention_summary")
        if isinstance(iv, dict) and iv.get("any"):
            n_intervene += 1
        else:
            # Fall back to sidecar presence with any events
            from sensors_dcs.intervention_log import (
                read_intervention_events,
                summarize_intervention_events,
            )

            try:
                ev = read_intervention_events(ep)
                if summarize_intervention_events(ev).get("any"):
                    n_intervene += 1
            except Exception:  # noqa: BLE001
                pass

        sc = _align_score(ep)
        if sc is not None:
            align_scores.append(sc)

    durations_sorted = sorted(durations)
    align_sorted = sorted(align_scores)
    completion_denom = n_success + n_fail + n_partial

    kpis = {
        "episodes": n_final,
        "keep_rate": (n_valid / n_final) if n_final else None,
        "valid_true": n_valid,
        "valid_false": n_invalid,
        "completion_rate": (n_success / completion_denom) if completion_denom else None,
        "completion_denom": completion_denom,
        "unlabeled_rate": (n_unlabeled / n_final) if n_final else None,
        "unlabeled": n_unlabeled,
        "with_outcome": n_with_outcome,
        "outcome_counts": {
            "success": n_success,
            "fail": n_fail,
            "partial": n_partial,
            "void": n_void,
            "unknown": n_unknown,
        },
        "intervention_rate": (n_intervene / n_final) if n_final else None,
        "with_intervention": n_intervene,
        "duration_s": {
            "n": len(durations_sorted),
            "mean": (sum(durations_sorted) / len(durations_sorted)) if durations_sorted else None,
            "p50": _percentile(durations_sorted, 50),
            "p95": _percentile(durations_sorted, 95),
        },
        "align_quality": {
            "n": len(align_sorted),
            "mean": (sum(align_sorted) / len(align_sorted)) if align_sorted else None,
            "p50": _percentile(align_sorted, 50),
        },
        "by_mode": dict(by_mode),
        "sessions": _count_sessions(base),
        "merged_episodes": _count_merged_episodes(base),
        "tag_coverage": {
            "with_task_id": n_with_task,
            "with_env_id": n_with_env,
            "with_object_id": n_with_object,
            "untagged": n_untagged,
        },
        "by_task_id": _finalize_pivots(by_task),
        "by_env_id": _finalize_pivots(by_env),
        "by_object_id": _finalize_pivots(by_object),
    }

    out["kpis"] = kpis
    # M3: sensor/timeline coverage (orthogonal to E4 tag_coverage / valid keep_rate)
    try:
        from sensors_dcs.metrics.coverage import build_coverage_block

        out["coverage"] = build_coverage_block(finalized_eps)
    except Exception as e:  # noqa: BLE001
        out["coverage"] = {
            "schema": "dcs_sensor_coverage_v1",
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "summary": {},
            "episodes": [],
        }
    # M2: read export/quality/traj_quality.json (+ near_miss for thick)
    try:
        from sensors_dcs.export.traj_quality import read_traj_quality

        by_grade = {"good": 0, "warn": 0, "bad": 0, "missing": 0}
        n_with = 0
        n_thick = 0
        n_overspeed_eps = 0
        n_table_eps = 0
        n_self_eps = 0
        n_near_eps = 0
        q_rows: list[dict[str, Any]] = []
        for ep in finalized_eps:
            tq = read_traj_quality(ep)
            if not isinstance(tq, dict):
                by_grade["missing"] = int(by_grade["missing"]) + 1
                q_rows.append({"episode": ep.name, "grade": "missing", "kind": "missing"})
                continue
            n_with += 1
            kind = str(tq.get("kind") or "thin").strip().lower()
            if kind == "thick":
                n_thick += 1
            grade = str(tq.get("grade") or "missing").strip().lower()
            if grade not in by_grade:
                grade = "missing"
            by_grade[grade] = int(by_grade[grade]) + 1
            thin = tq.get("thin") if isinstance(tq.get("thin"), dict) else {}
            oc = tq.get("overspeed_count")
            if oc is None:
                oc = thin.get("overspeed_count")
            try:
                if oc is not None and int(oc) > 0:
                    n_overspeed_eps += 1
            except (TypeError, ValueError):
                pass
            n_table = tq.get("n_table")
            n_self = tq.get("n_self")
            n_near = tq.get("n_near_miss")
            geo = tq.get("geometry") if isinstance(tq.get("geometry"), dict) else {}
            if n_table is None:
                n_table = geo.get("n_table")
            if n_self is None:
                n_self = geo.get("n_self")
            if n_near is None:
                n_near = geo.get("n_near_miss")
            try:
                if n_table is not None and int(n_table) > 0:
                    n_table_eps += 1
                if n_self is not None and int(n_self) > 0:
                    n_self_eps += 1
                if n_near is not None and int(n_near) > 0:
                    n_near_eps += 1
            except (TypeError, ValueError):
                pass
            q_rows.append(
                {
                    "episode": ep.name,
                    "kind": kind,
                    "grade": grade,
                    "overspeed_count": oc,
                    "jump_count": tq.get("jump_count", thin.get("jump_count")),
                    "tcp_overspeed_count": tq.get(
                        "tcp_overspeed_count", thin.get("tcp_overspeed_count")
                    ),
                    "n_table": n_table,
                    "n_self": n_self,
                    "n_near_miss": n_near,
                    "agent_id": tq.get("agent_id"),
                }
            )
        out["quality"] = {
            "schema": "dcs_traj_quality_summary_v1",
            "kind": "mixed",
            "note": (
                "M2 traj_quality sidecars: kind=thin (dynamics) and/or thick "
                "(sensors.traj_safety geometry). near_miss.json optional."
            ),
            "summary": {
                "episodes": len(finalized_eps),
                "with_traj_quality": n_with,
                "missing": int(by_grade["missing"]),
                "thick": n_thick,
                "thin": max(0, n_with - n_thick),
                "by_grade": dict(by_grade),
                "overspeed_episodes": n_overspeed_eps,
                "table_hit_episodes": n_table_eps,
                "self_hit_episodes": n_self_eps,
                "near_miss_episodes": n_near_eps,
            },
            "episodes": q_rows,
        }
    except Exception as e:  # noqa: BLE001
        out["quality"] = {
            "schema": "dcs_traj_quality_summary_v1",
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "summary": {},
            "episodes": [],
        }
    if write:
        written = write_metrics_summary(base, out)
        out["written"] = written
    return out


def write_metrics_summary(
    root: str | Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``metrics_summary.json`` under root. Re-aggregates if payload omitted."""
    base = Path(root).expanduser().resolve()
    data = payload if isinstance(payload, dict) else aggregate_metrics(base, write=False)
    path = base / "metrics_summary.json"
    try:
        base.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "path": str(path)}
    except OSError as e:
        return {"ok": False, "error": str(e), "path": str(path)}
