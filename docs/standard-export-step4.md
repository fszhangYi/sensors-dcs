# Step 4 · 标准格式导出

承接 **Step 2** 的宽表 `export/timeline_filtered.parquet`（或 `.csv`），写出可选主流训练布局。  
图像按宽表里的 `{cam}.image_relpath` 从 **原始 episode**（`cameras/…`）读取。  
**不使用** `export/filtered/`（那是 Step 2 `--materialize` 的可选旁路，与 Step 4 无关）。

也不替代 Step 3（`export-hik-dataset`）。一键三步 / 快速采集仍只跑 Step 1–3。

## 入口

| 入口 | 选择 | 行为 |
|------|------|------|
| 单集 | `episode_*` 目录 | 检查非空 `timeline_filtered`；按勾选格式写出；**强制覆盖** |
| 批量 | 含多个 `episode_*` 的数据集根 | 逐集同上；**一律强制覆盖** |

```bash
sensors-dcs export-standard -e path/to/episode_00009 -f lerobot_v21 -f hdf5
sensors-dcs export-standard-root -i path/to/_dcs_data --formats lerobot_v21,lerobot_v30
```

HTTP：`POST /api/postprocess/standard-export` · `…-root` · `GET …/standard-formats`

## 格式 → 输出目录

| id | 目录（相对 episode） |
|----|----------------------|
| `lerobot_v21` | `export/lerobot_v21/` |
| `lerobot_v30` | `export/lerobot_v30/` |
| `hdf5` | `export/standard_hdf5/`（`h5py`，桌面包已打入） |

只重建上述格式子目录，不动 Step 1–2 的 parquet / meta。

## Step 2 前置

| 产物 | Step 4 |
|------|--------|
| `export/timeline_filtered.parquet` | **必需**，且 `rows > 0` |
| `export/filter_meta.json` | 可选（诊断） |
| `export/filtered/` | **不读、不要求** |

`rows_out=0` 时无法导出：先放宽 require / max_match_dt 或补齐图像，再跑 Step 2。

`camera-map` 优先 UI/CLI；否则 `export/hik_camera_map.yaml`；再否则用 agent_id 作相机名。  
任务文案优先 `manifest.tags.task_id`。
