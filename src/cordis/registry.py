"""Registry —— 插件注册表。

对应 cordis ``packages/core/src/registry.ts``：
- ``Plugin`` 三形态：函数 / 类 / 带 ``apply`` 方法的对象
- 同一插件可多次装载成为多个独立 Fiber（同 runtime、不同实例）
- ``ctx.plugin()`` 返回可 await 的 Fiber
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .disposable import DisposableList
from .fiber import Fiber

if TYPE_CHECKING:
    from .context import Context as ContextType


def Inject(name: str, config: Any = None) -> Callable:
    """声明式依赖注入装饰器（对应 cordis registry.ts 的 ``@Inject``）。

    用法（类与函数插件均可）：:

        @Inject("database", {"host": "db"})
        def plugin(ctx, config): ...

        @Inject("database")
        class Plugin: ...

    类装饰器写入 ``cls.inject``（子类自动继承父类声明并浅拷贝独立）；
    函数装饰器附加 ``fn.inject`` 属性。插件装载时按 ``inject`` 声明解析
    依赖与拦截配置。
    """

    def decorator(target: Any) -> Any:
        inject = dict(getattr(target, "inject", None) or {})
        inject[name] = config
        target.inject = inject
        return target

    return decorator


@dataclass
class PluginRuntime:
    """一个插件的运行时元信息（同一插件所有实例共享）。"""

    name: str | None
    callback: Callable
    config_validator: Callable[[Any], Any] | None = None
    fibers: DisposableList[Fiber] = field(default_factory=DisposableList)


def _is_applicable(object_: Any) -> bool:
    return object_ is not None and isinstance(object_, dict) and "apply" in object_


def resolve_inject(inject: Any) -> dict[str, Any]:
    """把 inject 声明解析为 dict：列表（值为 None）或 dict（浅拷贝）。"""
    result: dict[str, Any] = {}
    if not inject:
        return result
    if isinstance(inject, (list, tuple)):
        for name in inject:
            result[name] = None
    elif isinstance(inject, dict):
        result.update(inject)
    return result


class RegistryService:
    """插件注册表：装载插件为 Fiber，管理 runtime 与实例计数。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._counter = 0
        self._internal: dict[Callable, PluginRuntime] = {}

    def __repr__(self) -> str:
        return f"<RegistryService size={len(self._internal)}>"

    @property
    def counter(self) -> int:
        self._counter += 1
        return self._counter

    @property
    def size(self) -> int:
        return len(self._internal)

    def resolve(self, plugin: Any) -> Callable | None:
        """把插件解析为可执行的回调（函数 / 类 / 对象的 apply 方法）。"""
        if callable(plugin):
            return plugin
        if _is_applicable(plugin):
            return plugin["apply"]
        return None

    def has(self, plugin: Any) -> bool:
        callback = self.resolve(plugin)
        return callback is not None and callback in self._internal

    def delete(self, plugin: Any):
        """卸载某插件（无剩余实例时从注册表移除，并卸载全部纤维）。"""
        callback = self.resolve(plugin)
        runtime = self._internal.get(callback) if callback else None
        if not runtime:
            return None
        self._internal.pop(callback, None)
        for fiber in list(runtime.fibers):
            fiber.dispose()
        return runtime

    def keys(self):
        return self._internal.keys()

    def values(self):
        return self._internal.values()

    def entries(self):
        return self._internal.items()

    def plugin(self, plugin: Any, config: Any = None, *, ctx: ContextType | None = None) -> Fiber:
        """装载一个插件并返回其 Fiber（可 await）。

        同一插件多次调用得到多个独立实例；返回的 Fiber 挂载在调用方上下文
        的生命周期上（调用方销毁 → 实例级联卸载）。
        """
        ctx = ctx or self.ctx
        callback = self.resolve(plugin)
        if not callback:
            raise TypeError(
                "invalid plugin, expect function or object with an 'apply' method, "
                f"received {type(plugin).__name__}"
            )
        ctx.fiber.assert_active()

        runtime = self._internal.get(callback)
        if not runtime:
            if isinstance(plugin, dict):
                name = plugin.get("name")
            else:
                # 函数插件取 __name__（TS 是 fn.name，Python 属性为 __name__）
                name = getattr(plugin, "name", None) or getattr(plugin, "__name__", None)
            validator = getattr(plugin, "Config", None)
            runtime = PluginRuntime(name=name, callback=callback, config_validator=validator)
            self._internal[callback] = runtime

        return Fiber(
            ctx,
            config=config,
            inject=resolve_inject(getattr(plugin, "inject", None)),
            runtime=runtime,
        )

    def inject(self, inject: Any, callback: Callable, *, ctx: ContextType | None = None) -> Fiber:
        """以声明式依赖注入的形式装载一个函数插件。"""
        plugin_obj = {"inject": inject, "apply": callback, "name": callback.__name__}
        return self.plugin(plugin_obj, ctx=ctx)
