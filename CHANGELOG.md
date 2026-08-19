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
