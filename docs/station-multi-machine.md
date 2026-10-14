# 多机主从站（LAN）方案

日期：2026-10-14  
状态：已定案，按分期实现  
前置提交：`5c46497`（working tree clean，与 `origin/master` 同步）

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
| 4 | 时钟 | **建议局域网 NTP/chrony**；协议仍写 `t_wall`/`t_mono`，barrier 再加可选 `t_sync` |
| 5 | 拓扑 | **固定一主多从**；从站不 listen peer；不做从从互连、不做主主切换 |

补充定案（设计讨论）：

- 身份与 `advertise_ip` **仅 YAML 配置，前端只读回显**。
- 从站连主站时上报的是**站元信息 + agents/sensors 摘要**，不覆盖主站运行 YAML。
- 仅 master 接受 peer 入站；若对从站发起「被连接」则失败。
- 有活跃从站时，主站开录/开推理走 **PREPARE → ACK → COMMIT** barrier；超时则 ABORT，主站也不 start。

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
  → PREPARE{session_id, episode_hint, mode, token}
  → 从站：建目录 / sampler 待命 → ACK{ready, t_mono, t_wall}
  → 全员 ready（超时 → ABORT，主站不 start）
  → COMMIT{t_start_wall?}
  → 各站 RecordController.start
Stop 同理：STOP → ACK → 各自 finalize
```

- `session_id` 由主站分配；各站本地仍用 `episode_NNNNN`。
- manifest 增加可选：`station_id`、`station_role`、`session_id`、`peer_slaves`（主站）。
- 单机/无从站：不走 barrier，不强制写新字段（缺省兼容）。

## 8. 数据与后处理（一期）

- **各站本地** `dcs_episode_v1`；不在采集热路径传图。
- 事后拷到分析机；按 `session_id` 多站合并。
- 时钟：优先 NTP；对齐仍可用现有 timeline/`--master` agent 思路扩展站维度。
- 二期可选：录后异步 sync 整包或只推元数据/缩略图（非本期）。

### 8.1 多站对齐操作约定（P3 说明）

1. 各站 episode 的 `manifest.json` 含同一 `session_id`（主站 barrier 分配）及本站 `station_id` / `station_role`。
2. 收集方式：共享盘 / `rsync` / 移动介质，建议目录布局：
   ```text
   sessions/<session_id>/
     <station_id>/episode_XXXXX/
   ```
3. 单站内对齐：继续用现有 `export-timeline` / `filter-timeline`（见 [hw-timeline-align.md](hw-timeline-align.md)），`--master` 选该站某 camera。
4. 跨站对齐（工具尚未自动化）：
   - 优先依赖局域网 **NTP/chrony**，直接用各站 `t_wall` 并排；
   - 无 NTP 时，用 barrier `COMMIT` 时刻的相对偏移（各站 prepare_ack 的 `t_wall`/`t_mono`）做一次线性校正后再 merge；
   - 合并产物以 `session_id` 为键，勿假定各站 `episode_index` 相同。
5. 现有后处理 UI 仍面向**单站 save_dir**；跨站一键合并列为后续增强，不阻塞采集。

## 9. 实现分期

| 阶段 | 内容 | 对 master 回退 |
|------|------|----------------|
| **P0** | `station` YAML + status 只读回显；peer listen/connect/心跳/断连 UI | ✅ 已落地 |
| **P1** | 从站 API/UI 权限门禁 | ✅ 已落地（`station_control_locked` + HTTP deny） |
| **P2** | 采集/推理 barrier | ✅ 已落地（`barrier_start` / `barrier_stop`） |
| **P3** | manifest `session_id` + 多站对齐说明/工具钩子 | 字段已写入 manifest；对齐工具待扩 |
| **P4** | （可选）录后同步、NTP 运维说明 | 不碰热路径 |

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

`GET /api/status` 亦含 `station` / `peer` / `station_control_locked`。无活跃从站时 master 录制路径不变。

### P1 从站门禁

服务端拒绝（`station_denied`）：record start/stop/save_dir、apply-config、arm/gripper 写、gello sync/teleop、pi05 connect/step/prompt、home set/save/go、delta-pose-offset。  
前端：主页身份卡 + 采集/推理控制按钮置灰（预览 WS / 传感器页仍可用）。  
Peer 直调 `RecordController`（P2 barrier）不经上述 HTTP，故不受本门禁影响。

### P2 Barrier

主站 `POST /api/record/start|stop` 经 `Orchestrator.coordinated_record_*`：

- **无从站** → 直接 `RecordController`（与改前一致）
- **有活跃从站** → `PREPARE` → 全员 `prepare_ack` → 主站本地 start → `COMMIT` → `commit_ack`；失败则 `ABORT`，主站不开录（若已开则需人工/后续 fail-stop）
- 停录：`STOP` → `stop_ack` → 主站 `local_stop`

从站收到 peer 指令后本地落盘；HTTP 仍拒绝本地 Start/Stop。

manifest 可选字段：`session_id`、`station_id`、`station_role`、`peer_slaves`（单机可缺省）。

## 11. 非目标（本期不做）

- 从站实时把图像/状态推到主站落盘
- 自动主备切换、从站互连
- 前端改 role/IP
- 用从站 YAML 覆盖主站 agents/设备清单
