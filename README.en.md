# cordis-py

[中文 README](README.md)

A Python port of [Cordis](https://github.com/cordiverse/cordis): a spatiotemporal composability framework for plugin systems.

> Current version: `0.4.0`. The core semantics have been validated against Cordis `core`. As a 0.x release, the public API may still change in future iterations.

## Core ideas

Cordis treats dynamic plugin composition as two orthogonal dimensions:

- **Temporal composability**: side effects created by a plugin can be fully reverted, so unloading does not leave listeners, services, or resources behind.
- **Spatial composability**: plugins declare dependencies and automatically load, unload, or reload as dependencies appear, disappear, or are replaced.

`cordis-py` is implemented for Python 3.12+ using the standard library and currently has no runtime third-party dependencies.

## Implemented features

- `Context`: root and child contexts, service attribute access, and lifecycle management
- `Fiber`: plugin instance state machine, dependency refresh, reload, config update, and cascading disposal
- `Effect`: cleanup functions, `None`, awaitables, synchronous generators, and asynchronous generators
- Plugins: function, class, and object forms, with multiple independent instances
- Services: `ctx.provide()`, `ctx.get()`, `ctx.set()`, and the `Service` base class
- Dependency injection: the `@Inject` decorator and `ctx.inject()`
- Events: `emit`, `parallel`, `serial`, `bail`, `waterfall`, and `once`
- Isolation: `ctx.isolate()` for independent instances of the same service name
- Configuration: `ctx.intercept()` and `service_config()` merging
- Logging: scoped `Logger` built on Python's standard `logging` module

## Installation

Install the published wheel from the GitHub Release:

```bash
python -m pip install https://github.com/quanming1/cordis-py/releases/download/v0.4.0/cordis_py-0.4.0-py3-none-any.whl
```

Install from source:

```bash
git clone https://github.com/quanming1/cordis-py.git
cd cordis-py
python -m pip install .
```

Install the development tools for tests and linting:

```bash
python -m pip install -e ".[dev]"
```

Requirement: Python `>=3.12`.

## Quick start

The following example creates a database service and an application plugin that depends on it. When the database goes offline, the application plugin is unloaded automatically. When a new database becomes available, the application plugin reloads automatically.

The complete runnable example is available at [`examples/basic.py`](examples/basic.py).

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

    # The provider goes offline: the consumer unloads and returns to PENDING.
    await wait_for(provider.dispose())

    # A new provider appears: the consumer reloads and registers its event again.
    provider = ctx.plugin(database_plugin, {"version": "v2"})
    await provider
    await consumer
    ctx.emit("query", "select 2")

    # The root context cascades disposal to all plugins and side effects.
    await wait_for(ctx.dispose())


asyncio.run(main())
```

Run the example from the repository root:

```bash
# macOS/Linux
PYTHONPATH=src python examples/basic.py
```

Windows cmd:

```bat
set PYTHONPATH=src
python examples/basic.py
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python examples/basic.py
```

Expected output is similar to:

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

## Core usage

### Effect: register a reversible side effect

`ctx.effect()` binds a side effect to the current Fiber. When the Fiber unloads, cleanup functions run in last-in-first-out order.

```python
def setup_connection():
    print("connect")

    def cleanup():
        print("disconnect")

    return cleanup


handle = ctx.effect(setup_connection, "database-connection")
handle()  # idempotent cleanup
```

Generators can express resource setup and cleanup as well:

```python
def setup_resources():
    open_connection()
    yield close_connection

    register_listener()
    yield remove_listener
```

Cleanup runs as `remove_listener` → `close_connection`.

Async Effects, async plugins, and async cleanup require a running `asyncio` event loop.

### Plugins and Fibers

A plugin function receives `(ctx, config)` and may return a cleanup function, an awaitable, or a generator:

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

The same plugin can be loaded as multiple independent instances:

```python
first = ctx.plugin(plugin, {"name": "first"})
second = ctx.plugin(plugin, {"name": "second"})
```

Fibers also support configuration updates and restarts:

```python
await fiber.update({"name": "new-config"})
await fiber.restart()
```

### Services and dependency injection

Subclassing `Service` automatically provides the service when an instance is constructed:

```python
class Cache(Service):
    provide = "cache"

    def get(self, key: str):
        ...


cache = Cache(ctx)
assert ctx.cache is cache
```

Declare dependencies with `@Inject`:

```python
@Inject("cache")
def consumer(ctx, config):
    cache = ctx.cache
    return lambda: print("consumer unloaded")
```

If the dependency is unavailable, the consumer Fiber remains `PENDING`. It becomes `ACTIVE` automatically when the service appears.

### Events

```python
remove = ctx.on("message", lambda message: print(message))
ctx.emit("message", "hello")
remove()

ctx.once("ready", lambda: print("runs once"))
```

Asynchronous dispatch modes:

```python
await ctx.parallel("shutdown")
result = await ctx.serial("before-request", request)
result = ctx.bail("authorize", request)
```

### Isolated contexts

The same service name can have different implementations in different isolation domains:

```python
tenant_a = ctx.isolate("database", "tenant-a")
tenant_b = ctx.isolate("database", "tenant-b")

tenant_a.provide("database", "A-connection")
tenant_b.provide("database", "B-connection")

assert tenant_a.get("database") == "A-connection"
assert tenant_b.get("database") == "B-connection"
```

### Configuration interception

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

The Logger uses Python's standard `logging` module. Handlers and formatting are configured by the application.

## Current status and known differences

`0.4.0` passes 116 tests and has been validated against the semantics of Cordis `core`. The following features are not implemented yet:

- `traceable`: the complete Shadow mechanism for automatically following the calling context when accessing services
- `associate`: associated property behavior
- The full framework-level `Service.invoke/extend` interception and extension mechanism
- `cordis-hmr`: source file watching and module hot replacement
- `logger-console` / WebUI output layers
- JavaScript Proxy, prototype-chain, and object-identity details that have no direct Python equivalent

These differences do not affect the core plugin, service, Effect, event, or dependency lifecycle scenarios. See [`docs/prd/PRD-E1-parity.md`](docs/prd/PRD-E1-parity.md) for the detailed parity record.

## Development and verification

```bash
python -m pytest
python -m ruff check .
```

The project follows the [Rondo method](https://quanming1.github.io/minimal-blog/posts/rondo-method/). See [`AGENTS.md`](AGENTS.md) and [`docs/PROCESS.md`](docs/PROCESS.md) for the workflow.

## Version and license

- Current version: `0.4.0`
- Release page: [GitHub Releases](https://github.com/quanming1/cordis-py/releases/tag/v0.4.0)
- License: MIT
