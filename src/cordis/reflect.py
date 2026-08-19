"""Reflect —— 服务注册表（C1：isolate 隔离键 + 属性访问支撑）。

对应 cordis ``packages/core/src/reflect.ts``：
- 服务按 (name, isolate_key) 存储，隔离上下文持有独立键 → 同名服务
  在不同隔离 ctx 解析到不同实例
- ``provide`` 绑定提供者 fiber 生命周期；依赖方按自己 ctx 的隔离键解析
- ``notify`` 驱动依赖方 epoch 刷新 + ``internal/service`` 广播（带 isolate 过滤）
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .fiber import Fiber, FiberState

# 隔离键：object() 哨兵（可哈希、恒不等）
IsolateKey = object


@dataclass
class Impl:
    """一个服务实现（对应 cordis reflect.ts 的 Impl）。"""

    name: str
    value: Any
    fiber: Fiber
    check: Callable[[], bool] | None = None


def _gather_awaits(fibers: list[Fiber]) -> Any:
    """聚合并等待若干 fiber 的惯性（load/unload）完成；无循环时返回 None。"""
    tasks = []
    for fiber in fibers:
        awaiting = fiber.await_()
        if awaiting is not None:
            tasks.append(awaiting)
    if not tasks:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if len(tasks) > 1:
        return loop.create_task(asyncio.gather(*tasks))
    return tasks[0]


class ReflectService:
    """服务注册表：提供/查找/替换服务，并驱动依赖方状态刷新。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # name → {isolate_key: Impl}
        self.store: dict[str, dict[IsolateKey, Impl]] = {}
        # 每个服务名的默认共享键（非隔离 ctx 使用，对齐 TS root.isolate[name] ??=）
        self._keys: dict[str, IsolateKey] = {}

    def __repr__(self) -> str:
        return f"<ReflectService store={{{', '.join(self.store)}}}>"

    # ── 键解析 ──────────────────────────────────────────────

    def _key_for(self, name: str, ctx) -> IsolateKey:
        """取某个 ctx 下服务名的隔离键；未隔离时用默认共享键。"""
        key = ctx._isolate.get(name)
        if key is not None:
            return key
        return self._keys.setdefault(name, object())

    # ── 提供 / 读取 ──────────────────────────────────────────

    def provide(
        self,
        name: str,
        value: Any = None,
        check: Callable[[], bool] | None = None,
        *,
        fiber: Fiber | None = None,
        ctx=None,
    ) -> Any:
        """在指定 ctx（默认调用方，可先经 ``ctx.isolate`` 隔离）的 fiber 上注册服务。

        返回清理函数（服务下线）。服务绑定提供者 fiber 生命周期：
        提供者卸载 → 服务下线 → 依赖方卸载。
        """
        ctx = ctx or self.ctx
        fiber = fiber or ctx.fiber
        return fiber.effect(
            lambda: self._provide_impl(fiber, ctx, name, value, check),
            f"ctx.provide({name})",
        )

    def _provide_impl(
        self, fiber: Fiber, ctx, name: str, value: Any, check: Callable[[], bool] | None
    ) -> Callable[[], Any]:
        key = self._key_for(name, ctx)
        by_key = self.store.setdefault(name, {})
        if key in by_key:
            raise RuntimeError(f'service "{name}" has been registered')
        impl = Impl(name=name, value=value, fiber=fiber, check=check)
        by_key[key] = impl
        fiber.store[name] = impl
        if fiber.state is FiberState.ACTIVE:
            self.notify([name], ctx=ctx)

        def cleanup() -> Any:
            # TS: await Promise.allSettled(fibers.map(f => f.await()))
            if key in by_key:
                del by_key[key]
                if not by_key:
                    self.store.pop(name, None)
            fibers = self.notify([name], ctx=ctx)
            fiber.store.pop(name, None)
            return _gather_awaits(fibers)

        return cleanup

    def get(self, name: str, strict: bool = True, *, ctx=None) -> Any:
        """读取服务值；strict 时提供者非 ACTIVE 返回 None。"""
        impl = self._get_impl(name, strict, ctx=ctx)
        return impl.value if impl else None

    def set(self, name: str, value: Any, *, fiber: Fiber | None = None, ctx=None) -> bool:
        """只能由服务的提供者写入（对齐 TS set 的多 fiber 检查）。"""
        ctx = ctx or self.ctx
        by_key = self.store.get(name)
        impl = by_key.get(self._key_for(name, ctx)) if by_key else None
        if not impl:
            raise RuntimeError(f'cannot set property "{name}" without provide')
        if impl.fiber is not (fiber or ctx.fiber):
            raise RuntimeError(f'cannot set property "{name}" in multiple fibers')
        impl.value = value
        return True

    def _get_impl(self, name: str, strict: bool = True, *, ctx=None) -> Impl | None:
        """按 ctx 的隔离键查服务实现；strict 时提供者非 ACTIVE 视为不可用。"""
        ctx = ctx or self.ctx
        by_key = self.store.get(name)
        if not by_key:
            return None
        impl = by_key.get(self._key_for(name, ctx))
        if not impl:
            return None
        if strict and impl.fiber.state is not FiberState.ACTIVE:
            return None
        return impl

    # ── 依赖刷新 ─────────────────────────────────────────────

    def notify(self, names: list[str], *, ctx=None) -> list[Fiber]:
        """通知所有声明了受影响服务的 fiber，刷新其依赖并收集需重载者。

        依赖方按自身 ctx 的隔离键解析服务，广播以 ``ctx``（提供者视角，默认
        root）解析值并作为 thisArg（隔离域过滤）。
        """
        ctx = ctx or self.ctx
        affected: list[Fiber] = []
        for runtime in self.ctx.registry.values():
            for fiber in runtime.fibers:
                has_update = False
                for name in names:
                    if name not in fiber.inject:
                        continue
                    has_update = True
                    fiber._check_impl(name)
                if not has_update:
                    continue
                fiber._refresh()
                affected.append(fiber)
        # internal/service 事件（thisArg 带 isolate 过滤，对齐 TS）
        for name in names:
            impl = self._get_impl(name, strict=False, ctx=ctx)
            self.ctx.events.emit(
                ctx, "internal/service", name, impl.value if impl else None
            )
        return affected
