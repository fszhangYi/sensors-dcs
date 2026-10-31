# Gello → Arm 摇操（遥操作）

> 参考旧项目：`autodl-tmp/hww/hik_gello`（`experiments/run_env.py` 启动闸 + 100Hz 跟随；`gello/robots/elite_robot.py` `TT_add_joint`）  
> 对照 DCS：一次性对齐 [gello-arm-sync.md](gello-arm-sync.md)、点动 [arm-write.md](arm-write.md)、`runtime.set_gello_arm_sync`  
> 对照臂侧 B.1：[sensors-arm/docs/cs66-remote-teleop-smooth.md](../../sensors-arm/docs/cs66-remote-teleop-smooth.md)  
> 状态：**已实现**；默认关闭；须显式点「摇操」。  
> **范围声明：本功能是「对齐之后」的持续关节跟随；与「同步」并列，不是把同步改成遥操作。**

---

## 变更记录

### 2026-10-31 — latest-wins + 定频写线程；取消步长限速

**背景：** 真机 gello_trace 显示 `gello_read`≈50 Hz，但 `teleop_cmd` 仅≈6 Hz。根因是旧写环「读 gello → **阻塞** `writeServoj` → `sleep(1/teleop_hz)`」把采样与写臂串在同一线程；写慢则整环变稀。另有 `teleop_step_max_rad=0.05` 限速，使大步跟随被裁成多拍（`teleop_cmd_rate_limited`），观感更顿。Fake 稠密路径丝滑证明臂/`servoj_once` 本身可跟。

**改动：**

| 项 | 旧 | 新 |
|----|----|----|
| 架构 | 单线程：读+写+sleep | **设定点轮询线程**（latest-wins）+ **定频写线程**（≈`teleop_hz`） |
| `teleop_step_max_rad` | 默认 0.05，步长裁剪 | **取消**（配置字段删除；旧 YAML 键 ignore） |
| 跟随目标 | 可能是限速后的中间角 | **始终最新 gello 前 6 轴**（透传） |
| 跳变闸 | `step_max` 与 `jump_abort` 两档 | 仅保留 **`teleop_jump_abort_rad`**（默认 0.35）超限解除 |
| 写慢时 | 连 gello 采样一起变稀 | 写线程可掉频，设定点仍按 poll 更新；下一拍取**最新**点 |

**代码 / 配置：**

- `runtime.py`：`_gello_arm_teleop_setpoint_loop` + `_gello_arm_teleop_write_loop`；删除 `_rate_limit_step` 使用
- `config.py`：`GelloArmTeleopConfig` 去掉 `teleop_step_max_rad`，`extra=ignore`
- `configs/gello_arm_sync.yaml`：去掉 `teleop_step_max_rad`
- `viz.py`：状态行不再显示「限速中」
- `tests/test_gello_arm_teleop.py`：去掉限速单测，增加 legacy 键 ignore

**验收要点：** 开摇操后 status/`message` 含 `latest-wins`；gello_trace 中不应再出现 `teleop_cmd_rate_limited`；写臂 RTT 大时 `teleop_cmd` 可能仍低于 100 Hz，但命令应为当拍最新 gello（非排队旧点）。

---

## 0. 最重要的产品边界（必读）

### 0.1 与「同步」的分工

| 功能 | 按钮 | 目标从哪来 | 结束后 |
|------|------|------------|--------|
| 同步 | 「同步」 / 「取消同步」 | 命令时刻**冻结**的 gello 前 6 轴 → 插值 | gello **不再**控臂 |
| 摇操 | 「摇操」 / 「解除摇操」 | **每拍 live** gello 前 6 轴 | 解除后 gello **不再**控臂 |

- 摇操**不**替代同步：开环要求臂与 gello 已足够接近；差距过大时弹窗要求先同步。
- 摇操**不**自动在启动或 Arm 后开启。
- 只写机械臂 6 轴；夹爪仍走独立 gello→gripper sync。

### 0.2 必须满足的安全约定

1. **未 Arm 不得摇操**；gate 失败时**零写**。
2. 写环仅在服务端；浏览器只发 `enabled` toggle，不经 WebSocket 回传控制。
3. 「解除摇操」/ Disarm / Estop / 安全退出 / 开启同步 → **立刻停写**，且 `enabled == false`。
4. 检测到 gello **异常跳变**或读数中断 → 自动解除摇操 + 弹窗；**不**静默假使能。
5. 跳变自动解除时**默认保持 armed**（便于再同步后重开），不强制 Disarm。

---

## 1. 状态机

