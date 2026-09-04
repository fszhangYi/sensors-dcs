"""Server-side sensors-view embed URL normalize + reachability ping."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse, urlunparse

DEFAULT_SENSORS_EMBED_URL = (
    "https://uu658526-m86b-7fdc269f.weste.seetacloud.com:8443/"
)


def normalize_sensors_embed_url(raw: str, *, fallback_default: bool = False) -> str:
    text = (raw or "").strip()
    if not text:
        if fallback_default:
            return DEFAULT_SENSORS_EMBED_URL
        raise ValueError("url required")
    if not re.match(r"^https?://", text, flags=re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        if fallback_default:
            return DEFAULT_SENSORS_EMBED_URL
        raise ValueError("url must be http(s) with a host")
    path = parsed.path or "/"
    if path != "/" and not path.endswith("/"):
        path = path + "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _http_get(url: str, timeout: float = 6.0) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,*/*",
        "User-Agent": "sensors-dcs-sensors-view-ping/1.0",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"raw": body}
            return {"ok": True, "status": int(resp.status), "data": parsed}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
        except json.JSONDecodeError:
            parsed = {"raw": err_body}
        return {
            "ok": False,
            "status": int(e.code),
            "error": parsed,
            "message": str(e),
        }
    except Exception as e:
        return {"ok": False, "status": 0, "message": str(e)}


def ping_sensors_view(raw_url: str, timeout: float = 6.0) -> dict[str, Any]:
    """Probe sensors-view base URL: /api/health then SPA root."""
    try:
        base = normalize_sensors_embed_url(raw_url)
    except ValueError as e:
        return {"ok": False, "status": 0, "message": str(e)}

    candidates: list[str] = []
    base_trimmed = base.rstrip("/")
    candidates.append(f"{base_trimmed}/api/health")
    candidates.append(base if base.endswith("/") else f"{base}/")

    last: dict[str, Any] = {
        "ok": False,
        "status": 0,
        "message": "no probe attempted",
        "url": base,
    }
    for probe in candidates:
        result = _http_get(probe, timeout=timeout)
        last = {
            "ok": bool(result.get("ok")),
            "status": int(result.get("status") or 0),
            "url": base,
            "probed": probe,
            "message": result.get("message")
            or ("ok" if result.get("ok") else "unreachable"),
        }
        if result.get("ok"):
            last["message"] = f"reachable · HTTP {last['status']}"
            return last
        if int(result.get("status") or 0) in (200, 301, 302, 303, 307, 308, 401, 403):
            last["ok"] = True
            last["message"] = f"reachable · HTTP {last['status']}"
            return last
    return last
