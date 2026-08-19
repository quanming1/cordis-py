"""Service 基类 + @Inject 装饰器测试（PRD-D1 AC1）。"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from cordis import Context, Inject, Service


async def settle(n: int = 8) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── Service 基类 ────────────────────────────────────────────


def test_service_auto_provides() -> None:
    ctx = Context()

    class Database(Service):
        pass

    db = Database(ctx)
    assert db.name == "database"  # 类名小写
    assert ctx.get("database") is db  # 构造即提供
    assert ctx.database is db  # 属性访问


def test_service_explicit_name_and_provide_attr() -> None:
    ctx = Context()

    class DB(Service):
        provide: ClassVar[str | None] = "mydb"

    db = DB(ctx)
    assert db.name == "mydb"  # 类属性 provide 优先

    class Mail(Service):
        pass

    m = Mail(ctx, "mailer")
    assert m.name == "mailer"  # 构造参数最优先


def test_service_lifecycle_with_provider() -> None:
    # 服务随提供者插件 fiber 卸载而下线
    async def main() -> None:
        ctx = Context()
        order: list[str] = []

        class Database(Service):
            def __init__(self, plugin_ctx: Any) -> None:
                super().__init__(plugin_ctx)
                self._cleanup = lambda: order.append("db-unloaded")

        def provider(plugin_ctx: Any, config: Any):
            Database(plugin_ctx)
            return lambda: order.append("provider-unloaded")

        fiber = ctx.plugin(provider)
        await fiber
        assert ctx.get("database") is not None
        task = fiber.dispose()
        if task is not None:
            await task
        assert ctx.get("database") is None  # 服务随插件卸载

    asyncio.run(main())


def test_service_check_predicate() -> None:
    # check 谓词影响依赖方装载（对齐 TS：impl.check 在 _checkImpl 时判定）
    async def main() -> None:
        ctx = Context()
        state = {"ready": False}
        order: list[str] = []

        class Database(Service):
            def check(self) -> bool:
                return state["ready"]

        Database(ctx)

        def consumer(plugin_ctx: Any, config: Any):
            order.append("loaded")
            return None

        consumer.inject = {"database": None}  # type: ignore[attr-defined]
        fiber = ctx.plugin(consumer)
        await settle(8)
        assert fiber.state.name == "PENDING"  # check 不满足 → 不装载
        assert order == []
        state["ready"] = True
        ctx.reflect.notify(["database"])
        await settle(8)
        assert fiber.state.name == "ACTIVE"  # 重检通过 → 自动装载
        assert order == ["loaded"]

    asyncio.run(main())


def test_service_config_merge() -> None:
    # C2 机制闭环：Service.Config.merge 生效于 service_config
    ctx = Context()

    def custom_merge(*configs):
        result: dict[str, Any] = {}
        for config in configs:
            for key, value in config.items():
                if isinstance(value, list):
                    result[key] = result.get(key, []) + value
                else:
                    result[key] = value
        return result

    svc_config = type("Config", (), {"merge": staticmethod(custom_merge)})()

    class Database(Service):
        Config = svc_config

    interceptor = ctx.intercept("database", {"filters": ["a"]})
    Database(ctx)
    merged = interceptor.service_config("database", head={"filters": ["b"]})
    assert merged == {"filters": ["a", "b"]}


def test_callable_service() -> None:
    ctx = Context()

    class Echo(Service):
        def __call__(self, msg: str) -> str:
            return f"echo:{msg}"

    Echo(ctx)
    assert ctx.echo("hi") == "echo:hi"  # 属性访问即服务，可直接调用


# ── @Inject 装饰器 ──────────────────────────────────────────


def test_inject_function_plugin() -> None:
    seen: list[Any] = []

    @Inject("database", {"host": "db"})
    def consumer(plugin_ctx: Any, config: Any):
        seen.append(plugin_ctx.service_config("database"))
        return None

    async def main() -> None:
        ctx = Context()
        pr = ctx.provide("database", "conn")
        fiber = ctx.plugin(consumer)
        await fiber
        assert seen == [{"host": "db"}]
        task = pr()
        if task:
            await task

    asyncio.run(main())


def test_inject_class_plugin() -> None:
    seen: list[Any] = []

    @Inject("database")
    class Consumer:
        def __init__(self, plugin_ctx: Any, config: Any) -> None:
            seen.append(plugin_ctx.get("database"))

    async def main() -> None:
        ctx = Context()
        pr = ctx.provide("database", "conn-x")
        fiber = ctx.plugin(Consumer)
        await fiber
        assert seen == ["conn-x"]
        task = pr()
        if task:
            await task

    asyncio.run(main())


def test_inject_inheritance() -> None:
    @Inject("database")
    class Base:
        pass

    # 子类继承父类的 inject 声明，并可追加
    @Inject("mail")
    class Child(Base):
        pass

    assert Child.inject == {"database": None, "mail": None}
    assert Base.inject == {"database": None}  # 父类不受影响


def test_inject_multiple() -> None:
    @Inject("database")
    @Inject("mail", {"host": "smtp"})
    def plugin(plugin_ctx: Any, config: Any):
        return None

    # 装饰器从内向外：mail 先、database 后
    assert plugin.inject == {"database": None, "mail": {"host": "smtp"}}
