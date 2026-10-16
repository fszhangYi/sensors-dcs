# 多机主从站（LAN）方案

日期：2026-10-14（P4 同日收口）  
状态：P0–P4 底线已落地（chrony 文档 + COMMIT 先发紧 barrier）；**不做** Peer 拉取跨站后处理  
时钟专文：[station-clock-align.md](station-clock-align.md)

## 1. 目标与硬约束

在现有单机 `Orchestrator` + FastAPI/`viz.py` + 本地 `RecordController` 之上，增加**可空转的 Peer 层**，支持局域网一主多从协同采集/推理。

**必须守住：**

```text
role 缺省 / master + 无已连接从站
  → 与改前单机路径完全一致
  → peer 不参与 record/infer 控制流
```

验收：同一份不含 `station` 段（或 `role: master` 且无从站）的 YAML，采集 / 推理 / 后处理行为、目录布局、manifest 与改前兼容。

## 2. 已拍板决策

| # | 议题 | 定案 |
|---|------|------|
| 1 | 从站本地预览 WS、传感器 iframe | **允许**（方便现场看图） |
| 2 | 从站本机机械臂/夹爪点动、Home、abs-ramp | **禁止**（避免双控） |
| 3 | 一期数据落盘 | **各站本地落盘**；事后拷贝/`rsync` + `session_id` 对齐；不做采集热路径推流 |
| 4 | 时钟 | **chrony（NTP），不上 PTP**；跨站后处理默认相对 τ；详见 [station-clock-align.md](station-clock-align.md) |
| 5 | 拓扑 | **固定一主多从**；从站不 listen peer；不做从从互连、不做主主切换 |

补充定案（设计讨论）：

- 身份与 `advertise_ip` **仅 YAML 配置，前端只读回显**。
- 从站连主站时上报的是**站元信息 + agents/sensors 摘要**，不覆盖主站运行 YAML。
- 仅 master 接受 peer 入站；若对从站发起「被连接」则失败。
- 有活跃从站时，主站开录/开推理走 **PREPARE → ACK → COMMIT** barrier；超时则 ABORT，主站也不 start。
- **大钟差（甚至 ≥1min）**：采集与 barrier 仍可用；跨站禁止用绝对 `t_wall` 硬并，必须用相对 τ（开录 `t0` 为零时）。
- **P4 底线**：chrony 运维说明 + barrier **先广播 COMMIT、再主站 local start**；录后目录同步 / RTT 补偿仍为可选、未做。
- **跨站后处理数据**：默认拷盘 / rsync / 共享盘；**不做** Peer 自动拉 episode。

## 3. 与现状的关系

| 现状 | 多机用法 |
|------|----------|
| 单进程 Orchestrator，viz 绑 `runtime.viz_host:viz_port` | 各站各自跑一份进程 |
| `runtime.control_port: 7010` 已预留未绑定 | Peer 控制面默认用该端口（可被 `station.peer_port` 覆盖） |
| `site` 为工位标签 | 保留；另加 `station.id` 作机器稳定 ID |
| `t_wall` / `t_mono`、后处理 timeline master | 多站用同一 `session_id` 合并对齐 |
| 无站间协议 | 新增 Peer（TCP/WebSocket JSON） |

## 4. YAML 配置（前端不可改）

```yaml
station:
  role: master              # master | slave；整段省略 ≡ master + peer 不参与控制
  id: cell-a                # 稳定站 ID
  advertise_ip: 192.168.1.10  # 前端回显；从站连主、主站展示用（≠ bind 0.0.0.0）
  peer_port: 7010           # 默认取 runtime.control_port
  # 仅 slave：
  master_ip: 192.168.1.10
  master_port: 7010
```

校验要点：

- `role=slave` 时 `master_ip` 必填。
- `advertise_ip` 建议为可路由局域网地址；空则 status 里标 `unset`（仍允许单机 master）。
- 改身份 / IP = 改 YAML + 现有 `apply-config` 重启。

## 5. 连接与状态机

```text
从站 ──主动连接──▶ 主站 peer_port
主站 listen；从站不接受 peer 入站（或 listen 但一律拒绝）
```

连接态（UI/API）：`idle` | `connecting` | `connected` | `syncing` | `error`

