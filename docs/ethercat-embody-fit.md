# EtherCAT × 具身多仓（autodl-tmp）契合点调研

日期：2026-10-31  
范围：`sensors` / `sensors-dcs` / `sensors-arm` / Gello / 夹爪 / F/T / 多机站  
目的：① 简历说得通的技术亮点；② 对**当前工位真有效果**的落点；③ 明确**禁止硬蹭**的叙事。

与姊妹文对照：[webrtc-embody-fit.md](webrtc-embody-fit.md)（浏览器媒体面）｜本文（现场总线 / 确定性 I/O）。

---

## 0. 结论先行

> 当前大仓的机械臂已经是 **厂商控制器 + Ethernet SDK**（EC TT / CS ExternalControl+RTSI），**不是**「PC 当 EtherCAT Master 直驱关节」。  
> EtherCAT 的说得通落点是：**外围确定性设备**（六维力、分布式 IO、安全扩展、自研/改造关节驱动）以 **Slave** 接入，在 `sensors` 增加 `kind`，由 DCS agent 与臂状态 **统一时间戳采集**；主臂仍走现有 Elite 链路。  
> **不要**声称「用 EtherCAT 替换 CS66 外控就能丝滑遥操」——那既不匹配硬件开放度，也掩盖已定位的写路径问题。

一句话决策：**EtherCAT = 工位实时外设总线层；不是替换艾利特示教器运动栈。**

---

## 1. 现状总线地图（autodl-tmp 真实拓扑）

```text
┌─ PC（DCS / sensors-arm / Windows 或 Linux 工控）─────────────────┐
│  RealSense ×N ………… USB3                                              │
│  Gello ………………… Dynamixel Protocol 2.0 / RS485（USB 转串）          │
│  慧灵 / DH 夹爪 …… Modbus RTU / RS485                                 │
│  HIK 六维力 ……… 自定义串口帧 @460800（kind force_torque）            │
│  Elite EC616 ……… TCP 54321 + monitor（TT_add_joint）                   │
│  Elite CS66 ……… Primary 30001 + RTSI 30004 + ExternalControl 反连     │
└──────────────────────────────────────────────────────────────────────┘
         ▲
         │  厂商柜内：伺服通常已在控制器侧总线（用户不可见/不开放）
         ▼
   示教器 · 驱动器 · 本体
```

| 子系统 | 现协议 | 周期量级（本仓实践） | 与 EtherCAT 关系 |
|--------|--------|----------------------|------------------|
| CS66 运动 | `writeServoj` / RTSI | 遥操目标 ~50–100 Hz；读曾低至 ~30 Hz | **不替代**；厂商已封关节环 |
| EC616 运动 | TT 透传 | 类似 | **不替代** |
| Gello | Dynamixel | ~50 Hz | 硬件是串口舵机，**无 EtherCAT 口** |
| 夹爪 | Modbus RTU | ~50 Hz | 可并存；高端夹爪偶有 EC 型号，需换硬件 |
| F/T（HIK） | 私有串口 | 高波特但非硬实时网 | **换 EC 力觉**时最有叙事 |
| 相机 | USB | 30 Hz | 不走 EtherCAT |
| 站间 Peer | TCP/WS JSON | 控制面 | 与 EtherCAT 无关 |

架构硬约束（`sensors/docs/ecosystem.md`）：**驱动只进 `sensors`**，apps 只消费。EtherCAT 若做，必须是新 `kind` + YAML，而不是写进 `viz.py`。

---

## 2. EtherCAT 在工业里实际解决什么

EtherCAT（Ethernet for Control Automation Technology）典型价值：

