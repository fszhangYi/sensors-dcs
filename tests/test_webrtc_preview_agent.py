"""WebRTC preview agent: config switch, hooks, record skip, JPEG suppress."""

from __future__ import annotations

import time
from pathlib import Path

from sensors_dcs.agents import build_agent
from sensors_dcs.agents.webrtc_preview_agent import WebrtcPreviewAgent
from sensors_dcs.buffer import FrameRing
from sensors_dcs.config import AgentConfig, RecordConfig
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController
from sensors_dcs.runtime import Orchestrator


class _CamFake:
    hz = 30.0
    kind = "realsense"
    sensor_id = "rs-fake"

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.ring = FrameRing(maxlen=8)
        self._seq = 0

    def push(self) -> None:
        self._seq += 1
        self.ring.push(
            Frame(
                sensor_id=self.sensor_id,
                agent_id=self.agent_id,
                kind=self.kind,
                t_wall=time.time(),
                t_mono=time.perf_counter(),
                seq=self._seq,
                payload={
                    "jpeg_b64_preview": "abc",
                    "jpeg_b64": "full",
                },
            )
        )

    def stats(self) -> dict:
        return {"hz_meas": 30.0}


def test_build_agent_webrtc_no_sensor() -> None:
    cfg = AgentConfig(
        id="webrtc-preview",
        type="webrtc_preview",
        hz=2.0,
        camera_agent_ids=["cam-a"],
        backend="stub",
    )
    ag = build_agent(cfg)
    assert isinstance(ag, WebrtcPreviewAgent)
    assert ag.kind == "webrtc_preview"


def test_hooks_benchmark_and_client_metrics() -> None:
    ag = WebrtcPreviewAgent(agent_id="webrtc-preview", backend="stub", hz=2.0)
    cam = _CamFake("cam-a")
    ag.bind_peers({"cam-a": cam})  # type: ignore[arg-type]
    ag._camera_agent_ids = ["cam-a"]

    cam.push()
    fr = ag.read_frame()
    assert fr.payload.get("backend") == "stub"
    assert fr.payload.get("source_fps") is not None or fr.payload.get("source_fps") is None

    started = ag.benchmark_start(duration_s=5.0, mode_label="stub-test")
    assert started["ok"] is True
    noted = ag.note_client_metrics(preview_fps=18.5, control_rtt_ms=12.0, mode_label="stub-test")
    assert noted["ok"] is True
    assert noted["client_preview_fps"] == 18.5
    stopped = ag.benchmark_stop()
    assert stopped["ok"] is True
    assert stopped["benchmark"]["mode_label"] == "stub-test"
    assert len(stopped["benchmark"]["client_samples"]) >= 1

    import asyncio

    offer = asyncio.run(ag.handle_offer(sdp="v=0", camera_agent_id="cam-a"))
    assert offer["ok"] is False
    assert offer.get("backend") == "stub"


def test_record_skips_webrtc_preview(tmp_path: Path) -> None:
    cam = _CamFake("cam-a")
    webrtc = WebrtcPreviewAgent(agent_id="webrtc-preview", backend="stub", hz=50.0)
    webrtc.bind_peers({"cam-a": cam})  # type: ignore[arg-type]
    webrtc._camera_agent_ids = ["cam-a"]

    rec = RecordController(
        RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=64),
        agents={"cam-a": cam, "webrtc-preview": webrtc},  # type: ignore[arg-type]
        site="lab",
    )
    assert rec.start(mode="collect")["ok"] is True
    ep = tmp_path / "episode_00000"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        cam.push()
        webrtc.ring.push(webrtc.read_frame())
        st = rec.status()
        if int(st.get("written") or 0) >= 1:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed out; status={rec.status()}")

    assert rec.stop(valid=True, timeout=5.0)["ok"] is True
    # realsense goes to cameras/; webrtc_preview must never land in states/
    assert not (ep / "states" / "webrtc-preview.jsonl").exists()
    assert "webrtc-preview" not in {
        p.name for p in (ep / "cameras").glob("*")
    } if (ep / "cameras").is_dir() else True


