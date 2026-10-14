"""StationPeer TCP hello / heartbeat / disconnect (LAN master-slave P0)."""

from __future__ import annotations

import time

import pytest

from sensors_dcs.config import DcsConfig
from sensors_dcs.station_peer import StationPeer


def _cfg(role: str, *, port: int, master_ip: str = "", advertise_ip: str = "127.0.0.1") -> DcsConfig:
    station: dict = {
        "role": role,
        "id": f"{role}-t",
        "advertise_ip": advertise_ip,
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
            "agents": [{"id": "g", "type": "gello", "sensor_id": "g1"}],
        }
    )


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture
def peer_pair():
    port = _free_port()
    master = StationPeer(_cfg("master", port=port))
    slave = StationPeer(_cfg("slave", port=port, master_ip="127.0.0.1"))
    master.start()
    # Wait until listening.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        st = master.status()
        if st.get("listening") or st.get("link_state") in ("listening", "connected"):
            break
        time.sleep(0.05)
    yield master, slave, port
    slave.stop()
    master.stop()


def test_master_listen_idle(peer_pair) -> None:
    master, _slave, _port = peer_pair
    st = master.status()
    assert st["listening"] is True
    assert st["link_state"] == "listening"
    assert st["slaves"] == []


def test_slave_connect_and_disconnect(peer_pair) -> None:
    master, slave, _port = peer_pair
    r = slave.connect_to_master()
    assert r.get("ok") is True, r
    assert r.get("link_state") == "connected"

    deadline = time.time() + 3.0
    while time.time() < deadline:
        ms = master.status()
        if ms.get("slaves"):
            break
        time.sleep(0.05)
    ms = master.status()
    assert len(ms["slaves"]) == 1
    assert ms["slaves"][0]["id"] == "slave-t"
    assert ms["link_state"] == "connected"

    d = slave.disconnect()
    assert d.get("ok") is True
    assert d.get("link_state") == "idle"

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not master.status().get("slaves"):
            break
        time.sleep(0.05)
    assert master.status()["slaves"] == []


def test_master_kick_slave(peer_pair) -> None:
    master, slave, _port = peer_pair
    assert slave.connect_to_master().get("ok") is True
    deadline = time.time() + 3.0
    while time.time() < deadline and not master.status().get("slaves"):
        time.sleep(0.05)
    assert master.has_active_slaves() is True
    kick = master.kick_slave("slave-t")
    assert kick.get("ok") is True
    assert master.has_active_slaves() is False


def test_slave_rejects_inbound() -> None:
    port = _free_port()
    slave = StationPeer(_cfg("slave", port=port, master_ip="127.0.0.1"))
    # Force a listen path by temporarily calling accept handler is covered by role check
    # in _handle_inbound; here we only assert connect without master fails cleanly.
    r = slave.connect_to_master()
    assert r.get("ok") is False
    slave.stop()


def test_has_active_slaves_false_when_idle(peer_pair) -> None:
    master, _slave, _port = peer_pair
    assert master.has_active_slaves() is False
