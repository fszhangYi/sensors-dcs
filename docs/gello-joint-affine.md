# Gello 关节仿射标定（joint_offsets / joint_signs）

调研依据：`/root/autodl-tmp/gello.py` 中 `DynamixelRobot.get_joint_state`，以及同构的  
`hww/hik_gello/gello/robots/dynamixel.py`、`hww/hik_gello/scripts/gello_get_offset.py`、  
`sensors/src/sensors/drivers/gello/leader.py`。

本文说明：**Dynamixel 原始读数不能直接当机械臂关节角用**；控制 / 录制 / 与 Elite 对齐前，必须做一次与物理装配相关的仿射变换。

---

## 1. 结论（先读这段）

| 量 | 含义 |
|----|------|
| `q_raw` | 舵机 Present Position → 弧度：`ticks / 2048 * π` |
| `q` | 与从臂 / 运动学对齐的关节角（录制、跟随应使用此量） |

仿射（核心）：

```text
q = (q_raw − joint_offsets) ⊙ joint_signs
```

- `joint_offsets`：每轴常数偏置（通常为 `k·π/2`，反映零位与舵机多圈零点差）
- `joint_signs`：每轴 `+1` / `−1`（反映传动方向是否与从臂坐标系相反）
- `⊙`：逐元素相乘

**不同 FTDI 串口 / 不同 Gello 实物，offsets、signs 不同**，应写成配置，而不是写死在业务逻辑里。

---

## 2. `gello.py` 中的读路径

### 2.1 原始弧度（驱动层）

`DynamixelDriver.get_joints()` 只做 ticks → rad：

```text
q_raw = ticks / 2048 * π
```

（与 hik-sensors `GelloLeaderSensor` 中公式一致。）

### 2.2 仿射后的关节角（机器人层）

`DynamixelRobot.get_joint_state()`：

```python
pos = (self._driver.get_joints() - self._joint_offsets) * self._joint_signs
```

即上一节的 `q = (q_raw − offsets) ⊙ signs`。

随后还有两类**后处理**（与仿射正交，但同属「读到的最终值」）：

1. **夹爪归一化**（若配置了 `gripper_config`）  
   最后一维映射到 `[0, 1]`：

   ```text
   g = clamp( (q_last − open_rad) / (close_rad − open_rad) , 0, 1 )
   ```

   `open_rad` / `close_rad` 由配置里的开合角度（度）转弧度得到。

2. **指数平滑**（EMA）  
   `α ≈ 0.99`：`pos ← last*(1−α) + pos*α`，降低抖动；会引入轻微相位滞后。

`GelloAgent.act()` 直接返回 `get_joint_state()`，因此 MegaCollect / hik_gello 侧「动作 / 示教」用的都是仿射后的量。

### 2.3 写路径（对照）

```python
command_joint_state(joint_state):
    set_joints(joint_state + joint_offsets)   # 注意：未再乘/除 signs
```

读是 `(raw − offset) * sign`，写侧当前实现只加了 `offset`。若某轴 `sign = −1`，读写不完全互逆；实际遥操作主路径多为**读 Gello → 写从臂**，写回 Dynamixel 较少。对接时勿想当然地「对称逆变换」。

### 2.4 `start_joints` 的 2π 折绕

若构造时传入 `start_joints`（期望零位姿态），会在已有 offset 上再叠加整圈修正，使当前读数最靠近目标姿态：

```text
Δoffset_i = 2π · round( (−s_i + c_i) / 2π ) · sign_i
offset_i  ← Δoffset_i + offset_i
```

用于上电后「摆到约定姿态再锁定零位」。日常录制若 offsets 已标定好，可不依赖这一步。

---

## 3. 参数从哪来

### 3.1 按串口绑定的配置表

`gello.py` / `hik_gello/.../gello_agent.py` 的 `PORT_CONFIG_MAP` 按 FTDI by-id（或 `COM6`）给出一套：

- `joint_ids`
- `joint_offsets`（多为 `n * π/2`）
- `joint_signs`（`±1`）
- 可选 `gripper_config`

