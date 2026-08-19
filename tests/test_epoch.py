"""epoch 依赖刷新测试（PRD-B2 AC1：依赖上线自动装载 / 下线自动卸载 / 替换重载 / 级联）。

核心场景：服务提供与依赖消费之间的状态联动。
"""
from __future__ import annotations

import asyncio
from typing import Any

from cordis import Context, FiberState


async def settle(n: int = 8) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def test_dependency_pending_until_provided() -> None:
    """依赖缺失 → PENDING；服务上线 → 自动装载为 ACTIVE。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def dep_plugin(ctx: Any, config: Any):
            db = ctx.get("database")
            order.append(f"dep-loaded:{db}")
            return lambda: order.append("dep-unloaded")

        dep_plugin.inject = {"database": None}  # type: ignore[attr-defined]
        fiber = ctx.plugin(dep_plugin)
        await settle()
        # 依赖缺失：PENDING，未装载
        assert fiber.state is FiberState.PENDING
        assert order == []

        # 服务上线 → 自动装载
        ctx.provide("database", "db-conn")
        await settle(12)
        assert fiber.state is FiberState.ACTIVE
        assert order == ["dep-loaded:db-conn"]

    asyncio.run(main())


def test_dependency_removed_unloads() -> None:
    """服务下线 → 依赖插件自动卸载（副作用撤销），回到 PENDING。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def dep_plugin(ctx: Any, config: Any):
            order.append("dep-loaded")
            return lambda: order.append("dep-unloaded")

        dep_plugin.inject = {"database": None}  # type: ignore[attr-defined]
        handle = ctx.provide("database", "db-conn")
        fiber = ctx.plugin(dep_plugin)
        await settle(12)
        assert fiber.state is FiberState.ACTIVE
        assert order == ["dep-loaded"]

        # 服务下线 → 依赖方卸载
        task = handle()
        assert task is not None
        await task
        await settle(8)
        assert fiber.state is FiberState.PENDING
        assert order == ["dep-loaded", "dep-unloaded"]

    asyncio.run(main())


def test_dependency_replacement_reloads() -> None:
    """提供者销毁（服务下线）→ 依赖方卸载；新提供者上线 → 依赖方重载。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def dep_plugin(ctx: Any, config: Any):
            order.append(f"dep-loaded:{ctx.get('database')}")
            return lambda: order.append("dep-unloaded")

        dep_plugin.inject = {"database": None}  # type: ignore[attr-defined]

        def provider(ctx: Any, config: Any):
            ctx.provide("database", config["value"])
            return None

        p1 = ctx.plugin(provider, {"value": "v1"})
        d = ctx.plugin(dep_plugin)
        await settle(12)
        assert d.state is FiberState.ACTIVE
        assert order == ["dep-loaded:v1"]

        # 销毁提供者 → database 下线 → 依赖方卸载
        await p1.dispose()
        await settle(8)
        assert d.state is FiberState.PENDING
        assert order == ["dep-loaded:v1", "dep-unloaded"]

        # 新提供者上线 → 依赖方重载
        ctx.plugin(provider, {"value": "v2"})
        await settle(12)
        assert d.state is FiberState.ACTIVE
        assert order == ["dep-loaded:v1", "dep-unloaded", "dep-loaded:v2"]

    asyncio.run(main())


def test_provider_fiber_dispose_removes_service() -> None:
    """提供者 fiber 销毁 → 其提供的服务从表中移除。"""

    async def main() -> None:
        ctx = Context()

        def provider(ctx: Any, config: Any):
            ctx.provide("mail", "mailer")
            return None

        fiber = ctx.plugin(provider)
        await fiber
        assert ctx.get("mail") == "mailer"
        await fiber.dispose()
        assert ctx.get("mail") is None

    asyncio.run(main())


def test_parent_plugin_unload_cascades_to_child() -> None:
    """父插件卸载 → 其装载的子插件级联卸载（fiber 挂在父生命周期上）。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def child(ctx: Any, config: Any):
            order.append("child-loaded")
            return lambda: order.append("child-unloaded")

        def parent(ctx: Any, config: Any):
            ctx.plugin(child)
            order.append("parent-loaded")
            return lambda: order.append("parent-unloaded")

        pf = ctx.plugin(parent)
        await settle(10)
        # 装载顺序：parent 回调同步执行（先 parent-loaded），child 装载为异步 task
        assert order == ["parent-loaded", "child-loaded"]
        task = pf.dispose()
        assert task is not None
        await task
        await settle(8)
        # 子插件随父插件卸载
        assert "child-unloaded" in order
        assert order.count("child-loaded") == 1
        assert "parent-unloaded" in order

    asyncio.run(main())


def test_dependency_check_predicate() -> None:
    """provide 的 check 谓词：不满足时依赖方不装载。"""

    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        def dep_plugin(ctx: Any, config: Any):
            order.append("dep-loaded")
            return lambda: order.append("dep-unloaded")

        dep_plugin.inject = {"database": None}  # type: ignore[attr-defined]

        state = {"enabled": False}
        ctx.provide("database", "db", check=lambda: state["enabled"])
        fiber = ctx.plugin(dep_plugin)
        await settle(12)
        # check 不满足 → 视为依赖不可用
        assert fiber.state is FiberState.PENDING
        assert order == []

        state["enabled"] = True
        # 触发一次服务事件（下线重建或 notify）以重试：直接重建服务
        ctx.reflect.notify(["database"])
        await settle(12)
        assert fiber.state is FiberState.ACTIVE
        assert order == ["dep-loaded"]

    asyncio.run(main())
