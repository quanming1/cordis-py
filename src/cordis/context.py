"""Context —— 上下文骨架。

对应 cordis ``packages/core/src/context.ts`` 的最小子集（B1 版）：
仅持有 fiber 与 logger，``ctx.effect()`` 委托 fiber。
服务注册 / isolate / intercept 等能力在 B2+ 阶段扩展。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .fiber import Effect, EffectHandle, Fiber

EffectFn = Callable[[], Effect]


class Context:
    """上下文：副作用的作用域载体。"""

    def __init__(self, fiber: Fiber | None = None) -> None:
        self.fiber = fiber if fiber is not None else Fiber()
        self.logger = logging.getLogger(f"cordis.{self.fiber.name}")

    def __repr__(self) -> str:
        return f"<Context fiber={self.fiber.name!r}>"

    def effect(self, execute: EffectFn, label: str | None = None) -> EffectHandle:
        """收集一个可撤销副作用（委托 fiber.effect）。"""
        return self.fiber.effect(execute, label)

    def dispose(self) -> Any:
        """清理当前上下文的全部副作用（委托 fiber.dispose）。"""
        return self.fiber.dispose()
