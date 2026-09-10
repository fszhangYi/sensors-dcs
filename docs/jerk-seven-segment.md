# Jerk 七段插值（替换余弦 S 型）— 实现计划

日期：2026-09-09  
状态：**已实现**  
范围：绝对下发 / Home 共用的关节空间 α 轨迹；gello sync 仍用 `linear`。

---

## 0. 产品结论

| # | 结论 |
|---|------|
| 1 | 将 abs 默认 `profile: cosine` **改为** `seven_segment`（真·七段 jerk 有限）。 |
| 2 | 保留 `cosine` / `linear` 兼容；sync 路径继续 `linear`。 |
| 3 | 六轴共用同一 α(u)，u=k/N；α(0)=0，α(1)=1，端点 \(\dot\alpha\approx0,\ddot\alpha\approx0\)。 |
| 4 | 可调参数：`jerk_seg_frac`（每段加/减 jerk 时长占比 Tj）、`accel_seg_frac`（匀加速时长占比 Ta）。 |
| 5 | UI 在 t_min/t_max/v_norm 旁增加 Tj / Ta；经 `/api/arm/command` 下发覆盖 YAML。 |
| 6 | 另增可选 `arm_abs_ramp.ramp_hz`（默认 20）：减轻 5Hz 阶梯感导致的「抖动」；未配则回退 gello `ramp_hz`。 |

---

## 1. 七段定义（归一化时间 u∈[0,1]）

时间轴：

```text
[+J] Tj | [0] Ta | [-J] Tj | [0] Tv | [-J] Tj | [0] Ta | [+J] Tj
约束：Tv = 1 − 4·Tj − 2·Ta ≥ 0
```

- 以 J=1 积分得 raw 位置，再除以 raw(1) → α(u)∈[0,1]。  
- Tj / Ta 非法时按比例收缩，保证 Tv≥0。  
- 默认：`jerk_seg_frac=0.10`，`accel_seg_frac=0.15` → Tv=0.30。

与余弦差：端点 jerk/accel 连续归零，启停冲击更小。

---

## 2. 配置 / API

### `ArmAbsRampConfig`

| 字段 | 默认 | 说明 |
|------|------|------|
| `profile` | `seven_segment` | `seven_segment` \| `cosine` \| `linear` |
| `jerk_seg_frac` | `0.10` | Tj，钳位约 `[0.02, 0.22]` |
| `accel_seg_frac` | `0.15` | Ta，钳位约 `[0, 0.30]` |
| `ramp_hz` | `20` | abs 命令频率；`null` 则用 gello sync hz |

### `ArmCommandBody` / `start_arm_abs_ramp`

可选覆盖：`jerk_seg_frac`、`accel_seg_frac`（及既有 t_min/t_max/v_norm）。

`arm_abs_ramp_status().params` 暴露上述字段供 UI 种子。

---

## 3. 代码改动点

| 位置 | 改动 |
|------|------|
| `runtime._seven_segment_alpha` | 新建 |
| `runtime._interp_path` | 支持 `seven_segment` + Tj/Ta |
| `runtime.start_arm_abs_ramp` / `_arm_abs_ramp_loop` | 传参；`ramp_hz` 优先 abs 配置 |
| `config.ArmAbsRampConfig` | 校验新字段 |
| `configs/default.yaml` | 新默认 |
| `viz.py` 推理 + 采集 timing 行 | Tj/Ta 输入 + sync |
| `ui_i18n.py` | 文案 |
| `tests/test_arm_abs_ramp_timing.py` | 端点、单调、端速度≈0、peak vs cosine |

---

## 4. UI

```html
<label>Tj <input class="arm-abs-jerk-frac" … value="0.10" /></label>
<label>Ta <input class="arm-abs-accel-frac" … value="0.15" /></label>
```

- 与 t_min 等同一 `arm-abs-timing` 容器，读写进 `window.__armAbsTiming`。  
- `sendInfArmJointsOnce` / Home / 采集下发带上这两个字段。

---

## 5. 非目标

- 不做每轴独立七段。  
- 不改笛卡尔路径 `arm_pose.cartesian_linear_joint_path`。  
- 不在本期做在线重规划 / 速度前馈。

---

## 6. 验收

1. `profile=seven_segment` 路径：α 单调，α(1)=1，首末步 Δα 小于中段。  
2. 数值上端点平均速度（前/后两步）显著小于 cosine 端点附近冲击感（或 \(\dot\alpha\) 估计近 0）。  
3. UI 改 Tj/Ta 后，下发 `params` / 实际 path peak 有可观测变化。  
4. 既有 cosine / linear 单测仍过；新增七段单测。  
5. Home 与下发共用同一套 profile。

---

## 7. 实现顺序

1. 落本文档  
2. `_seven_segment_alpha` + `_interp_path` + 单测  
3. config / yaml / ramp_hz / API  
4. UI + i18n  
5. 跑测 → 提交推送  
