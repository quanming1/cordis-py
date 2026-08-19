# PRD-C1：Reflect 完整版（isolate 隔离键 + Context 属性访问）

| 项 | 值 |
|---|---|
| 阶段 | C1 |
| 状态 | 已验收 |
| 分支 | feature/C1-reflect |
| 日期 | 2026-08-19 |

## 1. 背景与目标

B2 交付了按名字的全局服务表（`store: dict[name, Impl]`）。本阶段补上
cordis 空间维度的另一半：

- **isolate 隔离**：同一逻辑服务名在不同隔离上下文解析到**不同实例**
  （多实例应用各自提供同名服务互不干扰）
- **Context 属性访问**：`ctx.database` 直接拿到服务（B2 只有 `ctx.get('database')`）

对应 cordis 原实现：`reflect.ts` 的 isolate/provide/get/_getImpl/notify 的隔离键
部分、`context.ts` 的 `isolate()` 与 Proxy handler 的属性拦截。

## 2. 功能需求（FR）

- FR1 `ctx.isolate(name, label?) -> Context`：返回带隔离标记的新上下文；
  其 `isolate` 映射继承父映射并对 `name` 分配新键（对齐 TS `Object.create` 遮蔽链）
- FR2 服务按 isolate 键存储：`Reflect.store` 键为隔离键（每服务名一个
  共享键 + 隔离 ctx 覆盖键）；同一 ctx 内重名 provide 报错
- FR3 `ctx.provide/get/set` 按当前 ctx 的 isolate 键解析
- FR4 依赖注入联动（B2 notify/_refresh）按 isolate 键过滤：
  依赖插件的 ctx 与提供服务的 isolate 键须一致
- FR5 Context 属性访问：`ctx.database`（`__getattr__` 解析服务）；
  未提供时抛错；写入方向：内部白名单外走 `ctx.set`（对齐 TS：cannot set
  without provide）
- FR6 `event_filter` 由 isolate 键派生：`internal/service` 广播带上过滤
  （对齐 TS `Object.create(ctx)` + filter）

## 3. 非目标

- 不做 callable service（`serve.mixin` 混入属性，D 阶段）
- 不做 `ctx.intercept()` 配置拦截（C2）
- 不做 Proxy 对 `Symbol.iterator`/`has` 等全部元操作的拦截（Python 取舍，
  见 §4）

## 4. 技术方案

- **isolate 键**：Python 用 `object()` 哨兵作为隔离键（可哈希、恒不等）；
  `Context._isolate: dict[str, object]`，root 为 `{}`；`isolate(name)` 返回
  `Context.__copy__`（浅拷贝 + 新 `_isolate`：继承原 + `name→新哨兵`）
- **Reflect.store**：`dict[name, dict[key, Impl]]` 或 `dict[key, Impl]` +
  name→key 映射；提供时：`key = ctx._isolate.get(name)`；若 ctx 未隔离该名，
  用 root 分配的共享键（root._isolate[name] ??= 哨兵，对齐 TS `root.isolate[name] ??=`）
- **属性访问**：`Context.__getattr__`（仅常规属性检索失败时）解析服务；
  `Context.__setattr__` 对内部白名单（fiber/root/reflect/registry/events/logger/
  _isolate/event_filter 等）放行，其余交 `reflect.set`（未提供即抛错）
  —— 与 TS Proxy 全量拦截的有意差异：Python 以 `__getattr__` 提供读取属性风格，
  写入仍需 `ctx.set`（记入 PRD）
- **依赖过滤**：`notify` 与 `_refresh` 中按 `name` 的 isolate 键匹配
  （`ctx._isolate[name] == fiber_ctx._isolate[name]`）
- **internal/service**：广播 thisArg 带 isolate 过滤（复用 B3 `_event_filter`，
  C1 使其默认派生自 isolate 键一致性）

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - isolate：两隔离 ctx 提供同名服务互不影响、各自依赖方正确解析
  - `ctx.isolate()` 映射继承/遮蔽；root 共享键与非隔离 ctx 兼容
  - 属性访问 `ctx.database` 与 `ctx.get('database')` 等价
  - 未提供属性访问抛错；写入未提供服务抛错
  - B2 epoch 依赖刷新在 isolate 维度下正确（隔离 + 非隔离混存）
- AC2 `python -m ruff check .` 通过
- AC3 B1/B2/B3 既有 62 项测试保持通过（无回归）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|
