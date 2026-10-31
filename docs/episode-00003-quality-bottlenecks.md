# episode_00003 采集质量差 — 瓶颈分析与后续方案

日期：2026-10-31  
样本：`sensors-dcs/episode_00003.zip`（≈1005 MB；源目录 `_dcs_data/episode_00003`）  
工位：`dcs-g26562`（CS66 + Gello + 慧灵夹爪 + 三路 RealSense）  
采集时长：≈ **84.4 s**（`manifest.t_start`…`t_end`）  
结论：**差质量是「采集运动链路稀 + 臂读数不稳」与「后处理 require 别名未解析」叠加；不是单纯相机坏了。**

元数据抽检目录（本机分析用，可不入库）：`_episode_00003_meta/`。

---

## 1. 包内有什么

| 路径 | 内容 | 规模（约） |
|------|------|------------|
| `manifest.json` | 工位 / agent / 相机内参 / `written`/`dropped` | 1 |
| `cameras/<agent>/*.jpg` + `index.jsonl` | 三路彩色帧 + 索引 | left 2422 / right 2425 / wrist 2410 |
| `states/*.jsonl` | gello / arm 读 / arm 写 / gripper 读写 / reinforce | 6 流 |
| `export/timeline_*.parquet` | 对齐宽表 + 事件表 + 过滤结果 | aligned 409 行；**filtered 0 行** |
| `export/export_meta.json` / `filter_meta.json` | 对齐与过滤元数据、`align_quality` | grade **poor / 7.2** |
| `export/filter_align.png` | 对齐质量图 | 1 |
| `raw_grid.mp4` | 预览拼接视频 | ≈35 MB |

`manifest`：`written=24384`，`dropped=432`（录制环有丢帧）。

---

## 2. 质量判决（为什么说「非常差」）

### 2.1 后处理几乎交不出可用集

`filter_meta.json`：

| 项 | 值 |
|----|----|
| `rows_in`（aligned） | 409 |
| `rows_out`（filtered） | **0** |
| `drop_reasons` | `{ "missing_data": 409 }` |
| `align_quality.grade` | **poor** |
| `align_quality.score` | **7.2** |
| `require` | `arm,cam-left,cam-right,cam-middle,gripper-read` |
| `max_match_dt.default` | **0.033 s** |

即：对齐表还在，**过滤一步把全部行扔掉**，训练/导出下游看到的是空集或仅 raw。

### 2.2 对齐本身已偏低分辨率

`export_meta.align.rate_policy` = **`fixed_5hz`** → 84 s × 5 Hz ≈ **409** 行。  
相机原始约 30 Hz、Gello 约 50 Hz，对齐却压到 5 Hz，时间细节大量丢失（即便不过滤也会「糊」）。

### 2.3 真机跟随轨迹不密

对 `states/arm-cs66-write.jsonl`：采样约 35 Hz，但 **command 关节发生变化** 的有效更新仅约 **6.7 Hz**（Δt 中位 ≈113 ms，p95 ≈394 ms）。  
这与同日 gello_trace 中 `teleop_cmd≈6 Hz` 一致：**摇操写臂稀 → 臂轨迹顿挫 → 画面/状态「跟手差」**。（采集时刻早于 2026-10-31 latest-wins 改造。）

---

## 3. 各流实测频率（t_wall）

| 流 | n | 均值 Hz | 目标 Hz | 备注 |
|----|---|--------|--------|------|
| gello | 4003 | **49.0** | 50 | 健康；>100 ms 空档极少 |
| gripper-hl-read | 3403 | **41.6** | 50 | 可用 |
| cam-*-wrist/left/right | ~2410–2425 | **≈29.5–29.7** | 30 | 基本达标；偶发 100–270 ms 空档 |
| arm-cs66（读） | 2546 | **31.1** | 50 | **明显偏低**；>50 ms 空档 16%，>100 ms 7.3%，max≈499 ms |
| arm-cs66-write（状态采样） | 2859 | 35.0 | 50 | 采样密，但见下「命令变点」 |
| arm-cs66-write **命令变点** | — | **≈6.7** | 希望 ≈100 | **运动瓶颈** |
| gripper-hl-write | 2708 | ≈42 | 50 | 存在 **≈28 s** 级超大空档（max dt）；对齐 `match_dt` 均值被拉到数秒 |

Gello↔臂（asof 到 arm 读）：

