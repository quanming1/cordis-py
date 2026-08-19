# PRD-B2：Registry + Fiber 完整状态机 + epoch 依赖刷新

| 项 | 值 |
|---|---|
| 阶段 | B2 |
| 状态 | 已验收 |
| 分支 | feature/B2-registry |
| 日期 | 2026-08-19 |

## 1. 背景与目标

B1 交付了 Effect 机制（可撤销副作用内核）。本阶段把"单个副作用"升级为
"插件生命周期 + 服务依赖"的系统：

- 插件（function / class / object 三形态）装载成 Fiber 实例
- Fiber 完整状态机（PENDING/LOADING/ACTIVE/FAILED/DISPOSED/UNLOADING）
- **epoch 依赖刷新**：插件声明依赖（inject），服务上线/下线/替换时，
  依赖它的插件自动装载/卸载/重载

对应 cordis 原实现：`registry.ts`（约 214 行）、`fiber.ts` 的状态机与 epoch
部分（约 250 行）、`reflect.ts` 的 store/provide/notify/_checkImpl（约 120 行）。

## 2. 功能需求（FR）

- FR1 `Plugin` 三形态：函数 `(ctx, config)` / 类 `(ctx, config)` 构造 / 对象
  `{ apply(ctx, config) }`；插件可带 `name`、`Config`（配置校验器）、`inject`（依赖声明）
- FR2 `RegistryService`：
  - `ctx.plugin(plugin, config)` / `ctx.inject(deps, callback)`：装载插件
  - 同一插件多次装载 = 多独立实例（同 runtime、不同 Fiber）
  - 返回可 await 的 Fiber（`await ctx.plugin(...)` = 等装载完成，出错则抛）
  - `runtime.fibers` 维护活实例；插件全部卸载后 runtime 从注册表移除
- FR3 `Fiber` 完整状态机：`PENDING → LOADING → ACTIVE`（成功）/ `FAILED`（出错，
  错误记入日志）/ 依赖不满足回 `PENDING`；`dispose()` → `UNLOADING → DISPOSED`
- FR4 epoch 依赖刷新：
  - 服务的提供（`ctx.provide(name, factory_or_value, check)`）绑定在提供者的
    fiber 生命周期上（提供者卸载 → 服务自动下线）
  - `inject: { name: config }` 的服务上线 → 等待中的依赖插件自动装载
  - 服务下线 → 依赖插件自动卸载（其副作用全部撤销，回 PENDING）
  - 服务替换（重新 provide）→ 依赖插件重载
  - `epoch` 由所依赖服务的宿主 fiber uid 拼接而成，变化即触发重载
- FR5 依赖解析：`fiber._checkImpl` 校验依赖可用性 + `check` 谓词；
  `fiber.store` 记录本 fiber 提供的服务，供依赖方查找
- FR6 `ctx.get(name)` / `ctx.set(name, value)`：服务表的直接读写
  （属性风格 `ctx.name` 留 C1 的 isolate/Proxy 层）
- FR7 Fiber 挂载进父上下文生命周期：父 context 销毁 → 所有子插件 fiber 级联清理

## 3. 非目标

- 不实现 isolate 隔离键 / Proxy 属性拦截（C1）
- 不实现 intercept 配置合并 / 声明式树形加载（C2）
- 不实现 Events 事件系统（B3）
- Config 校验仅支持可调用校验器，不做 Standard Schema 等价物

## 4. 技术方案

- 新模块 `src/cordis/registry.py`（PluginRuntime/RegistryService/Inject 解析）；
  扩展 `src/cordis/reflect.py`（简化 ReflectService：store/provide/get/set/notify）；
  升级 `src/cordis/fiber.py`（完整状态机 + epoch）
- Fiber 状态数据：`uid`（registry 全局递增）、`state`、
  `inject`（声明的依赖名→配置）、`store`（提供的服务，dict）
- 服务 impl：`{ name, value, fiber, check }`，存放于 Reflect `store`（按名字）
- epoch 算法（对齐 TS `_refresh`）：对每个 `inject` 名称，取其实 impl 的
  `fiber.uid`，拼接成字符串指纹；任何 impl 不存在则 epoch 为 `INACTIVE` 哨兵
- 依赖变化传播（对齐 `notify`）：遍历 `registry.values()` 的所有 fiber，
  对声明了受影响服务名的 fiber 执行 `_checkImpl` + `_refresh`，收集需重载者并 `await`
- `Fiber.await()`：等待进行中的 LOADING/UNLOADING 惯性完成；`FAILED` 抛存储的错误
- `context.emit('internal/plugin'...)` 留 B3，先用内联回调

## 5. 验收标准（AC）

- AC1 `python -m pytest` 全绿，覆盖：
  - 三形态插件装载 / 卸载（副作用撤销）/ 多实例独立
  - `await ctx.plugin()` 出错上抛；`ctx.get/set` 读写
  - 依赖上线 → 自动装载；依赖下线 → 自动卸载；服务替换 → 自动重载
  - 服务提供者 fiber 销毁 → 服务下线 → 依赖方卸载（级联回滚）
  - Fiber 状态迁移正确（PENDING→LOADING→ACTIVE→DISPOSED 等）
- AC2 `python -m ruff check .` 通过
- AC3 B1 既有效果语义不回归（原有 25 个测试保持通过）

## 6. 变更记录

| 日期 | 变更 | 理由 |
|---|---|---|
