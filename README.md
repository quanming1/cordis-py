# cordis-py

[Cordis](https://github.com/cordiverse/cordis) 的 Python 移植 —— 时空可组合性（Spatiotemporal Composability）元框架。

> 当前发布版本：0.4.0。核心语义已完成对 Cordis `core` 的对标验收；作为
> 0.x 版本，公共 API 在后续迭代中仍可能调整。

## 设计理念

Cordis 将插件系统的动态组合拆分为两个正交维度：

- **时间可组合性**（temporal）：组件被移除时，其副作用能被完全撤销 —— 可撤销副作用（Effect）
- **空间可组合性**（spatial）：组件能声明依赖并对依赖变化做出反应 —— 响应式依赖注入

Python 版完整保留了这套语义，并以 Python 原生能力重新实现：

- Effect 四形态统一为生成器/异步生成器（`yield` 清理函数）
- 依赖注入基于属性访问协议
- 零运行时第三方依赖，仅使用标准库

## 开发规范

本项目按 [Rondo 方法](https://quanming1.github.io/minimal-blog/posts/rondo-method/)运作：

- PRD 驱动：每个阶段开工前必须有定稿 PRD（`docs/prd/`）
- TODO 清单驱动：`docs/TODO.yaml` 是唯一执行依据
- 全 PR 流：main 只放发布版本，develop 只接受 PR 合入，本地禁止 merge
- 机器强制：`.githooks/` 校验提交格式与推送保护

详见 [AGENTS.md](AGENTS.md) 与 [docs/PROCESS.md](docs/PROCESS.md)。

## License

MIT
