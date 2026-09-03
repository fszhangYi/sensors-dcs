# Save Data 深度对比：hik_gello 基线 vs sensors-dcs

> 基线：`hww/hik_gello/save_data.py` + `data_postprocess.py`（及 `gello/zmq_core/robot_node.py` 的 `_command_state`）  
> 对照：`sensors-dcs` 在线录制 `src/sensors_dcs/record.py` + agent 环缓冲，以及离线 `export-timeline` / `filter-timeline` / `export-hik-dataset`  
> 日期：2026-09-03  
> 评价原则：**以原项目意图与产物语义为基线**；DCS 的优势/不足相对该基线陈述。实现错误用 **【错误】** 标出；设计取舍用 **【差距】**；基线自身问题单列，不计入 DCS「错误」。

---

## 0. 一句话结论

| 维度 | 结论 |
|------|------|
| 训练目录语义（`metadata` / `steps` / `rgb_*`） | DCS **刻意对齐** `data_postprocess` 写出结果；`actions` 同样由观测差分推导，**不是**指令轨。 |
| 在线采集形态 | 基线 = **整段内存缓冲 → 停采后 pickle**；DCS = **边采边落盘（per-stream jsonl/jpg）**，对齐推到离线。 |
| 相对基线最大缺口 | ① 无等价的高频 `action_state`（ZMQ `_command_state`）审计轨；② 相机缺 RealSense **硬件时间戳**；③ 深度默认链路与基线「必录 + 对齐写 `d_*`」不完全同构。 |
| 必须修的实现问题 | 见 §5 **【错误】**：写失败帧被丢、缺 JPEG 仍计 `written`、无「作废本局」等价物等。 |

---

## 1. 端到端对照

### 1.1 基线（hik_gello）

```text
ZMQ robot (get_joint_state / get_command_state)
Realsense 多路 get_camera_frame
        │
        ▼  DataCollector 多线程 ~200Hz 臂/指令 + 相机全速
  data_buffer 内存列表（含 color/depth ndarray）
        │ stop → deepcopy → 异步 JPEG/PNG encode → pickle.dump
        ▼
  {index}_data.pkl
        │ data_postprocess.parse_pickle_data
        ▼  以 D435 color_timestamp 为轴、~5Hz 采样、近邻对齐
  metadata.json + steps.json + rgb_* + d_*（深度对齐到 RGB）
```

关键代码：

```126:174:/root/autodl-tmp/hww/hik_gello/save_data.py
    def collect_robot_data(self):
        ...
                robot_state = self._robot.get_joint_state()
                ...
                self.data_buffer["robot_state"].append({"timestamp": timestamp, "robot_state": robot_state})
            time.sleep(0.005)

    def collect_action_data(self):
        ...
                action_state = self._robot.get_command_state()
                ...
                self.data_buffer["action_state"].append({"timestamp": action_state[-1], "action_state": action_state[:-1]})

    def collect_camera_data(self, camera_index):
        ...
                    self.data_buffer[f"camera_{serial_no}"]["frames"].append({
                        "systems_timestamp": timestamp,
                        "color_timestamp": color_timestamp,
                        "depth_timestamp": depth_timestamp,
                        "color": copy.deepcopy(color_array),
                        "depth": copy.deepcopy(depth_array)
                    })
```

```55:71:/root/autodl-tmp/hww/hik_gello/gello/zmq_core/robot_node.py
                elif method == "command_joint_state":
                    timestamp = time.time() * 1000
                    with self._lock:
                        self._command_state = args["joint_state"].tolist()
                        self._command_state.append(timestamp)
                    ...
                elif method == "get_command_state":
                    with self._lock:
                        result = copy.deepcopy(self._command_state)
```

### 1.2 当前（sensors-dcs）

