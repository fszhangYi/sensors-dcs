"""TCP client for ``pi05_jax_sft.serve`` — gather obs, show next_state; never commands arm."""

from __future__ import annotations

import base64
import socket
import struct
import threading
import time
from typing import Any

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            pkt = sock.recv(n - len(buf))
        except OSError:
            return None
        if not pkt:
            return None
        buf += pkt
    return buf


def pack_serve_request(
    *,
    top_jpeg: bytes,
    chest_jpeg: bytes,
    wrist2_jpeg: bytes,
    text: str,
    robot_state: list[float],
) -> bytes:
    """Big-endian wire frame matching ``pi05_jax_sft.serve`` client→server."""
    if len(robot_state) != 7:
        raise ValueError(f"robot_state must be length 7, got {len(robot_state)}")
    parts: list[bytes] = []
    for jpeg in (top_jpeg, chest_jpeg, wrist2_jpeg):
        parts.append(struct.pack(">I", len(jpeg)))
        parts.append(jpeg)
    tb = (text or "").encode("utf-8")
    parts.append(struct.pack(">I", len(tb)))
    parts.append(tb)
    parts.append(struct.pack(">7f", *[float(x) for x in robot_state]))
    return b"".join(parts)


def unpack_serve_response(sock: socket.socket) -> dict[str, Any] | None:
    """Parse server→client reply. term_flag is float32 (matches serve.py send)."""
    raw = _recv_exact(sock, 28)
    if raw is None:
        return None
    next_state = list(struct.unpack(">7f", raw))
    raw = _recv_exact(sock, 4)
    if raw is None:
        return None
    term_flag = float(struct.unpack(">f", raw)[0])
    raw = _recv_exact(sock, 4)
    if raw is None:
        return None
    reject_flag = int(struct.unpack(">I", raw)[0])
    raw = _recv_exact(sock, 4)
    if raw is None:
        return None
    text_len = int(struct.unpack(">I", raw)[0])
    server_text = ""
    if text_len > 0:
        tb = _recv_exact(sock, text_len)
        if tb is None:
            return None
        server_text = tb.decode("utf-8", errors="replace")
    return {
        "next_state": next_state,
        "term_flag": term_flag,
        "reject_flag": reject_flag,
        "server_text": server_text,
    }


class _Pi05NullSensor:
    """Placeholder so BaseAgent.sensor_id works without a hik-sensors device."""

    id = "pi05-client"

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> dict[str, Any]:
        return {}


