# 多机时钟对齐策略

日期：2026-10-14  
状态：定案（P4 底线：chrony + 紧 barrier；相对 τ 为跨站后处理默认）  
关联：[station-multi-machine.md](station-multi-machine.md) · [hw-timeline-align.md](hw-timeline-align.md) · [export-timeline.md](export-timeline.md)

> 一句话：**用 chrony 压绝对钟差，用 barrier 的 COMMIT/开录瞬间作各站零时，后处理用相对时间 τ 合并；不选 PTP。**

---

## 1. 问题是什么

多机采集时，每台机器独立跑 `sensors-dcs`，帧上写的是**本机**时间：

| 字段 | 含义 | 跨站直接比较？ |
|------|------|----------------|
| `t_wall` | 本机墙钟（秒） | 仅当两台钟已对齐时可靠 |
| `t_mono` | 本机单调钟 | **不可**跨机直接比绝对值 |
| 相机 `color_timestamp` / hw_ts | 相机硬件时基 | **站内**精对齐；跨站需先对齐主机纪元 |

若两台墙钟相差 **1 分钟以上**，仍可正常采集（barrier 不依赖绝对时刻），但若后处理用绝对 `t_wall` 硬并，会整体错开约 60s。

本策略要同时覆盖：

1. **大钟差**（分钟级）— 采集与后处理仍可用；  
2. **小残差**（毫秒～几十毫秒，来自网络与 barrier）— 压到可接受；  
3. **运维可执行** — 不引入 PTP 硬件与交换机依赖。

---

## 2. 选型结论：chrony（NTP），不上 PTP

| 方案 | 典型精度 | 部署成本 | 对本项目 |
|------|----------|----------|----------|
| **chrony / NTP** | 局域网常见毫秒～亚毫秒 | 低：一台源 + 各站客户端 | **选用** |
| PTP (IEEE 1588) | 微秒～亚微秒 | 高：网卡/交换机/驱动 | **本期不做** |

理由：

- 开停已由 **barrier** 同步；站内还可选 **hw_ts**（见 hw-timeline-align）。  
- 业务不要求跨站亚毫秒硬实时总线。  
- PTP 精度过剩，运维与硬件门槛不匹配当前一主多从、本地落盘架构。

**仅当**未来明确要求「跨站亚毫秒、且不以 barrier/相对 τ 兜底」时，再单独立项评估 PTP。

---

## 3. 三层对齐模型

由粗到细，层层兜底；上层失败时下层仍可工作。

```text
L1  chrony          缩小各站绝对墙钟差（目标：稳定在数毫秒量级）
L2  barrier 零时    COMMIT/开录瞬间 → 各站本地 t0（manifest.t_start）
L3  相对时间 τ      τ = t_wall - t0（或 mono 相对开录）→ 跨站合并默认时钟
(+ 站内 hw_ts)      单站多传感器精对齐，与跨站策略正交
```

| 层 | 解决什么 | 不解决什么 |
|----|----------|------------|
| L1 chrony | 绝对时间可读、排障、减小大钟差 | 不能保证开录瞬间完全同时 |
| L2 barrier | 物理上「几乎同时」开始录 | 单程网络延迟导致 t0 仍有毫秒级偏斜 |
| L3 相对 τ | **大钟差下仍可跨站对齐** | τ=0 仍含 barrier 抖动；需 L1+紧路径压抖动 |
| 站内 hw_ts | 单机多路相机/传感器 | 不自动解决跨机 |

---

## 4. 相对时间 τ（跨站后处理默认）

### 4.1 定义

对每个站、每一帧：

```text
t0  := 该站本集真正开始录制的时刻
      （实现：RecordController 开录时的 time.time() → manifest.t_start）
τ   := t_wall - t0
```

更抗「录制中改系统时间」的变体（若落盘了开录 mono）：

```text
τ := t_mono - t_mono_at_start
```

**零时是 COMMIT 后真正 `RecordController.start` 的瞬间，不是：**

- 操作员点击「开始」；  
- PREPARE / prepare_ack。

### 4.2 为何能扛「差 1 分钟」

假设物理时刻 T 两站几乎同时开录，但 B 的墙钟快 60s：

| | 站 A | 站 B |
|--|------|------|
| t0 | 12:00:00.000 | 12:01:00.020 |
| 物理 T+1s 的 t_wall | 12:00:01.000 | 12:01:01.020 |
| τ | 1.000 s | 1.000 s |

绝对 `t_wall` 差约 60s；**τ 仍对齐**（残差约等于 barrier/网络偏斜，与钟差无关）。

### 4.3 网络延迟造成的 t0 偏斜

主站先（或并行）开录再发 `COMMIT`，从站收包后才开录 → 从站 t0 通常晚约：

```text
Δ ≈ 单程延迟 + 从站调度/开录开销
```

局域网常见为 **数毫秒～几十毫秒**，不是分钟级。

**本期策略（P4 底线）：**

1. **上 chrony**，压绝对钟差；  
2. **缩短 barrier 关键路径**（PREPARE 做重活，COMMIT 后尽快 start；减少 COMMIT 路径上的多余工作）；  
3. **接受未补偿的 Δ 作为已知误差上界**（约 RTT 量级）。

