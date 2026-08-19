# PRD-B1：Effect 机制（可撤销副作用内核）

| 项 | 值 |
|---|---|
| 阶段 | B1 |
| 状态 | 已验收 |
| 分支 | feature/B1-effect |
| 日期 | 2026-08-19 |

## 1. 背景与目标

cordis 的时间可组合性核心是 **Effect**：每个副作用的建立过程同时交出它的撤销操作，
由 Fiber 统一跟踪，组件移除时倒序执行。对应 cordis 原实现
`packages/core/src/fiber.ts` 的 `Fiber.effect()`（约 120 行）与
`packages/core/src/utils.ts` 的 `DisposableList`（约 40 行）。

本阶段交付：Effect 四形态统一收集 + LIFO 清理 + 失败级联清理，
以及承载它的最小 Fiber/Context 骨架（完整状态机与 plugin 注册是 B2）。

## 2. 功能需求（FR）

- FR1 `DisposableList`：插入序维护、`delete(value)`、`clear()` 倒序弹出并清空、
  支持 `len()` 与迭代、`push()` 返回删除回调
- FR2 `FiberState` 枚举：`PENDING / ACTIVE / DISPOSED`（B2 再扩展完整状态机）
- FR3 `Fiber.effect(execute, label)`：`execute` 返回以下四种形态之一，统一收敛为
  disposable 集合：
  1. 清理函数（`Callable[[], Any]`）
  2. `None`（无清理）
  3. awaitable（解析后为清理函数）
  4. 生成器（逐个 `yield` 清理函数；异步生成器逐个 `yield`，每步间可被中断）
- FR4 `effect()` 返回 **disposable wrapper**，双语义（对齐 TS 的
  `AsyncDisposable`）：
  - 调用 `wrapper()` → 触发清理（先等未完成 task，再串行执行清理）
  - `await wrapper` → 等副作用建立完成后，返回清理函数
- FR5 失败路径：
  - `execute` 同步抛错 → 已收集的清理函数立即执行（倒序）并向上抛出
  - awaitable/异步生成器中途失败 → 自动级联清理已收集部分，错误记入日志
- FR6 `Fiber.dispose()`：倒序清理全部收集的 disposable，串行执行异步清理
- FR7 `Context` 最小骨架：持有 `fiber` 与 `logger`；`ctx.effect()` 委托 fiber；
  Fiber 失活后调用 `effect()` 抛 `CordisError`
- FR8 日志：标准库 `logging`，logger 名为 `cordis.<fiber 名>`；清理失败记 error

## 3. 非目标

- 不实现 plugin 注册 / Fiber 完整状态机 / epoch 依赖刷新（B2）
- 不实现 isolate / intercept / 服务注册（C 阶段）
- 不实现 callable service、装饰器（D 阶段）

## 4. 技术方案

- **模块划分**：`src/cordis/disposable.py`（DisposableList）、
  `src/cordis/fiber.py`（Fiber/FiberState/CordisError/effect 解析）、
  `src/cordis/context.py`（Context 骨架）
- **形态判别顺序**（与 cordis TS 对齐）：`None` → `callable` → `isawaitable` →
  `AsyncIterator` → `Iterator` → 否则 `TypeError`
- **与 JS 的有意差异**（记录在案）：awaitable 形态要求**运行中的事件循环**
  （`asyncio.get_running_loop()`），否则抛 `RuntimeError` —— Python 无全局微任务
  队列，coroutine 必须由循环驱动；这是平台语义差异而非功能缺失
- **清理串行性**：多个清理函数按 LIFO 串行执行，前一个是异步则等待完成
  （对齐 TS 的 `task.then(dispose)` 链）
- **wrapper 实现**：返回对象实现 `__call__` 与 `__await__`；
  `await wrapper` 得到的清理函数幂等（二次调用无操作），对齐 TS `epoch` 门闩
- epoch 中断异步生成器的完整机制留待 B2，B1 先用 `fiber.state` 门闩

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - 四种形态各自的收集与清理
  - 多层 effect 嵌套的 LIFO 清理顺序
  - `execute` 同步抛错 → 已收集清理被执行且异常上抛
  - awaitable 失败 → 级联清理 + 日志记录
  - `await wrapper` 返回幂等清理函数
  - 失活 fiber 上调用 `effect()` 抛 `CordisError`
- AC2 `python -m ruff check .` 通过
- AC3 DisposableList 单元行为：push/delete/clear 倒序/len/迭代

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|
