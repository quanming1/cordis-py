# PROCESS.md — 六步闭环推进办法

cordis-py 按 Rondo 方法推进开发。任何阶段没有定稿的 PRD 不开工。

## 1. 六步闭环

```
立项 → 评审 → 开发 → 验证 → 收尾 → 发布
```

| 步骤 | 动作 | 产物 / 状态 |
|---|---|---|
| 1. 立项 | 从 `docs/TODO.yaml` 选定步骤，标 `in_progress`，复制模板写 PRD | `docs/prd/PRD-<步骤id>-<名称>.md`（状态：草稿） |
| 2. 评审 | 逐条核对需求与验收标准 | PRD 状态：`approved`（定稿冻结，变更需走「变更记录」） |
| 3. 开发 | 按 PRD 实现；分支 `feature/<步骤id>-<名称>` | 代码 + 测试；PRD 状态：开发中 |
| 4. 验证 | 对照 PRD「验收标准」逐条执行（lint / test / build / 手动） | 全部通过 → 收尾；失败 → 回开发 |
| 5. 收尾 | 三联动缺一不可：PRD 标已验收 + TODO 标 done + CHANGELOG 追加 | push feature 分支 → GitHub PR 合入 develop |
| 6. 发布 | release 分支 + 版本冻结 + 回归 + tag | `release/<ver>` → main + tag |

## 2. PRD 生命周期状态机

```
草稿 → 评审 → approved → 开发中 → 已验收
```

- 定稿（approved）后 PRD 冻结，正文改动必须走「变更记录」
- 「已验收」表示验收标准全部执行通过

## 3. 需求变更双路径

收到新需求时，先判断再动手：

- **路径 A（新开 PRD）**：新阶段 / 全新主题 / 范围超出原 PRD 边界
  → TODO.yaml 选定/新增步骤（标 in_progress）→ 复制模板新建 PRD → 回到立项
- **路径 B（修改原 PRD）**：同一步骤内、同主题、对原 FR/AC 的细化修正
  → 修改正文 + **必须**在末尾「变更记录」追加（日期 + 变更 + 理由）
  → 重新核对受影响的验收标准

## 4. 收尾三联动（缺一不可）

1. PRD 状态改为「已验收」（验收标准逐条核对后）
2. `docs/TODO.yaml` 对应步骤标 `done`
3. `CHANGELOG.md` 追加条目

然后 push feature 分支 → GitHub PR → 合入 develop。本地禁止 merge。

## 5. 全 PR 流保护

- main 永不直接提交；develop 只接受 PR 合入
- 推送保护由 `.githooks/pre-push` 强制（main 双重保护 + develop 三重保护）
- 提交格式由 `.githooks/commit-msg` 强制（详见 AGENTS.md §4）

## 6. 存量反推

本项目从零开始，无存量代码；如未来引入存量，按 Rondo 方法 §3.4 反推补写 TODO/PRD。
