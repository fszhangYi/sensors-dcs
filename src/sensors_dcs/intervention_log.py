"""E2: per-episode intervention event log → intervention rate KPIs.

Sidecar: ``episode_XXXXX/events/interventions.jsonl`` (append-only).
Stop-time summary may be copied into ``manifest.intervention_summary``.
Emit failures must never raise into control paths.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = "dcs_intervention_v1"
REL_PATH = Path("events") / "interventions.jsonl"

# type → duration bucket key for on/off pairs
_DURATION_PAIRS: dict[str, tuple[str, str]] = {
    "teleop": ("teleop_on", "teleop_off"),
    "delta_offset": ("delta_offset_on", "delta_offset_off"),
}

ON_TYPES = frozenset({pair[0] for pair in _DURATION_PAIRS.values()})
OFF_TYPES = frozenset({pair[1] for pair in _DURATION_PAIRS.values()})
KNOWN_TYPES = frozenset(
    {
        "teleop_on",
        "teleop_off",
        "delta_offset_on",
        "delta_offset_off",
        "estop",
        "disarm",
        "arm_stop",
    }
)


def interventions_path(episode_dir: str | Path) -> Path:
    return Path(episode_dir).expanduser().resolve() / REL_PATH


def append_intervention(
    episode_dir: str | Path,
    *,
    type: str,
    source: str = "",
    detail: dict[str, Any] | None = None,
    t_wall: float | None = None,
    t_start: float | None = None,
) -> dict[str, Any]:
    """Append one jsonl row. Returns ``{ok, ...}``; never raises."""
    try:
        ep = Path(episode_dir).expanduser().resolve()
        if not ep.is_dir():
            return {"ok": False, "error": f"episode dir missing: {ep}"}
        typ = str(type or "").strip().lower()
        if not typ:
            return {"ok": False, "error": "type required"}
        tw = float(time.time() if t_wall is None else t_wall)
        tr: float | None = None
        if t_start is not None:
            try:
                tr = tw - float(t_start)
            except (TypeError, ValueError):
                tr = None
        row: dict[str, Any] = {
            "schema": SCHEMA,
            "t_wall": tw,
            "t_rel": tr,
            "type": typ,
            "source": str(source or "").strip(),
            "detail": dict(detail) if isinstance(detail, dict) else {},
        }
        path = interventions_path(ep)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "path": str(path), "event": row}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def read_intervention_events(episode_dir: str | Path) -> list[dict[str, Any]]:
    path = interventions_path(episode_dir)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def _event_t(row: dict[str, Any]) -> float | None:
    for key in ("t_wall", "t"):
        if row.get(key) is None:
            continue
        try:
            return float(row[key])
        except (TypeError, ValueError):
            continue
    return None


def summarize_intervention_events(
    events: list[dict[str, Any]],
    *,
    t_end: float | None = None,
) -> dict[str, Any]:
    """Build ``intervention_summary`` from event rows (on/off duration pairing)."""
    counts: dict[str, int] = {}
    for row in events:
        typ = str(row.get("type") or "").strip().lower()
        if not typ:
            continue
        counts[typ] = int(counts.get(typ, 0)) + 1

    duration_s: dict[str, float] = {k: 0.0 for k in _DURATION_PAIRS}
    for bucket, (on_t, off_t) in _DURATION_PAIRS.items():
        open_t: float | None = None
        for row in events:
            typ = str(row.get("type") or "").strip().lower()
            ts = _event_t(row)
            if ts is None:
                continue
            if typ == on_t:
                # Nested on: close previous open at this timestamp (0-length ok).
                if open_t is not None:
                    duration_s[bucket] += max(0.0, ts - open_t)
                open_t = ts
            elif typ == off_t and open_t is not None:
                duration_s[bucket] += max(0.0, ts - open_t)
                open_t = None
        if open_t is not None and t_end is not None:
            try:
                duration_s[bucket] += max(0.0, float(t_end) - open_t)
            except (TypeError, ValueError):
                pass

    # Round for stable JSON
    duration_s = {k: round(float(v), 3) for k, v in duration_s.items()}
    any_flag = bool(counts) or any(v > 0 for v in duration_s.values())
    return {
        "any": any_flag,
        "counts": counts,
        "duration_s": duration_s,
        "event_count": int(sum(counts.values())),
    }


def summarize_episode_interventions(
    episode_dir: str | Path,
    *,
    t_end: float | None = None,
) -> dict[str, Any]:
    ep = Path(episode_dir).expanduser().resolve()
    events = read_intervention_events(ep)
    # Prefer manifest t_end if caller omitted.
    end = t_end
    if end is None:
        man_path = ep / "manifest.json"
        if man_path.is_file():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
                if isinstance(man, dict) and man.get("t_end") is not None:
                    end = float(man["t_end"])
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
    summary = summarize_intervention_events(events, t_end=end)
    summary["episode"] = str(ep)
    summary["path"] = str(interventions_path(ep))
    return summary


class InterventionLogger:
    """Thread-safe logger bound to the active recording episode (or unbound)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ep_dir: Path | None = None
        self._t_start: float | None = None

    def bind(self, episode_dir: str | Path, *, t_start: float | None = None) -> None:
        with self._lock:
            self._ep_dir = Path(episode_dir).expanduser().resolve()
            self._t_start = float(t_start) if t_start is not None else None
            try:
                (self._ep_dir / "events").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def unbind(self) -> None:
        with self._lock:
            self._ep_dir = None
            self._t_start = None

    @property
    def episode_dir(self) -> Path | None:
        with self._lock:
            return self._ep_dir

    def emit(
        self,
        type: str,
        *,
        source: str = "",
        detail: dict[str, Any] | None = None,
        t_wall: float | None = None,
    ) -> dict[str, Any]:
        """Append while recording; no-op (ok=False) if unbound. Never raises."""
        try:
            with self._lock:
                ep = self._ep_dir
                t0 = self._t_start
            if ep is None:
                return {"ok": False, "error": "no active episode", "skipped": True}
            return append_intervention(
                ep,
                type=type,
                source=source,
                detail=detail,
                t_wall=t_wall,
                t_start=t0,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def summarize(self, *, t_end: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            ep = self._ep_dir
        if ep is None:
            return None
        try:
            return summarize_episode_interventions(ep, t_end=t_end)
        except Exception:  # noqa: BLE001
            return None


def summarize_interventions(root: str | Path) -> dict[str, Any]:
    """Scan ``root/episode_*`` (and shallow nested) for intervention KPIs."""
    base = Path(root).expanduser().resolve()
    episodes: list[Path] = []
    if base.is_dir():
        episodes.extend(sorted(base.glob("episode_*")))
        episodes.extend(sorted(base.glob("*/episode_*")))
        episodes.extend(sorted(base.glob("*/*/episode_*")))
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in episodes:
        key = str(p.resolve())
        if key in seen or not p.is_dir():
            continue
        seen.add(key)
        uniq.append(p)

    n = 0
    with_any = 0
    with_log = 0
    total_events = 0
    duration_sum = {"teleop": 0.0, "delta_offset": 0.0}
    episode_duration_sum = 0.0
    counts_total: dict[str, int] = {}

    for ep in uniq:
        n += 1
        man: dict[str, Any] = {}
        man_path = ep / "manifest.json"
        if man_path.is_file():
            try:
                raw = json.loads(man_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    man = raw
            except (OSError, json.JSONDecodeError, TypeError):
                man = {}
        try:
            if man.get("duration_s") is not None:
                episode_duration_sum += float(man["duration_s"])
        except (TypeError, ValueError):
            pass

        if interventions_path(ep).is_file():
            with_log += 1
        summary = man.get("intervention_summary")
        if not isinstance(summary, dict):
            summary = summarize_episode_interventions(ep)

        if not isinstance(summary, dict):
            continue
        if summary.get("any"):
            with_any += 1
        try:
            total_events += int(summary.get("event_count") or 0)
        except (TypeError, ValueError):
            pass
        for k, v in (summary.get("counts") or {}).items():
            try:
                counts_total[str(k)] = int(counts_total.get(str(k), 0)) + int(v)
            except (TypeError, ValueError):
                continue
        dur = summary.get("duration_s") if isinstance(summary.get("duration_s"), dict) else {}
        for k in duration_sum:
            try:
                duration_sum[k] += float(dur.get(k) or 0.0)
            except (TypeError, ValueError):
                continue

    intervene_rate = (float(with_any) / float(n)) if n else None
    time_share = None
    interv_dur = float(duration_sum["teleop"] + duration_sum["delta_offset"])
    if episode_duration_sum > 0:
        time_share = interv_dur / float(episode_duration_sum)

    return {
        "ok": True,
        "root": str(base),
        "episodes": n,
        "with_intervention_log": with_log,
        "with_any_intervention": with_any,
        "intervention_rate": intervene_rate,
        "intervention_time_share": time_share,
        "total_events": total_events,
        "counts": counts_total,
        "duration_s": {k: round(v, 3) for k, v in duration_sum.items()},
        "episode_duration_s_sum": round(episode_duration_sum, 3),
    }
