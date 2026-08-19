"""Effect 机制测试（PRD-B1 AC1）：四形态收集、LIFO 清理、失败级联、幂等语义。

无 pytest-asyncio 依赖：异步场景统一用 ``asyncio.run`` 驱动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from cordis import Context, CordisError, EffectHandle, FiberState


async def settle(steps: int = 4) -> None:
    """让出若干轮事件循环，等待 done_callback 与级联清理链跑完。"""
    for _ in range(steps):
        await asyncio.sleep(0)


# ── 形态 1：清理函数 ─────────────────────────────────────────


def test_function_effect_sync_dispose() -> None:
    # 纯同步场景（无事件循环）：清理立即执行
    ctx = Context()
    order: list[str] = []

    def setup() -> object:
        order.append("setup")
        return lambda: order.append("cleanup")

    handle = ctx.effect(setup, "fn")
    assert isinstance(handle, EffectHandle)
    assert order == ["setup"]
    result = handle()
    assert result is None  # 全同步清理：无返回 task
    assert order == ["setup", "cleanup"]


def test_none_effect() -> None:
    # 形态 2：无清理
    ctx = Context()
    called: list[str] = []

    def setup() -> None:
        called.append("ran")
        return None

    handle = ctx.effect(setup)
    assert handle() is None
    assert called == ["ran"]


# ── 形态 3：awaitable ────────────────────────────────────────


def test_awaitable_effect() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def setup():
            order.append("setup-start")
            await asyncio.sleep(0)
            order.append("setup-done")
            return lambda: order.append("cleanup")

        handle = ctx.effect(setup, "awaitable")
        dispose = await handle  # 等建立完成，得到句柄自身
        assert dispose is handle
        assert order == ["setup-start", "setup-done"]
        # 清理函数是同步的：调用后立即完成，无需返回 task（对齐 TS 全同步时返回 undefined）
        assert handle() is None
        assert order == ["setup-start", "setup-done", "cleanup"]

    asyncio.run(main())


def test_awaitable_requires_running_loop() -> None:
    # 有意差异：无事件循环时 awaitable 形态直接失败（PRD-B1 §4）
    ctx = Context()

    async def setup():
        return lambda: None

    with pytest.raises(RuntimeError):
        ctx.effect(setup)


# ── 形态 4：同步生成器 ───────────────────────────────────────


def test_sync_generator_effect() -> None:
    ctx = Context()
    order: list[str] = []

    def setup():
        order.append("start")
        yield lambda: order.append("cleanup-1")
        yield lambda: order.append("cleanup-2")
        order.append("exhausted")

    handle = ctx.effect(setup, "sync-gen")
    # 同步生成器立即驱动到底
    assert order == ["start", "exhausted"]
    assert handle() is None
    # 倒序清理
    assert order == ["start", "exhausted", "cleanup-2", "cleanup-1"]


def test_sync_generator_yield_none_skipped() -> None:
    # yield None：跳过，不收集（对齐 safeCollect）
    ctx = Context()
    order: list[str] = []

    def setup():
        yield lambda: order.append("cleanup-1")
        yield None
        yield lambda: order.append("cleanup-2")

    handle = ctx.effect(setup)
    handle()
    assert order == ["cleanup-2", "cleanup-1"]


def test_sync_generator_invalid_yield_raises() -> None:
    ctx = Context()
    cleaned: list[str] = []

    def setup():
        yield lambda: cleaned.append("cleanup-1")
        yield 123  # 非法清理项

    with pytest.raises(TypeError, match="Invalid effect"):
        ctx.effect(setup)
    # 已收集部分被清理
    assert cleaned == ["cleanup-1"]


# ── 形态 5：异步生成器 ───────────────────────────────────────


def test_async_generator_effect() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def setup():
            order.append("start")
            yield lambda: order.append("cleanup-1")
            await asyncio.sleep(0)
            yield lambda: order.append("cleanup-2")
            order.append("exhausted")

        handle = ctx.effect(setup, "async-gen")
        await handle  # 等生成器耗尽
        assert order == ["start", "exhausted"]
        # 清理全同步：立即执行完毕
        assert handle() is None
        assert order == ["start", "exhausted", "cleanup-2", "cleanup-1"]

    asyncio.run(main())


def test_async_generator_interrupted_by_dispose() -> None:
    # 门闩语义（对齐 TS epoch 中断）：
    # - 挂起在途的 next() 完成值仍会被收集（TS 同样行为）
    # - 清理触发后不再驱动新的 next()：后续生成器体不执行、清理不收集
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def setup():
            order.append("start")
            yield lambda: order.append("cleanup-1")
            await asyncio.sleep(0.05)  # 在此挂起期间触发清理
            order.append("late")  # 挂起中的生成器体会跑完到下一个 yield
            yield lambda: order.append("cleanup-2")
            order.append("unreachable")  # 不应执行：anext 不再被驱动
            yield lambda: order.append("cleanup-3")

        handle = ctx.effect(setup, "interrupt")
        await settle(2)  # task 跑到 anext#2 的 sleep 中
        assert order == ["start"]
        task = handle()  # 触发清理：等 task 结束（门闩使其在 yield c2 后停止）
        assert task is not None
        await task
        # late 执行（在途 next 完成）、c2 被收集并清理；unreachable 未执行
        assert order == ["start", "late", "cleanup-2", "cleanup-1"]
        assert "unreachable" not in order

    asyncio.run(main())


# ── 失败路径 ─────────────────────────────────────────────────


def test_execute_raises_cleans_collected() -> None:
    # execute 同步抛错 → 已收集清理被执行且异常上抛
    ctx = Context()
    order: list[str] = []

    def bad():
        yield lambda: order.append("cleanup-1")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        ctx.effect(bad)
    assert order == ["cleanup-1"]


def test_awaitable_failure_cascades(caplog: pytest.LogCaptureFixture) -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def bad():
            yield lambda: order.append("cleanup-1")
            await asyncio.sleep(0)
            raise RuntimeError("boom")

        handle = ctx.effect(bad, "bad-awaitable")
        with pytest.raises(RuntimeError, match="boom"):
            await handle
        await settle()
        # 级联清理：已收集部分被清掉
        assert order == ["cleanup-1"]

    with caplog.at_level(logging.ERROR, logger="cordis.root"):
        asyncio.run(main())
    assert any("建立失败" in record.message for record in caplog.records)


def test_invalid_effect_shape_raises() -> None:
    ctx = Context()
    with pytest.raises(TypeError, match="Invalid effect"):
        ctx.effect(lambda: 123)


# ── 幂等与生命周期 ───────────────────────────────────────────


def test_handle_idempotent() -> None:
    # await 得到的清理函数幂等：二次调用无操作
    async def main() -> None:
        ctx = Context()
        count: list[int] = []

        async def setup():
            return lambda: count.append(1)

        handle = ctx.effect(setup)
        dispose = await handle
        assert dispose() is None  # 同步清理：立即完成
        assert count == [1]
        assert dispose() is None  # 幂等
        assert count == [1]

    asyncio.run(main())


def test_inactive_fiber_rejects_effect() -> None:
    # 对齐 TS：root fiber 可复用（dispose 后 uid 保持 0）；实例 fiber 拆线后不可用
    async def main() -> None:
        ctx = Context()

        def plugin(plugin_ctx: Any, config: Any):
            return None

        fiber = ctx.plugin(plugin)
        await fiber
        assert fiber.state is FiberState.ACTIVE
        task = fiber.dispose()
        if task is not None:
            await task
        assert fiber.uid is None
        with pytest.raises(CordisError) as exc_info:
            fiber.effect(lambda: None)
        assert exc_info.value.code == "INACTIVE_EFFECT"

    asyncio.run(main())


# ── Fiber 统一清理 ───────────────────────────────────────────


def test_fiber_dispose_lifo_sync() -> None:
    # 纯同步：fiber.dispose 倒序触发全部 effect
    ctx = Context()
    order: list[str] = []

    def make(name: str):
        def setup():
            order.append(f"setup-{name}")
            return lambda: order.append(f"cleanup-{name}")
        return setup

    ctx.effect(make("a"), "a")
    ctx.effect(make("b"), "b")
    ctx.effect(make("c"), "c")
    assert ctx.fiber.dispose() is None
    assert order == [
        "setup-a", "setup-b", "setup-c",
        "cleanup-c", "cleanup-b", "cleanup-a",
    ]


def test_fiber_dispose_async_effects() -> None:
    # 异步清理函数：fiber.dispose 返回可等待的聚合 task
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def setup(name: str):
            order.append(f"setup-{name}")
            await asyncio.sleep(0)

            async def cleanup() -> None:
                order.append(f"cleanup-{name}")
            return cleanup

        async def setup_a():
            return await setup("a")

        async def setup_b():
            return await setup("b")

        ctx.effect(setup_a, "a")
        ctx.effect(setup_b, "b")
        await settle(2)
        task = ctx.fiber.dispose()
        assert task is not None
        await task
        # effect 之间并行，但同批 gather 完成
        assert sorted(order[-2:]) == ["cleanup-a", "cleanup-b"]

    asyncio.run(main())


def test_fiber_dispose_idempotent() -> None:
    ctx = Context()
    count: list[int] = []
    ctx.effect(lambda: lambda: count.append(1))
    assert ctx.fiber.dispose() is None
    assert ctx.fiber.dispose() is None
    assert count == [1]


def test_mixed_sync_async_cleanup_chain() -> None:
    # 混合清理链的倒序串行语义（对齐 TS dispose 的 then 链）：
    # 倒序后第一项 async_2 的同步前缀立即执行；
    # 其后的 sync_2/sync_1 必须排在异步项之后（串行，不可跳跃）
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def async_cleanup() -> None:
            order.append("async-start")
            await asyncio.sleep(0)
            order.append("async-done")

        def setup():
            def sync_1():
                order.append("sync-1")

            def sync_2():
                order.append("sync-2")

            def async_2():
                order.append("async-2")
                return async_cleanup()

            yield sync_1
            yield sync_2
            yield async_2

        handle = ctx.effect(setup, "mixed")
        task = handle()
        assert task is not None
        # 倒序第一项（async_2）的同步前缀立即执行
        assert order == ["async-2"]
        await task
        # 串行链：async-2 → async 体 → 剩余同步清理（倒序）
        assert order == ["async-2", "async-start", "async-done", "sync-2", "sync-1"]

    asyncio.run(main())


# ── Context 集成 ─────────────────────────────────────────────


def test_context_effect_delegates_to_fiber() -> None:
    ctx = Context()
    assert ctx.fiber.name == "root"
    order: list[str] = []
    ctx.effect(lambda: lambda: order.append("done"), "via-ctx")
    ctx.dispose()
    assert order == ["done"]
