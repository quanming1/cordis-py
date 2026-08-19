"""logger 日志服务测试（PRD-D2 AC1：作用域 / 等级 / 方法映射）。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from cordis import Context


async def settle(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── 作用域命名 ──────────────────────────────────────────────


def test_root_logger_scope(caplog: Any) -> None:
    ctx = Context()
    assert ctx.logger.scope == "root"
    with caplog.at_level(logging.INFO, logger="cordis.root"):
        ctx.logger.info("hello")
    assert "hello" in caplog.text
    assert any(r.name == "cordis.root" for r in caplog.records)


def test_plugin_logger_scope(caplog: Any) -> None:
    async def main() -> None:
        ctx = Context()

        def plugin(plugin_ctx: Any, config: Any):
            plugin_ctx.logger.info("from-plugin")
            return None

        plugin.__name__ = "my-plugin"
        fiber = ctx.plugin(plugin)
        await settle(6)
        assert fiber.runtime.name == "my-plugin"
        assert ctx.logger.scope == "root"

    with caplog.at_level(logging.INFO):
        asyncio.run(main())
    # 插件 ctx 的 logger scope 为 fiber 名
    assert any(r.name == "cordis.my-plugin" for r in caplog.records)


def test_child_logger() -> None:
    ctx = Context()
    child = ctx.logger.child("database")
    assert child.scope == "root.database"
    assert child._logger.name == "cordis.root.database"
    grand = child.child("orm")
    assert grand.scope == "root.database.orm"


# ── 等级控制 ────────────────────────────────────────────────


def test_level_filter(caplog: Any) -> None:
    ctx = Context()
    previous = ctx.logger.level
    ctx.logger.level = logging.WARNING
    ctx.logger.info("hidden")
    ctx.logger.warn("shown")
    ctx.logger.level = previous
    assert "shown" in caplog.text
    assert "hidden" not in caplog.text


def test_level_property() -> None:
    ctx = Context()
    ctx.logger.level = logging.ERROR
    assert ctx.logger.level == logging.ERROR


# ── 方法映射 ────────────────────────────────────────────────


def test_method_mapping(caplog: Any) -> None:
    ctx = Context()
    with caplog.at_level(logging.DEBUG, logger="cordis.root"):
        ctx.logger.trace("t")
        ctx.logger.debug("d")
        ctx.logger.success("s")
        ctx.logger.fatal("f")
    text = caplog.text
    assert "t" in text and "d" in text and "s" in text and "f" in text


def test_error_level_records(caplog: Any) -> None:
    ctx = Context()
    with caplog.at_level(logging.ERROR, logger="cordis.root"):
        ctx.logger.error("boom")
    assert any(r.levelno == logging.ERROR for r in caplog.records)
