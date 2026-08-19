"""Fiber —— 插件实例的运行时容器与可撤销副作用内核。

对应 cordis ``packages/core/src/fiber.ts``：
- Effect 机制（四形态收集 / LIFO 清理 / 失败级联）完整保留（B1）
- 插件装载 + 完整状态机 + epoch 依赖刷新（B2）：
  - 每个 Fiber 承载一个插件实例；同插件多实例各自独立
  - 依赖（inject）就绪 → 自动装载；下线 → 自动卸载；替换 → 自动重载
  - Fiber 挂载到父上下文生命周期：父销毁 → 所有子插件级联清理

与 TypeScript 版的有意差异（见 PRD-B2 §4）：
- awaitable / 异步生成器形态要求运行中的事件循环（Python 无全局微任务队列）
- 无事件循环时同步插件走同步装载回退路径
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from .disposable import DisposableList

if TYPE_CHECKING:
    from .context import Context
    from .registry import PluginRuntime

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

# 依赖不满足时的 epoch 哨兵（对齐 TS 的 INACTIVE 常量）
INACTIVE = "__INACTIVE__"


class FiberState(Enum):
    """Fiber 生命周期状态（对齐 TS FiberState）。"""

    PENDING = auto()
    LOADING = auto()
    ACTIVE = auto()
    FAILED = auto()
    DISPOSED = auto()
    UNLOADING = auto()


class CordisError(Exception):
    """cordis 运行时错误（对应 TS ``CordisError``，code + 默认消息）。"""

    INACTIVE_EFFECT = "cannot create effect on inactive context"

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def _try_running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _dispatch_effect(
    result: Any, collect: Callable[[Disposable], None], is_active: Callable[[], bool]
) -> asyncio.Task | None:
    """解析 execute 的返回值形态，把清理函数交给 ``collect``。

    返回值：异步建立过程的 task（awaitable / 异步生成器形态），否则 None。
    """
    if result is None:
        return None
    if callable(result):
        collect(result)
        return None
    if inspect.isawaitable(result):
        loop = _try_running_loop()
        if loop is None:
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise RuntimeError("awaitable effect requires a running event loop")

        async def gather_await() -> None:
            dispose = await result
            if dispose is not None:
                collect(dispose)

        return loop.create_task(gather_await())
    if isinstance(result, AsyncIterator):
        loop = _try_running_loop()
        if loop is None:
            raise RuntimeError("async generator effect requires a running event loop")
        return loop.create_task(_collect_async_iter(result, collect, is_active))
    if isinstance(result, Iterator):
        for dispose in result:
            if dispose is not None and not callable(dispose):
                raise TypeError(f"Invalid effect: {dispose!r} 不是可调用的清理函数")
            if callable(dispose):
                collect(dispose)
        return None
    raise TypeError(f"Invalid effect: {result!r} 不是合法的 Effect 形态")


async def _collect_async_iter(
    aiter: AsyncIterator[Disposable], collect: Callable[[Disposable], None], is_active: Callable
) -> None:
    # 门闩检查在 __anext__ 之前：不满足后不再驱动生成器前进（对齐 TS epoch 中断）
    while True:
        if not is_active():
            aclose = getattr(aiter, "aclose", None)
            if aclose is not None:
                await aclose()
            return
        try:
            dispose = await anext(aiter)
        except StopAsyncIteration:
            return
        if dispose is not None and not callable(dispose):
            raise TypeError(f"Invalid effect: {dispose!r} 不是可调用的清理函数")
        if callable(dispose):
            collect(dispose)


def _run_serial(
    items: list[Disposable], logger: logging.Logger, label: str
) -> Awaitable[None] | None:
    """倒序串行执行清理函数（对齐 TS dispose 的 then 链语义）。

    - 同步清理立即执行（不推迟到事件循环）
    - 遇到异步清理：若在事件循环内则转为 task 继续串行；否则记录 error 并止步
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

    loop = _try_running_loop()
    if loop is None:
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
        self._meta: dict = {"label": label, "children": []}  # effect 元数据树（E1）

    def __repr__(self) -> str:
        state = "active" if self._active else "disposed"
        return f"<EffectHandle {self.label!r} {state}>"

    def _safe_collect(self, dispose: Any) -> None:
        """收集单个清理函数；None 跳过；嵌套 handle 记入 children；
        其余类型抛 TypeError（对齐 safeCollect）。"""
        if dispose is None:
            return
        if isinstance(dispose, EffectHandle):
            # 嵌套 effect：进入元数据树，其句柄本身也作为清理函数收集，
            # 并从 fiber 顶层列表移除（对齐 TS：collect 时 delete 顶层）
            self._meta["children"].append(dispose._meta)
            self._disposables.append(dispose)
            self._fiber._disposables.delete(dispose)
            return
        if not callable(dispose):
            raise TypeError(f"Invalid effect: {dispose!r} 不是可调用的清理函数")
        self._disposables.append(dispose)

    def _consume(self, result: Any) -> asyncio.Task | None:
        """解析 execute 的返回值形态，收集到句柄自身列表。

        awaitable / 异步生成器形态经 ``_start_task`` 托管：
        存入 ``_setup_task`` 并注册失败级联清理（对齐 TS ``task.catch(dispose)``）。
        """
        if result is None:
            return None
        if callable(result):
            self._safe_collect(result)
            return None
        if inspect.isawaitable(result):
            return self._start_task(self._collect_awaitable(result), result)
        if isinstance(result, AsyncIterator):
            return self._start_task(self._collect_async_effect(result), result)
        if isinstance(result, Iterator):
            for dispose in result:
                self._safe_collect(dispose)
            return None
        raise TypeError(f"Invalid effect: {result!r} 不是合法的 Effect 形态")

    async def _collect_awaitable(self, awaitable: Awaitable[Disposable]) -> None:
        self._safe_collect(await awaitable)

    async def _collect_async_effect(self, aiter: AsyncIterator[Disposable]) -> None:
        # 门闩检查在 __anext__ 之前：清理触发后不再驱动生成器前进
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
    ) -> asyncio.Task:
        """在有运行循环的前提下启动建立 task，并注册失败级联清理。

        无运行循环时关闭协程并抛 RuntimeError（Python 平台边界，见 PRD-B2 §4）。
        """
        loop = _try_running_loop()
        if loop is None:
            coro.close()
            close = getattr(origin, "close", None)
            if close is not None:
                close()
            raise RuntimeError("awaitable effect requires a running event loop")
        task = loop.create_task(coro)
        self._setup_task = task
        task.add_done_callback(self._on_setup_done)
        return task

    def _on_setup_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        # 建立失败：级联清理已收集部分（对齐 task.catch(dispose)），错误记日志
        self._fiber.logger.error("effect %r 建立失败：%s", self.label, exc)
        self._run_dispose_chain()

    def _run_dispose_chain(self) -> Awaitable[None] | None:
        items = self._disposables[::-1]
        self._disposables.clear()
        return _run_serial(items, self._fiber.logger, self.label)

    def __call__(self) -> Awaitable[None] | None:
        """触发清理（幂等）：先等建立 task 完成（若失败则跳过），再串行清理。

        清理过程抛错不向外传播——记入日志（对齐 fiber.spec 'dispose error'）。
        """
        if not self._active:
            return None
        self._active = False

        if self._setup_task is not None and not self._setup_task.done():
            loop = _try_running_loop()
            if loop is None:
                self._run_dispose_chain()
                return None
            return self._guarded(loop, self._wait_then_dispose())
        raw = self._run_dispose_chain()
        if raw is None:
            return None
        loop = _try_running_loop()
        if loop is None:
            return None
        return self._guarded(loop, raw)

    def _guarded(self, loop: asyncio.AbstractEventLoop, coro: Awaitable[None]) -> asyncio.Task:
        """包装清理协程：异常记日志而非传播（对齐 TS composeError 的清理路径）。"""

        async def guarded() -> None:
            try:
                await coro
            except BaseException as error:
                self._fiber.logger.error("effect %r 清理失败：%s", self.label, error)

        return loop.create_task(guarded())

    async def _wait_then_dispose(self) -> None:
        task = self._setup_task
        assert task is not None
        try:
            await task
        except BaseException:
            return
        raw = self._run_dispose_chain()
        if raw is not None:
            await raw

    def __await__(self):
        return self._wait_setup().__await__()

    async def _wait_setup(self) -> EffectHandle:
        """等副作用建立完成，resolve 自身（对齐 TS wrapper.then → disposeAsync）。"""
        if self._setup_task is not None:
            await self._setup_task
        return self


