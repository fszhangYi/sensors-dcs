"""UI must stay up when agent open() fails under dry_run=false."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_boot_box_locks_collect_until_cleared(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from sensors_dcs.auth_session import init_auth
    from sensors_dcs.viz import VizHub, create_viz_app

    init_auth()
    hub = VizHub()
    boot_box: dict = {"error": "agents starting…"}

    def status_fn() -> dict:
        return {"ok": True, "agents": {}}

    app = create_viz_app(
        hub,
        status_fn,
        boot_box=boot_box,
        config_path=str(tmp_path / "x.yaml"),
        postprocess_save_dir=str(tmp_path),
    )
    client = TestClient(app)

    health = client.get("/api/health").json()
    assert health["boot_error"] is True
    assert health["collect_ok"] is False
    assert health["infer_ok"] is True

    st = client.get("/api/status").json()
    assert st["boot_error"] is True
    assert st["infer_ok"] is True
    assert "agents starting" in (st.get("error") or "")

    rec = client.post("/api/record/start").json()
    assert rec["ok"] is False

    pp = client.get("/api/postprocess/defaults").json()
    assert pp["ok"] is True

    boot_box["error"] = None
    health2 = client.get("/api/health").json()
    assert health2["boot_error"] is False
    assert health2["collect_ok"] is True
    assert health2["infer_ok"] is True


def test_cli_serve_survives_agent_open_failure(tmp_path: Path, monkeypatch) -> None:
    """Regression: non-dry-run open() raise used to crash before uvicorn."""
    import json
    import socket
    import threading
    import time
    import urllib.request

    import yaml

    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from sensors_dcs.auth_session import init_auth

    init_auth()

    sensors = tmp_path / "sensors_gello.yaml"
    sensors.write_text(
        Path("/root/autodl-tmp/sensors-dcs/configs/sensors_gello.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    dcs = tmp_path / "gello.yaml"
    data = yaml.safe_load(
        Path("/root/autodl-tmp/sensors-dcs/configs/gello_only.yaml").read_text(
            encoding="utf-8"
        )
    )
    data["dry_run"] = False
    data["sensors_config"] = str(sensors)
    data["runtime"]["viz_host"] = "127.0.0.1"
    data["record"]["save_dir"] = str(tmp_path / "data")
    dcs.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")

    from sensors_dcs.config import load_dcs_config
    from sensors_dcs.runtime import Orchestrator

    cfg = load_dcs_config(dcs)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    cfg.runtime.viz_port = port
    cfg.runtime.viz_host = "127.0.0.1"

    orch = Orchestrator(cfg)
    th = threading.Thread(target=orch.serve, name="orch-serve-test", daemon=True)
    th.start()

    deadline = time.time() + 15.0
    st = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=1.0
            ) as resp:
                st = json.loads(resp.read().decode("utf-8"))
            err = str(st.get("error") or "")
            if st.get("boot_error") and "starting" not in err:
                break
            if st.get("boot_error") is False:
                break
        except Exception:
            time.sleep(0.2)

    assert st is not None, "UI never became reachable"
    assert st.get("boot_error") is True
    assert st.get("collect_ok") is False
    assert "dynamixel" in str(st.get("error") or "").lower() or "open" in str(
        st.get("error") or ""
    ).lower() or st.get("error")

    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/postprocess/defaults", timeout=2.0
    ) as resp:
        pp = json.loads(resp.read().decode("utf-8"))
    assert pp.get("ok") is True

    orch.request_shutdown()
    th.join(timeout=8.0)
