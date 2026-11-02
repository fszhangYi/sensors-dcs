"""Hello carries preview_catalog; master merges; disconnect drops (P2)."""

from __future__ import annotations

import time

from sensors_dcs.config import DcsConfig
from sensors_dcs.preview_catalog import build_local_preview_catalog
from sensors_dcs.station_peer import StationPeer


def _cfg(role: str, *, port: int, master_ip: str = "", station_id: str | None = None) -> DcsConfig:
    station: dict = {
        "role": role,
        "id": station_id or f"{role}-t",
        "advertise_ip": "127.0.0.1",
        "peer_port": port,
    }
    if role == "slave":
        station["master_ip"] = master_ip or "127.0.0.1"
        station["master_port"] = port
    return DcsConfig.model_validate(
        {
            "site": f"site-{role}",
            "sensors_config": "/tmp/x.yaml",
            "runtime": {"control_host": "127.0.0.1", "control_port": port, "viz_port": port + 1},
            "station": station,
            "agents": [
                {"id": "cam1", "type": "realsense", "sensor_id": "c1"},
                {"id": "webrtc-preview", "type": "webrtc_preview", "camera_agent_ids": ["cam1"]},
            ],
        }
    )


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def test_hello_preview_catalog_merged_and_drop() -> None:
    port = _free_port()

    def master_cat() -> dict:
        return build_local_preview_catalog(
            station_id="master-t",
            role="master",
            advertise_ip="192.168.1.10",
            viz_port=7011,
            agent_summaries=[
                {"id": "cam_m", "kind": "realsense"},
                {"id": "webrtc-preview", "kind": "webrtc_preview"},
            ],
        )

    def slave_cat() -> dict:
        return build_local_preview_catalog(
            station_id="slave-t",
            role="slave",
            advertise_ip="192.168.1.11",
            viz_port=7012,
            agent_summaries=[
                {"id": "cam_s", "kind": "realsense"},
                {"id": "webrtc-preview", "kind": "webrtc_preview"},
            ],
        )

    master = StationPeer(_cfg("master", port=port, station_id="master-t"), preview_catalog_fn=master_cat)
    slave = StationPeer(_cfg("slave", port=port, station_id="slave-t"), preview_catalog_fn=slave_cat)
    master.start()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if master.status().get("listening"):
            break
        time.sleep(0.05)
    try:
        r = slave.connect_to_master()
        assert r.get("ok") is True, r
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if master.status().get("slaves"):
                break
            time.sleep(0.05)
        snap = master.status()["slaves"][0]
        assert snap.get("preview_catalog"), snap
        assert snap["preview_catalog"].get("station_id") == "slave-t"

        merged = master.merged_preview_catalog()
        ids = {s.get("station_id") for s in merged["stations"]}
        assert "master-t" in ids and "slave-t" in ids
        assert len(merged["streams"]) >= 2
        rev_connected = merged["rev"]

        slave.disconnect()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not master.status().get("slaves"):
                break
            time.sleep(0.05)
        merged2 = master.merged_preview_catalog()
        assert all(s.get("station_id") != "slave-t" for s in merged2["stations"])
        assert merged2["rev"] != rev_connected
    finally:
        slave.stop()
        master.stop()


def test_hello_without_preview_catalog_still_connects() -> None:
    """Legacy slave: no preview_catalog_fn → degrade from agents[]; still connect."""
    port = _free_port()
    master = StationPeer(_cfg("master", port=port))
    slave = StationPeer(
        _cfg("slave", port=port),
        agents_summary_fn=lambda: [
            {"id": "cam1", "kind": "realsense"},
            {"id": "webrtc-preview", "kind": "webrtc_preview"},
        ],
    )
    master.start()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if master.status().get("listening"):
            break
        time.sleep(0.05)
    try:
        r = slave.connect_to_master()
        assert r.get("ok") is True, r
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if master.status().get("slaves"):
                break
            time.sleep(0.05)
        cat = master.status()["slaves"][0].get("preview_catalog") or {}
        assert cat.get("degraded") is True
        assert cat.get("streams")
    finally:
        slave.stop()
        master.stop()
