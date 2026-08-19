"""Logger —— 作用域日志器（对应 cordis logger.ts 的核心包装层）。

基于标准库 logging 实现（对齐项目约定），scope 命名映射到记录器名
``cordis.<scope>``；提供 cordis 风格的方法集（success/trace/fatal 等）。
输出/格式化交给应用层的 logging handler，本模块不绑定 console/WebUI。
"""
from __future__ import annotations

import logging
from typing import Any


class Logger:
    """作用域日志器：``ctx.logger`` 与 ``logger.child(name)``。"""

    __slots__ = ("scope", "_logger")

    def __init__(self, scope: str = "", level: int = logging.NOTSET) -> None:
        self.scope = scope
        name = f"cordis.{scope}" if scope else "cordis"
        self._logger = logging.getLogger(name)
        if level != logging.NOTSET:
            self._logger.setLevel(level)

    def __repr__(self) -> str:
        return f"<Logger {self.scope!r}>"

    @property
    def level(self) -> int:
        """当前生效的日志级别（对齐 TS logger.level 语义）。"""
        return self._logger.getEffectiveLevel()

    @level.setter
    def level(self, value: int) -> None:
        self._logger.setLevel(value)

    def child(self, name: str) -> Logger:
        """派生子日志器（scope 为 ``self.scope.name``）。"""
        scope = f"{self.scope}.{name}" if self.scope else name
        return Logger(scope)

    # ── 记录方法 ────────────────────────────────────────────

    def trace(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """trace：映射到 debug（logging 无 trace 等级）。"""
        self._logger.debug(message, *args, **kwargs)

    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(message, *args, **kwargs)

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.info(message, *args, **kwargs)

    def success(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """success：映射到 info（cordis 风格的成功日志）。"""
        self._logger.info(message, *args, **kwargs)

    def warn(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(message, *args, **kwargs)

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.error(message, *args, **kwargs)

    def fatal(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """fatal：映射到 critical。"""
        self._logger.critical(message, *args, **kwargs)

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        """异常上下文日志（标准库 exception 语义）。"""
        self._logger.exception(message, *args, **kwargs)

    # ── 转发其余标准库能力（setLevel 便捷）───────────────────

    def set_level(self, level: int) -> None:
        self._logger.setLevel(level)
