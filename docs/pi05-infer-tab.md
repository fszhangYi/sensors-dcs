# 推理 Tab + pi05 client — 实现计划

日期：2026-09-04  
状态：**计划中（未实现）**  
范围：新建「推理」Tab、共享 YAML、`pi05` client agent、采集时原样落盘 VLA 输出；**首版不控臂、不走 RTC**。

---

## 0. 对话结论（约束）

| # | 结论 |
|---|------|
| 1 | 推理服务只用 `pi05_jax_sft.serve`（相对 SE(3) → 服务端 `compose_pose` → `next_state`），**不做 RTC / serve_absolute**。 |
| 2 | **一个** agent：`pi05`（client），服务端在外部用 `scripts/serve.sh` 启动。 |
| 3 | 飞轮：**在线尽量原样多记**（传感器 + VLA 实际输出）；**可训练格式在后处理**再转。 |
| 4 | 推理 Tab **先复制「数据采集」Tab 再改**，最大化复用录制 / 预览 / 落盘。 |
| 5 | 复制后立刻改前后缀、`id` / `class`，避免与采集页 DOM 混淆。 |
| 6 | **与采集共用同一份 DCS YAML**；推理页**不校验、不展示 gello**。 |
| 7 | `pi05` 配置对采集 Tab **不生效、不展示**。 |
| 8 | `pi05` **只**：汇聚传感器观测 → TCP 发给 serve → 把返回展示到前端；**不控制机械臂、不调用 `arm_write`**。 |

---

## 1. 产品边界（必读）

### 1.1 两个 Tab 的分工

| | 数据采集 Collect | 推理 Infer |
|--|------------------|------------|
| 目的 | 人用 gello（等）采专家数据 | 连外部 pi05，看策略输出；可顺带录 episode |
| YAML | 同一 `SENSORS_DCS_CONFIG` / Settings 配置 | 同左 |
| gello | 正常校验与展示（若配置了） | **缺失/写错不报错；UI 不渲染 gello 卡片** |
| pi05 | **忽略配置；UI 不渲染** | 连接 IP/端口、发观测、展示 `next_state` |
| 写臂 | 点动 / 同步 / 摇操可用 | **首版禁止由 pi05 驱动写臂**（robot · Write 可保留展示，但不接 pi05→arm） |

### 1.2 pi05 client 做什么 / 不做什么

```text
做：
  - TCP 客户端连 host:port（serve 线协议）
  - 组包：top/chest/wrist2 JPEG + text/prompt + robot_state[7]
  - 收包：next_state[7] + term + reject + text
  - ring / WS 展示；录制时写入 episode（与传感器并列）

不做：
  - 不起 JAX 服务
  - 不调用 ArmWriteAgent.command / 不接 teleop 写环
  - 不做 RTC、temporal-agg 首版可不暴露（跟服务端默认 chunk-replay）
  - 不在线把输出「变成训练集」
```

### 1.3 与「像 gello」的关系（概念）

角色上 pi05 是「产生运动意图」的上游，但：

- 线协议是 **笛卡尔 `next_state`**，不是 `joints_rad`；
- 必须先喂图 + state；
- **首版明确不闭环控臂**，避免与采集摇操、安全门控纠缠；控臂作为后续阶段单独开计划。

---

## 2. 协议摘要（`serve` only）

参考：`/root/autodl-tmp/hww/pi05_jax_sft/src/pi05_jax_sft/serve.py`、`scripts/serve.sh`。

### 2.1 Client → Server（每步，big-endian）

1. `4B len + top JPEG`
2. `4B len + chest JPEG`
3. `4B len + wrist2 JPEG`（映射模型 `wrist`）
4. `4B len + UTF-8 text`（task prompt，可空）
5. `28B robot_state` = 7×float32 `[x,y,z,rx,ry,rz,gripper]`

### 2.2 Server → Client

1. `28B next_state` = 7×float32 绝对笛卡尔目标  
2. term / reject / 状态文本（按 serve 实现打包）

