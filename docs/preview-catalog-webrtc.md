# 多站 WebRTC 预览目录 — 开发 Plan

日期：2026-11-01（P0–P7 落地：2026-11-01；Phase E 遥测 DC：2026-11-01）  
状态：**P0–P7 已实现**；**Phase E** 全传感器预览遥测 DataChannel（best-effort）已接上  
关联：[station-multi-machine.md](station-multi-machine.md) · [station-clock-align.md](station-clock-align.md) · [webrtc-embody-fit.md](webrtc-embody-fit.md) · [webrtc-preview-agent.md](webrtc-preview-agent.md)  
上位机代码：[`../../webrtc-preview/`](../../webrtc-preview/)（风格参照 `computer-configuration-scan/pyqt-web-software-style`）

## 定案（不可漂移）

| # | 议题 | 定案 |
|---|------|------|
| 1 | 主站合成物 | **预览目录**（preview catalog），**不是**可执行 DCS / sensors YAML |
| 2 | 媒体拓扑 | 预览软件 **各站直连** WebRTC（不经 Peer、一期不做 SFU / 主站中继） |
| 3 | 合成 API | 必须给出可连接的 **stream 清单**（含 `signaling_url`） |
| 4 | 联合采集 | **不得破坏**现有 Peer barrier 多机开停录 / 本地落盘 / fail-stop |
| 5 | 非视频传感器 | **每个**非相机 agent 一条 catalog 流（与相机平级）+ 各自 DataChannel；**允许丢**；**不进** recorder / 不替代 barrier |

与 [station-multi-machine.md](station-multi-machine.md) 对齐：

- Peer = **控制面**（hello / 心跳 / PREPARE→COMMIT）；**不做**热路径推流。
- hello **不覆盖**主站运行 YAML；本方案仅附加可选 `preview_catalog`，只进预览缓存。
- WebRTC = **可选预览媒体面**；`WebrtcPreviewAgent` 不进 recorder。

---

## 0. 目标 / 非目标

| 目标 | 非目标 |
|------|--------|
| 独立预览端按主站合成目录连多站相机 | 主站合并 YAML 后驱动本机 Orchestrator |
| 主站 `GET` 实时预览目录 + stream 清单 | Peer TCP 上传视频 / DataChannel 替代 barrier |
| 各站现有 `/api/webrtc/preview/offer` 被预览直连 | SFU / 主站中继转码（一期不做） |
| 采集/推理页「打开预览」深链（带主站基址） | 改 episode 落盘、`session_id`、相对 τ 对齐 |

**硬不变量（回归必过）：**

1. 无 `station` / 单机 master / 无从站 → 行为与现网一致。
2. 有从站时 `PREPARE→ACK→COMMIT` 开停录、fail-stop、本地落盘不变。
3. `WebrtcPreviewAgent` 仍不进 recorder；`suppress_jpeg_ws` 仅在存在活跃 WebRTC peer 时生效（语义不因目录改）。
4. hello 增加的字段只进 **preview 缓存**，不写回 `DcsConfig`、不触发 `apply-config`。

---

## 1. 目标架构

```text
                    ┌─────────────────────────────────────┐
  预览软件 ─────────│ GET /api/preview/catalog  (仅主站)   │
    │               │  → stations[] + streams[]           │
    │               └──────────────┬──────────────────────┘
    │                              │ 由 hello.preview_catalog 合成
    │         station Peer (控制面，既有)   │
    │    slave ──hello(+preview_catalog)──▶ master 缓存/断连删除
    │
    ├── POST http://{master_advertise}:{viz_port}/api/webrtc/preview/offer
    └── POST http://{slave_advertise}:{viz_port}/api/webrtc/preview/offer
         （媒体面：各站直连，不经 Peer）
```

职责拆分：

| 组件 | 职责 |
|------|------|
| Peer | 连接、barrier、latency；顺带捎带 `preview_catalog` 元数据 |
| 主站 preview API | 只读合成目录 |
| 各站 webrtc agent + viz offer | 媒体与信令 |
| 预览软件 | 拉目录 → 按 stream 清单对各站建 RTCPeerConnection |

---

## 2. 数据契约

### 2.1 单站 `preview_catalog`（hello 捎带 / 本机自建）

```json
{
  "schema": "sensors-dcs.preview_catalog.v1",
  "station_id": "cell-b",
  "role": "slave",
  "advertise_ip": "192.168.1.11",
  "viz_base": "http://192.168.1.11:7011",
  "viz_port": 7011,
  "webrtc_signaling": {
    "offer_path": "/api/webrtc/preview/offer",
    "status_path": "/api/webrtc/preview/status"
  },
  "config_rev": "<hash of relevant yaml slice>",
  "streams": [
    {
      "stream_id": "cell-b/cam_left",
      "station_id": "cell-b",
      "agent_id": "cam_left",
      "kind": "camera",
      "label": "cam_left",
      "signaling_url": "http://192.168.1.11:7011/api/webrtc/preview/offer",
      "offer_body_hint": { "agent_id": "cam_left" },
      "enabled": true
    }
  ],
  "webrtc_agent": {
    "id": "webrtc_preview",
    "available": true,
    "backend": "aiortc",
    "camera_agent_ids": ["cam_left", "cam_right"],
    "telemetry_enabled": true,
    "telemetry_hz": 15,
    "telemetry_data_channel": "sensors-preview"
  }
}
```

