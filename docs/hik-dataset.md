# hik_dataset 产物全解（详细版）

本文说明 `export/hik_dataset/` **每一类内容从哪里来、怎么算、与 hik_gello 原管线有何异同**。  
实现入口：`src/sensors_dcs/export/hik_dataset.py`（`export_hik_dataset` / `write_hik_dataset` / `build_steps`）。

相关短文：

- 仅 `steps.json` 字段：[hik-dataset-steps.md](hik-dataset-steps.md)
- **`actions` 来源 / 可采集性评估**（含通俗解释）：[hik-dataset-actions.md](hik-dataset-actions.md) §0.1
- 过滤与 CLI：[filter-timeline.md](filter-timeline.md)
- Gello 关节标定：[gello-joint-affine.md](gello-joint-affine.md)

上游对照：`hww/hik_gello/save_data.py`、`data_postprocess.py`、`gello/zmq_core/robot_node.py`、`gello/robots/elite_robot.py`、`gello/robots/dh_ag95.py`。

---

## 1. 目录里有什么

典型输出：

```text
<episode>/export/hik_dataset/
  metadata.json          # 数据集级元数据（机器人、传感器列表、内外参等）
  steps.json             # 逐步 observations / actions
  rgb_<hik_cam>_<i>.jpg  # 第 i 步、某 hik 相机名的彩色图
  d_<hik_cam>_<i>.png    # 可选；当前 DCS 录制通常没有深度，故常缺省
  camera_map.yaml        # 本次使用的 serial→hik 名映射（拷贝）
```

同级常见旁路文件（不在 hik_dataset 内，但同源）：

```text
<episode>/
  manifest.json                 # 录制 start 即写；含 cameras.* 内参
  states/*.jsonl
  cameras/<agent_id>/...
  export/filtered/              # filter --materialize 同构树
  export/hik_dataset_meta.json  # CLI 导出摘要（若写出）
```

步长索引 **`i = 0 .. N-1`** 与 `export/filtered` 里各 agent 的 `seq` / 宽表 `step` 一致；图像文件名下标与 `steps.json` 第 `i` 个元素对齐。

---

## 2. 端到端数据流

```text
┌─────────────────── 在线采集（DCS）───────────────────┐
│  RealSense open() → camera_infos（内参）               │
│  record start() → manifest.json（含 cameras）          │
│  环缓冲写盘 → states/*.jsonl + cameras/*/index+jpg     │
│  record stop() → 补全 t_end / written（保留 cameras）  │
└──────────────────────────┬────────────────────────────┘
                           ▼
┌─────────────────── 离线时间轴 ───────────────────────┐
│  export-timeline → 长表 / 宽表（t_wall 对齐）          │
│  filter-timeline → 质量过滤 + step 重编号               │
│  --materialize → export/filtered/{manifest,states,cams}│
│    （拷贝 manifest.cameras 内参）                      │
└──────────────────────────┬────────────────────────────┘
                           ▼
┌─────────────────── export-hik-dataset ───────────────┐
│  --camera-map：serial → hik 相机名                     │
│  读 filtered states/cameras → 对齐步列表               │
│  写 metadata.json / steps.json / rgb_* / camera_map    │
└──────────────────────────────────────────────────────┘
```

CLI：

```bash
# 两步
sensors-dcs filter-timeline -e episode_00000 --materialize \
  --require gello,cam-left,cam-middle --camera-map configs/hik_camera_map.yaml
sensors-dcs export-hik-dataset -e episode_00000 \
  --camera-map configs/hik_camera_map.yaml

# 一步（隐含 materialize）
sensors-dcs filter-timeline -e episode_00000 \
  --hik-dataset --camera-map configs/hik_camera_map.yaml ...
```

**输入硬依赖**：已存在的 `export/filtered/`（或 `--hik-dataset` 先 materialize），以及 **serial→名** 的 camera-map（或 filtered 内已有 `camera_map.yaml`）。

---

## 3. 与 hik_gello 原管线对照

