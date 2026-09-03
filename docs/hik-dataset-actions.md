# hik_dataset `actions` 来源与可采集性评估

> 日期：2026-09-03  
> 范围：`steps.json` 中的 **`actions`**（及为算清它而依赖的观测与上游对照）  
> 代码主入口：`src/sensors_dcs/export/hik_dataset.py` → `build_steps`  
> 上游对照：`hww/hik_gello/data_postprocess.py` → `parse_pickle_data`；采集侧 `save_data.py` / `gello/zmq_core/robot_node.py`  
> 字段速查仍见 [hik-dataset-steps.md](hik-dataset-steps.md)；全目录说明见 [hik-dataset.md](hik-dataset.md)。

---

## 0. 结论（先读）

| 问题 | 结论 |
|------|------|
| `steps.actions` 是「下发指令日志」吗？ | **不是**。DCS 与 hik_gello 训练目录里的 `actions` 都是从 **相邻观测状态** 推出来的标签。 |
| 笛卡尔 action 怎么来？ | 有 FK 时：\(T_{\mathrm{rel}}=T_i^{-1}T_{i+1}\)（TCP 系相对位姿 → xyz+rpy）；末步全 0；无 FK 时整表 0。 |
| 夹爪 action 怎么来？ | **下一步**观测夹爪；末步 0。不依赖 FK。 |
| hik_gello 的 `action_state`（ZMQ 上次 `command_joint_state`）进不进 `steps.actions`？ | **不进**。它写在 pickle / 预览视频叠加里，**不**写入 `steps.json`。 |
| 当前 DCS 能否得到与 hik 训练语义一致的 actions？ | **能计算**：在 FK 可用、关节/夹爪观测正确的前提下，公式与 hik_gello `parse_pickle_data` 同构。 |
| 还缺什么？ | （1）FK 与 hik `RL_ROBOT` 数值是否逐位一致尚未做对拍；（2）原始「指令轨」可部分落盘（`arm_write`/`gripper_write`），但 **不参与** `build_steps`；（3）无 ZMQ 式统一 `_command_state` 流。 |

一句话：**训练用的 `actions` 是状态轨迹的后处理结果，不是传感器直接采到的「动作通道」。** 当前系统已经能按同一策略算出它们；若误把 ZMQ/`arm_write` 指令当成 `steps.actions`，会对不齐上游真实行为。

通俗版见下方 **§0.1**。

---

## 0.1 通俗理解（对话共识）

下面用日常说法钉死几件容易想岔的事。技术细节仍以正文为准。

### 不是「gello 下发 → 差分 → 再积分出机械臂」

容易先想到：遥控端上下帧算个 delta，再积成臂的每时刻位姿。  
**实际完全反过来：**

```text
每帧先有绝对关节角 q_i（优先机械臂读数 arm_read，没有才用 gello）
  → FK(q_i) 得到该帧绝对 TCP 位姿 T_i   （写进 observations）
  → action_i = inv(T_i) @ T_{i+1}       （写进 actions；最后一帧填 0）
```

- **没有**「用 gello 的 delta 积分出 arm」。  
- 每一时刻的位姿都是对该帧关节 **正算一次 FK** 得到的绝对量；action 只是相邻两帧绝对位姿的相对变换。

### 也没有用 gello / 写通道的下发指令

`steps.actions` **不是**当时遥控器或 `arm_write` / `gripper_write` 发出去的命令。  
它是事后从观测轨迹 **算出来的监督标签**——训练里当 ground truth 用，但并不是「控制器日志里的那条真指令」。可以把它理解成：**一个按约定造出来的、与实测状态自洽的标签，而不是采到的下发真值。**

### 有 arm_read 时：机械臂永远「对」，gello 只是遥控工具

双源正常采集时：

- 进 `steps` 的关节 / 由此推出的笛卡尔观测与 action，都以 **`arm_read`（臂实际走到哪）** 为准。  
- **gello** 负责示教时把臂遥控起来；它可以落盘，但默认 **不是** steps 的关节源，更不会当 action。  
- 语义上就是：**标签对齐「臂做了什么」**，不是「主臂当时喊了什么」。

例外：配置里没有 `arm_read`、只能靠 gello 当关节源时，轨迹才变成「主臂读数当状态」——那就不是「臂永远对」了。

### 夹爪同一套，只是更简单

- 观测优先 **`gripper_read`**；没有才退 gello 第 7 维；**不用** `gripper_write` 下发。  
- `actions.gripper[i]` = **下一帧读到的夹爪**；最后一帧为 0。  
- 没有 FK、没有相对位姿——同样是「读爪轨迹当真理，下发只当遥控」。

### 默认前提：read 到的就是正确的

