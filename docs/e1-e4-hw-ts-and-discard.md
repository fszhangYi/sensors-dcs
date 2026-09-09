# E1 HW 时间戳落盘 + E4 作废按钮（过程记录）

日期：2026-09-03  
范围：只改采集落盘与 UI/API；**不**改 timeline / filter 对齐时钟（仍用 `t_wall`）。

## 背景

相对 `hik_gello/save_data` 基线对比（见 [save-data-compare.md](save-data-compare.md)）：

- **E1**：驱动已能读 RealSense `color_timestamp` / `depth_timestamp`，但 agent → record 未写入 `index.jsonl`。
- **E4**：`stop` 成功后一律 `manifest.valid=true`，无「作废本局仍归档」路径。

约定（产品侧）：

1. E1 **只存** HW 时间戳与 domain，后处理不消费。  
2. E4 **同一 stop 路径**：正常结束 `valid=true`，作废 `valid=false`；目录不删。  
3. 导出默认跳过 `valid=false`；需要时用 `--allow-invalid`。

## 改动摘要

| 层 | 文件 | 行为 |
|----|------|------|
| Agent | `agents/realsense_agent.py` | payload 透传 `color/depth_timestamp` + `*_domain` |
| Record | `record.py` | `stop(..., valid=)`；最终 manifest 写 `valid`；相机 `index.jsonl` 写 HW 字段；开局 provisional 仍 `valid=false` |
| Viz UI | `viz.py` | 「作废」按钮 → `POST /api/record/stop` + `{"valid":false}`；「结束」传 `valid:true`；无 body 兼容旧客户端 → `valid=true` |
| Export | `export/timeline.py` `ensure_episode_exportable`；`filter.py` / `hik_dataset.py` / `cli.py` | 默认拒绝 `valid=false`；`--allow-invalid` 覆盖；filter materialize 保留源 `valid` |

## 行为对照

| 操作 | 落盘 | `manifest.valid` | 导出 |
|------|------|------------------|------|
| 开始 | 写 provisional manifest | `false` | 不应导出半成品 |
| 结束 | flush 完整 episode | `true` | 正常 |
| 作废 | 同样 flush | `false` + note | 默认拒绝，`--allow-invalid` 可强行 |

## 未做（有意）— 部分已被 W2 覆盖

- ~~timeline 不以 HW ts 为轴~~ → **W2 已可选** `export-timeline --align-clock hw_ts`（默认仍 `wall`；见 [hw-timeline-align.md](hw-timeline-align.md)）。  
- E2 / E3 / E5 本轮不动。  
- 作废不删目录、不改 episode 序号规则（仍递增）。  
- 录制侧**不**因 HW 对齐额外丢预热帧；warmup 裁剪仍在 `filter-timeline --trim`（与 E4 作废局、E2 写失败丢帧正交）。

## 验证

```bash
pytest tests/test_record_manifest_cameras.py tests/test_record_depth.py tests/test_episode_valid_gate.py -q
```