```text
idle
  │ 用户点「摇操」
  ▼
gate（能否进入 teleop？）
  │ 未 Arm            → 弹窗 → idle（零写）
  │ 无 live joints    → 弹窗 → idle（零写）
  │ Δ_max > enter     → 弹窗「请先同步」→ idle（零写）
  │ 同步进行中        → 弹窗 → idle（零写）
  │ 通过
  ▼
teleop（设定点 poll latest-wins + 写线程 @ teleop_hz → arm_write）
  │ 点「解除摇操」
  │ Disarm / Estop / 安全退出
  │ 开启「同步」
  │ 跳变超限 / gello 读数中断超时
  │ armed 丢失 / 写失败超限
  ▼
idle（enabled=false；gello 无控制）
```

UI 按钮文案：

| `enabled` | 按钮文字 |
|-----------|----------|
| false | **摇操** |
| true | **解除摇操** |

---

## 2. Gate：能否进入摇操

在**尚未下发跟随运动**前检查；失败则弹窗，**禁止**开环。

| 检查 | 条件 | 失败提示要点 |
|------|------|----------------|
| 配置齐全 | 存在 gello + arm read + arm_write | 缺 agent |
| 已 Arm | `arm_write.armed` | **请先点 Arm，再开摇操。** |
| live 数据 | gello、arm 均有有限 `joints_rad` 长度 ≥ 6 | 等待读数 |
| 对齐带 | \(\Delta_{\max}=\max_i\|q_g[i]-q_a[i]\| \le\) `teleop_enter_max_rad` | **请先点「同步」对齐后再摇操。** 附 \(\Delta_{\max}\) 与最差轴号 |
| 与同步互斥 | `gello_arm_sync.enabled == false` | 请先取消同步，或等同步结束 |

说明：

- `teleop_enter_max_rad` 默认 **0.05 rad**（与 `sync_done_eps_rad` 同量级）。  
  **不要**用同步 gate 的 `align_max_rad=0.8` 作为摇操开环阈值——0.8 只允许「开始慢速对齐」，不允许「立即 50Hz 跟随」。
- 复用 Runtime 现有 `_resolve_gello_arm_agents`；新增 `_gello_arm_teleop_gate`（独立于 `_gello_arm_gate`）。

---

## 3. 写环算法（latest-wins，对齐臂侧 B.1）

### 3.1 对照

| 项 | hik | sensors-arm 远程连续 B.1 | DCS 摇操（本页） |
|----|-----|--------------------------|-----------------|
| 使能 | 进程起来后即跟随 | UI 远程连续下发 | Arm + 显式「摇操」 |
| 开环闸 | \(\Delta_{\max}\le 0.8\) + 斜坡 | 预下发 / 连接 | \(\Delta_{\max}\le 0.05\)（先「同步」） |
| 设定点 | 主环内 act | HTTP `/setpoint` latest-wins | **gello ring 轮询** latest-wins |
| 写 | ~100 Hz TT | 定频写线程 `servoj_once` | 定频写线程 `servoj_once` / EC TT |
| 跳变 | 无显式产品闸 | （源侧） | **仅** `teleop_jump_abort_rad` 超限解除 |

### 3.2 双线程伪代码

```text
# --- setpoint poller（不写臂；写慢不拖住）---
q_prev = None
stale_ticks = 0
poll_hz = max(teleop_hz, 100)   # 上限约 200
while enabled and not stop:
  q_g = gello.ring joints_rad[0:6]
  if q_g 无效:
    stale_ticks += 1
    if stale_ticks > teleop_stale_max_ticks: abort; break
    sleep(1/poll_hz); continue
  stale_ticks = 0
  if q_prev is not None:
    δ = max_i |q_g[i] - q_prev[i]|
    if δ > teleop_jump_abort_rad: abort 跳变; break
  setpoint = q_g          # latest-wins 覆盖
  q_prev = q_g
  sleep(1/poll_hz)

# --- write thread（定频取最新点）---
while enabled and not stop:
  if not armed: abort; break
  q = setpoint            # 拷贝最新；无则等下一拍
  if q is None: sleep(1/teleop_hz); continue
  writer.command(         # CS: servoj_once + relative_to_last
    joints_rad=q, ...
  )
  if 连续失败超阈值: abort; break
  sleep(1/teleop_hz)      # 注意：周期含写耗时 → 有效写频可能 < teleop_hz
```

要点：

- Gello **只更新目标**；写线程只消费最新值（中间点丢弃 = latest-wins）。
- **无** `teleop_step_max_rad` 步长裁剪。
- 写臂 RTT 大时有效写频下降，但不会把 gello 采样锁在同一把「等写完」的锁里。

