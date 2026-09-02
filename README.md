# sensors-dcs

在 **hik-sensors**（本地 `./sensors` 软链接）之上的数据采集运行时（Agent / 缓冲 / 可视化）。当前 Agent：`gello` / `gripper_read` / `realsense`。

## 依赖布局

```text
sensors-dcs/
  sensors -> ~/autodl-tmp/sensors   # 软链接，代码与配置均经此引用
  configs/
  src/sensors_dcs/
  docs/export-timeline.md           # 统一时间轴导出设计
  docs/filter-timeline.md           # 宽表过滤设计
```

运行时会把 `./sensors/src` 插到 `sys.path` 最前，不依赖 site-packages 里的 `hik-sensors` 安装路径。

## 安装

```bash
cd /root/autodl-tmp/sensors-dcs
# 若尚无软链接：
# ln -sfn ~/autodl-tmp/sensors ./sensors
pip install -e .

# 导出时间轴（CSV/Parquet）额外依赖
pip install -e ".[export]"
```

## 快速开始（dry-run）

```bash
# gello + gripper_read
sensors-dcs run -c configs/gello_gripper.yaml

# 仅 gello
sensors-dcs run -c configs/gello_only.yaml

# 仅 RealSense（单路，serial=336222075436）
sensors-dcs run -c configs/camera-only.yaml

# 多路 RealSense（left / right / middle 各一 agent）
sensors-dcs run -c configs/camera-multi.yaml

# gello + gripper + 多相机
sensors-dcs run -c configs/full_cell.yaml
```

浏览器打开：`http://127.0.0.1:7011/`（CLI `run` 会尝试打开浏览器）  
页面显示保存路径与 episode；「开始/结束」控制流水线写盘（传感器常开）。  
落盘完成前「开始」不可点；完成后 episode +1。

### 指定 YAML 配置

所有 CLI 子命令通过 `-c` / `--config` 指定 **DCS 启动 YAML**（含 `sensors_config` + `agents`），例如：

```bash
sensors-dcs run -c configs/camera-multi.yaml
sensors-dcs run -c /path/to/my_dcs.yaml --dry-run
sensors-dcs show-config -c configs/full_cell.yaml
```

桌面端（`sensors-dcs.exe` 或 `python -m sensors_dcs.desktop_main`）同样支持：

```bash
# 命令行参数
sensors-dcs.exe -c "%APPDATA%\sensors-dcs\configs\camera-only.yaml"

# 或环境变量（优先级低于 -c）
set SENSORS_DCS_CONFIG=D:\configs\my_dcs.yaml
sensors-dcs.exe
```

未指定时，桌面端默认读取 `%APPDATA%\sensors-dcs\configs\` 下内置模板。

### 桌面端：无 UI / 弹出 UI

打包后的 `sensors-dcs.exe` **默认仅启动后端**（不弹窗），控制台会打印 viz 地址：

```text
[sensors-dcs] headless backend at http://127.0.0.1:7011/
```

| 方式 | 行为 |
|------|------|
| 双击 `sensors-dcs.exe` | 后台服务，手动浏览器打开上述 URL |
| `sensors-dcs.exe --ui` | 打开 pywebview 窗口（失败则系统浏览器） |
| `set SENSORS_DCS_UI=1` 后启动 | 同 `--ui` |
| `set SENSORS_DCS_BROWSER=1` | 强制系统浏览器，不用 pywebview |

开发环境：

```bash
python -m sensors_dcs.desktop_main -c configs/camera-only.yaml          # 无 UI
python -m sensors_dcs.desktop_main -c configs/camera-only.yaml --ui     # 有 UI
```

真机 Gello（需 Dynamixel 串口 + SDK）：

```bash
pip install -e ".[dynamixel]"
# 编辑 configs/sensors_gello.yaml：dry_run: false，并确认 endpoint / port_substr
sensors-dcs run -c configs/gello_only.yaml --no-dry-run
```

真机 RealSense：

```bash
pip install -e ".[realsense]"
# 单路：configs/sensors_camera.yaml 已写死 serial: "336222075436"
sensors-dcs run -c configs/camera-only.yaml --no-dry-run
# 多路：先改 configs/sensors_cameras.yaml 里 left/right serial，middle 已写死
sensors-dcs run -c configs/camera-multi.yaml --no-dry-run
```

也可直接使用软链接下的机台配置，例如：

```yaml
sensors_config: sensors/configs/default.yaml
```

## 录制与 episode 目录

录制结束后，数据落在 YAML 里 `record.save_dir` 解析出的路径下，例如：

```text
configs/data/episode_00000/
  manifest.json
  states/*.jsonl
  cameras/<agent_id>/index.jsonl + *.jpg
```

- 状态（gello / gripper）每行含 `t_wall`、`seq`、`payload`
- 图片文件名是 **agent 内 seq**；**`t_wall` 在 `index.jsonl`**

## 统一时间轴导出（后处理）

对**某一集已录完的 episode** 做离线导出，详见 [docs/export-timeline.md](docs/export-timeline.md)。  
开发机：`sensors-dcs export-timeline …`；打包桌面端：`sensors-dcs.exe export-timeline …`（同一套子命令）。

### 长表、宽表、网格：差异与作用

录制落盘后，各 sensor 各自一条时间线（gello 50Hz、相机 15Hz…）。导出时把同一份 episode 组织成不同形状：

```text
episode（states/*.jsonl + cameras/*/index.jsonl）
              │
       export-timeline
              │
      ┌───────┼───────┐
      ▼       ▼       ▼
    长表    宽表    网格（宽表的一种 --align grid）
