# Runtime flow / 运行流程

The README diagram is a labelled illustration of the shipped boot and request path. It is not a live
trace, not telemetry and not a performance measurement.
README 中的流程图是对实际启动与请求链路的示意标注，不是实时运行记录、监控数据或性能数据。

![Animated runtime diagram / 动态流程图](../assets/runtime-flow.svg)

Static, motion-free version for print and diff review: [`assets/runtime-flow-static.svg`](../assets/runtime-flow-static.svg).
需要无动画版本（打印、评审）时使用静态图。

## What the diagram claims / 图里画的到底是什么

| Band | Meaning / 含义 |
|---|---|
| `00` Boot | `main.py` starts uvicorn with the `create_app` factory; `create_app()` fails closed on a weak JWT secret; the lifespan registers catalogs, probes storage/auth/RAG/LLM with 10-second bounds, and serves only after `app.state.initialized`. A failed probe degrades a named component instead of aborting. Shutdown is awaited in a fixed order. 启动先过 JWT 守卫；lifespan 在 10 秒边界内探测各组件，失败则降级，关闭按固定顺序释放。 |
| `01` Entry and admission | Web, JSON, SSE and MCP enter the same service. Every `/api/v1` route except auth and health passes the operator gate; request validation rejects bad dialects or disabled models; `submit_query` applies the 120-second deadline; `run_query` takes an admission slot and serialises the session. 四种入口共用一条服务链路，认证、校验、截止时间、准入与会话绑定依次生效。 |
| `02` Control plane | `PlanLoader` reads `request_query.yml` (mtime reload); the request builds its own five-node registry; `WorkflowRunner` rejects an empty plan, an unknown metric, a missing/duplicate node or an unsatisfied dependency before node one. 顺序来自计划文件，模型不能插入、跳过或重试节点。 |
| `03` Five nodes | `parse_nl → schema_linking → generate_sql → validate_sql → execute_sql`. Each runs `setup_input → execute → update_context → evaluate` and writes only into `WorkflowContext`; the diagram names the context key each node owns. Permission, constraint, SQL-policy and budget guard rails apply per step. 五个节点只通过上下文通信；每一步都受权限、约束、SQL 策略与预算约束。 |
| `04` Branches and failures | 401/403, 422, 503, 409 and 504 terminate before or around the workflow; `execute=false` or a missing target produces a skipped execution and `execution: null`; a failed validation blocks execution; a node exception becomes a failed evaluation with zero retries; RAG or mock providers degrade with a warning. 执行是分支，跳过与阻断都要明说。 |
| `05` End state | Per-target statuses aggregate to `completed` or `failed`; business failures still return HTTP 200. The session turn, MySQL query history, JSONL trace and optional usage audit are written before the reply. 状态聚合、统一契约、持久化，然后才是响应。 |

The diagram labels the JSON/SSE `QueryResponse` fields (MCP projects them into a tool result) but
deliberately omits SSE heartbeat framing, per-driver connection leases and per-node latency figures.
Those live in [`query-contract.md`](optimization/query-contract.md) and
[`runtime-and-load.md`](optimization/runtime-and-load.md), because drawing them would make the
picture unreadable.

图标注的是 JSON/SSE 的 `QueryResponse` 字段（MCP 会投影成工具结果），但有意省略 SSE 心跳帧、
逐驱动连接租约与逐节点耗时。这些写在[查询契约](optimization/query-contract.md) 与
[运行与负载](optimization/runtime-and-load.md) 里，画上去会让图失去可读性。

## Text form / 文本形态

The README carries the picture; this is the same path as five lines you can grep and diff.

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

The diagram is generated, not hand-drawn. Only the standard library is required:

```bash
python scripts/render_runtime_flow.py
```

The script writes both SVGs into `assets/` and touches nothing else: no network, no model, no
database, no environment secrets. Motion is declarative SMIL, so the file stays small and degrades to
a still image wherever animation is unavailable. The script fails if a label exceeds its card width.

脚本只用标准库，输出两个 SVG 到 `assets/`，不联网、不读写凭据。动画是声明式 SMIL，文件不大，
在不支持动画的环境里退化为静态图。文案超出卡片宽度时脚本会报错退出。

When you change a node name, a guard, an endpoint or the execution semantics, update in the same
commit:

1. `scripts/render_runtime_flow.py` — the labels and the timeline live in the data tables at the top of
   `build()`.
2. The text form in this file and, if a band is renamed, the caption in all README versions.
3. This file, if the claim table above changed.

修改节点名、护栏、接口或执行语义时，请在同一次提交里同步：脚本里的文案表、本文件的五行文本形态，
以及上表的说明。
