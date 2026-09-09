# 用相机硬件时间戳做多传感器对齐（设计说明）

文档名：`docs/hw-timeline-align.md`  
日期：2026-09-09（W2 D8）  
状态：**接口草案冻结** · 实现从 D9 起  
关联：[e1-e4-hw-ts-and-discard.md](e1-e4-hw-ts-and-discard.md) · [export-timeline.md](export-timeline.md) · W1 基线 `docs/portfolio/baseline/` · Gap **G01**（in_progress）

> 一句话：今天对齐靠主机墙钟 `t_wall`；本周让导出**可选**改用 RealSense 落盘的 `color_timestamp`（硬件时间戳）建主时间网格，并和 W1 的 QC 数字做 before/after 对照。

---

## 0. 读者需要先知道的两件事

| 概念 | 是什么 | 在本仓哪里 |
|------|--------|------------|
| **wall / `t_wall`** | 采集进程用主机时钟打的墙钟时间，**单位：秒** | 所有 `index.jsonl` / `states/*.jsonl` |
| **hw_ts / `color_timestamp`** | RealSense 驱动给出的彩色帧硬件时间戳（E1 已写入 index） | `cameras/*/index.jsonl` |

W1 已证明：基线三档 HW **覆盖率 100%**，但 `export-timeline` 与 `qc_episode_sync.py` 仍只用 wall。

---

## 1. 目标与非目标

### 1.1 目标（W2）

1. `export-timeline` 增加时钟选择：`align_clock=wall|hw_ts`（默认 `wall`，旧行为不变）。
2. `hw_ts` 模式下：用**主相机**（默认 `cam-middle`）的硬件时间戳建主网格，再挂其它流。
3. 同一批 W1 基线 ep 产出 wall / hw 两套指标与 `COMPARE_wall_vs_hw.md`。
4. `qc_episode_sync.py` 可按同一 `align_clock` 出**可比对** JSON（**不改名** W1 已冻字段）。

### 1.2 非目标（本周明确不做）

- 不改录制路径、不改 `hik_dataset` 字段语义、不大改 `filter-timeline` 产品语义。
- 不要求臂/Gello 自带硬件钟（状态侧目前只有 `t_wall`）。
- 不上 PTP / 交换机授时。
- 不在本周做导出 QC 门禁阻断（那是 W3）。

---

## 2. 冻结接口名（后面少重构）

### 2.1 CLI（`sensors-dcs export-timeline`）

| 参数 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `--align-clock` | `wall` \| `hw_ts` | `wall` | 主时间轴用哪套时钟 |
| `--primary-camera` | str | `cam-middle` | HW 网格来源相机；不存在则报错或 fallback（见 §6） |
| `--align` | `asof` \| `nearest` \| `grid` \| `union` | 不变 | **对齐算法**（与 clock 正交） |
| `--master` | str | 现逻辑 | wall 模式下主 agent；**hw_ts 模式下主网格固定来自 primary-camera**，`--master` 仅影响「谁当 asof 左表」时的语义见 §4.3 |

> 注意：现有 `--align` 是 asof/nearest/…；新增的是 **`--align-clock`**，不要合成一个含糊的 `--align hw`。

### 2.2 Python API（建议签名，实现时可微调默认值，**名字冻结**）

```python
def export_episode_timeline(
    episode: Path,
    output_dir: Path,
    *,
    align: AlignMode | None = None,
    align_clock: Literal["wall", "hw_ts"] = "wall",
    primary_camera: str = "cam-middle",
    master: str | None = None,
    hz: float | None = None,
    master_hz: float | None = None,
    fmt: str = "both",
    allow_invalid: bool = False,
) -> dict[str, Any]:
    ...
```

导出 `export_meta.json` **必增字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `align_clock` | str | `wall` / `hw_ts` |
| `primary_camera` | str \| null | 实际使用的主相机 |
| `align_fallback` | bool | 是否因 HW 不可用而回退 wall |
| `align_fallback_reason` | str \| null | 回退原因码（见 §6） |
| `hw_unit` | str | 规范化后网格使用的单位，固定写 `"seconds"` |
| `hw_raw_unit_detected` | str | 检测结果：`ms` / `us` / `s` / `unknown` |