```text
各 Agent（hz 可配）→ ring.latest
        │ RecordController._sampler_loop (~2ms 轮询)
        ▼  有界 queue（满则 drop_oldest）
  _writer_loop 流式写盘
        ▼
  episode_NNNNN/
    manifest.json
    states/<agent>.jsonl
    cameras/<agent>/{seq}.jpg [+ _depth.png]
        │ export-timeline → filter → materialize
        ▼
  export/hik_dataset/（布局对齐 postprocess）
```

关键代码：

```326:355:/root/autodl-tmp/sensors-dcs/src/sensors_dcs/record.py
    def _sampler_loop(self) -> None:
        ...
                fr = agent.ring.latest.get()
                if fr is None or fr.error:
                    continue
                ...
                # queue Full → drop_oldest
```

---

## 2. 分项对比表（基线 → DCS）

| 能力 | 基线 save_data | DCS 现状 | 评价 |
|------|----------------|----------|------|
| 关节观测 | `robot_state` ~200Hz，整向量（含夹爪维） | `arm_read` / `gello` jsonl，按 agent `hz` | **优势**：通道拆分、标定字段更全；**不足**：默认频率常低于 200Hz，依赖配置 |
| 指令轨 | `action_state` ← ZMQ 上次 `command_joint_state` + 下发时刻 | 可选 `arm_write` / `gripper_write` jsonl | **【差距】** 非同一语义；且失败帧不落盘（见错误） |
| 相机 RGB | 内存 raw → 停采 JPEG q=90 | 采集侧即 JPEG q=90 落盘 | **优势**：峰值内存小；质量相当 |
| 相机深度 | **始终**缓冲 depth；postprocess `align_depth_to_rgb` 写 `d_*` | 依赖 `enable_depth`；导出时 **原样拷贝** PNG，无 postprocess 那套 scale/对齐 | **【差距】** 默认是否开深度看 YAML；对齐语义不等价 |
| 时间戳 | 系统 ms + **RS color/depth HW ts**；对齐主轴用 HW color ts | 仅 `t_wall≈time.time()`（驱动读前打的墙钟） | **【错误/缺口】** 无法复现基线时间轴策略 |
| 多流同步 | 停采后一次性近邻对齐 | 在线不同步；离线 asof/nearest | **优势**：可复跑过滤；**不足**：在线不可预览「已对齐步」 |
| Episode 落盘 | 单文件 pickle | 目录树 + manifest | **优势**：可部分读、可增量排查 |
| 作废本局 | `collection_flag==2` → `valid=0` 仍写 pkl | stop 成功则 `manifest.valid=true`；无「标记作废」API | **【错误/缺口】** |
| IO 门控 | 采时 `Y005=1`，读 `X009` | 无对应硬件 IO 联锁 | **【差距】**（若现场依赖该灯/门，DCS 未接） |
| 预览 HUD | shared_memory 拼图 + robot/action 文字 | Web viz + 可选 grid video | **优势**：浏览器；**不足**：默认预览不叠 `action_state` |
| 峰值内存 | 全 episode 图像 deepcopy | 小 ring + 流式写 | **DCS 显著优势** |
| 停采落盘可靠性 | daemon 线程 encode+pickle，主流程不 join | stop 会 drain queue（有 timeout） | **DCS 优势**（基线有丢尾风险） |

---

## 3. 优势（相对基线）

1. **内存与时长**：长 episode / 多相机时，基线易因 `copy.deepcopy` 帧列表爆内存；DCS 边写边丢环上旧帧，适合连续采集。  
2. **可运维落盘格式**：jsonl + jpg 可直接 `head`/`ffprobe`；manifest 在 start 即含内参，crash 后仍有线索。  
3. **通道模块化**：gello / arm / gripper / camera 独立 hz 与配置；写通道与读通道可分 agent，便于只开相机或只开臂。  
4. **离线可复现管线**：filter 阈值、相机名 map、materialize 可反复跑；基线是「一个 pkl → 一次脚本」。  
5. **训练 `steps.actions` 语义对齐 postprocess**：由观测关节 FK 相对位姿 + 下一帧夹爪推导（见 `hik-dataset-actions.md`），与基线一致——**两边都没有把指令轨写进训练 actions**。  
6. **传感器常开、录制只开关写盘**：比基线每局依赖采集进程内缓冲更利于 UI 启停。

