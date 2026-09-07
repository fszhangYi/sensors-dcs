# Arm 绝对下发斜坡增强 — 设计计划

日期：2026-09-07  
状态：**已实现（首版）** — UI 暴露 `t_min` / `t_max` / `v_norm`；`T=clamp(d/v_norm,t_min,t_max)` + cosine S 型；Home 仍固定时长。  
范围：**仅**「绝对关节斜坡」规划与执行——即点击「下发」之后、或任意调用同一套下发入口、或受 **0.1–30s** 时长约束的路径。

---

## 0. 边界（必读）

### 0.1 在范围内

| 入口 | 说明 |
|------|------|
| Infer「下发」 | `infArmSend` → `sendInfArmJointsOnce` → `POST` → `start_arm_abs_ramp` |
| Infer 单步/LOOP 自动下发 | VLA+IK 填 joints 后调用同一 `sendInfArmJointsOnce` / 同等 API，再 `waitInfArmArrive` |
| Robot Write 卡片「下发」 | `arm-abs-send` + 同款时长控件 → 同一 `start_arm_abs_ramp` |
| Home（可选同源） | `go_arm_home` → **同一** `start_arm_abs_ramp`；Settings 回 Home 时长同为 1–300×100ms。**是否改用「按 d 定 T」见 §6**；至少共享 S 型时间律实现 |

核心代码焦点：

- `Orchestrator.start_arm_abs_ramp` / `_arm_abs_ramp_loop` / `_interp_path`
- UI：Infer / Collect 的 `t_min` · `t_max` · `v_norm` 三框；进度 `arm_abs_ramp` WS 状态（含 `params`）
- 门控：`max_delta`、armed、与 sync/teleop 互斥（**保持现有**）

### 0.2 明确不在范围内

| 排除项 | 理由 |
|--------|------|
| VLA 推理、组包、serve 协议、IK(`next_state`→joints) | 发生在「下发」**之前** |
| 笛卡尔直线 / pose 插值 / SLERP 主路径 | 本增强坚持 **joints 空间**，避开欧拉万向节 |
| QP / MPC 轨迹优化 | 过重；本问题用显式 \(T(d)\) + S 型即可 |
| gello→arm **同步/摇操** 斜坡 | 另有 `ramp_duration_s` 等配置；本次不改 |
| 点动 ± / Arm·Disarm·Estop 语义 | 非「到达时间」斜坡 |
| 夹爪下发 | 独立通道 |

```text
[VLA / 手工填 joints / Home 目标]  →  (本设计从这里开始)
        joints_rad + 时长相关 UI/参数
                    ↓
           start_arm_abs_ramp
           规划 T、N、α(t)、q_k
                    ↓
           arm_write.command 按 hz 下发
                    ↓
           completed / cancel / error
```

---

## 1. 现状摘要（基线）

| 项 | 当前行为 |
|----|----------|
| 路径空间 | **关节空间线性**：\(q(\alpha)=q_a+\alpha(q_g-q_a)\)，\(\alpha=k/N\) |
| 时长 | UI `duration_s = n×0.1`，名义 0.1–30s；后端 `start_arm_abs_ramp` 将 `dur` **夹到 1.0–30.0s**（与 UI 下限不完全一致） |
| 点数 | \(N=\mathrm{round}(T\cdot f)\)，\(f=\) `gello_arm_sync.ramp_hz`（默认 5） |
| 时间律 | 均匀 \(\alpha\) → 近似恒定关节参数速率；**非** T/S 型 |
| 短距离 | 与长距离 **共用同一 T** → 近点「爬行」、空等观感差 |
| 与 VLA | LOOP 在斜坡结束后再推理；短距离仍占满 T → 节拍被人为拉长 |
| 姿态 | 不经欧拉插值；无典型万向节死锁；TCP 一般 **非** 直线 |
| 库内未接线 | `arm_pose.cartesian_linear_joint_path`（含 Slerp）存在但 **未** 接入 abs ramp |

---

## 2. 目标与非目标

### 2.1 目标

1. **按关节位移 \(d\) 决定本段执行时长**（近快远慢，有上下界），不再「每段强制同一 T」。
2. 在关节直线路径上使用 **S 型时间律** \(\alpha(t)\)（jerk 受限或等价平滑），改善启停冲击。
3. UI 用 **时间直觉** 调快慢（见 §4），不暴露难感知的 \(v_{\mathrm{nom}}\)。
4. 考虑 **VLA 推理耗时**：\(T_{\mathrm{move}}\) 有与推理节拍相关的 **下界**，避免臂过早停住空等下一点。
5. 保持 joints 规划，**不做** pose/SLERP 主路径。
6. 继续遵守驱动 **`max_delta`**：按 S 型 **峰值单步** 做 gate（不够则拒发或提示加大 \(t_{\max}\)/降低速度感）。

