# sensors-dcs 新机安装手册（给 Cursor Agent）

> **读者**：另一台电脑上的 Cursor Agent。  
> **目标**：在干净机器上把 `sensors-dcs` + 依赖仓库 `sensors`（hik-sensors）装到可 `dry-run` 跑通；可选装真机硬件依赖。  
> **不要**：改 git config、提交密钥、把 `users.json` / `.auth.json` 写进仓库。

---

## 0. 成功标准（做完必须全部通过）

在目标机执行，全部成功才算安装完成：

```bash
# 布局
test -d "$WORKSPACE/sensors/src/sensors"
test -L "$WORKSPACE/sensors-dcs/sensors" || test -d "$WORKSPACE/sensors-dcs/sensors/src/sensors"

# 包可导入（优先用本地 softlink，不依赖 site-packages 里的 hik-sensors 路径）
cd "$WORKSPACE/sensors-dcs"
python -c "from sensors_dcs.paths import ensure_sensors_import; ensure_sensors_import(); import sensors, sensors_dcs; print('ok', sensors.__file__, sensors_dcs.__file__)"

# CLI
sensors-dcs --help
sensors kinds

# dry-run 冒烟（起服务后 Ctrl+C 结束即可）
SENSORS_DCS_AUTH_DISABLED=1 sensors-dcs run -c configs/gello_only.yaml
# 浏览器应能打开 http://127.0.0.1:7011/ （或终端打印的端口）
```

---

## 1. 前置条件

| 项 | 要求 |
|----|------|
| OS | Linux x86_64 优先（本手册默认）；Windows 开发可用，但 RealSense/串口路径不同 |
| Python | **≥ 3.10**（推荐 3.10–3.12；勿用过旧 3.9） |
| Git + SSH | 能 clone 私有仓库 `fszhangYi/sensors-dcs` 与 `fszhangYi/sensors` |
| 磁盘 | 源码 + venv 约数 GB；录数据另算 |
| 网络 | PyPI（或内网镜像）可装包 |

可选系统包（真机 / bus 需要时再装）：

```bash
# Debian/Ubuntu 示例
sudo apt update
sudo apt install -y git build-essential socat libusb-1.0-0
# RealSense 真机：另装 Intel librealsense / 官方驱动（见 Intel 文档），再 pip 装 pyrealsense2
```

`sensors-view` **不是**启动 DCS 的硬依赖；仅「传感器状态」iframe 嵌入时需要另装并在设置里填 URL。本手册可跳过。

---

## 2. 选择工作区并取得源码

把下面的 `WORKSPACE` 换成目标机实际路径（示例：`/root/autodl-tmp` 或 `$HOME/lab`）。

```bash
export WORKSPACE="${WORKSPACE:-$HOME/lab}"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"
```

### 2.1 Clone 两个仓库（必须并列）

```bash
# 若本机 SSH Host 别名不同，改成可用的 github 地址
git clone git@github.com:fszhangYi/sensors.git
git clone git@github.com:fszhangYi/sensors-dcs.git
```

源机曾用 Host 别名 `github.com-sensors` / `github.com-sensors-dcs`；新机若未配置别名，用上面标准 `github.com` 即可。

期望布局：

```text
$WORKSPACE/
  sensors/                 # hik-sensors（独立 git 仓库）
  sensors-dcs/             # 本仓库
    sensors -> ../sensors  # 软链接（下一步创建）
```

### 2.2 创建软链接（强制）

`sensors-dcs` 运行时把 `./sensors/src` 插到 `sys.path` 最前；**不要**只靠 `pip install hik-sensors` 的 site-packages 路径。

```bash
cd "$WORKSPACE/sensors-dcs"
ln -sfn "$WORKSPACE/sensors" ./sensors
ls -la sensors/src/sensors   # 应能看到 Python 包目录
```

`.gitignore` 已忽略 `sensors` 软链接，勿提交。

---

## 3. Python 环境

推荐独立 venv（或 conda env），避免污染系统 Python。

```bash
cd "$WORKSPACE/sensors-dcs"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
```

conda 等价：

```bash
conda create -n sensors-dcs python=3.12 -y
conda activate sensors-dcs
```

此后所有 `pip` / `sensors-dcs` 命令都在该环境中执行。

---

## 4. 安装 Python 包

