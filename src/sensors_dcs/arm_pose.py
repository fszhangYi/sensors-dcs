"""Live arm Cartesian pose from joints (FK) for viz — same path as hik_dataset.

Joints (rad) → flange FK (``sensors.kinematics``) → TCP offset → xyz + XYZ euler.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()

# Match hik_dataset / data_postprocess default tool offset (meters).
DEFAULT_TCP_XYZ: tuple[float, float, float] = (0.0, 0.0, 0.18)

_fk_fn = None


def _get_fk():
    global _fk_fn
    if _fk_fn is None:
        from sensors.kinematics import make_hik_fk_fn

        _fk_fn = make_hik_fk_fn()
    return _fk_fn


def _tcp_matrix(tcp_xyz: tuple[float, float, float]) -> np.ndarray:
    tcp = np.eye(4, dtype=np.float64)
    tcp[:3, 3] = np.asarray(tcp_xyz, dtype=np.float64)
    return tcp


def _mat_to_euler_xyz(rot: np.ndarray) -> list[float]:
    """XYZ extrinsic euler (matches scipy Rotation.as_euler('xyz'))."""
    try:
        from scipy.spatial.transform import Rotation as R  # type: ignore

        return R.from_matrix(rot).as_euler("xyz").tolist()
    except Exception:  # noqa: BLE001
        pass
    r = np.asarray(rot, dtype=np.float64)
    sy = math.sqrt(float(r[0, 0]) ** 2 + float(r[1, 0]) ** 2)
    if sy > 1e-8:
        x = math.atan2(float(r[2, 1]), float(r[2, 2]))
        y = math.atan2(-float(r[2, 0]), sy)
        z = math.atan2(float(r[1, 0]), float(r[0, 0]))
    else:
        x = math.atan2(-float(r[1, 2]), float(r[1, 1]))
        y = math.atan2(-float(r[2, 0]), sy)
        z = 0.0
    return [x, y, z]


def _pose_to_xyzrpy(pose: np.ndarray) -> list[float]:
    return pose[:3, 3].tolist() + _mat_to_euler_xyz(pose[:3, :3])


def joints_rad_to_xyzrpy(
    joints_rad: Sequence[float] | None,
    *,
    tcp_xyz: tuple[float, float, float] = DEFAULT_TCP_XYZ,
) -> list[float] | None:
    """Return ``[x,y,z,rx,ry,rz]`` (m / rad) or ``None`` if joints unusable."""
    if joints_rad is None:
        return None
    try:
        j = [float(x) for x in list(joints_rad)[:6]]
    except (TypeError, ValueError):
        return None
    if len(j) < 6 or any(not math.isfinite(x) for x in j):
        return None
    try:
        T = np.asarray(_get_fk()(j), dtype=np.float64) @ _tcp_matrix(tcp_xyz)
        out = _pose_to_xyzrpy(T)
    except Exception:  # noqa: BLE001
        return None
    if len(out) != 6 or any(not math.isfinite(float(x)) for x in out):
        return None
    return [float(x) for x in out]


def cartesian_payload(joints_rad: Sequence[float] | None) -> dict[str, Any]:
    """Compact fields for arm agent / viz payloads."""
    xyzrpy = joints_rad_to_xyzrpy(joints_rad)
    return {
        "cartesian_xyzrpy": xyzrpy,
        "cartesian_tcp_xyz": list(DEFAULT_TCP_XYZ),
        "cartesian_frame": "base_tcp" if xyzrpy is not None else None,
    }
