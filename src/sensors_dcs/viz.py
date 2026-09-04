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
  <title>sensors-dcs · 采集 / 后处理</title>
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml" />
  <link rel="icon" href="/assets/favicon.ico" sizes="any" />
  <link rel="apple-touch-icon" href="/assets/favicon.png" />
  <link rel="stylesheet" href="/assets/fonts/ibm-plex-sans.css" />
  <style>
    :root {
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
    header #btnSettings {
      flex-shrink: 0;
      appearance: none;
      border: 1px solid var(--border);
      background: var(--chrome);
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
    header #btnSettings:hover {
      border-color: var(--accent);
      background: var(--accent-dim);
    }
    header .header-actions {
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 0.45rem;
      flex-shrink: 0;
    }
    .lang-switch {
      display: inline-flex;
      gap: 0.25rem;
      padding: 0.15rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--chrome);
    }
    .lang-switch .lang-btn {
      appearance: none;
      border: 1px solid transparent;
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      padding: 0.22rem 0.65rem;
      border-radius: 999px;
      cursor: pointer;
    }
    .lang-switch .lang-btn:hover { color: var(--text); }
    .lang-switch .lang-btn.active {
      color: var(--text);
      background: linear-gradient(90deg, var(--spark-dim), var(--accent-dim));
      border-color: rgba(61, 214, 198, 0.4);
    }
    .settings-lang-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 0.85rem;
      align-items: center;
    }
    .settings-lang-row > span { color: var(--muted); min-width: 5rem; }
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
    pre#ppLog, pre#raw {
      margin: 0;
      padding: 0.65rem 0.85rem;
      overflow: auto;
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      font-size: 0.72rem;
      line-height: 1.4;
      color: #c5d0e0;
      font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
    }
    pre#ppLog { max-height: 28vh; min-height: 5rem; white-space: pre-wrap; }
    pre#raw {
      flex-shrink: 0;
      width: 100%;
      max-height: 22vh;
      min-height: 4.5rem;
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
    .settings-modal.modal-card {
      max-width: 40rem;
      width: min(40rem, calc(100vw - 2rem));
      max-height: min(88vh, 40rem);
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      overflow: hidden;
    }
    .settings-modal .settings-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }
    .settings-modal .settings-head h3 { margin: 0; }
    .settings-modal .settings-close {
      border-color: var(--border);
      background: var(--chrome);
      color: var(--muted);
      padding: 0.28rem 0.7rem;
    }
    .settings-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
    }
    .settings-nav button {
      appearance: none;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 0.8rem;
      font-weight: 550;
      padding: 0.32rem 0.85rem;
      border-radius: 999px;
      cursor: pointer;
    }
    .settings-nav button.active {
      color: var(--text);
      border-color: var(--accent);
      background: var(--accent-dim);
    }
    .settings-body {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      display: grid;
      gap: 0.75rem;
    }
    .settings-panel { display: none; gap: 0.65rem; }
    .settings-panel.active { display: grid; }
    .settings-panel .hint {
      margin: 0;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.45;
    }
    .settings-kv {
      display: grid;
      gap: 0.35rem;
      font-size: 0.85rem;
    }
    .settings-kv div {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.75rem;
      align-items: baseline;
    }
    .settings-kv span { color: var(--muted); min-width: 5rem; }
    .settings-kv strong { color: var(--accent); font-weight: 600; }
    .settings-users-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }
    .settings-users-table th,
    .settings-users-table td {
      text-align: left;
      padding: 0.4rem 0.35rem;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }
    .settings-users-table th { color: var(--muted); font-weight: 550; }
    .settings-users-table select,
    .settings-users-table input[type="password"],
    .settings-add input,
    .settings-add select {
      appearance: none;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      font-size: 0.8rem;
      padding: 0.28rem 0.45rem;
      border-radius: 8px;
      max-width: 9rem;
    }
    .settings-users-table button,
    .settings-add button,
    .settings-actions button,
    .settings-config-row button {
      appearance: none;
      border: 1px solid var(--border);
      background: var(--chrome);
      color: var(--text);
      font: inherit;
      font-size: 0.78rem;
      font-weight: 550;
      padding: 0.28rem 0.65rem;
      border-radius: 999px;
      cursor: pointer;
    }
    .settings-users-table button:hover,
    .settings-add button:hover,
    .settings-actions button:hover,
    .settings-config-row button:hover {
      border-color: var(--accent);
      background: var(--accent-dim);
    }
    .settings-config-row button.primary {
      background: linear-gradient(120deg, var(--accent-dim), color-mix(in srgb, var(--spark-dim) 55%, var(--accent-dim)));
      border-color: var(--accent);
    }
    .settings-users-table button:disabled,
    .settings-add button:disabled,
    .settings-config-row button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    .settings-add {
      display: grid;
      gap: 0.5rem;
      padding-top: 0.35rem;
      border-top: 1px solid var(--border);
    }
    .settings-add-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      align-items: center;
    }
    .settings-msg {
      margin: 0;
      font-size: 0.8rem;
      color: var(--spark);
      min-height: 1.2em;
    }
    .settings-msg.err { color: var(--danger); }
    .settings-config-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      align-items: center;
    }
    .settings-config-row input[type="text"] {
      flex: 1 1 16rem;
      min-width: 12rem;
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
      <button type="button" id="btnSettings" data-i18n="home.cta_settings">设置</button>
      <button type="button" class="danger" id="btnExit" data-i18n="header.exit">安全退出</button>
    </div>
  </header>
  <nav class="tabs" role="tablist">
    <button type="button" class="tab active" id="tabBtnHome" data-tab="home" role="tab" aria-selected="true" data-i18n="tab.home">主页</button>
    <button type="button" class="tab" id="tabBtnCollect" data-tab="collect" role="tab" aria-selected="false" data-i18n="tab.collect">数据采集</button>
    <button type="button" class="tab" id="tabBtnPost" data-tab="post" role="tab" aria-selected="false" data-i18n="tab.post">数据后处理</button>
  </nav>
  <div class="boot-banner" id="bootBanner" role="alert" hidden>
    <strong data-i18n="boot.banner_title">配置错误 — 仅后处理可用</strong>
    <span id="bootBannerHint" data-i18n="boot.collect_locked">YAML 配置无效，无法进入数据采集。请修正配置后重新启动。</span>
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
            <li data-i18n="home.tips_3">右上角「设置」可切换语言、退出登录与管理账号；「安全退出」会停录制、关传感器并结束进程。</li>
          </ul>
        </section>
        <section class="pp-card home-cta">
          <h2 data-i18n="home.cta_title">开始使用</h2>
          <p data-i18n="home.cta_body">从下方进入采集或后处理；右上角「设置」可切换语言与管理账号。</p>
          <div class="pp-actions">
            <button type="button" class="primary" id="btnHomeCollect" data-i18n="home.cta_collect">进入数据采集</button>
            <button type="button" id="btnHomePost" data-i18n="home.cta_post">进入数据后处理</button>
          </div>
        </section>
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
            <input type="text" id="ppMaster" value="cam-left" />
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
  <div class="modal-backdrop" id="settingsModal" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
    <div class="modal-card settings-modal">
      <div class="settings-head">
        <h3 id="settingsTitle" data-i18n="settings.title">设置</h3>
        <button type="button" class="settings-close" id="btnSettingsClose" data-i18n="settings.close">关闭</button>
      </div>
      <div class="settings-nav" role="tablist">
        <button type="button" class="active" id="settingsNavAuth" data-settings-tab="auth" data-i18n="settings.nav_auth">鉴权</button>
        <button type="button" id="settingsNavConfig" data-settings-tab="config" data-i18n="settings.nav_config">配置文件</button>
        <button type="button" id="settingsNavUsers" data-settings-tab="users" data-i18n="settings.nav_users" hidden>用户管理</button>
      </div>
      <div class="settings-body">
        <div class="settings-panel active" id="settingsPanelAuth" data-settings-panel="auth">
          <p class="hint" data-i18n="settings.auth_hint">Cookie 会话鉴权（HttpOnly）。关闭鉴权请设环境变量 SENSORS_DCS_AUTH_DISABLED=1。</p>
          <div class="settings-kv">
            <div><span data-i18n="settings.auth_status">状态</span><strong id="settingsAuthStatus">—</strong></div>
            <div><span data-i18n="settings.auth_user">当前用户</span><strong id="settingsAuthUser">—</strong></div>
            <div><span data-i18n="settings.auth_role">角色</span><strong id="settingsAuthRole">—</strong></div>
            <div class="settings-lang-row">
              <span data-i18n="settings.lang_label">界面语言</span>
              <div class="lang-switch" data-i18n-title="lang.title" title="界面语言 / Language">
                <button type="button" class="lang-btn active" data-locale="zh" data-i18n="lang.zh">中文</button>
                <button type="button" class="lang-btn" data-locale="en" data-i18n="lang.en">EN</button>
              </div>
            </div>
          </div>
          <div class="settings-actions">
            <button type="button" id="btnSettingsLogout" data-i18n="settings.logout">退出登录</button>
            <button type="button" id="btnSettingsRefreshAuth" data-i18n="settings.refresh">刷新</button>
          </div>
          <p class="settings-msg" id="settingsAuthMsg"></p>
        </div>
        <div class="settings-panel" id="settingsPanelConfig" data-settings-panel="config">
          <p class="hint" data-i18n="settings.config_hint">选择 DCS 启动 YAML（需含 sensors_config 与 agents）。确认后将写入活动配置并重启前后端。</p>
          <div class="settings-kv">
            <div><span data-i18n="settings.config_current">当前配置</span><strong id="settingsConfigCurrent">—</strong></div>
          </div>
          <div class="settings-config-row">
            <input type="text" id="settingsConfigPath" data-i18n-placeholder="settings.config_path_ph" placeholder="DCS YAML 绝对路径" autocomplete="off" />
            <button type="button" id="btnSettingsBrowseConfig" data-i18n="settings.config_browse">浏览…</button>
            <button type="button" class="primary" id="btnSettingsApplyConfig" data-i18n="settings.config_apply">确认并重启</button>
          </div>
          <p class="settings-msg" id="settingsConfigMsg"></p>
        </div>
        <div class="settings-panel" id="settingsPanelUsers" data-settings-panel="users">
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
                <button type="button" id="btnSettingsAddUser" data-i18n="settings.add_btn">添加</button>
              </div>
            </div>
          </div>
          <p class="settings-msg" id="settingsUsersMsg"></p>
        </div>
      </div>
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
    function setLocale(loc) {
      if (loc !== 'zh' && loc !== 'en') return;
      currentLocale = loc;
      try { localStorage.setItem(LS_LOCALE, loc); } catch (e) {}
      document.documentElement.lang = loc === 'zh' ? 'zh-CN' : 'en';
      document.querySelectorAll('.lang-btn').forEach((btn) => {
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
    }
    document.querySelectorAll('.lang-btn').forEach((btn) => {
      btn.addEventListener('click', () => setLocale(btn.getAttribute('data-locale')));
    });
    setLocale(currentLocale);

    const agentsEl = document.getElementById('agents');
    const camGridEl = document.getElementById('cam-grid');
    const statusEl = document.getElementById('status');
    let exitRequested = false;
    function setConnStatus(text, cls) {
      statusEl.textContent = text;
      statusEl.className = cls || '';
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
    const btnDiscard = document.getElementById('btnDiscard');
    const btnSaveDir = document.getElementById('btnSaveDir');
    const saveDirInput = document.getElementById('saveDirInput');
    const runHint = document.getElementById('runHint');
    const chkQuickCollect = document.getElementById('chkQuickCollect');
    const chkAsyncFlush = document.getElementById('chkAsyncFlush');
    const LS_QUICK = 'dcs.quickCollect';
    const LS_ASYNC = 'dcs.asyncFlush';
    const LS_PP = 'dcs.postprocess';
    try {
      chkQuickCollect.checked = localStorage.getItem(LS_QUICK) === '1';
      if (chkAsyncFlush && localStorage.getItem(LS_ASYNC) === '1') chkAsyncFlush.checked = true;
    } catch (e) {}
    chkQuickCollect.addEventListener('change', () => {
      try {
        localStorage.setItem(LS_QUICK, chkQuickCollect.checked ? '1' : '0');
      } catch (e) {}
    });
    if (chkAsyncFlush) {
      chkAsyncFlush.addEventListener('change', () => {
        try { localStorage.setItem(LS_ASYNC, chkAsyncFlush.checked ? '1' : '0'); } catch (e) {}
      });
    }    let lastMsgT = null, emaFront = null;
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
      window.__lastRecordStatus = rec;
      const st = rec.state || 'idle';
      const flushN = rec.flushing_count || 0;
      recStateEl.textContent = flushN > 0 && st === 'idle'
        ? (st + ' · flush×' + flushN)
        : st;
      saveDirEl.textContent = rec.save_dir || '—';
      if (document.activeElement !== saveDirInput) {
        saveDirInput.placeholder = rec.save_dir || t('save.placeholder');
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
        btnDiscard.disabled = false;
        runHint.textContent = t('hint.recording');
      } else if (st === 'flushing') {
        btnStart.disabled = true;
        btnStop.disabled = true;
        btnDiscard.disabled = true;
        runHint.textContent = t('hint.flushing');
      } else {
        btnStart.disabled = false;
        btnStop.disabled = true;
        btnDiscard.disabled = true;
        if (flushN > 0) {
          runHint.textContent = t('hint.async_flushing', { n: flushN, ep: episodeEl.textContent });
        } else {
          runHint.textContent = t('hint.idle_ep', { ep: episodeEl.textContent });
        }
      }
    }

    async function postRecord(path, body) {
      busy = true;
      btnStart.disabled = true;
      btnStop.disabled = true;
      btnDiscard.disabled = true;
      const isStop = path.indexOf('stop') >= 0;
      const discarding = isStop && body && body.valid === false;
      const asyncFlush = !!(chkAsyncFlush && chkAsyncFlush.checked);
      if (isStop && body && typeof body === 'object') {
        body.async_flush = asyncFlush;
      }
      runHint.textContent = discarding
        ? t('hint.discarding')
        : (isStop ? (asyncFlush ? t('hint.async_stopping') : t('hint.stopping')) : t('hint.starting'));
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
          runHint.textContent = j.error;
        } else if (discarding && j.ok) {
          runHint.textContent = t('hint.discarded');
        }
        if (isStop && j.ok && chkQuickCollect.checked && j.finished_episode_path) {
          const epPath = j.finished_episode_path;
          if (ppEpisode) ppEpisode.value = epPath;
          const runQc = async () => {
            runHint.textContent = discarding
              ? t('hint.qc_discard')
              : t('hint.qc_stop');
            const pp = await runPostprocess({
              steps: ['export-timeline', 'filter-timeline', 'export-hik-dataset'],
              allow_invalid: discarding || (document.getElementById('ppAllowInvalid') || {}).checked,
            }, epPath);
            if (pp && pp.ok) {
              runHint.textContent = t('hint.qc_ok', { path: epPath });
              showAppModal(t('modal.qc_ok'), epPath);
            } else if (pp) {
              runHint.textContent = t('hint.qc_fail', { error: pp.error || 'unknown' });
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
        runHint.textContent = String(e);
      } finally {
        busy = false;
        try {
          const s = await fetch('/api/record/status').then((x) => x.json());
          applyRecordUi(s);
        } catch (e) {}
      }
    }

    btnStart.addEventListener('click', () => postRecord('/api/record/start'));
    btnStop.addEventListener('click', () => postRecord('/api/record/stop', { valid: true }));
    btnDiscard.addEventListener('click', () => {
      if (!confirm(t('confirm.discard'))) return;
      postRecord('/api/record/stop', { valid: false });
    });

    // ---- tabs + postprocess ----
    const tabBtnHome = document.getElementById('tabBtnHome');
    const tabBtnCollect = document.getElementById('tabBtnCollect');
    const tabBtnPost = document.getElementById('tabBtnPost');
    const tabHome = document.getElementById('tab-home');
    const tabCollect = document.getElementById('tab-collect');
    const tabPost = document.getElementById('tab-post');
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
      const which = (name === 'collect' || name === 'post' || name === 'home') ? name : 'home';
      tabBtnHome.classList.toggle('active', which === 'home');
      tabBtnCollect.classList.toggle('active', which === 'collect');
      tabBtnPost.classList.toggle('active', which === 'post');
      tabBtnHome.setAttribute('aria-selected', which === 'home' ? 'true' : 'false');
      tabBtnCollect.setAttribute('aria-selected', which === 'collect' ? 'true' : 'false');
      tabBtnPost.setAttribute('aria-selected', which === 'post' ? 'true' : 'false');
      tabHome.classList.toggle('active', which === 'home');
      tabCollect.classList.toggle('active', which === 'collect');
      tabPost.classList.toggle('active', which === 'post');
    }
    tabBtnHome.addEventListener('click', () => switchTab('home'));
    tabBtnCollect.addEventListener('click', () => switchTab('collect'));
    tabBtnPost.addEventListener('click', () => {
      switchTab('post');
      refreshEpisodeList();
    });
    const btnHomeCollect = document.getElementById('btnHomeCollect');
    const btnHomePost = document.getElementById('btnHomePost');
    if (btnHomeCollect) {
      btnHomeCollect.addEventListener('click', () => switchTab('collect'));
    }
    if (btnHomePost) {
      btnHomePost.addEventListener('click', () => {
        switchTab('post');
        refreshEpisodeList();
      });
    }
    // Default landing tab after login: home
    switchTab('home');

    // ---- settings modal (auth + users) ----
    const settingsModal = document.getElementById('settingsModal');
    const btnSettings = document.getElementById('btnSettings');
    const btnSettingsClose = document.getElementById('btnSettingsClose');
    const settingsNavAuth = document.getElementById('settingsNavAuth');
    const settingsNavConfig = document.getElementById('settingsNavConfig');
    const settingsNavUsers = document.getElementById('settingsNavUsers');
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
    let settingsRoots = {};
    let pathPicker = {
      columns: [],
      activePath: '',
      draft: '',
      rootKey: 'workspace',
      rootPath: '',
      pathKind: 'file',
    };

    function setSettingsMsg(el, text, isErr) {
      if (!el) return;
      el.textContent = text || '';
      el.classList.toggle('err', !!isErr);
    }

    function switchSettingsTab(name) {
      const which = (name === 'users' || name === 'config') ? name : 'auth';
      if (settingsNavAuth) settingsNavAuth.classList.toggle('active', which === 'auth');
      if (settingsNavConfig) settingsNavConfig.classList.toggle('active', which === 'config');
      if (settingsNavUsers) settingsNavUsers.classList.toggle('active', which === 'users');
      if (settingsPanelAuth) settingsPanelAuth.classList.toggle('active', which === 'auth');
      if (settingsPanelConfig) settingsPanelConfig.classList.toggle('active', which === 'config');
      if (settingsPanelUsers) settingsPanelUsers.classList.toggle('active', which === 'users');
    }

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

    async function openPathPicker() {
      const overlay = document.getElementById('pathPickerOverlay');
      if (!overlay) return;
      if (!Object.keys(settingsRoots).length) {
        await refreshSettingsConfig();
      }
      const seed = (settingsConfigPath && settingsConfigPath.value) || (settingsConfigCurrent && settingsConfigCurrent.textContent) || '';
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
      pathPicker.rootKey = rootKey;
      pathPicker.rootPath = rootPath;
      pathPicker.pathKind = 'file';
      pathPicker.draft = seed || pathPicker.rootPath;
      pathPicker.activePath = pathPicker.draft;
      pathPicker.columns = [];
      pathPicker.err = '';
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
      if (!settingsModal) return;
      switchSettingsTab('auth');
      settingsModal.classList.add('show');
      await refreshSettingsAuth();
      await refreshSettingsConfig();
      await refreshSettingsUsers();
    }

    function closeSettings() {
      if (settingsModal) settingsModal.classList.remove('show');
      closePathPicker();
    }

    if (btnSettings) btnSettings.addEventListener('click', () => openSettings());
    if (btnSettingsClose) btnSettingsClose.addEventListener('click', closeSettings);
    if (settingsModal) {
      settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) closeSettings();
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
    if (settingsConfigPath) {
      settingsConfigPath.addEventListener('input', () => {
        settingsConfigPath.dataset.touched = '1';
      });
    }
    const btnSettingsBrowseConfig = document.getElementById('btnSettingsBrowseConfig');
    if (btnSettingsBrowseConfig) {
      btnSettingsBrowseConfig.addEventListener('click', () => openPathPicker());
    }
    const btnSettingsApplyConfig = document.getElementById('btnSettingsApplyConfig');
    if (btnSettingsApplyConfig) {
      btnSettingsApplyConfig.addEventListener('click', () => applySelectedConfig());
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
        if (settingsConfigPath) {
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

    function readPpForm() {
      const hz = parseFloat(ppMasterHz.value);
      return {
        episode: (ppEpisode.value || '').trim(),
        align: ppAlign.value || 'asof',
        master: (ppMaster.value || '').trim() || 'cam-left',
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

    function loadPpForm(defaults) {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(LS_PP) || 'null');
      } catch (e) {}
      const src = Object.assign({}, defaults || {}, saved || {});
      if (src.align) ppAlign.value = src.align;
      if (src.master) ppMaster.value = src.master;
      if (src.master_hz != null) ppMasterHz.value = src.master_hz;
      if (src.require) ppRequire.value = src.require;
      if (src.max_match_dt) ppMaxDt.value = src.max_match_dt;
      if (src.trim) ppTrim.value = src.trim;
      if (src.materialize != null) ppMaterialize.checked = !!src.materialize;
      if (src.camera_map) ppCameraMap.value = src.camera_map;
      else if (defaults && defaults.camera_map) ppCameraMap.value = defaults.camera_map;
      if (src.allow_invalid != null) ppAllowInvalid.checked = !!src.allow_invalid;
      if (src.episode) ppEpisode.value = src.episode;
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
      }
    });
    document.getElementById('btnPpRefresh').addEventListener('click', () => refreshEpisodeList());

    function setPpBusy(on, text) {
      ppBusy = !!on;
      ['btnPpExport', 'btnPpFilter', 'btnPpHik', 'btnPpRunAll', 'btnPpRefresh'].forEach((id) => {
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
    }).catch(() => loadPpForm({}));
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

    async function applySaveDir() {
      const path = saveDirInput.value.trim();
      btnSaveDir.disabled = true;
      runHint.textContent = path ? t('hint.save_updating') : t('hint.save_refresh');
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
          runHint.textContent = t('hint.save_ok', { ep: episodeEl.textContent });
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
          syncBtn.textContent = syncing ? t('arm.unsync') : t('arm.sync');
          syncBtn.classList.toggle('arm-sync-on', syncing);
          syncBtn.dataset.enabled = syncing ? '1' : '0';
          teleopBtn.disabled = !hasRead || syncing;
          teleopBtn.textContent = teleoping ? t('arm.unteelop') : t('arm.teleop');
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
    async def record_start() -> dict[str, Any]:
        if _current_boot_error() is not None:
            return {"ok": False, "error": "collect unavailable (boot error)", "state": "idle"}
        if recorder is None:
            return {"ok": False, "error": "recorder unavailable", "state": "idle"}
        return recorder.start()

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