`robot_state` 来源规划：臂 FK TCP 位姿（与 hik / UI pose 一致，默认 TCP z=0.18m）+ gripper（`gripper_read` norm 或约定标量）。相机 JPEG 来自已有 `realsense` agent 最新帧（按 role / agent_id 映射 top/chest/wrist）。

---

## 3. UI：从采集 Tab 复制出推理 Tab

### 3.1 复制策略

1. 以 `#tab-collect` 整块为模板，复制为 `#tab-infer`。
2. 同步复制采集页相关工具条、预览栅格、录制按钮区、agent 卡片挂载点等 **结构与样式钩子**。
3. 导航增加按钮：`tabBtnInfer` / `data-tab="infer"` / i18n `tab.infer`。
4. `switchTab` 增加 `infer`；**Settings 齿轮仍仅主页**（Embody 约定不变）。
5. 登录后默认仍主页；主页可加「去推理」入口（可选，二期）。

### 3.2 标识重命名（防止与采集混淆）

约定前缀：**`inf`** / **`infer`**（二选一文档内统一用 **`inf`** 短前缀 + 语义名）。

| 采集（示例） | 推理（对应） |
|--------------|--------------|
| `#tab-collect` / `#tabBtnCollect` | `#tab-infer` / `#tabBtnInfer` |
| `#chkQuickCollect` | `#infChkQuickCollect`（若保留；语义可后改） |
| `#chkAsyncFlush` | `#infChkAsyncFlush` |
| `#btnStart` / `#btnStop` / `#btnDiscard` | `#infBtnStart` / `#infBtnStop` / `#infBtnDiscard` |
| `#saveDirInput` / `#btnSaveDir` | `#infSaveDirInput` / `#infBtnSaveDir` |
| `#runHint` / `#recState` / … | `#infRunHint` / `#infRecState` / … |
| `.arm-cmd`（采集卡内） | 推理侧卡片容器加 `#infAgents` / `.inf-agent-card`，内部 class 用 `.inf-arm-cmd` 等 |
| LS keys `dcs.asyncFlush` 等 | `dcs.inf.asyncFlush` 等，**勿共用 localStorage** |

CSS：推理面板根上加 `.tab-panel#tab-infer` 作用域；能复用的通用类（`.card`、`.agent-vals`）保留，**页面私有控件必须带 `inf-` 前缀**。

JS：采集逻辑函数不要直接操作推理 DOM；可抽共享 helper（录制 API、fmtRad），但 **事件绑定与状态变量分开**（`collectUi` vs `inferUi`）。

### 3.3 推理页相对采集的首批差异（复制后立刻改）

| 项 | 行为 |
|----|------|
| gello | 不渲染 gello 类 agent 卡片；后端不因缺 gello 锁 Tab |
| pi05 | 增加连接区：IP、端口、Connect/Disconnect、prompt、延迟/`next_state` 展示 |
| 写臂 | 保留 robot · Read / Write **展示**可与采集类似；**隐藏或禁用**依赖 gello 的「同步/摇操」入口；**不**把 pi05 输出接到写臂 |
| 文案 | i18n：`tab.infer`、`infer.*`（连接、未连接、发送中、错误） |

### 3.4 采集页相对推理的约束

- 配置里即使有 `type: pi05`：**采集 Tab 不展示**对应卡片，录制环可不订阅其决策流（或订阅但不在 UI 画卡——推荐 **采集录制不写 pi05 流**，仅推理 Tab 录制写 pi05，避免两套语义混盘；若共用 recorder，用 flag 区分，见 §6）。
- gello 校验逻辑 **只作用于采集**（及现有 boot_box / collect gate）。

---

## 4. YAML / 启动门控

### 4.1 共用一份配置

- 仍：`configs/default.yaml`（或 Settings 选中的路径）一份 DCS YAML。
- `agents:` 可同时列 `gello`、`realsense`、`arm`、`arm_write`、`gripper_*`、`pi05`。

### 4.2 门控矩阵

