# Gripper `fake` 读门控

> 状态：**已实现**（YAML 配置，无运行期热切换）  
> 日期：2026-09-03  
> 驱动：`sensors/.../drivers/gripper/dh_ag95.py`  
> DCS 透传：`gripper_read_agent`（payload 带 `fake` / `position_source`）

---

## 1. 做什么

AG95 位置反馈寄存器不可靠时，用配置项 **`fake: true`**：

- **`read()`** 不再读 `REG_POSITION_FB` 作为开合；  
- 改为返回 **最近一次成功外部位置下发** 的缓存目标；  
- **`fake: false`（默认）**：行为与改造前一致。

「外部下发」含 UI / `POST /api/gripper/command` 与 gello sync——二者都经 `sensor.write({position_norm|position_raw})`，缓存只在驱动里维护一处。

---

## 2. 怎么控制（仅 YAML）

写在 **sensors 设备配置**（`params` 或 `defaults.gripper`，经 `device_to_sensor_config` 合并）：

```yaml
defaults:
  gripper:
    fake: false   # 站点默认

devices:
  - id: gripper-dh
    kind: gripper
    endpoint: "COM4"
    params:
      fake: true  # 本设备打开假读
```

DCS 示例已加注释：

- `configs/sensors_gello_gripper.yaml`
- `configs/sensors_full_cell.yaml`

hik-sensors：`configs/default.yaml`

改配置后需 **重启进程** 生效（无 HTTP 热切换）。

---

## 3. 行为摘要

| | `fake=false` | `fake=true` |
|--|--------------|-------------|
| 位置来源 | `REG_POSITION_FB` | 上次成功 `write` 的目标 |
| sample 标记 | `fake: false`, `position_source: "register"` | `fake: true`, `position_source: "last_command"` |
| 从未下发过位置 | 正常读寄存器 / dry_run 合成 | `position_norm=null` + `error: fake read: no commanded position cached yet` |
| `init_state` / `fault` | 稀疏读寄存器 | 同样稀疏读（与假位置解耦） |
| `initialize` / `calibrate` | 不改位置缓存 | 同左；只有位置 `write` 成功才更新缓存 |

写失败不更新缓存。`dry_run` 下成功的 write 也会更新缓存。

---

## 4. 下游影响

打开 `fake` 后，`gripper_read` / 录制 / hik steps 夹爪观测在名义上仍是「read」，物理上是 **命令轨迹**。启用站点需知情（见 [hik-dataset-actions.md](hik-dataset-actions.md)「默认相信 read」）。

---

## 5. 验收

```text
sensors: pytest tests/test_smoke.py -k gripper_fake
# 或全量 gripper 相关 smoke
```

期望：`fake` 下 read 等于上次 write 的 `position_norm`；无缓存时报错字段；`fake=false` 仍走 register 路径。
