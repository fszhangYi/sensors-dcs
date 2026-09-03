# Gello → Arm 同步（对齐）— 安全计划（未实现）

> 参考旧项目：`autodl-tmp/hww/hik_gello`（启动前对齐 + 斜坡）  
> 对照 DCS：`arm_write` 点动（`docs/arm-write.md`）、gello→gripper sync  
> 状态：**已实现首版**（Runtime + API/UI + `configs/gello_arm_sync.yaml`）；默认关闭。  
> **范围声明：本功能只做「把臂对齐到下发命令时刻的 gello 姿态」的一次性同步，不是遥操作。**

---

## 0. 最重要的产品边界（必读）

### 0.1 Gello 只提供一次目标 joints（同步过程不跟踪）

**同步目标 = 用户点击「同步」、服务端接受并开始下发命令那一刻，读到的 gello 前 6 轴 `joints_rad`。**

- Gello **只采样一次**，作为本轮轨迹的**固定终点** \(q_g^{\star}\)。
- **同步（ramping）进行期间，即使 gello joints 发生变化，也不更新终点、不改路径、不影响已生成的 100 个点。**
- 整轮 20s / 5Hz 下发的始终是「命令时刻冻结的那组目标」，**不是** live 跟随。
- 实现禁令：ramping 循环内 **禁止** 再读 gello 来改 `q_k`；gello ring 可以继续刷 UI，但写臂逻辑不得订阅。

| 是 | 否 |
|----|----|
| 命令时刻快照 \(q_g^{\star}\) → 插值 → 下发 | 同步中每拍追 gello 最新角 |
| 同步中人扳动 gello，臂仍走向**旧**目标 | 「边同步边跟手」 |

> 若校验失败后**重新开一轮** ramping：那是**新一次**「下发命令时刻」采样，与上一轮冻结值无关。单轮内部仍然只采一次。

### 0.2 同步结束后 gello 不能控臂

| 是 | 否 |
|----|----|
| 用户显式点「同步」→ 在固定时间内把 **arm 6 轴** 插值挪到 **命令时刻的 gello 前 6 轴** | 同步结束后 gello **继续**带动臂运动 |
| 对齐完成后自动停写，进入「已同步 / 完成」态 | 把 hik 的 100Hz 跟随遥操作搬进来 |
| 之后只能靠 **Arm 点动 ±**、Disarm、或将来单独的「遥操作」功能动臂 | 静默、常开、或 WebSocket 回传控制 |

**同步完成（含中途取消、失败停环）之后：gello 不得再向机械臂下发任何关节命令。**  
实现上必须满足：

1. 写环线程退出，且 `gello_arm_sync.enabled == false`
2. 不存在「后台仍按 gello 最新角 `TT_add_joint`」的路径
3. UI 明确显示「同步已结束 · gello 未控制机械臂」；不得显示成「跟随中」
4. 与现有 **gello→gripper sync** 无关；夹爪同步若开启，仍只写夹爪，不写臂

> 下文状态名仍用 **ramping → streaming**，但 **streaming 在此计划中 =「对齐校验 / 收尾」**，**不是** 持续遥操作流。校验通过即停写并结束同步。

---

## 1. 状态机（路线不变，语义收紧）

```text
idle
  │ 用户点「同步」
  ▼
gate（能否进入 ramping？）
  │ 否 → 弹窗：请手动把臂/ gello 摆到合适相对位置 → 回到 idle（不写臂）
  │ 是
  ▼
ramping（固定 20s × 5Hz = 100 路径点，整包下发）
  │ 100 点发完
  ▼
streaming（对齐校验，短窗口；默认不再连续跟 gello）
  │ 通过 → 停写 → idle/completed（gello 无控制）
  │ 不通过 → 重新算 delta，再进 ramping（可限最大重试次数）
  │ 取消 / Disarm / Estop / 安全退出 → 立刻停写 → idle（gello 无控制）
```

---

