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
    .agent-bars { display: grid; gap: 0.55rem; }
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
      <span class="hint" id="runHint">空闲 — 点「开始」录制当前 episode</span>
    </div>
    <div class="save-path">
      <label for="saveDirInput">保存路径</label>
      <input type="text" id="saveDirInput" placeholder="留空则沿用当前路径" />
      <button type="button" id="btnSaveDir">应用</button>
    </div>
    <div class="meta">
      <div>连接：<strong id="status">connecting…</strong></div>
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
  <script>
    const agentsEl = document.getElementById('agents');
    const camGridEl = document.getElementById('cam-grid');
    const statusEl = document.getElementById('status');
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

    function ensureRows(barsRoot, n, labelFn, mode) {
      // mode: 'dual' (gello/gripper read) | 'single' (arm / write status)
      const want = mode || 'dual';
      if (barsRoot.dataset.barMode !== want) {
        barsRoot.innerHTML = '';
        barsRoot.dataset.barMode = want;
      }
      while (barsRoot.children.length > n) barsRoot.removeChild(barsRoot.lastChild);
      while (barsRoot.children.length < n) {
        const row = document.createElement('div');
        row.className = 'row';
        if (want === 'dual') {
          row.innerHTML =
            '<span class="lab"></span>' +
            '<div class="track track-dual">' +
              '<div class="track-lane"><div class="fill fill-cal"></div></div>' +
              '<div class="track-lane"><div class="fill fill-raw"></div></div>' +
            '</div>' +
            '<span class="val"><span class="v-cal">—</span><br><span class="v-raw">—</span></span>';
        } else {
          row.innerHTML =
            '<span class="lab"></span>' +
            '<div class="track"><div class="fill fill-cal"></div></div>' +
            '<span class="val"><span class="v-cal">—</span></span>';
        }
        barsRoot.appendChild(row);
      }
      for (let i = 0; i < barsRoot.children.length; i++) {
        const lab = barsRoot.children[i].querySelector('.lab');
        if (lab) lab.textContent = labelFn(i);
      }
    }

    function setDualBar(row, calPct, rawPct, calText, rawText) {
      const calFill = row.querySelector('.fill-cal');
      const rawFill = row.querySelector('.fill-raw');
      const vCal = row.querySelector('.v-cal');
      const vRaw = row.querySelector('.v-raw');
      if (calFill) calFill.style.width = Math.max(0, Math.min(100, calPct)).toFixed(1) + '%';
      if (rawFill) rawFill.style.width = Math.max(0, Math.min(100, rawPct)).toFixed(1) + '%';
      if (vCal) vCal.textContent = calText;
      if (vRaw) vRaw.textContent = rawText;
    }

    function setSingleBar(row, pct, text) {
      const fill = row.querySelector('.fill-cal') || row.querySelector('.fill');
      const vCal = row.querySelector('.v-cal') || row.querySelector('.val');
      if (fill) fill.style.width = Math.max(0, Math.min(100, pct)).toFixed(1) + '%';
      if (vCal) vCal.textContent = text;
    }

    function jointBarPct(rad) {
      const r = Number(rad);
      if (!Number.isFinite(r)) return 0;
      // Wider range so raw (often ±π offsets) and calibrated (±1) both readable.
      return Math.max(0, Math.min(1, (r + Math.PI) / (2 * Math.PI))) * 100;
    }

    function renderArmRead(card, frame, hzText) {
      card.querySelector('h2').textContent = 'robot · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const joints = (frame.payload && frame.payload.joints_rad) || [];
      const barsRoot = card.querySelector('.agent-bars');
      ensureRows(barsRoot, joints.length, (i) => 'j' + i, 'single');
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      for (let i = 0; i < joints.length; i++) {
        const cal = joints[i];
        const txt = cal == null || !Number.isFinite(Number(cal)) ? '—' : Number(cal).toFixed(3);
        setSingleBar(barsRoot.children[i], cal == null ? 0 : jointBarPct(cal), txt);
      }
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
      const barsRoot = card.querySelector('.agent-bars');
      const n = Math.max(joints.length, jointsRaw.length);
      ensureRows(barsRoot, n, (i) => 'j' + i, 'dual');
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      for (let i = 0; i < n; i++) {
        const cal = joints[i];
        const raw = jointsRaw[i];
        const calTxt = cal == null || !Number.isFinite(Number(cal))
          ? '—'
          : ('cal ' + Number(cal).toFixed(3));
        const rawTxt = raw == null || !Number.isFinite(Number(raw))
          ? '—'
          : ('raw ' + Number(raw).toFixed(3));
        setDualBar(
          barsRoot.children[i],
          cal == null ? 0 : jointBarPct(cal),
          raw == null ? 0 : jointBarPct(raw),
          calTxt,
          rawTxt,
        );
      }
    }

    function renderGripperRead(card, frame, hzText) {
      card.querySelector('h2').textContent = 'gripper · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const pos = frame.payload && frame.payload.position_norm;
      const raw = (frame.payload && (frame.payload.raw_value ?? frame.payload.position_raw));
      const barsRoot = card.querySelector('.agent-bars');
      ensureRows(barsRoot, 1, () => 'pos', 'dual');
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      const p = pos == null ? null : Number(pos);
      const r = raw == null ? null : Number(raw);
      const calPct = p == null || !Number.isFinite(p) ? 0 : Math.max(0, Math.min(1, p / 0.637)) * 100;
      const rawPct = r == null || !Number.isFinite(r) ? 0 : Math.max(0, Math.min(1, r / 1000)) * 100;
      setDualBar(
        barsRoot.children[0],
        calPct,
        rawPct,
        p == null || !Number.isFinite(p) ? '—' : ('norm ' + p.toFixed(3)),
        r == null || !Number.isFinite(r) ? '—' : ('raw ' + String(Math.round(r))),
      );
    }

    function renderGripperWrite(card, frame, hzText) {
      card.querySelector('h2').textContent = 'gripper · Write';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const barsRoot = card.querySelector('.agent-bars');
      ensureRows(barsRoot, 1, () => 'cmd', 'single');
      const p = frame.payload || {};
      const norm = p.command_position_norm;
      const pct = norm == null || !Number.isFinite(Number(norm))
        ? 0
        : Math.max(0, Math.min(1, Number(norm) / 0.637)) * 100;
      const ok = p.last_ok;
      const txt = norm == null
        ? '未下发'
        : ('norm ' + Number(norm).toFixed(3) + (ok === false ? ' ✗' : ok ? ' ✓' : ''));
      setSingleBar(barsRoot.children[0], pct, txt);
      let box = card.querySelector('.grip-cmd');
      if (!box) {
        box = document.createElement('div');
        box.className = 'grip-cmd';
        box.innerHTML =
          '<label>position_norm</label>' +
          '<input type="number" step="0.01" min="0" max="0.637" value="0.32" class="grip-norm" />' +
          '<button type="button" class="grip-send">下发</button>' +
          '<span class="cmd-hint">0≈开 … 0.637≈合（与读侧 norm 同尺度）</span>';
        card.appendChild(box);
        const btn = box.querySelector('.grip-send');
        const inp = box.querySelector('.grip-norm');
        btn.addEventListener('click', async () => {
          const v = Number(inp.value);
          if (!Number.isFinite(v)) {
            runHint.textContent = '夹爪：请输入有效数字';
            return;
          }
          btn.disabled = true;
          try {
            const r = await fetch('/api/gripper/command', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ agent_id: frame.agent_id, position_norm: v }),
            }).then((x) => x.json());
            runHint.textContent = r.ok
              ? ('夹爪已下发 norm=' + v)
              : ('夹爪下发失败：' + (r.error || JSON.stringify(r)));
          } catch (e) {
            runHint.textContent = '夹爪下发异常：' + e;
          } finally {
            btn.disabled = false;
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
        )

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
