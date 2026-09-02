# 宽表过滤与重新编号（filter-timeline）

对 **已导出的宽表**（`timeline_aligned.parquet`）做质量过滤，输出 `step` 从 0 连续的 `timeline_filtered.*`，可选按 step 物化图片。  
**原则**：不修改原始 `episode_*` 写盘；过滤为只读后处理。

---

## 1. 背景

`export-timeline --align asof --master X` 生成的宽表：

- 每行 = master 的一个采样时刻
- 其他列为 as-of backward 匹配结果，含 `{agent}.match_dt`

边界问题：

| 边界 | 现象 | 处理 |
|------|------|------|
| **开头 warmup** | 其它 sensor 列 NaN 或 match_dt 过大 | 从第一行「全部 require 通过」起保留 |
| **宽表内结尾 cooldown** | 其它 sensor 已停，末尾 match_dt 飙升 | 从最后一行「全部 require 通过」止截断 |
| **宽表外尾巴** | 其它 sensor 在 master 结束后仍有原始采样 | **不并入宽表**；写入 `filter_meta.tail_after_master` |

---

## 2. 过滤规则

### 2.1 单行保留条件

对 `--require` 中每个 agent `A`：

1. 关键列非空（gello：`A.j0`；gripper：`A.position_norm`；相机：`A.image_relpath`）
2. `A.file_missing != True`（若列存在）
3. 非 master：`A.match_dt <= max_match_dt(A)`（as-of backward 时 match_dt ≥ 0）
4. master：`A.match_dt == 0`（已有）

### 2.2 `--max-match-dt`

- 全局秒数，如 `0.033`
- 或 per-agent：`cam-left:0.033,gello:0.02`
- 默认 `0.033`（约 30Hz 相机一帧）

### 2.3 `--trim`

| 值 | 行为 |
|----|------|
| `none` | 仅按行规则过滤，不额外裁头尾 |
| `start` | 去掉开头连续不合格行（warmup） |
| `end` | 去掉结尾连续不合格行（cooldown） |
| `both`（默认） | 头尾都裁 |

在布尔 mask 上：找第一个/最后一个 `True`，保留闭区间。

### 2.4 重新编号

过滤后增加列 **`step`**：`0 .. N-1`。  
原始 `{agent}.seq`、`t_wall` 保留。

### 2.5 `--materialize`

将 require 中相机 agent 的图片复制到：

```text
<episode>/export/filtered/images/<agent_id>/<step:08d>.jpg
```

并写入 `{agent}.filtered_file` 相对路径。不修改原始 `cameras/`。

---

## 3. CLI

```bash
sensors-dcs filter-timeline -e configs/data/episode_00000 \
  --require gello,cam-left,cam-middle \
  --max-match-dt 0.033 \
  --trim both

sensors-dcs filter-timeline -e episode_00000 \
  --input export/timeline_aligned.parquet \
  -o export/timeline_filtered.parquet \
  --require cam-left \
  --max-match-dt cam-left:0.05,cam-right:0.05 \
  --materialize
```

默认输入：`<episode>/export/timeline_aligned.parquet`（或 `.csv`）  
默认输出：`<episode>/export/timeline_filtered.parquet`  
元数据：`<episode>/export/filter_meta.json`

---

## 4. 输出 `filter_meta.json`

```json
{
  "source_episode": "episode_00000",
  "input": "export/timeline_aligned.parquet",
  "master": "gello",
  "rows_in": 30468,
  "rows_out": 28102,
  "step_range": [0, 28101],
  "trimmed_start": 120,
  "trimmed_end": 45,
  "last_master_t_wall": 1788310650.12,
  "tail_after_master": {"cam-right": 18},
  "drop_reasons": {
    "row_filter": 2231,
    "trim_start": 120,
    "trim_end": 45
  },
  "require": ["gello", "cam-left"],
  "max_match_dt": {"default": 0.033}
}
```

---

## 5. 流程

```text
episode_*  →  export-timeline --align asof --master …
           →  filter-timeline --require … --trim both
           →  timeline_filtered.parquet（step 从 0）
           →  （可选）filtered/images/
```

---

## 6. 实施清单

- [x] 本文档
- [x] `src/sensors_dcs/export/filter.py`
- [x] CLI `filter-timeline`
- [x] 单元测试
- [x] README 用法
