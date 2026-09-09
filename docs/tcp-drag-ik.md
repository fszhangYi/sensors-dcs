# 琥珀色 TCP 球拖拽 + 实时 IK — 实现计划

日期：2026-09-09  
状态：**已实现**  
范围：推理 Tab · 控制分页 · Three.js 末端位姿画布；**不写臂**，只预览 IK joints。

---

## 0. 产品结论

| # | 结论 |
|---|------|
| 1 | 琥珀色球（live TCP）可拖拽；粉目标 / 蓝 Home **不**参与拖拽。 |
| 2 | 球具有「极大质量」：不跟鼠标指针，只跟鼠标运动**趋势**缓慢移动（强阻尼速度积分）。 |
| 3 | 拖拽只改 TCP **位置** xyz；rpy 保持拖拽开始时的 live 姿态。 |
| 4 | 拖拽过程中节流调用 IK，把 `joints_rad` 显式展示在画布上的**浮动窗**；浮动窗有关闭按钮。 |
| 5 | 关闭浮动窗 → 退出拖拽预览，TCP 球恢复跟随 `arm · Read` FK。 |
| 6 | **不下发** `arm_write`；URDF 可随 IK joints 预览刷新。 |
| 7 | LMB 未点中球时仍为相机 orbit；RMB/MMB pan、滚轮缩放、双击跟随不变。 |

---

## 1. 现状（挂点）

| 项 | 位置 |
|----|------|
| Three 画布 / 琥珀球 | `viz.py` `initInfPoseViz`：`tcpMarker` color `0xf0b429` |
| 指针 | 仅 `orbit` / `pan`，无 Raycaster |
| `tick()` | 每帧 `setTcpPose(__armReadCartesian)`，会冲掉拖拽态 |
| IK | `arm_pose.xyzrpy_to_joints_rad`；仅 `pi05_step` 内调用，**无独立 HTTP** |
| joints 展示 | `#infArmJoints` 文本框；无画布浮动窗 |

---

## 2. 后端

### 2.1 API

`POST /api/arm/ik`

```json
{ "xyzrpy": [x, y, z, rx, ry, rz] }
```

响应复用 `Orchestrator._xyzrpy_to_joints`：

```json
{ "ok": true, "joints_rad": [...], "residual_norm": ..., "pos_err_m": ..., "error": null }
```

失败：`ok: false` + `error`。

### 2.2 接线

- `create_viz_app(..., arm_ik=...)`
- `runtime.serve` / `desktop_main` 传入 `orch._xyzrpy_to_joints`（或薄封装 `arm_ik`）
- Pydantic：`ArmIkBody { xyzrpy: list[float] }`

---

## 3. 前端交互

### 3.1 命中与模式

1. `pointerdown` LMB：`Raycaster` 命中 `tcpMarker`（可加略大 invisible hit mesh）→ `dragMode='tcp'`；否则 `orbit`。
2. 进入 tcp：记下 `dragXyzrpy`、`vel=0`，打开浮动窗，设 `tcpDragActive=true`（`tick` 不再用 live FK 覆盖球位）。
3. `pointermove`（tcp）：将屏幕 Δ 投到相机平面，累加到速度 `vel += impulse / MASS`（MASS 很大 → 位移很小）。
4. `tick` 内积分：`pos += vel * dt`；`vel *= damp`（如 0.90–0.95）；更新球位 + 节流 IK。
5. `pointerup`：停止施加 impulse，球可继续惯性地滑一小段后停住；浮动窗仍开直至用户关。
6. 浮动窗关闭：清 `tcpDragActive`，恢复 live TCP / 可选恢复 live URDF joints。

### 3.2 「极大质量」参数（可硬编码常量）

| 常量 | 含义 | 建议初值 |
|------|------|----------|
| `TCP_MASS` | 质量（越大越钝） | `80`–`120` |
| `TCP_DAMP` | 每帧速度衰减 | `0.92` |
| `TCP_IMPULSE_SCALE` | 像素→世界冲量 | 与相机距离成比例 |
| `TCP_V_MAX` | 最大速度帽 | ~`0.08` m/s |
| `IK_MIN_INTERVAL_MS` | IK 节流 | `80`–`120` |

验收：快速甩鼠标时球明显滞后、不会贴着光标；慢拖时有可感知的缓慢漂移。

### 3.3 浮动窗 UI

- 挂在 `.inf-pose-canvas-wrap` 内：`position:absolute`，右上或右下。
- 内容：标题「拖拽 IK」+ 6 轴 joints（rad，3–4 位小数）+ IK 状态行 + **关闭**按钮。
- 样式贴近现有 glass / trail-clear，不引入新设计语言。
- i18n：`infer.tcp_drag_*`（zh/en）。

### 3.4 IK 成功路径

1. 更新浮动窗数字。
2. `fillInfArmJointsFromStep` 同类逻辑写入 `#infArmJoints`（便于随后手动下发）——**可选**；计划：**写入**，与单步调试一致。
3. `__applyInfPoseJoints(joints)` 刷新 URDF 预览。

失败：浮动窗显示 `ik_error`，球位仍可继续拖。

---

## 4. 非目标

- 不改粉球 goal / 轨迹珠逻辑。
- 不自动 `arm_command`。
- 不做姿态（rpy）拖拽 / 6D gizmo。
- 不在客户端重写 IK（必须走服务端 `ik_flange`）。

---

## 5. 验收

1. 点空白 LMB → 仍旋转相机；点琥珀球 → 进入拖拽。
2. 拖拽时球缓动跟随趋势，不贴鼠标。
3. 浮动窗实时更新 joints；关窗后球回到 live TCP。
4. `#infArmJoints` 在 IK 成功时更新；点「下发」仍走原有 abs ramp。
5. 无 arm_read 种子时 IK 返回明确错误，不崩溃。

---

## 6. 文件清单

| 文件 | 变更 |
|------|------|
| `docs/tcp-drag-ik.md` | 本计划 |
| `src/sensors_dcs/viz.py` | HTML/CSS/JS 拖拽 + API 路由 |
| `src/sensors_dcs/runtime.py` | `arm_ik` 公开方法 + `serve` 接线 |
| `src/sensors_dcs/desktop_main.py` | 同接线 |
| `src/sensors_dcs/ui_i18n.py` | 文案 |

---

## 7. 实现顺序

1. 后端 `POST /api/arm/ik` + 接线  
2. 浮动窗 DOM/CSS/i18n  
3. Raycaster + 质量阻尼拖拽 + `tick` 门控  
4. 节流 IK → 填窗 / joints / URDF  
5. 手工点验后提交推送  
