"""E3: dataset KPI aggregation — empty / missing fields / mixed modes."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.metrics.aggregate import aggregate_metrics, write_metrics_summary


def _write_ep(
    root: Path,
    name: str,
    *,
    valid: bool = True,
    mode: str = "collect",
    duration_s: float | None = 10.0,
    outcome: str | None = "success",
    intervention_any: bool | None = False,
    align_score: float | None = None,
    provisional: bool = False,
    tags: dict | None = None,
) -> Path:
    ep = root / name
    ep.mkdir(parents=True, exist_ok=True)
    man: dict = {
        "format": "dcs_episode_v1",
        "valid": valid,
        "record_mode": mode,
    }
    if duration_s is not None:
        man["duration_s"] = duration_s
    if provisional:
        man["provisional"] = True
    if outcome is not None:
        man["task_outcome"] = {"outcome": outcome, "labels": [], "source": "operator"}
    if tags is not None:
        man["tags"] = tags
    if intervention_any is not None:
        man["intervention_summary"] = {
            "any": bool(intervention_any),
            "counts": {"teleop_on": 1} if intervention_any else {},
            "duration_s": {"teleop": 1.0 if intervention_any else 0.0, "delta_offset": 0.0},
            "event_count": 1 if intervention_any else 0,
        }
    (ep / "manifest.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    if align_score is not None:
        export = ep / "export"
        export.mkdir(exist_ok=True)
        (export / "filter_meta.json").write_text(
            json.dumps({"align_quality": {"score": align_score, "grade": "good"}}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return ep


def test_empty_root(tmp_path: Path) -> None:
    out = aggregate_metrics(tmp_path)
    assert out["ok"] is True
    k = out["kpis"]
    assert k["episodes"] == 0
    assert k["keep_rate"] is None
    assert k["completion_rate"] is None
    assert k["intervention_rate"] is None
    assert k["align_quality"]["mean"] is None
    assert k["sessions"] == 0


def test_mixed_modes_and_missing_fields(tmp_path: Path) -> None:
    _write_ep(tmp_path, "episode_00000", outcome="success", intervention_any=True, align_score=80.0)
    _write_ep(tmp_path, "episode_00001", outcome="fail", intervention_any=False, align_score=60.0)
    _write_ep(
        tmp_path,
        "episode_00002",
        valid=False,
        outcome=None,
        intervention_any=None,
        align_score=None,
        duration_s=None,
    )
    _write_ep(tmp_path, "episode_00003", mode="infer", outcome="partial", duration_s=5.0)
    _write_ep(tmp_path, "episode_00099", provisional=True, outcome="success")  # skipped

    # Nested session copy must not inflate primary rates
    nested = tmp_path / "sessions" / "sid" / "slave" / "episode_00000"
    nested.mkdir(parents=True)
    (nested / "manifest.json").write_text(
        json.dumps({"format": "dcs_episode_v1", "valid": True, "task_outcome": {"outcome": "success"}}),
        encoding="utf-8",
    )
    (tmp_path / "sessions" / "sid" / "session.json").write_text("{}", encoding="utf-8")
    merged = tmp_path / "sessions" / "sid" / "merged" / "episode_00000"
    merged.mkdir(parents=True)

    out = aggregate_metrics(tmp_path)
    assert out["ok"] is True
    k = out["kpis"]
    assert k["episodes"] == 4  # provisional skipped
    assert k["keep_rate"] == 0.75  # 3 valid / 4
    assert abs(k["completion_rate"] - (1 / 3)) < 1e-9  # success / (success+fail+partial)
    assert k["unlabeled"] == 1
    assert abs(k["unlabeled_rate"] - 0.25) < 1e-9
    assert k["with_intervention"] == 1
    assert abs(k["intervention_rate"] - 0.25) < 1e-9
    assert k["by_mode"]["collect"] == 3
    assert k["by_mode"]["infer"] == 1
    assert k["sessions"] == 1
    assert k["merged_episodes"] == 1
    assert k["align_quality"]["n"] == 2
    assert abs(k["align_quality"]["mean"] - 70.0) < 1e-9
    assert k["duration_s"]["n"] == 3
    assert k["duration_s"]["p50"] is not None


def test_write_metrics_summary(tmp_path: Path) -> None:
    _write_ep(tmp_path, "episode_00000")
    out = aggregate_metrics(tmp_path, write=True)
    assert out["ok"] is True
    assert out["written"]["ok"] is True
    path = Path(out["written"]["path"])
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "dcs_metrics_summary_v1"
    assert loaded["kpis"]["episodes"] == 1
    w2 = write_metrics_summary(tmp_path)
    assert w2["ok"] is True


def test_missing_root() -> None:
    out = aggregate_metrics("/no/such/dcs/root/ever")
    assert out["ok"] is False
    assert "error" in out


def test_tag_pivots_merge_object_completion(tmp_path: Path) -> None:
    """E4.3: same object_id across episodes merges counts + completion_rate."""
    tags_cup = {"task_id": "pick_place_v1", "env_id": "lab_table_a", "object_ids": ["cup_red"]}
    _write_ep(tmp_path, "episode_00000", outcome="success", tags=tags_cup)
    _write_ep(tmp_path, "episode_00001", outcome="fail", tags=tags_cup)
    _write_ep(
        tmp_path,
        "episode_00002",
        outcome="success",
        tags={"task_id": "pick_place_v1", "env_id": "lab_table_b", "object_ids": ["cup_red", "bowl"]},
    )
    _write_ep(tmp_path, "episode_00003", outcome="success", tags=None)  # untagged

    out = aggregate_metrics(tmp_path)
    k = out["kpis"]
    cov = k["tag_coverage"]
    assert cov["with_object_id"] == 3
    assert cov["with_task_id"] == 3
    assert cov["with_env_id"] == 3
    assert cov["untagged"] == 1

    cup = k["by_object_id"]["cup_red"]
    assert cup["episodes"] == 3
    assert cup["success"] == 2
    assert cup["fail"] == 1
    assert cup["completion_denom"] == 3
    assert abs(cup["completion_rate"] - (2 / 3)) < 1e-9

    bowl = k["by_object_id"]["bowl"]
    assert bowl["episodes"] == 1
    assert bowl["success"] == 1
    assert abs(bowl["completion_rate"] - 1.0) < 1e-9

    assert k["by_env_id"]["lab_table_a"]["episodes"] == 2
    assert k["by_task_id"]["pick_place_v1"]["episodes"] == 3