### 2.3 QC 脚本（`scripts/qc_episode_sync.py`）

| 参数 | 说明 |
|------|------|
| `--align-clock wall\|hw_ts` | 默认 `wall`；与导出一致 |
| `--camera-primary` | 已有；与 `--primary-camera` **同义**（QC 保持旧旗标名，文档写清别名） |

**冻结输出字段（W1 已有，禁止改名）**：

`cam_dt_p50` · `cam_dt_p95` · `cam_gap_count` · `state_cam_abs_dt_p50` · `state_cam_abs_dt_p95` · `missing_agents` · `hw_ts_present` · `hw_ts_coverage` · `align_clock`

COMPARE 表若写简称 `state_cam_dt_p95`，脚注必须指向 `state_cam_abs_dt_p95`。

`hw_ts` 模式下各字段语义：

| 字段 | wall | hw_ts（MVP） |
|------|------|----------------|
| `cam_dt_*` / `cam_gap_count` | 主相机 `t_wall` 相邻差 | 主相机 **规范化后的 `t_hw`** 相邻差 |
| `state_cam_abs_dt_*` | 状态 `t_wall` ↔ 最近相机 `t_wall` | **仍用 wall 近邻**（状态无 HW）；另可选写辅助字段 `cam_hw_dt_*`（新增允许，不改旧名） |
| `align_clock` | `"wall"` | `"hw_ts"` 或回退时仍写实际生效值 + `align_fallback` |

---

## 3. 单位约定（必须先统一）

基线真实样例（`ep_good` / `cam-middle` 一行）：

```json
{
  "seq": 34725,
  "t_wall": 1788752504.3331985,
  "color_timestamp": 1788752504337.1316,
  "color_timestamp_domain": "timestamp_domain.system_time",
  "file": "00034725.jpg"
}
```

观察：`color_timestamp / 1000 ≈ t_wall`（差约数毫秒级）→ 当前落盘 HW 值是**毫秒**量级，wall 是**秒**。

**冻结规则：**

1. 代码内时间比较、网格、Δt **一律用秒（float）**。
2. 集中函数（建议放 `timeline.py`）：

```python
def normalize_hw_timestamp(raw: float, *, t_wall_hint: float | None = None) -> tuple[float, str]:
    """Return (t_hw_seconds, raw_unit_detected)."""
```

启发式（实现可测）：

| 条件 | 判定 |
|------|------|
| `raw` 与 `t_wall` 同数量级（比值 ∈ [0.5, 2]） | 已是秒 |
| `raw/1000` 与 `t_wall` 同数量级 | 毫秒 → `/1000` |
| `raw/1e6` 与 `t_wall` 同数量级 | 微秒 → `/1e6` |
| 无法判定 | `unknown` → 触发 fallback（§6） |

禁止在业务代码里四处写 `/1000`。

---

## 4. 数据流

### 4.1 总览

```text
cameras/<primary>/index.jsonl
        │
        ├─ t_wall, color_timestamp, color_timestamp_domain, file, seq
        ▼
  normalize_hw_timestamp → t_hw_s
        ▼
  建主网格 FrameAnchor[]   （按 align_clock 选排序键）
        │
        ├─ wall 模式：按 t_wall 排序（现状）
        └─ hw_ts 模式：按 t_hw_s 排序 / 去重
        ▼
  其它相机、states/* 挂接（asof / nearest / …）
        ▼
  timeline_events / timeline_aligned + export_meta.json
```

### 4.2 示例：一行 index → 网格时间

输入（上节样例）在 `align_clock=hw_ts`、`primary_camera=cam-middle` 下：

