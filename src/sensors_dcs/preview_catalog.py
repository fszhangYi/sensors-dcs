"""Preview catalog builder/merger — media-plane directory only (not DCS runtime YAML).

See docs/preview-catalog-webrtc.md.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_STATION = "sensors-dcs.preview_catalog.v1"
SCHEMA_MERGED = "sensors-dcs.preview_catalog.merged.v1"

_CAMERA_KINDS = frozenset(
    {
        "realsense",
        "camera",
        "rgb",
        "usb_cam",
        "usbcam",
        "opencv_cam",
    }
)


def _stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _is_unspecified(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("", "0.0.0.0", "::", "::0")


def _is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("localhost", "127.0.0.1", "::1")


def _public_host(advertise_ip: str, *, allow_loopback: bool = True) -> str | None:
    """Host for viz_base / signaling.

    Loopback is allowed for single-machine preview (catalog pulled via 127.0.0.1).
    Unspecified bind addresses are never used as a client-facing host.
    """
    ip = (advertise_ip or "").strip()
    if not ip or _is_unspecified(ip):
        return None
    if _is_loopback(ip) and not allow_loopback:
        return None
    return ip


def _viz_base(advertise_ip: str, viz_port: int) -> tuple[str | None, str]:
    """Return (viz_base_url or None, note)."""
    host = _public_host(advertise_ip, allow_loopback=True)
    if host is None:
        return None, "unset"
    note = "loopback" if _is_loopback(host) else "ok"
    return f"http://{host}:{int(viz_port)}", note


def rewrite_local_station_origin(
    catalog: Mapping[str, Any] | dict[str, Any],
    *,
    local_station_id: str,
    origin: str,
) -> dict[str, Any]:
    """Point this station's stream signaling URLs at the HTTP origin the client used.

    Fixes single-PC dry-run when ``advertise_ip`` is a LAN IP that is not routed
    on this machine, while the catalog was fetched via ``http://127.0.0.1:viz``.
    Slave stations in a merged catalog are left unchanged.
    """
    out = dict(catalog)
    origin_n = (origin or "").rstrip("/")
    if not origin_n or not local_station_id:
        return out
    stations: list[dict[str, Any]] = []
    for st in out.get("stations") or []:
        if not isinstance(st, Mapping):
            continue
        row = dict(st)
        if str(row.get("station_id") or "") == local_station_id:
            row["viz_base"] = origin_n
            row["viz_base_note"] = "request_origin"
            streams_st = []
            for s in row.get("streams") or []:
                if not isinstance(s, Mapping):
                    continue
                ss = dict(s)
                ss["signaling_url"] = f"{origin_n}/api/webrtc/preview/offer"
                if ss.get("agent_id"):
                    ss["enabled"] = True
                streams_st.append(ss)
            row["streams"] = streams_st
        stations.append(row)
    out["stations"] = stations
    streams: list[dict[str, Any]] = []
    for s in out.get("streams") or []:
        if not isinstance(s, Mapping):
            continue
        ss = dict(s)
        if str(ss.get("station_id") or "") == local_station_id:
            ss["signaling_url"] = f"{origin_n}/api/webrtc/preview/offer"
            if ss.get("agent_id"):
                ss["enabled"] = True
        streams.append(ss)
    out["streams"] = streams
    return out


def _camera_ids_from_webrtc_agent(agent: Any) -> list[str]:
    ids = getattr(agent, "_camera_agent_ids", None) or getattr(agent, "camera_agent_ids", None)
    if ids:
        return [str(x).strip() for x in ids if str(x).strip()]
    resolve = getattr(agent, "_resolve_camera_ids", None)
    if callable(resolve):
        try:
            return [str(x).strip() for x in resolve() if str(x).strip()]
        except Exception:  # noqa: BLE001
            return []
    return []


def _find_webrtc_agent(agents: Mapping[str, Any] | None) -> tuple[str | None, Any | None]:
    if not agents:
        return None, None
    for aid, ag in agents.items():
        kind = str(getattr(ag, "kind", "") or "").strip().lower()
        typ = str(getattr(ag, "type", "") or "").strip().lower()
        if kind == "webrtc_preview" or typ == "webrtc_preview":
            return str(aid), ag
    return None, None


def _agent_kind(ag: Any) -> str:
    return str(getattr(ag, "kind", None) or getattr(ag, "type", None) or "").strip().lower()


def _is_camera_kind(kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k in _CAMERA_KINDS:
        return True
    if "realsense" in k or k.startswith("cam"):
        return True
    return False


def _is_skipped_preview_kind(kind: str) -> bool:
    k = (kind or "").strip().lower()
    return k in ("", "webrtc_preview") or _is_camera_kind(k)


def _sensor_stream_rows(
    *,
    station_id: str,
    base: str | None,
    sensor_rows: Sequence[tuple[str, str]],
    enabled: bool,
) -> list[dict[str, Any]]:
    """One catalog stream per non-camera sensor (equal to cameras)."""
    streams: list[dict[str, Any]] = []
    sig = f"{base}/api/webrtc/preview/offer" if base else ""
    for aid, kind in sensor_rows:
        aid_n = str(aid).strip()
        if not aid_n:
            continue
        kind_n = str(kind or "sensor").strip() or "sensor"
        streams.append(
            {
                "stream_id": f"{station_id}/{aid_n}",
                "station_id": station_id,
                "agent_id": aid_n,
                "kind": kind_n,
                "media": "datachannel",
                "label": aid_n,
                "signaling_url": sig,
                "offer_body_hint": {
                    "telemetry_only": True,
                    "telemetry_agent_id": aid_n,
                    "camera_agent_id": None,
                },
                "enabled": bool(enabled and sig),
            }
        )
    return streams


def degrade_catalog_from_agents_summary(
    *,
    station_id: str,
    role: str,
    advertise_ip: str,
    viz_port: int,
    agents: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Legacy hello without preview_catalog: infer camera streams from agents[]."""
    agents = list(agents or [])
    webrtc_id = None
    cam_ids: list[str] = []
    for a in agents:
        aid = str(a.get("id") or "").strip()
        kind = str(a.get("kind") or a.get("type") or "").strip().lower()
        if not aid:
            continue
        if kind == "webrtc_preview":
            webrtc_id = aid
            continue
        if _is_camera_kind(kind):
            cam_ids.append(aid)
    enabled = bool(webrtc_id and cam_ids)
    base, note = _viz_base(advertise_ip, viz_port)
    streams: list[dict[str, Any]] = []
    for cid in cam_ids:
        sig = f"{base}/api/webrtc/preview/offer" if base else ""
        streams.append(
            {
                "stream_id": f"{station_id}/{cid}",
                "station_id": station_id,
                "agent_id": cid,
                "kind": "camera",
                "media": "video",
                "label": cid,
                "signaling_url": sig,
                "offer_body_hint": {"camera_agent_id": cid},
                "enabled": bool(enabled and sig),
            }
        )
    sensor_rows: list[tuple[str, str]] = []
    for a in agents:
        aid = str(a.get("id") or "").strip()
        kind = str(a.get("kind") or a.get("type") or "").strip().lower()
        if not aid or aid == webrtc_id or _is_skipped_preview_kind(kind):
            continue
        if aid in cam_ids:
            continue
        sensor_rows.append((aid, kind or "sensor"))
    if webrtc_id:
        streams.extend(
            _sensor_stream_rows(
                station_id=station_id,
                base=base,
                sensor_rows=sensor_rows,
                enabled=bool(enabled),
            )
        )
    payload = {
        "schema": SCHEMA_STATION,
        "station_id": station_id,
        "role": role,
        "advertise_ip": (advertise_ip or "").strip(),
        "viz_port": int(viz_port),
        "viz_base": base,
        "viz_base_note": note,
        "webrtc_signaling": {
            "offer_path": "/api/webrtc/preview/offer",
            "status_path": "/api/webrtc/preview/status",
        },
        "streams": streams,
        "webrtc_agent": {
            "id": webrtc_id,
            "available": bool(webrtc_id),
            "backend": None,
            "camera_agent_ids": list(cam_ids),
            "telemetry_enabled": None,
            "telemetry_hz": None,
            "telemetry_data_channel": "sensors-preview",
            "telemetry_agent_ids": [r[0] for r in sensor_rows],
        },
        "degraded": True,
        "degraded_reason": "hello missing preview_catalog; inferred from agents[]",
    }
    payload["config_rev"] = _stable_hash(
        {
            "station_id": station_id,
            "cams": cam_ids,
            "webrtc": webrtc_id,
            "viz_port": viz_port,
            "advertise_ip": advertise_ip,
        }
    )
    return payload


