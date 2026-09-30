# TCP 工作空间盒子 Clip — 设计计划

日期：2026-09-30  
状态：**已实现（首版）**  
范围：**仅** serve `next_state` 解码路径（单步调试 / LOOP / 编排页复用的同一步骤）；**不含** 手动「下发」、Home、TCP 拖拽。

可视化：被 clip 的下发目标在左侧 Three.js 画布上以约 2s 周期缓慢闪烁（透明度脉冲）；未 clip 的目标保持常亮。

---

## 0. 已锁定决策

| 项 | 决策 |
|----|------|
| 作用范围 | **1A**：仅单步/LOOP（及 Flow 编排调用的同一 `runInfPi05StepOnce`） |
| joints 策略 | **2A**：`joints → FK → clip xyz → 再 IK`，插值终点用新 joints |
| bounds 传递 | 前端 UI 配置，**随每次** `POST /api/pi05/step` 下发 |
| 哨兵值 | 某一边界为 **`-1` 表示该侧不限制**；六项全为 `-1` ≡ 关闭 clip |
| 裁剪轴 | 只 clip `x,y,z`；`rx,ry,rz` 与 grip 不动 |
| 超界策略 | **夹到边界**（不整步拒绝） |
| 权威实现 | **后端** `_finish_decoded_goal` 内、IK 之前 |

---

## 1. 现状与插入点

```text
单步 / LOOP → POST /api/pi05/step
  → _decode_next_state → _finish_decoded_goal → IK → next_joints_rad
  → 回填 joints 框 → LOOP: sendInfArmJointsOnce 关节斜坡
```

今日：`joints` 在 `_finish_decoded_goal` 早退直通，不再 IK。插值仍是关节空间 `start_arm_abs_ramp`；本功能改的是 **终点**，不改斜坡时间律。

---

## 2. 目标与非目标

### 2.1 目标

1. 三种 `next_state_format` 都先得到绝对 `goal_xyzrpy`，再对 xyz 做盒子 clip。
2. clip 后的 pose 再 IK；`next_joints_rad` / 回填框 / LOOP 下发终点一致。
3. 前端可设 `x_min,x_max,y_min,y_max,z_min,z_max`（默认全 `-1`），持久化 localStorage，step 时随请求体带上。
4. 响应带回 clip 前后位姿，便于 HUD/调试。

### 2.2 非目标（本期）

- 手动「下发」/ Home / TCP 拖拽 / Gello sync 路径上的 clip
- 笛卡尔直线插值、姿态 clip、整步 reject
- 服务端 YAML 默认盒子（本期以 UI + step body 为准）

---

## 3. 语义与算法

### 3.1 Bounds 模型

```text
tcp_clip: {
  x_min, x_max, y_min, y_max, z_min, z_max   # float；-1 = 该侧不限制
}
```

辅助：`is_free(v) := (v == -1)`（精确相等即可；UI 用 number input）。

单轴：

```text
if not is_free(lo):  x = max(x, lo)
if not is_free(hi):  x = min(x, hi)
```

若两侧都有效且 `lo > hi`：step 返回 decode 失败（参数错误），不静默 swap。

### 3.2 统一解码 + clip 顺序

| recv_fmt | clip 前 | clip 后 |
|----------|---------|---------|
| `pose` | `next_state[0:6]` | clip xyz → IK |
| `delta_pose` | 当前 TCP ⊕ delta → 绝对 pose | 同上 |
| `joints` | FK(`joints`) → pose | clip xyz → **强制再 IK**（不再 joints 早退） |

当六边界全 `-1`：行为与今日一致（含 joints 早退直通，避免无意义 FK/IK）。

### 3.3 响应字段

在现有 `goal_xyzrpy` / `next_joints_rad` / `ik_ok` 上增加：

- `goal_xyzrpy_raw`：clip 前绝对 pose（6）
- `goal_xyzrpy`：clip 后（IK 用的终点）
- `tcp_clip_applied`：bool（是否有分量被改动）
- `tcp_clip`：回显本次生效的 bounds

---

## 4. API / 代码改动清单

### 4.1 后端

| 位置 | 改动 |
|------|------|
| `Pi05StepBody` | 可选 `tcp_clip` 对象；缺省视为全 `-1` |
| `POST /api/pi05/step` → `pi05_step` | 把 bounds 传入 decode |
| `arm_pose.clip_tcp_xyzrpy` | 纯函数；`-1` / `lo>hi` |
| `_finish_decoded_goal` | IK 前 clip；joints 仅在「有有效边界」时取消早退并 FK→clip→IK |

### 4.2 前端

Infer「推理」区 wire format 行下方：6 个 number（默认 `-1`，单位 m）、`getInfTcpClip()` → step body、localStorage、i18n。客户端 IK fallback 使用后端已 clip 的 `goal_xyzrpy`。

### 4.3 文档

本文件；[`pi05-infer-tab.md`](pi05-infer-tab.md) / [`infer-orchestration-flow.md`](infer-orchestration-flow.md) 交叉引用。

---

## 5. 数据流（实现后）

```text
UI bounds (default all -1)
        │
        ▼
POST /api/pi05/step { …, tcp_clip }
        │
        ▼
serve next_state
        │
        ▼
绝对 goal_xyzrpy_raw  ← pose | compose(delta) | FK(joints)
        │
        ▼
clip xyz（-1 侧跳过）→ goal_xyzrpy
        │
        ▼
IK → next_joints_rad   ← 单步回填 / LOOP 插值终点
```

---

## 6. 测试计划

- unit：`clip_tcp_xyzrpy` 边界与 `-1`、`lo>hi`
- unit：`normalize_tcp_clip` / 有界时 joints 不再早退
- 手工：Infer 设小盒子 LOOP；全 `-1` 与改前无差异；joints recv 越界被拉回盒子