class Fiber:
    """插件实例的运行时容器。

    - root fiber（``runtime=None``）：整个应用的生命周期根，state 初始 ACTIVE
    - 插件 fiber（``runtime`` 非空）：承载一个插件实例，由 ``ctx.plugin()`` 创建
    """

    def __init__(
        self,
        parent: Context,
        config: Any = None,
        inject: dict[str, Any] | None = None,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self.parent = parent
        self.runtime = runtime
        self.inject = inject or {}
        self.config = config
        self._error: Any = None
        self._runner_epoch: str = INACTIVE
        self._inertia: asyncio.Task | None = None
        self._store: dict[str, Any] = {}
        self.store: dict[str, Any] | None = None
        self._disposables = DisposableList[Callable[[], Any]]()
        self._instance: Any = None

        if runtime is not None:
            self.uid = parent.registry.counter
            self.state = FiberState.PENDING
            self.logger = logging.getLogger(f"cordis.{self.name}")
            # 子上下文：共享 root 的服务表与注册表
            self.ctx = parent.child_context(self)
            # inject 声明的配置写入本插件的拦截链（对齐 TS Fiber 构造，
            # 仅在带配置时拷贝替换，避免污染父链）
            injected = {n: c for n, c in self.inject.items() if c is not None}
            if injected:
                self.ctx._intercept = {**self.ctx._intercept, **injected}
            # 构造即宣告插件实例（对齐 TS Fiber 构造里的 internal/plugin 事件）
            self.ctx.emit("internal/plugin", self)
            # 装载前先按声明检查依赖（对齐 TS 构造里的 _checkImpl 循环）
            for name in self.inject:
                self._check_impl(name)
            # 装载动作挂在父 fiber 的生命周期上（父销毁 → 本实例级联卸载）
            self._remove_runtime: Callable[[], bool] | None = None
            self._lifecycle = parent.fiber.effect(self._load_callback, f"ctx.plugin({self.name})")
        else:
            self.uid = 0
            self.state = FiberState.ACTIVE
            self.store = {}
            self.ctx = parent  # root fiber 的 ctx 即根上下文
            self.logger = logging.getLogger("cordis.root")
            self._lifecycle = None

    # ── 元信息 ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        """沿父链路找第一个具名 runtime，否则 root。"""
        fiber: Fiber | None = self
        while fiber is not None and fiber.runtime is not None:
            if fiber.runtime.name:
                return fiber.runtime.name
            fiber = fiber.parent.fiber
        return "root"

    def __repr__(self) -> str:
        return f"<Fiber {self.name!r} #{self.uid} {self.state.name}>"

    # ── 断言 ────────────────────────────────────────────────

    def assert_active(self) -> None:
        """断言 fiber 未销毁（对齐 TS assertActive：仅检查 uid 非空）。"""
        if self.uid is None:
            raise CordisError("INACTIVE_EFFECT")

    def _is_active(self) -> bool:
        """异步收集中断门闩：拆线后停止驱动。"""
        return self.uid is not None and self._runner_epoch != INACTIVE

    # ── Effect（B1 语义）────────────────────────────────────

    def effect(self, execute: Callable[[], Effect], label: str | None = None) -> EffectHandle:
        """收集一个可撤销副作用（B1 语义，断言改用 uid）。"""
        self.assert_active()
        handle = EffectHandle(self, label or "anonymous")
        try:
            result = execute()
            handle._consume(result)
        except BaseException:
            handle._run_dispose_chain()
            raise
        self._disposables.push(handle)
        return handle

    # ── 生命周期装载 ────────────────────────────────────────

    def _load_callback(self) -> Effect:
        """生命周期 effect 的执行体：注册为 runtime 活实例并首次刷新依赖。"""
        assert self.runtime is not None
        self._remove_runtime = self.runtime.fibers.push(self)
        try:
            self.config = resolve_config(self.runtime, self.config)
            self._refresh()
        except BaseException as error:
            self.logger.error(error)
            self._error = error
        return self._unload_callback

    def _unload_callback(self) -> Any:
        """生命周期 effect 的清理体：同步拆除 + 排干全部卸载惯性。

        拆除（uid 置空 / runtime 移除 / 触发卸载）是同步的，保证无事件循环
        环境下也能完成；卸载会依次经历 reload→unload 链，此处循环等待
        直到状态机静止（对齐 TS ``while (this.inertia) await this.inertia``）。
        """
        assert self.runtime is not None
        assert self._remove_runtime is not None
        self.uid = None
        # 宣告插件实例销毁（对齐 TS 卸载时的 internal/plugin 事件）
        self.ctx.emit("internal/plugin", self)
        if self.ctx.registry.has(self.runtime.callback):
            self._remove_runtime()
            if not len(self.runtime.fibers):
                self.ctx.registry.delete(self.runtime.callback)
        self._set_epoch(INACTIVE)

        async def drain() -> None:
            while self._inertia is not None:
                task = self._inertia
                await task
            return None

        # 无进行中的惯性（含同步回退路径）→ 立即完成
        if self._inertia is None:
            return None
        return drain()

    def _start_load_sync(self) -> None:
        """无事件循环的同步装载（仅支持同步插件）。"""
        assert self.runtime is not None
        loop = _try_running_loop()
        if loop is not None:
            raise RuntimeError("expected synchronous load path outside event loop")
        if inspect.isclass(self.runtime.callback):
            self._instance = self.runtime.callback(self.ctx, self.config)
            if hasattr(self._instance, "init"):
                result = self._instance.init()
                self._start_sync_collect(result)
            return
        result = self.runtime.callback(self.ctx, self.config)
        if inspect.isawaitable(result) or isinstance(result, AsyncIterator):
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise RuntimeError("async plugin requires a running event loop")
        self._start_sync_collect(result)

    def _start_sync_collect(self, result: Any) -> None:
        """同步路径的形态收集（awaitable/async-iter 形态在同步装载中禁止）。"""
        if inspect.isawaitable(result) or isinstance(result, AsyncIterator):
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise RuntimeError("async plugin requires a running event loop")
        _dispatch_effect(result, self._disposables.push, self._is_active)

    async def _start_load(self) -> None:
        """事件循环内的异步装载：执行回调（含类 init）并等待异步建立完成。"""
        assert self.runtime is not None
        if inspect.isclass(self.runtime.callback):
            self._instance = self.runtime.callback(self.ctx, self.config)
            if hasattr(self._instance, "init"):
                # 类插件 init()：返回值经形态派发收集（可异步阻塞装载，
                # 对齐 TS Service.init 的 pending inject 语义）
                result = self._instance.init()
                task = _dispatch_effect(result, self._disposables.push, self._is_active)
                if task is not None:
                    await task
            return
        result = self.runtime.callback(self.ctx, self.config)
        task = _dispatch_effect(result, self._disposables.push, self._is_active)
        if task is not None:
            await task

    # ── 状态机 ──────────────────────────────────────────────

    def _check_impl(self, name: str) -> None:
        """按依赖声明检查名为 name 的服务是否可用（按本 fiber ctx 的隔离键），更新 _store。"""
        impl = self.ctx.reflect._get_impl(name, ctx=self.ctx)
        if not impl:
            self._store.pop(name, None)
            return
        try:
            if impl.check and not impl.check():
                self._store.pop(name, None)
                return
        except BaseException as error:
            self.logger.error(error)
            self._store.pop(name, None)
            return
        self._store[name] = impl

    def _refresh(self) -> None:
        """按依赖服务的宿主 uid 计算 epoch 指纹；任何依赖缺失则 INACTIVE。"""
        epoch = ""
        for name in self.inject:
            impl = self._store.get(name)
            if not impl:
                epoch = INACTIVE
                break
            epoch += f":{impl.fiber.uid}"
        self._set_epoch(epoch)

    def _set_epoch(self, epoch: str) -> None:
        if epoch == self._runner_epoch:
            return
        old = self._runner_epoch
        self._runner_epoch = epoch
        if self._inertia is not None:
            return
        if epoch != INACTIVE and old == INACTIVE:
            # 记录装载发起时的基准（供 reload tail 判定；TS 在 reload 首个
            # await 前同步捕获，task 运行时捕获会被拆线污染）
            self._reload_basis = epoch
            self._update_state(FiberState.LOADING)
            self._start_transition(self._reload())
        else:
            self._update_state(FiberState.UNLOADING)
            self._start_transition(self._unload())

    def _start_transition(self, coro: Awaitable[None]) -> None:
        """调度一次状态转换（load/unload）；无循环时同步回退。"""
        loop = _try_running_loop()
        if loop is None:
            coro.close()
            self._transition_sync()
            return
        self._inertia = loop.create_task(coro)

    def _transition_sync(self) -> None:
        """无事件循环的同步状态转换：reload 或 unload 各走同步变体。"""
        if self._runner_epoch != INACTIVE:
            self._reload_sync()
        else:
            self._unload_sync()

    def _reload_sync(self) -> None:
        old_epoch = self._runner_epoch
        try:
            self.store = dict(self._store)
            self._start_load_sync()
        except BaseException as error:
            self.logger.error(error)
            self._error = error
            self._runner_epoch = INACTIVE
        if self._runner_epoch == old_epoch:
            self._update_state(None)
        else:
            self._update_state(FiberState.UNLOADING)
            self._unload_sync()

    def _unload_sync(self) -> None:
        for handle in self._disposables.clear():
            handle()
        # handle() 可能返回 None（同步完成）或 task（async 清理，无循环时内部已记 error）
        self.store = None
        if self._runner_epoch != INACTIVE:
            self._reload_sync()
        else:
            self._update_state(None)

    async def _reload(self) -> None:
        """依赖就绪：装载插件并进入 ACTIVE（或根据新 epoch 转 UNLOADING）。"""
        old_epoch = getattr(self, "_reload_basis", self._runner_epoch)
        try:
            self.store = dict(self._store)
            await self._start_load()
        except BaseException as error:
            self.logger.error(error)
            self._error = error
            self._runner_epoch = INACTIVE
        self._inertia = None
        if self._runner_epoch == old_epoch:
            self._update_state(None)
        else:
            self._update_state(FiberState.UNLOADING)
            self._start_transition(self._unload())

    async def _unload(self) -> None:
        """依赖缺失/拆线：清空全部副作用，回到 PENDING 或按新 epoch 重载。"""
        tasks = []
        for handle in self._disposables.clear():
            task = handle()
            if task is not None:
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)
        self.store = None
        self._inertia = None
        if self._runner_epoch == INACTIVE:
            self._update_state(None)
        else:
            self._update_state(FiberState.LOADING)
            self._start_transition(self._reload())

    def _get_state(self) -> FiberState:
        if self.uid is None:
            return FiberState.DISPOSED
        if self._error is not None:
            return FiberState.FAILED
        if self._runner_epoch != INACTIVE:
            return FiberState.ACTIVE
        return FiberState.PENDING

    def _update_state(self, next_state: FiberState | None) -> None:
        old = self.state
        self.state = next_state or self._get_state()
        if old is self.state:
            return
        # 状态在 ACTIVE ↔ 非 ACTIVE 间变化时，通知依赖本 fiber 服务的插件
        if old is not FiberState.ACTIVE and self.state is not FiberState.ACTIVE:
            return
        for name, by_key in self.ctx.reflect.store.items():
            for impl in by_key.values():
                if impl.fiber is self:
                    self.ctx.reflect.notify([name])
                    break

    # ── 对外接口 ────────────────────────────────────────────

    async def await_(self) -> Fiber:
        """等待本 fiber 的惯性（load/unload）完成；失败抛出存储的错误。"""
        while self._inertia is not None:
            await self._inertia
        if self._error is not None:
            raise self._error
        return self

    def __await__(self):
        return self.await_().__await__()

    def getEffects(self) -> list[dict]:
        """效果元数据森林（对齐 TS ``fiber.getEffects()``，供调试与快照）。

        每个已收集 effect 句柄输出 ``{"label", "children"}``，嵌套 effect
        进入父级 children。
        """
        return [handle._meta for handle in self._disposables if hasattr(handle, "_meta")]

    async def restart(self) -> None:
        """卸载全部副作用后按当前 config 重载（对齐 TS ``fiber.restart()``）。"""
        if self.runtime is None:
            raise RuntimeError("root fiber cannot restart")
        self.assert_active()
        self._set_epoch(INACTIVE)
        await self.await_()  # 等卸载完成（若本已 PENDING 则立即返回）
        self._refresh()  # 依赖仍在 → 重新装载
        await self.await_()

    def update(self, config: Any) -> Awaitable[None]:
        """热更新配置并重启（对齐 TS ``fiber.update(config)``；await 等待完成）。"""
        if self.runtime is None:
            raise RuntimeError("root fiber cannot update")
        self.assert_active()
        self.config = resolve_config(self.runtime, config)
        self._error = None
        return self.restart()

    def dispose(self) -> Awaitable[None] | None:
        """整体销毁 fiber。

        - 插件实例：触发生命周期 effect（从 runtime 拆线 + 清空副作用）
        - root：清空全部 effect（子插件级联卸载）；uid 保持 0，可继续使用
          （对齐 plugin.spec 'root dispose'）
        """
        if self.uid is None:
            return None
        if self._lifecycle is not None:
            return self._lifecycle()
        tasks = []
        for handle in self._disposables.clear():
            task = handle()
            if task is not None:
                tasks.append(task)
        # root 语义：uid 保持 0（TS root 可 restart）；仅清空效果
        self.uid = 0
        self.state = FiberState.ACTIVE
        if len(tasks) == 1:
            return tasks[0]
        if len(tasks) > 1:
            loop = _try_running_loop()
            if loop is None:
                return None
            return asyncio.gather(*tasks)
        return None


def resolve_config(runtime: PluginRuntime, config: Any) -> Any:
    """应用插件的配置校验器；无校验器则原样返回。"""
    validator = runtime.config_validator
    if validator is None:
        return config
    return validator(config)