class Pi05ClientAgent(BaseAgent):
    """Client-only agent: TCP to external serve; no arm_write."""

    kind = "pi05"

    def __init__(
        self,
        *,
        agent_id: str,
        hz: float = 5.0,
        buffer_frames: int = 8,
        host: str = "127.0.0.1",
        port: int = 5000,
        prompt: str = "",
        camera_map: dict[str, str] | None = None,
        arm_agent_id: str | None = None,
        gripper_agent_id: str | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=_Pi05NullSensor(),  # type: ignore[arg-type]
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self._lock = threading.RLock()
        self._host = str(host or "127.0.0.1")
        self._port = int(port)
        self._prompt = str(prompt or "")
        self._camera_map = dict(
            camera_map
            or {
                "top": "cam-middle",
                "chest": "cam-left",
                "wrist2": "cam-right",
            }
        )
        self._arm_agent_id = arm_agent_id
        self._gripper_agent_id = gripper_agent_id
        self._peers: dict[str, BaseAgent] = {}
        self._sock: socket.socket | None = None
        self._connected = False
        self._step = 0
        self._last_latency_ms: float | None = None
        self._last_robot_state: list[float] | None = None
        self._last_next_state: list[float] | None = None
        self._last_term: float | None = None
        self._last_reject: int | None = None
        self._last_server_text: str | None = None
        self._last_ok: bool | None = None
        self._last_jpeg_lens: dict[str, int] = {}

    def bind_peers(self, agents: dict[str, BaseAgent]) -> None:
        self._peers = agents

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "connected": self._connected,
                "host": self._host,
                "port": self._port,
                "prompt": self._prompt,
                "step": self._step,
                "latency_ms": self._last_latency_ms,
                "robot_state": list(self._last_robot_state) if self._last_robot_state else None,
                "next_state": list(self._last_next_state) if self._last_next_state else None,
                "term_flag": self._last_term,
                "reject_flag": self._last_reject,
                "server_text": self._last_server_text,
                "last_ok": self._last_ok,
                "error": self._last_error,
                "jpeg_lens": dict(self._last_jpeg_lens),
                "camera_map": dict(self._camera_map),
            }

    @staticmethod
    def _normalize_host(host: str) -> str:
        h = (host or "").strip() or "127.0.0.1"
        # Bind-all addresses are not valid connect targets.
        if h in {"0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return h

    def connect(self, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        with self._lock:
            if host is not None:
                self._host = self._normalize_host(str(host))
            if port is not None:
                self._port = int(port)
            self._close_sock_unlocked()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            try:
                sock.connect((self._host, self._port))
            except OSError as e:
                sock.close()
                self._connected = False
                self._last_ok = False
                self._last_error = (
                    f"connect failed: {e}  "
                    f"(start serve on {self._host}:{self._port}, e.g. "
                    f"python3 tools/pi05_fake_serve/serve.py --port {self._port})"
                )
                return {"ok": False, "error": self._last_error, **self.status_payload()}
            sock.settimeout(None)  # idle until Step; serve also waits indefinitely
            self._sock = sock
            self._connected = True
            self._last_error = None
            self._last_ok = True
            return {"ok": True, **self.status_payload()}

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._close_sock_unlocked()
            self._connected = False
            self._last_ok = True
            self._last_error = None
            return {"ok": True, **self.status_payload()}

    def set_prompt(self, prompt: str) -> dict[str, Any]:
        with self._lock:
            self._prompt = str(prompt or "")
            return {"ok": True, **self.status_payload()}

    def step(self) -> dict[str, Any]:
        """Gather obs once, send to serve, store/echo next_state. Manual only."""
        with self._lock:
            if not self._connected or self._sock is None:
                return {
                    "ok": False,
                    "error": "not connected — connect to serve first",
                    **self.status_payload(),
                }
            prompt = self._prompt
            sock = self._sock

        try:
            jpegs, jpeg_lens = self._gather_jpegs()
            robot_state = self._gather_robot_state()
            req = pack_serve_request(
                top_jpeg=jpegs["top"],
                chest_jpeg=jpegs["chest"],
                wrist2_jpeg=jpegs["wrist2"],
                text=prompt,
                robot_state=robot_state,
            )
            t0 = time.perf_counter()
            with self._lock:
                sock = self._sock
                if sock is None:
                    raise RuntimeError("socket closed")
                sock.sendall(req)
                resp = unpack_serve_response(sock)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if resp is None:
                raise ConnectionError("serve disconnected during response")
            frame = self._push_result_frame(
                robot_state=robot_state,
                resp=resp,
                jpeg_lens=jpeg_lens,
                latency_ms=latency_ms,
                prompt=prompt,
                ok=True,
                error=None,
            )
            out = {"ok": True, **self.status_payload()}
            out["frame_seq"] = frame.seq
            return out
        except ConnectionError as e:
            with self._lock:
                self._last_ok = False
                self._last_error = str(e)
                self._close_sock_unlocked()
                self._connected = False
            self._push_status_frame()
            return {"ok": False, "error": str(e), **self.status_payload()}
        except Exception as e:  # noqa: BLE001
            # Keep TCP up for gather/protocol mistakes so user can retry Step.
            with self._lock:
                self._last_ok = False
                self._last_error = str(e)
            self._push_status_frame()
            return {"ok": False, "error": str(e), **self.status_payload()}

    def _close_sock_unlocked(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"agent-{self.agent_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 2.0, close_sensor: bool = True) -> None:
        del close_sensor  # no hik sensor
        self._stop.set()
        with self._lock:
            self._close_sock_unlocked()
            self._connected = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.05, float(join_timeout)))
        self._thread = None

    def read_frame(self) -> Frame:
        """Heartbeat status for Infer card — does not call serve."""
        return self._make_status_frame()

    def _push_status_frame(self) -> Frame:
        frame = self._make_status_frame()
        self.ring.push(frame)
        return frame

    def _make_status_frame(self) -> Frame:
        t_wall = time.time()
        t_mono = time.perf_counter()
        self._seq += 1
        with self._lock:
            payload = {
                "connected": self._connected,
                "host": self._host,
                "port": self._port,
                "prompt": self._prompt,
                "dry_run": False,
                "step": self._step,
                "robot_state": list(self._last_robot_state) if self._last_robot_state else None,
                "next_state": list(self._last_next_state) if self._last_next_state else None,
                "term_flag": self._last_term,
                "reject_flag": self._last_reject,
                "server_text": self._last_server_text,
                "latency_ms": self._last_latency_ms,
                "ok": self._last_ok,
                "error": self._last_error,
                "jpeg_lens": dict(self._last_jpeg_lens),
                "camera_map": dict(self._camera_map),
            }
            err = self._last_error
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            # Keep error=None so recorder/viz still accept status heartbeats.
            error=None,
        )

    def _push_result_frame(
        self,
        *,
        robot_state: list[float],
        resp: dict[str, Any],
        jpeg_lens: dict[str, int],
        latency_ms: float,
        prompt: str,
        ok: bool,
        error: str | None,
    ) -> Frame:
        t_wall = time.time()
        t_mono = time.perf_counter()
        self._seq += 1
        with self._lock:
            self._step += 1
            self._last_robot_state = list(robot_state)
            self._last_next_state = list(resp["next_state"])
            self._last_term = float(resp["term_flag"])
            self._last_reject = int(resp["reject_flag"])
            self._last_server_text = str(resp["server_text"] or "")
            self._last_latency_ms = latency_ms
            self._last_jpeg_lens = jpeg_lens
            self._last_ok = ok
            self._last_error = error
            step = self._step
            connected = self._connected
            host = self._host
            port = self._port
            camera_map = dict(self._camera_map)
        payload = {
            "connected": connected,
            "host": host,
            "port": port,
            "prompt": prompt,
            "dry_run": False,
            "step": step,
            "robot_state": list(robot_state),
            "next_state": list(resp["next_state"]),
            "term_flag": float(resp["term_flag"]),
            "reject_flag": int(resp["reject_flag"]),
            "server_text": str(resp["server_text"] or ""),
            "latency_ms": latency_ms,
            "ok": ok,
            "error": error,
            "jpeg_lens": jpeg_lens,
            "camera_map": camera_map,
        }
        frame = Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
            error=None,
        )
        self.ring.push(frame)
        # Also advance BaseAgent counters like a normal successful read.
        self._ok_count += 1
        return frame

    def _gather_jpegs(self) -> tuple[dict[str, bytes], dict[str, int]]:
        out: dict[str, bytes] = {}
        lens: dict[str, int] = {}
        for cam_key in ("top", "chest", "wrist2"):
            hint = str(self._camera_map.get(cam_key) or "").strip()
            raw = self._jpeg_for_hint(hint)
            if raw is None or not raw:
                raise RuntimeError(f"missing JPEG for {cam_key} (map={hint!r})")
            out[cam_key] = raw
            lens[cam_key] = len(raw)
        return out, lens

    def _jpeg_for_hint(self, hint: str) -> bytes | None:
        if not hint:
            return None
        peers = self._peers
        agent = peers.get(hint)
        if agent is not None:
            return self._jpeg_from_frame(agent.ring.latest.get())
        hint_l = hint.lower()
        for a in peers.values():
            if a.kind != "realsense":
                continue
            fr = a.ring.latest.get()
            if fr is None:
                continue
            role = str((fr.payload or {}).get("role") or "").lower()
            aid = str(a.agent_id or "").lower()
            if role == hint_l or hint_l in aid or aid == hint_l:
                return self._jpeg_from_frame(fr)
        return None

    @staticmethod
    def _jpeg_from_frame(fr: Frame | None) -> bytes | None:
        if fr is None:
            return None
        p = fr.payload or {}
        b64 = p.get("jpeg_b64") or p.get("jpeg_b64_preview")
        if not b64 or not isinstance(b64, str):
            return None
        try:
            return base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return None

    def _gather_robot_state(self) -> list[float]:
        peers = self._peers
        arm = None
        if self._arm_agent_id and self._arm_agent_id in peers:
            arm = peers[self._arm_agent_id]
        else:
            for a in peers.values():
                if a.kind == "arm_read":
                    arm = a
                    break
        if arm is None:
            raise RuntimeError("no arm_read agent for robot_state")
        fr = arm.ring.latest.get()
        if fr is None:
            raise RuntimeError("no live arm_read frame")
        xyzrpy = (fr.payload or {}).get("cartesian_xyzrpy")
        if not isinstance(xyzrpy, (list, tuple)) or len(xyzrpy) < 6:
            raise RuntimeError("arm_read missing cartesian_xyzrpy")
        grip = 0.0
        g_agent = None
        if self._gripper_agent_id and self._gripper_agent_id in peers:
            g_agent = peers[self._gripper_agent_id]
        else:
            for a in peers.values():
                if a.kind == "gripper_read":
                    g_agent = a
                    break
        if g_agent is not None:
            gfr = g_agent.ring.latest.get()
            if gfr is not None:
                gn = (gfr.payload or {}).get("position_norm")
                if gn is not None and _finite(gn):
                    grip = float(gn)
        state = [float(xyzrpy[i]) for i in range(6)] + [grip]
        if not all(_finite(x) for x in state):
            raise RuntimeError("robot_state has non-finite values")
        return state


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x and abs(x) != float("inf")
