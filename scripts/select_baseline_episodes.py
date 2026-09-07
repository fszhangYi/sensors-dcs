#!/usr/bin/env python3
"""Scan episode_* dirs, grade good/mid/bad, freeze baseline + README.

D2 helper for docs/job-prep-60d/week-01.md. Stdlib only.

Example::

    python scripts/select_baseline_episodes.py \\
      --data-root /path/to/recordings \\
      --out-baseline docs/portfolio/baseline \\
      --link symlink

Dry-run (rank only, no links)::

    python scripts/select_baseline_episodes.py --data-root /path/to/recordings --dry-run
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

Grade = Literal["good", "mid", "bad"]
LinkMode = Literal["symlink", "copy", "none"]

STATE_KINDS = frozenset({"gello", "arm", "arm_read", "arm_write", "gripper_read", "gripper"})
PRIMARY_STATE_KINDS = frozenset({"gello", "arm", "arm_read", "arm_write"})


@dataclass
class CamStats:
    agent_id: str
    n_frames: int = 0
    n_jpg_missing: int = 0
    hw_ts_present: int = 0
    hw_ts_coverage: float = 0.0
    cam_dt_p50: float | None = None
    cam_dt_p95: float | None = None
    cam_gap_count: int = 0
    t_wall_backsteps: int = 0


@dataclass
class EpisodeMetrics:
    path: str
    name: str
    grade: Grade = "bad"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    valid: bool | None = None
    provisional: bool = False
    duration_s: float | None = None
    written: int | None = None
    dropped: int | None = None
    record_mode: str | None = None
    cameras: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    state_kinds: list[str] = field(default_factory=list)
    missing_agents: list[str] = field(default_factory=list)
    n_cameras: int = 0
    n_states: int = 0
    has_primary_state: bool = False
    primary_camera: str | None = None
    hw_ts_coverage: float = 0.0
    hw_ts_present: bool = False
    cam_dt_p50: float | None = None
    cam_dt_p95: float | None = None
    cam_gap_count: int = 0
    state_cam_abs_dt_p50: float | None = None
    state_cam_abs_dt_p95: float | None = None
    per_camera: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class Thresholds:
    min_duration_s: float = 15.0
    good_duration_s: float = 30.0
    gap_factor: float = 2.5  # dt > factor * median → gap
    gap_count_good_max: int = 2
    gap_count_mid_max: int = 15
    hw_good: float = 0.95
    hw_mid: float = 0.50
    state_cam_p95_good: float = 0.050  # seconds
    state_cam_p95_mid: float = 0.120
    score_good: float = 75.0
    score_mid: float = 45.0
    drop_rate_mid_max: float = 0.02
    drop_rate_bad: float = 0.08


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return float(ys[0])
    k = (len(ys) - 1) * (p / 100.0)
    f = int(math.floor(k))
    c = min(f + 1, len(ys) - 1)
    return float(ys[f] + (ys[c] - ys[f]) * (k - f))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _iter_episode_dirs(data_root: Path) -> list[Path]:
    root = data_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"data root not found: {root}")
    # Direct children episode_* OR one level of nested task dirs.
    found = [p for p in root.glob("episode_*") if p.is_dir()]
    if found:
        return sorted(found, key=lambda p: p.name)
    nested: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        nested.extend(p for p in child.glob("episode_*") if p.is_dir())
    return sorted(nested, key=lambda p: (p.parent.name, p.name))


def _load_manifest(ep: Path) -> dict[str, Any] | None:
    path = ep / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _camera_stats(
    ep: Path,
    cam_id: str,
    *,
    gap_factor: float,
) -> CamStats:
    idx_path = ep / "cameras" / cam_id / "index.jsonl"
    rows = _read_jsonl(idx_path)
    st = CamStats(agent_id=cam_id, n_frames=len(rows))
    if not rows:
        return st

    t_walls: list[float] = []
    for row in rows:
        tw = row.get("t_wall")
        if isinstance(tw, (int, float)):
            t_walls.append(float(tw))
        cts = row.get("color_timestamp")
        if cts is not None and cts != "":
            st.hw_ts_present += 1
        fname = row.get("file")
        if fname:
            if not (ep / "cameras" / cam_id / str(fname)).is_file():
                st.n_jpg_missing += 1

    st.hw_ts_coverage = st.hw_ts_present / max(st.n_frames, 1)

    dts: list[float] = []
    for i in range(1, len(t_walls)):
        d = t_walls[i] - t_walls[i - 1]
        if d < 0:
            st.t_wall_backsteps += 1
        else:
            dts.append(d)
    st.cam_dt_p50 = _pct(dts, 50)
    st.cam_dt_p95 = _pct(dts, 95)
    if dts and st.cam_dt_p50 and st.cam_dt_p50 > 0:
        thr = gap_factor * st.cam_dt_p50
        st.cam_gap_count = sum(1 for d in dts if d > thr)
    return st


def _nearest_abs_dt(query_ts: list[float], ref_ts: list[float]) -> list[float]:
    if not query_ts or not ref_ts:
        return []
    ref = sorted(ref_ts)
    out: list[float] = []
    for t in query_ts:
        i = bisect_left(ref, t)
        cands: list[float] = []
        if i < len(ref):
            cands.append(abs(ref[i] - t))
        if i > 0:
            cands.append(abs(ref[i - 1] - t))
        out.append(min(cands))
    return out


def analyze_episode(
    ep: Path,
    *,
    thresholds: Thresholds,
    primary_camera: str | None = None,
) -> EpisodeMetrics:
    ep = ep.expanduser().resolve()
    m = EpisodeMetrics(path=str(ep), name=ep.name)
    man = _load_manifest(ep)
    if man is None:
        m.error = "missing or unreadable manifest.json"
        m.reasons.append(m.error)
        m.grade = "bad"
        return m

    m.valid = None if "valid" not in man else bool(man.get("valid"))
    m.provisional = bool(man.get("provisional"))
    m.written = int(man["written"]) if man.get("written") is not None else None
    m.dropped = int(man["dropped"]) if man.get("dropped") is not None else None
    m.record_mode = str(man.get("record_mode") or "collect")
    if man.get("duration_s") is not None:
        try:
            m.duration_s = float(man["duration_s"])
        except (TypeError, ValueError):
            m.duration_s = None
    if m.duration_s is None and man.get("t_start") is not None and man.get("t_end") is not None:
        try:
            m.duration_s = float(man["t_end"]) - float(man["t_start"])
        except (TypeError, ValueError):
            pass

    declared: list[str] = []
    kind_by_id: dict[str, str] = {}
    for item in man.get("agents") or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("agent_id") or "").strip()
        if not aid:
            continue
        declared.append(aid)
        kind_by_id[aid] = str(item.get("kind") or "")

    cam_dir = ep / "cameras"
    state_dir = ep / "states"
    cameras = sorted(p.name for p in cam_dir.iterdir() if p.is_dir()) if cam_dir.is_dir() else []
    states = sorted(p.stem for p in state_dir.glob("*.jsonl")) if state_dir.is_dir() else []
    m.cameras = cameras
    m.states = states
    m.n_cameras = len(cameras)
    m.n_states = len(states)
    m.state_kinds = sorted({kind_by_id.get(s, "") for s in states if kind_by_id.get(s)})
    m.has_primary_state = any(
        kind_by_id.get(s) in PRIMARY_STATE_KINDS
        or s.startswith(("gello", "arm"))
        for s in states
    )

    for aid in declared:
        kind = kind_by_id.get(aid, "")
        if kind == "realsense" or aid.startswith("cam-"):
            if aid not in cameras:
                m.missing_agents.append(aid)
        elif kind in STATE_KINDS or not kind:
            # Prefer states/ for non-camera agents when kind known; skip cameras map-only ids.
            if kind in STATE_KINDS and aid not in states:
                m.missing_agents.append(aid)
            elif not kind and aid not in states and aid not in cameras:
                m.missing_agents.append(aid)

    if not cameras:
        m.reasons.append("no cameras/*/index.jsonl")
        m.grade = "bad"
        m.score = 0.0
        return m

    # Primary camera: CLI override → cam-middle → first with most frames.
    cam_stats = [_camera_stats(ep, c, gap_factor=thresholds.gap_factor) for c in cameras]
    m.per_camera = [asdict(cs) for cs in cam_stats]
    by_id = {cs.agent_id: cs for cs in cam_stats}

    if primary_camera and primary_camera in by_id:
        prim = by_id[primary_camera]
    elif "cam-middle" in by_id:
        prim = by_id["cam-middle"]
    else:
        prim = max(cam_stats, key=lambda c: c.n_frames)

    m.primary_camera = prim.agent_id
    m.hw_ts_coverage = float(prim.hw_ts_coverage)
    m.hw_ts_present = prim.hw_ts_coverage > 0
    m.cam_dt_p50 = prim.cam_dt_p50
    m.cam_dt_p95 = prim.cam_dt_p95
    m.cam_gap_count = prim.cam_gap_count

    # State ↔ camera nearest |Δt| on primary camera t_wall.
    cam_rows = _read_jsonl(ep / "cameras" / prim.agent_id / "index.jsonl")
    cam_ts = [float(r["t_wall"]) for r in cam_rows if isinstance(r.get("t_wall"), (int, float))]
    state_ts: list[float] = []
    for sid in states:
        if not m.has_primary_state:
            break
        kind = kind_by_id.get(sid, "")
        if kind and kind not in PRIMARY_STATE_KINDS and not sid.startswith(("gello", "arm")):
            continue
        for row in _read_jsonl(ep / "states" / f"{sid}.jsonl"):
            tw = row.get("t_wall")
            if isinstance(tw, (int, float)):
                state_ts.append(float(tw))
    # Cap samples for speed on long episodes.
    if len(state_ts) > 5000:
        step = len(state_ts) // 5000
        state_ts = state_ts[::step]
    abs_dts = _nearest_abs_dt(state_ts, cam_ts)
    m.state_cam_abs_dt_p50 = _pct(abs_dts, 50)
    m.state_cam_abs_dt_p95 = _pct(abs_dts, 95)

    m.score, m.reasons, m.grade = _score_and_grade(m, thresholds)
    return m


def _score_and_grade(
    m: EpisodeMetrics,
    th: Thresholds,
) -> tuple[float, list[str], Grade]:
    reasons: list[str] = []
    score = 0.0

    if m.valid is False:
        reasons.append("manifest.valid=false (作废/provisional)")
    elif m.provisional:
        reasons.append("provisional manifest")
    else:
        score += 15.0

    if m.n_cameras >= 1:
        score += 10.0
    if m.n_cameras >= 2:
        score += 5.0
        reasons.append(f"multi-camera ×{m.n_cameras}")
    else:
        reasons.append("single camera")

    if m.has_primary_state:
        score += 15.0
        reasons.append("has gello/arm state")
    elif m.n_states:
        score += 5.0
        reasons.append("has non-primary state only")
    else:
        reasons.append("no states/*.jsonl")

    if m.missing_agents:
        penalty = min(20.0, 8.0 * len(m.missing_agents))
        score -= penalty
        reasons.append(f"missing_agents={m.missing_agents}")

    cov = m.hw_ts_coverage
    score += 25.0 * max(0.0, min(1.0, cov))
    if cov >= th.hw_good:
        reasons.append(f"HW ts coverage {cov:.0%} (good)")
    elif cov >= th.hw_mid:
        reasons.append(f"HW ts coverage {cov:.0%} (partial)")
    else:
        reasons.append(f"HW ts coverage {cov:.0%} (weak/absent)")

    # Frame regularity
    if m.cam_gap_count <= th.gap_count_good_max:
        score += 15.0
        reasons.append(f"cam gaps={m.cam_gap_count} (stable)")
    elif m.cam_gap_count <= th.gap_count_mid_max:
        score += 7.0
        reasons.append(f"cam gaps={m.cam_gap_count} (mild)")
    else:
        score -= 5.0
        reasons.append(f"cam gaps={m.cam_gap_count} (severe)")

    if m.cam_dt_p50 and m.cam_dt_p95 and m.cam_dt_p50 > 0:
        ratio = m.cam_dt_p95 / m.cam_dt_p50
        if ratio <= 1.8:
            score += 5.0
        elif ratio >= 4.0:
            score -= 5.0
            reasons.append(f"cam_dt p95/p50={ratio:.1f}")

    # Cross-sensor align
    sc = m.state_cam_abs_dt_p95
    if sc is None:
        reasons.append("state↔cam |Δt| n/a")
    elif sc <= th.state_cam_p95_good:
        score += 15.0
        reasons.append(f"state_cam_p95={sc*1000:.1f}ms (tight)")
    elif sc <= th.state_cam_p95_mid:
        score += 7.0
        reasons.append(f"state_cam_p95={sc*1000:.1f}ms (ok)")
    else:
        score -= 5.0
        reasons.append(f"state_cam_p95={sc*1000:.1f}ms (loose)")

    dur = m.duration_s or 0.0
    if dur >= th.good_duration_s:
        score += 10.0
        reasons.append(f"duration={dur:.1f}s")
    elif dur >= th.min_duration_s:
        score += 5.0
        reasons.append(f"duration={dur:.1f}s (shortish)")
    else:
        reasons.append(f"duration={dur:.1f}s (too short)")

    written = float(m.written or 0)
    dropped = float(m.dropped or 0)
    if written + dropped > 0:
        drop_rate = dropped / (written + dropped)
        if drop_rate >= th.drop_rate_bad:
            score -= 15.0
            reasons.append(f"drop_rate={drop_rate:.1%} (high)")
        elif drop_rate >= th.drop_rate_mid_max:
            score -= 5.0
            reasons.append(f"drop_rate={drop_rate:.1%}")

    jpg_miss = sum(int(c.get("n_jpg_missing") or 0) for c in m.per_camera)
    if jpg_miss:
        score -= min(15.0, 2.0 * jpg_miss)
        reasons.append(f"missing jpg ×{jpg_miss}")

    score = max(0.0, min(100.0, score))

    # Absolute grade (hard gates first).
    hard_bad = (
        m.valid is False
        or m.provisional
        or m.n_cameras < 1
        or not m.has_primary_state
        or dur < th.min_duration_s
        or cov < 0.05
        or (m.cam_gap_count > th.gap_count_mid_max and (sc or 0) > th.state_cam_p95_mid)
    )
    if hard_bad and score < th.score_mid:
        grade: Grade = "bad"
    elif (
        score >= th.score_good
        and m.valid is not False
        and cov >= th.hw_good
        and m.has_primary_state
        and m.cam_gap_count <= th.gap_count_good_max
        and (sc is None or sc <= th.state_cam_p95_good)
        and dur >= th.good_duration_s
    ):
        grade = "good"
    elif score >= th.score_mid and m.valid is not False and m.n_cameras >= 1:
        grade = "mid"
    else:
        grade = "bad"

    return score, reasons, grade


def select_representatives(
    metrics: list[EpisodeMetrics],
) -> dict[Grade, EpisodeMetrics | None]:
    """Pick one ep per grade; fall back to relative best/median/worst."""
    if not metrics:
        return {"good": None, "mid": None, "bad": None}

    ranked = sorted(metrics, key=lambda m: m.score, reverse=True)
    by_grade: dict[Grade, list[EpisodeMetrics]] = {"good": [], "mid": [], "bad": []}
    for m in ranked:
        by_grade[m.grade].append(m)

    chosen: dict[Grade, EpisodeMetrics | None] = {
        "good": by_grade["good"][0] if by_grade["good"] else None,
        "mid": None,
        "bad": by_grade["bad"][-1] if by_grade["bad"] else None,  # worst bad
    }

    if by_grade["mid"]:
        # Prefer mid closest to score 60.
        chosen["mid"] = min(by_grade["mid"], key=lambda m: abs(m.score - 60.0))

    used = {id(x) for x in chosen.values() if x is not None}

    # Relative fallback so we always freeze 3 when pool >= 3.
    if chosen["good"] is None:
        for m in ranked:
            if id(m) not in used:
                chosen["good"] = m
                used.add(id(m))
                break
        if chosen["good"] is None and ranked:
            chosen["good"] = ranked[0]
            used.add(id(ranked[0]))

    if chosen["bad"] is None:
        for m in reversed(ranked):
            if id(m) not in used:
                chosen["bad"] = m
                used.add(id(m))
                break

    if chosen["mid"] is None:
        remaining = [m for m in ranked if id(m) not in used]
        if remaining:
            chosen["mid"] = remaining[len(remaining) // 2]
            used.add(id(chosen["mid"]))
        elif len(ranked) >= 2:
            # Only 2 unique — reuse median of all excluding good if possible.
            for m in ranked:
                if id(m) not in used or m is not chosen["good"]:
                    if m is not chosen["good"] and m is not chosen["bad"]:
                        chosen["mid"] = m
                        break
            if chosen["mid"] is None:
                chosen["mid"] = ranked[len(ranked) // 2]

    # Ensure three distinct when possible.
    picks = [chosen["good"], chosen["mid"], chosen["bad"]]
    if len(ranked) >= 3 and len({id(p) for p in picks if p}) < 3:
        chosen["good"] = ranked[0]
        chosen["mid"] = ranked[len(ranked) // 2]
        chosen["bad"] = ranked[-1]

    return chosen


def _fmt_ms(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 1000:.1f}ms"


def _fmt_s(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.3f}s"


def render_readme(
    *,
    data_root: Path,
    chosen: dict[Grade, EpisodeMetrics | None],
    all_metrics: list[EpisodeMetrics],
    link_mode: LinkMode,
    freeze_date: str | None = None,
) -> str:
    day = freeze_date or date.today().isoformat()
    sensors_lines: list[str] = []
    sample = next((m for m in (chosen["good"], chosen["mid"], chosen["bad"]) if m), None)
    if sample:
        sensors_lines.append(f"- 相机：{', '.join(sample.cameras) or '(无)'}")
        sensors_lines.append(f"- 状态：{', '.join(sample.states) or '(无)'}")
        sensors_lines.append("- 期望：主相机 index.jsonl 含 `color_timestamp`（E1）")

    def section_clean(grade: Grade, why_title: str) -> str:
        m = chosen.get(grade)
        if m is None:
            return f"## ep_{grade} — {why_title}\n\n_池中未选出（候选不足）。_\n"
        if link_mode == "copy":
            link = f"`docs/portfolio/baseline/ep_{grade}`（拷贝自 `{m.path}`）"
        elif link_mode == "none":
            link = f"未落盘；建议源 `{m.path}`"
        else:
            link = f"`docs/portfolio/baseline/ep_{grade}` → `{m.path}`"
        reasons = "; ".join(m.reasons[:8]) if m.reasons else "(无)"
        one_liners = {
            "good": "完整多源 + HW ts 较全，用作当前 wall 对齐上限。",
            "mid": "日常可训练水平，用来定 QC warn 阈值。",
            "bad": "门禁应 fail / 对齐很差的反例，不是训练正样本。",
        }
        dur = f"{m.duration_s:.1f}s" if m.duration_s is not None else "n/a"
        relative_note = ""
        if m.grade != grade:
            relative_note = (
                f"\n> 注：绝对分级为 `{m.grade}`，因该档不足，按池内相对排序充当 `ep_{grade}`。\n"
            )
        return f"""## ep_{grade} — {why_title}
{relative_note}
| 项 | 内容 |
|----|------|
| 源路径 | `{m.path}` |
| 落盘 | {link} |
| 绝对分级 | `{m.grade}`（score={m.score:.1f}） |
| 时长 | {dur} |
| 传感器 | cameras={m.cameras}; states={m.states} |
| primary_camera | `{m.primary_camera}` |
| HW ts | coverage={m.hw_ts_coverage:.1%}（present={m.hw_ts_present}） |
| cam_dt p50/p95 | {_fmt_s(m.cam_dt_p50)} / {_fmt_s(m.cam_dt_p95)}；gaps={m.cam_gap_count} |
| state↔cam \\|Δt\\| p50/p95 | {_fmt_ms(m.state_cam_abs_dt_p50)} / {_fmt_ms(m.state_cam_abs_dt_p95)} |
| valid / dropped | valid={m.valid}; written={m.written}; dropped={m.dropped} |
| 质量观察 | {reasons} |
| 导出 | `sensors-dcs export-timeline --episode <ep_dir> -o docs/portfolio/baseline/reports/tl_wall_{grade}` |

