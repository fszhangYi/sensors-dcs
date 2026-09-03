# hik 训练集 `steps.json` 字段说明

> **全链路详细版**（目录、metadata、图像、与 hik_gello / action_state 对照）：见 [hik-dataset.md](hik-dataset.md)。  
> **`actions` 怎么算、能否采集**：见 [hik-dataset-actions.md](hik-dataset-actions.md)。  
> 本文仅展开 **`steps.json` 各字段含义**。

`export-hik-dataset`（或 `filter-timeline --hik-dataset`）写出的目录与 `hik_gello/data_postprocess.py` 对齐：

```text
export/hik_dataset/
  metadata.json
  steps.json
  rgb_<hik_cam>_<i>.jpg
  # 可选 d_<hik_cam>_<i>.png
```

本文说明 **`steps.json` 各字段含义**，以及图像下标与 step 的对应关系。实现见 `src/sensors_dcs/export/hik_dataset.py` 的 `build_steps`。

---

## 1. 总体约定

| 概念 | 说明 |
|------|------|
| **步长索引 `i`** | `0 .. N-1`，与过滤后对齐宽表的 `step`、以及文件名 `rgb_*_<i>.jpg` 一致 |
| **`observations`** | **当前步**测得的状态（看什么） |
| **`actions`** | **从当前步指向下一步**的控制量（做什么）；最后一步无「下一步」，对应字段置零 |
| **数组长度** | 除夹爪外，各列表长度均为 `N`；夹爪为嵌套一层 `[[ ... N 个标量 ... ]]` |
| **单位** | 关节角、欧拉角：**弧度 (rad)**；平移：**米 (m)**（与 hik_gello FK/TCP 约定一致） |

`steps.json` **不**内嵌图像像素；图像按文件名与 `i` 对齐。

示意结构：

```json
{
  "observations": {
    "joint_position": [[j0..j5], ...],
    "gripper_position": [[g0, g1, ...]],
    "cartesian_position": [[x,y,z,rx,ry,rz], ...]
  },
  "actions": {
    "cartesian_position": [[dx,dy,dz,drx,dry,drz], ...],
    "gripper_position": [[g1, g2, ..., 0.0]]
  }
}
```

---

## 2. `observations`（观测 / 当前状态）

### 2.1 `observations.joint_position`

- **类型**：`list[list[float]]`，形状约 `N × 6`
- **含义**：第 `i` 步机械臂 **6 轴关节角**（不含夹爪）
- **DCS 来源**（按优先级）：
  1. `arm_read` 的 `joints_rad`（若有）
  2. 否则 `gello` 的标定后 `joints_rad`（前 6 维；见 [gello-joint-affine.md](gello-joint-affine.md)）
- **不足 6 维**时右侧补 `0`，超过则截断到 6

### 2.2 `observations.gripper_position`

- **类型**：`list[list[float]]`，形状 `1 × N`（外层长度为 1，兼容多夹爪布局；单臂只用 `[0]`）
- **含义**：第 `i` 步夹爪开合量
- **DCS 来源**：
  1. `gripper_read` 的 `position_norm`（DH AG95 等驱动归一化值）
  2. 否则若仅有 gello，取 **第 7 维** `joints_rad[6]`（若存在）
- **取值**：与采集驱动一致的归一化开合；**不是**原始舵机脉冲（原始值在 episode `states` 的 `raw_value` / `position_raw`，**不**进 `steps.json`）

### 2.3 `observations.cartesian_position`

- **类型**：`list[list[float]]`，形状约 `N × 6`
- **含义**：第 `i` 步 **末端执行器（TCP）在机器人基座坐标系下的位姿**，写成：

  ```text
  [x, y, z, rx, ry, rz]
  ```

  | 分量 | 含义 |
  |------|------|
  | `x, y, z` | TCP 平移（米），基座系 |
  | `rx, ry, rz` | 姿态欧拉角，**XYZ 外旋**（与 `scipy.spatial.transform.Rotation.as_euler("xyz")` / hik_gello 一致），弧度 |

- **在 hik_gello 中的计算**（本导出对齐同一公式）：

  ```text
  T_flange = FK(joint_position[i])     # 法兰 / 机器人 FK
  T_tcp    = T_flange @ T_tcp_offset   # 再乘 TCP 偏移（默认约 z=0.18 m）
  cartesian = [T_tcp 的平移] + [T_tcp 旋转矩阵 → xyz 欧拉角]
  ```

