# Elite 8056 监控重连 — 修复计划

> 现象：断线再连、久置后再启动 `sensors-dcs`，控制口偶发已连上，但 **Elibot monitor thread** 崩溃。  
> 状态：**Phase 1 已实现**（驱动层重试）；Phase 2 vendor patch 仍可选。  
> 范围：`elite` SDK（桌面捆绑 `elirobots`）监控握手 + DCS/`hik-sensors` 臂驱动生命周期。  
> 不做：改 Elite 控制器固件；不借此放开 `arm_read` 写路径。

---

## 0. 一句话结论

根因在捆绑 SDK 的 **`__first_connect`：对 TCP 8056 只 `recv` 一次 4 字节包头就 `struct.unpack`，短读/空包直接炸线程**；断线与久置后重连最容易踩。难改是因为炸点在 **第三方包内不可变源码**，且 8056 会话与控制器侧状态耦合，DCS 只能在外围做 **重试 / 停干净 / 兜底**。

---

## 1. 现象与日志证据

典型启动日志（桌面 `sensors-dcs.exe`，`full_cell_plus.yaml`，`robot_ip=10.111.34.200`）：

```text
… |Robot_IP: 10.111.34.200| DEBUG | 10.111.34.200 connect success
Exception in thread Elibot monitor thread,IP:10.111.34.200:
  …
  File "elite\_monitor.py", line 144, in monitor_run
  File "elite\_monitor.py", line 105, in __first_connect
struct.error: unpack requires a buffer of 4 bytes
```

解读：

| 行 | 含义 |
|----|------|
| `connect success` | `EC(auto_connect=True)` **控制 TCP** 已成功 |
| `Elibot monitor thread` | 随后 `monitor_thread_run()` 起的 **8056 监控线程** |
| `struct.error … 4 bytes` | 监控握手读包头失败，线程未捕获异常而退出 |

之后 `machinePos` 不可用 → 读臂 / 点动 / Infer IK seed 全部连锁失败。串口类传感器无此路径。

---

## 2. 根因（SDK 行为）

桌面捆绑 `elite/_monitor.py`（PyPI `elirobots`）：

```text
monitor_run()
  └─ __first_connect()
        connect(8056)
        byte_msg_size = sock.recv(4)     # 期望完整 uint32 MessageSize
        shutdown+close 探测 socket
        MSG_SIZE = struct.unpack("!I", byte_msg_size)   # 短读 → struct.error
  └─ __socket_create()   # 真正的长连接监控流（跑不到这里）
```

要点：

1. **探测连接用一次 `recv(4)`，不做「凑满 4 字节」循环**，也不校验 `len == 4`。
2. **只 `except socket.timeout`**，不处理空包、`ConnectionReset`、`struct.error`。
3. 异常冒泡出 `monitor_run` → **daemon 线程静默死亡**；主进程仍可能显示「已连接」。
4. 探测 socket 立刻关掉再开长连接；控制器侧若仍占着旧 8056 会话，新 TCP 常能 `connect` 但马上被对端关掉 → `recv` 得 `b''`。

与「串口传感器」差异：臂状态依赖 **控制器上的长生命周期 TCP**；进程杀、断电、NAT/防火墙久置掐线，都会在机器人侧留下半开/占用态。

---

## 3. 为何「很难改」

| 难点 | 说明 |
|------|------|
| 炸点在第三方包 | `_internal/elite/` 随桌面打包；改源码需 **fork/vendoring** 或运行时 monkeypatch，升级 `elirobots` 易冲掉 |
| 不可观测的控制器态 | 无法从 DCS 可靠查询「8056 是否仍被旧客户端占用」 |
| 双端口语义 | 控制口成功 ≠ 监控口可用；现有 UI/probe 容易只看到前者 |
| 生命周期耦合 | `arm` + `arm_write` 可共享同一 `sensor_id`；`open`/`close`/`monitor_thread_stop` 时序一旦漏停，重连必炸 |
| 桌面 Windows | 用户杀窗 / 任务管理器结束进程时 `close()` 常跑不完 |
| 无现成单测真机 | 8056 竞态依赖真机与时序，CI 只能 mock 短读 |

