# Boot：YAML / agent 失败时前端仍可起

日期：2026-09-04

## 问题

`dry_run: false` 时，合法 DCS YAML 也能在 `agent.start()` → `sensor.open()` 阶段失败
（缺 SDK、串口不可用、相机未接等）。旧 CLI 路径 `Orchestrator.serve()` **先 open 再起 uvicorn**，
一抛异常进程直接退出，登录页 / 后处理 Tab 都看不到。

YAML 本身非法（缺 `sensors_config`、误用 `sensors_*.yaml` 启动）早已走 `create_error_app`。

## 做法

1. **先起 viz**，再用可变 `boot_box={"error": …}` 锁住「数据采集」Tab。
2. `open()` 成功 → `boot_box["error"]=None`，采集可用。
3. `open()` 失败 → 写入 traceback 到 `boot_box`，**进程与后处理 UI 继续**；Collect 显示 boot banner。
4. Desktop 入口同样：agent boot 放后台线程，不挡 `serve_app_blocking`。

## 相关

- `src/sensors_dcs/runtime.py` — `Orchestrator.serve`
- `src/sensors_dcs/desktop_main.py` — `_boot_agents` 线程
- `src/sensors_dcs/viz.py` — `boot_box` / `_current_boot_error`
- `tests/test_boot_box_agent_fail.py`
