"""Master-timeline rate policies (piecewise progress → variable Hz)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RateSegment:
    """Half-open ``[start, end)`` on episode progress, except last may include 1.0."""

    start: float
    end: float
    hz: float


@dataclass(frozen=True)
class RatePolicy:
    """Offline master downsample schedule loaded from JSON."""

    name: str
    version: int
    type: str
    axis: str
    segments: tuple[RateSegment, ...]
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "axis": self.axis,
            "segments": [
                {"start": s.start, "end": s.end, "hz": s.hz} for s in self.segments
            ],
        }


def fixed_hz_policy(hz: float, *, name: str | None = None) -> RatePolicy:
    """Compat shim: uniform Hz over the whole episode."""
    h = float(hz)
    if h <= 0:
        raise ValueError(f"hz must be > 0, got {hz!r}")
    return RatePolicy(
        name=name or f"fixed_{h:g}hz",
        version=1,
        type="piecewise_progress",
        axis="time_fraction",
        segments=(RateSegment(0.0, 1.0, h),),
    )


def _as_float(val: Any, field: str) -> float:
    try:
        return float(val)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} must be a number, got {val!r}") from e


def parse_rate_policy(
    data: dict[str, Any],
    *,
    source_path: str | None = None,
) -> RatePolicy:
    """Validate and normalize a rate-policy JSON object."""
    if not isinstance(data, dict):
        raise ValueError("rate policy must be a JSON object")
    typ = str(data.get("type") or "piecewise_progress").strip()
    if typ != "piecewise_progress":
        raise ValueError(f"unsupported rate policy type: {typ!r}")
    axis = str(data.get("axis") or "time_fraction").strip()
    if axis != "time_fraction":
        raise ValueError(f"unsupported rate policy axis: {axis!r}")
    name = str(data.get("name") or "unnamed").strip() or "unnamed"
    try:
        version = int(data.get("version") or 1)
    except (TypeError, ValueError) as e:
        raise ValueError(f"version must be an int, got {data.get('version')!r}") from e

    raw_segs = data.get("segments")
    if not isinstance(raw_segs, list) or not raw_segs:
        raise ValueError("segments must be a non-empty list")

    segs: list[RateSegment] = []
    for i, item in enumerate(raw_segs):
        if not isinstance(item, dict):
            raise ValueError(f"segments[{i}] must be an object")
        start = _as_float(item.get("start"), f"segments[{i}].start")
        end = _as_float(item.get("end"), f"segments[{i}].end")
        hz = _as_float(item.get("hz"), f"segments[{i}].hz")
        if not (0.0 <= start < end <= 1.0 + 1e-12):
            raise ValueError(
                f"segments[{i}] must satisfy 0 <= start < end <= 1, got [{start}, {end}]"
            )
        if hz <= 0:
            raise ValueError(f"segments[{i}].hz must be > 0, got {hz}")
        segs.append(RateSegment(start=start, end=min(end, 1.0), hz=hz))

    segs.sort(key=lambda s: s.start)
    if abs(segs[0].start) > 1e-9:
        raise ValueError(f"first segment must start at 0, got {segs[0].start}")
    if abs(segs[-1].end - 1.0) > 1e-9:
        raise ValueError(f"last segment must end at 1, got {segs[-1].end}")
    for i in range(len(segs) - 1):
        gap = segs[i + 1].start - segs[i].end
        if abs(gap) > 1e-9:
            raise ValueError(
                f"segments must be contiguous: gap between "
                f"[{segs[i].start}, {segs[i].end}] and "
                f"[{segs[i + 1].start}, {segs[i + 1].end}]"
            )

    return RatePolicy(
        name=name,
        version=version,
        type=typ,
        axis=axis,
        segments=tuple(segs),
        source_path=source_path,
    )


def load_rate_policy(path: str | Path) -> RatePolicy:
    """Load a rate policy JSON file from disk."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"rate policy not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in rate policy {p}: {e}") from e
    return parse_rate_policy(data, source_path=str(p))


def save_rate_policy(path: str | Path, policy: RatePolicy | dict[str, Any]) -> Path:
    """Validate and write a rate policy JSON (creates parent dirs)."""
    if isinstance(policy, RatePolicy):
        parsed = policy
        payload = policy.to_dict()
    else:
        parsed = parse_rate_policy(policy)
        payload = parsed.to_dict()
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def hz_at_fraction(policy: RatePolicy, frac: float) -> float:
    """Return the target Hz for progress fraction in [0, 1]."""
    f = min(1.0, max(0.0, float(frac)))
    for i, seg in enumerate(policy.segments):
        last = i == len(policy.segments) - 1
        if last:
            if seg.start <= f <= seg.end + 1e-12:
                return seg.hz
        elif seg.start <= f < seg.end:
            return seg.hz
    return policy.segments[-1].hz


def subsample_times_policy(times: list[float], policy: RatePolicy) -> list[float]:
    """Downsample master timestamps with a piecewise (or fixed) rate policy.

    Progress axis is ``(t - t0) / (t_end - t0)`` on the master timeline.
    Same pick rule as uniform ``subsample_times``: keep the first sample at/after
    each target, advancing ``target`` by ``1/hz`` for the segment under the
    current target fraction.
    """
    if not times:
        return times
    if len(times) == 1:
        return list(times)
    t0 = float(times[0])
    t_end = float(times[-1])
    span = t_end - t0
    if span <= 1e-12:
        return [t0]

    out: list[float] = []
    target = t0
    idx = 0
    # Cap iterations: densest segment * duration * small slack.
    max_hz = max(s.hz for s in policy.segments)
    max_steps = max(8, int(span * max_hz) + len(times) + 8)
    steps = 0
    while target <= t_end + 1e-9 and steps < max_steps:
        steps += 1
        while idx + 1 < len(times) and times[idx + 1] < target + 1e-9:
            idx += 1
        out.append(times[idx])
        frac = (target - t0) / span
        hz = hz_at_fraction(policy, frac)
        target += 1.0 / hz
    return out


def resolve_rate_policy(
    *,
    rate_policy: str | Path | RatePolicy | dict[str, Any] | None = None,
    master_hz: float | None = None,
) -> RatePolicy | None:
    """Pick a policy: explicit path/object wins; else ``master_hz`` as fixed.

    Returns ``None`` when neither is set (keep all master samples).
    """
    if isinstance(rate_policy, RatePolicy):
        return rate_policy
    if isinstance(rate_policy, dict):
        return parse_rate_policy(rate_policy)
    if rate_policy is not None and str(rate_policy).strip():
        return load_rate_policy(rate_policy)
    if master_hz is not None and float(master_hz) > 0:
        return fixed_hz_policy(float(master_hz))
    return None
