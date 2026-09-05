"""CLI fake ``pi05_jax_sft.serve`` TCP endpoint.

Wire protocol (big-endian) matches real serve + ``Pi05ClientAgent``:

Client → server (per step):
  4B len + top JPEG
  4B len + chest JPEG
  4B len + wrist2 JPEG
  4B len + UTF-8 text
  28B robot_state = 7×float32

Server → client:
  28B next_state = 7×float32
  4B  term_flag  = float32
  4B  reject_flag = uint32
  4B  text_len + UTF-8 status text

``next_state`` is sampled on a deterministic arc or line in TCP xyz
(anchored to the robot_state at the start of each cycle), not random jitter.

No idle socket timeout (real serve also waits indefinitely between steps).
"""

from __future__ import annotations

import argparse
import math
import signal
import socket
import struct
import threading
import time
from datetime import datetime
from typing import Any


_clients_lock = threading.Lock()
_active_clients = 0
_total_steps = 0


def recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            pkt = conn.recv(n - len(buf))
        except TimeoutError:
            continue
        except OSError:
            return None
        if not pkt:
            return None
        buf += pkt
    return buf


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _sample_path(
    origin: list[float],
    *,
    phase: int,
    n_cycle: int,
    path: str,
    radius: float,
    plane: str,
) -> list[float]:
    """Return next_state[7] on an arc/line through TCP xyz; rpy+grip from origin.

    ``phase`` is 1..n_cycle within the current big cycle.
    """
    n = max(1, int(n_cycle))
    # Progress in (0, 1]: phase=n → 1.0 (end of segment / full turn).
    t = float(phase) / float(n)
    x0, y0, z0 = float(origin[0]), float(origin[1]), float(origin[2])
    rpy = [float(origin[i]) for i in range(3, 6)]
    grip = max(0.0, min(1.0, float(origin[6])))
    r = max(1e-4, float(radius))

    if path == "line":
        # Straight segment of length ``radius`` along the plane's first axis, then
        # the orthogonal in-plane axis gets a small coupled offset so the trail
        # is visible as a short diagonal.
        if plane == "xz":
            dx, dy, dz = r * t, 0.0, 0.25 * r * t
        elif plane == "yz":
            dx, dy, dz = 0.0, r * t, 0.25 * r * t
        else:  # xy
            dx, dy, dz = r * t, 0.25 * r * t, 0.0
        xyz = [x0 + dx, y0 + dy, z0 + dz]
    else:
        # Arc (default): full circle in the chosen plane, radius ``r``,
        # starting at the origin pose (θ=0 → back to start at θ=2π).
        theta = 2.0 * math.pi * t
        c, s = math.cos(theta), math.sin(theta)
        if plane == "xz":
            # Center at (x0 - r, y0, z0); θ=0 → (x0, y0, z0).
            xyz = [x0 - r + r * c, y0, z0 + r * s]
        elif plane == "yz":
            xyz = [x0, y0 - r + r * c, z0 + r * s]
        else:  # xy (robot Z-up horizontal)
            xyz = [x0 - r + r * c, y0 + r * s, z0]

    return xyz + rpy + [grip]


def handle_client(
    conn: socket.socket,
    addr: tuple,
    *,
    path: str,
    radius: float,
    plane: str,
    term_every: int,
) -> None:
    global _active_clients, _total_steps
    with _clients_lock:
        _active_clients += 1
        n_cli = _active_clients
    print(
        f"[{_ts()}] ACCEPT  {addr[0]}:{addr[1]}  "
        f"(active_clients={n_cli})  status=CONNECTED  "
        f"path={path} radius={radius:.4f}m plane={plane}",
        flush=True,
    )
    step = 0
    origin: list[float] | None = None
    n_cycle = max(1, int(term_every) if term_every > 0 else 20)
    try:
        # No idle timeout — Connect may sit open until the user clicks Step.
        conn.settimeout(None)
        while True:
            jpeg_lens: dict[str, int] = {}
            for cam in ("top", "chest", "wrist2"):
                hdr = recv_exact(conn, 4)
                if hdr is None:
                    print(
                        f"[{_ts()}] EOF     {addr[0]}:{addr[1]}  "
                        f"during {cam} header (client closed)",
                        flush=True,
                    )
                    return
                n = struct.unpack(">I", hdr)[0]
                if n > 20_000_000:
                    print(
                        f"[{_ts()}] ERROR   {addr[0]}:{addr[1]}  "
                        f"absurd jpeg len={n} for {cam}",
                        flush=True,
                    )
                    return
                body = recv_exact(conn, n) if n else b""
                if body is None:
                    print(
                        f"[{_ts()}] EOF     {addr[0]}:{addr[1]}  "
                        f"during {cam} body",
                        flush=True,
                    )
                    return
                jpeg_lens[cam] = len(body)

            hdr = recv_exact(conn, 4)
            if hdr is None:
                return
            tlen = struct.unpack(">I", hdr)[0]
            text = ""
            if tlen:
                raw = recv_exact(conn, tlen)
                if raw is None:
                    return
                text = raw.decode("utf-8", errors="replace")

            raw = recv_exact(conn, 28)
            if raw is None:
                return
            robot_state = list(struct.unpack(">7f", raw))

            step += 1
            with _clients_lock:
                _total_steps += 1
                tot = _total_steps

            phase = ((step - 1) % n_cycle) + 1  # 1..n_cycle
            if phase == 1 or origin is None:
                origin = list(robot_state)

            next_state = _sample_path(
                origin,
                phase=phase,
                n_cycle=n_cycle,
                path=path,
                radius=radius,
                plane=plane,
            )

            # Last sample of each cycle → terminate so Infer LOOP can exit cleanly.
            term_flag = 1.0 if (term_every > 0 and phase == n_cycle) else 0.0
            reject_flag = 0
            msg = (
                f"fake-ok step={step} phase={phase}/{n_cycle} "
                f"path={path} term={int(term_flag)}"
            )
            msg_b = msg.encode("utf-8")

            conn.sendall(struct.pack(">7f", *[float(x) for x in next_state]))
            conn.sendall(struct.pack(">f", float(term_flag)))
            conn.sendall(struct.pack(">I", int(reject_flag)))
            conn.sendall(struct.pack(">I", len(msg_b)))
            conn.sendall(msg_b)

            prompt_preview = text.replace("\n", " ")[:60]
            print(
                f"[{_ts()}] STEP    {addr[0]}:{addr[1]}  "
                f"session_step={step} phase={phase}/{n_cycle} total_steps={tot}  "
                f"jpeg={jpeg_lens}  text={prompt_preview!r}  "
                f"robot=[{', '.join(f'{v:.3f}' for v in robot_state)}]  "
                f"→ next=[{', '.join(f'{v:.3f}' for v in next_state)}]  "
                f"term={term_flag} reject={reject_flag}",
                flush=True,
            )
    except OSError as e:
        print(f"[{_ts()}] ERROR   {addr[0]}:{addr[1]}  {e}", flush=True)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        with _clients_lock:
            _active_clients = max(0, _active_clients - 1)
            n_cli = _active_clients
        print(
            f"[{_ts()}] CLOSE   {addr[0]}:{addr[1]}  "
            f"steps={step}  active_clients={n_cli}  status=DISCONNECTED",
            flush=True,
        )


