# 传感器 / 同步覆盖（M3）与场景标签覆盖（E4）对照

日期：2026-11-03  
相关：[capability-metrics-plan.md](capability-metrics-plan.md) · [filter-timeline.md](filter-timeline.md) · [hw-timeline-align.md](hw-timeline-align.md)

---

## 1. 不要混名

| 名称 | 代码位置 | 含义 |
|------|----------|------|
| **数据保留率** | `kpis.keep_rate` | `manifest.valid=true` / 定稿 episode 数（作废占比的补） |
| **场景 / 物体覆盖** | `kpis.tag_coverage` + `by_*` | E4：`task_id` / `env_id` / `object_ids` 标签透视 |
| **传感器 / 同步覆盖** | `coverage`（本页） | M3：滤波保留率、require 缺口、录制丢帧、hw_ts 覆盖 |

口语里都叫「覆盖率」时，**先问是标签覆盖还是时间轴覆盖**。

---

## 2. M3 字段（`metrics_summary.json` → `coverage`）

- **schema**：`dcs_sensor_coverage_v1`
- **per episode**：`status=unprocessed|filtered`，`filter_keep_rate`，`missing_agents`，`drop_rate`，`hw_coverage`，`alerts[]`
- **summary**：已滤波 / 未后处理计数、滤波保留率直方图、缺失 agent 频次、告警计数

阈值（可后续做成配置）：

- `filter_keep_rate < 0.9` → `low_filter_keep_rate`
- 有 `missing_agents` → `missing_agents`
- `drop_rate > 0.05` → `high_record_drop_rate`
- `align_clock=hw_ts` 且 `hw_coverage < 1` → `incomplete_hw_coverage`

**验收约定：** 无 `export/filter_meta.json` → `status=unprocessed`（UI「未后处理」）；有 filter → 给出 `filter_keep_rate` 与 `missing_agents`。

---

## 3. 数据源

| 量 | 优先来源 |
|----|----------|
| 滤波保留率 | `filter_meta.align_quality.components.keep_rate` 或 `rows_out/rows_in` |
| 缺失 require | `filter_meta.require_unmatched_columns`（否则相对 `export_meta.agents` 软比对） |
| 录制丢帧 | `manifest.written/dropped`（或 export_meta 镜像） |
| HW 覆盖 | `export_meta.hw_coverage` + `align_clock` |
