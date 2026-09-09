#!/usr/bin/env python3
"""D12: overlay wall vs hw_ts QC histograms for baseline good/mid/bad.

Writes ``COMPARE_wall_vs_hw.png`` under the reports dir (cam_dt overlay per ep
+ bar chart of cam_dt_p95).

Example::

    python scripts/plot_compare_wall_vs_hw.py \\
      --baseline-dir docs/portfolio/baseline
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
import numpy as np  # noqa: E402


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


def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def plot_compare(
    baseline_dir: Path,
    *,
    out_png: Path,
    camera_primary: str = "cam-middle",
    gap_factor: float = 2.5,
) -> dict[str, dict[str, dict[str, Any]]]:
    qc = _load_qc_mod()
    reports_dir = baseline_dir / "reports"
    tags = ("good", "mid", "bad")
    bundles: dict[str, dict[str, Any]] = {}
    reports: dict[str, dict[str, dict[str, Any]]] = {}

    for tag in tags:
        ep = baseline_dir / f"ep_{tag}"
        reports[tag] = {}
        for clock, key in (("wall", "wall"), ("hw_ts", "hw")):
            sync = reports_dir / f"sync_{key}_{tag}.json"
            bundle = qc.collect_sync_series(
                ep,
                camera_primary=camera_primary,
                gap_factor=gap_factor,
                align_clock=clock,
            )
            rep = bundle["report"]
            if sync.is_file():
                rep = {**rep, **_load_report(sync)}
            bundles[f"{tag}_{key}"] = bundle
            reports[tag][key] = rep

    fig = plt.figure(figsize=(12, 10), constrained_layout=True)
    gs = fig.add_gridspec(4, 2, height_ratios=[1.1, 1.1, 1.1, 1.2])

    for i, tag in enumerate(tags):
        ax = fig.add_subplot(gs[i, 0])
        w = _ms(bundles[f"{tag}_wall"]["cam_dts"])
        h = _ms(bundles[f"{tag}_hw"]["cam_dts"])
        xmax = max(
            float(np.percentile(w, 99)) if w else 100.0,
            float(np.percentile(h, 99)) if h else 100.0,
            80.0,
        )
        bins = np.linspace(0, xmax, 40)
        ax.hist(w, bins=bins, alpha=0.45, label="wall", color="#4C78A8", density=True)
        ax.hist(h, bins=bins, alpha=0.45, label="hw_ts", color="#F58518", density=True)
        ax.set_title(f"{tag}: cam_dt distribution")
        ax.set_xlabel("cam Δt (ms)")
        ax.set_ylabel("density")
        ax.legend(loc="upper right", fontsize=8)

        ax2 = fig.add_subplot(gs[i, 1])
        ws = _ms(bundles[f"{tag}_wall"]["state_cam_abs_dts"])
        hs = _ms(bundles[f"{tag}_hw"]["state_cam_abs_dts"])
        xmax2 = max(
            float(np.percentile(ws, 99)) if ws else 40.0,
            float(np.percentile(hs, 99)) if hs else 40.0,
            40.0,
        )
        bins2 = np.linspace(0, xmax2, 40)
        ax2.hist(ws, bins=bins2, alpha=0.45, label="wall", color="#4C78A8", density=True)
        ax2.hist(hs, bins=bins2, alpha=0.45, label="hw_ts", color="#F58518", density=True)
        ax2.set_title(f"{tag}: state↔cam |Δt| (wall-domain MVP)")
        ax2.set_xlabel("|Δt| (ms)")
        ax2.set_ylabel("density")
        ax2.legend(loc="upper right", fontsize=8)

    axb = fig.add_subplot(gs[3, :])
    x = np.arange(len(tags))
    width = 0.35
    cam_w_ms = [(reports[t]["wall"].get("cam_dt_p95") or 0) * 1000 for t in tags]
    cam_h_ms = [(reports[t]["hw"].get("cam_dt_p95") or 0) * 1000 for t in tags]
    bars1 = axb.bar(x - width / 2, cam_w_ms, width, label="wall cam_dt_p95", color="#4C78A8")
    bars2 = axb.bar(x + width / 2, cam_h_ms, width, label="hw_ts cam_dt_p95", color="#F58518")
    axb.set_xticks(x)
    axb.set_xticklabels(list(tags))
    axb.set_ylabel("cam_dt_p95 (ms)")
    axb.set_title("Before/after: primary-camera frame interval p95")
    axb.legend(loc="upper left")
    for b in list(bars1) + list(bars2):
        h = b.get_height()
        axb.annotate(
            f"{h:.1f}",
            xy=(b.get_x() + b.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    fig.suptitle(
        "W2 D12: wall vs hw_ts QC (MVP — state join still on paired t_wall)",
        fontsize=12,
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return reports


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Overlay wall vs hw_ts QC for D12")
    p.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path("docs/portfolio/baseline"),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="PNG path (default: <baseline>/reports/COMPARE_wall_vs_hw.png)",
    )
    p.add_argument("--camera-primary", default="cam-middle")
    p.add_argument("--gap-factor", type=float, default=2.5)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = args.baseline_dir.expanduser().resolve()
    out = (
        args.out.expanduser().resolve()
        if args.out
        else base / "reports" / "COMPARE_wall_vs_hw.png"
    )
    plot_compare(
        base,
        out_png=out,
        camera_primary=args.camera_primary,
        gap_factor=args.gap_factor,
    )
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
