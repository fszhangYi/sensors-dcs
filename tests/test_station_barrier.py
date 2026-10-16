"""P2 peer barrier: PREPARE/COMMIT/STOP with master fallback when no slaves."""

from __future__ import annotations

import time
from typing import Any

import pytest

from sensors_dcs.config import DcsConfig, RecordConfig
from sensors_dcs.record import RecordController
from sensors_dcs.station_peer import StationPeer


def _cfg(role: str, *, port: int, master_ip: str = "") -> DcsConfig:
    station: dict[str, Any] = {
        "role": role,
        "id": f"{role}-b",
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
            "runtime": {
                "control_host": "127.0.0.1",
                "control_port": port,
                "viz_port": port + 1,
            },
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
def linked_peers():
    port = _free_port()
    master = StationPeer(_cfg("master", port=port))
    slave = StationPeer(_cfg("slave", port=port))
    master.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not master.status().get("listening"):
        time.sleep(0.05)
    assert slave.connect_to_master().get("ok") is True
    deadline = time.time() + 3.0
    while time.time() < deadline and not master.has_active_slaves():
        time.sleep(0.05)
    assert master.has_active_slaves()
    yield master, slave
    slave.stop()
    master.stop()


def test_barrier_falls_back_without_slaves() -> None:
    port = _free_port()
    master = StationPeer(_cfg("master", port=port))
    master.start()
    calls: list[dict[str, Any]] = []

    def local_start(*, mode: str = "collect", session_meta=None):
        calls.append({"mode": mode, "session_meta": session_meta})
        return {"ok": True, "state": "recording"}

    r = master.barrier_start(mode="collect", local_start=local_start)
    assert r.get("ok") is True
    assert r.get("barrier") is None or r.get("barrier") is False
    assert calls == [{"mode": "collect", "session_meta": None}]
    master.stop()


def test_barrier_start_and_stop(linked_peers) -> None:
    master, slave = linked_peers
    slave_events: list[tuple[str, Any]] = []

    def on_prepare(mode: str) -> dict[str, Any]:
        slave_events.append(("prepare", mode))
        return {"ok": True}

    def on_commit(mode: str, meta: dict[str, Any]) -> dict[str, Any]:
        slave_events.append(("commit", mode, meta))
        return {"ok": True, "state": "recording"}

    def on_abort(session_id: str, reason: str) -> None:
        slave_events.append(("abort", session_id, reason))

    def on_stop(body: dict[str, Any]) -> dict[str, Any]:
        slave_events.append(("stop", body))
        return {"ok": True, "state": "idle"}

    slave.bind_record_handlers(
        on_prepare=on_prepare,
        on_commit=on_commit,
        on_abort=on_abort,
        on_stop=on_stop,
    )

    master_starts: list[Any] = []

    def local_start(*, mode: str = "collect", session_meta=None):
        master_starts.append({"mode": mode, "session_meta": session_meta})
        return {"ok": True, "state": "recording", "t_start": time.time()}

    def local_stop(*, valid=True, async_flush=False, keep_files=True):
        return {
            "ok": True,
            "state": "idle",
            "valid": valid,
            "async_flush": async_flush,
            "keep_files": keep_files,
        }

    r = master.barrier_start(mode="collect", local_start=local_start)
    assert r.get("ok") is True, r
    assert r.get("barrier") is True
    assert r.get("session_id")
    assert master_starts and master_starts[0]["session_meta"]["session_id"]
    assert master_starts[0]["session_meta"]["station_role"] == "master"

    deadline = time.time() + 3.0
    while time.time() < deadline and not any(e[0] == "commit" for e in slave_events):
        time.sleep(0.05)
    assert any(e[0] == "prepare" for e in slave_events)
    assert any(e[0] == "commit" for e in slave_events)
    commit = next(e for e in slave_events if e[0] == "commit")
    assert commit[2].get("station_role") == "slave"
    assert commit[2].get("session_id") == r["session_id"]

    s = master.barrier_stop(local_stop=local_stop, valid=True)
    assert s.get("ok") is True, s
    assert any(e[0] == "stop" for e in slave_events)


def test_prepare_nack_aborts(linked_peers) -> None:
    master, slave = linked_peers

    slave.bind_record_handlers(
        on_prepare=lambda _m: {"ok": False, "error": "busy"},
        on_commit=lambda _m, _meta: {"ok": True},
        on_stop=lambda _b: {"ok": True},
    )

    called = {"start": False}

    def local_start(*, mode: str = "collect", session_meta=None):
        called["start"] = True
        return {"ok": True}

    r = master.barrier_start(mode="infer", local_start=local_start)
    assert r.get("ok") is False
    assert called["start"] is False
    assert "busy" in str(r.get("error") or "")


def test_record_manifest_session_meta(tmp_path) -> None:
    rec = RecordController(
        RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8),
        agents={},
        site="cell",
    )
    meta = {
        "session_id": "sess-1",
        "station_id": "master-b",
        "station_role": "master",
        "peer_slaves": [{"id": "slave-b"}],
    }
    r = rec.start(mode="collect", session_meta=meta)
    assert r.get("ok") is True
    man_path = tmp_path / "episode_00000" / "manifest.json"
    assert man_path.is_file()
    import json

    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["session_id"] == "sess-1"
    assert man["station_role"] == "master"
    assert man["peer_slaves"][0]["id"] == "slave-b"
    rec.stop(valid=True, async_flush=False)