### 2.2 非目标（本期）

- 笛卡尔直线接近、避障、奇异处理  
- QP  
- 密插后按 `min_delta` 抽稀作为 **主** 规划器（可作为可选去重，见 §5.4）  
- 改变 VLA/IK/录制协议  

---

## 3. 方案选型（对话结论）

| 候选 | 结论 |
|------|------|
| 固定 T + 关节线性（现状） | 替换 |
| 固定 T 密插 → `min_delta` 抽稀 → 不保留原 T → 再 S 型 | **不作为主方案**（非一般做法；可用作辅助去重） |
| **按 \(d\) 定 \(T\)/\(N\)，再 S 型** | **采纳** |
| QP | 不采纳 |
| 对外暴露 \(v_{\mathrm{nom}}\) | 不采纳；用 \(T_{\mathrm{ref}}\) 映射 |

每段 \(T_{\mathrm{move}}\) **默认不统一**；短距离经 \(t_{\min}\) 对齐后会聚到下界附近。

---

## 4. 控制参数（谁调「有多快」）

### 4.1 对外（UI / API）

| 参数 | 含义 | 建议默认 / 范围 |
|------|------|-----------------|
| **\(T_{\mathrm{ref}}\)** | （设计备选）参考到达；**首版 UI 改为直接暴露 \(v_{\mathrm{norm}}\)** | — |
| **\(t_{\min}\)** | 本段最短执行时间（对齐 VLA） | UI + YAML `arm_abs_ramp.t_min_s`，默认 0.1s |
| **\(t_{\max}\)** | 本段最长 | UI + YAML，默认 30s |
| **\(v_{\mathrm{norm}}\)** | 名义最大关节角速度（rad/s），**主快慢旋钮** | UI + YAML `v_norm_rad_s`，默认 0.02 |

映射：

\[
T = \mathrm{clamp}(d / v_{\mathrm{norm}},\; t_{\min},\; t_{\max})
\]

### 4.2 对内 / 已有

| 参数 | 作用 |
|------|------|
| `ramp_hz` | 采样频率；\(N=\max(1,\mathrm{round}(T\cdot f))\) |
| S 型 jerk 或加减速比 | 同 \(T\) 下速度形状；不改变总时长契约 |
| `max_delta` | 硬约束；峰值步长超限 → 拒发并提示所需更长 \(T\) |

### 4.3 API 兼容

- 请求体可继续带 `duration_s`：在新语义下解释为 **\(T_{\mathrm{ref}}\)**（推荐），避免再发明字段名炸 UI。  
- 响应 `duration_s` / 进度条：**改为本段实际规划的 \(T\)**（\(T(d)\) clamp 后），便于 LOOP 的 `waitInfArmArrive` 与进度文案正确。  
- 可选回传：`duration_ref_s`、`delta_max`、`t_min`/`t_max`、`profile: "s_curve"`，便于调试。

---

## 5. 规划与执行流水线

### 5.1 输入

- \(q_a\)：live arm read（无读则拒发，保持现状）  
- \(q_g\)：`joints_rad` 目标（6 轴）  
- \(T_{\mathrm{ref}}\)、\(t_{\min}\)、\(t_{\max}\)、\(d_{\mathrm{ref}}\)、\(f\)、S 型参数  

### 5.2 步骤

```text
1. d = max_i |q_g[i] - q_a[i]|
2. T = clamp(T_ref * d / d_ref, t_min, t_max)
   - 若 d ≈ 0（低于极小阈值 ε）：跳过运动或 N=1 写终点，T≈0/极短；LOOP 仍应满足「可进入下一步」的等待策略
3. N = max(1, round(T * ramp_hz))
4. 生成 S 型 α_k = s_curve(k/N) 或 α(t_k)，α∈[0,1]，α_N=1
5. q_k = q_a + α_k * (q_g - q_a)   // 六轴共用同一 α
6. peak_step = max over k of ||q_k - q_{k-1}||_∞
7. if peak_step > max_delta → reject（提示增大 T_ref/t_max 或所需 T）
8. 按 period=1/hz 依次 writer.command(q_k)；支持 cancel
```

### 5.3 S 型定义（实现约定）

- **同步**：六轴共用一个 \(\alpha(t)\)，同时到达。  
- **推荐**：归一化七段 jerk 型，或简化「五段」加加速–匀速–减加速，使 \(\alpha(0)=0,\alpha(T)=1,\dot\alpha(0)=\dot\alpha(T)=0\)。  
- 首版允许用 **余弦平滑** \(\alpha=(1-\cos(\pi u))/2\)（\(u=k/N\)）作 S 型近似，降低实现成本；文档与配置标明 `profile: cosine | seven_segment`。  
- **不做** 每轴独立 S 型。

