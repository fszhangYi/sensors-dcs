# UI：主页 / 数据采集 / 数据后处理 Tab 与快速采集

日期：2026-09-03（2026-09-04 增补主页）

## 目标

登录后默认落在 **主页**（软件介绍，中英 i18n）。另有两个工作 Tab，并把常用三条后处理 CLI 做成可视化接口：

```text
sensors-dcs export-timeline -e … --align asof --master cam-left --rate-policy configs/rate_policies/fixed_5hz.json
sensors-dcs filter-timeline -e … --require arm,cam-left,cam-right,cam-middle,gripper-read --max-match-dt 0.033 --trim both --materialize
sensors-dcs export-hik-dataset -e … --camera-map …/hik_camera_map.yaml
```

后处理页先有「生成变频策略」区（**双轴二维图**拖阶梯编辑 %→Hz，并可叠加多集 gripper read/write 曲线作参考；写出 JSON 契约不变），「对齐参数」再**加载**该策略（不再填单一 master-hz 数字）。默认 `configs/rate_policies/fixed_5hz.json`（等价原 5 Hz）。一键三步与快速采集都读该表单（`localStorage` 键 `dcs.postprocess`），再传给 `export-timeline --rate-policy`。图表交互细节见 [rate-policy-chart-editor.md](rate-policy-chart-editor.md)。

「数据采集」Tab 增加 **快速采集** 复选框：勾选后，点「结束」或「作废」在落盘完成后自动跑上述三步（参数与「数据后处理」Tab 共用）。

## 行为

| 入口 | 行为 |
|------|------|
| 登录 / 打开 `/` | 默认 **主页** Tab |
| 主页 CTA | 可跳到采集或后处理 |
| 数据后处理 → 生成策略 | 图上拖阶梯（可叠 gripper）→ 保存 JSON → 应用到对齐 |
| 数据后处理 → 各 Step 按钮 | 只跑对应一步 |
| 一键执行三步 | 顺序三步，失败即停 |
| **批量一键三步** | 选数据集根，遍历 `episode_*`；默认删除各集 `export/` 后重跑；不覆盖则跳过已有 `export/`；`valid=false` 默认跳过 |
| 快速采集 + 结束 | `valid=true` 后跑三步；`allow_invalid` 跟表单 |
| 快速采集 + 作废 | 落盘 `valid=false` 后跑三步，并 **强制** `allow_invalid` |

## API

- `GET /api/postprocess/defaults` — 默认参数、camera-map / rate-policy 候选、`save_dir` 下 episode 列表  
- `GET /api/postprocess/rate-policy?path=` — 校验并返回策略 JSON  
- `POST /api/postprocess/rate-policy/save` — body `{path, policy}` 写出策略文件  
- `POST /api/postprocess/gripper-series` — body `{paths, kinds?}` 返回多集 gripper 进度曲线（图表叠加）  
- `POST /api/postprocess/run` — body 见 `PostprocessBody`（`episode` + 可选 `steps` + `rate_policy`）  
- `POST /api/postprocess/run-root` — body 见 `PostprocessRootBody`（`input_root` + 与单集相同的对齐/过滤/hik 字段 + `overwrite`，默认 true）  
- `POST /api/record/stop` 响应新增 `finished_episode_path` / `finished_episode_index` / `valid`

CLI：`sensors-dcs export-timeline … --rate-policy configs/rate_policies/fine_middle_v1.json`；`run-postprocess-root` 同样支持 `--rate-policy`（覆盖 `--master-hz`）。

实现：`src/sensors_dcs/export/rate_policy.py`、`postprocess_service.py`、`viz.py`、`ui_i18n.py`（`home.*` / `tab.home` / `pp.batch_*` / `pp.rate_*`）。
