"""Config paths must stay valid after desktop chdir into user-data/run."""

from __future__ import annotations

import os
from pathlib import Path


def test_default_yaml_resolved_from_launch_pwd(tmp_path: Path, monkeypatch) -> None:
    import sensors_dcs.paths as paths

    repo = Path("/root/autodl-tmp/sensors-dcs")
    assert (repo / "configs" / "default.yaml").is_file()

    paths._LAUNCH_CWD = None
    monkeypatch.chdir(repo)
    paths.capture_launch_cwd()

    # Relative -c as start-6006.sh / desktop_main pass.
    monkeypatch.setenv("SENSORS_DCS_CONFIG", "configs/default.yaml")
    resolved = paths.default_dcs_config()
    assert resolved == (repo / "configs" / "default.yaml").resolve()
    assert resolved.is_file()

    # Simulate desktop chdir away from the repo.
    work = tmp_path / "run"
    work.mkdir()
    monkeypatch.chdir(work)
    assert Path.cwd() == work.resolve()

    still = paths.default_dcs_config()
    assert still == resolved
    assert still.is_file()


def test_ensure_runtime_env_keeps_pwd_default(monkeypatch, tmp_path: Path) -> None:
    import sensors_dcs.paths as paths

    repo = Path("/root/autodl-tmp/sensors-dcs")
    paths._LAUNCH_CWD = None
    monkeypatch.chdir(repo)
    monkeypatch.delenv("SENSORS_DCS_CONFIG", raising=False)
    # Isolate active-config pointer / user-data side effects.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    data = paths.ensure_runtime_env(desktop=True)
    assert data.is_dir()
    # After chdir, cwd is under user-data/run — config must still be repo default.
    assert Path.cwd() != repo
    cfg = paths.default_dcs_config()
    assert cfg.name == "default.yaml"
    assert cfg.is_file()
    assert "configs/default.yaml" in str(cfg).replace("\\", "/")
    assert os.environ["SENSORS_DCS_CONFIG"] == str(cfg)
