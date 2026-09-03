# 异步落盘（Async flush）设计

日期：2026-09-03

## 问题

默认落盘是**串行**的：点「结束 / 作废」后 `RecordController` 进入 `flushing`，
UI 禁用「开始」，直到当前 episode 的队列写完、manifest 落盘才回到 `idle`。
快速采集还会在同一条请求链里再跑后处理，下一集更晚才能开。

## 目标

勾选「异步落盘」后：

1. 「结束 / 作废」**立刻**把控制器状态切回 `idle`，并 `episode_index += 1`；
2. 上一集的 sampler/writer 在**后台线程**继续 drain 队列并写最终 manifest；
3. 下一集可以马上「开始」录制（与上一集落盘并行）；
4. 若同时勾选「快速采集」，后处理也在后台跑，不挡住下一集。

未勾选时行为与原先完全一致（同步 `flushing`）。

## 结构

```
RecordController
├── state: idle | recording | flushing   # flushing 仅同步模式
├── _session: 当前录制中的 _EpisodeSession | None
└── _flush_jobs: {ep → {state, path, valid, …}}  # 后台落盘任务表
```

每个 `_EpisodeSession` 自带独立的 `Queue` / sampler / writer / `written|dropped`，
因此后台 flush 与下一集 recording **不共享队列**。

### 同步 stop

1. `accepting=false`，`state=flushing`
2. 同线程 join sampler/writer、写 manifest
3. `state=idle`，bump episode

### 异步 stop（`async_flush=True`）

1. `accepting=false`，把 session 从 `_session` 摘下
2. **立即** `state=idle` + bump episode + 登记 `_flush_jobs[ep]=flushing`
3. 后台线程 `_finalize_session`：join + manifest，再把 job 标为 `done|error`
4. HTTP 立刻返回 `finished_episode_path`（快速采集可据此开跑）

## API / UI

- `POST /api/record/stop` JSON：`{ "valid": true|false, "async_flush": true|false }`
- 状态里增加 `flushing_count` / `flushing_jobs`（UI 显示「后台落盘中 ×N」）
- 采集页复选框「异步落盘」，偏好存 `localStorage` 键 `dcs.asyncFlush`

## 约束与注意

- 改保存路径仍要求当前不在 `recording|flushing`（同步 flush 中不可改）；异步模式下有后台 job 时允许改路径（新 episode 写到新根）。
- 异步 + 快速采集：后处理可能与下一集录制抢磁盘 IO，属预期权衡。
- 进程退出（安全退出）仍会先尽量停掉当前 recording；已 detach 的 flush 线程为 daemon，极端 kill 可能丢尾部队列，与原先 daemon writer 风险同类。
- 多集同时 flush 时，agents 环缓冲仍只有「最新帧」语义；录制采样按各 session 自己的 last_seq 去重，互不影响。

## 相关代码

- `src/sensors_dcs/record.py` — `RecordController.stop(async_flush=…)`
- `src/sensors_dcs/viz.py` — 复选框 + stop body + 快速采集非阻塞
