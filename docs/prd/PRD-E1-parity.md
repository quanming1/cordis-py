# PRD-E1：对标验收 —— 移植 cordis 原 core/tests 语义

| 项 | 值 |
|---|---|
| 阶段 | E1 |
| 状态 | 已验收 |
| 分支 | feature/E1-parity |
| 日期 | 2026-08-19 |

## 1. 背景与目标

A~D 阶段已按 cordis@4.0.0-rc 的源码语义逐模块移植。本阶段直接对照上游
`packages/core/tests` 全部 12 个 spec 文件（plain 语义部分），补齐缺口并
新增逐文件映射的**对标测试**，作为整个移植的验收基准。

对照结论（已通读全部 spec）：
- 已覆盖：events 五种派发 / isolate / service 生命周期 / 插件三形态 /
  effect 四形态与失败级联 / epoch 依赖刷新 / 嵌套插件注册表清理 /
  plugin error → FAILED / 异步生成器中断粒度

**缺口清单**（本阶段补齐）：
1. `Fiber.getEffects()` —— effect 元数据树（label + children 嵌套）
2. 类插件 `init()` 生命周期方法（TS `Service.init` symbol）—— 实例化后调用，
   返回值作为清理函数；异步 init 阻塞装载（pending inject）
3. `fiber.restart()` / `fiber.update(config)` —— 重启与配置热更新
4. dispose 错误容错：清理函数抛错 → dispose 正常 resolve + 记日志（不传播）
5. root dispose 语义：uid 保持 0（对齐 TS root 可复用）
6. `repr(Context)` 对齐 `Context <name>`

**记录的差异**（不实现，写进对标文档）：
- traceable（service 属性跟随调用方 ctx）—— TS getTraceable/Shadow 机制
- associate（`ctx.foo.bar` 关联属性）
- wrapper proxy（`Object.hasOwn(fiber, 'state') === false` 等对象身份细节）
- 类插件 `Service.invoke/extend` 的框架层拦截（可经由 `service_config` 组合实现）
- logger-console/WebUI 输出层

## 2. 功能需求（FR）

- FR1 `Fiber.getEffects() -> list[EffectMeta]`：label + children 树
  （嵌套 effect 作为 children，对齐 dispose.spec 用例 1-3）
- FR2 类插件生命周期：
  - 实例化后若有 `init()` 方法 → 调用，返回值作为清理函数收集
  - `init()` 可异步（await 其完成才 ACTIVE，期间依赖方不装载）
- FR3 `fiber.restart()`：卸载全部副作用后按当前 config 重载
- FR4 `fiber.update(config)`：热更新配置并重启（`await fiber` 可等待完成）
- FR5 清理错误容错：`EffectHandle` 触发清理时遇异常 → `logger.error` 记录，
  不向上传播（对齐 fiber.spec 'dispose error'）
- FR6 root dispose：`root.fiber.dispose()` 清空 effect、uid 保持 0
  （对齐 plugin.spec 'root dispose'；root 可继续 use）

## 3. 非目标

- 见 §1 差异清单（traceable/associate/wrapper/框架 invoke）
- 不移植测试辅助（getHookSnapshot / withTimers mock 设施）

## 4. 技术方案

- `fiber.py`：
  - `EffectHandle` 增加 `_meta = {"label", "children"}`；`_safe_collect` 检测
    嵌套 handle 追加 children；`Fiber.getEffects()` 输出森林
  - 类插件 `_start_load`/`_start_load_sync`：实例化后调 `init()`，返回值
    （可 awaitable/清理函数）走形态派发收集
  - `restart()`：`_set_epoch(INACTIVE)` + `_refresh()` + `await await_()`
  - `update(config)`：resolve_config 后重启
  - 清理链容错：`__call__` 返回的 task 内 catch 异常 → logger.error
  - root dispose：不清 uid
- `context.py`：`__repr__` → `Context <{fiber.name}>`
- 新增 `tests/test_parity.py`：逐 spec 映射对标（文件头注释标明
  `parity: <上游文件> <用例>`）

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，`test_parity.py` 覆盖：
  - getEffects 树（孤立 + 嵌套 + generator 组合）
  - init 生命周期（同步清理 / 异步阻塞装载）
  - restart / update 热更新（回调再次调用、config 更新）
  - dispose 错误容错（resolve + 日志、幂等）
  - root dispose（uid=0、effect 清空、可复用）
  - plugin error → FAILED / nested plugins registry 清理 /
    inactive context 三操作抛错 / root dispose 幂等
  - inertia lock（装载中的依赖变化不打断）
- AC2 `python -m ruff check .` 通过
- AC3 既有 99 项测试保持通过（无回归，含 B1 dispose 幂等语义修订）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|