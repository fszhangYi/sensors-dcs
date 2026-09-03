# Embody login page + cookie session for sensors-dcs

日期：2026-09-03

按 `embody-login-page` + companion `cookie-session-auth` 接到嵌入式 FastAPI 壳：

| 路径 | 行为 |
|------|------|
| `/login` | Embody 风格登录页（grid/orb/scan、玻璃卡、语言 pill）；启动默认打开此页 |
| `/` | 需登录时 302 → `/login?from=/`；登录后默认「数据后处理」Tab |
| `/api/auth/*` | status / me / login / logout（公开） |
| 其它 `/api/*`、`/ws` | 鉴权开启时需 Cookie |

YAML / boot 失败时仍走同一套 UI（`create_error_app` → `create_viz_app`）：

- 健康检查 `/api/health` 公开，webview/browser 可打开
- `collect_ok=false`：锁定「数据采集」Tab，横幅展示错误
- 「数据后处理」仍可用（离线三步）

实现：

- `auth_session.py` — opaque session、`sensors_dcs_session` HttpOnly Cookie  
- `users_store.py` — `user_data_dir/configs/users.json` + PBKDF2  
- `login_page.py` — LOGIN_HTML  

环境变量：

| 变量 | 作用 |
|------|------|
| `SENSORS_DCS_AUTH_DISABLED=1` | 关闭鉴权 |
| `SENSORS_DCS_AUTH_USER` | 默认 `sensors` |
| `SENSORS_DCS_AUTH_PASSWORD` | 设置/更新 admin 密码 |

首次无用户时会生成随机密码并打印到终端（同时写 `.auth.json` 备份，勿提交）。