| 环节 | hik_gello | sensors-dcs → hik_dataset |
|------|-----------|---------------------------|
| 原始采集 | `save_data.py`：robot / **action_state** / 多相机并行缓冲 | DCS agents：gello/arm_read、gripper_read、realsense；**不录 ZMQ `_command_state`** |
| 对齐下采样 | `data_postprocess.parse_pickle_data` | `export-timeline` + `filter-timeline` + materialize |
| 相机命名 | 代码内 `camera_name_refator`（serial→名） | YAML `--camera-map`（同思路，可配置） |
| 训练目录 | `metadata.json` + `steps.json` + `rgb_*` / `d_*` | **同布局** |
| `steps.observations.joint` | pickle 里 `robot_state[:6]` | filtered 里 arm/gello 的 `joints_rad`（标定后） |
| `steps.observations.gripper` | `robot_state[-1]` | `gripper_read.position_norm`，否则 gello 第 7 维 |
| `steps.observations.cartesian` | `FK(q) @ tcp` → xyz+rpy | **同公式**；默认用 `sensors.kinematics`（demo_test 移植）；失败时才全 0 |
| `steps.actions.cartesian` | `inv(T_i)@T_{i+1}` 相对 TCP | 同；随 FK 可用性 |
| `steps.actions.gripper` | 下一步 `robot_state[-1]` | 下一步观测夹爪；末步 0 |
| 原始指令轨 | `action_state` ← ZMQ 上次 `command_joint_state` | **本导出不写入 steps**；DCS 也未采集该缓存 |
| 内参 | pickle 内 RealSense infos | `manifest.cameras`（open 时）→ metadata；可被 `--calibration-json` 覆盖 |

要点：**DCS 的 hik_dataset 对齐的是 data_postprocess 写出的训练目录语义，不是 save_data 里那条 `action_state` 指令日志。**

---

## 4. 上游 episode / filtered 各自贡献什么

### 4.1 原始 episode

| 路径 | 贡献给 hik_dataset 的内容 |
|------|---------------------------|
| `manifest.cameras.<agent_id>` | color/depth 内参矩阵、`depth_to_color`、serial、role、分辨率等（录制 **start** 写入） |
| `manifest.agents[]` | kind / hz；gello 可有 `gello_calib` |
| `states/<arm\|gello>.jsonl` | 经 filter 后成为关节源 |
| `states/<gripper>.jsonl` | 经 filter 后成为夹爪源 |
| `cameras/<agent>/` | 经 filter 后拷贝为 `rgb_*` |
| `cameras/*/index.jsonl` 的 serial | 解析 hik 名；filtered 缺 serial 时可回退读原始 episode |

### 4.2 `export/filtered/`（materialize）

与原始同构，但：

- 每步各 agent **同一 `t_wall`（master 时钟）**，`seq == step`
- 图已按对齐结果拷贝为 `00000000.jpg` …
- `manifest.cameras` 从源 episode **原样拷贝**（供内参）
- 可选落下 `camera_map.yaml`

`export_hik_dataset` **只读 filtered**（serial 可再回落到源 episode），不重新跑对齐。

---

## 5. 关节与夹爪：从哪一列进 `steps`

`_pick_joint_source(manifest.agents)`：

1. **臂**：优先 `kind=arm_read`，否则 `kind=gello`
2. **夹爪**：优先 `kind=gripper_read`；否则用臂 payload 里 `joints_rad[6]`（若存在）

每步：

```text
observations.joint_position[i]  ← payload.joints_rad 前 6 维（不足补 0）
observations.gripper_position[0][i] ← gripper.position_norm 或 joints_rad[6]
```

Gello 路径下 `joints_rad` 已是仿射后：

```text
q = (q_raw − offsets) ⊙ signs
```

详见 [gello-joint-affine.md](gello-joint-affine.md)。`joints_rad_raw` / 夹爪 `raw_value` **不进** `steps.json`（仍可在 episode/filtered states 里查）。

---

