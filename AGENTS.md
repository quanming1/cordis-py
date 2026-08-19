# AGENTS.md — cordis-py 协作规范

本文件是对所有 AI agent 与人类协作者的**强制行为规范**。动手前必须完整阅读并遵守。
规范出处：[Rondo 方法](https://quanming1.github.io/minimal-blog/posts/rondo-method/)。

## 1. 项目简介

cordis-py 是 [cordis](https://github.com/cordiverse/cordis)（TypeScript 时空可组合性元框架）的 Python 移植。
核心语义：**可撤销副作用**（时间维度）+ **响应式依赖注入**（空间维度）。
上游参考源码：`packages/core/src`（约 1,850 行 TS），移植时以语义对等为准，不逐行直译。

## 2. 工作方式

- 严格按 `docs/TODO.yaml` 的阶段顺序推进，不跳步、不越权
- 每个步骤开工前，必须有**定稿（approved）的 PRD**：`docs/prd/PRD-<步骤id>-<名称>.md`
- PRD 是开发的唯一依据：需求、实现、测试、验收全部对照 PRD；禁止开发 PRD 未定义的内容
- 验收不通过 = 未完成：PRD「验收标准」逐条核对，全部通过才更新 TODO / CHANGELOG
- 动手前先读相关文档与现有代码，遵循已有模式；不另起一套并行模式
- 只改任务范围内的文件；不引入未声明的依赖（本项目**零运行时依赖**，仅标准库）
- 需求变更走 PRD「变更记录」（见 `docs/PROCESS.md` §3.3）

## 3. 代码风格

- Python 3.12+，类型注解完整（公共 API 必须有）
- 代码注释与提交信息 subject 用中文；标识符用英文
- 格式与 lint 由 ruff 强制：`python -m ruff check .` 必须通过
- 模块顶部写中文 docstring 说明对应 cordis 原文件的映射关系
- 异步一律 asyncio；日志统一用标准库 `logging`
- 每个模块配套 `tests/test_<模块>.py`，测试先想清楚行为再写实现

## 4. Git 规范（全 PR 流）

### 分支模型

```
main      ← 仅存放可发布版本（永不直接提交）
  └─ develop  ← 日常集成分支（只接受 PR 合入）
       ├─ feature/<步骤id>-<名称>   新功能（从 develop 切出）
       ├─ release/<版本>            发布准备
       └─ hotfix/<名称>             紧急修复（从 main 切出，回灌 main+develop）
```

### 提交规范

```
<type>(<scope>): <subject>
```

- `subject` 用中文，一行说清这笔提交做了什么
- `type` 白名单：`feat` `fix` `docs` `style` `refactor` `test` `chore` `perf` `ci` `build`
- `feat` / `fix` 的 `scope` **必须是 `docs/TODO.yaml` 中真实存在的步骤 id**（如 `A1`、`B2`）
- `feat` 提交的暂存区**必须包含对应 PRD 文件**（`docs/prd/PRD-<scope>*`）
- `feature/<id>-*` 分支上的 `feat`/`fix`，其 `scope` 必须与分支名中的 `id` 一致

### 强制手段

- 规范不靠自觉：`.githooks/commit-msg` 与 `.githooks/pre-push` 机器校验，写错直接拒绝
- 所有合入 `develop` 的改动一律走 GitHub PR；本地**禁止** `git merge` 回 `develop`
- AI 与人类同规则，没有例外

## 5. 测试

- 测试框架 pytest，运行：`python -m pytest`
- 新功能必须附带测试；修 bug 先写复现测试再修
- 移植功能的验收以「语义对等测试」为准（对照 cordis 原仓库 `core/tests` 的用例语义）

## 6. 文档

- `docs/TODO.yaml`：唯一执行依据，状态随时与实际同步
- `docs/PROCESS.md`：六步闭环流程
- `docs/prd/`：所有 PRD，从 `PRD-TEMPLATE.md` 复制起稿
- `CHANGELOG.md`：每步收尾时追加条目

## 7. 安全边界

- 不引入 secrets、keys，更不提交进仓库
- 不引入任何运行时第三方依赖（dev 依赖仅 pytest / ruff）
- 不破坏现有公共 API；破坏性变更必须在 PRD 中声明并记录到 CHANGELOG