- 握手：从站 → `{station_id, role, advertise_ip, agents[], sensors_summary, clocks}`  
  主站校验 `role==slave` 后缓存；否则断开。
- 心跳：约 1–2s；超时标 offline；**录制中超时 → 主从 fail-stop**。
- 主站 UI：从站列表（id / ip / 态 / 摘要）+「断开」。
- 从站 UI：YAML 中的主站目标 +「连接 / 断开」+ 连接态。

## 6. 权限（从站）

服务端门禁 + 前端置灰（防绕过）。

| 能力 | 从站 |
|------|------|
| 采集/推理 Record start/stop/discard | 禁用（仅响应主站 peer） |
| Infer LOOP / Flow 启动 | 禁用 |
| Arm/Gripper 写、Home、abs-ramp、gello sync/teleop | 禁用 |
| pi05 connect/step | 禁用（只读 status 可留） |
| `save_dir` / apply-config 切主流程 | 禁用或只读 |
| 本地 WS 预览、传感器 iframe | **允许** |
| 后处理浏览本地盘 | 允许只读；跨站合并在主站/离线工具 |

主站：现有能力不变；增加从站面板与「带从站时的同步开录」。

## 7. Barrier 同步开录 / 开推理

```text
主站 UI Start
  → syncing
  → PREPARE{session_id, episode_index, mode, token}
  → 从站：校验 idle / episode 目录未占用 → ACK{ready, t_mono, t_wall}
  → 全员 ready（超时 → ABORT，主站不 start）
  → COMMIT{session_meta含同一 episode_index, t_start_wall?}
  → 各站 RecordController.start（主从同一 episode_NNNNN）
Stop 同理：STOP → ACK → 各自 finalize
```

- `session_id` 与 **`episode_index`** 均由主站分配；有活跃从站时主从打开同名 `episode_NNNNN`。
- manifest 增加可选：`station_id`、`station_role`、`session_id`、`peer_slaves`（主站）、`episode_index`（与目录名一致）。
- 单机/无从站：不走 barrier，不强制写新字段（缺省兼容）。
- 主站从站卡支持 **测速（ping/pong RTT）** 与踢除，用于评估开录零时的网络预算（约单向 ≈ RTT/2）。

## 8. 数据与后处理（一期）

- **各站本地** `dcs_episode_v1`；不在采集热路径传图。
- 事后拷到分析机；按 `session_id` 多站合并。
- 时钟与跨站对齐策略见专文 **[station-clock-align.md](station-clock-align.md)**（chrony + barrier 零时 + 相对 τ）。
- 录后自动 sync / Peer 拉集：**不做**（见 §11）。

### 8.1 多站对齐操作约定

1. 各站 episode 的 `manifest.json` 含同一 `session_id`（主站 barrier 分配）及本站 `station_id` / `station_role`；开录零时为 **`manifest.t_start`（COMMIT 后真正 start）**。
2. 收集方式：共享盘 / `rsync` / 移动介质，建议目录布局：
   ```text
   sessions/<session_id>/
     <station_id>/episode_XXXXX/
   ```
3. 单站内对齐：继续用现有 `export-timeline` / `filter-timeline`（见 [hw-timeline-align.md](hw-timeline-align.md)），`--master` 选该站某 camera。
4. 跨站对齐（默认）：
   - 每帧 `τ = t_wall - t0`（`t0 = manifest.t_start`）；在 **τ 域** 合并；  
   - **不要**在未同步或大钟差时用绝对 `t_wall` 硬并；  
   - chrony 用于缩小绝对差与排障，**不替代** τ 作为跨站默认；  
   - 合并键为 `session_id`；协同开录时各站 `episode_index` 应相同（主站分配），仍建议按 `station_id` 分子目录存放以免拷盘冲突。
5. barrier 网络延迟导致两边 `t0` 可能差数毫秒～几十毫秒：靠 chrony + 紧 COMMIT 路径压掉；主站 UI「测速」可读 RTT/2；显式写入 manifest 补偿仍为可选增强。
6. 现有后处理 UI 仍面向**单站 save_dir**；跨站一键合并列为后续增强，不阻塞采集。

## 9. 实现分期

