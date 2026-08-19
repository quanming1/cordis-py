# PRD-D2：logger 日志服务

| 项 | 值 |
|---|---|
| 阶段 | D2 |
| 状态 | 已验收 |
| 分支 | feature/D2-logger |
| 日期 | 2026-08-19 |

## 1. 背景与目标

D1 交付了 Service 体系，但上下文日志仍是裸标准库 `logging.Logger`。
本阶段提供 cordis 风格的 **scoped logger**：

- `ctx.logger` 按上下文作用域命名（插件按 fiber 名）
- 派生（`child`）与等级控制
- 保留标准库 logging 后端（对齐项目约定：日志统一用 logging）

对应 cordis 原实现：`logger.ts`（约 246 行，含 console/WebUI 输出层——
本阶段只做核心 Logger 包装，输出层由标准 logging 承担）。

## 2. 功能需求（FR）

- FR1 `Logger` 包装类（scope 名 + 标准库后端）：
  - 方法：`trace/debug/info/success/warn/error/fatal`（success 映射 info）
  - 底层记录器名：`cordis.<scope>`（scope 为空时 `cordis`）
- FR2 `ctx.logger`：
  - root 上下文：scope `root`
  - 插件上下文：scope = fiber.name（对齐既有 `cordis.<fiber.name>`）
- FR3 `logger.child(name)`：派生 `scope.name` 子日志器
- FR4 等级控制：`logger.level`（设/取），委托标准库 `setLevel`
- FR5 `ctx.logger(...)` 可调用风格保留？——不（Python 无此惯例），
  用 `ctx.logger.child(...)`；PRD 记录差异

## 3. 非目标

- 不做 console/WebUI 输出插件（对应 TS 的 logger-console / logger-webui）
- 不做日志着色/富格式化（留给应用层 handler）
- 不改造 Fiber 内部日志（沿用 logging.getLogger）

## 4. 技术方案

- 新模块 `src/cordis/logger.py`：`class Logger`（标准库 logging 包装）
- `Context.logger` 从 `logging.Logger` 改为 `Logger` 实例：
  - root：`Logger("root")`；子上下文：`Logger(fiber.name)`
  - 方法签名与既有调用兼容（error/info/warn/debug）
- 内部使用处（fiber/events/reflect 的 `self.ctx.logger`）不经改动即可工作

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - scope 命名（root / 插件 fiber 名 / child 派生）
  - 等级过滤（setLevel 后低等级不输出）
  - success/trace/fatal 方法映射
  - 插件 ctx 的 logger 名正确（caplog 验证）
- AC2 `python -m ruff check .` 通过
- AC3 既有 92 项测试保持通过（无回归）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|