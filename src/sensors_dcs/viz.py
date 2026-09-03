from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from pydantic import BaseModel


class SaveDirBody(BaseModel):
    save_dir: str | None = None


class GripperCommandBody(BaseModel):
    agent_id: str | None = None
    position_norm: float | None = None
    position_raw: int | None = None
    initialize: bool = False


class GripperGelloSyncBody(BaseModel):
    enabled: bool
    gello_agent_id: str | None = None
    gripper_agent_id: str | None = None
    joint_index: int = 6
    hz: float | None = None


class ArmCommandBody(BaseModel):
    agent_id: str | None = None
    arm: bool = False
    disarm: bool = False
    stop: bool = False
    joints_rad: list[float] | None = None
    jog_joint: int | None = None
    delta_rad: float | None = None
    delta_deg: float | None = None


class GelloArmSyncBody(BaseModel):
    enabled: bool
    gello_agent_id: str | None = None
    arm_agent_id: str | None = None
    arm_write_agent_id: str | None = None


class GelloArmTeleopBody(BaseModel):
    enabled: bool
    gello_agent_id: str | None = None
    arm_agent_id: str | None = None
    arm_write_agent_id: str | None = None


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
    html, body {
      height: 100%;
      overflow: hidden;
    }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1c2b3a 0%, var(--bg) 55%);
      color: var(--text);
      min-height: 100vh;
      max-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      flex-shrink: 0;
      padding: 0.85rem 1.25rem 0.45rem;
      border-bottom: 1px solid var(--line);
    }
    header h1 {
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: 0.02em;
      font-weight: 600;
    }
    header p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.85rem; }
    main {
      flex: 1;
      min-height: 0;
      padding: 0.75rem 1.25rem 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
      overflow: hidden;
    }
    .meta {
      flex-shrink: 0;
      display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem;
      color: var(--muted); font-size: 0.85rem;
    }
    .meta strong { color: var(--accent); font-weight: 600; }
    .actions {
      flex-shrink: 0;
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
    .save-path {
      flex-shrink: 0;
      display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
      font-size: 0.85rem;
    }
    .save-path label { color: var(--muted); }
    .save-path input {
      flex: 1 1 12rem;
      min-width: 8rem;
      max-width: 28rem;
      appearance: none;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      font: inherit;
      padding: 0.4rem 0.65rem;
      border-radius: 8px;
    }
    .save-path input:disabled { opacity: 0.45; }
    .agent-card {
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.75rem;
      display: grid;
      gap: 0.5rem;
      flex-shrink: 0;
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
    #status.st-live { color: #3d9a8b; }
    #status.st-connecting { color: #8b9bb4; }
    #status.st-reconnecting { color: #e8a838; }
    #status.st-error { color: #e07070; }
    #status.st-offline { color: #e07070; }
    button.danger {
      background: color-mix(in srgb, #c44 32%, #0b1017); border-color: #a33;
    }
    .agent-vals {
      display: flex; flex-wrap: wrap; gap: 0.35rem 0.55rem;
      font-variant-numeric: tabular-nums; font-size: 0.8rem;
    }
    .agent-vals .jv {
      background: #0b1017; border: 1px solid var(--line); border-radius: 6px;
      padding: 0.2rem 0.45rem; color: var(--muted);
    }
    .agent-vals .jv b { color: var(--text); font-weight: 600; }
    .agent-vals .jv .cal { color: var(--accent); }
    .agent-vals .jv .raw { color: #e8a838; }
    .agent-bars { display: none; }
    .content-row {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
      align-items: stretch;
    }
    #agents {
      min-width: 0;
      min-height: 0;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
      align-items: stretch;
    }
    .row { display: grid; grid-template-columns: 4.5rem 1fr 5.5rem; gap: 0.75rem; align-items: center; }
    .row span { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 0.85rem; }
    .track {
      height: 14px; background: #0b1017; border-radius: 999px; overflow: hidden;
      border: 1px solid var(--line);
    }
    .track.track-dual {
      height: auto;
      display: grid;
      gap: 3px;
      padding: 2px 0;
      background: transparent;
      border: none;
      overflow: visible;
      border-radius: 0;
    }
    .track.track-dual .track-lane {
      height: 7px;
      background: #0b1017;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--line);
    }
    .fill {
      height: 100%; width: 50%;
      background: linear-gradient(90deg, #2f6f66, var(--accent));
      transform-origin: left center;
    }
    .fill.fill-cal {
      background: linear-gradient(90deg, #2f6f66, var(--accent));
    }
    .fill.fill-raw {
      background: linear-gradient(90deg, #b8791f, #e8a838);
    }
    .row .val .v-cal { color: var(--accent); }
    .row .val .v-raw { color: #e8a838; }
    .grip-cmd {
      display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center;
      margin-top: 0.55rem; font-size: 0.82rem;
    }
    .grip-cmd input {
      width: 7rem; padding: 0.35rem 0.5rem; border-radius: 8px;
      border: 1px solid var(--line); background: #0b1017; color: var(--text);
    }
    .grip-cmd button {
      padding: 0.35rem 0.7rem; border-radius: 8px; border: 1px solid var(--line);
      background: color-mix(in srgb, var(--accent) 28%, #0b1017); color: var(--text);
      cursor: pointer;
    }
    .grip-cmd .cmd-hint { color: var(--muted); font-size: 0.75rem; }
    .arm-cmd {
      display: grid; gap: 0.45rem; margin-top: 0.55rem; font-size: 0.82rem;
    }
    .arm-cmd .arm-tools {
      display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center;
    }
    .arm-cmd input[type="range"] { width: 10rem; accent-color: var(--accent); }
    .arm-cmd button {
      padding: 0.3rem 0.65rem; border-radius: 8px; border: 1px solid var(--line);
      background: color-mix(in srgb, var(--accent) 28%, #0b1017); color: var(--text);
      cursor: pointer;
    }
    .arm-cmd button.arm-estop {
      background: color-mix(in srgb, #c44 35%, #0b1017); border-color: #a33;
    }
    .arm-cmd button:disabled { opacity: 0.4; cursor: not-allowed; }
    .arm-cmd .arm-jog-row {
      display: grid; grid-template-columns: 2.8rem auto auto 1fr; gap: 0.4rem; align-items: center;
    }
    .arm-cmd .arm-jog-row button { min-width: 2.2rem; }
    .arm-cmd .cmd-hint { color: var(--muted); font-size: 0.75rem; }
    .arm-cmd .arm-sync-prog {
      color: var(--muted); font-size: 0.78rem; min-height: 1.1em;
    }
    .arm-cmd .arm-teleop-prog {
      color: var(--muted); font-size: 0.78rem; min-height: 1.1em;
    }
    .arm-cmd button.arm-sync-on {
      background: color-mix(in srgb, #c90 32%, #0b1017); border-color: #a70;
    }
    .arm-cmd button.arm-teleop-on {
      background: color-mix(in srgb, #3d9a8b 40%, #0b1017); border-color: var(--accent);
    }
    .modal-backdrop {
      display: none; position: fixed; inset: 0; z-index: 50;
      background: rgba(0,0,0,0.55); align-items: center; justify-content: center;
    }
    .modal-backdrop.show { display: flex; }
    .modal-card {
      max-width: 28rem; margin: 1rem; padding: 1rem 1.15rem; border-radius: 12px;
      background: var(--panel); border: 1px solid var(--line); color: var(--text);
      box-shadow: 0 12px 40px rgba(0,0,0,0.45);
    }
    .modal-card h3 { margin: 0 0 0.5rem; font-size: 1rem; }
    .modal-card p { margin: 0 0 0.85rem; color: var(--muted); font-size: 0.88rem; line-height: 1.45; white-space: pre-wrap; }
    .modal-card button {
      padding: 0.35rem 0.85rem; border-radius: 8px; border: 1px solid var(--line);
      background: color-mix(in srgb, var(--accent) 28%, #0b1017); color: var(--text); cursor: pointer;
    }
    .cam-section {
      min-width: 0;
      min-height: 0;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .cam-section h2 {
      margin: 0 0 0.45rem;
      font-size: 1rem;
      font-weight: 600;
      flex-shrink: 0;
    }
    #cam-grid {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 0.5rem;
      width: 100%;
    }
    .cam-cell {
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.45rem;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 0.3rem;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
    }
    .cam-cell .cam-title {
      font-size: 0.82rem;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .cam-cell .cam-title strong { color: var(--accent); font-weight: 600; }
    .cam-cell .cam-title .k-hz { color: #7dcea0; font-variant-numeric: tabular-nums; }
    .cam-cell img {
      width: 100%;
      height: 100%;
      max-height: 100%;
      aspect-ratio: auto;
      object-fit: contain;
      background: #0b1017;
      border-radius: 8px;
      border: 1px solid var(--line);
      display: block;
    }
    .cam-cell.empty img { opacity: 0.25; }
    .cam-cell .cam-sub { font-size: 0.75rem; color: var(--muted); }
    pre#raw {
      flex-shrink: 0;
      width: 100%;
      margin: 0;
      padding: 0.65rem 0.85rem;
      overflow: auto;
      background: #0b1017;
      border: 1px solid var(--line);
      border-radius: 10px;
      font-size: 0.72rem;
      line-height: 1.4;
      color: #c5d0e0;
      max-height: 22vh;
      min-height: 4.5rem;
    }
  </style>
</head>
<body>
  <header>
    <h1>sensors-dcs · Agents</h1>
    <p>低频整帧预览。连接 <code>/ws</code>。「开始/结束」控制录制流水线写盘。相机固定四宫格；状态卡不含相机预览。</p>
  </header>
  <main>
    <div class="actions">
      <button type="button" class="primary" id="btnStart">开始</button>
      <button type="button" id="btnStop" disabled>结束</button>
      <button type="button" class="danger" id="btnExit">安全退出</button>
      <span class="hint" id="runHint">空闲 — 点「开始」录制当前 episode</span>
    </div>
    <div class="save-path">
      <label for="saveDirInput">保存路径</label>
      <input type="text" id="saveDirInput" placeholder="留空则沿用当前路径" />
      <button type="button" id="btnSaveDir">应用</button>
    </div>
    <div class="meta">
      <div>连接：<strong id="status" class="st-connecting">connecting…</strong></div>
      <div>录制：<strong id="recState">idle</strong></div>
      <div>保存路径：<strong id="saveDir">—</strong></div>
      <div>episode：<strong id="episode">—</strong></div>
      <div>前端 hz：<strong id="hzFront">—</strong></div>
      <div>已写帧：<strong id="written">0</strong></div>
    </div>
    <div class="content-row">
      <section class="cam-section">
        <h2>Camera preview · 2×2</h2>
        <div id="cam-grid"></div>
      </section>
      <div id="agents"></div>
    </div>
    <pre id="raw">{}</pre>
  </main>
  <div class="modal-backdrop" id="appModal" role="dialog" aria-modal="true">
    <div class="modal-card">
      <h3 id="appModalTitle">提示</h3>
      <p id="appModalBody"></p>
      <button type="button" id="appModalOk">知道了</button>
    </div>
  </div>
  <script>
    const agentsEl = document.getElementById('agents');
    const camGridEl = document.getElementById('cam-grid');
    const statusEl = document.getElementById('status');
    function setConnStatus(text, cls) {
      statusEl.textContent = text;
      statusEl.className = cls || '';
    }
    const appModal = document.getElementById('appModal');
    const appModalTitle = document.getElementById('appModalTitle');
    const appModalBody = document.getElementById('appModalBody');
    const appModalOk = document.getElementById('appModalOk');
    function showAppModal(title, body) {
      appModalTitle.textContent = title || '提示';
      appModalBody.textContent = body || '';
      appModal.classList.add('show');
    }
    appModalOk.addEventListener('click', () => appModal.classList.remove('show'));
    appModal.addEventListener('click', (e) => {
      if (e.target === appModal) appModal.classList.remove('show');
    });
    const recStateEl = document.getElementById('recState');
    const saveDirEl = document.getElementById('saveDir');
    const episodeEl = document.getElementById('episode');
    const writtenEl = document.getElementById('written');
    const hzFrontEl = document.getElementById('hzFront');
    const rawEl = document.getElementById('raw');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const btnSaveDir = document.getElementById('btnSaveDir');
    const saveDirInput = document.getElementById('saveDirInput');
    const runHint = document.getElementById('runHint');
    let lastMsgT = null, emaFront = null;
    let busy = false;
    const backState = {};
    const CAM_SLOTS = [
      { key: 'left', label: 'Left' },
      { key: 'right', label: 'Right' },
      { key: 'middle', label: 'Middle' },
      { key: 'wrist', label: 'Wrist' },
    ];
    const camCells = {};

    function initCamGrid() {
      camGridEl.innerHTML = '';
      CAM_SLOTS.forEach((slot) => {
        const cell = document.createElement('div');
        cell.className = 'cam-cell empty';
        cell.id = 'cam-slot-' + slot.key;
        cell.innerHTML =
          '<div class="cam-title">' +
            '<span>' + slot.label + '</span>' +
            '<strong class="k-agent">—</strong>' +
            '<strong class="k-hz">— Hz</strong>' +
          '</div>' +
          '<img alt="' + slot.label + '" />' +
          '<div class="cam-sub k-sub">empty</div>';
        camGridEl.appendChild(cell);
        camCells[slot.key] = cell;
      });
    }
    initCamGrid();

    function applyRecordUi(rec) {
      if (!rec) return;
      const st = rec.state || 'idle';
      recStateEl.textContent = st;
      saveDirEl.textContent = rec.save_dir || '—';
      if (document.activeElement !== saveDirInput) {
        saveDirInput.placeholder = rec.save_dir || '留空则沿用当前路径';
      }
      episodeEl.textContent = (rec.episode_index == null) ? '—' : String(rec.episode_index);
      writtenEl.textContent = String(rec.written == null ? 0 : rec.written);
      const recBusy = st === 'recording' || st === 'flushing';
      saveDirInput.disabled = recBusy;
      btnSaveDir.disabled = recBusy;
      if (busy) return;
      if (st === 'recording') {
        btnStart.disabled = true;
        btnStop.disabled = false;
        runHint.textContent = '录制中 — 点「结束」停止流入并落盘';
      } else if (st === 'flushing') {
        btnStart.disabled = true;
        btnStop.disabled = true;
        runHint.textContent = '落盘中 — 完成前不可开始下一集';
      } else {
        btnStart.disabled = false;
        btnStop.disabled = true;
        runHint.textContent = '空闲 — 点「开始」录制 episode ' + episodeEl.textContent;
      }
    }

    async function postRecord(path) {
      busy = true;
      btnStart.disabled = true;
      btnStop.disabled = true;
      runHint.textContent = path.indexOf('stop') >= 0 ? '正在停止并落盘…' : '正在开始录制…';
      try {
        const r = await fetch(path, { method: 'POST' });
        const j = await r.json();
        applyRecordUi(j);
        if (!j.ok && j.error) runHint.textContent = j.error;
      } catch (e) {
        runHint.textContent = String(e);
      } finally {
        busy = false;
        // refresh authoritative status
        try {
          const s = await fetch('/api/record/status').then((x) => x.json());
          applyRecordUi(s);
        } catch (e) {}
      }
    }

    btnStart.addEventListener('click', () => postRecord('/api/record/start'));
    btnStop.addEventListener('click', () => postRecord('/api/record/stop'));
    const btnExit = document.getElementById('btnExit');
    if (btnExit) {
      btnExit.addEventListener('click', async () => {
        if (!confirm('安全退出：停止录制、关闭传感器并结束进程？')) return;
        btnExit.disabled = true;
        runHint.textContent = '正在安全退出…';
        setConnStatus('shutting down…', 'st-offline');
        try {
          const r = await fetch('/api/shutdown', { method: 'POST' }).then((x) => x.json());
          runHint.textContent = r.ok ? '已请求退出，可关闭页面' : ('退出失败：' + (r.error || JSON.stringify(r)));
        } catch (e) {
          runHint.textContent = '退出请求已发送（连接可能已断开）';
        }
      });
    }

    async function applySaveDir() {
      const path = saveDirInput.value.trim();
      btnSaveDir.disabled = true;
      runHint.textContent = path ? '正在更新保存路径…' : '正在刷新保存路径…';
      try {
        const r = await fetch('/api/record/save_dir', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ save_dir: path || null }),
        });
        const j = await r.json();
        applyRecordUi(j);
        if (j.ok) {
          saveDirInput.value = '';
          runHint.textContent = '保存路径已更新 — 点「开始」录制 episode ' + episodeEl.textContent;
        } else if (j.error) {
          runHint.textContent = j.error;
        }
      } catch (e) {
        runHint.textContent = String(e);
      } finally {
        btnSaveDir.disabled = false;
      }
    }
    btnSaveDir.addEventListener('click', () => applySaveDir());
    saveDirInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') applySaveDir();
    });

    function fmtRate(meas, target) {
      const m = meas == null ? '—' : meas.toFixed(1);
      const t = target == null ? '—' : Number(target).toFixed(0);
      return m + ' / 目标 ' + t;
    }

    function ensureStateCard(agentId) {
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

    function fmtRad(v) {
      if (v == null || !Number.isFinite(Number(v))) return '—';
      const r = Number(v);
      return r.toFixed(3) + ' rad / ' + (r * 180 / Math.PI).toFixed(1) + '°';
    }

    function ensureValsRoot(card) {
      let root = card.querySelector('.agent-vals');
      if (!root) {
        root = document.createElement('div');
        root.className = 'agent-vals';
        const bars = card.querySelector('.agent-bars');
        if (bars) bars.insertAdjacentElement('afterend', root);
        else card.appendChild(root);
      }
      return root;
    }

    function renderJointVals(root, items) {
      // items: [{lab, cal, raw}]
      let html = '';
      for (const it of items) {
        const lab = it.lab || '';
        if (it.raw != null && it.showRaw) {
          html += '<span class="jv"><b>' + lab + '</b> <span class="cal">' +
            (it.calText != null ? it.calText : fmtRad(it.cal)) +
            '</span> · <span class="raw">' +
            (it.rawText != null ? it.rawText : fmtRad(it.raw)) +
            '</span></span>';
        } else {
          html += '<span class="jv"><b>' + lab + '</b> ' +
            (it.calText != null ? it.calText : fmtRad(it.cal)) + '</span>';
        }
      }
      root.innerHTML = html || '<span class="jv">—</span>';
    }

        function renderArmRead(card, frame, hzText) {
      card.querySelector('h2').textContent = 'robot · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const joints = (frame.payload && frame.payload.joints_rad) || [];
      window.__armReadJoints = joints;
      window.__armReadAgentId = frame.agent_id;
      const root = ensureValsRoot(card);
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      renderJointVals(root, joints.map((v, i) => ({ lab: 'j' + i, cal: v })));
    }

    function renderGello(card, frame, hzText) {
      card.querySelector('h2').textContent = frame.agent_id + ' · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const joints = p.joints_rad || [];
      const jointsRaw = p.joints_rad_raw || [];
      const n = Math.max(joints.length, jointsRaw.length);
      const root = ensureValsRoot(card);
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      const items = [];
      for (let i = 0; i < n; i++) {
        items.push({
          lab: 'j' + i,
          cal: joints[i],
          raw: jointsRaw[i],
          showRaw: true,
          calText: joints[i] == null || !Number.isFinite(Number(joints[i])) ? 'cal —' : ('cal ' + fmtRad(joints[i])),
          rawText: jointsRaw[i] == null || !Number.isFinite(Number(jointsRaw[i])) ? 'raw —' : ('raw ' + fmtRad(jointsRaw[i])),
        });
      }
      renderJointVals(root, items);
    }

    function renderGripperRead(card, frame, hzText) {
      card.querySelector('h2').textContent = 'gripper · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const root = ensureValsRoot(card);
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      const norm = p.position_norm;
      const raw = p.raw_value != null ? p.raw_value : p.position_raw;
      renderJointVals(root, [{
        lab: 'grip',
        showRaw: true,
        calText: norm == null || !Number.isFinite(Number(norm)) ? 'norm —' : ('norm ' + Number(norm).toFixed(3)),
        rawText: raw == null || !Number.isFinite(Number(raw)) ? 'raw —' : ('raw ' + String(Math.round(Number(raw)))),
      }]);
    }

    function renderGripperWrite(card, frame, hzText) {
      card.querySelector('h2').textContent = 'gripper · Write';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const root = ensureValsRoot(card);
      const norm = p.command_position_norm;
      const ok = p.last_ok;
      let calText;
      if (norm == null) calText = p.initialized ? '已初始化' : '未下发';
      else calText = 'cmd ' + Number(norm).toFixed(3) + (ok === false ? ' ✗' : ok ? ' ✓' : '');
      renderJointVals(root, [{ lab: 'cmd', calText: calText }]);
      let box = card.querySelector('.grip-cmd');
      if (!box) {
        box = document.createElement('div');
        box.className = 'grip-cmd';
        box.innerHTML =
          '<button type="button" class="grip-init">初始化</button>' +
          '<button type="button" class="grip-sync">同步</button>' +
          '<label>position_norm</label>' +
          '<input type="number" step="0.01" min="0" max="0.637" value="0.32" class="grip-norm" />' +
          '<button type="button" class="grip-send">下发</button>' +
          '<span class="cmd-hint">同步=服务端 gello j6(cal)→夹爪；前端只开关</span>';
        card.appendChild(box);
        const btn = box.querySelector('.grip-send');
        const initBtn = box.querySelector('.grip-init');
        const syncBtn = box.querySelector('.grip-sync');
        const inp = box.querySelector('.grip-norm');
        const setManualEnabled = (on) => {
          btn.disabled = !on;
          inp.disabled = !on;
        };
        const applySyncUi = (sync) => {
          const on = !!(sync && sync.enabled);
          syncBtn.textContent = on ? '取消同步' : '同步';
          syncBtn.dataset.enabled = on ? '1' : '0';
          setManualEnabled(!on);
          if (on && sync.last_norm != null && Number.isFinite(Number(sync.last_norm))) {
            inp.value = String(Number(sync.last_norm).toFixed(3));
          }
        };
        box._applySyncUi = applySyncUi;
        const postGrip = async (body, busyEl) => {
          busyEl.disabled = true;
          btn.disabled = true;
          initBtn.disabled = true;
          syncBtn.disabled = true;
          try {
            const r = await fetch('/api/gripper/command', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            }).then((x) => x.json());
            return r;
          } finally {
            busyEl.disabled = false;
            initBtn.disabled = false;
            syncBtn.disabled = false;
            applySyncUi(window.__gripGelloSync || { enabled: syncBtn.dataset.enabled === '1' });
          }
        };
        initBtn.addEventListener('click', async () => {
          runHint.textContent = '夹爪初始化中（约数秒，夹爪会动作）…';
          try {
            const r = await postGrip({ agent_id: frame.agent_id, initialize: true }, initBtn);
            runHint.textContent = r.ok
              ? '夹爪初始化成功，可下发 / 同步'
              : ('夹爪初始化失败：' + (r.error || JSON.stringify(r)));
          } catch (e) {
            runHint.textContent = '夹爪初始化异常：' + e;
          }
        });
        syncBtn.addEventListener('click', async () => {
          const want = syncBtn.dataset.enabled !== '1';
          syncBtn.disabled = true;
          try {
            const r = await fetch('/api/gripper/gello-sync', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                enabled: want,
                gripper_agent_id: frame.agent_id,
                joint_index: 6,
              }),
            }).then((x) => x.json());
            if (!r.ok) {
              runHint.textContent = '同步失败：' + (r.error || JSON.stringify(r));
              return;
            }
            window.__gripGelloSync = r;
            applySyncUi(r);
            runHint.textContent = r.enabled
              ? '已同步：服务端 gello j6(cal) → gripper（数据不经前端）'
              : '已取消同步';
          } catch (e) {
            runHint.textContent = '同步异常：' + e;
          } finally {
            syncBtn.disabled = false;
          }
        });
        btn.addEventListener('click', async () => {
          const v = Number(inp.value);
          if (!Number.isFinite(v)) {
            runHint.textContent = '夹爪：请输入有效数字';
            return;
          }
          try {
            const r = await postGrip(
              { agent_id: frame.agent_id, position_norm: v },
              btn,
            );
            runHint.textContent = r.ok
              ? ('夹爪已下发 norm=' + v)
              : ('夹爪下发失败：' + (r.error || JSON.stringify(r)));
          } catch (e) {
            runHint.textContent = '夹爪下发异常：' + e;
          }
        });
      }
      if (box && box._applySyncUi) {
        box._applySyncUi(window.__gripGelloSync || { enabled: false });
      }
    }

    function renderArmWrite(card, frame, hzText) {
      card.querySelector('h2').textContent = 'robot · Write';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const n = Math.max(1, Number(p.num_joints) || 6);
      // Prefer live arm-read joints for display (safety: show measured pose).
      const fb = (window.__armReadJoints && window.__armReadJoints.length)
        ? window.__armReadJoints
        : (p.feedback_joints_rad || p.command_joints_rad || []);
      const root = ensureValsRoot(card);
      const items = [];
      for (let i = 0; i < n; i++) {
        items.push({ lab: 'j' + i, cal: fb[i] });
      }
      if (p.command_joints_rad) {
        items.push({
          lab: 'last_cmd',
          calText: p.last_ok === false ? 'fail' : (p.last_ok ? 'ok' : '—'),
        });
      }
      renderJointVals(root, items);
      let box = card.querySelector('.arm-cmd');
      if (!box) {
        box = document.createElement('div');
        box.className = 'arm-cmd';
        let jogHtml = '';
        for (let i = 0; i < n; i++) {
          jogHtml +=
            '<div class="arm-jog-row" data-j="' + i + '">' +
              '<span>j' + i + '</span>' +
              '<button type="button" class="arm-minus" data-j="' + i + '">−</button>' +
              '<button type="button" class="arm-plus" data-j="' + i + '">+</button>' +
              '<span class="arm-jval">—</span>' +
            '</div>';
        }
        box.innerHTML =
          '<div class="arm-tools">' +
            '<button type="button" class="arm-arm">Arm</button>' +
            '<button type="button" class="arm-disarm">Disarm</button>' +
            '<button type="button" class="arm-estop">Estop</button>' +
            '<button type="button" class="arm-gello-sync">同步</button>' +
            '<button type="button" class="arm-gello-teleop">摇操</button>' +
            '<label>delta°</label>' +
            '<input type="range" class="arm-delta" min="0.1" max="5" step="0.1" value="1.0" />' +
            '<span class="arm-delta-val">1.0°</span>' +
            '<span class="arm-armed-tag">idle</span>' +
          '</div>' +
          '<div class="arm-sync-prog">gello→arm：空闲（完成后 gello 不控臂）</div>' +
          '<div class="arm-teleop-prog">摇操：空闲（gello 不控臂）</div>' +
          jogHtml +
          '<span class="cmd-hint">须先有 robot·Read；Arm →「同步」对齐 →「摇操」跟随；解除后 gello 不再控臂</span>';
        card.appendChild(box);
        const delta = box.querySelector('.arm-delta');
        const deltaVal = box.querySelector('.arm-delta-val');
        const armedTag = box.querySelector('.arm-armed-tag');
        const armBtn = box.querySelector('.arm-arm');
        const disarmBtn = box.querySelector('.arm-disarm');
        const estopBtn = box.querySelector('.arm-estop');
        const syncBtn = box.querySelector('.arm-gello-sync');
        const teleopBtn = box.querySelector('.arm-gello-teleop');
        const syncProg = box.querySelector('.arm-sync-prog');
        const teleopProg = box.querySelector('.arm-teleop-prog');
        const updateDeltaLabel = () => {
          deltaVal.textContent = Number(delta.value).toFixed(1) + '°';
        };
        delta.addEventListener('input', updateDeltaLabel);
        const postArm = async (body) => {
          const r = await fetch('/api/arm/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_id: frame.agent_id, ...body }),
          }).then((x) => x.json());
          return r;
        };
        const setJogEnabled = (on) => {
          box.querySelectorAll('.arm-minus, .arm-plus').forEach((b) => { b.disabled = !on; });
        };
        box._applyArmUi = (armed) => {
          const hasRead = Array.isArray(window.__armReadJoints) && window.__armReadJoints.length > 0;
          const sync = window.__gelloArmSync || {};
          const teleop = window.__gelloArmTeleop || {};
          const syncing = !!sync.enabled;
          const teleoping = !!teleop.enabled;
          armedTag.textContent = !hasRead ? 'need read' : (armed ? 'armed' : 'idle');
          setJogEnabled(!!armed && hasRead && !syncing && !teleoping);
          armBtn.disabled = !hasRead || syncing || teleoping;
          syncBtn.disabled = !hasRead || teleoping;
          syncBtn.textContent = syncing ? '取消同步' : '同步';
          syncBtn.classList.toggle('arm-sync-on', syncing);
          syncBtn.dataset.enabled = syncing ? '1' : '0';
          teleopBtn.disabled = !hasRead || syncing;
          teleopBtn.textContent = teleoping ? '解除摇操' : '摇操';
          teleopBtn.classList.toggle('arm-teleop-on', teleoping);
          teleopBtn.dataset.enabled = teleoping ? '1' : '0';
          if (sync.phase === 'ramping' && sync.ramp_n) {
            const left = Math.max(0, (Number(sync.ramp_n) - Number(sync.ramp_index || 0)) / 5);
            syncProg.textContent =
              'ramping ' + (sync.ramp_index || 0) + '/' + sync.ramp_n +
              ' · round ' + (sync.round || 1) +
              ' · ~' + left.toFixed(1) + 's · 目标已冻结';
          } else if (sync.phase === 'verifying') {
            syncProg.textContent = '校验中（不写臂）…';
          } else if (sync.phase === 'completed') {
            syncProg.textContent = '同步完成；gello 未控制机械臂';
          } else if (sync.phase === 'error' && sync.last_error) {
            syncProg.textContent = '失败：' + sync.last_error;
          } else if (sync.message) {
            syncProg.textContent = String(sync.message);
          } else {
            syncProg.textContent = 'gello→arm：空闲（完成后 gello 不控臂）';
          }
          if (teleop.phase === 'teleop' || teleoping) {
            teleopProg.textContent = teleop.message || (
              teleop.rate_limited
                ? ('限速中 · ' + (teleop.hz || '?') + ' Hz')
                : ('摇操中 · ' + (teleop.hz || '?') + ' Hz · 已写 ' + (teleop.write_count || 0))
            );
          } else if (teleop.phase === 'error' && teleop.last_error) {
            teleopProg.textContent = '失败：' + teleop.last_error;
          } else if (teleop.message) {
            teleopProg.textContent = String(teleop.message);
          } else {
            teleopProg.textContent = '摇操：空闲（gello 不控臂）';
          }
        };
        armBtn.addEventListener('click', async () => {
          runHint.textContent = '机械臂 Arm / TT_init…';
          try {
            const r = await postArm({ arm: true });
            runHint.textContent = r.ok ? 'Arm 成功，可用 ± 点动 / 同步 / 摇操' : ('Arm 失败：' + (r.error || JSON.stringify(r)));
            box._applyArmUi(!!(r.ok && r.armed));
          } catch (e) {
            runHint.textContent = 'Arm 异常：' + e;
          }
        });
        disarmBtn.addEventListener('click', async () => {
          try {
            const r = await postArm({ disarm: true });
            runHint.textContent = r.ok ? '已 Disarm' : ('Disarm 失败：' + (r.error || JSON.stringify(r)));
            box._applyArmUi(false);
          } catch (e) {
            runHint.textContent = 'Disarm 异常：' + e;
          }
        });
        estopBtn.addEventListener('click', async () => {
          try {
            const r = await postArm({ stop: true });
            runHint.textContent = r.ok ? 'Estop/停写已发送' : ('Estop 失败：' + (r.error || JSON.stringify(r)));
            box._applyArmUi(false);
          } catch (e) {
            runHint.textContent = 'Estop 异常：' + e;
          }
        });
        syncBtn.addEventListener('click', async () => {
          const want = syncBtn.dataset.enabled !== '1';
          syncBtn.disabled = true;
          try {
            const r = await fetch('/api/arm/gello-sync', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                enabled: want,
                arm_write_agent_id: frame.agent_id,
              }),
            }).then((x) => x.json());
            window.__gelloArmSync = r;
            if (!r.ok) {
              showAppModal(
                r.gate_failed ? '无法进入同步' : '同步失败',
                r.error || JSON.stringify(r),
              );
              runHint.textContent = '同步失败：' + (r.error || JSON.stringify(r));
              box._applyArmUi(!!p.armed);
              return;
            }
            box._applyArmUi(!!p.armed);
            runHint.textContent = r.enabled
              ? '同步进行中：目标为命令时刻 gello（中途扳 gello 不改路径）'
              : (r.message || '已取消同步；gello 未控制机械臂');
            if (!r.enabled && r.phase === 'completed') {
              showAppModal('同步完成', r.message || '同步完成；gello 已不再控制机械臂');
            }
          } catch (e) {
            runHint.textContent = '同步异常：' + e;
            showAppModal('同步异常', String(e));
          } finally {
            syncBtn.disabled = false;
            box._applyArmUi(!!p.armed);
          }
        });
        teleopBtn.addEventListener('click', async () => {
          const want = teleopBtn.dataset.enabled !== '1';
          teleopBtn.disabled = true;
          try {
            const r = await fetch('/api/arm/gello-teleop', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                enabled: want,
                arm_write_agent_id: frame.agent_id,
              }),
            }).then((x) => x.json());
            window.__gelloArmTeleop = r;
            if (!r.ok) {
              showAppModal(
                r.gate_failed ? '无法进入摇操' : '摇操失败',
                r.error || JSON.stringify(r),
              );
              runHint.textContent = '摇操失败：' + (r.error || JSON.stringify(r));
              box._applyArmUi(!!p.armed);
              return;
            }
            box._applyArmUi(!!p.armed);
            runHint.textContent = r.enabled
              ? (r.message || '摇操已开启')
              : (r.message || '已解除摇操；gello 未控制机械臂');
          } catch (e) {
            runHint.textContent = '摇操异常：' + e;
            showAppModal('摇操异常', String(e));
          } finally {
            teleopBtn.disabled = false;
            box._applyArmUi(!!p.armed);
          }
        });
        const jog = async (jointIndex, sign) => {
          const dDeg = Number(delta.value);
          if (!Number.isFinite(dDeg) || dDeg <= 0) {
            runHint.textContent = 'delta 无效';
            return;
          }
          try {
            const r = await postArm({
              jog_joint: jointIndex,
              delta_deg: sign * dDeg,
            });
            runHint.textContent = r.ok
              ? ('点动 j' + jointIndex + ' ' + (sign > 0 ? '+' : '−') + dDeg.toFixed(1) + '°')
              : ('点动失败：' + (r.error || JSON.stringify(r)));
            if (r.armed === false) box._applyArmUi(false);
          } catch (e) {
            runHint.textContent = '点动异常：' + e;
          }
        };
        box.querySelectorAll('.arm-minus').forEach((b) => {
          b.addEventListener('click', () => jog(Number(b.dataset.j), -1));
        });
        box.querySelectorAll('.arm-plus').forEach((b) => {
          b.addEventListener('click', () => jog(Number(b.dataset.j), +1));
        });
        box._applyArmUi(false);
      }
      if (box && box._applyArmUi) {
        box._applyArmUi(!!p.armed);
        const rows = box.querySelectorAll('.arm-jog-row');
        rows.forEach((row) => {
          const i = Number(row.dataset.j);
          const span = row.querySelector('.arm-jval');
          const v = fb[i];
          if (span) {
            span.textContent = v == null || !Number.isFinite(Number(v))
              ? '—'
              : (Number(v).toFixed(3) + ' rad / ' + (Number(v) * 180 / Math.PI).toFixed(1) + '°');
          }
        });
      }
    }

    function pickCamSlot(frame, used) {
      const role = String((frame.payload && frame.payload.role) || '').toLowerCase();
      const agentHint = String(frame.agent_id || '').toLowerCase();
      const order = CAM_SLOTS.map((s) => s.key);
      for (const key of order) {
        if (used.has(key)) continue;
        if (role === key || agentHint.includes(key)) return key;
      }
      for (const key of order) {
        if (!used.has(key)) return key;
      }
      return null;
    }

    function renderCamSlot(slotKey, frame, hzText) {
      const cell = camCells[slotKey];
      if (!cell) return;
      cell.classList.remove('empty');
      const p = frame.payload || {};
      cell.querySelector('.k-agent').textContent = frame.agent_id;
      const hzEl = cell.querySelector('.k-hz');
      if (hzEl) hzEl.textContent = hzText;
      const img = cell.querySelector('img');
      if (p.jpeg_b64_preview) img.src = 'data:image/jpeg;base64,' + p.jpeg_b64_preview;
      else if (p.jpeg_b64) img.src = 'data:image/jpeg;base64,' + p.jpeg_b64;
      const sn = p.serial || '—';
      const dry = p.dry_run ? ' dry' : '';
      const fps = p.fps != null ? (' · cfg ' + Number(p.fps).toFixed(0) + 'fps') : '';
      cell.querySelector('.k-sub').textContent =
        'seq ' + frame.seq + ' · sn ' + sn + fps + dry;
    }

    function updateBackHz(agentId, frame, target, measFromBackend, nowT) {
      let st = backState[agentId];
      if (!st) {
        st = { lastSeq: null, lastT: null, ema: null };
        backState[agentId] = st;
      }
      // Prefer agent-loop measured rate from backend (stable even if sensor ts stalls).
      if (measFromBackend != null && Number.isFinite(Number(measFromBackend))) {
        st.ema = Number(measFromBackend);
      } else if (st.lastSeq != null && st.lastT != null) {
        const dSeq = frame.seq - st.lastSeq;
        // Use viz message clock, not sensor t_wall (camera ts can repeat).
        const dT = nowT - st.lastT;
        if (dSeq > 0 && dT > 1e-4) {
          const inst = dSeq / dT;
          st.ema = st.ema == null ? inst : st.ema * 0.8 + inst * 0.2;
        }
      }
      st.lastSeq = frame.seq;
      st.lastT = nowT;
      return fmtRate(st.ema, target);
    }

    function render(msg) {
      applyRecordUi(msg.record);

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
      const usedSlots = new Set();
      if (msg.gripper_gello_sync) {
        window.__gripGelloSync = msg.gripper_gello_sync;
      }
      if (msg.gello_arm_sync) {
        const prev = window.__gelloArmSync || {};
        const cur = msg.gello_arm_sync;
        window.__gelloArmSync = cur;
        if (prev.phase !== 'completed' && cur.phase === 'completed') {
          showAppModal('同步完成', cur.message || '同步完成；gello 已不再控制机械臂');
        } else if (prev.phase !== 'error' && cur.phase === 'error' && cur.last_error) {
          showAppModal('同步失败', cur.last_error);
        }
      }
      if (msg.gello_arm_teleop) {
        const prevT = window.__gelloArmTeleop || {};
        const curT = msg.gello_arm_teleop;
        window.__gelloArmTeleop = curT;
        if (prevT.phase === 'teleop' && curT.phase === 'error' && curT.last_error) {
          showAppModal('摇操已解除', curT.last_error);
        }
      }
      frames.forEach((frame) => {
        const ar = agentRates[frame.agent_id] || {};
        const hzText = updateBackHz(
          frame.agent_id,
          frame,
          ar.hz_target,
          ar.hz_meas,
          msg.t_wall,
        );
        if (frame.kind === 'realsense') {
          const slot = pickCamSlot(frame, usedSlots);
          if (slot) {
            usedSlots.add(slot);
            renderCamSlot(slot, frame, hzText);
          }
          return;
        }
        const card = ensureStateCard(frame.agent_id);
        if (frame.kind === 'gripper_read') {
          renderGripperRead(card, frame, hzText);
        } else if (frame.kind === 'gripper_write') {
          renderGripperWrite(card, frame, hzText);
        } else if (frame.kind === 'arm_write') {
          renderArmWrite(card, frame, hzText);
        } else if (frame.kind === 'arm_read') {
          renderArmRead(card, frame, hzText);
        } else {
          renderGello(card, frame, hzText);
        }
      });

      rawEl.textContent = JSON.stringify(msg, (k, v) => {
        if ((k === 'jpeg_b64' || k === 'jpeg_b64_preview') && typeof v === 'string') {
          return '<jpeg ' + v.length + ' chars>';
        }
        return v;
      }, 2);
    }

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      setConnStatus('connecting…', 'st-connecting');
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { setConnStatus('live', 'st-live'); };
      ws.onclose = () => {
        setConnStatus('reconnecting…', 'st-reconnecting');
        setTimeout(connect, 800);
      };
      ws.onerror = () => { setConnStatus('error', 'st-error'); };
      ws.onmessage = (ev) => {
        try { render(JSON.parse(ev.data)); } catch (e) {}
      };
    }
    fetch('/api/record/status').then((r) => r.json()).then(applyRecordUi).catch(() => {});
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


