# 统一时间轴导出（CSV / Parquet）

本文档描述如何将 `episode_*` 录制目录中的多 sensor 数据，离线导出为按 `t_wall` 可查询的统一时间轴。  
**原则**：现有写盘格式不变；导出为只读后处理。

---

## 1. 背景与问题

当前 episode 按 sensor 分文件存储：

```text
episode_00000/
  manifest.json
  states/
    gello.jsonl              # 每行：seq, t_wall, t_mono, payload
    gripper_read.jsonl
  cameras/
    left/
      00000028.jpg           # 文件名 = 该 agent 的 seq（非队列号）
      index.jsonl            # 每行：seq, t_wall, t_mono, file, ...
    right/
      ...
```

- **Gello / gripper** 的时间戳在 `states/*.jsonl` 每行里。
- **图片** 文件名只有 `seq`；**`t_wall` 在 `index.jsonl`**，不在 `.jpg` 里。
- 各 agent 的 `seq` **独立递增**，不能跨 sensor 用 seq 对齐。
- 跨 sensor 对齐只能依赖 **`t_wall`**（墙钟）或同进程内的 **`t_mono`**（单调钟）。

---

## 2. 导出目标

| 形态 | 说明 | 典型用途 |
|------|------|----------|
| **长表（Event Log）** | 一行 = 一条原始采样，全局按 `t_wall` 排序 | 审计、无损归档、自定义 join |
| **宽表（Aligned Timeline）** | 一行 = 一个对齐时刻，列展开各 sensor | 训练、pandas 分析 |

建议默认同时支持两种；宽表需指定对齐策略（见 §4）。

---

## 3. 读取与归一化

新增模块（计划路径）：`src/sensors_dcs/export/timeline.py`

中间结构：

```python
@dataclass
class Sample:
    t_wall: float
    t_mono: float
    agent_id: str
    sensor_id: str
    kind: str
    seq: int
    fields: dict[str, Any]   # 展平后的标量/向量
    image_relpath: str | None
```

### 3.1 读取规则

1. **`states/*.jsonl`** — 逐行解析；`payload` 按 kind 展平：
   - `gello` → `joints_rad[0..N-1]`
   - `gripper_read` → `position_norm`, `position_raw`
2. **`cameras/<agent_id>/index.jsonl`** — 每行一条 Sample：
   - `image_relpath = cameras/{agent_id}/{file}`
   - 不嵌入 JPEG 二进制
3. **`manifest.json`** — 写入导出元数据（`t_start`, `t_end`, `agents`, `site` 等）；录制 start 时已写入的 `cameras.<agent_id>`（内参）一并保留

### 3.2 校验

- 各 jsonl 按 `t_wall` 排序（源文件应近似单调）
- 统计每 agent 样本数、时间范围
- 可选：对照 `manifest.written` / 检查 jpg 是否存在

---

## 4. 宽表对齐策略

各 agent 采样率不同（如 gello 50Hz、相机 15Hz），宽表必须选一种对齐方式。

### 4.1 Master clock + as-of（推荐默认）

选一条**主时间轴**，其他 sensor 做 as-of join：

| 主时钟 | 适用 |
|--------|------|
| `gello`（或最高频 state agent） | 遥操作 / 关节为主 |
| 某路 `camera` | 视觉为主 |
| 固定网格 `t0 + k * dt` | 训练要固定帧率 |

对每个主时刻 `t*`、每个非主 agent `A`：

- **`asof_backward`（默认）**：取 `t_wall <= t*` 的最近一条（因果，不用未来帧）
- **`nearest`**：取 `|t_wall - t*|` 最小的一条（可视化/离线标注）

记录 `match_dt = t* - t_wall`，便于过滤对齐误差过大的行。

### 4.2 全时间戳并集

所有 agent 的 `t_wall` 并集排序；每行只填该时刻有样本的列，其余 NaN。  
不丢原始样本，行稀疏。

### 4.3 固定网格重采样

在 `[manifest.t_start, manifest.t_end]` 上按 `dt = 1/hz` 生成格点，各列 as-of/nearest 到格点。  
适合固定长度 episode。

---

## 5. 输出 Schema

导出目录（默认）：

```text
episode_00000/
  export/
    timeline_events.parquet       # 长表
    timeline_aligned.parquet      # 宽表（若指定 --align）
    export_meta.json              # 参数与统计
```

### 5.1 长表 `timeline_events.parquet`

| 列 | 类型 | 说明 |
|----|------|------|
| `t_wall` | float64 | 墙钟 |
| `t_mono` | float64 | 单调钟 |
| `agent_id` | string | |
| `kind` | string | gello / gripper_read / realsense |
| `seq` | int64 | agent 内序号 |
| `sensor_id` | string | |
| `joints_rad` | list[float64] | gello 仿射后（拆列 `j0..jN`） |
| `joints_rad_raw` | list[float64] | gello 仿射前（拆列 `j_raw0..`） |
| `position_norm` | float64 | gripper |
| `image_relpath` | string | 相机相对路径 |
| `role`, `dry_run` | | 元数据 |

### 5.2 宽表 `timeline_aligned.parquet`