| 步骤 | 结果 |
|------|------|
| 读入 | `t_wall=1788752504.3331985`，`color_timestamp=1788752504337.1316` |
| 规范化 | `t_hw_s = 1788752504.3371316`，`hw_raw_unit_detected=ms` |
| 生成锚点 | `FrameAnchor(seq=34725, t_wall=…, t_hw=…, t_grid=t_hw_s, file=00034725.jpg)` |
| 网格时刻 | 宽表该行的对齐时刻 = **`t_grid`**（秒） |

事件表（长表）建议额外列（可选但推荐）：

- `t_grid`：本行参与排序/对齐的时钟  
- `t_hw`：规范化后的硬件秒（仅相机行有）  
- `align_clock`：冗余便于单文件阅读  

### 4.3 状态怎么挂（MVP vs 理想）

状态行**没有** `color_timestamp`，只有 `t_wall` / `t_mono`。

#### MVP（本周必须落地，推荐）

1. 主相机每个锚点保留配对：`(t_wall, t_hw_s, seq, file)`。  
2. **网格顺序 / 去重 / cam_dt**：按 `t_hw_s`。  
3. **状态 asof/nearest**：仍在 **wall 域** 完成——用状态 `t_wall` 对锚点的 `t_wall` 做现有 merge_asof / nearest。  
4. 报告同时给出：
   - HW 网格帧间隔（`cam_dt_*` on `t_hw`）
   - 状态↔锚点 wall |Δt|（继续叫 `state_cam_abs_dt_*`）

这样不假装「状态已在 HW 域」，但能回答：「按硬件钟排干净相机帧后，状态贴合有没有变好/变差」。

#### 理想（有臂侧 HW 钟再做，本周只写边界）

- 状态也有可溯源硬件时戳，或  
- 用整段 `(t_wall ↔ t_hw)` 分段线性映射把状态 `t_wall` 映到 `t_hw` 再 asof。  

映射在时钟漂移非线性和 drop 严重时会歪，故不作为 W2 MVP。

#### 与现有 `--align` / `--master` 的关系

| `align_clock` | 主网格来源 | asof 左表时间列 |
|---------------|------------|-----------------|
| `wall` | 今：`--master`（默认 gello 等）的 `t_wall` | `t_wall` |
| `hw_ts` | **强制** `primary_camera` 锚点序列 | 左表用锚点 `t_wall` 做状态 join（MVP）；网格展示/排序键为 `t_hw` |

`grid` / `union` 在 `hw_ts` 下：以主相机 HW 时间范围为界；`hz` 含义变为「对 HW 秒轴均匀采样」——若实现成本高，W2 可仅保证 **asof/nearest + events**，`grid` 暂不支持 hw 并在 meta 写 `align_clock_grid_unsupported`。

---

## 5. 主相机与多相机

1. **主相机**：`--primary-camera`（默认 `cam-middle`）。  
2. **副相机**（left/right）：W2 可继续按 wall asof 挂到主锚点的 `t_wall`；另在 QC/报告中可选输出副相机相对主相机的 wall 偏移（非必须）。  
3. 不要求三路 RealSense 硬件钟已做外参级同步；domain 不一致见 §6。

---

## 6. 失败模式与 fallback

一律：**打 warning → `align_fallback=true` → 按 wall 跑完**（保证管道不炸），除非用户将来加 `--strict-hw`（本周可不实现 strict）。

| 原因码 `align_fallback_reason` | 触发条件 | 行为 |
|--------------------------------|----------|------|
| `missing_primary_camera` | 无该相机目录 / index | fallback wall；master 回现逻辑 |
| `hw_field_absent` | 主相机 index 无任何 `color_timestamp` | fallback |
| `hw_coverage_low` | 覆盖率 &lt; 阈值（建议默认 0.95，可配） | fallback |
| `hw_all_zero_or_null` | 有字段但全空/0 | fallback |
| `hw_unit_unknown` | 规范化失败 | fallback |
| `hw_domain_mixed` | 主相机行内 `color_timestamp_domain` 多种且冲突 | warning；MVP 仍可用时间值，meta 标记；严格模式可 fallback |
| `hw_non_monotonic` | 规范化后 `t_hw` 回绕/大量逆序 | 排序前记录逆序次数；逆序率 &gt; 阈值则 fallback |
| `hw_wall_incoherent` | 中位 \|t_hw_s − t_wall\| 过大（如 &gt; 1s） | fallback（防单位判错） |

