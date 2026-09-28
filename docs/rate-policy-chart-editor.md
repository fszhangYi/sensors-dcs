# 变频策略二维图编辑器（1A + 2C）

日期：2026-09-28

## 目标

后处理页「0 · 生成变频策略」从分段表格改为**双轴二维图**编辑；策略仍保存为既有 `piecewise_progress` JSON，对齐 / 一键三步 / 快速采集 / CLI `--rate-policy` **不变**。

| 决策 | 选择 |
|------|------|
| 拖动构建策略 | **1A** 阶梯编辑：拖竖线改边界、拖横条改 Hz、增删断点 |
| gripper 叠加 | **2C** 多选 episode，且每集可选 `gripper_read` / `gripper_write` |

## 为何换生成方式

策略语义仍是「按 episode 时间进度分段设定 Hz」。对照真实夹爪开合曲线在进度轴上画阶梯，比手填起始%/结束%/Hz 表格更直观，尤其适合在夹爪动作密集段提高采样率。

## 不改的契约

[`src/sensors_dcs/export/rate_policy.py`](../src/sensors_dcs/export/rate_policy.py)：

```json
{
  "name": "fine_middle_v1",
  "version": 1,
  "type": "piecewise_progress",
  "axis": "time_fraction",
  "segments": [
    {"start": 0.0, "end": 0.10, "hz": 5},
    {"start": 0.10, "end": 0.50, "hz": 10}
  ]
}
```

- `segments` 必须从 `0` 连续覆盖到 `1`，`hz > 0`。
- 保存：`POST /api/postprocess/rate-policy/save` body `{path, policy}`。
- 加载：`GET /api/postprocess/rate-policy?path=`；对齐区摘要仍为 `a-b%@Hz`。

生成区仍保留：名称、JSON 预览、保存路径、保存、应用到对齐、精细中段 / 固定 5Hz 模板。

## 图坐标系

```text
  gripper (0–1) │                    │ Hz (右轴)
                │   ╭─ gripper 曲线  │
                │  ╱                 │  ┌──┐   ← 策略阶梯横条（可竖拖）
                │ ╱                  │  │  └──┐
                │╱                   │──┘     └──
                └────────────────────┴────────────→ episode % (0–100)
                     ↑ 竖线断点（可水平拖；0%/100% 锁定）
```

| 轴 | 含义 | 范围 |
|----|------|------|
| 横轴 | episode 时间进度百分制 | 0–100（= `time_fraction * 100`） |
| 左纵轴 | gripper 传感器 / 指令 | 0–1（`position_norm` / `command_position_norm`） |
| 右纵轴 | 变频策略 Hz | 建议可视 0–60；编辑夹紧 0.5–60 |

gripper 曲线**只作参考**，不写入策略 JSON。

## 进度定义

对每个 episode：

1. 扫描 `states/*.jsonl` 全部样本的 `t_wall`，取 `t0 = min`、`t_end = max`（整集百分制，不把 gripper 自己拉满 0–100）。
2. 点进度：`pct = (t_wall - t0) / (t_end - t0) * 100`。
3. 若 `t_end ≈ t0` 或没有 states：该集无曲线，仍可只编辑策略阶梯。
4. 缺 gripper 的 episode：可加载为空 series 列表，不阻断策略编辑。

## 1A 阶梯交互

策略是**分段常数** step 函数，不是手绘曲线。

| 操作 | 行为 |
|------|------|
| 拖中间竖线 | 水平移动断点；改相邻两段 `end`/`start`；吸附 **0.5%**；最小段宽 **1%** |
| 0% / 100% 竖线 | **锁定**不可拖 |
| 拖横条 | 竖直改该段 `hz`，夹紧 **0.5–60** |
| 双击图区或「添加断点」 | 在点击 % 处拆段；新右段继承左段 Hz |
| 选中中间断点 + Delete /「删除断点」 | 合并左右段，Hz 取左段 |
| 精细中段 / 固定 5Hz | 重置内存 segments 并重绘 |

拖动时 tooltip 示例：`12.0–50.0% · 10 Hz`。

内存中的 segments（`start_pct` / `end_pct` / `hz`）驱动 `buildRatePolicyObject()` → JSON 预览 → 现有 save / apply。

## 2C 叠加

- 多选若干 episode（来自后处理 episode 列表或路径）。
- 每个 episode 独立勾选：
  - `gripper_read` → 字段 `position_norm`
  - `gripper_write` → 字段 `command_position_norm`
- 多条曲线叠在同一左轴上，不同颜色区分；图例显示 `episode_label · kind`。
- 「加载曲线」调用下方 API；失败只提示，不阻断阶梯编辑。

## 数据流

```mermaid
flowchart LR
  overlay[多选 episode read write]
  api[POST gripper-series]
  canvas[双轴 canvas]
  segs[segments 内存]
  json[现有 save 与 apply]

  overlay --> api --> canvas
  canvas -->|拖竖线横条| segs
  segs --> json
```

## API：`POST /api/postprocess/gripper-series`

**Request**

```json
{
  "paths": ["/data/episode_00016", "/data/episode_00017"],
  "kinds": ["gripper_read", "gripper_write"]
}
```

- `paths`：必填，episode 目录列表。
- `kinds`：可选，默认 `["gripper_read", "gripper_write"]`。

**Response**

```json
{
  "ok": true,
  "series": [
    {
      "episode": "/data/episode_00016",
      "episode_label": "episode_00016",
      "agent_id": "gripper-read",
      "kind": "gripper_read",
      "field": "position_norm",
      "t0": 1000.0,
      "t_end": 1012.5,
      "points": [{"pct": 0.0, "v": 0.12}, {"pct": 1.25, "v": 0.15}]
    }
  ],
  "errors": [
    {"path": "/missing", "error": "episode directory not found"}
  ]
}
```

实现约定：

- 只读 `states/*.jsonl`，不扫 cameras。
- 服务端按进度均匀抽稀，**每条最多 800 点**。
- 单集失败进 `errors`，其它集仍返回；`ok` 为 true 表示请求本身成功（可含部分错误）。

实现位置：[`postprocess_service.py`](../src/sensors_dcs/postprocess_service.py) + [`viz.py`](../src/sensors_dcs/viz.py) 路由。

## UI 替换范围

[`viz.py`](../src/sensors_dcs/viz.py) 后处理「生成变频策略」区：

| 移除 / 降级 | 保留 | 新增 |
|-------------|------|------|
| `#ppRateSegTable` 为主编辑器 | 名称、JSON 预览、保存路径、保存、应用到对齐、两模板按钮 | episode 多选 + 每集 read/write 勾选、「加载曲线」 |
| 「添加分段」表格行 | 同上 | 原生 `<canvas>` 双轴、添加/删除断点、tooltip |

图表用**原生 canvas**（无 Chart.js/plotly），风格跟现有 `--spark` / 面板 CSS。i18n 键：`pp.rate_chart_*`（中英）。

## 测试清单

| 项 | 方式 |
|----|------|
| `parse_rate_policy` / 仓库默认 JSON / `subsample_times_policy` | 现有 `tests/test_rate_policy.py` 必须仍过 |
| gripper-series：read/write、缺目录、抽稀 | 新单测（合成 `states/*.jsonl`） |
| 保存后对齐区摘要 `a-b%@Hz` | API 路径不变，逻辑回归 |
| 多选叠加、拖边界、拖 Hz、增删断点、保存 JSON | 后处理页手测（无浏览器自动化时人工确认） |
