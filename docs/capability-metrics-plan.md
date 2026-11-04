# 数采能力量化指标 — 详细说明与开发计划（易 / 中）

日期：2026-11-02  
状态：E1–E4 + **M3** + **薄 M2** 已落地；**厚 M2** 走 sensors.traj_safety（文档 + v0 SDK），不再长期依赖 embody JS；M1 仍待  
范围：sensors-dcs 采集/后处理 + sensors 共享轨迹安全 SDK；embody 后续迁到同一核心库  
相关：[pi05-infer-flywheel.md](pi05-infer-flywheel.md) · [filter-timeline.md](filter-timeline.md) · [sensor-coverage.md](sensor-coverage.md)（M3 ↔ E4 术语） · [station-multi-machine.md](station-multi-machine.md) · [session-gather.md](session-gather.md)（从站 gather） · [known-gaps.md](known-gaps.md) · [standard-export-step4.md](standard-export-step4.md)（Step 4 主流格式） · sensors [traj-safety-sdk.md](../../sensors/docs/traj-safety-sdk.md)

## 0. 背景与定案

完整数采系统除「能录、能导出」外，还应能量化：**任务完成率、抓取/操作成功率、人工干预率、恢复成功率、碰撞/near-miss 率、单次任务耗时、轨迹质量、覆盖率、回充成功率、物体/环境泛化**。

对照本仓库现状：

| 档 | 指标 | 现状一句话 |
|----|------|------------|
| 已有 | 单次任务耗时 | `manifest.duration_s` / `t_start` / `t_end` |
| **易** | 任务完成率、干预率、汇总看板、物体/场景标签 | 差停录标注与事件落盘 |
| **中** | 抓取启发、轨迹/near-miss（离线）、同步覆盖报表 | 有半成品信号，需接链路 |
| 难 / 非本栈 | 恢复成功率、在线 near-miss、回充、自动泛化评估 | 本文不排期 |

**硬约束（与现网一致）：**

1. 不改 barrier / `RecordController` 热路径语义；指标写在 **停录定稿** 或 **旁路事件 sidecar**。  
2. `manifest.valid`（保留/作废）**继续只表示数据可用性**，不得与「任务成功」混用。  
3. Infer 的 `term_flag` / `reject_flag` 是策略终止信号，**不得直接当成人任务成功**。  
4. `align_quality` 是时间轴同步质量，**不是**操作技能轨迹质量；二者分字段存放。  
5. 回充 / AMR / 真·恢复状态机：明确 **非目标**（见 §6）。

---

## 1. 现状可复用信号

| 信号 | 落点 | 用途 |
|------|------|------|
| `manifest.valid` + discard note | `record.py` `_write_manifest` | 数据保留率；**非**任务成功 |
| `duration_s`, `t_start`, `t_end` | `manifest.json` | 单次任务/episode 耗时 |
| `written` / `dropped` | manifest / export meta | 丢帧率、数据质量 |
| `record_mode` ∈ {`collect`,`infer`} | manifest | 专家轨 vs 策略 rollout 分桶 |
| `session_id` / `station_id` | 联机会话 meta → manifest | 多机聚合键 |
| `align_quality`（score/grade/keep_rate…） | `export/align_plot.py` → `filter_meta.json` | 同步门控 |
| Gello 遥操开关、Infer `delta_pose_offset`、Estop | runtime + Collect/Infer UI | 干预事件源 |
| embody `meta.task_outcome` / `meta.objects` | `embody_model_eval/.../task_events.js` | 标注图式对齐目标 |
| `sensors` 的 `ft` / `tactile` kinds | 驱动已有，DCS YAML 未接 | 抓取启发输入 |
| embody 碰撞 / TCP 门控 | `collision_geom.js` / `batch_score.py` 等 | 离线轨迹与 near-miss |

---

## 2. 「容易做」项目详解

### E1 — 停录任务结果标注 → 任务完成率

#### 2.1 解释

