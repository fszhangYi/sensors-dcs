"""LAN master/slave peer control plane (TCP JSON lines).

See docs/station-multi-machine.md. Master listens; slave dials. Idle peer does
not affect local record/infer (master single-machine fallback).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Any, Callable

from sensors_dcs.config import (
    DcsConfig,
    resolve_master_port,
    resolve_peer_port,
    station_summary,
)

PROTOCOL = "sensors-dcs-peer/v1"
HEARTBEAT_S = 1.5
HEARTBEAT_TIMEOUT_S = 5.0
BARRIER_TIMEOUT_S = 8.0
RECV_BUF = 65536


def _now() -> float:
    return time.time()


def _json_line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _read_lines(buf: bytearray) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        nl = buf.find(b"\n")
        if nl < 0:
            break
        raw = bytes(buf[:nl]).decode("utf-8", errors="replace").strip()
        del buf[: nl + 1]
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out


class _PeerConn:
    def __init__(
        self,
        sock: socket.socket,
        *,
        remote_addr: str,
        direction: str,
    ) -> None:
        self.sock = sock
        self.remote_addr = remote_addr
        self.direction = direction  # "inbound" | "outbound"
        self.station_id = ""
        self.advertise_ip = ""
        self.role = ""
        self.agents: list[dict[str, Any]] = []
        self.sensors_summary: dict[str, Any] = {}
        self.state = "connecting"
        self.last_rx = _now()
        self.last_error = ""
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False

    def send(self, msg: dict[str, Any]) -> bool:
        with self._lock:
            if self._closed:
                return False
            try:
                self.sock.sendall(_json_line(msg))
                return True
            except OSError as e:
                self.last_error = str(e)
                return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.state = "idle"

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf.extend(data)
        self.last_rx = _now()
        return _read_lines(self._buf)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.station_id or "?",
            "advertise_ip": self.advertise_ip,
            "role": self.role,
            "remote_addr": self.remote_addr,
            "direction": self.direction,
            "state": self.state,
            "last_rx_age_s": round(max(0.0, _now() - self.last_rx), 2),
            "last_error": self.last_error or None,
            "agents": list(self.agents),
            "sensors_summary": dict(self.sensors_summary),
        }


class StationPeer:
    """Threading TCP peer hub attached to an Orchestrator."""

    def __init__(
        self,
        cfg: DcsConfig,
        *,
        agents_summary_fn: Callable[[], list[dict[str, Any]]] | None = None,
        sensors_summary_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.cfg = cfg
        self._agents_summary_fn = agents_summary_fn or (lambda: [])
        self._sensors_summary_fn = sensors_summary_fn or (lambda: {})
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._listen_sock: socket.socket | None = None
        self._listen_thread: threading.Thread | None = None
        self._hb_thread: threading.Thread | None = None
        self._slaves: dict[str, _PeerConn] = {}
        self._master_conn: _PeerConn | None = None
        self._link_state = "idle"
        self._last_error = ""
        self._session_token = ""
        self._active_session_id = ""
        # Record barrier handlers (slave side) + master ack waiters.
        self._on_prepare: Callable[[str], dict[str, Any]] | None = None
        self._on_commit: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        self._on_abort: Callable[[str, str], None] | None = None
        self._on_stop: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._ack_cv = threading.Condition()
        self._pending_acks: dict[str, dict[str, Any]] = {}
        self._barrier_expect: set[str] = set()
        self._barrier_phase = ""
        self._barrier_token = ""

    # --- public status / control -------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = station_summary(self.cfg)
            slaves = [c.snapshot() for c in self._slaves.values()]
            master = self._master_conn.snapshot() if self._master_conn else None
            return {
                "link_state": self._link_state,
                "last_error": self._last_error or None,
                "listening": self._listen_sock is not None,
                "peer_port": resolve_peer_port(self.cfg),
                "slaves": slaves,
                "master": master,
                "station": st,
            }

    def has_active_slaves(self) -> bool:
        with self._lock:
            return any(c.state == "connected" for c in self._slaves.values())

    def bind_record_handlers(
        self,
        *,
        on_prepare: Callable[[str], dict[str, Any]] | None = None,
        on_commit: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        on_abort: Callable[[str, str], None] | None = None,
        on_stop: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """Slave-side hooks for PREPARE/COMMIT/ABORT/STOP (P2 barrier)."""
        self._on_prepare = on_prepare
        self._on_commit = on_commit
        self._on_abort = on_abort
        self._on_stop = on_stop

    def barrier_start(
        self,
        *,
        mode: str,
        local_start: Callable[..., dict[str, Any]],
        timeout_s: float = BARRIER_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Master: PREPARE→ACK→local start→COMMIT→ACK. No slaves → local_start only."""
        if self.cfg.station.role != "master":
            return {"ok": False, "error": "barrier_start is master-only", **self.status()}
        if not self.has_active_slaves():
            return local_start(mode=mode)

        mode_n = "infer" if str(mode).strip().lower() == "infer" else "collect"
        session_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        local = station_summary(self.cfg)
        with self._lock:
            slaves = [c for c in self._slaves.values() if c.state == "connected"]
            self._link_state = "syncing"
            self._active_session_id = session_id
            self._last_error = ""
            peer_slaves = [
                {"id": c.station_id, "advertise_ip": c.advertise_ip} for c in slaves
            ]

        expect = {c.station_id for c in slaves}
        self._begin_barrier(phase="prepare", expect=expect, token=token)
        for c in slaves:
            if not c.send(
                {
                    "type": "prepare",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "mode": mode_n,
                }
            ):
                self._broadcast_abort(session_id, token, reason="prepare send failed")
                with self._lock:
                    self._link_state = "connected" if self._slaves else "listening"
                    self._last_error = f"prepare send failed to {c.station_id}"
                return {"ok": False, "error": self._last_error, "session_id": session_id}

        ok_prep, prep_detail = self._wait_acks(
            expect=expect, ack_type="prepare_ack", token=token, timeout_s=timeout_s
        )
        if not ok_prep:
            self._broadcast_abort(session_id, token, reason=prep_detail)
            with self._lock:
                self._link_state = "connected" if self._slaves else "listening"
                self._last_error = prep_detail
            return {
                "ok": False,
                "error": prep_detail,
                "session_id": session_id,
                **self.status(),
            }

        session_meta = {
            "session_id": session_id,
            "station_id": local["id"],
            "station_role": "master",
            "peer_slaves": peer_slaves,
        }
        local_res = local_start(mode=mode_n, session_meta=session_meta)
        if not local_res.get("ok"):
            self._broadcast_abort(
                session_id, token, reason=str(local_res.get("error") or "master start failed")
            )
            with self._lock:
                self._link_state = "connected" if self._slaves else "listening"
            return {**local_res, "session_id": session_id}

        t_start_wall = float(local_res.get("t_start") or _now())
        # Prefer recorder status clocks if present.
        try:
            t_start_wall = float(
                (local_res.get("t_start") if "t_start" in local_res else None)
                or time.time()
            )
        except (TypeError, ValueError):
            t_start_wall = time.time()

        self._begin_barrier(phase="commit", expect=expect, token=token)
        slave_meta = {
            "session_id": session_id,
            "station_role": "slave",
        }
        for c in slaves:
            c.send(
                {
                    "type": "commit",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "mode": mode_n,
                    "t_start_wall": t_start_wall,
                    "session_meta": {
                        **slave_meta,
                        "station_id": c.station_id,
                    },
                }
            )

        ok_commit, commit_detail = self._wait_acks(
            expect=expect, ack_type="commit_ack", token=token, timeout_s=timeout_s
        )
        if not ok_commit:
            # Best-effort fail-stop: abort remaining + stop local.
            self._broadcast_abort(session_id, token, reason=commit_detail)
            try:
                # Caller may pass stop via barrier_stop; here we only signal.
                pass
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._link_state = "error"
                self._last_error = commit_detail
            return {
                "ok": False,
                "error": commit_detail,
                "session_id": session_id,
                "master_started": True,
                **local_res,
                **self.status(),
            }

        with self._lock:
            self._link_state = "connected"
            self._session_token = token
        return {
            "ok": True,
            "session_id": session_id,
            "barrier": True,
            **local_res,
            "peer": self.status(),
        }

    def barrier_stop(
        self,
        *,
        local_stop: Callable[..., dict[str, Any]],
        valid: bool = True,
        async_flush: bool = False,
        keep_files: bool = True,
        timeout_s: float = BARRIER_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Master: STOP→ACK then local_stop. No slaves → local_stop only."""
        if self.cfg.station.role != "master":
            return {"ok": False, "error": "barrier_stop is master-only", **self.status()}
        if not self.has_active_slaves():
            return local_stop(
                valid=valid, async_flush=async_flush, keep_files=keep_files
            )

        session_id = self._active_session_id or str(uuid.uuid4())
        token = self._session_token or str(uuid.uuid4())
        with self._lock:
            slaves = [c for c in self._slaves.values() if c.state == "connected"]
            self._link_state = "syncing"
        expect = {c.station_id for c in slaves}
        self._begin_barrier(phase="stop", expect=expect, token=token)
        for c in slaves:
            c.send(
                {
                    "type": "stop",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "valid": bool(valid),
                    "async_flush": bool(async_flush),
                    "keep_files": bool(keep_files),
                }
            )
        ok_stop, stop_detail = self._wait_acks(
            expect=expect, ack_type="stop_ack", token=token, timeout_s=timeout_s
        )
        local_res = local_stop(
            valid=valid, async_flush=async_flush, keep_files=keep_files
        )
        with self._lock:
            self._link_state = "connected" if self._slaves else "listening"
            self._active_session_id = ""
            if not ok_stop:
                self._last_error = stop_detail
        out = {
            "ok": bool(local_res.get("ok")) and ok_stop,
            "session_id": session_id,
            "barrier": True,
            "slaves_ok": ok_stop,
            "slaves_detail": None if ok_stop else stop_detail,
            **local_res,
            "peer": self.status(),
        }
        if not ok_stop and out.get("ok"):
            out["ok"] = False
            out["error"] = stop_detail
        return out

    def _begin_barrier(self, *, phase: str, expect: set[str], token: str) -> None:
        with self._ack_cv:
            self._barrier_phase = phase
            self._barrier_expect = set(expect)
            self._barrier_token = token
            self._pending_acks.clear()
            self._ack_cv.notify_all()

    def _note_ack(self, msg: dict[str, Any]) -> None:
        sid = str(msg.get("station_id") or "").strip()
        if not sid:
            return
        with self._ack_cv:
            if str(msg.get("token") or "") != self._barrier_token:
                return
            self._pending_acks[sid] = msg
            self._ack_cv.notify_all()

    def _wait_acks(
        self,
        *,
        expect: set[str],
        ack_type: str,
        token: str,
        timeout_s: float,
    ) -> tuple[bool, str]:
        deadline = _now() + max(0.5, float(timeout_s))
        with self._ack_cv:
            while _now() < deadline and not self._stop.is_set():
                if self._barrier_token != token:
                    return False, "barrier token changed"
                got = {
                    sid: m
                    for sid, m in self._pending_acks.items()
                    if str(m.get("type") or "") == ack_type
                }
                if expect.issubset(got.keys()):
                    for sid in expect:
                        m = got[sid]
                        if m.get("ready") is False or m.get("ok") is False:
                            err = str(m.get("error") or f"{sid} not ready")
                            return False, err
                    return True, "ok"
                remaining = deadline - _now()
                if remaining <= 0:
                    break
                self._ack_cv.wait(timeout=min(0.2, remaining))
            missing = sorted(expect - set(self._pending_acks.keys()))
            return False, f"{ack_type} timeout; missing={missing}"

    def _broadcast_abort(self, session_id: str, token: str, *, reason: str) -> None:
        with self._lock:
            slaves = list(self._slaves.values())
        for c in slaves:
            c.send(
                {
                    "type": "abort",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "reason": reason,
                }
            )

    def start(self) -> None:
        """Start listen (master) or leave idle until slave connect()."""
        if self._stop.is_set():
            self._stop.clear()
        role = self.cfg.station.role
        if role == "master":
            self._start_listen()
        # Heartbeat supervisor for both roles.
        if self._hb_thread is None or not self._hb_thread.is_alive():
            self._hb_thread = threading.Thread(
                target=self._heartbeat_loop, name="station-peer-hb", daemon=True
            )
            self._hb_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.disconnect_all()
        sock = self._listen_sock
        self._listen_sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        t = self._listen_thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        self._listen_thread = None
        with self._lock:
            if self.cfg.station.role == "master":
                self._link_state = "idle"
            else:
                self._link_state = "idle"

    def connect_to_master(self) -> dict[str, Any]:
        """Slave: dial master_ip:master_port and hello."""
        st = self.cfg.station
        if st.role != "slave":
            return {"ok": False, "error": "only slave can connect to master", **self.status()}
        if not (st.master_ip or "").strip():
            return {"ok": False, "error": "station.master_ip unset", **self.status()}
        with self._lock:
            if self._master_conn is not None and self._master_conn.state == "connected":
                return {"ok": True, "note": "already connected", **self.status()}
            # Drop stale.
            if self._master_conn is not None:
                self._master_conn.close()
                self._master_conn = None
            self._link_state = "connecting"
            self._last_error = ""

        host = st.master_ip.strip()
        port = resolve_master_port(self.cfg)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect((host, port))
        except OSError as e:
            sock.close()
            with self._lock:
                self._link_state = "error"
                self._last_error = f"connect failed: {e}"
            return {"ok": False, "error": self._last_error, **self.status()}

        sock.settimeout(2.0)
        conn = _PeerConn(sock, remote_addr=f"{host}:{port}", direction="outbound")
        hello = self._hello_payload()
        if not conn.send(hello):
            conn.close()
            with self._lock:
                self._link_state = "error"
                self._last_error = "failed to send hello"
            return {"ok": False, "error": self._last_error, **self.status()}

        # Wait briefly for hello_ack / reject.
        deadline = _now() + 5.0
        accepted = False
        while _now() < deadline and not self._stop.is_set():
            try:
                data = sock.recv(RECV_BUF)
            except socket.timeout:
                continue
            except OSError as e:
                with self._lock:
                    self._link_state = "error"
                    self._last_error = str(e)
                conn.close()
                return {"ok": False, "error": self._last_error, **self.status()}
            if not data:
                with self._lock:
                    self._link_state = "error"
                    self._last_error = "master closed during hello"
                conn.close()
                return {"ok": False, "error": self._last_error, **self.status()}
            for msg in conn.feed(data):
                mtype = str(msg.get("type") or "")
                if mtype == "reject":
                    err = str(msg.get("error") or "rejected")
                    conn.close()
                    with self._lock:
                        self._link_state = "error"
                        self._last_error = err
                    return {"ok": False, "error": err, **self.status()}
                if mtype == "hello_ack":
                    conn.station_id = str(msg.get("station_id") or "master")
                    conn.advertise_ip = str(msg.get("advertise_ip") or "")
                    conn.role = "master"
                    conn.state = "connected"
                    accepted = True
                    break
            if accepted:
                break

        if not accepted:
            conn.close()
            with self._lock:
                self._link_state = "error"
                self._last_error = "hello_ack timeout"
            return {"ok": False, "error": self._last_error, **self.status()}

        with self._lock:
            self._master_conn = conn
            self._link_state = "connected"
            self._session_token = str(uuid.uuid4())
        # Reader thread for ongoing traffic.
        threading.Thread(
            target=self._slave_reader_loop,
            args=(conn,),
            name="station-peer-slave-rx",
            daemon=True,
        ).start()
        return {"ok": True, **self.status()}

    def disconnect(self) -> dict[str, Any]:
        """Slave: drop master link. Master: no-op on self (use kick)."""
        with self._lock:
            conn = self._master_conn
            self._master_conn = None
            self._link_state = "idle"
            self._last_error = ""
        if conn is not None:
            conn.send({"type": "disconnect", "protocol": PROTOCOL})
            conn.close()
        return {"ok": True, **self.status()}

    def disconnect_all(self) -> None:
        with self._lock:
            slaves = list(self._slaves.values())
            self._slaves.clear()
            master = self._master_conn
            self._master_conn = None
        for c in slaves:
            try:
                c.send({"type": "disconnect", "protocol": PROTOCOL})
            except Exception:  # noqa: BLE001
                pass
            c.close()
        if master is not None:
            try:
                master.send({"type": "disconnect", "protocol": PROTOCOL})
            except Exception:  # noqa: BLE001
                pass
            master.close()

    def kick_slave(self, station_id: str) -> dict[str, Any]:
        if self.cfg.station.role != "master":
            return {"ok": False, "error": "only master can kick slaves", **self.status()}
        sid = (station_id or "").strip()
        with self._lock:
            conn = self._slaves.pop(sid, None)
            if not self._slaves and self._listen_sock is not None:
                self._link_state = "listening"
        if conn is None:
            return {"ok": False, "error": f"slave not found: {sid}", **self.status()}
        conn.send({"type": "disconnect", "protocol": PROTOCOL, "reason": "kicked"})
        conn.close()
        return {"ok": True, **self.status()}

    # --- listen / accept (master) ------------------------------------------------

    def _start_listen(self) -> None:
        if self._listen_sock is not None:
            return
        host = (self.cfg.runtime.control_host or "0.0.0.0").strip() or "0.0.0.0"
        port = resolve_peer_port(self.cfg)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            sock.listen(8)
            sock.settimeout(1.0)
        except OSError as e:
            sock.close()
            with self._lock:
                self._link_state = "error"
                self._last_error = f"peer listen failed on {host}:{port}: {e}"
            print(f"[sensors-dcs] {self._last_error}", flush=True)
            return
        self._listen_sock = sock
        with self._lock:
            self._link_state = "listening"
            self._last_error = ""
        self._listen_thread = threading.Thread(
            target=self._accept_loop, name="station-peer-listen", daemon=True
        )
        self._listen_thread.start()
        print(f"[sensors-dcs] peer listen {host}:{port} (master)", flush=True)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._listen_sock
            if sock is None:
                break
            try:
                client, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_inbound,
                args=(client, addr),
                name=f"station-peer-in-{addr[0]}",
                daemon=True,
            ).start()

    def _handle_inbound(self, client: socket.socket, addr: tuple[Any, ...]) -> None:
        # Slaves must not accept peer connections.
        if self.cfg.station.role != "master":
            try:
                client.sendall(
                    _json_line(
                        {
                            "type": "reject",
                            "protocol": PROTOCOL,
                            "error": "this station is slave; cannot be connected",
                        }
                    )
                )
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
            return

        client.settimeout(2.0)
        remote = f"{addr[0]}:{addr[1]}"
        conn = _PeerConn(client, remote_addr=remote, direction="inbound")
        deadline = _now() + 5.0
        hello: dict[str, Any] | None = None
        while _now() < deadline and not self._stop.is_set():
            try:
                data = client.recv(RECV_BUF)
            except socket.timeout:
                continue
            except OSError:
                conn.close()
                return
            if not data:
                conn.close()
                return
            for msg in conn.feed(data):
                if str(msg.get("type") or "") == "hello":
                    hello = msg
                    break
            if hello is not None:
                break

        if hello is None:
            conn.send({"type": "reject", "protocol": PROTOCOL, "error": "hello timeout"})
            conn.close()
            return

        remote_role = str(hello.get("role") or "").strip().lower()
        if remote_role != "slave":
            conn.send(
                {
                    "type": "reject",
                    "protocol": PROTOCOL,
                    "error": "only slave role may connect to master",
                }
            )
            conn.close()
            return

        sid = str(hello.get("station_id") or "").strip() or f"slave@{addr[0]}"
        conn.station_id = sid
        conn.advertise_ip = str(hello.get("advertise_ip") or addr[0])
        conn.role = "slave"
        conn.agents = list(hello.get("agents") or [])
        sens = hello.get("sensors_summary")
        conn.sensors_summary = dict(sens) if isinstance(sens, dict) else {}
        conn.state = "connected"

        local = station_summary(self.cfg)
        conn.send(
            {
                "type": "hello_ack",
                "protocol": PROTOCOL,
                "station_id": local["id"],
                "advertise_ip": local.get("advertise_ip") or "",
                "role": "master",
            }
        )

        with self._lock:
            old = self._slaves.get(sid)
            self._slaves[sid] = conn
            self._link_state = "connected"
        if old is not None and old is not conn:
            old.close()

        self._master_serve_loop(conn)

    def _master_serve_loop(self, conn: _PeerConn) -> None:
        sock = conn.sock
        sock.settimeout(1.0)
        while not self._stop.is_set() and not conn._closed:
            try:
                data = sock.recv(RECV_BUF)
            except socket.timeout:
                if _now() - conn.last_rx > HEARTBEAT_TIMEOUT_S:
                    conn.last_error = "heartbeat timeout"
                    break
                continue
            except OSError as e:
                conn.last_error = str(e)
                break
            if not data:
                break
            for msg in conn.feed(data):
                mtype = str(msg.get("type") or "")
                if mtype == "heartbeat":
                    conn.send({"type": "heartbeat_ack", "protocol": PROTOCOL, "t": _now()})
                elif mtype in ("prepare_ack", "commit_ack", "stop_ack"):
                    if not msg.get("station_id"):
                        msg = {**msg, "station_id": conn.station_id}
                    self._note_ack(msg)
                elif mtype == "disconnect":
                    conn.state = "idle"
                    self._drop_slave(conn.station_id, conn)
                    return
        self._drop_slave(conn.station_id, conn)

    def _drop_slave(self, sid: str, conn: _PeerConn) -> None:
        with self._lock:
            cur = self._slaves.get(sid)
            if cur is conn:
                self._slaves.pop(sid, None)
            if not self._slaves and self._listen_sock is not None:
                self._link_state = "listening"
        conn.close()

    # --- slave reader / heartbeat ------------------------------------------------

    def _slave_reader_loop(self, conn: _PeerConn) -> None:
        sock = conn.sock
        sock.settimeout(1.0)
        while not self._stop.is_set() and not conn._closed:
            try:
                data = sock.recv(RECV_BUF)
            except socket.timeout:
                if _now() - conn.last_rx > HEARTBEAT_TIMEOUT_S:
                    conn.last_error = "heartbeat timeout"
                    break
                continue
            except OSError as e:
                conn.last_error = str(e)
                break
            if not data:
                break
            for msg in conn.feed(data):
                mtype = str(msg.get("type") or "")
                if mtype == "disconnect":
                    break
                if mtype == "heartbeat_ack":
                    continue
                if mtype in ("prepare", "commit", "abort", "stop"):
                    self._slave_handle_control(conn, msg)
        with self._lock:
            if self._master_conn is conn:
                self._master_conn = None
                self._link_state = "error" if conn.last_error else "idle"
                if conn.last_error:
                    self._last_error = conn.last_error
        conn.close()

    def _slave_handle_control(self, conn: _PeerConn, msg: dict[str, Any]) -> None:
        mtype = str(msg.get("type") or "")
        session_id = str(msg.get("session_id") or "")
        token = str(msg.get("token") or "")
        local = station_summary(self.cfg)
        sid = local["id"]

        if mtype == "prepare":
            mode = str(msg.get("mode") or "collect")
            ready = True
            err = ""
            if self._on_prepare is not None:
                try:
                    res = self._on_prepare(mode) or {}
                    ready = bool(res.get("ok", True))
                    err = str(res.get("error") or "")
                except Exception as e:  # noqa: BLE001
                    ready = False
                    err = str(e)
            with self._lock:
                self._link_state = "syncing"
                self._active_session_id = session_id
            conn.send(
                {
                    "type": "prepare_ack",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "station_id": sid,
                    "ready": ready,
                    "ok": ready,
                    "error": err or None,
                    "t_wall": _now(),
                    "t_mono": time.monotonic(),
                }
            )
            return

        if mtype == "commit":
            mode = str(msg.get("mode") or "collect")
            meta = msg.get("session_meta") if isinstance(msg.get("session_meta"), dict) else {}
            meta = dict(meta)
            meta.setdefault("session_id", session_id)
            meta.setdefault("station_id", sid)
            meta.setdefault("station_role", "slave")
            ok = True
            err = ""
            if self._on_commit is not None:
                try:
                    res = self._on_commit(mode, meta) or {}
                    ok = bool(res.get("ok", True))
                    err = str(res.get("error") or "")
                except Exception as e:  # noqa: BLE001
                    ok = False
                    err = str(e)
            with self._lock:
                self._link_state = "connected" if ok else "error"
                if err:
                    self._last_error = err
            conn.send(
                {
                    "type": "commit_ack",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "station_id": sid,
                    "ok": ok,
                    "ready": ok,
                    "error": err or None,
                }
            )
            return

        if mtype == "abort":
            reason = str(msg.get("reason") or "aborted")
            if self._on_abort is not None:
                try:
                    self._on_abort(session_id, reason)
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._link_state = "connected"
                self._active_session_id = ""
                self._last_error = reason
            return

        if mtype == "stop":
            body = {
                "valid": bool(msg.get("valid", True)),
                "async_flush": bool(msg.get("async_flush", False)),
                "keep_files": bool(msg.get("keep_files", True)),
                "session_id": session_id,
            }
            ok = True
            err = ""
            if self._on_stop is not None:
                try:
                    res = self._on_stop(body) or {}
                    ok = bool(res.get("ok", True))
                    err = str(res.get("error") or "")
                except Exception as e:  # noqa: BLE001
                    ok = False
                    err = str(e)
            with self._lock:
                self._link_state = "connected"
                self._active_session_id = ""
            conn.send(
                {
                    "type": "stop_ack",
                    "protocol": PROTOCOL,
                    "session_id": session_id,
                    "token": token,
                    "station_id": sid,
                    "ok": ok,
                    "ready": ok,
                    "error": err or None,
                }
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(HEARTBEAT_S)
            with self._lock:
                master = self._master_conn
                slaves = list(self._slaves.values())
            if master is not None and master.state == "connected":
                if not master.send({"type": "heartbeat", "protocol": PROTOCOL, "t": _now()}):
                    with self._lock:
                        if self._master_conn is master:
                            self._master_conn = None
                            self._link_state = "error"
                            self._last_error = master.last_error or "heartbeat send failed"
                    master.close()
            for c in slaves:
                if c.state != "connected":
                    continue
                # Master relies on slave heartbeats; optionally ping too.
                if _now() - c.last_rx > HEARTBEAT_TIMEOUT_S:
                    c.last_error = "heartbeat timeout"
                    self._drop_slave(c.station_id, c)

    def _hello_payload(self) -> dict[str, Any]:
        st = station_summary(self.cfg)
        return {
            "type": "hello",
            "protocol": PROTOCOL,
            "role": st["role"],
            "station_id": st["id"],
            "advertise_ip": st.get("advertise_ip") or "",
            "peer_port": st.get("peer_port"),
            "agents": self._agents_summary_fn(),
            "sensors_summary": self._sensors_summary_fn(),
            "t_wall": _now(),
            "t_mono": time.monotonic(),
        }