## 2. Gate：能否进入 ramping

在 **尚未下发运动** 前做检查；失败则 **弹窗**（或 UI 模态），提示手动调整，**禁止**自动硬拽。

建议条件（可配置）：

| 检查 | 建议 | 失败提示要点 |
|------|------|----------------|
| 配置齐全 | 存在 gello + arm read + arm_write | 缺 agent |
| 已 Arm | `arm_write.armed` | 请先点 Arm |
| live 数据 | gello、arm 均有有限 `joints_rad` 长度 ≥ 6 | 等待读数 |
| 最大关节差 | `Δ_max = max_i |q_g[i]−q_a[i]|`（i=0..5）≤ `align_max_rad` | **请手动控制机械臂（或挪 gello）到更接近的姿态后再同步** |
| 可选：软限位 | 目标 gello 姿态在 teach 软限位内 | 目标超限，请改姿态 |

说明：

- `align_max_rad` 建议默认 **0.8 rad**（对齐 hik 启动闸）；过大不允许开同步，避免 20s 内仍要跑过大行程。
- 弹窗文案需可操作：「当前最大关节差 = … rad（第 k 轴）；请点动/手动将臂移近 gello 后重试。」
- Gate 失败时：**零写**。

---

## 3. Ramping 算法（按你的思路）

### 3.1 采样端点（Gello 目标只采一次）

在 **gate 通过、即将开始下发** 的时刻各读一次并**冻结**（见 §0.1）：

- \(q_g^{\star} \in \mathbb{R}^6\)：**下发命令时刻**的 gello `joints_rad[0:6]` → **本轮唯一目标**；之后 gello 再变也忽略
- \(q_a \in \mathbb{R}^6\)：同时刻 arm read `joints_rad[0:6]`（必须用实测，禁止用虚构绝对角）

此后整轮 ramping **只使用**这对冻结值生成路径；不得在 100 拍循环里刷新 \(q_g^{\star}\)。

### 3.2 最大关节角 delta

\[
\Delta_{\max} = \max_{i=0..5} |q_g^{\star}[i] - q_a[i]|
\]

（用于日志 / 是否与 gate 一致；轨迹本身对 **每一轴** 做线性插值，见下。）

### 3.3 固定同步时间与节拍 → 路径点个数

| 参数 | 值 | 含义 |
|------|-----|------|
| `ramp_duration_s` | **20** | 本轮 ramping 总时长 |
| `ramp_hz` | **5** | ramping 下发频率 |
| `N` | **100** | \(20 \times 5 = 100\) 个路径点 |

> 节拍：**ramping 阶段下放频率固定为 5 Hz**（`period = 0.2 s`）。  
> 与后续若存在的其它写路径解耦；本阶段不用 50/100 Hz。

### 3.4 六轴一起插成 100 份

对每个轴 \(i=0..5\)、每个点 \(k=1..N\)（或 \(k=0..N-1\)，实现时统一闭开区间约定）：

\[
\alpha_k = \frac{k}{N},\quad
q_k[i] = q_a[i] + \alpha_k \cdot (q_g^{\star}[i] - q_a[i])
\]

即：在 **同一组** \(\alpha_k\) 下对所有关节线性插值；最大差那一轴恰好被「拉直」成 N 段，其余轴按各自总位移同比分段。  
**不是**只插 \(\Delta_{\max}\) 再复制到其它轴；而是「用统一时间比例，整向量从 \(q_a\) 插到冻结的 \(q_g^{\star}\)」。

得到路径：\(q_1, q_2, \ldots, q_{100}\)（最后一点应等于 \(q_g^{\star}\)，数值误差内）。  
路径在开写前一次性算完并缓存；下发循环只按索引取点。

### 3.5 以 5 Hz 顺序下发

对 \(k = 1..100\)：