| 检查 | 采集 Tab | 推理 Tab |
|------|----------|----------|
| gello 缺失 / sensor 对不上 | 可按现有策略锁采集或告警 | **忽略，不锁推理** |
| pi05 缺失 | 不影响采集 | 推理可进页，但连接区提示「未配置 pi05」 |
| realsense / arm read 缺失 | 照旧 | 推理发观测时缺图/缺 state → 前端报错，不硬崩进程 |
| agents open 失败 | 现有 boot_box | 同进程；推理 Tab 是否可用取决于是否需要已 open 的相机/臂读数 |

实现提示：

- `boot_box` / `collect_ok` **不要**因「无 gello」而连推理一起锁。
- 可增加 `infer_ok`（例如：至少有相机或允许 dry 图），与 `collect_ok` 独立。

### 4.3 建议的 `pi05` agent 配置草案

```yaml
agents:
  - id: pi05
    type: pi05
    hz: 5                    # client 主动询问节奏（首版建议 5）
    buffer_frames: 8
    # 以下可放在 agent 扩展字段或顶层 pi05: 块（实现时二选一，优先顶层便于 UI）
# 顶层可选：
pi05:
  host: "127.0.0.1"
  port: 5000
  prompt: ""                 # 可被 UI 覆盖
  camera_map:
    top: cam-middle          # agent_id 或 role，实现时钉死一种
    chest: cam-left
    wrist2: cam-right
  # state: arm FK + gripper_read
```

UI「输入 IP」覆盖 `host`（及端口），写入进程内状态即可；是否回写 YAML 二期再定。

---

## 5. `pi05` agent 设计

### 5.1 注册

- `AgentConfig.type` Literal 增加 `"pi05"`。
- `build_agent` → `Pi05ClientAgent`（`kind` 建议 `pi05`）。
- **无对应 sensors YAML 设备**：这是纯客户端 agent，不绑 `SensorManager` 设备 id（或占位 `sensor_id` 可选）。若现有 `build_agent` 强制要 sensor，实现时允许 `sensor_id` 省略或指向 dummy。

### 5.2 运行时行为

```text
idle
  │ UI/API Connect(host, port)
  ▼
connected（TCP）
  │ 按 hz 或「手动 Step」：
  │   assemble obs from other agents' rings
  │   send serve frame
  │   recv next_state…
  │   push Frame to own ring + hub
  ▼
Disconnect → idle
```

首版节奏：**固定 `hz`（如 5）** 在已连接且「推理运行中」时轮询；暂停按钮只停询问、不断开（可选）。

### 5.3 Payload（ring / WS 展示）

建议字段：

- `connected`, `host`, `port`
- `robot_state`（发出的 7D）
- `next_state`（返回的 7D）
- `term_flag`, `reject_flag`, `server_text`
- `latency_ms`, `ok`, `error`
- `step` / `t_wall`
- 可选：各相机 jpeg 长度（不强制回传整图到 WS，避免炸带宽；录盘另议）

前端推理页：专用卡片 `pi05 · Client`（仅 `#tab-infer` 渲染路径挂载）。

### 5.4 API（建议）

- `GET /api/pi05/status`
- `POST /api/pi05/connect` `{ host, port }`
- `POST /api/pi05/disconnect`
- `POST /api/pi05/prompt` `{ prompt }`（可选）
- 运行开关可并入 connect 后自动 run，或 `POST /api/pi05/run { enabled }`

**禁止**：任何「把 next_state 发给 arm_write」的 API（首版）。

---

## 6. 录制与飞轮

### 6.1 在线（忠实记录）

推理 Tab 开始/结束录制 **复用** `RecordController` 与 episode 目录结构。

额外写入（新建流或 sidecar，实现时定一种）：

- 每个 pi05 步：`t_wall`, 发出 `robot_state`, 返回 `next_state`, term/reject, latency, prompt  
- 传感器流：与采集相同（arm / gripper / realsense / …）；**无 gello 时就不写 gello**

原则：**不在线插值成 50Hz 假专家轨迹**；策略步是稀疏决策记录。

### 6.2 后处理（二期，本计划只留接口）

