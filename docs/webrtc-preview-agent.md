# WebRTC Preview Agent — 开发记录

日期起：2026-10-31  
状态：**Phase E 预览遥测 DataChannel 已接上**（相机 VideoTrack + 全传感器 latest-wins JSON DC）  
相关：[webrtc-embody-fit.md](webrtc-embody-fit.md) · [preview-catalog-webrtc.md](preview-catalog-webrtc.md)  
更新：2026-11-01 Phase E（telemetry DataChannel）

---

## 1. 目标与非目标

**目标**

- YAML 配置 `type: webrtc_preview` 即启用预览媒体面；未配置 = 零行为。
- Agent 只负责：**生命周期、参数、状态环、A/B 对比钩子**；码流不进 `RecordController`。
- 与现有 JPEG@WS 可切换对比（`suppress_jpeg_ws`）。
- **预览态转发全部传感器**：相机走 VideoTrack；关节/夹爪/力等走 **一条 DataChannel**（允许丢、latest-wins）。

**非目标**

- 不改 episode 录盘格式；不把 Peer barrier/急停/机器人命令迁到 DataChannel。
- **不保证**遥测完备或可靠；丢包只影响预览 HUD 瞬时值。
- 本阶段不要求公网 TURN；默认 LAN。

---

## 2. 配置面（开关）

```yaml
agents:
  - id: webrtc-preview
    type: webrtc_preview
    hz: 2                    # status 心跳进 ring（非视频帧率）
    buffer_frames: 8
    camera_agent_ids: []     # 空 = 自动订阅全部 realsense
    # camera_agent_ids: [cam-g26562-wrist]
    suppress_jpeg_ws: true   # 开启后 WS 预览去掉 jpeg_b64_preview（控制面减负）
    max_bitrate_kbps: 2500
    preferred_codec: H264    # 意向；stub/软编可能降级
    backend: auto            # auto | stub | aiortc
    # Phase E — 预览遥测（非落盘）
    telemetry_enabled: true
    telemetry_hz: 15         # DataChannel 打包发送上限
    telemetry_agent_ids: []  # 空 = 除本 agent 外全部 peer；可显式列表
```

`sensor_id` **不需要**（与 `pi05` / `gello_reinforce` 同类）。

### 2.1 预览遥测 DataChannel（Phase E）

| 项 | 定案 |
|----|------|
| 通道 | 每个非相机传感器 **一条** catalog 流 `{station}/{agent_id}`（`media: datachannel`），与相机 VideoTrack **平级勾选** |
| 协商 | 浏览器 `createDataChannel('sensors-preview')` + `telemetry_only` + `telemetry_agent_id`；服务端只推该 agent |
| 可靠 | 默认有序 SCTP（小 JSON）；应用层 latest-wins |
| 内容 | 该 agent ring 的 **latest** Frame 摘要（剥 JPEG/depth/大数组） |
| 频率 | `telemetry_hz`（默认 15） |
| 落盘 | **不进** recorder |
| 消费方 | `webrtc-preview` 按流勾选 `arm` / `gello` / …；相机流只负责画面 |

消息 schema：`sensors-dcs.preview_telemetry.v1`（单传感器 peer）：

```json
{
  "schema": "sensors-dcs.preview_telemetry.v1",
  "t_wall": 1.234,
  "agent_id": "arm",
  "agents": {
    "arm": {"kind": "arm", "seq": 12, "t_wall": 1.23, "error": null, "payload": {"q": [0.1]}}
  }
}
```

---

## 3. A/B 对比钩子（Agent 自带）

| 钩子 | 方向 | 字段 / API |
|------|------|------------|
| `source_fps` | 服务端 | 从相机 ring 观察到的新 `seq` 速率 |
| `encode_fps` | 服务端 | 实际送入编码器/替换轨的帧率（stub 时可为 null） |
| `peer_count` | 服务端 | 当前 PeerConnection 数 |
| `bitrate_kbps_est` | 服务端 | 粗估（有 aiortc stats 时更新） |
| `backend` | 服务端 | `stub` / `aiortc` / `missing` |
| `telemetry_enabled` / `telemetry_hz` | 服务端 | DataChannel 预览遥测开关与发送上限 |
| `suppress_jpeg_ws` | 服务端 | 当前是否剥 WS JPEG |
| `client_preview_fps` | 浏览器→服务端 | `POST /api/webrtc/preview/client-metrics` |
| `client_glass_ms` | 浏览器→服务端 | 可选 glass-to-glass 样本 |
| `control_rtt_ms` | 浏览器→服务端 | 测控面时上报 |
| Benchmark 窗 | 双向 | `POST .../benchmark/start\|stop` → 快照 JSON |

**建议对比流程**

1. **A（基线）**：YAML **去掉** webrtc agent；测 30s（FPS / Mbps / 控制 RTT）。  
2. **B（WebRTC）**：加上 agent 且 `suppress_jpeg_ws: true`；同网同浏览器再测 30s。  
3. 导出两次 `benchmark` 快照对比；通过线见 [webrtc-embody-fit.md](webrtc-embody-fit.md) §2.1。

---

## 4. API（信令 + 钩子）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/webrtc/preview/status` | 聚合 status + 钩子 |
| POST | `/api/webrtc/preview/client-metrics` | 浏览器上报预览 FPS 等 |
| POST | `/api/webrtc/preview/benchmark/start` | 开始对比窗 |
| POST | `/api/webrtc/preview/benchmark/stop` | 结束并返回快照 |
| POST | `/api/webrtc/preview/offer` | SDP offer → answer（需 aiortc；否则明确错误） |