def test_barrier_assigns_shared_episode_index(linked_peers, tmp_path) -> None:
    master, slave = linked_peers
    master_rec = RecordController(
        RecordConfig(save_dir=str(tmp_path / "m"), episode_index=7, queue_maxsize=8),
        agents={},
        site="m",
    )
    slave_rec = RecordController(
        RecordConfig(save_dir=str(tmp_path / "s"), episode_index=99, queue_maxsize=8),
        agents={},
        site="s",
    )

    def on_prepare(mode: str, body=None) -> dict[str, Any]:
        body = body or {}
        assert body.get("episode_index") == 7
        return {"ok": True, "mode": mode}

    def on_commit(mode: str, meta: dict[str, Any]) -> dict[str, Any]:
        assert meta.get("episode_index") == 7
        return slave_rec.start(mode=mode, session_meta=meta)

    slave.bind_record_handlers(
        on_prepare=on_prepare,
        on_commit=on_commit,
        on_stop=lambda body: slave_rec.stop(
            valid=bool(body.get("valid", True)),
            async_flush=False,
            keep_files=True,
        ),
    )

    r = master.barrier_start(
        mode="collect",
        local_start=master_rec.start,
        episode_index=7,
    )
    assert r.get("ok") is True, r
    assert (tmp_path / "m" / "episode_00007").is_dir()
    assert (tmp_path / "s" / "episode_00007").is_dir()
    assert slave_rec.episode_index == 7
    assert master_rec.status().get("episode_index") == 7

    s = master.barrier_stop(local_stop=master_rec.stop, valid=True)
    assert s.get("ok") is True, s


def test_ping_slave_rtt(linked_peers) -> None:
    master, slave = linked_peers
    sid = "slave-b"
    r = master.ping_slave(sid)
    assert r.get("ok") is True, r
    assert r.get("station_id") == sid
    assert isinstance(r.get("rtt_ms"), (int, float))
    assert r["rtt_ms"] >= 0
    assert r.get("one_way_ms") == round(float(r["rtt_ms"]) / 2.0, 3)
    assert r.get("clock_offset_est_ms") is None or isinstance(
        r.get("clock_offset_est_ms"), (int, float)
    )
    slaves = master.status().get("slaves") or []
    assert slaves and slaves[0].get("last_rtt_ms") is not None


def test_probe_slave_zero_time_budget(linked_peers, tmp_path) -> None:
    master, slave = linked_peers
    # Point both peers at writable save dirs for mkdir probe.
    master.cfg.record.save_dir = str(tmp_path / "m")
    slave.cfg.record.save_dir = str(tmp_path / "s")
    sid = "slave-b"
    r = master.probe_slave(sid)
    assert r.get("ok") is True, r
    assert r.get("rtt_ms") is not None
    assert r.get("one_way_ms") is not None
    assert r.get("slave_start_overhead_ms") is not None
    assert r.get("master_commit_to_start_ms") is not None
    assert r.get("delta_est_ms") is not None
    assert float(r["delta_est_ms"]) >= float(r["one_way_ms"])
    slaves = master.status().get("slaves") or []
    assert slaves and slaves[0].get("last_probe")
    assert slaves[0]["last_probe"].get("delta_est_ms") is not None