| 能力 | 含义 | 对具身采集的意义 |
|------|------|------------------|
| 确定性周期 | Master↔多 Slave 微秒～亚毫秒级周期、低抖动 | 力控/多轴同步采样 |
| 分布式时钟 (DC) | Slave 间时间对齐 | F/T 与关节状态对齐，减轻「力–位不同步」 |
| 单网线菊花链 | 驱 + IO + 安全模块同总线 | 工位扩展不堆一堆 USB/RS485 |
| CiA402 驱动配置文件 | 标准伺服状态机 | **自研臂/改装臂**，不是品牌示教器外控 |

公开工程叙事（如模块化柜控 + EtherCAT 接 COTS 驱动）强调的是 **vendor-agnostic 驱动柜**，与「艾利特示教器外控」是两条产品线——简历里必须分清你做的是哪一条。

---

## 3. 契合点排序（对当前系统）

| 优先级 | 结合点 | 对现系统的真实效果 | 简历可讲性 | 硬件前提 |
|--------|--------|--------------------|------------|----------|
| **P0** | **EtherCAT 六维力 / 力矩 Slave → `sensors` 新 kind** | 替换或并行 HIK 串口力觉；与臂 JSONL **同 episode、可对齐**；力控采集/接触任务可用 | ★★★★★ | 采购 EC 力传感器 + Master（见 §5） |
| **P1** | **Beckhoff 类 EC IO / 安全端子** | 工位急停、光栅、夹具气缸进 DCS 状态与 gate；比「纯软件 UI 急停」可审计 | ★★★★☆ | EC 耦合器 + 端子 |
| **P2** | **自研/教学臂 CiA402 驱动（PC Master）** | 完全开放关节环；可做 1 kHz 阻抗等；与 Elite 工位 **并存为第二 cell** | ★★★★★（研究向） | 驱动器+电机+RT Linux；**不是**改 CS66 |
| **P3** | EC 夹爪/快换（若选型支持） | 夹爪与 IO 同总线，减串口争用 | ★★★☆☆ | 换末端硬件 |
| **弱相关** | 「遥操更丝滑」 | **无效直接因果**；丝滑靠 servoj/TT + latest-wins | 禁止当主叙事 | — |
| **禁止** | PC EtherCAT 接管 CS66/EC616 关节 | 厂商未开放为通用 CiA402 从站给用户 PC；安全与保修风险 | 写了减分 | — |
| **禁止** | Gello 改 EtherCAT | 舵机无此接口 | — | — |

### 3.1 为什么 P0 对「当前」就说得通且有效

本仓已有：

- `force_torque`（HIK 串口）kind；  
- DCS 多 agent 录 `states/*.jsonl` + timeline 对齐；  
- 接触/装配类数据缺「与关节严格同步的高确定性 wrench」。

引入 EtherCAT F/T 后，效果可量化：

```text
对比 A) HIK 串口 F/T @ Windows 用户态轮询
     B) EtherCAT F/T @ 固定周期（如 1 kHz）+ DC 时间戳
指标：周期抖动 p95、与 arm-cs66.t_wall / RTSI 样本的 |Δt|、丢帧率、接触实验可重复性
```

简历不吹「发明了 EtherCAT」，而吹：**在具身采集栈中把工业实时总线外设纳入统一 sensor kind / episode 时间线**。

### 3.2 P1 工位 IO：补安全与可演示性

多机/真机 checklist 大量依赖示教器与人工；若工位有光栅、双手按钮、夹具到位：

- 经 EtherCAT IO 读入 → DCS gate（未到位禁止摇操/开录）；  
- episode 里留下数字量轨迹，便于复盘「为何中止」。

这与 [cs66-live-control-checklist.md](../../sensors-arm/docs/cs66-live-control-checklist.md) 的安全叙事同向，且不碰臂厂商协议。

### 3.3 P2 自研臂：最强简历，但是**新硬件线**

仅当团队真有 CiA402 驱动柜时再开。架构建议：

```text
RT Linux Master (IgH / SOEM / TwinCAT)
  → 共享内存 / Zenoh / ZMQ / ROS2
  → sensors 新 kind `arm_ecia402_write|read`
  → 仍可被 sensors-dcs 当普通 arm agent 录制
Elite Cell 保持现网不动
```

