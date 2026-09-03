# Embody login page + cookie session for sensors-dcs

日期：2026-09-03

按 `embody-login-page` + companion `cookie-session-auth` 接到嵌入式 FastAPI 壳：

| 路径 | 行为 |
|------|------|
| `/login` | Embody 风格登录页（grid/orb/scan、玻璃卡、语言 pill） |
| `/` | 需登录时 302 → `/login?from=/` |
| `/api/auth/*` | status / me / login / logout（公开） |
| 其它 `/api/*`、`/ws` | 鉴权开启时需 Cookie |

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
