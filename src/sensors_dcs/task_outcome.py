"""Task outcome / episode tags for capability KPIs (E1).

``manifest.valid`` remains data keep/discard. ``task_outcome.outcome`` is the
operator (or heuristic) judgment of task success — never conflate the two.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

OUTCOMES = frozenset({"success", "fail", "partial", "void", "unknown"})
LABELS_ALLOWED = frozenset({"grasp", "place", "other"})
# Counted in completion-rate denominator.
RATE_DENOM = frozenset({"success", "fail", "partial"})


def normalize_outcome(raw: Any) -> str:
    if raw is None or raw == "":
        return "unknown"
    s = str(raw).strip().lower()
    aliases = {
        "ok": "success",
        "pass": "success",
        "true": "success",
        "1": "success",
        "failed": "fail",
        "failure": "fail",
        "false": "fail",
        "0": "fail",
        "incomplete": "partial",
        "skip": "void",
        "n/a": "void",
        "na": "void",
    }
    s = aliases.get(s, s)
    return s if s in OUTCOMES else "unknown"


def normalize_labels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip().lower() for p in raw]
    else:
        return []
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if p not in LABELS_ALLOWED:
            continue
        if p not in out:
            out.append(p)
    return out


def normalize_tags(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    task_id = str(raw.get("task_id") or "").strip()
    env_id = str(raw.get("env_id") or "").strip()
    objs_raw = raw.get("object_ids")
    object_ids: list[str] = []
    if isinstance(objs_raw, str):
        object_ids = [x.strip() for x in objs_raw.replace(";", ",").split(",") if x.strip()]
    elif isinstance(objs_raw, (list, tuple)):
        object_ids = [str(x).strip() for x in objs_raw if str(x).strip()]
    tags: dict[str, Any] = {}
    if task_id:
        tags["task_id"] = task_id
    if env_id:
        tags["env_id"] = env_id
    if object_ids:
        tags["object_ids"] = object_ids
    return tags or None


def build_task_outcome(
    raw: Any,
    *,
    default_outcome: str = "unknown",
    source: str = "operator",
) -> dict[str, Any] | None:
    """Build a manifest ``task_outcome`` object, or None if intentionally omitted."""
    if raw is False:
        return None
    if raw is None:
        outcome = normalize_outcome(default_outcome)
        if outcome == "unknown" and default_outcome == "unknown":
            # Explicit "no annotation" — omit field so aggregators count as unlabeled.
            return None
        return {
            "outcome": outcome,
            "labels": [],
            "reason_code": "",
            "note": "",
            "source": source,
            "t_wall": float(time.time()),
        }
    if not isinstance(raw, dict):
        return {
            "outcome": normalize_outcome(raw),
            "labels": [],
            "reason_code": "",
            "note": "",
            "source": source,
            "t_wall": float(time.time()),
        }
    outcome = normalize_outcome(raw.get("outcome", default_outcome))
    labels = normalize_labels(raw.get("labels"))
    reason_code = str(raw.get("reason_code") or "").strip()
    note = str(raw.get("note") or "").strip()
    src = str(raw.get("source") or source).strip() or source
    try:
        t_wall = float(raw.get("t_wall")) if raw.get("t_wall") is not None else float(time.time())
    except (TypeError, ValueError):
        t_wall = float(time.time())
    return {
        "outcome": outcome,
        "labels": labels,
        "reason_code": reason_code,
        "note": note,
        "source": src,
        "t_wall": t_wall,
    }


def apply_outcome_to_manifest(
    manifest: dict[str, Any],
    *,
    task_outcome: Any = None,
    tags: Any = None,
    default_outcome: str = "unknown",
    source: str = "operator",
) -> dict[str, Any]:
    to = build_task_outcome(task_outcome, default_outcome=default_outcome, source=source)
    if to is not None:
        manifest["task_outcome"] = to
    tg = normalize_tags(tags)
    if tg is not None:
        manifest["tags"] = tg
    return manifest


def _read_manifest(ep_dir: Path) -> dict[str, Any] | None:
    path = ep_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def patch_episode_outcome(
    episode_dir: str | Path,
    *,
    task_outcome: Any = None,
    tags: Any = None,
    default_outcome: str = "unknown",
    source: str = "operator",
    allow_provisional: bool = False,
) -> dict[str, Any]:
    """Merge task_outcome/tags into an existing episode manifest.json (post-stop)."""
    ep = Path(episode_dir).expanduser().resolve()
    if not ep.is_dir():
        return {"ok": False, "error": f"episode dir missing: {ep}", "episode": str(ep)}
    path = ep / "manifest.json"
    man = _read_manifest(ep)
    if man is None:
        return {"ok": False, "error": f"manifest missing/unreadable: {path}", "episode": str(ep)}
    if man.get("provisional") and not allow_provisional:
        return {
            "ok": False,
            "error": "manifest still provisional (async flush not finished)",
            "episode": str(ep),
            "provisional": True,
        }
    apply_outcome_to_manifest(
        man,
        task_outcome=task_outcome,
        tags=tags,
        default_outcome=default_outcome,
        source=source,
    )
    try:
        path.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}", "episode": str(ep)}
    return {
        "ok": True,
        "episode": str(ep),
        "task_outcome": man.get("task_outcome"),
        "tags": man.get("tags"),
    }


def summarize_outcomes(root: str | Path) -> dict[str, Any]:
    """Scan ``root/episode_*`` (and shallow nested) for task_outcome KPIs."""
    base = Path(root).expanduser().resolve()
    episodes: list[Path] = []
    if base.is_dir():
        episodes.extend(sorted(base.glob("episode_*")))
        # One level down (e.g. sessions/.../merged/episode_*)
        episodes.extend(sorted(base.glob("*/episode_*")))
        episodes.extend(sorted(base.glob("*/*/episode_*")))
    # De-dupe while preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in episodes:
        key = str(p.resolve())
        if key in seen or not p.is_dir():
            continue
        seen.add(key)
        uniq.append(p)

    counts = {
        "episodes": 0,
        "with_outcome": 0,
        "unlabeled": 0,
        "success": 0,
        "fail": 0,
        "partial": 0,
        "void": 0,
        "unknown": 0,
        "valid_true": 0,
        "valid_false": 0,
        "by_label": {"grasp": 0, "place": 0, "other": 0},
        "by_task_id": {},
        "by_env_id": {},
        "by_object_id": {},
    }
    durations: list[float] = []

    for ep in uniq:
        man = _read_manifest(ep)
        if man is None:
            continue
        # Skip provisional mid-record files
        if man.get("provisional"):
            continue
        counts["episodes"] += 1
        if man.get("valid") is True:
            counts["valid_true"] += 1
        elif man.get("valid") is False:
            counts["valid_false"] += 1
        dur = man.get("duration_s")
        try:
            if dur is not None and float(dur) == float(dur):
                durations.append(float(dur))
        except (TypeError, ValueError):
            pass

        to = man.get("task_outcome")
        if not isinstance(to, dict) or not to.get("outcome"):
            counts["unlabeled"] += 1
        else:
            counts["with_outcome"] += 1
            oc = normalize_outcome(to.get("outcome"))
            if oc in counts:
                counts[oc] += 1
            for lab in normalize_labels(to.get("labels")):
                counts["by_label"][lab] = int(counts["by_label"].get(lab, 0)) + 1

        tags = man.get("tags") if isinstance(man.get("tags"), dict) else {}
        tid = str(tags.get("task_id") or "").strip()
        eid = str(tags.get("env_id") or "").strip()
        if tid:
            counts["by_task_id"][tid] = int(counts["by_task_id"].get(tid, 0)) + 1
        if eid:
            counts["by_env_id"][eid] = int(counts["by_env_id"].get(eid, 0)) + 1
        for oid in tags.get("object_ids") or []:
            oid_s = str(oid).strip()
            if oid_s:
                counts["by_object_id"][oid_s] = int(counts["by_object_id"].get(oid_s, 0)) + 1

    denom = counts["success"] + counts["fail"] + counts["partial"]
    completion_rate = (counts["success"] / denom) if denom else None
    duration_mean = (sum(durations) / len(durations)) if durations else None

    return {
        "ok": True,
        "root": str(base),
        "counts": counts,
        "completion_rate": completion_rate,
        "completion_denom": denom,
        "duration_s_mean": duration_mean,
        "duration_s_n": len(durations),
    }
