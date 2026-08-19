"""Reflect 完整版测试（PRD-C1 AC1：isolate 隔离 / 属性访问 / 联动）。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cordis import Context, FiberState


async def settle(n: int = 8) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── 属性访问 ────────────────────────────────────────────────


def test_attribute_access() -> None:
    async def main() -> None:
        ctx = Context()
        ctx.provide("database", "db-conn")
        # __getattr__ 解析服务
        assert ctx.database == "db-conn"
        # 与显式 get 等价
        assert ctx.database == ctx.get("database")

    asyncio.run(main())


def test_attribute_access_missing_raises() -> None:
    ctx = Context()
    with pytest.raises(AttributeError, match="without inject"):
        _ = ctx.database


def test_setattr_service_write() -> None:
    async def main() -> None:
        ctx = Context()
        handle = ctx.provide("database", "db-conn")
        # 提供者（root context）可写
        ctx.database = "db-2"
        assert ctx.get("database") == "db-2"
        task = handle()
        if task is not None:
            await task

    asyncio.run(main())


def test_setattr_without_provide_raises() -> None:
    ctx = Context()
    with pytest.raises(RuntimeError, match="without provide"):
        ctx.database = "x"


# ── isolate 隔离 ────────────────────────────────────────────


def test_isolate_separate_instances() -> None:
    async def main() -> None:
        ctx = Context()
        # root 提供默认实例
        ctx.provide("database", "default-conn")

        # 隔离上下文提供独立实例
        iso_a = ctx.isolate("database")
        iso_a.provide("database", "conn-a")
        assert ctx.get("database") == "default-conn"
        assert iso_a.get("database") == "conn-a"

        iso_b = ctx.isolate("database")
        iso_b.provide("database", "conn-b")
        assert iso_b.get("database") == "conn-b"
        assert iso_a.get("database") == "conn-a"  # A/B 互不影响
        assert ctx.get("database") == "default-conn"

    asyncio.run(main())


def test_attribute_access_respects_isolate() -> None:
    async def main() -> None:
        ctx = Context()
        iso = ctx.isolate("database")
        iso.provide("database", "iso-conn")
        # 属性访问走当前 ctx 的隔离键
        assert iso.database == "iso-conn"
        # 非隔离 ctx 拿不到隔离实例
        with pytest.raises(AttributeError, match="without inject"):
            _ = ctx.database

    asyncio.run(main())


def test_isolate_mapping_inheritance() -> None:
    ctx = Context()
    a = ctx.isolate("database")
    b = a.isolate("mail")  # b 继承 a 对 database 的隔离，再隔离 mail
    assert b._isolate["database"] is a._isolate["database"]
    assert b._isolate["mail"] is not a._isolate.get("mail")


def test_dependency_resolves_by_isolate() -> None:
    """依赖方与提供者隔离键一致才装载（跨隔离不混淆）。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def dep_plugin(plugin_ctx: Any, config: Any):
            order.append(f"dep-loaded:{plugin_ctx.get('database')}")

        dep_plugin.inject = {"database": None}  # type: ignore[attr-defined]

        # 提供者在隔离 ctx 提供服务
        iso = ctx.isolate("database")
        iso.provide("database", "iso-conn")

        # 未隔离的依赖方：应拿到默认 key（此时无默认服务 → 不装载）
        f_plain = ctx.plugin(dep_plugin)
        await settle(8)
        assert f_plain.state is FiberState.PENDING
        assert order == []

    asyncio.run(main())


def test_plugin_in_isolate_context() -> None:
    """插件在自己的隔离 ctx 内提供并消费服务。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def provider(plugin_ctx: Any, config: Any):
            # plugin_ctx 已继承 iso 的隔离映射（database→共享键）
            plugin_ctx.provide("database", "app-db")
            return lambda: order.append("provider-unloaded")

        def consumer(plugin_ctx: Any, config: Any):
            order.append(f"consumer:{plugin_ctx.get('database')}")
            return None

        consumer.inject = {"database": None}  # type: ignore[attr-defined]

        # provider 与 consumer 都在同一隔离 ctx 装载 → 共享同一隔离键
        iso = ctx.isolate("database")
        iso.plugin(provider)
        cf = iso.plugin(consumer)
        await settle(12)
        assert cf.state is FiberState.ACTIVE
        assert "consumer:app-db" in order

    asyncio.run(main())


# ── event_filter 派生 ───────────────────────────────────────


def test_internal_service_filtered_by_isolate() -> None:
    """internal/service 广播按 isolate 域过滤：非隔离监听器收不到隔离事件。"""

    async def main() -> None:
        ctx = Context()
        root_seen: list[str] = []
        iso_seen: list[str] = []
        ctx.on("internal/service", lambda name, value: root_seen.append(value))

        iso = ctx.isolate("database")

        def watcher(plugin_ctx: Any, config: Any):
            plugin_ctx.on("internal/service", lambda name, value: iso_seen.append(value))
            return None

        iso.plugin(watcher)
        await settle(6)
        iso.provide("database", "iso-conn")
        await settle(6)
        # iso 域的监听器收到；root（非隔离）监听器被过滤
        assert "iso-conn" in iso_seen
        assert "iso-conn" not in root_seen

    asyncio.run(main())