---

## 4. 不足（相对基线意图，非必改代码）

1. **无「控制节点缓存指令」一等公民**：基线 `action_state` 与摇操/ZMQ 同进程同源；DCS 的 write-agent 是旁路审计，频率、字段、失败语义都不同。  
2. **时间对齐信息变弱**：缺 HW 帧时间戳后，多相机 + 臂的近邻对齐只能靠墙钟，抖动与调度延迟会直接进误差。  
3. **深度产物默认不如基线「开箱即有」**：`sensors_gello*.yaml` 未必开 `enable_depth`；即便开启，导出侧不做 wrist/非 wrist 的 `depth_scale` + `align_depth_to_rgb`。  
4. **无硬件采集指示灯/门控 IO**：现场若依赖 `Y005`/`X009`，需另接。  
5. **作废 episode 工作流缺失**：操作员误采只能事后删目录或靠 filter，不能像基线标 `valid=0` 仍归档。  
6. **文档滞后**：`docs/预检报告.md` 仍写「sampler 跳过 write」「深度落不了」——与代码不符，易误判能力（文档债，不是运行时 bug）。

---

## 5. 【错误】当前实现问题（相对基线或自洽性）

下列项视为 **需要修或明确接受为缺陷**，不是「有意简化」。

### 【错误 E1】相机时间戳丢失硬件源 — 对齐保真度低于基线

- **基线**：每帧存 `color_timestamp` / `depth_timestamp`（`pyrealsense2`），postprocess 以主相机 **HW color ts** 为采样轴，其它相机用 `systems_timestamp` 近邻。  
- **DCS**：`sensors/.../realsense.py` 的 `ts = time.time()`（在 `wait_for_frames` 之前/旁路墙钟），**未**调用 `color_frame.get_timestamp()`；record 只持久化 `t_wall`。  
- **影响**：多相机同步、与臂状态对齐的可复现性弱于基线；无法事后用 HW 时钟重对齐。  
- **建议**：驱动 `read()` 增加 `color_timestamp` / `depth_timestamp`（及可选 `backend_timestamp`），record index.jsonl 原样写出；timeline 对齐优先 HW ts，回退 `t_wall`。

### 【错误 E2】写通道失败帧被 sampler 丢弃

```336:337:/root/autodl-tmp/sensors-dcs/src/sensors_dcs/record.py
                if fr is None or fr.error:
                    continue
```

- `arm_write` / `gripper_write` 在失败时把错误放进 `Frame.error`。  
- **结果**：失败指令 **完全不进** jsonl，与基线「只要 `get_command_state` 有值就记」相反；排障时看不到拒写/跳变中断。  
- **建议**：对 write kind 即使有 `error` 也落盘（payload 带 error）；或 error 与 payload 并存时仍写。

### 【错误 E3】缺 JPEG 仍增加 `written` 计数

```383:409:/root/autodl-tmp/sensors-dcs/src/sensors_dcs/record.py
                try:
                    self._write_frame(item, state_fp, cam_paths)
                    self._written += 1
                ...
        if fr.kind == "realsense":
            jpeg_b64 = (fr.payload or {}).get("jpeg_b64")
            if not jpeg_b64:
                return
```

- `_write_frame` 对无图相机帧直接 `return`，外层仍 `written += 1`。  
- **影响**：manifest 的 `written` 虚高，监控/验收失真。  
- **建议**：`_write_frame` 返回是否真正写入，或无 jpeg 时抛/计 `dropped`。

### 【错误 E4】无「本局作废」等价路径

- 基线：`collection_flag == 2` → `data['valid']=0` 仍 pickle。  
- DCS：成功 `stop` → `valid=true`；无法在 UI/API 标记废片。  
- **影响**：误操作数据与好数据混在同一套 `valid` 语义里。  
- **建议**：`POST /api/record/stop?valid=0` 或 discard 接口，写入 `manifest.valid`。

