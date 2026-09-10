# 推理 Tab「编排」子页 — 运行流程图（实现计划）

日期：2026-09-10  
状态：**实现中（P0–P2 已落地骨架）**  
范围：在推理 Tab 新增与「控制 / 预览」并列的「编排」子页；canvas 流程图编辑器 + 可执行「基础模块」；首版只交付一种模块类型与顺序连线执行。

---

## 0. 产品结论（约束）

| # | 结论 |
|---|------|
| 1 | 推理 Tab 子导航增加第三项：**编排**（`data-inf-page="flow"`），与「控制」「预览」并列。 |
| 2 | 画布为 **canvas 流程图**：左侧 toolbox 拖入「模块」；模块间 **有向连线** = 执行顺序。 |
| 3 | 首版只做一种模块：**基础模块（Basic）**。后续模块类型另开计划。 |
| 4 | 基础模块字段：`start`（6D pose + gripper）、`motion_mode`、`goal`（6D pose + gripper）、jerk 参数、起始误差阈值。 |
| 5 | `motion_mode` 仅两种：`program`（程序下发）\| `infer`（模型推理）。 |
| 6 | `program`：起点校验通过后，关节空间 **jerk 七段** 从起点姿态对应 joints 运动到终点（复用现有 abs ramp / `scale_by_d`）。 |
| 7 | `infer`：须 pi05 serve **已连接**；执行逻辑对齐 **LOOP 启动 / LOOP 继续**（step → 填 joints → abs 下发 → `waitInfArmArrive`，尊重 pause）；除终止点外还须配置 **终止条件**；仅当终止条件触发时，才将终止点**假装**为当前 LOOP 的最后一个点，并进入与 **LOOP 暂停** 等价的挂起态。 |
| 7a | `infer` 终止条件（首版）：由 **z 增大到某阈值** 或 **z 减小到某阈值** 两个要素构成（OR）；未触发前即使接近终止点也不结束本模块推理循环。 |
| 8 | 起点校验失败 → 模块 **显式失败**（UI 状态 + 中止本次编排 run），不得静默跳过。 |
| 9 | 每个模块自带 `t_min / t_max / v_norm / Tj / Ta`，下发时覆盖全局臂控制区同名参数（仅本模块本次运动有效）。 |
| 10 | 首版不引入第三方流程图库；自研轻量 canvas（节点 DOM/canvas 二选一，见 §3）。不改 `sensors` 核心驱动。 |

---

## 1. 与现有能力的关系

```text
控制页（已有）
  LOOP / 暂停 / 继续 / 单步
  绝对下发 + jerk（t_min…Ta）
  pi05 connect / step
  Read pose / joints / gripper
        │
        │  复用（不要叉一份）
        ▼
编排页（新建）
  FlowEditor（画布）
  FlowRuntime（按边拓扑跑模块）
    ├─ program → IK(start/goal) + arm abs ramp + gripper
    └─ infer   → 同 LOOP 一步链路；term_cond(z↑/z↓) 触发后假装 goal 为 loop 末点 → pause
```

| 能力 | 复用点 | 说明 |
|------|--------|------|
| 子 Tab 切换 | `switchInfPage` / `inf-subnav` / `dcs.inf.page` | 扩展第三态 `flow` |
| abs / jerk | `clampArmAbsTiming`、`sendInfArmJointsOnce`、`/api/arm/command` | 模块参数注入 body |
| IK | `/api/arm/ik` 或前端 `Ec616Ik` | pose→joints；种子用当前 Read |
| 到位等待 | `waitInfArmArrive` | 需抽成与 LOOP gen **解耦** 的通用 wait（见 §5.3） |
| LOOP 步进 | `runInfPi05StepOnce`、`fillInfArmJointsFromStep`、chunk-skip | infer 模式调用同一套 |
| LOOP 暂停 | `pi05LoopPaused` / `waitWhileLoopPaused` | infer **终止条件触发**（goal 假装为 loop 末点）后进入等价暂停；编排 run 另有 `flowPaused` 或复用同一 flag（见开放问题 Q1） |
| Gripper | `/api/gripper/command` `position_norm` | pose 第 7 维 = gripper 开度 |
| Pose 格式 | `[x,y,z,rx,ry,rz]` + `grip∈[0,1]` | 与 pi05 `robot_state[7]` / Read Cartesian 一致 |

---

## 2. 数据模型

### 2.1 图（Graph）

