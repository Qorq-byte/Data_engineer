# 子 Agent 的固定工作流与租户作用域

实现位于 `app/api/subagents.py`、`app/subagent/workflow.py` 和 `app/mcp/subagent_tools.py`。
验证：`tests/test_api/test_subagent_workflow.py`（真实 SQLite，脚本模型仅控制生成输出）。

## 支持的请求

`POST /api/v1/subagents/{name}/query` 要求 JWT 身份通过 operator gate，同时符合子 Agent 的
`access_control.allowed_users`。此 REST 字段使用用户 ID 字符串（例如 `["1"]`），`["*"]` 表示所有已授权
operator，空列表拒绝全部。GET 列表和详情使用认证租户和同一可见性规则。新旧配置的租户由 `tenant_id` 绑定，缺省值为 `1`。

受信配置需显式绑定已连接的 registry ID，例如：

```yaml
name: ecommerce_analyst
tenant_id: 1
workflow_enabled: true
domain: ecommerce
capabilities: [nl_query]
context:
  databases:
    - db_id: sqlite_demo
  enable_execution: true
  enable_multi_candidate: false
access_control:
  allowed_users: ["1"]
```

`db_id` 来自数据库连接 API 的结果。YAML 里旧的 `alias/connection_ref` 仅是声明，当前没有引用解析器，
不会被当作可执行绑定。单一绑定可省略请求 database；多个绑定必须明确选择。未绑定/未连接/未激活返回 409，
越库、越域、用户不允许、配置禁止执行或多候选返回 403。任意非空 context 返回 400。

`nl_query` 能力接收 `nl_text`、可选 `database/domain`、`execute`（默认 false）、`num_candidates`（默认 1）和空 context。`metric_query` 能力接收显式 `metrics`、`metric_catalog_version` 及可选的 `metric_dimensions`、`metric_filters`、`metric_grain`，继承目标范围、目录绑定和版本检查。两种动作分别使用固定的 `request_query` 和 `metric_query`，继承共享请求准入、
HTTP 截止时间、单请求预算、权限默认拒绝、SQL 只读/行数/语句超时、审计、会话和追踪。
响应保留 `status/data/errors/trace_id` 外层，data 现在是完整 QueryResponse，另有 execution_backend；
这是旧实验编排结果的有意兼容性变化，旧消费者应按 QueryResponse 读取 SQL/validation/execution。
trace_id 是 workflow_ids[0] 对应的持久化 JSONL 工作流 ID，不是工作台内存摘要的 tr_ ID。

独立 MCP HTTP 服务提供 `list_subagents` 与 `subagent_query`，使用经过认证的 operator Bearer 身份。工作台在自然语言输入区列出已启用且有数据库绑定的 `nl_query` 子 Agent。单 worker 从受信 YAML 加载定义，共享 worker 从当前发布代加载封存的租户注册目录。 [共享 worker 配置](immutable-workers.md#封存共享-worker-的子-agent-目录)

## 实验组件与部署边界

- `main.py:105` 创建的 OrchestratorAgent 与六角色注册仍属实验组件，不接管主请求。
  `app/agents/sql_generator.py` 无 generator 注入时只返回空候选；注册不等于真实生成与执行。
- `SubagentInstance.query()` / `SubagentRouter.route_api()` 是内部实验接口，仍有宽 context 合并，
  不应作为外部安全入口；本次 REST 已不再调用它们。
- 外部子 Agent 的指标动作由固定 Harness 执行。端点使用配置中的 `capabilities` 允许对应动作。
- YAML 的独立模型/temperature/max_tokens、max_turns、每小时 rate_limit 不是本次 REST 的独立控制器；
  REST 使用全局受预算模型和共享准入。现有 check_rate_limit 需要外部计数，未提供滑动窗口。
- 用户归属来自认证记录。同一租户的配置、会话、查找和 SQL 连接按现有租户作用域访问。

真正的多 Agent 产品需要先确定协作目的，让角色输出成为固定 YAML 计划的数据，所有执行、调用、失败、
取消和审计都受同一个 Harness 控制；不能让 LLM 选择下一节点，也不能在节点中互相调用。
