# Chunk 跳点（单步 / LOOP）— 实现计划

日期：2026-09-09  
状态：**已实现**  
范围：推理 Tab · 控制分页 · 单步调试 / LOOP；**纯前端语法糖**，不改 serve 协议与后端 chunk API。

---

## 0. 产品结论

| # | 结论 |
|---|------|
| 1 | 在「单步调试」与「LOOP」之间增加整数输入框：**跳点**，默认 `1`，范围 `1–15`。 |
| 2 | 跳点 = N 时，点一次「单步调试」≡ 连续调用现有 `runInfPi05StepOnce()` **N 次**（仍不自动下发）。 |
| 3 | LOOP 一轮迭代：原先 `step → 下发 → 等待`；现为 `step×N → 下发×1 → 等待`。 |
| 4 | 只组合复用原子能力：`runInfPi05StepOnce` / `fillInfArmJointsFromStep` / `sendInfArmJointsOnce` / `waitInfArmArrive`。 |
| 5 | **不**新增后端 chunk API；每次 step 仍推进 serve 侧 chunk 游标一格。 |
| 6 | term / reject / IK 失败：在 N 次 step 的**任意一次**出现即按现逻辑中断；仅最后一次成功 step 的 joints 用于随后一次下发。 |

---

## 1. UI

```html
<button id="infPi05Step">单步调试</button>
<label class="inf-loop-rounds" …>
  <span data-i18n="infer.chunk_skip">跳点</span>
  <input type="number" id="infChunkSkip" min="1" max="15" step="1" value="1" />
</label>
<button id="infPi05Loop">LOOP</button>
```

- 复用 `.inf-loop-rounds` 样式。  
- `localStorage` 键：`dcs.inf.chunkSkip`（镜像 `dcs.inf.loopRounds`）。  
- LOOP / step busy 时禁用输入（与轮数类似）。  
- i18n：`infer.chunk_skip` / `infer.chunk_skip_hint`（zh/en）。

---

## 2. 前端逻辑

### 2.1 读数

```js
function clampChunkSkip(v) {
  let n = Math.round(Number(v));
  if (!Number.isFinite(n)) n = 1;
  return Math.max(1, Math.min(15, n));
}
function getChunkSkip() {
  return clampChunkSkip(infChunkSkip ? infChunkSkip.value : 1);
}
```

### 2.2 原子组合

```js
async function runInfPi05StepNTimes(n, { shouldAbort } = {}) {
  let last = null;
  for (let i = 0; i < n; i++) {
    if (shouldAbort && shouldAbort()) return last;
    last = await runInfPi05StepOnce();
    if (!last || !last.ok) return last;
    // 调用方可再查 term/reject/ik；本函数只负责连续 step
  }
  return last;
}
```

### 2.3 单步调试按钮

```text
click → runInfPi05StepNTimes(getChunkSkip())
```

中途 `!ok` 即停；最终 UI 提示仍基于 **last** 结果（与现单步一致）。

### 2.4 LOOP（改 `runPi05LoopOnce` 内一步）

将：

```text
stepRes = await runInfPi05StepOnce()
→ term/reject/ik 检查
→ sendInfArmJointsOnce
→ waitInfArmArrive
```

改为：

```text
N = getChunkSkip()
for i in 1..N:
  stepRes = await runInfPi05StepOnce()
  若 stopped / !ok / term / reject / !ik → 与现逻辑相同处理并 return
fillInfArmJointsFromStep(stepRes)  // last
sendInfArmJointsOnce()
waitInfArmArrive(...)
```

`pi05LoopStepN` 仍按「下发周期」+1（一次 LOOP 迭代 = 一次下发），hint 文案可附带 `×N`（可选，非必须）。

---

## 3. 非目标

- 不解析 serve 多步 action 数组。  
- 不改 `POST /api/pi05/step` 契约。  
- 不改手动「下发」按钮语义。  
- 不做后端跳点参数。

---

## 4. 验收

| 操作 | 期望 |
|------|------|
| 跳点=1 | 行为与改前完全一致 |
| 跳点=5 + 单步 | 连续 5 次 step；`#infArmJoints` 为第 5 次结果；无自动下发 |
| 跳点=3 + LOOP | 每轮：3×step → 1×下发 → 等待；任一步 term 则本轮结束且不下发 |
| busy | 跳点框禁用；停 LOOP 后恢复 |

---

## 5. 文件清单

| 文件 | 变更 |
|------|------|
| `docs/chunk-skip.md` | 本计划 |
| `src/sensors_dcs/viz.py` | DOM + `runInfPi05StepNTimes` + step/LOOP 接线 |
| `src/sensors_dcs/ui_i18n.py` | 文案 |

---

## 6. 实现顺序

1. 落本文档  
2. HTML / i18n / localStorage  
3. `runInfPi05StepNTimes` + 单步接线  
4. `runPi05LoopOnce` 内 N 次 step  
5. 提交并推送  