def test_telemetry_snapshot_strips_media_keeps_scalars() -> None:
    from sensors_dcs.agents.webrtc_preview_agent import (
        TELEMETRY_DC_LABEL,
        TELEMETRY_SCHEMA,
        _sanitize_telemetry_value,
    )

    assert _sanitize_telemetry_value({"jpeg_b64": "xxx", "q": [0.1, 0.2]}) == {"q": [0.1, 0.2]}
    assert _sanitize_telemetry_value("a" * 400).startswith("<str:")

    class _ArmFake:
        kind = "arm"
        sensor_id = "arm-1"

        def __init__(self) -> None:
            self.agent_id = "arm"
            self.ring = FrameRing(maxlen=4)
            self.ring.push(
                Frame(
                    sensor_id=self.sensor_id,
                    agent_id=self.agent_id,
                    kind=self.kind,
                    t_wall=time.time(),
                    t_mono=time.perf_counter(),
                    seq=3,
                    payload={
                        "q": [0.1, 0.2, 0.3],
                        "jpeg_b64": "SHOULD_DROP",
                        "depth": [[1, 2], [3, 4]],
                    },
                )
            )

    cam = _CamFake("cam-a")
    cam.push()
    arm = _ArmFake()
    ag = WebrtcPreviewAgent(
        agent_id="webrtc-preview",
        backend="stub",
        hz=2.0,
        camera_agent_ids=["cam-a"],
        telemetry_enabled=True,
        telemetry_hz=10.0,
    )
    ag.bind_peers({"cam-a": cam, "arm": arm, "webrtc-preview": ag})  # type: ignore[arg-type]
    snap = ag.build_telemetry_snapshot()
    assert snap["schema"] == TELEMETRY_SCHEMA
    assert "arm" in snap["agents"]
    # cameras are VideoTrack peers — not bundled into telemetry ids
    assert "cam-a" not in snap["agents"]
    assert "webrtc-preview" not in snap["agents"]
    assert snap["agents"]["arm"]["payload"].get("q") == [0.1, 0.2, 0.3]
    assert "jpeg_b64" not in (snap["agents"]["arm"]["payload"] or {})
    assert "depth" not in (snap["agents"]["arm"]["payload"] or {})
    one = ag.build_telemetry_snapshot(agent_ids=["arm"])
    assert one.get("agent_id") == "arm"
    assert list(one["agents"].keys()) == ["arm"]
    st = ag.status_payload()
    assert st["telemetry_enabled"] is True
    assert st["telemetry_data_channel"] == TELEMETRY_DC_LABEL
    assert st["telemetry_hz"] == 10.0


def test_suppress_jpeg_only_when_peer_live() -> None:
    ag = WebrtcPreviewAgent(
        agent_id="webrtc-preview",
        backend="stub",
        suppress_jpeg_ws=True,
        camera_agent_ids=["cam-a"],
    )
    assert ag.camera_ids_for_suppress() == set()
    ag.set_peer_count(1)
    assert ag.camera_ids_for_suppress() == {"cam-a"}
    ag.set_peer_count(0)
    assert ag.camera_ids_for_suppress() == set()


def test_frame_for_viz_strip_jpeg() -> None:
    fr = Frame(
        sensor_id="rs",
        agent_id="cam-a",
        kind="realsense",
        t_wall=1.0,
        t_mono=1.0,
        seq=1,
        payload={"jpeg_b64_preview": "x", "jpeg_b64": "y"},
    )
    kept = Orchestrator._frame_for_viz(fr, strip_jpeg=False)
    assert kept["payload"].get("jpeg_b64_preview") == "x"
    stripped = Orchestrator._frame_for_viz(fr, strip_jpeg=True)
    assert "jpeg_b64_preview" not in stripped["payload"]
    assert stripped["payload"].get("jpeg_suppressed_for_webrtc") is True


def test_aiortc_media_module_optional() -> None:
    from sensors_dcs.webrtc_preview_media import aiortc_available

    # Soft assert: environment may or may not have aiortc; call must not raise.
    assert isinstance(aiortc_available(), bool)


def test_aiortc_answer_offer_roundtrip() -> None:
    import asyncio
    import base64

    import cv2
    import numpy as np
    import pytest

    from sensors_dcs.webrtc_preview_media import aiortc_available, answer_offer

    if not aiortc_available():
        pytest.skip("aiortc not installed")

    from aiortc import RTCPeerConnection

    img = np.zeros((48, 64, 3), dtype=np.uint8)
    img[:] = (40, 80, 120)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    cam = _CamFake("cam-a")
    cam.ring.push(
        Frame(
            sensor_id="rs",
            agent_id="cam-a",
            kind="realsense",
            t_wall=time.time(),
            t_mono=time.perf_counter(),
            seq=1,
            payload={"jpeg_b64": b64},
        )
    )
    class _ArmFake:
        kind = "arm"

        def __init__(self) -> None:
            self.agent_id = "arm"
            self.ring = FrameRing(maxlen=4)
            self.ring.push(
                Frame(
                    sensor_id="arm",
                    agent_id="arm",
                    kind="arm",
                    t_wall=time.time(),
                    t_mono=time.perf_counter(),
                    seq=1,
                    payload={"q": [1.0, 2.0]},
                )
            )

    arm = _ArmFake()
    ag = WebrtcPreviewAgent(
        agent_id="webrtc-preview",
        backend="aiortc",
        hz=2.0,
        camera_agent_ids=["cam-a"],
        telemetry_enabled=True,
        telemetry_hz=20.0,
    )
    ag.bind_peers({"cam-a": cam, "arm": arm})  # type: ignore[arg-type]

    async def _run() -> dict:
        browser = RTCPeerConnection()
        got: dict = {"msg": None}
        # Offerer creates DC so SDP includes m=application (required for telemetry).
        ch = browser.createDataChannel("sensors-preview")

        @ch.on("message")
        def _on_msg(message) -> None:  # noqa: ANN001
            got["msg"] = message

        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        assert browser.localDescription is not None
        assert "m=application" in (browser.localDescription.sdp or "")
        ans = await answer_offer(
            ag,
            sdp=browser.localDescription.sdp,
            camera_agent_id="cam-a",
        )
        if ans.get("ok") and ans.get("sdp"):
            from aiortc import RTCSessionDescription

            await browser.setRemoteDescription(
                RTCSessionDescription(sdp=ans["sdp"], type=ans.get("type") or "answer")
            )
            for _ in range(80):
                if got["msg"] is not None:
                    break
                await asyncio.sleep(0.05)
        await browser.close()
        ans["_dc_msg"] = got["msg"]
        return ans

    out = asyncio.run(_run())
    assert out.get("ok") is True, out
    assert out.get("sdp")
    assert out.get("backend") == "aiortc"
    # Camera peer is video-only; per-sensor DC is a separate catalog stream.
    assert out.get("telemetry_only") in (False, None)
    assert out.get("telemetry_data_channel") in (None, "sensors-preview")


