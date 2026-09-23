"""Multi-sensor alignment overview figure for filter-timeline.

Writes a compact PNG that shows:
  1. Per-sensor sample swimlanes over relative time (kept vs dropped)
  2. match_dt (ms) traces vs time with per-agent thresholds
  3. Per-sensor match_dt histograms on kept rows

Uses OpenCV (already a hard dependency) so desktop bundles that exclude
matplotlib still produce the figure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

_PALETTE_BGR = (
    (143, 157, 42),   # #2a9d8f
    (81, 111, 231),   # #e76f51
    (83, 70, 38),     # #264653
    (106, 196, 233),  # #e9c46a
    (157, 123, 69),   # #457b9d
    (97, 162, 244),   # #f4a261
    (87, 53, 29),     # #1d3557
    (220, 218, 168),  # #a8dadc
)

_MAX_PLOT_ROWS = 1600
_BG = (18, 18, 22)
_PANEL = (28, 30, 36)
_GRID = (55, 58, 66)
_TEXT = (220, 220, 225)
_MUTED = (140, 145, 155)
_ACCENT = (143, 157, 42)  # teal-ish BGR


def list_match_dt_agents(columns: Sequence[str]) -> list[str]:
    out: list[str] = []
    for col in columns:
        if col.endswith(".match_dt"):
            out.append(col[: -len(".match_dt")])
    return out


def _percentile(arr: np.ndarray, q: float) -> float:
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def _grade_for_score(score: float) -> str:
    if score >= 85.0:
        return "excellent"
    if score >= 70.0:
        return "good"
    if score >= 55.0:
        return "fair"
    return "poor"


def compute_align_quality(
    df,
    *,
    mask: Sequence[bool] | np.ndarray,
    master: str | None = None,
    require: Sequence[str] | None = None,
    default_max_dt: float = 0.033,
    per_agent_max_dt: dict[str, float] | None = None,
    rows_out: int | None = None,
    trimmed_start: int = 0,
    trimmed_end: int = 0,
    drop_reasons: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute alignment quality metrics and a 0–100 composite score.

    Score (weights fixed for UI/docs consistency)::

        score = 100 * (0.35 * keep_rate + 0.45 * sync + 0.20 * budget)

    - **keep_rate**: ``rows_out / rows_in`` (fallback: kept-mask fraction)
    - **sync**: mean over require agents of ``clamp(1 − mean(|match_dt|)/max_dt)``
      on kept rows (master contributes 1.0)
    - **budget**: same with p95 instead of mean (headroom before the threshold)
    """
    per_agent_max_dt = dict(per_agent_max_dt or {})
    keep = np.asarray(mask, dtype=bool)
    rows_in = int(len(df)) if df is not None else 0
    kept_n = int(keep.sum()) if keep.size else 0
    if rows_out is None:
        rows_out = kept_n
    keep_rate = (float(rows_out) / float(rows_in)) if rows_in > 0 else 0.0

    agents = list(require) if require else list_match_dt_agents(list(df.columns) if df is not None else [])
    if df is not None:
        agents = [a for a in agents if f"{a}.match_dt" in df.columns] or list_match_dt_agents(list(df.columns))
    else:
        agents = []

    per_agent: list[dict[str, Any]] = []
    sync_parts: list[float] = []
    budget_parts: list[float] = []

    for agent in agents:
        max_dt = float(per_agent_max_dt.get(agent, default_max_dt))
        is_master = bool(master) and agent == master
        col = f"{agent}.match_dt"
        stats: dict[str, Any] = {
            "agent": agent,
            "is_master": is_master,
            "max_match_dt_s": max_dt,
            "max_match_dt_ms": max_dt * 1000.0,
            "n": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "sync": 1.0 if is_master else 0.0,
            "budget": 1.0 if is_master else 0.0,
        }
        if df is None or col not in df.columns or kept_n == 0:
            per_agent.append(stats)
            sync_parts.append(float(stats["sync"]))
            budget_parts.append(float(stats["budget"]))
            continue
        series = np.asarray(df.loc[keep, col], dtype=float)
        series = series[np.isfinite(series)]
        series = np.abs(series)
        stats["n"] = int(series.size)
        if series.size == 0:
            if is_master:
                stats["mean_ms"] = 0.0
                stats["p50_ms"] = 0.0
                stats["p95_ms"] = 0.0
                stats["max_ms"] = 0.0
            per_agent.append(stats)
            sync_parts.append(float(stats["sync"]))
            budget_parts.append(float(stats["budget"]))
            continue
        mean_s = float(series.mean())
        p50_s = _percentile(series, 50)
        p95_s = _percentile(series, 95)
        max_s = float(series.max())
        stats["mean_ms"] = mean_s * 1000.0
        stats["p50_ms"] = p50_s * 1000.0
        stats["p95_ms"] = p95_s * 1000.0
        stats["max_ms"] = max_s * 1000.0
        if is_master:
            stats["sync"] = 1.0
            stats["budget"] = 1.0
        elif max_dt > 0:
            stats["sync"] = float(np.clip(1.0 - mean_s / max_dt, 0.0, 1.0))
            stats["budget"] = float(np.clip(1.0 - p95_s / max_dt, 0.0, 1.0))
        else:
            stats["sync"] = 0.0
            stats["budget"] = 0.0
        per_agent.append(stats)
        sync_parts.append(float(stats["sync"]))
        budget_parts.append(float(stats["budget"]))

    sync = float(np.mean(sync_parts)) if sync_parts else 0.0
    budget = float(np.mean(budget_parts)) if budget_parts else 0.0
    score = 100.0 * (0.35 * keep_rate + 0.45 * sync + 0.20 * budget)
    score = float(np.clip(score, 0.0, 100.0))

    return {
        "score": round(score, 1),
        "grade": _grade_for_score(score),
        "weights": {"keep_rate": 0.35, "sync": 0.45, "budget": 0.20},
        "components": {
            "keep_rate": round(keep_rate, 4),
            "sync": round(sync, 4),
            "budget": round(budget, 4),
        },
        "rows_in": rows_in,
        "rows_out": int(rows_out),
        "kept_mask": kept_n,
        "trimmed_start": int(trimmed_start),
        "trimmed_end": int(trimmed_end),
        "drop_reasons": dict(drop_reasons or {}),
        "agents": per_agent,
        "master": master,
        "default_max_dt": float(default_max_dt),
    }


