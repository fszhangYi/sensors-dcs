from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

PREVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sensors-dcs · Gello</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --accent: #3d9a8b;
      --line: #2a3a4f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1c2b3a 0%, var(--bg) 55%);
      color: var(--text);
      min-height: 100vh;
    }
    header {
      padding: 1.25rem 1.5rem 0.5rem;
      border-bottom: 1px solid var(--line);
    }
    header h1 {
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: 0.02em;
      font-weight: 600;
    }
    header p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
    main { padding: 1rem 1.5rem 2rem; display: grid; gap: 1rem; }
    .meta {
      display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem;
      color: var(--muted); font-size: 0.85rem;
    }
    .meta strong { color: var(--accent); font-weight: 600; }
    .bars {
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 1rem;
      display: grid;
      gap: 0.55rem;
    }
    .row { display: grid; grid-template-columns: 4.5rem 1fr 5rem; gap: 0.75rem; align-items: center; }
    .row span { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 0.85rem; }
    .track {
      height: 14px; background: #0b1017; border-radius: 999px; overflow: hidden;
      border: 1px solid var(--line);
    }
    .fill {
      height: 100%; width: 50%;
      background: linear-gradient(90deg, #2f6f66, var(--accent));
      transform-origin: left center;
    }
    pre {
      margin: 0; padding: 1rem; overflow: auto;
      background: #0b1017; border: 1px solid var(--line); border-radius: 10px;
      font-size: 0.78rem; line-height: 1.45; color: #c5d0e0;
      max-height: 280px;
    }
  </style>
</head>
<body>
  <header>
    <h1>sensors-dcs · Gello Agent</h1>
    <p>低频整帧预览（关节角）。连接 <code>/ws</code>。</p>
  </header>
  <main>
    <div class="meta">
      <div>状态：<strong id="status">connecting…</strong></div>
      <div>seq：<strong id="seq">—</strong></div>
      <div>hz≈：<strong id="hz">—</strong></div>
      <div>dry_run：<strong id="dry">—</strong></div>
    </div>
    <div class="bars" id="bars"></div>
    <pre id="raw">{}</pre>
  </main>
  <script>
    const bars = document.getElementById('bars');
    const statusEl = document.getElementById('status');
    const seqEl = document.getElementById('seq');
    const hzEl = document.getElementById('hz');
    const dryEl = document.getElementById('dry');
    const rawEl = document.getElementById('raw');
    let lastT = null, emaHz = null;

    function ensureRows(n) {
      while (bars.children.length < n) {
        const i = bars.children.length;
        const row = document.createElement('div');
        row.className = 'row';
        row.innerHTML = `<span>j${i}</span><div class="track"><div class="fill" id="f${i}"></div></div><span id="v${i}">0.000</span>`;
        bars.appendChild(row);
      }
    }

    function render(msg) {
      const frame = msg.frames && msg.frames[0];
      if (!frame) return;
      const joints = (frame.payload && frame.payload.joints_rad) || [];
      ensureRows(joints.length);
      joints.forEach((rad, i) => {
        const norm = Math.max(0, Math.min(1, (rad + 1.2) / 2.4));
        const fill = document.getElementById('f' + i);
        const val = document.getElementById('v' + i);
        if (fill) fill.style.width = (norm * 100).toFixed(1) + '%';
        if (val) val.textContent = Number(rad).toFixed(3);
      });
      seqEl.textContent = String(frame.seq);
      dryEl.textContent = String(!!(frame.payload && frame.payload.dry_run));
      if (lastT != null) {
        const dt = frame.t_wall - lastT;
        if (dt > 1e-4) {
          const inst = 1 / dt;
          emaHz = emaHz == null ? inst : emaHz * 0.8 + inst * 0.2;
          hzEl.textContent = emaHz.toFixed(1);
        }
      }
      lastT = frame.t_wall;
      rawEl.textContent = JSON.stringify(msg, null, 2);
    }

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { statusEl.textContent = 'live'; };
      ws.onclose = () => {
        statusEl.textContent = 'reconnecting…';
        setTimeout(connect, 800);
      };
      ws.onerror = () => { statusEl.textContent = 'error'; };
      ws.onmessage = (ev) => {
        try { render(JSON.parse(ev.data)); } catch (e) {}
      };
    }
    connect();
  </script>
</body>
</html>
"""


class VizHub:
    """Broadcast latest viz payloads to WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._clients.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        with self._lock:
            self._clients.discard(ws)

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)


def create_viz_app(hub: VizHub, status_fn: Callable[[], dict[str, Any]]) -> FastAPI:
    app = FastAPI(title="sensors-dcs viz", version="0.1.0")

    @app.on_event("startup")
    async def _startup() -> None:
        hub.bind_loop(asyncio.get_running_loop())

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PREVIEW_HTML

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return status_fn()

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hub.register(ws)
        try:
            while True:
                # keep alive; client messages ignored
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.unregister(ws)
        except Exception:  # noqa: BLE001
            hub.unregister(ws)

    return app
