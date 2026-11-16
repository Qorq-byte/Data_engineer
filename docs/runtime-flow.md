# Runtime flow / 运行流程

The README diagram is a labelled illustration of the fixed request path; it is not a live trace,
not telemetry and not a performance measurement.
README 中的流程图是对固定请求链路的示意标注，不是实时运行记录、监控数据或性能数据。

![Animated runtime diagram / 动态流程图](../assets/runtime-flow.svg)

Static, motion-free version for print and diff review: [`assets/runtime-flow-static.svg`](../assets/runtime-flow-static.svg).
需要无动画版本（打印、评审）时使用静态图。

## What the diagram claims / 图里画的到底是什么

| Band | Meaning / 含义 |
|---|---|
| 01 Entry surfaces | Web workspace, JSON API, SSE stream and MCP each enter the same request path. 四个入口走同一条请求路径。 |
| 02 Bind | Authentication against the auth store, request validation, then session and target binding happen before any node runs. 认证、校验、绑定目标先于节点执行。 |
| 03 Control plane | `PlanLoader` reads `request_query.yml`; `WorkflowRunner` walks `node_order` and owns every transition. 顺序来自计划文件，模型不能插入、跳过或重试节点。 |
| 04 Five nodes | `parse_nl` → `schema_linking` → `generate_sql` → `validate_sql` → `execute_sql`, each emitting only into `WorkflowContext`. 五个节点只通过上下文通信。 |
| 05 Guard rails | `PermissionManager`, `ConstraintEnforcer`, the single-statement read-only policy and the LLM budget are consulted per step; unlisted tools are denied. 四道护栏逐步生效，未列出的工具默认拒绝。 |
| 06 Conditional execution | Execution is a branch, not a guarantee: no target, `execute=false`, a blocking check or a row cap all produce an explicit non-success outcome. 执行是分支，跳过与阻断都要明说。 |
| 07 One contract | JSON, SSE and MCP share `QueryResponse`; the trace is persisted and the optional audit never stores SQL. 三种出口共用一份契约。 |

Optional detail that the drawing deliberately omits: the SSE event stream emits one event per node,
cancellation closes streams and releases connection leases, and per-request usage records keep
observed values separate from missing telemetry. Those are described in
[`query-contract.md`](optimization/query-contract.md), not drawn, because a diagram that tries to show
them becomes unreadable.

图有意省略的细节：SSE 每个节点一个事件、取消时关闭流并释放连接租约、逐次用量与缺测分开记录。
这些写在 [query-contract.md](optimization/query-contract.md) 里，画上去会让图失去可读性。

## Regenerate / 重新生成

The diagram is generated, not hand-drawn. Only the standard library is required:

```bash
python scripts/render_runtime_flow.py
```

The script writes both SVGs into `assets/` and touches nothing else: no network, no model, no
database, no environment secrets. Motion is declarative SMIL, so the file stays small and degrades to
a still image wherever animation is unavailable.

脚本只用标准库，输出两个 SVG 到 `assets/`，不联网、不读写凭据。动画是声明式 SMIL，文件很小，
在不支持动画的环境里退化为静态图。

When you change a node name, a guard or the execution semantics, update in the same commit:

1. `scripts/render_runtime_flow.py` — the labels and the timeline live in the data tables at the top of
   `build()`.
2. The Mermaid source in `README.md` and `README.zh-CN.md`.
3. This file, if the claim table above changed.

修改节点名、护栏或执行语义时，请在同一次提交里同步：脚本里的文案表、两份 README 的 Mermaid
源码，以及上表的说明。