### 【错误 E5】深度导出与基线 postprocess 不等价（若声称「兼容 hik_dataset 含深度」）

- 基线：`depth*10` 编码进 pkl → decode 后按相机类型 `depth_scale`（wrist `1e-4` / 其它 `1e-3`）再 `align_depth_to_rgb` 写 `d_*`。  
- DCS：驱动侧可 `align_to_color`；导出 **shutil.copy** `_depth.png`，**不**跑同一套 align/scale。  
- **影响**：下游若假设「与历史 hik 深度图同分布」，可能尺度或对齐不一致。  
- **判定**：若产品承诺像素级兼容历史 `d_*`，则属错误；若只承诺「有深度文件」，则降级为 **【差距】** 并在 metadata 标明 `depth_pipeline: dcs_raw_png`。

### 【错误 E6】（条件）`action_state` 审计能力名存实亡

- 若配置未挂 `arm_write`，或摇操只改臂读轨迹、写通道无帧/失败被 E2 吃掉，则 **无法** 像基线视频第四格那样对照「下发 vs 实测」。  
- 训练虽不依赖该轨，但基线采集的**可观测控制意图**在 DCS 默认全单元配置里经常缺失。  
- **建议**：全单元默认录 write agents；修复 E2；可选单独 `command_state.jsonl` 在 `command()` 路径强制 append（不经 error 门控）。

---

## 6. 基线自身问题（不记为 DCS 错误）

| 点 | 说明 |
|----|------|
| 训练不用 `action_state` | `parse_pickle_data` 把 `action_state` 对齐进 `aligned_data`，但 `steps.actions` 仍由 `robot_state` FK 差分；指令轨主要用于预览字串。 |
| 内存与 daemon 落盘 | 停采后 `threading.Thread(..., daemon=True)` 写 pkl，进程退出可能截断。 |
| `get_command_state` 为 `None` | 尚未下发过时 `collect_action_data` 解包会炸。 |
| 深度 `*10` 与硬编码 serial | 预览左右中相机依赖写死 serial 表；深度 scale 靠名字前缀启发式。 |

这些说明：**DCS 对齐的是 postprocess 的训练语义，不是 save_data 里每一条旁路字段。**

---

## 7. 「是否实现错误」快速清单

| ID | 项 | 严重度 | 是否阻塞训练导出 |
|----|----|--------|------------------|
| E1 | 无 RS 硬件时间戳 | 高（多相机/对齐） | 不阻塞，但质量风险 |
| E2 | 写失败帧不落盘 | 中高（审计/排障） | 不阻塞 steps |
| E3 | `written` 虚高 | 低 | 否 |
| E4 | 无 valid=0 | 中（数据治理） | 否 |
| E5 | 深度图与历史 pipeline 不等价 | 高（若要深度兼容） | 无深度时常不触发 |
| E6 | 缺稳定 command 审计轨 | 中 | 否（与基线训练一致） |

---

## 8. 建议优先级（若继续逼近基线）

1. **E1**：驱动 + record 持久化 HW timestamps；timeline 可选主时钟。  
2. **E2 + E6**：write 失败也落盘；全单元默认录 write；或独立 command log。  
3. **E5**：明确深度兼容策略——要么移植 `align_depth_to_rgb`，要么在 metadata 声明差异并默认 `enable_depth` 与全单元一致。  
4. **E4**：作废本局 API。  
5. **E3**：修正计数；顺手刷新 `预检报告.md` 过时段落。

---

## 9. 相关文档

- 训练 actions 通俗说明：[hik-dataset-actions.md](hik-dataset-actions.md)  
- hik_dataset 全解：[hik-dataset.md](hik-dataset.md)  
- 过滤 / materialize：[filter-timeline.md](filter-timeline.md)  
- 夹爪 fake 读（影响观测可信度，间接影响由观测导出的 actions）：[gripper-fake-read.md](gripper-fake-read.md)
