"""cordis-py 基础使用示例：插件、服务、依赖注入、事件与自动重载。"""
from __future__ import annotations

import asyncio
from typing import Any

from cordis import Context, Inject, Service


class Database(Service):
    """一个随提供者插件生命周期上下线的数据库服务。"""

    provide = "database"

    def __init__(self, ctx: Context, version: str) -> None:
        self.version = version
        super().__init__(ctx)

    def query(self, sql: str) -> str:
        return f"{self.version}: {sql}"


async def wait_for(value: Any) -> None:
    """等待 cordis 返回的可选清理任务。"""
    if value is not None:
        await value


def database_plugin(ctx: Context, config: dict[str, str]):
    """提供 database 服务，并返回插件卸载时的清理函数。"""
    database = Database(ctx, config["version"])
    print(f"[database] loaded {database.version}")

    def cleanup() -> None:
        print(f"[database] unloaded {database.version}")

    return cleanup


@Inject("database")
def application_plugin(ctx: Context, config: Any):
    """依赖 database 的应用插件。"""
    database = ctx.database
    print(f"[application] loaded with {database.version}")
    ctx.on("query", lambda sql: print(f"[application] {database.query(sql)}"))

    def cleanup() -> None:
        print(f"[application] unloaded from {database.version}")

    return cleanup


async def main() -> None:
    ctx = Context()

    print("=== load plugins ===")
    provider = ctx.plugin(database_plugin, {"version": "v1"})
    consumer = ctx.plugin(application_plugin)
    await provider
    await consumer
    ctx.emit("query", "select 1")

    print("=== provider unloads: consumer becomes pending ===")
    await wait_for(provider.dispose())
    print(f"consumer state: {consumer.state.name}")

    print("=== a new provider appears: consumer reloads automatically ===")
    provider = ctx.plugin(database_plugin, {"version": "v2"})
    await provider
    await consumer
    ctx.emit("query", "select 2")

    print("=== dispose the root context ===")
    await wait_for(ctx.dispose())


if __name__ == "__main__":
    asyncio.run(main())