一句话：{one_liners[grade]}
"""

    ranking_rows = []
    for m in sorted(all_metrics, key=lambda x: x.score, reverse=True):
        ranking_rows.append(
            f"| `{m.name}` | {m.grade} | {m.score:.1f} | {m.hw_ts_coverage:.0%} | "
            f"{m.cam_gap_count} | {_fmt_ms(m.state_cam_abs_dt_p95)} | `{m.path}` |"
        )

    return f"""# Baseline episodes（W1 冻结）

冻结日期：{day}  
数据根：`{data_root.resolve()}`  
用途：wall-clock 同步基线；W2 起 wall vs hw_ts before/after 对照。  
约定：目录只读；改对齐逻辑不得覆盖这三条源数据。  
生成：`scripts/select_baseline_episodes.py`

## 传感器清单（以入选样本为准；差异见各条）

{chr(10).join(sensors_lines) if sensors_lines else "- （无）"}

{section_clean("good", "为何「好」")}

{section_clean("mid", "为何「中」")}

{section_clean("bad", "为何「差」")}

## 候选池排名（全部扫描）

| episode | grade | score | hw_cov | gaps | state_cam_p95 | path |
|---------|-------|------:|------:|-----:|--------------:|------|
{chr(10).join(ranking_rows)}

## 尚未量化（留给 D4/D5）