整条链路默认相信 **`arm_read` / `gripper_read` 可信**。  
观测和推出来的 action 都钉在这条实测轨迹上，**不会**用写通道去对账、纠错。  
因此：读数漂、标定错、读写不同步 → 标签跟着错。

### 三句话收束

1. **因果是「状态 → 差分」，不是「差分 → 积分成状态」。**  
2. **标签对齐从端实测，不对齐遥控/下发指令。**  
3. **相信 read；gello（以及 write）在 steps 语义里是工具，不是 ground truth 源。**

---

## 1. 问题界定：两套容易混的「action」

| 名称 | 在哪 | 是什么 | 进不进 `steps.json` |
|------|------|--------|---------------------|
| **A. 训练 actions** | `export/hik_dataset/steps.json` → `actions.*` | 由观测关节 → FK → 相对 TCP；夹爪取下一帧 | **进**（本文主对象） |
| **B. 指令缓存 / action_state** | hik：`save_data` pickle；DCS：可选 `arm_write` / `gripper_write` jsonl | 控制器「上次下发」的关节/夹爪目标 | **默认不进 steps** |

下文若无特别声明，**action = A**。

---

## 2. DCS：`steps.actions` 怎么算

### 2.1 输入：对齐后的观测步 `_AlignedStep`

`export-hik-dataset` 读 `export/filtered/`，`load_aligned_from_filtered` 按对齐 `seq` 组步：

| 字段 | 取值规则 |
|------|----------|
| `joints`（6） | 优先 `arm_read`，否则 `gello`；取 `payload.joints_rad`，不足补 0、超出截断 |
| `gripper` | 同 `seq` 的 `gripper_read.position_norm`；否则若关节向量 ≥7 维取 `[6]`；再否则 `0` |
| 相机 | 另路拷贝 `rgb_*`；**不参与** action 公式 |

关节源选择（**明确排除写通道**）：

```178:199:src/sensors_dcs/export/hik_dataset.py
def _pick_joint_source(
    agents_meta: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (arm_agent_id, gripper_agent_id). Prefer arm_read over gello."""
    ...
    for kind in ("arm_read", "gello"):
        ...
    grip_ids = by_kind.get("gripper_read") or []
```

因此：即使 episode 里已有 `arm_write.command_joints_rad` / `gripper_write.command_position_norm`，**也不会**成为 `steps.actions` 的直接来源。

### 2.2 观测侧（算 action 的前置）

对每一步 \(i\)：

1. `observations.joint_position[i] = q_i`
2. `observations.gripper_position[0][i] = g_i`
3. 若注入 FK：  
   \(T_{\mathrm{tcp},i} = \mathrm{FK}(q_i)\,T_{\mathrm{tcp}}\)，  
   \(T_{\mathrm{tcp}}\) 默认平移 `(0,0,0.18)` m（`--tcp-z` / `tcp_xyz`）  
   → `observations.cartesian_position[i] = xyzrpy(T_{\mathrm{tcp},i})`
4. 无 FK：笛卡尔观测与笛卡尔 action 均为 `[0]*6`；`metadata.cartesian_source = "zeros_no_fk"`

FK 默认：`sensors.kinematics.make_hik_fk_fn()`（demo_test Elite DH；返回法兰 4×4，TCP 在 `build_steps` 内右乘）。

### 2.3 动作侧公式（核心）

实现：`build_steps`（与 hik_gello 同结构）：

```265:280:src/sensors_dcs/export/hik_dataset.py
    for i, item in enumerate(aligned):
        if fk is None or poses[i] is None:
            steps["actions"]["cartesian_position"].append([0.0] * 6)
            grip_next = float(aligned[i + 1].gripper) if i + 1 < len(aligned) else 0.0
            steps["actions"]["gripper_position"][0].append(grip_next if i + 1 < len(aligned) else 0.0)
            continue
        if i == len(aligned) - 1:
            steps["actions"]["cartesian_position"].append([0.0] * 6)
            steps["actions"]["gripper_position"][0].append(0.0)
            continue
        cur_ee = poses[i]
        next_ee = poses[i + 1]
        assert cur_ee is not None and next_ee is not None
        cur_action = np.linalg.inv(cur_ee) @ next_ee
        steps["actions"]["cartesian_position"].append(_pose_to_xyzrpy(cur_action))
        steps["actions"]["gripper_position"][0].append(float(aligned[i + 1].gripper))
```

| 字段 | 有 FK 且非末步 | 末步 | 无 FK |
|------|----------------|------|-------|
| `actions.cartesian_position[i]` | \(T_{\mathrm{rel}}=T_i^{-1}T_{i+1}\) → `[Δx,Δy,Δz,Δrx,Δry,Δrz]`（相对**当前 TCP**） | `[0]*6` | `[0]*6` |
| `actions.gripper_position[0][i]` | \(g_{i+1}\) | `0` | 仍用 \(g_{i+1}\) / 末步 `0` |

