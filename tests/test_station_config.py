"""StationConfig YAML identity + summary helpers (multi-machine P0)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sensors_dcs.config import (
    DcsConfig,
    StationConfig,
    config_summary,
    resolve_master_port,
    resolve_peer_port,
    station_summary,
)


def _cfg(**kwargs) -> DcsConfig:
    base = {
        "sensors_config": "/tmp/x.yaml",
        "agents": [{"id": "g", "type": "gello", "sensor_id": "gello-1"}],
    }
    base.update(kwargs)
    return DcsConfig.model_validate(base)


def test_station_omitted_is_master() -> None:
    cfg = _cfg()
    assert cfg.station.role == "master"
    assert cfg.station.advertise_ip == ""
    st = station_summary(cfg)
    assert st["is_master"] is True
    assert st["is_slave"] is False
    assert st["id"] == "default"  # falls back to site
    assert st["peer_port"] == 7010
    assert st["master_port"] is None


def test_station_id_falls_back_to_site() -> None:
    cfg = _cfg(site="cell-b", station={"role": "master", "id": ""})
    assert station_summary(cfg)["id"] == "cell-b"
    cfg2 = _cfg(site="cell-b", station={"role": "master", "id": "rack-1"})
    assert station_summary(cfg2)["id"] == "rack-1"


def test_peer_port_falls_back_to_control_port() -> None:
    cfg = _cfg(
        runtime={"control_port": 7020, "viz_port": 7011},
        station={"role": "master", "peer_port": None},
    )
    assert resolve_peer_port(cfg) == 7020
    cfg2 = _cfg(station={"role": "master", "peer_port": 7100})
    assert resolve_peer_port(cfg2) == 7100


def test_slave_requires_master_ip() -> None:
    with pytest.raises(ValidationError, match="master_ip"):
        StationConfig(role="slave")
    st = StationConfig(role="slave", master_ip="192.168.1.10")
    assert st.role == "slave"
    assert st.master_ip == "192.168.1.10"


def test_slave_master_port_resolution() -> None:
    cfg = _cfg(
        runtime={"control_port": 7010},
        station={
            "role": "slave",
            "master_ip": "10.0.0.1",
            "advertise_ip": "10.0.0.2",
            "id": "slave-a",
        },
    )
    assert resolve_master_port(cfg) == 7010
    st = station_summary(cfg)
    assert st["is_slave"] is True
    assert st["master_ip"] == "10.0.0.1"
    assert st["master_port"] == 7010
    assert st["advertise_ip"] == "10.0.0.2"

    cfg2 = _cfg(
        station={
            "role": "slave",
            "master_ip": "10.0.0.1",
            "master_port": 7111,
        },
    )
    assert resolve_master_port(cfg2) == 7111


def test_role_aliases() -> None:
    assert StationConfig(role="primary").role == "master"
    assert StationConfig(role="follower", master_ip="1.2.3.4").role == "slave"


def test_config_summary_includes_station() -> None:
    cfg = _cfg(station={"role": "master", "advertise_ip": "192.168.1.5", "id": "m1"})
    summary = config_summary(cfg)
    assert summary["station"]["role"] == "master"
    assert summary["station"]["advertise_ip"] == "192.168.1.5"
    assert summary["station"]["id"] == "m1"


def test_bad_port_rejected() -> None:
    with pytest.raises(ValidationError):
        StationConfig(peer_port=0)
    with pytest.raises(ValidationError):
        StationConfig(peer_port=99999)