Windows 开发机只做 UI/DCS；**Master 不要绑在日常办公 Windows 上硬实时**（见 §5）。

---

## 4. 与「遥操顿挫 / episode 质量」的关系（防洗稿）

| 问题 | 主因（已有文档） | EtherCAT 能做什么 |
|------|------------------|-------------------|
| teleop_cmd ≈6 Hz | 阻塞 writeServoj 串行环 | **无**；已用 latest-wins 治 |
| filtered=0 | require 别名 | **无** |
| 臂读 ≈31 Hz | RTSI/锁争用 | **无直接**；至多「同机另网卡跑 EC 减 USB 争用」是弱间接 |
| 力–位不同步 | 串口 F/T vs 臂网口 | **有**（P0） |
| 工位 IO 不可观测 | 无总线 IO | **有**（P1） |

写简历时：**EtherCAT 段只讲外设确定性与同步；遥操段只讲 latest-wins / servoj。** 两段并列可以，因果合并不行。

---

## 5. 工程约束（本仓库落地前必须认清）

### 5.1 Master 放哪

| 选项 | 说明 | 建议 |
|------|------|------|
| Linux PREEMPT_RT + IgH/SOEM | 学术/自研常见 | P0/P2 首选独立小工控 |
| TwinCAT / 厂商主站 | 工业成熟 | 有预算时 |
| Windows 用户态 SOEM | 可通，难称硬实时 | 仅 PoC，勿写「硬实时」 |

当前操作者环境多为 **Windows 跑 DCS**：合理切分是 **「RT 侧盒跑 EtherCAT Master → 以太网/共享内存把 wrench/IO 喂给 sensors 驱动」**。

### 5.2 网卡与拓扑

- EtherCAT 建议 **独立网口**，不与 RealSense/公司网/CS Primary 混抢同一消费级交换机路径。  
- 与 CS66 `robot_ip`、Peer `advertise_ip` 网段隔离，避免广播风暴影响 RTSI。

### 5.3 软件落点（遵守生态规则）

| 层 | 做什么 |
|----|--------|
| `sensors` | `kind`：如 `ft_ethercat`、`io_ethercat`；只谈 PDO 映射与读采样 |
| `sensors-dcs` | agent YAML、录 `states/`、可选 gate |
| `sensors-arm` | 一般不直连 EC；除非 arm-only 站演示力控 |
| 不进 | `viz.py` 里堆协议；不进 embody 前端 |

---

## 6. 简历叙事（说得通的写法）

### 6.1 推荐条目（中文）— 外围总线向（匹配现有 Elite 工位）

> 在艾利特臂 **厂商 Ethernet 外控**（CS ExternalControl / EC TT）之外，规划并实现工位 **EtherCAT 外设层**：将六维力/分布式 IO 以确定性周期接入自研 `sensors` 驱动框架，与 DCS 多模态 episode 统一时间线，避免 USB/RS485 力觉与关节状态时钟漂移；主臂控制仍走 SDK，形成「运动控制与实时感知总线分离」的工业具身采集架构。

### 6.2 推荐条目（英文）

> Alongside vendor Ethernet teleoperation (Elite CS ExternalControl / EC TT), introduced an **EtherCAT peripheral plane** for deterministic F/T and cell I/O: new `sensors` device kinds stream PDO-timed wrench/IO into the DCS episode timeline, reducing clock skew vs serial force sensors while keeping the robot motion stack on the manufacturer controller—i.e. **separation of motion control and real-time sensing bus**.

### 6.3 若真有自研 CiA402 臂（更强，需硬件属实）

> Built a **CiA402 / EtherCAT master** stack on PREEMPT_RT for a research arm, exposing joints to the same DCS agent model used by Elite cells, enabling kHz-class synchronized logging for contact-rich policies.

### 6.4 面试追问 → 标准答法