def _rel_time(df) -> np.ndarray:
    if "t_rel" in df.columns:
        t = np.asarray(df["t_rel"], dtype=float)
        if np.isfinite(t).any():
            return t
    if "t_wall" not in df.columns:
        return np.arange(len(df), dtype=float)
    tw = np.asarray(df["t_wall"], dtype=float)
    finite = tw[np.isfinite(tw)]
    t0 = float(finite.min()) if len(finite) else 0.0
    return tw - t0


def _stride_indices(n: int, max_rows: int = _MAX_PLOT_ROWS) -> np.ndarray:
    if n <= max_rows:
        return np.arange(n, dtype=int)
    step = int(np.ceil(n / float(max_rows)))
    idx = np.arange(0, n, step, dtype=int)
    if idx[-1] != n - 1:
        idx = np.concatenate([idx, np.array([n - 1], dtype=int)])
    return idx


def _agent_threshold(
    agent: str,
    *,
    default_max_dt: float,
    per_agent_max_dt: dict[str, float] | None,
) -> float:
    if per_agent_max_dt and agent in per_agent_max_dt:
        return float(per_agent_max_dt[agent])
    return float(default_max_dt)


def _hex_ok() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _put(img: np.ndarray, text: str, org: tuple[int, int], *, color=_TEXT, scale=0.45, thick=1) -> None:
    import cv2

    cv2.putText(
        img,
        text,
        org,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thick,
        lineType=cv2.LINE_AA,
    )