```

| | 长表 | 宽表（`asof` / `nearest`） | 网格（`--align grid`） |
|---|------|------------------------------|-------------------------|
| **输出文件** | `timeline_events.*` | `timeline_aligned.*` | 同上（宽表） |
| **一行含义** | 一条**原始采样** | 一个 **master 时刻**的多 sensor 快照 | 一个**固定格点**的多 sensor 快照 |
| **是否原始** | 完全原始，无损 | master 列原始；其余列为**时间匹配**结果 | 全部是匹配到格点的结果 |
| **行数** | 所有 jsonl 行之和 | ≈ master 的采样数 | `duration × hz` |
| **主时间轴** | 无 | 需 `--master`（见下） | 需 `--hz`，不绑某 agent |
| **典型用途** | 归档、自定义后处理 | 训练样本（关节 + 图像路径） | 固定帧率 tensor |

**长表** — 一行一条原始记录，按 `t_wall` 排序；不做对齐。不加 `--align` 时**只**生成长表。

**宽表** — 一行一个对齐时刻，列展开 `gello.j0`、`cam-left.image_relpath` 等；另有 `{agent}.match_dt`（对齐误差，秒）。`asof` 为因果 backward 最近邻（不用未来帧）；`nearest` 为绝对最近邻。还有 `union` 模式：行 = 所有 agent 的 `t_wall` 并集，稀疏、无 master。

**网格** — 不是第三种文件，而是 `--align grid --hz 50`：在 `[t_start, t_end]` 上每 20ms 一行，各 agent 都 as-of 匹配到格点；适合固定帧率训练。

### 主时间轴比其它 sensor 快怎么办？

宽表 `asof` / `nearest` 的行数 = **master 的原始采样数**。若 master（如 gello 50Hz）快于相机（15Hz），会出现：

- 多行共用**同一张图**（`cam-left.image_relpath` 重复）
- 非 master 列的 `match_dt` 逐行增大，直到下一帧相机到来

可选对策（可组合）：

| 目标 | 做法 |
|------|------|
| 行数跟相机一致 | 换 **`--master cam-left`**（以慢 sensor 为主轴） |
| 仍用 gello 为主，但降到 15Hz | **`--master-hz 15`**（对 master 时间戳下采样后再对齐） |
| 固定训练帧率、不绑某 agent | **`--align grid --hz 15`** |
| 去掉重复图行 | 导出后 **`filter-timeline --dedupe cam-left.image_relpath`** |
| 去掉对齐过差行 | **`filter-timeline --max-match-dt 0.033 --trim both`** |

`--master-hz` 仅作用于 `asof` / `nearest`（在 master 原始时刻上按目标频率取子序列）；与 `--align grid --hz` 互斥（grid 本身已指定格点频率）。

```bash
# gello 50Hz 为主，但宽表只保留约 15Hz 的 master 行
sensors-dcs export-timeline -e episode_00000 \
  --align asof --master gello --master-hz 15