要点：

- 笛卡尔 action 是 **相对位姿的 6D 表示**，不是基座系绝对目标，也不是关节增量。
- 欧拉角约定：XYZ 外旋，与 `scipy.spatial.transform.Rotation.as_euler("xyz")` / hik 一致。
- `steps.json` **没有** `actions.joint_position`；关节只出现在 observations。

### 2.4 数据流（DCS）

```text
录制 agents
  arm_read / gello     → states/*.jsonl  joints_rad
  gripper_read         → position_norm
  （可选）arm_write / gripper_write → command_*（不进 build_steps）
       ↓
export-timeline → filter-timeline --materialize
       ↓
export/filtered/{states,cameras,manifest}
       ↓
export-hik-dataset
  load_aligned → build_steps(FK, tcp)
       ↓
export/hik_dataset/steps.json  ← actions 仅在此步生成
```

---

## 3. 上游 hik_gello：同一套训练语义 + 另一条指令轨

### 3.1 训练目录里的 actions（与 DCS 同构）

`hww/hik_gello/data_postprocess.py` 的 `parse_pickle_data`：

```208:226:/root/autodl-tmp/hww/hik_gello/data_postprocess.py
        steps['observations']['joint_position'].append(aligned_data[i]["robot_state"][:6])
        steps['observations']['gripper_position'][0].append(aligned_data[i]["robot_state"][-1])
        current_pos = robot.fk(aligned_data[i]["robot_state"][:6])
        cur_ee = np.dot(current_pos, tcp)
        ...
            cur_action = np.dot(np.linalg.inv(cur_ee), next_ee)
            ...
            steps['actions']['cartesian_position'].append(action_pos)
            steps['actions']['gripper_position'][0].append(aligned_data[i+1]["robot_state"][-1])
        else:
            steps['actions']['cartesian_position'].append([0.0] * 6)
            steps['actions']['gripper_position'][0].append(0.0)
```

对照表：

| 项 | hik_gello | sensors-dcs |
|----|-----------|-------------|
| 关节观测 | pickle `robot_state[:6]` | filtered `arm_read`/`gello` `joints_rad` |
| 夹爪观测 | `robot_state[-1]` | `gripper_read` / gello`[6]` |
| 笛卡尔 action | \(T_i^{-1}T_{i+1}\) | 同 |
| 夹爪 action | 下一步 `robot_state[-1]`；末步 0 | 同 |
| 默认 TCP Z | 0.18 m | 0.18 m |
| FK 实现 | `RL_ROBOT` / EA66 等 | `sensors.kinematics`（demo_test DH） |
| 是否用 `action_state` 填 steps | **否** | **否** |

### 3.2 采集侧的 `action_state`（不进 steps，易误解）

ZMQ `robot_node` 在 `command_joint_state` 时缓存 `_command_state`；`get_command_state` 读出：

```55:71:/root/autodl-tmp/hww/hik_gello/gello/zmq_core/robot_node.py
                elif method == "command_joint_state":
                    ...
                        self._command_state = args["joint_state"].tolist()
                        self._command_state.append(timestamp)
                ...
                elif method == "get_command_state":
                    ...
                        result = copy.deepcopy(self._command_state)
```

`save_data.collect_action_data` 约 200 Hz 把该向量写入 pickle 的 `action_state`。后处理对齐时会挂上 `action_state`，主要用于 **视频叠加 / 调试**，**不会**写入 `steps['actions']`。

> 若外部材料声称「postprocess 的 action 标签来自 action_state」，以 **本仓库对照的源码** 为准：与代码不符。

---

## 4. 评估：当前系统能否采集 / 计算

### 4.1 分项能力

| 量 | 能否「采」 | 能否「算」进 hik_dataset | 说明 |
|----|------------|---------------------------|------|
| 观测关节 | **能** | **能** | `arm_read` / `gello` → `observations.joint_position` |
| 观测夹爪 | **能** | **能** | `gripper_read`；无则 gello 第 7 维 |
| 观测笛卡尔 TCP | 一般**不采**真值 | **能算**（FK） | 依赖 kinematics；失败则全 0 |
| **训练 actions.cartesian** | 不直接采 | **能算** | 依赖 FK + 相邻观测关节 |
| **训练 actions.gripper** | 不直接采 | **能算** | 仅需夹爪观测序列 |
| 原始指令关节（类 action_state） | **部分能** | **不导出到 steps** | 有 `arm_write`/`gripper_write` 落盘时；`build_steps` 忽略 |
| ZMQ 统一 `_command_state` 7 维流 | **不能**（架构不同） | — | DCS 非 hik ZMQ robot_node 同构 |

