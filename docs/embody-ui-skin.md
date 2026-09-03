# Embody UI skin for sensors-dcs viz

日期：2026-09-03

将嵌入式预览页 / 错误页对齐 Embody Model Eval dark lab-console（`embody-ui-style` 最小皮）：

- Tokens：`--bg #0b1018`、`--accent #3dd6c6`、`--spark #f0b429`、玻璃 `--panel` / `--chrome` / `--input-bg`
- 品牌标题 `--brand-title` 渐变裁剪；kicker 大写字距
- Pill：Tab、主按钮、元数据 chip、安全退出
- 氛围：双径向 spot + 淡网格；`prefers-reduced-motion` 关过渡
- 字体：Google Fonts IBM Plex Sans（含 CJK fallback）

实现仍在 `viz.py` 的 `PREVIEW_HTML` / `ERROR_HTML`（非独立 React 前端）。未接 light theme / density（见 `embody-appearance-theme`）。