def serve_forever(
    host: str,
    port: int,
    *,
    path: str,
    radius: float,
    plane: str,
    term_every: int = 20,
    heartbeat_s: float = 0.0,
) -> None:
    global _total_steps
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(
        f"[pi05-fake-serve] LISTEN  {host}:{port}  "
        f"protocol=pi05_jax_sft.serve  path={path} radius={radius:.4f}m "
        f"plane={plane} term_every={term_every}  "
        f"status=READY (waiting for Infer Connect)",
        flush=True,
    )

    def _heartbeat(interval: float) -> None:
        while True:
            time.sleep(interval)
            with _clients_lock:
                n_cli = _active_clients
                tot = _total_steps
            print(
                f"[{_ts()}] STATUS  listening  active_clients={n_cli}  "
                f"total_steps={tot}",
                flush=True,
            )

    if heartbeat_s > 0:
        threading.Thread(
            target=_heartbeat,
            args=(heartbeat_s,),
            name="pi05-fake-heartbeat",
            daemon=True,
        ).start()

    def _stop(*_a: object) -> None:
        print(f"\n[{_ts()}] SIGNAL  shutting down fake serve…", flush=True)
        try:
            srv.close()
        except OSError:
            pass

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        pass

    try:
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            # Do NOT set a short idle timeout — Connect opens TCP, Step may come later.
            conn.settimeout(None)
            print(
                f"[{_ts()}] INCOMING TCP from {addr[0]}:{addr[1]} — spawning handler",
                flush=True,
            )
            threading.Thread(
                target=handle_client,
                args=(conn, addr),
                kwargs={
                    "path": path,
                    "radius": radius,
                    "plane": plane,
                    "term_every": term_every,
                },
                name=f"pi05-fake-{addr[0]}-{addr[1]}",
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] stopped (KeyboardInterrupt)", flush=True)
    finally:
        try:
            srv.close()
        except OSError:
            pass
        print(f"[{_ts()}] STOPPED  total_steps={_total_steps}", flush=True)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument(
        "--path",
        choices=("arc", "line"),
        default="arc",
        help="Sample next_state on an arc (full circle) or a straight segment.",
    )
    p.add_argument(
        "--radius",
        type=float,
        default=0.04,
        help="Arc radius / line length in meters (TCP xyz). Default 0.04.",
    )
    p.add_argument(
        "--plane",
        choices=("xy", "xz", "yz"),
        default="xy",
        help="Plane for arc / primary axes for line (robot Z-up). Default xy.",
    )
    p.add_argument(
        "--term-every",
        type=int,
        default=20,
        help="Steps per cycle; term_flag=1 on the last sample (0→use 20, never term).",
    )
    p.add_argument(
        "--jitter",
        type=float,
        default=None,
        help=argparse.SUPPRESS,  # deprecated alias → --radius
    )
    p.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="Seconds between STATUS heartbeats (0=disable).",
    )
    args = p.parse_args(argv)
    radius = float(args.radius)
    if args.jitter is not None:
        # Old flag meant per-axis noise; map to a small geometric size.
        radius = max(radius, abs(float(args.jitter)) * 8.0)
        print(
            f"[pi05-fake-serve] NOTE  --jitter is deprecated; "
            f"using path geometry radius={radius:.4f}m",
            flush=True,
        )
    term_every = int(args.term_every)
    serve_forever(
        args.host,
        args.port,
        path=str(args.path),
        radius=max(1e-4, radius),
        plane=str(args.plane),
        term_every=max(0, term_every),
        heartbeat_s=max(0.0, float(args.heartbeat)),
    )


if __name__ == "__main__":
    main()
