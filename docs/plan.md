# sensors-dcs 开发计划

## 1. 目标与边界

构建独立的 **数据采集系统（DCS）**：在不侵入 `hik-sensors` 核心驱动的前提下，对 `SensorManager` / 各 kind 驱动做上层封装（Agent），实现 **传感器常开、按需录制、流式写盘、低频整帧可视化推送**。

| 约束（来自 README） | 落地方式 |
|---------------------|----------|
| 单一 YAML 启动 | `sensors-dcs` 入口只读一份运行配置（频率、传感器清单、保存路径、可视化端口等） |
| 基于 sensors 再封装 | 本仓库实现 Agent / Runtime；`sensors` 仅作依赖，默认不改其源码 |
| 低频整帧推前端 | 独立可视化采样线程/进程，以 10~20Hz 向固定端口推送完整一帧 |
| 参考但优于 `save_data.py` | 去掉 episode 级内存堆积 + 结束时 `deepcopy`/`pickle`；改为边录边写 |

参考实现与已知问题（`hww/hik_gello/save_data.py`）：

- 录制期间把 color/depth 原图 `deepcopy` 进 `data_buffer`，episode 结束再统一 JPEG/PNG + pickle → 内存峰值高、停录后卡顿。
- 可视化靠 SHM 拼图，与采集线程耦合，不利于对接现有 `sensors-view` / WebSocket。
- 启停依赖 `collection_flag` SHM，可保留兼容，但主路径改为 YAML + HTTP/键盘信号。

依赖关系：

```text
sensors-dcs/
  sensors -> ~/autodl-tmp/sensors   # 软链接；sys.path 优先 ./sensors/src
             →  可选对接 sensors-view（消费可视化端口）
```

---

## 2. 目标架构

采用架构设计文档中的 **生产者-消费者** 模型：

```text
YAML 启动
   ↓
[Sensor Agents 常驻读取] → [环形缓冲 / 最新帧槽] → [录制消费者] → 磁盘（流式、分片）
                                    ↓
                           [可视化采样 10~20Hz] → WebSocket/HTTP → 浏览器
                                    ↑
                         [开始/结束信号：HTTP / 键盘 / 兼容 SHM flag]
```

核心原则：

1. **传感器常开**：Agent 不随 episode 启停；仅录制消费者开关写盘。
2. **缓冲策略**：图像默认长度 1~3（最新帧）；关节/力觉等低频状态可短预录；禁止按整 episode 堆原图。
3. **流式写盘**：录制线程即时压缩（JPEG/PNG）并异步写入；支持按时间/大小分片。
4. **进程隔离**：可视化采样与推送独立于采集/录制，避免拖垮采集频率。
5. **统一时钟**：所有样本打 `time.time()`（或单调钟 + 墙钟映射），便于多传感器对齐。

---

## 3. 仓库与包结构（建议）

```text
sensors-dcs/
  plan.md
  README.md
  pyproject.toml
  configs/
    collect.example.yaml          # DCS 运行配置（本计划重点）
  src/sensors_dcs/
    __init__.py
    cli.py                        # `sensors-dcs run -c configs/xxx.yaml`
    config.py                     # 加载/校验 YAML
    frame.py                      # Frame / Sample 统一数据结构
    buffer/
      ring.py                     # 定长 deque / 最新帧槽
      latest.py
    agents/
      base.py                     # Agent 协议：start/stop/read_loop
      factory.py                  # 根据 YAML + SensorManager 创建 Agent
      gello_agent.py              # gello leader 封装示例
      arm_agent.py
      camera_agent.py
      ft_agent.py
      ...
    runtime/
      orchestrator.py             # 主进程协调：启停、背压、优雅退出
      record_controller.py        # 开始/结束信号 → 录制会话
      recorder.py                 # 从缓冲取数据流式写盘
      writer_binary.py            # 自定义二进制 + 索引（首期）
    viz/
      sampler.py                  # 固定频率取最新整帧
      publisher.py                # WebSocket 广播（或 ZMQ→WS 桥）
      schema.py                   # 推送帧协议（JPEG + JSON 状态）
    control/
      http_api.py                 # FastAPI：/record/start|stop|status
      keyboard.py                 # 可选 pynput
      shm_flag.py                 # 可选兼容 MegaCollect collection_flag
  tests/
    test_config.py
    test_ring_buffer.py
    test_recorder_dry_run.py
    test_viz_schema.py
```

原则：**Agent 只调用 `sensors` 的 `open/read/close`（或 Manager 长期 open 后循环 read）**；新增能力优先落在本仓库。

---

## 4. YAML 配置契约（启动唯一入口）

示例字段（实现时以 schema 校验为准）：