今天 Stop = `valid=true`、Discard = `valid=false`，只回答「这条数据留不留」，不回答「任务做成没做成」。完成率需要独立字段，例如：

\[
\text{任务完成率} = \frac{\#\{\texttt{outcome}=\texttt{success}\}}{\#\{\text{有 outcome 且}\notin\{\texttt{void},\texttt{unknown}\}\}}
\]

可选子标签（grasp / place / other）支撑「抓取/操作成功率」的**人工版**（自动版见 M1）。

#### 2.2 数据契约（建议）

扩展定稿 `manifest.json`（或并列 `outcome.json`，推荐 **写入 manifest** 以免双源）：

```json
{
  "format": "dcs_episode_v1",
  "valid": true,
  "duration_s": 42.1,
  "task_outcome": {
    "outcome": "success",
    "labels": ["grasp", "place"],
    "reason_code": "",
    "note": "",
    "source": "operator",
    "t_wall": 1730550000.0
  },
  "tags": {
    "object_ids": ["cup_red"],
    "env_id": "lab_table_a",
    "task_id": "pick_place_v1"
  }
}
```

| `outcome` | 含义 |
|-----------|------|
| `success` | 任务达成 |
| `fail` | 明确失败 |
| `partial` | 部分达成（可选） |
| `void` | 不计入完成率（误触开录、调试等）；可与 `valid=false` 同时出现 |
| `unknown` | 未判（兼容 embody；聚合时视同未标注） |

与 embody 对齐：沿用 `meta.task_outcome.outcome` / `labels` / `reason_code`（见 `embody_model_eval/.../task_events.js`），便于同一套评测脚本。

#### 2.3 产品行为

| 场景 | 行为 |
|------|------|
| Collect / Infer 点 **Stop** | **立刻** barrier/本地 stop 停采样并开始落盘；随后弹结果面板，确认后 `PATCH` 写入 manifest（不挡停录） |
| 点 **Discard** | 立刻 stop；保留落盘时默认 `void`，弹窗可改；Infer 丢文件不写 outcome |
| 多机联机 | 主站填表后 `POST /api/record/outcome` 写本集，并 `task_outcome` peer 消息同步各从站同 `episode_index` 的 manifest |
| 快速采集自动后处理 | 若未标注 outcome，后处理仍可跑；KPI 聚合时该条进「未标注」桶，不进完成率分母（或配置强制必填） |
| 配置开关 | `runtime.require_task_outcome: false`（默认）→ true 时取消填表会提示，但**不会**推迟停录 |

#### 2.4 开发任务

| ID | 任务 | 仓库 / 文件 | 估时 |
|----|------|-------------|------|
| E1.1 | Pydantic / 文档定稿 `task_outcome` + `tags` schema | `docs/` + 可选 `schemas/` | 0.5d |
| E1.2 | `_write_manifest` / stop|discard API 接受 outcome | `record.py`, `runtime.py`, `viz.py` API | 1d |
| E1.3 | Collect / Infer 停录模态（i18n） | `viz.py`, `ui_i18n.py` | 1d |
| E1.4 | CLI：`sensors-dcs outcomes summary -c save_dir` 打印完成率 | 新小模块或 `postprocess_service` | 0.5d |
| E1.5 | 单测：stop 写入 / discard→void / 缺字段聚合 | `tests/` | 0.5d |

**验收：** 连续 10 条 Stop 均有 `task_outcome`；汇总脚本给出 success 率；旧 episode 无字段时聚合不报错。

---

### E2 — 干预事件落盘 → 人工干预率

#### 2.5 解释

干预控件已在 UI/runtime（Gello 遥操、Infer「Gello 强制干预」`delta_pose_offset`、Estop/Disarm），但 **没有按 episode 计量**。人工干预率常用：

\[
\text{干预率} = \frac{\#\{\text{episode 曾干预}\}}{\#\{\text{episode}\}}
\quad\text{或}\quad
\frac{\sum t_{\text{intervene}}}{\sum t_{\text{episode}}}
\]

需要事件流，而不是只靠最终布尔。

#### 2.6 数据契约

建议 sidecar（避免频繁重写整份 manifest）：

```text
episode_XXXXX/
  events/
    interventions.jsonl
```

每行示例：

```json
{
  "schema": "dcs_intervention_v1",
  "t_wall": 1730550012.3,
  "t_rel": 12.3,
  "type": "teleop_on",
  "source": "gello",
  "detail": {}
}
```

| `type` | 触发 |
|--------|------|
| `teleop_on` / `teleop_off` | Collect Gello→臂遥操开关 |
| `delta_offset_on` / `delta_offset_off` | Infer 强制干预 |
| `estop` | 急停 |
| `disarm` | 下使能（可选） |
| `arm_stop` | 停止运动（可选） |

定稿时可选把摘要写入 manifest：

```json
"intervention_summary": {
  "any": true,
  "counts": {"teleop_on": 2, "estop": 1},
  "duration_s": {"teleop": 8.5, "delta_offset": 0.0}
}
```

#### 2.7 开发任务

| ID | 任务 | 落点 | 估时 |
|----|------|------|------|
| E2.1 | `InterventionLogger`：按 episode 目录 append jsonl | 新 `sensors_dcs/intervention_log.py` | 0.5d |
| E2.2 | 在遥操 / delta_offset / estop 路径打点（失败不影响控制） | `runtime.py`, arm/gello 命令路径 | 1d |
| E2.3 | 停录时汇总 `intervention_summary` → manifest | `record.py` | 0.5d |
| E2.4 | 汇总脚本：干预率（次数 / 时长占比） | 与 E1.4 同 CLI | 0.5d |
| E2.5 | 单测：模拟 on/off 配对算 duration | `tests/` | 0.5d |

**验收：** 一次遥操开→关能在 jsonl 成对出现且 summary.duration 合理；急停必有一条 `estop`；打点异常不得阻断臂命令。

---

### E3 — 数据集 KPI 汇总看板

#### 2.8 解释

把已有字段 + E1/E2 新字段做成 **只读报表**，避免人肉翻 episode。输入是 `save_dir` 或联机 `sessions/` 根；输出 JSON + 后处理页一块只读面板即可（不必上独立 BI）。

#### 2.9 指标定义（v1）

| KPI | 定义 | 数据源 |
|-----|------|--------|
| 保留率 | `valid=true` / 全部定稿 episode | manifest |
| 任务完成率 | `outcome=success` / 有 outcome 且非 void/unknown | E1 |
| 未标注率 | 无 `task_outcome` / 定稿 episode | E1 |
| 平均 / P50 / P95 耗时 | `duration_s` | 已有 |
| 干预率（集级） | `intervention_summary.any` | E2 |
| 平均对齐分 | `filter_meta.align_quality.score`（有则计入） | 后处理 |
| Infer vs Collect 分桶 | `record_mode` | 已有 |
| 联机会话数 | `sessions/*/session.json` | 已有 |

实现建议：`sensors_dcs/metrics/aggregate.py` + `GET /api/metrics/summary?root=...`；Postprocess 页或 Home 只读卡片。

#### 2.10 开发任务

| ID | 任务 | 估时 |
|----|------|------|
| E3.1 | 扫描 `episode_*` / `sessions` 的聚合纯函数 | 1d |
| E3.2 | API + 简单 UI 表 | 1d |
| E3.3 | 导出 `metrics_summary.json` 到 root | 0.25d |
| E3.4 | 单测：夹具目录覆盖空/缺字段/混合 mode | 0.5d |

**验收：** 对现有 `_dcs_data` 根跑通；缺 E1/E2 字段时降级显示「—」而非报错。

---

### E4 — 物体 / 场景 / 任务标签 → 覆盖与泛化底座

#### 2.11 解释

「覆盖率」「泛化」在无标签时无法定义。E4 只做 **受控词表 + 录入**，不做自动识别。

建议：

- 录前或停录填写：`task_id`、`env_id`、`object_ids[]`（见 E1 schema `tags`）  
- 词表文件（可选）：`configs/tag_vocab.yaml` 或用户目录，UI 下拉 + 允许临时新建  
- 聚合：按 `object_id` / `env_id` 的 episode 计数、完成率透视 → **覆盖直方图** 与 **分层完成率**（泛化的最小统计形态）

#### 2.12 开发任务

| ID | 任务 | 估时 | 状态 |
|----|------|------|------|
| E4.1 | `tags` 写入 manifest（与 E1 同模态） | 0.5d（可与 E1.3 合并） | ✅（E1 停录弹窗已写） |
| E4.2 | 可选词表 YAML + UI 下拉 | 0.5–1d | ✅ `configs/tag_vocab.yaml` + `/api/tag-vocab` + datalist |
| E4.3 | E3 汇总增加按 tag 透视 | 0.5d | ✅ `by_object_id` / `by_env_id` / `by_task_id` + UI |

**验收：** 同一 `object_id` 多条 episode 能在 summary 透视表中合并计数与完成率。

---

## 3. 「中等」项目详解

### M1 — 力觉进 DCS + 离线抓取成功启发

#### 3.1 解释

人工 `labels: ["grasp"]`（E1）给出监督标签；M1 用 **夹爪行程 +（可选）FT** 做弱自动启发，降低每条都手点的成本，但 **不替代** 人工金标（除非校准后明确切换）。

启发示例（可配置阈值）：

- 闭合过程中夹爪位置进入夹持区间并维持 ≥ \(T\) 秒  
- 若有 FT：法向力超过 \(F_{\min}\) 且未触发安全上限  
- 与 E1 `outcome` 不一致时记 `grasp_heuristic` 仅供对照，不覆盖 `task_outcome`

#### 3.2 前置

| 项 | 说明 |
|----|------|
| DCS agent | 增加 `type` 对应 FT（或复用 sensors kind），写入 `states/<ft>.jsonl` |
| 配置 | 至少一份 example YAML；默认配置可不启用 |
| 导出 | timeline 已泛化 jsonl 则可自动带上；hik 映射另议 |

#### 3.3 开发任务

| ID | 任务 | 估时 |
|----|------|------|
| M1.1 | DCS 接入 FT agent（只读 ring → recorder） | 1–2d |
| M1.2 | 离线脚本：`grasp_heuristic.py` 读 gripper±FT → 报告 | 1d |
| M1.3 | 与人工 labels 混淆矩阵小报表 | 0.5d |
| M1.4 | 文档：阈值标定流程、假阳性说明 | 0.5d |

**验收：** 有 FT 的夹具集上启发与人工 grasp 标签一致率有记录；无 FT 时仅用夹爪规则仍可跑并标注 `ft=false`。

---

### M2 — 导出后轨迹质量 / near-miss（离线 embody 门控）

#### 3.4 解释

采集热路径不做在线碰撞检测。厚 M2 在 **已有 episode** 上跑 **`sensors.traj_safety`**（从 embody `collision_geom` / `advanced_cd` 迁出的可移植几何分析），**不**把 DCS 长期绑在 embody JS 上。

产出 sidecar，例如：

```text
episode_XXXXX/export/quality/
  traj_quality.json      # thin and/or thick grade, gates[]
  near_miss.json         # events[{t, kind, clearance_m}]  （厚）
```

「轨迹质量」KPI：动力学（薄）与几何（厚）可合并——门控全过 = good；仅超速/near-miss = warn；几何碰撞 = bad。与 `align_quality` **分目录/分字段**。

near-miss：无真机接触传感时，用 **最小间隙 ∈ (hit, hit+margin]** 定义为 near-miss；真碰记 collision。阈值进 `SafetyConfig`。

架构与 API：**[`sensors/docs/traj-safety-sdk.md`](../../sensors/docs/traj-safety-sdk.md)**。

#### 3.4b 薄 M2（已落地，2026-11）

不依赖 URDF。本地读 `states/arm*.jsonl`（优先 arm_read），写：

```text
episode_XXXXX/export/quality/traj_quality.json   # kind=thin, grade good|warn|missing
```

| 入口 | 说明 |
|------|------|
| CLI | `sensors-dcs traj-quality -e <ep>` 或 `-c <root>` |
| 后处理可选步 | `traj-quality` ∈ `OPTIONAL_STEPS` / `INDEPENDENT_STEPS`；**不在** `DEFAULT_STEPS` / 快速采集 |
| UI | 「附带 traj-quality」默认关；「仅运行 traj-quality」；KPI 折叠「轨迹质量薄 M2」 |
| E3 | `metrics_summary.json` → `quality`（`dcs_traj_quality_summary_v1`） |

#### 3.5 开发任务

| ID | 任务 | 估时 | 状态 |
|----|------|------|------|
| M2.0 | **薄 M2**：arm.jsonl 动力学 → traj_quality + CLI + 可选后处理 + E3/UI | 0.5–1d | **done** |
| M2.1a | **核心 SDK 文档 + v0**：`sensors.traj_safety`（sphere/hull + near_miss schema） | 0.5–1d | **done** |
| M2.1b | 连杆 FK / capsule profile + DCS 适配写 thick sidecar + UI | 1–2d | **done** |
| M2.1c | mesh/STL 采样进 sensors；embody 消费同一 SDK | 1–2d | **SDK done** / embody pending |
| M2.2 | 批跑 CLI + 写 `export/quality/*`（含 near_miss） | 1d | thin done / thick pending |
| M2.3 | E3 汇总接入 quality 分布（thin+thick） | 0.5d | **done** |
| M2.4 | 与后处理「一键三步」可选（默认关；薄+厚） | 0.5–1d | **done** |

**薄 M2 验收：** 干净轨迹 → good；人为超速/跳变 → warn；无 arm jsonl → missing；默认三步/快速采集耗时不变（默认关）。  
**厚 M2 验收：** 已知桌面/自碰夹具 → bad；near-miss 带 → warn；干净 → good；算法在 `sensors`，DCS/embody 只做适配。

---

### M3 — 传感器 / 同步覆盖报表

#### 3.6 解释

此处「覆盖率」= **模态与时间轴覆盖**，不是场景覆盖（场景见 E4）。

复用 filter / export 已有量：

| 量 | 含义 |
|----|------|
| `keep_rate` | 对齐后保留帧比例 |
| per-agent 缺口 | require 列表中谁缺失 |
| `written`/`dropped` | 录制丢帧 |
| 相机 HW 时间戳缺口 | 已有质量文档可钩 |

报表输出：每个 episode 一行 + 数据集直方图；阈值告警（如 keep_rate &lt; 0.9）。

#### 3.7 开发任务

| ID | 任务 | 估时 | 状态 |
|----|------|------|------|
| M3.1 | 从 `filter_meta.json` / `export_meta` / manifest 抽覆盖行 | 0.5–1d | ✅ `metrics/coverage.py` |
| M3.2 | 并入 E3 summary（`coverage` 块） | 0.5d | ✅ aggregate + KPI UI 折叠表 |
| M3.3 | 文档：与 E4「场景覆盖」术语对照，避免混名 | 0.25d | ✅ [`sensor-coverage.md`](sensor-coverage.md) |

**验收：** 无 filter 的 episode 显示「未后处理」；有 filter 的给出 keep_rate 与缺失 agent 列表。

---

## 4. 推荐实施顺序与里程碑

```text
Week 1     E1 停录 outcome + E4 tags（同模态）
Week 1–2   E2 干预 jsonl + summary
Week 2     E3 汇总 API/UI（先 consum 旧字段，再接 E1/E2）
Week 3     M3 同步覆盖并入 E3
Week 3–4   M2 离线 quality/near-miss（默认关）
Week 4–5   M1 FT agent + grasp 启发（有硬件再启）
```

| 里程碑 | 交付物 |
|--------|--------|
| **M0** | 本文定案；schema 冻结 v1 |
| **M1-easy** | 完成率 + 干预率可在任意 `save_dir` 算出 |
| **M2-dash** | UI/API 汇总看板 |
| **M3-offline** | quality sidecar + 覆盖报表 |
| **M4-ft** | FT 可选接入与启发对照表 |

依赖：E3 可先做「无 E1」降级版；E1/E4 强烈建议同 PR，避免两次改停录 UX。

---

## 5. 非目标（本文不排期）

| 项 | 原因 |
|----|------|
| 恢复成功率 | 需恢复状态机与尝试边界定义；现仅有 auto-home UX |
| 在线碰撞 / near-miss 急停联锁 | 安全与实时性另案；与采集热路径解耦 |
| 回充成功率 | 无 AMR/充电桩产品面 |
| 自动物体识别泛化评测 | 依赖检测模型与分层实验设计；E4 只给标签底座 |
| 用 `term_flag` 冒充任务成功 | 语义错误，禁止 |

---

## 6. 风险与对策

| 风险 | 对策 |
|------|------|
| 操作员嫌停录多点一次 | 默认不强制；快捷键 1/2/3；快速采集可「继承上次 outcome」 |
| `valid` 与 `success` 被下游混用 | 文档 + 聚合脚本分列；导出入训仍只看 `valid`（除非显式 `--require-success`） |
| 干预打点漏关导致 duration 爆 | 停录强制 flush `*_off`；summary 用 min(end, episode_t_end) clamp |
| FT 噪声导致启发不可信 | 启发默认只出报告，不写回 `task_outcome` |
| embody 批处理重、拖慢采集机 | M2 默认关；建议在后处理机跑 |

---

## 7. 验收总表（易 + 中）

| 能力 | 通过标准 |
|------|----------|
| 任务完成率 | 标注集上可复现计算；未标注不进分母（或单独一列） |
| 人工干预率 | 次数率 + 时长占比两种均可导出 |
| 耗时 | 继续用 `duration_s`；summary 含分位数 |
| 标签覆盖 | 按 object/env 透视非空 |
| 同步覆盖 | keep_rate / 缺失 agent 报表 |
| 轨迹 / near-miss | 可选 sidecar；与 align 分字段 |
| 抓取启发 | 有对照表；不覆盖人工金标 |

---

## 8. 实现索引（落地时填 PR）

| 项 | 预期落点 |
|----|----------|
| Schema / 文档 | 本文 + `manifest` 字段说明补丁 |
| 停录 UX | `viz.py` Collect/Infer |
| 写入 | `record.py` `_write_manifest` |
| 干预 | `intervention_log.py` + runtime 打点 |
| 汇总 | `sensors_dcs/metrics/` + `/api/metrics/summary`（含 E4 tag 透视 + M3 `coverage`） |
| 标签词表 | `tag_vocab.py` + `configs/tag_vocab.yaml` + `/api/tag-vocab` |
| 传感器覆盖 | `metrics/coverage.py` · [`sensor-coverage.md`](sensor-coverage.md) |
| 抓取启发 | `sensors_dcs/export/grasp_heuristic.py`（名可调） |
| 离线质量 | `export/quality/` + embody 适配器 |
| 配置 | `runtime.require_task_outcome`；FT example YAML |

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-11-02 | 初稿：易 E1–E4、中 M1–M3 与排期 |
| 2026-11-02 | E1 实现：`task_outcome`/`tags` 写入 manifest、停录弹窗、`sensors-dcs outcomes summary` |
| 2026-11-03 | E4：词表 YAML/API/停录 datalist；metrics 按 object/env/task 透视与 KPI UI |
| 2026-11-03 | M3：`coverage` 块（未后处理 / 滤波保留率 / 缺失 agent / 丢帧 / hw）；术语对照文档 |