## 6. `steps.json` 全字段（来源 + 算法）

更短的字段表见 [hik-dataset-steps.md](hik-dataset-steps.md)。此处强调 **怎么来的**。

### 6.1 结构

```json
{
  "observations": {
    "joint_position": [[...6...], ...],       // 长度 N
    "gripper_position": [[g0, g1, ..., gN-1]], // 外层长度 1
    "cartesian_position": [[x,y,z,rx,ry,rz], ...]
  },
  "actions": {
    "cartesian_position": [[...6...], ...],
    "gripper_position": [[...]]
  }
}
```

单位：关节/欧拉角 **rad**，平移 **m**。欧拉角为 **XYZ 外旋**（与 scipy `as_euler("xyz")` / hik_gello 一致）。

### 6.2 `observations`（当前步状态）

| 字段 | 来源 | 算法 |
|------|------|------|
| `joint_position[i]` | filtered 臂/gello `joints_rad` | 取前 6 维 |
| `gripper_position[0][i]` | gripper_read 或 gello[6] | 归一化开合（非 Modbus 原始值） |
| `cartesian_position[i]` | 由关节 **正算** | 见下 |

**笛卡尔观测（有 FK 时）**：

```text
T_flange = FK(joint_position[i])          # 4×4
T_tcp    = T_flange @ T_tcp_offset        # 默认 tcp ≈ (0,0,0.18)
obs_cart[i] = [T_tcp.x,y,z] + euler_xyz(T_tcp.R)
```

**无 FK 时（kinematics 导入失败）**：`obs_cart[i] = [0]*6`，`metadata.cartesian_source = "zeros_no_fk"`，并写入 `cartesian_fk_error`。  
默认已接入 `sensors.kinematics`（自 demo_test），`cartesian_source` 一般为 `"fk"`。FK 本身只需 NumPy；旧的 `steps.json` 需重新 `export-hik-dataset` 才会更新。

### 6.3 `actions`（从状态轨迹推出，不是指令缓存）

与 hik_gello `data_postprocess` **相同策略**：

| 字段 | 有 FK | 无 FK |
|------|-------|-------|
| `actions.cartesian_position[i]` | `T_rel = inv(T_tcp[i]) @ T_tcp[i+1]` → xyz+rpy；**末步 `[0]*6`** | 全 `[0]*6` |
| `actions.gripper_position[0][i]` | **下一步**观测夹爪；**末步 `0`** | 仍按「下一步夹爪」填（不依赖 FK） |

相对笛卡尔是在 **当前 TCP 坐标系**下的增量，不是基座系绝对目标。

### 6.4 与 hik_gello「缓存 action_state」的关系（勿混）

| | 原始 `action_state`（save_data） | `steps.json` 的 `actions` |
|--|----------------------------------|---------------------------|
| 来源 | ZMQ `ZMQServerRobot._command_state`：最近一次 `command_joint_state` | 相邻 **观测** `robot_state` / filtered joints 推演 |
| 形态 | 绝对关节指令（约 7 维）+ 下发时间戳 | 相对 TCP 6 维 + 下一帧夹爪 |
| Elite/DH | Elite 本身不存命令；夹爪 `last_state` 仅供周期 `SetTargetPosition` | 不读这些缓存 |
| DCS | **未采集** | 仅有后处理推出的 actions |

二者意图上常相关（跟手好时轨迹形状接近），但 **空间、延迟、夹爪偏移（如 Elite `+0.3`）都不同**，不能互换。详见调研结论：训练标签走「状态轨迹推演」这条线。

---

## 7. 图像文件

### 7.1 彩色 `rgb_<hik_cam>_<i>.jpg`

1. filtered：`cameras/<agent_id>/<seq:08d>.jpg`（materialize 已对齐）
2. 用 **serial**（index.jsonl / 源 episode）+ `--camera-map` 得到 `hik_cam`
3. `shutil.copy2` → `rgb_{hik_cam}_{i}.jpg`（`i` 为对齐步下标，通常等于 filtered `seq`）

