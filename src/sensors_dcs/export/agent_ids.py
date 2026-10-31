"""Resolve postprocess agent tokens across stations (role / kind aliases).

Legacy UI defaults use ``cam-left`` / ``arm`` / ``gripper-read``. Real episodes
may use ``cam-g26562-left`` / ``arm-cs66`` / ``gripper-hl-read``. Resolution is
driven by ``manifest.agents`` (+ optional ``role``) and column prefixes — never
by a hard-coded station name map.
"""

from __future__ import annotations

from typing import Any


class AgentResolveError(ValueError):
    """Ambiguous or unresolved require/master/primary token."""


def _norm_kind(kind: str | None) -> str:
    k = str(kind or "").strip().lower()
    if k in {"arm", "arm_read"}:
        return "arm_read"
    if k in {"arm_write"}:
        return "arm_write"
    if k in {"gripper_read", "gripper-read"}:
        return "gripper_read"
    if k in {"gripper_write", "gripper-write"}:
        return "gripper_write"
    if k in {"realsense", "camera", "cam"}:
        return "realsense"
    return k


def infer_camera_role(agent_id: str, *, role_hint: str | None = None) -> str | None:
    """Infer left/right/middle/wrist from explicit role or id suffix."""
    if role_hint:
        r = str(role_hint).strip().lower()
        # sensors YAML uses g26562_wrist etc.
        for key in ("wrist", "middle", "left", "right"):
            if r == key or r.endswith(f"_{key}") or r.endswith(f"-{key}"):
                return key
    aid = str(agent_id or "").strip().lower()
    if not aid:
        return None
    for key in ("wrist", "middle", "left", "right"):
        if (
            aid == f"cam-{key}"
            or aid.endswith(f"-{key}")
            or f"-{key}-" in aid
            or aid.endswith(f"_{key}")
        ):
            return key
    return None