1. （可选）再读一次 arm，作 `reference_joints_rad`（安全：相对当前实测限幅）
2. `arm_write.command(joints_rad=q_k, reference_joints_rad=…)`
3. 仍受驱动层 `max_delta_deg` / 软限位约束  
   - 若单步 \(q_k - q_{k-1}\) 已超过驱动单次上限：应在 **规划轨迹时** 就拒绝开同步或提高 N / 延长时间（实现时二选一写死策略，推荐：**gate 用 align_max 保证 20s×5Hz 下每步 ≤ max_delta**）
4. `sleep` 至满足 5 Hz；中途取消则立刻停、跳出，**不再继续发剩余点**

每步最大理论步进（均匀轴）：

\[
\delta_{\text{step}} \approx \Delta_{\max} / N = \Delta_{\max} / 100
\]

例如 \(\Delta_{\max}=0.8\) rad → 约 0.008 rad/步 ≈ 0.46°/步，通常低于默认 `max_delta_deg=2°`。

### 3.6 一轮 ramping 结束

100 点发完（或提前取消）→ 进入 **streaming（校验）**，**不要**自动改成「按 gello 最新角持续写」。

---

## 4. Streaming（本计划 = 对齐校验 + 收尾，非遥操作）

100 点发完后：

1. 再读 **当前** gello`[0:6]` 与 arm`[0:6]`（注意：ramping 期间人可能又动了 gello）
2. 计算 \(\Delta_{\max}' = \max_i |q_g'[i]-q_a'[i]|\)
3. **若** \(\Delta_{\max}' \le \texttt{sync_done_eps_rad}\)（建议默认 **0.02–0.05 rad**，可配）  
   → **判定同步完成**：  
   - 立即停写环  
   - `enabled=false`  
   - UI：成功提示「同步完成；gello 已不再控制机械臂」  
   - 臂保持最后姿态（仍 armed，除非用户 Disarm）  
4. **若**仍大于阈值：  
   - **重新**采集新的命令时刻 \(q_g^{\star},q_a\)，再跑一整轮 ramping（又是 20s / 100 点；新一轮目标再次只采一次）  
   - 可设 `max_ramp_rounds`（建议默认 **3**），超限则失败弹窗，停写，要求手动调整后重试  
5. streaming 阶段 **禁止**「固定高频把 live gello 当目标一直 `TT_add_joint`」——那是遥操作，不在本 plan

命名保留 streaming 仅图与前一版路线一致；文档与 UI 对外文案建议用 **「校验中 / 同步完成」**，避免用户理解成「流式遥控」。

---

## 5. 节拍与控制权（写清楚）

| 阶段 | 下发节拍 | 目标从哪来 | gello 能否控臂 |
|------|----------|------------|----------------|
| idle / gate 失败 | 无 | — | **否** |
| ramping | **固定 5 Hz**，共 100 拍 / 轮 | **仅**命令时刻一次采样的 \(q_g^{\star}\) 生成的 \(q_k\)；同步期间 gello 变化**不影响** | 不跟踪；只执行预设轨迹 |
| streaming（校验） | 少量读回比较，**默认零运动写** | 校验可读 live gello（只用于比误差，**不写臂**） | **否** |
| 同步完成 / 取消 / 失败 | 无 | — | **否** |

**再强调：gello 在本功能里的角色 = 提供一次目标 joints，不是实时主手。**  
若校验时 live gello 已离开 \(q_g^{\star}\)，按 §4 重开一轮时才会**再次**采样新的命令时刻目标；上一轮路径不改写。

---

## 6. 与 arm_write / UI 的关系

- 同步进行中（ramping）：**禁用** ± 点动与手动 `joints_rad`（同 gripper sync 互斥）
- Disarm / Estop / 安全退出 / 「取消同步」：立刻停环，**gello 无残余控制**
- 同步完成后：点动重新可用；**不会**因 gello 移动而自动写臂
- 夹爪：不进本功能；gello j6→gripper 仍为独立开关

UI 建议：

1. 按钮：「同步」 / 「取消同步」  
2. Gate 失败：**弹窗**（阻塞），含 \(\Delta_{\max}\) 与轴号  
3. 进度：ramping `k/100`、剩余约 `(100-k)/5` 秒  
4. 完成文案必须含：**「gello 未控制机械臂」**

---

## 7. 实现清单（摘要）

### Runtime

- [x] `set_gello_arm_sync(enabled)`：开 → gate → ramping 线程；关 → 停写
- [x] Ramping：命令时刻冻结 \(q_a,q_g^{\star}\)，生成 100 点，5 Hz 下发（环内不读 gello 改目标）
- [x] 结束后校验；失败重试至 `max_ramp_rounds`；成功则 **enabled=false** 并清线程
- [x] 任何退出路径保证：无 gello→arm 后台写
- [x] status：`phase=idle|ramping|verifying|completed|error`，`ramp_index`，`delta_max`，`rounds`

### API / UI

- [x] `POST/GET /api/arm/gello-sync`
- [x] 弹窗（gate / 重试耗尽 / 完成）
- [x] 进度条；完成态文案强调 gello 无控制

### 配置（建议）

```yaml
gello_arm_sync:
  align_max_rad: 0.8          # gate：过大不准开
  ramp_duration_s: 20
  ramp_hz: 5                  # → N=100
  sync_done_eps_rad: 0.03     # 校验通过阈值
  max_ramp_rounds: 3
```

### 验证阶梯

- [x] dry_run / 单测：路径插值与步数
- [ ] dry_run：gate 失败弹窗文案；成功则 100 步计数约 20s；完成后无继续写
- [ ] dry_run：校验失败触发第二轮 ramping
- [ ] dry_run：中途取消 → 写计数停止
- [ ] 真机：急停在手；小 \(\Delta_{\max}\)；看 20s 对齐；完成后晃 gello，**臂不动**
- [ ] 真机/dry_run：ramping 中途故意扳动 gello → 臂仍走向**点击同步时**的目标，不追新角度

### 明确不做

- [ ] 同步完成后的 gello 遥操作 / 100Hz streaming 跟随  
- [ ] ramping 中重读 gello 改终点或改路径（**目标只采一次**）  
- [ ] 把夹爪并进臂同步  
- [ ] open/启动自动同步  

---

## 8. 与上一版计划 / hik 的差异

| 项 | 上一版 DCS 草稿 | 本版（按你的算法） |
|----|-----------------|-------------------|
| ramping | 每拍朝 live gello 裁 0.05 rad | **20s / 5Hz / 100 点线性插值**，终点冻结 |
| streaming | 持续跟 gello（遥操作） | **仅校验 + 收尾停写** |
| 同步完成后 | 易理解成仍跟随 | **明确：gello 不能控臂** |
| 节拍 | sync_hz 50–100 | ramping **锁定 5 Hz** |

| 项 | hik_gello | 本版 |
|----|-----------|------|
| 目的 | 对齐后进入持续跟随 | **只对齐，不跟随** |
| 使能 | 构造即 TT | 先 Arm，再同步 |
| 开环闸 | 0.8 rad | 同，失败弹窗手动调 |

---

## 9. 建议实现顺序

1. Runtime：gate + 100 点生成 + 5Hz 下发 + 完成后强制停写（dry_run）  
2. 校验失败重试 + `max_ramp_rounds`  
3. API + 弹窗/进度/完成文案（强调 gello 无控制）  
4. 配置 YAML + 与 `arm-write.md` 交叉链接  
5. 真机小行程验收：「完成后晃 gello，臂不动」为必过项  

---

## 10. 启用流程（草稿）

```bash
sensors-dcs run -c configs/gello_arm_sync.yaml          # dry_run
# UI：Read 有数 → Arm →（手动靠近）→ 同步 → 看 100/100 → 「同步完成；gello 未控制机械臂」
# 必验：再动 gello，臂关节不变
```