| 阶段 | 内容 | 对 master 回退 |
|------|------|----------------|
| **P0** | `station` YAML + status 只读回显；peer listen/connect/心跳/断连 UI | ✅ 已落地 |
| **P1** | 从站 API/UI 权限门禁 | ✅ 已落地（`station_control_locked` + HTTP deny） |
| **P2** | 采集/推理 barrier | ✅ 已落地（`barrier_start` / `barrier_stop`） |
| **P3** | manifest `session_id` + 多站对齐说明/工具钩子 | ✅ 字段已写入；约定见 §8.1 / clock 专文 |
| **P4** | chrony 运维说明 + `COMMIT→master start` 紧路径 | ✅ 底线已落地；可选 sync/RTT **未做**；Peer 跨站后处理 **明确不做** |

回退写法约定：

```python
if peer.has_active_slaves():
    barrier_then_start(...)
else:
    legacy_record_start(...)  # 今日路径
```

## 10. 相关代码锚点

- `src/sensors_dcs/config.py` — `StationConfig` / `station_summary` / `resolve_peer_port`
- `src/sensors_dcs/station_peer.py` — TCP JSON-lines peer（hello / heartbeat / kick）
- `src/sensors_dcs/runtime.py` — Orchestrator 挂载 `peer`；`status().station` / `status().peer`
- `src/sensors_dcs/viz.py` + `ui_i18n.py` — 主页「本站身份」只读卡 + 从站连接按钮
- `src/sensors_dcs/record.py` — start/stop；后续 session 元数据（P2/P3）
- `runtime.control_port` — Peer listen 默认端口（可被 `station.peer_port` 覆盖）

### P0 已落地 API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/station/status` | `station` + `peer` |
| POST | `/api/station/peer/connect` | 从站连主站 |
| POST | `/api/station/peer/disconnect` | 从站断开 |
| POST | `/api/station/peer/kick` | 主站踢从站 `{ "station_id" }` |
| POST | `/api/station/peer/ping` | 主站对指定从站测速 `{ "station_id" }` → `rtt_ms` / `one_way_ms` |

`GET /api/status` 亦含 `station` / `peer` / `station_control_locked`。无活跃从站时 master 录制路径不变。

### P1 从站门禁

服务端拒绝（`station_denied`）：record start/stop/save_dir、apply-config、arm/gripper 写、gello sync/teleop、pi05 connect/step/prompt、home set/save/go、delta-pose-offset。  
前端：主页身份卡 + 采集/推理/臂爪写卡控制按钮置灰（`body.station-slave-locked`；预览 WS / 传感器页仍可用）。  
Peer 直调 `RecordController`（P2 barrier）不经上述 HTTP，故不受本门禁影响。

### P2 / P4 Barrier

主站 `POST /api/record/start|stop` 经 `Orchestrator.coordinated_record_*`：

- **无从站** → 直接 `RecordController`（与改前一致）
- **有活跃从站（P4 顺序）** → `PREPARE(含 episode_index)` → `prepare_ack` → **先广播 `COMMIT`** → **主站 `local_start`** → `commit_ack`；prepare 失败则 `ABORT` 且主站不开录；commit 失败则已开录的主站标 `master_started`（需停录/人工收口）
- 停录：`STOP` → `stop_ack` → 主站 `local_stop`
- 主从使用同一 `episode_index`（主站当前 next index）

从站收到 peer 指令后本地落盘；HTTP 仍拒绝本地 Start/Stop。

manifest 可选字段：`session_id`、`station_id`、`station_role`、`peer_slaves`、`episode_index`（单机可缺省）。

## 11. 非目标（本期不做）

- 从站实时把图像/状态推到主站落盘
- **经 Peer 从从站拉取 episode 做跨站后处理**（数据仍靠拷盘/rsync/共享盘）
- 自动主备切换、从站互连
- 前端改 role/IP
- 用从站 YAML 覆盖主站 agents/设备清单
- **PTP / 硬件授时交换机**（时钟策略明确选 chrony，见 clock 专文）

## 12. 文档索引

| 文档 | 内容 |
|------|------|
| 本文 | 主从拓扑、权限、barrier、分期与 API |
| [station-clock-align.md](station-clock-align.md) | 多机时钟：chrony、相对 τ、barrier 偏斜、后处理合并 |
| [hw-timeline-align.md](hw-timeline-align.md) | **单站内**相机 hw_ts 对齐（与跨站正交） |
