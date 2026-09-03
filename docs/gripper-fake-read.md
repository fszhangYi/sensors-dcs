# Gripper `fake` 读门控 — 设计规划

> 状态：**仅规划，未实现**  
> 日期：2026-09-03  
> 背景：DH AG95 硬件读反馈偶发不准；需要在配置打开时，用「上次外部下发目标」代替寄存器反馈作为读数。  
> 相关实现锚点（现状，供对照，本稿不改代码）：  
> - 驱动：`sensors/.../drivers/gripper/dh_ag95.py` → `read()` / `write()`  
> - DCS：`gripper_read_agent` / `gripper_write_agent` / `runtime.gripper_command` / gello sync  

---

## 1. 需求理解（共识）

你的意思可以收束成：

1. **硬件读寄存器不可靠** → 不能把 `REG_POSITION_FB` 当可信状态。  
2. 增加门控开关 **`fake`**（布尔）：  
   - **`fake == true`**：`read` 路径**不再**用寄存器当前位置；改为返回 **缓存的「上一次外部下发值」**。  
   - **`fake == false`**：保持**现有**正常读逻辑（寄存器 → `raw` → `position_norm`）。  
3. 为此必须在 **接受外部指令并真正下发** 时，把该目标值 **缓存**起来，供后续 fake 读使用。

**不是**要改 hik_dataset 的 action 公式，也不是让 gello 直接当读源；而是：**在驱动/读通道层，用「上次命令」冒充「当前反馈」**，让上层（agent / WS / 录制 / 导出）仍然只看见熟悉的 `position_norm` 等字段。

和既有语义的关系（参见 [hik-dataset-actions.md](hik-dataset-actions.md) §0.1）：

- 平时默认「相信 `gripper_read`」。  
- 打开 `fake` 后：读到的仍叫 `position_norm`，但物理含义变成 **「我们认为爪应该在哪（上次下发）」**，不是「寄存器说爪在哪」。  
- 导出若仍优先 `gripper_read`，则 fake 打开时 steps 会跟着走「命令轨迹」，这是预期副作用，需在启用时知情。

---

## 2. 目标与非目标

### 2.1 目标

| # | 目标 |
|---|------|
| G1 | 配置可开关的 `fake`；默认 **false**（行为与今天一致） |
| G2 | 每次外部位置下发成功（或至少「已接受并写出」）时更新缓存 |
| G3 | `fake=true` 时，读外发的 `position_norm` / `position_raw`（及别名）来自缓存，不读位置反馈寄存器 |
| G4 | 上层 API / agent / 录制字段名尽量不变，避免大范围改调用方 |
| G5 | 可读出「当前是否 fake、缓存是否有效」，便于 UI/日志排查 |

### 2.2 非目标（本规划不做）

- 不修硬件 / 不改 Modbus 协议本身  
- 不引入第二套 `position_norm` 字段名（除非后续评审要求显式 `position_norm_cmd`）  
- 不在本阶段做「读寄存器与命令融合 / 滤波 / 卡尔曼」  
- 不自动根据故障码切换 fake（仅配置门控）  
- 不改 arm / gello 其它关节逻辑  

---

## 3. 语义定义

### 3.1 `fake`

- 类型：`bool`  
- 建议配置落点：**gripper 设备 YAML**（与 `port` / `baudrate` 同级），例如 `fake: true`  
- 运行期：驱动持有；是否允许 HTTP 热切换可作为二期（首期以配置为准即可）

### 3.2 「外部下发值」缓存

缓存的是 **外部要求爪到达的目标**，来源包括：

- UI / `POST /api/gripper/command` → `gripper_command` → `GripperWriteAgent.command` → `sensor.write({position_norm|position_raw})`  
- gello sync 循环 → 同样 `writer.command(position_norm=…)` → 同一 `write`  

建议缓存内容（驱动内一块 `_last_cmd`）：

| 字段 | 含义 |
|------|------|
| `position_norm` | 归一化目标（有则存；若只下发 raw 则由 raw 反算或只存 raw） |
| `position_raw` | Modbus 目标 ticks（有则存；norm 下发时用现有 `norm_to_raw` 得到） |
| `t_wall` | 最近一次更新时间 |
| `valid` | 是否至少成功下发过一次位置 |

**更新时机（建议）：**

- 在 `DhAg95Sensor.write` 中，当本次是 **位置命令**（`position_norm` 或 `position_raw`），且写寄存器返回 `ok`（或 dry_run 成功）时更新。  
- `initialize` / `calibrate`：**默认不**用 init 姿态覆盖「运动目标缓存」；或单独规定 calibrate 结束后写入开位目标——实现前在评审里定一条，避免歧义。  
- 写失败：**不**更新缓存（读仍用上一成功值；若从未成功则见 §5）。

### 3.3 `fake=true` 时的 `read()`

