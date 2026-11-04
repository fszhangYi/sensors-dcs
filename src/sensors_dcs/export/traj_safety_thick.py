"""Thick M2: episode arm.jsonl → sensors.traj_safety → quality sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sensors_dcs.export.traj_quality import _load_samples, _pick_arm_jsonl, run_traj_quality

SCHEMA_THICK = "dcs_traj_quality_v1"
SCHEMA_NEAR = "dcs_near_miss_v1"


def _merge_grade(thin_grade: str | None, geo_grade: str | None) -> str:
    order = {"missing": -1, "good": 0, "warn": 1, "bad": 2}
    a = order.get(str(thin_grade or "missing"), -1)
    b = order.get(str(geo_grade or "missing"), -1)
    best = max(a, b)
    for k, v in order.items():
        if v == best:
            return k
    return "missing"


def run_traj_safety_thick(
    episode: str | Path,
    *,
    arm_jsonl: str | Path | None = None,
    write: bool = True,
    profile_id: str = "elite_cs66",
    safety_profile_path: str | Path | None = None,
    stride: int = 1,
    also_thin: bool = True,
) -> dict[str, Any]:
    """Geometric thick M2 (+ optional thin dynamics merge).

    Writes:
      export/quality/traj_quality.json   (kind=thick or merged)
      export/quality/near_miss.json
    """
    from sensors.traj_safety import analyze_joint_trajectory
    from sensors_dcs.export.timeline import resolve_episode_dir

    ep = resolve_episode_dir(episode)
    src = Path(arm_jsonl).expanduser() if arm_jsonl else _pick_arm_jsonl(ep)
    out: dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA_THICK,
        "kind": "thick",
        "note": (
            "Thick M2 via sensors.traj_safety (sphere/capsule proxies + FK link poses). "
            "Not mesh/URDF Phase C."
        ),
        "episode": ep.name,
        "episode_path": str(ep),
        "agent_id": None,
        "source": None,
        "grade": None,
        "error": None,
        "geometry": None,
        "thin": None,
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
    if len(samples) < 1:
        out["ok"] = False
        out["error"] = "no joint samples"
        out["grade"] = "missing"
        return out

    joints = [s["joints"] for s in samples]
    try:
        geo = analyze_joint_trajectory(
            joints,
            profile=profile_id,
            safety_profile_path=str(safety_profile_path) if safety_profile_path else None,
            stride=stride,
        )
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        out["grade"] = "missing"
        return out

    out["geometry"] = geo
    thin_meta = None
    if also_thin and len(samples) >= 2:
        thin_meta = run_traj_quality(ep, arm_jsonl=src_p, write=False)
        out["thin"] = {
            "grade": thin_meta.get("grade"),
            "overspeed_count": thin_meta.get("overspeed_count"),
            "jump_count": thin_meta.get("jump_count"),
            "tcp_overspeed_count": thin_meta.get("tcp_overspeed_count"),
            "gates": thin_meta.get("gates"),
        }

    geo_grade = str(geo.get("grade") or "missing")
    thin_grade = (thin_meta or {}).get("grade") if thin_meta else None
    out["grade"] = _merge_grade(thin_grade, geo_grade)
    out["n_table"] = geo.get("n_table")
    out["n_self"] = geo.get("n_self")
    out["n_near_miss"] = geo.get("n_near_miss")
    out["profile_id"] = geo.get("profile_id")
    out["kinematics_profile_id"] = geo.get("kinematics_profile_id")

    near_doc = {
        "schema": SCHEMA_NEAR,
        "episode": ep.name,
        "source": rel,
        "profile_id": geo.get("profile_id"),
        "events": list(geo.get("near_miss") or []),
        "table_hits": list(geo.get("table_hits") or []),
        "self_hits": list(geo.get("self_hits") or []),
        "n_near_miss": geo.get("n_near_miss"),
        "n_table": geo.get("n_table"),
        "n_self": geo.get("n_self"),
        "grade": geo_grade,
    }

    if write:
        qdir = ep / "export" / "quality"
        try:
            qdir.mkdir(parents=True, exist_ok=True)
            tq = qdir / "traj_quality.json"
            tq.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            nm = qdir / "near_miss.json"
            nm.write_text(
                json.dumps(near_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            out["written"] = str(tq)
            out["written_near_miss"] = str(nm)
            out["written_rel"] = "export/quality/traj_quality.json"
            out["written_near_miss_rel"] = "export/quality/near_miss.json"
        except OSError as e:
            out["ok"] = False
            out["error"] = f"write failed: {e}"
    return out


def run_traj_safety_thick_root(
    root: str | Path,
    *,
    write: bool = True,
    profile_id: str = "elite_cs66",
    safety_profile_path: str | Path | None = None,
    stride: int = 1,
    also_thin: bool = True,
) -> dict[str, Any]:
    """Batch thick M2 on every ``episode_*`` under root."""
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        return {"ok": False, "error": f"root not found: {base}", "items": []}
    items: list[dict[str, Any]] = []
    n_ok = 0
    by_grade = {"good": 0, "warn": 0, "bad": 0, "missing": 0}
    for ep in sorted(p for p in base.glob("episode_*") if p.is_dir()):
        man = ep / "manifest.json"
        if man.is_file():
            try:
                raw = json.loads(man.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("provisional"):
                    continue
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        one = run_traj_safety_thick(
            ep,
            write=write,
            profile_id=profile_id,
            safety_profile_path=safety_profile_path,
            stride=stride,
            also_thin=also_thin,
        )
        grade = str(one.get("grade") or "missing")
        if grade not in by_grade:
            grade = "missing"
        by_grade[grade] = int(by_grade[grade]) + 1
        items.append(
            {
                "episode": ep.name,
                "ok": one.get("ok"),
                "grade": one.get("grade"),
                "n_table": one.get("n_table"),
                "n_self": one.get("n_self"),
                "n_near_miss": one.get("n_near_miss"),
                "written": one.get("written"),
                "error": one.get("error"),
            }
        )
        if one.get("ok"):
            n_ok += 1
    return {
        "ok": n_ok == len(items) and len(items) > 0,
        "root": str(base),
        "ok_count": n_ok,
        "failed": len(items) - n_ok,
        "episodes": len(items),
        "by_grade": by_grade,
        "items": items,
    }