| 指标 | 值 |
|------|-----|
| \(\max_i\|q_g-q_a\|\) mean / p95 / max | 0.042 / 0.098 / 0.151 rad |
| asof 滞后 mean / p95 / max | 43 / 152 / 488 ms |

说明：主手与臂有持续跟随误差 + 读侧时间对齐不紧，和写稀、臂读不稳一致。

---

## 4. 瓶颈分层（按优先级）

```text
P0  后处理 require 仍用旧别名（arm / cam-middle / …）
    → kind=unknown → 每行 missing_data → filtered=0
    （即使原始采集「还行」，成品集也是空的）

P1  采集时 gello→arm 摇操写频 ≈6–7 Hz（阻塞 writeServoj 串行环）
    → 轨迹顿挫、视觉上「质量差」、对齐到 5 Hz 网格时每格跨越大动作

P2  arm 读仅 ≈31 Hz + 长空档
    → 相对 wrist 主钟 match_dt 常 >33 ms（mean≈42 ms，max≈413 ms）
    → 即便 require 修正，在 33 ms 门限下仍掉约 45% 行

P3  导出策略固定 5 Hz
    → 时序过稀，放大 P1/P2

P4  gripper-write 长空档 / reinforce 无图像列
    → 若误加入 require 会继续屠表；本集 filter 尚未真正评到它们
```

### 4.1 P0 证据：别名未解析（确定性 bug / 配置债）

本集 `filter_meta.require` 字面量：

`arm, cam-left, cam-right, cam-middle, gripper-read`

而真实列前缀是：

`arm-cs66`, `cam-g26562-left|right|wrist`, `gripper-hl-read`

用当前 `export.filter._agent_kind` 复现：

| require | kept |
|---------|------|
| 旧别名（本集实际） | **0 / 409**（全 `missing_data`） |
| 真实 id + `max_dt=0.033` | **227 / 409**（其余多为 `match_dt_exceeded`） |
| 真实 id + `max_dt=0.05` | 290 / 409 |
| 真实 id + `max_dt=0.10` | 360 / 409 |

默认值来源：`postprocess_service.DEFAULT_REQUIRE`、`viz.py` 后处理表单默认、`ui_i18n` 提示文案——仍是旧工位命名，**G26562 站未自动映射**。

> 这是「分数 7.2 / filtered=0」的**直接原因**。感官上的差还有 P1/P2，但流水线成品为空首先要修 P0。

### 4.2 P1 证据：写臂命令过稀（运动质量）

| 指标 | 值 |
|------|-----|
| write 状态采样 Hz | ≈35 |
| **command 变化 Hz** | **≈6.7** |
| 命令间隔 p95 | ≈394 ms |
| `last_ok` | 全 True（不是写失败，是写太少） |

机制（采集当时）：摇操单线程「读 gello → 阻塞 `writeServoj` → sleep(1/teleop_hz)」+ 曾有 `teleop_step_max_rad` 限速。  
**2026-10-31 已在 DCS 落地 latest-wins + 定频写线程并取消步长限速**（见 [gello-arm-teleop.md](gello-arm-teleop.md) 变更记录）。本集是改造前样本，应用新代码**重采**才能验证运动侧改善。

### 4.3 P2 证据：臂读时钟相对相机偏稀

相对 `cam-g26562-wrist` 对齐：

| 流 | match_dt mean | max | 相对 33 ms |
|----|---------------|-----|------------|
| gello | 10 ms | 88 ms | 多数过关 |
| cam-left/right | 17–19 ms | 64–153 ms | 边缘 |
| **arm-cs66** | **42 ms** | **413 ms** | **主拖累** |
| arm-cs66-write | 103 ms | 832 ms | 写状态更稀（与 P1 一致） |
| gripper-hl-write | **5.8 s** | 28 s | 切勿默认 require |

根因候选：CS `arm_cs_write` 与 RTSI 读争用 `_io_lock` / 同进程 USB·网卡负载；录制 `dropped=432` 也说明整机 IO 有压力。

### 4.4 P3：5 Hz 策略

`fixed_5hz.json` 适合粗预览，不适合模仿学习细动作。G26562 建议至少：

- 相机主钟附近 **15–30 Hz**，或  
- 按进度分段：接近操作中段升高 Hz（已有 rate-policy 编辑器）。

---

## 5. 「差」到底差在哪一层（对照表）