### 4.1 最小可跑（dry-run / 无真机）

```bash
cd "$WORKSPACE/sensors"
pip install -e .

cd "$WORKSPACE/sensors-dcs"
pip install -e .
# 时间轴导出 / hik 训练集导出
pip install -e ".[export]"
# 开发自测（可选）
pip install -e ".[dev]"
```

说明：

- `sensors-dcs` 的 `pyproject.toml` **不**把 hik-sensors 写成 PyPI 依赖；靠软链接 + `ensure_sensors_import()`。
- 仍建议对 `sensors` 执行 `pip install -e .`，以便 `sensors` CLI（`sensors kinds` 等）可用。

### 4.2 真机可选 extras（按需）

| 场景 | 安装命令 | 额外系统要求 |
|------|----------|--------------|
| Gello / Dynamixel | `pip install -e ".[dynamixel]"`（在 sensors-dcs 目录） | USB 串口权限、`pyserial` |
| RealSense 相机 | `pip install -e ".[realsense]"` | Intel 驱动 + USB3 |
| Elite 机械臂 | `pip install elirobots loguru`（或 `pip install -r requirements-hardware.txt`） | 网口通控制器，监控 TCP **8056** |
| Infer IK（pose→joints） | `pip install -e ".[kinematics]"` 或 `scipy>=1.10` | — |
| 桌面打包自检全量 | 见 §4.3 | Wine/打包机才需要 |

在 `sensors` 侧也可：

```bash
cd "$WORKSPACE/sensors"
pip install -e ".[serial,zmq,realsense,dynamixel,kinematics]"
```

### 4.3 全量对齐桌面包依赖（开发机 / 打包机）

与 README「桌面包依赖清单」一致：

```bash
cd "$WORKSPACE/sensors-dcs"
pip install -r requirements.txt -r requirements-desktop.txt -r requirements-hardware.txt
python scripts/check_desktop_deps.py
```

注意：`requirements.txt` 将 `numpy` 钉在 `1.23.5`（桌面/Wine 兼容）；纯 Linux 开发若已装更新 numpy，以 `pyproject` 的 `numpy>=1.24` 为准即可，冲突时优先满足你当前用途（开发 vs Wine 打包）。

---

## 5. 鉴权与首次启动

默认开启 Cookie 会话鉴权。新机首次无用户时会生成随机 admin 密码并打印到终端（并写用户数据目录下的 `.auth.json`，**勿提交**）。

开发时可临时关闭：

```bash
export SENSORS_DCS_AUTH_DISABLED=1
```

或指定初始密码：

```bash
export SENSORS_DCS_AUTH_USER=sensors
export SENSORS_DCS_AUTH_PASSWORD='你的强密码'
```

用户数据目录（开发机）：通常在 `~/.local/share/sensors-dcs/`（Linux）或 `%APPDATA%\sensors-dcs\`（Windows 桌面包）。仓库内 `configs/users.example.json` 仅作格式参考。

---

## 6. 冒烟与常用入口

### 6.1 dry-run（无硬件）

```bash
cd "$WORKSPACE/sensors-dcs"
source .venv/bin/activate   # 若用 venv
export SENSORS_DCS_AUTH_DISABLED=1

sensors-dcs run -c configs/gello_only.yaml
# 或全工位默认配置
sensors-dcs run -c configs/default.yaml
```

也可用：

```bash
./start-6006.sh configs/gello_only.yaml
# 固定端口 6006；登录页 http://127.0.0.1:6006/login
```

桌面入口（开发）：

```bash
python -m sensors_dcs.desktop_main -c configs/camera-only.yaml       # 无 UI
python -m sensors_dcs.desktop_main -c configs/camera-only.yaml --ui  # 弹窗/浏览器
```

### 6.2 真机开关

YAML 里设备清单常带 `dry_run`；CLI 可用 `--no-dry-run` 强制真机。改配置前先读对应 docs（`docs/arm-write.md`、`docs/gello-joint-affine.md` 等）。

```bash
# 例：RealSense（先装 [realsense] 并改 serial）
sensors-dcs run -c configs/camera-only.yaml --no-dry-run

