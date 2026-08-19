"""Fiber —— 副作用容器。

对应 cordis ``packages/core/src/fiber.ts`` 中 Effect 相关部分：
- ``FiberState`` 状态枚举（B1 仅 ACTIVE/DISPOSED，完整状态机在 B2）
- ``Fiber.effect()``：统一收集四种形态的副作用，交出可撤销句柄
- ``EffectHandle``：effect 的返回值，可调用（触发清理）亦可等待（建立完成后得到自身）

与 TypeScript 版的有意差异（见 PRD-B1 §4）：
awaitable / 异步生成器形态要求运行中的事件循环（Python 无全局微任务队列）。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from enum import Enum, auto
from typing import Any

from .disposable import DisposableList

# 清理函数：无参调用，返回 None 或 awaitable
Disposable = Callable[[], Any]

# Effect 的四种形态（execute 的返回值）
Effect = (
    Disposable                      # 1. 清理函数
    | None                          # 2. 无清理
    | Awaitable[Disposable]         # 3. awaitable，解析后为清理函数
    | Iterator[Disposable]          # 4. 同步生成器，逐个 yield 清理函数
    | AsyncIterator[Disposable]     # 5. 异步生成器，逐个 yield 清理函数
)


class FiberState(Enum):
    """Fiber 生命周期状态（B1 子集，B2 扩展 LOADING/FAILED/UNLOADING）。"""

    PENDING = auto()
    ACTIVE = auto()
    DISPOSED = auto()


class CordisError(Exception):
    """cordis 运行时错误（对应 TS ``CordisError``，code + 默认消息）。"""

    INACTIVE_EFFECT = "cannot create effect on inactive context"

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def _run_serial(
    items: list[Disposable], logger: logging.Logger, label: str
) -> Awaitable[None] | None:
    """倒序串行执行清理函数（对齐 TS dispose 的 then 链语义）。

    - 同步清理立即执行（不推迟到事件循环）
    - 遇到异步清理：若在事件循环内则转为 task 继续串行；否则记录 error 并止步
    - 返回值：后续异步链的 task（全同步时为 None）
    """
    index = 0
    pending: Awaitable[None] | None = None
    while index < len(items):
        result = items[index]()
        if inspect.isawaitable(result):
            pending = result
            break
        index += 1

    if pending is None:
        return None

    rest = items[index + 1:]

    async def _continue() -> None:
        await pending
        for dispose in rest:
            result = dispose()
            if inspect.isawaitable(result):
                await result

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("effect %r 的异步清理无法在无事件循环时执行", label)
        pending.close()
        return None
    return loop.create_task(_continue())


class EffectHandle:
    """effect() 返回的句柄：可调用（触发清理）、可等待（建立完成后得到自身）。

    对齐 TS 的 ``AsyncDisposable``：
    - ``handle()`` 触发清理（幂等），返回可选的清理完成 awaitable
    - ``await handle`` 等副作用建立完成，resolve 为 handle 自身（可调用）
    """

    def __init__(self, fiber: Fiber, label: str) -> None:
        self._fiber = fiber
        self.label = label
        self._disposables: list[Disposable] = []
        self._setup_task: asyncio.Task[None] | None = None
        self._active = True  # 幂等门闩（对齐 TS runner.epoch）

    def __repr__(self) -> str:
        state = "active" if self._active else "disposed"
        return f"<EffectHandle {self.label!r} {state}>"

    # ── 内部：收集与解析 ──────────────────────────────────────

    def _safe_collect(self, dispose: Any) -> None:
        """收集单个清理函数；None 跳过；其余类型抛 TypeError（对齐 safeCollect）。"""
        if dispose is None:
            return
        if not callable(dispose):
            raise TypeError(f"Invalid effect: {dispose!r} 不是可调用的清理函数")
        self._disposables.append(dispose)

    def _consume(self, result: Any) -> asyncio.Task[None] | None:
        """解析 execute 的返回值形态，收集清理函数。

        返回值：建立过程的 task（awaitable / 异步生成器形态），否则 None。
        """
        if result is None:
            return None
        if callable(result):
            self._safe_collect(result)
            return None
        if inspect.isawaitable(result):
            return self._start_task(self._collect_awaitable(result), result)
        if isinstance(result, AsyncIterator):
            return self._start_task(self._collect_async_iter(result))
        if isinstance(result, Iterator):
            # 同步生成器：立即驱动到底（对齐 TS 的同步迭代循环）
            for dispose in result:
                self._safe_collect(dispose)
            return None
        raise TypeError(f"Invalid effect: {result!r} 不是合法的 Effect 形态")

    async def _collect_awaitable(self, awaitable: Awaitable[Disposable]) -> None:
        self._safe_collect(await awaitable)

    async def _collect_async_iter(self, aiter: AsyncIterator[Disposable]) -> None:
        # 门闩检查在 __anext__ 之前：清理触发后不再驱动生成器前进
        # （对齐 TS: while (true) { if (epoch 变化) return; await iter.next() }）
        while True:
            if not self._active:
                aclose = getattr(aiter, "aclose", None)
                if aclose is not None:
                    await aclose()
                return
            try:
                dispose = await anext(aiter)
            except StopAsyncIteration:
                return
            self._safe_collect(dispose)

    def _start_task(
        self, coro: Coroutine[Any, Any, None], origin: Any = None
    ) -> asyncio.Task[None]:
        """在有运行循环的前提下启动建立 task，并注册失败级联清理。

        无运行循环时关闭协程并抛 RuntimeError（Python 平台边界，见 PRD-B1 §4）。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            close = getattr(origin, "close", None)
            if close is not None:
                close()
            raise
        task = loop.create_task(coro)
        self._setup_task = task
        # 对齐 TS: task?.catch(dispose).catch(err => logger.error(err))
        task.add_done_callback(self._on_setup_done)
        return task

    def _on_setup_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        # 建立失败：级联清理已收集部分（对齐 task.catch(dispose)），错误记日志
        self._fiber.logger.error("effect %r 建立失败：%s", self.label, exc)
        self._run_dispose_chain()

    # ── 清理 ────────────────────────────────────────────────

    def _run_dispose_chain(self) -> Awaitable[None] | None:
        """取出已收集清理（倒序）并串行执行（对齐 TS dispose）。"""
        items = self._disposables[::-1]
        self._disposables.clear()
        return _run_serial(items, self._fiber.logger, self.label)

    # ── 对外接口 ────────────────────────────────────────────

    def __call__(self) -> Awaitable[None] | None:
        """触发清理（幂等）：先等建立 task 完成（若失败则跳过），再串行清理。"""
        if not self._active:
            return None
        self._active = False

        if self._setup_task is not None and not self._setup_task.done():
            # 建立过程还在进行：等它结束后再清理（对齐 task.then(dispose)）
            return asyncio.get_running_loop().create_task(self._wait_then_dispose())
        return self._run_dispose_chain()

    async def _wait_then_dispose(self) -> None:
        task = self._setup_task
        assert task is not None
        try:
            await task
        except BaseException:
            # 建立失败：级联清理已在 _on_setup_done 完成；
            # 吞掉异常避免未处理任务异常（对齐 TS task.catch(dispose) 吞掉 rejection）
            return
        self._run_dispose_chain()

    def __await__(self):
        return self._wait_setup().__await__()

    async def _wait_setup(self) -> EffectHandle:
        """等副作用建立完成，resolve 自身（对齐 TS wrapper.then → disposeAsync）。"""
        if self._setup_task is not None:
            await self._setup_task
        return self


