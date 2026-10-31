from __future__ import annotations

import math
import threading
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame

# Cap in-memory gello→arm trace (~30 min @ 50 Hz).
_TRACE_MAX_SAMPLES = 100_000


def _as_float_list(values: Any, *, n: int, fill: float) -> list[float]:
    seq = list(values or [])
    out = [float(x) for x in seq[:n]]
    if len(out) < n:
        out.extend([fill] * (n - len(out)))
    return out


def apply_gello_affine(
    joints_raw: list[float],
    *,
    offsets: list[float],
    signs: list[float],
) -> list[float]:
    """q = (q_raw - offsets) * signs (same as hik-sensors GelloLeaderSensor / gello.py)."""
    n = len(joints_raw)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    return [(float(joints_raw[i]) - off[i]) * sgn[i] for i in range(n)]


def invert_gello_affine(
    joints: list[float],
    *,
    offsets: list[float],
    signs: list[float],
) -> list[float]:
    """Inverse of apply_gello_affine (sign must be ±1)."""
    n = len(joints)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    out: list[float] = []
    for i in range(n):
        s = sgn[i] if sgn[i] != 0.0 else 1.0
        out.append(float(joints[i]) / s + off[i])
    return out


class GelloAgent(BaseAgent):
    """Upper-layer agent for hik-sensors ``kind=gello`` (Dynamixel leader)."""

    kind = "gello"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 50.0,
        buffer_frames: int = 64,
        dry_run_synth: bool = True,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self.dry_run_synth = dry_run_synth
        n = len(list(getattr(sensor, "joint_ids", None) or [1, 2, 3, 4, 5, 6, 7]))
        self._n_joints = n
        # In-memory capture of calibrated joints (same space teleop sends toward the arm).
        self._trace_lock = threading.Lock()
        self._trace_recording = False
        self._trace_samples: list[dict[str, Any]] = []
        self._trace_started_wall: float | None = None
        self._trace_prev_joints: list[float] | None = None
        self._trace_dropped = 0

    def trace_status(self) -> dict[str, Any]:
        with self._trace_lock:
            return {
                "agent_id": self.agent_id,
                "recording": self._trace_recording,
                "sample_count": len(self._trace_samples),
                "dropped": self._trace_dropped,
                "started_t_wall": self._trace_started_wall,
                "max_samples": _TRACE_MAX_SAMPLES,
            }

    def set_trace_recording(self, *, enabled: bool) -> dict[str, Any]:
        """Start (clears buffer) or stop in-memory gello joint capture."""
        with self._trace_lock:
            if enabled:
                self._trace_samples = []
                self._trace_prev_joints = None
                self._trace_dropped = 0
                self._trace_started_wall = time.time()
                self._trace_recording = True
            else:
                self._trace_recording = False
            snap = {
                "agent_id": self.agent_id,
                "recording": self._trace_recording,
                "sample_count": len(self._trace_samples),
                "dropped": self._trace_dropped,
                "started_t_wall": self._trace_started_wall,
                "max_samples": _TRACE_MAX_SAMPLES,
            }
        return {"ok": True, **snap}

    def export_trace(self) -> dict[str, Any]:
        """Snapshot for download (calibrated joints = what teleop would feed the arm)."""
        with self._trace_lock:
            samples = list(self._trace_samples)
            meta = {
                "agent_id": self.agent_id,
                "recording": self._trace_recording,
                "sample_count": len(samples),
                "dropped": self._trace_dropped,
                "started_t_wall": self._trace_started_wall,
                "exported_t_wall": time.time(),
                "calib": self.calib_dict(),
                "note": (
                    "joints_rad = affine-calibrated (q=(q_raw-offsets)*signs); "
                    "first 6 are the arm follow space used by gello→arm teleop. "
                    "dmax / dmax_joint = |Δ| vs previous sample (rad)."
                ),
            }
        return {"ok": True, "meta": meta, "samples": samples}

    def _trace_append_unlocked(
        self,
        *,
        t_wall: float,
        t_mono: float,
        seq: int,
        joints_rad: list[float] | None,
        joints_rad_raw: list[float] | None,
        dry_run: bool,
        synth: bool,
        source: str = "gello_read",
    ) -> None:
        if not self._trace_recording:
            return
        if len(self._trace_samples) >= _TRACE_MAX_SAMPLES:
            self._trace_dropped += 1
            return
        j = [float(x) for x in (joints_rad or [])]
        j_raw = [float(x) for x in (joints_rad_raw or [])] if joints_rad_raw is not None else None
        dmax = None
        dmax_joint = None
        prev = self._trace_prev_joints
        if prev is not None and j:
            n = min(len(prev), len(j), 6)
            best_d = 0.0
            best_i = 0
            for i in range(n):
                d = abs(j[i] - prev[i])
                if d > best_d:
                    best_d = d
                    best_i = i
            dmax = best_d
            dmax_joint = best_i
        if j:
            self._trace_prev_joints = list(j)
        self._trace_samples.append(
            {
                "i": len(self._trace_samples),
                "t_wall": float(t_wall),
                "t_mono": float(t_mono),
                "seq": int(seq),
                "source": source,
                "joints_rad": j,
                "joints_rad_arm6": j[:6] if len(j) >= 6 else j,
                "joints_rad_raw": j_raw,
                "dmax": dmax,
                "dmax_joint": dmax_joint,
                "dry_run": bool(dry_run),
                "synth": bool(synth),
            }
        )

    def note_teleop_command(
        self,
        joints_rad: list[float],
        *,
        t_wall: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        """Optional: append the rate-limited command actually written to the arm."""
        with self._trace_lock:
            if not self._trace_recording:
                return
            j = [float(x) for x in joints_rad[:6]]
            self._trace_append_unlocked(
                t_wall=float(t_wall if t_wall is not None else time.time()),
                t_mono=time.perf_counter(),
                seq=self._seq,
                joints_rad=j,
                joints_rad_raw=None,
                dry_run=False,
                synth=False,
                source="teleop_cmd_rate_limited" if rate_limited else "teleop_cmd",
            )

    def calib_dict(self) -> dict[str, Any]:
        """Affine parameters loaded from sensors YAML (next to port)."""
        sensor = self.sensor
        ids = list(getattr(sensor, "joint_ids", None) or list(range(1, self._n_joints + 1)))
        n = len(ids)
        offsets = _as_float_list(getattr(sensor, "joint_offsets", None), n=n, fill=0.0)
        signs = _as_float_list(getattr(sensor, "joint_signs", None), n=n, fill=1.0)
        return {
            "joint_ids": ids,
            "joint_offsets": offsets,
            "joint_signs": [
                int(round(s)) if abs(s - round(s)) < 1e-12 else float(s) for s in signs
            ],
            "affine": "q=(q_raw-offsets)*signs",
        }

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        calib = self.calib_dict()
        offsets = list(calib["joint_offsets"])
        signs = [float(s) for s in calib["joint_signs"]]

        joints = sample.get("joints_rad")
        joints_raw = sample.get("joints_rad_raw")
        dry = bool(sample.get("dry_run")) or (joints is None and joints_raw is None)
        if dry and self.dry_run_synth and joints is None and joints_raw is None:
            # Synth in calibrated space, then invert so UI raw ≠ cal when affine ≠ I.
            joints = self._synth_joints(t_wall)
            joints_raw = invert_gello_affine(joints, offsets=offsets, signs=signs)
            sample = {
                **sample,
                "joints_rad": joints,
                "joints_rad_raw": joints_raw,
                "joints_raw_ticks": None,
                "dry_run": True,
                "synth": True,
            }
        else:
            if joints_raw is not None:
                joints_raw = [float(x) for x in joints_raw]
                # Always derive calibrated from raw so UI / export show affine, not a copy.
                joints = apply_gello_affine(joints_raw, offsets=offsets, signs=signs)
            elif joints is not None:
                joints = [float(x) for x in joints]
                joints_raw = invert_gello_affine(joints, offsets=offsets, signs=signs)

        payload: dict[str, Any] = {
            # Primary: affine-calibrated joints (use for follow / dataset / FK)
            "joints_rad": joints,
            # Raw Dynamixel radians before affine
            "joints_rad_raw": joints_raw,
            "joints_raw_ticks": sample.get("joints_raw_ticks"),
            "joint_ids": sample.get("joint_ids") or calib["joint_ids"],
            "joint_offsets": offsets,
            "joint_signs": calib["joint_signs"],
            "port": sample.get("port") or getattr(self.sensor, "port", None),
            "dry_run": dry,
            "synth": bool(sample.get("synth")),
        }
        j_list = [float(x) for x in joints] if joints is not None else None
        j_raw_list = [float(x) for x in joints_raw] if joints_raw is not None else None
        with self._trace_lock:
            self._trace_append_unlocked(
                t_wall=t_wall,
                t_mono=t_mono,
                seq=self._seq,
                joints_rad=j_list,
                joints_rad_raw=j_raw_list,
                dry_run=dry,
                synth=bool(sample.get("synth")),
                source="gello_read",
            )
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
        )

    def _synth_joints(self, t_wall: float) -> list[float]:
        """Sine demo so dry-run viz looks alive without hardware."""
        out: list[float] = []
        for i in range(self._n_joints):
            out.append(0.35 * math.sin(t_wall * (0.7 + 0.11 * i) + i * 0.4))
        return out