- **在当前 sensors-dcs 默认行为**：
  - 默认注入 `sensors.kinematics.make_hik_fk_fn()`（Elite machine 关节语义，来自 demo_test）
  - 仅当 kinematics 不可用时：整表填 **`[0,0,0,0,0,0]`**
  - `metadata.json` 中会带 `cartesian_source`：
    - `"fk"` — 已用 FK+TCP 算出真实位姿
    - `"zeros_no_fk"` — 无 FK，笛卡尔全零

---

## 3. `actions`（动作 / 相对下一步）

与 `observations` 不同：`actions` 描述 **从步 `i` 到步 `i+1` 的变化或目标**，最后一步无后继，置零。

### 3.1 `actions.cartesian_position`

- **类型**：`list[list[float]]`，约 `N × 6`
- **含义（有 FK 时）**：相邻两帧 TCP 位姿的 **相对变换** 的 xyz+rpy：

  ```text
  T_rel = inv(T_tcp[i]) @ T_tcp[i+1]
  action_cartesian[i] = [T_rel 平移] + [T_rel 旋转 → xyz 欧拉角]
  ```

  即在 **当前 TCP 坐标系**下，下一步 TCP 相对当前位置的增量（hik_gello 注释中的 *delta xyz* 同类量，含姿态）。

- **最后一步** `i = N-1`：固定为 `[0,0,0,0,0,0]`
- **无 FK**：整表 `[0]*6`（与 observations 笛卡尔同源限制）

> 注意：这是 **相对位姿增量的 6D 表示**，不是基座系下绝对目标位姿；绝对值在 `observations.cartesian_position`。

### 3.2 `actions.gripper_position`

- **类型**：`1 × N` 嵌套列表（同 observations）
- **含义**：第 `i` 步动作对应的夹爪目标 = **下一步** `observations.gripper_position`（即步 `i+1` 的夹爪）
- **最后一步**：`0.0`
- **无 FK 时**：仍按「下一步夹爪」填写（夹爪不依赖 FK）；仅最后一步为 `0`

---

## 4. 与图像、元数据的关系

| 资源 | 关系 |
|------|------|
| `rgb_<hik_cam>_<i>.jpg` | 与 `joint_position[i]` 等同一步；相机名来自 `--camera-map`（serial→hik 名） |
| `d_<hik_cam>_<i>.png` | 若 materialize 含深度才会写出 |
| `metadata.json` | 数据集级描述：机器人名、`sensor_list`、内外参、`cartesian_source` 等（见 [filter-timeline.md](filter-timeline.md) §2.6） |
| `metadata.intrinsic_matrix` | 优先来自录制 start 写入的 `manifest.cameras.*.intrinsic_matrix` |

`steps.json` **没有**单独的 `joint` action 字段：关节轨迹在 observations；笛卡尔 action 为相对 TCP；夹爪 action 为下一帧开合。

---

## 5. 字段速查

| 路径 | 每步含义 | 无 FK 时 |
|------|----------|----------|
| `observations.joint_position[i]` | 当前 6 轴关节角 (rad) | 仍写入真实关节 |
| `observations.gripper_position[0][i]` | 当前夹爪归一化开合 | 仍写入 |
| `observations.cartesian_position[i]` | 当前 TCP 在基座系的 `[xyz,rpy]` | **全 0** |
| `actions.cartesian_position[i]` | `i→i+1` 的相对 TCP `[Δxyz, Δrpy]`；末步 0 | **全 0** |
| `actions.gripper_position[0][i]` | 下一步夹爪；末步 0 | 仍按下一步填写 |

---

## 6. 相关文档

- **hik_dataset 全解（推荐）**：[hik-dataset.md](hik-dataset.md)
- 过滤与导出入口：[filter-timeline.md](filter-timeline.md)
- Gello 关节标定（进入 `joint_position` 的值）：[gello-joint-affine.md](gello-joint-affine.md)
- **actions 来源与可采集性**：[hik-dataset-actions.md](hik-dataset-actions.md)
- 上游参考：`hww/hik_gello/data_postprocess.py` → `parse_pickle_data` 中构造 `steps` 的段落
