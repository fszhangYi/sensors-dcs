"""Live arm Cartesian pose from joints (FK) for viz — same path as hik_dataset.

Joints (rad) → flange FK (``sensors.kinematics``) → TCP offset → xyz + XYZ euler.
Inverse: TCP xyzrpy → flange target → ``ik_flange`` → joints (rad).
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


def _xyzrpy_to_matrix(xyzrpy: Sequence[float]) -> np.ndarray:
    """Build 4×4 TCP pose from ``[x,y,z,rx,ry,rz]`` (m / XYZ euler rad)."""
    vals = [float(x) for x in list(xyzrpy)[:6]]
    if len(vals) < 6 or any(not math.isfinite(v) for v in vals):
        raise ValueError("xyzrpy must be 6 finite floats")
    x, y, z, rx, ry, rz = vals
    try:
        from scipy.spatial.transform import Rotation as R  # type: ignore

        rot = R.from_euler("xyz", [rx, ry, rz]).as_matrix()
    except Exception:  # noqa: BLE001
        # Fallback XYZ extrinsic: Rz @ Ry @ Rx
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        rot = np.array(
            [
                [cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
                [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
                [-sy, sx * cy, cx * cy],
            ],
            dtype=np.float64,
        )
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = rot
    T[:3, 3] = (x, y, z)
    return T


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


def xyzrpy_to_joints_rad(
    xyzrpy: Sequence[float] | None,
    *,
    q_seed_rad: Sequence[float],
    tcp_xyz: tuple[float, float, float] = DEFAULT_TCP_XYZ,
    enforce_teach_soft_limits: bool = True,
    # Infer control tolerances (default ik_flange 1e-5m / 5e-4rad is far too tight).
    position_tolerance_m: float = 2e-3,
    orientation_tolerance_rad: float = 2e-2,
    accept_residual: float = 5e-3,
) -> dict[str, Any]:
    """IK: TCP ``xyzrpy`` → ``joints_rad`` (seeded by current arm joints).

    ``next_state`` from pi05 serve is Cartesian TCP pose, not joint angles.
    """
    if xyzrpy is None:
        return {"ok": False, "error": "xyzrpy is None", "joints_rad": None}
    try:
        seed = [float(x) for x in list(q_seed_rad)[:6]]
    except (TypeError, ValueError):
        return {"ok": False, "error": "q_seed_rad must be 6 floats", "joints_rad": None}
    if len(seed) < 6 or any(not math.isfinite(x) for x in seed):
        return {"ok": False, "error": "q_seed_rad must be 6 finite floats", "joints_rad": None}
    try:
        T_tcp = _xyzrpy_to_matrix(xyzrpy)
        T_flange = T_tcp @ np.linalg.inv(_tcp_matrix(tcp_xyz))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"invalid xyzrpy: {e}", "joints_rad": None}

    try:
        from sensors.kinematics import ik_flange
        from sensors.kinematics.model import IkResult
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"IK unavailable: {e}", "joints_rad": None}

    seed_deg = np.rad2deg(np.asarray(seed, dtype=np.float64))

    def _run(*, soft_limits: bool) -> Any:
        return ik_flange(
            T_flange,
            seed_deg,
            return_details=True,
            enforce_teach_soft_limits=bool(soft_limits),
            max_nfev=1200,
            position_tolerance_m=float(position_tolerance_m),
            orientation_tolerance_rad=float(orientation_tolerance_rad),
            ori_weight=0.3,
        )

    result = None
    last_err: str | None = None
    attempts = [bool(enforce_teach_soft_limits)]
    if enforce_teach_soft_limits:
        attempts.append(False)  # retry without soft-limit bounds if needed
    for soft in attempts:
        try:
            result = _run(soft_limits=soft)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            result = None
            continue
        if not isinstance(result, IkResult):
            last_err = "IK returned unexpected type"
            result = None
            continue
        # Accept official success, or a usable residual when LM stops on ftol early.
        if result.success or float(result.residual_norm) <= float(accept_residual):
            break
        last_err = (
            f"IK did not converge ({result.message}); "
            f"residual={result.residual_norm:.3e}"
        )
        result = None

    if result is None:
        return {
            "ok": False,
            "error": last_err or "IK failed",
            "joints_rad": None,
        }

    joints = [
        float(x)
        for x in np.deg2rad(np.asarray(result.joint_deg, dtype=np.float64).reshape(6))
    ]
    if any(not math.isfinite(x) for x in joints):
        return {"ok": False, "error": "IK returned non-finite joints", "joints_rad": None}

    # Final FK check in TCP frame (same path as joints_rad_to_xyzrpy).
    achieved = joints_rad_to_xyzrpy(joints, tcp_xyz=tcp_xyz)
    pos_err = None
    if achieved is not None:
        tgt = [float(x) for x in list(xyzrpy)[:6]]
        pos_err = math.sqrt(sum((achieved[i] - tgt[i]) ** 2 for i in range(3)))
        if pos_err > float(position_tolerance_m) * 2.5:
            return {
                "ok": False,
                "error": (
                    f"IK pose check failed: pos_err={pos_err:.3e}m "
                    f"(residual={result.residual_norm:.3e})"
                ),
                "joints_rad": None,
            }

    return {
        "ok": True,
        "joints_rad": joints,
        "residual_norm": float(result.residual_norm),
        "nfev": int(result.nfev),
        "ik_success_flag": bool(result.success),
        "pos_err_m": pos_err,
    }


def cartesian_payload(joints_rad: Sequence[float] | None) -> dict[str, Any]:
    """Compact fields for arm agent / viz payloads."""
    xyzrpy = joints_rad_to_xyzrpy(joints_rad)
    return {
        "cartesian_xyzrpy": xyzrpy,
        "cartesian_tcp_xyz": list(DEFAULT_TCP_XYZ),
        "cartesian_frame": "base_tcp" if xyzrpy is not None else None,
    }
