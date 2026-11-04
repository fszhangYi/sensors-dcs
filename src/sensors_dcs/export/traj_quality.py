"""Thin M2: offline trajectory dynamics quality (no URDF collision).

Reads arm state jsonl under an episode, writes::

    episode/export/quality/traj_quality.json

Grades (collision reserved for full M2)::

    good — no joint overspeed / jump / TCP speed breaches
    warn — any soft gate fails
    bad  — not used in thin mode (kept for schema stability)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "dcs_traj_quality_v1"
STEP_NAME = "traj-quality"

DEFAULT_OMEGA_MAX_RAD_S = 2.0
DEFAULT_JUMP_RAD = 0.35
DEFAULT_TCP_SPEED_MAX_M_S = 1.0
DEFAULT_MIN_DT_S = 1e-4


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _as_float_list(v: Any) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or not v:
        return None
    out: list[float] = []
    for x in v:
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        if f != f or math.isinf(f):
            return None
        out.append(f)
    return out


def _pick_arm_jsonl(ep: Path) -> Path | None:
    """Prefer arm_read jsonl; fall back to largest non-write arm*.jsonl."""
    states = ep / "states"
    if not states.is_dir():
        return None
    man = _read_json(ep / "manifest.json") or {}
    prefer_ids: list[str] = []
    for a in man.get("agents") or []:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "").strip().lower()
        aid = str(a.get("agent_id") or a.get("id") or "").strip()
        if not aid:
            continue
        if kind in {"arm_read", "arm", "elite_cs", "elite_ec", "cs_arm", "ec_arm"}:
            prefer_ids.append(aid)
        elif "arm" in kind and "write" not in kind:
            prefer_ids.append(aid)

    candidates: list[Path] = []
    for p in sorted(states.glob("*.jsonl")):
        name = p.name.lower()
        if "arm" not in name:
            continue
        if "write" in name:
            continue
        candidates.append(p)
    if not candidates:
        # last resort: any arm* including write
        candidates = sorted(states.glob("arm*.jsonl"))
    if not candidates:
        return None

    for aid in prefer_ids:
        for p in candidates:
            if p.stem == aid or p.stem.startswith(aid):
                return p
    # Prefer larger files (more samples)
    candidates.sort(key=lambda p: p.stat().st_size if p.is_file() else 0, reverse=True)
    return candidates[0]


def _load_samples(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    samples: list[dict[str, Any]] = []
    agent_id: str | None = None
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if agent_id is None:
                    agent_id = str(obj.get("agent_id") or obj.get("sensor_id") or "").strip() or None
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
                joints = _as_float_list(
                    payload.get("joints_rad")
                    or payload.get("feedback_joints_rad")
                    or payload.get("q")
                )
                if not joints:
                    continue
                try:
                    t = float(obj.get("t_wall") if obj.get("t_wall") is not None else obj.get("t_mono"))
                except (TypeError, ValueError):
                    continue
                if t != t:
                    continue
                xyz = None
                cart = payload.get("cartesian_xyzrpy")
                if isinstance(cart, (list, tuple)) and len(cart) >= 3:
                    try:
                        xyz = (float(cart[0]), float(cart[1]), float(cart[2]))
                        if any(x != x for x in xyz):
                            xyz = None
                    except (TypeError, ValueError):
                        xyz = None
                samples.append({"t": t, "joints": joints, "xyz": xyz})
    except OSError:
        return [], agent_id
    samples.sort(key=lambda s: s["t"])
    return samples, agent_id


def analyze_arm_samples(
    samples: list[dict[str, Any]],
    *,
    omega_max_rad_s: float = DEFAULT_OMEGA_MAX_RAD_S,
    jump_rad: float = DEFAULT_JUMP_RAD,
    tcp_speed_max_m_s: float = DEFAULT_TCP_SPEED_MAX_M_S,
    min_dt_s: float = DEFAULT_MIN_DT_S,
) -> dict[str, Any]:
    """Pure analysis on sorted samples → metrics + grade."""
    n = len(samples)
    overspeed = 0
    jumps = 0
    tcp_overspeed = 0
    path_m = 0.0
    max_omega = 0.0
    max_jump = 0.0
    max_tcp_speed = 0.0
    dt_sum = 0.0
    pairs = 0

    for i in range(1, n):
        a, b = samples[i - 1], samples[i]
        dt = float(b["t"]) - float(a["t"])
        if dt < min_dt_s:
            continue
        qa, qb = a["joints"], b["joints"]
        m = min(len(qa), len(qb))
        if m <= 0:
            continue
        dqs = [abs(qb[j] - qa[j]) for j in range(m)]
        jump = max(dqs) if dqs else 0.0
        omega = jump / dt
        max_jump = max(max_jump, jump)
        max_omega = max(max_omega, omega)
        if omega > omega_max_rad_s:
            overspeed += 1
        if jump > jump_rad:
            jumps += 1
        xa, xb = a.get("xyz"), b.get("xyz")
        if xa is not None and xb is not None:
            dist = math.sqrt(sum((xb[k] - xa[k]) ** 2 for k in range(3)))
            path_m += dist
            v = dist / dt
            max_tcp_speed = max(max_tcp_speed, v)
            if v > tcp_speed_max_m_s:
                tcp_overspeed += 1
        dt_sum += dt
        pairs += 1

    duration = (samples[-1]["t"] - samples[0]["t"]) if n >= 2 else 0.0
    gates = [
        {
            "name": "joint_overspeed",
            "ok": overspeed == 0,
            "count": overspeed,
            "limit_rad_s": float(omega_max_rad_s),
            "max_rad_s": round(max_omega, 6),
        },
        {
            "name": "joint_jump",
            "ok": jumps == 0,
            "count": jumps,
            "limit_rad": float(jump_rad),
            "max_rad": round(max_jump, 6),
        },
        {
            "name": "tcp_speed",
            "ok": tcp_overspeed == 0,
            "count": tcp_overspeed,
            "limit_m_s": float(tcp_speed_max_m_s),
            "max_m_s": round(max_tcp_speed, 6),
            "path_m": round(path_m, 6),
        },
    ]
    soft_fail = any(not g["ok"] for g in gates)
    grade = "warn" if soft_fail else "good"

    return {
        "grade": grade,
        "gates": gates,
        "overspeed_count": overspeed,
        "jump_count": jumps,
        "tcp_overspeed_count": tcp_overspeed,
        "n_samples": n,
        "n_pairs": pairs,
        "duration_s": round(float(duration), 6) if duration == duration else None,
        "mean_dt_s": round(dt_sum / pairs, 6) if pairs else None,
        "path_m": round(path_m, 6),
        "thresholds": {
            "omega_max_rad_s": float(omega_max_rad_s),
            "jump_rad": float(jump_rad),
            "tcp_speed_max_m_s": float(tcp_speed_max_m_s),
            "min_dt_s": float(min_dt_s),
        },
    }


def run_traj_quality(
    episode: str | Path,
    *,
    arm_jsonl: str | Path | None = None,
    write: bool = True,
    omega_max_rad_s: float = DEFAULT_OMEGA_MAX_RAD_S,
    jump_rad: float = DEFAULT_JUMP_RAD,
    tcp_speed_max_m_s: float = DEFAULT_TCP_SPEED_MAX_M_S,
) -> dict[str, Any]:
    """Analyze one episode and optionally write ``export/quality/traj_quality.json``."""
    from sensors_dcs.export.timeline import resolve_episode_dir

    ep = resolve_episode_dir(episode)
    src = Path(arm_jsonl).expanduser() if arm_jsonl else _pick_arm_jsonl(ep)
    out: dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA,
        "kind": "thin",
        "note": (
            "Thin M2: joint/TCP dynamics only. No URDF self/desk collision "
            "(see full M2 / embody collision_geom)."
        ),
        "episode": ep.name,
        "episode_path": str(ep),
        "agent_id": None,
        "source": None,
        "grade": None,
        "gates": [],
        "overspeed_count": 0,
        "jump_count": 0,
        "error": None,
    }
    if src is None or not Path(src).is_file():
        out["ok"] = False
        out["error"] = "no arm state jsonl found under states/"
        out["grade"] = "missing"
        return out

    src_p = Path(src)
    try:
        rel = str(src_p.relative_to(ep))
    except ValueError:
        rel = str(src_p)
    samples, agent_id = _load_samples(src_p)
    out["agent_id"] = agent_id or src_p.stem
    out["source"] = rel
    if len(samples) < 2:
        out["ok"] = False
        out["error"] = f"need >=2 joint samples, got {len(samples)}"
        out["grade"] = "missing"
        out["n_samples"] = len(samples)
        return out

    metrics = analyze_arm_samples(
        samples,
        omega_max_rad_s=omega_max_rad_s,
        jump_rad=jump_rad,
        tcp_speed_max_m_s=tcp_speed_max_m_s,
    )
    out.update(metrics)

    if write:
        qdir = ep / "export" / "quality"
        try:
            qdir.mkdir(parents=True, exist_ok=True)
            path = qdir / "traj_quality.json"
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            out["written"] = str(path)
            try:
                out["written_rel"] = str(path.relative_to(ep))
            except ValueError:
                out["written_rel"] = "export/quality/traj_quality.json"
        except OSError as e:
            out["ok"] = False
            out["error"] = f"write failed: {e}"
    return out


def run_traj_quality_root(
    root: str | Path,
    *,
    write: bool = True,
    omega_max_rad_s: float = DEFAULT_OMEGA_MAX_RAD_S,
    jump_rad: float = DEFAULT_JUMP_RAD,
    tcp_speed_max_m_s: float = DEFAULT_TCP_SPEED_MAX_M_S,
) -> dict[str, Any]:
    """Run thin traj quality on every ``episode_*`` under root."""
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        return {"ok": False, "error": f"root not found: {base}", "results": []}
    eps = sorted(p for p in base.glob("episode_*") if p.is_dir())
    results: list[dict[str, Any]] = []
    n_ok = 0
    for ep in eps:
        man = _read_json(ep / "manifest.json")
        if isinstance(man, dict) and man.get("provisional"):
            continue
        one = run_traj_quality(
            ep,
            write=write,
            omega_max_rad_s=omega_max_rad_s,
            jump_rad=jump_rad,
            tcp_speed_max_m_s=tcp_speed_max_m_s,
        )
        results.append(
            {
                "episode": ep.name,
                "ok": one.get("ok"),
                "grade": one.get("grade"),
                "overspeed_count": one.get("overspeed_count"),
                "jump_count": one.get("jump_count"),
                "error": one.get("error"),
                "written": one.get("written"),
            }
        )
        if one.get("ok"):
            n_ok += 1
    return {
        "ok": n_ok == len(results) and bool(results),
        "root": str(base),
        "n": len(results),
        "n_ok": n_ok,
        "results": results,
    }


def read_traj_quality(episode_dir: str | Path) -> dict[str, Any] | None:
    ep = Path(episode_dir)
    return _read_json(ep / "export" / "quality" / "traj_quality.json")
