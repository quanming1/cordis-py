"""Context —— 上下文。

对应 cordis ``packages/core/src/context.ts``：
- 根上下文：创建 root fiber 并持有全局共享的 reflect / registry
- 子上下文：由插件 fiber 创建，共享 root 的服务表与注册表
- B1 的 ``ctx.effect()`` 语义保留；B2 新增 ``ctx.plugin()`` / ``provide`` / ``get`` / ``set``
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .fiber import Effect, EffectHandle, Fiber
from .reflect import ReflectService
from .registry import RegistryService

EffectFn = Callable[[], Effect]


class Context:
    """上下文：副作用的作用域载体。"""

    def __init__(self, *, fiber: Fiber | None = None, parent: Context | None = None) -> None:
        if fiber is None:
            # 根上下文
            self.root = self
            self.fiber = Fiber(self)
            self.logger = logging.getLogger(f"cordis.{self.fiber.name}")
            self.reflect = ReflectService(self)
            self.registry = RegistryService(self)
        else:
            # 子上下文：共享 root 的服务表与注册表
            base = parent.root if parent is not None else None
            assert base is not None
            self.root = base
            self.fiber = fiber
            self.logger = logging.getLogger(f"cordis.{fiber.name}")
            self.reflect = base.reflect
            self.registry = base.registry

    def __repr__(self) -> str:
        return f"<Context fiber={self.fiber.name!r}>"

    def child_context(self, fiber: Fiber) -> Context:
        """为插件 fiber 创建共享 root 服务的子上下文。"""
        return Context(fiber=fiber, parent=self)

    # ── Effect（B1）─────────────────────────────────────────

    def effect(self, execute: EffectFn, label: str | None = None) -> EffectHandle:
        return self.fiber.effect(execute, label)

    def dispose(self) -> Any:
        """清理本上下文的全部副作用（委托 root fiber）。"""
        return self.root.fiber.dispose()

    # ── 插件与注册（B2）──────────────────────────────────────

    def plugin(self, plugin: Any, config: Any = None) -> Fiber:
        """装载一个插件（函数 / 类 / 带 apply 的对象），返回可 await 的 Fiber。"""
        return self.registry.plugin(plugin, config, ctx=self)

    def inject(self, inject: dict[str, Any] | list[str], callback: Callable) -> Fiber:
        """以声明式依赖注入的形式装载一个函数插件。"""
        return self.registry.inject(inject, callback, ctx=self)

    # ── 服务（B2，属性风格在 C1）────────────────────────────

    def provide(
        self,
        name: str,
        value: Any = None,
        check: Callable[[], bool] | None = None,
    ) -> Any:
        """注册一个服务（绑定当前 fiber 生命周期），返回下线清理函数。"""
        return self.reflect.provide(name, value, check, fiber=self.fiber)

    def get(self, name: str, strict: bool = True) -> Any:
        """读取服务值；strict 时提供者非 ACTIVE 返回 None。"""
        return self.reflect.get(name, strict)

    def set(self, name: str, value: Any) -> bool:
        """写入服务值（仅限提供者 fiber）。"""
        return self.reflect.set(name, value, fiber=self.fiber)
