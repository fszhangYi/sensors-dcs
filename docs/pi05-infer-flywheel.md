# 推理落盘数据怎么用 · 数据飞轮怎么建

日期：2026-09-05  
状态：设计说明（后处理 `export-pi05-*` **尚未实现**）  
相关：[`pi05-infer-tab.md`](pi05-infer-tab.md)（Tab/agent 实现计划）、[`hik-dataset.md`](hik-dataset.md)（**采集**侧专家数据导出，勿与推理混用）

---

## 0. 一句话原则

| 阶段 | 做什么 | 不做什么 |
|------|--------|----------|
| **在线推理录制** | 原样记下传感器 + VLA 实际输入/输出 | 不在线插成 50Hz 训练集 |
| **离线飞轮** | 按 `step` 抽决策 → 对齐图像/状态 → 转训练样本 → 再训 → 再部署 | 不要把 Infer episode 直接丢进现有 `export-hik-dataset`（那是 gello 专家轨语义） |

推理数据的价值不在「数值是否像真机」，而在 **结构可复现**：同一套目录能支撑调试、评测与日后再训练。

---

## 1. 推理 episode 长什么样

Infer Tab 录制时 `manifest.record_mode = "infer"`。典型树：

```text
episode_XXXXX/
  manifest.json                 # record_mode=infer；agents 含 pi05；valid 等
  states/
    pi05.jsonl                  # VLA 决策流（稀疏；见 §2）
    arm.jsonl                   # 臂读
    arm-write.jsonl             # 若 LOOP/Home/绝对发送在跑，则有写指令回放
    gripper-read.jsonl
    gripper-write.jsonl
    # 无 gello.jsonl（infer 会话显式跳过 gello）
  cameras/
    cam-left|cam-middle|cam-right/
      index.jsonl               # 含 HW color/depth timestamp 等
      NNNNNNNN.jpg
```

与采集 episode **同构**（便于复用预览/后处理脚手架），差异主要在：

1. 多了 `states/pi05.jsonl`  
2. 没有（或不该依赖）`gello` 专家流  
3. 策略决策是 **稀疏** 的，不是相机帧率

### 1.1 `pi05.jsonl` 一行是什么

每行外层与其它 agent 相同：`agent_id / sensor_id / kind / seq / t_wall / t_mono / payload`。

`payload` 核心字段：

| 字段 | 含义 |
|------|------|
| `robot_state` | 发给 serve 的 7D：`[x,y,z,rx,ry,rz,gripper]` |
| `next_state` | serve 返回的 7D 绝对笛卡尔目标（同约定） |
| `term_flag` / `reject_flag` | 终止 / 拒识 |
| `step` | agent 内决策计数（**真·一步推理**） |
| `latency_ms` / `ok` / `error` / `server_text` | 延迟与协议状态 |
| `jpeg_lens` | 当次发出三路 JPEG 字节长度（`top/chest/wrist2`） |
| `camera_map` | 角色→agent_id（如 `top→cam-middle`）；**Step 结果帧曾缺此字段，心跳帧有——用时按 `step` 去重并以配置兜底** |
| `prompt` / `host` / `port` | 任务语与服务端 |

**重要：** ring 心跳也会带着上次的 `next_state` 被 recorder 采到，因此 jsonl 行数 ≫ 真实决策数。使用前必须按 **`step`（或 `next_state` 变化）去重**，只保留每个 `step` 首次/末次出现的一行。

---

## 2. 现在可以怎么用（无需新代码）

### 2.1 联调 / 回归（假数据也适用）

只看结构是否闭合：

```bash
# 是否 Infer、是否跳过 gello、是否有 pi05
python -c "
import json; from pathlib import Path
ep=Path('data_new/episode_00006')
m=json.loads((ep/'manifest.json').read_text())
assert m['record_mode']=='infer'
assert not (ep/'states/gello.jsonl').exists()
assert (ep/'states/pi05.jsonl').exists()
print('ok', m['episode_index'], 'written', m['written'])
"
```

检查项清单：

- [ ] `record_mode == infer`，`valid` 符合预期  
- [ ] 有 `pi05.jsonl`；无 `gello.jsonl`  
- [ ] 三路相机 `index.jsonl` 行数 = `.jpg` 数，且 `file` 可打开  
- [ ] 去重后每个决策的 `robot_state` / `next_state` 均为长度 7  
- [ ] （可选）`term_flag` 轨迹是否出现过终止，便于测 LOOP 自动 Home