- 新文档 / 命令：例如 `export-pi05-dataset`（名称待定）  
- 输入：episode（传感器 + pi05 决策）  
- 输出：与当前 `serve` 训练分布一致的样本（时间对齐、action 定义、图像打包）  
- **不阻塞** 首版 Tab + client 合并。

### 6.3 采集 Tab 录制

- 行为保持现状；不因 YAML 里有 pi05 而改变。  
- 若 recorder 全局订阅所有 agent：对 `kind==pi05` 在 **collect 会话** 中 skip；仅 **infer 会话** 写入。可用录制 session 标签 `mode=collect|infer` 区分。

---

## 7. 实施阶段

| 阶段 | 内容 | 完成标准 |
|------|------|----------|
| **P0** | 复制采集 Tab → 推理 Tab；全量改 id/class/LS；导航 + i18n；先功能对等但独立 DOM | 两 Tab 互不抢元素；切换正常 |
| **P1** | 推理页隐藏 gello；采集页隐藏 pi05；门控拆分 collect_ok / infer 可用性 | YAML 无 gello 仍可开推理页 |
| **P2** | `Pi05ClientAgent` + connect API + TCP `serve` 组包/解包；前端展示 `next_state` | 外部 `serve.sh` 下可见返回位姿 |
| **P3** | 推理 Tab 录制写入 pi05 决策流；与传感器并列落盘 | episode 内可回放/查到 VLA 输出 |
| **P4**（后续计划） | 后处理转可训练格式 | 另开文档 |
| **P5**（后续计划） | pi05→IK→arm_write 闭环 | 另开安全计划；**不在本首版** |

---

## 8. 非目标（本计划明确不做）

- RTC / `serve_absolute` / `serve_rtc.sh`
- pi05 驱动机械臂或调用 `arm_write`
- 采集与推理拆成两份 YAML
- 在线生成最终训练集
- 把 pi05 拆成 read/write 两个 agent

---

## 9. 风险与注意点

1. **带宽**：三路 JPEG × 5Hz 经 TCP；WS 勿整图广播。  
2. **相机映射**：role vs agent_id 必须与现场 `sensors_*.yaml` 一致，首版写死配置表。  
3. **pose 约定**：`robot_state` 必须与训练时 TCP/欧拉约定一致（复用 `arm_pose` / hik FK）。  
4. **DOM 泄漏**：复制后若漏改一个 `getElementById('btnStart')`，推理操作会打到采集——P0 应用 grep 验收。  
5. **安全**：即使用户以后要求控臂，也须另开门控文档；本首版 UI 文案应写明「仅推理展示，不控臂」。

---

## 10. 验收清单（首版）

- [ ] 存在「推理」Tab；id/class 与采集无冲突（抽样 grep `tab-collect` 控件 id 不在 infer 绑定中出现）
- [ ] 同一 YAML：无 gello 时采集可按原规则处理，推理 Tab 可打开且无 gello 卡
- [ ] 同一 YAML：有 pi05 时仅推理页显示；采集页无 pi05 卡
- [ ] 填写 IP/端口可连接外部 `serve`；页面显示 `next_state`（及错误/延迟）
- [ ] 连接运行期间 **arm_write 无因 pi05 产生的 command**
- [ ] 推理录制 episode 含传感器数据 + pi05 逐步输出（无 gello 时允许无 gello 文件）

---

## 11. 参考路径

| 项 | 路径 |
|----|------|
| serve 入口 | `hww/pi05_jax_sft/scripts/serve.sh` → `python -m pi05_jax_sft.serve` |
| 线协议 | `hww/pi05_jax_sft/src/pi05_jax_sft/serve.py` 文件头注释 |
| 采集 UI | `sensors-dcs/src/sensors_dcs/viz.py` `#tab-collect` |
| FK pose | `sensors-dcs/src/sensors_dcs/arm_pose.py` |
| Agent 工厂 | `sensors-dcs/src/sensors_dcs/agents/__init__.py`、`config.py` |
| 摇操对照（后续控臂勿混进首版） | `docs/gello-arm-teleop.md` |
