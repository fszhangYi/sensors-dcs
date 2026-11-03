# 联机后处理 · 从站数据 Gather

日期：2026-11-03  
状态：**已实现（方案 B · 从站 push + 断点续传）**  
关联：[station-multi-machine.md](station-multi-machine.md) · [station-clock-align.md](station-clock-align.md) · [standard-export-step4.md](standard-export-step4.md)

> 一句话：**停录后按 `session.json` / 主站 episode 元信息，经 Peer 只发控制消息；从站用 rclone/rsync/robocopy/python 把整棵 `episode_*` 推到主站 `publish_uri` 约定树；支持断点续传。不进采集热路径、不走 Peer 传图、默认不压缩。**

---

## 1. 问题

联机采集时各站本地写 `dcs_episode_v1`。联机后处理（federated merge → Step 1–3 / Step 4）要求主站上出现：

```text
{master_save_dir}/
  episode_XXXXX/                                 # 主站本地
  sessions/<session_id>/session.json
  sessions/<session_id>/<slave_station_id>/episode_XXXXX/   # 从站整目录
```

目标：用自动化 **gather** 替代手工搬运，且不破坏「各站本地落盘 + Peer 只做控制」的硬约束。

---

## 2. 硬约束（必须守住）

| # | 约束 | 含义 |
|---|------|------|
| 1 | **不进采集热路径** | 开录→采样→写盘→停录链路不拷跨站大文件；gather 在停录且 flush 完成后人工/CLI 触发 |
| 2 | **不走 Peer 传图** | Peer 只承载 `gather_request` / `gather_ack` / `gather_done`；整集 JPEG/jsonl 走独立文件通道 |
| 3 | **缺站拒绝静默合并** | gather 未齐 → `ok=false`；联机后处理仍按缺站拒绝 |
| 4 | **布局兼容** | 输出落在 §1 约定树，现有 `session/run` / federate **无需改语义** |
| 5 | **跨 OS** | 主站常见 Windows；从站 Windows / Linux |

---

## 3. 定案：方案 B · 从站 push

| 角色 | 动作 |
|------|------|
| **主站** | 读 `session.json`（或主站 episode `manifest.session_id`），为每个 `role=slave` 拼 `dest_uri`，Peer 广播 `gather_request`，等 ack/done，校验本地约定路径 |
| **从站** | 立即 `gather_ack`；后台把本地 `save_dir/episode_XXXXX/` **sync** 到消息里的 `dest_uri`；完成后 `gather_done` |
| **字节通道** | `rclone sync` → `rsync` → `robocopy /Z`（Win UNC）→ 纯 Python 断点拷（同 size+mtime 跳过） |

主站 YAML：

```yaml
station:
  role: master
  # 从站必须能写入的根（UNC / smb / sftp / 本机挂载）
  gather:
    backend: auto          # auto | rclone | rsync | robocopy | python
    publish_uri: "\\\\NAS\\dcs_share"   # 或 /mnt/dcs 或 smb://…
    timeout_s: 3600
  # 若 publish_uri 为空，回退 station.share_root
  share_root: "\\\\NAS\\dcs_share"
```

`dest_uri` = `publish_uri/sessions/<session_id>/<slave_id>/episode_XXXXX`。  
主站本机同时 `mkdir` 约定路径 `save_dir/sessions/...`（当 `publish_uri` 与本地 `save_dir` 为同一卷/挂载时，push 即落盘可见）。

---

## 4. Gather 契约

### 4.1 Peer 消息（控制 only）

```text
master → slave  gather_request {
  job_id, session_id, episode_index, station_id,
  dest_uri, backend, force, timeout_s
}
slave  → master gather_ack  { job_id, station_id, ok, accepted, error? }
slave  → master gather_done { job_id, station_id, ok, backend, elapsed_s, error?, … }
```

### 4.2 触发

| 触发 | 说明 |
|------|------|
| UI | 后处理 Tab → 联机会话 → **收集从站（Gather）** |
| HTTP | `POST /api/postprocess/session/gather` |
| CLI | `sensors-dcs session-gather`（转发到正在跑的主站 viz） |

输入优先：所选 **episode** 的 `manifest.session_id` / `episode_index`；或所选 **联机会话**。

### 4.3 CLI

```bash
# 解析目标（不传文件）
sensors-dcs session-gather -c configs/master.yaml \
  --session-id <sid> --dry-run

# 对正在运行的主站发起 gather（从站须已 Peer 连接）
sensors-dcs session-gather -c configs/master.yaml \
  --episode D:/data/episode_00012

sensors-dcs session-gather -c configs/master.yaml \
  --session-id <sid> --station slave-right --force
```

### 4.4 HTTP

```http
POST /api/postprocess/session/gather
{"session_id":"…", "episode":"D:/data/episode_00012", "save_dir":null, "force":false}

GET  /api/postprocess/session/gather/status?job_id=
```

### 4.5 校验（成功条件）

对每个从站目标目录：

1. 存在且为目录；  
2. `manifest.json` 可读；  
3. `manifest.session_id`（若有）与清单一致；  
4. `manifest.episode_index` 与请求一致（若双方都有）。

任一从站失败 → 整体 `ok=false`；**不**自动跑 federate。

---

## 5. 压缩策略

同机房有线默认 **不压缩**。弱网可选日后单集打包（非本期）。

---

## 6. 与后处理流水线的衔接

```text
barrier 停录 + 各站 flush
    → Gather（UI / API / CLI）
    → POST /api/postprocess/session/run   # federate + Step 1–3
    → 可选 Step 4 export-standard
```

---

## 7. 实现状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| G0 | 路径约定 + sync 后端 + Peer 控制 + CLI dry-run / API | **完成** |
| G1 | UI Gather 按钮 + i18n + busy | **完成** |
| G2 | 停录后可选自动 gather | 未做（默认关） |
| G3 | `share_root` 已可见则 skip copy | 未做 |

代码入口：

- `sensors_dcs/gather_sync.py` — 目录 sync / 断点续传  
- `sensors_dcs/session_gather.py` — 解析 / 校验 / job registry  
- `station_peer.broadcast_gather` / `_handle_gather_request_as_slave`  
- `Orchestrator.run_session_gather` / `_peer_on_gather`

---

## 8. 决策摘要

| 议题 | 定案 |
|------|------|
| 大方向 | 方案 **B 停录后 gather**，**从站 push** 到主站 `publish_uri` |
| 传输 | rclone → rsync → robocopy → python resume |
| Peer | **只控制**；不传图 |
| 压缩 | 默认否 |
| 布局 | `sessions/<sid>/<slave>/episode_*` |
