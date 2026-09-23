# 已知漏洞 / 待补缺口（P1–P3）

日期：2026-09-23  
范围：录制 / 异步落盘 / 快速后处理 / 编排·推理 / 打包与运维（非安全渗透清单）  
排序原则：**假成功、半集进训练集、运动安全误判、误删** 优先于代码洁癖。

已缓解（勿重复当开 bug）：

| 主题 | 状态 |
|------|------|
| 正常结束 + 异步落盘时快速后处理读到 provisional `valid=false` | `57a0c4e`：UI 等待 + `wait_episode_manifest_ready`；残余见 **#1 / #2** |
| Flow ↔ LOOP 互斥 | 已有 `__syncFlowLoopMutex` / `__pi05LoopIsRunning` |
| Abs-ramp / 夹爪 Modbus / Gello bias | 近期多轮硬化；残余多为负载相关时序 |

---

## 严重度约定

| 级 | 含义 |
|----|------|
| **P1** | 易出假成功、半成品数据集，或操作员安全语义陷阱；建议先补 |
| **P2** | 常用路径会踩：关机脏集、并发、打包空洞、文档误导、局域网暴露面 |
| **P3** | 体验 / 桌面打包边角 / 低频竞态 |

---

## P1

### 1. `allow_invalid` 连 provisional 也放行

- **现象**：`--allow-invalid` / 作废后快速后处理强制 `allow_invalid=true` 时，若等待超时或导出抢跑，**未定稿** episode 仍可进入三步导出。
- **证据**：`export/timeline.py` → `ensure_episode_exportable` 在 `allow_invalid=True` 时直接 return，不检查 `provisional`；`wait_episode_manifest_ready` 超时仍可能返回 provisional 文件；`viz.py` 作废 QC：`allow_invalid: discarding \|\| …`。
- **补法**：`provisional` **永远拒绝**；`allow_invalid` 只覆盖**已定稿**的 `valid=false`（真作废）。

### 2. writer `join(timeout)` 超时仍写最终 manifest 并标 `done`

- **现象**：大相机集落盘超过默认约 120s 时，flush 仍去掉 provisional、异步 job 标 `done`，快速后处理以为写完，实际 writer 可能还在 drain → **半棵树 + 假成功**。
- **证据**：`record.py` → `_finalize_session`：`writer.join(timeout=…)` 后无条件 `_write_manifest(provisional=False)`；未检查 `is_alive()`。
- **补法**：join 后若线程仍存活 → job=`error`，保持 provisional 或显式 `incomplete` note，且不得当作 flush-ready。

### 3. Flow「Pause」只停编排等待，abs-ramp 机械臂继续跑

- **现象**：操作员以为暂停运动；规划等待冻结，但绝对路点插值仍在写臂。
- **证据**：`flow_runtime.js` → `pauseFlow` 仅 `state.paused=true`；arrive 等待尊重 pause，但不调用 `__flowCancelAbs`。Stop 会 cancel；Pause 不会。`docs/infer-orchestration-flow.md` 曾记为故意行为。
- **补法**：Pause 时 cancel/hold abs-ramp，Resume 从当前位姿续；或 UI/文案改为「仅暂停规划」。

### 4. 编排 / 推理 Discard（快捷键 X）无确认直接删目录

- **现象**：采集页作废仍确认并归档 `valid=false`；Infer/Flow 一键 `rmtree`，误触不可逆。
- **证据**：`viz.py` Infer discard：`keep_files: false`、无 `confirm`；键盘 X 点该按钮；`record.py` → `_abort_discard`。提交 `ae5c839` 起为有意行为；部分旧文档仍写「作废永不删除」。
- **补法**：二次确认或短撤销窗；文档明确 **Collect = 归档作废 / Infer·Flow = 删除本集**。

---

## P2

### 5. 关机短超时异步落盘留下脏集

- **现象**：安全退出对 recorder `stop(async_flush=True, timeout≈1s)`；flush 线程为 daemon，强退易留下 `provisional=true, valid=false`，外表像作废。
- **证据**：`runtime.py` 关机路径；`docs/async-flush.md`。
- **补法**：关机改为更长预算的同步 flush，或给未完成集打 `incomplete_shutdown` 并拒绝默认导出。

### 6. 多集异步快速后处理无单飞队列