def build_local_preview_catalog(
    *,
    station_id: str,
    role: str,
    advertise_ip: str,
    viz_port: int,
    agents: Mapping[str, Any] | None = None,
    agent_summaries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a single-station preview catalog from live agents or summaries."""
    webrtc_id, webrtc_ag = _find_webrtc_agent(agents)
    backend = None
    available = False
    cam_ids: list[str] = []
    telemetry_enabled: bool | None = None
    telemetry_hz: float | None = None
    telemetry_dc = "sensors-preview"
    sensor_ids: list[str] = []
    if webrtc_ag is not None:
        available = True
        cam_ids = _camera_ids_from_webrtc_agent(webrtc_ag)
        telemetry_enabled = bool(getattr(webrtc_ag, "_telemetry_enabled", True))
        telemetry_hz = float(getattr(webrtc_ag, "_telemetry_hz", 15.0) or 15.0)
        resolve_tel = getattr(webrtc_ag, "_resolve_telemetry_ids", None)
        if callable(resolve_tel):
            try:
                sensor_ids = [str(x).strip() for x in resolve_tel() if str(x).strip()]
            except Exception:  # noqa: BLE001
                sensor_ids = []
        st = getattr(webrtc_ag, "status_payload", None)
        if callable(st):
            try:
                sp = st()
                backend = sp.get("backend")
                available = bool(sp.get("ok", True)) and str(backend) != "missing"
                cams = sp.get("camera_agent_ids")
                if isinstance(cams, list) and cams:
                    cam_ids = [str(x).strip() for x in cams if str(x).strip()]
                if "telemetry_enabled" in sp:
                    telemetry_enabled = bool(sp.get("telemetry_enabled"))
                if sp.get("telemetry_hz") is not None:
                    telemetry_hz = float(sp.get("telemetry_hz"))
                if sp.get("telemetry_data_channel"):
                    telemetry_dc = str(sp.get("telemetry_data_channel"))
                tels = sp.get("telemetry_agent_ids")
                if isinstance(tels, list) and tels:
                    sensor_ids = [str(x).strip() for x in tels if str(x).strip()]
            except Exception:  # noqa: BLE001
                pass
        if not cam_ids and agents:
            for aid, ag in agents.items():
                if _is_camera_kind(_agent_kind(ag)):
                    cam_ids.append(str(aid))
        if not sensor_ids and agents:
            cam_set = set(cam_ids)
            for aid, ag in agents.items():
                if str(aid) in cam_set or str(aid) == webrtc_id:
                    continue
                if _is_skipped_preview_kind(_agent_kind(ag)):
                    continue
                sensor_ids.append(str(aid))

    if webrtc_id is None and agent_summaries:
        return degrade_catalog_from_agents_summary(
            station_id=station_id,
            role=role,
            advertise_ip=advertise_ip,
            viz_port=viz_port,
            agents=agent_summaries,
        )

    base, note = _viz_base(advertise_ip, viz_port)
    streams: list[dict[str, Any]] = []
    for cid in cam_ids:
        sig = f"{base}/api/webrtc/preview/offer" if base else ""
        streams.append(
            {
                "stream_id": f"{station_id}/{cid}",
                "station_id": station_id,
                "agent_id": cid,
                "kind": "camera",
                "media": "video",
                "label": cid,
                "signaling_url": sig,
                "offer_body_hint": {"camera_agent_id": cid},
                "enabled": bool(available and sig),
            }
        )
    if telemetry_enabled:
        sensor_rows: list[tuple[str, str]] = []
        for sid in sensor_ids:
            kind = "sensor"
            if agents and sid in agents:
                kind = _agent_kind(agents[sid]) or "sensor"
            sensor_rows.append((sid, kind))
        streams.extend(
            _sensor_stream_rows(
                station_id=station_id,
                base=base,
                sensor_rows=sensor_rows,
                enabled=bool(available),
            )
        )

    payload = {
        "schema": SCHEMA_STATION,
        "station_id": station_id,
        "role": role,
        "advertise_ip": (advertise_ip or "").strip(),
        "viz_port": int(viz_port),
        "viz_base": base,
        "viz_base_note": note,
        "webrtc_signaling": {
            "offer_path": "/api/webrtc/preview/offer",
            "status_path": "/api/webrtc/preview/status",
        },
        "streams": streams,
        "webrtc_agent": {
            "id": webrtc_id,
            "available": available,
            "backend": backend,
            "camera_agent_ids": list(cam_ids),
            "telemetry_enabled": telemetry_enabled,
            "telemetry_hz": telemetry_hz,
            "telemetry_data_channel": telemetry_dc,
            "telemetry_agent_ids": list(sensor_ids) if telemetry_enabled else [],
        },
        "degraded": False,
    }
    payload["config_rev"] = _stable_hash(
        {
            "station_id": station_id,
            "role": role,
            "advertise_ip": advertise_ip,
            "viz_port": viz_port,
            "webrtc_id": webrtc_id,
            "cams": cam_ids,
            "sensors": sensor_ids,
            "backend": backend,
            "telemetry_enabled": telemetry_enabled,
            "telemetry_hz": telemetry_hz,
        }
    )
    return payload


def merge_preview_catalogs(
    local: Mapping[str, Any],
    slave_catalogs: Iterable[Mapping[str, Any] | None],
    *,
    master_station_id: str | None = None,
) -> dict[str, Any]:
    """Merge local + connected slave catalogs into a flat stream list."""
    stations: list[dict[str, Any]] = []
    streams: list[dict[str, Any]] = []
    seen_stream: dict[str, str] = {}
    errors: list[str] = []

    def _ingest(cat: Mapping[str, Any] | None) -> None:
        if not cat or not isinstance(cat, Mapping):
            return
        sid = str(cat.get("station_id") or "").strip() or "?"
        stations.append(dict(cat))
        for s in cat.get("streams") or []:
            if not isinstance(s, Mapping):
                continue
            stream = dict(s)
            sid_s = str(stream.get("stream_id") or "").strip()
            if not sid_s:
                aid = str(stream.get("agent_id") or "").strip() or "cam"
                sid_s = f"{sid}/{aid}"
                stream["stream_id"] = sid_s
            prev = seen_stream.get(sid_s)
            if prev is not None and prev != sid:
                errors.append(f"stream_id conflict {sid_s!r} ({prev} vs {sid})")
                stream = dict(stream)
                stream["error"] = "stream_id_conflict"
                stream["stream_id"] = f"{sid}/{stream.get('agent_id') or 'cam'}"
                sid_s = stream["stream_id"]
            seen_stream[sid_s] = sid
            streams.append(stream)

    _ingest(local)
    for sc in slave_catalogs:
        _ingest(sc)

    mid = (
        master_station_id
        or str((local or {}).get("station_id") or "").strip()
        or "master"
    )
    rev = _stable_hash(
        {
            "stations": [s.get("station_id") for s in stations],
            "revs": [s.get("config_rev") for s in stations],
            "stream_ids": [s.get("stream_id") for s in streams],
        }
    )
    out: dict[str, Any] = {
        "schema": SCHEMA_MERGED,
        "master_station_id": mid,
        "generated_at": time.time(),
        "stations": stations,
        "streams": streams,
        "rev": rev,
    }
    if errors:
        out["errors"] = errors
    return out


def normalize_hello_preview_catalog(
    raw: Any,
    *,
    station_id: str,
    role: str,
    advertise_ip: str,
    viz_port: int | None,
    agents: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Accept hello.preview_catalog or degrade from agents."""
    port = int(viz_port) if viz_port is not None else 7011
    if isinstance(raw, Mapping) and raw.get("schema"):
        cat = dict(raw)
        cat.setdefault("station_id", station_id)
        cat.setdefault("role", role)
        cat.setdefault("advertise_ip", advertise_ip)
        if "viz_port" not in cat:
            cat["viz_port"] = port
        # Refresh signaling URLs if base missing but advertise present.
        if not cat.get("viz_base"):
            base, note = _viz_base(str(cat.get("advertise_ip") or advertise_ip), int(cat["viz_port"]))
            cat["viz_base"] = base
            cat["viz_base_note"] = note
        return cat
    return degrade_catalog_from_agents_summary(
        station_id=station_id,
        role=role,
        advertise_ip=advertise_ip,
        viz_port=port,
        agents=agents,
    )
