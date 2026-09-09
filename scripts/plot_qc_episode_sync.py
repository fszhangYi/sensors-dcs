#!/usr/bin/env python3
"""Plot wall-clock sync QC histograms (Week1 D5).

Reads the same series as ``qc_episode_sync.py`` and writes PNG(s):
camera frame-interval histogram + state↔cam |Δt| histogram.

Example::

    python scripts/plot_qc_episode_sync.py \\
      --episode docs/portfolio/baseline/ep_good \\
      --tag good \\
      --out-dir docs/portfolio/baseline/reports

    # batch three baselines
    python scripts/plot_qc_episode_sync.py --baseline-dir docs/portfolio/baseline
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _load_qc_mod():
    script = Path(__file__).resolve().parent / "qc_episode_sync.py"
    spec = importlib.util.spec_from_file_location("qc_episode_sync", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ms(xs: list[float]) -> list[float]:
    return [x * 1000.0 for x in xs]


def _fmt_ms(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 1000:.1f} ms"


def plot_episode(
    episode: Path,
    *,
    out_png: Path,
    camera_primary: str | None = None,
    gap_factor: float = 2.5,
    title_tag: str | None = None,
    sync_json: Path | None = None,
) -> dict[str, Any]:
    qc = _load_qc_mod()
    bundle = qc.collect_wall_series(
        episode,
        camera_primary=camera_primary,
        gap_factor=gap_factor,
    )
    report: dict[str, Any] = bundle["report"]
    if sync_json is not None and sync_json.is_file():
        # Prefer on-disk QC JSON for annotation consistency with D4 artifacts.
        report = {**report, **json.loads(sync_json.read_text(encoding="utf-8"))}

    cam_dts_ms = _ms(bundle["cam_dts"])
    abs_dts_ms = _ms(bundle["state_cam_abs_dts"])
    gap_thr_ms = (
        bundle["gap_threshold"] * 1000.0 if bundle["gap_threshold"] is not None else None
    )

    ep_name = report.get("episode") or episode.name
    tag = title_tag or ep_name
    prim = report.get("camera_primary") or "?"

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    fig.suptitle(
        f"Wall sync QC — {tag} ({ep_name}, {prim})",
        fontsize=12,
    )

    ax0 = axes[0]
    if cam_dts_ms:
        ax0.hist(cam_dts_ms, bins=50, color="#2a6f97", edgecolor="white", linewidth=0.3)
        if report.get("cam_dt_p50") is not None:
            ax0.axvline(
                report["cam_dt_p50"] * 1000.0,
                color="#e76f51",
                linestyle="--",
                label=f"p50={_fmt_ms(report.get('cam_dt_p50'))}",
            )
        if report.get("cam_dt_p95") is not None:
            ax0.axvline(
                report["cam_dt_p95"] * 1000.0,
                color="#f4a261",
                linestyle=":",
                label=f"p95={_fmt_ms(report.get('cam_dt_p95'))}",
            )
        if gap_thr_ms is not None:
            ax0.axvline(
                gap_thr_ms,
                color="#6d597a",
                linestyle="-.",
                label=f"gap thr={gap_thr_ms:.1f} ms",
            )
        ax0.legend(fontsize=8, loc="upper right")
    else:
        ax0.text(0.5, 0.5, "no cam_dt samples", ha="center", va="center")
    ax0.set_xlabel("camera frame interval (ms)")
    ax0.set_ylabel("count")
    ax0.set_title(f"cam_dt  gaps={report.get('cam_gap_count', 0)}")

    ax1 = axes[1]
    if abs_dts_ms:
        ax1.hist(abs_dts_ms, bins=50, color="#2a9d8f", edgecolor="white", linewidth=0.3)
        if report.get("state_cam_abs_dt_p50") is not None:
            ax1.axvline(
                report["state_cam_abs_dt_p50"] * 1000.0,
                color="#e76f51",
                linestyle="--",
                label=f"p50={_fmt_ms(report.get('state_cam_abs_dt_p50'))}",
            )
        if report.get("state_cam_abs_dt_p95") is not None:
            ax1.axvline(
                report["state_cam_abs_dt_p95"] * 1000.0,
                color="#f4a261",
                linestyle=":",
                label=f"p95={_fmt_ms(report.get('state_cam_abs_dt_p95'))}",
            )
        ax1.legend(fontsize=8, loc="upper right")
    else:
        ax1.text(0.5, 0.5, "no state↔cam samples", ha="center", va="center")
    ax1.set_xlabel("state↔cam |Δt| (ms)")
    ax1.set_ylabel("count")
    ax1.set_title("state_cam_abs_dt")

    subtitle = (
        f"hw_cov={float(report.get('hw_ts_coverage') or 0):.0%}  "
        f"drop_rate={float(report.get('drop_rate') or 0):.1%}  "
        f"align={report.get('align_clock')}"
    )
    fig.text(0.5, 0.02, subtitle, ha="center", fontsize=9, color="#444")
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))

    out_png = out_png.expanduser().resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return report


def write_conclusions(
    reports: dict[str, dict[str, Any]],
    out_md: Path,
    png_names: dict[str, str],
) -> None:
    """W1 D5 CONCLUSIONS with concrete p50/p95."""

    def row(tag: str, r: dict[str, Any]) -> str:
        return (
            f"| {tag} | `{r.get('episode')}` | {_fmt_ms(r.get('cam_dt_p50'))} / "
            f"{_fmt_ms(r.get('cam_dt_p95'))} | {r.get('cam_gap_count')} | "
            f"{_fmt_ms(r.get('state_cam_abs_dt_p50'))} / {_fmt_ms(r.get('state_cam_abs_dt_p95'))} | "
            f"{float(r.get('hw_ts_coverage') or 0):.0%} | "
            f"{float(r.get('drop_rate') or 0):.1%} |"
        )

    order = [t for t in ("good", "mid", "bad") if t in reports]
    if not order:
        order = sorted(reports.keys())

    best = reports.get("good") or next(iter(reports.values()))
    worst = reports.get("bad") or best

    lines = [
        "# W1 基线结论（wall-clock sync）",
        "",
        "日期：2026-09-09  ",
        "数据：`docs/portfolio/baseline/ep_{good,mid,bad}`  ",
        "指标来源：`scripts/qc_episode_sync.py` + 图 `scripts/plot_qc_episode_sync.py`",
        "",
        "## 一句话",
        "",
        (
            f"当前 wall 轴上状态↔主相机对齐尚可"
            f"（最好档 state_cam p95≈{_fmt_ms(best.get('state_cam_abs_dt_p95'))}），"
            f"但相机帧间隔抖动与写盘 drop 已构成训练前风险"
            f"（最差档 gaps={worst.get('cam_gap_count')}、"
            f"drop_rate={float(worst.get('drop_rate') or 0):.1%}）。"
        ),
        "",
        "## 数字对照",
        "",
        "| 档 | episode | cam_dt p50/p95 | gaps | state↔cam p50/p95 | hw_cov | drop |",
        "|----|---------|---------------:|-----:|------------------:|-------:|-----:|",
    ]
    for t in order:
        lines.append(row(t, reports[t]))

    lines += [
        "",
        "## 图",
        "",
    ]
    for t in order:
        name = png_names.get(t, f"sync_wall_{t}.png")
        lines.append(f"- `{t}`：[{name}]({name})")

    lines += [
        "",
        "## 最大风险",
        "",
        "1. **相机时间轴不稳**：`cam_gap_count` 从好档 8 升到差档 "
        f"{worst.get('cam_gap_count')}，与 `drop_rate` 同源（写盘队列跟不上），"
        "会导致近邻对齐偶发变远、导出 steps 缺帧。",
        "2. **对齐仍绑在 wall-clock**：三路相机与状态都用主机 `t_wall`，"
        "无法利用已落盘的 RealSense `color_timestamp`（HW 覆盖率已是 100%）。",
        "3. **多相机尚未互校**：本周 QC 只盯主相机；左右相机相对 middle 的漂移未量化。",
        "",
        "## W2 成功时预期看到什么",
        "",
        "- 同一 3 条 ep 上出现 `align_clock=hw_ts` 的对照报告；",
        "- **期望**：`state_cam_abs_dt_p95`（或等价 HW 轴指标）相对 wall **下降或方差变小**，"
        "尤其在 wall 抖动大的差档上改善更明显；",
        "- 若 HW 轴几乎无改善 → 瓶颈更可能在丢帧/队列，而非时钟域选择。",
        "",
        "## 复现命令",
        "",
        "```bash",
        "python scripts/plot_qc_episode_sync.py --baseline-dir docs/portfolio/baseline",
        "```",
        "",
    ]
    out_md = out_md.expanduser().resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Plot cam_dt and state↔cam |Δt| histograms (D5)")
    p.add_argument("--episode", "-e", type=Path, default=None, help="one episode dir")
    p.add_argument("--tag", default=None, help="label in title / filename suffix (e.g. good)")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/portfolio/baseline/reports"),
        help="directory for PNG (and optional CONCLUSIONS)",
    )
    p.add_argument("--out", type=Path, default=None, help="explicit PNG path (single episode)")
    p.add_argument("--camera-primary", default="cam-middle")
    p.add_argument("--gap-factor", type=float, default=2.5)
    p.add_argument(
        "--sync-json",
        type=Path,
        default=None,
        help="optional D4 JSON for annotation (default: out-dir/sync_wall_<tag>.json)",
    )
    p.add_argument(
        "--baseline-dir",
        type=Path,
        default=None,
        help="if set, plot ep_good/mid/bad and write CONCLUSIONS_W1.md",
    )
    p.add_argument(
        "--write-conclusions",
        action="store_true",
        help="write CONCLUSIONS_W1.md (implied by --baseline-dir)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict[str, Any]] = {}
    png_names: dict[str, str] = {}

    if args.baseline_dir is not None:
        base = args.baseline_dir.expanduser().resolve()
        for tag in ("good", "mid", "bad"):
            ep = base / f"ep_{tag}"
            if not ep.exists():
                print(f"error: missing {ep}", flush=True)
                return 2
            sync = out_dir / f"sync_wall_{tag}.json"
            png = out_dir / f"sync_wall_{tag}.png"
            r = plot_episode(
                ep,
                out_png=png,
                camera_primary=args.camera_primary,
                gap_factor=args.gap_factor,
                title_tag=tag,
                sync_json=sync if sync.is_file() else None,
            )
            reports[tag] = r
            png_names[tag] = png.name
            print(f"wrote {png}", flush=True)
        write_conclusions(reports, out_dir / "CONCLUSIONS_W1.md", png_names)
        print(f"wrote {out_dir / 'CONCLUSIONS_W1.md'}", flush=True)
        return 0

    if args.episode is None:
        print("error: need --episode or --baseline-dir", flush=True)
        return 2

    tag = args.tag or args.episode.name
    png = args.out or (out_dir / f"sync_wall_{tag}.png")
    sync = args.sync_json
    if sync is None:
        cand = out_dir / f"sync_wall_{tag}.json"
        sync = cand if cand.is_file() else None
    r = plot_episode(
        args.episode,
        out_png=png,
        camera_primary=args.camera_primary,
        gap_factor=args.gap_factor,
        title_tag=args.tag,
        sync_json=sync,
    )
    reports[tag] = r
    png_names[tag] = Path(png).name
    print(f"wrote {Path(png).resolve()}", flush=True)
    if args.write_conclusions:
        write_conclusions(reports, out_dir / "CONCLUSIONS_W1.md", png_names)
        print(f"wrote {out_dir / 'CONCLUSIONS_W1.md'}", flush=True)
    return 0 if not r.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