要点：

- **目录 ≠ 完整 DCS YAML**；不含串口 / 臂 IP / 账号等敏感驱动块。
- `stream_id` 全局唯一：`{station_id}/{agent_id}`；相机 `media: video`，其它传感器 `media: datachannel`，**各自一条、平级勾选**。
- `signaling_url` 用 `advertise_ip` + 本机 `runtime.viz_port` 拼；禁止用 `0.0.0.0` / `127.0.0.1` 对外宣告（loopback 仅单机自测）。
- 传感器 offer body：`{"telemetry_only": true, "telemetry_agent_id": "<agent_id>"}`；完备落盘仍靠各站 recorder。

### 2.2 主站合成结果 `MergedPreviewCatalog`

```json
{
  "schema": "sensors-dcs.preview_catalog.merged.v1",
  "master_station_id": "cell-a",
  "generated_at": 1.234,
  "stations": [],
  "streams": [],
  "rev": "<hash of stations' config_rev + connection set>"
}
```

**合成函数规则：**

| 规则 | 行为 |
|------|------|
| 键 | `station_id`；同 id 重连 → 覆盖缓存 |
| 断连 | 删该站条目并重算 `streams` / `rev` |
| 冲突 `stream_id` | 不应发生；若发生以 `station_id` 前缀为准并标 `error` |
| 主站自身 | 始终包含本机 catalog（无从站也可用） |
| 不合并 | agents 运行参数、record 路径、infer 配置 |

**协议兼容：** 旧从站无 `preview_catalog` → 主站用既有 `agents[]` **降级推导** camera 流；若无 webrtc agent 则 `enabled=false` 并提示；**不踢连接**。

---

## 3. 代码落点（不扩散）

| 模块 | 改动 | 禁止 |
|------|------|------|
| `station_peer._hello_payload` | 增加可选 `preview_catalog` | 改 barrier / hello_ack 语义 |
| `_PeerConn` | 缓存 `preview_catalog`；`snapshot()` 可读 | 用 catalog 改 `cfg` |
| `_drop_slave` | 删缓存 → catalog `rev` bump | — |
| 新 `preview_catalog.py` | `build_local_catalog(...)` + `merge_catalogs(...)` | 解析整份 YAML 当运行时 |
| `viz.py` | `GET /api/preview/catalog`（主站合并；从站可仅本站或提示去主站） | 动 record / infer 路由 |
| `WebrtcPreviewAgent` | 多 peer 并发；VideoTrack + `sensors-preview` DC；status 暴露 `camera_agent_ids` / telemetry_* | 写入 episode；可靠控制命令 |
| 采集 / 推理 UI | 「打开预览」按钮 | 从站开放写控制 |
| 新目录（如 `preview_app/`）或独立仓库 | 独立预览客户端 | 把预览唯一塞进现有 viz 巨页 |

hello_ack / station status 建议带回主站 `viz_port`（或完整 `catalog_url`），供从站按钮拼深链，避免写死 7011。

---

## 4. 分阶段交付

### P0 — 契约与不变量（文档，~0.5d）

- 本文即契约正文；交叉引用 station / webrtc 文档，消除「从站预览轨必须经主站浏览器」与「各站直连」的表述冲突。
- 写明回归清单：单机录、主从 barrier、本机 webrtc bar。
- **验收：** 三份文档无矛盾。

### P1 — 本机构建器（~1d）

- `build_local_preview_catalog(orch|cfg+agents)`：从 webrtc agent 的 `camera_agent_ids` + 相机 agent 列表生成 `streams[]`。
- 单测：无 webrtc agent → streams 空或 `enabled=false`；advertise 空 → `viz_base` 标 `unset`。
- **验收：** 不启 Peer 也能对本机构建结果做单测。

### P2 — Hello 捎带 + 主站缓存 / 合成（~1–1.5d）

- slave hello 带 `preview_catalog`；master 存 `_PeerConn.preview_catalog`。
- `merge_preview_catalogs(local, slaves)`；断连删除。
- **验收：** 连/断从站，`rev` 与 `streams` 长度变化；**同时跑一次主从开停录**，barrier 路径行为与现网一致。

### P3 — 合成 API（~0.5–1d）

