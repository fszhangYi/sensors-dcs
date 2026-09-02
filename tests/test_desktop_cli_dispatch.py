from __future__ import annotations

import pytest

from sensors_dcs.desktop_main import _CLI_COMMANDS, _dispatch_cli_if_needed


def test_cli_commands_include_export() -> None:
    assert "export-timeline" in _CLI_COMMANDS
    assert "filter-timeline" in _CLI_COMMANDS


def test_dispatch_skips_desktop_flags() -> None:
    assert _dispatch_cli_if_needed(["-c", "configs/x.yaml", "--ui"]) is None
    assert _dispatch_cli_if_needed([]) is None


def test_dispatch_forwards_export_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_cli(argv: list[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 0

    monkeypatch.setattr("sensors_dcs.cli.main", fake_cli)
    # Import path used inside dispatch
    import sensors_dcs.cli as cli_mod

    monkeypatch.setattr(cli_mod, "main", fake_cli)

    code = _dispatch_cli_if_needed(
        ["export-timeline", "-e", "episode_00000", "--align", "asof"]
    )
    assert code == 0
    assert seen == [["export-timeline", "-e", "episode_00000", "--align", "asof"]]