# 例：Elite 只读（先装 elirobots，改 configs/sensors_robot.yaml 的 robot_ip）
sensors-dcs run -c configs/robot_only.yaml --no-dry-run
```

### 6.3 导出工具

```bash
pip install -e ".[export]"
sensors-dcs export-timeline -e episode_00000 --align asof --master gello --master-hz 15
sensors-dcs filter-timeline -e episode_00000 --require gello,cam-left --trim both
sensors-dcs export-hik-dataset -e episode_00000 --camera-map configs/hik_camera_map.yaml
```

---

## 7. Cursor 在新机上的推荐操作顺序（直接照做）

1. 确认 `python3 --version` ≥ 3.10；无则安装。  
2. 设 `WORKSPACE`，clone `sensors` + `sensors-dcs` 到并列目录。  
3. 在 `sensors-dcs` 下 `ln -sfn ../sensors ./sensors`（或绝对路径）。  
4. 创建并激活 venv/conda。  
5. `pip install -e .`（先 sensors，再 sensors-dcs）；再 `pip install -e ".[export]"`。  
6. 跑 §0 成功标准；失败则检查软链接与 `python -c "import sensors"` 的 `__file__` 是否指向 `$WORKSPACE/sensors/src/...`。  
7. 若用户要真机：按 §4.2 装对应 extras + 系统驱动，再改 YAML serial / `robot_ip` / 串口。  
8. 若用户只要「零 Python 桌面 exe」：不要走本手册源码安装，改用已发布的 `release/sensors-dcs-desktop-windows-x64-*.zip`（见仓库 README「桌面打包」）；目标机仍需 USB/网口驱动。

---

## 8. 常见失败与处理

| 现象 | 处理 |
|------|------|
| `No module named 'sensors'` | 软链接未建或指向错误；执行 §2.2 |
| `import sensors` 指向错误 site-packages | 确认在 `sensors-dcs` 目录启动，且 `./sensors/src` 存在；必要时 `pip uninstall hik-sensors` 后依赖软链接 |
| `No module named 'elite'` | `pip install elirobots`（开发）；桌面包用完整 zip 重装 |
| RealSense 打不开 | 驱动/USB；`pip install pyrealsense2`；检查 serial |
| Gello 串口权限 | 用户加入 `dialout`，或 `udev` 规则；确认 `port_substr` |
| Elite `struct.error` / monitor | 关 DCS 等 3–5s 再开；勿双开同一 IP；见 `docs/elite-monitor-reconnect.md` |
| 导出缺 pyarrow | `pip install -e ".[export]"` |
| 鉴权卡在登录 | `SENSORS_DCS_AUTH_DISABLED=1` 或看终端打印的初始密码 |
| numpy / scipy 冲突 | 桌面打包钉 `numpy==1.23.5` + `scipy<1.12`；纯开发用较新 numpy 即可 |

---

## 9. 仓库与文档索引

| 路径 | 用途 |
|------|------|
| `sensors-dcs/README.md` | 总览、配置表、桌面打包 |
| `sensors/README.md` | hik-sensors 驱动与 `sensors` CLI |
| `sensors-dcs/pyproject.toml` | 核心与 optional-dependencies |
| `requirements*.txt` | 桌面/硬件钉版本清单 |
| `docs/embody-login.md` | 登录与用户管理 |
| `docs/arm-write.md` / `gello-arm-*.md` | 真机写臂 / 遥操作安全说明 |
| `configs/*.yaml` | DCS 启动与设备清单 |

---

## 10. 环境变量速查

| 变量 | 含义 |
|------|------|
| `SENSORS_DCS_CONFIG` | 默认 DCS YAML（优先级低于 `-c`） |
| `SENSORS_DCS_PORT` / `SENSORS_DCS_VIZ_HOST` | viz 端口/绑定（`start-6006.sh` 默认 6006） |
| `SENSORS_DCS_UI=1` | 桌面弹窗 |
| `SENSORS_DCS_BROWSER=1` | 强制系统浏览器 |
| `SENSORS_DCS_AUTH_DISABLED=1` | 关鉴权 |
| `SENSORS_DCS_AUTH_USER` / `SENSORS_DCS_AUTH_PASSWORD` | 初始/更新 admin |
| `SENSORS_DCS_DRY_RUN=0` | 与 `start-6006.sh` 配合强制真机（若脚本/配置支持） |

---

**安装完成后**：向用户报告 `WORKSPACE` 路径、Python 环境名、已装 extras、以及一条已验证的启动命令。
