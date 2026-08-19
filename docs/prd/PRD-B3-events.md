# PRD-B3：Events 事件系统

| 项 | 值 |
|---|---|
| 阶段 | B3 |
| 状态 | 已验收 |
| 分支 | feature/B3-events |
| 日期 | 2026-08-19 |

## 1. 背景与目标

B2 交付了插件生命周期 + epoch 依赖刷新，但插件间的"解耦通信"仍缺失。
本阶段实现 cordis 的事件总线：`ctx.on/once` + 五种派发模式。

对应 cordis 原实现：`packages/core/src/events.ts`（约 178 行）。
生命周期内部事件（internal/plugin / internal/status / internal/service）在本阶段
接入 Fiber 装载/卸载，为后续阶段和用户插件提供统一观察入口。

## 2. 功能需求（FR）

- FR1 `ctx.on(name, listener, options?) -> 移除函数`：绑定监听器到当前 fiber，
  该 fiber 卸载时监听器自动移除（对齐 TS：经 `ctx.fiber.effect` 注册）
  - `options.prepend`：插到监听器最前
  - `options.global`：全局监听器，不受派发过滤影响
- FR2 `ctx.once(name, listener, options?)`：首次触发后自移除
- FR3 五种派发模式：
  - `ctx.emit(name, *args)`：同步广播，无返回
  - `ctx.parallel(name, *args)`：异步并行，聚合错误为 `AggregateError`
  - `ctx.serial(name, *args)`：异步串行，首个非 None/false 返回值短路并返回
  - `ctx.bail(name, *args)`：同步串行，首个非 None/false 返回值短路并返回
  - `ctx.waterfall(name, *args)`：链式传递，前一个返回值作为 rest 参数传给下一个，
    尾部执行默认回调
- FR4 事件参数 `thisArg` 过滤（TS 的 `_resolve`）：支持在 emit 时指定前置
  thisArg（服务/ctx），按该对象过滤非 global 监听器（对齐 TS filter 语义）
- FR5 内部事件：
  - `internal/plugin(fiber)`：插件实例创建（装载前）与销毁
  - `internal/status(fiber, old_state)`：状态变化
  - `internal/service(name, value)`：服务上线/下线
- FR6 监听器返回在五种模式中的短路值：True / 非 None 的值（TS `isBailed`）

## 3. 非目标

- 不做 callable service / 服务属性混入 `ctx.database`（C1 的 Proxy 层）
- 不做 serve.symbol 的装饰器与 serve.mixin 属性化（D 阶段）
- 不做 event 过滤器的完整 isolate 维度（C1 接入 isolate 键）

## 4. 技术方案

- 新模块 `src/cordis/events.py`：`EventsService`（五种派发 + on/once + register）
- `EventsService` 作为 root 共享单例（同 reflect/registry），事件表
  `dict[str, list[Hook]]`；Hook 记录 ctx / callback / prepend / global
- `ctx.on` 通过 `ctx.fiber.effect` 注册，effect 清理时 `unregister`
- `_resolve(name, args)`：
  - 首参为对象/可调用时视为 thisArg，从参数中弹出
  - 非 global 监听器按 thisArg 的 filter 过滤（B3 先支持 service 名过滤：
    `hook.ctx.fiber.name` 与 thisArg 匹配；C1 升级为 isolate 键过滤）
- `internal/plugin` / `internal/service` 由 Fiber/reflect 在 B2 既有事件点增补 emit
- 异常处理：emit 同步模式下监听器抛错直接向上抛（TS 行为）；列表继续执行则
  与 TS 一致（TS emit 循环不 try-catch）

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - on/once 绑定、prepend/global 语义、手动移除
  - 五种派发各自语义（广播 / 并行聚合错误 / 串行短路 / 同步短路 / 瀑布传递）
  - 监听器随 fiber 卸载自动移除（effect 清理）
  - internal/plugin、internal/service 在装载/卸载/服务上下线时触发
- AC2 `python -m ruff check .` 通过
- AC3 B1/B2 既有 43 项测试保持通过（无回归）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|
