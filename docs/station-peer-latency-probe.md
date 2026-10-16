# 主从站测速与开录零时偏斜预算

日期：2026-10-16  
状态：P0 已落地（peer ping/pong RTT + 从站管理卡）；增强项（开销分解 / chrony）待做  
关联：[station-multi-machine.md](station-multi-machine.md) · [station-clock-align.md](station-clock-align.md)

> 一句话：**测速回答「开录零时的网络与调度预算大概多少」；不替代录完后各站 `manifest.t_start` / τ 对账，也不替代 chrony。**

---

## 1. 目标

协同采集时，从站相对主站的开录零时偏斜大致为：

```text
Δ ≈ 单程网络延迟
  + 从站收包后 RecordController.start 调度/建目录开销
  + 主站「先广播 COMMIT、再本机 start」的本地时序差
# 另：两机墙钟 / chrony 偏差（影响绝对 t_wall 可读性，不进 τ）
```

测速模块用来在**不开录或干跑**的前提下，把上述预算摊开给现场看，便于判断链路是否到「数毫秒～几十毫秒」可接受区间。

跨站后处理默认仍用相对 τ（`τ = t_wall - manifest.t_start`）；见时钟专文。

---

## 2. 已实现（P0）

### 2.1 Peer 协议

| 方向 | `type` | 说明 |
|------|--------|------|
| 主→从 | `ping` | `{ id, t_send, station_id }` |
| 从→主 | `pong` | `{ id, t_send, t_recv, station_id }` |

实现：`StationPeer.ping_slave()`（`src/sensors_dcs/station_peer.py`）。  
从站 `_slave_reader_loop` 收到 `ping` 立即回 `pong`；主站 `_master_serve_loop` 收 `pong` 计入 pending。

### 2.2 计算口径

| 字段 | 含义 |
|------|------|
| `rtt_ms` | 主站 `t_recv_local - t_send`（本机墙钟） |
| `one_way_ms` | `rtt_ms / 2`（**假设链路对称**） |
| `last_rtt_ms` / `last_rtt_at` | 写入该从站 `_PeerConn.snapshot()`，UI 可回显 |

### 2.3 API / UI

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/station/peer/ping` | body `{ "station_id" }` → `rtt_ms` / `one_way_ms` |
| POST | `/api/station/peer/kick` | 踢除从站（管理卡一并提供） |

主站首页「本站身份」下按从站分卡：**测速** / **断开从站**，展示上次 RTT 与约单向延迟。

桌面入口已 `peer.start()` + 接线 `station_peer_ping`（`desktop_main.py`），与 `sensors-dcs run` 一致。

### 2.4 与协同开录的关系（已实现、非测速本身）

有活跃从站时 barrier 由主站分配同一 `episode_index`（PREPARE/COMMIT/`session_meta`），主从打开同名 `episode_NNNNN`。  
从站本地采集/写臂等按钮置灰（`station_control_locked` + `body.station-slave-locked`）。

### 2.5 P0 能回答 / 不能回答

| 问题 | P0 测速 |
|------|---------|
| 控制面 RTT / 约单向网络预算？ | ✅ |
| 从站 start 调度开销？ | ❌ |
| 主站 COMMIT→local_start 时序？ | ❌ |
| 两机 chrony / 墙钟偏差？ | ❌ |
| 某次真实开录的 `t_start` 差？ | ❌（需对账 episode） |

---

## 3. 开录零时四成分评估（增强设计依据）

针对「前四点能否进测速」的结论（2026-10-16）：

### 3.1 网络单向延迟（COMMIT 飞行）

| | |
|--|--|
| 可行性 | **高**（P0 已覆盖） |
| 做法 | 维持 RTT/2；可选「轻量 COMMIT 模拟」只收发不开录 |
| 精度 | 局域网通常可用；不对称链路时 RTT/2 有偏 |
| 建议 | 留在测速，标成「网络预算」 |

### 3.2 从站收包后 `RecordController.start` 开销

| | |
|--|--|
| 可行性 | **中高**；**不要每次真开录** |
| 做法 | `probe`：从站收包 → 与 start 相近的轻量路径（或 dry start 后立刻丢弃），回传从站 **monotonic** 的 `t_recv` / `t_ready` |
| 精度 | 量级可信；与真开录仍可能差数毫秒 |
| 建议 | 可进测速；默认干跑/探针，避免污染 `episode_*` |

### 3.3 主站「先 COMMIT 再 local start」时序

| | |
|--|--|
| 可行性 | **高**（纯主站本地） |
| 做法 | 记 `t_commit_send`、`t_master_start`（wall + mono），差值即主站侧贡献 |
| 精度 | 本机时钟，可信 |
| 建议 | 增强测速必做；成本最低 |

### 3.4 两机墙钟 / chrony 偏差

| | |
|--|--|
| 可行性 | **中低（进程内）** |
| peer 估 offset | 多次 `t1..t4` NTP 式：`offset ≈ ((t2−t1)+(t3−t4))/2`，**受不对称 RTT 污染**，毫秒级参考，亚毫秒勿信 |
| 可靠做法 | 读 `chronyc tracking`（外挂/本机命令），UI 只读展示 |
| 建议 | 测速可给「peer 估 offset（参考）」；真值旁路 chrony |

### 3.5 合在一起的展示形态

```text
Δ_est ≈ one_way(RTT/2)
      + slave_start_overhead   # 探针 mono
      + master_commit_to_start # 主站 mono
# 另列：clock_offset_est（peer） / chrony_offset（若可读）
```

仍是**预算/上界**，不是「这一集实际零时差」。实际差看 barrier 后两边 `manifest.t_start`（或后处理 τ）。

### 3.6 不建议

- 点测速就真录一集再删（脏、重、易和 episode 分配冲突）
- 单次 ping 墙钟差冒充 chrony 钟差
- 用测速替代 τ 域后处理对齐

### 3.7 推荐落地顺序

1. **轻量测速加强**：RTT + RTT/2 + 主站本地时间戳占位（含 §3.3）  
2. **干跑探针**：模拟 COMMIT 路径量 §3.2 + §3.3，从站不落盘或临时目录即删  
3. **钟差**：peer 粗估可选；旁路「chrony 状态」只读  

---

## 4. 与时钟策略的关系

| 层级 | 作用 | 测速角色 |
|------|------|----------|
| L1 chrony | 压绝对墙钟差 | UI 可展示 tracking；不在 peer 热路径 |
| L2 barrier COMMIT | 各站本地 `t_start` 作 τ₀ | 测速估 Δ 预算；可选写入 `barrier_skew_s`（未做） |
| L3 相对 τ 合并 | 跨站后处理默认 | 测速不替代 |

详见 [station-clock-align.md](station-clock-align.md) §4.3。

---

## 5. 代码索引

| 模块 | 路径 |
|------|------|
| ping/pong / `ping_slave` | `src/sensors_dcs/station_peer.py` |
| API | `POST /api/station/peer/ping`（`viz.py`） |
| 从站卡 UI | `viz.py` `renderStationSlaves` + `ui_i18n.py` |
| 桌面接线 | `desktop_main.py` |
| 测试 | `tests/test_station_barrier.py::test_ping_slave_rtt` |
| 拓扑 / barrier / episode | [station-multi-machine.md](station-multi-machine.md) |

---

## 6. 变更记录

| 日期 | 内容 |
|------|------|
| 2026-10-15 | P0：peer ping/pong、从站管理卡、桌面 `peer.start`、主站分配 `episode_index` |
| 2026-10-16 | 本文：沉淀 P0 口径 + 四成分增强评估与推荐顺序 |