命名示例（map 里 `336222075436 → top`）：

```text
rgb_top_0.jpg
rgb_rear_left_1_0.jpg
```

**不**把像素写进 `steps.json`。

### 7.2 深度 `d_<hik_cam>_<i>.png`

仅当 filtered `index.jsonl` 带 `depth_file` 且文件存在时拷贝。当前 DCS RealSense 写盘以 JPEG 彩色为主，**多数包没有深度**，`metadata.depth_camera_num` 常为 0。  
hik_gello 原管线会对深度做 `align_depth_to_rgb`；DCS 导出若无深度文件则跳过该步。

---

## 8. `metadata.json` 全字段

`create_metadata` + 导出时 `extra` 合并。

### 8.1 与 hik_gello 对齐的核心字段

| 字段 | 含义 | DCS 默认 / 来源 |
|------|------|-----------------|
| `name` | 数据集名 | `"hik_ebai_data"` |
| `robot` | 机器人标识 | CLI `--robot-name`，默认 `"elite"` |
| `task_morphology` | 形态 | `"single_arm"` |
| `dof` | 臂自由度 | `6` |
| `sensor_list` | 传感器键名列表 | `rgb_<hik>`，有深度再加 `d_<hik>` |
| `rgb_camera_num` / `depth_camera_num` | 计数 | 由 `sensor_list` 统计 |
| `natural_language` | 语言标注 | CLI，默认可空 |
| `intrinsic_matrix` | 各相机 3×3 K | 见 §8.2 |
| `extrinsic_matrix` | 各相机 4×4 | 见 §8.3 |

### 8.2 `intrinsic_matrix`

键：`rgb_<hik_cam>`（有深度时还有 `d_<hik_cam>`）。

优先级：

1. **`--calibration-json`** 里的 `intrinsic_matrix`（覆盖）
2. 否则 **`manifest.cameras[agent_id].intrinsic_matrix`**（录制 start / RealSense `open()`）
   - depth 优先 `depth_intrinsic_matrix`，否则回退 color K
3. 再没有 → **单位矩阵** `I_3`

`extra.intrinsics_source`：`"calibration_json"` | `"episode_manifest"` | `"identity"`。

### 8.3 `extrinsic_matrix`

- 默认常为 **单位阵 `I_4`**（DCS 通常无「相机到机器人」手眼结果）
- 若 manifest 有 `depth_to_color`，会写入 `d_<hik>` 的 extrinsic（深度→彩色外参，与 hik 深度对齐语义相近）
- `--calibration-json` 的 `extrinsic_matrix` 可覆盖

手眼 `camera_to_robot` 在 hik_gello 里常来自标定；DCS 未自动生成时不要假设 metadata 外参已是基座系。

### 8.4 DCS 扩展字段（`extra`）

| 字段 | 含义 |
|------|------|
| `source` | `"sensors-dcs"` |
| `source_format` | `"dcs_filtered_v1"` |
| `cartesian_source` | `"fk"` 或 `"zeros_no_fk"` |
| `tcp_xyz` | 写入 steps 笛卡尔时用的 TCP 偏移 |
| `camera_agent_map` | `hik_name → agent_id` |
| `steps` | 步数 N |
| `intrinsics_source` | 见上 |

---

## 9. `camera_map.yaml`

```yaml
cameras:
  "317222074437": rear_left_1
  "336222075436": top
```

- **必填**（或 filtered 里已有同内容文件）
- 键必须是 RealSense **序列号**；值是 hik 训练侧相机名（出现在 `rgb_*` / `metadata.sensor_list`）
- 导出时拷到 `filtered/camera_map.yaml` 与 `hik_dataset/camera_map.yaml`，便于复跑

解析失败常见原因：filtered index 无 serial、map 缺该 serial、agent 未进 require。

---

## 10. 内参从采集到 metadata 的链路