```ts
type FlowGraph = {
  version: 1;
  modules: FlowModule[];
  edges: FlowEdge[];          // from → to，有向；首版单后继足够（允许多出边但 runtime 只取拓扑序一条主链或报错）
  meta?: { name?: string; updated_at?: string };
};

type FlowEdge = {
  id: string;
  from: string;              // module id
  to: string;
};
```

首版执行策略（建议）：

- 入度为 0 的模块作为候选入口；**恰好 1 个** 才可 Run，否则报错。
- 不允许环；保存 / Run 前做 DFS 环检测。
- 每个节点出度 ≤ 1（简化为链表）；若拖出多条出边，UI 警告并在 Run 时拒绝（二期再做分支）。

### 2.2 基础模块（Basic）

```ts
type Pose7 = {
  xyzrpy: [number, number, number, number, number, number]; // m, rad
  gripper: number;  // position_norm [0,1]，与 gripper Write / pi05 一致
};

type BasicModule = {
  id: string;
  type: 'basic';
  title?: string;
  // canvas
  x: number;
  y: number;
  // semantics
  start: Pose7;
  goal: Pose7;
  motion_mode: 'program' | 'infer';
  /** 仅 motion_mode==='infer' 有效；未触发前不算「到达终止」 */
  term_cond?: InferTermCond;
  start_tol: {
    pos_m: number;       // 默认 0.01
    rot_rad: number;     // 默认 0.05
    grip: number;        // 默认 0.05
  };
  timing: {
    t_min_s: number;
    t_max_s: number;
    v_norm_rad_s: number;
    jerk_seg_frac: number;   // Tj
    accel_seg_frac: number;  // Ta
  };
};

/** infer 终止条件：z 上/下阈值，OR 触发即视为可结束 */
type InferTermCond = {
  z_rise_to?: number | null;   // 当前 TCP z ≥ 该值则触发（增大到）
  z_fall_to?: number | null;   // 当前 TCP z ≤ 该值则触发（减小到）
};
// 约定：至少一个阈值非 null；均为 null → Run 前校验失败（infer 模块缺终止条件）
```

默认 timing 与控制页一致：`0.1 / 30 / 0.02 / 0.10 / 0.15`。

### 2.3 运行时状态（不持久化，或可选记 last_run）

```ts
type ModuleRunState =
  | 'idle'
  | 'checking_start'
  | 'running'
  | 'paused'          // infer 终止条件触发（goal 假装为 loop 末点），或用户点暂停
  | 'succeeded'
  | 'failed';         // 含起点超差、IK 失败、serve 断连、到位超时等

type FlowRunState = {
  running: boolean;
  paused: boolean;
  gen: number;                 // 取消令牌，对齐 pi05LoopGen
  current_module_id: string | null;
  modules: Record<string, { state: ModuleRunState; error?: string }>;
};
```

模块卡片上 **显式** 展示 `failed` + 错误文案（起点超差必须可读）。

### 2.4 持久化

- `localStorage` key：`dcs.inf.flow.graph`（首版）。
- 可选二期：导出 / 导入 JSON 文件；再远期进 YAML。

---

## 3. UI 结构

### 3.1 子 Tab 挂载

`viz.py` / `ui_i18n.py`：

```text
inf-subnav
  [控制] [预览] [编排]     ← 新增

infPageFlow (data-inf-page="flow")
  .flow-shell
    .flow-toolbox          ← 可拖模块类型
    .flow-canvas-wrap
      canvas#infFlowCanvas 或 .flow-canvas (DOM 层)
    .flow-toolbar          ← Run / 暂停 / 继续 / 停止 / 清空 / 适应画布
    .flow-inspector        ← 选中模块属性（pose、模式、infer 终止条件、timing、tol）
```

扩展 `switchInfPage`：`control | sensors | flow`；`localStorage dcs.inf.page`。

### 3.2 Toolbox

首版一项：

| 拖出类型 | 落盘 type | 说明 |
|----------|-----------|------|
| 基础模块 | `basic` | 唯一模块 |

拖到画布空白处 → `createBasicModule(x,y)`，填默认 start/goal（可读当前 TCP+grip，或全 0 + 提示「从 Read 填入」按钮）。

### 3.3 模块卡片（视觉）

建议 **DOM 节点叠在可平移缩放的 canvas/世界层上**（比纯 canvas 画控件更易做表单），连线用底层 `<canvas>` 或 SVG：

