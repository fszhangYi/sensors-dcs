#!/usr/bin/env python3
"""Fake ``pi05_jax_sft.serve`` TCP endpoint for sensors-dcs Infer dry runs.

Wire protocol matches ``pi05_jax_sft.serve`` / ``Pi05ClientAgent`` (big-endian):

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

``next_state`` is random (optionally a small jitter around the received
``robot_state``). No JAX / checkpoint required.

Usage:
  python3 tools/pi05_fake_serve/serve.py --host 0.0.0.0 --port 5000
"""

from __future__ import annotations

import argparse
import random
import socket
import struct
import threading
import time
from datetime import datetime


def recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        pkt = conn.recv(n - len(buf))
        if not pkt:
            return None
        buf += pkt
    return buf


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def handle_client(conn: socket.socket, addr: tuple, *, jitter: float) -> None:
    print(f"[{_ts()}] {addr} connected (fake pi05 serve)", flush=True)
    step = 0
    try:
        while True:
            # --- 3 JPEG views ---
            jpeg_lens: dict[str, int] = {}
            for cam in ("top", "chest", "wrist2"):
                hdr = recv_exact(conn, 4)
                if hdr is None:
                    return
                n = struct.unpack(">I", hdr)[0]
                if n > 20_000_000:
                    print(f"[{_ts()}] {addr} absurd jpeg len={n} for {cam}", flush=True)
                    return
                body = recv_exact(conn, n) if n else b""
                if body is None:
                    return
                jpeg_lens[cam] = len(body)

            # --- text ---
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

            # --- robot_state ---
            raw = recv_exact(conn, 28)
            if raw is None:
                return
            robot_state = list(struct.unpack(">7f", raw))

            step += 1
            # Random absolute cartesian-ish pose + gripper in [0, 1].
            next_state = [
                robot_state[i] + random.uniform(-jitter, jitter) for i in range(6)
            ] + [max(0.0, min(1.0, robot_state[6] + random.uniform(-0.05, 0.05)))]
            # Occasional fully random pose so UI clearly changes.
            if random.random() < 0.25:
                next_state = [
                    random.uniform(-0.5, 0.5),
                    random.uniform(-0.5, 0.5),
                    random.uniform(0.1, 0.6),
                    random.uniform(-1.0, 1.0),
                    random.uniform(-1.0, 1.0),
                    random.uniform(-1.0, 1.0),
                    random.random(),
                ]

            term_flag = 1.0 if random.random() < 0.02 else 0.0
            reject_flag = 1 if random.random() < 0.02 else 0
            msg = f"fake-ok step={step}"
            msg_b = msg.encode("utf-8")

            conn.sendall(struct.pack(">7f", *[float(x) for x in next_state]))
            conn.sendall(struct.pack(">f", float(term_flag)))
            conn.sendall(struct.pack(">I", int(reject_flag)))
            conn.sendall(struct.pack(">I", len(msg_b)))
            conn.sendall(msg_b)

            print(
                f"[{_ts()}] {addr} step={step} "
                f"jpeg={jpeg_lens} text_len={len(text)} "
                f"robot=[{', '.join(f'{v:.3f}' for v in robot_state)}] "
                f"→ next=[{', '.join(f'{v:.3f}' for v in next_state)}] "
                f"term={term_flag} reject={reject_flag}",
                flush=True,
            )
    except OSError as e:
        print(f"[{_ts()}] {addr} error: {e}", flush=True)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print(f"[{_ts()}] {addr} disconnected (steps={step})", flush=True)


def serve(host: str, port: int, *, jitter: float) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(
        f"[pi05-fake-serve] listening on {host}:{port}  "
        f"(protocol=pi05_jax_sft.serve, random next_state)",
        flush=True,
    )
    try:
        while True:
            conn, addr = srv.accept()
            conn.settimeout(60.0)
            threading.Thread(
                target=handle_client,
                args=(conn, addr),
                kwargs={"jitter": jitter},
                name=f"pi05-fake-{addr[0]}-{addr[1]}",
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\n[pi05-fake-serve] stopped", flush=True)
    finally:
        srv.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument(
        "--jitter",
        type=float,
        default=0.02,
        help="Uniform xyz/rpy jitter around received robot_state (meters/rad).",
    )
    p.add_argument("--seed", type=int, default=None, help="Optional RNG seed.")
    args = p.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    serve(args.host, args.port, jitter=max(0.0, float(args.jitter)))


if __name__ == "__main__":
    main()