**可选增强（非底线）：** 用发 COMMIT / 收 ACK 的本地时刻估 `RTT/2`，写入 manifest（如 `barrier_skew_s`），后处理平移从站 τ。需要亚毫秒再谈 PTP。

---

## 5. 后处理：分布在两台机器上的数据怎么并

### 5.1 收拢

```text
sessions/<session_id>/
  <station_id_master>/episode_XXXXX/
  <station_id_slave>/episode_YYYYY/
```

配对键是 manifest 的 **`session_id`**，不要假定两边 `episode_index` 相同。

### 5.2 时间域

1. 各站读本集 `manifest.t_start` 作为 `t0`；  
2. 所有 jsonl / camera index 的时间列改为 `τ = t_wall - t0`（或 mono 相对值）；  
3. 在 **τ 域** 选全局主时钟（通常主站某路相机），对其余流做 nearest / asof；  
4. 站内若启用 hw_ts，先在站内对齐，再映射到该站 τ，再跨站合并。

### 5.3 与现有工具的关系

| 步骤 | 工具 | 说明 |
|------|------|------|
| 单站对齐/导出 | 现有 `export-timeline` / `filter-timeline` / hik 导出 | 仍按单站 `save_dir` |
| 跨站合并 | 约定见上；自动化工具列为后续增强 | UI 尚未一键跨站 |
| 站内 hw 精对齐 | [hw-timeline-align.md](hw-timeline-align.md) | 与跨站正交 |

推荐流程：**分站先跑通现有链 → 再按 session_id + τ 拼总表**；或先合成「虚拟多 agent episode」再走导出（实现量更大）。

---

## 6. chrony 运维要点（P4 文档底线）

> 下列为策略与验收口径；具体发行版包名以现场 OS 为准。

### 6.1 角色

- **时间源**：主站，或局域网内一台常开、稳定的机器。  
- **客户端**：所有采集站（含主站若源在别处）。

### 6.2 验收（采集前建议过一遍）

- 各站能看到正常 tracking（未长期 `Not synchronised`）。  
- 站间墙钟差稳定在 **数毫秒～亚秒** 量级（远小于「1 分钟」故障态）。  
- 记录：源地址、各站 offset 抽样，便于对照某次 `session_id`。

### 6.3 与软件的关系

- **不**在 `sensors-dcs` 进程内嵌入 NTP 客户端。  
- 软件假定：运维已尽量同步；后处理**仍默认用 τ**，不假设绝对钟完美。  
- 无 chrony 时：**允许采集**；跨站合并必须走 τ，并预期绝对时间不可比。

---

## 7. Barrier 路径收紧（P4 底线，实现项）

目标：减小 `Δ`（从站 t0 相对主站开录的滞后）。

| 原则 | 说明 |
|------|------|
| 重活放 PREPARE | 建目录、校验 idle、预热等放在 COMMIT 之前 |
| COMMIT 路径要短 | 收到后尽快 `RecordController.start`，少日志/少阻塞 IO |
| 时序 | 评估「先广播 COMMIT、主从几乎同时 start」vs「主站先 start 再 COMMIT」；以减小最大站间 Δ 为准并文档化选定顺序 |
| 度量 | 可选：记录 `t_send_commit` / 从站 `t_recv` / `t_start`，便于估 RTT |

当前代码路径（实现时以仓库为准）：有从站时 `PREPARE → prepare_ack → 主站 start → COMMIT → commit_ack`。P4 收紧时应对照上表评审是否调整顺序。

---

## 8. 误差预算（量级，非硬指标）

| 来源 | 无 chrony | 有 chrony + 紧 barrier |
|------|-----------|-------------------------|
| 绝对墙钟差 | 可达分钟～更大 | 目标毫秒～亚秒 |
| barrier / 网络导致的 t0 偏斜 | 同左（与钟差无关） | 典型数毫秒～几十毫秒（LAN） |
| 跨站用绝对 t_wall 硬并 | **不可用** | 可用但不如 τ 稳妥 |
| 跨站用 τ | **可用** | **推荐默认** |

---

## 9. 决策摘要

| 议题 | 决定 |
|------|------|
| NTP vs PTP | **chrony/NTP**；不上 PTP |
| 跨站默认时钟 | **相对 τ**（开录 t0 为零时） |
| 大钟差（≥1min） | 采集 OK；后处理必须用 τ（或显式钟偏），禁用绝对硬并 |
| P4 底线 | **chrony 运维说明 + 缩短 barrier 关键路径** |
| P4 可选 | 录后 `session_id` 目录同步；RTT/2 写入 manifest |
| 与站内 hw_ts | 正交；先站内后跨站 |

---

## 10. 相关代码 / 数据锚点

- Peer barrier：`src/sensors_dcs/station_peer.py`（`barrier_start` / `barrier_stop`）  
- 开录与 `manifest.t_start`：`src/sensors_dcs/record.py`  
- 会话元数据：`session_id`、`station_id`、`station_role`、`peer_slaves`（manifest 可选字段）  
- 总方案：[station-multi-machine.md](station-multi-machine.md)