### 2.2 抽「真实决策」时间线（分析 / 报表）

伪代码：

```python
import json
from pathlib import Path

def unique_pi05_decisions(ep: Path):
    rows = [json.loads(l) for l in (ep/"states/pi05.jsonl").read_text().splitlines() if l.strip()]
    by_step = {}
    for r in rows:
        pl = r["payload"]
        if not pl.get("next_state"):
            continue
        s = pl["step"]
        # 保留该 step 第一次落盘（通常是真正的 Step 结果帧）
        by_step.setdefault(s, r)
    return [by_step[k] for k in sorted(by_step)]
```

用途：延迟分布、`term/reject` 统计、prompt 覆盖、与 `arm-write` 是否同窗执行等。**不要**把未去重的 jsonl 当成「每行一个训练样本」。

### 2.3 图像怎么取

`pi05` 行 **不存整图**（只存 `jpeg_lens`），图像在 `cameras/<agent_id>/`。

对齐方式（推荐）：

1. 读决策行的 `t_wall`（及可选 `camera_map`）  
2. 对 `top/chest/wrist2` 各自对应的 camera agent，在其 `index.jsonl` 里按 `t_wall` **近邻**取一帧 JPG  
3. 若缺 `camera_map`，用 YAML / agent 默认映射（与 Infer 连接时一致）

这与采集侧 `export-timeline`「按时间轴近邻」同一思想，但主时钟应是 **pi05 `step`**，不是 gello/相机固定 Hz。

### 2.4 现在不要怎么用

| 错误用法 | 原因 |
|----------|------|
| 整集丢进现有「数据后处理 → hik_dataset」一键三步当专家数据 | 该管线假定 gello/臂观测差分 → `actions`；Infer 没有 gello，语义是 VLA `next_state` |
| 把 `pi05.jsonl` 每一行当一个训练步 | 心跳重复；须按 `step` 去重 |
| 在线把 `next_state` 插值成 50Hz 再落盘当专家 | 违反「原样多记、离线再转」；会污染真假难辨的标签 |
| 用 `arm-write` 指令轨直接当 pi05 监督 | 写轨含斜坡/Home/限速中间点；监督应对齐 **策略输出** 或 **事后实测达成**，需明确定义（见 §4） |

---

## 3. 两类数据在飞轮里的角色

```text
┌─────────────────┐         ┌──────────────────┐
│ Collect episode │         │  Infer episode   │
│ (专家 / gello)  │         │ (策略 rollout)   │
└────────┬────────┘         └────────┬─────────┘
         │                           │
         ▼                           ▼
  export-hik-dataset          export-pi05-dataset（待建）
  → 监督学习主集               → 策略轨迹 / 失败案 / 纠偏候选
         │                           │
         └──────────┬────────────────┘
                    ▼
              再训练 / 评测 / 部署 serve
                    │
                    └──► 再开 Infer 录制 ──► 飞轮
```

- **Collect**：高质量示范，现有后处理已通。  
- **Infer**：模型在真实（或仿真）闭环里「跑过」的证据——成功段可作额外监督或过滤难例；失败段（`reject`、未达 `term`、撞限）用于评测与主动回采。

飞轮不是「只采 Infer 替代专家」，而是 **专家冷启动 + 策略 rollout 增广/挖掘**。

---

## 4. 建设数据飞轮（建议阶段）

### F0 — 契约冻结（现在就能做）

1. 固定文档化字段：§1.1；`camera_map` 在 Step/心跳帧对齐（小补丁）。  
2. Recorder：Infer 下对 pi05 **按 `step` 去重再写盘**（可选，降低体积）。  
3. 约定评测标签：`valid`、人工「成功/失败」、是否含 LOOP 控臂。

交付物：本文件 + 稳定 schema；无需新导出命令。

### F1 — 只读分析工具（低成本）

做一个薄 CLI / notebook，例如 `inspect-pi05-episode`：

- 去重决策表  
- 延迟 / term / reject 直方图  
- 决策 `t_wall` ↔ 三路近邻图路径列表  
- （可选）`next_state` 与同时刻 arm FK 的位姿误差（看执行是否跟上）

交付物：人能快速判断「这集能不能进池」。

### F2 — `export-pi05-dataset`（飞轮核心，待实现）

**输入：** `record_mode=infer` 的 episode（可多集）。  
**输出（建议，对齐 serve 训练分布，名称可调整）：**