# 或：已导出且多行重复同一相机帧时
sensors-dcs filter-timeline -e episode_00000 \
  --require gello,cam-left --dedupe cam-left.image_relpath --trim both
```

**怎么选**

- 完整保留、自己写 join → **长表**
- 以关节为主、对齐图像 → **宽表 + `--align asof --master gello`**
- 以某路相机为主、对齐关节/其他相机 → **宽表 + `--master cam-left`**（agent `id`，见下）
- 固定 50Hz batch → **网格 + `--hz 50`**

### 主时间轴什么时候需要？

| 导出形态 | 是否需要 `--master` | 说明 |
|----------|---------------------|------|
| **长表** | 否 | 不做对齐，各行互 independent |
| **宽表 `asof` / `nearest`** | **是** | 行 = **master agent 的每个采样时刻**；其他列 as-of 匹配 |
| **宽表 `union`** | 否 | 行 = 所有 agent 的 `t_wall` 并集 |
| **网格 `grid`** | 否（要 **`--hz`**） | 行 = 人为等间隔格点，不跟某一路 sensor |

因此：**不是只有网格才需要主时间轴**；`asof` / `nearest` 宽表同样要指定 master（或接受自动选择规则）。

### 宽表 as-of 直观例子

假设 gello 在 `t=1.00、1.02` 采样，相机在 `t=1.01、1.03` 各有一帧：

**长表** → 4 行，4 条原始记录，原样保留。

**宽表 `--align asof --master gello`** → 2 行（gello 有几个时刻就有几行）：

```text
t=1.00  gello=原始    camera=NaN           （1.01 的图尚未发生，backward 取不到）
t=1.02  gello=原始    camera=1.01 那张图   camera.match_dt ≈ 0.01
```

- master 列（gello）是**原始采样**
- 其他列（camera）是**匹配到的最近过去样本**，不一定是同一时刻的原始帧
- 用 `{agent}.match_dt` 过滤对齐过差的行（例如 `cam-right.match_dt.abs() < 0.033`）

**网格 `--align grid --hz 50`** → 每 20ms 一行（`0.00, 0.02, 0.04, …`），**所有列**都是匹配到格点的结果；各列均有 `match_dt`。

### 长表示例（原始数据）

| t_wall | agent_id | kind | seq | 数据列 |
|--------|----------|------|-----|--------|
| 1.00 | gello | gello | 10 | j0, j1, … |
| 1.01 | cam-left | realsense | 5 | image_relpath |
| 1.02 | gello | gello | 11 | j0, j1, … |

一行一条采样，**不做**「同一 moment 多 sensor 合并」。

### 命令示例

```bash
# 长表（每行一条原始采样），默认 Parquet → <episode>/export/
sensors-dcs export-timeline -e configs/data/episode_00000

# 宽表：以 gello 为主时钟，as-of 对齐（因果，不用未来帧）
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master gello

# 宽表：以左路相机为主时钟（多相机时见下一节）
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master cam-left

# 最近邻对齐
sensors-dcs export-timeline -e episode_00000 --align nearest --master cam-middle

# 固定 50Hz 网格 + CSV（不需要 --master）
sensors-dcs export-timeline -e episode_00000 --align grid --hz 50 --format csv

# 指定输出目录、同时写 Parquet 与 CSV
sensors-dcs export-timeline -e episode_00000 --align asof -o /tmp/export --format both

# master 下采样（gello 50Hz → 宽表约 15Hz）
sensors-dcs export-timeline -e episode_00000 \
  --align asof --master gello --master-hz 15
```

### 多相机时指定主时间轴

`--master` 填的是 DCS YAML 里 agent 的 **`id`**（不是 `sensor_id`，也不是 role 名 `left`）。  
与 episode 目录 `cameras/<agent_id>/` 一致。

以 `configs/camera-multi.yaml` / `configs/full_cell.yaml` 为例：

| `--master` 取值 | 含义 |
|-----------------|------|
| `cam-left` | 以左路相机采样时刻为主轴 |
| `cam-right` | 右路 |
| `cam-middle` | 中间 |
| `cam-wrist` | 腕部 |
| `gello` | 关节（full_cell 时自动默认倾向） |

单相机 `configs/camera-only.yaml` 里 agent `id` 为 **`camera`**：

```bash
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master camera
```

多相机：以某一路为主，其余 gello / 其他相机 as-of 对齐到该路时刻：

```bash
# 以左路相机为主时间轴
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master cam-left

