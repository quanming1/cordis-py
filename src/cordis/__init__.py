"""cordis-py —— Cordis 的 Python 移植（时空可组合性元框架）。

移植自 https://github.com/cordiverse/cordis （TypeScript）。
核心语义：可撤销副作用（时间维度）+ 响应式依赖注入（空间维度）。

当前进度：B2（Registry + 插件生命周期 + epoch 依赖刷新），见 docs/TODO.yaml。
"""
from .context import Context
from .disposable import DisposableList
from .fiber import CordisError, Disposable, Effect, EffectHandle, Fiber, FiberState
from .registry import Inject, RegistryService
from .service import Service

__version__ = "0.4.0"

__all__ = [
    "Context",
    "CordisError",
    "Disposable",
    "DisposableList",
    "Effect",
    "EffectHandle",
    "Fiber",
    "FiberState",
    "Inject",
    "RegistryService",
    "Service",
]