```text
┌─ Basic #1 ─────────────┐
│ 模式: [程序下发 ▾]      │
│ 起始: x,y,z,r,p,y | g  │  ← 可展开 / 「从 Read 填入」
│ 终止: …                │
│ 终止条件(仅 infer):     │  ← z↑到 / z↓到（可只填一侧）
│ t_min … Ta             │
│ 状态: idle | failed…   │
│ ○out                in○│
└────────────────────────┘
```

- 端口：左侧 `in`、右侧 `out`；从 out 拖到另一模块 in 成边。
- 选中高亮；Delete 删模块（及关联边）。

### 3.4 画布交互（MVP）

- 平移：中键 / Space+拖；缩放：滚轮。
- 模块拖拽移动；框选可二期。
- 连线：端口 mousedown → mousemove 橡皮筋 → 合法 in 上 mouseup。
- 与 pose 3D 画布事件隔离（编排页独立，不共享 `infPoseCanvas`）。

### 3.5 库选型

| 方案 | 利 | 弊 | 建议 |
|------|----|----|------|
| A. 自研 DOM+SVG | 无依赖、易嵌现有 CSS/i18n | 连线/hit-test 自写 | **首版采用** |
| B. 引入 litegraph / drawflow 等 | 功能全 | 体积、皮肤、与 embody 风格冲突 | 不做 |
| C. React Flow | 体验好 | 本页是 Jinja/纯 JS，引入成本高 | 不做 |

---

## 4. 基础模块执行语义

### 4.1 公共：起始点位门禁

执行模块前：

1. 读当前臂 TCP `xyzrpy`（`window.__armReadCartesian` / FK）与 gripper `position_norm`。
2. 与 `module.start` 比：

   - 位置：\(\|Δp\|_2 ≤ start_tol.pos_m\)
   - 姿态：旋转向量模长或 rpy 差 ≤ `start_tol.rot_rad`（实现选定一种，默认 axis-angle）
   - 夹爪：\(|Δg| ≤ start_tol.grip\)

3. 任一超限 → `state=failed`，`error=start_pose_mismatch`（附实测 vs 期望），**编排 run 停止**，不执行后续模块。

### 4.2 `program`（程序下发）

```text
check start
  → IK(start) / IK(goal)（seed=当前 joints；失败 → failed）
  → gripper → start.gripper（可选：已在容差内则跳过）
  → 若当前 joints 距 start_joints 仍超关节阈值，可先短距 abs 到 start（开放问题 Q2）
  → abs ramp: joints_rad=goal_joints, timing=模块 timing（scale_by_d + seven_segment）
  → gripper → goal.gripper
  → wait arrive（通用 wait，可被 flow 暂停冻结）
  → 近 goal pose（容差可与 start_tol 同结构或更松）→ succeeded → 沿边进入下一模块
```

说明：与现网一致，**jerk 在关节空间**；「从一个起始 pose 到终止 pose」= 两端 IK 后再关节插值，不做笛卡尔直线（笛卡尔插值若需要另开计划，可复用 `arm_pose` 内已有路径雏形）。

### 4.3 `infer`（模型推理）

前置：`infPi05` 已连接，否则 `failed: serve_offline`。  
另须：`term_cond` 至少配置 `z_rise_to` 或 `z_fall_to` 之一，否则 `failed: missing_term_cond`。

#### 终止点 vs 终止条件

| 概念 | 含义 |
|------|------|
| **终止点** `goal` | 本模块目标 pose+grip；** alone 不足以结束** infer 循环。 |
| **终止条件** `term_cond` | 何时允许把终止点当作「当前 LOOP 的最后一个点」。首版两要素 **OR**：`z ≥ z_rise_to`（增大到）或 `z ≤ z_fall_to`（减小到）。未配置的一侧忽略。 |

语义（与用户约定对齐）：

1. 步进循环照常跑（对齐 LOOP）；每步后读当前 TCP `z`。
2. **仅当终止条件已触发** 时，才将 `goal` **假装**为当前 loop 的最后一个点（即：此后按「已到终止」路径处理——进入与 LOOP 暂停等价的挂起，而不是仅因接近 goal 就结束）。
3. 终止条件未触发前：即使 TCP 已靠近 `goal`，也 **继续** step，不 pause、不 succeeded。
4. 终止条件触发后：进入「假装 goal 为 loop 末点」；是否还要再核对 pose+grip 近 `goal`（容差）见开放问题 **Q7**。计划默认建议：**条件触发即视为可结束**（可选用近 goal 做软提示，不挡 pause）。

