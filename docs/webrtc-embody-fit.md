# WebRTC × 具身多仓（autodl-tmp）契合点调研

日期：2026-10-31  
范围：`sensors` / `sensors-view` / `sensors-dcs` / `sensors-arm` / `embody_model_eval` / 多机 `station.*`  
目的：① 简历说得通的技术亮点；② 对**当前系统真有效果**的落点；③ 明确**不该**用 WebRTC 硬蹭的地方。

---

## 0. 结论先行（可对外说的一句话）

> 在具身采集/遥操栈里，**控制面继续走现有 HTTP + WebSocket JSON**；**视觉反馈面**在「跨网/多路预览/多机主站汇聚」场景引入 **WebRTC（H.264/VP8 + 拥塞控制）**，把今天「Base64 JPEG over WS」的带宽与延迟瓶颈拆开——这是业界遥操常见分层，也是本仓唯一说得通且能量化收益的结合点。

**不要**指望 WebRTC 修好：摇操写臂稀（已用 latest-wins 治）、filter require 别名、IK/`servoj`、录盘 dropped。那些是别的问题。

---

## 1. 现状：媒体与控制怎么走

### 1.1 视觉预览（今天的痛点所在）

```text
RealSense / 驱动 → agent ring（可含全分辨率 jpeg_b64 写盘）
                 → Orchestrator._frame_for_viz：只保留 jpeg_b64_preview
                 → VizHub WebSocket /ws 按 runtime.viz_hz 广播（多数站 YAML 为 5 Hz）
                 → 浏览器 <img src="data:image/jpeg;base64,...">
```

特点：

| 项 | 现状 |
|----|------|
| 传输 | **TCP WebSocket + 整帧 JPEG + Base64**（约 +33% 膨胀） |
| 节拍 | `viz_hz` 常 **5**，与相机 30 Hz / 采集写盘解耦 |
| 多路 | 一页 2×2～多相机，每路一帧 JPEG |
| 控制/状态 | 同一 WS 上还有 joints、录制状态、摇操 status 等 JSON |
| 嵌入 | `embody_model_eval` iframe → `sensors-view`；DCS 亦可 iframe 探针 |

局域网、单人盯屏时「能用」；一旦 **跨 VLAN / VPN / 公网演示 / 主站同时看多从站画面**，JPEG×N×Base64 会先打满带宽，再拖垮同一条 WS 上的控制消息（队头阻塞）。

### 1.2 控制与站间（WebRTC 不应替代）

| 通道 | 用途 | 判定 |
|------|------|------|
| HTTP REST | Arm/摇操/录制/后处理 | 保留 |
| `/ws` JSON | 状态、低带宽 telemetry | 保留（可与媒体分流） |
| `station` Peer（TCP/WS JSON） | 主从 barrier、ping RTT | 保留；**明确不做**采集热路径推流（见 [station-multi-machine.md](station-multi-machine.md)） |
| 录盘 JPG/JSONL | 训练数据 | 与预览无关，**不要**改成 WebRTC |

多机文档已拍板：**一期不推流跨站落盘**；从站本地预览允许。WebRTC 若做，应是 **预览/遥操 overlay**，不是 record pipeline。

### 1.3 业界对照（简历「说得通」的依据）

遥操/远程监控常见分层（论文与工程一致）：

- **视频**：WebRTC / RTP（UDP、拥塞控制、浏览器硬解 H.264）  
- **指令**：WebSocket / MQTT / 专用可靠通道  

公开材料中常见量级（环境相关，引用时需自测）：相对 MJPEG/HTTP，WebRTC 端到端常可再降约 **50–150 ms** 量级；硬件 H.264 在带宽与稳定性上优于纯 CPU VP8。本仓价值不在复述论文，而在：**把已有 RealSense→浏览器链路换成可测的 WebRTC 媒体面**。

---

## 2. 契合点排序（对当前系统）

