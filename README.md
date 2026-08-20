# cordis-py

[English README](README.en.md)

[Cordis](https://github.com/cordiverse/cordis) 的 Python 移植 —— 一个面向插件系统的时空可组合性元框架。

> 当前版本：`0.4.0`。核心语义已完成对 Cordis `core` 的对标验收；作为 0.x 版本，公共 API 在后续迭代中仍可能调整。

## 核心理念

Cordis 把插件系统的动态组合拆成两个正交维度：

- **时间可组合性**：插件创建的副作用可以完整撤销，卸载时不会遗留监听器、服务或资源。
- **空间可组合性**：插件声明依赖，并在依赖上线、下线或替换时自动加载、卸载和重载。

`cordis-py` 使用 Python 3.12+ 和标准库实现，当前没有运行时第三方依赖。

## 已实现能力

- `Context`：根上下文、子上下文、服务属性访问和生命周期管理
- `Fiber`：插件实例状态机、依赖刷新、重载、配置更新和级联卸载
- `Effect`：清理函数、`None`、awaitable、同步生成器和异步生成器
- 插件：函数、类、对象三种形态，以及同一插件的多实例装载
- 服务：`ctx.provide()`、`ctx.get()`、`ctx.set()` 和 `Service` 基类
- 依赖注入：`@Inject` 装饰器和 `ctx.inject()`
- 事件：`emit`、`parallel`、`serial`、`bail`、`waterfall` 与 `once`
- 隔离：`ctx.isolate()` 为同名服务创建独立实例域
- 配置：`ctx.intercept()` 和 `service_config()` 配置合并
- 日志：作用域 `Logger`，基于标准库 `logging`

## 安装

从 GitHub Release 安装：

```bash
python -m pip install https://github.com/quanming1/cordis-py/releases/download/v0.4.0/cordis_py-0.4.0-py3-none-any.whl
```

从源码安装：

```bash
git clone https://github.com/quanming1/cordis-py.git
cd cordis-py
python -m pip install .
```

开发环境安装测试和 lint 工具：

```bash
python -m pip install -e ".[dev]"
```

要求：Python `>=3.12`。

## 快速开始

下面的例子展示一个数据库服务和一个依赖数据库的应用插件。数据库下线时，应用插件会自动卸载；新的数据库上线时，应用插件会自动重载。

完整可运行文件见 [`examples/basic.py`](examples/basic.py)。

```python
import asyncio
from typing import Any

from cordis import Context, Inject, Service


class Database(Service):
    provide = "database"

    def __init__(self, ctx: Context, version: str) -> None:
        self.version = version
        super().__init__(ctx)

    def query(self, sql: str) -> str:
        return f"{self.version}: {sql}"


def database_plugin(ctx: Context, config: dict[str, str]):
    database = Database(ctx, config["version"])
    print(f"database loaded: {database.version}")
    return lambda: print(f"database unloaded: {database.version}")


@Inject("database")
def application_plugin(ctx: Context, config: Any):
    database = ctx.database
    ctx.on("query", lambda sql: print(database.query(sql)))
    print(f"application loaded with: {database.version}")
    return lambda: print(f"application unloaded from: {database.version}")


async def wait_for(value: Any) -> None:
    if value is not None:
        await value


async def main() -> None:
    ctx = Context()

    provider = ctx.plugin(database_plugin, {"version": "v1"})
    consumer = ctx.plugin(application_plugin)
    await provider
    await consumer
    ctx.emit("query", "select 1")

    # 服务下线：consumer 自动卸载并回到 PENDING。
    await wait_for(provider.dispose())

    # 新服务上线：consumer 自动重载并重新监听事件。
    provider = ctx.plugin(database_plugin, {"version": "v2"})
    await provider
    await consumer
    ctx.emit("query", "select 2")

    # 根上下文销毁：所有子插件和副作用按生命周期级联清理。
    await wait_for(ctx.dispose())


asyncio.run(main())
```

运行仓库内示例：

```bash
set PYTHONPATH=src
python examples/basic.py
```

PowerShell：

```powershell
$env:PYTHONPATH = "src"
python examples/basic.py
```

预期输出类似：

```text
=== load plugins ===
[database] loaded v1
[application] loaded with v1
v1: select 1
=== provider unloads: consumer becomes pending ===
[application] unloaded from v1
[database] unloaded v1
consumer state: PENDING
=== a new provider appears: consumer reloads automatically ===
[database] loaded v2
[application] loaded with v2
v2: select 2
```

## 核心用法

### Effect：注册可撤销副作用

`ctx.effect()` 会把副作用绑定到当前 Fiber。Fiber 卸载时，清理函数按后进先出顺序执行。

```python
def setup_connection():
    print("connect")

    def cleanup():
        print("disconnect")

    return cleanup


handle = ctx.effect(setup_connection, "database-connection")
handle()  # 幂等清理
```

也可以使用生成器表达资源和清理逻辑：

```python
def setup_resources():
    open_connection()
    yield close_connection

    register_listener()
    yield remove_listener
```

清理顺序为 `remove_listener` → `close_connection`。

异步 Effect、异步插件和异步清理需要在运行中的 `asyncio` 事件循环内使用。

### 插件与 Fiber

插件函数接收 `(ctx, config)`，可以返回清理函数、awaitable 或生成器：

```python
def plugin(ctx, config):
    print("loaded", config)
    return lambda: print("unloaded")


fiber = ctx.plugin(plugin, {"name": "demo"})
await fiber
print(fiber.state.name)  # ACTIVE
cleanup = fiber.dispose()
if cleanup is not None:
    await cleanup
```

同一个插件可以装载多个独立实例：

```python
first = ctx.plugin(plugin, {"name": "first"})
second = ctx.plugin(plugin, {"name": "second"})
```

Fiber 还支持配置重载：

```python
await fiber.update({"name": "new-config"})
await fiber.restart()
```

### Service 与依赖注入

继承 `Service` 后，实例构造会自动提供服务：

```python
class Cache(Service):
    provide = "cache"

    def get(self, key: str):
        ...


cache = Cache(ctx)
assert ctx.cache is cache
```

使用 `@Inject` 声明依赖：

```python
@Inject("cache")
def consumer(ctx, config):
    cache = ctx.cache
    return lambda: print("consumer unloaded")
```

依赖服务不存在时，consumer Fiber 保持 `PENDING`；服务出现后自动进入 `ACTIVE`。

### 事件

```python
remove = ctx.on("message", lambda message: print(message))
ctx.emit("message", "hello")
remove()

ctx.once("ready", lambda: print("runs once"))
```

异步派发：

```python
await ctx.parallel("shutdown")
result = await ctx.serial("before-request", request)
result = ctx.bail("authorize", request)
```

### 隔离上下文

同一个服务名可以在不同隔离域中拥有不同实现：

```python
tenant_a = ctx.isolate("database", "tenant-a")
tenant_b = ctx.isolate("database", "tenant-b")

tenant_a.provide("database", "A-connection")
tenant_b.provide("database", "B-connection")

assert tenant_a.get("database") == "A-connection"
assert tenant_b.get("database") == "B-connection"
```

### 配置拦截

```python
scoped = ctx.intercept("database", {"host": "localhost"})
config = scoped.service_config("database", head={"port": 5432})
# {"host": "localhost", "port": 5432}
```

### Logger

```python
ctx.logger.info("application started")
ctx.logger.success("database connected")
ctx.logger.child("database").debug("query prepared")
```

Logger 使用 Python 标准库 `logging`；handler 和输出格式由应用配置。

## 当前状态与已知差异

`0.4.0` 已通过 116 项测试，并完成 Cordis `core` 测试语义对标。以下能力暂未实现：

- `traceable`：服务属性随调用上下文自动追踪的完整 Shadow 机制
- `associate`：关联属性机制
- 原版框架层完整的 `Service.invoke/extend` 拦截扩展
- `cordis-hmr`：源码文件监听与模块热替换
- `logger-console` / WebUI 等日志输出层
- JavaScript Proxy、原型链和对象身份等无法直接映射到 Python 的细节

这些差异不影响当前插件、服务、Effect、事件和依赖生命周期的核心使用场景。详细对标记录见 [`docs/prd/PRD-E1-parity.md`](docs/prd/PRD-E1-parity.md)。

## 开发与验证

```bash
python -m pytest
python -m ruff check .
```

项目按 [Rondo 方法](https://quanming1.github.io/minimal-blog/posts/rondo-method/)推进，详见 [`AGENTS.md`](AGENTS.md) 和 [`docs/PROCESS.md`](docs/PROCESS.md)。

## 版本与许可证

- 当前版本：`0.4.0`
- 发布页面：[GitHub Releases](https://github.com/quanming1/cordis-py/releases/tag/v0.4.0)
- 许可证：MIT