| 层级 | 本集状态 | 用户可感知 |
|------|----------|------------|
| 相机原流 | ≈30 Hz，大体正常 | 单帧图未必糊 |
| Gello 原流 | ≈50 Hz | 主手本身顺 |
| **臂跟随** | **命令 ≈6.7 Hz** | **顿挫、跟手差** |
| 臂状态读 | ≈31 Hz + 空档 | 对齐/监督信号抖 |
| 对齐表 | 5 Hz × 409 | 时间分辨率低 |
| **过滤成品** | **0 行** | **没法训 / 导出像废集** |

---

## 6. 后续解决方案

### 6.1 立刻（不重采也能救数据）

1. **修正后处理 require / master / primary**  
   - 例：  
     `require=arm-cs66,cam-g26562-left,cam-g26562-right,cam-g26562-wrist,gripper-hl-read`  
     `master=cam-g26562-wrist`  
   - **不要**默认加入 `gripper-hl-write` / `gello-reinforce`（本集无图像）。  
2. **放宽门限做抢救导出**：`max_match_dt` 先 **0.05–0.10**，看 keep 率；同时记录 `match_dt` 分布。  
3. **对本 zip 重跑** `export-timeline`（可换更高 rate-policy）+ `filter-timeline`，覆盖 `export/`。  
4. 产品改动：后处理默认值按 **manifest.agents** 自动填真实 `agent_id`，并废弃/映射 `cam-middle`→腕/中相机等别名（`postprocess_service` + UI）。

### 6.2 短周期（重采验证运动）

1. 使用已合入的 **gello→arm latest-wins 摇操**（`teleop_hz=100`，无 step 限速）；采集前确认 UI 文案含 `latest-wins`。  
2. 开 **gello_trace** 录一段：期望 `teleop_cmd` 有效密度显著高于 6 Hz，且无 `teleop_cmd_rate_limited`。  
3. 采集时避免与重导出/多路高分辨率无关负载抢盘；关注 `manifest.dropped`。  
4. 对齐策略改为 **≥15 Hz**（或任务相关分段策略），再 filter。

### 6.3 中期（治本臂读/写争用）

1. 评估 CS 读线程与 `writeServoj` 的锁粒度：读路径是否可无短超时 / 独立 RTSI 快照，避免被写阻塞拉到 31 Hz。  
2. 遥操写线程：周期按 **deadline**（扣除 write_ms）而非「写完再 sleep(period)」，逼近真实 100 Hz。  
3. 录制侧：相机 JPEG 质量/分辨率与 dropped 的关系做一次压测（左右 1280×720×30×2 + wrist 已不轻）。  
4. filter `align_quality`：在 `missing_data` 占主导时给出 **「require 未匹配任何列」** 的显式诊断，避免只显示 poor 分数。

### 6.4 验收清单（下一集）

- [ ] `filter` keep_rate ≫ 0；`align_quality.grade` ≥ fair/good（按现权重）  
- [ ] `require` 全部能解析到真实 agent；无「全 missing_data」  
- [ ] gello_trace：`teleop_cmd` 有效 Hz 与 `gello_read` 同量级或写线程稳定 ≥50 Hz  
- [ ] `arm-cs66` 均值 Hz ≥45，>100 ms 空档 <1%  
- [ ] aligned 行数与策略 Hz×时长相符；`arm.match_dt` p95 ≤ 选定门限  
- [ ] `manifest.dropped` 可解释且可接受  

---

## 7. 建议改动落点（代码）

| 项 | 仓库路径 |
|----|----------|
| 摇操 latest-wins（已做） | `runtime.py` / [gello-arm-teleop.md](gello-arm-teleop.md) |
| 后处理默认 require/master | `postprocess_service.py`, `viz.py` Postprocess 表单 |
| 别名 → 真实 agent | `export/filter.py` 或 postprocess 解析层（按 role/kind） |
| 更高默认 rate-policy | `configs/rate_policies/` + UI 默认 |
| 臂读/写争用 | `sensors` `cs_write.py` + DCS `arm`/`arm_write` agent |

---

## 8. 一句话总结

**本集「质量非常差」= 采集时摇操写臂太稀（≈7 Hz）导致轨迹顿 + 臂读不稳拉高 match_dt，再叠加后处理仍用旧相机/臂别名把 409 行全部打成 `missing_data`（filtered=0）。**  
先修 require/重导出可抢救对齐数据；运动手感与稠密监督必须用新摇操链路重采，并提高对齐 Hz、收紧臂读稳定性。