- **现象**：异步结束立刻 `busy=false` 并 fire-and-forget `runQc()`；多集 flush + QC + 正在录可抢同一盘 IO，并绕过后处理页 `ppBusy`。
- **证据**：`viz.py` 异步分支；`docs/async-flush.md` 记 IO 争用为权衡。
- **补法**：按 episode 路径单飞 / 全局 QC 队列；录制日志展示 flush×N、QC×N。

### 7. pack-hik 遇残缺输出永久 `already_exists` 跳过

- **现象**：`{out}/{i}/` 或单独的 `video/{i}.mp4` 残留时整集永久跳过，训练集出现「空洞」。
- **证据**：`export/pack_hik_datasets.py` 存在即 skip；跳过策略见 `157e99c`。
- **补法**：识别不完整包（缺视频 / 空树）并支持 `--force` / 修复拷贝。

### 8. 文档与实现脱节

- **现象**：按文档操作会预期错误行为。
- **证据（示例）**：
  - `infer-orchestration-flow.md`：term_cond → pause；运行时已是模块 `succeeded` 进下一边（`1323230`）
  - `e1-e4-hw-ts-and-discard.md` / `ui-postprocess-tabs.md`：作废永不删盘
  - `async-flush.md` API 未写 `keep_files`
  - 部分旧文深度/预检叙述互相矛盾
- **补法**：一轮对齐 Collect vs Infer 作废、term_cond、深度与 API。

### 9. Path picker 可指向任意可读根

- **现象**：已登录用户可通过 `root_path` 浏览命名沙箱外的绝对路径。
- **证据**：`fs_browse.py` → `list_children` 只约束「落在给定 root 下」，root 本身可任意。
- **补法**：自定义 root 白名单（save_dir / user_data / 工程父目录），或仅管理员。

### 10. Auth：明文 bootstrap + 内存 session + 无 Secure cookie

- **现象**：首启写/打印明文管理员密码；重启登出全失效；Cookie 仅 HttpOnly/SameSite。
- **证据**：`auth_session.py`。实验室本机可接受；共享/入网主机偏弱。
- **补法**：优先环境变量注入、可选 session 持久化、HTTPS 时加 `Secure`。

---

## P3

### 11. 桌面 seed 缺部分配置

- **现象**：frozen/desktop `ensure_runtime_env` 种子配置未含 `pi05_infer.yaml` 等。
- **证据**：`paths.py` seed 列表 vs 仓库 `configs/`。
- **补法**：补进 seed（及同类非 legacy 配置）。

### 12. `_abort_discard` 删失败仍可能 bump 集号

- **现象**：writer join 超时后 `rmtree` 失败时，可能留下孤儿目录且 `episode_index` 已 +1。
- **证据**：`record.py` → `_abort_discard`；频率依赖大相机队列（中等不确定）。
- **补法**：writer 未死拒绝 bump；或先 rename 到 `*.discarding` 再删。

### 13. 对齐评分 / master 默认偏经验

- **现象**：后处理默认 master、对齐评分权重按当前机型经验；换机型未必稳。
- **证据**：`postprocess_service.py` 默认；`align_plot.compute_align_quality` 固定权重。
- **补法**：按 manifest agents 自适应建议；文档标明评分是启发式非标定真值。

---

## 建议落地顺序

1. **#1 + #2**（数据正确性：假成功 / 半集进训练集）  
2. **#3**（运动安全语义）  
3. **#4 + #5**（误删 / 关机脏集）  
4. **#6–#8**（并发 QC、打包、文档）  
5. **#9–#13** 按是否外网暴露、是否桌面打包再排

**若只先修 3 项**：provisional 不可被 `allow_invalid` 绕过；flush join 超时不得标 `done`；Pause 真正停臂（或改文案）。

---

## 相关代码 / 文档

| 路径 | 角色 |
|------|------|
| `src/sensors_dcs/record.py` | 录制、provisional、async flush、discard 删盘 |
| `src/sensors_dcs/export/timeline.py` | `ensure_episode_exportable` / `wait_episode_manifest_ready` |
| `src/sensors_dcs/viz.py` | 快速采集、快捷键、Infer/Flow 录制面板 |
| `src/sensors_dcs/static/flow_runtime.js` | Flow Pause / term_cond |
| `src/sensors_dcs/export/pack_hik_datasets.py` | 批量打包 skip 规则 |
| `docs/async-flush.md` | 异步落盘设计 |
| `docs/infer-orchestration-flow.md` | 编排语义（部分待与代码对齐） |

本文随修复进展更新：项关闭时改为「已修 + commit」，勿静默删除编号以免讨论错位。