### 3.3 驱动层 `max_delta`

单步仍可能被 sensor `max_delta` 再裁或拒绝。产品层不得假设「发出即跟上」；写失败累计超阈值同样自动解除，避免假使能。

---

## 4. 摇操过程中 gello 跳变如何应对

「跳变」= 设定点轮询相邻有效样本上，相对上一拍已接受 gello \(q^{-}\) 的突变（串口毛刺、丢包后恢复、offset 跳圈等）。

| \(\delta = \max_i\|q_g[i]-q^{-}[i]\|\) | 行为 |
|----------------------------------------|------|
| \(\delta \le\) `teleop_jump_abort_rad`（默认 **0.35**） | **透传**：`setpoint = q_g` |
| \(\delta >\) `jump_abort` | **异常跳变**：不停追尖峰；停双线程；`enabled=false`；**保持 armed**；弹窗 |
| gello 连续无效 \(>\) `teleop_stale_max_ticks`（默认 **3**） | 同跳变：自动解除 + 弹窗「读数中断」 |

弹窗文案示例（跳变）：

> 检测到 gello 关节跳变（Δ=… rad，轴 k），已自动解除摇操。请检查主手后先「同步」，再开摇操。

**明确禁止：**

- 跳变后仍显示「摇操中」却不再写（假使能）
- 用步长限速「慢慢追」尖峰（已取消 `teleop_step_max_rad`）
- 仅依赖驱动 `max_delta` 而不做产品层跳变闸

---

## 5. 与 arm_write / 同步 / UI 的关系

| 场景 | 行为 |
|------|------|
| 摇操中 | **禁用** ± 点动与手动绝对角 |
| 摇操中点「同步」 | 先停摇操，再走同步 gate（或拒绝并提示先解除——实现时选：**先停摇操再允许开同步**） |
| 同步中点「摇操」 | gate 拒绝 |
| Disarm / Estop | 停摇操；再走既有 disarm/stop |
| 安全退出 | `request_shutdown` 路径先停摇操与同步 |
| 夹爪 sync | 独立；摇操不写夹爪 |

UI 建议（`arm_write` 卡片，`同步` 按钮旁）：

1. 按钮：「摇操」 / 「解除摇操」
2. Gate / 跳变 / 读数中断 / Arm 丢失：**弹窗**（`showAppModal`）
3. 状态行：`摇操中 · {hz} Hz · latest-wins · 已写 {n}` / `空闲（gello 不控臂）`
4. 摇操中「同步」按钮可保留，但点击语义为先解除再同步，或禁用直至解除

---

## 6. API / Runtime / 配置

### Runtime

- [x] `set_gello_arm_teleop(enabled, …)`：开 → gate → 设定点线程 + 写线程；关 → 停双线程
- [x] `gello_arm_teleop_status()`：见下表字段
- [x] 任何退出路径保证：无 gello→arm 后台写
- [x] `arm_command` 点动：teleop 中拒绝
- [x] Disarm / Estop / shutdown：先 `teleop=false`

### Status 字段

| 字段 | 含义 |
|------|------|
| `enabled` | 摇操是否应运行 |
| `phase` | `idle` \| `teleop` \| `error` |
| `gello_agent_id` / `arm_agent_id` / `arm_write_agent_id` | 解析到的 agent |
| `delta_max` / `worst_joint` | 最近一次 gate 或设定点环内误差 |
| `write_count` | 本轮已写次数 |
| `rate_limited` | 恒为 `false`（步长限速已取消；保留字段兼容 UI） |
| `has_setpoint` | 是否已有最新设定点 |
| `write_path` | `cs_servoj_once` \| `ec_tt` |
| `last_ok` / `last_error` / `message` | 最近结果与文案 |
| `last_t_wall` | 最近更新墙钟 |
| `params` | 当前配置快照 |

### HTTP

- [x] `GET /api/arm/gello-teleop` → status
- [x] `POST /api/arm/gello-teleop` body：`{ "enabled": bool, "gello_agent_id"?, "arm_agent_id"?, "arm_write_agent_id"? }`

### 配置

```yaml
gello_arm_teleop:
  teleop_hz: 100                # 写线程目标频率；钳位 [1, 100]；对齐 CS servoj_time≈0.01
  teleop_enter_max_rad: 0.05    # 开环前 |gello−arm| 上限；超则「先同步」
  teleop_jump_abort_rad: 0.35   # 超此视为跳变 → 自动解除（无步长限速）
  teleop_stale_max_ticks: 3     # gello 连续无效拍数上限
  teleop_write_fail_max: 5      # 连续写失败次数上限 → 自动解除
```