```text
check start + term_cond 已配置
  → 进入与「LOOP 启动」等价的步进循环（chunk-skip、send、wait arrive）
  → 每步后读 TCP z，判定 term_cond：
       z_rise_to 已设且 z ≥ z_rise_to  → 条件触发
       或 z_fall_to 已设且 z ≤ z_fall_to → 条件触发
       否则 → 继续 step（term/reject/IK/send 错误处理对齐 LOOP）
  → 条件触发 → 将 goal 假装为当前 LOOP 最后一个点
       → 模块 state=paused，编排进入暂停（复用 LOOP 暂停语义：冻结等待，不 bump gen）
  → 用户「继续」→ 同 LOOP 继续；若仍在该模块且未改 goal，可继续推理或按产品定为「暂停仅等人确认后进下一模块」（见 Q3）
```

用户原文：「到达终止点位之后如果是模型推理模式，则复用 loop 暂停的逻辑」→ 在补充终止条件后，应理解为：**终止条件触发并把 goal 当作 loop 末点之后 = 自动 pause**，不是自动 succeeded 跳下一模块。  
「继续」后的行为（留在本模块继续推理 vs 视为模块完成进下一节点）见开放问题 **Q3**；计划默认建议：

> **建议默认**：终止条件触发 → 假装 goal 为 loop 末点 → 自动 pause → 用户点「继续」= **本模块 succeeded，沿边执行下一模块**（若无后继则整图结束）。若需「暂停后再推理」，由用户把下一模块也设为 infer，或二期加模块内按钮「继续推理」。

### 4.4 编排级 Run / 暂停 / 继续 / 停止

| 控件 | 行为 |
|------|------|
| Run | `gen++`，从唯一入口模块开始顺序执行 |
| 暂停 | 冻结当前 wait/step（同 `pi05LoopPaused` 语义）；program 斜坡是否暂停见 Q4 |
| 继续 | 解除冻结；若因 infer **终止条件触发**（goal 被假装为 loop 末点）而 pause，按 Q3 默认进入下一模块 |
| 停止 | `gen++`，取消 run，模块态除 failed 外回 idle（failed 可保留至下次 Run） |

与控制页 LOOP **互斥**（建议）：编排 Run 中禁用 LOOP；LOOP 运行中禁用编排 Run。避免抢占 `arm_write` / abs ramp。

---

## 5. 代码落点（建议）

### 5.1 文件

| 路径 | 职责 |
|------|------|
| `docs/infer-orchestration-flow.md` | 本计划（本文件） |
| `src/sensors_dcs/viz.py` | 子 Tab HTML/CSS；挂载点；少量胶水 |
| `src/sensors_dcs/static/flow_editor.js` | 画布、toolbox、连线、序列化（新建） |
| `src/sensors_dcs/static/flow_runtime.js` | 模块执行状态机（新建） |
| `src/sensors_dcs/ui_i18n.py` | `infer.page_flow` 等文案 |
| `src/sensors_dcs/static/flow.css`（或并入 viz） | 编排页样式 |
| 测试 | `tests/test_flow_*.py` 仅测纯函数（容差、拓扑）；UI E2E 不做首版 |

`viz.py` 已很大：流程图 **逻辑放独立 JS**，经 `/assets/flow_*.js` 引入，避免再堆数千行进 `PREVIEW_HTML`。

### 5.2 对现有函数的抽取

从 `viz.py` 内联 LOOP 逻辑中抽出（或 `window.__*` 挂载）供 runtime 调用：

- `__flowSendAbs(joints, timing)` → 现 `sendInfArmJointsOnce` 参数化
- `__flowWaitArrive(gen, duration_s, isPausedFn)` → 现 `waitInfArmArrive` 去耦 `pi05Loop*`
- `__flowPi05Step()` → `runInfPi05StepOnce`
- `__flowReadPose7()` / `__flowCheckNear(pose7, tol)`
- `__flowGripper(norm)`
- `__flowIk(xyzrpy)`

### 5.3 后端

首版 **可不新增 API**：全部走现有

- `POST /api/arm/command`
- `POST /api/arm/ik`
- `POST /api/gripper/command`
- 既有 pi05 step 代理

若起点校验要服务端权威，二期再加 `POST /api/flow/check-start`（非必须）。

---

## 6. 分阶段交付

### P0 — 壳与编辑器（可演示，不可跑臂）