# 以 middle 相机为主
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master cam-middle
```

**如何确认 agent 名称**

1. 录制 YAML 里 `agents[].id`（例如 `cam-left`）
2. episode 内 `manifest.json` → `agents[].agent_id`
3. 目录 `episode_xxx/cameras/` 下子文件夹名

```text
episode_00000/cameras/
  cam-left/
  cam-right/
  cam-middle/
```

**未写 `--master` 时的自动规则**：优先第一个 **gello**；若无 gello，选 hz 最高的 state agent；若**只有多路相机**，按 `agent_id` 字典序取第一个——**不保证是你想要的那路，多相机请始终显式 `--master`**。

**宽表里的多路相机列**：每路仍有独立列（`cam-left.image_relpath`、`cam-right.image_relpath` 等）。  
指定 `--master cam-left` 时，`cam-left.match_dt == 0`；`cam-right`、`gello` 等列为 as-of 匹配结果及其 `match_dt`。

### 输出与读取

```text
episode_00000/export/
  timeline_events.parquet      # 长表
  timeline_aligned.parquet     # 宽表（仅 --align 时）
  export_meta.json             # 参数、行数、match_dt 统计
```

宽表列名形如 `gello.j0`、`cam-left.image_relpath`、`cam-left.match_dt`。  
下游用 `t_wall` + `match_dt` 对齐，**不要**用跨 agent 的 `seq`。

Python 读取示例：

```python
import pandas as pd
from pathlib import Path

ep = Path("configs/data/episode_00000")
df = pd.read_parquet(ep / "export/timeline_aligned.parquet")
row = df.iloc[0]
# row["cam-left.image_relpath"], row["gello.j0"], row["cam-right.match_dt"]
```

### 宽表过滤与重新编号（filter-timeline）

对已导出的宽表做质量过滤，输出 **`step` 从 0 连续** 的 `timeline_filtered.*`。详见 [docs/filter-timeline.md](docs/filter-timeline.md)。

**处理的问题**

| 问题 | 处理方式 |
|------|----------|
| 开头其它 sensor 对不上 | `--trim start/both`：裁掉 master 已开、其它列 NaN 或 match_dt 过大的 warmup 行 |
| 结尾 match_dt 飙升（其它 sensor 已停） | `--trim end/both`：裁掉 cooldown 段 |
| master 结束后其它 sensor 仍有原始帧 | **不并入宽表**；写入 `filter_meta.json` 的 `tail_after_master` |

```bash
# 先导出宽表
sensors-dcs export-timeline -e configs/data/episode_00000 \
  --align asof --master gello

# 再过滤：require 列必须有效，match_dt ≤ 0.033s，头尾裁剪，step 从 0 编号
sensors-dcs filter-timeline -e configs/data/episode_00000 \
  --require gello,cam-left,cam-middle \
  --max-match-dt 0.033 \
  --trim both

# per-agent 阈值 + 物化图片到 export/filtered/images/<agent>/<step>.jpg
sensors-dcs filter-timeline -e episode_00000 \
  --require cam-left,cam-right \
  --max-match-dt cam-left:0.033,cam-right:0.05 \
  --materialize
```

| 参数 | 说明 |
|------|------|
| `--require` | 每行必须有效的 agent（逗号分隔）；默认宽表中所有带 `match_dt` 的 agent |
| `--max-match-dt` | 非 master 允许的最大 `match_dt`（秒）；默认 `0.033` |
| `--trim` | `none` / `start` / `end` / `both`（默认 `both`） |
| `--dedupe` | 按列去重**连续重复行**（如 `cam-left.image_relpath` 或 `cam-left.*`） |
| `--master` | 覆盖 master（默认读 `export_meta.json`） |
| `--materialize` | 按 `step` 复制相机图到 `export/filtered/images/` |

输出：

```text
episode_00000/export/
  timeline_filtered.parquet
  filter_meta.json              # rows_in/out, tail_after_master, drop_reasons
  filtered/images/cam-left/00000000.jpg   # --materialize 时
