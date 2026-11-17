# 指标发现与 MCP：实施及验收

日期：2026-11-17；前序本地验收 4726 passed，保持当前工作区已有修改。

## 目标与选择

客户端目前需要预先知道指标名和目录版本，本阶段补齐发现→执行闭环。
本阶段先做 REST/MCP 可用接口；共享 worker 的目录封存和 MySQL 协调实库演练由
同日后续[共享目录阶段](shared-metric-catalog.md)单独记录。以下测试数字保留本阶段历史范围。
业务目录、跨表/时间语义和多租户不在本阶段。

## 范围

- `GET /api/v1/metrics?domain=...&database=...`：管理员身份、两个作用域参数必须显式给出；只返回匹配绑定的
  指标名、表列、聚合、维度以及目录 version/sha256，不返回其他绑定、服务器路径、密钥或任意 SQL。
- MCP `list_metrics` 与 `metric_query`：复用同一发现服务和 QueryRequest/submit_query；查询固定
  metric_resolve→validate_sql→execute_sql，无 LLM，默认 execute=false，最多投影 100 行并如实标记截断。
- MCP HTTP 工具调用从 Authorization Bearer 验证现有 JWT 与 ADMIN_USER_IDS；本地 stdio/in-process
  必须由宿主通过工厂参数显式启用信任。该参数不能绕过 HTTP 身份检查，也不能从工具参数选择。
  不把令牌放入 MCP 工具参数。原有五个 MCP 工具的访问策略不是本次整体认证改造的范围。
- 无有效目录/绑定/连接时明确拒绝；发现结果只证明配置，不承诺已对目标 Schema 或业务口径验收。
  在发现后目录更新时，查询仍须传旧版本并被拒绝；不自动切换版本或选择别的数据库。

## 阶段与验收标准

1. 共享的只读发现服务与 REST 路由；真实身份检查、作用域隔离、未配置/损坏/断连/未知绑定用例。
2. MCP 工具与共享投影/错误分类；保持 nl_query 兼容，真实 SQLite 校验发现后执行、版本失效、零模型调用、行数截断。
3. 验证 HTTP 请求上下文中的 JWT，以及宿主显式信任的本地调用；缺失/过期/非管理员不得读目录或执行。
4. 锁定环境运行相关测试与全量本地回归、ruff、mypy、README 检查；更新支持矩阵、使用文档和聚合证据。

验证解释器：`.stage62/.venv/Scripts/python.exe`（实际导入当前工作区）。命令使用 `-m pytest`、
`-m ruff check .`、`-m mypy app/`、`-m scripts.check_dependencies`、`scripts/check_readmes.py`。
HTTP 测试采用进程内真实 ASGI/MCP 请求；数据库使用临时 SQLite，不调用外部供应商或服务。

## 状态

实现与接线完成。相关回归 162 passed，含 11 项 REST 发现与 19 项 MCP 新测试。
ruff 与全仓 mypy（214 files）、33 项依赖检查、前端契约/构建、四语言 README 检查通过。
最终全量本地验收 **4756 passed / 1 skipped / 145 deselected**，359.39s；唯一 skipped 为 Windows uvloop，
145 项外部数据库案例未执行，两项 Pydantic ReadOnly 提示为非失败警告。
聚合证据：[metric-discovery-mcp-2026-11-17.json](results/metric-discovery-mcp-2026-11-17.json)，
包含当前源码/fixture/JUnit 哈希、依赖版本、计数与适用边界；原始日志在 ignored `data/metric-discovery-mcp/`。

## REST 使用方式

沿用 v1 的 `METRIC_CATALOG_PATH`、已登记 db_id 和结构化目录；默认未配置时仍拒绝。
持有管理员 JWT 的调用方先请求：

```text
GET /api/v1/metrics?domain=ecommerce&database=metric-db
Authorization: Bearer <operator JWT>
```

返回 version、sha256、domain、database 和按名称排序的 metrics。每项只有
name/table/column/aggregation/dimensions；不返回完整 YAML、目录路径、其他域/库绑定或任何凭据。
两个作用域参数必填且最长 128 字符；缺参数/超限返回 422，未配置/损坏/未连接/无绑定返回 400 INVALID_REQUEST，
缺令牌/非管理员分别 401/403。发现过程不运行 SQL、不创建查询会话或调用模型。

evidence 明确为 `configured_catalog; target_schema_and_business_semantics_unverified`。
发现接口不替代查询的 Schema 校验或业务指标签收。使用返回的名称/version 在查询 API 或 MCP 里显式提交，
目录已换版则拒绝旧版本，不自动重选。

## MCP 使用方式

`create_nl2sql_server()` 的工具增加为 7 个，新增的两个为：

