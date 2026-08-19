#!/usr/bin/env python3
"""提交信息校验（Rondo 方法机器强制）。

校验规则：
1. 格式：<type>(<scope>): <subject>，subject 中文
2. type 白名单：feat/fix/docs/style/refactor/test/chore/perf/ci/build
3. feat/fix 的 scope 必须是 docs/TODO.yaml 中真实存在的步骤 id
4. feat 提交的暂存区必须包含对应 PRD 文件（docs/prd/PRD-<scope>*）
5. 分支名交叉校验：feature/<id>-* 分支上的 feat/fix，scope 必须等于 <id>

用法：由 .githooks/commit-msg 调用，参数为 commit-msg 文件路径。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ── 裁剪点（按项目修改）───────────────────────────────────────
TODO_PATH = Path("docs/TODO.yaml")
PRD_DIR = "docs/prd"
TYPE_WHITELIST = {
    "feat", "fix", "docs", "style", "refactor",
    "test", "chore", "perf", "ci", "build",
}
STRICT_TYPES = {"feat", "fix"}  # 这些 type 强制 scope 校验
# ────────────────────────────────────────────────────────────

COMMIT_RE = re.compile(r"^(\w+)(?:\(([^)]+)\))?: (.+)$")
ID_LINE_RE = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$")
BRANCH_RE = re.compile(r"^feature/([^-/]+)-")


def run_git(*args: str) -> str:
    """执行 git 命令并返回输出，失败返回空串。"""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def load_stage_ids() -> set[str]:
    """从 TODO.yaml 收集全部阶段/步骤 id（极简缩进解析，零依赖）。"""
    if not TODO_PATH.exists():
        return set()
    ids: set[str] = set()
    for line in TODO_PATH.read_text(encoding="utf-8").splitlines():
        # 跳过注释行
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        m = ID_LINE_RE.match(line)
        if m:
            ids.add(m.group(1).strip("'\""))
    return ids


def staged_prd_prefixes() -> set[str]:
    """返回暂存区中所有 PRD 文件名的前缀集合（如 PRD-A1）。"""
    out = run_git("ls-files", "--cached", "--", PRD_DIR)
    prefixes = set()
    for filepath in out.splitlines():
        name = Path(filepath).name
        if name.startswith("PRD-"):
            prefixes.add(name)  # 形如 PRD-A1-foundation.md
    return prefixes


def main() -> int:
    if len(sys.argv) < 2:
        print("错误：缺少 commit-msg 文件参数", file=sys.stderr)
        return 1

    msg_file = Path(sys.argv[1])
    try:
        first_line = msg_file.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        print("错误：无法读取提交信息", file=sys.stderr)
        return 1

    m = COMMIT_RE.match(first_line)
    if not m:
        print(
            f"错误：提交信息格式不合法：{first_line!r}\n"
            f"要求：<type>(<scope>): <subject>，subject 用中文\n"
            f"type 白名单：{'/'.join(sorted(TYPE_WHITELIST))}",
            file=sys.stderr,
        )
        return 1

    ctype, scope, subject = m.group(1), m.group(2), m.group(3)
    if ctype not in TYPE_WHITELIST:
        print(
            f"错误：type 不在白名单：{ctype}\n白名单：{'/'.join(sorted(TYPE_WHITELIST))}",
            file=sys.stderr,
        )
        return 1

    # feat/fix 强制 scope 校验
    if ctype in STRICT_TYPES:
        if not scope:
            print(f"错误：{ctype} 提交必须带 scope（TODO.yaml 中的步骤 id）", file=sys.stderr)
            return 1

        stage_ids = load_stage_ids()
        if scope not in stage_ids:
            print(
                f"错误：scope {scope!r} 不在 {TODO_PATH} 的阶段 id 中\n"
                f"已有 id：{', '.join(sorted(stage_ids)) or '（无）'}",
                file=sys.stderr,
            )
            return 1

        # feat 额外强制：暂存必须包含对应 PRD
        if ctype == "feat":
            staged = staged_prd_prefixes()
            if not any(name.startswith(f"PRD-{scope}") for name in staged):
                print(
                    f"错误：feat({scope}) 提交的暂存区必须包含 {PRD_DIR}/PRD-{scope}*.md\n"
                    f"当前暂存的 PRD：{', '.join(sorted(staged)) or '（无）'}",
                    file=sys.stderr,
                )
                return 1

        # 分支名交叉校验
        branch = run_git("branch", "--show-current")
        bm = BRANCH_RE.match(branch) if branch else None
        if bm and scope != bm.group(1):
            print(
                f"错误：分支 {branch} 上的 {ctype} 提交 scope 必须是 {bm.group(1)!r}，"
                f"实际是 {scope!r}",
                file=sys.stderr,
            )
            return 1

    if not subject.strip():
        print("错误：subject 不能为空，且用中文描述", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
