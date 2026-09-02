"""Convert filtered episode material into hik_gello ``data_postprocess`` layout.

Output (under ``out_dir``) mirrors ``hww/hik_gello/data_postprocess.py``:

- ``metadata.json``
- ``steps.json``  (observations / actions: joint, cartesian, gripper)
- ``rgb_<cam_name>_<i>.jpg``
- optional ``d_<cam_name>_<i>.png`` when depth frames exist under filtered cameras

Camera naming is **not** hardcoded: pass ``--camera-map`` YAML (serial → hik name)
at filter / export-hik-dataset time (same idea as hik_gello ``camera_name_refator``).
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import yaml

FkFn = Callable[[list[float]], np.ndarray]


@dataclass
class _CamStep:
    agent_id: str
    file: str
    role: str | None
    serial: str | None = None
    depth_file: str | None = None


@dataclass
class _AlignedStep:
    seq: int
    t_wall: float
    joints: list[float]  # 6
    gripper: float
    cameras: dict[str, _CamStep]  # hik_name -> cam


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _pad6(joints: list[float] | None) -> list[float]:
    j = [float(x) for x in (joints or [])]
    if len(j) < 6:
        j = j + [0.0] * (6 - len(j))
    return j[:6]


def load_camera_map_yaml(path: str | Path) -> dict[str, str]:
    """Load serial → hik camera name map from YAML.

    Accepted shapes::

        cameras:
          "317222074437": rear_left_1
          "336222075436": top

        # or
        serial_to_name:
          "317222074437": rear_left_1

        # or flat
        "317222074437": rear_left_1
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"camera map YAML not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"camera map YAML is empty: {p}")
    if not isinstance(raw, dict):
        raise ValueError(f"camera map YAML must be a mapping: {p}")

    body: Any = raw
    for key in ("cameras", "serial_to_name", "camera_name_refator", "serials"):
        if key in raw and isinstance(raw[key], dict):
            body = raw[key]
            break

    if not isinstance(body, dict) or not body:
        raise ValueError(
            f"camera map YAML has no serial→name entries "
            f"(use top-level keys or a `cameras:` block): {p}"
        )

    out: dict[str, str] = {}
    for k, v in body.items():
        if v is None:
            continue
        sk = str(k).strip()
        name = str(v).strip()
        if not sk or not name:
            continue
        out[sk] = name
    if not out:
        raise ValueError(f"camera map YAML produced empty serial→name map: {p}")
    return out


def resolve_camera_map(
    *,
    camera_map: Mapping[str, str] | None = None,
    camera_map_yaml: str | Path | None = None,
    filtered_root: Path | None = None,
) -> tuple[dict[str, str], str | None]:
    """Resolve serial→name map; prefer explicit YAML, else ``filtered/camera_map.yaml``."""
    if camera_map is not None:
        m = {str(k).strip(): str(v).strip() for k, v in camera_map.items() if v is not None}
        if not m:
            raise ValueError("camera_map is empty")
        return m, None
    if camera_map_yaml:
        p = Path(camera_map_yaml)
        return load_camera_map_yaml(p), str(p.resolve() if p.exists() else p)
    if filtered_root is not None:
        bundled = Path(filtered_root) / "camera_map.yaml"
        if bundled.is_file():
            return load_camera_map_yaml(bundled), str(bundled)
    raise ValueError(
        "camera serial→name map required: pass --camera-map <file.yaml> "
        "(or ensure export/filtered/camera_map.yaml exists from a prior filter)"
    )