示例（与 `FTAA088F` / `COM6` 一致的一版，摘自 `gello.py`）：

```text
joint_ids:     1..7
joint_offsets: 1·π/2, 4·π/2, 4·π/2, 4·π/2, 3·π/2, 2·π/2, 1.78
joint_signs:   +1, +1, −1, +1, +1, +1, −1
```

换一条手臂 / 换一个 USB 串号，必须换表或重标定。

### 3.2 标定脚本思路（`gello_get_offset.py`）

1. 把 Gello **摆到与从臂一致的已知姿态** `start_joints`（常为全 0）。
2. 读当前 `q_raw`。
3. 对每轴在 `[-8π, 8π]` 上以 `π/2` 为步长搜索 `offset`，使  

   `| sign_i · (q_raw_i − offset) − start_i |`  

   最小。
4. 打印 `best_offsets`（及折成 `n*π/2` 的形式）；夹爪另打开放/闭合角度建议。

`joint_signs` 一般由机械设计给定，标定脚本以给定 signs 为主搜 offsets。

---

## 4. 与 sensors-dcs / hik-sensors 的关系

hik-sensors `GelloLeaderSensor.read()` **已经实现同一仿射**：

```python
rad = ticks / 2048 * π
calibrated = (rad - offsets) * signs
# 返回 joints_rad = calibrated，并附带 joints_rad_raw = rad
```

配置项写在 **与 port 同级的设备 params**（及 `defaults.gello`），例如 `configs/sensors_gello.yaml`：

```yaml
devices:
  - id: gello-leader
    endpoint: "COM3"          # 或 Linux by-id
    params:
      port_substr: ""
      joint_offsets: [1.5708, 6.2832, ...]   # 与 port 放一起，按工位改
      joint_signs: [1, 1, -1, 1, 1, 1, -1]
```

加载链：`BundleConfig.defaults.gello` → `device.params` → `GelloLeaderSensor` → `GelloAgent`。

### 4.1 录制 `states/gello.jsonl`

每条 payload 同时包含：

| 字段 | 含义 |
|------|------|
| `joints_rad` | 仿射后（跟随 / 数据集 / FK 用这个） |
| `joints_rad_raw` | 仿射前舵机弧度 |
| `joints_raw_ticks` | Dynamixel ticks（可空） |
| `joint_offsets` / `joint_signs` | 本帧使用的标定（便于离线复现） |

`manifest.json` 的 `agents[]` 对 gello 另有 `gello_calib` 摘要。

### 4.2 后处理列约定

| 产物 | 标定后 | 原始 |
|------|--------|------|
| 长表 events | `j{i}` | `j_raw{i}` |
| 宽表 asof/nearest/grid | `{agent}.j{i}` | `{agent}.j_raw{i}` |
| 质量过滤 / filter | 仍检查 `{agent}.j0`（标定后） | 不参与 match_dt 判定 |
| materialize filtered | `payload.joints_rad` + `joints_rad_raw` + offsets/signs | |
| `export-hik-dataset` | 使用 `joints_rad`（标定后）写入 `steps.json` | |

`export_meta.json` / `filter_meta.json` 含 `gello_calib` 与列含义说明（`joint_columns`）。

缺省 offsets=0、signs=+1 时 `joints_rad ≈ joints_rad_raw`，与 MegaCollect 不对齐——**工位 YAML 必须填对本机的 offsets/signs**。

---

## 5. 参考路径

| 路径 | 内容 |
|------|------|
| `/root/autodl-tmp/gello.py` | `DynamixelRobot.get_joint_state` / `PORT_CONFIG_MAP` |
| `hww/hik_gello/gello/agents/gello_agent.py` | 多串口 offsets/signs 表 |
| `hww/hik_gello/scripts/gello_get_offset.py` | offset 搜索标定 |
| `sensors/src/sensors/drivers/gello/leader.py` | DCS 实际读数与仿射实现 |
| `sensors-dcs/configs/sensors_gello*.yaml` | port + joint_offsets/signs |
| `sensors-dcs/src/sensors_dcs/agents/gello_agent.py` | 录制 payload 双值 + calib |