```

Python 读取过滤结果：

```python
import pandas as pd
from pathlib import Path

ep = Path("configs/data/episode_00000")
df = pd.read_parquet(ep / "export/timeline_filtered.parquet")
row = df.iloc[0]
step = int(row["step"])  # 从 0 连续
# row["cam-left.image_relpath"] 或 row["cam-left.filtered_file"]（materialize 后）
```

完整后处理链路：

```text
episode → export-timeline --align asof --master …
        → filter-timeline --require … --trim both
        → timeline_filtered.parquet（step=0..N-1）
```

## 配置

| 文件 | 作用 |
|------|------|
| `configs/gello_gripper.yaml` | DCS：gello + gripper_read |
| `configs/sensors_gello_gripper.yaml` | 对应设备清单（gello + DH 夹爪） |
| `configs/gello_only.yaml` | 仅 Gello Agent |
| `configs/sensors_gello.yaml` | 仅 gello 设备清单 |
| `configs/camera-only.yaml` | 仅 RealSense Agent（单路） |
| `configs/sensors_camera.yaml` | 单路设备清单（serial=`336222075436`） |
| `configs/camera-multi.yaml` | 多路 RealSense（left/right/middle） |
| `configs/sensors_cameras.yaml` | 多路设备清单（middle 已写死；left/right 需填 serial） |
| `configs/full_cell.yaml` | gello + gripper_read + 多路 RealSense |
| `configs/sensors_full_cell.yaml` | 对应全量设备清单 |
| `sensors/configs/*.yaml` | 软链接指向的 hik-sensors 设备清单 |

## 桌面打包（Linux → Windows）

基于 `scheme-a-linux-to-windows-desktop`：Wine + 嵌入式 CPython + PyInstaller onedir。

```bash
# 需已有 ./sensors 软链接与 wine64
./build.sh --target windows
# 或
bash scripts/build-desktop.sh --target windows
```

产物：

- `release/sensors-dcs-desktop-windows-x64-<UTC>/sensors-dcs.exe`
- 同名 `.zip`

目标机无需安装 Python；用户数据在 `%APPDATA%\sensors-dcs\`。

打包 exe 用法摘要：

```bat
sensors-dcs.exe                                    REM 无 UI，后台
sensors-dcs.exe --ui                               REM 弹窗
sensors-dcs.exe -c "%APPDATA%\sensors-dcs\configs\camera-multi.yaml" --ui
```

`export-timeline` / `filter-timeline`：开发机用 `pip install -e ".[export]"` 后运行；打包桌面端也可用同一子命令，例如：

```bat
sensors-dcs.exe export-timeline -e episode_00000 --align asof --master gello --master-hz 15
sensors-dcs.exe filter-timeline -e episode_00000 --require gello,cam-left --trim both
```

重新打包后的桌面包已内置 pandas/pyarrow；**当前旧 exe 需重新 `build-desktop` 才生效**。

### 桌面包依赖清单

| 功能 | Python 包 | 是否打入 exe | 说明 |
|------|-----------|--------------|------|
| 采集 / viz HTTP+WS | `fastapi`, `uvicorn`, `starlette`, `httptools`, `websockets`, `watchfiles` | 是 | `requirements.txt` |
| 配置 | `PyYAML`, `pydantic`, `pydantic_core` | 是 | |
| 预览 JPEG | `opencv-python-headless` (`cv2`) | 是 | `requirements-hardware.txt` |
| Gello / 串口 | `dynamixel-sdk`, `pyserial` | 是 | |
| `--ui` 窗口 | `pywebview`（import 名 `webview`） | 是 | 失败时回退系统浏览器 |
| 时间轴导出 | `pandas`, `pyarrow` | 是 | `requirements-desktop.txt` |
| 真机 RealSense | `pyrealsense2` | **否** | 目标机装 Intel SDK + wheel；`dry_run` 可不装 |

构建前可在 Linux 开发机自检（Wine 构建环境应能通过 pip 装齐）：

```bash
pip install -r requirements.txt -r requirements-desktop.txt -r requirements-hardware.txt
python scripts/check_desktop_deps.py
```
