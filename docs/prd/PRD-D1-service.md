# PRD-D1：Service 基类 + @Inject 装饰器

| 项 | 值 |
|---|---|
| 阶段 | D1 |
| 状态 | 已验收 |
| 分支 | feature/D1-service |
| 日期 | 2026-08-19 |

## 1. 背景与目标

C1/C2 交付了服务注册/隔离/配置拦截，但"服务"仍是裸值或隐式 `ctx.provide`。
本阶段提供正式的 **Service 基类**（构造即自动提供、随提供者 fiber 生命周期
上下线、可携带 `Config.merge`）与 **`@Inject` 装饰器**（类/函数插件的声明式
依赖书写）。

对应 cordis 原实现：`service.ts`（约 80 行）、`registry.ts` 的 `Inject`
装饰器（约 40 行）。

## 2. 功能需求（FR）

- FR1 `Service` 基类：
  - `Service(ctx, name=None, *, value=None, check=None)`：构造时自动
    `ctx.provide(name, value_or_self, check)`，随提供者 fiber 生命周期上下线
  - `name` 缺省取类属性 `provide`（TS 语义），再缺省取类名小写
  - 子类可定义 `Config`（含 `merge` 静态方法，用于 C2 的配置合并）
  - 子类可定义 `check(ctx)`（服务可用性谓词，注册为 provide 的 check）
- FR2 可调用服务：Service 子类实现 `__call__` 即自动可调用（`ctx.database()`），
  对应 TS createCallable 的简化（Python 原生 callable 协议）
- FR3 `@Inject(name, config=None)` 装饰器（类与函数插件）：
  - 类：写入/继承 `cls.inject`（子类继承父类声明）
  - 函数：附加 `fn.inject` 属性
  - 与插件装载联动（registry 已按 `plugin.inject` 解析）
- FR4 `ctx.service(name, value=None, check=None)` 便捷方法？不需要——
  直接 `new Service(ctx)` 即注册。D1 提供 `Service` 导出即可

## 3. 非目标

- 不做 TS 方法级 `@Inject`（方法注入 bound service）——D 阶段后留给后续
  迭代，PRD 记录
- 不做 `Service.extend()` 拆分/代理（保持单一实例语义；多实例用 isolate）
- 不改变既有 `ctx.provide` 行为（Service 是其上层糖）

## 4. 技术方案

- 新模块 `src/cordis/service.py`：
  - `class Service:`——`__init__(ctx, name=None, *, value=None, check=None)`
    里 `ctx.provide(name, value if value is not None else self, self._check_wrapper)`
  - `check` 包装：`check(ctx)` 子类覆写的谓词；构造传入的 check 优先级更高
  - `Config.merge(*configs)` 静态方法约定（C2 的 service_config 已支持读取）
- `src/cordis/registry.py` 增加 `Inject` 装饰器（类/函数双形态）
- `src/cordis/__init__.py` 导出 `Service` / `Inject`
- 测试 `tests/test_service.py`：
  - 构造自动提供 + 属性访问 + 卸载清理
  - 提供者插件生命周期联动（服务随插件卸载）
  - `Config.merge` 经 `service_config` 生效（C2 机制闭环）
  - `@Inject` 函数/类/继承
  - callable service
  - check 谓词

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖 §4 列出的全部场景
- AC2 `python -m ruff check .` 通过
- AC3 既有 82 项测试保持通过（无回归）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|