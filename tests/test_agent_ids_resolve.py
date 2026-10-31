"""Manifest-driven require / camera alias resolution (multi-station)."""

from __future__ import annotations

import pytest

from sensors_dcs.export.agent_ids import (
    AgentResolveError,
    resolve_agent_token,
    resolve_require_list,
    suggest_master_agent,
    suggest_primary_camera,
    suggest_require_agents,
)


def _g26562_manifest() -> dict:
    return {
        "agents": [
            {"agent_id": "gello", "kind": "gello"},
            {"agent_id": "gripper-hl-read", "kind": "gripper_read"},
            {"agent_id": "gripper-hl-write", "kind": "gripper_write"},
            {"agent_id": "cam-g26562-wrist", "kind": "realsense"},
            {"agent_id": "cam-g26562-right", "kind": "realsense"},
            {"agent_id": "cam-g26562-left", "kind": "realsense"},
            {"agent_id": "arm-cs66", "kind": "arm"},
            {"agent_id": "arm-cs66-write", "kind": "arm_write"},
        ]
    }


def test_suggest_require_g26562_ids() -> None:
    ids = suggest_require_agents(_g26562_manifest())
    assert "arm-cs66" in ids
    assert "gripper-hl-read" in ids
    assert "gripper-hl-write" not in ids
    assert "cam-g26562-left" in ids
    assert "cam-g26562-wrist" in ids


def test_legacy_aliases_resolve_to_g26562() -> None:
    man = _g26562_manifest()
    resolved, log = resolve_require_list(
        "arm,cam-left,cam-right,cam-middle,gripper-read",
        manifest=man,
    )
    assert "arm-cs66" in resolved
    assert "cam-g26562-left" in resolved
    assert "cam-g26562-right" in resolved
    # middle → wrist fallback when no cam-*middle*
    assert "cam-g26562-wrist" in resolved
    assert "gripper-hl-read" in resolved
    assert {e["token"] for e in log} >= {"arm", "cam-left", "cam-middle"}


def test_ambiguous_arm_raises() -> None:
    from sensors_dcs.export.agent_ids import agents_index_from_manifest

    man = {
        "agents": [
            {"agent_id": "arm-a", "kind": "arm"},
            {"agent_id": "arm-b", "kind": "arm"},
        ]
    }
    with pytest.raises(AgentResolveError, match="ambiguous"):
        resolve_agent_token("arm", agents=agents_index_from_manifest(man))


def test_suggest_primary_prefers_wrist() -> None:
    assert suggest_primary_camera(_g26562_manifest()) == "cam-g26562-wrist"
    assert suggest_master_agent(_g26562_manifest()) == "cam-g26562-wrist"