### 4.2 「能对齐 hik 训练语义」的条件

已满足 / 可控：

1. 导出默认注入 FK + TCP 0.18 m，公式与 hik 一致。  
2. 夹爪 action 策略一致。  
3. 录制侧只要有 `arm_read`（或标定后 gello）+ `gripper_read`，观测侧即可支撑 action 推演。

仍属风险 / 缺口（不影响「公式是否实现」，影响「数值是否与旧包一致」）：

1. **FK 数值对拍未做**：demo_test DH vs hik `RL_ROBOT(EA66)` 是否逐帧一致未知。  
2. **对齐与抽帧策略不同**：hik 有相机主时钟、`Δq < 0.001` 丢弃等；DCS 走 timeline/filter，动作序列长度与时刻可能不同。  
3. **指令轨**：训练 steps 不需要它也能与 hik **steps 语义**对齐；若业务要「对比下发 vs 实测」，需另开导出，不能指望现有 `actions.*`。

### 4.3 与近期写通道改动的关系

`arm_write` / `gripper_write` 已可进 timeline / filtered states（指令与反馈字段），用途是 **审计、回放、对比**。  
它们 **不改变** 当前 `steps.actions` 定义；要把指令写进训练集，需要 **新产品约定**（例如额外字段或并行 json），而不是改口称现有 `actions` 来自指令。

---

## 5. 场景对照：你实际在采什么 → 能得到什么 actions

| 采集配置 | 观测关节 | 观测夹爪 | actions.cartesian | actions.gripper |
|----------|----------|----------|-------------------|-----------------|
| arm_read + gripper_read + FK 正常 | 真值 | 真值 | 相对 TCP 真值推演 | 下一步夹爪 |
| 仅 gello（已标定）+ gripper / 第 7 维 | gello 标定角 | 有 | 同上（源换成 gello） | 同上 |
| FK 导入失败 | 仍有 | 仍有 | **全 0** | 仍有下一步 |
| 无夹爪读数 | 有 | 0 | 笛卡尔仍可算 | 多为 0 |
| 仅有 arm_write、无 arm_read | **steps 可能无臂源 / 失败** | — | 写通道 **不会**自动当观测 | — |

---

## 6. 建议（边界清晰，不扩 scope）

1. **训练消费方**：继续把 `actions` 理解为「状态差分标签」，与 hik_gello 后处理一致；勿与 ZMQ `action_state` 混谈。  
2. **质量门禁**：对同一组关节，抽几帧对比 DCS FK vs hik FK（法兰/TCP）；`metadata.cartesian_source` 必须为 `"fk"` 再入库。  
3. **若需要指令监督**：在 filtered / 旁路元数据中保留 `arm_write`/`gripper_write`，另文档字段；**不要** silently 改写现有 `steps.actions` 语义。  
4. **文档入口**：字段字典用 [hik-dataset-steps.md](hik-dataset-steps.md)；本报告专讲 **来源、公式、可采集性与和指令轨的边界**。

---

## 7. 相关路径速查

| 路径 | 角色 |
|------|------|
| `src/sensors_dcs/export/hik_dataset.py` | `_pick_joint_source` / `build_steps` / `export_hik_dataset` |
| `src/sensors_dcs/export/filter.py` | materialize；`arm_write` → `command_joints_rad` |
| `sensors/kinematics`（软链） | `make_hik_fk_fn` / `fk_flange_from_joints_rad` |
| `hww/hik_gello/data_postprocess.py` | 训练 steps 原型 |
| `hww/hik_gello/save_data.py` | 采集 `robot_state` / `action_state` |
| `hww/hik_gello/gello/zmq_core/robot_node.py` | `_command_state` 缓存 |
| [hik-dataset.md](hik-dataset.md) §6.3–6.4 | 产品文档中的 actions / action_state 对照 |
| [hik-dataset-steps.md](hik-dataset-steps.md) §3 | 字段含义 |

---

## 8. 收束

`hik_dataset` 的 **action 不是传感器通道上读出来的**，而是导出阶段用 **观测关节 + FK + 下一步夹爪** 算出来的，且与 hik_gello `data_postprocess` **故意对齐**。  
当前 DCS **已经具备计算这些训练 actions 的能力**；采集侧需要保证的是 **可靠的臂/爪观测** 与 **可用的 FK**，而不是再采一条「action 传感器」。  
真正尚未对齐 hik 采集包的，是 **可选的原始指令轨**（以及 FK 数值对拍），那不属于现有 `steps.actions` 的定义范围。
