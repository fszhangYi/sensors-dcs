# Embody-style zh/en i18n for sensors-dcs viz

日期：2026-09-03

嵌入式 HTML 壳（非 React）按 `embody-i18n` 约定落地轻量双语文案：

| 项 | 实现 |
|----|------|
| 存储 | `localStorage['sensors-dcs.locale']`（`zh` \| `en`，默认 **zh**） |
| `html.lang` | `zh-CN` / `en` |
| 文案 | 扁平 dotted keys，`src/sensors_dcs/ui_i18n.py` 中 zh/en 对等 |
| DOM | `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` + `applyDomI18n` |
| 动态串 | JS `t(path, {vars})`，`{name}` 插值 |
| UI | 顶栏 pill：中文 / EN |

服务端在 `GET /` 用 `inject_i18n_json()` 把 catalog 注入为 JS 对象字面量（`const DCS_I18N = __DCS_I18N_JSON__;`，勿再 `JSON.parse('...')`）。错误页 / 登录页同样支持切换。

未接 `docsLocale` / LocaleProvider（无 React 树）。
