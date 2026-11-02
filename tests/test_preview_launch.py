"""Unit tests for webrtc-preview launch helpers (no real spawn)."""

from __future__ import annotations

from sensors_dcs.preview_launch import (
    build_launch_argv,
    build_preview_open_url,
    normalize_preview_app_url,
    parse_preview_listen,
    resolve_preview_app_url,
)


def test_normalize_preview_app_url() -> None:
    assert normalize_preview_app_url("") == "http://127.0.0.1:7050/"
    assert normalize_preview_app_url("127.0.0.1:7051") == "http://127.0.0.1:7051/"
    assert normalize_preview_app_url("http://192.168.1.8:7050/foo") == "http://192.168.1.8:7050/"


def test_resolve_priority(monkeypatch) -> None:
    monkeypatch.delenv("WEBRTC_PREVIEW_URL", raising=False)
    monkeypatch.delenv("SENSORS_DCS_PREVIEW_APP_URL", raising=False)
    assert resolve_preview_app_url(None, runtime_url="http://10.0.0.1:7050/") == "http://10.0.0.1:7050/"
    monkeypatch.setenv("WEBRTC_PREVIEW_URL", "http://127.0.0.1:7060")
    assert resolve_preview_app_url(None, runtime_url="http://10.0.0.1:7050/") == "http://127.0.0.1:7060/"
    assert (
        resolve_preview_app_url("http://override:1/", runtime_url="http://10.0.0.1:7050/")
        == "http://override:1/"
    )


def test_parse_and_open_url() -> None:
    assert parse_preview_listen("http://127.0.0.1:7050/") == ("127.0.0.1", 7050)
    url = build_preview_open_url(
        "http://127.0.0.1:7050/",
        "http://192.168.1.10:7011/api/preview/catalog",
    )
    assert url.startswith("http://127.0.0.1:7050/?catalog=")
    assert "7011" in url


def test_build_launch_argv_includes_port(monkeypatch) -> None:
    monkeypatch.delenv("WEBRTC_PREVIEW_LAUNCH", raising=False)
    monkeypatch.delenv("SENSORS_DCS_PREVIEW_LAUNCH", raising=False)
    monkeypatch.delenv("WEBRTC_PREVIEW_EXE", raising=False)
    argv, env, _cwd = build_launch_argv(base_url="http://127.0.0.1:7055/", catalog="http://x/c")
    assert "--port" in argv
    assert "7055" in argv
    assert env["WEBRTC_PREVIEW_PORT"] == "7055"
    assert env["WEBRTC_PREVIEW_CATALOG"] == "http://x/c"