def create_viz_app(
    hub: VizHub,
    status_fn: Callable[[], dict[str, Any]],
    *,
    recorder: Any | None = None,
    gripper_command: Callable[..., dict[str, Any]] | None = None,
    gripper_gello_sync: Callable[..., dict[str, Any]] | None = None,
    gripper_gello_sync_status: Callable[[], dict[str, Any]] | None = None,
    arm_command: Callable[..., dict[str, Any]] | None = None,
    gello_arm_sync: Callable[..., dict[str, Any]] | None = None,
    gello_arm_sync_status: Callable[[], dict[str, Any]] | None = None,
    gello_arm_teleop: Callable[..., dict[str, Any]] | None = None,
    gello_arm_teleop_status: Callable[[], dict[str, Any]] | None = None,
    shutdown: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
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

    @app.get("/api/record/status")
    async def record_status() -> dict[str, Any]:
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return {"ok": True, **recorder.status()}

    @app.post("/api/record/start")
    async def record_start() -> dict[str, Any]:
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return recorder.start()

    @app.post("/api/record/stop")
    async def record_stop() -> dict[str, Any]:
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        # Block until disk flush completes so UI can keep Start disabled.
        return await asyncio.to_thread(recorder.stop)

    @app.post("/api/record/save_dir")
    async def record_save_dir(req: SaveDirBody) -> dict[str, Any]:
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return recorder.set_save_dir(req.save_dir)

    @app.post("/api/gripper/command")
    async def gripper_cmd(req: GripperCommandBody) -> dict[str, Any]:
        if gripper_command is None:
            return {"ok": False, "error": "gripper write unavailable"}
        return await asyncio.to_thread(
            gripper_command,
            agent_id=req.agent_id,
            position_norm=req.position_norm,
            position_raw=req.position_raw,
            initialize=bool(req.initialize),
        )

    @app.get("/api/gripper/gello-sync")
    async def gripper_gello_sync_get() -> dict[str, Any]:
        if gripper_gello_sync_status is None:
            return {"ok": False, "error": "gripper gello sync unavailable", "enabled": False}
        return {"ok": True, **gripper_gello_sync_status()}

    @app.post("/api/gripper/gello-sync")
    async def gripper_gello_sync_set(req: GripperGelloSyncBody) -> dict[str, Any]:
        if gripper_gello_sync is None:
            return {"ok": False, "error": "gripper gello sync unavailable", "enabled": False}
        return await asyncio.to_thread(
            gripper_gello_sync,
            enabled=bool(req.enabled),
            gello_agent_id=req.gello_agent_id,
            gripper_agent_id=req.gripper_agent_id,
            joint_index=int(req.joint_index),
            hz=req.hz,
        )

    @app.post("/api/arm/command")
    async def arm_cmd(req: ArmCommandBody) -> dict[str, Any]:
        if arm_command is None:
            return {"ok": False, "error": "arm write unavailable"}
        return await asyncio.to_thread(
            arm_command,
            agent_id=req.agent_id,
            arm=bool(req.arm),
            disarm=bool(req.disarm),
            stop=bool(req.stop),
            joints_rad=req.joints_rad,
            jog_joint=req.jog_joint,
            delta_rad=req.delta_rad,
            delta_deg=req.delta_deg,
        )

    @app.get("/api/arm/gello-sync")
    async def arm_gello_sync_get() -> dict[str, Any]:
        if gello_arm_sync_status is None:
            return {"ok": False, "error": "gello arm sync unavailable", "enabled": False}
        return {"ok": True, **gello_arm_sync_status()}

    @app.post("/api/arm/gello-sync")
    async def arm_gello_sync_set(req: GelloArmSyncBody) -> dict[str, Any]:
        if gello_arm_sync is None:
            return {"ok": False, "error": "gello arm sync unavailable", "enabled": False}
        return await asyncio.to_thread(
            gello_arm_sync,
            enabled=bool(req.enabled),
            gello_agent_id=req.gello_agent_id,
            arm_agent_id=req.arm_agent_id,
            arm_write_agent_id=req.arm_write_agent_id,
        )

    @app.get("/api/arm/gello-teleop")
    async def arm_gello_teleop_get() -> dict[str, Any]:
        if gello_arm_teleop_status is None:
            return {"ok": False, "error": "gello arm teleop unavailable", "enabled": False}
        return {"ok": True, **gello_arm_teleop_status()}

    @app.post("/api/arm/gello-teleop")
    async def arm_gello_teleop_set(req: GelloArmTeleopBody) -> dict[str, Any]:
        if gello_arm_teleop is None:
            return {"ok": False, "error": "gello arm teleop unavailable", "enabled": False}
        return await asyncio.to_thread(
            gello_arm_teleop,
            enabled=bool(req.enabled),
            gello_agent_id=req.gello_agent_id,
            arm_agent_id=req.arm_agent_id,
            arm_write_agent_id=req.arm_write_agent_id,
        )

    @app.post("/api/shutdown")
    async def api_shutdown() -> dict[str, Any]:
        if shutdown is None:
            return {"ok": False, "error": "shutdown unavailable"}
        return await asyncio.to_thread(shutdown)

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


ERROR_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sensors-dcs · 配置错误</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --accent: #3d9a8b;
      --danger: #d9776c;
      --line: #2a3a4f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #2a1c1c 0%, var(--bg) 55%);
      color: var(--text);
      min-height: 100vh;
    }
    header {
      padding: 1.25rem 1.5rem 0.5rem;
      border-bottom: 1px solid var(--line);
    }
    header h1 { margin: 0; font-size: 1.35rem; font-weight: 600; color: var(--danger); }
    header p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
    main { padding: 1rem 1.5rem 2rem; display: grid; gap: 1rem; max-width: 920px; }
    .card {
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 1rem 1.1rem;
    }
    .card h2 { margin: 0 0 0.5rem; font-size: 1rem; }
    .path { color: var(--accent); font-size: 0.9rem; word-break: break-all; }
    pre {
      margin: 0; padding: 1rem; overflow: auto; white-space: pre-wrap; word-break: break-word;
      background: #0b1017; border: 1px solid var(--line); border-radius: 10px;
      font-size: 0.82rem; line-height: 1.45; color: #f0c4be;
    }
    .hint { color: var(--muted); font-size: 0.85rem; line-height: 1.5; }
    code { color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <h1>配置错误</h1>
    <p>程序未退出；请修正 YAML 后重新启动。Agent 未启动。</p>
  </header>
  <main>
    <div class="card">
      <h2>配置文件</h2>
      <div class="path" id="cfgPath">—</div>
    </div>
    <div class="card">
      <h2>错误详情</h2>
      <pre id="errMsg">—</pre>
    </div>
    <div class="card hint">
      DCS 启动 YAML 需含 <code>sensors_config</code> 与 <code>agents</code>。
      不要用 <code>sensors_*.yaml</code>（设备清单）直接启动。
      桌面端可用 <code>sensors-dcs.exe -c &lt;dcs.yaml&gt;</code>
      或环境变量 <code>SENSORS_DCS_CONFIG</code>。
    </div>
  </main>
  <script>
    fetch('/api/status').then(r => r.json()).then(j => {
      document.getElementById('cfgPath').textContent = j.config_path || '—';
      document.getElementById('errMsg').textContent = j.error || '—';
    }).catch(e => {
      document.getElementById('errMsg').textContent = String(e);
    });
  </script>
</body>
</html>
"""


def create_error_app(*, error: str, config_path: str | None = None) -> FastAPI:
    """Minimal UI when YAML / boot fails — keep process alive for the user."""
    app = FastAPI(title="sensors-dcs error", version="0.1.0")
    payload = {
        "ok": False,
        "boot_error": True,
        "error": error,
        "config_path": config_path,
    }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return ERROR_HTML

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return payload

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": False, "boot_error": True}

    return app