```yaml
version: 1
site: lab-default

# 可引用或内联 sensors 设备清单
sensors_config: sensors/configs/default.yaml   # 经仓库根目录 sensors/ 软链接

runtime:
  control_host: "0.0.0.0"
  control_port: 7010          # 录制控制 HTTP
  viz_host: "0.0.0.0"
  viz_port: 7011              # 可视化推送（WebSocket）
  viz_hz: 15
  clock: wall                 # wall | monotonic

agents:
  - id: gello-leader
    type: gello              # → GelloAgent
    sensor_id: gello-leader  # 对应 sensors YAML device id
    hz: 125
    buffer_frames: 64        # 状态可短预录
  - id: arm-follower
    type: arm
    sensor_id: arm-follower
    hz: 125
    buffer_frames: 64
  - id: rs-left
    type: camera
    sensor_id: rs-left
    hz: 15
    buffer_frames: 2         # 图像不预录整段
    encode: jpeg
    jpeg_quality: 90

record:
  enabled_on_start: false
  save_dir: ./data
  format: binary_v1          # 自定义二进制 + sidecar index
  shard:
    max_seconds: 60
    max_bytes_mb: 500
  pre_roll:
    enabled: false
    seconds: 0.5
    include_kinds: [arm, gello, ft]   # 不含大图
  backpressure: drop_oldest  # 或 block_briefly

viz:
  include: [rs-left, rs-right, rs-middle, arm-follower, gello-leader]
  image_max_width: 640
  jpeg_quality: 70
```

启动：

```bash
sensors-dcs run -c configs/collect.example.yaml
```

---

## 5. 分阶段里程碑

### Phase 0 — 脚手架与契约（0.5~1 天）

- [ ] 初始化 `pyproject.toml`，经 `./sensors` 软链接引用 hik-sensors；依赖 `fastapi`/`uvicorn`/`websockets`、`opencv-python-headless`、`numpy`、`pyyaml`、`pydantic`
- [ ] 定义 `Frame`/`Sample`（`sensor_id`, `kind`, `t_wall`, `t_mono`, `payload`）
- [ ] YAML schema（Pydantic）+ 示例配置 + CLI `run`
- [ ] 文档：与 `sensors` / `sensors-view` 的职责切分写进本仓库 README

**验收**：无硬件时 `dry_run` 能解析配置并启动空 Orchestrator，打印拓扑。

### Phase 1 — Agent 层与常驻读取（1~2 天）

- [ ] `BaseAgent`：独立线程按目标 `hz` 调用 `sensor.read()`，写入 ring / latest slot
- [ ] 首批 Agent：`CameraAgent`（realsense）、`ArmAgent`、`GelloAgent`；其余 kind 先 generic 包装
- [ ] 工厂：`SensorManager.from_yaml` 打开设备 → 绑定 Agent（**不改 sensors 源码**）
- [ ] 统一时间戳与基础健康检查（读失败计数、超时）

**验收**：真机或 mock 下，各 Agent 稳定产出带时间戳的样本；停 Agent 不关进程级资源泄漏（close 路径正确）。

### Phase 2 — 环形缓冲、录制控制与流式写盘（2~3 天）

- [ ] Ring buffer：定长 `deque(maxlen=N)`；图像默认 N=2；状态可更大
- [ ] `RecordController`：HTTP `POST /record/start|stop` + 可选键盘；可选 SHM flag 兼容
- [ ] `Recorder`：开始时建会话目录与索引；循环消费队列；图像即时 `cv2.imencode`；状态 append 写
- [ ] 异步写：`queue.Queue` 解耦；队列满时按 `backpressure` 丢旧帧并打点日志
- [ ] 分片：达 `max_seconds` / `max_bytes` 轮转文件
- [ ] Episode 元数据：`manifest.json`（配置快照、设备列表、起止时间、valid 标记）

对比 `save_data.py` 的明确改进：

| 旧行为 | 新行为 |
|--------|--------|
| episode 内堆原图 | 边读边压边写 |
| 结束时 `deepcopy` 整包 | 无整包拷贝 |
| 单文件巨大 pickle | 分片二进制 + 索引 |
| 仅录制时开相机线程 | 传感器常开，录制只开消费者 |

**验收**：连续录制数分钟，内存平稳；停录后秒级完成收尾；磁盘上可读回时间戳与抽帧。

### Phase 3 — 可视化推送（1~2 天）

- [ ] `VizSampler`：按 `viz_hz` 从 latest slot 取「完整一帧」（多相机 JPEG + 关节/力觉 JSON）
- [ ] `VizPublisher`：WebSocket 广播到 `viz_port`
- [ ] 推送协议文档（二进制帧或 JSON+base64；优先二进制 JPEG 降低开销）
- [ ] 与 `sensors-view` 对接方案：新增订阅客户端或独立轻量预览页（二选一，优先最小可用预览页）

**验收**：浏览器 15Hz 左右刷新图像与状态；采集/录制帧率不明显下降（对比关闭 viz）。

### Phase 4 — 预录、背压、运维与硬化（1~2 天）

- [ ] 可选 pre-roll（仅低频状态）
- [ ] 资源监控日志：队列深度、丢帧率、写盘 MB/s、RSS
- [ ] 优雅退出：SIGINT → 停录 → flush → close sensors
- [ ] dry-run / 无硬件 CI：mock Agent + 单元测试缓冲与 writer
- [ ] 性能对照：同场景对比旧 `save_data.py` 的峰值内存与停录延迟

