"""Reflect —— 服务注册表（B2 简化版）。

对应 cordis ``packages/core/src/reflect.ts`` 的 store/provide/notify 部分
（隔离键 isolate 与 Context 属性访问在 C1 阶段实现）。

服务（service）由提供者的 fiber 通过 ``ctx.provide()`` 注册，绑定在提供者
的生命周期上：提供者卸载 → 服务自动下线；服务上线/下线/替换 → 通知依赖方重载。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .fiber import Fiber, FiberState


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
    """服务注册表：提供/查找/替换服务，并驱动依赖方的状态刷新。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.store: dict[str, Impl] = {}

    def __repr__(self) -> str:
        return f"<ReflectService store={{{', '.join(self.store)}}}>"

    # ── 提供 / 读取 ──────────────────────────────────────────

    def provide(
        self,
        name: str,
        value: Any = None,
        check: Callable[[], bool] | None = None,
        *,
        fiber: Fiber | None = None,
    ) -> Any:
        """在指定 fiber（默认调用方上下文所属 fiber）上注册一个服务。

        返回清理函数（服务下线）。服务绑定在提供者 fiber 的生命周期上：
        提供者卸载时服务下线，并通知依赖方卸载。
        """
        fiber = fiber or self.ctx.fiber
        return fiber.effect(
            lambda: self._provide_impl(fiber, name, value, check),
            f"ctx.provide({name})",
        )

    def _provide_impl(
        self, fiber: Fiber, name: str, value: Any, check: Callable[[], bool] | None
    ) -> Callable:
        if name in self.store:
            raise RuntimeError(f'service "{name}" has been registered')
        impl = Impl(name=name, value=value, fiber=fiber, check=check)
        self.store[name] = impl
        fiber.store[name] = impl
        if fiber.state is FiberState.ACTIVE:
            self.notify([name])

        def cleanup() -> Any:
            # TS: await Promise.allSettled(fibers.map(f => f.await()))
            if name in self.store:
                del self.store[name]
            fibers = self.notify([name])
            fiber.store.pop(name, None)
            return _gather_awaits(fibers)

        return cleanup

    def get(self, name: str, strict: bool = True) -> Any:
        """读取服务值；strict 时提供者非 ACTIVE 返回 None（对齐 TS strict）。"""
        impl = self._get_impl(name, strict)
        return impl.value if impl else None

    def set(self, name: str, value: Any, *, fiber: Fiber | None = None) -> bool:
        """只能由服务的提供者写入（对齐 TS set 的多 fiber 检查）。"""
        impl = self.store.get(name)
        if not impl:
            raise RuntimeError(f'cannot set property "{name}" without provide')
        if impl.fiber is not (fiber or self.ctx.fiber):
            raise RuntimeError(f'cannot set property "{name}" in multiple fibers')
        impl.value = value
        return True

    def _get_impl(self, name: str, strict: bool = True) -> Impl | None:
        """按名字查服务实现；strict 时提供者非 ACTIVE 视为不可用（对齐 TS _getImpl）。"""
        impl = self.store.get(name)
        if not impl:
            return None
        if strict and impl.fiber.state is not FiberState.ACTIVE:
            return None
        return impl

    # ── 依赖刷新 ─────────────────────────────────────────────

    def notify(self, names: list[str]) -> list[Fiber]:
        """通知所有声明了受影响服务的 fiber，刷新其依赖并收集需重载者。

        对应 cordis reflect.ts 的 notify（无 isolate 过滤的简化版）。
        """
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
        # internal/service 事件（对齐 TS notify：对每个受影响服务广播）
        for name in names:
            impl = self._get_impl(name, strict=False)
            self.ctx.events.emit(
                self.ctx, "internal/service", name, impl.value if impl else None
            )
        return affected
