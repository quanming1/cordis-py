"""Events —— 事件总线。

对应 cordis ``packages/core/src/events.ts``：
- ``ctx.on/once`` 绑定监听器到当前 fiber，fiber 卸载时自动移除（effect 联动）
- 五种派发模式：emit（同步广播）/ parallel（异步并行，聚合错误）/
  serial（异步串行，短路）/ bail（同步串行，短路）/ waterfall（续延链式）
- thisArg 过滤：emit 首参为对象时按该对象过滤非 global 监听器

与 TypeScript 版的差异：监听器签名统一为 ``(*args)``（Python 无 this 绑定），
thisArg 仅用于过滤（记入 PRD-B3 §4）。
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from .disposable import DisposableList  # noqa: F401  (保留类型引用)


def is_bailed(value: Any) -> bool:
    """事件派发的短路判定（对齐 TS isBailed：非 null / 非 false）。"""
    return value is not None and value is not False


class Hook:
    """一个已注册的监听器（对齐 TS Hook）。"""

    def __init__(
        self, ctx, callback: Callable, *, prepend: bool = False, global_: bool = False
    ) -> None:
        self.ctx = ctx
        self.callback = callback
        self.prepend = prepend
        self.global_ = global_


class EventsService:
    """事件总线：注册/派发/listener 生命周期管理。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._hooks: dict[str, list[Hook]] = {}

    def __repr__(self) -> str:
        return f"<EventsService {len(self._hooks)} events>"

    # ── 解析 ────────────────────────────────────────────────

    def _split_this_arg(self, name: str, args: tuple):
        """拆出前置 thisArg（对齐 TS `emit(thisArg, name, ...args)` 重载）。

        ``name`` 本身具 ``_event_filter`` 能力时视为 thisArg，其后的串为
        事件名与参数。返回 (this_arg, name, 剩余参数)。
        """
        if hasattr(name, "_event_filter"):
            assert args, "缺少事件名"
            this_arg = name
            return this_arg, args[0], args[1:]
        return None, name, args

    def _resolve(self, name: str, args: list, this_arg=None):
        """解析 (this_arg, 匹配的监听器回调列表)。

        - ``this_arg`` 可前置传入（``emit(thisArg, ...)`` 形式）
        - 也可作为 args 首参携带（服务对象 thisArg 场景）
        - global 监听器恒入选；thisArg 存在时按其 filter 过滤
        """
        if this_arg is None and args and hasattr(args[0], "_event_filter"):
            this_arg = args[0]
            args = args[1:]

        callbacks = []
        for hook in self._hooks.get(name, []):
            if hook.global_ or this_arg is None:
                callbacks.append(hook.callback)
                continue
            if this_arg._event_filter(hook.ctx):
                callbacks.append(hook.callback)
        return this_arg, callbacks

    # ── 注册 ────────────────────────────────────────────────

    def register(
        self,
        label: str,
        hooks: list[Hook],
        callback: Callable,
        options: dict,
        *,
        ctx=None,
        fiber=None,
    ) -> Any:
        """经指定 ctx 的 fiber（默认调用方上下文）的 effect 注册监听器。"""
        ctx = ctx or self.ctx
        fiber = fiber or ctx.fiber
        return fiber.effect(
            lambda: self._do_register(ctx, hooks, callback, options), label
        )

    def _do_register(
        self, ctx, hooks: list[Hook], callback: Callable, options: dict
    ) -> Callable[[], bool]:
        hook = Hook(
            ctx,
            callback,
            prepend=options.get("prepend", False),
            global_=options.get("global", False),
        )
        if hook.prepend:
            hooks.insert(0, hook)
        else:
            hooks.append(hook)
        return lambda: self.unregister(hooks, callback)

    def unregister(self, hooks: list[Hook], callback: Callable) -> bool:
        for index, hook in enumerate(hooks):
            if hook.callback is callback:
                hooks.pop(index)
                return True
        return False

    def on(
        self,
        name: str,
        listener: Callable,
        options: bool | dict | None = None,
        *,
        ctx=None,
    ) -> Any:
        """绑定监听器到指定 ctx 的 fiber；返回移除函数（可调用/可等待，幂等）。"""
        if isinstance(options, bool):
            options = {"prepend": options}
        options = options or {}
        ctx = ctx or self.ctx
        ctx.fiber.assert_active()
        hooks = self._hooks.setdefault(name, [])
        return self.register(f"ctx.on({name})", hooks, listener, options, ctx=ctx)

    def once(
        self,
        name: str,
        listener: Callable,
        options: bool | dict | None = None,
        *,
        ctx=None,
    ) -> Any:
        """首次触发后自移除。"""
        state: dict = {}

        def wrapped(*args: Any) -> Any:
            if "dispose" in state:
                state["dispose"]()
            return listener(*args)

        dispose = self.on(name, wrapped, options, ctx=ctx)
        state["dispose"] = dispose
        return dispose

    # ── 派发 ────────────────────────────────────────────────

    def emit(self, name: str, *args: Any) -> None:
        """同步广播。监听器抛错直接上抛（对齐 TS emit）。"""
        this_arg, name, args = self._split_this_arg(name, args)
        _, callbacks = self._resolve(name, list(args), this_arg)
        for callback in callbacks:
            callback(*args)

    async def parallel(self, name: str, *args: Any) -> None:
        """异步并行；收集所有错误并抛 ExceptionGroup（对齐 TS AggregateError）。"""
        this_arg, name, args = self._split_this_arg(name, args)
        _, callbacks = self._resolve(name, list(args), this_arg)

        async def run(callback: Callable) -> Any:
            result = callback(*args)
            if inspect.isawaitable(result):
                result = await result
            return result

        results = await asyncio.gather(*(run(cb) for cb in callbacks), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise ExceptionGroup("event parallel dispatch failed", errors)

    async def serial(self, name: str, *args: Any) -> Any:
        """异步串行；首个非 None/false 返回值短路并返回。"""
        this_arg, name, args = self._split_this_arg(name, args)
        _, callbacks = self._resolve(name, list(args), this_arg)
        for callback in callbacks:
            result = callback(*args)
            if inspect.isawaitable(result):
                result = await result
            if is_bailed(result):
                return result

    def bail(self, name: str, *args: Any) -> Any:
        """同步串行；首个非 None/false 返回值短路并返回。"""
        this_arg, name, args = self._split_this_arg(name, args)
        _, callbacks = self._resolve(name, list(args), this_arg)
        for callback in callbacks:
            result = callback(*args)
            if is_bailed(result):
                return result

    def waterfall(self, name: str, *args: Any, inner: Callable | None = None) -> Any:
        """续延链式派发：前一个监听器把 next 续延传给下一个；最后执行 inner。

        监听器签名：``(…payload, next)``；inner 经 ``ctx.waterfall(name, ..., inner=cb)``
        或直接作为最后一个参数传入。
        """
        this_arg, name, args = self._split_this_arg(name, args)
        _, callbacks = self._resolve(name, list(args), this_arg)
        if inner is None and args:
            inner = args[-1]
            args = args[:-1]
        payload = list(args)

        def next_() -> Any:
            if not callbacks:
                if inner is not None:
                    return inner(*payload)
                return None
            callback = callbacks.pop(0)
            return callback(*payload, next_)

        return next_()