| 优先级 | 结合点 | 对系统的真实效果 | 简历可讲性 |
|--------|--------|------------------|------------|
| **P0** | **DCS / sensors-view 浏览器预览：JPEG@WS → WebRTC 视频轨** | 同带宽下更高预览 FPS、更低 glass-to-glass；控制 JSON 与媒体解耦，减轻 WS 卡顿 | ★★★★★ 完整「媒体面/控制面分离」叙事 |
| **P1** | **多机：从站编码 → 主站/办公网浏览器订阅（可选 SFU）** | 主站不靠「每个从站开一整页高带宽 JPEG」；演示/监工可行；仍不碰落盘热路径 | ★★★★☆ 分布式工位 + 实时汇聚 |
| **P2** | **远程遥操 overlay（人在办公网，臂在实验室）** | 视觉闭环延迟下降后，人手遥操更稳；**指令仍走现有 teleop API / latest-wins** | ★★★★☆ 但需安全闸（Arm/急停/只读预览默认） |
| **P3** | DataChannel 传低频 telemetry 或「预览专用」事件 | 次要；机器人命令仍建议可靠有序通道 | ★★☆☆☆ 易写成空话，慎作为主叙事 |
| **不做** | 用 WebRTC 替换 episode 录制 / parquet / filter | 无益，增加复杂度 | 简历写了反而减分 |
| **不做** | 用 WebRTC「加速」IK、servoj、对齐 Hz | 风马牛不相及 | 禁止 |

### 2.1 为什么 P0 对「当前」就有效

今日配置下预览常被 `viz_hz=5` 人为限速——部分是怕打爆 WS。引入 WebRTC 后可以：

1. **预览目标拉到 15–30 FPS**（与相机同量级），而不把 Base64 JPEG 塞进控制 WS；  
2. 三路 720p 时带宽从「每帧独立 JPEG」改为 **时间压缩 + ABR**；  
3. 采集写盘路径不变（全分辨率 JPG 仍落 `cameras/`），避免污染数据集。

量化验收（写进简历/答辩用）：

```text
同机同网：glass-to-glass p50/p95、预览 FPS、下行 Mbps、控制 WS 消息延迟
对比：A) jpeg_b64_preview@WS@viz_hz   B) WebRTC H.264
通过线：B 的预览 FPS↑ 且控制 RTT/丢消息不明显变差；跨 Wi‑Fi/VPN 时 B 不先卡死页面
```

### 2.2 P1 与多机定案如何兼容

[station-multi-machine.md](station-multi-machine.md)：**不做 Peer 热路径推流、不做跨站后处理拉流**。  
WebRTC 方案应写成：

- **可选能力**：`station.preview_webrtc: true` 时，从站只推 **预览轨** 到主站浏览器或轻量 SFU；  
- **默认关闭** → 单机路径零行为变化（符合「可空转 Peer」精神）；  
- 落盘仍各站本地；WebRTC 帧 **不入** `RecordController`。

这样既不推翻既有架构，又补上「主站监多路预览」的产品缺口。

### 2.3 P2 与摇操改造的关系

摇操卡顿主因是 **写臂设定点稀**（已做 latest-wins），不是视频协议。  
WebRTC 的增益是：**人眼反馈更快** → 在写频已密的前提下减少「看晚了多拧一圈」。  
叙事顺序必须正确：**先控频，再视频**；简历里可写「闭环：100 Hz latest-wins 指令 + WebRTC 视觉反馈」。

---

## 3. 简历叙事（说得通的写法）

### 3.1 推荐条目（中文）

> 在具身数据采集系统中设计并实现 **控制面与媒体面分离**：关节/录制状态仍走 WebSocket JSON，多路 RealSense 预览升级为 **WebRTC 实时视频**（H.264、拥塞控制），在跨网与多机监看场景降低预览延迟与带宽，并与 CS66 遥操 latest-wins 写线程组成低时延人机闭环；用 glass-to-glass 与 Mbps 指标对比旧 Base64-JPEG 方案。

### 3.2 推荐条目（英文）

> Separated **control and media planes** in an embodied data-collection stack: robot/telemetry over WebSocket JSON; multi-camera previews over **WebRTC** (H.264 + congestion control). Cut preview latency/bandwidth vs Base64-JPEG-on-WS for LAN/VPN monitoring, and paired with a **100 Hz latest-wins** teleop writer for closed-loop operator feedback—validated with glass-to-glass and bitrate metrics.

### 3.3 面试可能追问 → 标准答法

| 追问 | 答 |
|------|-----|
| 为什么不用 WebRTC 传关节角？ | 命令需要可靠、有序、易审计；WS/HTTP + 服务端写线程更合适。DataChannel 不可靠模式不适合急停语义。 |
| 和录数据集什么关系？ | 无关。录盘仍是本地 JPG/JSONL；WebRTC 只服务人看的预览。 |
| 和 ROS/Foxglove 比？ | 我们是浏览器直连工位进程，无强制 ROS；WebRTC 是浏览器原生低时延选项。 |
| 难点是什么？ | 信令与 NAT（局域网可简化）；工控机编码负载（优先硬编）；与现有 `viz_hz`/iframe 嵌入兼容；默认关闭保证单机回归。 |

