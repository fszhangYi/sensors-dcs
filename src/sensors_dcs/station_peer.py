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
                # Future: prepare/commit/abort handled in P2.
        with self._lock:
            if self._master_conn is conn:
                self._master_conn = None
                self._link_state = "error" if conn.last_error else "idle"
                if conn.last_error:
                    self._last_error = conn.last_error
        conn.close()

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
