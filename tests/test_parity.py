"""对标验收测试（PRD-E1）：逐用例映射 cordis@4.0.0-rc core/tests。

映射表：
- dispose.spec.ts   → test_get_effects_* / test_dispose_* / 失败路径
- plugin.spec.ts    → test_plugin_* / test_root_dispose / test_service_init_lifecycle
- fiber.spec.ts     → test_plugin_error_failed / test_dispose_error / test_update /
                      test_restart / test_inertia_lock
- service.spec.ts   → test_class_init_blocks_load（pending inject 语义）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from cordis import Context, CordisError, FiberState


async def settle(n: int = 8) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── dispose.spec.ts：getEffects 元数据树 ────────────────────


def test_get_effects_simple() -> None:
    # parity: dispose.spec 'dispose manually'
    ctx = Context()
    ctx.effect(lambda: lambda: None, "test")
    assert ctx.fiber.getEffects() == [{"label": "test", "children": []}]


def test_get_effects_nested() -> None:
    # parity: dispose.spec 'dispose by plugin' / 'yield dispose'
    ctx = Context()

    def outer():
        def inner():
            yield lambda: None
        yield ctx.effect(inner, "inner-effect")
        yield lambda: None

    ctx.effect(outer, "outer-effect")
    tree = ctx.fiber.getEffects()
    assert len(tree) == 1
    assert tree[0]["label"] == "outer-effect"
    children = tree[0]["children"]
    assert children[0]["label"] == "inner-effect"
    assert children[0]["children"] == []


def test_yield_dispose_order() -> None:
    # parity: dispose.spec 'yield dispose'（LIFO 顺序 3,2,1）
    ctx = Context()
    seq: list[int] = []

    def p(n: int):
        return lambda: seq.append(n)

    def setup():
        yield p(1)
        yield p(2)
        yield p(3)

    dispose = ctx.effect(setup)
    assert seq == []
    dispose()
    assert seq == [3, 2, 1]
    dispose()  # 幂等
    assert seq == [3, 2, 1]


def test_return_with_error() -> None:
    # parity: dispose.spec 'return with error'
    ctx = Context()
    seq: list[int] = []

    def bad():
        raise RuntimeError("test")
        yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="test"):
        ctx.effect(bad)
    assert seq == []


def test_async_return_with_error() -> None:
    # parity: dispose.spec 'async return with error'——await handle 传播错误
    async def main() -> None:
        ctx = Context()

        async def bad():
            raise RuntimeError("test")

        handle = ctx.effect(bad)
        with pytest.raises(RuntimeError, match="test"):
            await handle

    asyncio.run(main())


# ── plugin.spec.ts ──────────────────────────────────────────


def test_plugin_inactive_context_raises() -> None:
    # parity: plugin.spec 'inactive context'——拆线后三操作抛 INACTIVE_EFFECT
    async def main() -> None:
        ctx = Context()
        captured: dict[str, Any] = {}

        def plugin_ctx_capture(plugin_ctx: Any, config: Any):
            captured["ctx"] = plugin_ctx
            return None

        fiber = ctx.plugin(plugin_ctx_capture)
        await fiber
        task = fiber.dispose()
        if task is not None:
            await task
        dead = captured["ctx"]
        with pytest.raises(CordisError):
            dead.plugin(lambda x: None)
        with pytest.raises(CordisError):
            dead.effect(lambda: None)
        with pytest.raises(CordisError):
            dead.on("x", lambda: None)

    asyncio.run(main())


def test_root_dispose_semantics() -> None:
    # parity: plugin.spec 'root dispose'——uid 保持 0、effect 清空、幂等
    async def main() -> None:
        root = Context()
        disposed: list[int] = []
        fiber = root.plugin(lambda plugin_ctx, config: lambda: disposed.append(1))
        assert root.fiber.uid == 0
        assert fiber.uid == 1
        assert len(list(root.fiber._disposables)) == 1
        task = root.fiber.dispose()
        if task is not None:
            await task
        assert root.fiber.uid == 0  # root 保持
        assert fiber.uid is None  # 子实例拆线
        assert disposed == [1]
        assert len(list(root.fiber._disposables)) == 0
        # root 可复用（对齐 TS root restart）
        assert root.fiber.dispose() is None

    asyncio.run(main())


def test_nested_plugins_registry_cleanup() -> None:
    # parity: plugin.spec 'nested plugins'——卸载后 registry 清空
    async def main() -> None:
        root = Context()
        hits: list[int] = []

        def counter():
            return lambda: hits.append(1)

        async def child3(plugin_ctx: Any, config: Any):
            plugin_ctx.on("event", counter())

        async def child2(plugin_ctx: Any, config: Any):
            plugin_ctx.on("event", counter())
            await plugin_ctx.plugin(child3)

        async def parent(plugin_ctx: Any, config: Any):
            plugin_ctx.on("event", counter())
            await plugin_ctx.plugin(child2)

        root.on("event", counter())
        fiber = await root.plugin(parent)
        assert root.registry.size == 3
        root.emit("event")
        assert len(hits) == 4
        await fiber.dispose()
        assert root.registry.size == 0
        hits.clear()
        root.emit("event")
        assert len(hits) == 1  # 只剩 root 自己的
        # 幂等拆线：第二次 dispose 为 no-op（JS await undefined 合法，
        # Python 不可 await None——语言差异，直接调用）
        assert fiber.dispose() is None
        assert root.registry.size == 0

    asyncio.run(main())


def test_print_inspect_context() -> None:
    # parity: plugin.spec 'context inspect'
    root = Context()
    assert repr(root) == "Context <root>"

    async def main() -> None:
        async def foo(plugin_ctx: Any, config: Any):
            assert repr(plugin_ctx) == "Context <foo>"

        await root.plugin(foo)

    asyncio.run(main())


# ── fiber.spec.ts ───────────────────────────────────────────


def test_plugin_error_failed() -> None:
    # parity: fiber.spec 'plugin error'——FAILED 态 + 错误日志
    async def main() -> None:
        root = Context()
        hits: list[int] = []

        def plugin(plugin_ctx: Any, config: Any):
            plugin_ctx.on("e", lambda: hits.append(1))
            if not config.get("ok"):
                raise RuntimeError("plugin error")

        f1 = root.plugin(plugin, {})
        f2 = root.plugin(plugin, {"ok": True})
        await settle(8)
        assert f1.state is FiberState.FAILED
        assert f2.state is FiberState.ACTIVE
        root.emit("e")
        assert len(hits) == 1  # 只有成功者注册的监听器生效

    asyncio.run(main())


def test_dispose_error_resolves() -> None:
    # parity: fiber.spec 'dispose error'——清理抛错被吞 + 记日志，dispose resolve
    async def main() -> None:
        root = Context()

        def bad_cleanup() -> None:
            raise RuntimeError("test")

        def plugin(plugin_ctx: Any, config: Any):
            return bad_cleanup

        fiber = await root.plugin(plugin)
        task = fiber.dispose()
        assert task is not None
        await task  # 不抛

    asyncio.run(main())


def test_dispose_error_logged(caplog: pytest.LogCaptureFixture) -> None:
    # parity: fiber.spec 'dispose error'（日志断言）
    async def main() -> None:
        root = Context()

        def bad_cleanup() -> None:
            raise RuntimeError("cleanup boom")

        fiber = await root.plugin(lambda plugin_ctx, config: bad_cleanup)
        task = fiber.dispose()
        if task is not None:
            await task
        await settle(2)

    with caplog.at_level(logging.ERROR):
        asyncio.run(main())
    assert any("清理失败" in r.message for r in caplog.records)


def test_update_config_hot_reload() -> None:
    # parity: fiber.spec 'update config on wrapped fiber'
    async def main() -> None:
        root = Context()
        seen: list[Any] = []

        def plugin(plugin_ctx: Any, config: Any):
            seen.append(config)
            return None

        fiber = root.plugin(plugin, {"msg": "hello"})
        await fiber
        assert seen == [{"msg": "hello"}]
        await fiber.update({"msg": "world"})
        assert seen == [{"msg": "hello"}, {"msg": "world"}]
        await fiber.update({"msg": "!!!"})
        assert seen == [{"msg": "hello"}, {"msg": "world"}, {"msg": "!!!"}]

    asyncio.run(main())


def test_restart() -> None:
    # parity: fiber.spec 'restart wrapped fiber'
    async def main() -> None:
        root = Context()
        count = [0]

        def plugin(plugin_ctx: Any, config: Any):
            count[0] += 1
            return None

        fiber = root.plugin(plugin)
        await fiber
        assert count == [1]
        await fiber.restart()
        assert count == [2]
        assert fiber.state is FiberState.ACTIVE

    asyncio.run(main())


def test_inertia_lock() -> None:
    # parity: fiber.spec 'inertia lock'——装载中依赖变化不打断，完成后按新状态收尾
    async def main() -> None:
        root = Context()
        order: list[str] = []
        provide_handle = root.provide("foo", 1)

        async def dependent(plugin_ctx: Any, config: Any):
            order.append("loaded")
            await asyncio.sleep(0.05)
            return lambda: order.append("unloaded")

        fiber = root.inject(["foo"], dependent)
        await asyncio.sleep(0.01)
        assert fiber.state is FiberState.LOADING  # 仍在装载
        task = provide_handle()  # 依赖下线（装载中）
        assert task is not None
        await task
        await settle(10)
        # 装载完成 → 依赖缺失 → 卸载，顺序不被打断
        assert order == ["loaded", "unloaded"]

    asyncio.run(main())


# ── service.spec.ts：init 阻塞装载 ──────────────────────────


def test_class_init_blocks_load() -> None:
    # parity: service.spec 'pending inject'——异步 init 完成前不进入 ACTIVE
    async def main() -> None:
        root = Context()
        ready = asyncio.Event()
        order: list[str] = []

        class Pending:
            def __init__(self, plugin_ctx: Any, config: Any) -> None:
                pass

            def init(self):
                async def wait_ready():
                    await ready.wait()
                    order.append("init-done")
                    return lambda: order.append("cleanup")

                return wait_ready()

        fiber = root.plugin(Pending)
        await settle(4)
        assert fiber.state is FiberState.LOADING  # init 挂起阻塞装载
        ready.set()
        await fiber
        assert fiber.state is FiberState.ACTIVE
        assert order == ["init-done"]
        task = fiber.dispose()
        if task is not None:
            await task
        assert order == ["init-done", "cleanup"]

    asyncio.run(main())


def test_class_init_sync_cleanup() -> None:
    # parity: plugin.spec 'Service.init'（同步 init 返回值作清理）
    async def main() -> None:
        root = Context()
        order: list[str] = []

        class WithInit:
            def __init__(self, plugin_ctx: Any, config: Any) -> None:
                pass

            def init(self):
                order.append("start")
                return lambda: order.append("stop")

        fiber = await root.plugin(WithInit)
        assert order == ["start"]
        task = fiber.dispose()
        if task is not None:
            await task
        assert order == ["start", "stop"]

    asyncio.run(main())
