# 力觉 / 触觉接入点（对照 DeepSeek 分享 + 本仓库）

来源：[DeepSeek 分享对话](https://chat.deepseek.com/share/6fqb383cvl48jcxsut)。文中传感器精度/成功率等数字来自该对话，落地前仍需对照 datasheet 与实测。

## 结论

对话建议把 **指尖触觉（帕西尼类）** 与 **腕部六维力** 做成「腕-指协同」，用于精密装配 / 滑移检测 / 视-触-力策略。  
本仓库里：**驱动与 probe 已就绪，数采与 M1 辅助标注尚未接线**——亮点工程应落在「DCS 录进 episode → 停录启发预填 → 金标对照」，而不是再写一套驱动。

| 对话里的角色 | 本仓库对应 |
|--------------|------------|
| 腕部六维力 | `kind: ft` → `ForceTorqueSensor`（HIK 串口腕力） |
| 指尖触觉 | `kind: tactile` → `PaxiniTactileSensor`（帕西尼 DLL） |
| ROS 节点 / Franka | **不必**；走 hik-sensors + sensors-dcs 即可 |
| 停录后特征 / 简历量化 | 对齐计划 **M1 辅助标注飞轮** |

## DeepSeek 对话要点（摘要）

1. 触觉不要当摆设：对准视觉盲区（精密插装、滑移、接触质量）。  
2. 六维力 = 宏观合力/力矩；触觉 = 局部分布 / 高频微接触；可分层滑移或装配力控。  
3. 工程硬点：时间戳对齐、坐标标定、高频实时处理、基线/去皮。  
4. 简历要写对比指标（成功率、峰值力），并写清同步与标定。

对话里对「帕西尼 = 六维力」的表述与本仓库拆分不一致：此处以 **本仓库 kinds** 为准（`ft` 与 `tactile` 分开）。

## 本仓库现状

### 已有（sensors / sensors-view）

| Kind | 驱动 | 作用 |
|------|------|------|
| `ft` | `sensors/src/sensors/drivers/extensions/force_torque.py` | 腕力串口；`force` / `torque` / `wrench`；可 `tare` |
| `tactile` | `sensors/src/sensors/drivers/extensions/paxini.py` | 帕西尼；`rest_force` / `contact_force` / 阵列分量 |

- 注册：`@register_sensor` + `load_all_drivers()`  
- 示例设备：`sensors/configs/default.yaml`（`ft-wrist`、`tactile-paxini`）  
- Probe / 测速：`sensors-view`（`CONNECT_FIELDS`、rate-probe 文档）

### 缺口（sensors-dcs）

- `AgentConfig.type` **没有** `ft` / `tactile`  
- 各 cell YAML **未**声明对应 agent → **不会**写出 `states/*.jsonl`  
- 无 `grasp_heuristic.py`、停录预填、差异 sidecar（M1 仍待开发）  
- timeline 列展开未覆盖 FT/触觉；后处理默认三步不含启发

## 接入点（按落地顺序）

### A. 采集热路径（必做才能「有数据」）

1. **设备 YAML**  
   把 `sensors/configs/default.yaml` 里 `ft-wrist` / `tactile-paxini` 拷入当前站的 `sensors_config`（如 `sensors-dcs/configs/sensors_default.yaml`），改 `port` / `dll_path`。

2. **Agent 类型**  
   - 扩 `sensors-dcs/src/sensors_dcs/config.py` 的 `AgentConfig.type`  
   - 新增只读 agent（模板：`gripper_read_agent.py`）→ `kind: ft` / `tactile`  
   - `agents/__init__.py` → `build_agent()` 注册  

3. **启动 YAML**（默认可关）  

```yaml
- id: ft-wrist
  type: ft
  sensor_id: ft-wrist
  hz: 100
  buffer_frames: 128
```

4. **录像**  
   `record.py` 已对非 RealSense agent 写 `episode_*/states/<id>.jsonl`——agent 起来即落盘，**不必改 writer 主路径**。  
   注意：`Frame.error` 会被 sampler 丢掉；FT 超时会变时间轴空洞。触觉 payload 很大，采集宜压成摘要字段，完整阵列可降频或另存。

5. **Viz**  
   `viz.py` 在 `gripper_read` 等分支旁为 `ft`/`tactile` 做扳手/热力卡片，避免掉进 `renderGello`。

### B. 与 DeepSeek / M1 对齐的「辅助标注」

1. `export/grasp_heuristic.py`：读 `gripper_read` + 可选 `ft`（触觉可作增强特征）→ 启发标签 + `ft=true|false`  
2. 停录弹窗（`viz.py` outcome modal）：预填可改；金标仍走 `task_outcome`  
3. Sidecar：`export/grasp_heuristic.json`（启发 vs 金标差异）  
4. 可选后处理步 `grasp-heuristic` ∈ `OPTIONAL_STEPS` / `INDEPENDENT_STEPS`  
5. 需要进对齐表时再扩 `export/timeline.py` 的 FT 列；启发式也可像 `traj_quality` 一样直接扫 jsonl

### C. 对话里的算法亮点（本仓之上）

驱动打通后，才适合做：腕-指分层滑移、插装力控、视-触 token 等。  
数据同步优先用 DCS 统一 `t_wall`/`hw_ts` 对齐（M3 / filter），标定腕系↔指尖系写进配置与文档。

## 边界

- 对话数字（0.01N、1000Hz、成功率%）**未经本仓验证**。  
- EtherCAT FT（`ft_ethercat`）仅在规划文档，非现成驱动。  
- 不要在 app 里 fork `sensors` 驱动；新 kind 只进核心库。

## 一句话（面试）

「核心库已有腕力 `ft` 与帕西尼 `tactile` 驱动和 probe；数采侧补 agent 写入 episode，停录用夹爪±力觉做辅助标注预填，人工金标与差异回流——对应能力计划 M1，并支撑腕-指协同课题叙事。」