因此计划采用 **「不改控制器 + 尽量少碰 SDK 核心协议」** 的外围加固，而不是一次性「重写 Elite SDK」。

---

## 4. 目标与非目标

### 目标

- [ ] 断线再启、久置再启、快速重启后，**监控线程能稳定起来**（允许有限次重试 + 退避）。
- [ ] `__first_connect` 类失败对上层变成 **可恢复错误**（明确 `ok/error`），而不是静默线程死。
- [ ] `open()` / `close()` 保证 **尽量停干净** monitor；启动前可选「冷却等待」。
- [ ] UI / 日志能区分：**控制口 OK vs 监控口失败**。
- [ ] 文档写清运维缓解（重启间隔、勿双开 DCS、控制器侧重置）。

### 非目标

- 不修改 Elite 控制器固件或官方协议。
- 不把 `arm_read` 改成可写。
- 不在本计划内解决 TCP 位姿 / 夹爪 / 相机映射等问题。
- 不保证「任意时刻热插拔网线零丢包」；只保证 **可重试恢复**。

---

## 5. 方案选项（取舍）

### A. 驱动层重试包装（推荐主路径）

在 `hik-sensors` 的 `elite_read` / `elite_write`：

- `open()`：`EC` + `monitor_thread_run` 后，在 `monitor_wait_s` 内轮询 `machinePos`；若监控线程已死或一直 `None`，则 `monitor_thread_stop` / 丢弃实例，**退避后重建 EC**（有限次，如 3～5 次，间隔 0.5～2s）。
- `close()`：先 `monitor_thread_stop`（join 超时可配置），再丢引用；必要时短睡眠，降低控制器 TIME_WAIT 竞态。
- 捕获/检测：线程 `is_alive()==False`、或 `machinePos` 超时 → 一律当监控握手失败处理。

**优点**：不改 `elite` 包文件；桌面只需新版 `sensors` 源码进 bundle。  
**缺点**：治标（仍依赖 SDK 每次握手运气）；极端占用态可能仍需人工重启控制器网络服务。

### B. Vendoring / patch `elite/_monitor.py`

把 `__first_connect` 改为：

- `recv` 循环直到 4 字节或 EOF；
- EOF / 短读 → 明确异常或重试，而不是 `struct.error`；
- 可选：探测失败不杀整个 `monitor_run`，外层重试。

**优点**：对准根因。  
**缺点**：与上游 `elirobots` 分叉；打包与许可证/升级成本高。适合作为 A 仍不够时的 **Phase 2**。

### C. Monkeypatch（运行时）

启动时对 `elite.ECMonitor.__first_connect` 打补丁。

**优点**：不改 site-packages 文件布局。  
**缺点**：脆、难测、PyInstaller 路径易踩坑。仅作应急，不作为主方案。

### D. 运维缓解（现在就可做，与代码并行）

- 重启 DCS 前等 **3～5s**；避免双开 `sensors-dcs.exe`。
- 复现后重启机器人控制器网络/整机再连。
- 确认仅一台客户端占 8056。

写入 README / 本文件「运维」小节即可。

**建议路线：D（立即）+ A（主实现）+ 视效果再开 B。**

---

## 6. 实现分期

### Phase 0 — 文档与观测（本文件）

- [x] 记录现象、栈、根因、难改点  
- [x] README「Elite 以太网」排障增加本错误条目与缓解步骤  
- [x] 驱动日志统一前缀：`[elite-monitor] connect/retry/give-up`

### Phase 1 — `hik-sensors` 外围重试（主交付）

仓库：`/root/autodl-tmp/sensors`（DCS 经 symlink / 桌面 `sensors_src` 带入）。

