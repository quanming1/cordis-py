# Changelog

本项目的所有显著变更记录于此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
