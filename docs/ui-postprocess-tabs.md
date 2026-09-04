# UI：数据采集 / 数据后处理 Tab 与快速采集

日期：2026-09-03

## 目标

把桌面预览页拆成两个 Tab，并把常用三条后处理 CLI 做成可视化接口：

```text
sensors-dcs export-timeline -e … --align asof --master cam-left --master-hz <N>
sensors-dcs filter-timeline -e … --require arm,cam-left,cam-right,cam-middle,gripper-read --max-match-dt 0.033 --trim both --materialize
sensors-dcs export-hik-dataset -e … --camera-map …/hik_camera_map.yaml
```

后处理页「对齐参数」区可改 `align` / `master` / **`master-hz`**（默认 5，**不是写死**）。一键三步与快速采集都读该表单（`localStorage` 键 `dcs.postprocess`），再传给 `export-timeline`。

「数据采集」Tab 增加 **快速采集** 复选框：勾选后，点「结束」或「作废」在落盘完成后自动跑上述三步（参数与「数据后处理」Tab 共用）。

## 行为

| 入口 | 行为 |
|------|------|
| 数据后处理 → 各 Step 按钮 | 只跑对应一步 |
| 一键执行三步 | 顺序三步，失败即停 |
| 快速采集 + 结束 | `valid=true` 后跑三步；`allow_invalid` 跟表单 |
| 快速采集 + 作废 | 落盘 `valid=false` 后跑三步，并 **强制** `allow_invalid` |

## API

- `GET /api/postprocess/defaults` — 默认参数、camera-map 候选、`save_dir` 下 episode 列表  
- `POST /api/postprocess/run` — body 见 `PostprocessBody`（`episode` + 可选 `steps`）  
- `POST /api/record/stop` 响应新增 `finished_episode_path` / `finished_episode_index` / `valid`

实现：`src/sensors_dcs/postprocess_service.py`、`viz.py`。