| 追问 | 答 |
|------|-----|
| 为什么不把 CS66 改成 EtherCAT 直驱？ | 用户侧开放的是 ExternalControl/RTSI，不是通用驱动 PDO；直驱等于另做控制器，超出现网安全边界。 |
| 和 Modbus 夹爪比呢？ | 夹爪 50 Hz 级够用；EtherCAT 留给要 DC/多从站同步的力与 IO。 |
| 和 WebRTC 什么关系？ | WebRTC=浏览器视觉面；EtherCAT=现场实时总线。正交。 |
| 你写的「硬实时」依据？ | 若 Master 在 RT Linux 且给出周期抖动数据才写；Windows PoC 只写「工业以太网现场总线集成」。 |

### 6.5 禁止写进简历的夸大

- 「基于 EtherCAT 实现了 CS66 丝滑遥操」  
- 「自研 EtherCAT 协议栈」（若只用 SOEM/IgH 配置 PDO）  
- 「千赫兹全身控制」却只有串口 Gello + 示教器外控  
- 无抖动/对齐数据的「大幅提升同步精度」

---

## 7. 建议实施顺序（若开做）

| 阶段 | 内容 | 退出标准 |
|------|------|----------|
| 0. 选型 | 确认 F/T 或 IO 的 ESI/PDO；独立网卡；Master 主机 OS | 硬件到货清单 |
| A. 旁路 PoC | RT 盒读 EC F/T → UDP/ZMQ → 临时脚本 | 周期与抖动曲线 |
| B. `sensors` kind | 正式驱动 + dry_run + health | `sensors kinds` 可见；YAML 可配 |
| C. DCS 接入 | agent 录制 + 与 arm 对齐评估 | episode 含 wrench；match_dt 报告 |
| D. Gate（可选） | IO 不到位禁摇操/开录 | 与 checklist 文案一致 |
| E. 叙事固化 | 本页 + 测分表 | 简历与仓库一致 |

**相对优先级建议：** 摇操 latest-wins、后处理 require 别名、WebRTC 预览（演示）可先于 EtherCAT；**接触操作/力监督数据**成为产品目标时，把 P0 EtherCAT F/T 提前。

---

## 8. 与仓内文档关系

| 文档 | 关系 |
|------|------|
| [cs66-vs-ec616-control.md](../../sensors-arm/docs/cs66-vs-ec616-control.md) | 臂侧仍是厂商协议；EC 不替代 |
| [gello-arm-teleop.md](gello-arm-teleop.md) | 指令面；与 EtherCAT 正交 |
| [webrtc-embody-fit.md](webrtc-embody-fit.md) | 媒体面；与 EtherCAT 正交 |
| [episode-00003-quality-bottlenecks.md](episode-00003-quality-bottlenecks.md) | 质量问题勿甩锅 EtherCAT |
| `sensors` `force_torque` / `bus` | 现串口外设；EC 版应对标扩展 |

---

## 9. 最终判定

| 问题 | 判定 |
|------|------|
| 简历能不能写？ | **能**，写成「厂商臂 Ethernet 外控 + EtherCAT 实时外设层 / 统一采集时间线」 |
| 对当前系统有没有用？ | **有条件有用**：换/加 EC 力觉与工位 IO 时收益实在；**对现成 CS66 写臂频率无直接修复** |
| 是否现在必须做？ | **非必须**；无 EC 硬件时先不要空写代码。有接触数据需求再开 P0 |
| 最契合落点 | **`sensors` 新 kind（F/T、IO）→ DCS 录制与 gate**；自研 CiA402 为可选第二产品线 |
| 最危险踩坑 | 假装用 EtherCAT「接管艾利特关节」 |

**一句话：** EtherCAT 亮点应落在 **工业实时感知与 IO 总线进入具身采集栈**，与 Elite 运动栈 **并列分层**；分层讲清楚，简历与工程才同时成立。
