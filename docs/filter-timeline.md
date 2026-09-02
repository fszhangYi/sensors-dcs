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

写出与原始 episode **同构** 的目录（下采样 + 时间对齐后的结果）：

```text
<episode>/export/filtered/
  manifest.json
  states/
    gello.jsonl
    gripper-read.jsonl
    …
  cameras/
    cam-left/
      00000000.jpg
      index.jsonl
    cam-middle/
      …
```

约定：

- 每个 step 一行，各 agent 共用对齐后的 `t_wall`（master 时钟）
- `seq` 等于 `step`（从 0 连续编号）
- 相机图片按 `cameras/<agent>/<step:08d>.jpg` 落盘；`index.jsonl` 与生数据格式一致
- state jsonl 从宽表还原 `joints_rad` / `joints_rad_raw` / `position_norm` 等；gello 可带回 `joint_offsets`/`joint_signs`（来自 episode manifest / export_meta）；`payload` 可含 `src_seq` / `src_t_wall` / `match_dt` 便于追溯
- `manifest.cameras`（录制 start 时写入的 RealSense 内参）会原样拷到 filtered `manifest.json`，供 `export-hik-dataset` 填 `metadata.intrinsic_matrix`
- **不修改** 原始 `episode_*/cameras|states`

宽表里额外写入 `{agent}.filtered_file`（相对 episode 的路径，如 `filtered/cameras/cam-left/00000000.jpg`）。

### 2.6 `export-hik-dataset`（filter 之后）

将 `export/filtered/` 转成与 `hik_gello/data_postprocess.py` 一致的训练集目录：

```text
<episode>/export/hik_dataset/
  metadata.json
  steps.json
  rgb_<hik_cam>_<i>.jpg
  # 若 filtered 含深度帧，另有 d_<hik_cam>_<i>.png
```

约定：

- 输入须先 `--materialize`（或 `filter-timeline --hik-dataset` 自动 materialize）
- 关节优先 `arm_read`，否则 `gello`；夹爪优先 `gripper_read`，否则 `gello` 第 7 轴
- 相机名来自 **`--camera-map` YAML**（serial → hik 名，例见 `configs/hik_camera_map.yaml`）；filter 时指定后会写入 `export/filtered/camera_map.yaml`，后续 `export-hik-dataset` 可复用
- DCS 录制无深度 / 无专有 FK 时：`cartesian_*` 填 0，`metadata.cartesian_source=zeros_no_fk`；`depth_camera_num=0`
- `metadata.intrinsic_matrix` 优先取自 episode/`filtered` `manifest.cameras.<agent>.intrinsic_matrix`（录制 start 时 RealSense `open()` 写入）；可用 `--calibration-json` 覆盖
- `steps.json` 结构与 data_postprocess 相同：`observations`/`actions` 的 `joint_position`、`cartesian_position`、`gripper_position`（**字段含义见 [hik-dataset-steps.md](hik-dataset-steps.md)**）

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

# filter 产物 → hik 训练集（必须带 camera-map）
sensors-dcs export-hik-dataset -e episode_00000 \
  --camera-map configs/hik_camera_map.yaml

# 一步：filter + materialize + hik 导出
sensors-dcs filter-timeline -e episode_00000 \
  --require arm,cam-left,cam-middle \
  --trim both \
  --hik-dataset \
  --camera-map configs/hik_camera_map.yaml
```

默认输入：`<episode>/export/timeline_aligned.parquet`（或 `.csv`）  
默认输出：`<episode>/export/timeline_filtered.parquet`  
元数据：`<episode>/export/filter_meta.json`  
hik 导出默认：`<episode>/export/hik_dataset/`，旁路元数据 `export/hik_dataset_meta.json`

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
           →  （可选 --materialize）export/filtered/{manifest,states,cameras}
           →  export-hik-dataset → export/hik_dataset/{metadata,steps,rgb_*}
```

---

## 6. 实施清单

- [x] 本文档
- [x] `src/sensors_dcs/export/filter.py`
- [x] CLI `filter-timeline`
- [x] `src/sensors_dcs/export/hik_dataset.py` + CLI `export-hik-dataset`
- [x] 单元测试
- [x] README 用法
