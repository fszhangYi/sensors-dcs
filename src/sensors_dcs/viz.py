from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

from pydantic import BaseModel
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse


class SaveDirBody(BaseModel):
    save_dir: str | None = None


class RecordStartBody(BaseModel):
    mode: str | None = None  # collect | infer


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
    duration_s: float | None = None
    cancel_abs_ramp: bool = False
    jog_joint: int | None = None
    delta_rad: float | None = None
    delta_deg: float | None = None


class PostprocessBody(BaseModel):
    """UI / quick-collect payload for the three offline CLI steps."""

    episode: str
    steps: list[str] | None = None
    align: str = "asof"
    master: str = "cam-left"
    master_hz: float | None = 5.0
    require: str = "arm,cam-left,cam-right,cam-middle,gripper-read"
    max_match_dt: str = "0.033"
    trim: str = "both"
    materialize: bool = True
    camera_map: str | None = None
    allow_invalid: bool = False


class AuthLoginBody(BaseModel):
    username: str = ""
    password: str = ""


class UserCreateBody(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "operator"


class UserUpdateBody(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    password: str | None = None


class ApplyConfigBody(BaseModel):
    path: str = ""


class ArmHomeSetBody(BaseModel):
    joints_rad: list[float] | None = None
    from_live: bool = False
    duration_s: float | None = None


class ArmHomeSaveBody(BaseModel):
    path: str | None = None


class ArmHomeGoBody(BaseModel):
    agent_id: str | None = None
    duration_s: float | None = None


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


class Pi05ConnectBody(BaseModel):
    host: str | None = None
    port: int | None = None
    agent_id: str | None = None


class Pi05PromptBody(BaseModel):
    prompt: str = ""
    agent_id: str | None = None


class Pi05RunBody(BaseModel):
    enabled: bool
    agent_id: str | None = None


class Pi05AgentIdBody(BaseModel):
    agent_id: str | None = None


PREVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark" data-theme-pref="dark" data-compact="0" data-density="comfortable">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sensors-dcs · 采集 / 后处理</title>
  <script>
  (function () {
    try {
      var framed = false;
      try { framed = window.self !== window.top; } catch (e) { framed = true; }
      var theme = localStorage.getItem('sensors-dcs.theme') || 'dark';
      var localeHint = null;
      if (framed) {
        try {
          var q = new URLSearchParams(location.search);
          var loc = (q.get('locale') || q.get('lang') || '').toLowerCase();
          if (loc === 'zh' || loc === 'en') localeHint = loc;
          var th = (q.get('theme') || '').toLowerCase();
          if (th === 'system' || th === 'dark' || th === 'light') theme = th;
        } catch (e) {}
      }
      if (theme !== 'system' && theme !== 'dark' && theme !== 'light') theme = 'dark';
      var compact = localStorage.getItem('sensors-dcs.compact');
      var density = localStorage.getItem('sensors-dcs.density') || 'comfortable';
      if (density !== 'comfortable' && density !== 'compact' && density !== 'dense') density = 'comfortable';
      var resolved = theme;
      if (theme === 'system') {
        resolved = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches)
          ? 'light' : 'dark';
      }
      var root = document.documentElement;
      root.setAttribute('data-theme', resolved);
      root.setAttribute('data-theme-pref', theme);
      root.setAttribute('data-compact', (compact === '1' || compact === 'true') ? '1' : '0');
      root.setAttribute('data-density', density);
      root.style.colorScheme = resolved;
      if (localeHint) root.lang = localeHint === 'zh' ? 'zh-CN' : 'en';
      if (framed) root.setAttribute('data-embed', '1');
    } catch (e) {}
  })();
  </script>
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml" />
  <link rel="icon" href="/assets/favicon.ico" sizes="any" />
  <link rel="apple-touch-icon" href="/assets/favicon.png" />
  <link rel="stylesheet" href="/assets/fonts/ibm-plex-sans.css" />
  <link rel="stylesheet" href="/assets/settings.css" />
  <link rel="stylesheet" href="/assets/appearance.css" />
  <style>
    :root,
    html[data-theme='dark'] {
      --bg: #0b1018;
      --panel: rgba(18, 26, 38, 0.9);
      --border: rgba(58, 77, 102, 0.75);
      --line: var(--border);
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --accent: #3dd6c6;
      --accent-dim: rgba(61, 214, 198, 0.14);
      --spark: #f0b429;
      --spark-dim: rgba(240, 180, 41, 0.14);
      --danger: #f87171;
      --ok: #4ade80;
      --warn: #fbbf24;
      --off: #6b7a90;
      --surface: rgba(18, 26, 38, 0.96);
      --surface-2: rgba(22, 32, 48, 0.92);
      --surface-3: rgba(28, 40, 58, 0.95);
      --chrome: rgba(26, 35, 50, 0.88);
      --input-bg: rgba(8, 12, 20, 0.85);
      --bg-spot: #1a2740;
      --bg-spot-2: rgba(26, 39, 64, 0.55);
      --bg-spot-3: rgba(20, 48, 52, 0.35);
      --overlay-scrim: rgba(4, 8, 14, 0.72);
      --brand-title: linear-gradient(120deg, #e7ecf3 25%, #3dd6c6 70%, #f0b429 100%);
      --header-bg: linear-gradient(180deg, #0d131c 0%, #0b1018 100%);
      --scrollbar-thumb: rgba(61, 214, 198, 0.45);
      --scrollbar-thumb-hover: rgba(61, 214, 198, 0.7);
      --scrollbar-size: 4px;
      --motion-ease: cubic-bezier(0.22, 1, 0.36, 1);
      --motion-fast: 160ms;
      --motion-med: 280ms;
      color-scheme: dark;
    }
    * { box-sizing: border-box; }
    * { scrollbar-width: thin; scrollbar-color: var(--scrollbar-thumb) transparent; }
    *::-webkit-scrollbar { width: var(--scrollbar-size); height: var(--scrollbar-size); }
    *::-webkit-scrollbar-track { background: transparent; }
    *::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 999px; }
    *::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
    html, body {
      height: 100%;
      overflow: hidden;
    }
    body {
      margin: 0;
      font-family: 'IBM Plex Sans', 'Segoe UI', 'PingFang SC', 'Noto Sans SC', sans-serif;
      background:
        radial-gradient(900px 480px at 12% -8%, var(--bg-spot-2), transparent 55%),
        radial-gradient(700px 420px at 88% 8%, var(--bg-spot-3), transparent 50%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
      max-height: 100vh;
      display: flex;
      flex-direction: column;
      position: relative;
    }
    body.dcs-page::before {
      content: "";
      pointer-events: none;
      position: fixed;
      inset: 0;
      z-index: 0;
      opacity: 0.35;
      background-image:
        linear-gradient(rgba(61, 214, 198, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(61, 214, 198, 0.04) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: radial-gradient(ellipse 70% 60% at 50% 20%, #000 20%, transparent 75%);
    }
    header, .tabs, main, .modal-backdrop { position: relative; z-index: 1; }
    header {
      flex-shrink: 0;
      padding: 0.85rem 1.25rem 0.55rem;
      border-bottom: 1px solid var(--border);
      background: var(--header-bg);
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }
    header .header-brand {
      display: flex; align-items: flex-start; gap: 0.75rem; flex: 1; min-width: 0;
    }
    header .header-logo {
      width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0; margin-top: 0.15rem;
      box-shadow: 0 0 0 1px rgba(61, 214, 198, 0.22);
    }
    header .header-text { flex: 1; min-width: 0; }
    header .kicker {
      margin: 0 0 0.2rem;
      font-size: 0.68rem;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
    }
    header h1 {
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: 0.02em;
      font-weight: 650;
      background: var(--brand-title);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }
    header p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.82rem; line-height: 1.45; }
    header #btnExit {
      flex-shrink: 0;
      appearance: none;
      border: 1px solid var(--danger);
      background: color-mix(in srgb, var(--danger) 22%, var(--chrome));
      color: var(--text);
      font: inherit;
      font-size: 0.85rem;
      font-weight: 550;
      padding: 0.45rem 1.15rem;
      border-radius: 999px;
      cursor: pointer;
      margin-top: 0.15rem;
      transition: border-color var(--motion-fast) var(--motion-ease), background var(--motion-fast) var(--motion-ease);
    }
    header #btnExit:hover {
      border-color: #fca5a5;
      background: color-mix(in srgb, var(--danger) 36%, var(--chrome));
      color: #fff;
    }
    header #btnExit:disabled { opacity: 0.45; cursor: not-allowed; }
    header .header-actions {
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 0.45rem;
      flex-shrink: 0;
    }
    .iframe-embed-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      border: 1px solid rgba(248, 113, 113, 0.45);
      color: #fecaca;
      background: rgba(80, 20, 28, 0.45);
      font-size: 0.72rem;
      font-weight: 650;
    }
    .iframe-embed-badge[hidden] { display: none !important; }
    .iframe-embed-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: #f87171;
      animation: iframe-pulse 1.2s ease-in-out infinite;
    }
    @keyframes iframe-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.35; }
    }
    html[data-embed='1'] #btnSettings,
    html[data-embed='1'] #btnExit {
      display: none !important;
    }
    html[lang='en'] header p { max-width: 42rem; }
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
      display: flex; flex-wrap: wrap; gap: 0.55rem 0.85rem;
      color: var(--muted); font-size: 0.8rem;
    }
    .meta > div {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.22rem 0.65rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--chrome);
      backdrop-filter: blur(8px);
    }
    .meta strong { color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
    .actions {
      flex-shrink: 0;
      display: flex; flex-wrap: wrap; gap: 0.55rem; align-items: center;
    }
    .actions button,
    .save-path button,
    .pp-actions button,
    .grip-cmd button,
    .arm-cmd button {
      appearance: none;
      border: 1px solid var(--border);
      background: var(--chrome);
      color: var(--text);
      font: inherit;
      font-size: 0.85rem;
      font-weight: 550;
      padding: 0.42rem 1.05rem;
      border-radius: 999px;
      cursor: pointer;
      transition: border-color var(--motion-fast) var(--motion-ease), background var(--motion-fast) var(--motion-ease), transform var(--motion-fast) var(--motion-ease);
    }
    .actions button:hover,
    .save-path button:hover,
    .pp-actions button:hover { border-color: var(--accent); background: var(--accent-dim); }
    .actions button:disabled,
    .pp-actions button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
      pointer-events: none;
    }
    .actions button.primary,
    .pp-actions button.primary {
      background: linear-gradient(120deg, var(--accent-dim), color-mix(in srgb, var(--spark-dim) 55%, var(--accent-dim)));
      border-color: var(--accent);
      color: var(--text);
      box-shadow: 0 0 0 1px rgba(61, 214, 198, 0.12);
    }
    .actions button.primary:hover,
    .pp-actions button.primary:hover {
      border-color: var(--spark);
    }
    .actions button.primary:disabled,
    .pp-actions button.primary:disabled {
      opacity: 1;
      color: var(--muted);
      background: var(--chrome);
      border-color: var(--border);
      box-shadow: none;
      cursor: not-allowed;
      pointer-events: none;
    }
    .actions button.primary:disabled:hover,
    .pp-actions button.primary:disabled:hover {
      border-color: var(--border);
      background: var(--chrome);
    }
    .actions .hint, .pp-actions .hint { color: var(--muted); font-size: 0.82rem; }
    .save-path {
      flex-shrink: 0;
      display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
      font-size: 0.85rem;
    }
    .save-path label { color: var(--muted); }
    .save-path input,
    .pp-row input[type="text"],
    .pp-row input[type="number"],
    .pp-row select,
    .grip-cmd input {
      appearance: none;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      padding: 0.4rem 0.65rem;
      border-radius: 8px;
    }
    .save-path input {
      flex: 1 1 12rem;
      min-width: 8rem;
      max-width: 28rem;
      font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
      font-size: 0.8rem;
    }
    .save-path input:disabled { opacity: 0.45; }
    .agent-card,
    .pp-card,
    .cam-cell {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      transition: border-color var(--motion-med) var(--motion-ease), box-shadow var(--motion-med) var(--motion-ease);
    }
    .agent-card:hover,
    .pp-card:hover,
    .cam-cell:hover {
      border-color: rgba(61, 214, 198, 0.45);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
    }
    .agent-card {
      padding: 0.75rem;
      display: grid;
      gap: 0.5rem;
      flex-shrink: 0;
    }
    .agent-card h2, .pp-card h2, .cam-section h2 {
      margin: 0;
      font-size: 0.95rem;
      font-weight: 600;
    }
    .agent-meta {
      display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
      color: var(--muted); font-size: 0.78rem;
    }
    .agent-meta strong { color: var(--accent); font-weight: 600; }
    #status.st-live { color: var(--accent); }
    #status.st-connecting { color: var(--muted); }
    #status.st-reconnecting { color: var(--spark); }
    #status.st-error, #status.st-offline { color: var(--danger); }
    button.danger {
      background: color-mix(in srgb, var(--danger) 22%, var(--chrome));
      border-color: var(--danger);
      color: var(--text);
    }
    button.discard {
      background: color-mix(in srgb, var(--spark) 18%, var(--chrome));
      border-color: color-mix(in srgb, var(--spark) 55%, var(--border));
      color: var(--text);
    }
    .tabs {
      flex-shrink: 0;
      display: flex;
      gap: 0.4rem;
      padding: 0.55rem 1.25rem 0.35rem;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(11, 16, 24, 0.55), transparent);
    }
    .tabs button.tab {
      appearance: none;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 0.82rem;
      font-weight: 550;
      padding: 0.38rem 1rem;
      border-radius: 999px;
      cursor: pointer;
      transition: background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease), border-color var(--motion-fast) var(--motion-ease);
    }
    .tabs button.tab:hover { color: var(--text); border-color: rgba(61, 214, 198, 0.4); }
    .tabs button.tab.active {
      color: var(--text);
      background: linear-gradient(90deg, var(--spark-dim), var(--accent-dim));
      border-color: rgba(61, 214, 198, 0.45);
      box-shadow: inset 0 0 0 1px rgba(61, 214, 198, 0.12);
    }
    .tabs button.tab.tab-locked,
    .tabs button.tab:disabled {
      opacity: 0.42;
      cursor: not-allowed;
      color: var(--muted);
    }
    .tabs button.tab.tab-locked:hover,
    .tabs button.tab:disabled:hover {
      color: var(--muted);
      border-color: var(--border);
    }
    .boot-banner {
      flex-shrink: 0;
      margin: 0.55rem 1.25rem 0;
      padding: 0.65rem 0.9rem;
      border-radius: 12px;
      border: 1px solid rgba(248, 113, 113, 0.45);
      background: rgba(64, 26, 26, 0.55);
      color: #f0c4be;
      font-size: 0.82rem;
      line-height: 1.45;
      display: none;
      gap: 0.35rem;
      flex-direction: column;
    }
    .boot-banner.visible { display: flex; }
    .boot-banner strong { color: var(--danger); font-weight: 600; }
    .boot-banner .boot-path {
      color: var(--accent);
      font-family: ui-monospace, Consolas, monospace;
      word-break: break-all;
      font-size: 0.78rem;
    }
    .boot-banner pre {
      margin: 0.25rem 0 0;
      max-height: 7rem;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.72rem;
      color: #f0c4be;
      font-family: ui-monospace, Consolas, monospace;
    }
    .tab-panel { display: none; }
    .tab-panel.active {
      display: flex;
      flex: 1;
      min-height: 0;
      flex-direction: column;
      gap: 0.65rem;
      overflow: hidden;
    }
    #tab-post.active,
    #tab-home.active { overflow-y: auto; }
    /* Collect/Infer: allow scroll so chrome (esp. Infer pi05 panel) cannot crush sensors. */
    #tab-collect.active,
    #tab-infer.active {
      overflow-y: auto;
      overflow-x: hidden;
    }
    .content-row {
      flex: 1 1 auto;
      min-height: 22rem;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
      align-items: stretch;
    }
    #tab-sensors.active {
      gap: 0;
      overflow: hidden;
      padding: 0;
    }
    .sensors-page {
      position: relative;
      flex: 1;
      min-height: 0;
      overflow: hidden;
      background: var(--bg);
    }
    .sensors-embed {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      border: 0;
      display: block;
      background: var(--bg);
    }
    .sensors-unreachable {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 24px;
      text-align: center;
      background: var(--bg);
    }
    .sensors-unreachable-title {
      margin: 0;
      font-size: 1.05rem;
      font-weight: 600;
      color: var(--text);
    }
    .sensors-unreachable-msg {
      margin: 0;
      max-width: 36rem;
      font-size: 0.85rem;
      color: var(--muted);
    }
    .sensors-unreachable-url {
      margin: 8px 0 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.72rem;
      word-break: break-all;
      color: var(--muted);
    }
    .home-grid {
      display: grid;
      gap: 0.75rem;
      padding-bottom: 1rem;
      max-width: 52rem;
    }
    .home-grid .home-lead {
      color: var(--text);
      font-size: 0.95rem;
      line-height: 1.55;
      margin: 0.35rem 0 0;
    }
    .home-grid .pp-card > p {
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.55;
      margin: 0.4rem 0 0;
    }
    .home-list {
      margin: 0.45rem 0 0;
      padding-left: 1.2rem;
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.55;
    }
    .home-list li { margin: 0.28rem 0; }
    .home-cta .pp-actions { margin-top: 0.65rem; }
    .quick-collect {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      color: var(--muted);
      font-size: 0.82rem;
      user-select: none;
      cursor: pointer;
      padding: 0.28rem 0.7rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--chrome);
    }
    .quick-collect input { accent-color: var(--accent); }
    .pp-grid {
      display: grid;
      gap: 0.75rem;
      padding-bottom: 1rem;
    }
    .pp-card {
      padding: 0.85rem 1rem;
      display: grid;
      gap: 0.55rem;
    }
    .pp-card .pp-hint {
      margin: 0;
      color: var(--muted);
      font-size: 0.78rem;
      font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
    }
    .pp-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 0.75rem;
      align-items: center;
      font-size: 0.85rem;
    }
    .pp-row label { color: var(--muted); min-width: 5.5rem; }
    .pp-row input[type="text"],
    .pp-row input[type="number"],
    .pp-row select {
      min-width: 8rem;
    }
    .pp-row input.wide { flex: 1 1 16rem; min-width: 12rem; font-family: ui-monospace, Consolas, monospace; font-size: 0.8rem; }
    .pp-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
    pre#ppLog, pre#raw, pre#infRaw {
      margin: 0;
      padding: 0.65rem 0.85rem;
      overflow: auto;
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      font-size: 0.72rem;
      line-height: 1.4;
      color: var(--text);
      font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
    }
    pre#ppLog { max-height: 28vh; min-height: 5rem; white-space: pre-wrap; }
    pre#raw, pre#infRaw {
      flex-shrink: 0;
      width: 100%;
      max-height: 12vh;
      min-height: 3rem;
    }
    .inf-banner {
      margin: 0 0 0.55rem;
      padding: 0.45rem 0.65rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel, rgba(18, 26, 38, 0.55));
      color: var(--muted);
      font-size: 0.82rem;
    }
    .inf-pi05-panel {
      --inf-fs: 0.8125rem;
      --inf-ctrl-h: 1.85rem;
      flex-shrink: 0;
      margin: 0 0 0.65rem;
      padding: 0.65rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel, rgba(18, 26, 38, 0.4));
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      font-size: var(--inf-fs);
      line-height: 1.35;
      color: var(--text);
    }
    .inf-pi05-panel * { box-sizing: border-box; }
    .inf-pi05-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem 0.55rem;
      align-items: center;
      min-height: var(--inf-ctrl-h);
    }
    .inf-pi05-row.inf-pi05-hints {
      min-height: 1.35rem;
      align-items: flex-start;
    }
    .inf-pi05-panel label,
    .inf-pi05-panel .inf-k,
    .inf-pi05-panel .hint,
    .inf-pi05-panel .arm-abs-dur,
    .inf-pi05-panel .arm-abs-label {
      color: var(--muted);
      font-size: var(--inf-fs);
      font-weight: 500;
      margin: 0;
    }
    .inf-pi05-panel button {
      font-size: var(--inf-fs);
      height: var(--inf-ctrl-h);
      padding: 0 0.7rem;
      line-height: 1;
    }
    .inf-pi05-panel input[type="text"],
    .inf-pi05-panel input[type="number"] {
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      font-size: var(--inf-fs);
      height: var(--inf-ctrl-h);
      padding: 0 0.5rem;
      min-width: 0;
    }
    #infPi05Host { width: 8.5rem; flex: 0 0 auto; }
    #infPi05Port { width: 5.25rem; flex: 0 0 auto; }
    #infPi05Prompt { flex: 1 1 12rem; min-width: 10rem; }
    #infArmJoints {
      flex: 1 1 16rem;
      min-width: 12rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: var(--inf-fs);
    }
    .inf-pi05-panel .arm-abs-dur {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      height: var(--inf-ctrl-h);
    }
    .inf-pi05-panel .arm-abs-dur input[type="number"] {
      width: 4.25rem;
      text-align: right;
    }
    #infArmDurVal {
      min-width: 3.2rem;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    #infPi05Status {
      display: inline-flex;
      align-items: center;
      min-width: 11.5rem;
      max-width: 14rem;
      height: var(--inf-ctrl-h);
      padding: 0 0.45rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--input-bg, rgba(0,0,0,0.12));
      font-size: var(--inf-fs);
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    #infPi05Status.st-live { color: var(--ok, #3dcc91); }
    #infPi05Status.st-offline { color: var(--muted); }
    #infPi05Status.st-error { color: var(--danger, #e07070); }
    #infPi05Status.st-connecting { color: var(--warn, #d4a017); }
    .inf-flag {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      flex: 0 0 auto;
      width: 8.6rem;
      height: var(--inf-ctrl-h);
      padding: 0 0.5rem;
      border: 1px solid var(--border);
      border-radius: 999px;
      font-size: var(--inf-fs);
      color: var(--muted);
      background: var(--input-bg, rgba(0,0,0,0.12));
      overflow: hidden;
    }
    .inf-flag-lab {
      font-weight: 600;
      letter-spacing: 0.02em;
      color: var(--text);
      flex: 0 0 auto;
    }
    .inf-flag-val {
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }
    .inf-dot {
      width: 0.55rem;
      height: 0.55rem;
      border-radius: 50%;
      flex: 0 0 auto;
      background: var(--muted);
      box-shadow: 0 0 0 2px rgba(255,255,255,0.04);
    }
    .inf-dot.st-idle { background: #6b7280; }
    .inf-dot.st-ok { background: var(--ok, #3dcc91); }
    .inf-dot.st-warn { background: var(--warn, #d4a017); }
    .inf-dot.st-bad { background: var(--danger, #e07070); }
    #infPi05Panel button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    #infPi05Hint,
    #infArmProg {
      flex: 1 1 10rem;
      min-width: 8rem;
      min-height: 1.35em;
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: var(--inf-fs);
      color: var(--muted);
    }
    .inf-auto-home {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      height: var(--inf-ctrl-h);
      color: var(--muted);
      font-size: var(--inf-fs);
      user-select: none;
      cursor: pointer;
    }
    .inf-auto-home input { width: auto; height: auto; margin: 0; }
    .inf-loop-rounds {
      display: inline-flex;
      align-items: center;
      gap: 0.28rem;
      height: var(--inf-ctrl-h);
      color: var(--muted);
      font-size: var(--inf-fs);
      white-space: nowrap;
    }
    .inf-loop-rounds input[type="number"] {
      width: 4.2rem;
      height: var(--inf-ctrl-h);
      padding: 0 0.35rem;
    }
    .inf-loop-round-idx {
      min-width: 3.5rem;
      color: var(--text);
      font-variant-numeric: tabular-nums;
    }
    .arm-home-set-row {
      display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center;
      margin-top: 0.35rem;
    }
    .arm-home-set-row button { font-size: 0.78rem; height: 1.7rem; padding: 0 0.55rem; }
    #infPi05Out { display: none; }
    .inf-pi05-raw-modal .modal-card {
      max-width: min(44rem, calc(100vw - 2rem));
      width: 44rem;
    }
    .inf-pi05-raw-modal .modal-body {
      margin: 0;
      max-height: min(60vh, 28rem);
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
      padding: 0.65rem 0.75rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--input-bg);
    }
    .agent-vals {
      display: flex; flex-wrap: wrap; gap: 0.35rem 0.55rem;
      font-variant-numeric: tabular-nums; font-size: 0.8rem;
    }
    .agent-vals .jv {
      background: var(--input-bg); border: 1px solid var(--border); border-radius: 8px;
      padding: 0.2rem 0.45rem; color: var(--muted);
    }
    .agent-vals .jv b { color: var(--text); font-weight: 600; }
    .agent-vals .jv .cal { color: var(--accent); }
    .agent-vals .jv .raw { color: var(--spark); }
    .agent-pose {
      display: flex; flex-wrap: wrap; gap: 0.35rem 0.55rem; align-items: center;
      margin-top: 0.35rem;
      font-variant-numeric: tabular-nums; font-size: 0.8rem;
    }
    .agent-pose[hidden] { display: none !important; }
    .agent-pose .pose-tag {
      color: var(--muted); font-size: 0.72rem; margin-right: 0.15rem;
    }
    .agent-pose .jv {
      background: var(--input-bg); border: 1px solid var(--border); border-radius: 8px;
      padding: 0.2rem 0.45rem; color: var(--muted);
    }
    .agent-pose .jv b { color: var(--text); font-weight: 600; }
    .agent-bars { display: none; }
    #agents, #infAgents {
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
      height: 14px; background: var(--input-bg); border-radius: 999px; overflow: hidden;
      border: 1px solid var(--border);
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
      background: var(--input-bg);
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--border);
    }
    .fill {
      height: 100%; width: 50%;
      background: linear-gradient(90deg, #1f6f68, var(--accent));
      transform-origin: left center;
    }
    .fill.fill-cal {
      background: linear-gradient(90deg, #1f6f68, var(--accent));
    }
    .fill.fill-raw {
      background: linear-gradient(90deg, #a67a1a, var(--spark));
    }
    .row .val .v-cal { color: var(--accent); }
    .row .val .v-raw { color: var(--spark); }
    .grip-cmd {
      display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center;
      margin-top: 0.55rem; font-size: 0.82rem;
    }
    .grip-cmd input { width: 7rem; }
    .grip-cmd button {
      background: var(--accent-dim); border-color: rgba(61, 214, 198, 0.4);
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
      background: var(--accent-dim); border-color: rgba(61, 214, 198, 0.4);
    }
    .arm-cmd button.arm-estop {
      background: color-mix(in srgb, var(--danger) 28%, var(--chrome)); border-color: var(--danger);
    }
    .arm-cmd button:disabled { opacity: 0.4; cursor: not-allowed; }
    .arm-cmd .arm-jog-row {
      display: grid; grid-template-columns: 2.8rem auto auto 1fr; gap: 0.4rem; align-items: center;
    }
    .arm-cmd .arm-jog-row button { min-width: 2.2rem; padding-left: 0.55rem; padding-right: 0.55rem; }
    .arm-cmd .arm-abs-row {
      display: flex; flex-wrap: wrap; gap: 0.4rem 0.55rem; align-items: center;
      margin-top: 0.35rem;
    }
    .arm-cmd .arm-abs-label {
      cursor: pointer;
      user-select: none;
      border-bottom: 1px dashed color-mix(in srgb, var(--muted) 55%, transparent);
    }
    .arm-cmd .arm-abs-label:hover { color: var(--accent, #3dd6c6); }
    .arm-cmd .arm-abs-row input[type="text"] {
      flex: 1 1 16rem; min-width: 12rem;
      font-family: ui-monospace, Consolas, monospace; font-size: 0.78rem;
    }
    .arm-cmd .arm-abs-dur {
      display: inline-flex; align-items: center; gap: 0.35rem;
      color: var(--muted); font-size: 0.72rem;
    }
    .arm-cmd .arm-abs-dur input[type="number"] {
      width: 4.25rem; height: 1.7rem; padding: 0 0.35rem;
      text-align: right; font-size: 0.78rem;
    }
    .arm-cmd .arm-abs-dur-val {
      min-width: 3.2rem; font-variant-numeric: tabular-nums;
    }
    .arm-cmd .arm-abs-prog {
      width: 100%; color: var(--muted); font-size: 0.72rem; margin-top: 0.15rem;
    }
    .arm-cmd .cmd-hint { color: var(--muted); font-size: 0.75rem; }
    .arm-cmd .arm-sync-prog,
    .arm-cmd .arm-teleop-prog {
      color: var(--muted); font-size: 0.78rem; min-height: 1.1em;
    }
    .arm-cmd button.arm-sync-on {
      background: var(--spark-dim); border-color: color-mix(in srgb, var(--spark) 55%, var(--border));
    }
    .arm-cmd button.arm-teleop-on {
      background: var(--accent-dim); border-color: var(--accent);
    }
    .modal-backdrop {
      display: none; position: fixed; inset: 0; z-index: 50;
      background: var(--overlay-scrim); align-items: center; justify-content: center;
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
    }
    .modal-backdrop.show { display: flex; }
    .modal-card {
      max-width: 28rem; margin: 1rem; padding: 1rem 1.15rem; border-radius: 14px;
      background: var(--surface); border: 1px solid var(--border); color: var(--text);
      box-shadow: 0 16px 48px rgba(0,0,0,0.5);
      backdrop-filter: blur(12px);
    }
    .modal-card h3 { margin: 0 0 0.5rem; font-size: 1rem; font-weight: 600; }
    .modal-card p { margin: 0 0 0.85rem; color: var(--muted); font-size: 0.88rem; line-height: 1.45; white-space: pre-wrap; }
    .modal-card button {
      padding: 0.4rem 1rem; border-radius: 999px; border: 1px solid var(--accent);
      background: var(--accent-dim); color: var(--text); cursor: pointer; font: inherit; font-weight: 550;
    }
    .path-picker-overlay {
      position: fixed;
      inset: 0;
      z-index: 1200;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
      background: var(--overlay-scrim);
      backdrop-filter: blur(4px);
      -webkit-backdrop-filter: blur(4px);
    }
    .path-picker-overlay.show { display: flex; }
    .path-picker-dialog {
      width: min(920px, 96vw);
      max-height: 86vh;
      display: flex;
      flex-direction: column;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--surface);
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
      color: var(--text);
    }
    .path-picker-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px 10px;
      border-bottom: 1px solid var(--border);
    }
    .path-picker-head h3 { margin: 0; font-size: 0.95rem; }
    .path-picker-close {
      border: 0;
      background: transparent;
      color: var(--muted);
      font-size: 1.4rem;
      line-height: 1;
      cursor: pointer;
      padding: 0 4px;
    }
    .path-picker-root {
      padding: 8px 16px;
      font-size: 0.7rem;
      color: var(--muted);
    }
    .path-picker-root code { color: var(--accent); font-size: 0.68rem; }
    .path-picker-err {
      margin: 0 16px 8px;
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 0.72rem;
      color: #fecaca;
      background: rgba(80, 20, 28, 0.45);
      border: 1px solid rgba(248, 113, 113, 0.35);
    }
    .path-picker-cascade {
      display: flex;
      gap: 0;
      margin: 0 16px;
      min-height: 220px;
      max-height: 42vh;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--input-bg);
    }
    .path-picker-loading {
      padding: 24px;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .path-picker-col {
      list-style: none;
      margin: 0;
      padding: 6px 0;
      min-width: 180px;
      max-width: 240px;
      border-right: 1px solid var(--border);
      overflow-y: auto;
    }
    .path-picker-col:last-child { border-right: 0; }
    .path-picker-item {
      display: flex;
      align-items: center;
      gap: 6px;
      width: 100%;
      padding: 6px 10px;
      border: 0;
      background: transparent;
      color: var(--text);
      font: inherit;
      font-size: 0.76rem;
      text-align: left;
      cursor: pointer;
    }
    .path-picker-item:hover,
    .path-picker-item.active { background: rgba(61, 214, 198, 0.12); }
    .path-picker-item.active { color: var(--accent); }
    .path-picker-icon { flex-shrink: 0; font-size: 0.72rem; color: var(--muted); width: 1.6rem; }
    .path-picker-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .path-picker-edit {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin: 12px 16px 0;
      font-size: 0.72rem;
      color: var(--muted);
    }
    .path-picker-edit input {
      appearance: none;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      font-size: 0.8rem;
      font-family: ui-monospace, Consolas, monospace;
      padding: 0.4rem 0.65rem;
      border-radius: 8px;
    }
    .path-picker-foot {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      padding: 14px 16px 16px;
    }
    .path-picker-btn {
      border-radius: 8px;
      padding: 8px 14px;
      font: inherit;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .path-picker-btn.ghost {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
    }
    .path-picker-btn.primary {
      background: var(--accent-dim);
      border: 1px solid var(--accent);
      color: var(--text);
      font-weight: 550;
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
      flex-shrink: 0;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      font-size: 0.72rem;
      color: var(--muted);
      font-weight: 600;
    }
    #cam-grid, #infCamGrid {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 0.5rem;
      width: 100%;
    }
    .cam-cell {
      padding: 0.45rem;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 0.3rem;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
    }
    .cam-cell .cam-title {
      font-size: 0.78rem;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .cam-cell .cam-title strong { color: var(--accent); font-weight: 600; }
    .cam-cell .cam-title .k-hz { color: var(--ok); font-variant-numeric: tabular-nums; }
    .cam-cell img {
      width: 100%;
      height: 100%;
      max-height: 100%;
      aspect-ratio: auto;
      object-fit: contain;
      background: var(--input-bg);
      border-radius: 8px;
      border: 1px solid var(--border);
      display: block;
    }
    .cam-cell.empty img { opacity: 0.25; }
    .cam-cell .cam-sub { font-size: 0.72rem; color: var(--muted); font-family: ui-monospace, Consolas, monospace; }
    @media (prefers-reduced-motion: reduce) {
      * { transition: none !important; }
    }
  </style>
</head>
<body class="dcs-page">
  <header>
    <div class="header-brand">
      <img class="header-logo" src="/assets/favicon.svg" alt="" width="36" height="36" />
      <div class="header-text">
      <p class="kicker" data-i18n="header.kicker">Robotics lab console</p>
      <h1>sensors-dcs</h1>
      <p data-i18n="header.subtitle">主页介绍软件；「数据采集」录制写盘；「数据后处理」等价于 export-timeline → filter-timeline → export-hik-dataset。</p>
      </div>
    </div>
    <div class="header-actions">
      <div class="iframe-embed-badge" id="iframeEmbedBadge" hidden title="iframe">
        <span class="iframe-embed-dot" aria-hidden="true"></span>
        <span data-i18n="embed.iframe">iframe</span>
      </div>
      <button type="button" class="settings-gear-btn" id="btnSettings" aria-haspopup="dialog" data-i18n-attr="aria-label" data-i18n="common.settings" aria-label="设置">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" stroke="currentColor" stroke-width="1.8"/>
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
        </svg>
        <span class="settings-gear-label" data-i18n="common.settings">设置</span>
      </button>
      <button type="button" class="danger" id="btnExit" data-i18n="header.exit">安全退出</button>
    </div>
  </header>
  <nav class="tabs" role="tablist">
    <button type="button" class="tab active" id="tabBtnHome" data-tab="home" role="tab" aria-selected="true" data-i18n="tab.home">主页</button>
    <button type="button" class="tab" id="tabBtnCollect" data-tab="collect" role="tab" aria-selected="false" data-i18n="tab.collect">数据采集</button>
    <button type="button" class="tab" id="tabBtnInfer" data-tab="infer" role="tab" aria-selected="false" data-i18n="tab.infer">推理</button>
    <button type="button" class="tab" id="tabBtnPost" data-tab="post" role="tab" aria-selected="false" data-i18n="tab.post">数据后处理</button>
    <button type="button" class="tab" id="tabBtnSensors" data-tab="sensors" role="tab" aria-selected="false" data-i18n="tab.sensors">传感器状态</button>
  </nav>
  <div class="boot-banner" id="bootBanner" role="alert" hidden>
    <strong data-i18n="boot.banner_title">配置错误 — 采集不可用</strong>
    <span id="bootBannerHint" data-i18n="boot.collect_locked">YAML/agent 启动失败，无法进入数据采集。推理与后处理仍可用；请修正配置后重新启动。</span>
    <div class="boot-path" id="bootBannerPath"></div>
    <pre id="bootBannerErr"></pre>
  </div>
  <main>
    <div class="tab-panel active" id="tab-home" role="tabpanel">
      <div class="home-grid">
        <section class="pp-card">
          <h2 data-i18n="home.title">sensors-dcs 是什么</h2>
          <p class="home-lead" data-i18n="home.lead">面向机器人遥操作 / 多传感器单元的本地数据采集与离线后处理控制台：一边把 GELLO、机械臂、夹爪、RealSense 等写进 episode，一边把落盘数据对齐导出成 hik_dataset。</p>
        </section>
        <section class="pp-card">
          <h2 data-i18n="home.what_title">能做什么</h2>
          <p data-i18n="home.what_body">在「数据采集」实时预览并录制；在「数据后处理」按常用三步流水线（export-timeline → filter-timeline → export-hik-dataset）处理已落盘 episode。也可勾选「快速采集」，结束/作废后自动跑同一套参数。</p>
        </section>
        <section class="pp-card">
          <h2 data-i18n="home.flow_title">推荐流程</h2>
          <ol class="home-list">
            <li data-i18n="home.flow_1">用含 sensors_config + agents 的 DCS YAML 启动（不要直接拿 sensors_*.yaml 当启动配置）。</li>
            <li data-i18n="home.flow_2">确认保存路径，在「数据采集」开始 / 结束（或作废）录制一集。</li>
            <li data-i18n="home.flow_3">到「数据后处理」选 episode，核对 align / master / master-hz 与 camera-map，一键三步或逐步执行。</li>
            <li data-i18n="home.flow_4">需要连续采多集时，可打开「异步落盘」，上一集写盘未完也能开下一集。</li>
          </ol>
        </section>
        <section class="pp-card">
          <h2 data-i18n="home.tips_title">使用提示</h2>
          <ul class="home-list">
            <li data-i18n="home.tips_1">dry_run=true 用合成数据联调 UI；真机请设 dry_run=false 并保证驱动 / 串口 / 相机可用。</li>
            <li data-i18n="home.tips_2">YAML 或传感器 open 失败时，本页与后处理仍可用，「数据采集」会被锁定并显示错误横幅。</li>
            <li data-i18n="home.tips_3">主页右上角「设置」可切换语言、退出登录与管理账号；「安全退出」会停录制、关传感器并结束进程。</li>
          </ul>
        </section>
        <section class="pp-card home-cta">
          <h2 data-i18n="home.cta_title">开始使用</h2>
          <p data-i18n="home.cta_body">从下方进入采集或后处理；右上角「设置」可切换语言与管理账号。</p>
          <div class="pp-actions">
            <button type="button" class="primary" id="btnHomeCollect" data-i18n="home.cta_collect">进入数据采集</button>
            <button type="button" id="btnHomeInfer" data-i18n="home.cta_infer">进入推理</button>
            <button type="button" id="btnHomePost" data-i18n="home.cta_post">进入数据后处理</button>
            <button type="button" id="btnHomeSensors" data-i18n="home.cta_sensors">传感器状态</button>
          </div>
        </section>
      </div>
    </div>

    <div class="tab-panel" id="tab-sensors" role="tabpanel">
      <div class="sensors-page" id="sensorsPage">
        <iframe
          class="sensors-embed"
          id="sensorsEmbedFrame"
          title="sensors-view"
          allow="fullscreen; clipboard-read; clipboard-write"
          referrerpolicy="no-referrer-when-downgrade"
          hidden
        ></iframe>
        <div class="sensors-unreachable" id="sensorsUnreachable" role="status" hidden>
          <p class="sensors-unreachable-title" data-i18n="settings.sensors.statusFail">不可达</p>
          <p class="sensors-unreachable-msg" id="sensorsUnreachableMsg" data-i18n="settings.sensors.unreachableHint">地址不可达时，「传感器状态」入口会置灰，无法打开。</p>
          <p class="sensors-unreachable-url" id="sensorsUnreachableUrl"></p>
        </div>
      </div>
    </div>

    <div class="tab-panel" id="tab-collect" role="tabpanel">
    <div class="actions">
      <button type="button" class="primary" id="btnStart" data-i18n="btn.start">开始</button>
      <button type="button" id="btnStop" disabled data-i18n="btn.stop">结束</button>
      <button type="button" class="discard" id="btnDiscard" disabled data-i18n="btn.discard">作废</button>
      <label class="quick-collect" data-i18n-title="quick.title" title="结束或作废后自动执行后处理三步（参数见「数据后处理」Tab）">
        <input type="checkbox" id="chkQuickCollect" />
        <span data-i18n="quick.label">快速采集</span>
      </label>
      <label class="quick-collect" data-i18n-title="async.title" title="结束/作废后后台落盘；未写完也可开始下一集">
        <input type="checkbox" id="chkAsyncFlush" />
        <span data-i18n="async.label">异步落盘</span>
      </label>
      <span class="hint" id="runHint" data-i18n="hint.idle">空闲 — 点「开始」录制当前 episode</span>
    </div>
    <div class="save-path">
      <label for="saveDirInput" data-i18n="save.label">保存路径</label>
      <input type="text" id="saveDirInput" data-i18n-placeholder="save.placeholder" placeholder="留空则沿用当前路径" />
      <button type="button" id="btnSaveDir" data-i18n="btn.apply">应用</button>
    </div>
    <div class="meta">
      <div><span data-i18n="meta.conn">连接：</span><strong id="status" class="st-connecting">connecting…</strong></div>
      <div><span data-i18n="meta.rec">录制：</span><strong id="recState">idle</strong></div>
      <div><span data-i18n="meta.save">保存路径：</span><strong id="saveDir">—</strong></div>
      <div><span data-i18n="meta.episode">episode：</span><strong id="episode">—</strong></div>
      <div><span data-i18n="meta.hz">前端 hz：</span><strong id="hzFront">—</strong></div>
      <div><span data-i18n="meta.written">已写帧：</span><strong id="written">0</strong></div>
    </div>
    <div class="content-row">
      <section class="cam-section">
        <h2 data-i18n="cam.title">Camera preview · 2×2</h2>
        <div id="cam-grid"></div>
      </section>
      <div id="agents"></div>
    </div>
    <pre id="raw">{}</pre>
    </div>

    <div class="tab-panel" id="tab-infer" role="tabpanel">
    <p class="inf-banner" data-i18n="infer.banner">推理页与采集共用录制；可手动下发 joints（单步调试回填 next_state）。</p>
    <div class="inf-pi05-panel" id="infPi05Panel">
      <div class="inf-pi05-row">
        <span class="inf-k" data-i18n="infer.status_label">Serve：</span>
        <strong id="infPi05Status" class="st-offline">未连接</strong>
        <span class="inf-flag" id="infFlagTerm" title="term_flag">
          <span class="inf-dot st-idle" id="infFlagTermDot"></span>
          <span class="inf-flag-lab" data-i18n="infer.flag_term">term</span>
          <span class="inf-flag-val" id="infFlagTermVal" data-i18n="infer.flag_idle">—</span>
        </span>
        <span class="inf-flag" id="infFlagReject" title="reject_flag">
          <span class="inf-dot st-idle" id="infFlagRejectDot"></span>
          <span class="inf-flag-lab" data-i18n="infer.flag_reject">reject</span>
          <span class="inf-flag-val" id="infFlagRejectVal" data-i18n="infer.flag_idle">—</span>
        </span>
        <button type="button" id="infPi05RawBtn" data-i18n="infer.raw_btn">原始数据</button>
      </div>
      <div class="inf-pi05-row">
        <label for="infPi05Host" data-i18n="infer.host">Host</label>
        <input type="text" id="infPi05Host" value="127.0.0.1" autocomplete="off" spellcheck="false" />
        <label for="infPi05Port" data-i18n="infer.port">Port</label>
        <input type="number" id="infPi05Port" value="5000" min="1" max="65535" step="1" />
        <button type="button" class="primary" id="infPi05Connect" data-i18n="infer.connect">连接</button>
        <button type="button" id="infPi05Disconnect" data-i18n="infer.disconnect" disabled>断开</button>
      </div>
      <div class="inf-pi05-row">
        <button type="button" class="primary" id="infPi05Step" data-i18n="infer.step" disabled>单步调试</button>
        <button type="button" id="infPi05Loop" data-i18n="infer.loop" disabled>LOOP</button>
          <label class="inf-loop-rounds" data-i18n-title="infer.loop_rounds_hint" title="大循环轮数 1–1000：每轮=LOOP至terminate→Home；开轮前校验已在 Home">
            <span data-i18n="infer.loop_rounds">轮数</span>
            <input type="number" id="infLoopRounds" min="1" max="1000" step="1" value="1" />
            <span class="inf-loop-round-idx" id="infLoopRoundIdx">—</span>
          </label>
        <label class="inf-auto-home" title="terminate 后自动回 Home">
          <input type="checkbox" id="infAutoHome" />
          <span data-i18n="infer.auto_home">自动复位</span>
        </label>
        <button type="button" id="infArmHome" data-i18n="arm.home">Home</button>
        <label for="infArmJoints" class="arm-abs-label" data-i18n="arm.abs_label" data-i18n-title="infer.joints_tip" title="单步后 IK(next_state→joints) 回填；下发为 joints_rad">joints</label>
        <input type="text" id="infArmJoints" class="inf-arm-joints" data-i18n-placeholder="arm.abs_ph" placeholder="0.00,0.00,0.00,0.00,0.00,0.00" autocomplete="off" spellcheck="false" />
        <button type="button" id="infArmSend" data-i18n="arm.abs_send">下发</button>
        <label class="arm-abs-dur" data-i18n-title="arm.abs_dur_hint" title="1–300（100ms–30s）">
          <span data-i18n="arm.abs_dur">到达</span>
          <input type="number" id="infArmDur" min="1" max="300" step="1" value="100" />
          <span data-i18n="arm.abs_dur_unit">×100ms</span>
          <span id="infArmDurVal">10.0s</span>
        </label>
      </div>
      <div class="inf-pi05-row inf-pi05-hints">
        <span class="hint" id="infArmProg" data-i18n="arm.abs_idle">绝对下发：空闲</span>
      </div>
      <div class="inf-pi05-row">
        <label for="infPi05Prompt" data-i18n="infer.prompt">Prompt</label>
        <input type="text" id="infPi05Prompt" data-i18n-placeholder="infer.prompt_ph" placeholder="任务描述（可空）" autocomplete="off" />
        <button type="button" id="infPi05PromptApply" data-i18n="btn.apply">应用</button>
      </div>
      <div class="inf-pi05-row inf-pi05-hints">
        <span class="hint" id="infPi05Hint" data-i18n="infer.hint_idle">连接后点「单步调试」采集传感器并发给 serve</span>
      </div>
      <pre class="inf-pi05-out" id="infPi05Out" hidden>{}</pre>
    </div>
    <div class="modal-backdrop inf-pi05-raw-modal" id="infPi05RawModal" role="dialog" aria-modal="true" aria-labelledby="infPi05RawTitle">
      <div class="modal-card">
        <h3 id="infPi05RawTitle" data-i18n="infer.raw_title">pi05 原始数据</h3>
        <pre class="modal-body" id="infPi05RawBody">{}</pre>
        <div style="margin-top:0.75rem;display:flex;justify-content:flex-end;">
          <button type="button" class="primary" id="infPi05RawClose" data-i18n="common.done">完成</button>
        </div>
      </div>
    </div>
    <div class="actions">
      <button type="button" class="primary" id="infBtnStart" data-i18n="btn.start">开始</button>
      <button type="button" id="infBtnStop" disabled data-i18n="btn.stop">结束</button>
      <button type="button" class="discard" id="infBtnDiscard" disabled data-i18n="btn.discard">作废</button>
      <label class="quick-collect" data-i18n-title="quick.title" title="结束或作废后自动执行后处理三步（参数见「数据后处理」Tab）">
        <input type="checkbox" id="infChkQuickCollect" />
        <span data-i18n="quick.label">快速采集</span>
      </label>
      <label class="quick-collect" data-i18n-title="async.title" title="结束/作废后后台落盘；未写完也可开始下一集">
        <input type="checkbox" id="infChkAsyncFlush" />
        <span data-i18n="async.label">异步落盘</span>
      </label>
      <span class="hint" id="infRunHint" data-i18n="hint.idle">空闲 — 点「开始」录制当前 episode</span>
    </div>
    <div class="save-path">
      <label for="infSaveDirInput" data-i18n="save.label">保存路径</label>
      <input type="text" id="infSaveDirInput" data-i18n-placeholder="save.placeholder" placeholder="留空则沿用当前路径" />
      <button type="button" id="infBtnSaveDir" data-i18n="btn.apply">应用</button>
    </div>
    <div class="meta">
      <div><span data-i18n="meta.conn">连接：</span><strong id="infStatus" class="st-connecting">connecting…</strong></div>
      <div><span data-i18n="meta.rec">录制：</span><strong id="infRecState">idle</strong></div>
      <div><span data-i18n="meta.save">保存路径：</span><strong id="infSaveDir">—</strong></div>
      <div><span data-i18n="meta.episode">episode：</span><strong id="infEpisode">—</strong></div>
      <div><span data-i18n="meta.hz">前端 hz：</span><strong id="infHzFront">—</strong></div>
      <div><span data-i18n="meta.written">已写帧：</span><strong id="infWritten">0</strong></div>
    </div>
    <div class="content-row">
      <section class="cam-section">
        <h2 data-i18n="cam.title">Camera preview · 2×2</h2>
        <div id="infCamGrid"></div>
      </section>
      <div id="infAgents"></div>
    </div>
    <pre id="infRaw">{}</pre>
    </div>


    <div class="tab-panel" id="tab-post" role="tabpanel">
      <div class="pp-grid">
        <section class="pp-card">
          <h2 data-i18n="pp.episode">Episode</h2>
          <p class="pp-hint" data-i18n="pp.episode_hint">选择已落盘目录，或粘贴完整路径（如 D:\\data_new\\episode_00016）。</p>
          <div class="pp-row">
            <label for="ppEpisodeSelect" data-i18n="pp.list">列表</label>
            <select id="ppEpisodeSelect"></select>
            <button type="button" id="btnPpRefresh" data-i18n="btn.refresh">刷新</button>
          </div>
          <div class="pp-row">
            <label for="ppEpisode" data-i18n="pp.path">路径</label>
            <input type="text" class="wide" id="ppEpisode" placeholder="D:\\data_new\\episode_00016" />
            <button type="button" id="btnPpBrowseEpisode" data-i18n="pp.browse">浏览…</button>
          </div>
          <div class="pp-row">
            <label class="quick-collect"><input type="checkbox" id="ppAllowInvalid" /> <span data-i18n="pp.allow_invalid">allow-invalid（作废 episode 也导出）</span></label>
          </div>
        </section>

        <section class="pp-card">
          <h2 data-i18n="pp.align_title">对齐参数（Step 1 → hik）</h2>
          <p class="pp-hint" data-i18n="pp.align_hint">master-hz 作用于 export-timeline 下采样；一键三步 / 快速采集转 hik_dataset 时都读这里，不是写死 5。</p>
          <div class="pp-row">
            <label for="ppAlign">align</label>
            <select id="ppAlign">
              <option value="asof" selected>asof</option>
              <option value="nearest">nearest</option>
              <option value="grid">grid</option>
              <option value="union">union</option>
            </select>
            <label for="ppMaster">master</label>
            <select id="ppMaster">
              <option value="" data-i18n="pp.master_pick">先选择合格 episode…</option>
            </select>
            <label for="ppMasterHz">master-hz</label>
            <input type="number" id="ppMasterHz" value="5" step="0.1" min="0.1" />
          </div>
        </section>

        <section class="pp-card">
          <h2 data-i18n="pp.step1">1 · export-timeline</h2>
          <p class="pp-hint" data-i18n="pp.step1_hint">sensors-dcs export-timeline -e … --align / --master / --master-hz（见上方对齐参数）</p>
          <div class="pp-actions">
            <button type="button" id="btnPpExport" data-i18n="pp.run1">运行 Step 1</button>
          </div>
        </section>

        <section class="pp-card">
          <h2 data-i18n="pp.step2">2 · filter-timeline</h2>
          <p class="pp-hint" data-i18n="pp.step2_hint">--require arm,cam-left,cam-right,cam-middle,gripper-read --max-match-dt 0.033 --trim both --materialize</p>
          <div class="pp-row">
            <label for="ppRequire">require</label>
            <input type="text" class="wide" id="ppRequire" value="arm,cam-left,cam-right,cam-middle,gripper-read" />
          </div>
          <div class="pp-row">
            <label for="ppMaxDt">max-match-dt</label>
            <input type="text" id="ppMaxDt" value="0.033" />
            <label for="ppTrim">trim</label>
            <select id="ppTrim">
              <option value="both" selected>both</option>
              <option value="start">start</option>
              <option value="end">end</option>
              <option value="none">none</option>
            </select>
            <label class="quick-collect"><input type="checkbox" id="ppMaterialize" checked /> materialize</label>
          </div>
          <div class="pp-actions">
            <button type="button" id="btnPpFilter" data-i18n="pp.run2">运行 Step 2</button>
          </div>
        </section>

        <section class="pp-card">
          <h2 data-i18n="pp.step3">3 · export-hik-dataset</h2>
          <p class="pp-hint" data-i18n="pp.step3_hint">--camera-map …\\hik_camera_map.yaml；若尚未对齐，请先跑 Step1（master-hz 见上方）或一键三步</p>
          <div class="pp-row">
            <label for="ppCameraMap">camera-map</label>
            <input type="text" class="wide" id="ppCameraMap" data-i18n-placeholder="pp.camera_map_ph" placeholder="路径到 hik_camera_map.yaml" />
            <button type="button" id="btnPpBrowseCameraMap" data-i18n="pp.browse">浏览…</button>
          </div>
          <div class="pp-actions">
            <button type="button" id="btnPpHik" data-i18n="pp.run3">运行 Step 3</button>
          </div>
        </section>

        <section class="pp-card">
          <h2 data-i18n="pp.run_all_title">一键三步</h2>
          <p class="pp-hint" data-i18n="pp.run_all_hint">顺序执行上述三步；「快速采集」勾选后结束/作废也会走同一套参数。</p>
          <div class="pp-actions">
            <button type="button" class="primary" id="btnPpRunAll" data-i18n="pp.run_all">一键执行三步</button>
            <span class="hint" id="ppHint"></span>
          </div>
          <pre id="ppLog" data-i18n="pp.log_idle">（尚未运行）</pre>
        </section>
      </div>
    </div>
  </main>
  <div class="modal-backdrop" id="appModal" role="dialog" aria-modal="true">
    <div class="modal-card">
      <h3 id="appModalTitle" data-i18n="modal.default_title">提示</h3>
      <p id="appModalBody"></p>
      <button type="button" id="appModalOk" data-i18n="modal.ok">知道了</button>
    </div>
  </div>
  <div class="settings-overlay" id="settingsModal" role="presentation">
    <div class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settingsTitle" id="settingsDialog">
      <header class="settings-head">
        <div>
          <p class="settings-kicker" data-i18n="settings.kicker">Preferences</p>
          <h2 id="settingsTitle" data-i18n="settings.title">设置</h2>
        </div>
        <button type="button" class="settings-close" id="btnSettingsClose" data-i18n-attr="aria-label" data-i18n="common.closeSettings" aria-label="关闭设置">×</button>
      </header>
      <div class="settings-body">
        <nav class="settings-nav" role="tablist" data-i18n-attr="aria-label" data-i18n="settings.navAria" aria-label="设置分类">
          <button type="button" class="settings-nav-item active" id="settingsNavAppearance" data-settings-tab="appearance" aria-current="page">
            <span class="settings-nav-label" data-i18n="settings.tabs.appearance.label">外观</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.appearance.hint">主题与界面密度</span>
          </button>
          <button type="button" class="settings-nav-item" id="settingsNavLanguage" data-settings-tab="language">
            <span class="settings-nav-label" data-i18n="settings.tabs.language.label">语言</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.language.hint">界面中英</span>
          </button>
          <button type="button" class="settings-nav-item" id="settingsNavSensors" data-settings-tab="sensors">
            <span class="settings-nav-label" data-i18n="settings.tabs.sensors.label">传感器</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.sensors.hint">sensors-view 嵌入地址</span>
          </button>
          <button type="button" class="settings-nav-item" id="settingsNavAuth" data-settings-tab="auth">
            <span class="settings-nav-label" data-i18n="settings.tabs.auth.label">鉴权</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.auth.hint">会话与退出</span>
          </button>
          <button type="button" class="settings-nav-item" id="settingsNavConfig" data-settings-tab="config">
            <span class="settings-nav-label" data-i18n="settings.tabs.config.label">配置文件</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.config.hint">DCS YAML</span>
          </button>
          <button type="button" class="settings-nav-item" id="settingsNavUsers" data-settings-tab="users" hidden>
            <span class="settings-nav-label" data-i18n="settings.tabs.users.label">用户</span>
            <span class="settings-nav-hint" data-i18n="settings.tabs.users.hint">本地账号</span>
          </button>
        </nav>
        <div class="settings-panel active" id="settingsPanelAppearance" data-settings-panel="appearance" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.appearance.label">外观</h3>
          <div class="settings-row">
            <div class="settings-row-text">
              <div class="settings-row-title">
                <span data-i18n="settings.appearance.theme">主题</span>
                <span class="settings-badge" data-i18n="common.live">实时</span>
              </div>
              <p class="settings-row-desc" data-i18n="settings.appearance.themeDesc">跟随系统，或强制深色 / 浅色。偏好保存在本机，立即生效。</p>
            </div>
            <div class="settings-row-control">
              <div class="settings-seg" id="settingsThemeSeg" role="group" data-i18n-attr="aria-label" data-i18n="settings.appearance.theme" aria-label="主题">
                <button type="button" class="settings-seg-btn" data-theme-pref="system" data-i18n="settings.appearance.themeSystem">跟随系统</button>
                <button type="button" class="settings-seg-btn active" data-theme-pref="dark" data-i18n="settings.appearance.themeDark">深色</button>
                <button type="button" class="settings-seg-btn" data-theme-pref="light" data-i18n="settings.appearance.themeLight">浅色</button>
              </div>
            </div>
          </div>
          <div class="settings-row">
            <div class="settings-row-text">
              <div class="settings-row-title">
                <span data-i18n="settings.appearance.compact">紧凑布局</span>
                <span class="settings-badge" data-i18n="common.live">实时</span>
              </div>
              <p class="settings-row-desc" data-i18n="settings.appearance.compactDesc">缩小顶栏间距，适合小屏或密集操作。</p>
            </div>
            <div class="settings-row-control">
              <button type="button" class="settings-toggle" id="settingsCompactToggle" data-i18n-attr="aria-label" data-i18n="settings.appearance.compact" aria-label="紧凑布局" aria-pressed="false">
                <span class="settings-toggle-knob"></span>
              </button>
            </div>
          </div>
          <div class="settings-row">
            <div class="settings-row-text">
              <div class="settings-row-title">
                <span data-i18n="settings.appearance.density">界面密度</span>
                <span class="settings-badge" data-i18n="common.live">实时</span>
              </div>
              <p class="settings-row-desc" data-i18n="settings.appearance.densityDesc">调整字号与间距（舒适 / 紧凑 / 密集）。</p>
            </div>
            <div class="settings-row-control">
              <div class="settings-seg" id="settingsDensitySeg" role="group" data-i18n-attr="aria-label" data-i18n="settings.appearance.density" aria-label="界面密度">
                <button type="button" class="settings-seg-btn active" data-density-pref="comfortable" data-i18n="settings.appearance.densityComfortable">舒适</button>
                <button type="button" class="settings-seg-btn" data-density-pref="compact" data-i18n="settings.appearance.densityCompact">紧凑</button>
                <button type="button" class="settings-seg-btn" data-density-pref="dense" data-i18n="settings.appearance.densityDense">密集</button>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-panel" id="settingsPanelLanguage" data-settings-panel="language" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.language.label">语言</h3>
          <div class="settings-row">
            <div class="settings-row-text">
              <div class="settings-row-title">
                <span data-i18n="settings.lang_label">界面语言</span>
                <span class="settings-badge" data-i18n="common.live">实时</span>
              </div>
              <p class="settings-row-desc" data-i18n="settings.lang_desc">立即切换主界面与设置文案；写入本机 localStorage。</p>
            </div>
            <div class="settings-row-control">
              <div class="settings-seg" role="group" data-i18n-attr="aria-label" data-i18n="settings.lang_label" aria-label="界面语言">
                <button type="button" class="settings-seg-btn active" data-locale="zh" data-i18n="lang.zh">中文</button>
                <button type="button" class="settings-seg-btn" data-locale="en" data-i18n="lang.en">EN</button>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-panel" id="settingsPanelSensors" data-settings-panel="sensors" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.sensors.label">传感器</h3>
          <div class="settings-row settings-row-stack">
            <div class="settings-row-text">
              <div class="settings-row-title">
                <span data-i18n="settings.sensors.url">sensors-view 地址</span>
                <span class="settings-badge" data-i18n="common.live">实时</span>
              </div>
              <p class="settings-row-desc" data-i18n="settings.sensors.urlDesc">「传感器状态」页通过 iframe 嵌入此 URL。修改后自动写入本机，并立即探测可达性；不可达时导航入口置灰。</p>
            </div>
            <div class="settings-row-control">
              <div class="settings-sensors-control">
                <input type="url" id="settingsSensorsUrl" class="settings-users-input" data-i18n-placeholder="settings.sensors.urlPlaceholder" placeholder="https://host:8443/" spellcheck="false" autocomplete="off" />
                <div class="settings-sensors-meta">
                  <span class="settings-sensors-status" id="settingsSensorsStatus"></span>
                  <button type="button" class="settings-ghost-btn settings-sensors-ping" id="btnSettingsSensorsPing" data-i18n="settings.sensors.ping">重新探测</button>
                </div>
                <p class="settings-sensors-saved" id="settingsSensorsSaved" hidden data-i18n="settings.sensors.saved">已保存到本机</p>
                <p class="settings-sensors-hint" data-i18n="settings.sensors.unreachableHint">地址不可达时，「传感器状态」入口会置灰，无法打开。</p>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-panel" id="settingsPanelAuth" data-settings-panel="auth" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.auth.label">鉴权</h3>
          <p class="hint" data-i18n="settings.auth_hint">Cookie 会话鉴权（HttpOnly）。关闭鉴权请设环境变量 SENSORS_DCS_AUTH_DISABLED=1。</p>
          <div class="settings-kv">
            <div><span data-i18n="settings.auth_status">状态</span><strong id="settingsAuthStatus">—</strong></div>
            <div><span data-i18n="settings.auth_user">当前用户</span><strong id="settingsAuthUser">—</strong></div>
            <div><span data-i18n="settings.auth_role">角色</span><strong id="settingsAuthRole">—</strong></div>
          </div>
          <div class="settings-actions">
            <button type="button" class="settings-ghost-btn" id="btnSettingsLogout" data-i18n="settings.logout">退出登录</button>
            <button type="button" class="settings-ghost-btn" id="btnSettingsRefreshAuth" data-i18n="settings.refresh">刷新</button>
          </div>
          <p class="settings-msg" id="settingsAuthMsg"></p>
        </div>
        <div class="settings-panel" id="settingsPanelConfig" data-settings-panel="config" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.config.label">配置文件</h3>
          <p class="hint" data-i18n="settings.config_hint">选择 DCS 启动 YAML（需含 sensors_config 与 agents）。确认后将写入活动配置并重启前后端。</p>
          <div class="settings-kv">
            <div><span data-i18n="settings.config_current">当前配置</span><strong id="settingsConfigCurrent">—</strong></div>
          </div>
          <div class="settings-config-row">
            <input type="text" id="settingsConfigPath" data-i18n-placeholder="settings.config_path_ph" placeholder="DCS YAML 绝对路径" autocomplete="off" />
            <button type="button" class="settings-ghost-btn" id="btnSettingsBrowseConfig" data-i18n="settings.config_browse">浏览…</button>
            <button type="button" class="settings-primary-btn" id="btnSettingsApplyConfig" data-i18n="settings.config_apply">确认并重启</button>
          </div>
          <p class="settings-msg" id="settingsConfigMsg"></p>
          <h3 class="settings-panel-title" style="margin-top:1rem;" data-i18n="settings.home_title">Home 关节角</h3>
          <p class="hint" data-i18n="settings.home_hint">与 YAML home_joints_rad / home_duration_s /「设为 home」同步；回 Home 用独立时长，不与步进「到达」共用。</p>
          <div class="settings-config-row">
            <input type="text" id="settingsHomeJoints" data-i18n-placeholder="arm.abs_ph" placeholder="0.00,0.00,0.00,0.00,0.00,0.00" autocomplete="off" spellcheck="false" style="flex:1;min-width:12rem;" />
          </div>
          <div class="settings-config-row" style="margin-top:0.45rem;">
            <label class="arm-abs-dur" data-i18n-title="settings.home_dur_hint" title="回 Home 到位时间 1–300×100ms（独立于步进）">
              <span data-i18n="settings.home_dur">回 Home</span>
              <input type="number" id="settingsHomeDur" min="1" max="300" step="1" value="200" />
              <span data-i18n="arm.abs_dur_unit">×100ms</span>
              <span id="settingsHomeDurVal">20.0s</span>
            </label>
            <button type="button" class="settings-ghost-btn" id="btnSettingsHomeReload" data-i18n="settings.home_reload">同步</button>
            <button type="button" class="settings-primary-btn" id="btnSettingsHomeSave" data-i18n="settings.home_save">更新到 YAML</button>
          </div>
          <p class="settings-msg" id="settingsHomeMsg"></p>
        </div>
        <div class="settings-panel" id="settingsPanelUsers" data-settings-panel="users" role="tabpanel">
          <h3 class="settings-panel-title" data-i18n="settings.tabs.users.label">用户</h3>
          <p class="hint" data-i18n="settings.users_hint">仅管理员可管理本地账号（写入用户数据目录 configs/users.json，勿提交仓库）。</p>
          <div id="settingsUsersNonAdmin" hidden>
            <p class="hint" data-i18n="settings.users_need_admin">当前账号无管理员权限。</p>
          </div>
          <div id="settingsUsersAdmin">
            <table class="settings-users-table">
              <thead>
                <tr>
                  <th data-i18n="settings.col_user">用户</th>
                  <th data-i18n="settings.col_role">角色</th>
                  <th data-i18n="settings.col_enabled">启用</th>
                  <th data-i18n="settings.col_password">新密码</th>
                  <th data-i18n="settings.col_actions">操作</th>
                </tr>
              </thead>
              <tbody id="settingsUsersBody"></tbody>
            </table>
            <div class="settings-add">
              <strong data-i18n="settings.invite">添加用户</strong>
              <p class="hint" data-i18n="settings.invite_desc">创建本地账号并写入 users.json。</p>
              <div class="settings-add-grid">
                <input type="text" id="settingsNewUser" data-i18n-placeholder="settings.new_user_ph" placeholder="用户名" autocomplete="off" />
                <input type="password" id="settingsNewPass" data-i18n-placeholder="settings.new_pass_ph" placeholder="密码" autocomplete="new-password" />
                <select id="settingsNewRole">
                  <option value="operator" data-i18n="settings.role_operator">操作员</option>
                  <option value="admin" data-i18n="settings.role_admin">管理员</option>
                  <option value="guest" data-i18n="settings.role_guest">访客</option>
                </select>
                <button type="button" class="settings-primary-btn" id="btnSettingsAddUser" data-i18n="settings.add_btn">添加</button>
              </div>
            </div>
          </div>
          <p class="settings-msg" id="settingsUsersMsg"></p>
        </div>
      </div>
      <footer class="settings-foot">
        <span class="muted" data-i18n="common.escHint">按 Esc 关闭</span>
        <button type="button" class="settings-primary-btn" id="btnSettingsDone" data-i18n="common.done">完成</button>
      </footer>
    </div>
  </div>
  <div class="path-picker-overlay" id="pathPickerOverlay" role="presentation">
    <div class="path-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="pathPickerTitle" id="pathPickerDialog">
      <header class="path-picker-head">
        <h3 id="pathPickerTitle" data-i18n="pathPicker.title">选择配置文件</h3>
        <button type="button" class="path-picker-close" id="pathPickerClose" data-i18n-attr="aria-label" data-i18n="pathPicker.closeAria" aria-label="关闭">×</button>
      </header>
      <div class="path-picker-root"><span data-i18n="pathPicker.root">根目录：</span><code id="pathPickerRootCode"></code></div>
      <div class="path-picker-err" id="pathPickerErr" hidden></div>
      <div class="path-picker-cascade" id="pathPickerCascade" data-i18n-attr="aria-label" data-i18n="pathPicker.cascade" aria-label="路径级联选择"></div>
      <label class="path-picker-edit">
        <span data-i18n="pathPicker.pathEdit">路径（可手动修改）</span>
        <input type="text" id="pathPickerDraft" data-i18n-placeholder="pathPicker.filePlaceholder" placeholder="文件绝对路径" />
      </label>
      <footer class="path-picker-foot">
        <button type="button" class="path-picker-btn ghost" id="pathPickerCancel" data-i18n="pathPicker.cancel">取消</button>
        <button type="button" class="path-picker-btn primary" id="pathPickerConfirm" data-i18n="pathPicker.confirm">确认</button>
      </footer>
    </div>
  </div>
  <script>
    const DCS_I18N = __DCS_I18N_JSON__;
    const LS_LOCALE = 'sensors-dcs.locale';
    let currentLocale = 'zh';
    try {
      const stored = localStorage.getItem(LS_LOCALE);
      if (stored === 'zh' || stored === 'en') currentLocale = stored;
    } catch (e) {}
    function formatMessage(raw, vars) {
      if (!vars) return raw;
      return String(raw).replace(/[{](\\w+)[}]/g, (_, k) =>
        (vars[k] == null ? '{' + k + '}' : String(vars[k])));
    }
    function t(path, vars) {
      const table = DCS_I18N[currentLocale] || DCS_I18N.zh || {};
      const fallback = DCS_I18N.zh || {};
      const raw = (table[path] != null ? table[path] : fallback[path]);
      return formatMessage(raw != null ? raw : path, vars);
    }
    function applyDomI18n(root) {
      const scope = root || document;
      scope.querySelectorAll('[data-i18n]').forEach((el) => {
        const key = el.getAttribute('data-i18n');
        if (!key) return;
        const attr = el.getAttribute('data-i18n-attr');
        const textVal = t(key);
        if (attr) el.setAttribute(attr, textVal);
        else if (el.hasAttribute('data-i18n-html')) el.innerHTML = textVal;
        else el.textContent = textVal;
      });
      scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
        const key = el.getAttribute('data-i18n-title');
        if (key) el.setAttribute('title', t(key));
      });
      scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (key) el.setAttribute('placeholder', t(key));
      });
      document.title = t('meta.title');
    }
    function setLocale(loc, opts) {
      if (loc !== 'zh' && loc !== 'en') return;
      const persist = !opts || opts.persist !== false;
      currentLocale = loc;
      if (persist) {
        try { localStorage.setItem(LS_LOCALE, loc); } catch (e) {}
      }
      document.documentElement.lang = loc === 'zh' ? 'zh-CN' : 'en';
      document.querySelectorAll('.settings-seg-btn[data-locale]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-locale') === loc);
      });
      applyDomI18n(document);
      try {
        if (typeof applyRecordUi === 'function' && window.__lastRecordStatus) {
          applyRecordUi(window.__lastRecordStatus);
        }
      } catch (e) {}
      try {
        const b = document.getElementById('btnHomeCollect');
        const tc = document.getElementById('tabBtnCollect');
        if (b && b.disabled) b.title = t('boot.collect_title');
        if (tc && tc.disabled) tc.title = t('boot.collect_title');
      } catch (e) {}
      try {
        if (typeof pushSensorsEmbedPrefs === 'function') pushSensorsEmbedPrefs();
        if (typeof syncSensorsEmbedStatusUi === 'function') syncSensorsEmbedStatusUi();
        if (typeof applySensorsGate === 'function') applySensorsGate();
      } catch (e) {}
    }
    document.querySelectorAll('.settings-seg-btn[data-locale]').forEach((btn) => {
      btn.addEventListener('click', () => setLocale(btn.getAttribute('data-locale')));
    });
    setLocale(currentLocale);

    const agentsEl = document.getElementById('agents');
    const camGridEl = document.getElementById('cam-grid');
    const infAgentsEl = document.getElementById('infAgents');
    const infCamGridEl = document.getElementById('infCamGrid');
    const statusEl = document.getElementById('status');
    const infStatusEl = document.getElementById('infStatus');
    let exitRequested = false;
    function setConnStatus(text, cls) {
      [statusEl, infStatusEl].forEach((el) => {
        if (!el) return;
        el.textContent = text;
        el.className = cls || '';
      });
      // After「安全退出」, WS reconnect shows connecting — force a full page reload.
      if (exitRequested && cls === 'st-connecting') {
        try {
          location.reload();
        } catch (e) {}
      }
    }
    const appModal = document.getElementById('appModal');
    const appModalTitle = document.getElementById('appModalTitle');
    const appModalBody = document.getElementById('appModalBody');
    const appModalOk = document.getElementById('appModalOk');
    function showAppModal(title, body) {
      appModalTitle.textContent = title || t('modal.default_title');
      appModalBody.textContent = body || '';
      appModal.classList.add('show');
    }
    window.__armHome = window.__armHome || { configured: false, home_joints_rad: null, home_duration_s: 20 };
    function formatHomeJointsCsv(joints) {
      if (!Array.isArray(joints) || joints.length < 6) return '';
      return joints.slice(0, 6).map((v) => Number(v).toFixed(4)).join(',');
    }
    function homeDurationHundredMsFromState(st) {
      const sec = Number((st && st.home_duration_s) != null ? st.home_duration_s : 20);
      if (!Number.isFinite(sec)) return 200;
      return Math.max(1, Math.min(300, Math.round(sec * 10)));
    }
    function homeDurationSecondsFromUi() {
      const inp = document.getElementById('settingsHomeDur');
      let n = inp ? Math.round(Number(inp.value)) : homeDurationHundredMsFromState(window.__armHome);
      if (!Number.isFinite(n)) n = 200;
      n = Math.max(1, Math.min(300, n));
      return n * 0.1;
    }
    function updateSettingsHomeDurLabel() {
      const inp = document.getElementById('settingsHomeDur');
      const lab = document.getElementById('settingsHomeDurVal');
      if (!inp) return;
      let n = Math.round(Number(inp.value));
      if (!Number.isFinite(n)) n = 200;
      n = Math.max(1, Math.min(300, n));
      if (String(inp.value) !== String(n)) inp.value = String(n);
      if (lab) lab.textContent = (n * 0.1).toFixed(1) + 's';
    }
    function syncSettingsHomeFromState(st) {
      const inp = document.getElementById('settingsHomeJoints');
      if (inp && document.activeElement !== inp) {
        const joints = (st && st.home_joints_rad) || null;
        inp.value = formatHomeJointsCsv(joints);
      }
      const durInp = document.getElementById('settingsHomeDur');
      if (durInp && document.activeElement !== durInp) {
        durInp.value = String(homeDurationHundredMsFromState(st));
        updateSettingsHomeDurLabel();
      }
    }
    async function refreshArmHomeState() {
      try {
        const r = await fetch('/api/arm/home', { cache: 'no-store' }).then((x) => x.json());
        window.__armHome = r;
        syncSettingsHomeFromState(r);
        return r;
      } catch (e) {
        return window.__armHome || {};
      }
    }
    async function goArmHomeWithDuration(duration_s, progEl) {
      const st0 = window.__armHome || {};
      if (!st0.configured || !Array.isArray(st0.home_joints_rad) || st0.home_joints_rad.length < 6) {
        const live = await refreshArmHomeState();
        if (!live.configured) {
          showAppModal(t('arm.home_bad_title'), live.error || t('arm.home_missing'));
          if (progEl) progEl.textContent = live.error || t('arm.home_missing');
          return { ok: false, error: live.error || t('arm.home_missing') };
        }
      }
      // Prefer dedicated home duration; never fall back to Infer step abs duration.
      let dur = duration_s;
      if (dur == null || !Number.isFinite(Number(dur))) {
        const st = window.__armHome || {};
        dur = Number(st.home_duration_s);
        if (!Number.isFinite(dur)) dur = homeDurationSecondsFromUi();
      }
      dur = Math.max(0.1, Math.min(30, Number(dur)));
      const agentId = window.__armWriteAgentId || null;
      try {
        const r = await fetch('/api/arm/home/go', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agent_id: agentId,
            duration_s: dur,
          }),
        }).then((x) => x.json());
        if (r && r.home) {
          window.__armHome = r.home;
          syncSettingsHomeFromState(r.home);
        }
        const usedDur = (r && r.duration_s != null) ? Number(r.duration_s) : dur;
        if (!r.ok) {
          showAppModal(t('arm.home_bad_title'), r.error || JSON.stringify(r));
          if (progEl) progEl.textContent = t('arm.home_fail', { error: r.error || JSON.stringify(r) });
        } else {
          if (progEl) {
            progEl.textContent = t('arm.home_ok', { dur: Number(usedDur).toFixed(1) });
          }
          if (r) window.__armAbsRamp = r;
        }
        return r;
      } catch (e) {
        showAppModal(t('arm.home_bad_title'), String(e));
        if (progEl) progEl.textContent = t('arm.home_fail', { error: e });
        return { ok: false, error: String(e) };
      }
    }
    appModalOk.addEventListener('click', () => appModal.classList.remove('show'));
    appModal.addEventListener('click', (e) => {
      if (e.target === appModal) appModal.classList.remove('show');
    });
    function bindRecordPanel(ids, lsQuick, lsAsync, mode) {
      return {
        recStateEl: document.getElementById(ids.recState),
        saveDirEl: document.getElementById(ids.saveDir),
        episodeEl: document.getElementById(ids.episode),
        writtenEl: document.getElementById(ids.written),
        hzFrontEl: document.getElementById(ids.hzFront),
        rawEl: document.getElementById(ids.raw),
        btnStart: document.getElementById(ids.btnStart),
        btnStop: document.getElementById(ids.btnStop),
        btnDiscard: document.getElementById(ids.btnDiscard),
        btnSaveDir: document.getElementById(ids.btnSaveDir),
        saveDirInput: document.getElementById(ids.saveDirInput),
        runHint: document.getElementById(ids.runHint),
        chkQuickCollect: document.getElementById(ids.chkQuickCollect),
        chkAsyncFlush: document.getElementById(ids.chkAsyncFlush),
        lsQuick: lsQuick,
        lsAsync: lsAsync,
        mode: mode || 'collect',
      };
    }
    const collectRec = bindRecordPanel({
      recState: 'recState', saveDir: 'saveDir', episode: 'episode', written: 'written',
      hzFront: 'hzFront', raw: 'raw',
      btnStart: 'btnStart', btnStop: 'btnStop', btnDiscard: 'btnDiscard',
      btnSaveDir: 'btnSaveDir', saveDirInput: 'saveDirInput', runHint: 'runHint',
      chkQuickCollect: 'chkQuickCollect', chkAsyncFlush: 'chkAsyncFlush',
    }, 'dcs.quickCollect', 'dcs.asyncFlush', 'collect');
    const inferRec = bindRecordPanel({
      recState: 'infRecState', saveDir: 'infSaveDir', episode: 'infEpisode', written: 'infWritten',
      hzFront: 'infHzFront', raw: 'infRaw',
      btnStart: 'infBtnStart', btnStop: 'infBtnStop', btnDiscard: 'infBtnDiscard',
      btnSaveDir: 'infBtnSaveDir', saveDirInput: 'infSaveDirInput', runHint: 'infRunHint',
      chkQuickCollect: 'infChkQuickCollect', chkAsyncFlush: 'infChkAsyncFlush',
    }, 'dcs.inf.quickCollect', 'dcs.inf.asyncFlush', 'infer');
    const recordPanels = [collectRec, inferRec].filter((p) => p && p.btnStart);
    // Legacy aliases (collect) used by exit / arm hints elsewhere.
    const recStateEl = collectRec.recStateEl;
    const saveDirEl = collectRec.saveDirEl;
    const episodeEl = collectRec.episodeEl;
    const writtenEl = collectRec.writtenEl;
    const hzFrontEl = collectRec.hzFrontEl;
    const rawEl = collectRec.rawEl;
    const btnStart = collectRec.btnStart;
    const btnStop = collectRec.btnStop;
    const btnDiscard = collectRec.btnDiscard;
    const btnSaveDir = collectRec.btnSaveDir;
    const saveDirInput = collectRec.saveDirInput;
    const runHint = collectRec.runHint;
    const chkQuickCollect = collectRec.chkQuickCollect;
    const chkAsyncFlush = collectRec.chkAsyncFlush;
    const LS_QUICK = collectRec.lsQuick;
    const LS_ASYNC = collectRec.lsAsync;
    const LS_PP = 'dcs.postprocess';
    function wireRecordChecks(panel) {
      if (!panel) return;
      try {
        if (panel.chkQuickCollect) {
          panel.chkQuickCollect.checked = localStorage.getItem(panel.lsQuick) === '1';
          panel.chkQuickCollect.addEventListener('change', () => {
            try { localStorage.setItem(panel.lsQuick, panel.chkQuickCollect.checked ? '1' : '0'); } catch (e) {}
          });
        }
        if (panel.chkAsyncFlush) {
          if (localStorage.getItem(panel.lsAsync) === '1') panel.chkAsyncFlush.checked = true;
          panel.chkAsyncFlush.addEventListener('change', () => {
            try { localStorage.setItem(panel.lsAsync, panel.chkAsyncFlush.checked ? '1' : '0'); } catch (e) {}
          });
        }
      } catch (e) {}
    }
    recordPanels.forEach(wireRecordChecks);
    let lastMsgT = null, emaFront = null;
    let busy = false;
    const backState = {};
    const CAM_SLOTS = [
      { key: 'left', label: 'Left' },
      { key: 'right', label: 'Right' },
      { key: 'middle', label: 'Middle' },
      { key: 'wrist', label: 'Wrist' },
    ];
    const camCellsByScope = { collect: {}, infer: {} };

    function initCamGridInto(gridEl, scope) {
      if (!gridEl) return;
      const cells = camCellsByScope[scope];
      gridEl.innerHTML = '';
      CAM_SLOTS.forEach((slot) => {
        const cell = document.createElement('div');
        cell.className = 'cam-cell empty';
        cell.id = (scope === 'infer' ? 'inf-cam-slot-' : 'cam-slot-') + slot.key;
        cell.innerHTML =
          '<div class="cam-title">' +
            '<span>' + slot.label + '</span>' +
            '<strong class="k-agent">—</strong>' +
            '<strong class="k-hz">— Hz</strong>' +
          '</div>' +
          '<img alt="' + slot.label + '" />' +
          '<div class="cam-sub k-sub">empty</div>';
        gridEl.appendChild(cell);
        cells[slot.key] = cell;
      });
    }
    initCamGridInto(camGridEl, 'collect');
    initCamGridInto(infCamGridEl, 'infer');
    // Back-compat alias used by renderCamSlot helpers that still look at camCells.
    const camCells = camCellsByScope.collect;

    function applyRecordUiToPanel(panel, rec) {
      if (!panel || !rec) return;
      const st = rec.state || 'idle';
      const flushN = rec.flushing_count || 0;
      if (panel.recStateEl) {
        panel.recStateEl.textContent = flushN > 0 && st === 'idle'
          ? (st + ' · flush×' + flushN)
          : st;
      }
      if (panel.saveDirEl) panel.saveDirEl.textContent = rec.save_dir || '—';
      if (panel.saveDirInput && document.activeElement !== panel.saveDirInput) {
        panel.saveDirInput.placeholder = rec.save_dir || t('save.placeholder');
      }
      if (panel.episodeEl) {
        panel.episodeEl.textContent = (rec.episode_index == null) ? '—' : String(rec.episode_index);
      }
      if (panel.writtenEl) {
        panel.writtenEl.textContent = String(rec.written == null ? 0 : rec.written);
      }
      const recBusy = st === 'recording' || st === 'flushing';
      if (panel.saveDirInput) panel.saveDirInput.disabled = recBusy;
      if (panel.btnSaveDir) panel.btnSaveDir.disabled = recBusy;
      if (busy) return;
      if (st === 'recording') {
        if (panel.btnStart) panel.btnStart.disabled = true;
        if (panel.btnStop) panel.btnStop.disabled = false;
        if (panel.btnDiscard) panel.btnDiscard.disabled = false;
        if (panel.runHint) panel.runHint.textContent = t('hint.recording');
      } else if (st === 'flushing') {
        if (panel.btnStart) panel.btnStart.disabled = true;
        if (panel.btnStop) panel.btnStop.disabled = true;
        if (panel.btnDiscard) panel.btnDiscard.disabled = true;
        if (panel.runHint) panel.runHint.textContent = t('hint.flushing');
      } else {
        if (panel.btnStart) panel.btnStart.disabled = false;
        if (panel.btnStop) panel.btnStop.disabled = true;
        if (panel.btnDiscard) panel.btnDiscard.disabled = true;
        if (panel.runHint) {
          if (flushN > 0) {
            panel.runHint.textContent = t('hint.async_flushing', {
              n: flushN,
              ep: panel.episodeEl ? panel.episodeEl.textContent : '—',
            });
          } else {
            panel.runHint.textContent = t('hint.idle_ep', {
              ep: panel.episodeEl ? panel.episodeEl.textContent : '—',
            });
          }
        }
      }
    }

    function applyRecordUi(rec) {
      if (!rec) return;
      window.__lastRecordStatus = rec;
      recordPanels.forEach((p) => applyRecordUiToPanel(p, rec));
    }

    async function postRecord(path, body, panel) {
      const ui = panel || collectRec;
      busy = true;
      recordPanels.forEach((p) => {
        if (p.btnStart) p.btnStart.disabled = true;
        if (p.btnStop) p.btnStop.disabled = true;
        if (p.btnDiscard) p.btnDiscard.disabled = true;
      });
      const isStop = path.indexOf('stop') >= 0;
      const discarding = isStop && body && body.valid === false;
      const asyncFlush = !!(ui.chkAsyncFlush && ui.chkAsyncFlush.checked);
      if (isStop && body && typeof body === 'object') {
        body.async_flush = asyncFlush;
      }
      if (ui.runHint) {
        ui.runHint.textContent = discarding
          ? t('hint.discarding')
          : (isStop ? (asyncFlush ? t('hint.async_stopping') : t('hint.stopping')) : t('hint.starting'));
      }
      try {
        const opts = { method: 'POST' };
        if (body !== undefined) {
          opts.headers = { 'Content-Type': 'application/json' };
          opts.body = JSON.stringify(body);
        }
        const r = await fetch(path, opts);
        const j = await r.json();
        applyRecordUi(j);
        if (!j.ok && j.error) {
          if (ui.runHint) ui.runHint.textContent = j.error;
        } else if (discarding && j.ok) {
          if (ui.runHint) ui.runHint.textContent = t('hint.discarded');
        }
        const wantQc = !!(ui.chkQuickCollect && ui.chkQuickCollect.checked);
        if (isStop && j.ok && wantQc && j.finished_episode_path) {
          const epPath = j.finished_episode_path;
          if (ppEpisode) ppEpisode.value = epPath;
          const runQc = async () => {
            if (ui.runHint) {
              ui.runHint.textContent = discarding
                ? t('hint.qc_discard')
                : t('hint.qc_stop');
            }
            await inspectSelectedEpisode(epPath, { silent: true });
            const pp = await runPostprocess({
              steps: ['export-timeline', 'filter-timeline', 'export-hik-dataset'],
              allow_invalid: discarding || (document.getElementById('ppAllowInvalid') || {}).checked,
            }, epPath);
            if (pp && pp.ok) {
              if (ui.runHint) ui.runHint.textContent = t('hint.qc_ok', { path: epPath });
              showAppModal(t('modal.qc_ok'), epPath);
            } else if (pp) {
              if (ui.runHint) ui.runHint.textContent = t('hint.qc_fail', { error: pp.error || 'unknown' });
              showAppModal(t('modal.qc_fail'), pp.error || JSON.stringify(pp));
              try { switchTab('post'); } catch (e) {}
            }
          };
          if (asyncFlush || j.async_flush) {
            busy = false;
            try {
              const s = await fetch('/api/record/status').then((x) => x.json());
              applyRecordUi(s);
            } catch (e) {}
            runQc();
            return;
          }
          await runQc();
        }
      } catch (e) {
        if (ui.runHint) ui.runHint.textContent = String(e);
      } finally {
        busy = false;
        try {
          const s = await fetch('/api/record/status').then((x) => x.json());
          applyRecordUi(s);
        } catch (e) {}
      }
    }

    function wireRecordButtons(panel) {
      if (!panel || !panel.btnStart) return;
      panel.btnStart.addEventListener('click', () => postRecord(
        '/api/record/start',
        { mode: panel.mode || 'collect' },
        panel,
      ));
      panel.btnStop.addEventListener('click', () => postRecord('/api/record/stop', {
        valid: true,
        async_flush: !!(panel.chkAsyncFlush && panel.chkAsyncFlush.checked),
      }, panel));
      panel.btnDiscard.addEventListener('click', () => {
        if (!confirm(t('confirm.discard'))) return;
        postRecord('/api/record/stop', {
          valid: false,
          async_flush: !!(panel.chkAsyncFlush && panel.chkAsyncFlush.checked),
        }, panel);
      });
    }
    recordPanels.forEach(wireRecordButtons);

    const infPi05Host = document.getElementById('infPi05Host');
    const infPi05Port = document.getElementById('infPi05Port');
    const infPi05Prompt = document.getElementById('infPi05Prompt');
    const infPi05Hint = document.getElementById('infPi05Hint');
    const infPi05Out = document.getElementById('infPi05Out');
    const infPi05Status = document.getElementById('infPi05Status');
    const infPi05Connect = document.getElementById('infPi05Connect');
    const infPi05Disconnect = document.getElementById('infPi05Disconnect');
    const infPi05Step = document.getElementById('infPi05Step');
    const infPi05Loop = document.getElementById('infPi05Loop');
    const infLoopRounds = document.getElementById('infLoopRounds');
    const infLoopRoundIdx = document.getElementById('infLoopRoundIdx');
    const infPi05PromptApply = document.getElementById('infPi05PromptApply');
    const infFlagTermDot = document.getElementById('infFlagTermDot');
    const infFlagTermVal = document.getElementById('infFlagTermVal');
    const infFlagTerm = document.getElementById('infFlagTerm');
    const infFlagRejectDot = document.getElementById('infFlagRejectDot');
    const infFlagRejectVal = document.getElementById('infFlagRejectVal');
    const infFlagReject = document.getElementById('infFlagReject');
    const infPi05RawBtn = document.getElementById('infPi05RawBtn');
    const infPi05RawModal = document.getElementById('infPi05RawModal');
    const infPi05RawBody = document.getElementById('infPi05RawBody');
    const infPi05RawClose = document.getElementById('infPi05RawClose');
    const infArmJoints = document.getElementById('infArmJoints');
    let pi05LastRawText = '{}';
    const infArmSend = document.getElementById('infArmSend');
    const infArmDur = document.getElementById('infArmDur');
    const infArmDurVal = document.getElementById('infArmDurVal');
    const infArmProg = document.getElementById('infArmProg');
    const LS_PI05 = 'dcs.inf.pi05';
    let pi05StepBusy = false;
    let pi05LoopRunning = false;
    let pi05LoopGen = 0;
    let pi05LoopStepN = 0;
    function loadPi05Form() {
      try {
        const raw = localStorage.getItem(LS_PI05);
        if (!raw) return;
        const j = JSON.parse(raw);
        if (infPi05Host && j.host) infPi05Host.value = j.host;
        if (infPi05Port && j.port != null) infPi05Port.value = String(j.port);
        if (infPi05Prompt && j.prompt != null) infPi05Prompt.value = j.prompt;
      } catch (e) {}
    }
    function savePi05Form() {
      try {
        localStorage.setItem(LS_PI05, JSON.stringify({
          host: (infPi05Host && infPi05Host.value) || '127.0.0.1',
          port: Number((infPi05Port && infPi05Port.value) || 5000),
          prompt: (infPi05Prompt && infPi05Prompt.value) || '',
        }));
      } catch (e) {}
    }
    function formatPi05Out(p) {
      if (!p) return '{}';
      const slim = {
        ok: p.ok,
        connected: p.connected,
        host: p.host,
        port: p.port,
        step: p.step,
        latency_ms: p.latency_ms,
        robot_state: p.robot_state,
        next_state: p.next_state,
        next_joints_rad: p.next_joints_rad,
        ik_ok: p.ik_ok,
        ik_error: p.ik_error,
        term_flag: p.term_flag,
        reject_flag: p.reject_flag,
        server_text: p.server_text,
        jpeg_lens: p.jpeg_lens,
        error: p.error,
      };
      return JSON.stringify(slim, null, 2);
    }
    function setPi05StatusEl(kind, text, titleText) {
      if (!infPi05Status) return;
      infPi05Status.classList.remove('st-live', 'st-offline', 'st-error', 'st-connecting');
      infPi05Status.classList.add(kind || 'st-offline');
      infPi05Status.textContent = text;
      infPi05Status.title = titleText != null ? String(titleText) : String(text || '');
    }
    function setInfDot(el, kind) {
      if (!el) return;
      el.classList.remove('st-idle', 'st-ok', 'st-warn', 'st-bad');
      el.classList.add(kind || 'st-idle');
    }
    function updatePi05Flags(p) {
      const term = (p && p.term_flag != null) ? Number(p.term_flag) : null;
      const rej = (p && p.reject_flag != null) ? Number(p.reject_flag) : null;
      if (term == null || !Number.isFinite(term)) {
        setInfDot(infFlagTermDot, 'st-idle');
        if (infFlagTermVal) infFlagTermVal.textContent = t('infer.flag_idle');
        if (infFlagTerm) infFlagTerm.title = 'term_flag';
      } else if (term > 0.5) {
        setInfDot(infFlagTermDot, 'st-warn');
        if (infFlagTermVal) infFlagTermVal.textContent = t('infer.flag_term_on');
        if (infFlagTerm) infFlagTerm.title = 'term_flag=' + term;
      } else {
        setInfDot(infFlagTermDot, 'st-ok');
        if (infFlagTermVal) infFlagTermVal.textContent = t('infer.flag_term_off');
        if (infFlagTerm) infFlagTerm.title = 'term_flag=' + term;
      }
      if (rej == null || !Number.isFinite(rej)) {
        setInfDot(infFlagRejectDot, 'st-idle');
        if (infFlagRejectVal) infFlagRejectVal.textContent = t('infer.flag_idle');
        if (infFlagReject) infFlagReject.title = 'reject_flag';
      } else if (rej !== 0) {
        setInfDot(infFlagRejectDot, 'st-bad');
        if (infFlagRejectVal) infFlagRejectVal.textContent = t('infer.flag_reject_on');
        if (infFlagReject) infFlagReject.title = 'reject_flag=' + rej;
      } else {
        setInfDot(infFlagRejectDot, 'st-ok');
        if (infFlagRejectVal) infFlagRejectVal.textContent = t('infer.flag_reject_off');
        if (infFlagReject) infFlagReject.title = 'reject_flag=0';
      }
    }
    function setPi05RawPayload(p) {
      pi05LastRawText = formatPi05Out(p);
      if (infPi05Out) infPi05Out.textContent = pi05LastRawText;
      if (infPi05RawBody && infPi05RawModal && infPi05RawModal.classList.contains('show')) {
        infPi05RawBody.textContent = pi05LastRawText;
      }
    }
    function openPi05RawModal() {
      if (!infPi05RawModal || !infPi05RawBody) return;
      infPi05RawBody.textContent = pi05LastRawText || '{}';
      infPi05RawModal.classList.add('show');
    }
    function closePi05RawModal() {
      if (infPi05RawModal) infPi05RawModal.classList.remove('show');
    }
    if (infPi05RawBtn) infPi05RawBtn.addEventListener('click', openPi05RawModal);
    if (infPi05RawClose) infPi05RawClose.addEventListener('click', closePi05RawModal);
    if (infPi05RawModal) {
      infPi05RawModal.addEventListener('click', (e) => {
        if (e.target === infPi05RawModal) closePi05RawModal();
      });
    }
    function fillInfArmJointsFromStep(r) {
      if (!infArmJoints || !r) return false;
      const joints = r.next_joints_rad;
      if (!r.ik_ok || !Array.isArray(joints) || joints.length < 6) return false;
      const vals = [];
      for (let i = 0; i < 6; i++) {
        const v = Number(joints[i]);
        if (!Number.isFinite(v)) return false;
        vals.push(v.toFixed(4));
      }
      infArmJoints.value = vals.join(',');
      return true;
    }
    function updateInfArmDurLabel() {
      if (!infArmDur) return;
      let n = Math.round(Number(infArmDur.value));
      if (!Number.isFinite(n)) n = 100;
      n = Math.max(1, Math.min(300, n));
      if (String(infArmDur.value) !== String(n)) infArmDur.value = String(n);
      if (infArmDurVal) infArmDurVal.textContent = (n * 0.1).toFixed(1) + 's';
    }
    function infArmDurSeconds() {
      if (!infArmDur) return 10;
      let n = Math.round(Number(infArmDur.value));
      if (!Number.isFinite(n)) n = 100;
      n = Math.max(1, Math.min(300, n));
      return n * 0.1;
    }
    if (infArmDur) {
      infArmDur.addEventListener('input', updateInfArmDurLabel);
      infArmDur.addEventListener('change', updateInfArmDurLabel);
      updateInfArmDurLabel();
    }
    function applyPi05PanelFromPayload(p) {
      if (!infPi05Hint) return;
      const configured = !(p && p.configured === false);
      const connected = !!(p && p.connected);
      if (!configured) {
        setPi05StatusEl('st-offline', t('infer.status_unconfigured'));
        infPi05Hint.textContent = t('infer.hint_unconfigured');
        if (infPi05Hint) infPi05Hint.title = infPi05Hint.textContent || '';
        if (infArmProg) infArmProg.title = infArmProg.textContent || '';
        if (infPi05Connect) infPi05Connect.disabled = true;
        if (infPi05Disconnect) infPi05Disconnect.disabled = true;
        if (infPi05Step) infPi05Step.disabled = true;
        if (infPi05Loop) {
          infPi05Loop.disabled = true;
          infPi05Loop.textContent = t('infer.loop');
        }
        if (infPi05Host) infPi05Host.disabled = true;
        if (infPi05Port) infPi05Port.disabled = true;
        setPi05RawPayload(p);
        updatePi05Flags(p);
        return;
      }
      if (infPi05Host) infPi05Host.disabled = connected || pi05LoopRunning;
      if (infPi05Port) infPi05Port.disabled = connected || pi05LoopRunning;
      if (infPi05Connect) infPi05Connect.disabled = connected || pi05LoopRunning;
      if (infPi05Disconnect) infPi05Disconnect.disabled = !connected || pi05LoopRunning;
      if (infPi05Step) infPi05Step.disabled = !connected || pi05StepBusy || pi05LoopRunning;
      if (infLoopRounds) infLoopRounds.disabled = pi05LoopRunning;
      if (infPi05Loop) {
        infPi05Loop.disabled = !connected || (pi05StepBusy && !pi05LoopRunning);
        infPi05Loop.textContent = pi05LoopRunning ? t('infer.loop_stop') : t('infer.loop');
      }
      if (connected) {
        const host = (p && p.host) || ((infPi05Host && infPi05Host.value) || '127.0.0.1');
        const port = (p && p.port != null) ? p.port : ((infPi05Port && infPi05Port.value) || '5000');
        const detail = t('infer.status_connected', { host: host, port: port });
        setPi05StatusEl('st-live', t('infer.status_connected_short'), detail);
        infPi05Hint.textContent = (p && p.error && p.ok === false)
          ? String(p.error)
          : t('infer.hint_connected');
      } else if (p && p.error) {
        setPi05StatusEl('st-error', t('infer.status_error'), String(p.error));
        infPi05Hint.textContent = String(p.error);
      } else {
        setPi05StatusEl('st-offline', t('infer.status_disconnected'));
        infPi05Hint.textContent = t('infer.hint_idle');
      }
      if (infPi05Hint) infPi05Hint.title = infPi05Hint.textContent || '';
      if (infArmProg) infArmProg.title = infArmProg.textContent || '';
      setPi05RawPayload(p);
      if (p && p.host && infPi05Host && document.activeElement !== infPi05Host && !connected) {
        infPi05Host.value = p.host;
      }
      if (p && p.port != null && infPi05Port && document.activeElement !== infPi05Port && !connected) {
        infPi05Port.value = String(p.port);
      }
      if (p && p.prompt != null && infPi05Prompt && document.activeElement !== infPi05Prompt) {
        infPi05Prompt.value = p.prompt;
      }
      updatePi05Flags(p);
    }
    async function postPi05(path, body) {
      savePi05Form();
      const r = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }).then((x) => x.json());
      applyPi05PanelFromPayload(r);
      if (infPi05Hint && r.error && !r.ok) infPi05Hint.textContent = r.error;
      return r;
    }
    async function postInfArm(body) {
      const agentId = window.__armWriteAgentId || null;
      const r = await fetch('/api/arm/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(agentId ? { agent_id: agentId, ...body } : body),
      }).then((x) => x.json());
      return r;
    }
    loadPi05Form();
    fetch('/api/pi05/status').then((r) => r.json()).then(applyPi05PanelFromPayload).catch(() => {});
    if (infPi05Connect) {
      infPi05Connect.addEventListener('click', async () => {
        setPi05StatusEl('st-connecting', t('infer.status_connecting'));
        if (infPi05Connect) infPi05Connect.disabled = true;
        try {
          await postPi05('/api/pi05/connect', {
            host: (infPi05Host && infPi05Host.value) || '127.0.0.1',
            port: Number((infPi05Port && infPi05Port.value) || 5000),
          });
        } catch (e) {
          setPi05StatusEl('st-error', t('infer.status_error'));
          if (infPi05Hint) infPi05Hint.textContent = String(e);
          if (infPi05Connect) infPi05Connect.disabled = false;
        }
      });
    }
    if (infPi05Disconnect) {
      infPi05Disconnect.addEventListener('click', () => postPi05('/api/pi05/disconnect', {}));
    }
    function sleepMs(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }
    function parseInfArmJoints6() {
      const raw = ((infArmJoints && infArmJoints.value) || '').trim();
      const parts = raw.split(/[,\s;]+/).filter(Boolean);
      if (parts.length < 6) return { ok: false, error: t('arm.abs_need6') };
      const joints = parts.slice(0, 6).map((x) => Number(x));
      if (joints.some((v) => !Number.isFinite(v))) return { ok: false, error: t('arm.abs_bad') };
      return { ok: true, joints: joints };
    }
    async function runInfPi05StepOnce() {
      if (infPi05Hint) infPi05Hint.textContent = t('infer.hint_stepping');
      const r = await postPi05('/api/pi05/step', {});
      if (r && r.ok && fillInfArmJointsFromStep(r)) {
        if (infArmProg) infArmProg.textContent = t('infer.joints_filled');
      } else if (r && r.ok && r.ik_ok === false) {
        if (infArmProg) {
          infArmProg.textContent = t('infer.ik_fail', {
            error: r.ik_error || 'IK failed',
          });
        }
      }
      return r;
    }
    async function sendInfArmJointsOnce() {
      const parsed = parseInfArmJoints6();
      if (!parsed.ok) return { ok: false, error: parsed.error };
      if (!Array.isArray(window.__armReadJoints) || window.__armReadJoints.length < 6) {
        return { ok: false, error: t('arm.need_read') };
      }
      if (!window.__armWriteAgentId) {
        return { ok: false, error: t('infer.arm_need_writer') };
      }
      const duration_s = infArmDurSeconds();
      const r = await postInfArm({ joints_rad: parsed.joints, duration_s: duration_s });
      if (infArmProg) {
        infArmProg.textContent = r.ok
          ? t('arm.abs_ok', { dur: Number(duration_s).toFixed(1) })
          : t('arm.abs_fail', { error: r.error || JSON.stringify(r) });
      }
      if (r.ok) window.__armAbsRamp = r;
      return Object.assign({ duration_s: duration_s }, r);
    }
    async function waitInfArmArrive(gen, duration_s) {
      const timeoutMs = Math.max(5000, (Number(duration_s) || 10) * 1500 + 2000);
      const t0 = Date.now();
      while (pi05LoopRunning && gen === pi05LoopGen) {
        const ramp = window.__armAbsRamp || {};
        if (!ramp.enabled) {
          if (ramp.phase === 'error') {
            return { ok: false, error: ramp.last_error || ramp.message || 'abs ramp error' };
          }
          if (ramp.phase === 'completed' || ramp.phase === 'idle' || ramp.phase == null) {
            return { ok: true, phase: ramp.phase || 'idle' };
          }
        }
        if (Date.now() - t0 > timeoutMs) {
          return { ok: false, error: 'arrive timeout' };
        }
        await sleepMs(120);
      }
      return { ok: false, error: 'stopped', stopped: true };
    }
    function syncPi05LoopButton() {
      if (!infPi05Loop) return;
      infPi05Loop.textContent = pi05LoopRunning ? t('infer.loop_stop') : t('infer.loop');
    }
    /** Match gello_arm_sync.sync_done_eps_rad default (rad). */
    const HOME_ARRIVE_EPS_RAD = 0.03;
    function clampLoopRounds(raw) {
      let n = Math.round(Number(raw));
      if (!Number.isFinite(n)) n = 1;
      return Math.max(1, Math.min(1000, n));
    }
    function getLoopRounds() {
      return clampLoopRounds(infLoopRounds ? infLoopRounds.value : 1);
    }
    function syncLoopRoundsInput() {
      if (!infLoopRounds) return;
      const n = clampLoopRounds(infLoopRounds.value);
      if (String(infLoopRounds.value) !== String(n)) infLoopRounds.value = String(n);
    }
    function setLoopRoundIdx(r, R) {
      if (!infLoopRoundIdx) return;
      if (r == null || R == null) {
        infLoopRoundIdx.textContent = '—';
        return;
      }
      infLoopRoundIdx.textContent = t('infer.loop_round_idx', { r: r, R: R });
    }
    function homeArriveEpsRad() {
      const sync = window.__gelloArmSync || {};
      const fromParams = Number(sync.params && sync.params.sync_done_eps_rad);
      if (Number.isFinite(fromParams) && fromParams > 0) return fromParams;
      const fromTop = Number(sync.sync_done_eps_rad);
      if (Number.isFinite(fromTop) && fromTop > 0) return fromTop;
      return HOME_ARRIVE_EPS_RAD;
    }
    function checkJointsNearHome() {
      const st = window.__armHome || {};
      const home = st.home_joints_rad;
      const live = window.__armReadJoints;
      const eps = homeArriveEpsRad();
      if (!st.configured || !Array.isArray(home) || home.length < 6) {
        return { ok: false, error: t('arm.home_missing'), eps: eps, max_delta: null };
      }
      if (!Array.isArray(live) || live.length < 6) {
        return { ok: false, error: t('arm.need_read'), eps: eps, max_delta: null };
      }
      let maxd = 0;
      for (let i = 0; i < 6; i++) {
        maxd = Math.max(maxd, Math.abs(Number(live[i]) - Number(home[i])));
      }
      return { ok: maxd <= eps, eps: eps, max_delta: maxd, error: null };
    }
    async function waitJointsNearHome(gen, timeoutMs) {
      const t0 = Date.now();
      let last = checkJointsNearHome();
      while (pi05LoopRunning && gen === pi05LoopGen) {
        last = checkJointsNearHome();
        if (last.ok) return last;
        if (Date.now() - t0 > timeoutMs) return last;
        await sleepMs(150);
      }
      return Object.assign({}, last, { stopped: true, ok: false });
    }
    async function stopPi05Loop(reasonHint) {
      if (!pi05LoopRunning) return;
      pi05LoopRunning = false;
      pi05LoopGen += 1;
      syncPi05LoopButton();
      setLoopRoundIdx(null, null);
      if (infLoopRounds) infLoopRounds.disabled = false;
      const ramp = window.__armAbsRamp || {};
      if (ramp.enabled) {
        try {
          const r = await postInfArm({ cancel_abs_ramp: true });
          window.__armAbsRamp = r;
          if (infArmProg && r.ok) infArmProg.textContent = t('arm.abs_cancelled');
        } catch (e) {}
      }
      if (reasonHint && infPi05Hint) infPi05Hint.textContent = reasonHint;
      const st = await fetch('/api/pi05/status').then((x) => x.json()).catch(() => ({}));
      applyPi05PanelFromPayload(st);
    }
    function isArmJointLimitError(err) {
      const s = String(err || '');
      return /outside soft limits|soft\s*limits?|关节.{0,8}超限/i.test(s);
    }
    async function stopPi05LoopForError(n, error) {
      const err = String(error || 'error');
      if (isArmJointLimitError(err)) {
        showAppModal(t('infer.loop_limit_title'), err);
        await stopPi05Loop(t('infer.hint_loop_limit', { n: n }) + ' · ' + err);
        return;
      }
      await stopPi05Loop(t('infer.hint_loop_error', { error: err }));
    }
    /** One controlled LOOP until term/reject/error/stop. Does not clear pi05LoopRunning on term. */
    async function runPi05LoopOnce(gen, roundIdx, roundTotal) {
      pi05LoopStepN = 0;
      try {
        while (pi05LoopRunning && gen === pi05LoopGen) {
          pi05LoopStepN += 1;
          const n = pi05LoopStepN;
          if (infPi05Hint) {
            infPi05Hint.textContent = t('infer.hint_looping', {
              n: n, r: roundIdx, R: roundTotal,
            });
          }
          pi05StepBusy = true;
          if (infPi05Step) infPi05Step.disabled = true;
          let stepRes;
          try {
            stepRes = await runInfPi05StepOnce();
          } finally {
            pi05StepBusy = false;
          }
          if (!pi05LoopRunning || gen !== pi05LoopGen) {
            return { ok: false, reason: 'stopped', steps: n };
          }
          if (!stepRes || !stepRes.ok) {
            const err = (stepRes && (stepRes.error || stepRes.message)) || 'step failed';
            await stopPi05LoopForError(n, err);
            return { ok: false, reason: 'error', steps: n, error: err };
          }
          const term = (stepRes.term_flag != null) ? Number(stepRes.term_flag) : 0;
          const rej = (stepRes.reject_flag != null) ? Number(stepRes.reject_flag) : 0;
          if (Number.isFinite(term) && term > 0.5) {
            return { ok: true, reason: 'term', steps: n };
          }
          if (Number.isFinite(rej) && rej !== 0) {
            await stopPi05Loop(t('infer.hint_loop_reject', { n: n }));
            return { ok: false, reason: 'reject', steps: n };
          }
          if (!stepRes.ik_ok || !Array.isArray(stepRes.next_joints_rad)) {
            const err = stepRes.ik_error || t('infer.ik_fail', { error: 'no next_joints_rad' });
            showAppModal(t('infer.ik_fail_title'), err);
            await stopPi05Loop(t('infer.ik_fail', { error: err }));
            return { ok: false, reason: 'ik', steps: n, error: err };
          }
          if (!fillInfArmJointsFromStep(stepRes)) {
            const err = t('infer.ik_fail', { error: 'bad next_joints_rad' });
            showAppModal(t('infer.ik_fail_title'), err);
            await stopPi05Loop(err);
            return { ok: false, reason: 'ik', steps: n, error: err };
          }
          let sendRes;
          try {
            sendRes = await sendInfArmJointsOnce();
          } catch (e) {
            await stopPi05LoopForError(n, String(e));
            return { ok: false, reason: 'error', steps: n, error: String(e) };
          }
          if (!pi05LoopRunning || gen !== pi05LoopGen) {
            return { ok: false, reason: 'stopped', steps: n };
          }
          if (!sendRes || !sendRes.ok) {
            const err = (sendRes && sendRes.error) || 'send failed';
            await stopPi05LoopForError(n, err);
            return { ok: false, reason: 'error', steps: n, error: err };
          }
          if (infPi05Hint) {
            infPi05Hint.textContent = t('infer.hint_loop_wait', {
              n: n, r: roundIdx, R: roundTotal,
            });
          }
          const waitRes = await waitInfArmArrive(gen, sendRes.duration_s);
          if (!pi05LoopRunning || gen !== pi05LoopGen) {
            return { ok: false, reason: 'stopped', steps: n };
          }
          if (waitRes.stopped) return { ok: false, reason: 'stopped', steps: n };
          if (!waitRes.ok) {
            await stopPi05LoopForError(n, waitRes.error || 'wait failed');
            return { ok: false, reason: 'error', steps: n, error: waitRes.error };
          }
        }
      } catch (e) {
        await stopPi05LoopForError(pi05LoopStepN || 0, String(e));
        return { ok: false, reason: 'error', steps: pi05LoopStepN || 0, error: String(e) };
      }
      return { ok: false, reason: 'stopped', steps: pi05LoopStepN || 0 };
    }
    async function runPi05LoopRounds() {
      const gen = pi05LoopGen;
      syncLoopRoundsInput();
      const roundTotal = getLoopRounds();
      if (infLoopRounds) infLoopRounds.disabled = true;
      try {
        for (let r = 1; r <= roundTotal; r++) {
          if (!pi05LoopRunning || gen !== pi05LoopGen) break;
          setLoopRoundIdx(r, roundTotal);
          // Gate: each big round starts only when live joints are near configured Home.
          if (infPi05Hint) {
            infPi05Hint.textContent = t('infer.hint_home_check', { r: r, R: roundTotal });
          }
          const near = await waitJointsNearHome(gen, 2500);
          if (!pi05LoopRunning || gen !== pi05LoopGen) break;
          if (near.stopped) break;
          if (!near.ok) {
            const delta = (near.max_delta != null) ? Number(near.max_delta).toFixed(4) : '?';
            const eps = Number(near.eps != null ? near.eps : homeArriveEpsRad()).toFixed(4);
            const body = near.error
              || t('infer.hint_home_miss', { r: r, R: roundTotal, delta: delta, eps: eps });
            showAppModal(t('infer.home_miss_title'), body);
            await stopPi05Loop(
              t('infer.hint_home_miss', { r: r, R: roundTotal, delta: delta, eps: eps }),
            );
            return;
          }
          if (infPi05Hint) {
            infPi05Hint.textContent = t('infer.hint_round', { r: r, R: roundTotal });
          }
          const once = await runPi05LoopOnce(gen, r, roundTotal);
          if (!pi05LoopRunning || gen !== pi05LoopGen) break;
          if (once.reason !== 'term') {
            if (once.reason === 'stopped' && pi05LoopRunning && gen === pi05LoopGen) {
              await stopPi05Loop(t('infer.hint_loop_stopped', { n: once.steps || 0 }));
            }
            return;
          }
          // Big round = LOOP-until-term + Home (always; next round will re-check near Home).
          if (infPi05Hint) infPi05Hint.textContent = t('infer.hint_auto_home');
          const homeGo = await goArmHomeWithDuration(null, infArmProg);
          if (!pi05LoopRunning || gen !== pi05LoopGen) break;
          if (!homeGo || !homeGo.ok) {
            const err = (homeGo && homeGo.error) || 'home failed';
            showAppModal(t('arm.home_bad_title'), err);
            await stopPi05Loop(t('arm.home_fail', { error: err }));
            return;
          }
          const homeDur = Number(
            (homeGo && homeGo.duration_s) != null
              ? homeGo.duration_s
              : ((window.__armHome && window.__armHome.home_duration_s) || 20),
          );
          const homeWait = await waitInfArmArrive(gen, homeDur);
          if (!pi05LoopRunning || gen !== pi05LoopGen) break;
          if (homeWait.stopped) break;
          if (!homeWait.ok) {
            await stopPi05LoopForError(once.steps || 0, homeWait.error || 'home arrive failed');
            return;
          }
          if (infPi05Hint) {
            infPi05Hint.textContent = t('infer.hint_round_done', {
              r: r, R: roundTotal, n: once.steps || 0,
            });
          }
        }
      } finally {
        if (infLoopRounds && !pi05LoopRunning) infLoopRounds.disabled = false;
      }
      if (pi05LoopRunning && gen === pi05LoopGen) {
        await stopPi05Loop(t('infer.hint_rounds_done', { R: roundTotal }));
      } else if (!pi05LoopRunning) {
        setLoopRoundIdx(null, null);
        if (infLoopRounds) infLoopRounds.disabled = false;
      } else {
        const st = await fetch('/api/pi05/status').then((x) => x.json()).catch(() => ({}));
        applyPi05PanelFromPayload(st);
      }
    }
    if (infPi05Step) {
      infPi05Step.addEventListener('click', async () => {
        if (pi05LoopRunning || pi05StepBusy) return;
        pi05StepBusy = true;
        infPi05Step.disabled = true;
        if (infPi05Loop) infPi05Loop.disabled = true;
        try {
          const r = await runInfPi05StepOnce();
          if (r && r.ok && r.ik_ok) {
            if (infPi05Hint) infPi05Hint.textContent = t('infer.joints_filled');
          } else if (r && r.ok && r.ik_ok === false) {
            if (infPi05Hint) {
              infPi05Hint.textContent = t('infer.ik_fail', {
                error: r.ik_error || 'IK failed',
              });
            }
          }
        } finally {
          pi05StepBusy = false;
          const st = await fetch('/api/pi05/status').then((x) => x.json()).catch(() => ({}));
          applyPi05PanelFromPayload(st);
        }
      });
    }
    if (infLoopRounds) {
      try {
        const lsR = localStorage.getItem('dcs.inf.loopRounds');
        if (lsR != null && lsR !== '') infLoopRounds.value = String(clampLoopRounds(lsR));
      } catch (e) {}
      syncLoopRoundsInput();
      setLoopRoundIdx(null, null);
      infLoopRounds.addEventListener('change', () => {
        syncLoopRoundsInput();
        try {
          localStorage.setItem('dcs.inf.loopRounds', String(getLoopRounds()));
        } catch (e) {}
      });
    }
    if (infPi05Loop) {
      infPi05Loop.addEventListener('click', async () => {
        if (pi05LoopRunning) {
          await stopPi05Loop(t('infer.hint_loop_stopped', { n: pi05LoopStepN || 0 }));
          return;
        }
        if (pi05StepBusy) return;
        syncLoopRoundsInput();
        pi05LoopRunning = true;
        pi05LoopGen += 1;
        syncPi05LoopButton();
        if (infPi05Step) infPi05Step.disabled = true;
        if (infLoopRounds) infLoopRounds.disabled = true;
        await runPi05LoopRounds();
      });
    }
    if (infPi05PromptApply) {
      infPi05PromptApply.addEventListener('click', () => postPi05('/api/pi05/prompt', {
        prompt: (infPi05Prompt && infPi05Prompt.value) || '',
      }));
    }
    if (infArmSend) {
      infArmSend.addEventListener('click', async () => {
        if (pi05LoopRunning) {
          await stopPi05Loop(t('infer.hint_loop_stopped', { n: pi05LoopStepN || 0 }));
          return;
        }
        const absRamp = window.__armAbsRamp || {};
        if (absRamp.enabled) {
          try {
            const r = await postInfArm({ cancel_abs_ramp: true });
            if (infArmProg) {
              infArmProg.textContent = r.ok
                ? t('arm.abs_cancelled')
                : t('arm.abs_fail', { error: r.error || JSON.stringify(r) });
            }
          } catch (e) {
            if (infArmProg) infArmProg.textContent = t('arm.abs_err', { error: e });
          }
          return;
        }
        try {
          await sendInfArmJointsOnce();
        } catch (e) {
          if (infArmProg) infArmProg.textContent = t('arm.abs_err', { error: e });
        }
      });
    }
    const infArmHome = document.getElementById('infArmHome');
    if (infArmHome) {
      infArmHome.addEventListener('click', async () => {
        if (pi05LoopRunning) {
          await stopPi05Loop(t('infer.hint_loop_stopped', { n: pi05LoopStepN || 0 }));
        }
        await goArmHomeWithDuration(null, infArmProg);
      });
    }
    const infAutoHome = document.getElementById('infAutoHome');
    try {
      const lsAuto = localStorage.getItem('dcs.inf.autoHome');
      if (infAutoHome && lsAuto === '1') infAutoHome.checked = true;
    } catch (e) {}
    if (infAutoHome) {
      infAutoHome.addEventListener('change', () => {
        try {
          localStorage.setItem('dcs.inf.autoHome', infAutoHome.checked ? '1' : '0');
        } catch (e) {}
      });
    }
    refreshArmHomeState();

    // ---- tabs + postprocess ----
    const tabBtnHome = document.getElementById('tabBtnHome');
    const tabBtnCollect = document.getElementById('tabBtnCollect');
    const tabBtnInfer = document.getElementById('tabBtnInfer');
    const tabBtnPost = document.getElementById('tabBtnPost');
    const tabBtnSensors = document.getElementById('tabBtnSensors');
    const tabHome = document.getElementById('tab-home');
    const tabCollect = document.getElementById('tab-collect');
    const tabInfer = document.getElementById('tab-infer');
    const tabPost = document.getElementById('tab-post');
    const tabSensors = document.getElementById('tab-sensors');
    const bootBanner = document.getElementById('bootBanner');
    const bootBannerPath = document.getElementById('bootBannerPath');
    const bootBannerErr = document.getElementById('bootBannerErr');
    const ppEpisode = document.getElementById('ppEpisode');
    const ppEpisodeSelect = document.getElementById('ppEpisodeSelect');
    const ppAlign = document.getElementById('ppAlign');
    const ppMaster = document.getElementById('ppMaster');
    const ppMasterHz = document.getElementById('ppMasterHz');
    const ppRequire = document.getElementById('ppRequire');
    const ppMaxDt = document.getElementById('ppMaxDt');
    const ppTrim = document.getElementById('ppTrim');
    const ppMaterialize = document.getElementById('ppMaterialize');
    const ppCameraMap = document.getElementById('ppCameraMap');
    const ppAllowInvalid = document.getElementById('ppAllowInvalid');
    const ppHint = document.getElementById('ppHint');
    const ppLog = document.getElementById('ppLog');
    let ppBusy = false;
    let collectOk = true;

    const SENSORS_EMBED_URL_KEY = 'sensors-dcs.sensorsEmbedUrl';
    const DEFAULT_SENSORS_EMBED_URL = 'http://127.0.0.1:6008/';
    let sensorsEmbed = {
      url: DEFAULT_SENSORS_EMBED_URL,
      reachability: 'unknown',
      reachMessage: '',
      canPing: false,
      pingSeq: 0,
      mountedBase: '',
    };
    const sensorsEmbedFrame = document.getElementById('sensorsEmbedFrame');
    const sensorsUnreachable = document.getElementById('sensorsUnreachable');
    const sensorsUnreachableMsg = document.getElementById('sensorsUnreachableMsg');
    const sensorsUnreachableUrl = document.getElementById('sensorsUnreachableUrl');
    const settingsSensorsUrl = document.getElementById('settingsSensorsUrl');
    const settingsSensorsStatus = document.getElementById('settingsSensorsStatus');
    const settingsSensorsSaved = document.getElementById('settingsSensorsSaved');
    let sensorsUrlDraftTimer = null;

    function normalizeSensorsEmbedUrl(raw) {
      const trimmed = String(raw || '').trim();
      if (!trimmed) return DEFAULT_SENSORS_EMBED_URL;
      let withProto = trimmed;
      if (!/^https?:[/][/]/i.test(withProto)) withProto = 'https://' + withProto;
      try {
        const u = new URL(withProto);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return DEFAULT_SENSORS_EMBED_URL;
        const path = (u.pathname === '/' || u.pathname === '') ? '/' : u.pathname.replace(/[/]?$/, '/');
        return u.origin + path;
      } catch (e) {
        return DEFAULT_SENSORS_EMBED_URL;
      }
    }
    function sensorsEmbedOrigin(url) {
      try { return new URL(normalizeSensorsEmbedUrl(url)).origin; }
      catch (e) { return new URL(DEFAULT_SENSORS_EMBED_URL).origin; }
    }
    function readStoredSensorsEmbedUrl() {
      try {
        const raw = localStorage.getItem(SENSORS_EMBED_URL_KEY);
        if (raw && raw.trim()) return normalizeSensorsEmbedUrl(raw);
      } catch (e) {}
      return DEFAULT_SENSORS_EMBED_URL;
    }
    function persistSensorsEmbedUrl(url) {
      const normalized = normalizeSensorsEmbedUrl(url);
      try { localStorage.setItem(SENSORS_EMBED_URL_KEY, normalized); } catch (e) {}
      return normalized;
    }
    function buildSensorsEmbedSrc(baseUrl) {
      const u = new URL(normalizeSensorsEmbedUrl(baseUrl));
      u.searchParams.set('locale', currentLocale === 'en' ? 'en' : 'zh');
      u.searchParams.set('theme', (appearancePrefs && appearancePrefs.theme) || 'dark');
      return u.toString();
    }
    function pushSensorsEmbedPrefs() {
      if (!sensorsEmbedFrame || !sensorsEmbedFrame.contentWindow) return;
      const origin = sensorsEmbedOrigin(sensorsEmbed.url);
      try {
        sensorsEmbedFrame.contentWindow.postMessage({
          source: 'sensors-view-host',
          type: 'embed-prefs',
          locale: currentLocale === 'en' ? 'en' : 'zh',
          theme: (appearancePrefs && appearancePrefs.theme) || 'dark',
        }, origin);
      } catch (e) {}
    }
    function syncSensorsEmbedStatusUi() {
      if (settingsSensorsStatus) {
        const map = {
          ok: 'settings.sensors.statusOk',
          fail: 'settings.sensors.statusFail',
          checking: 'settings.sensors.statusChecking',
          unknown: 'settings.sensors.statusUnknown',
        };
        const key = map[sensorsEmbed.reachability] || map.unknown;
        let label = t(key);
        if (sensorsEmbed.reachMessage) label = label + ' · ' + sensorsEmbed.reachMessage;
        settingsSensorsStatus.textContent = label;
        settingsSensorsStatus.className = 'settings-sensors-status'
          + (sensorsEmbed.reachability === 'ok' ? ' settings-sensors-status-ok' : '')
          + (sensorsEmbed.reachability === 'fail' ? ' settings-sensors-status-fail' : '')
          + (sensorsEmbed.reachability === 'checking' ? ' settings-sensors-status-checking' : '');
      }
      if (settingsSensorsUrl && document.activeElement !== settingsSensorsUrl) {
        settingsSensorsUrl.value = sensorsEmbed.url;
      }
      const unreachable = sensorsEmbed.reachability === 'fail';
      if (sensorsEmbedFrame) sensorsEmbedFrame.hidden = unreachable;
      if (sensorsUnreachable) sensorsUnreachable.hidden = !unreachable;
      if (unreachable) {
        if (sensorsUnreachableMsg) {
          sensorsUnreachableMsg.textContent = sensorsEmbed.reachMessage || t('settings.sensors.unreachableHint');
        }
        if (sensorsUnreachableUrl) sensorsUnreachableUrl.textContent = sensorsEmbed.url;
      }
    }
    function applySensorsGate() {
      const blocked = sensorsEmbed.reachability === 'fail';
      if (tabBtnSensors) {
        tabBtnSensors.classList.toggle('tab-locked', blocked);
        tabBtnSensors.disabled = blocked;
        tabBtnSensors.setAttribute('aria-disabled', blocked ? 'true' : 'false');
        tabBtnSensors.title = blocked ? t('settings.sensors.unreachableHint') : '';
      }
      const homeBtn = document.getElementById('btnHomeSensors');
      if (homeBtn) {
        homeBtn.disabled = blocked;
        homeBtn.setAttribute('aria-disabled', blocked ? 'true' : 'false');
        homeBtn.title = blocked ? t('settings.sensors.unreachableHint') : '';
      }
      if (blocked && tabSensors && tabSensors.classList.contains('active')) {
        switchTab('home');
      }
      syncSensorsEmbedStatusUi();
    }
    function ensureSensorsIframeMounted() {
      if (sensorsEmbed.reachability === 'fail') {
        syncSensorsEmbedStatusUi();
        return;
      }
      if (!sensorsEmbedFrame) return;
      const base = normalizeSensorsEmbedUrl(sensorsEmbed.url);
      if (sensorsEmbed.mountedBase !== base) {
        sensorsEmbed.mountedBase = base;
        sensorsEmbedFrame.src = buildSensorsEmbedSrc(base);
      }
      sensorsEmbedFrame.hidden = false;
      if (sensorsUnreachable) sensorsUnreachable.hidden = true;
    }
    async function pingSensorsEmbed() {
      if (!sensorsEmbed.canPing) {
        sensorsEmbed.reachability = 'unknown';
        sensorsEmbed.reachMessage = '';
        applySensorsGate();
        return false;
      }
      const seq = ++sensorsEmbed.pingSeq;
      sensorsEmbed.reachability = 'checking';
      sensorsEmbed.reachMessage = '';
      syncSensorsEmbedStatusUi();
      const pingBtn = document.getElementById('btnSettingsSensorsPing');
      if (pingBtn) {
        pingBtn.disabled = true;
        pingBtn.textContent = t('settings.sensors.pinging');
      }
      try {
        const qs = new URLSearchParams({ url: sensorsEmbed.url });
        const r = await fetch('/api/sensors-view/ping?' + qs.toString(), {
          cache: 'no-store',
          credentials: 'same-origin',
        });
        const j = await r.json().catch(() => ({ ok: false, message: 'HTTP ' + r.status }));
        if (seq !== sensorsEmbed.pingSeq) return false;
        if (j && j.ok) {
          sensorsEmbed.reachability = 'ok';
          sensorsEmbed.reachMessage = j.message || '';
          applySensorsGate();
          return true;
        }
        sensorsEmbed.reachability = 'fail';
        sensorsEmbed.reachMessage = (j && (j.message || j.error)) || 'unreachable';
        applySensorsGate();
        return false;
      } catch (e) {
        if (seq !== sensorsEmbed.pingSeq) return false;
        sensorsEmbed.reachability = 'fail';
        sensorsEmbed.reachMessage = String(e.message || e);
        applySensorsGate();
        return false;
      } finally {
        if (pingBtn) {
          pingBtn.disabled = false;
          pingBtn.textContent = t('settings.sensors.ping');
        }
      }
    }
    function setSensorsEmbedUrl(next) {
      const saved = persistSensorsEmbedUrl(next);
      const changed = saved !== sensorsEmbed.url;
      sensorsEmbed.url = saved;
      if (settingsSensorsUrl) settingsSensorsUrl.value = saved;
      if (settingsSensorsSaved) {
        settingsSensorsSaved.hidden = false;
        setTimeout(() => { if (settingsSensorsSaved) settingsSensorsSaved.hidden = true; }, 1600);
      }
      if (changed) sensorsEmbed.mountedBase = '';
      pingSensorsEmbed();
    }
    sensorsEmbed.url = readStoredSensorsEmbedUrl();
    if (settingsSensorsUrl) settingsSensorsUrl.value = sensorsEmbed.url;
    window.addEventListener('message', (event) => {
      const want = sensorsEmbedOrigin(sensorsEmbed.url);
      if (event.origin !== want) return;
      const data = event.data;
      if (!data || data.source !== 'sensors-view') return;
      if (data.type !== 'embed-ready' && data.type !== 'embed-request-prefs') return;
      try {
        event.source.postMessage({
          source: 'sensors-view-host',
          type: 'embed-prefs',
          locale: currentLocale === 'en' ? 'en' : 'zh',
          theme: (appearancePrefs && appearancePrefs.theme) || 'dark',
        }, event.origin);
      } catch (e) {}
    });

    function applyCollectGate(ok, info) {
      collectOk = !!ok;
      tabBtnCollect.classList.toggle('tab-locked', !collectOk);
      tabBtnCollect.disabled = !collectOk;
      tabBtnCollect.setAttribute('aria-disabled', collectOk ? 'false' : 'true');
      tabBtnCollect.title = collectOk ? '' : t('boot.collect_title');
      const btnHomeCollect = document.getElementById('btnHomeCollect');
      if (btnHomeCollect) {
        btnHomeCollect.disabled = !collectOk;
        btnHomeCollect.setAttribute('aria-disabled', collectOk ? 'false' : 'true');
        btnHomeCollect.title = collectOk ? '' : t('boot.collect_title');
      }
      if (!collectOk) {
        if (bootBanner) {
          bootBanner.hidden = false;
          bootBanner.classList.add('visible');
          if (bootBannerPath) bootBannerPath.textContent = (info && info.config_path) || '';
          if (bootBannerErr) bootBannerErr.textContent = (info && info.error) || '';
        }
        if (tabCollect && tabCollect.classList.contains('active')) {
          switchTab('home');
        }
      } else if (bootBanner) {
        bootBanner.hidden = true;
        bootBanner.classList.remove('visible');
      }
    }

    function switchTab(name) {
      if (name === 'collect' && !collectOk) {
        if (runHint) runHint.textContent = t('boot.collect_locked');
        return;
      }
      if (name === 'sensors' && sensorsEmbed.reachability === 'fail') {
        return;
      }
      const which = (name === 'collect' || name === 'infer' || name === 'post' || name === 'home' || name === 'sensors')
        ? name
        : 'home';
      tabBtnHome.classList.toggle('active', which === 'home');
      tabBtnCollect.classList.toggle('active', which === 'collect');
      if (tabBtnInfer) tabBtnInfer.classList.toggle('active', which === 'infer');
      tabBtnPost.classList.toggle('active', which === 'post');
      if (tabBtnSensors) tabBtnSensors.classList.toggle('active', which === 'sensors');
      tabBtnHome.setAttribute('aria-selected', which === 'home' ? 'true' : 'false');
      tabBtnCollect.setAttribute('aria-selected', which === 'collect' ? 'true' : 'false');
      if (tabBtnInfer) tabBtnInfer.setAttribute('aria-selected', which === 'infer' ? 'true' : 'false');
      tabBtnPost.setAttribute('aria-selected', which === 'post' ? 'true' : 'false');
      if (tabBtnSensors) tabBtnSensors.setAttribute('aria-selected', which === 'sensors' ? 'true' : 'false');
      tabHome.classList.toggle('active', which === 'home');
      tabCollect.classList.toggle('active', which === 'collect');
      if (tabInfer) tabInfer.classList.toggle('active', which === 'infer');
      tabPost.classList.toggle('active', which === 'post');
      if (tabSensors) tabSensors.classList.toggle('active', which === 'sensors');
      // Embody convention: Settings gear only on the home overview.
      const gear = document.getElementById('btnSettings');
      if (gear) gear.hidden = which !== 'home';
      if (which === 'sensors') ensureSensorsIframeMounted();
    }
    tabBtnHome.addEventListener('click', () => switchTab('home'));
    tabBtnCollect.addEventListener('click', () => switchTab('collect'));
    if (tabBtnInfer) tabBtnInfer.addEventListener('click', () => switchTab('infer'));
    tabBtnPost.addEventListener('click', () => {
      switchTab('post');
      refreshEpisodeList();
    });
    if (tabBtnSensors) tabBtnSensors.addEventListener('click', () => switchTab('sensors'));
    const btnHomeCollect = document.getElementById('btnHomeCollect');
    const btnHomeInfer = document.getElementById('btnHomeInfer');
    const btnHomePost = document.getElementById('btnHomePost');
    const btnHomeSensors = document.getElementById('btnHomeSensors');
    if (btnHomeCollect) {
      btnHomeCollect.addEventListener('click', () => switchTab('collect'));
    }
    if (btnHomeInfer) {
      btnHomeInfer.addEventListener('click', () => switchTab('infer'));
    }
    if (btnHomePost) {
      btnHomePost.addEventListener('click', () => {
        switchTab('post');
        refreshEpisodeList();
      });
    }
    if (btnHomeSensors) {
      btnHomeSensors.addEventListener('click', () => switchTab('sensors'));
    }
    // Default landing tab after login: home
    switchTab('home');

    // ---- settings modal (auth + users) ----
    const settingsModal = document.getElementById('settingsModal');
    const settingsDialog = document.getElementById('settingsDialog');
    const btnSettings = document.getElementById('btnSettings');
    const btnSettingsClose = document.getElementById('btnSettingsClose');
    const btnSettingsDone = document.getElementById('btnSettingsDone');
    const settingsNavAppearance = document.getElementById('settingsNavAppearance');
    const settingsNavLanguage = document.getElementById('settingsNavLanguage');
    const settingsNavSensors = document.getElementById('settingsNavSensors');
    const settingsNavAuth = document.getElementById('settingsNavAuth');
    const settingsNavConfig = document.getElementById('settingsNavConfig');
    const settingsNavUsers = document.getElementById('settingsNavUsers');
    const settingsPanelAppearance = document.getElementById('settingsPanelAppearance');
    const settingsPanelLanguage = document.getElementById('settingsPanelLanguage');
    const settingsPanelSensors = document.getElementById('settingsPanelSensors');
    const settingsPanelAuth = document.getElementById('settingsPanelAuth');
    const settingsPanelConfig = document.getElementById('settingsPanelConfig');
    const settingsPanelUsers = document.getElementById('settingsPanelUsers');
    const settingsAuthStatus = document.getElementById('settingsAuthStatus');
    const settingsAuthUser = document.getElementById('settingsAuthUser');
    const settingsAuthRole = document.getElementById('settingsAuthRole');
    const settingsAuthMsg = document.getElementById('settingsAuthMsg');
    const settingsConfigMsg = document.getElementById('settingsConfigMsg');
    const settingsConfigCurrent = document.getElementById('settingsConfigCurrent');
    const settingsConfigPath = document.getElementById('settingsConfigPath');
    const settingsUsersMsg = document.getElementById('settingsUsersMsg');
    const settingsUsersBody = document.getElementById('settingsUsersBody');
    const settingsUsersAdmin = document.getElementById('settingsUsersAdmin');
    const settingsUsersNonAdmin = document.getElementById('settingsUsersNonAdmin');
    let settingsMe = null;
    let settingsBusy = false;
    let settingsBodyOverflow = '';
    let settingsRoots = {};
    let pathPicker = {
      columns: [],
      activePath: '',
      draft: '',
      rootKey: 'workspace',
      rootPath: '',
      pathKind: 'file',
      target: 'config',
    };

    async function ensureSettingsRoots() {
      if (Object.keys(settingsRoots).length) return;
      try {
        const j = await fetch('/api/fs/roots', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.json());
        if (j && j.ok && j.roots) {
          settingsRoots = j.roots;
          return;
        }
      } catch (e) {}
      await refreshSettingsConfig();
    }

    function setSettingsMsg(el, text, isErr) {
      if (!el) return;
      el.textContent = text || '';
      el.classList.toggle('err', !!isErr);
    }

    function switchSettingsTab(name) {
      const allowed = { appearance: 1, language: 1, sensors: 1, auth: 1, config: 1, users: 1 };
      const which = allowed[name] ? name : 'appearance';
      const navs = {
        appearance: settingsNavAppearance,
        language: settingsNavLanguage,
        sensors: settingsNavSensors,
        auth: settingsNavAuth,
        config: settingsNavConfig,
        users: settingsNavUsers,
      };
      const panels = {
        appearance: settingsPanelAppearance,
        language: settingsPanelLanguage,
        sensors: settingsPanelSensors,
        auth: settingsPanelAuth,
        config: settingsPanelConfig,
        users: settingsPanelUsers,
      };
      Object.keys(navs).forEach((k) => {
        const nav = navs[k];
        const panel = panels[k];
        const on = k === which;
        if (nav) {
          nav.classList.toggle('active', on);
          if (on) nav.setAttribute('aria-current', 'page');
          else nav.removeAttribute('aria-current');
        }
        if (panel) panel.classList.toggle('active', on);
      });
    }

    const APPEARANCE_THEME_KEY = 'sensors-dcs.theme';
    const APPEARANCE_COMPACT_KEY = 'sensors-dcs.compact';
    const APPEARANCE_DENSITY_KEY = 'sensors-dcs.density';
    const DEFAULT_APPEARANCE = { theme: 'dark', compact: false, density: 'comfortable' };
    let appearancePrefs = Object.assign({}, DEFAULT_APPEARANCE);
    let appearanceMedia = null;

    function isThemePref(v) {
      return v === 'system' || v === 'dark' || v === 'light';
    }
    function isDensityPref(v) {
      return v === 'comfortable' || v === 'compact' || v === 'dense';
    }
    function readStoredAppearance() {
      const out = Object.assign({}, DEFAULT_APPEARANCE);
      try {
        const theme = localStorage.getItem(APPEARANCE_THEME_KEY);
        if (isThemePref(theme)) out.theme = theme;
        const compact = localStorage.getItem(APPEARANCE_COMPACT_KEY);
        if (compact === '1' || compact === 'true') out.compact = true;
        if (compact === '0' || compact === 'false') out.compact = false;
        const density = localStorage.getItem(APPEARANCE_DENSITY_KEY);
        if (isDensityPref(density)) out.density = density;
      } catch (e) {}
      return out;
    }
    function persistAppearance(prefs) {
      try {
        localStorage.setItem(APPEARANCE_THEME_KEY, prefs.theme);
        localStorage.setItem(APPEARANCE_COMPACT_KEY, prefs.compact ? '1' : '0');
        localStorage.setItem(APPEARANCE_DENSITY_KEY, prefs.density);
      } catch (e) {}
    }
    function resolveTheme(pref) {
      if (pref === 'dark' || pref === 'light') return pref;
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
      return 'dark';
    }
    function applyAppearance(prefs) {
      const resolved = resolveTheme(prefs.theme);
      const root = document.documentElement;
      root.setAttribute('data-theme', resolved);
      root.setAttribute('data-theme-pref', prefs.theme);
      root.setAttribute('data-compact', prefs.compact ? '1' : '0');
      root.setAttribute('data-density', prefs.density);
      root.style.colorScheme = resolved;
      return resolved;
    }
    function syncAppearanceControls() {
      document.querySelectorAll('#settingsThemeSeg [data-theme-pref]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-theme-pref') === appearancePrefs.theme);
      });
      document.querySelectorAll('#settingsDensitySeg [data-density-pref]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-density-pref') === appearancePrefs.density);
      });
      const tog = document.getElementById('settingsCompactToggle');
      if (tog) {
        tog.classList.toggle('on', !!appearancePrefs.compact);
        tog.setAttribute('aria-pressed', appearancePrefs.compact ? 'true' : 'false');
      }
    }
    function setAppearancePrefs(partial, opts) {
      const persist = !opts || opts.persist !== false;
      appearancePrefs = Object.assign({}, appearancePrefs, partial || {});
      if (persist) persistAppearance(appearancePrefs);
      applyAppearance(appearancePrefs);
      syncAppearanceControls();
      bindAppearanceMedia();
      try { if (typeof pushSensorsEmbedPrefs === 'function') pushSensorsEmbedPrefs(); } catch (e) {}
    }
    function bindAppearanceMedia() {
      if (appearanceMedia) {
        try { appearanceMedia.removeEventListener('change', onAppearanceMediaChange); } catch (e) {}
        appearanceMedia = null;
      }
      if (appearancePrefs.theme !== 'system' || !window.matchMedia) return;
      appearanceMedia = window.matchMedia('(prefers-color-scheme: light)');
      appearanceMedia.addEventListener('change', onAppearanceMediaChange);
    }
    function onAppearanceMediaChange() {
      if (appearancePrefs.theme === 'system') {
        applyAppearance(appearancePrefs);
        try { if (typeof pushSensorsEmbedPrefs === 'function') pushSensorsEmbedPrefs(); } catch (e) {}
      }
    }
    appearancePrefs = readStoredAppearance();
    applyAppearance(appearancePrefs);
    syncAppearanceControls();
    bindAppearanceMedia();

    (function bootGuestEmbed() {
      function isFramed() {
        try { return window.self !== window.top; }
        catch (e) { return true; }
      }
      const badge = document.getElementById('iframeEmbedBadge');
      const framed = isFramed();
      if (badge) badge.hidden = !framed;
      if (!framed) {
        document.documentElement.removeAttribute('data-embed');
        return;
      }
      document.documentElement.setAttribute('data-embed', '1');
      const closeSettingsFn = typeof closeSettings === 'function' ? closeSettings : null;
      if (closeSettingsFn) closeSettingsFn();
      function qsPrefs() {
        const out = {};
        try {
          const q = new URLSearchParams(location.search);
          const loc = (q.get('locale') || q.get('lang') || '').toLowerCase();
          if (loc === 'zh' || loc === 'en') out.locale = loc;
          const th = (q.get('theme') || '').toLowerCase();
          if (th === 'dark' || th === 'light' || th === 'system') out.theme = th;
        } catch (e) {}
        return out;
      }
      function applyHostPrefs(prefs) {
        if (!prefs) return;
        if (prefs.locale === 'zh' || prefs.locale === 'en') setLocale(prefs.locale, { persist: false });
        if (prefs.theme === 'dark' || prefs.theme === 'light' || prefs.theme === 'system') {
          setAppearancePrefs({ theme: prefs.theme }, { persist: false });
        }
      }
      applyHostPrefs(qsPrefs());
      window.addEventListener('message', (event) => {
        const data = event.data;
        if (!data || typeof data !== 'object') return;
        if (data.source !== 'sensors-view-host') return;
        if (data.type === 'embed-prefs' || data.type === 'appearance') {
          applyHostPrefs({ locale: data.locale, theme: data.theme });
        } else if (data.type === 'embed-locale') {
          applyHostPrefs({ locale: data.locale });
        } else if (data.type === 'embed-theme') {
          applyHostPrefs({ theme: data.theme });
        }
      });
      try {
        parent.postMessage({ source: 'sensors-view', version: 1, type: 'embed-ready' }, '*');
        parent.postMessage({ source: 'sensors-view', version: 1, type: 'embed-request-prefs' }, '*');
      } catch (e) {}
    })();

    function roleLabel(role) {
      if (role === 'admin') return t('settings.role_admin');
      if (role === 'operator') return t('settings.role_operator');
      return t('settings.role_guest');
    }

    async function refreshSettingsAuth() {
      setSettingsMsg(settingsAuthMsg, '');
      try {
        const j = await fetch('/api/auth/me', { credentials: 'same-origin' }).then((r) => r.json());
        settingsMe = j;
        const required = !!j.authRequired;
        const authed = !!j.authenticated;
        if (settingsAuthStatus) {
          settingsAuthStatus.textContent = !required
            ? t('settings.auth_off')
            : (authed ? t('settings.auth_on') : t('settings.auth_needed'));
        }
        const user = j.user || {};
        if (settingsAuthUser) {
          settingsAuthUser.textContent = required
            ? (user.username || t('settings.auth_anonymous'))
            : t('settings.auth_na');
        }
        if (settingsAuthRole) {
          settingsAuthRole.textContent = required
            ? (user.role ? roleLabel(user.role) : '—')
            : t('settings.auth_na');
        }
        const isAdmin = required && authed && user.role === 'admin';
        if (settingsNavUsers) settingsNavUsers.hidden = !required;
        if (settingsUsersAdmin) settingsUsersAdmin.hidden = !isAdmin;
        if (settingsUsersNonAdmin) settingsUsersNonAdmin.hidden = !required || isAdmin;
        const btnLogout = document.getElementById('btnSettingsLogout');
        if (btnLogout) btnLogout.style.display = required ? '' : 'none';
        const nextCanPing = !required || authed;
        if (nextCanPing !== sensorsEmbed.canPing) {
          sensorsEmbed.canPing = nextCanPing;
          if (nextCanPing) pingSensorsEmbed();
          else applySensorsGate();
        } else if (nextCanPing && sensorsEmbed.reachability === 'unknown') {
          pingSensorsEmbed();
        }
        return j;
      } catch (e) {
        setSettingsMsg(settingsAuthMsg, String(e), true);
        return null;
      }
    }

    async function refreshSettingsUsers() {
      setSettingsMsg(settingsUsersMsg, '');
      if (!settingsUsersBody) return;
      if (!settingsMe || !settingsMe.authRequired || !(settingsMe.user && settingsMe.user.role === 'admin')) {
        settingsUsersBody.innerHTML = '';
        return;
      }
      try {
        const r = await fetch('/api/users', { credentials: 'same-origin', cache: 'no-store' });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          setSettingsMsg(settingsUsersMsg, j.error || ('HTTP ' + r.status), true);
          return;
        }
        const meName = (settingsMe.user && settingsMe.user.username) || '';
        settingsUsersBody.innerHTML = '';
        (j.users || []).forEach((row) => {
          const tr = document.createElement('tr');
          const tdUser = document.createElement('td');
          tdUser.textContent = row.username || '';
          const tdRole = document.createElement('td');
          const sel = document.createElement('select');
          ['operator', 'admin', 'guest'].forEach((role) => {
            const opt = document.createElement('option');
            opt.value = role;
            opt.textContent = roleLabel(role);
            if (role === row.role) opt.selected = true;
            sel.appendChild(opt);
          });
          sel.disabled = settingsBusy;
          sel.addEventListener('change', () => patchUser(row.username, { role: sel.value }));
          tdRole.appendChild(sel);
          const tdEn = document.createElement('td');
          const chk = document.createElement('input');
          chk.type = 'checkbox';
          chk.checked = row.enabled !== false;
          chk.disabled = settingsBusy || row.username === meName;
          chk.addEventListener('change', () => patchUser(row.username, { enabled: chk.checked }));
          tdEn.appendChild(chk);
          const tdPw = document.createElement('td');
          const inp = document.createElement('input');
          inp.type = 'password';
          inp.placeholder = t('settings.reset_pass_ph');
          inp.autocomplete = 'new-password';
          tdPw.appendChild(inp);
          const tdAct = document.createElement('td');
          const btnReset = document.createElement('button');
          btnReset.type = 'button';
          btnReset.textContent = t('settings.reset_pass');
          btnReset.addEventListener('click', () => {
            const pw = inp.value || '';
            if (!pw) {
              setSettingsMsg(settingsUsersMsg, t('settings.need_password'), true);
              return;
            }
            patchUser(row.username, { password: pw }).then(() => { inp.value = ''; });
          });
          const btnDel = document.createElement('button');
          btnDel.type = 'button';
          btnDel.textContent = t('settings.delete');
          btnDel.disabled = row.username === meName;
          btnDel.addEventListener('click', () => {
            if (!confirm(t('settings.confirm_delete', { user: row.username }))) return;
            deleteUser(row.username);
          });
          tdAct.appendChild(btnReset);
          tdAct.appendChild(document.createTextNode(' '));
          tdAct.appendChild(btnDel);
          tr.appendChild(tdUser);
          tr.appendChild(tdRole);
          tr.appendChild(tdEn);
          tr.appendChild(tdPw);
          tr.appendChild(tdAct);
          settingsUsersBody.appendChild(tr);
        });
      } catch (e) {
        setSettingsMsg(settingsUsersMsg, String(e), true);
      }
    }

    async function patchUser(username, patch) {
      if (settingsBusy) return;
      settingsBusy = true;
      setSettingsMsg(settingsUsersMsg, t('settings.saving'));
      try {
        const r = await fetch('/api/users/' + encodeURIComponent(username), {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          setSettingsMsg(settingsUsersMsg, j.error || ('HTTP ' + r.status), true);
        } else {
          setSettingsMsg(settingsUsersMsg, t('settings.saved'));
        }
        await refreshSettingsUsers();
      } catch (e) {
        setSettingsMsg(settingsUsersMsg, String(e), true);
      } finally {
        settingsBusy = false;
      }
    }

    async function deleteUser(username) {
      if (settingsBusy) return;
      settingsBusy = true;
      setSettingsMsg(settingsUsersMsg, t('settings.saving'));
      try {
        const r = await fetch('/api/users/' + encodeURIComponent(username), {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          setSettingsMsg(settingsUsersMsg, j.error || ('HTTP ' + r.status), true);
        } else {
          setSettingsMsg(settingsUsersMsg, t('settings.deleted'));
        }
        await refreshSettingsUsers();
      } catch (e) {
        setSettingsMsg(settingsUsersMsg, String(e), true);
      } finally {
        settingsBusy = false;
      }
    }

    async function refreshSettingsConfig() {
      setSettingsMsg(settingsConfigMsg, '');
      try {
        const j = await fetch('/api/runtime/config', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.json());
        if (!j.ok) {
          setSettingsMsg(settingsConfigMsg, j.error || 'config error', true);
          return j;
        }
        settingsRoots = j.roots || {};
        if (settingsConfigCurrent) settingsConfigCurrent.textContent = j.path || '—';
        if (settingsConfigPath && !settingsConfigPath.value) {
          settingsConfigPath.value = j.path || '';
        } else if (settingsConfigPath && j.path && !settingsConfigPath.dataset.touched) {
          settingsConfigPath.value = j.path;
        }
        await refreshArmHomeState();
        return j;
      } catch (e) {
        setSettingsMsg(settingsConfigMsg, String(e), true);
        return null;
      }
    }

    function relParts(root, absPath) {
      const r = String(root || '').replace(/[/\\\\]+$/g, '');
      const p = String(absPath || '').replace(/[/\\\\]+$/g, '');
      if (!p || p === r) return [];
      const slash = r.indexOf('\\\\') >= 0 ? '\\\\' : '/';
      const prefix = r.endsWith('/') || r.endsWith('\\\\') ? r : (r + slash);
      if (!(p === r || p.startsWith(prefix))) return [];
      return p.slice(r.length).split(/[/\\\\]/).filter(Boolean);
    }

    async function fetchFsChildren(rootKey, path, rootPath) {
      const qs = new URLSearchParams({ root: rootKey || 'workspace' });
      if (path) qs.set('path', path);
      if (rootPath) qs.set('rootPath', rootPath);
      const r = await fetch('/api/fs/children?' + qs.toString(), { credentials: 'same-origin', cache: 'no-store' });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
      return j;
    }

    async function buildPickerColumns(rootKey, root, targetPath) {
      const rootRes = await fetchFsChildren(rootKey, root, root);
      const columns = [rootRes.entries || []];
      let selectedPath = rootRes.path;
      const parts = relParts(root, targetPath);
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const parentCol = columns[columns.length - 1] || [];
        const hit = parentCol.find((e) => e.name === part && e.isDir);
        if (!hit) break;
        const childRes = await fetchFsChildren(rootKey, hit.path, root);
        columns.push(childRes.entries || []);
        selectedPath = childRes.path;
      }
      if (targetPath && String(targetPath).startsWith(String(root))) {
        selectedPath = targetPath;
      }
      return { columns, selectedPath };
    }

    function renderPathPicker() {
      const cascade = document.getElementById('pathPickerCascade');
      const draftEl = document.getElementById('pathPickerDraft');
      const rootCode = document.getElementById('pathPickerRootCode');
      const errEl = document.getElementById('pathPickerErr');
      if (rootCode) rootCode.textContent = pathPicker.rootPath || '';
      if (draftEl) draftEl.value = pathPicker.draft || '';
      if (!cascade) return;
      cascade.innerHTML = '';
      if (!pathPicker.columns.length) {
        const loading = document.createElement('div');
        loading.className = 'path-picker-loading';
        loading.textContent = t('pathPicker.loading');
        cascade.appendChild(loading);
        return;
      }
      pathPicker.columns.forEach((col, colIndex) => {
        const ul = document.createElement('ul');
        ul.className = 'path-picker-col';
        (col || []).forEach((entry) => {
          const li = document.createElement('li');
          const btn = document.createElement('button');
          btn.type = 'button';
          const active = entry.path === pathPicker.activePath || entry.path === pathPicker.draft;
          btn.className = 'path-picker-item' + (active ? ' active' : '') + (entry.isDir ? ' dir' : ' file');
          const icon = document.createElement('span');
          icon.className = 'path-picker-icon';
          icon.textContent = entry.isDir ? '[D]' : '[F]';
          const name = document.createElement('span');
          name.className = 'path-picker-name';
          name.textContent = entry.name;
          btn.appendChild(icon);
          btn.appendChild(name);
          btn.addEventListener('click', () => onPathPickerEntry(colIndex, entry));
          li.appendChild(btn);
          ul.appendChild(li);
        });
        cascade.appendChild(ul);
      });
      if (errEl) {
        if (pathPicker.err) {
          errEl.hidden = false;
          errEl.textContent = pathPicker.err;
        } else {
          errEl.hidden = true;
          errEl.textContent = '';
        }
      }
    }

    async function onPathPickerEntry(colIndex, entry) {
      pathPicker.err = '';
      pathPicker.activePath = entry.path;
      pathPicker.draft = entry.path;
      const nextCols = pathPicker.columns.slice(0, colIndex + 1);
      if (entry.isDir) {
        try {
          const res = await fetchFsChildren(pathPicker.rootKey, entry.path, pathPicker.rootPath);
          nextCols.push(res.entries || []);
          pathPicker.columns = nextCols;
        } catch (e) {
          pathPicker.err = String(e.message || e);
          pathPicker.columns = nextCols;
        }
        renderPathPicker();
        return;
      }
      if (pathPicker.pathKind === 'file') {
        pathPicker.columns = nextCols;
      }
      renderPathPicker();
    }

    async function openPathPicker(opts) {
      const overlay = document.getElementById('pathPickerOverlay');
      if (!overlay) return;
      opts = opts || {};
      const target = opts.target || 'config';
      const pathKind = opts.pathKind || (target === 'episode' ? 'dir' : 'file');
      const titleKey = opts.titleKey || (
        target === 'episode' ? 'pathPicker.titleEpisode'
          : (target === 'cameraMap' ? 'pathPicker.titleCameraMap' : 'pathPicker.title')
      );
      await ensureSettingsRoots();
      let seed = opts.seed;
      if (seed == null || seed === '') {
        if (target === 'episode') {
          seed = (ppEpisode && ppEpisode.value) || '';
        } else if (target === 'cameraMap') {
          seed = (ppCameraMap && ppCameraMap.value) || '';
        } else {
          seed = (settingsConfigPath && settingsConfigPath.value)
            || (settingsConfigCurrent && settingsConfigCurrent.textContent)
            || '';
          if (seed === '—') seed = '';
        }
      }
      // Sandbox root = parent of project root (settingsRoots.workspace). Do not
      // shrink it to the seed file's parent — that only drives cascade focus.
      let rootKey = 'workspace';
      let rootPath = settingsRoots.workspace || '';
      if (settingsRoots.user && seed && seed.indexOf(settingsRoots.user) === 0) {
        rootKey = 'user';
        rootPath = settingsRoots.user;
      } else if (settingsRoots.home && seed && seed.indexOf(settingsRoots.home) === 0
                 && !(settingsRoots.workspace && seed.indexOf(settingsRoots.workspace) === 0)) {
        rootKey = 'home';
        rootPath = settingsRoots.home;
      } else if (settingsRoots.workspace) {
        rootKey = 'workspace';
        rootPath = settingsRoots.workspace;
      } else if (settingsRoots.configs) {
        rootKey = 'configs';
        rootPath = settingsRoots.configs;
      }
      pathPicker.target = target;
      pathPicker.rootKey = rootKey;
      pathPicker.rootPath = rootPath;
      pathPicker.pathKind = pathKind;
      pathPicker.draft = seed || pathPicker.rootPath;
      pathPicker.activePath = pathPicker.draft;
      pathPicker.columns = [];
      pathPicker.err = '';
      const titleEl = document.getElementById('pathPickerTitle');
      if (titleEl) titleEl.textContent = t(titleKey);
      const draftEl = document.getElementById('pathPickerDraft');
      if (draftEl) {
        draftEl.placeholder = t(pathKind === 'dir' ? 'pathPicker.dirPlaceholder' : 'pathPicker.filePlaceholder');
      }
      overlay.classList.add('show');
      renderPathPicker();
      try {
        const built = await buildPickerColumns(pathPicker.rootKey, pathPicker.rootPath, pathPicker.draft);
        pathPicker.columns = built.columns;
        pathPicker.activePath = built.selectedPath;
        if (!pathPicker.draft) pathPicker.draft = built.selectedPath;
        renderPathPicker();
      } catch (e) {
        pathPicker.err = String(e.message || e);
        renderPathPicker();
      }
    }

    function closePathPicker() {
      const overlay = document.getElementById('pathPickerOverlay');
      if (overlay) overlay.classList.remove('show');
    }

    async function applySelectedConfig() {
      const path = ((settingsConfigPath && settingsConfigPath.value) || '').trim();
      if (!path) {
        setSettingsMsg(settingsConfigMsg, t('settings.config_need_path'), true);
        return;
      }
      if (!confirm(t('settings.config_confirm', { path: path }))) return;
      setSettingsMsg(settingsConfigMsg, t('settings.config_restarting'));
      const btn = document.getElementById('btnSettingsApplyConfig');
      if (btn) btn.disabled = true;
      try {
        const r = await fetch('/api/runtime/apply-config', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) {
          setSettingsMsg(settingsConfigMsg, j.error || ('HTTP ' + r.status), true);
          if (btn) btn.disabled = false;
          return;
        }
        setSettingsMsg(settingsConfigMsg, t('settings.config_restarting'));
        // Wait for backend to come back, then reload UI.
        const started = Date.now();
        const poll = async () => {
          if (Date.now() - started > 45000) {
            setSettingsMsg(settingsConfigMsg, t('settings.config_restart_timeout'), true);
            if (btn) btn.disabled = false;
            return;
          }
          try {
            await new Promise((res) => setTimeout(res, 800));
            const h = await fetch('/api/health', { cache: 'no-store' });
            if (h.ok) {
              location.reload();
              return;
            }
          } catch (e) {}
          poll();
        };
        setTimeout(poll, 1200);
      } catch (e) {
        // Connection drop during restart is expected — keep polling.
        setSettingsMsg(settingsConfigMsg, t('settings.config_restarting'));
        const started = Date.now();
        const poll = async () => {
          if (Date.now() - started > 45000) {
            setSettingsMsg(settingsConfigMsg, t('settings.config_restart_timeout'), true);
            if (btn) btn.disabled = false;
            return;
          }
          try {
            await new Promise((res) => setTimeout(res, 800));
            const h = await fetch('/api/health', { cache: 'no-store' });
            if (h.ok) {
              location.reload();
              return;
            }
          } catch (err) {}
          poll();
        };
        setTimeout(poll, 1200);
      }
    }

    async function openSettings() {
      if (document.documentElement.getAttribute('data-embed') === '1') return;
      if (!settingsModal) return;
      switchSettingsTab('appearance');
      settingsBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      settingsModal.classList.add('show');
      if (btnSettingsClose) btnSettingsClose.focus();
      await refreshSettingsAuth();
      await refreshSettingsConfig();
      await refreshSettingsUsers();
    }

    function closeSettings() {
      if (settingsModal) settingsModal.classList.remove('show');
      document.body.style.overflow = settingsBodyOverflow || '';
      closePathPicker();
    }

    function onSettingsKeydown(e) {
      if (e.key === 'Escape' && settingsModal && settingsModal.classList.contains('show')) {
        e.preventDefault();
        closeSettings();
      }
    }
    document.addEventListener('keydown', onSettingsKeydown);

    if (btnSettings) btnSettings.addEventListener('click', () => openSettings());
    if (btnSettingsClose) btnSettingsClose.addEventListener('click', closeSettings);
    if (btnSettingsDone) btnSettingsDone.addEventListener('click', closeSettings);
    if (settingsModal) {
      settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) closeSettings();
      });
    }
    if (settingsDialog) {
      settingsDialog.addEventListener('click', (e) => e.stopPropagation());
    }
    if (settingsNavAppearance) settingsNavAppearance.addEventListener('click', () => switchSettingsTab('appearance'));
    if (settingsNavLanguage) settingsNavLanguage.addEventListener('click', () => switchSettingsTab('language'));
    if (settingsNavSensors) {
      settingsNavSensors.addEventListener('click', () => {
        switchSettingsTab('sensors');
        syncSensorsEmbedStatusUi();
      });
    }
    if (settingsNavAuth) settingsNavAuth.addEventListener('click', () => switchSettingsTab('auth'));
    if (settingsNavConfig) {
      settingsNavConfig.addEventListener('click', async () => {
        switchSettingsTab('config');
        await refreshSettingsConfig();
      });
    }
    if (settingsNavUsers) {
      settingsNavUsers.addEventListener('click', async () => {
        switchSettingsTab('users');
        await refreshSettingsAuth();
        await refreshSettingsUsers();
      });
    }
    if (settingsSensorsUrl) {
      settingsSensorsUrl.addEventListener('input', () => {
        if (sensorsUrlDraftTimer) clearTimeout(sensorsUrlDraftTimer);
        sensorsUrlDraftTimer = setTimeout(() => {
          const next = normalizeSensorsEmbedUrl(settingsSensorsUrl.value);
          if (next !== sensorsEmbed.url) setSensorsEmbedUrl(settingsSensorsUrl.value);
        }, 450);
      });
      settingsSensorsUrl.addEventListener('blur', () => {
        if (sensorsUrlDraftTimer) clearTimeout(sensorsUrlDraftTimer);
        const next = normalizeSensorsEmbedUrl(settingsSensorsUrl.value);
        settingsSensorsUrl.value = next;
        if (next !== sensorsEmbed.url) setSensorsEmbedUrl(next);
      });
    }
    const btnSettingsSensorsPing = document.getElementById('btnSettingsSensorsPing');
    if (btnSettingsSensorsPing) {
      btnSettingsSensorsPing.addEventListener('click', () => pingSensorsEmbed());
    }
    document.querySelectorAll('#settingsThemeSeg [data-theme-pref]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const pref = btn.getAttribute('data-theme-pref');
        if (isThemePref(pref)) setAppearancePrefs({ theme: pref });
      });
    });
    document.querySelectorAll('#settingsDensitySeg [data-density-pref]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const pref = btn.getAttribute('data-density-pref');
        if (isDensityPref(pref)) setAppearancePrefs({ density: pref });
      });
    });
    const settingsCompactToggle = document.getElementById('settingsCompactToggle');
    if (settingsCompactToggle) {
      settingsCompactToggle.addEventListener('click', () => {
        setAppearancePrefs({ compact: !appearancePrefs.compact });
      });
    }
    if (settingsConfigPath) {
      settingsConfigPath.addEventListener('input', () => {
        settingsConfigPath.dataset.touched = '1';
      });
    }
    const btnSettingsBrowseConfig = document.getElementById('btnSettingsBrowseConfig');
    if (btnSettingsBrowseConfig) {
      btnSettingsBrowseConfig.addEventListener('click', () => openPathPicker({ target: 'config', pathKind: 'file' }));
    }
    const btnSettingsApplyConfig = document.getElementById('btnSettingsApplyConfig');
    if (btnSettingsApplyConfig) {
      btnSettingsApplyConfig.addEventListener('click', () => applySelectedConfig());
    }
    const settingsHomeMsg = document.getElementById('settingsHomeMsg');
    const settingsHomeJoints = document.getElementById('settingsHomeJoints');
    const settingsHomeDur = document.getElementById('settingsHomeDur');
    const btnSettingsHomeReload = document.getElementById('btnSettingsHomeReload');
    const btnSettingsHomeSave = document.getElementById('btnSettingsHomeSave');
    if (settingsHomeDur) {
      settingsHomeDur.addEventListener('input', updateSettingsHomeDurLabel);
      settingsHomeDur.addEventListener('change', updateSettingsHomeDurLabel);
      updateSettingsHomeDurLabel();
    }
    if (btnSettingsHomeReload) {
      btnSettingsHomeReload.addEventListener('click', async () => {
        const r = await refreshArmHomeState();
        setSettingsMsg(
          settingsHomeMsg,
          r.configured ? t('settings.home_synced') : (r.error || t('arm.home_missing')),
          !r.configured,
        );
      });
    }
    if (btnSettingsHomeSave) {
      btnSettingsHomeSave.addEventListener('click', async () => {
        const raw = ((settingsHomeJoints && settingsHomeJoints.value) || '').trim();
        const parts = raw.split(/[,\s;]+/).filter(Boolean);
        if (parts.length !== 6 || parts.some((x) => !Number.isFinite(Number(x)))) {
          setSettingsMsg(settingsHomeMsg, t('arm.home_missing'), true);
          showAppModal(t('arm.home_bad_title'), t('arm.home_missing'));
          return;
        }
        const joints = parts.map((x) => Number(x));
        const duration_s = homeDurationSecondsFromUi();
        try {
          const setR = await fetch('/api/arm/home/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ joints_rad: joints, duration_s: duration_s }),
          }).then((x) => x.json());
          if (!setR.ok) {
            setSettingsMsg(settingsHomeMsg, setR.error || 'set failed', true);
            showAppModal(t('arm.home_bad_title'), setR.error || JSON.stringify(setR));
            return;
          }
          window.__armHome = setR;
          syncSettingsHomeFromState(setR);
          const path = (settingsConfigPath && settingsConfigPath.value) || null;
          const saveR = await fetch('/api/arm/home/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path }),
          }).then((x) => x.json());
          window.__armHome = saveR;
          syncSettingsHomeFromState(saveR);
          if (!saveR.ok) {
            setSettingsMsg(settingsHomeMsg, saveR.error || 'save failed', true);
            showAppModal(t('arm.home_bad_title'), saveR.error || JSON.stringify(saveR));
            return;
          }
          setSettingsMsg(settingsHomeMsg, saveR.message || t('settings.home_saved'));
        } catch (e) {
          setSettingsMsg(settingsHomeMsg, String(e), true);
          showAppModal(t('arm.home_bad_title'), String(e));
        }
      });
    }
    const pathPickerOverlay = document.getElementById('pathPickerOverlay');
    const pathPickerDialog = document.getElementById('pathPickerDialog');
    const pathPickerClose = document.getElementById('pathPickerClose');
    const pathPickerCancel = document.getElementById('pathPickerCancel');
    const pathPickerConfirm = document.getElementById('pathPickerConfirm');
    const pathPickerDraft = document.getElementById('pathPickerDraft');
    if (pathPickerOverlay) {
      pathPickerOverlay.addEventListener('click', (e) => {
        if (e.target === pathPickerOverlay) closePathPicker();
      });
    }
    if (pathPickerDialog) {
      pathPickerDialog.addEventListener('click', (e) => e.stopPropagation());
    }
    if (pathPickerClose) pathPickerClose.addEventListener('click', closePathPicker);
    if (pathPickerCancel) pathPickerCancel.addEventListener('click', closePathPicker);
    if (pathPickerDraft) {
      pathPickerDraft.addEventListener('input', () => {
        pathPicker.draft = pathPickerDraft.value;
      });
    }
    if (pathPickerConfirm) {
      pathPickerConfirm.addEventListener('click', () => {
        const val = (pathPicker.draft || (pathPickerDraft && pathPickerDraft.value) || '').trim();
        const target = pathPicker.target || 'config';
        if (target === 'episode') {
          if (ppEpisode) {
            ppEpisode.value = val;
            if (ppEpisodeSelect) {
              ppEpisodeSelect.value = val;
              if (ppEpisodeSelect.value !== val) ppEpisodeSelect.value = '';
            }
            savePpForm();
            if (val) inspectSelectedEpisode(val);
          }
        } else if (target === 'cameraMap') {
          if (ppCameraMap) {
            ppCameraMap.value = val;
            savePpForm();
          }
        } else if (settingsConfigPath) {
          settingsConfigPath.value = val;
          settingsConfigPath.dataset.touched = '1';
        }
        closePathPicker();
      });
    }
    const btnSettingsRefreshAuth = document.getElementById('btnSettingsRefreshAuth');
    if (btnSettingsRefreshAuth) {
      btnSettingsRefreshAuth.addEventListener('click', async () => {
        await refreshSettingsAuth();
        await refreshSettingsUsers();
      });
    }
    const btnSettingsLogout = document.getElementById('btnSettingsLogout');
    if (btnSettingsLogout) {
      btnSettingsLogout.addEventListener('click', async () => {
        try {
          await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
        } catch (e) {}
        location.href = '/login';
      });
    }
    const btnSettingsAddUser = document.getElementById('btnSettingsAddUser');
    if (btnSettingsAddUser) {
      btnSettingsAddUser.addEventListener('click', async () => {
        if (settingsBusy) return;
        const nameEl = document.getElementById('settingsNewUser');
        const passEl = document.getElementById('settingsNewPass');
        const roleEl = document.getElementById('settingsNewRole');
        const username = (nameEl && nameEl.value || '').trim();
        const password = (passEl && passEl.value) || '';
        const role = (roleEl && roleEl.value) || 'operator';
        if (!username || !password) {
          setSettingsMsg(settingsUsersMsg, t('settings.need_credentials'), true);
          return;
        }
        settingsBusy = true;
        setSettingsMsg(settingsUsersMsg, t('settings.saving'));
        try {
          const r = await fetch('/api/users', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, role }),
          });
          const j = await r.json();
          if (!r.ok || !j.ok) {
            setSettingsMsg(settingsUsersMsg, j.error || ('HTTP ' + r.status), true);
          } else {
            setSettingsMsg(settingsUsersMsg, t('settings.added'));
            if (nameEl) nameEl.value = '';
            if (passEl) passEl.value = '';
            if (roleEl) roleEl.value = 'operator';
            await refreshSettingsUsers();
          }
        } catch (e) {
          setSettingsMsg(settingsUsersMsg, String(e), true);
        } finally {
          settingsBusy = false;
        }
      });
    }

    fetch('/api/status', { credentials: 'same-origin' }).then((r) => r.json()).then((j) => {
      const ok = j.collect_ok !== false && !j.boot_error;
      applyCollectGate(ok, j);
    }).catch(() => {});
    refreshSettingsAuth().catch(() => {});
    if (sensorsEmbedFrame) sensorsEmbedFrame.title = t('sensors.title');

    function readPpForm() {
      const hz = parseFloat(ppMasterHz.value);
      return {
        episode: (ppEpisode.value || '').trim(),
        align: ppAlign.value || 'asof',
        master: (ppMaster.value || '').trim() || '',
        master_hz: Number.isFinite(hz) ? hz : 5,
        require: (ppRequire.value || '').trim(),
        max_match_dt: (ppMaxDt.value || '').trim() || '0.033',
        trim: ppTrim.value || 'both',
        materialize: !!ppMaterialize.checked,
        camera_map: (ppCameraMap.value || '').trim() || null,
        allow_invalid: !!ppAllowInvalid.checked,
      };
    }

    function savePpForm() {
      try {
        localStorage.setItem(LS_PP, JSON.stringify(readPpForm()));
      } catch (e) {}
    }

    function fillMasterSelect(candidates, preferred) {
      const prev = preferred || ppMaster.value || '';
      ppMaster.innerHTML = '';
      const opt0 = document.createElement('option');
      opt0.value = '';
      opt0.textContent = (candidates && candidates.length)
        ? t('pp.master_select')
        : t('pp.master_pick');
      ppMaster.appendChild(opt0);
      (candidates || []).forEach((aid) => {
        const o = document.createElement('option');
        o.value = aid;
        o.textContent = aid;
        ppMaster.appendChild(o);
      });
      if (prev && candidates && candidates.indexOf(prev) >= 0) {
        ppMaster.value = prev;
      } else if (preferred && candidates && candidates.indexOf(preferred) >= 0) {
        ppMaster.value = preferred;
      } else if (candidates && candidates.length === 1) {
        ppMaster.value = candidates[0];
      }
      ppMaster.disabled = !(candidates && candidates.length);
    }

    function loadPpForm(defaults) {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(LS_PP) || 'null');
      } catch (e) {}
      const src = Object.assign({}, defaults || {}, saved || {});
      if (src.align) ppAlign.value = src.align;
      // master filled after episode inspect (select list)
      if (src.master_hz != null) ppMasterHz.value = src.master_hz;
      if (src.require) ppRequire.value = src.require;
      if (src.max_match_dt) ppMaxDt.value = src.max_match_dt;
      if (src.trim) ppTrim.value = src.trim;
      if (src.materialize != null) ppMaterialize.checked = !!src.materialize;
      if (src.allow_invalid != null) ppAllowInvalid.checked = !!src.allow_invalid;
      // Do not restore episode path on boot — leave empty until the user picks one
      // on the Postprocess tab (avoids home-page manifest modals from stale LS).
      ppEpisode.value = '';
      window._ppSavedMaster = src.master || (defaults && defaults.master) || '';
      // Prefer launch-pwd camera-map from server; skip stale AppData/user-data seeds.
      let map = src.camera_map || '';
      const defMap = (defaults && defaults.camera_map) || '';
      if (defMap) {
        const norm = String(map).replace(/\\\\/g, '/');
        const staleUser =
          /sensors-dcs\/configs\/hik_camera_map\.yaml$/i.test(norm) &&
          (/\/\.local\/share\//i.test(norm) ||
            /\/AppData\/Roaming\//i.test(norm) ||
            /\/Application Support\//i.test(norm));
        if (!map || staleUser) map = defMap;
      }
      if (map) ppCameraMap.value = map;
      else if (defMap) ppCameraMap.value = defMap;
    }

    [ppAlign, ppMaster, ppMasterHz, ppRequire, ppMaxDt, ppTrim, ppMaterialize, ppCameraMap, ppAllowInvalid, ppEpisode].forEach((el) => {
      el.addEventListener('change', savePpForm);
      el.addEventListener('blur', savePpForm);
    });

    function fillEpisodeSelect(episodes) {
      const cur = ppEpisode.value;
      ppEpisodeSelect.innerHTML = '';
      const opt0 = document.createElement('option');
      opt0.value = '';
      opt0.textContent = episodes && episodes.length ? t('pp.select_ep') : t('pp.no_ep');
      ppEpisodeSelect.appendChild(opt0);
      (episodes || []).forEach((ep) => {
        const o = document.createElement('option');
        o.value = ep.path;
        let label = ep.name;
        if (ep.valid === false) label += t('pp.tag_discard');
        else if (ep.valid === true) label += t('pp.tag_valid');
        if (ep.has_hik) label += ' · hik';
        else if (ep.has_export) label += ' · export';
        o.textContent = label;
        ppEpisodeSelect.appendChild(o);
      });
      if (cur) {
        ppEpisodeSelect.value = cur;
        if (ppEpisodeSelect.value !== cur) ppEpisodeSelect.value = '';
      }
    }

    function isPostTabActive() {
      return !!(tabPost && tabPost.classList.contains('active'));
    }

    function showManifestBadModal(detail) {
      // Only surface on Postprocess — never on Home / Collect / Sensors.
      if (!isPostTabActive()) return;
      showAppModal(t('pp.manifest_bad_title'), detail || t('pp.manifest_bad'));
    }

    async function inspectSelectedEpisode(path, opts) {
      const silent = opts && opts.silent;
      const want = (path || '').trim();
      if (!want) {
        fillMasterSelect([]);
        return null;
      }
      try {
        const j = await fetch(
          '/api/postprocess/episode?path=' + encodeURIComponent(want),
          { credentials: 'same-origin' }
        ).then((r) => r.json());
        if (!j.ok) {
          const prefer = window._ppSavedMaster || j.suggested_master || '';
          // Still offer agents so allow-invalid / quick-collect discard can pick master.
          fillMasterSelect(j.master_candidates || [], prefer);
          if (!silent) {
            const detail = [j.error, j.note].filter(Boolean).join('\\n');
            showManifestBadModal(detail || t('pp.manifest_bad'));
          }
          return j;
        }
        const prefer = window._ppSavedMaster || j.suggested_master || '';
        fillMasterSelect(j.master_candidates || [], prefer);
        if (ppMaster.value) window._ppSavedMaster = ppMaster.value;
        savePpForm();
        return j;
      } catch (e) {
        fillMasterSelect([]);
        if (!silent) {
          showManifestBadModal(String(e));
        }
        return null;
      }
    }

    async function refreshEpisodeList() {
      try {
        const j = await fetch('/api/postprocess/defaults').then((r) => r.json());
        if (j.ok) {
          fillEpisodeSelect(j.episodes || []);
          if (!ppCameraMap.value && j.camera_map) ppCameraMap.value = j.camera_map;
        }
      } catch (e) {}
    }

    ppEpisodeSelect.addEventListener('change', () => {
      if (ppEpisodeSelect.value) {
        ppEpisode.value = ppEpisodeSelect.value;
        savePpForm();
        inspectSelectedEpisode(ppEpisode.value);
      } else {
        fillMasterSelect([]);
      }
    });
    ppEpisode.addEventListener('change', () => {
      const path = (ppEpisode.value || '').trim();
      if (path) {
        // Sync list selection when path pasted.
        ppEpisodeSelect.value = path;
        if (ppEpisodeSelect.value !== path) ppEpisodeSelect.value = '';
        inspectSelectedEpisode(path);
      } else {
        fillMasterSelect([]);
      }
    });
    document.getElementById('btnPpRefresh').addEventListener('click', () => refreshEpisodeList());
    const btnPpBrowseEpisode = document.getElementById('btnPpBrowseEpisode');
    if (btnPpBrowseEpisode) {
      btnPpBrowseEpisode.addEventListener('click', () => openPathPicker({ target: 'episode', pathKind: 'dir' }));
    }
    const btnPpBrowseCameraMap = document.getElementById('btnPpBrowseCameraMap');
    if (btnPpBrowseCameraMap) {
      btnPpBrowseCameraMap.addEventListener('click', () => openPathPicker({ target: 'cameraMap', pathKind: 'file' }));
    }

    function setPpBusy(on, text) {
      ppBusy = !!on;
      ['btnPpExport', 'btnPpFilter', 'btnPpHik', 'btnPpRunAll', 'btnPpRefresh', 'btnPpBrowseEpisode', 'btnPpBrowseCameraMap'].forEach((id) => {
        const b = document.getElementById(id);
        if (b) b.disabled = !!on;
      });
      if (text) ppHint.textContent = text;
    }

    async function runPostprocess(extra, episodeOverride) {
      const body = Object.assign(readPpForm(), extra || {});
      if (episodeOverride) body.episode = episodeOverride;
      if (!body.episode) {
        ppHint.textContent = t('pp.need_episode');
        return { ok: false, error: 'missing episode' };
      }
      if (!body.master) {
        ppHint.textContent = t('pp.need_master');
        return { ok: false, error: 'missing master' };
      }
      savePpForm();
      setPpBusy(true, t('pp.running'));
      ppLog.textContent = 'running…\\n' + JSON.stringify(body, null, 2);
      try {
        const r = await fetch('/api/postprocess/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const j = await r.json();
        ppLog.textContent = (j.log || '') + '\\n\\n' + JSON.stringify(j, null, 2);
        ppHint.textContent = j.ok ? t('pp.done') : t('pp.fail', { error: j.error || '' });
        return j;
      } catch (e) {
        ppHint.textContent = String(e);
        ppLog.textContent = String(e);
        return { ok: false, error: String(e) };
      } finally {
        setPpBusy(false);
        refreshEpisodeList();
      }
    }

    document.getElementById('btnPpExport').addEventListener('click', () =>
      runPostprocess({ steps: ['export-timeline'] }));
    document.getElementById('btnPpFilter').addEventListener('click', () =>
      runPostprocess({ steps: ['filter-timeline'] }));
    document.getElementById('btnPpHik').addEventListener('click', () =>
      runPostprocess({ steps: ['export-hik-dataset'] }));
    document.getElementById('btnPpRunAll').addEventListener('click', () =>
      runPostprocess({ steps: ['export-timeline', 'filter-timeline', 'export-hik-dataset'] }));

    fetch('/api/postprocess/defaults').then((r) => r.json()).then((j) => {
      if (!j.ok) return;
      loadPpForm(j);
      fillEpisodeSelect(j.episodes || []);
      fillMasterSelect([]);
      // Episode path stays empty until the user selects one on Postprocess.
    }).catch(() => {
      loadPpForm({});
      fillMasterSelect([]);
    });
    const btnExit = document.getElementById('btnExit');
    if (btnExit) {
      btnExit.addEventListener('click', async () => {
        if (!confirm(t('confirm.exit'))) return;
        exitRequested = true;
        btnExit.disabled = true;
        runHint.textContent = t('hint.exiting');
        setConnStatus('shutting down…', 'st-offline');
        try {
          const r = await fetch('/api/shutdown', { method: 'POST' }).then((x) => x.json());
          runHint.textContent = r.ok ? t('hint.exit_ok') : t('hint.exit_fail', { error: r.error || JSON.stringify(r) });
        } catch (e) {
          runHint.textContent = t('hint.exit_sent');
        }
      });
    }

    async function applySaveDir(panel) {
      const ui = panel || collectRec;
      if (!ui || !ui.saveDirInput) return;
      const path = ui.saveDirInput.value.trim();
      if (ui.btnSaveDir) ui.btnSaveDir.disabled = true;
      if (ui.runHint) {
        ui.runHint.textContent = path ? t('hint.save_updating') : t('hint.save_refresh');
      }
      try {
        const r = await fetch('/api/record/save_dir', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ save_dir: path || null }),
        });
        const j = await r.json();
        applyRecordUi(j);
        if (j.ok) {
          ui.saveDirInput.value = '';
          if (ui.runHint) {
            ui.runHint.textContent = t('hint.save_ok', {
              ep: ui.episodeEl ? ui.episodeEl.textContent : '—',
            });
          }
        } else if (j.error && ui.runHint) {
          ui.runHint.textContent = j.error;
        }
      } catch (e) {
        if (ui.runHint) ui.runHint.textContent = String(e);
      } finally {
        if (ui.btnSaveDir) ui.btnSaveDir.disabled = false;
      }
    }
    recordPanels.forEach((panel) => {
      if (!panel.btnSaveDir) return;
      panel.btnSaveDir.addEventListener('click', () => applySaveDir(panel));
      if (panel.saveDirInput) {
        panel.saveDirInput.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') applySaveDir(panel);
        });
      }
    });

    function fmtRate(meas, target) {
      const m = meas == null ? '—' : meas.toFixed(1);
      const t = target == null ? '—' : Number(target).toFixed(0);
      return m + ' / 目标 ' + t;
    }

    function ensureStateCard(agentId, scope) {
      const sc = scope || 'collect';
      const parent = sc === 'infer' ? infAgentsEl : agentsEl;
      if (!parent) return null;
      const id = (sc === 'infer' ? 'inf-card-' : 'card-') + agentId;
      let card = document.getElementById(id);
      if (card) return card;
      card = document.createElement('section');
      card.className = 'agent-card' + (sc === 'infer' ? ' inf-agent-card' : '');
      card.id = id;
      card.dataset.scope = sc;
      card.innerHTML =
        '<h2></h2>' +
        '<div class="agent-meta">' +
        '<div>kind：<strong class="k-kind"></strong></div>' +
        '<div>seq：<strong class="k-seq">—</strong></div>' +
        '<div>后端 hz：<strong class="k-hz">—</strong></div>' +
        '<div>dry_run：<strong class="k-dry">—</strong></div>' +
        '</div>' +
        '<div class="agent-bars"></div>';
      parent.appendChild(card);
      return card;
    }

    function fmtRad(v) {
      if (v == null || !Number.isFinite(Number(v))) return '—';
      const r = Number(v);
      return r.toFixed(3) + ' rad / ' + (r * 180 / Math.PI).toFixed(1) + '°';
    }

    function fmtMeters(v) {
      if (v == null || !Number.isFinite(Number(v))) return '—';
      return Number(v).toFixed(4) + ' m';
    }

    function cartesianItemsFromPose(xyzrpy) {
      if (!Array.isArray(xyzrpy) || xyzrpy.length < 6) return [];
      const labs = ['x', 'y', 'z', 'rx', 'ry', 'rz'];
      const items = [];
      for (let i = 0; i < 6; i++) {
        const v = xyzrpy[i];
        items.push({
          lab: labs[i],
          calText: i < 3 ? fmtMeters(v) : fmtRad(v),
        });
      }
      return items;
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

    function ensurePoseRoot(card) {
      let root = card.querySelector('.agent-pose');
      if (!root) {
        root = document.createElement('div');
        root.className = 'agent-pose';
        root.hidden = true;
        const vals = ensureValsRoot(card);
        vals.insertAdjacentElement('afterend', root);
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

    function renderPoseRow(card, xyzrpy) {
      const poseRoot = ensurePoseRoot(card);
      const items = cartesianItemsFromPose(xyzrpy);
      if (!items.length) {
        poseRoot.hidden = true;
        poseRoot.innerHTML = '';
        return;
      }
      let html = '<span class="pose-tag" title="' + t('arm.pose_tcp') + '">' + t('arm.pose_fk') + '</span>';
      for (const it of items) {
        html += '<span class="jv"><b>' + it.lab + '</b> ' + it.calText + '</span>';
      }
      poseRoot.innerHTML = html;
      poseRoot.hidden = false;
    }

        function renderArmRead(card, frame, hzText) {
      card.querySelector('h2').textContent = 'robot · Read';
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const joints = p.joints_rad || [];
      window.__armReadJoints = joints;
      window.__armReadCartesian = p.cartesian_xyzrpy || null;
      window.__armReadAgentId = frame.agent_id;
      const root = ensureValsRoot(card);
      const cmd = card.querySelector('.grip-cmd');
      if (cmd) cmd.remove();
      renderJointVals(root, joints.map((v, i) => ({ lab: 'j' + i, cal: v })));
      renderPoseRow(card, p.cartesian_xyzrpy);
      if (!card.querySelector('.arm-home-set-row')) {
        const row = document.createElement('div');
        row.className = 'arm-home-set-row';
        row.innerHTML =
          '<button type="button" class="arm-home-set" data-i18n="arm.home_set">设为 home</button>' +
          '<span class="hint arm-home-set-hint"></span>';
        card.appendChild(row);
        applyDomI18n(row);
        const btn = row.querySelector('.arm-home-set');
        const hint = row.querySelector('.arm-home-set-hint');
        if (btn) {
          btn.addEventListener('click', async () => {
            try {
              const r = await fetch('/api/arm/home/set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from_live: true }),
              }).then((x) => x.json());
              window.__armHome = r;
              if (hint) {
                hint.textContent = r.ok
                  ? t('arm.home_set_ok')
                  : t('arm.home_set_fail', { error: r.error || JSON.stringify(r) });
              }
              if (!r.ok) showAppModal(t('arm.home_bad_title'), r.error || JSON.stringify(r));
              else syncSettingsHomeFromState(r);
            } catch (e) {
              if (hint) hint.textContent = String(e);
              showAppModal(t('arm.home_bad_title'), String(e));
            }
          });
        }
      }
    }

    function fmtPose7(arr) {
      if (!Array.isArray(arr) || arr.length < 7) return '—';
      const xyz = arr.slice(0, 3).map((v) => Number(v).toFixed(3)).join(', ');
      const rpy = arr.slice(3, 6).map((v) => Number(v).toFixed(3)).join(', ');
      const g = Number(arr[6]).toFixed(3);
      return 'xyz[' + xyz + '] rpy[' + rpy + '] g=' + g;
    }

    function renderPi05(card, frame, hzText) {
      card.querySelector('h2').textContent = t('infer.card_title');
      card.querySelector('.k-kind').textContent = frame.kind;
      card.querySelector('.k-seq').textContent = String(frame.seq);
      card.querySelector('.k-hz').textContent = hzText;
      card.querySelector('.k-dry').textContent = String(!!(frame.payload && frame.payload.dry_run));
      const p = frame.payload || {};
      const root = ensureValsRoot(card);
      const items = [
        { lab: 'conn', calText: p.connected ? 'on' : 'off' },
        { lab: 'host', calText: (p.host || '—') + ':' + (p.port != null ? p.port : '—') },
        { lab: 'step', calText: p.step != null ? String(p.step) : '—' },
        {
          lab: 'lat',
          calText: p.latency_ms != null && Number.isFinite(Number(p.latency_ms))
            ? (Number(p.latency_ms).toFixed(0) + ' ms')
            : '—',
        },
        { lab: 'ok', calText: p.ok === false ? '✗' : (p.ok ? '✓' : '—') },
        { lab: 'term', calText: p.term_flag != null ? String(p.term_flag) : '—' },
        { lab: 'rej', calText: p.reject_flag != null ? String(p.reject_flag) : '—' },
      ];
      if (p.error) items.push({ lab: 'err', calText: String(p.error).slice(0, 120) });
      if (p.server_text) items.push({ lab: 'txt', calText: String(p.server_text).slice(0, 80) });
      renderJointVals(root, items);
      const poseRoot = ensurePoseRoot(card);
      poseRoot.hidden = false;
      poseRoot.innerHTML =
        '<span class="jv"><b>next_state</b> ' + fmtPose7(p.next_state) + '</span>' +
        '<span class="jv"><b>robot_state</b> ' + fmtPose7(p.robot_state) + '</span>';
      applyPi05PanelFromPayload(Object.assign({ configured: true }, p));
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
        const inferScope = card.dataset.scope === 'infer';
        box.innerHTML =
          '<button type="button" class="grip-init">初始化</button>' +
          (inferScope ? '' : '<button type="button" class="grip-sync">同步</button>') +
          '<label>position_norm</label>' +
          '<input type="number" step="0.01" min="0" max="0.637" value="0.32" class="grip-norm" />' +
          '<button type="button" class="grip-send">下发</button>' +
          (inferScope
            ? '<span class="cmd-hint">推理页不提供 gello→夹爪同步</span>'
            : '<span class="cmd-hint">同步=服务端 gello j6(cal)→夹爪；前端只开关</span>');
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
          if (!syncBtn) {
            setManualEnabled(true);
            return;
          }
          const on = !!(sync && sync.enabled);
          syncBtn.textContent = on ? t('grip.unsync') : t('grip.sync');
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
          if (syncBtn) syncBtn.disabled = true;
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
            if (syncBtn) syncBtn.disabled = false;
            applySyncUi(window.__gripGelloSync || { enabled: syncBtn && syncBtn.dataset.enabled === '1' });
          }
        };
        initBtn.addEventListener('click', async () => {
          runHint.textContent = t('grip.init_busy');
          try {
            const r = await postGrip({ agent_id: frame.agent_id, initialize: true }, initBtn);
            runHint.textContent = r.ok
              ? '夹爪初始化成功，可下发 / 同步'
              : ('夹爪初始化失败：' + (r.error || JSON.stringify(r)));
          } catch (e) {
            runHint.textContent = t('grip.init_err', { error: e });
          }
        });
        if (syncBtn) syncBtn.addEventListener('click', async () => {
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
              runHint.textContent = t('grip.sync_fail', { error: r.error || JSON.stringify(r) });
              return;
            }
            window.__gripGelloSync = r;
            applySyncUi(r);
            runHint.textContent = r.enabled
              ? '已同步：服务端 gello j6(cal) → gripper（数据不经前端）'
              : '已取消同步';
          } catch (e) {
            runHint.textContent = t('grip.sync_err', { error: e });
          } finally {
            syncBtn.disabled = false;
          }
        });
        btn.addEventListener('click', async () => {
          const v = Number(inp.value);
          if (!Number.isFinite(v)) {
            runHint.textContent = t('grip.bad_num');
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
            runHint.textContent = t('grip.cmd_err', { error: e });
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
      window.__armWriteAgentId = frame.agent_id;
      const p = frame.payload || {};
      const n = Math.max(1, Number(p.num_joints) || 6);
      // Prefer live arm-read joints for display (safety: show measured pose).
      const usingRead = !!(window.__armReadJoints && window.__armReadJoints.length);
      const fb = usingRead
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
      const poseSrc = (usingRead && window.__armReadCartesian)
        ? window.__armReadCartesian
        : p.cartesian_xyzrpy;
      renderPoseRow(card, poseSrc);
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
        const inferScope = card.dataset.scope === 'infer';
        box.innerHTML =
          '<div class="arm-tools">' +
            '<button type="button" class="arm-arm">Arm</button>' +
            '<button type="button" class="arm-disarm">Disarm</button>' +
            '<button type="button" class="arm-estop">Estop</button>' +
            (inferScope ? '' :
              '<button type="button" class="arm-gello-sync">同步</button>' +
              '<button type="button" class="arm-gello-teleop">摇操</button>') +
            '<label>delta°</label>' +
            '<input type="range" class="arm-delta" min="0.1" max="5" step="0.1" value="1.0" />' +
            '<span class="arm-delta-val">1.0°</span>' +
            '<span class="arm-armed-tag">idle</span>' +
          '</div>' +
          (inferScope ? '' :
            '<div class="arm-sync-prog">gello→arm：空闲（完成后 gello 不控臂）</div>' +
            '<div class="arm-teleop-prog">摇操：空闲（gello 不控臂）</div>') +
          jogHtml +
          '<div class="arm-abs-row">' +
            '<label class="arm-abs-label" data-i18n="arm.abs_label" data-i18n-title="arm.abs_label_tip" title="双击填入当前关节角">joints</label>' +
            '<input type="text" class="arm-abs-input" data-i18n-placeholder="arm.abs_ph" placeholder="0.00,0.00,0.00,0.00,0.00,0.00" autocomplete="off" spellcheck="false" />' +
            '<button type="button" class="arm-abs-send" data-i18n="arm.abs_send">下发</button>' +
            '<button type="button" class="arm-home-go" data-i18n="arm.home">Home</button>' +
            '<label class="arm-abs-dur" data-i18n-title="arm.abs_dur_hint" title="1–300（100ms–30s）">' +
              '<span data-i18n="arm.abs_dur">到达</span>' +
              '<input type="number" class="arm-abs-dur-range" min="1" max="300" step="1" value="100" />' +
              '<span data-i18n="arm.abs_dur_unit">×100ms</span>' +
              '<span class="arm-abs-dur-val">10.0s</span>' +
            '</label>' +
          '</div>' +
          '<div class="arm-abs-prog" data-i18n="arm.abs_idle">绝对下发：空闲</div>' +
          (inferScope
            ? '<span class="cmd-hint">推理页保留点动/绝对下发展示；不提供 gello 同步/摇操，pi05 不控臂</span>'
            : '<span class="cmd-hint">须先有 robot·Read；Arm →「同步」对齐 →「摇操」跟随；解除后 gello 不再控臂</span>');
        card.appendChild(box);
        applyDomI18n(box);
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
        const absInput = box.querySelector('.arm-abs-input');
        const absSend = box.querySelector('.arm-abs-send');
        const absHome = box.querySelector('.arm-home-go');
        const absLabel = box.querySelector('.arm-abs-label');
        const absDur = box.querySelector('.arm-abs-dur-range');
        const absDurVal = box.querySelector('.arm-abs-dur-val');
        const absProg = box.querySelector('.arm-abs-prog');
        const fillAbsFromRead = () => {
          const ref = window.__armReadJoints;
          if (!Array.isArray(ref) || ref.length < 6) {
            runHint.textContent = t('arm.need_read');
            return false;
          }
          const vals = [];
          for (let i = 0; i < 6; i++) {
            const v = Number(ref[i]);
            if (!Number.isFinite(v)) {
              runHint.textContent = t('arm.need_read');
              return false;
            }
            vals.push(v.toFixed(4));
          }
          if (absInput) absInput.value = vals.join(',');
          runHint.textContent = t('arm.abs_filled');
          return true;
        };
        if (absLabel) {
          absLabel.addEventListener('dblclick', (e) => {
            e.preventDefault();
            fillAbsFromRead();
          });
        }
        const updateDeltaLabel = () => {
          deltaVal.textContent = Number(delta.value).toFixed(1) + '°';
        };
        const updateAbsDurLabel = () => {
          if (!absDur) return;
          let n = Math.round(Number(absDur.value));
          if (!Number.isFinite(n)) n = 100;
          n = Math.max(1, Math.min(300, n));
          if (String(absDur.value) !== String(n)) absDur.value = String(n);
          if (absDurVal) absDurVal.textContent = (n * 0.1).toFixed(1) + 's';
        };
        delta.addEventListener('input', updateDeltaLabel);
        if (absDur) {
          absDur.addEventListener('input', updateAbsDurLabel);
          absDur.addEventListener('change', updateAbsDurLabel);
        }
        updateAbsDurLabel();
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
          if (absInput) absInput.disabled = !on;
          if (absDur) absDur.disabled = !on;
          if (absSend) absSend.disabled = !on;
          if (absHome) absHome.disabled = !on || !!(window.__armAbsRamp && window.__armAbsRamp.enabled);
        };
        box._applyArmUi = (armed) => {
          const hasRead = Array.isArray(window.__armReadJoints) && window.__armReadJoints.length > 0;
          const sync = window.__gelloArmSync || {};
          const teleop = window.__gelloArmTeleop || {};
          const absRamp = window.__armAbsRamp || {};
          const syncing = !!sync.enabled;
          const teleoping = !!teleop.enabled;
          const absRamping = !!absRamp.enabled;
          armedTag.textContent = !hasRead ? 'need read' : (armed ? 'armed' : 'idle');
          setJogEnabled(!!armed && hasRead && !syncing && !teleoping && !absRamping);
          if (absRamping && absSend) {
            absSend.disabled = false;
            absSend.textContent = t('arm.abs_cancel');
          } else if (absSend) {
            absSend.textContent = t('arm.abs_send');
          }
          if (absHome) absHome.disabled = !armed || !hasRead || syncing || teleoping || absRamping;
          armBtn.disabled = !hasRead || syncing || teleoping || absRamping;
          if (syncBtn) {
            syncBtn.disabled = !hasRead || teleoping || absRamping;
            syncBtn.textContent = syncing ? t('arm.unsync') : t('arm.sync');
            syncBtn.classList.toggle('arm-sync-on', syncing);
            syncBtn.dataset.enabled = syncing ? '1' : '0';
          }
          if (teleopBtn) {
            teleopBtn.disabled = !hasRead || syncing || absRamping;
            teleopBtn.textContent = teleoping ? t('arm.unteelop') : t('arm.teleop');
            teleopBtn.classList.toggle('arm-teleop-on', teleoping);
            teleopBtn.dataset.enabled = teleoping ? '1' : '0';
          }
          if (syncProg) {
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
          }
          if (absProg) {
            const hz = Number(absRamp.hz) || 5;
            if (absRamp.phase === 'ramping' && absRamp.ramp_n) {
              const left = Math.max(0, (Number(absRamp.ramp_n) - Number(absRamp.ramp_index || 0)) / hz);
              absProg.textContent =
                t('arm.abs_ramping', {
                  i: absRamp.ramp_index || 0,
                  n: absRamp.ramp_n,
                  left: left.toFixed(1),
                  dur: Number(absRamp.duration_s || 0).toFixed(0),
                });
            } else if (absRamp.phase === 'completed') {
              absProg.textContent = absRamp.message || t('arm.abs_done');
            } else if (absRamp.phase === 'error' && absRamp.last_error) {
              absProg.textContent = t('arm.abs_fail', { error: absRamp.last_error });
            } else if (absRamp.message) {
              absProg.textContent = String(absRamp.message);
            } else {
              absProg.textContent = t('arm.abs_idle');
            }
          }
          if (teleopProg) {
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
          }
        };
        armBtn.addEventListener('click', async () => {
          runHint.textContent = t('arm.arming');
          try {
            const r = await postArm({ arm: true });
            runHint.textContent = r.ok ? t('arm.arm_ok') : t('arm.arm_fail', { error: r.error || JSON.stringify(r) });
            box._applyArmUi(!!(r.ok && r.armed));
          } catch (e) {
            runHint.textContent = t('arm.arm_err', { error: e });
          }
        });
        disarmBtn.addEventListener('click', async () => {
          try {
            const r = await postArm({ disarm: true });
            runHint.textContent = r.ok ? t('arm.disarm_ok') : t('arm.disarm_fail', { error: r.error || JSON.stringify(r) });
            box._applyArmUi(false);
          } catch (e) {
            runHint.textContent = t('arm.disarm_err', { error: e });
          }
        });
        estopBtn.addEventListener('click', async () => {
          try {
            const r = await postArm({ stop: true });
            runHint.textContent = r.ok ? t('arm.estop_ok') : t('arm.estop_fail', { error: r.error || JSON.stringify(r) });
            box._applyArmUi(false);
          } catch (e) {
            runHint.textContent = t('arm.estop_err', { error: e });
          }
        });
        if (syncBtn) syncBtn.addEventListener('click', async () => {
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
              showAppModal(t('arm.sync_modal_ok'), r.message || t('arm.sync_done'));
            }
          } catch (e) {
            runHint.textContent = '同步异常：' + e;
            showAppModal(t('arm.sync_modal_err'), String(e));
          } finally {
            syncBtn.disabled = false;
            box._applyArmUi(!!p.armed);
          }
        });
        if (teleopBtn) teleopBtn.addEventListener('click', async () => {
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
            showAppModal(t('arm.teleop_modal_err'), String(e));
          } finally {
            teleopBtn.disabled = false;
            box._applyArmUi(!!p.armed);
          }
        });
        const jog = async (jointIndex, sign) => {
          const dDeg = Number(delta.value);
          if (!Number.isFinite(dDeg) || dDeg <= 0) {
            runHint.textContent = t('arm.bad_delta');
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
            runHint.textContent = t('arm.jog_err', { error: e });
          }
        };
        box.querySelectorAll('.arm-minus').forEach((b) => {
          b.addEventListener('click', () => jog(Number(b.dataset.j), -1));
        });
        box.querySelectorAll('.arm-plus').forEach((b) => {
          b.addEventListener('click', () => jog(Number(b.dataset.j), +1));
        });
        if (absSend) {
          absSend.addEventListener('click', async () => {
            const absRamp = window.__armAbsRamp || {};
            if (absRamp.enabled) {
              try {
                const r = await postArm({ cancel_abs_ramp: true });
                runHint.textContent = r.ok
                  ? t('arm.abs_cancelled')
                  : t('arm.abs_fail', { error: r.error || JSON.stringify(r) });
              } catch (e) {
                runHint.textContent = t('arm.abs_err', { error: e });
              }
              return;
            }
            const raw = ((absInput && absInput.value) || '').trim();
            const parts = raw.split(/[,\\s;]+/).filter(Boolean);
            if (parts.length !== 6) {
              runHint.textContent = t('arm.abs_need6');
              return;
            }
            const joints = parts.map((x) => Number(x));
            if (joints.some((v) => !Number.isFinite(v))) {
              runHint.textContent = t('arm.abs_bad');
              return;
            }
            const ref = window.__armReadJoints;
            if (!Array.isArray(ref) || ref.length < 6) {
              runHint.textContent = t('arm.need_read');
              return;
            }
            let durN = absDur ? Math.round(Number(absDur.value)) : 100;
            if (!Number.isFinite(durN)) durN = 100;
            durN = Math.max(1, Math.min(300, durN));
            const duration_s = durN * 0.1;
            try {
              const r = await postArm({ joints_rad: joints, duration_s: duration_s });
              runHint.textContent = r.ok
                ? t('arm.abs_ok', { dur: Number(duration_s).toFixed(1) })
                : t('arm.abs_fail', { error: r.error || JSON.stringify(r) });
              if (r.armed === false) box._applyArmUi(false);
              if (r.ok) {
                window.__armAbsRamp = r;
                if (box._applyArmUi) box._applyArmUi(true);
              }
            } catch (e) {
              runHint.textContent = t('arm.abs_err', { error: e });
            }
          });
        }
        if (absHome) {
          absHome.addEventListener('click', async () => {
            const r = await goArmHomeWithDuration(null, absProg || runHint);
            if (r && r.ok && box._applyArmUi) box._applyArmUi(true);
          });
        }
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
      ['collect', 'infer'].forEach((scope) => {
        const cell = (camCellsByScope[scope] || {})[slotKey];
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
      });
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
      const hzTextFront = fmtRate(emaFront, vizTarget);
      recordPanels.forEach((p) => {
        if (p.hzFrontEl) p.hzFrontEl.textContent = hzTextFront;
      });

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
          showAppModal(t('arm.sync_modal_ok'), cur.message || t('arm.sync_done'));
        } else if (prev.phase !== 'error' && cur.phase === 'error' && cur.last_error) {
          showAppModal(t('arm.sync_modal_fail'), cur.last_error);
        }
      }
      if (msg.gello_arm_teleop) {
        const prevT = window.__gelloArmTeleop || {};
        const curT = msg.gello_arm_teleop;
        window.__gelloArmTeleop = curT;
        if (prevT.phase === 'teleop' && curT.phase === 'error' && curT.last_error) {
          showAppModal(t('arm.teleop_modal_off'), curT.last_error);
        }
      }
      if (msg.arm_abs_ramp) {
        window.__armAbsRamp = msg.arm_abs_ramp;
        const sendBtn = document.getElementById('infArmSend');
        const prog = document.getElementById('infArmProg');
        const absRamp = msg.arm_abs_ramp;
        if (sendBtn) {
          sendBtn.textContent = absRamp.enabled ? t('arm.abs_cancel') : t('arm.abs_send');
        }
        if (prog) {
          const hz = Number(absRamp.hz) || 5;
          if (absRamp.phase === 'ramping' && absRamp.ramp_n) {
            const left = Math.max(0, (Number(absRamp.ramp_n) - Number(absRamp.ramp_index || 0)) / hz);
            prog.textContent = t('arm.abs_ramping', {
              i: absRamp.ramp_index || 0,
              n: absRamp.ramp_n,
              left: left.toFixed(1),
              dur: Number(absRamp.duration_s || 0).toFixed(0),
            });
          } else if (absRamp.phase === 'completed') {
            prog.textContent = absRamp.message || t('arm.abs_done');
          } else if (absRamp.phase === 'error' && absRamp.last_error) {
            prog.textContent = t('arm.abs_fail', { error: absRamp.last_error });
          }
        }
      }
      if (msg.arm_home) {
        window.__armHome = msg.arm_home;
        syncSettingsHomeFromState(msg.arm_home);
      }
      frames.forEach((frame) => {
        try {
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
          ['collect', 'infer'].forEach((scope) => {
            // Shared YAML: Collect hides pi05; Infer hides gello.
            if (scope === 'collect' && frame.kind === 'pi05') return;
            if (scope === 'infer' && frame.kind === 'gello') return;
            const card = ensureStateCard(frame.agent_id, scope);
            if (!card) return;
            if (frame.kind === 'gripper_read') {
              renderGripperRead(card, frame, hzText);
            } else if (frame.kind === 'gripper_write') {
              renderGripperWrite(card, frame, hzText);
            } else if (frame.kind === 'arm_write') {
              renderArmWrite(card, frame, hzText);
            } else if (frame.kind === 'arm_read') {
              renderArmRead(card, frame, hzText);
            } else if (frame.kind === 'pi05') {
              if (scope !== 'infer') return;
              renderPi05(card, frame, hzText);
            } else {
              if (scope === 'infer') return;
              renderGello(card, frame, hzText);
            }
          });
        } catch (e) {
          /* one bad agent frame must not block cameras / other cards */
        }
      });

      const rawText = JSON.stringify(msg, (k, v) => {
        if ((k === 'jpeg_b64' || k === 'jpeg_b64_preview') && typeof v === 'string') {
          return '<jpeg ' + v.length + ' chars>';
        }
        return v;
      }, 2);
      recordPanels.forEach((p) => {
        if (p.rawEl) p.rawEl.textContent = rawText;
      });
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
    pi05_connect: Callable[..., dict[str, Any]] | None = None,
    pi05_disconnect: Callable[..., dict[str, Any]] | None = None,
    pi05_status: Callable[..., dict[str, Any]] | None = None,
    pi05_set_prompt: Callable[..., dict[str, Any]] | None = None,
    pi05_step: Callable[..., dict[str, Any]] | None = None,
    arm_home_status: Callable[..., dict[str, Any]] | None = None,
    arm_home_set: Callable[..., dict[str, Any]] | None = None,
    arm_home_save: Callable[..., dict[str, Any]] | None = None,
    arm_home_go: Callable[..., dict[str, Any]] | None = None,
    shutdown: Callable[[], dict[str, Any]] | None = None,
    boot_error: str | None = None,
    boot_box: dict[str, Any] | None = None,
    config_path: str | None = None,
    postprocess_save_dir: str | None = None,
) -> FastAPI:
    from sensors_dcs.auth_session import (
        auth_enabled,
        cookie_header_clear,
        cookie_header_set,
        destroy_session,
        init_auth,
        is_authenticated,
        parse_session_cookie,
        path_requires_auth,
        public_status,
        request_profile,
        sanitize_from,
        try_login,
    )
    from sensors_dcs.login_page import LOGIN_HTML
    from sensors_dcs.ui_i18n import inject_i18n_json
    from fastapi.responses import JSONResponse, RedirectResponse

    init_auth()
    app = FastAPI(title="sensors-dcs viz", version="0.1.0")

    def _current_boot_error() -> str | None:
        """Static boot_error or mutable boot_box['error'] (agent open may fail after UI is up)."""
        if boot_box is not None:
            err = boot_box.get("error")
            if err is None or err is False:
                return None
            return str(err)
        return boot_error

    from pathlib import Path

    from fastapi.staticfiles import StaticFiles
    from sensors_dcs.static_assets import static_root

    _static = static_root()
    if _static.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_static)), name="assets")

    @app.get("/favicon.ico")
    async def favicon_ico():
        """Browsers probe /favicon.ico; keep it public (see auth_session.PUBLIC_EXACT)."""
        from fastapi.responses import FileResponse

        ico = _static / "favicon.ico"
        if ico.is_file():
            return FileResponse(ico, media_type="image/x-icon")
        svg = _static / "favicon.svg"
        if svg.is_file():
            return FileResponse(svg, media_type="image/svg+xml")
        return HTMLResponse("", status_code=404)

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if not path_requires_auth(path):
            return await call_next(request)
        cookie = request.headers.get("cookie")
        if is_authenticated(cookie):
            return await call_next(request)
        if path.startswith("/api/") or path.startswith("/ws"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "unauthorized",
                    "authRequired": True,
                    "loginPath": "/login",
                },
                status_code=401,
            )
        dest = sanitize_from(path)
        return RedirectResponse(url=f"/login?from={dest}", status_code=302)

    @app.on_event("startup")
    async def _startup() -> None:
        hub.bind_loop(asyncio.get_running_loop())

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> str:
        return inject_i18n_json(LOGIN_HTML)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        if path_requires_auth("/") and not is_authenticated(request.headers.get("cookie")):
            return RedirectResponse(url="/login?from=/", status_code=302)
        return HTMLResponse(inject_i18n_json(PREVIEW_HTML))

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        err = _current_boot_error()
        return {
            "ok": err is None,
            "authRequired": auth_enabled(),
            "boot_error": err is not None,
            "collect_ok": err is None,
            # Infer is independent of gello/collect boot lock (shared YAML).
            "infer_ok": True,
        }

    @app.get("/api/auth/status")
    async def auth_status() -> dict[str, Any]:
        return public_status()

    @app.get("/api/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        if not auth_enabled():
            return {
                "ok": True,
                "authRequired": False,
                "authenticated": True,
                "user": None,
            }
        prof = request_profile(request.headers.get("cookie"))
        return {
            "ok": True,
            "authRequired": True,
            "authenticated": prof is not None,
            "user": prof,
            "usernameHint": public_status().get("usernameHint"),
        }

    @app.post("/api/auth/login")
    async def auth_login(req: AuthLoginBody) -> JSONResponse:
        out = try_login(req.username, req.password)
        if not out.get("ok"):
            return JSONResponse(out, status_code=401)
        resp = JSONResponse(
            {
                "ok": True,
                "authRequired": out.get("authRequired", True),
                "user": out.get("user"),
            }
        )
        token = out.get("token")
        if token:
            resp.headers["Set-Cookie"] = cookie_header_set(str(token))
        return resp

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> JSONResponse:
        token = parse_session_cookie(request.headers.get("cookie"))
        destroy_session(token)
        resp = JSONResponse({"ok": True})
        resp.headers["Set-Cookie"] = cookie_header_clear()
        return resp

    def _admin_or_error(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
        from sensors_dcs.auth_session import request_is_admin

        if not auth_enabled():
            return None, JSONResponse(
                {"ok": False, "error": "auth disabled"}, status_code=403
            )
        cookie = request.headers.get("cookie")
        prof = request_profile(cookie)
        if not prof:
            return None, JSONResponse(
                {
                    "ok": False,
                    "error": "unauthorized",
                    "authRequired": True,
                    "loginPath": "/login",
                },
                status_code=401,
            )
        if not request_is_admin(cookie):
            return None, JSONResponse(
                {"ok": False, "error": "admin required"}, status_code=403
            )
        return prof, None

    @app.get("/api/users")
    async def users_list(request: Request) -> JSONResponse:
        from sensors_dcs.users_store import list_users_public

        _prof, err = _admin_or_error(request)
        if err is not None:
            return err
        return JSONResponse({"ok": True, "users": list_users_public()})

    @app.post("/api/users")
    async def users_create(request: Request, req: UserCreateBody) -> JSONResponse:
        from sensors_dcs.users_store import create_user

        _prof, err = _admin_or_error(request)
        if err is not None:
            return err
        try:
            user = create_user(req.username, req.password, req.role)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "user": user})

    @app.put("/api/users/{username}")
    async def users_update(
        username: str, request: Request, req: UserUpdateBody
    ) -> JSONResponse:
        from sensors_dcs.users_store import update_user

        prof, err = _admin_or_error(request)
        if err is not None:
            return err
        if (
            prof
            and username == prof.get("username")
            and req.enabled is False
        ):
            return JSONResponse(
                {"ok": False, "error": "cannot disable yourself"}, status_code=400
            )
        try:
            user = update_user(
                username,
                role=req.role,
                enabled=req.enabled,
                password=req.password,
            )
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "user": user})

    @app.delete("/api/users/{username}")
    async def users_delete(username: str, request: Request) -> JSONResponse:
        from sensors_dcs.users_store import delete_user

        prof, err = _admin_or_error(request)
        if err is not None:
            return err
        if prof and username == prof.get("username"):
            return JSONResponse(
                {"ok": False, "error": "cannot delete yourself"}, status_code=400
            )
        try:
            delete_user(username)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse({"ok": True})

    @app.get("/api/fs/roots")
    async def fs_roots() -> dict[str, Any]:
        from sensors_dcs.fs_browse import browse_roots

        return {"ok": True, "roots": browse_roots()}

    @app.get("/api/sensors-view/ping")
    async def sensors_view_ping(url: str = "") -> dict[str, Any]:
        from sensors_dcs.sensors_embed import ping_sensors_view

        return ping_sensors_view(url)

    @app.get("/api/fs/children")
    async def fs_children(
        root: str = "workspace",
        path: str = "",
        rootPath: str | None = None,
    ) -> JSONResponse:
        from sensors_dcs.fs_browse import list_children

        try:
            payload = list_children(root, path, rootPath)
        except (ValueError, PermissionError, NotADirectoryError, OSError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse(payload)

    @app.get("/api/runtime/config")
    async def runtime_config() -> dict[str, Any]:
        from sensors_dcs.fs_browse import browse_roots
        from sensors_dcs.paths import default_dcs_config

        path = config_path or str(default_dcs_config())
        return {"ok": True, "path": path, "roots": browse_roots()}

    @app.post("/api/runtime/apply-config")
    async def runtime_apply_config(
        request: Request, req: ApplyConfigBody
    ) -> JSONResponse:
        from sensors_dcs.reexec import (
            schedule_reexec,
            validate_dcs_config_file,
            write_active_config_path,
        )

        # Require login when auth is on (any authenticated user may switch config).
        if auth_enabled() and not is_authenticated(request.headers.get("cookie")):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "unauthorized",
                    "authRequired": True,
                    "loginPath": "/login",
                },
                status_code=401,
            )
        try:
            path = validate_dcs_config_file(req.path)
            write_active_config_path(path)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        schedule_reexec(path, shutdown=shutdown)
        return JSONResponse(
            {
                "ok": True,
                "restarting": True,
                "path": str(path),
            }
        )

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        try:
            payload = dict(status_fn() or {})
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "error": str(exc)}
        err = _current_boot_error()
        if err is not None:
            payload.setdefault("ok", False)
            payload["boot_error"] = True
            payload["collect_ok"] = False
            payload["error"] = err
            if config_path is not None:
                payload["config_path"] = config_path
        else:
            payload.setdefault("boot_error", False)
            payload.setdefault("collect_ok", True)
        # Infer tab stays reachable even when Collect is locked (no gello / agent open fail).
        payload["infer_ok"] = True
        return payload

    @app.get("/api/record/status")
    async def record_status() -> dict[str, Any]:
        if _current_boot_error() is not None:
            return {
                "ok": False,
                "error": "collect unavailable (boot error)",
                "state": "idle",
                "boot_error": True,
                "collect_ok": False,
            }
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return {"ok": True, **recorder.status()}

    @app.post("/api/record/start")
    async def record_start(req: RecordStartBody | None = None) -> dict[str, Any]:
        if _current_boot_error() is not None:
            return {"ok": False, "error": "collect unavailable (boot error)", "state": "idle"}
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        body = req or RecordStartBody()
        mode = "infer" if str(body.mode or "").strip().lower() == "infer" else "collect"
        return await asyncio.to_thread(recorder.start, mode=mode)

    @app.post("/api/record/stop")
    async def record_stop(request: Request) -> dict[str, Any]:
        if _current_boot_error() is not None:
            return {"ok": False, "error": "collect unavailable (boot error)", "state": "idle"}
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        # Accept JSON ``{"valid": true|false, "async_flush": true|false}``.
        valid = True
        async_flush = False
        try:
            ctype = (request.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                raw = await request.body()
                if raw and raw.strip():
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        if "valid" in data:
                            valid = bool(data["valid"])
                        if "async_flush" in data:
                            async_flush = bool(data["async_flush"])
        except Exception:  # noqa: BLE001
            valid = True
            async_flush = False
        return await asyncio.to_thread(
            recorder.stop, valid=valid, async_flush=async_flush
        )

    @app.post("/api/record/save_dir")
    async def record_save_dir(req: SaveDirBody) -> dict[str, Any]:
        if _current_boot_error() is not None:
            return {"ok": False, "error": "collect unavailable (boot error)", "state": "idle"}
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return recorder.set_save_dir(req.save_dir)

    _pp_lock = threading.Lock()

    @app.get("/api/postprocess/defaults")
    async def postprocess_defaults() -> dict[str, Any]:
        from sensors_dcs.postprocess_service import postprocess_defaults as _defaults

        save_dir = postprocess_save_dir
        if recorder is not None and _current_boot_error() is None:
            try:
                save_dir = recorder.status().get("save_dir") or save_dir
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, **_defaults(save_dir=save_dir)}

    @app.get("/api/postprocess/episode")
    async def postprocess_episode(path: str = "") -> dict[str, Any]:
        """Inspect episode manifest.json → qualify + master candidate list."""
        from sensors_dcs.postprocess_service import inspect_episode

        return inspect_episode(path)

    @app.post("/api/postprocess/run")
    async def postprocess_run(req: PostprocessBody) -> dict[str, Any]:
        from sensors_dcs.postprocess_service import run_postprocess

        if not _pp_lock.acquire(blocking=False):
            return {"ok": False, "error": "another postprocess job is running"}
        try:
            return await asyncio.to_thread(
                run_postprocess,
                episode=req.episode,
                steps=req.steps,
                align=req.align,
                master=req.master,
                master_hz=req.master_hz,
                require=req.require,
                max_match_dt=req.max_match_dt,
                trim=req.trim,
                materialize=req.materialize,
                camera_map=req.camera_map,
                allow_invalid=req.allow_invalid,
            )
        finally:
            _pp_lock.release()

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
            duration_s=req.duration_s,
            cancel_abs_ramp=bool(req.cancel_abs_ramp),
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

    @app.get("/api/pi05/status")
    async def pi05_status_get() -> dict[str, Any]:
        if pi05_status is None:
            return {"ok": False, "configured": False, "error": "pi05 unavailable"}
        return await asyncio.to_thread(pi05_status)

    @app.post("/api/pi05/connect")
    async def pi05_connect_set(req: Pi05ConnectBody) -> dict[str, Any]:
        if pi05_connect is None:
            return {"ok": False, "configured": False, "error": "pi05 unavailable"}
        return await asyncio.to_thread(
            pi05_connect,
            host=req.host,
            port=req.port,
            agent_id=req.agent_id,
        )

    @app.post("/api/pi05/disconnect")
    async def pi05_disconnect_set(req: Pi05AgentIdBody | None = None) -> dict[str, Any]:
        if pi05_disconnect is None:
            return {"ok": False, "configured": False, "error": "pi05 unavailable"}
        body = req or Pi05AgentIdBody()
        return await asyncio.to_thread(pi05_disconnect, agent_id=body.agent_id)

    @app.post("/api/pi05/prompt")
    async def pi05_prompt_set(req: Pi05PromptBody) -> dict[str, Any]:
        if pi05_set_prompt is None:
            return {"ok": False, "configured": False, "error": "pi05 unavailable"}
        return await asyncio.to_thread(
            pi05_set_prompt, req.prompt, agent_id=req.agent_id
        )

    @app.post("/api/pi05/step")
    async def pi05_step_set(req: Pi05AgentIdBody | None = None) -> dict[str, Any]:
        if pi05_step is None:
            return {"ok": False, "configured": False, "error": "pi05 unavailable"}
        body = req or Pi05AgentIdBody()
        return await asyncio.to_thread(pi05_step, agent_id=body.agent_id)

    @app.get("/api/arm/home")
    async def arm_home_get() -> dict[str, Any]:
        if arm_home_status is None:
            return {"ok": False, "configured": False, "error": "arm home unavailable"}
        return await asyncio.to_thread(arm_home_status)

    @app.post("/api/arm/home/set")
    async def arm_home_set_api(req: ArmHomeSetBody | None = None) -> dict[str, Any]:
        if arm_home_set is None:
            return {"ok": False, "error": "arm home unavailable"}
        body = req or ArmHomeSetBody()
        return await asyncio.to_thread(
            arm_home_set,
            joints_rad=body.joints_rad,
            from_live=bool(body.from_live),
            duration_s=body.duration_s,
        )

    @app.post("/api/arm/home/save")
    async def arm_home_save_api(req: ArmHomeSaveBody | None = None) -> dict[str, Any]:
        if arm_home_save is None:
            return {"ok": False, "error": "arm home unavailable"}
        body = req or ArmHomeSaveBody()
        return await asyncio.to_thread(arm_home_save, path=body.path)

    @app.post("/api/arm/home/go")
    async def arm_home_go_api(req: ArmHomeGoBody | None = None) -> dict[str, Any]:
        if arm_home_go is None:
            return {"ok": False, "error": "arm home unavailable"}
        body = req or ArmHomeGoBody()
        return await asyncio.to_thread(
            arm_home_go,
            duration_s=body.duration_s,
            agent_id=body.agent_id,
        )

    @app.post("/api/shutdown")
    async def api_shutdown() -> dict[str, Any]:
        if shutdown is None:
            return {"ok": False, "error": "shutdown unavailable"}
        return await asyncio.to_thread(shutdown)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        from sensors_dcs.auth_session import is_authenticated as _is_auth

        if not _is_auth(ws.headers.get("cookie")):
            await ws.close(code=4401)
            return
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