1. 子 Tab「编排」+ 空画布 + toolbox 拖入基础模块。  
2. 模块选中 inspector：编辑 start/goal（CSV / 数值格）、模式、infer 终止条件（z↑/z↓）、timing、tol；「从 Read 填入」。
3. 端口连线、删除、localStorage 存读。  
4. 环检测 / 单入口校验（仅提示）。

### P1 — `program` 单模块执行

1. Run 仅执行选中或唯一入口一个 basic + `program`。  
2. 起点门禁 + 显式 failed。  
3. IK + abs jerk（模块 timing）+ gripper + arrive。  
4. 工具栏停止。

### P2 — 多模块链表 + `infer` 模式

1. 沿边顺序执行多个 basic。  
2. `infer`：复用 LOOP step 链；终止条件（z↑/z↓）触发后将 goal 假装为 loop 末点 → pause；继续策略按 Q3。  
3. 与控制页 LOOP 互斥。  
4. 模块态颜色：idle / running / paused / succeeded / failed。

### P3 — 体验与稳健（可选）

1. 导出/导入 JSON；画布小地图。  
2. program 路径「先回到 start 再去 goal」。  
3. 分支/汇合；更多模块类型（延时、条件、Home…）。

---

## 7. 验收清单（相对本计划）

- [ ] 推理 Tab 可见「编排」，与控制/预览切换互不破坏。  
- [ ] 可从 toolbox 拖入基础模块并连线保存刷新不丢。  
- [ ] `program`：起点故意偏置 → 模块显示失败且不运动。  
- [ ] `program`：起点正确 → 臂 jerk 运动到 goal，夹爪到 goal.gripper。  
- [ ] `infer`：未连 serve → 失败提示；缺终止条件 → 失败提示；已连接 → 行为类似 LOOP，**仅终止条件触发后**将 goal 假装为 loop 末点并进入暂停态；条件未触发时靠近 goal 也不结束。  
- [ ] 模块 timing 与控制页全局值可不同，且本次下发生效。  
- [ ] 编排 Run 与 LOOP 不同时跑。

---

## 8. 开放问题（实现前需拍板）

| ID | 问题 | 建议默认 |
|----|------|----------|
| Q1 | 编排 pause 与控制页 `pi05LoopPaused` 是否共用一个 flag？ | **分设 `flowPaused`**，infer 步进复用 wait 原语但 flag 独立；UI 两边暂停按钮各管各的。 |
| Q2 | program 时若已过起点门禁但仍有厘米级误差，是否先 abs 到 start？ | **先直达 goal**（门禁已保证足够近）；若实机抖可加「强制经 start」。 |
| Q3 | infer **终止条件触发**（goal 假装为 loop 末点）自动 pause 后，「继续」含义？ | **模块成功 → 下一节点**（见 §4.3）。 |
| Q4 | program 斜坡中点「暂停」是否中止 ramp？ | 首版暂停只冻结合阶 wait；斜坡仍跑完（与现 LOOP 一致）。停止才 `cancel_abs_ramp`。 |
| Q5 | 姿态误差用 rpy 差还是旋转向量？ | **旋转向量模长**（与 IK 残差一致）。 |
| Q6 | 出度 >1 / 多入口？ | 首版 **禁止**，Run 前校验。 |
| Q7 | 终止条件触发后，是否还要求 TCP 近 `goal`（pose+grip 容差）才 pause？ | **不要求**；条件触发即 pause。近 goal 仅作 UI 提示（可选）。 |
| Q8 | `z_rise_to` 与 `z_fall_to` 同时配置时的语义？ | **OR**（任一触发即可）；与产品「两个要素」一致。 |

---

## 9. 非目标（本计划明确不做）

- 新模块类型（延时、并行、条件分支、子图）。  
- 笛卡尔空间 jerk / 直线运动。  
- 编排结果自动写成训练集 / 改 pi05 协议。  
- 服务端编排引擎（首版执行在浏览器状态机）。  
- 与「预览」页传感器 iframe 耦合。

---

## 10. 实现时建议顺序（落地 checklist）

1. 写本文件（✅）并评审 Q1–Q8。  
2. P0：Tab + `flow_editor.js` 骨架。  
3. 抽取 `window.__flow*` 适配层（不先大拆 LOOP）。  
4. P1：`program` 跑通。  
5. P2：边串联 + `infer` + 互斥。  
6. 补 i18n、失败态样式、简单单测（容差/拓扑）。
