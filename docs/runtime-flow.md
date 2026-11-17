# Runtime flow / 运行流程

The README diagram is a labelled one-page illustration of the shipped boot and request path. It is
not a live trace, not telemetry and not a performance measurement.
README 中的流程图是一页式示意标注，描述实际启动与请求链路，不是实时记录、监控数据或性能数据。

![Animated runtime diagram / 动态流程图](../assets/runtime-flow.svg)

Static, motion-free version for print and diff review: [`assets/runtime-flow-static.svg`](../assets/runtime-flow-static.svg).
需要无动画版本（打印、评审）时使用静态图。

## What the diagram claims / 图里画的到底是什么

| Part | Meaning / 含义 |
|---|---|
| Boot line | `main.py` → `create_app()` → lifespan catalogs and 10-second probes → `:8001 ready`. A failed probe marks a component unavailable instead of aborting; shutdown closes background work, sub-agents, database leases, the auth pool, storage, RAG and the router in order. 启动只做一次，失败降级；关闭按固定顺序等待释放。 |
| Entry row | Web, JSON, SSE and MCP enter the same service; the operator gate returns 401/403, request validation 422, admission 503, session conflict 409 and the 120-second deadline 504. 四种入口共用一条服务链路，错误类别在入口处就分开。 |
| Plan bar | `request_query.yml` fixes `node_order` for the default request; `WorkflowRunner` walks it with `max_retries 0` and aborts on failure. An explicit `plan_id` or `auto_plan=true` selects another executable plan before admission, but the planner never inserts or skips nodes at runtime. 默认请求的顺序来自 `request_query.yml`；`plan_id`/`auto_plan` 只决定选哪份计划，运行期仍不能插入、跳过或重试节点。 |
| Node row | `parse_nl → schema_linking → generate_sql → validate_sql → execute_sql`. Each card names the value it writes into `WorkflowContext`; the SQL policy and the write check gate execution. 五个节点只通过上下文通信。 |
| Response card | JSON and SSE return `QueryResponse`; MCP projects the same status, identifiers, SQL and errors into a tool result. The trace is persisted before the reply. 一份结果契约，三种出口。 |
| Footnotes | Guards: `PermissionManager`, `ConstraintEnforcer`, SQL policy, LLM budget. Branches: skipped execution, blocked validation, node abort, explicit degradation and HTTP error classes. 护栏与分支各压成一行。 |

The diagram deliberately omits SSE heartbeat framing, per-driver connection leases and per-node
latency figures. Those live in [`query-contract.md`](optimization/query-contract.md) and
[`runtime-and-load.md`](optimization/runtime-and-load.md), because drawing them would break the
one-page layout.

图有意省略 SSE 心跳帧、逐驱动连接租约与逐节点耗时。这些写在[查询契约](optimization/query-contract.md)
与[运行与负载](optimization/runtime-and-load.md) 里，画上去会破坏一页式布局。

## Text form / 文本形态

```text
in    web workspace · JSON · SSE · MCP  →  auth · validate · bind session and target
plan  PlanLoader reads request_query.yml  →  WorkflowRunner owns node_order
run   parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
out   executed: read-only rows  ·  skipped/blocked: explicit status  →  QueryResponse
gate  PermissionManager · ConstraintEnforcer · SQL policy · LLM budget (every step)
```

```text
入   Web 工作台 · JSON · SSE · MCP  →  认证 · 校验 · 绑定会话与目标库
计划 PlanLoader 读取 request_query.yml  →  WorkflowRunner 按 node_order 推进
执行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
出   执行：只读结果  ·  跳过/阻断：明确状态  →  QueryResponse
闸门 PermissionManager · ConstraintEnforcer · SQL 策略 · LLM 预算（每步生效）
```

## Regenerate / 重新生成

```bash
python scripts/render_runtime_flow.py
```

The script writes both SVGs into `assets/` and touches nothing else: no network, no model, no
database, no environment secrets. The canvas is 1000 × 398 so the README stays compact; motion is
declarative SMIL and degrades to a still image. The script fails if a label exceeds its card width.

脚本只用标准库，输出两个 SVG 到 `assets/`，画布 1000 × 398，保持 README 紧凑；动画是声明式 SMIL，
不支持动画时退化为静态图。文案超出卡片宽度时脚本会报错退出。

When you change a node name, a guard, an endpoint or the execution semantics, update in the same
commit:

1. `scripts/render_runtime_flow.py` — the labels live in the small tables inside `build()`.
2. The text form in this file and the caption in all four READMEs.
3. This file, if the claim table above changed.

修改节点名、护栏、接口或执行语义时，请在同一次提交里同步：脚本里的文案、本文件的五行文本形态，
以及四份 README 的图注。