未配置 agent 时上述接口返回 `ok: false, error: "webrtc_preview agent not configured"`。

---

## 5. 实施阶段日志

### Phase A — 骨架 + 钩子

- [x] `AgentConfig.type += webrtc_preview` + 字段  
- [x] `WebrtcPreviewAgent`（null sensor、bind_peers、status ring）  
- [x] 录制跳过 `kind=webrtc_preview`  
- [x] `_frame_for_viz` / payload：可剥订阅相机的 JPEG  
- [x] REST 钩子 + offer 占位  
- [x] 单测（无 aiortc）  
- [x] 本文档  

### Phase B — 单路媒体 PoC（2026-10-31）

- [x] optional-dep `webrtc = ["aiortc>=1.6"]`；`webrtc_preview_media.answer_offer`  
- [x] 相机 ring 的 `jpeg_b64` → OpenCV decode → `VideoStreamTrack`（~15 FPS pace）  
- [x] Collect 页 WebRTC 条：选相机 / 连接 / 断开 / 测分 30s / hooks JSON  
- [x] 浏览器 `requestVideoFrameCallback` → `client-metrics`（preview_fps + control RTT）  
- [x] `test_aiortc_answer_offer_roundtrip`（本机 venv 已装 aiortc 1.15）  
- [ ] **人工**：同机 LAN 填 §6 测分表（需真机/浏览器点测）  

**Phase B 已知限制**

- 软编路径：JPEG 再解再压，CPU 重；硬编 / 原生 H.264 另开阶段。  
- 默认 `iceServers: []`（仅 host candidate，适合 LAN）。  
- 前端条仅 Collect 主预览区；Infer 预览页未复用。  
- `backend: auto` 在已装 aiortc 时走媒体；未装则 stub（钩子仍可用）。  

**UI 修正（2026-10-31 晚）**

- 未点「连接预览」时：**不展示**大黑 `<video>`；钩子 JSON 收到 `<details>` 里。  
- `suppress_jpeg_ws`：**仅 peer_count>0 时**才剥 WS JPEG，避免未连接时四格裂图。  
- 剥 JPEG 时格子显示「WebRTC 已接管」提示，而非破图图标。  
- Collect 相机栏 `.cam-section` 纵向滚动；`#cam-grid` 保底高度，避免 WebRTC 视频挤扁 2×2。  

### Phase C — 多路 / 编码预算

- [ ] 2–3 路；降分辨率策略  
- [ ] 控制 WS 堆积回归  

### Phase D — 多机可选

- [x] 与 [preview-catalog-webrtc.md](preview-catalog-webrtc.md) 对齐：各站直连 + catalog（P0–P7）

### Phase E — 全传感器预览遥测 DataChannel（2026-11-01）

- [x] YAML：`telemetry_enabled` / `telemetry_hz` / `telemetry_agent_ids`  
- [x] `WebrtcPreviewAgent.build_telemetry_snapshot()`（剥大图/深度，latest-wins）  
- [x] `webrtc_preview_media`：offer 时建 `sensors-preview` DC + 发送环  
- [x] preview catalog `webrtc_agent` 声明 telemetry 字段  
- [x] `webrtc-preview`：`ondatachannel` → 按站 HUD  
- [x] 单测：snapshot 剥离 jpeg / 保留关节等标量  

**Phase E 明确不做**

- 不替代 episode / Peer barrier / 急停。  
- 不保证每样本送达；多路同站视频会各自带一份同站遥测（可接受冗余）。  

---

## 6. 测分表（待填 · 人工）

操作：A 不配 webrtc agent；B 配上且 `suppress_jpeg_ws: true`，点「连接预览」→「测分 30s」→「结束测分」，把 hooks JSON 关键进表。

| 场景 | 模式 | 预览 FPS | g2g p95 ms | Mbps | 控制 RTT p95 | 备注 |
|------|------|----------|------------|------|--------------|------|
| LAN 单路 | A jpeg@WS | | | | | |
| LAN 单路 | B webrtc | | | | | |

---

## 7. 变更索引

| 路径 | 角色 |
|------|------|
| `src/sensors_dcs/agents/webrtc_preview_agent.py` | Agent + 钩子 + telemetry snapshot + async offer |
| `src/sensors_dcs/webrtc_preview_media.py` | aiortc PeerConnection / VideoTrack / DataChannel |
| `src/sensors_dcs/config.py` | YAML 模型 |
| `src/sensors_dcs/agents/__init__.py` | `build_agent` |
| `src/sensors_dcs/preview_catalog.py` | catalog 声明 telemetry 能力 |
| `src/sensors_dcs/runtime.py` | bind + viz 剥图 + API |
| `src/sensors_dcs/viz.py` | REST + Collect WebRTC UI |
| `src/sensors_dcs/record.py` | 跳过录盘 |
| `src/sensors_dcs/pyproject.toml` | `[project.optional-dependencies] webrtc` |
| `tests/test_webrtc_preview_agent.py` | 单测 |
| `configs/webrtc_preview.example.yaml` | 示例片段 |
| `../../webrtc-preview/` | 上位机消费 DC HUD |

### 启用步骤（开发机）

```bash
pip install -e ".[webrtc]"   # 或: pip install aiortc
# 在 g26562.yaml agents 追加 webrtc_preview.example.yaml 中的片段
# 重启 sensors-dcs → Collect 页出现 WebRTC 条 → 连接预览
```