**验收**：长稳跑（≥30min）无泄漏；磁盘跟不上时有可控丢帧而非 OOM。

### Phase 5 — 扩展 Agent 与产品化（按需）

- [ ] `FtAgent` / `TactileAgent` / `GripperAgent`
- [ ] 多机台配置模板、保存路径策略（按日期/episode index）
- [ ] 可选 ZMQ 桥（若现场已有 ZMQ 生态）
- [ ] 读回工具：`sensors-dcs dump-index` / 导出训练友好格式（后续再定，不阻塞主路径）

---

## 6. 模块职责与接口草图

### 6.1 Agent

```text
start() → 后台循环：
  sample = sensor.read()
  buffer.push(Frame(...))
stop() / close()
```

- `GelloAgent`：封装 `kind=gello`，对外只暴露关节/夹爪状态帧。
- 禁止在 Agent 内做重编码或写盘。

### 6.2 RecordController

- 输入：HTTP / 键盘 / SHM
- 输出：`RecordingSession`（路径、index、valid、开始墙钟）
- 开始：挂接消费者到各 buffer 的「实时出口」；若启用 pre-roll，先刷入历史状态
- 结束：停消费者、写 manifest、递增 episode index

### 6.3 Viz

- 输入：各 Agent latest frame
- 输出：`VizFrame { t, images: {id: jpeg_bytes}, states: {...} }`
- 独立线程；发送失败不影响采集

### 6.4 对 sensors 的修改策略

- **默认零修改**。
- 仅当发现驱动层协议缺口（例如缺少「长期 open 后非阻塞 read」）时，再向上游提最小 PR；本仓库先用适配层 workaround。

---

## 7. 技术选型（与架构文档对齐）

| 模块 | 方案 |
|------|------|
| 传感器读取 | 本仓库 Agent 线程 + `./sensors`（hik-sensors）驱动 |
| 内存缓冲 | `collections.deque(maxlen=...)` + 最新帧槽 |
| 录制写盘 | 自定义二进制 + 索引；队列异步写；图像即时 JPEG |
| 控制信号 | FastAPI HTTP 为主；键盘可选；SHM flag 兼容旧流程 |
| 可视化 | WebSocket；推送压缩图 + JSON 状态 |
| 前端 | 最小预览页，或扩展 `sensors-view` 订阅 viz 端口 |

---

## 8. 测试与验收清单

**功能**

- [ ] 一份 YAML 即可启动全链路
- [ ] 传感器常开；多次 start/stop 录制互不影响设备句柄
- [ ] 可视化端口可持续收到完整一帧
- [ ] 录制文件可按时间戳对齐多路传感器

**性能（相对 save_data.py）**

- [ ] 录制中 RSS 不随 episode 时长近似线性涨到 OOM
- [ ] 停录到文件关闭 < 数秒（视分片 flush）
- [ ] 可视化开关前后，采集目标 hz 偏差可接受（记录基线）

**工程**

- [ ] 单元测试覆盖 buffer / config / writer（dry-run）
- [ ] README：安装、YAML 字段、控制 API、viz 协议
- [ ] 明确声明：勿在业务需求下直接改 `sensors` 驱动

---

## 9. 建议实施顺序（执行清单）

1. Phase 0 脚手架 + YAML 契约  
2. Phase 1 `CameraAgent` + `GelloAgent` + `ArmAgent` 常驻读  
3. Phase 2 流式 Recorder + HTTP 启停（替换 save_data 主路径）  
4. Phase 3 Viz WebSocket 整帧推送  
5. Phase 4 背压 / 预录 / 长稳与对比基准  
6. Phase 5 其余 kind Agent 与产品化项  

当前仓库仅有架构文档与 README，**下一步应从 Phase 0 落地包结构与 `collect.example.yaml` 开始实现**。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 多路 1080p 压满 CPU/磁盘 | 录制与 viz 使用不同 JPEG 质量/分辨率；图像不预录 |
| `sensor.read()` 阻塞拖垮其它路 | 每传感器独立 Agent 线程；超时与健康计数 |
| 改 sensors 影响其它项目 | 适配层优先；上游变更走独立 PR 与评审 |
| 与 sensors-view 协议分叉 | 尽早固定 `VizFrame` schema 并版本化（`viz_schema_version`） |
| 旧 SHM 流程并存混乱 | SHM 兼容模块可选、默认关闭；文档标明废弃路径 |

---

## 11. 非目标（本期不做）

- 不在本仓库重写 RealSense / Dynamixel / ZMQ 驱动  
- 不把训练数据集格式（LeRobot 等）作为首期交付  
- 不做复杂分布式多机采集编排（仅保留旧 remote index 兼容的调研位）  
- 不以 Vue/React 大前端为阻塞项；先保证推送协议与最小预览