| 字段 | 行为 |
|------|------|
| `position_norm` / `position_raw` / `raw_value` | 来自缓存；`raw_value` 与 `position_raw` 保持同义 |
| `init_state` / `fault` | **建议仍稀疏读寄存器**（或缓存上次 init）；与「位置假读」解耦，便于知道爪是否已初始化 |
| 额外标记 | 建议增加 `fake: true`，以及 `position_source: "last_command"`（名称可定） |
| 缓存无效 | 见 §5 |

`fake=false`：完全走现有 `REG_POSITION_FB` 逻辑；缓存可继续在 write 时更新（便于随时打开 fake），但不影响 read。

---

## 4. 分层职责（推荐落点）

优先做在 **sensors 驱动 `DhAg95Sensor`**，理由：

1. 所有外部下发最终都进 `write()`，缓存一处即可覆盖 UI 与 gello sync。  
2. `GripperReadAgent` 只调 `read()`，自动吃到假读，无需改导出/WS 契约。  
3. 单一 Serial 生命周期内，读写共享同一缓存状态。

```text
外部指令
  → GripperWriteAgent.command / sync
  → DhAg95Sensor.write  ──更新──► _last_cmd 缓存
                                     ▲
GripperReadAgent.read_frame          │
  → DhAg95Sensor.read  ──fake?───────┘ 用缓存冒充位置
                       └─ else → REG_POSITION_FB（现状）
```

DCS 层：首期 **只透传配置**（sensors YAML 里 `fake`）；agent 可不感知。若需 UI 显示「假读中」，可读 sample 里的 `fake` / `position_source`。

---

## 5. 边界与失败态

| 情况 | 建议行为 |
|------|----------|
| `fake=true` 且从未成功下发过位置 | `read` 返回明确错误或 `position_norm=null` + `error`/`last_error`（二选一，实现前定）；**禁止**静默造假数 |
| 仅 `initialize` 过、未下发位置 | 同上，缓存仍 invalid |
| `fake` 中途 false→true | 用已有缓存；无缓存则同上 |
| `fake` true→false | 立刻恢复寄存器读；缓存可保留 |
| dry_run | write 仍更新缓存；read 在 fake 下用缓存，与真机策略一致 |
| 并发 write/read | 驱动内对 `_last_cmd` 加锁（或与现有串口访问同一把锁），避免半更新 |

---

## 6. 配置示意（文档级，非落地）

```yaml
# sensors YAML 设备段示意
- id: gripper-dh
  kind: gripper
  port: ...
  fake: false   # true：位置读用来自上次外部下发的缓存
```

DCS `sensor_id: gripper-dh` 无需改 agent 类型。

---

## 7. 对下游的影响（知情同意）

| 下游 | `fake=false` | `fake=true` |
|------|--------------|-------------|
| 预览 / WS | 显示寄存器反馈 | 显示上次命令（跟手、不反映真卡滞） |
| 录制 `gripper_read` | 实测 | **命令轨迹**（名义仍是 read） |
| hik `steps` 夹爪 obs/action | 信 read | 信「命令当 read」→ 与「臂永远对、信 read」叙事一致，但物理真值变了 |
| `gripper_write` 帧 | 仍是 command_* | 不变 |

启用 fake 的站点应在运行说明里写明：**夹爪观测不再代表硬件反馈。**

---

## 8. 实现阶段（待你拍板后再写代码）

| 阶段 | 内容 | 验收 |
|------|------|------|
| P0 | 驱动：缓存 + `fake` 配置 + `read`/`write` 行为 | 单测：fake 下 read==上次 write；false 下仍走寄存器（可用 mock） |
| P1 | sample 增加 `fake` / `position_source`；README/本页标记「已实现」 | 人工看 WS payload |
| P2（可选） | UI 角标「夹爪假读」；或 HTTP 热切换 | 操作可见 |
| P3（可选） | 无缓存时的明确错误策略与文档示例 | 不静默 |

**本稿阶段停在规划；未开始写代码。**

---

## 9. 待确认问题（写代码前）

1. **缓存更新是否要求 Modbus `ok==true`？**（建议：是）  
2. **只下发 `position_raw` 时，`position_norm` 是否必须反算进缓存？**（建议：是，保证 read 两边字段齐全）  
3. **`init_state`/`fault` 在 fake 下是否继续读总线？**（建议：继续稀疏读）  
4. **无有效缓存时：报错 vs 返回 null？**（需产品偏好）  
5. **`fake` 是否允许运行期 API 切换，还是仅 YAML？**（建议首期仅 YAML）  
6. 文档与实现放在 **sensors** 仓还是 **sensors-dcs** 仓为主？（逻辑在 sensors 驱动；本规划文放在 dcs `docs/` 便于联调可见）

---

## 10. 一句话

**`fake` 打开时：夹爪「读到的当前状态」=「上一次外部成功下发的目标」；关掉则仍读寄存器。** 缓存挂在驱动 `write` 上，读路径一门控即可，上层尽量无感。
