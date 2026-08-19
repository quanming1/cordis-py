# Changelog

本项目的所有显著变更记录于此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.0] - 2026-08-19

### Added
- A1：Rondo 规范资产（AGENTS.md / TODO.yaml / PROCESS.md / PRD 模板 / Git Hooks）与 Python 工程基建
- B1：Effect 机制 —— `DisposableList`、`Fiber.effect()` 四形态（清理函数 / None /
  awaitable / 同步与异步生成器）、LIFO 串行清理、失败级联清理、幂等句柄、
  最小 `Fiber`/`Context` 骨架（对应 cordis fiber.ts 的 Effect 部分）
- B2：Registry 与插件生命周期 —— Plugin 三形态（函数/类/对象）、多实例、
  Fiber 完整状态机（PENDING/LOADING/ACTIVE/FAILED/DISPOSED/UNLOADING）、
  epoch 依赖刷新（服务上线自动装载 / 下线自动卸载 / 替换重载）、
  父子插件级联卸载（对应 cordis registry.ts / reflect.ts store 部分）
- B3：Events 事件系统 —— `ctx.on/once`（随 fiber 卸载自动移除）、五种派发
  （emit 广播 / parallel 并行聚合错误 / serial 串行短路 / bail 同步短路 /
  waterfall 续延）、thisArg 过滤、internal/plugin 与 internal/service 内部事件
  （对应 cordis events.ts）
- C1：Reflect 完整版 —— isolate 隔离键（同名服务按上下文解析不同实例）、
  `ctx.database` 属性访问（`__getattr__`/`__setattr__` 服务风格）、
  internal/service 广播按隔离域过滤（对应 cordis reflect.ts / context.ts）
- C2：intercept 配置拦截 —— `ctx.intercept(name, config)` 拦截链继承/覆盖、
  inject 声明配置写入插件拦截链、`service_config` 合并（base → 链 → head，
  支持 `Config.merge` 自定义）（对应 cordis context.ts / service.ts 的
  resolveConfig）
- D1：Service 体系 —— `Service` 基类（构造即自动提供、随提供者 fiber 上下线、
  `check` 谓词 / `Config.merge` / callable 服务）、`@Inject` 装饰器
  （类继承 + 函数，声明式依赖书写）（对应 cordis service.ts / registry.ts）
- D2：logger 日志服务 —— `Logger` 作用域包装（root / 插件 fiber 名 / child
  派生、等级控制、success/trace/fatal 方法映射），基于标准库 logging
  （对应 cordis logger.ts 核心层）
- E1：对标验收 —— 通读 cordis core/tests 全部 12 个 spec 并补齐缺口：
  `getEffects` 元数据树、类插件 `init()` 生命周期（异步阻塞装载）、
  `fiber.restart/update` 热更新、清理错误容错（记日志不传播）、
  root dispose 语义（uid 保持 0 可复用）、`repr` 对齐；
  新增逐用例映射的 `test_parity.py`（17 项）
