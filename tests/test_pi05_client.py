"""Unit tests for pi05 serve wire codec and mock TCP round-trip."""

from __future__ import annotations

import socket
import struct
import threading
from pathlib import Path

import yaml


def test_pack_unpack_serve_roundtrip() -> None:
    from sensors_dcs.agents.pi05_agent import pack_serve_request, unpack_serve_response

    top = b"\xff\xd8TOP"
    chest = b"\xff\xd8CHEST"
    wrist = b"\xff\xd8WRIST"
    state = [0.1, 0.2, 0.3, 0.01, -0.02, 0.03, 0.5]
    blob = pack_serve_request(
        top_jpeg=top,
        chest_jpeg=chest,
        wrist2_jpeg=wrist,
        text="pick cup",
        robot_state=state,
    )
    # Minimal fake server: parse request, reply fixed next_state.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = int(srv.getsockname()[1])
    srv.listen(1)

    def handle() -> None:
        conn, _ = srv.accept()
        with conn:
            def recv_exact(n: int) -> bytes:
                buf = b""
                while len(buf) < n:
                    pkt = conn.recv(n - len(buf))
                    assert pkt
                    buf += pkt
                return buf

            for _ in range(3):
                n = struct.unpack(">I", recv_exact(4))[0]
                recv_exact(n)
            tlen = struct.unpack(">I", recv_exact(4))[0]
            text = recv_exact(tlen).decode() if tlen else ""
            assert text == "pick cup"
            rs = list(struct.unpack(">7f", recv_exact(28)))
            assert abs(rs[0] - 0.1) < 1e-5
            next_state = [1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 0.9]
            conn.sendall(struct.pack(">7f", *next_state))
            conn.sendall(struct.pack(">f", 0.0))
            conn.sendall(struct.pack(">I", 0))
            msg = b"ok"
            conn.sendall(struct.pack(">I", len(msg)) + msg)

    th = threading.Thread(target=handle, daemon=True)
    th.start()
    client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    try:
        client.sendall(blob)
        resp = unpack_serve_response(client)
    finally:
        client.close()
        srv.close()
        th.join(timeout=2.0)
    assert resp is not None
    assert abs(resp["next_state"][0] - 1.0) < 1e-5
    assert resp["server_text"] == "ok"
    assert resp["reject_flag"] == 0


def test_pi05_agent_config_optional_sensor(tmp_path: Path) -> None:
    from sensors_dcs.config import load_dcs_config

    sensors = tmp_path / "sensors.yaml"
    sensors.write_text(
        Path("/root/autodl-tmp/sensors-dcs/configs/sensors_camera.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    dcs = tmp_path / "dcs.yaml"
    data = {
        "version": 1,
        "site": "pi05-test",
        "sensors_config": str(sensors),
        "dry_run": True,
        "pi05": {"host": "10.0.0.1", "port": 5001, "prompt": "demo"},
        "agents": [
            {
                "id": "cam",
                "type": "realsense",
                "sensor_id": "rs-main",
                "hz": 5,
                "buffer_frames": 2,
            },
            {"id": "pi05", "type": "pi05", "hz": 5, "buffer_frames": 4},
        ],
        "record": {"save_dir": str(tmp_path / "data")},
    }
    dcs.write_text(yaml.dump(data), encoding="utf-8")
    cfg = load_dcs_config(dcs)
    assert cfg.pi05.host == "10.0.0.1"
    assert any(a.type == "pi05" and a.sensor_id is None for a in cfg.agents)


def test_pi05_api_unconfigured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from sensors_dcs.auth_session import init_auth
    from sensors_dcs.viz import VizHub, create_viz_app
    from fastapi.testclient import TestClient

    init_auth()
    app = create_viz_app(
        VizHub(),
        lambda: {"ok": True},
        boot_box={"error": None},
        postprocess_save_dir=str(tmp_path),
    )
    client = TestClient(app)
    st = client.get("/api/pi05/status").json()
    assert st["ok"] is False
    assert st["configured"] is False
