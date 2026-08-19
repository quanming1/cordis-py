#!/usr/bin/env python3
"""推送保护（Rondo 方法全 PR 流机器强制）。

main 双重保护：
1. 禁删 main
2. 非 main 分支禁推 main（含本地 merge 后的推送：新提交含 merge 即拒）

develop 三重保护：
1. 禁删 develop
2. 非 develop 分支禁推 develop（禁 feature 直推）
3. 当前 develop 本地领先远端即拒（本地有未经 PR 的提交）

用法：由 .githooks/pre-push 调用，从 stdin 读取待推送引用。
"""
from __future__ import annotations

import subprocess
import sys

ZERO_SHA = "0" * 40


def run_git(*args: str) -> tuple[int, str]:
    """执行 git 命令，返回 (返回码, 输出)。"""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as e:
        return 1, str(e)
    return result.returncode, result.stdout.strip()


def fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    print("提示：合入 develop/main 一律走 GitHub PR，本地禁止 merge 直推", file=sys.stderr)
    return 1


def check_push(local_ref: str, local_sha: str, remote_ref: str, remote_sha: str) -> int:
    branch = remote_ref.removeprefix("refs/heads/")
    current = run_git("branch", "--show-current")[1]

    if branch == "main":
        # 保护 1：禁删 main
        if local_sha == ZERO_SHA:
            return fail("禁止删除 main 分支")
        # 保护 2：非 main 分支禁推 main
        if current != "main":
            return fail(f"当前分支 {current!r} 不允许推送 main（main 只接受发布流程的 PR）")
        # 保护 2（续）：本地 merge 禁推 —— 新提交中含 merge commit 即拒
        if remote_sha != ZERO_SHA:
            code, out = run_git("rev-list", "--merges", f"{remote_sha}..{local_sha}")
            if code == 0 and out:
                return fail("检测到本地 merge commit 推往 main，禁止（release 合入走 GitHub PR）")
        return 0

    if branch == "develop":
        # 保护 1：禁删 develop
        if local_sha == ZERO_SHA:
            return fail("禁止删除 develop 分支")
        # 保护 2：非 develop 分支禁推 develop（禁 feature 直推）
        if current != "develop":
            return fail(
                f"当前分支 {current!r} 不允许直推 develop（feature 分支请走 GitHub PR 合入）"
            )
        # 保护 3：develop 本地领先即拒（本地有未经 PR 的提交）
        if remote_sha != ZERO_SHA and remote_sha != local_sha:
            code, _ = run_git("merge-base", "--is-ancestor", remote_sha, local_sha)
            if code == 0:
                return fail(
                    "本地 develop 领先于远端（存在未经 PR 的提交），禁止推送；"
                    "请先 git pull 同步，改动走 feature 分支 + PR"
                )
        return 0

    # 其他分支（feature/*、release/* 等）不限制
    return 0


def main() -> int:
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        code = check_push(*parts)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
