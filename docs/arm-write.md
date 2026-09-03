# Elite 机械臂 Write Agent — 安全计划与实现说明

> 参考：`autodl-tmp/hww/hik_gello/gello/robots/elite_robot.py`  
> 对照：DCS `gripper_write` agent（UI 只发命令，写环在服务端）  
> 状态：首版实现 **点动（± + delta）**；**不做** gello→arm 自动同步。

---

## 1. 背景与风险

`EliteRobot.command_joint_state` 将关节 **rad→deg** 后调用 `TT_add_joint` 流式下发；构造时还会 `robot_servo_on` + `TT_init`。

| 风险 | 说明 |
|------|------|
| 错误目标 / 单位错误 | 直接带动臂体 |
| open 即上使能 | 采集场景危险；DCS 必须拆成「连接监视」与「Arm 使能」 |
| 与 `arm_read` 混用 | read 契约禁止一切运动 API；writer 必须独立 `kind` |
| LAN 暴露 viz | 远程误点会动真机 |

现有 `arm_read` / `type: arm` **保持只读**，不得放开 `write()`。

---

## 2. 安全开发列表（清单）

### 0. 范围与原则

- [x] Writer 只做 **6 轴关节**；夹爪继续 `gripper_write`
- [x] 新建 `kind=arm_write` / agent `type: arm_write`
- [x] **必须与 `type: arm`（read）配对**：同一 `sensor_id`；点动相对 **read 最新 joints**
- [x] 默认需显式 **Arm**；`dry_run` 可无真机联调 UI
- [x] 单位：API/UI 用 **rad**（点动 delta 滑条用 **deg** 显示），下发 Elite 用 **deg**

### 1. 驱动层（sensors）

- [x] `dry_run`：校验长度/有限值，记录 last_cmd，零 Elite 运动调用
- [x] `open()`：仅连接 + monitor（对齐 read）；**不**自动 `servo_on` / `TT_init`
- [x] `arm` / `initialize`：servo_on + TT_init → armed
- [x] `write`：`joints_rad` / `jog` / `stop` / `disarm`；拒绝未知键
- [x] 软限位（teach soft limits）+ **单次最大 |Δq|**（默认 2°）

### 2. Agent / API / UI

- [x] `ArmWriteAgent`：默认不轮询写；响应 API；状态含 armed / last_cmd
- [x] 两段式：**Arm → 点动**；**Disarm / Estop**
- [x] UI：每轴 **±** + **delta 滚动条**（不做自由填绝对角首版）
- [x] （后续）gello→arm 同步：独立开关、默认关、限速+死人手 → 见 [gello-arm-sync.md](gello-arm-sync.md)（一次性对齐，非遥操作）

### 3. 配置与部署

- [x] 独立 `configs/robot_write.yaml` + `sensors_robot_write.yaml`
- [ ] 真机前慎用 `viz_host: 0.0.0.0`；防火墙限制 7011
- [ ] 打包说明：write 配置依赖 `elirobots`

### 4. 验证阶梯

- [x] dry_run 冒烟：Arm / ±点动 / Disarm
- [ ] 真机低速、清空空间、急停在手：仅 Arm + 读回
- [ ] 小 Δq 点动 → 再考虑连续 TT / 遥操作

### 5. 首版明确不做

- [x] 不做笛卡尔 / 大范围 `move_joint` PTP
- [x] 不把 gripper 偏移写进 arm writer
- [x] 不在 desktop 启动时自动 servo_on
- [x] 不做启动即自动 gello→arm 同步（对齐需显式「同步」；见 gello-arm-sync.md）

---

## 3. 控制语义（对齐 hik_gello）

| 动作 | Elite API | DCS 入口 |
|------|-----------|----------|
| 监视关节 | `monitor_info.machinePos` | `arm_write.read()` / 可选并列 `arm_read` |
| 使能流控 | `robot_servo_on` + `TT_init` | `POST ... arm=true` / `initialize` |
| 流式关节 | `TT_add_joint(deg×6)` | 点动合成绝对角后 `joints_rad` |
| 停止 | `stop`（若 SDK 可用） | `stop` / `disarm` |

点动：`q_cmd[i] = q_ref[i] ± delta_rad`，`q_ref` 优先 monitor 实测，否则 last_cmd。

---

## 4. UI（robot · Write）

1. **Arm** / **Disarm** / **Estop**
2. **delta** 滑条（度，例如 0.1°–5°）
3. 关节 j0…j5：**−** / **+**（每次发送一个 jog，数据经 API，不经 WebSocket 回传控制）

---

## 5. 配置示例

```bash
sensors-dcs run -c configs/robot_write.yaml          # dry_run 默认 true；含 arm read + arm_write
sensors-dcs run -c configs/robot_write.yaml --no-dry-run  # 真机（需确认清单）
```

配置要求：**同一 `sensor_id` 上同时挂 `type: arm` 与 `type: arm_write`**。无 read 时拒绝 Arm/点动。
