"""Piecewise rate-policy JSON and subsample helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensors_dcs.export.rate_policy import (
    fixed_hz_policy,
    hz_at_fraction,
    load_rate_policy,
    parse_rate_policy,
    save_rate_policy,
    subsample_times_policy,
)
from sensors_dcs.export.timeline import subsample_times


def test_parse_fine_middle_segments() -> None:
    policy = parse_rate_policy(
        {
            "name": "fine_middle_v1",
            "version": 1,
            "type": "piecewise_progress",
            "axis": "time_fraction",
            "segments": [
                {"start": 0.0, "end": 0.10, "hz": 5},
                {"start": 0.10, "end": 0.50, "hz": 10},
                {"start": 0.50, "end": 0.80, "hz": 15},
                {"start": 0.80, "end": 1.0, "hz": 5},
            ],
        }
    )
    assert hz_at_fraction(policy, 0.0) == 5
    assert hz_at_fraction(policy, 0.05) == 5
    assert hz_at_fraction(policy, 0.10) == 10
    assert hz_at_fraction(policy, 0.60) == 15
    assert hz_at_fraction(policy, 1.0) == 5


def test_parse_rejects_gap() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        parse_rate_policy(
            {
                "name": "bad",
                "segments": [
                    {"start": 0.0, "end": 0.4, "hz": 5},
                    {"start": 0.5, "end": 1.0, "hz": 10},
                ],
            }
        )


def test_repo_default_policies_load() -> None:
    root = Path(__file__).resolve().parents[1]
    fixed = load_rate_policy(root / "configs" / "rate_policies" / "fixed_5hz.json")
    fine = load_rate_policy(root / "configs" / "rate_policies" / "fine_middle_v1.json")
    assert fixed.segments[0].hz == 5
    assert len(fine.segments) == 4


def test_subsample_piecewise_denser_in_middle() -> None:
    # 50 Hz over 10 s → clear progress bins.
    times = [1000.0 + i * 0.02 for i in range(501)]
    policy = load_rate_policy(
        Path(__file__).resolve().parents[1]
        / "configs"
        / "rate_policies"
        / "fine_middle_v1.json"
    )
    out = subsample_times_policy(times, policy)
    fixed5 = subsample_times(times, 5.0)
    assert len(out) > len(fixed5)
    assert out[0] == pytest.approx(times[0])
    assert out[-1] <= times[-1] + 1e-9


def test_fixed_policy_matches_subsample_times() -> None:
    times = [1000.0 + i * 0.02 for i in range(100)]
    a = subsample_times(times, 15.0)
    b = subsample_times_policy(times, fixed_hz_policy(15.0))
    assert a == b


def test_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "my_policy.json"
    policy = parse_rate_policy(
        {
            "name": "tmp",
            "segments": [{"start": 0.0, "end": 1.0, "hz": 8}],
        }
    )
    save_rate_policy(path, policy)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["segments"][0]["hz"] == 8
    loaded = load_rate_policy(path)
    assert loaded.name == "tmp"
    assert loaded.source_path == str(path.resolve())
