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
  <title>sensors-dcs · Agents</title>
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
    .actions {
      display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center;
    }
    .actions button {
      appearance: none;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      font: inherit;
      font-size: 0.9rem;
      padding: 0.45rem 1.1rem;
      border-radius: 8px;
      cursor: pointer;
    }
    .actions button:hover { border-color: var(--accent); }
    .actions button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .actions button.primary {
      background: color-mix(in srgb, var(--accent) 28%, var(--panel));
      border-color: var(--accent);
    }
    .actions .hint { color: var(--muted); font-size: 0.85rem; }
    .agent-card {
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 1rem;
      display: grid;
      gap: 0.65rem;
    }
    .agent-card h2 {
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
    }
    .agent-meta {
      display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
      color: var(--muted); font-size: 0.8rem;
    }
    .agent-meta strong { color: var(--accent); font-weight: 600; }
    .agent-bars { display: grid; gap: 0.55rem; }
    #agents { display: grid; gap: 1rem; }
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
    <h1>sensors-dcs · Agents</h1>
    <p>低频整帧预览。连接 <code>/ws</code>。点「开始」刷新，「结束」冻结。</p>
  </header>
  <main>
    <div class="actions">
      <button type="button" class="primary" id="btnStart">开始</button>
      <button type="button" id="btnStop" disabled>结束</button>
      <span class="hint" id="runHint">预览已暂停 — 点「开始」刷新画面</span>
    </div>
    <div class="meta">
      <div>状态：<strong id="status">connecting…</strong></div>
      <div>预览：<strong id="preview">paused</strong></div>
      <div>前端 hz：<strong id="hzFront">—</strong></div>
    </div>
    <div id="agents"></div>
    <pre id="raw">{}</pre>
  </main>
  <script>
    const agentsEl = document.getElementById('agents');
    const statusEl = document.getElementById('status');
    const previewEl = document.getElementById('preview');
    const hzFrontEl = document.getElementById('hzFront');
    const rawEl = document.getElementById('raw');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const runHint = document.getElementById('runHint');
    let updating = false;
    let lastMsgT = null, emaFront = null;
    const backState = {}; // agent_id -> {lastSeq, lastT, ema}

    function setUpdating(on) {
      updating = !!on;
      btnStart.disabled = updating;
      btnStop.disabled = !updating;
      previewEl.textContent = updating ? 'running' : 'paused';
      runHint.textContent = updating
        ? '预览刷新中 — 点「结束」冻结画面'
        : '预览已暂停 — 点「开始」刷新画面';
      if (updating) {
        lastMsgT = null;
        emaFront = null;
        for (const k of Object.keys(backState)) delete backState[k];
      }
    }

    btnStart.addEventListener('click', () => setUpdating(true));
    btnStop.addEventListener('click', () => setUpdating(false));

    function fmtRate(meas, target) {
      const m = meas == null ? '—' : meas.toFixed(1);
      const t = target == null ? '—' : Number(target).toFixed(0);
      return m + ' / 目标 ' + t;
    }

    function ensureCard(agentId, kind) {
      let card = document.getElementById('card-' + agentId);
      if (card) return card;
      card = document.createElement('section');
      card.className = 'agent-card';
      card.id = 'card-' + agentId;
      card.innerHTML =
        '<h2></h2>' +
        '<div class="agent-meta">' +
        '<div>kind：<strong class="k-kind"></strong></div>' +
        '<div>seq：<strong class="k-seq">—</strong></div>' +
        '<div>后端 hz：<strong class="k-hz">—</strong></div>' +
        '<div>dry_run：<strong class="k-dry">—</strong></div>' +
        '</div>' +
        '<div class="agent-bars"></div>';
      agentsEl.appendChild(card);
      return card;
    }

    function ensureRows(barsRoot, n, labelFn) {
      while (barsRoot.children.length < n) {
        const i = barsRoot.children.length;
        const row = document.createElement('div');
        row.className = 'row';
        row.innerHTML =
          '<span class="lab"></span><div class="track"><div class="fill"></div></div><span class="val">0.000</span>';
        barsRoot.appendChild(row);
      }
      for (let i = 0; i < barsRoot.children.length; i++) {
        const lab = barsRoot.children[i].querySelector('.lab');
        if (lab) lab.textContent = labelFn(i);
      }
    }

    function setBar(row, widthPct, text) {
      const fill = row.querySelector('.fill');
      const val = row.querySelector('.val');
      if (fill) fill.style.width = widthPct.toFixed(1) + '%';
      if (val) val.textContent = text;
    }

    function renderGello(card, frame, hzText) {
      card.querySelector('h2').textContent = frame.agent_id + ' · Gello';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const joints = (frame.payload && frame.payload.joints_rad) || [];
      const barsRoot = card.querySelector('.agent-bars');
      ensureRows(barsRoot, joints.length, (i) => 'j' + i);
      joints.forEach((rad, i) => {
        const norm = Math.max(0, Math.min(1, (rad + 1.2) / 2.4));
        setBar(barsRoot.children[i], norm * 100, Number(rad).toFixed(3));
      });
    }

    function renderGripperRead(card, frame, hzText) {
      card.querySelector('h2').textContent = frame.agent_id + ' · Gripper Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const pos = frame.payload && frame.payload.position_norm;
      const barsRoot = card.querySelector('.agent-bars');
      ensureRows(barsRoot, 1, () => 'pos');
      const p = pos == null ? 0 : Number(pos);
      // DH AG95 norm ≈ 0..0.637
      const width = Math.max(0, Math.min(1, p / 0.637)) * 100;
      setBar(barsRoot.children[0], width, pos == null ? '—' : p.toFixed(3));
    }

    function updateBackHz(agentId, frame, target) {
      let st = backState[agentId];
      if (!st) {
        st = { lastSeq: null, lastT: null, ema: null };
        backState[agentId] = st;
      }
      if (st.lastSeq != null && st.lastT != null) {
        const dSeq = frame.seq - st.lastSeq;
        const dT = frame.t_wall - st.lastT;
        if (dSeq > 0 && dT > 1e-4) {
          const inst = dSeq / dT;
          st.ema = st.ema == null ? inst : st.ema * 0.8 + inst * 0.2;
        }
      }
      st.lastSeq = frame.seq;
      st.lastT = frame.t_wall;
      return fmtRate(st.ema, target);
    }

    function render(msg) {
      if (!updating) return;

      const rates = msg.rates || {};
      const vizTarget = rates.viz_hz_target;
      const agentRates = rates.agents || {};

      if (lastMsgT != null) {
        const dtMsg = msg.t_wall - lastMsgT;
        if (dtMsg > 1e-4) {
          const inst = 1 / dtMsg;
          emaFront = emaFront == null ? inst : emaFront * 0.8 + inst * 0.2;
        }
      }
      lastMsgT = msg.t_wall;
      hzFrontEl.textContent = fmtRate(emaFront, vizTarget);

      const frames = msg.frames || [];
      frames.forEach((frame) => {
        const ar = agentRates[frame.agent_id] || {};
        const hzText = updateBackHz(frame.agent_id, frame, ar.hz_target);
        const card = ensureCard(frame.agent_id, frame.kind);
        if (frame.kind === 'gripper_read') {
          renderGripperRead(card, frame, hzText);
        } else {
          renderGello(card, frame, hzText);
        }
      });

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
    setUpdating(false);
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