挂在 DCS YAML（可与 `gello_arm_sync` 同文件扩展）；`GelloArmTeleopConfig` 进 `config.py`（`extra=ignore`，旧键 `teleop_step_max_rad` 可残留）。

---

## 7. 实现清单（摘要）

### Runtime / Config

- [x] `GelloArmTeleopConfig` + `DcsConfig.gello_arm_teleop`
- [x] `_gello_arm_teleop_gate` + `set_gello_arm_teleop` + setpoint/write 双线程
- [x] 跳变闸（仅 jump_abort）+ stale 解除；status 字段
- [x] 与 sync / jog / disarm / shutdown 互斥钩子

### API / UI

- [x] `GET/POST /api/arm/gello-teleop`（`viz.py` + `desktop_main` 注入）
- [x] 按钮「摇操」↔「解除摇操」；弹窗；状态行
- [x] WS/status 广播 `gello_arm_teleop`（便于 UI 刷新，同 sync）

### 验证阶梯

- [x] dry_run / 单测：gate（未 Arm / 未对齐 / sync 互斥 / 对齐通过）；legacy step_max ignore
- [ ] dry_run：对齐后开摇操 → `write_count` 增长、`has_setpoint`；解除后停止
- [ ] dry_run：注入单拍 \(\delta>0.35\) → 自动解除 + error 文案
- [ ] dry_run：摇操中点动被拒；Disarm 停环
- [ ] 真机：急停在手；先同步再摇操；解除后晃 gello **臂不动**
- [ ] 真机：gello_trace 无 `teleop_cmd_rate_limited`；跟随观感接近 Fake 稠密路径

### 明确不做（首版）

- [x] 启动 / open / Arm 后自动摇操
- [x] 摇操写夹爪或合并 gripper sync
- [x] 笛卡尔空间遥操作
- [x] 用 WebSocket 从浏览器下发关节命令
- [x] 跳变后静默 hold 却仍显示摇操中

---

## 8. 与同步 / hik 差异表

| 项 | 同步 (`gello_arm_sync`) | 摇操（本计划） |
|----|-------------------------|----------------|
| 目标 | 冻结 \(q_g^{\star}\) | live \(q_g\) |
| 节拍 | 5 Hz × 100 点 / 轮 | 设定点 poll + 写 ≈100 Hz |
| 开环 \(\Delta\) | ≤ 0.8（可开始对齐） | ≤ 0.05（须已对齐） |
| 完成后 | 强制停写 | 「解除」或异常后停写 |
| 按钮 | 同步 | 摇操 |

| 项 | hik_gello | 本计划 |
|----|-----------|--------|
| 入口 | 进程启动后主环 | Arm +「摇操」 |
| 启动闸 | 0.8 + 斜坡 | 0.05（依赖「同步」） |
| 跟随中跳变 | 无显式产品闸 | 仅 jump_abort 超限解除（无步长限速） |

---

## 9. 建议实现顺序

1. Config + Runtime gate + latest-wins 双线程 + jump_abort + status  
2. 与 sync / jog / disarm / shutdown 互斥  
3. API + UI 按钮 / 弹窗 / 状态行  
4. 单测：gate、legacy 配置 ignore、解除停写  
5. 真机小行程：「解除后晃 gello，臂不动」+「跳变自动解除」+ trace 无 rate_limited  

---

## 10. 启用流程（草稿，实现后）

```bash
sensors-dcs run -c configs/gello_arm_sync.yaml          # 可同配置扩展 gello_arm_teleop:
# UI：Read 有数 → Arm →「同步」至完成 →「摇操」→ 扳 gello 看跟随
# 「解除摇操」后晃 gello，臂关节不变
# 必验：模拟跳变 → 自动解除 + 弹窗
```

---

## 11. 相关文件（实现时）

| 路径 | 职责 |
|------|------|
| `src/sensors_dcs/config.py` | `GelloArmTeleopConfig` |
| `src/sensors_dcs/runtime.py` | gate / 写环 / status / 互斥 |
| `src/sensors_dcs/viz.py` | API +「摇操」按钮 / 弹窗 |
| `src/sensors_dcs/desktop_main.py` | 注入 teleop 回调 |
| `docs/gello-arm-sync.md` | 一次性对齐（非遥操作） |
| `docs/arm-write.md` | Arm / 点动 / TT 语义 |
| `hww/hik_gello/experiments/run_env.py` | 旧项目跟随主环参考 |
