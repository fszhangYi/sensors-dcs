"""Infer-tab URDF / joint mapping — aligned with sensors-arm Cell B/C catalogs.

Resolve which visual model to load from the active DCS YAML's arm_write agents
and the linked hik-sensors device (``arm_write`` vs ``arm_cs_write`` + kinematics).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

# Cache-bust when meshes / URDF change (browsers cache /assets/*.urdf).
_EC616_URDF = "/assets/models/ec616/ec616.urdf?v=ee-full-1"
_CS66_URDF = "/assets/models/cs66/cs66.urdf?v=cs66-efg-c65-3"

# Mirrors sensors-arm BUILTIN_MODELS / models_catalog.yaml (elite_ec616, elite_cs66*).
ARM_VIZ_MODELS: dict[str, dict[str, Any]] = {
    "elite_ec616": {
        "id": "elite_ec616",
        "brand": "Elite",
        "product": "EC616",
        "num_joints": 6,
        "backend": "elite",
        "kinematics": "kinematics",
        "kinematics_profile": None,
        "urdf": _EC616_URDF,
        "joint_names": ["Joint1", "Joint2", "Joint3", "Joint4", "Joint5", "Joint6"],
        "joint_offset_rad": [0.0, -1.57079632679, 0.0, -1.57079632679, 3.14159265359, 0.0],
        "joint_signs": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "mirror_y": True,
        "gripper_rpy": None,
        "browser_ik": True,
    },
    "elite_cs66": {
        "id": "elite_cs66",
        "brand": "Elite",
        "product": "CS66",
        "num_joints": 6,
        "backend": "elite",
        "kinematics": "cs66_kinematics",
        "kinematics_profile": "elite_cs66",
        "urdf": _CS66_URDF,
        "joint_names": ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
        "joint_offset_rad": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "joint_signs": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "mirror_y": False,
        "gripper_rpy": None,
        "browser_ik": False,
    },
    # Cell C — same CS66 URDF/SDK; FK/IK = cs66_kin_g26562; EOAT +180° about tool Z.
    "elite_cs66_g26562": {
        "id": "elite_cs66_g26562",
        "brand": "Elite",
        "product": "CS66",
        "num_joints": 6,
        "backend": "elite",
        "kinematics": "cs66_kin_g26562",
        "kinematics_profile": "elite_cs66",
        "urdf": _CS66_URDF,
        "joint_names": ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
        "joint_offset_rad": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "joint_signs": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "mirror_y": False,
        "gripper_rpy": [0.0, 0.0, 3.141592653589793],
        "browser_ik": False,
    },
}

_DEFAULT_MODEL_ID = "elite_ec616"


def get_arm_viz_model(model_id: str | None) -> dict[str, Any]:
    """Return a shallow copy of a built-in catalog entry."""
    mid = (model_id or _DEFAULT_MODEL_ID).strip() or _DEFAULT_MODEL_ID
    entry = ARM_VIZ_MODELS.get(mid) or ARM_VIZ_MODELS[_DEFAULT_MODEL_ID]
    return dict(entry)


def _kin_to_model_id(kinematics: str | None) -> str:
    raw = (kinematics or "").strip().lower()
    if raw in ("cs66_kin_g26562", "sensors.cs66_kin_g26562", "cs66_g26562"):
        return "elite_cs66_g26562"
    if raw in ("cs66_kinematics", "sensors.cs66_kinematics", "cs66"):
        return "elite_cs66"
    if raw:
        # Unknown CS pack — still CS66 URDF (Cell B defaults).
        if "cs66" in raw:
            return "elite_cs66"
    return "elite_cs66"


def model_id_from_device(device: dict[str, Any] | None) -> str:
    """Map a hik-sensors device dict → catalog id."""
    if not isinstance(device, dict):
        return _DEFAULT_MODEL_ID
    kind = str(device.get("kind") or "").strip().lower()
    params = device.get("params") if isinstance(device.get("params"), dict) else {}
    kin = params.get("kinematics")
    if kind == "arm_cs_write":
        return _kin_to_model_id(str(kin) if kin is not None else None)
    return _DEFAULT_MODEL_ID


def _load_devices_by_id(sensors_path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not sensors_path:
        return {}
    p = Path(str(sensors_path))
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    devices = data.get("devices") or []
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(devices, list):
        return out
    for d in devices:
        if not isinstance(d, dict):
            continue
        did = d.get("id")
        if did:
            out[str(did)] = d
    return out


def resolve_arm_viz_model(
    *,
    sensors_config: str | Path | None = None,
    agents: list[Any] | None = None,
) -> dict[str, Any]:
    """Pick Infer URDF catalog from DCS agents + sensors device list.

    Prefer ``arm_cs_write`` (CS66 Cell B/C) when any ``arm_write`` agent points at
    such a device; otherwise Elite EC616. Matches sensors-arm Cell C when
    ``kinematics: cs66_kin_g26562``.
    """
    devices = _load_devices_by_id(sensors_config)
    write_devices: list[dict[str, Any]] = []
    if agents:
        for a in agents:
            atype = getattr(a, "type", None)
            if atype is None and isinstance(a, dict):
                atype = a.get("type")
            if str(atype or "") != "arm_write":
                continue
            sid = getattr(a, "sensor_id", None)
            if sid is None and isinstance(a, dict):
                sid = a.get("sensor_id")
            if not sid:
                continue
            dev = devices.get(str(sid))
            if dev is not None:
                write_devices.append(dev)

    cs_devs = [d for d in write_devices if str(d.get("kind") or "").lower() == "arm_cs_write"]
    if cs_devs:
        # Prefer G26562 (Cell C) when several CS packs appear.
        g26562 = [
            d
            for d in cs_devs
            if model_id_from_device(d) == "elite_cs66_g26562"
        ]
        chosen = g26562[0] if g26562 else cs_devs[0]
        return get_arm_viz_model(model_id_from_device(chosen))

    if write_devices:
        return get_arm_viz_model(_DEFAULT_MODEL_ID)

    # No arm_write agents: fall back to first CS / elite device in sensors YAML.
    for d in devices.values():
        if str(d.get("kind") or "").lower() == "arm_cs_write":
            return get_arm_viz_model(model_id_from_device(d))
    return get_arm_viz_model(_DEFAULT_MODEL_ID)


def inject_arm_viz_json(html: str, arm_viz: dict[str, Any] | None, *, marker: str = "__DCS_ARM_VIZ_JSON__") -> str:
    """Replace HTML marker with JSON for ``window.__armViz``."""
    payload = arm_viz if isinstance(arm_viz, dict) else get_arm_viz_model(_DEFAULT_MODEL_ID)
    return html.replace(marker, json.dumps(payload, ensure_ascii=False))