- `GET /api/preview/catalog` → `MergedPreviewCatalog`。
- 可选：`GET /api/preview/streams` 仅为 `streams` 快捷方式。
- 鉴权：与现有 viz cookie 会话一致；文档注明预览软件需同 cookie 或后续 token。
- **验收：** curl 主站可见主+从流；从站 API 行为按文档。

### P4 — WebRTC 多消费者（~1d）

- 本机 viz「连接预览」与外部预览软件可同时 offer；`peer_count` 正确。
- offer 校验 `agent_id` ∈ `camera_agent_ids`。
- **验收：** 两客户端同看一路 cam；断一不影响另一；录制文件无 webrtc 轨。

### P5 — 预览软件 MVP（~2–3d）

- 配置 `master_catalog_url`（或 `http://{ip}:{port}`）→ 拉 catalog → 勾选 stream → 对各 `signaling_url` offer/answer → `<video>` 网格。
- 一期可用轻量 Web（Vite）；桌面壳可后置。
- **验收：** 只配主站地址，能同时出主站 + 从站画面；从站断开后标 offline / 摘流。

### P6 — 「打开预览」按钮（~0.5–1d）

- 主站 / 从站采集页与推理页：打开  
  `preview_app?catalog=http://{master_ip}:{master_viz_port}/api/preview/catalog`  
  （从站用 YAML `station.master_ip` + 主站回显的 viz 端口）。
- **验收：** 从站不填配置点按钮可预览；主站同样。

### P7 — 回归与打磨（~1d）

- 自动化：catalog merge 单测；hello 新旧兼容；offer 多 peer。
- 手工：联合采集全流程 + 预览并行；断从站中录 fail-stop 仍符合现网。
- 安全默认：catalog 无 secrets；CORS / cookie 说明写入本文。

**推荐顺序：** P0 → P1 → P2（并行 barrier 回归）→ P3 → P4 → P5 → P6 → P7。

采集保护方式：媒体与目录全部走新模块 / 新路由；**不改** `barrier_start` / `RecordController` 热路径；P2/P7 强制 barrier 回归。

---

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| `advertise_ip` / 防火墙导致预览连不上从站 viz | catalog 可标探测结果；UI 显示 `signaling_url`；运维开放 `viz_port` |
| 把 merge 误做成运行时配置 | 路径命名 `preview_catalog`；禁止进入 `apply_config` |
| Peer 包变大 | catalog 只含 stream 元数据，禁止塞帧 / 标定大文件 |
| 主站 viz 鉴权挡住预览 | P5 先同机同 cookie；跨机再加 `PREVIEW_TOKEN` / 只读 bearer（单独立项） |
| 与旧叙事「从站预览轨 → 主站浏览器 / SFU」冲突 | **一期以各站直连为准**；SFU / 经主站中继列为明确非目标 |

---

## 6. 回归清单（P0/P7）

- [x] 单机路径未改 `barrier_start` / `RecordController` 热路径（代码审阅）
- [x] 主从连接：hello 含/不含 `preview_catalog` 均可连（`tests/test_preview_catalog_peer.py`）
- [ ] 主从 barrier 开停录；中途从站掉线 fail-stop（沿用既有 `test_station_barrier.py`；联机手工）
- [x] 本机 viz WebRTC bar API 保留；`peer_count` 按 `_PCS` 集合（多消费者）
- [x] 预览软件项目 `webrtc-preview` + DCS「打开预览软件」深链
- [x] catalog 仅 stream 元数据（无串口/臂 IP）；`/api/preview/` 与 `/api/webrtc/preview/` 公开前缀 + CORS

### 实现索引

| 阶段 | 落点 |
|------|------|
| P1 | `src/sensors_dcs/preview_catalog.py` · `tests/test_preview_catalog.py` |
| P2 | `station_peer.py` hello / `_PeerConn.preview_catalog` · `tests/test_preview_catalog_peer.py` |
| P3 | `GET /api/preview/catalog` · `/api/preview/streams` · `/api/preview/open-url` |
| P4 | 既有 `webrtc_preview_media._PCS` 多 peer；offer 校验 `camera_agent_ids` |
| P5–P6 | `autodl-tmp/webrtc-preview/` · viz 顶栏/webrtc 条按钮 |
| P7 | 本文 + 单测；鉴权公开前缀说明见上 |
| Phase E | `webrtc_preview_media` DataChannel · agent snapshot · preview HUD · `webrtc-preview-agent.md` §2.1 |

---

## 7. 与仓内其它文档的关系

| 文档 | 关系 |
|------|------|
| [station-multi-machine.md](station-multi-machine.md) | 控制面不变量；hello 不覆盖运行 YAML；本方案只附加预览目录 |
| [webrtc-embody-fit.md](webrtc-embody-fit.md) | 单机 WebRTC 动机与测分；多机预览以本文「各站直连」为准 |
| [station-clock-align.md](station-clock-align.md) | 无关预览媒体；联合采集时钟策略不变 |
