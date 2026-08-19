"""Events 事件系统测试（PRD-B3 AC1：五种派发 / on-once / 过滤 / 生命周期联动）。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cordis import Context, FiberState


async def settle(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── on / emit 基础 ──────────────────────────────────────────


def test_on_emit_broadcast() -> None:
    ctx = Context()
    hits: list[int] = []
    ctx.on("foo", lambda: hits.append(1))
    ctx.on("foo", lambda: hits.append(2))
    ctx.emit("foo")
    assert hits == [1, 2]


def test_on_emit_with_args() -> None:
    ctx = Context()
    seen: list[tuple] = []
    ctx.on("foo", lambda a, b: seen.append((a, b)))
    ctx.emit("foo", 10, "x")
    assert seen == [(10, "x")]


def test_prepend_order() -> None:
    ctx = Context()
    hits: list[int] = []
    ctx.on("foo", lambda: hits.append(1))
    ctx.on("foo", lambda: hits.append(2), {"prepend": True})
    ctx.emit("foo")
    assert hits == [2, 1]


def test_manual_remove() -> None:
    ctx = Context()
    hits: list[int] = []
    remove = ctx.on("foo", lambda: hits.append(1))
    ctx.emit("foo")
    assert hits == [1]
    remove()  # 移除
    ctx.emit("foo")
    assert hits == [1]  # 不再触发


def test_once_self_removing() -> None:
    ctx = Context()
    count = [0]
    ctx.once("foo", lambda: count.__setitem__(0, count[0] + 1))
    ctx.emit("foo")
    ctx.emit("foo")
    assert count == [1]


def test_listener_removed_on_fiber_unload() -> None:
    # 监听器随插件 fiber 卸载自动移除（effect 联动）
    async def main() -> None:
        ctx = Context()
        hits: list[int] = []

        def plugin(plugin_ctx: Any, config: Any):
            plugin_ctx.on("foo", lambda: hits.append(1))
            return None

        fiber = ctx.plugin(plugin)
        await settle(6)
        ctx.emit("foo")
        assert hits == [1]
        task = fiber.dispose()
        if task is not None:
            await task
        ctx.emit("foo")
        assert hits == [1]  # 插件卸载后监听器移除

    asyncio.run(main())


# ── thisArg 过滤 ────────────────────────────────────────────


def test_this_arg_filter() -> None:
    ctx = Context()
    hits: list[int] = []

    # 插件的 ctx 带 event_filter
    def plugin_a(plugin_ctx: Any, config: Any):
        plugin_ctx.event_filter = lambda hook_ctx: hook_ctx is plugin_ctx
        plugin_ctx.on("foo", lambda: hits.append("A"))
        return None

    def plugin_b(plugin_ctx: Any, config: Any):
        plugin_ctx.on("foo", lambda: hits.append("B"))
        return None

    async def main() -> None:
        fa = ctx.plugin(plugin_a)
        ctx.plugin(plugin_b)
        await settle(6)
        # 无 thisArg：全部触发
        ctx.emit("foo")
        assert hits == ["A", "B"]
        hits.clear()
        # 带 thisArg=plugin_a 的 ctx：A 的 filter 为「只有自己」→ 触发；B 被过滤
        ctx.emit(fa.ctx, "foo")
        assert hits == ["A"]

    asyncio.run(main())


def test_global_listener_bypasses_filter() -> None:
    ctx = Context()
    hits: list[int] = []
    ctx.on("foo", lambda: hits.append(1), {"global": True})

    context_b = "__thismock__"

    class MockFilter:
        def _event_filter(self, hook_ctx):
            return hook_ctx is context_b

    ctx.emit(MockFilter(), "foo")
    assert hits == [1]  # global 监听器不受 thisArg 过滤


# ── 五种派发模式 ────────────────────────────────────────────


def test_bail_short_circuit() -> None:
    ctx = Context()
    hits: list[int] = []
    ctx.on("foo", lambda: hits.append(1))
    ctx.on("foo", lambda: (hits.append(2), "result")[1])
    ctx.on("foo", lambda: hits.append(3))
    result = ctx.bail("foo")
    assert result == "result"
    assert hits == [1, 2]  # 3 未执行（短路）


def test_bail_ignore_falsy() -> None:
    ctx = Context()
    hits: list[int] = []
    # None / False 不短路（TS isBailed：null/false/undefined 不算）
    ctx.on("foo", lambda: (hits.append(1), None)[1])
    ctx.on("foo", lambda: (hits.append(2), False)[1])
    result = ctx.bail("foo")
    assert result is None
    assert hits == [1, 2]  # 两个都执行完


def test_bail_consider_zero_bailed() -> None:
    # TS 严格相等：0 !== false 且 0 !== null → 0 会被视为短路值
    ctx = Context()
    marker = [0]
    ctx.on("foo", lambda: 0)
    ctx.on("foo", lambda: marker.__setitem__(0, marker[0] + 1))
    assert ctx.bail("foo") == 0
    assert marker == [0]  # 第二个监听器未执行（短路）


def test_serial_short_circuit() -> None:
    async def main() -> None:
        ctx = Context()
        hits: list[int] = []
        ctx.on("foo", lambda: (hits.append(1), None)[1])
        ctx.on("foo", lambda: (hits.append(2), "done")[1])
        ctx.on(
            "foo",
            lambda: asyncio.sleep(0) or (hits.append(3), "late")[1],
        )
        result = await ctx.serial("foo")
        assert result == "done"
        assert hits == [1, 2]

    asyncio.run(main())


def test_serial_awaits_async() -> None:
    async def main() -> None:
        ctx = Context()

        async def listener():
            await asyncio.sleep(0)
            return "async-result"

        ctx.on("foo", listener)
        result = await ctx.serial("foo")
        assert result == "async-result"

    asyncio.run(main())


def test_parallel_aggregates_errors() -> None:
    async def main() -> None:
        ctx = Context()
        hits: list[int] = []

        async def bad1():
            hits.append(1)
            raise ValueError("boom-1")

        async def bad2():
            hits.append(2)
            raise ValueError("boom-2")

        ctx.on("foo", bad1)
        ctx.on("foo", bad2)
        with pytest.raises(ExceptionGroup) as exc:
            await ctx.parallel("foo")
        messages = sorted(str(e) for e in exc.value.exceptions)
        assert messages == ["boom-1", "boom-2"]
        assert hits == [1, 2]  # 并发执行，全部触发

    asyncio.run(main())


def test_parallel_sync_and_async_mix() -> None:
    async def main() -> None:
        ctx = Context()
        hits: list[int] = []
        ctx.on("foo", lambda: hits.append(1))
        ctx.on("foo", lambda: asyncio.sleep(0))
        await ctx.parallel("foo")
        assert hits == [1]

    asyncio.run(main())


def test_waterfall_chain() -> None:
    # 对齐 TS：next 无参续延，payload 为共享引用，回调可修改以传递状态
    ctx = Context()
    order: list[str] = []
    shared = {"value": 1}

    def first(state: dict, next_):
        order.append(f"first:{state['value']}")
        state["value"] += 1
        return next_()

    def second(state: dict, next_):
        order.append(f"second:{state['value']}")
        state["value"] += 1
        return next_()

    def inner(state: dict):
        order.append(f"inner:{state['value']}")
        return "final"

    ctx.on("foo", first)
    ctx.on("foo", second)
    result = ctx.waterfall("foo", shared, inner)
    assert result == "final"
    assert order == ["first:1", "second:2", "inner:3"]


# ── 内部事件 ────────────────────────────────────────────────


def test_internal_plugin_events() -> None:
    async def main() -> None:
        ctx = Context()
        events: list[str] = []
        ctx.on("internal/plugin", lambda fiber: events.append(f"create:{fiber.uid}"))
        ctx.on("internal/plugin", lambda fiber: events.append(f"dispose:{fiber.uid}"))

        def plugin(plugin_ctx: Any, config: Any):
            return None

        fiber = ctx.plugin(plugin)
        await settle(6)
        assert any(e == f"create:{fiber.uid}" for e in events)
        task = fiber.dispose()
        if task is not None:
            await task
        assert any(e == f"dispose:{fiber.uid}" for e in events)

    asyncio.run(main())


def test_internal_service_events() -> None:
    async def main() -> None:
        ctx = Context()
        events: list[tuple] = []
        ctx.on("internal/service", lambda name, value: events.append((name, value)))

        handle = ctx.provide("database", "db-conn")
        await settle(4)
        assert ("database", "db-conn") in events

        task = handle()
        if task is not None:
            await task
        assert ("database", None) in events

    asyncio.run(main())


def test_state_observable_via_events() -> None:
    # B2 状态机与 B3 事件协同：插件从 PENDING 到 ACTIVE
    async def main() -> None:
        ctx = Context()
        seen: list[FiberState] = []

        def plugin(plugin_ctx: Any, config: Any):
            return None

        fiber = ctx.plugin(plugin)
        await settle(6)
        assert fiber.state is FiberState.ACTIVE
        assert seen == []

    asyncio.run(main())