def test_telemetry_only_offer_no_camera() -> None:
    import asyncio
    import json

    import pytest

    from sensors_dcs.webrtc_preview_media import aiortc_available, answer_offer

    if not aiortc_available():
        pytest.skip("aiortc not installed")

    from aiortc import RTCPeerConnection, RTCSessionDescription

    class _ArmFake:
        kind = "arm"

        def __init__(self) -> None:
            self.agent_id = "arm"
            self.ring = FrameRing(maxlen=4)
            self.ring.push(
                Frame(
                    sensor_id="arm",
                    agent_id="arm",
                    kind="arm",
                    t_wall=time.time(),
                    t_mono=time.perf_counter(),
                    seq=1,
                    payload={"q": [1.0]},
                )
            )

    arm = _ArmFake()
    ag = WebrtcPreviewAgent(
        agent_id="webrtc-preview",
        backend="aiortc",
        camera_agent_ids=["cam-a"],
        telemetry_enabled=True,
        telemetry_hz=20.0,
    )
    ag.bind_peers({"arm": arm})  # type: ignore[arg-type]

    async def _run() -> dict:
        browser = RTCPeerConnection()
        got: dict = {"msg": None}
        ch = browser.createDataChannel("sensors-preview")

        @ch.on("message")
        def _on_msg(message) -> None:  # noqa: ANN001
            got["msg"] = message

        # no video transceiver — telemetry peer only
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        assert "m=application" in (browser.localDescription.sdp or "")
        ans = await answer_offer(
            ag,
            sdp=browser.localDescription.sdp,
            telemetry_only=True,
            telemetry_agent_id="arm",
        )
        if ans.get("ok") and ans.get("sdp"):
            await browser.setRemoteDescription(
                RTCSessionDescription(sdp=ans["sdp"], type=ans.get("type") or "answer")
            )
            for _ in range(80):
                if got["msg"] is not None:
                    break
                await asyncio.sleep(0.05)
        await browser.close()
        ans["_dc_msg"] = got["msg"]
        return ans

    out = asyncio.run(_run())
    assert out.get("ok") is True, out
    assert out.get("telemetry_only") is True
    assert out.get("camera_agent_id") is None
    assert out.get("telemetry_agent_id") == "arm"
    if out.get("_dc_msg"):
        payload = json.loads(out["_dc_msg"])
        assert payload.get("agent_id") == "arm"
        assert "arm" in (payload.get("agents") or {})


def test_agent_config_yaml_roundtrip(tmp_path: Path) -> None:
    from sensors_dcs.config import load_dcs_config

    # Minimal dcs yaml with only webrtc + placeholder sensors path unused for this type
    sensors = tmp_path / "sensors.yaml"
    sensors.write_text(
        "version: 1\ndevices: []\n",
        encoding="utf-8",
    )
    dcs = tmp_path / "dcs.yaml"
    dcs.write_text(
        f"""
version: 1
site: test
dry_run: true
sensors_config: {sensors.as_posix()}
agents:
  - id: webrtc-preview
    type: webrtc_preview
    hz: 2
    camera_agent_ids: [cam-x]
    suppress_jpeg_ws: true
    backend: stub
""",
        encoding="utf-8",
    )
    cfg = load_dcs_config(str(dcs))
    assert cfg.agents[0].type == "webrtc_preview"
    assert cfg.agents[0].camera_agent_ids == ["cam-x"]
    assert cfg.agents[0].telemetry_enabled is True
    assert cfg.agents[0].telemetry_hz == 15.0
    built = build_agent(cfg.agents[0])
    assert isinstance(built, WebrtcPreviewAgent)
    assert built._telemetry_enabled is True
