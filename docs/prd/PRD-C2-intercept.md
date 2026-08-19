# PRD-C2：intercept 配置拦截 + 声明式注入联动

| 项 | 值 |
|---|---|
| 阶段 | C2 |
| 状态 | 已验收 |
| 分支 | feature/C2-intercept |
| 日期 | 2026-08-19 |

## 1. 背景与目标

C1 交付了服务按 isolate 键隔离与属性访问。本阶段实现 cordis 空间维度的
最后一块拼图——**配置拦截**：

- `ctx.intercept('database', config)`：声明"本上下文范围内，名为 database
  的服务以这份配置实例化"
- 插件 `inject: { database: config }` 声明依赖时携带配置，该配置自动进入
  插件的拦截链，服务构造时按链合并

对应 cordis 原实现：`context.ts` 的 `intercept()`（约 15 行）、
`service.ts` 的 `resolveConfig()`（约 30 行）、`fiber.ts` 构造里的
inject→intercept 写入（约 10 行）。

## 2. 功能需求（FR）

- FR1 `ctx.intercept(name, config) -> Context`：返回新上下文，其拦截链继承
  当前链并对 `name` 覆盖配置（对齐 TS `Object.create` 遮蔽语义）
- FR2 插件装载时：`inject` 声明中带配置的条目（非 None）写入该插件 ctx 的
  拦截链顶层（对齐 TS Fiber 构造逻辑）
- FR3 `service_config(name, *, base, head)`（对应 TS `resolveConfig`）：
  - 从 ctx 的拦截链收集 `name` 的配置（祖先在前，链尾在后）
  - 合并顺序：base → 链上配置 → head（后者覆盖前者）
  - 无自定义 merge 时按浅合并（`{...a, ...b}` 语义）
- FR4 服务消费方通过 `ctx.intercept` 覆盖依赖服务配置；服务提供方构造时
  经 `ctx.service_config` 读取合并结果
- FR5 `Config.merge` 自定义合并支持（服务的 `<Svc>.Config.merge(...configs)`）

## 3. 非目标

- 不做 Service 基类（D1 提供，C2 只交付拦截/合并机制）
- 不做装饰器 `@Inject`（D1）
- 不做配置的运行时热更新（配置在装载期固定）

## 4. 技术方案

- `Context._intercept: dict[str, dict]`（name → config）；root 为 `{}`；
  子上下文继承 parent 的引用（同 `_isolate` 模式）；`intercept()` 返回拷贝+覆盖
- 拦截链收集：`chain = [config for ctx 链上 name in intercept]`——沿
  `ctx._intercept` + 提供者 ctx 的祖先链。由于 Python 无原型链，链以
  `_intercept_parent: Context | None` 显式保存（intercept() 时记录父 ctx）
- `fiber._load_callback` 前：把 inject 非 None 配置写入 fiber.ctx 的拦截链
- `service_config(name, base=None, head=None)` 挂在 ReflectService：
  `ctx.service_config(...)` 委托 reflect
- 合并：`Config.merge` 可调用时调用之；否则 `{**base, **chain_configs, **head}`
  （None 跳过）

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - intercept 产生新 ctx、链继承与覆盖（祖先配置保留、自身覆盖生效）
  - 插件 inject 带配置 → 服务消费方读取到合并配置
  - base/链/head 合并顺序与 None 跳过
  - `Config.merge` 自定义合并生效
- AC2 `python -m ruff check .` 通过
- AC3 B1~C1 既有 72 项测试保持通过（无回归）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|