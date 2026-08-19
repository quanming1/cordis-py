# PRD-A1：Rondo 规范资产 + Python 工程基建

| 项 | 值 |
|---|---|
| 阶段 | A1 |
| 状态 | approved（定稿冻结） |
| 分支 | feature/A1-foundation |
| 日期 | 2026-08-19 |

## 1. 背景与目标

cordis-py 是 cordis（TypeScript）的 Python 移植。开工前必须先落地工程约束体系，
保证后续所有阶段的开发在规范下运转：约束写进仓库、能被读取、能被强制。

本阶段交付两件事：
1. Rondo 方法完整资产（AGENTS.md / TODO.yaml / PROCESS.md / PRD 模板 / Git Hooks）
2. Python 工程基建（src 布局 / pytest / ruff / CHANGELOG）

## 2. 功能需求（FR）

- FR1 `AGENTS.md`：AI agent 与人类协作者的行为规范——工作方式（TODO 驱动）、
  代码风格（ruff / 中文注释）、Git 全 PR 流、PRD 驱动、测试要求
- FR2 `docs/TODO.yaml`：A~E 五阶段路线图，每步含 title / status / prd / acceptance
- FR3 `docs/PROCESS.md`：六步闭环（立项→评审→开发→验证→收尾→发布）推进办法
- FR4 `docs/prd/PRD-TEMPLATE.md`：PRD 模板，强制含非目标与可执行验收标准
- FR5 `.githooks/` 三件套并启用（`git config core.hooksPath .githooks`）：
  - `check_commit_msg.py`：提交校验逻辑（type 白名单 / scope 阶段存在性 /
    feat 带对应 PRD / 分支名与 scope 交叉校验）
  - `commit-msg`：调用上述脚本的 sh 包装
  - `pre-push`：main 双重保护 + develop 三重保护（禁删/禁 feature 直推/本地领先即拒）
- FR6 Python 基建：`pyproject.toml`（零运行时依赖）、`src/cordis/__init__.py`、
  `tests/` 含冒烟测试
- FR7 `CHANGELOG.md` 初始化（Keep a Changelog 格式）

## 3. 非目标

- 不实现任何 cordis 功能代码（那是 B+ 阶段的事；本阶段 src/cordis 仅放版本号占位）
- 不配置 CI（GitHub Actions 留待后续阶段按需引入）
- 不发布 PyPI

## 4. 技术方案

- hooks 用 Python 3.12 标准库实现（tomllib 不适用 YAML，TODO.yaml 解析用极简
  缩进行解析，避免引入 PyYAML 依赖——hooks 必须零依赖可运行）
- commit 格式：`<type>(<scope>): <subject>`；type ∈ feat/fix/docs/style/refactor/
  test/chore/perf/ci/build；feat/fix 的 scope 必须是 TODO.yaml 中真实存在的步骤 id；
  feat 提交的暂存区必须包含 `docs/prd/PRD-<scope>*` 文件
- 分支交叉校验：`feature/<id>-*` 分支上 feat/fix 的 scope 必须等于分支名中的 id

## 5. 验收标准（AC）

- AC1 `python -m pytest` 通过（冒烟测试 import cordis 成功）
- AC2 `python -m ruff check .` 通过
- AC3 commit-msg hook 实测：非法格式（如 `update files`）被拒绝；
  合法格式 `feat(A1): xxx` 且暂存含 PRD 时通过
- AC4 `git config core.hooksPath` 输出 `.githooks`
- AC5 PR 从 feature/A1-foundation 合入 develop（全 PR 流）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|
