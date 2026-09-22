# Gello 强制干预（累计 delta joints）

日期：2026-09-20  
状态：**关节差，下发时实时叠加**  
关联：[pi05-infer-tab.md](pi05-infer-tab.md) · [gello-arm-teleop.md](gello-arm-teleop.md) · [gello-arm-sync.md](gello-arm-sync.md) · [tcp-drag-ik.md](tcp-drag-ik.md)

> **范围声明：** Gello 在推理时的帧间关节差累成一份 `bias[7]`。绝对下发斜坡的**每一个路点写给臂之前**加上 `bias[:6]`。夹爪目标是策略夹爪加上当时的 `bias[6]`，斜坡进行中这个值一变就再写一次，不是只在斜坡开头写一次。不再把 Gello 做成笛卡尔 `delta_pose` 去改策略位姿。  
> **不是**摇操/同步的替代。Home 会清掉 `bias`，并且那段回零斜坡**不加**它。  
> 手停后、这一段斜坡还在走的时候，累计值保持。斜坡成功结束后自动清零。

策略自己的 `next_state` 仍可以是 `pose` / `joints` / `delta_pose`。解码只得到策略关节，不在这里加 Gello 偏移。

---

## 0. 约束

| # | 结论 |
|---|------|
| 1 | 生产：`Δq = q_cur − q_prev`，pending 累加，过死区后 `bias += pending`。不是 SE(3)，不做 Gello 正运动学。 |
| 2 | 消费：绝对下发每个路点 `q_write = q_waypoint + bias[:6]`，读的是写下那一刻的 `bias`。斜坡进行中新增的关节差，下一个路点就带上。 |
| 3 | 夹爪：策略 `position_norm` 加上当时的 `bias[6]`，夹到 `[0, 1]`。斜坡每个路点看一眼；目标和上次写出的差 ≥ `1e-3` 才再写一次。Modbus 仍在旁路线程，不占关节节拍。 |
| 4 | 清零（`bias`、pending，并把当前 Gello 关节收成新零点）：每一轮 LOOP 开始；**每一次绝对下发斜坡成功结束**；`go_arm_home`；Disarm / Estop；关掉启用；再次打开启用；手动清零。Home 斜坡本身不加 `bias`，结束时也不再清一次（开始时已经清过）。 |
| 5 | 手停、斜坡中途的单个路点、把新帧折进 `bias`，这三个时刻都不清。取消或写失败的斜坡也不清。 |

死区：任一关节 `|Δq| ≥ 1e-3` rad，或 `|dgrip| ≥ 2e-3`，才把整段 pending 折进 `bias`。

---

## 1. 产品边界

### 1.1 做什么 / 不做什么

```text
做：
  - Orchestrator 持有累计 bias[7] + pending[7] + enabled
  - gello_reinforce 每拍算帧间关节差，折进 pending，过死区后加进 bias
  - 绝对下发每个路点写入前加 bias[:6]；夹爪在斜坡中跟着 bias[6] 改
  - 无 agent 时 UI 置灰；有 agent 时可用启用复选框和清零

不做：
  - 不把 Gello 关节做正运动学，也不再把偏移乘进策略 TCP
  - 不替代 gello→arm 同步 / 摇操
  - 不改普通点动、TCP 拖拽直发
  - Home 斜坡不加 bias
  - 无 gello_reinforce 时不写 YAML / 不持久化 offset
```

### 1.2 与摇操 / 同步的分工

| 功能 | 控臂方式 | Gello 角色 |
|------|----------|------------|
| 同步 | 一次性关节插值对齐 | 冻结目标 |
| 摇操 | 50Hz 关节跟随 | live 关节直接当下发目标 |
| **强制干预** | 不直接写臂；改绝对下发目标 | 帧间 Δ 累成一份偏移，每次绝对下发都叠 |

强制干预只影响 **pi05 step → decode → abs-ramp** 这条链。

### 1.3 启用

- `enabled == false`：有效偏移全零，且累计值、pending、上一拍参考都被清掉。
- 再次启用：从当前 Gello 关节重新采参考，累计值从 0 开始。
- 启用期间不需要第二道「应用」开关。

---

## 2. 数据流与语义

### 2.1 总览

```text
Gello 20Hz
  Δq = q_cur − q_prev          # 6 关节 + 夹爪标量，不做正运动学
  prev ← cur
  pending += Δq                # 低于死区则停在 pending
  过死区： bias += pending ， pending ← 0

绝对下发斜坡
  每个路点： q_write = q_waypoint + bias[:6]
  夹爪： grip' = clamp(grip_policy + bias[6], 0, 1)
         与上次写出差 ≥ 1e-3 才再写
  斜坡成功结束： bias、pending 清零，prev 收成当前 Gello
```

### 2.2 生产

1. **启用**或**清零**时：`prev = 当前 gello.joints_rad`，`bias = 0`，`pending = 0`。
2. 之后每拍（仅 `enabled`）：
   - `pending += q_cur − q_prev`（7 维，含夹爪）。
   - 任一臂关节 `|Δ| ≥ 1e-3` rad，或 `|dgrip| ≥ 2e-3` 时，把整段 pending 加进 `bias` 并清空 pending。
3. 停手：新的帧间差约为 0，`bias` **保持**，直到这段绝对下发结束或 §2.5 的其它清零。
4. UI 七格显示累计 `bias`，不是最新一帧。

### 2.3 绝对下发怎么叠

策略 `pose` / `joints` / `delta_pose` 只解码成策略关节和策略夹爪，解码时不加 Gello 偏移。

