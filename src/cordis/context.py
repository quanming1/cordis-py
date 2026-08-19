"""Context —— 上下文。

对应 cordis ``packages/core/src/context.ts`` + ``reflect.ts`` 的属性访问部分：
- 根/子上下文共享 reflect / registry / events
- ``ctx.isolate(name)`` 派生带独立隔离键的上下文
- ``ctx.database`` 属性风格读取服务（``__getattr__``）；写入方向仍是 ``ctx.set``
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .events import EventsService
from .fiber import Effect, EffectHandle, Fiber
from .logger import Logger
from .reflect import ReflectService
from .registry import RegistryService

EffectFn = Callable[[], Effect]

# 内部属性白名单：__setattr__ 直接写入实例，不视为服务
_INTERNAL = {
    "root", "fiber", "reflect", "registry", "events", "logger",
    "_isolate", "event_filter",
}


class Context:
    """上下文：副作用的作用域载体。"""

    def __init__(self, *, fiber: Fiber | None = None, parent: Context | None = None) -> None:
        if fiber is None:
            # 根上下文
            self.root = self
            self.fiber = Fiber(self)
            self.logger = Logger(self.fiber.name)
            self.reflect = ReflectService(self)
            self.registry = RegistryService(self)
            self.events = EventsService(self)
        else:
            # 子上下文：共享 root 的服务表/注册表/事件总线
            base = parent.root if parent is not None else None
            assert base is not None
            self.root = base
            self.fiber = fiber
            self.logger = Logger(fiber.name)
            self.reflect = base.reflect
            self.registry = base.registry
            self.events = base.events

        # 隔离键映射：name → isolate key（子上下文继承父 ctx 的隔离映射，isolate() 时拷贝替换）
        self._isolate: dict[str, object] = (
            parent._isolate if parent is not None else {}
        )
        # 拦截链：name → config（intercept() 时拷贝替换）；_intercept_parent 指向父 ctx
        self._intercept: dict[str, Any] = parent._intercept if parent is not None else {}
        self._intercept_parent: Context | None = parent
        # thisArg 过滤钩子（None 时按 isolate 一致性派生）
        self.event_filter: Callable[[Context], bool] | None = None

    def _event_filter(self, hook_ctx: Context) -> bool:
        """事件派发过滤（对齐 TS Context.filter）。

        显式设置 ``event_filter`` 时用它；否则按 isolate 映射一致性派生：
        对每个已隔离名，要求 hook_ctx 的键与自身相同。
        """
        if self.event_filter is not None:
            return self.event_filter(hook_ctx)
        for name, key in self._isolate.items():
            if key is not hook_ctx._isolate.get(name):
                return False
        return True

    def __repr__(self) -> str:
        return f"<Context fiber={self.fiber.name!r}>"

    def child_context(self, fiber: Fiber) -> Context:
        """为插件 fiber 创建共享 root 服务的子上下文。"""
        return Context(fiber=fiber, parent=self)

    # ── 隔离 ────────────────────────────────────────────────

    def isolate(self, name: str, label: object | None = None) -> Context:
        """返回带独立隔离键的上下文（对齐 TS ``ctx.isolate(name, label)``）。

        新上下文共享本上下文的 fiber/root 与 isolate 继承映射，仅对 ``name``
        分配新键——此后 ``provide``/``get`` 的该服务名解析到独立实例。
        """
        ctx = object.__new__(Context)
        ctx.root = self.root
        ctx.fiber = self.fiber
        ctx.logger = self.logger
        ctx.reflect = self.reflect
        ctx.registry = self.registry
        ctx.events = self.events
        ctx.event_filter = None
        mapped = dict(self._isolate)
        mapped[name] = label if label is not None else object()
        ctx._isolate = mapped
        ctx._intercept = self._intercept
        ctx._intercept_parent = self
        return ctx

    # ── 配置拦截（C2）────────────────────────────────────────

    def intercept(self, name: str, config: Any) -> Context:
        """返回配置拦截上下文：本范围内名为 ``name`` 的服务以 ``config`` 实例化。

        拦截链继承当前 ctx 并对 ``name`` 覆盖配置（对齐 TS ``Object.create`` 遮蔽）。
        """
        ctx = object.__new__(Context)
        ctx.root = self.root
        ctx.fiber = self.fiber
        ctx.logger = self.logger
        ctx.reflect = self.reflect
        ctx.registry = self.registry
        ctx.events = self.events
        ctx.event_filter = None
        ctx._isolate = self._isolate
        mapped = dict(self._intercept)
        mapped[name] = config
        ctx._intercept = mapped
        ctx._intercept_parent = self
        return ctx

    def _collect_intercept(self, name: str) -> list[Any]:
        """沿拦截链收集 ``name`` 的全部配置（祖先在前，最近声明在后）。"""
        chain: list[Any] = []
        ctx: Context | None = self
        while ctx is not None:
            if name in ctx._intercept:
                chain.insert(0, ctx._intercept[name])
            ctx = ctx._intercept_parent
        return chain

    def service_config(self, name: str, *, base: Any = None, head: Any = None) -> Any:
        """读取合并后的服务配置（base → 拦截链 → head，后者覆盖前者）。"""
        return self.reflect.service_config(name, base=base, head=head, ctx=self)

    # ── 属性访问（服务风格）─────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # 下划线开头视为内部属性缺失，不当作服务
        if name.startswith("_"):
            raise AttributeError(name)
        impl = self.reflect._get_impl(name, strict=True, ctx=self)
        if impl is None:
            raise AttributeError(f'cannot get property "{name}" without inject')
        return impl.value

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _INTERNAL or name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        # 服务写入：仅提供者 fiber 可写（未提供即抛错，对齐 TS proxy set）
        self.reflect.set(name, value, fiber=self.fiber, ctx=self)

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

    # ── 服务（B2）───────────────────────────────────────────

    def provide(
        self,
        name: str,
        value: Any = None,
        check: Callable[[], bool] | None = None,
    ) -> Any:
        """注册一个服务（绑定当前 fiber 生命周期），返回下线清理函数。"""
        return self.reflect.provide(name, value, check, fiber=self.fiber, ctx=self)

    def get(self, name: str, strict: bool = True) -> Any:
        """读取服务值；strict 时提供者非 ACTIVE 返回 None。"""
        return self.reflect.get(name, strict, ctx=self)

    def set(self, name: str, value: Any) -> bool:
        """写入服务值（仅限提供者 fiber）。"""
        return self.reflect.set(name, value, fiber=self.fiber, ctx=self)

    # ── 事件总线（B3）────────────────────────────────────────

    def on(
        self,
        name: str,
        listener: Callable,
        options: bool | dict | None = None,
    ) -> Any:
        return self.events.on(name, listener, options, ctx=self)

    def once(
        self,
        name: str,
        listener: Callable,
        options: bool | dict | None = None,
    ) -> Any:
        return self.events.once(name, listener, options, ctx=self)

    def emit(self, name: str, *args: Any) -> None:
        return self.events.emit(name, *args)

    async def parallel(self, name: str, *args: Any) -> None:
        return await self.events.parallel(name, *args)

    async def serial(self, name: str, *args: Any) -> Any:
        return await self.events.serial(name, *args)

    def bail(self, name: str, *args: Any) -> Any:
        return self.events.bail(name, *args)

    def waterfall(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self.events.waterfall(name, *args, **kwargs)