| 列 | 说明 |
|----|------|
| `t_wall`, `t_mono` | 主时钟 |
| `gello.seq`, `gello.j0` … | 仿射后关节 + 匹配 seq |
| `gello.j_raw0` … | 仿射前关节（与 YAML offsets/signs 对应） |
| `gello.match_dt` | 对齐误差（主时钟为 gello 时为 0） |
| `gripper.*` | 同上 |
| `cam_left.file`, `cam_left.seq`, `cam_left.match_dt` | 各相机 |

### 5.3 CSV

- 与 Parquet 同 schema；关节拆成 `j0,j1,...` 列
- 适合小 episode 或人工查看；大 episode 优先 Parquet

### 5.4 `export_meta.json` 示例

```json
{
  "source_episode": "episode_00000",
  "export_version": 1,
  "align": {"mode": "asof_backward", "master": "gello-leader", "dt": null},
  "time_range": {"t_start": 1788310596.23, "t_end": 1788310650.12},
  "rows": {"events": 152340, "aligned": 30468},
  "agents": ["gello-leader", "gripper-read", "rs-left", "rs-right"]
}
```

---

## 6. CLI

```bash
# 长表，默认 Parquet
sensors-dcs export-timeline -e configs/data/episode_00000

# 宽表：以 gello 为主时钟
sensors-dcs export-timeline -e episode_00000 \
  --align asof --master gello-leader --format parquet

# 固定 50Hz 网格，CSV
sensors-dcs export-timeline -e episode_00000 \
  --align grid --hz 50 --format csv
```

可选：在 `RecordController.stop()` 完成后异步触发导出（不阻塞 UI）。

依赖建议：可选 extra `[export]` → `pyarrow` 或 `pandas[parquet]`。

---

## 7. 实施步骤（分三期）

### Phase 1 — 长表导出（约 1 天）

- [x] `load_episode(ep_dir) -> list[Sample]`
- [x] 合并、`t_wall` 排序 → DataFrame → Parquet
- [x] CLI `export-timeline`（仅 events）
- [x] 单元测试：fixture `episode_00000`

**验收**：长表行数 = 各 jsonl 行数之和；相机行含正确 `image_relpath` 与 `t_wall`。

### Phase 2 — 宽表 as-of 对齐（约 1–2 天）

- [x] `--align asof|nearest`，`--master <agent_id>`
- [x] 二分 / `pandas.merge_asof` 实现 as-of join
- [x] 输出 `*.match_dt` 列
- [x] 无 gello 时 fallback 到最高 hz 的 state agent

**验收**：以 gello 为主时钟时，`gello.match_dt == 0`；相机 `match_dt` 在合理范围（如 < 1/hz_cam）。

### Phase 3 — 网格重采样与质量报告（按需）

- [x] `--align grid --hz N`
- [x] `export_meta.json`：各 camera 平均/最大 `match_dt`、缺失率、`manifest.dropped`
- [ ] 可选：`stop()` 后自动 export

---

## 8. 边界情况

| 情况 | 处理 |
|------|------|
| 某时刻仅有相机、无 gello | 宽表对应列 NaN；可选 `--drop-incomplete` |
| 录制丢帧（`manifest.dropped > 0`） | 写入 `export_meta`；不补帧 |
| 多相机同 hz 但相位不同 | 各自 as-of 到主时钟 |
| `t_wall` 回跳（NTP） | 额外导出 `t_rel = t_wall - manifest.t_start`；分析可用 `t_mono` 相对值 |
| 关节数不一致 | 从 manifest + 首行 payload 推断列数 |
| index 有记录但 jpg 缺失 | 导出 `file_missing=true` |

---

## 9. 默认约定（MVP）

1. 输出：`timeline_events.parquet` + `timeline_aligned.parquet`（指定 `--align` 时）
2. 对齐：`--master` 默认第一个 gello agent，否则最高 hz state；`--align asof_backward`
3. 时间列：保留 `t_wall`，并增加 `t_rel`
4. 图像：仅相对路径 string，不 embed base64
5. 触发：CLI 手动；自动导出留 Phase 3

---

## 10. 下游使用示例

```python
import pandas as pd
from pathlib import Path

ep = Path("configs/data/episode_00000")
df = pd.read_parquet(ep / "export/timeline_aligned.parquet")
row = df.iloc[1000]

img = cv2.imread(ep / row["cam_left.file"])
joints = [row[f"gello.j{i}"] for i in range(7)]
assert abs(row["cam_left.match_dt"]) < 0.02  # 20ms 内
```

图片与关节的对齐依赖 **`t_wall`（及 `match_dt`）**，不依赖文件名 `seq`；`seq` 仅用于回溯原始 index 行。

---

## 11. 相关代码

| 模块 | 路径 |
|------|------|
| 写盘（states / index.jsonl） | `src/sensors_dcs/record.py` |
| Frame 时间戳字段 | `src/sensors_dcs/frame.py` |
| Agent seq 语义 | `src/sensors_dcs/agents/base.py` |
| 总体规划（Phase 5 提及 dump-index） | `plan.md` |