Domain 字段样例：`timestamp_domain.system_time`（当前基线）。多 domain 混用时先记录集合到 `export_meta.hw_domains`。

---

## 7. 与 W1 QC / 报告的对齐

| 产物 | wall | hw_ts |
|------|------|-------|
| `sync_wall_*.json` | 已有 | 新增 `sync_hw_*.json`（或同名 + `align_clock` 字段区分目录 `sync_hw_ts_*`） |
| 直方图 | `sync_wall_*.png` | `sync_hw_*.png` |
| 对比 | — | `COMPARE_wall_vs_hw.md` |

建议对比表列（实现 D12）：

| ep | clock | cam_dt_p95 | state_cam_abs_dt_p95 | gaps | fallback | notes |
|----|-------|------------|----------------------|------|----------|-------|

预期解读（写入结论时用）：

- HW 上 `cam_dt` 更稳、gaps↓ → 硬件钟排序去掉了 wall 抖动。  
- `state_cam_abs_dt` 几乎不变 → 符合 MVP（状态仍 wall）；不代表失败。  
- 若单位判错，会出现荒谬 Δt → 应走 fallback，而不是硬出数。

---

## 8. 代码落点（给 D9 开工）

| 位置 | 改动 |
|------|------|
| `src/sensors_dcs/export/timeline.py` | `normalize_hw_timestamp`；加载相机时保留 HW 字段；`build_grid` / `_build_base_times` 按 `align_clock` 选列；`export_meta` 增字段 |
| `src/sensors_dcs/cli.py` | `--align-clock`、`--primary-camera` |
| `scripts/qc_episode_sync.py` | `--align-clock`；hw 下 cam_dt 用 `t_hw` |
| `tests/test_timeline_hw_ts.py` | 单位归一化、网格首尾、无 HW 回退 wall |
| 本文档 | 实现中若 MVP 降级，只改 §4.3/§9，不改 §2 接口名 |

---

## 9. 已知限制（诚实写进交付）

1. **状态不在 HW 域**（MVP）：`state_cam_abs_dt_*` 在 hw 模式下仍是 wall 近邻。  
2. **单主相机 HW 网格**：不解决三路相机之间的硬件同步误差。  
3. **domain / 单位**依赖启发式；异常数据靠 fallback，不靠猜。  
4. **高 drop 场景**：HW 再齐也补不回丢掉的帧；与 `04_drop_rate_reduction.md` 正交。  
5. **`filter` / `hik_dataset`**：本周仍消费默认 wall 导出；hw 宽表作为可选产物，不强制下游改。

---

## 10. 验收（对应 week-02 / G01）

- [ ] 本文含「一行 index → 网格时间」例（§4.2）  
- [ ] §2 接口名冻结：`--align-clock`、`--primary-camera`、`align_fallback`  
- [ ] 默认 `wall` 与 W1 命令兼容  
- [ ] 基线至少 good 能导出 wall / hw_ts 两套目录  
- [ ] QC JSON 旧字段名不变  

---

## 11. 示例命令（实现后贴进 README；现为草案）

```bash
# 旧行为（默认）
sensors-dcs export-timeline \
  -e docs/portfolio/baseline/ep_good \
  -o docs/portfolio/baseline/reports/tl_wall_good \
  --align nearest

# 新：HW 网格（草案）
sensors-dcs export-timeline \
  -e docs/portfolio/baseline/ep_good \
  -o docs/portfolio/baseline/reports/tl_hw_good \
  --align nearest \
  --align-clock hw_ts \
  --primary-camera cam-middle

# QC 对照（草案）
python scripts/qc_episode_sync.py \
  --episode docs/portfolio/baseline/ep_good \
  --camera-primary cam-middle \
  --align-clock hw_ts \
  --out docs/portfolio/baseline/reports/sync_hw_good.json
```