class Fiber:
    """副作用容器（B1 骨架）：持有 disposable 列表，提供 effect 收集与统一清理。"""

    def __init__(self, name: str = "root") -> None:
        self.name = name
        self.state = FiberState.ACTIVE
        self.logger = logging.getLogger(f"cordis.{name}")
        self._disposables = DisposableList[Callable[[], Any]]()

    def __repr__(self) -> str:
        return f"<Fiber {self.name!r} {self.state.name}>"

    def assert_active(self) -> None:
        """断言 fiber 处于活跃状态，否则抛 CordisError（对齐 TS assertActive）。"""
        if self.state is not FiberState.ACTIVE:
            raise CordisError("INACTIVE_EFFECT")

    def effect(
        self,
        execute: Callable[[], Effect],
        label: str | None = None,
    ) -> EffectHandle:
        """收集一个可撤销副作用。

        ``execute`` 建立副作用并交出撤销操作，四种形态见 :data:`Effect`。
        同步失败（execute 抛错 / 形态非法）时清理已收集部分并向上抛出。
        """
        self.assert_active()
        handle = EffectHandle(self, label or "anonymous")
        try:
            result = execute()
            handle._consume(result)
        except BaseException:
            handle._run_dispose_chain()
            raise
        # 句柄自身进入 fiber 的收集列表（对齐 disposables.push(this._disposables.push(wrapper))）
        self._disposables.push(handle)
        return handle

    def dispose(self) -> Awaitable[None] | None:
        """倒序触发全部 effect 清理；各 effect 内部串行，effect 之间并行。

        对齐 TS ``_unload`` 的 ``Promise.all(disposables.map(dispose))``。
        """
        if self.state is FiberState.DISPOSED:
            return None
        self.state = FiberState.DISPOSED
        tasks: list[Awaitable[None]] = []
        for handle in self._disposables.clear():
            task = handle()
            if task is not None:
                tasks.append(task)
        if len(tasks) > 1:
            return asyncio.gather(*tasks)
        return tasks[0] if tasks else None
