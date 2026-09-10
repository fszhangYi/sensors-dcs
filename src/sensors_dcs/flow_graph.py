"""Pure helpers for infer-tab flow orchestration (topology + pose/term checks).

Mirrored in static/flow_editor.js / flow_runtime.js for browser runtime.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence


def find_entry_ids(modules: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return module ids with in-degree 0."""
    ids = {str(m["id"]) for m in modules}
    incoming: set[str] = set()
    for e in edges:
        to = str(e.get("to") or "")
        if to in ids:
            incoming.add(to)
    return sorted(ids - incoming)


def out_degrees(modules: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ids = {str(m["id"]) for m in modules}
    deg = {i: 0 for i in ids}
    for e in edges:
        frm = str(e.get("from") or "")
        if frm in deg:
            deg[frm] += 1
    return deg


def has_cycle(modules: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> bool:
    """DFS cycle detection on directed graph."""
    ids = {str(m["id"]) for m in modules}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        frm = str(e.get("from") or "")
        to = str(e.get("to") or "")
        if frm in adj and to in ids:
            adj[frm].append(to)
    visiting: set[str] = set()
    done: set[str] = set()

    def dfs(n: str) -> bool:
        if n in done:
            return False
        if n in visiting:
            return True
        visiting.add(n)
        for nxt in adj[n]:
            if dfs(nxt):
                return True
        visiting.remove(n)
        done.add(n)
        return False

    return any(dfs(n) for n in ids)


def validate_chain(
    modules: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> tuple[bool, str | None, str | None]:
    """Return (ok, entry_id, error_code). First-pass: single entry, no cycle, out-degree ≤ 1."""
    if not modules:
        return False, None, "empty_graph"
    if has_cycle(modules, edges):
        return False, None, "cycle"
    for mid, d in out_degrees(modules, edges).items():
        if d > 1:
            return False, mid, "out_degree_gt1"
    entries = find_entry_ids(modules, edges)
    if len(entries) != 1:
        return False, None, "need_single_entry"
    return True, entries[0], None


def chain_order(
    modules: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]], entry_id: str
) -> list[str]:
    """Follow unique out-edges from entry; stop at leaf or missing edge."""
    by_from = {str(e["from"]): str(e["to"]) for e in edges if e.get("from") and e.get("to")}
    ids = {str(m["id"]) for m in modules}
    out: list[str] = []
    cur: str | None = entry_id
    seen: set[str] = set()
    while cur and cur in ids and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = by_from.get(cur)
    return out


def _as6(xyzrpy: Sequence[Any] | None) -> list[float] | None:
    if not xyzrpy or len(xyzrpy) < 6:
        return None
    out = [float(xyzrpy[i]) for i in range(6)]
    if any(not math.isfinite(v) for v in out):
        return None
    return out


def rotvec_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Approximate orientation distance via |Δrpy| L2 (rad); good enough for start gate."""
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3, 6)))


def pose_near(
    actual_xyzrpy: Sequence[Any] | None,
    actual_grip: float | None,
    expect: Mapping[str, Any],
    tol: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Check TCP+grip against Pose7 + start_tol. expect has xyzrpy + gripper."""
    want = _as6(expect.get("xyzrpy"))
    got = _as6(actual_xyzrpy)
    if want is None or got is None:
        return False, "missing_pose"
    pos_tol = float(tol.get("pos_m", 0.01))
    rot_tol = float(tol.get("rot_rad", 0.05))
    grip_tol = float(tol.get("grip", 0.05))
    dp = math.sqrt(sum((got[i] - want[i]) ** 2 for i in range(3)))
    if dp > pos_tol:
        return False, "pos"
    if rotvec_delta(got, want) > rot_tol:
        return False, "rot"
    eg = float(expect.get("gripper", 0.0))
    if actual_grip is None or not math.isfinite(float(actual_grip)):
        return False, "missing_grip"
    if abs(float(actual_grip) - eg) > grip_tol:
        return False, "grip"
    return True, None


def normalize_term_cond(term_cond: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize {direction, threshold} or legacy {z_rise_to,z_fall_to} → dir+threshold."""
    if not term_cond:
        return None
    direction = term_cond.get("direction")
    if direction in ("z_rise", "z_fall"):
        th = term_cond.get("threshold", None)
        if th is None or not math.isfinite(float(th)):
            return None
        return {"direction": direction, "threshold": float(th)}
    rise = term_cond.get("z_rise_to", None)
    if rise is not None and math.isfinite(float(rise)):
        return {"direction": "z_rise", "threshold": float(rise)}
    fall = term_cond.get("z_fall_to", None)
    if fall is not None and math.isfinite(float(fall)):
        return {"direction": "z_fall", "threshold": float(fall)}
    return None


def term_cond_triggered(z: float | None, term_cond: Mapping[str, Any] | None) -> bool:
    """direction+threshold: z_rise → z≥th; z_fall → z≤th. Legacy OR fields also accepted."""
    if z is None or not math.isfinite(float(z)):
        return False
    n = normalize_term_cond(term_cond)
    if not n:
        return False
    zf = float(z)
    if n["direction"] == "z_rise":
        return zf >= float(n["threshold"])
    if n["direction"] == "z_fall":
        return zf <= float(n["threshold"])
    return False


def term_cond_configured(term_cond: Mapping[str, Any] | None) -> bool:
    return normalize_term_cond(term_cond) is not None
