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

No idle socket timeout (real serve also waits indefinitely between steps).
"""

from __future__ import annotations

import argparse
import random
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


def handle_client(
    conn: socket.socket,
    addr: tuple,
    *,
    jitter: float,
    term_every: int,
) -> None:
    global _active_clients, _total_steps
    with _clients_lock:
        _active_clients += 1
        n_cli = _active_clients
    print(
        f"[{_ts()}] ACCEPT  {addr[0]}:{addr[1]}  "
        f"(active_clients={n_cli})  status=CONNECTED",
        flush=True,
    )
    step = 0
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

            next_state = [
                robot_state[i] + random.uniform(-jitter, jitter) for i in range(6)
            ] + [max(0.0, min(1.0, robot_state[6] + random.uniform(-jitter, jitter)))]

            # ~every N steps: terminate so Infer LOOP can exit cleanly.
            term_flag = 1.0 if (term_every > 0 and step % term_every == 0) else 0.0
            reject_flag = 0
            msg = f"fake-ok step={step} term={int(term_flag)}"
            msg_b = msg.encode("utf-8")

            conn.sendall(struct.pack(">7f", *[float(x) for x in next_state]))
            conn.sendall(struct.pack(">f", float(term_flag)))
            conn.sendall(struct.pack(">I", int(reject_flag)))
            conn.sendall(struct.pack(">I", len(msg_b)))
            conn.sendall(msg_b)

            prompt_preview = text.replace("\n", " ")[:60]
            print(
                f"[{_ts()}] STEP    {addr[0]}:{addr[1]}  "
                f"session_step={step} total_steps={tot}  "
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
    jitter: float,
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
        f"protocol=pi05_jax_sft.serve  jitter={jitter}  term_every={term_every}  "
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
                kwargs={"jitter": jitter, "term_every": term_every},
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
        "--jitter",
        type=float,
        default=0.005,
        help="Small per-step delta around received robot_state (first 6 + gripper).",
    )
    p.add_argument(
        "--term-every",
        type=int,
        default=20,
        help="Set term_flag=1 every N steps (0=never). Default 20.",
    )
    p.add_argument("--seed", type=int, default=None, help="Optional RNG seed.")
    p.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="Seconds between STATUS heartbeats (0=disable).",
    )
    args = p.parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)
    serve_forever(
        args.host,
        args.port,
        jitter=max(0.0, float(args.jitter)),
        term_every=max(0, int(args.term_every)),
        heartbeat_s=max(0.0, float(args.heartbeat)),
    )


if __name__ == "__main__":
    main()