def _panel_rect(
    canvas: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> None:
    import cv2

    cv2.rectangle(canvas, (x0, y0), (x1, y1), _PANEL, thickness=-1)
    cv2.rectangle(canvas, (x0, y0), (x1, y1), _GRID, thickness=1)


def _map_x(t: float, t_min: float, t_max: float, x0: int, x1: int) -> int:
    if not np.isfinite(t) or t_max <= t_min:
        return x0
    u = (float(t) - t_min) / (t_max - t_min)
    u = min(1.0, max(0.0, u))
    return int(round(x0 + u * (x1 - x0)))


def _map_y(v: float, v_min: float, v_max: float, y0: int, y1: int) -> int:
    """Map value → pixel y (top=y0 is high values)."""
    if not np.isfinite(v) or v_max <= v_min:
        return y1
    u = (float(v) - v_min) / (v_max - v_min)
    u = min(1.0, max(0.0, u))
    return int(round(y1 - u * (y1 - y0)))


def plot_filter_alignment(
    df,
    *,
    mask: Sequence[bool] | np.ndarray,
    out_path: str | Path,
    master: str | None = None,
    require: Sequence[str] | None = None,
    default_max_dt: float = 0.033,
    per_agent_max_dt: dict[str, float] | None = None,
    trim_start_idx: int | None = None,
    trim_end_idx: int | None = None,
    title: str | None = None,
    dpi: int = 110,
) -> dict[str, Any]:
    """Render alignment overview PNG from the *aligned* wide table + keep mask.

    Returns a small dict with ``ok``, ``path``, and summary counts. On missing
    OpenCV or empty input, returns ``ok=False`` without raising.
    """
    del dpi  # kept for call-site compatibility with older matplotlib API
    import cv2

    out = Path(out_path).expanduser().resolve()
    info: dict[str, Any] = {
        "ok": False,
        "path": str(out),
        "agents": [],
        "rows": int(len(df)) if df is not None else 0,
        "kept": 0,
        "error": None,
    }
    if not _hex_ok():
        info["error"] = "opencv not available"
        return info
    if df is None or len(df) == 0:
        info["error"] = "empty frame"
        return info

    keep = np.asarray(mask, dtype=bool)
    if keep.shape[0] != len(df):
        info["error"] = f"mask length {keep.shape[0]} != rows {len(df)}"
        return info
    info["kept"] = int(keep.sum())

    agents = list(require) if require else list_match_dt_agents(list(df.columns))
    agents = [a for a in agents if f"{a}.match_dt" in df.columns]
    if not agents:
        agents = list_match_dt_agents(list(df.columns))
    if not agents:
        info["error"] = "no *.match_dt columns"
        return info
    info["agents"] = agents

    t_all = _rel_time(df)
    idx = _stride_indices(len(df))
    t = t_all[idx]
    keep_s = keep[idx]
    n_agents = len(agents)

    t_finite = t[np.isfinite(t)]
    t_min = float(t_finite.min()) if len(t_finite) else 0.0
    t_max = float(t_finite.max()) if len(t_finite) else 1.0
    if t_max <= t_min:
        t_max = t_min + 1.0

    # Canvas layout (px)
    width = 1280
    margin = 16
    label_w = 150
    top_h = 36
    lane_h = max(120, 28 * n_agents + 40)
    dt_h = 220
    hist_h = 200
    gap = 14
    height = margin + top_h + lane_h + gap + dt_h + gap + hist_h + margin
    canvas = np.full((height, width, 3), _BG, dtype=np.uint8)

    try:
        # Title
        ttl = title or (
            f"Alignment overview — kept {info['kept']}/{info['rows']} "
            f"(x=missing, dim=dropped, bright=kept)"
        )
        _put(canvas, ttl[:110], (margin, margin + 22), scale=0.55, thick=1)

        # ---- swimlane panel ----
        lx0, ly0 = margin + label_w, margin + top_h
        lx1, ly1 = width - margin, margin + top_h + lane_h
        _panel_rect(canvas, lx0, ly0, lx1, ly1)

        # Trim window shading
        if trim_start_idx is not None and trim_end_idx is not None and len(t_all):
            lo = int(max(0, trim_start_idx))
            hi = int(min(len(t_all) - 1, trim_end_idx))
            if hi >= lo:
                t_lo = float(t_all[lo]) if np.isfinite(t_all[lo]) else t_min
                t_hi = float(t_all[hi]) if np.isfinite(t_all[hi]) else t_max
                x_lo = _map_x(t_lo, t_min, t_max, lx0, lx1)
                x_hi = _map_x(t_hi, t_min, t_max, lx0, lx1)
                overlay = canvas.copy()
                cv2.rectangle(overlay, (x_lo, ly0), (x_hi, ly1), _ACCENT, thickness=-1)
                cv2.addWeighted(overlay, 0.12, canvas, 0.88, 0, canvas)
                cv2.line(canvas, (x_lo, ly0), (x_lo, ly1), _ACCENT, 1, cv2.LINE_AA)
                cv2.line(canvas, (x_hi, ly0), (x_hi, ly1), _ACCENT, 1, cv2.LINE_AA)

        row_h = max(16, (ly1 - ly0 - 20) // max(1, n_agents))
        for i, agent in enumerate(agents):
            col = f"{agent}.match_dt"
            series = np.asarray(df[col], dtype=float)[idx]
            present = np.isfinite(series)
            y = ly0 + 12 + i * row_h + row_h // 2
            color = _PALETTE_BGR[i % len(_PALETTE_BGR)]
            tag = agent + (" *" if master and agent == master else "")
            _put(canvas, tag[:22], (margin + 4, y + 4), color=color, scale=0.4)

            t_src = t - np.where(present, series, 0.0)
            miss = ~present
            drop_pts = present & (~keep_s)
            kept_pts = present & keep_s

            if miss.any():
                for xv in t[miss]:
                    px = _map_x(float(xv), t_min, t_max, lx0, lx1)
                    cv2.drawMarker(
                        canvas, (px, y), _MUTED, markerType=cv2.MARKER_TILTED_CROSS,
                        markerSize=6, thickness=1, line_type=cv2.LINE_AA,
                    )
            if drop_pts.any():
                for xv in t_src[drop_pts]:
                    px = _map_x(float(xv), t_min, t_max, lx0, lx1)
                    cv2.circle(canvas, (px, y), 2, tuple(int(c * 0.45) for c in color), -1, cv2.LINE_AA)
            if kept_pts.any():
                for xv in t_src[kept_pts]:
                    px = _map_x(float(xv), t_min, t_max, lx0, lx1)
                    cv2.circle(canvas, (px, y), 3, color, -1, cv2.LINE_AA)

        _put(canvas, "sensors / t_rel", (lx0 + 4, ly0 + 14), color=_MUTED, scale=0.35)

        # ---- match_dt panel ----
        dx0, dy0 = margin + label_w, ly1 + gap
        dx1, dy1 = width - margin, dy0 + dt_h
        _panel_rect(canvas, dx0, dy0, dx1, dy1)
        _put(
            canvas,
            "match_dt (ms) over time (dotted = threshold)",
            (dx0 + 4, dy0 + 16),
            color=_MUTED,
            scale=0.4,
        )

        # Collect series for y-scale
        dt_max = default_max_dt * 1000.0 * 1.5
        series_ms: list[tuple[str, np.ndarray, tuple[int, int, int], float]] = []
        for i, agent in enumerate(agents):
            col = f"{agent}.match_dt"
            series = np.asarray(df[col], dtype=float)[idx] * 1000.0
            color = _PALETTE_BGR[i % len(_PALETTE_BGR)]
            thr_ms = _agent_threshold(
                agent, default_max_dt=default_max_dt, per_agent_max_dt=per_agent_max_dt
            ) * 1000.0
            finite = series[np.isfinite(series)]
            if len(finite):
                dt_max = max(dt_max, float(np.percentile(finite, 99)) if len(finite) > 4 else float(finite.max()))
            series_ms.append((agent, series, color, thr_ms))
        dt_max = max(dt_max, 5.0)
        plot_top = dy0 + 28
        plot_bot = dy1 - 18

        # Grid lines
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            yy = _map_y(dt_max * frac, 0.0, dt_max, plot_top, plot_bot)
            cv2.line(canvas, (dx0, yy), (dx1, yy), _GRID, 1, cv2.LINE_AA)
            _put(canvas, f"{dt_max * frac:.0f}", (margin + 4, yy + 4), color=_MUTED, scale=0.35)

        # Trim lines
        if trim_start_idx is not None and trim_end_idx is not None and len(t_all):
            lo = int(max(0, trim_start_idx))
            hi = int(min(len(t_all) - 1, trim_end_idx))
            if hi >= lo:
                for tv in (t_all[lo], t_all[hi]):
                    if np.isfinite(tv):
                        xx = _map_x(float(tv), t_min, t_max, dx0, dx1)
                        cv2.line(canvas, (xx, plot_top), (xx, plot_bot), _ACCENT, 1, cv2.LINE_AA)

        for agent, series, color, thr_ms in series_ms:
            present = np.isfinite(series)
            pts = []
            for xv, yv in zip(t[present], series[present]):
                pts.append(
                    (
                        _map_x(float(xv), t_min, t_max, dx0, dx1),
                        _map_y(float(yv), 0.0, dt_max, plot_top, plot_bot),
                    )
                )
            if len(pts) >= 2:
                arr = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
                cv2.polylines(canvas, [arr], False, color, 1, cv2.LINE_AA)
            elif len(pts) == 1:
                cv2.circle(canvas, pts[0], 2, color, -1, cv2.LINE_AA)
            thr_y = _map_y(thr_ms, 0.0, dt_max, plot_top, plot_bot)
            # dotted threshold
            for x in range(dx0, dx1, 8):
                cv2.line(canvas, (x, thr_y), (min(x + 4, dx1), thr_y), color, 1, cv2.LINE_AA)

        # ---- histogram panel ----
        hx0, hy0 = margin + label_w, dy1 + gap
        hx1, hy1 = width - margin, hy0 + hist_h
        _panel_rect(canvas, hx0, hy0, hx1, hy1)
        _put(
            canvas,
            "kept-row match_dt histogram (ms)",
            (hx0 + 4, hy0 + 16),
            color=_MUTED,
            scale=0.4,
        )

        hist_xmax = default_max_dt * 1000.0 * 1.2
        kept_vals: list[tuple[str, np.ndarray, tuple[int, int, int]]] = []
        for i, agent in enumerate(agents):
            col = f"{agent}.match_dt"
            series = np.asarray(df[col], dtype=float)
            vals = series[keep & np.isfinite(series)] * 1000.0
            color = _PALETTE_BGR[i % len(_PALETTE_BGR)]
            if len(vals):
                hist_xmax = max(
                    hist_xmax,
                    float(np.percentile(vals, 99)) if len(vals) > 4 else float(vals.max()),
                )
            kept_vals.append((agent, vals, color))
        hist_xmax = max(hist_xmax, 5.0)
        n_bins = 36
        bins = np.linspace(0.0, hist_xmax, n_bins + 1)
        # density max across agents
        dens_max = 1e-6
        dens_list: list[np.ndarray] = []
        for _, vals, _ in kept_vals:
            if len(vals) == 0:
                dens_list.append(np.zeros(n_bins))
                continue
            hist, _ = np.histogram(vals, bins=bins, density=True)
            dens_list.append(hist.astype(float))
            dens_max = max(dens_max, float(hist.max()) if len(hist) else dens_max)

        plot_top_h = hy0 + 28
        plot_bot_h = hy1 - 22
        bar_group = max(1, (hx1 - hx0 - 4) // n_bins)
        bar_w = max(1, bar_group // max(1, n_agents))

        for bi in range(n_bins):
            for ai, (agent, vals, color) in enumerate(kept_vals):
                dens = dens_list[ai][bi] if bi < len(dens_list[ai]) else 0.0
                if dens <= 0:
                    continue
                x = hx0 + 2 + bi * bar_group + ai * bar_w
                bh = int(round((dens / dens_max) * (plot_bot_h - plot_top_h)))
                y1 = plot_bot_h
                y0 = max(plot_top_h, y1 - bh)
                cv2.rectangle(
                    canvas,
                    (x, y0),
                    (x + max(1, bar_w - 1), y1),
                    color,
                    thickness=-1,
                )

        thr_x = _map_x(default_max_dt * 1000.0, 0.0, hist_xmax, hx0, hx1)
        for y in range(plot_top_h, plot_bot_h, 8):
            cv2.line(canvas, (thr_x, y), (thr_x, min(y + 4, plot_bot_h)), (83, 70, 38), 1, cv2.LINE_AA)
        _put(canvas, "0", (hx0 + 2, hy1 - 6), color=_MUTED, scale=0.35)
        _put(canvas, f"{hist_xmax:.0f} ms", (hx1 - 70, hy1 - 6), color=_MUTED, scale=0.35)

        # Legend (agents + counts)
        legend_y = hy0 + 34
        for i, (agent, vals, color) in enumerate(kept_vals):
            lx = hx0 + 8 + (i % 4) * 220
            ly = legend_y + (i // 4) * 16
            cv2.rectangle(canvas, (lx, ly - 8), (lx + 10, ly + 2), color, -1)
            _put(canvas, f"{agent} n={len(vals)}", (lx + 14, ly), color=_TEXT, scale=0.35)

        out.parent.mkdir(parents=True, exist_ok=True)
        ok = bool(cv2.imwrite(str(out), canvas))
        if not ok or not out.is_file() or out.stat().st_size <= 0:
            info["error"] = f"failed to write PNG: {out}"
            return info
        info["ok"] = True
        info["error"] = None
        info["bytes"] = out.stat().st_size
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
    return info