| 项 | 内容 |
|----|------|
| 文件 | `drivers/arm/_elite_monitor.py` + `elite_read.py` / `elite_write.py` |
| `open_ec_with_monitor` | 创建 EC → `monitor_thread_run` → 等 `machinePos`；失败则 stop + sleep + 重试 |
| 配置 | `monitor_retries`（默认 3）、`monitor_retry_backoff_s`（默认 1.0）、`post_close_cooldown_s`（默认 0.3） |
| `close` | stop + timed join + cooldown |
| 错误信息 | 明确写「8056 monitor handshake failed …」 |
| 测试 | `tests/test_elite_monitor.py` mock 线程秒退 / 成功路径 |

- [x] Phase 1 代码与单测
- [ ] 桌面打包说明：需带上含 Phase 1 的 `sensors` 源码后再打 Windows zip（真机验收）
- [ ] DCS UI 透出监控失败文案（可选增强；当前日志已有 `[elite-monitor]`）

### Phase 2 —（可选）Vendor patch

- [ ] 将 `elite/_monitor.py` 拷入 `packaging/vendor/elite/` 或 patch 脚本  
- [ ] 修 `__first_connect` 短读；`monitor_run` 顶层 catch 后重试 N 次  
- [ ] `build_desktop.py` / `hardware_bundle` 验证加载的是 vendor 版  
- [ ] 记录上游版本号与 diff，便于升级对照  

### Phase 3 —（可选）产品化

- [ ] Infer / Collect 启动闸：监控未就绪禁止点动 / LOOP  
- [ ] 「重新连接臂」按钮：只 `close`+`open` 臂传感器，不必重启整进程  
- [ ] 指标：监控线程存活、最后 `machinePos` 时间戳  

---

## 7. 验收标准

| # | 场景 | 期望 |
|---|------|------|
| 1 | 正常冷启动 | 监控就绪，`machinePos` 有限值 |
| 2 | Ctrl+C / 关窗后再立刻启动 | 经 ≤N 次重试后恢复，或明确失败文案（禁止裸 `struct.error` 线程栈成为唯一信号） |
| 3 | 运行中断网再恢复后进程重启 | 同 2 |
| 4 | 久置（>30min）后再开 DCS | 同 2 |
| 5 | dry_run | 行为不变，不碰真机 |
| 6 | 单元测试 | mock 短读路径覆盖重试与耗尽 |

---

## 8. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 重试加重控制器负担 | 限制次数与退避；失败即停 |
| join 卡死退出 | `join(timeout=…)` + 日志；daemon 线程兜底 |
| vendor 与 PyPI 漂移 | Phase 2 钉版本 + diff 入库 |
| 双 agent 误建双 EC | 保持单 `sensor_id` 单实例；文档禁止两套 IP 双开 |

回滚：配置 `monitor_retries=0` 恢复近似旧行为；vendor 则改回官方 wheel。

---

## 9. 运维缓解（代码未合入前）

1. 关闭 DCS 后等待 **3～5 秒** 再开。  
2. 同一时刻只跑 **一个** 连该 IP 的客户端（含旧 hik_gello / 其他 monitor）。  
3. 若连续握手失败：重启机器人控制器网络或整机，再开 DCS。  
4. 看日志：若仅有 `connect success` 而无稳定关节，即监控已死，不要继续点动。  

---

## 10. 相关路径

| 路径 | 角色 |
|------|------|
| `sensors/.../arm/elite_read.py` | 只读 open/close + monitor |
| `sensors/.../arm/elite_write.py` | 写臂；open 同样起 monitor |
| 桌面 `_internal/elite/_monitor.py` | SDK 炸点（`__first_connect` L93–105，`monitor_run` L140–144） |
| `docs/arm-write.md` | 写臂安全契约（本计划不削弱） |
| `packaging/hardware_bundle.py` | 桌面捆绑 `elite` |

---

## 11. 建议落地顺序

1. 合并本计划 + README 排障（Phase 0）。  
2. 实现 Phase 1 helper + read/write `open`/`close` + 测试。  
3. 打一版 Windows 桌面，在真机复现「快速重启 / 久置」验收表。  
4. 若仍高频失败 → 开 Phase 2 vendor patch。  
5. 有余力再做 Phase 3「重连臂」按钮。