### 3.4 禁止写进简历的夸大

- 「用 WebRTC 解决了机械臂抖动」（假）  
- 「WebRTC 替代了整个 DCS」（假）  
- 「自研拥塞控制算法」却只接了浏览器默认 PeerConnection（虚）  
- 无前后对比数据的「大幅降低延迟」（空）

---

## 4. 推荐技术切片（落地时最小闭环）

```text
[工位 PC]
  RealSense 彩色帧
    → (可选) NVENC/软编 H.264
    → WebRTC PeerConnection 视频轨
  信令：复用现有 FastAPI（SDP/ICE over WS 或 HTTP）
  控制：原 /ws JSON + /api/arm/* 不变

[浏览器]
  RTCPeerConnection → <video>
  摇操/录制 UI 不变
```

依赖取向（工程现实）：

| 方案 | 适用 | 备注 |
|------|------|------|
| `aiortc`（Python） | 快速原型、软编 | CPU 吃紧时 720p×3 慎用 |
| GStreamer `webrtcbin` / mediasoup 推流 | 硬编、多订阅 | 运维重，适合 P1 SFU |
| 仅局域网 host candidate | 无 TURN | 先做通再谈公网 |

**iframe**：`embody_model_eval` 嵌 sensors-view 时，注意跨源与 `getUserMedia` 无关（我们是 **收流不是采麦**）；主要是自动播放策略与 HTTPS/安全上下文——桌面/localhost 通常可过。

---

## 5. 明确非目标（避免范围膨胀）

1. 不改变 `episode_*/cameras` 录制格式与后处理 timeline。  
2. 不把 Peer barrier/录制 COMMIT 改到 DataChannel。  
3. 不在无硬编机器上强上 1080p×3@30 作为默认。  
4. 不做「WebRTC 替代 sensors 驱动层」。  
5. 公网演示前未就绪 TURN 时，文档标明 **仅 LAN/VPN**。

---

## 6. 建议实施顺序（若开做）

| 阶段 | 内容 | 退出标准 |
|------|------|----------|
| A. 测量基线 | 现网 JPEG@WS glass-to-glass、Mbps、`viz_hz` | 有数字，可写进对比表 |
| B. 单路 PoC | DCS 单相机 WebRTC + 旧预览开关并存 | 功能开关；单机默认仍 JPEG |
| C. 多路 | 2–3 路；编码预算与降分辨率策略 | 预览 FPS≥15，控制 WS 无堆积 |
| D. 多机可选 | 从站预览轨 → 主站浏览器 | 符合 station「默认可空转」 |
| E. 叙事固化 | 本页 + 测分表格进 README/答辩 | 简历条目与仓库一致 |

工作量粗估：B≈1–2 周（一人，含坑）；C–D 视硬编与 SFU 再定。

---

## 7. 与仓内其它文档的关系

| 文档 | 关系 |
|------|------|
| [station-multi-machine.md](station-multi-machine.md) | 不冲突：WebRTC=可选预览，非落盘热路径 |
| [gello-arm-teleop.md](gello-arm-teleop.md) | 指令面；WebRTC 只补视觉闭环 |
| [episode-00003-quality-bottlenecks.md](episode-00003-quality-bottlenecks.md) | 质量差主因非预览协议；勿混为一谈 |
| [cs66-remote-teleop-smooth.md](../../sensors-arm/docs/cs66-remote-teleop-smooth.md) | 臂侧写路径；可与 P2 组成「指令+视频」故事 |

---

## 8. 最终判定

| 问题 | 判定 |
|------|------|
| 简历能不能写？ | **能**，按 §3 写「控制/媒体分离 + WebRTC 预览 + 指标对比」 |
| 对当前系统有没有用？ | **有**，集中在 **浏览器多路预览与跨网监看**；对录盘质量与写臂稀 **无直接修复** |
| 是否现在必须做？ | **非阻塞**；摇操 latest-wins、后处理 require 别名优先。WebRTC 作为下一阶段体验/演示增强 |
| 最契合落点 | **`sensors-dcs`（及可选 `sensors-view`）预览面**；embody 仅作 iframe 宿主受益 |

**一句话决策：** 把 WebRTC 定位成「具身采集系统的实时视觉面升级」，不要定位成「整个机器人协议栈重写」——前者简历与工程双赢，后者两边都亏。