```text
export/pi05_dataset/
  metadata.json       # serve 约定、camera_map、pose 定义、来源 episode
  samples.jsonl       # 一步一条（已按 step 去重）
  rgb_top_i.jpg …
```

每条样本建议至少包含：

| 字段 | 来源 |
|------|------|
| 三路图像 | cameras 近邻对齐 |
| `observation.state` / `robot_state` | 该步发出的 7D |
| `action` / `next_state` | 该步返回的 7D（或相对 SE(3)，与当前 SFT 配方一致） |
| `term` / `reject` | flags |
| `prompt` | 文本 |
| `t_wall` / `source_episode` / `step` | 溯源 |

**Action 语义二选一（开训前必须拍板）：**

1. **策略输出监督**：标签 = 当时 `next_state`（行为克隆策略自己）——实现简单，适合「自洽再训」。  
2. **达成监督**：标签 = 执行后实测 TCP/关节再 FK——更贴物理，但要对齐斜坡结束时刻，工程更重。

首版建议先做 **(1)**，与线上 serve 输入输出一致；真机误差用评测集单独报。

过滤规则建议：

- 丢弃 `ok != true` / 缺图 / 缺 7D  
- `reject_flag != 0` 进「难例池」而非主训集（或降权）  
- `term_flag` 仅作 episode 边界，不单独当正样本除非配方需要  

### F3 — 与现有后处理 UI 衔接

在「数据后处理」增加 Infer 路径或模式开关：

- Collect → 现有三步 → `hik_dataset`  
- Infer → `inspect` → `export-pi05-dataset`  

共用：选 episode 目录、camera-map、作废/`valid` 门控。  
不要强行复用 `filter-timeline --require gello`。

### F4 — 闭环运营

```text
部署 serve → Infer 录制（任务集）→ 人工/自动判成功
    → 成功：进 SFT 增广（F2）
    → 失败：进回采队列（Collect 补专家 或 改 prompt/场景）
    → 再训 → 回归评测（固定 episode 集 + 新 rollout）→ 再部署
```

度量建议：任务成功率、平均步数到 `term`、限位点名率、策略延迟 P50/P95、每轮新增可训样本数。

### F5 — 与控臂闭环的关系（已有 LOOP，单独治理）

产品已支持 Infer LOOP：`step → IK → arm_write → 等待到位`。飞轮上要分清：

- **日志**：`pi05` = 决策；`arm-write` = 执行过程；二者都保留。  
- **训练标签**：默认仍用 `pi05.next_state`，除非明确做「达成监督」。  
- **安全**：软限位 / Home 独立时长等不进入标签，只进运维与过滤。

---

## 5. 推荐工作流（日常）

### 5.1 当天推理实验

1. 外部拉起 `serve`（或 fake serve 做结构验收）  
2. Infer Tab：Connect →（可选 LOOP）→ **开始录制** → 跑任务 → 结束/作废  
3. 确认 `data_new/episode_*` 的 `record_mode` 与 `pi05.jsonl`  
4. 用 F1 工具出一页摘要：步数、term、延迟、缺图  

### 5.2 进训练池前

1. `valid=true` 且任务判定通过  
2. 去重后决策数 ≥ 阈值；图对齐成功率 100%  
3. 写入清单（CSV/JSON）：episode 路径、任务名、成功与否、模型版本、prompt  

### 5.3 再训后

1. 固定 **评测集**（勿与训练集混）上跑 Infer 并录盘  
2. 对比成功率与失败模式，再决定是否合并新 rollout  

---

## 6. 验收标准（飞轮是否「建成」）

| 级别 | 标准 |
|------|------|
| **结构可用**（当前） | Infer episode 目录稳定；可按 §2 人工分析 |
| **可导出**（F2） | 一键从 Infer episode 产出与 serve SFT 同分布的样本目录 |
| **可运营**（F4） | 有成功/失败分流、版本与 prompt 溯源、固定评测集对比 |

---

## 7. 参考路径

| 项 | 路径 |
|----|------|
| Infer / pi05 实现计划 | `docs/pi05-infer-tab.md` |
| 录制门控（infer skip gello；pi05 需 next_state） | `src/sensors_dcs/record.py` |
| pi05 帧字段 | `src/sensors_dcs/agents/pi05_agent.py` |
| 采集侧 hik 导出（对照，非 Infer） | `docs/hik-dataset.md` |
| serve 协议 | `hww/pi05_jax_sft` → `serve.py` |

---

## 8. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-09-05 | 首版：基于现网 Infer 落盘结构与「原样记录 → 离线再转」约定 |
