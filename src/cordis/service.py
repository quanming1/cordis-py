"""Service —— 服务基类与 @Inject 装饰器。

对应 cordis ``packages/core/src/service.ts`` 与 ``registry.ts`` 的 Inject 部分：
- ``Service`` 子类构造时自动 ``ctx.provide``，随提供者 fiber 生命周期上下线
- 子类可定义 ``Config.merge``（配置合并，供 C2 的 service_config 使用）
- 子类可覆写 ``check(ctx)``（服务可用性谓词）
- 实现 ``__call__`` 即成为可调用服务（对应 TS createCallable 的 Python 原生形态）
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar


class Service:
    """服务基类：构造即注册，随提供者上下文生命周期上下线。

    用法：:

        class Database(Service):
            Config = type("Config", (), {"merge": staticmethod(...)})()

            def __init__(self, ctx):
                super().__init__(ctx)
                ...

            def check(self, ctx) -> bool:
                return True

            def __call__(self, sql):   # 可选：可调用服务
                ...
    """

    #: 服务名缺省值（类级，TS ``static provide`` 语义）；再缺省取类名小写
    provide: ClassVar[str | None] = None

    def __init__(
        self,
        ctx: Any,
        name: str | None = None,
        *,
        value: Any = None,
        check: Callable[[], bool] | None = None,
    ) -> None:
        self.name = name or type(self).provide or type(self).__name__.lower()
        self.ctx = ctx

        if check is None:
            # 子类覆写 check() 时包装为无参谓词（reflect 的 impl.check 无参调用）
            provided_check = type(self).check
            if provided_check is not Service.check:
                check = lambda: bool(provided_check(self))  # noqa: E731

        self._dispose = ctx.provide(self.name, value if value is not None else self, check)

    def __repr__(self) -> str:
        return f"<Service {self.name!r}>"

    # 子类可覆写：服务可用性谓词（无参，对齐 TS impl.check；默认恒可用）
    def check(self) -> bool:
        return True
