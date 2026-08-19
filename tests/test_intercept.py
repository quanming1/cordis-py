"""intercept 配置拦截测试（PRD-C2 AC1：链继承/覆盖、inject 配置联动、合并）。"""
from __future__ import annotations

import asyncio
from typing import Any

from cordis import Context


async def settle(n: int = 8) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── 拦截链 ────────────────────────────────────────────────


def test_intercept_new_context() -> None:
    ctx = Context()
    c1 = ctx.intercept("database", {"port": 5432})
    assert c1 is not ctx
    assert c1.service_config("database") == {"port": 5432}
    # 原 ctx 不受影响
    assert ctx.service_config("database") == {}


def test_intercept_chain_inheritance_and_override() -> None:
    ctx = Context()
    c1 = ctx.intercept("database", {"host": "a", "port": 5432})
    c2 = c1.intercept("database", {"port": 9999})
    merged = c2.service_config("database")
    # 祖先配置在前，最近声明覆盖
    assert merged == {"host": "a", "port": 9999}


def test_intercept_other_names_unaffected() -> None:
    ctx = Context()
    c1 = ctx.intercept("database", {"port": 5432})
    # mail 不在拦截链
    assert c1.service_config("mail") == {}


# ── 合并优先级 ────────────────────────────────────────────


def test_service_config_merge_order() -> None:
    ctx = Context()
    c1 = ctx.intercept("database", {"a": 1, "b": 1})
    # 合并顺序 base → 链 → head，后者覆盖前者（对齐 TS resolveConfig）
    merged = c1.service_config("database", base={"a": 0, "c": 0}, head={"b": 2})
    assert merged == {"a": 1, "b": 2, "c": 0}


def test_service_config_none_skipped() -> None:
    ctx = Context()
    assert ctx.service_config("database", base=None, head=None) == {}
    c1 = ctx.intercept("database", {"port": 5432})
    assert c1.service_config("database", head=None) == {"port": 5432}


def test_service_config_custom_merge() -> None:
    ctx = Context()
    c1 = ctx.intercept("database", {"lists": [1]})
    c2 = c1.intercept("database", {"lists": [2]})

    def custom_merge(*configs):
        result: dict[str, Any] = {}
        for config in configs:
            for key, value in config.items():
                if isinstance(value, list):
                    result[key] = result.get(key, []) + value
                else:
                    result[key] = value
        return result

    # 通过服务上的 Config.merge 支持（D1 Service 的形态）
    class FakeService:
        Config = type("Config", (), {"merge": staticmethod(custom_merge)})()

        def __init__(self, ctx: Any, cf: dict) -> None:
            self.cf = cf

    # 模拟提供 service 后读取
    async def main() -> None:
        handle = ctx.provide("database", object())
        # 直接调用 reflect.service_config 的 merge 分支：
        # 服务 value 尚无 Config，改为手动传 base/head 走默认路径已在上面覆盖；
        # 这里验证多次 chain 的 list 合并需走 merge——把 FakeService 挂上
        impl = ctx.reflect.store["database"][next(iter(ctx.reflect.store["database"]))]
        impl.value = FakeService(ctx, {})
        merged = c2.service_config("database", base={"lists": [0]})
        assert merged == {"lists": [0, 1, 2]}
        if handle is not None:
            task = handle() if callable(handle) else None
            if task:
                await task

    asyncio.run(main())


# ── inject 声明式配置联动 ─────────────────────────────────


def test_inject_config_reaches_service_config() -> None:
    async def main() -> None:
        ctx = Context()
        seen: list[Any] = []
        # 服务需存在（inject 声明即依赖声明）
        provide_handle = ctx.provide("database", "db-conn")

        def consumer(plugin_ctx: Any, config: Any):
            # 插件 ctx 的拦截链应包含 inject 声明的配置
            seen.append(plugin_ctx.service_config("database"))
            return None

        consumer.inject = {"database": {"host": "injected"}}  # type: ignore[attr-defined]
        fiber = ctx.plugin(consumer)
        await fiber
        assert seen == [{"host": "injected"}]
        # 插件的拦截链不污染 root
        assert ctx.service_config("database") == {}
        task = provide_handle()
        if task is not None:
            await task

    asyncio.run(main())


def test_inject_config_with_intercept_chain() -> None:
    async def main() -> None:
        ctx = Context()
        seen: list[Any] = []
        # 外层 intercept 提供 host
        outer = ctx.intercept("database", {"host": "outer", "port": 1})
        provide_handle = ctx.provide("database", "db-conn")

        def consumer(plugin_ctx: Any, config: Any):
            seen.append(plugin_ctx.service_config("database"))
            return None

        consumer.inject = {"database": {"port": 2}}  # type: ignore[attr-defined]
        outer.plugin(consumer)
        await settle(8)
        # 外层拦截配置（祖先）+ 插件 inject 配置（自身层，覆盖 port）
        assert seen == [{"host": "outer", "port": 2}]
        task = provide_handle()
        if task is not None:
            await task

    asyncio.run(main())


def test_inject_null_config_skipped() -> None:
    async def main() -> None:
        ctx = Context()
        seen: list[Any] = []
        provide_handle = ctx.provide("database", "db-conn")

        def consumer(plugin_ctx: Any, config: Any):
            seen.append(plugin_ctx.service_config("database"))
            return None

        consumer.inject = {"database": None}  # type: ignore[attr-defined]
        fiber = ctx.plugin(consumer)
        await fiber
        # None 配置不写入拦截链
        assert seen == [{}]
        task = provide_handle()
        if task is not None:
            await task

    asyncio.run(main())


# ── 与依赖装载的协同 ───────────────────────────────────────


def test_intercept_with_dependency_lifecycle() -> None:
    async def main() -> None:
        ctx = Context()

        def provider(plugin_ctx: Any, config: Any):
            # 服务构造时读取外层拦截链的合并配置
            merged = plugin_ctx.service_config("database")
            plugin_ctx.provide("database", merged)
            return None

        def consumer(plugin_ctx: Any, config: Any):
            pass

        consumer.inject = {"database": None}  # type: ignore[attr-defined]
        outer = ctx.intercept("database", {"host": "outer", "port": 5432})
        outer.plugin(provider)  # provider 不声明 inject（避免自依赖）
        outer.plugin(consumer)
        await settle(12)
        # 服务配置来自外层 intercept 链
        assert ctx.get("database") == {"host": "outer", "port": 5432}

    asyncio.run(main())