- `list_metrics(domain, db_id)`：与 REST 相同的单绑定元数据，外加 status。
- `metric_query(domain, db_id, metrics, metric_catalog_version, metric_dimensions?, metric_filters?, execute=false)`：
  固定三节点解析/校验/执行，不接受目录路径、任意公式/SQL、模型覆盖或任意计划。
  请求版本和名称来自 list_metrics；num_candidates 固定为 1，不调用 LLM。

HTTP 客户端在每次工具调用的 HTTP `Authorization` 头里携带现有 API 的 operator JWT。
令牌不属于工具参数；未知工具参数由 FastMCP 输入校验拒绝，不会被静默忽略。
HTTP 调用验证真实请求上下文中的签名、过期时间和 ADMIN_USER_IDS；不接受由工具参数提供的身份或“本地信任”标记。

本地 stdio/in-process 没有 HTTP 身份上下文，默认拒绝新指标工具。仅由可信宿主显式配置：

```python
from app.mcp.fastmcp_server import create_nl2sql_server

# 宿主已完成初始化，在本 MCP 进程中注册目标连接并配置指标目录。
server = create_nl2sql_server(trust_local_metric_tools=True)
# await server.run_async(transport="stdio")
```

这个工厂参数不是工具参数，不能绕过 HTTP JWT；即便为 true，HTTP 缺失/过期/非管理员令牌仍被拒绝。
本地信任指整个宿主进程的权限边界，不是租户身份或网络访问授权。
MCP 进程须已连接目录绑定的 db_id；另一个 API 进程的连接不会自动传播，工具也不创建隐式数据库连接。

新工具的业务/鉴权失败返回 status=failed、error_code、http_status、errors；MCP HTTP 传输本身可以是 200，
不能只看传输状态。参数类型/未知字段等框架验证失败按 MCP 协议错误处理。
鉴权错误不回显令牌或 JWT 原始异常；内部异常不回显 SQL/路径/驱动细节。
超时与容量分别为 QUERY_TIMEOUT/504、QUERY_CAPACITY/503（含 retry_after_seconds）；取消继续传播，不转成成功或自动重试。

metric_query 的成功结果保留 query_id/session_id/workflow_ids、llm_usage/audit、sql、candidates、
validation、plan_output（目录 version/sha256 与固定节点）及 executed。execute=false 时仍校验，
返回 executed=false 和空行集，属于完成；执行开启后复用原行数/语句/HTTP 超时和权限门禁。
MCP 顶层行集最多 100 行，超出时截断且 row_count=100、truncated=true；候选排除 execution，避免旁路暴露超限行。
`nl_query` 的默认行为与既有字段保持兼容，共用同一投影/失败分类实现。

## 验证证据与边界

- `app/api/metrics.py` 在 create_app 的 operator 依赖下注册；共享发现实现为
  `app/workflow/metric_runtime.py::metric_catalog_view`，不是另一套目录解析规则。
- `app/mcp/metric_tools.py` 先校验 HTTP 身份或宿主信任，再调用发现服务或 submit_query。
  `app/mcp/query_projection.py` 共用有限行投影和失败分类，`fastmcp_server.py` 在工厂里注册工具。
- 测试使用仓库合成电商 Schema、真实临时 SQLite、真实签名/过期 JWT、真实 FastMCP 工具和进程内 ASGI HTTP；
  不是对 HTTP 依赖的替代实现。普通 fixture 5 订单/3 用户/4 商品，截断用例额外写入 150 个合成分组。
- 结果/目录哈希与 REST 一致、零模型调用、只校验、100 行截断、越域/越库/旧版本、未知参数拒绝、
  HTTP 身份负例、本地信任不能覆盖 HTTP、真实资源锁触发超时/容量、取消释放后可重用均有测试。
- 原有五个 MCP 工具沿用原访问策略，本阶段没有把整个 MCP 服务改造成统一 JWT/OAuth 服务。
  新指标工具的认证不能被解读为已有工具全部获得同等认证；MCP 网络部署仍须明确其整体受信边界。
- 未启动真实网络监听器/外部 MCP 客户端或另起 stdio 子进程，不证明代理转发、TLS、客户端 OAuth 兼容性或跨进程部署。
  未认证其他数据库上的指标结果、业务口径、共享 worker、子 Agent 指标动作或多租户隔离。

## 后续部署阶段

METRIC_CATALOG_PATH 的不可变发布包封存、启动覆盖检查和运行时一致性已在上述共享目录阶段继续实施，
本地负例和多 worker 实库结果以该阶段证据为准。缺少封存、配置不符或活跃运行时不可用仍明确拒绝；
本阶段单进程测试本身不证明共享部署。
Snowflake 专用凭据/fixture/费用范围、生产指标定义和业务标注数据仍待外部提供。
本阶段规划、实现和本地验收完成；没有部署/发布或调用外部模型/数据库。