```text
RealSenseSensor.open()
  → _capture_camera_infos() / dry_run 占位 K
  → sensor.camera_infos

RealSenseAgent.camera_infos_dict()
  → RecordController.start()
      → 写 provisional manifest.json
         cameras.<agent_id> = { serial, intrinsic_matrix, depth_*, depth_to_color, ... }

RecordController.stop()
  → 最终 manifest（合并保留 cameras）

filter materialize
  → filtered/manifest.json 拷贝 cameras

export-hik-dataset
  → intrinsics_from_episode_cameras(cameras, hik_to_agent)
  → metadata.intrinsic_matrix["rgb_"+hik] = ...
```

旧 episode（start 时尚未写 cameras）导出时 K 可能退化为单位阵，除非补 `--calibration-json`。

---

## 11. CLI / 代码参数对照

| 参数 | 作用 |
|------|------|
| `-e` / episode | 源 episode 目录 |
| `--filtered-dir` | 默认 `export/filtered` |
| `-o` / `--output-dir` | 默认 `export/hik_dataset` |
| `--camera-map` | serial→hik YAML |
| `--robot-name` | `metadata.robot` |
| `--natural-language` | `metadata.natural_language` |
| `--tcp-z` | TCP 平移 z（默认 0.18）；完整 xyz 可在 API 传 `tcp_xyz` |
| `--calibration-json` | 覆盖内外参 |
| API `fk=` | 注入正运动学；CLI 默认 **不注入** → 笛卡尔全 0 |

---

## 12. 当前能力边界（读包时注意）

1. **`observations/actions.cartesian_position`**：默认调用 `sensors.kinematics.make_hik_fk_fn()`（Elite machine 关节 rad→FK）；仅当 kinematics 不可用时才全 0（`zeros_no_fk`）。
2. **无原始 `action_state` 指令轨**：与 hik 采集侧「ZMQ 上次下发」不同；训练 actions 仅状态推演。
3. **深度图 / 深度对齐**：多数 DCS 包没有。
4. **手眼外参**：默认单位阵，除非 calibration / 手工写入。
5. **夹爪**：用归一化 `position_norm`；Modbus `raw_value` 只在原始 states。
6. **相机名**：完全依赖 camera-map + serial，无硬编码表。

---

## 13. 字段速查总表

| 产物 | 内容 | 直接来源 |
|------|------|----------|
| `rgb_<hik>_<i>.jpg` | 彩色帧 | filtered `cameras/<agent>/<file>` + camera-map |
| `d_<hik>_<i>.png` | 深度（可选） | filtered `depth_file` |
| `steps.observations.joint_position` | 6 轴 rad | arm_read / gello `joints_rad` |
| `steps.observations.gripper_position` | 归一化开合 | gripper_read / gello[6] |
| `steps.observations.cartesian_position` | TCP 基座系 xyz+rpy | FK@tcp；无 FK → 0 |
| `steps.actions.cartesian_position` | 相对 TCP 增量 | inv(T_i)@T_{i+1}；无 FK → 0 |
| `steps.actions.gripper_position` | 下一步夹爪 | 观测轨迹；末步 0 |
| `metadata.intrinsic_matrix` | 3×3 K | manifest.cameras 或 calibration |
| `metadata.extrinsic_matrix` | 4×4 | 默认 I；depth_to_color / calibration |
| `camera_map.yaml` | serial→名 | `--camera-map` 拷贝 |

---

## 14. 相关代码索引

| 模块 | 职责 |
|------|------|
| `sensors/.../camera/realsense.py` | open 时抓内参 |
| `sensors_dcs/record.py` | start/stop 写 manifest.cameras |
| `sensors_dcs/export/filter.py` | materialize + 拷贝 cameras |
| `sensors_dcs/export/hik_dataset.py` | 对齐加载、metadata、steps、拷图 |
| `sensors_dcs/agents/gello_agent.py` | 关节仿射 |
| `hww/hik_gello/data_postprocess.py` | 训练目录与 steps 语义原型 |
| `hww/hik_gello/gello/zmq_core/robot_node.py` | 原始 action_state 缓存（DCS 未用） |
