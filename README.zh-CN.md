# NL2SQL 数据工程 Agent

**用自然语言向关系型数据库提问，拿到可审查、可校验、可解释的 SQL —— 或者一句明确的「做不到」。**

[English](README.md) · **简体中文** · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

[![MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml)
[![Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](app/api/)
[![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933.svg?logo=nodedotjs&logoColor=white)](package.json)

[![SQL: read-only](https://img.shields.io/badge/SQL-read--only-0B7285.svg)](app/security/sql_policy.py)
[![Status: pre-1.0 MVP](https://img.shields.io/badge/status-pre--1.0%20MVP-F5C451.svg)](#项目现状)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-4C9A5A.svg)](CONTRIBUTING.md)
[![Security policy](https://img.shields.io/badge/security-policy-4B5563.svg)](SECURITY.md)

<a href="assets/runtime-flow-static.svg"><img src="assets/runtime-flow.svg" width="1000" alt="紧凑运行流程图：启动行、入口与准入行、固定 request_query 计划、五个节点、响应卡，以及护栏与分支脚注"></a>

*生成式示意图，不是运行记录。[静态版](assets/runtime-flow-static.svg) · [图里画了什么](docs/runtime-flow.md)。*

## 项目简介

这是一个面向关系型数据库的自然语言分析 Agent Harness。语言模型负责写 SQL；跑哪个节点、某个节点
能碰哪些工具、最多返回多少行、什么时候必须停下，由固定的 YAML 计划与声明式限制决定。请求返回的
不是一个分数，而是一条 SQL 加上它背后的证据；某项检查做不了时，会明确说明。

设计遵循三条规则：

- **模型不做路由。** YAML 计划固定节点顺序；`plan_id` 或 `auto_plan` 只决定运行哪份计划，
  `WorkflowRunner` 只负责执行。
- **只读是被强制的，不是被请求的。** 单语句 AST 策略、`execute_sql` 内部的写入检查，以及驱动本身
  提供的守卫（SQLite authorizer、ClickHouse 只读 profile）各自独立生效。
- **缺口就写成缺口。** 跳过的执行、缺失的结构、mock 提供方、未验证的关联，都是明确的字段、警告或
  `null`，不会是无声的成功。

这是一个面向单管理员组的 pre-1.0 MVP。[项目现状](#项目现状)列出哪些已接通、哪些只做了一半、
哪些是刻意没有做的。

## 核心特性

- **确定性控制流。** 所选计划固定节点顺序——默认 `request_query.yml` 为五个节点。节点之间互不调用，只通过
  `WorkflowContext` 通信；某个节点失败就中断，除非计划显式声明 `on_failure: skip`。默认计划的
  `max_retries` 为 `0`。
- **只读分层生效。** 单语句解析与 AST 检查；`execute_sql` 在接触数据库前阻断写入；SQLite 与
  ClickHouse 另有驱动侧守卫。部署时应使用数据库原生的只读账号。
- **给证据，不给置信度。** 每个候选记录结构来源、获取时间、方言、假设，以及逐项检查的通过 / 未通过 /
  未验证。`confidence` 按设计恒为 `0`，因为不存在经过校准的概率。
- **结构感知的生成。** 表、列、键、注释、业务术语表与领域规则，BM25 与向量检索经倒数排名融合
  （RRF）合并。
- **学习只做建议，不改写 SQL。** 审批通过的编辑规则会成为下一次匹配查询的提示与校验警告；
  `applied_rules` 说明本次用了哪些规则，SQL 仍要经过校验与只读执行，不会被静默改写。
- **7 个连接器有真库验收。** SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、Trino、StarRocks。
  Snowflake 已实现，但独立验收仍待完成。
- **一条流水线，三种出口。** Web 工作台、JSON/SSE 接口与 MCP 工具都调用同一个请求服务。JSON 与 SSE
  返回 `QueryResponse`；MCP 把相同的状态、标识、SQL 与错误投影为工具结果字典。
- **带硬预算的模型路由。** LiteLLM 可路由到 OpenAI、Anthropic、Google、DeepSeek、Qwen 或任意兼容
  OpenAI 的自建端点；`max_llm_calls_per_query`、重试、超时与并发由 Harness 强制执行。
- **MCP 双向使用。** 四个 FastMCP 服务把 NL2SQL 与数据工具暴露给外部客户端；同一进程也能消费外部
  MCP 工具。

## 项目现状

状态以运行中的代码为准，不以设计文档为准。逐项矩阵见
[docs/support-matrix.md](docs/support-matrix.md)。

| 领域 | 状态 |
|---|---|
| 固定五节点 `request_query` 流水线 | **已实现** —— JSON、SSE 与 MCP 均已接通 |
| 约束、权限与 SQL 策略执行 | **已实现** |
| Schema 链接、多阶段校验、混合检索 | **已实现** |
| 7 个只读数据库连接器 | **已实现并测试**；Snowflake 待验收 |
| 其余 6 个工作流计划 | **默认 5 个可执行** —— `gensql_agentic`、`ez_query`、`reflection`、`chat_agentic`、`explore`；确定性 `metric_query` v1 需配置目录并显式给出指标名 |
| 自动计划选择 | **已实现** —— `auto_plan=true` 依次走 L1 规则、L2 历史与能力过滤后的兜底；默认仍为 `request_query` |
| 多 Agent 编排与子 Agent | **部分实现** —— 限域 REST 自然语言查询已接固定工作流及访问检查；自主编排仍属实验组件 |
| 反馈驱动的规则学习 | **已实现** —— 审批通过的规则进入生成提示与校验，并在 `applied_rules` 回显；规则不自动改写 SQL |
| 多租户隔离 | **部分实现** —— 单 worker 和单机不可变多 worker 支持租户工作台、REST 和 MCP HTTP。双 worker 已在 MySQL 上验收数据源隔离、会话恢复和漂移拒绝。[部署与验收](docs/optimization/immutable-workers.md#v2-租户发布包) |
| 合成与校准基线 | **已记录合成基线**，覆盖检索、SQL 契约和有界负载。业务准确率、召回率和生产容量仍待校准。[基线报告](docs/optimization/synthetic-quality-baseline-2026-11-18.md) |

## 快速开始

**环境要求：** Python 3.12+、Node.js 22+（仅构建前端），以及一个 MySQL 8.0+ 账号库。没有 MySQL 时
服务仍能启动，但登录与注册会被禁用。

```bash
git clone https://github.com/Qorq-byte/Data_engineer.git
cd Data_engineer

python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts && npm run build:frontend

cp .env.example .env                      # Windows: copy .env.example .env
# 设置 JWT_SECRET（32 位以上）、REGISTRATION_TOKEN、AUTH_MYSQL_* 与 ADMIN_USER_IDS
python main.py                            # http://localhost:8001 · API 文档在 /docs
```

1. 打开 <http://localhost:8001>，用 `REGISTRATION_TOKEN` 里的邀请码注册。
2. 在工作台连接目标数据库（或调用 `POST /api/v1/databases/connect`）。
3. 提问。工作台按节点流式显示进度，SQL、校验报告与结果表并排展示。

没有模型 Key？路由会退化为 mock 模式：响应结构完整，SQL 是占位符，警告会写明这一点。没有本机
Ollama？在 `.env` 里设 `RAG_EMBEDDING_PROVIDER=mock`，改用确定性嵌入而不是语义嵌入。

## 安装与配置

### 安装

```bash
pip install -e ".[dev]" -c constraints.txt   # 应用 + 测试与 lint 工具
pip install -e ".[snowflake]"               # 可选 Snowflake 驱动（不在 constraints.txt 快照内）
```

运行时不需要 Node.js。Node 22+ 只用于 `npm run build:frontend`，把 `frontend_design/` 编译到
`frontend_dist/`，再由 FastAPI 提供静态文件。

### 环境变量

把 `.env.example` 复制为 `.env`。下表是最常需要调整的变量；其余开关在示例文件里都有注释。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8001` | 监听地址与端口 |
| `DATABASE_URL` | `sqlite:///./data/app.db` | 本地应用存储（不是账号库） |
| `AUTH_MYSQL_HOST`、`AUTH_MYSQL_PORT`、`AUTH_MYSQL_USER`、`AUTH_MYSQL_PASSWORD`、`AUTH_MYSQL_DATABASE` | `127.0.0.1`、`3306`、`root`、空、`nl2sql_auth` | MySQL 账号库 |
| `JWT_SECRET` | 无 —— **必填** | 签名密钥；少于 32 位或保留默认值会中止启动 |
| `REGISTRATION_TOKEN` | 空 | `POST /api/v1/auth/register` 需要的邀请码；留空即关闭注册 |
| `ADMIN_USER_IDS` | `[1]` | 操作员白名单；仅注册不会自动获得权限 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` | 空 | 启动时读取的提供方凭据 |
| `LLM_MOCK_MODE` | `false` | 强制 mock 路由，不调用真实提供方 |
| `LLM_MAX_CONCURRENCY` / `LLM_TIMEOUT_SECONDS` / `LLM_RATE_LIMIT_RPM` | `4` / `30` / `60` | 单进程 LLM 限制 |
| `QUERY_TIMEOUT_SECONDS` | `120` | 单次查询的 HTTP/SSE 截止时间 |
| `MAX_CONCURRENT_QUERIES` | `8` | 准入槽位；超出返回 `503` |
| `MAX_RESULT_ROWS` / `STATEMENT_TIMEOUT_MS` | `1000` / `30000` | 行数上限与单语句超时 |
| `RAG_EMBEDDING_PROVIDER` | `ollama` | `ollama`、`openai`、`bge` 或 `mock` |
| `OLLAMA_BASE_URL` / `OLLAMA_EMBEDDING_MODEL` / `OLLAMA_EMBEDDING_DIM` | `http://127.0.0.1:11434` / `embeddinggemma` / `768` | 本地嵌入服务 |
| `LLM_AUDIT_PATH` | 空 | 可选用量审计文件；留空即不落盘。默认不存 SQL |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` | `0.0.0.0` / `8080` | MCP 服务监听 |

### 声明式配置

| 文件 | 作用 |
|---|---|
| `app/config/agent.yml` | 提供方默认值、节点→工具→权限映射、硬约束 |
| `app/workflow/plans/request_query.yml` | 可执行计划：`node_order`、`failure_policy`、评估指标 |
| `app/workflow/plans/*.yml` | 另外 6 个计划；默认 5 个可执行，服务端指标目录可启用 `metric_query` v1 |
| `app/config/domains/*.yml` | 按领域的术语表、业务规则与术语映射 |
| `app/config/subagents/*.yaml` | 限定范围的子 Agent 定义与交付目标 |

`ConfigLoader` 与 `PlanLoader` 按文件 mtime 热更新，改完对下一次请求生效，无需重启。节点顺序仍在
计划加载时固定；单个请求不能插入或跳过节点。

模型 Key 有两个来源：启动时的 `.env`，或 `PUT /api/v1/settings/llm`。后者先用 AES-GCM 加密再写入
MySQL，立即生效并跨重启保留；`.env` 里的 Key 不会被回写。

## 使用示例

### 注册与登录

只有设置了 `REGISTRATION_TOKEN` 才开放注册。邀请码放在请求头，不在请求体里。

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <来自 .env 的值>" \
  -d '{"username":"admin","email":"admin@example.com","password":"<强密码>"}'

curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<强密码>"}'
# → {"access_token":"…","token_type":"bearer","user":{…}}
```

### 连接目标库

```bash
curl -X POST http://localhost:8001/api/v1/databases/connect \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"db_type":"sqlite","config":{"path":"data/app.db"},"alias":"demo"}'
# → {"status":"connected","db_id":"sqlite_demo_20261101143012", …}
```

查询目标请使用数据库原生的只读账号。返回的 `db_id` 就是查询接口需要的目标标识。

### 生成与校验

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"上个月收入最高的 10 个商品","domain":"ecommerce","dialect":"postgresql","database":"<db_id>","num_candidates":1,"execute":false}'
```

```jsonc
// 已删减：嵌套载荷只保留承载决策的字段。
{
  "query_id": "q_4f1c2a9b7e01",
  "status": "completed",
  "plan_id": "request_query",
  "primary_sql": "SELECT p.name, SUM(o.amount) AS revenue FROM orders o JOIN products p ON p.id = o.product_id WHERE o.created_at >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') GROUP BY p.name ORDER BY revenue DESC LIMIT 10",
  "linked_tables": ["orders", "products"],
  "validation": {
    "passed": true, "syntax_ok": true, "schema_valid": true, "type_valid": true, "score": 0.95
  },
  "execution": null,
  "confidence": 0.0,
  "errors": [],
  "warnings": []
}
```

把 `"execute"` 设为 `true`，校验通过后就会执行。执行对象包含 `columns`、`rows`、`row_count` 与
`truncated`；行数受 `MAX_RESULT_ROWS` 限制。

### 选择工作流计划

`request_query` 是默认计划。另外五个计划走同一个接口，并共用准入、只读检查、行数上限、语句超时与
LLM 预算：

| `plan_id` | 流水线 | 适用场景 |
|---|---|---|
| `gensql_agentic` | 链接 → 生成 → 校验 → 执行 | 不需要 NL 解析的标准流水线 |
| `ez_query` | 链接 → 生成 → 执行 | 简单读取，校验步骤可省 |
| `reflection` | 生成 → 执行 → 反射 → 修正 → 执行 | 预期会执行失败，需要一次自动修正 |
| `chat_agentic` | 解析 → 检索 → 生成 → 校验 → 回复 | 需要自然语言答复，而不只是 SQL |
| `explore` | 解析 → 检索 → 格式化 | 只想看表与文档，不生成 SQL |

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"列出订单","database":"<db_id>","execute":true,"plan_id":"ez_query","num_candidates":1}'
```

`reflection` 必须同时给出 `execute: true` 与 `database`，否则在创建会话之前就会失败。
`metric_query` v1 使用固定的解析 → 校验 → 执行计划，不调用模型。
需配置服务端 `METRIC_CATALOG_PATH`，显式给出指标名、版本、域、数据库和 `num_candidates: 1`。
未配置有效目录时返回 `400 INVALID_REQUEST`。共享 worker 必须在活跃发布包内显式封存目录，
见[共享目录配置与验收](docs/optimization/shared-metric-catalog.md)。
详见 [配置与验收](docs/optimization/metric-query-v1.md)。

管理员可通过 `GET /api/v1/metrics?domain=...&database=...` 发现名称和版本。
MCP 也提供 `list_metrics`、`metric_query`；HTTP Bearer 认证与本地进程显式信任方式见
[访问与使用](docs/optimization/metric-discovery-mcp.md)。
独立 stdio 与回环 HTTP 传输验收见[指标 MCP 传输验收](docs/optimization/metric-mcp-transports.md)。

子 Agent REST 请求复用固定五节点工作流，需绑定明确的 `db_id`、允许 operator 的用户 ID，执行默认关闭。
详见 [REST 边界](docs/optimization/subagent-rest-boundary.md)。

把 `"auto_plan"` 设为 `true`，则由能力过滤后的选择器在 `request_query` 与 `ez_query` 之间选择。
响应会带 `plan_selection`：`layer`（`L1` 规则、`L2` 历史、`default` 或 `filtered`）、`reason`、
`confidence` 与备选计划。选择器不会选 `metric_query`、`reflection`、`chat_agentic` 或 `explore`；
若它的判决落在集合之外，会回退到 `request_query` 并在原因里说明。不需要时保持关闭，请求仍然完全确定。

审批通过的学习规则会合并进生成提示，并在校验时产生非阻断的规则警告；响应用 `applied_rules`
列出本次实际应用的规则。规则不会改写 SQL。

### SSE 流式调用

```bash
curl -N -X POST http://localhost:8001/api/v1/query/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"按月统计收入","database":"<db_id>","execute":false}'
```

每个阶段发一个事件（`parse`、`schema`、`sql`、`validation`、`execution`），以 `done` 或 `error`
结束。客户端取消会关闭流。

### Python 客户端

```python
import httpx

response = httpx.post(
    "http://localhost:8001/api/v1/query",
    headers={"Authorization": f"Bearer {token}"},
    json={"nl_text": "按月统计收入", "database": db_id, "execute": False},
    timeout=120,
)
payload = response.json()
print(payload["primary_sql"], payload["validation"]["passed"])
```

### 直接执行 SQL 与其他接口

| 接口 | 方法 | 用途 |
|---|---|---|
| `/api/v1/execute` | POST | 绕过 NL 流水线直接执行 SQL；仍然只读 |
| `/api/v1/query/generate` | POST | 只生成；不链接结构、不校验、不执行 |
| `/api/v1/schema/{db_id}` | GET | 结构快照；`/refresh` 重新内省 |
| `/api/v1/settings/llm` | GET/PUT | 提供方与默认模型；Key 永不回显 |
| `/health`、`/health/ready` | GET | 存活与就绪（初始化、账号库、非 mock 模型、至少一个已连库） |

### 响应语义

- **`status`** 为 `completed` 或 `failed`。业务失败仍返回 HTTP 200，结论由状态与 `errors[]` 承载。
- **基础设施失败**返回 4xx/5xx，并带机器可读的 `detail.code`（`QUERY_TIMEOUT`、`QUERY_CAPACITY`、
  `SESSION_CONFLICT`、`SHARED_STATE_UNAVAILABLE`、`INTERNAL_ERROR`），能重试时附 `Retry-After`。
- **`confidence` 恒为 `0`。** 请读 `evidence`：结构来源、假设、澄清请求与逐项检查状态。
- **`execution: null` 表示什么都没跑。** 可能是 `execute=false`、没有连接目标，或检查被阻断。警告与
  节点轨迹会说明是哪一种。
- **`validation.score` 是诊断值。** 阻断性失败时为 `0`；其余情况反映诊断数量，不代表正确性。

请求字段与完整错误契约见 [docs/usage.md](docs/usage.md) 和
[docs/optimization/query-contract.md](docs/optimization/query-contract.md)。

## 运行流程

顶部示意图由 `scripts/render_runtime_flow.py` 生成，描述的是实际发布的链路，不是录制的运行轨迹。
图分四步：

- **启动** —— `main.py` → `create_app()` → lifespan 探测 → `:8001`。探测失败只降级命名组件；
  关闭按固定顺序等待释放。
- **进入与准入** —— 四种入口、操作员校验、请求校验、准入槽位、会话绑定与 120 秒截止时间。
- **计划与执行** —— `request_query.yml` 固定 `node_order`，`WorkflowRunner` 依次走五个节点。
  节点只写入 `WorkflowContext`。
- **响应** —— JSON 与 SSE 返回 `QueryResponse`，MCP 做投影；轨迹在回复前落盘。

护栏与失败分支收敛成图底两行脚注，不再单独占一段：

```text
入   Web 工作台 · JSON · SSE · MCP  →  认证 · 校验 · 绑定会话与目标库
计划 PlanLoader 读取 request_query.yml  →  WorkflowRunner 按 node_order 推进
执行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql
出   执行：只读结果  ·  跳过/阻断：明确状态  →  QueryResponse
闸门 PermissionManager · ConstraintEnforcer · SQL 策略 · LLM 预算（每步生效）
```

绘图说明、图中声明与重新生成命令见 [docs/runtime-flow.md](docs/runtime-flow.md)。

## 文档

- [使用说明](docs/usage.md) —— 账号、目标库、请求字段、响应证据、其他接口。
- [架构](docs/architecture.md) —— Harness 不变量、分层、目录结构、设计取舍。
- [支持矩阵](docs/support-matrix.md) —— 特性状态、数据库、模型。
- [缺口收口计划](docs/optimization/gap-closure-plan-2026-11-16.md) —— 哪些已接线、哪些仍未做，以及多租户与基线草案。
- [完成路径与验收](docs/optimization/completion-path-2026-11-17.md) —— 阶段、当前证据和外部阻塞。
- [运行流程](docs/runtime-flow.md) —— 图里声称了什么，以及如何重新生成。
- [查询契约](docs/optimization/query-contract.md) —— 状态码与错误语义。
- [节点开发](docs/node-development.md) —— 节点生命周期与骨架代码。
- [部署与恢复](docs/optimization/immutable-workers.md) —— 固定配置多 worker 的约束。

## 运行与测试

### 运行

```bash
python main.py                 # APP_PORT 可覆盖 8001
curl http://localhost:8001/health
curl http://localhost:8001/health/ready
```

初始化与关闭由 FastAPI lifespan 统一负责：节点、MCP 与 Agent 目录注册，存储、账号库、RAG 与持久化的
LLM 路由在启动时初始化；关闭时依次释放数据库连接、子 Agent、RAG 引擎与路由。

### 测试

```bash
pytest                                  # 全量（asyncio_mode=auto）
pytest -m "not real_database"           # CI 默认；不依赖外部服务
pytest tests/test_nodes/                # 单个目录
pytest -k "test_execute"                # 按关键字筛选
DE_REAL_DB_TESTS=1 pytest tests/real_db # 一次性 PostgreSQL/MySQL 实例
```

测试使用真实 SQLite、DuckDB、LanceDB 与 FTS5，不做 mock。共享 fixture 从
`tests/fixtures/test_schema.sql` 构建电商结构（`users`、`products`、`orders`）。

### 质量门禁

```bash
ruff check .                            # 行宽 100、py312、E/F/I/N/W/UP/B/C4/SIM
mypy app/                               # strict；CI 核心之外仍有已知债务
npm run check:frontend                  # 页面编译与函数契约
npm run check:frontend:build            # 可重复构建；失败时保留旧产物
python -m scripts.check_dependencies    # 依赖与环境一致性
python scripts/check_readmes.py         # 章节对齐、本地链接、流程图动画
```

CI 核心（`checks.yml`）运行同一组门禁，外加 `pytest -m 'not real_database'`。
`real-databases.yml` 启动一次性 PostgreSQL 16 与 MySQL 8.4 服务并运行 `tests/real_db`。

### 评估与压测

这些是离线工具，不是正确性基准：

```bash
python scripts/evaluate_sql_contracts.py     # 契约级 SQL 检查
python scripts/evaluate_quality.py           # 合成质量报告
python scripts/run_nl2sql_acceptance.py      # 冻结集批处理验收
python scripts/run_real_db_tests.py          # 驱动级验收
python scripts/load_test.py                  # 有界并发探测
python -m scripts.run_synthetic_baseline --output data/synthetic-baseline-20261118/aggregate.json --requests 20 --rows 1000
```

汇总入口会在一份报告中记录所有 fixture 哈希和分母。合成套件评的是契约匹配，不是业务正确性，
结果不构成生产容量或准确率基线。见[2026-11-18 范围与结果](docs/optimization/synthetic-quality-baseline-2026-11-18.md)。

## 目录结构

```text
app/api/             create_app() 工厂、13 个路由模块、操作员依赖、静态前端
app/workflow/        请求服务、计划运行时与选择、QueryResponse 契约、7 个 YAML 计划
app/harness/         PlanLoader · WorkflowRunner · 权限 · 约束 · 会话
app/nodes/           12 个已注册节点；request_query 的五个是主链路
app/core/            NL 解析、提示构建、SQL 生成与校验
app/db/              连接、结构内省、方言适配、固定的查询目标
app/rag/             LanceDB 向量、BM25/FTS5、RRF 融合、索引维护
app/knowledge/       Schema 图、术语表、业务规则、检索原语
app/learning/        反馈收集、编辑模式分析、已审批规则存储
app/llm/             LiteLLM 路由、预算、完成策略、用量、Key 加密
app/auth/            JWT 签发、MySQL 用户库、管理员校验
app/mcp/             四个 FastMCP 服务与外部工具客户端
app/evaluation/      冻结验收集、契约套件、预测导出
assets/              运行流程图（动态与静态）
docs/                使用、架构、支持矩阵、逐阶段工程记录
frontend_design/     源页面，由 npm 编译到 frontend_dist/
scripts/             构建、评估、验收与绘图入口
tests/               单元、集成、e2e 与 real_db 套件
```

## 常见问题

**必须有付费模型 Key 吗？**
不需要。没有 Key 时路由使用 mock 模式：流水线能端到端跑通，SQL 是占位符，`warnings` 会写
`MOCK LLM`。适合验证 Harness，不适合作为业务答案。

**必须装 MySQL 吗？**
账号功能需要。账号库与查询目标是两套东西。`init_auth_db()` 失败时服务仍会启动，但登录与注册被禁用。

**为什么 `confidence` 恒为 `0`？**
因为没有测过经过校准的概率，报一个数就是编造精度。请看每个候选的 `evidence`。

**`status` 是 `completed`，但 `execution` 是 `null`，是 bug 吗？**
不是。`execute=false`、没有 `db_id`，或执行节点被跳过都会这样。节点结果是 `skipped`，执行对象保持
`null`，警告会说明缺的是哪一环。

**哪些数据库真的验证过？**
SQLite、DuckDB、PostgreSQL、MySQL、ClickHouse、Trino、StarRocks 有只读验收。Snowflake 有实现但
待独立验收；BigQuery、Redshift、Databricks 只有方言定义。

**一套部署能给多个团队共用吗？**
单 worker 和单机不可变多 worker 部署可按租户开放查询、领域和模型配置，由部署端预配数据源。不可变模式通过停服维护更新配置。系统管理由默认租户管理员操作。[配置说明](docs/usage.md#tenant-operator-setup)

**为什么启动时报 JWT 错误？**
`JWT_SECRET` 缺失、仍是 `change-me-in-production`，或不足 32 位。这是 `create_app()` 里刻意的
fail-closed 检查。

**怎么新增节点或计划？**
读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [docs/node-development.md](docs/node-development.md)。
节点实现 `setup_input → execute → update_context`；计划是带固定 `node_order` 的 YAML。

**Windows 上要注意什么？**
用 `.venv\Scripts\activate` 激活，用 `copy .env.example .env` 复制，curl 示例建议在 PowerShell 或
Git Bash 里跑。Windows 会自动安装 `tzdata`。

## 贡献

欢迎贡献，尤其是能钉住失败语义的测试。先读 [CONTRIBUTING.md](CONTRIBUTING.md)：环境、分支模型、
提交约定，以及新增节点 / 计划的骨架代码。参与即表示同意[行为准则](CODE_OF_CONDUCT.md)。

改查询链路时，请在同一次提交里跑完上面的门禁，并同步计划、流程图文案和四份语言的 README。

## 安全

请按 [SECURITY.md](SECURITY.md) 私下报告漏洞，不要在公开 Issue、PR 或讨论区里贴。报告里不要放
真实凭据、连接串或客户数据，请先脱敏。

与安全相关的默认设置：

- 只读 SQL 由单语句 AST 策略与 `execute_sql` 内部的独立写入检查共同强制；SQLite 与 ClickHouse 另有
  驱动侧守卫，部署时应使用数据库原生的只读账号。
- `JWT_SECRET` 不足 32 位会中止启动。
- 通过工作台设置的提供方 Key 先经 AES-GCM 加密再落库，任何接口都不会回显。
- 可选用量审计默认不存问题文本，也不存 SQL。
- 未列出的工具由 `PermissionManager` 默认拒绝；行数、重试、超时与 LLM 调用上限不受模型控制。

支持版本、范围与披露流程见 [SECURITY.md](SECURITY.md)。

## 许可证

[MIT](LICENSE) © 2026 Majiaqi。
