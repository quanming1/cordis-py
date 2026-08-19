"""Registry / 插件装载测试（PRD-B2 AC1：三形态 / 多实例 / await / get-set）。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cordis import Context, Fiber, FiberState


async def settle(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── 三形态 ───────────────────────────────────────────────────


def test_function_plugin_load_unload() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def plugin(ctx: Any, config: Any):
            order.append("loaded")
            return lambda: order.append("unloaded")

        fiber = ctx.plugin(plugin, {"a": 1})
        assert isinstance(fiber, Fiber)
        await fiber
        assert fiber.state is FiberState.ACTIVE
        assert order == ["loaded"]
        task = fiber.dispose()
        assert task is not None
        await task
        assert order == ["loaded", "unloaded"]

    asyncio.run(main())


def test_class_plugin_load_unload() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        class MyPlugin:
            def __init__(self, ctx: Any, config: Any) -> None:
                order.append(f"init:{config['name']}")
                ctx.effect(lambda: lambda: order.append("unloaded"), "class-plugin")

        fiber = ctx.plugin(MyPlugin, {"name": "alpha"})
        await fiber
        assert fiber.state is FiberState.ACTIVE
        assert order == ["init:alpha"]
        await fiber.dispose()
        assert order == ["init:alpha", "unloaded"]

    asyncio.run(main())


def test_object_plugin_load_unload() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        plugin_obj = {
            "name": "obj-plugin",
            "apply": lambda ctx, config: order.append("infra"),
        }
        fiber = ctx.plugin(plugin_obj)
        await fiber
        assert fiber.name == "obj-plugin"
        assert order == ["infra"]

    asyncio.run(main())


def test_invalid_plugin_raises() -> None:
    ctx = Context()
    with pytest.raises(TypeError, match="invalid plugin"):
        ctx.plugin(123)


# ── 多实例 ───────────────────────────────────────────────────


def test_multiple_instances_independent() -> None:
    async def main() -> None:
        ctx = Context()
        done: list[str] = []

        def plugin(ctx: Any, config: dict):
            name = config["name"]
            return lambda: done.append(f"unload-{name}")

        f1 = ctx.plugin(plugin, {"name": "a"})
        f2 = ctx.plugin(plugin, {"name": "b"})
        assert f1 is not f2
        assert f1.uid != f2.uid
        await f1
        await f2
        await f1.dispose()
        assert done == ["unload-a"]
        await f2.dispose()
        assert done == ["unload-a", "unload-b"]

    asyncio.run(main())


# ── 配置 ─────────────────────────────────────────────────────


def test_config_passed_and_validated() -> None:
    async def main() -> None:
        ctx = Context()
        seen: list[Any] = []

        def validate(config: dict) -> dict:
            assert config["port"] == 80
            return {**config, "validated": True}

        def plugin(ctx: Any, config: dict):
            seen.append(config)
            return None

        plugin.Config = validate  # type: ignore[attr-defined]
        fiber = ctx.plugin(plugin, {"port": 80})
        await fiber
        assert fiber.config["validated"] is True
        assert seen[0]["validated"] is True

    asyncio.run(main())


# ── 异步插件 / 报错上抛 ─────────────────────────────────────


def test_async_plugin() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        async def plugin(ctx: Any, config: Any):
            order.append("async-loaded")
            await asyncio.sleep(0)
            return lambda: order.append("async-unloaded")

        fiber = ctx.plugin(plugin)
        await fiber
        assert order == ["async-loaded"]
        task = fiber.dispose()
        assert task is not None
        await task
        assert order == ["async-loaded", "async-unloaded"]

    asyncio.run(main())


def test_await_plugin_raises_error() -> None:
    async def main() -> None:
        ctx = Context()

        def bad_plugin(ctx: Any, config: Any):
            raise RuntimeError("plugin boom")

        with pytest.raises(RuntimeError, match="plugin boom"):
            fiber = ctx.plugin(bad_plugin)
            await fiber

    asyncio.run(main())


def test_sync_plugin_without_loop() -> None:
    # 无事件循环：同步插件走同步装载回退路径
    ctx = Context()
    order: list[str] = []

    def plugin(ctx: Any, config: Any):
        order.append("loaded")
        return lambda: order.append("unloaded")

    # 注意：这里在无循环环境下调用，回归到同步路径
    fiber = ctx.plugin(plugin)
    assert fiber.state is FiberState.ACTIVE
    assert order == ["loaded"]
    assert fiber.dispose() is None
    assert order == ["loaded", "unloaded"]


# ── 服务读写（get/set）──────────────────────────────────────


def test_get_set() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def provider(ctx: Any, config: Any):
            ctx.provide("database", "db-conn")
            return lambda: order.append("provider-unloaded")

        def consumer(ctx: Any, config: Any):
            assert ctx.get("database") == "db-conn"
            return None

        consumer.inject = {"database": None}  # type: ignore[attr-defined]
        pf = ctx.plugin(provider)
        await pf
        cf = ctx.plugin(consumer)
        await cf
        assert cf.state is FiberState.ACTIVE
        # root ctx 不能写入插件提供的服务（提供者是 pf fiber）
        with pytest.raises(RuntimeError, match="multiple fibers"):
            ctx.set("database", "db-2")
        assert ctx.get("database") == "db-conn"

    asyncio.run(main())


def test_provider_ctx_can_set() -> None:
    async def main() -> None:
        ctx = Context()
        # root fiber 直接提供服务：提供者可写
        handle = ctx.provide("database", "db-conn")
        assert ctx.get("database") == "db-conn"
        assert ctx.set("database", "db-2") is True
        assert ctx.get("database") == "db-2"
        # 下线后不可读
        task = handle()
        if task is not None:
            await task
        assert ctx.get("database", strict=False) is None

    asyncio.run(main())


# ── 级联清理（root → 子插件）────────────────────────────────


def test_context_dispose_cascades() -> None:
    async def main() -> None:
        ctx = Context()
        order: list[str] = []
        fibers: list[Fiber] = []

        def make(name: str):
            def plugin(ctx: Any, config: Any):
                return lambda: order.append(f"unload-{name}")
            return plugin

        fibers.append(ctx.plugin(make("a")))
        fibers.append(ctx.plugin(make("b")))
        for fiber in fibers:
            await fiber
        task = ctx.dispose()
        assert task is not None
        await task
        assert order == ["unload-b", "unload-a"]
        for fiber in fibers:
            assert fiber.uid is None

    asyncio.run(main())