### 5.4 可选：过近点去重

- 若连续 \(q_k\) 的 \(\|\Delta q\|_\infty < \delta_{\min}\)，可合并（**必须保留终点**）。  
- 去重后 **不回填** 原 \(T\)；实际时长随点数/重采样变化。  
- **非主路径**：主路径已由 \(T(d)\) 缩短近段；去重仅防数值重复写。

### 5.5 与 VLA 节拍

- LOOP：`waitInfArmArrive` 必须使用 **实际** `duration_s`（规划结果）。  
- \(t_{\min}\)：建议 ≥ 典型单步推理时间（可配置 `infer_move_t_min_s`；后期可改为 EMA 实测推理耗时）。  
- 本期 **不** 做推理与运动流水线重叠（边走边推下一点）；若未来重叠，再改为 \(\max(T_{\mathrm{infer}},T_{\mathrm{move}})\) 类契约。

---

## 6. Home 是否同一套「按 d 定 T」

| 选项 | 说明 |
|------|------|
| **A. Home 仍固定时长** | Settings `home_duration_s` = 本段固定 \(T\)；仅共用 S 型采样。回 Home 可预期、安全习惯好。 |
| **B. Home 也按 d 定 T** | `home_duration_s` 当作 \(T_{\mathrm{ref}}\)。 |

**建议默认 A**：Infer/卡片「下发」与 LOOP 用 §4；Home 固定 \(T\) + S 型。实现时 `start_arm_abs_ramp(..., timing="fixed"|"scale_by_d")`。

---

## 7. UI / i18n 变更要点

- 时长控件文案：由「本段到达 100ms–30s」改为「参考到达（按关节位移缩放）」+ hint 说明近短远远、受最短/最长限制。  
- 进度：`绝对下发 i/n · 实际 T`（用规划 \(T\)）。  
- 后端若仍 `max(1.0, dur)`：与 UI 0.1s 不一致；增强时应 **统一** 合法区间（建议全程 0.1–30s，或 UI 下限改为与后端一致），并在本设计实现清单中勾掉。

---

## 8. 文件与测试（实现时）

| 区域 | 预期 |
|------|------|
| `runtime.py` | `_interp_path` → 支持 S 型 α；`start_arm_abs_ramp` 计算 \(T(d)\)；status 回传实际 T |
| `config.py` / YAML | `d_ref_rad`、`infer_move_t_min_s`、`abs_ramp_profile` 等 |
| `viz.py` / `ui_i18n.py` | 文案与 hint；LOOP wait 用返回的实际时长 |
| `tests/` | α 端点 0→1；近距离 \(T\to t_{\min}\)；远距离 \(T\to t_{\max}\)；峰值步长与 `max_delta`；cosine/S 型单调 |

---

## 9. 实现清单（勾选）

- [ ] 统一 duration 合法区间（UI ↔ 后端）  
- [ ] \(T=\mathrm{clamp}(T_{\mathrm{ref}}\cdot d/d_{\mathrm{ref}}, t_{\min}, t_{\max})\)  
- [ ] 关节直线 + 共用 α 的 S 型（先 cosine 可验收，再可选七段）  
- [ ] `max_delta` 按峰值步长 gate  
- [ ] API：`duration_s` 入参 = \(T_{\mathrm{ref}}\)，出参 = 实际 \(T\)  
- [ ] Infer 下发 / LOOP / Write 卡片走同一路径  
- [ ] Home：默认 `timing=fixed` + S 型（§6A）  
- [ ] i18n / 进度文案  
- [ ] 单测 + dry_run 冒烟  
- [ ] （可选）`min_delta` 去重保留终点  
- [ ] （可选）\(t_{\min}\) 接推理耗时 EMA  

---

## 10. 验收标准

1. 同一 \(T_{\mathrm{ref}}\) 下，小 \(d\) 的实际斜坡明显短于大 \(d\)（除非都撞 \(t_{\min}/t_{\max}\)）。  
2. 启停比均匀 α 更平滑（主观 + 可选看指令 \(\Delta q\) 序列两端更小）。  
3. LOOP 等待时间与回报 `duration_s` 一致，无「臂早停仍空等满原 UI 秒数」。  
4. 超 `max_delta` 时拒发且错误可读。  
5. 取消 / Disarm / 与 sync·teleop 互斥行为与现网一致。  

---

## 11. 参考（仓库内）

- 现斜坡：`src/sensors_dcs/runtime.py` — `start_arm_abs_ramp`、`_interp_path`、`_arm_abs_ramp_loop`  
- 未接线笛卡尔：`src/sensors_dcs/arm_pose.py` — `cartesian_linear_joint_path`  
- 同步斜坡（对照，非本期）：`docs/gello-arm-sync.md`  
- Write 安全：`docs/arm-write.md`  