| 量 | 何时加 |
|----|--------|
| 臂关节 | 斜坡每个路点写入前，`q_waypoint + bias[:6]` |
| 夹爪 | 同一斜坡里，`grip_policy + bias[6]`，目标变化才写 |

斜坡成功结束后清零。下一次绝对目标从新的零点再累计，不会把上一段的 `bias` 再加一遍。

### 2.4 死区

| 量 | 阈值 | 含义 |
|----|------|------|
| 臂关节 | `1e-3` rad | pending 里任一关节的绝对值 |
| 夹爪 | `2e-3` | pending 的 `dgrip` 绝对值 |
| 夹爪重发 | `1e-3` | 和上次写出的 `position_norm` 之差，才再写 Modbus |

未过生产死区的量留在 `pending`，不进下发。过阈值后整段 pending 一次折入。

### 2.5 什么时候清零

清掉的是累计 `bias`、`pending`，并把 `prev` 收成**当前** Gello 关节（这一姿态是新的零点）。

| 时机 | 清 |
|------|----|
| 一次 Control LOOP 的每一轮开始 | 是。上一轮或开环前掰出来的偏移不带进这一轮 |
| 一次绝对下发斜坡成功结束 | 是。这段插值已经用过这份偏移，下一段从零再记 |
| `go_arm_home` | 是（动作开始前）。回零斜坡不加 bias，结束时不再清 |
| Disarm / Estop（`arm_command` 的 `disarm` 或 `stop`） | 是 |
| 关掉「启用」 | 是，并且立刻不再叠加 |
| 再次打开「启用」 | 是（从零重新记） |
| 按钮「清零」 | 是。推理进行中按它，之后的路点不再带这段偏移 |
| 手停、斜坡中途的单个路点、新帧折进累计值 | **否** |
| 斜坡被取消或路点写失败 | **否** |
| 手动单步 STEP（只推理、还没下发） | **否** |

---

## 3. 配置 / Agent

与首版相同。

- `AgentConfig.type` 含 `"gello_reinforce"`，可选 `gello_agent_id`，不要求 `sensor_id`。
- `GelloReinforceAgent`：`@hz` 读 peer Gello 的 7 维，调用 `note_gello_reinforce_sample`。内存真源在 Orchestrator。
- `configured = any(a.kind == "gello_reinforce")`。未配置时 POST 失败，UI 置灰。

YAML 示例：

```yaml
agents:
  - id: gello-reinforce
    type: gello_reinforce
    hz: 20
    buffer_frames: 8
    gello_agent_id: gello
```

---

## 4. Runtime

文件：`src/sensors_dcs/runtime.py`

| 字段 | 含义 |
|------|------|
| `_delta_pose_offset_configured` | 是否存在 `gello_reinforce` |
| `_delta_pose_offset_enabled` | 默认 `false` |
| `_delta_pose_offset` | 累计偏移，长度 7 |
| `_delta_pose_offset_pending` | 未过死区的增量，长度 7 |
| `_delta_pose_offset_prev_gello` | 上一拍 gello[7] |
| `_delta_pose_offset_lock` | 线程锁 |

方法：

- `delta_pose_offset_status()` — `configured` / `enabled` / `offset` / `pending` / `effective` / `prev_set`
- `effective_delta_pose_offset()` — 未配置或未启用 → 全零；否则返回累计 `offset`（不含 pending）
- `note_gello_reinforce_sample(joints7)` — 帧间 Δ 折进 pending / offset
- `clear_delta_pose_offset()` — §2.5 的清零
- `set_delta_pose_offset_enabled(False)` — 关闭并清零；`True` — 打开并清零、重采 prev
- `set_delta_pose_offset(offset)` — 用给定 7 维替换累计值，并清 pending、重采 prev

`_decode_next_state` / `pi05_step` 只得到策略关节和策略夹爪，不加 Gello 偏移。偏移加在绝对下发斜坡里。斜坡成功结束时调用 `clear_delta_pose_offset()`。

---

## 5. HTTP API / Infer UI

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/api/pi05/delta-pose-offset` | status |
| POST | `/api/pi05/delta-pose-offset` | `{ "enabled"?: bool, "offset"?: [7], "clear"?: true }` |

Infer UI（`#tab-infer`）：

- 「启用」复选框。文案说明累计偏移会叠到每一次绝对下发上。
- 七格只显示累计 `offset`。启用时约 20 Hz 轮询，避免被 5 Hz 的 viz 卡住。
- 「清零」发送 `{ "clear": true }`。
- **没有**「应用」复选框。
- Control LOOP 每一轮开始时 POST `{ "clear": true }`。
- 每一次绝对下发斜坡**成功结束**时，服务端自己清零（不依赖前端再 POST）。

---

## 6. 测试

| 覆盖 | 期望 |
|------|------|
| 连续两段平移 | 第二段之后 offset 是两段的合成，不是只剩最新一帧 |
| 停在同一姿态 | offset 保持，不回 0 |
| 低于死区 | 只进 pending，offset 不变 |
| `pose` / `joints` / `delta_pose` | 解码不加累计 bias；bias 加在斜坡路点上 |
| 夹爪斜坡中 bias 变化 | 策略夹爪 + 新的 bias[6] 会再写一次 |
| 绝对下发成功结束 | bias 与 pending 为 0，prev 重采 |
| 清零 / 关闭启用 | offset 与 pending 为 0，prev 重采 |
| 未配置 | setter 失败 |

---

## 7. 非目标

- 单独的 Gello 连杆 / DH。
- 把偏移写成 episode 字段。
- 干预当时用按钮套住或撤掉偏移。