def resolve_hik_camera_name(
    *,
    agent_id: str,
    serial: str | None,
    name_map: Mapping[str, str],
    role: str | None = None,
) -> str:
    """Map camera to hik name via configured serial→name YAML only."""
    del role  # naming is serial-only; role kept for call-site compatibility
    if not name_map:
        raise ValueError("name_map (from --camera-map YAML) is required")

    serial_key = str(serial).strip() if serial else ""
    if not serial_key:
        raise ValueError(
            f"camera agent {agent_id!r} has no serial; cannot resolve hik name "
            f"(record/filter must carry serial, and --camera-map keys are serials)"
        )

    if serial_key in name_map:
        return str(name_map[serial_key])

    lower = {str(k).lower(): str(v) for k, v in name_map.items()}
    if serial_key.lower() in lower:
        return lower[serial_key.lower()]

    known = ", ".join(sorted(name_map.keys())[:12])
    more = "" if len(name_map) <= 12 else f" … (+{len(name_map) - 12})"
    raise ValueError(
        f"serial {serial_key!r} (agent {agent_id!r}) not in camera map; "
        f"known serials: {known}{more}"
    )


def _pick_joint_source(
    agents_meta: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (arm_agent_id, gripper_agent_id). Prefer arm_read over gello."""
    by_kind: dict[str, list[str]] = {}
    for a in agents_meta:
        aid = a.get("agent_id")
        kind = a.get("kind")
        if not aid or not kind:
            continue
        by_kind.setdefault(str(kind), []).append(str(aid))

    arm_id = None
    for kind in ("arm_read", "gello"):
        ids = by_kind.get(kind) or []
        if ids:
            arm_id = ids[0]
            break

    grip_ids = by_kind.get("gripper_read") or []
    grip_id = grip_ids[0] if grip_ids else None
    return arm_id, grip_id


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


def build_steps(
    aligned: list[_AlignedStep],
    *,
    fk: FkFn | None,
    tcp_xyz: tuple[float, float, float] = (0.0, 0.0, 0.18),
) -> dict[str, Any]:
    """Build steps.json structure matching data_postprocess.parse_pickle_data."""
    steps: dict[str, Any] = {
        "actions": {
            "cartesian_position": [],
            "gripper_position": [[]],
        },
        "observations": {
            "cartesian_position": [],
            "joint_position": [],
            "gripper_position": [[]],
        },
    }
    tcp = _tcp_matrix(tcp_xyz)
    poses: list[np.ndarray | None] = []

    for item in aligned:
        steps["observations"]["joint_position"].append(list(item.joints))
        steps["observations"]["gripper_position"][0].append(float(item.gripper))
        if fk is None:
            poses.append(None)
            steps["observations"]["cartesian_position"].append([0.0] * 6)
        else:
            cur_ee = np.asarray(fk(list(item.joints)), dtype=np.float64) @ tcp
            poses.append(cur_ee)
            steps["observations"]["cartesian_position"].append(_pose_to_xyzrpy(cur_ee))

    for i, item in enumerate(aligned):
        if fk is None or poses[i] is None:
            steps["actions"]["cartesian_position"].append([0.0] * 6)
            grip_next = float(aligned[i + 1].gripper) if i + 1 < len(aligned) else 0.0
            steps["actions"]["gripper_position"][0].append(grip_next if i + 1 < len(aligned) else 0.0)
            continue
        if i == len(aligned) - 1:
            steps["actions"]["cartesian_position"].append([0.0] * 6)
            steps["actions"]["gripper_position"][0].append(0.0)
            continue
        cur_ee = poses[i]
        next_ee = poses[i + 1]
        assert cur_ee is not None and next_ee is not None
        cur_action = np.linalg.inv(cur_ee) @ next_ee
        steps["actions"]["cartesian_position"].append(_pose_to_xyzrpy(cur_action))
        steps["actions"]["gripper_position"][0].append(float(aligned[i + 1].gripper))

    return steps


def intrinsics_from_episode_cameras(
    cameras: Mapping[str, Any] | None,
    hik_to_agent: Mapping[str, str],
) -> dict[str, list[list[float]]]:
    """Map episode ``manifest.cameras[agent_id].intrinsic_matrix`` → hik rgb_/d_ keys."""
    out: dict[str, list[list[float]]] = {}
    if not cameras:
        return out
    for hik_name, agent_id in hik_to_agent.items():
        info = cameras.get(agent_id)
        if not isinstance(info, dict):
            continue
        k = info.get("intrinsic_matrix")
        if isinstance(k, list) and k:
            out[hik_name] = list(k)
            out[f"rgb_{hik_name}"] = list(k)
        dk = info.get("depth_intrinsic_matrix")
        if isinstance(dk, list) and dk:
            out[f"d_{hik_name}"] = list(dk)
        elif isinstance(k, list) and k:
            out[f"d_{hik_name}"] = list(k)
    return out


def create_metadata(
    *,
    robot_name: str,
    hik_camera_names: list[str],
    natural_language: str = "",
    include_depth: bool = False,
    intrinsic: Mapping[str, list[list[float]]] | None = None,
    extrinsic: Mapping[str, list[list[float]]] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sensor_list: list[str] = []
    for name in hik_camera_names:
        sensor_list.append(f"rgb_{name}")
        if include_depth:
            sensor_list.append(f"d_{name}")
    metadata: dict[str, Any] = {
        "name": "hik_ebai_data",
        "robot": robot_name,
        "task_morphology": "single_arm",
        "dof": 6,
        "sensor_list": sensor_list,
        "rgb_camera_num": len([s for s in sensor_list if s.startswith("rgb_")]),
        "depth_camera_num": len([s for s in sensor_list if s.startswith("d_")]),
        "natural_language": natural_language,
        "extrinsic_matrix": {},
        "intrinsic_matrix": {},
    }
    for name in hik_camera_names:
        rgb_key = f"rgb_{name}"
        eye = np.eye(4).tolist()
        k_eye = np.eye(3).tolist()
        metadata["extrinsic_matrix"][rgb_key] = list(
            (extrinsic or {}).get(rgb_key) or (extrinsic or {}).get(name) or eye
        )
        metadata["intrinsic_matrix"][rgb_key] = list(
            (intrinsic or {}).get(rgb_key) or (intrinsic or {}).get(name) or k_eye
        )
        if include_depth:
            d_key = f"d_{name}"
            metadata["extrinsic_matrix"][d_key] = list(
                (extrinsic or {}).get(d_key) or metadata["extrinsic_matrix"][rgb_key]
            )
            metadata["intrinsic_matrix"][d_key] = list(
                (intrinsic or {}).get(d_key) or metadata["intrinsic_matrix"][rgb_key]
            )
    if extra:
        metadata.update(dict(extra))
    return metadata


def _serials_from_source_episode(ep_dir: Path | None) -> dict[str, str]:
    """agent_id → serial from original episode camera index (first non-empty)."""
    out: dict[str, str] = {}
    if ep_dir is None:
        return out
    cameras = Path(ep_dir) / "cameras"
    if not cameras.is_dir():
        return out
    for cam_dir in cameras.iterdir():
        if not cam_dir.is_dir():
            continue
        for row in _read_jsonl(cam_dir / "index.jsonl"):
            sn = row.get("serial")
            if sn:
                out[cam_dir.name] = str(sn)
                break
    return out


def load_aligned_from_filtered(
    filtered_root: Path,
    *,
    name_map: Mapping[str, str],
    source_episode: Path | None = None,
) -> tuple[dict[str, Any], list[_AlignedStep], dict[str, str]]:
    """Load ``export/filtered`` tree into aligned steps.

    ``name_map`` is serial→hik name from ``--camera-map`` YAML (required).
    Returns (manifest, aligned_steps, hik_name -> agent_id).
    """
    if not name_map:
        raise ValueError("name_map from --camera-map YAML is required")
    filtered_root = Path(filtered_root)
    manifest_path = filtered_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"filtered manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    agents_meta = list(manifest.get("agents") or [])
    arm_id, grip_id = _pick_joint_source(agents_meta)
    source_serials = _serials_from_source_episode(source_episode)

    states_by_aid: dict[str, dict[int, dict[str, Any]]] = {}
    for path in sorted((filtered_root / "states").glob("*.jsonl")):
        aid = path.stem
        by_seq: dict[int, dict[str, Any]] = {}
        for row in _read_jsonl(path):
            by_seq[int(row["seq"])] = row
        states_by_aid[aid] = by_seq

    cams_by_aid: dict[str, dict[int, dict[str, Any]]] = {}
    hik_to_agent: dict[str, str] = {}
    agent_serial: dict[str, str | None] = {}
    for cam_dir in sorted((filtered_root / "cameras").glob("*")):
        if not cam_dir.is_dir():
            continue
        aid = cam_dir.name
        by_seq: dict[int, dict[str, Any]] = {}
        serial_hint: str | None = source_serials.get(aid)
        for row in _read_jsonl(cam_dir / "index.jsonl"):
            by_seq[int(row["seq"])] = row
            if row.get("serial"):
                serial_hint = str(row["serial"])
            role = row.get("role")
            hik = resolve_hik_camera_name(
                agent_id=aid,
                serial=serial_hint,
                name_map=name_map,
                role=str(role) if role is not None else None,
            )
            hik_to_agent.setdefault(hik, aid)
        cams_by_aid[aid] = by_seq
        agent_serial[aid] = serial_hint

    if not arm_id or arm_id not in states_by_aid:
        # fall back: any state with joints
        for aid, rows in states_by_aid.items():
            sample = next(iter(rows.values()), None)
            if sample and (sample.get("payload") or {}).get("joints_rad") is not None:
                arm_id = aid
                break
    if not arm_id or arm_id not in states_by_aid:
        raise ValueError(
            "no arm/gello state jsonl under filtered/states; "
            "need arm_read or gello for robot_state"
        )

    seqs = sorted(states_by_aid[arm_id].keys())
    aligned: list[_AlignedStep] = []
    for seq in seqs:
        arm_row = states_by_aid[arm_id][seq]
        payload = arm_row.get("payload") or {}
        joints = _pad6(payload.get("joints_rad"))
        gripper = 0.0
        if grip_id and grip_id in states_by_aid and seq in states_by_aid[grip_id]:
            gp = states_by_aid[grip_id][seq].get("payload") or {}
            if gp.get("position_norm") is not None:
                gripper = float(gp["position_norm"])
        elif isinstance(payload.get("joints_rad"), list) and len(payload["joints_rad"]) > 6:
            gripper = float(payload["joints_rad"][6])

        cameras: dict[str, _CamStep] = {}
        for aid, by_seq in cams_by_aid.items():
            row = by_seq.get(seq)
            if not row:
                continue
            fname = row.get("file")
            if not fname:
                continue
            role = row.get("role")
            serial = str(row["serial"]) if row.get("serial") else agent_serial.get(aid)
            hik = resolve_hik_camera_name(
                agent_id=aid,
                serial=serial,
                name_map=name_map,
                role=str(role) if role is not None else None,
            )
            depth = row.get("depth_file")
            cameras[hik] = _CamStep(
                agent_id=aid,
                file=str(fname),
                role=str(role) if role is not None else None,
                serial=serial,
                depth_file=str(depth) if depth else None,
            )

        aligned.append(
            _AlignedStep(
                seq=seq,
                t_wall=float(arm_row.get("t_wall") or 0.0),
                joints=joints,
                gripper=gripper,
                cameras=cameras,
            )
        )

    return manifest, aligned, hik_to_agent


def write_hik_dataset(
    aligned: list[_AlignedStep],
    *,
    filtered_root: Path,
    out_dir: Path,
    hik_to_agent: Mapping[str, str],
    robot_name: str = "elite",
    natural_language: str = "",
    tcp_xyz: tuple[float, float, float] = (0.0, 0.0, 0.18),
    fk: FkFn | None = None,
    clear_out: bool = True,
    calibration: Mapping[str, Any] | None = None,
    episode_cameras: Mapping[str, Any] | None = None,
    cartesian_fk_error: str | None = None,
) -> dict[str, Any]:
    """Write metadata.json, steps.json, and rgb (optional depth) images."""
    filtered_root = Path(filtered_root)
    out_dir = Path(out_dir)
    if clear_out and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hik_names = sorted({name for step in aligned for name in step.cameras})
    # Prefer stable order from first step that has all cams, else sorted
    if aligned:
        first_names = list(aligned[0].cameras.keys())
        rest = [n for n in hik_names if n not in first_names]
        hik_names = first_names + rest

    has_depth = False
    for i, step in enumerate(aligned):
        for hik_name, cam in step.cameras.items():
            src = filtered_root / "cameras" / cam.agent_id / cam.file
            if not src.is_file():
                raise FileNotFoundError(f"missing filtered image: {src}")
            dst = out_dir / f"rgb_{hik_name}_{i}.jpg"
            shutil.copy2(src, dst)
            if cam.depth_file:
                dsrc = filtered_root / "cameras" / cam.agent_id / cam.depth_file
                if dsrc.is_file():
                    shutil.copy2(dsrc, out_dir / f"d_{hik_name}_{i}.png")
                    has_depth = True

    # Prefer episode manifest cameras (open-time RealSense K); --calibration-json overrides.
    intrinsic: dict[str, list[list[float]]] = intrinsics_from_episode_cameras(
        episode_cameras, hik_to_agent
    )
    extrinsic: dict[str, list[list[float]]] = {}
    if episode_cameras:
        for hik_name, agent_id in hik_to_agent.items():
            info = episode_cameras.get(agent_id)
            if not isinstance(info, dict):
                continue
            d2c = info.get("depth_to_color")
            if isinstance(d2c, list) and d2c:
                extrinsic[f"d_{hik_name}"] = list(d2c)
    if calibration:
        for k, v in (calibration.get("intrinsic_matrix") or {}).items():
            intrinsic[str(k)] = list(v)
        for k, v in (calibration.get("extrinsic_matrix") or {}).items():
            extrinsic[str(k)] = list(v)

    metadata = create_metadata(
        robot_name=robot_name,
        hik_camera_names=hik_names,
        natural_language=natural_language,
        include_depth=has_depth,
        intrinsic=intrinsic or None,
        extrinsic=extrinsic or None,
        extra={
            "source": "sensors-dcs",
            "source_format": "dcs_filtered_v1",
            "cartesian_source": "fk" if fk is not None else "zeros_no_fk",
            **(
                {"cartesian_fk_error": cartesian_fk_error}
                if fk is None and cartesian_fk_error
                else {}
            ),
            "tcp_xyz": list(tcp_xyz),
            "camera_agent_map": dict(hik_to_agent),
            "steps": len(aligned),
            "intrinsics_source": (
                "calibration_json"
                if calibration and calibration.get("intrinsic_matrix")
                else ("episode_manifest" if intrinsic else "identity")
            ),
        },
    )
    steps = build_steps(aligned, fk=fk, tcp_xyz=tcp_xyz)

    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "steps.json").write_text(
        json.dumps(steps, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "out_dir": str(out_dir),
        "steps": len(aligned),
        "cameras": hik_names,
        "has_depth": has_depth,
        "cartesian_source": metadata["cartesian_source"],
        "rgb_files": len(aligned) * len(hik_names),
    }


def bundle_camera_map(filtered_root: Path, camera_map: Mapping[str, str], *, source_yaml: str | Path | None = None) -> Path:
    """Write ``camera_map.yaml`` next to filtered material for later reuse."""
    filtered_root = Path(filtered_root)
    filtered_root.mkdir(parents=True, exist_ok=True)
    dest = filtered_root / "camera_map.yaml"
    if source_yaml and Path(source_yaml).is_file():
        src = Path(source_yaml).resolve()
        dst = dest.resolve()
        if src != dst:
            try:
                shutil.copy2(src, dest)
            except shutil.SameFileError:
                pass
    else:
        payload = {"cameras": {str(k): str(v) for k, v in camera_map.items()}}
        dest.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest


def export_hik_dataset(
    episode: str | Path,
    *,
    filtered_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    camera_map_yaml: str | Path | None = None,
    camera_map: Mapping[str, str] | None = None,
    robot_name: str = "elite",
    natural_language: str = "",
    tcp_z: float = 0.18,
    tcp_xyz: tuple[float, float, float] | None = None,
    calibration_json: str | Path | None = None,
    fk: FkFn | None = None,
    clear_out: bool = True,
    bundle_map: bool = True,
) -> dict[str, Any]:
    """Convert ``export/filtered`` into hik_gello postprocess layout.

    Camera naming requires a serial→name YAML (``--camera-map``), or a previously
    bundled ``export/filtered/camera_map.yaml``.
    """
    ep = Path(episode)
    if not ep.is_dir():
        raise FileNotFoundError(f"episode not found: {ep}")

    if filtered_dir:
        filtered_root = Path(filtered_dir)
        if not filtered_root.is_absolute():
            filtered_root = ep / filtered_root
    else:
        filtered_root = ep / "export" / "filtered"
    if not filtered_root.is_dir():
        raise FileNotFoundError(
            f"filtered episode not found: {filtered_root}; "
            "run filter-timeline --materialize first"
        )

    if output_dir:
        out = Path(output_dir)
        if not out.is_absolute():
            out = ep / out
    else:
        out = ep / "export" / "hik_dataset"

    nmap, map_src = resolve_camera_map(
        camera_map=camera_map,
        camera_map_yaml=camera_map_yaml,
        filtered_root=filtered_root,
    )
    if bundle_map:
        bundled = bundle_camera_map(filtered_root, nmap, source_yaml=map_src)
        map_src = str(bundled)

    calibration = None
    if calibration_json:
        calibration = json.loads(Path(calibration_json).read_text(encoding="utf-8"))

    tcp = tcp_xyz if tcp_xyz is not None else (0.0, 0.0, float(tcp_z))
    cartesian_fk_error: str | None = None
    if fk is None:
        try:
            from sensors_dcs.paths import ensure_sensors_import

            ensure_sensors_import()
            from sensors.kinematics import make_hik_fk_fn

            fk = make_hik_fk_fn()
        except Exception as e:  # noqa: BLE001
            fk = None
            cartesian_fk_error = f"{type(e).__name__}: {e}"

    manifest, aligned, hik_to_agent = load_aligned_from_filtered(
        filtered_root,
        name_map=nmap,
        source_episode=ep,
    )
    if not aligned:
        raise ValueError(f"no aligned steps under {filtered_root}")

    episode_cameras = manifest.get("cameras") if isinstance(manifest.get("cameras"), dict) else {}
    if not episode_cameras:
        src_man = ep / "manifest.json"
        if src_man.is_file():
            try:
                sm = json.loads(src_man.read_text(encoding="utf-8"))
                if isinstance(sm.get("cameras"), dict):
                    episode_cameras = sm["cameras"]
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    info = write_hik_dataset(
        aligned,
        filtered_root=filtered_root,
        out_dir=out,
        hik_to_agent=hik_to_agent,
        robot_name=robot_name,
        natural_language=natural_language,
        tcp_xyz=tcp,
        fk=fk,
        clear_out=clear_out,
        calibration=calibration,
        episode_cameras=episode_cameras or None,
        cartesian_fk_error=cartesian_fk_error,
    )
    # also keep a copy beside hik output
    if bundle_map:
        shutil.copy2(filtered_root / "camera_map.yaml", out / "camera_map.yaml")

    info.update(
        {
            "source_episode": ep.name,
            "filtered_root": str(filtered_root),
            "episode_index": manifest.get("episode_index"),
            "robot": robot_name,
            "camera_map": map_src,
            "camera_serials": sorted(nmap.keys()),
        }
    )
    meta_path = out.parent / "hik_dataset_meta.json"
    if out.parent == ep / "export" or (ep / "export") in out.parents:
        meta_path = ep / "export" / "hik_dataset_meta.json"
    meta_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    info["meta_path"] = str(meta_path)
    return info