def agents_index_from_manifest(manifest: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize manifest agents to ``{id, kind, role}``."""
    out: list[dict[str, str]] = []
    if not isinstance(manifest, dict):
        return out
    seen: set[str] = set()
    for item in manifest.get("agents") or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("agent_id") or item.get("id") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        kind = _norm_kind(item.get("kind") or item.get("type"))
        role = str(item.get("role") or "").strip()
        if not role and kind == "realsense":
            inferred = infer_camera_role(aid)
            role = inferred or ""
        out.append({"id": aid, "kind": kind, "role": role})
    cameras = manifest.get("cameras")
    if isinstance(cameras, dict):
        for aid_raw, meta in cameras.items():
            aid = str(aid_raw).strip()
            if not aid or aid in seen:
                continue
            seen.add(aid)
            role = ""
            if isinstance(meta, dict):
                role = str(meta.get("role") or "").strip()
            if not role:
                role = infer_camera_role(aid) or ""
            out.append({"id": aid, "kind": "realsense", "role": role})
    return out


def available_ids_from_columns(df_columns: list[str]) -> list[str]:
    skip = {"step", "t_wall", "t_rel", "t_mono"}
    agents: set[str] = set()
    for col in df_columns:
        if col in skip or "." not in col:
            continue
        agents.add(col.split(".", 1)[0])
    return sorted(agents)


def suggest_require_agents(manifest: dict[str, Any] | None) -> list[str]:
    """Default require list: all cameras + one arm_read + one gripper_read."""
    agents = agents_index_from_manifest(manifest)
    cams = [a["id"] for a in agents if a["kind"] == "realsense"]
    arms = [a["id"] for a in agents if a["kind"] == "arm_read"]
    grips = [a["id"] for a in agents if a["kind"] == "gripper_read"]
    out: list[str] = []
    out.extend(cams)
    if arms:
        out.append(arms[0])
    if grips:
        out.append(grips[0])
    return out


def suggest_primary_camera(manifest: dict[str, Any] | None) -> str | None:
    agents = agents_index_from_manifest(manifest)
    cams = [a for a in agents if a["kind"] == "realsense"]
    if not cams:
        return None
    for prefer in ("wrist", "middle", "left", "right"):
        for a in cams:
            if infer_camera_role(a["id"], role_hint=a.get("role")) == prefer:
                return a["id"]
    return cams[0]["id"]


def suggest_master_agent(manifest: dict[str, Any] | None) -> str | None:
    """Prefer wrist/middle camera, else any camera, else gello, else arm."""
    primary = suggest_primary_camera(manifest)
    if primary:
        return primary
    agents = agents_index_from_manifest(manifest)
    for prefer_kind in ("gello", "arm_read"):
        for a in agents:
            if a["kind"] == prefer_kind:
                return a["id"]
    return agents[0]["id"] if agents else None


def _resolve_camera_alias(
    token: str,
    *,
    cams: list[dict[str, str]],
) -> str:
    role_map = {
        "cam-left": "left",
        "cam_left": "left",
        "left": "left",
        "cam-right": "right",
        "cam_right": "right",
        "right": "right",
        "cam-middle": "middle",
        "cam_middle": "middle",
        "middle": "middle",
        "cam-wrist": "wrist",
        "cam_wrist": "wrist",
        "wrist": "wrist",
    }
    role = role_map.get(token)
    if role is None:
        raise AgentResolveError(f"unknown camera alias {token!r}")
    # middle may fall back to wrist (G26562) and vice versa.
    order = [role]
    if role == "middle":
        order.append("wrist")
    elif role == "wrist":
        order.append("middle")
    matches: list[str] = []
    for want in order:
        hits = [
            a["id"]
            for a in cams
            if infer_camera_role(a["id"], role_hint=a.get("role")) == want
        ]
        if hits:
            matches = hits
            break
    if not matches:
        raise AgentResolveError(
            f"alias {token!r} matched no camera (have {[a['id'] for a in cams]})"
        )
    if len(matches) > 1:
        raise AgentResolveError(
            f"alias {token!r} is ambiguous among {matches}; use a concrete agent_id"
        )
    return matches[0]


def resolve_agent_token(
    token: str,
    *,
    agents: list[dict[str, str]],
    available_ids: list[str] | None = None,
) -> str:
    """Map one require/master/primary token to a concrete agent_id."""
    raw = str(token or "").strip()
    if not raw:
        raise AgentResolveError("empty agent token")
    avail = set(available_ids or [a["id"] for a in agents])
    if raw in avail:
        return raw
    # Exact id present in manifest but not yet in columns (align may omit) — still allow.
    by_id = {a["id"]: a for a in agents}
    if raw in by_id:
        return raw

    low = raw.lower().replace("-", "_")
    cams = [a for a in agents if a["kind"] == "realsense"]
    arms = [a for a in agents if a["kind"] == "arm_read"]
    grips = [a for a in agents if a["kind"] == "gripper_read"]

    cam_key = raw.lower().replace("_", "-")
    if cam_key in {
        "cam-left",
        "left",
        "cam-right",
        "right",
        "cam-middle",
        "middle",
        "cam-wrist",
        "wrist",
    }:
        return _resolve_camera_alias(cam_key, cams=cams)

    if low in {"arm", "arm_read", "robot"}:
        if not arms:
            raise AgentResolveError(f"alias {raw!r} matched no arm_read agent")
        if len(arms) > 1:
            # Prefer literal id "arm" if present.
            for a in arms:
                if a["id"] == "arm":
                    return "arm"
            raise AgentResolveError(
                f"alias {raw!r} is ambiguous among {[a['id'] for a in arms]}; "
                "use a concrete agent_id"
            )
        return arms[0]["id"]

    if raw.lower() in {"gripper-read", "gripper_read", "gripper"} or low in {
        "gripper_read",
        "gripper",
    }:
        if not grips:
            raise AgentResolveError(f"alias {raw!r} matched no gripper_read agent")
        if len(grips) > 1:
            for a in grips:
                if a["id"] in {"gripper-read", "gripper_read"}:
                    return a["id"]
            raise AgentResolveError(
                f"alias {raw!r} is ambiguous among {[a['id'] for a in grips]}; "
                "use a concrete agent_id"
            )
        return grips[0]["id"]

    raise AgentResolveError(
        f"cannot resolve {raw!r} against agents {[a['id'] for a in agents]}"
    )


def resolve_require_list(
    spec: str | None,
    *,
    manifest: dict[str, Any] | None,
    df_columns: list[str] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Parse require CSV and resolve aliases.

    Returns ``(resolved_ids, resolve_log)`` where log entries are
    ``{token, resolved}``.
    """
    agents = agents_index_from_manifest(manifest)
    available = available_ids_from_columns(df_columns or [])
    if not spec or not str(spec).strip():
        # Infer from columns if no spec.
        if available:
            return sorted(set(available)), []
        suggested = suggest_require_agents(manifest)
        return suggested, [{"token": "*", "resolved": r} for r in suggested]

    tokens = [x.strip() for x in str(spec).split(",") if x.strip()]
    resolved: list[str] = []
    log: list[dict[str, str]] = []
    seen: set[str] = set()
    for tok in tokens:
        rid = resolve_agent_token(tok, agents=agents, available_ids=available or None)
        log.append({"token": tok, "resolved": rid})
        if rid not in seen:
            seen.add(rid)
            resolved.append(rid)
    return resolved, log