完整 `qc_episode_sync.py` JSON 与 PNG 直方图 — 本 README 已含粗指标；稳定字段名以 D4 脚本为准。
"""


def _replace_link(dst: Path, src: Path, mode: LinkMode) -> None:
    if mode == "none":
        return
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.is_dir():
        shutil.rmtree(dst)
    if mode == "symlink":
        dst.symlink_to(src.resolve())
    elif mode == "copy":
        shutil.copytree(src, dst)


def run(
    *,
    data_root: Path,
    out_baseline: Path,
    link_mode: LinkMode = "symlink",
    dry_run: bool = False,
    primary_camera: str | None = None,
    thresholds: Thresholds | None = None,
    json_out: Path | None = None,
) -> dict[str, Any]:
    th = thresholds or Thresholds()
    episodes = _iter_episode_dirs(data_root)
    if not episodes:
        raise FileNotFoundError(f"no episode_* under {data_root}")

    metrics = [
        analyze_episode(ep, thresholds=th, primary_camera=primary_camera) for ep in episodes
    ]
    chosen = select_representatives(metrics)
    effective_link: LinkMode = "none" if dry_run else link_mode

    out_baseline = out_baseline.expanduser().resolve()
    reports = out_baseline / "reports"
    if not dry_run:
        reports.mkdir(parents=True, exist_ok=True)
        for grade, m in chosen.items():
            if m is None:
                continue
            _replace_link(out_baseline / f"ep_{grade}", Path(m.path), link_mode)
        readme = render_readme(
            data_root=data_root,
            chosen=chosen,
            all_metrics=metrics,
            link_mode=effective_link,
        )
        (out_baseline / "README.md").write_text(readme, encoding="utf-8")
    else:
        readme = render_readme(
            data_root=data_root,
            chosen=chosen,
            all_metrics=metrics,
            link_mode="none",
        )

    payload: dict[str, Any] = {
        "data_root": str(data_root.resolve()),
        "out_baseline": str(out_baseline),
        "dry_run": dry_run,
        "link_mode": effective_link,
        "n_episodes": len(metrics),
        "chosen": {
            g: (None if m is None else {"name": m.name, "path": m.path, "grade": m.grade, "score": m.score})
            for g, m in chosen.items()
        },
        "episodes": [asdict(m) for m in sorted(metrics, key=lambda x: x.score, reverse=True)],
        "readme": readme,
    }

    if json_out is not None:
        json_out = json_out.expanduser().resolve()
        json_out.parent.mkdir(parents=True, exist_ok=True)
        # README text can be large; keep in separate file when writing JSON next to baseline.
        slim = dict(payload)
        slim.pop("readme", None)
        slim["readme_path"] = str(out_baseline / "README.md")
        json_out.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif not dry_run:
        (reports / "select_baseline_rank.json").write_text(
            json.dumps({k: v for k, v in payload.items() if k != "readme"}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    return payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grade episode_* as good/mid/bad and freeze baseline + README.md"
    )
    p.add_argument("--data-root", required=True, type=Path, help="recording root containing episode_*")
    p.add_argument(
        "--out-baseline",
        type=Path,
        default=Path("docs/portfolio/baseline"),
        help="baseline output dir (default: docs/portfolio/baseline)",
    )
    p.add_argument(
        "--link",
        choices=("symlink", "copy", "none"),
        default="symlink",
        help="how to place ep_good/mid/bad under out-baseline",
    )
    p.add_argument("--dry-run", action="store_true", help="rank + print README; do not write/link")
    p.add_argument("--primary-camera", default=None, help="override primary camera agent id")
    p.add_argument("--json-out", type=Path, default=None, help="optional ranking JSON path")
    p.add_argument("--min-duration-s", type=float, default=15.0)
    p.add_argument("--good-duration-s", type=float, default=30.0)
    p.add_argument("--print-readme", action="store_true", help="always print README to stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    th = Thresholds(
        min_duration_s=args.min_duration_s,
        good_duration_s=args.good_duration_s,
    )
    try:
        result = run(
            data_root=args.data_root,
            out_baseline=args.out_baseline,
            link_mode=args.link,
            dry_run=args.dry_run,
            primary_camera=args.primary_camera,
            thresholds=th,
            json_out=args.json_out,
        )
    except FileNotFoundError as e:
        print(f"error: {e}", flush=True)
        return 2

    print(f"scanned {result['n_episodes']} episodes under {result['data_root']}")
    for g in ("good", "mid", "bad"):
        c = result["chosen"][g]
        if c is None:
            print(f"  ep_{g}: (none)")
        else:
            print(f"  ep_{g}: {c['name']}  grade={c['grade']} score={c['score']:.1f}  {c['path']}")
    if args.dry_run or args.print_readme:
        print("\n----- README.md -----\n")
        print(result["readme"])
    else:
        print(f"wrote {Path(result['out_baseline']) / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