def _guess_postprocess_save_dir(config_path: str | None) -> str | None:
    """Best-effort save_dir for postprocess when agents failed to boot."""
    from pathlib import Path

    from sensors_dcs.paths import user_data_dir

    fallback = str((user_data_dir() / "data").resolve())
    if not config_path:
        return fallback
    root = Path(config_path)
    if not root.is_file():
        return fallback
    try:
        import yaml

        from sensors_dcs.config import resolve_save_dir

        data = yaml.safe_load(root.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return fallback
        raw = (data.get("record") or {}).get("save_dir") if isinstance(data.get("record"), dict) else None
        if not raw:
            raw = data.get("save_dir")
        if raw:
            return str(resolve_save_dir(raw, config_file=root))
    except Exception:  # noqa: BLE001
        pass
    return fallback


def create_error_app(
    *,
    error: str,
    config_path: str | None = None,
    shutdown: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    """Full login + postprocess UI when YAML / boot fails — Collect tab locked.

    Replaces the old minimal ERROR_HTML shell so config errors still open a
    usable page (login → 数据后处理) instead of a blank / dead window.
    """
    hub = VizHub()
    cfg = config_path
    err = error

    def status_fn() -> dict[str, Any]:
        return {
            "ok": False,
            "boot_error": True,
            "collect_ok": False,
            "infer_ok": True,
            "error": err,
            "config_path": cfg,
            "agents": [],
        }

    def _shutdown() -> dict[str, Any]:
        if shutdown is not None:
            return shutdown()
        return {"ok": True, "note": "no shutdown hook"}

    return create_viz_app(
        hub,
        status_fn,
        boot_error=error,
        config_path=config_path,
        postprocess_save_dir=_guess_postprocess_save_dir(config_path),
        shutdown=_shutdown,
    )


# Kept for reference / docs screenshots; runtime boot errors use create_error_app → PREVIEW_HTML.
ERROR_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>sensors-dcs · 配置错误</title>
</head>
<body>
  <p>Deprecated shell — use create_error_app (login + postprocess).</p>
</body>
</html>
"""
