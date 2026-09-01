# sensors-dcs

在 **hik-sensors**（本地 `./sensors` 软链接）之上的数据采集运行时（Agent / 缓冲 / 可视化）。本期 MVP 仅实现 **Gello Agent**。

## 依赖布局

```text
sensors-dcs/
  sensors -> ~/autodl-tmp/sensors   # 软链接，代码与配置均经此引用
  configs/
  src/sensors_dcs/
```

运行时会把 `./sensors/src` 插到 `sys.path` 最前，不依赖 site-packages 里的 `hik-sensors` 安装路径。

## 快速开始（dry-run）

```bash
cd /root/autodl-tmp/sensors-dcs
# 若尚无软链接：
# ln -sfn ~/autodl-tmp/sensors ./sensors
pip install -e .

# gello + gripper_read
sensors-dcs run -c configs/gello_gripper.yaml

# 仅 gello
sensors-dcs run -c configs/gello_only.yaml
```

浏览器打开：`http://<host>:7011/`  
终端会以约 5Hz 打印关节角；页面经 WebSocket 约 15Hz 刷新条形图。

真机（需 Dynamixel 串口 + SDK）：

```bash
pip install -e ".[dynamixel]"
# 编辑 configs/sensors_gello.yaml：dry_run: false，并确认 endpoint / port_substr
sensors-dcs run -c configs/gello_only.yaml --no-dry-run
```

也可直接使用软链接下的机台配置，例如：

```yaml
sensors_config: sensors/configs/default.yaml
```

## 配置

| 文件 | 作用 |
|------|------|
| `configs/gello_gripper.yaml` | DCS：gello + gripper_read |
| `configs/sensors_gello_gripper.yaml` | 对应设备清单（gello + DH 夹爪） |
| `configs/gello_only.yaml` | 仅 Gello Agent |
| `configs/sensors_gello.yaml` | 仅 gello 设备清单 |
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
