# NL2SQL Data Engineering Agent — 技术实现方案

> **版本:** v3.4  
> **日期:** 2026-07-17  
> **状态:** Phase 1 ✅ 完成 | Phase 2 ✅ 完成 | Phase 3 ✅ 完成 | Phase 4 ✅ 完成 | Phase 5 ✅ 完成 | 审计修复 ✅ 完成  
> **总体:** 86/86 任务完成 + 14 处审计修复, 2904 测试通过, MVP 冻结 (M5 里程碑)
> **对应规范:** [SPEC.md](../SPEC.md) (v0.1, 3710 行)

---

## 目录

1. [项目总览](#1-项目总览)
2. [技术栈](#2-技术栈)
3. [系统架构](#3-系统架构)
4. [模块详细设计](#4-模块详细设计)
   - 4.1 [数据模型层](#41-数据模型层-appmodels)
   - 4.2 [配置层](#42-配置层-appconfig)
   - 4.3 [数据库连接层](#43-数据库连接层-appdb)
   - 4.4 [Node 系统](#44-node-系统-appnodes)
   - 4.5 [Harness 运行时](#45-harness-运行时-appharness)
   - 4.6 [MCP 协议层](#46-mcp-协议层-appmcp)
   - 4.7 [核心引擎](#47-核心引擎-appcore)
   - 4.8 [RAG 与领域知识引擎](#48-rag-与领域知识引擎-appknowledge)
   - 4.9 [工作流编排](#49-工作流编排-appworkflow)
   - 4.10 [LiteLLM 路由层](#410-litellm-路由层-appllm)
   - 4.11 [多 Agent 系统](#411-多-agent-系统-appagents)
   - 4.12 [Subagent 封装系统](#412-subagent-封装系统-appsubagent)
   - 4.13 [持续学习系统](#413-持续学习系统-applearning)
   - 4.14 [API 层](#414-api-层-appapi)
   - 4.15 [Web UI](#415-web-ui-appweb)
5. [数据流](#5-数据流)
6. [Prompt 策略体系](#6-prompt-策略体系)
7. [数据存储设计](#7-数据存储设计)
8. [实施路线图](#8-实施路线图)
9. [测试策略](#9-测试策略)
10. [安全与隐私](#10-安全与隐私)
11. [评估体系](#11-评估体系)
12. [风险分析与缓解](#12-风险分析与缓解)
13. [项目结构总览](#13-项目结构总览)
14. [附录](#14-附录)
15. [部署与运维](#15-部署与运维-deployment--operations)
16. [可观测性](#16-可观测性-observability)
17. [CI/CD 与开发生命周期](#17-cicd-与开发生命周期-cicd--dev-lifecycle)
18. [认证与授权](#18-认证与授权-authentication--authorization)
19. [多租户与资源隔离](#19-多租户与资源隔离-multi-tenancy)
20. [高可用与灾备](#20-高可用与灾备-high-availability--disaster-recovery)

---

## 1. 项目总览

### 1.1 产品愿景

构建一个**数据工程智能体**，通过领域感知推理、RAG 混合语义检索、Evolvable Context 活知识库和持续学习，将自然语言（中/英双语）准确地转换为 SQL 语句并安全执行。

### 1.2 核心路径

```
NL 输入 → 意图理解 → RAG 语义检索 → SQL 生成 (LiteLLM 多 LLM) → 验证 → 执行 (多方言)
    │                                                                    │
    └── Evolvable Context 活知识库 (Schema 元数据 + 参考 SQL + 语义模型 + 业务指标)
```

### 1.3 关键特性 (MVP)

| 特性 | 说明 |
|------|------|
| 🌐 中英双语输入 | 自动检测语言，NL 和术语表支持双语 |
| 🗃️ 多领域支持 | 手动选择 + 自动检测的领域切换 |
| 🔍 Schema 感知 | 自动读取表结构、注释、外键、类型 |
| 📖 业务术语表 | 业务术语 → SQL 字段/表达式的映射 |
| 💬 多轮对话 | 上下文继承，逐步完善的对话式查询 |
| 🔄 版本管理 | 可回溯到任意历史版本 |
| ✏️ SQL 双向编辑 | 用户编辑 SQL 后 NL 和术语同步学习 |
| 🛡️ 只读优先 | 默认只生成 SELECT，写操作需确认 |
| 🔁 多候选输出 | 歧义时展示多个候选 SQL |
| 📊 执行预览 | 生成后自动执行并展示结果预览 |
| 🧠 持续学习 | 反馈打分 + 查询对存储 + 规则提取 |
| 🌱 Evolvable Context | 活知识库，自动捕获并演化 |
| 📐 MetricFlow 语义层 | 声明式业务指标，跨方言 SQL |
| 🧩 多数据库适配 | 11 种内置适配器 |
| 🔌 多 LLM 提供商 | LiteLLM 统一路由 10+ 模型 |
| 🤖 Subagent 封装 | 成熟领域封装为 scoped chatbot |

### 1.4 核心术语

| 术语 | 定义 |
|------|------|
| **NL Query** | 自然语言查询，用户输入的中/英文问题 |
| **Domain** | 领域（如电商、金融），携带独立的术语表、规则和 Schema 映射 |
| **SQR** | Structured Query Representation，NL 解析后的结构化查询表示 |
| **SQL Candidate** | 一次生成的单个 SQL 候选，含 SQL 文本、置信度、推理 |
| **Evolvable Context** | 活的知识库，持续捕获 Schema、参考 SQL、语义模型、指标并自动演化 |
| **Metric** | 由 MetricFlow 定义的业务指标（名称、描述、计算逻辑、维度、时间粒度） |
| **Subagent** | 将成熟领域封装为 scoped chatbot，拥有独立上下文和工具配置 |
| **WorkflowContext** | Workflow 级别共享上下文，同一 Workflow 中所有 Node 共享 |
| **Dialect Adapter** | 数据库方言适配器，屏蔽 11 种数据库 SQL 差异 |

---

## 2. 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| 语言 | **Python 3.12+** | type 联合语法、`@dataclass`、`Self` 类型 |
| 包管理 | **uv** | 替代 pip，速度快 10-100x |
| Web 框架 | **FastAPI** + **uvicorn** | 异步 REST API，自动 OpenAPI 文档 |
| LLM 路由 | **LiteLLM** | 统一 10+ 提供商（OpenAI/Claude/Gemini/DeepSeek/Qwen 等） |
| MCP 框架 | **FastMCP** | 声明式 MCP Server/Client 构建 |
| AI Agent SDK | **OpenAI Agents SDK** | 多 Agent 编排、handoff、护栏机制 |
| 向量数据库 | **LanceDB** | 列式向量存储，3 命名空间隔离，零配置嵌入式 |
| 全文检索 | **SQLite FTS5** / **Tantivy** | BM25 稀疏索引 (RAG 混合检索) |
| 语义层 | **MetricFlow** (dbt Labs) | 声明式业务指标，跨方言 SQL 生成 |
| 数据库驱动 | **sqlite3** / **duckdb** / **asyncpg** / **aiomysql** / ... | 11 种数据库原生驱动 |
| SQL 工具 | **sqlglot** + **sqlparse** | 方言转换、语法解析、格式化 |
| Embedding | **text-embedding-3-small** / **BGE-large-zh** | 语义搜索向量化 |
| NLP | **jieba** | 中文分词 |
| 配置 | **PyYAML** + **pydantic-settings** | 类型安全的配置加载 |
| 前端 (MVP) | **Streamlit** | 快速构建交互式 Web UI |
| 流式通信 | **SSE** (sse-starlette) | 实时 SQL 生成过程展示 |
| 开发工具 | **pytest** + **ruff** + **mypy** | 测试、lint、类型检查 |

---

## 3. 系统架构

### 3.1 完整分层架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       交付层 (Delivery Layer)                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │  Web UI      │  │  REST API    │  │  MCP Server      │  │  Subagent    │ │
│  │  (Streamlit) │  │  (FastAPI)   │  │  (对外暴露NL2SQL) │  │  Chatbot     │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘  └──────────────┘ │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼─────────────────────────────────────┐
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║  Harness 控制平面: WorkflowRunner + Plan 模板 + agent.yml 配置驱动    ║  │
│  ║  LLM 不决定下一步 — Harness 决定。按 node_order 推进，可预测可审计    ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼─────────────────────────────────────┐
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║                    MCP 协议层 (FastMCP)                                ║  │
│  ║  ┌──────────────────────┐  ┌───────────────────────┐                  ║  │
│  ║  │ MCP Client           │  │ MCP Server Host       │                  ║  │
│  ║  │ (消费外部服务)        │  │ (对外暴露 NL2SQL 能力) │                  ║  │
│  ║  └──────────────────────┘  └───────────────────────┘                  ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼─────────────────────────────────────┐
│                        核心引擎层 (Multi-Agent)                               │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                     Orchestrator Agent (调度器)                         │ │
│  │  任务分解 → Agent 选择 → 任务分发 → 中间结果汇总 → 冲突裁决             │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│         │              │              │              │                       │
│  ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐              │
│  │ NL 理解     │ │ Schema 检索 │ │ SQL 生成   │ │ 验证 Agent │              │
│  │ Agent       │ │ Agent      │ │ Agent      │ │            │              │
│  └─────────────┘ └───────────┘ └────────────┘ └────────────┘              │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  领域知识引擎: RAG (BM25+LanceDB+RRF) + MetricFlow + Evolvable Context│   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  LiteLLM 多 LLM 路由: OpenAI │ Claude │ Gemini │ DeepSeek │ ... 10+  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼─────────────────────────────────────┐
│                     数据源连接层 (11 种数据库 + Dialect Adapter)              │
│                                                                              │
│  SQLite │ DuckDB │ PostgreSQL │ MySQL │ Snowflake │ StarRocks │ BigQuery    │
│  Redshift │ ClickHouse │ Databricks │ Trino                                   │
│                                                                              │
│  Dialect Adapter: sqlglot 方言转换 / 11 套类型映射 / 函数映射                │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Harness 四层架构

```
Layer 1: Workflow 编排层  →  workflow.yml Plan 模板 → DAG 拓扑序
Layer 2: WorkflowRunner    →  按 node_order 推进 → evaluate_result → 失败处理
Layer 3: Node 执行层       →  BaseNode → AgenticNode (7 项能力) → 12 内置 Node
Layer 4: 配置驱动层        →  agent.yml 声明式配置，LLM 无法突破约束
```

### 3.3 核心设计原则

1. **Pipeline 式处理** — NL → 理解 → 检索 → 生成 → 验证，每阶段可独立改进
2. **流式优先** — LLM 输出通过 SSE 流式传输到前端
3. **多层缓存** — 查询对缓存 → Schema 缓存 → Embedding 缓存 → LLM 结果缓存
4. **松耦合领域** — 每个领域独立管理术语表、规则、语义模型和 Schema 映射
5. **可观测性** — 每次请求记录完整 trace（输入、候选、用户操作、反馈）
6. **MCP 标准化** — 通过 MCP 协议统一数据源连接和工具暴露
7. **选择工作流** — 按查询复杂度、领域和用户角色路由到不同处理管道
8. **RAG 混合检索** — BM25 稀疏 + 向量密集 + RRF 融合，兼顾精确匹配和语义泛化
9. **Evolvable Context** — 知识库持续自动演化，Schema/反馈/指标变更触发生命周期更新
10. **LLM 中性** — 通过 LiteLLM 统一 10+ 模型，可替换、可路由、可降级
11. **方言适配** — 通过 Dialect Adapter 层屏蔽 11 种数据库差异
12. **Subagent 优先** — 成熟领域封装为独立 Subagent，通过多种渠道交付
13. **Harness 控制平面** — LLM 不决定下一步，执行路径可预测、可审计、可复现
14. **多 Agent 协作** — 核心 Pipeline 各阶段封装为独立 Agent，由 Orchestrator 统一调度

### 3.4 组件依赖关系

```
models/  (零外部依赖 — 所有模块的基础契约)
    │
    ├──► config/     (依赖: pydantic-settings, PyYAML)
    ├──► db/         (依赖: models/schema.py)
    │       │
    │       ▼
    ├──► harness/    (依赖: models/workflow.py, config/)
    │       │
    │       ▼
    ├──► nodes/      (依赖: models/, harness/, db/)
    │       │
    │       ▼
    ├──► core/       (依赖: models/, nodes/, llm/, knowledge/)
    ├──► knowledge/  (依赖: models/, db/, LanceDB, MetricFlow)
    ├──► llm/        (依赖: LiteLLM, models/query.py)
    ├──► agents/     (依赖: core/, knowledge/, llm/, OpenAI Agents SDK)
    ├──► subagent/   (依赖: agents/, knowledge/, workflow/)
    ├──► mcp/        (依赖: FastMCP, db/, knowledge/)
    ├──► workflow/   (依赖: models/workflow.py, nodes/)
    ├──► api/        (依赖: core/, mcp/, workflow/)
    ├──► learning/   (依赖: models/, knowledge/)
    └──► web/        (依赖: api/, Streamlit)
```

---

## 4. 模块详细设计

### 4.1 数据模型层 (`app/models/`)

> **SPEC 参考:** §4.1.3, §4.2, §4.3.2, §4.5-4.7, §4.10-4.12

**设计策略:** 内部传递使用 `@dataclass`（轻量、高性能），API 边界使用 Pydantic `BaseModel`（自动校验、序列化）。所有模型零外部依赖，作为所有模块的公共契约。

**Phase 1 状态:** ✅ 全部 11 个模型文件已实现。

| 文件 | 核心类 | 用途 | Phase 1 |
|------|--------|------|---------|
| `schema.py` | `ColumnSchema`, `TableSchema`, `SchemaSnapshot`, `IndexInfo`, `ForeignKey` | 数据库 Schema 元数据模型，含 `format_for_llm()` 方法 | ✅ |
| `query.py` | `SQR`, `Entity`, `TimeRange`, `Condition`, `OrderSpec`, `Ambiguity`, `SQLCandidate`, `ValidationReport`, `QueryResult`, `PlanAnalysis`, `IntentType` | NL 解析结果、SQL 候选、验证报告、执行结果 | ✅ |
| `domain.py` | `DomainConfig`, `GlossaryTerm`, `BusinessRule`, `TermMapping`, `DomainMatch` | 领域配置、术语映射、业务规则 | ✅ |
| `workflow.py` | `WorkflowPlan`, `NodeDef`, `WorkflowResult`, `Evaluation`, `WorkflowTrace`, `NodeTrace`, `NodeConfig`, `FailurePolicy` | 工作流模板和运行时追踪 | ✅ |
| `subagent.py` | `SubagentConfig`, `SubagentContext`, `LLMConfig`, `AccessControl`, `DeliveryConfig` | Subagent 定义和交付配置 | ✅ |
| `mcp.py` | `MCPServerConfig`, `MCPToolDef`, `MCPResourceDef` | MCP Server 配置定义 | ✅ |
| `feedback.py` | `FeedbackData`, `DiffOp` | 用户反馈和编辑操作 | ✅ |
| `conversation.py` | `ConversationTurn`, `ConversationSession` | 多轮对话和版本管理 | ✅ |
| `rag_schema.py` | `SchemaDocument` | SchemaMetadataRAG 的索引文档 | ✅ |
| `rag_metric.py` | `MetricDocument` | MetricRAG 的指标文档 | ✅ |
| `rag_document.py` | `Document` | DocumentStore 的文档索引 | ✅ |

**关键数据流:** `SQR` → `SQLCandidate` → `ValidationReport` → `QueryResult` → `FeedbackData`

---

### 4.2 配置层 (`app/config/`)

> **SPEC 参考:** §3.4.5

**Phase 1 状态:** ✅ 完整实现。

#### 4.2.1 Settings (`settings.py`)

使用 `pydantic-settings` 从 `.env` 加载，所有配置项有类型校验和默认值：

```python
class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
    
    # App
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    
    # Database
    database_url: str = "sqlite:///./data/app.db"
    
    # LLM API Keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    
    # MCP
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8080
    
    # Limits
    max_result_rows: int = 1000
    statement_timeout_ms: int = 30000
    read_only: bool = True
```

#### 4.2.2 agent.yml 完整结构

运行时行为完全由 `agent.yml` 声明式控制，Harness 强制执行。LLM 无法突破这些约束。

```yaml
agent:
  name: "NL2SQL Agent"
  version: "1.0.0"

  # LLM 提供商配置
  provider:
    default: claude-sonnet-4
    fallback: [gpt-4.1, deepseek-v3]

  # 8 个 Node 的模型/工具/权限/技能映射
  nodes:
    schema_linking:
      model: claude-haiku-4.5
      tools: [hybrid_search, lookup_term]
      permissions: {hybrid_search: allow, lookup_term: allow}
      skills: [schema_explorer]

    generate_sql:
      model: claude-sonnet-4
      tools: [build_prompt, call_llm]
      permissions: {call_llm: ask}
      streaming: true

    execute_sql:
      model: none  # 非 LLM 节点
      tools: [execute_read_query]
      permissions: {execute_read_query: allow, execute_write_query: deny}

    # ... parse_nl, validate_sql, reflection, hybrid_search, metric_resolve

  # 技能系统
  skills:
    mode: auto
    directory: "./skills"
    preload: [schema_explorer, sql_reviewer]

  # 工作流绑定
  workflow:
    default: gensql_agentic
    auto_select: true

  # Memory 策略 — 内置 subagent 默认 memory:false; chat 默认 true
  memory:
    builtin_subagents:
      gen_sql: false
      schema_linking: false
      execute_sql: false
    chat: true
    storage:
      subagent: ":memory:"
      top_level: "~/.data_engineer/sessions/"
    isolation: node_name

  # 全局上下文 — AGENTS.md 前 200 行注入所有 Node Prompt
  global_context:
    source: "AGENTS.md"
    inject_lines: 200
    auto_reload: true

  # 全局约束 — Harness 强制，LLM 不可绕过
  constraints:
    max_llm_calls_per_query: 10
    max_node_retries: 3
    reflection_max_rounds: 3
    session_ttl_seconds: 3600
    read_only: true
    max_result_rows: 1000
    statement_timeout_ms: 30000
```

#### 4.2.3 领域 YAML 配置

```
app/config/domains/{domain_name}/
├── domain.yaml      # 领域基础配置 (名称、数据库、关键词、时区)
├── glossary.yaml    # 术语表 (术语→SQL 表达式映射)
└── rules.yaml       # 业务规则 (模式→约束→SQL 模板)
```

**热加载机制:** `ConfigLoader` 记录 mtime，每次 `load()` 检查文件是否变更，变更后自动重载。

---

### 4.3 数据库连接层 (`app/db/`)

> **SPEC 参考:** §4.5.3

**Phase 5 状态:** 11/11 数据库完整实现 ✅（Phase 5 完成 7 种剩余适配器）

#### 4.3.1 数据库类型与适配器分级

| # | 数据库 | 级别 | 驱动 | 状态 |
|---|--------|------|------|------|
| 1 | **SQLite** | L2 内置 | `sqlite3` | ✅ 完整 |
| 2 | **DuckDB** | L2 内置 | `duckdb` | ✅ 完整 |
| 3 | **PostgreSQL** | L1 原生 | `asyncpg` / `psycopg2` | ✅ 完整 |
| 4 | **MySQL** | L1 原生 | `aiomysql` / `pymysql` | ✅ 完整 |
| 5 | **Snowflake** | L3 MetricFlow | `snowflake-connector` | ✅ 完整 |
| 6 | **StarRocks** | L4 适配器 | `mysql-connector` | ✅ 完整 |
| 7 | **BigQuery** | L3 MetricFlow | `google-cloud-bigquery` | ✅ 完整 |
| 8 | **Redshift** | L4 适配器 | `redshift-connector` | ✅ 完整 |
| 9 | **ClickHouse** | L4 适配器 | `clickhouse-connect` | ✅ 完整 |
| 10 | **Databricks** | L3 MetricFlow | `databricks-sql-connector` | ✅ 完整 |
| 11 | **Trino** | L4 适配器 | `trino-python-client` | ✅ 完整 |

**分级说明:**
- **L1 原生:** 完整 Schema 提取 + 执行 + EXPLAIN，方言原生支持
- **L2 内置:** 完整 Schema 提取 + 执行，sqlglot 方言转换，零配置
- **L3 MetricFlow 原生:** MetricFlow 直接生成对应方言 SQL
- **L4 适配器:** 通过 sqlglot 转换 + 原生驱动执行

#### 4.3.2 ConnectionFactory 设计

```python
class DatabaseType(StrEnum):
    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    # ... 11 types total

class ConnectionFactory:
    """统一连接工厂 — 按 db_type 分发到对应驱动"""
    
    _connections: dict[str, Any] = {}   # db_id → connection
    _infos: dict[str, ConnectionInfo] = {}
    
    @staticmethod
    async def create(db_type: DatabaseType, config: dict, db_id: str) -> Any: ...
    @staticmethod
    def close(db_id: str): ...
    @staticmethod
    def list_all() -> list[str]: ...
```

#### 4.3.3 SchemaExtractor 设计

多方言内省策略：

| 数据库 | 策略 | 实现方式 |
|--------|------|---------|
| PostgreSQL | information_schema | 查询 tables/columns/constraints + pg_indexes |
| SQLite | PRAGMA | `table_info`, `foreign_key_list`, `index_list` |
| DuckDB | information_schema | 查询 `information_schema.tables/columns` |
| 其他 | sqlalchemy inspect | generic fallback |

```python
class SchemaExtractor:
    async def extract(self, connection, db_type: DatabaseType) -> SchemaSnapshot: ...
    async def _extract_postgresql(self, conn) -> SchemaSnapshot: ...
    async def _extract_sqlite(self, conn) -> SchemaSnapshot: ...
    async def _extract_duckdb(self, conn) -> SchemaSnapshot: ...
```

#### 4.3.4 DialectAdapter 设计

```python
@dataclass
class DialectConfig:
    dialect: str                    # postgresql | mysql | sqlite | duckdb | ...
    driver: str
    type_mapping: dict[str, str]    # 通用类型 → 方言类型
    function_mapping: dict[str, str]
    quote_char: str                 # " 或 `
    supports: set[str]              # cte, window, json, array, ...

class DialectAdapter:
    def translate(self, sql: str, target_dialect: str) -> str:
        """使用 sqlglot 进行方言转换"""
        return sqlglot.transpile(sql, read=self.config.dialect, write=target_dialect)[0]
    
    def quote_identifier(self, name: str) -> str: ...
    def get_type_name(self, generic_type: str) -> str: ...
    def format_sql(self, sql: str) -> str: ...

# 11 种预置配置
PRESET_DIALECTS: dict[str, DialectConfig] = {
    "postgresql": DialectConfig(dialect="postgresql", driver="asyncpg", ...),
    "mysql": DialectConfig(dialect="mysql", driver="aiomysql", quote_char="`", ...),
    "sqlite": DialectConfig(dialect="sqlite", driver="sqlite3", ...),
    "duckdb": DialectConfig(dialect="duckdb", driver="duckdb", ...),
    # ... 7 more (stubs with empty type/function mappings in Phase 1)
}
```

---

### 4.4 Node 系统 (`app/nodes/`)

> **SPEC 参考:** §4.12

**Phase 4 状态:** 全部 16 个 Node 完整实现 ✅ (含 Phase 5 的 `_template.py` 与审计修复补齐的 `RespondNode`)

#### 4.4.1 继承树

```
BaseNode (execute / setup_input / update_context)
    │
    ├── AgenticNode (7 项 Harness 能力)
    │   ├── SchemaLinkingNode      [内置] ✅ 完整
    │   ├── GenerateSQLNode        [内置] ✅ 完整
    │   ├── ExecuteSQLNode         [内置] ✅ 完整
    │   ├── ValidateSQLNode        [内置] ✅ 完整
    │   ├── ParseNLNode            [内置] ✅ 完整
    │   ├── ReflectionNode         [内置] ✅ 完整
    │   ├── HybridSearchNode       [内置] ✅ 完整
    │   ├── MetricResolveNode      [内置] ✅ 完整
    │   ├── ExplainPlanNode        [内置] ✅ 完整
    │   └── RespondNode            [内置] ✅ 完整
    │
    └── PlainNode (非 LLM 节点)
        ├── DialectTranslateNode   [内置] ✅ 完整
        ├── FormatSQLNode          [内置] ✅ 完整
        └── (用户自定义 via _template.py)
```

#### 4.4.2 BaseNode — 统一执行接口

```python
@dataclass
class NodeInput:
    query_text: str
    context: dict       # 来自 Workflow 共享上下文
    config: dict         # 来自 workflow.yml 节点配置

@dataclass
class NodeOutput:
    result: Any
    metadata: dict
    errors: list[str]
    context: dict        # 写回 Workflow 共享上下文

class BaseNode(ABC):
    name: str
    description: str
    
    @abstractmethod
    async def execute(self, input: NodeInput) -> NodeOutput: ...
    async def setup_input(self, raw_input: dict) -> NodeInput: ...
    async def update_context(self, output: NodeOutput, shared_context: dict) -> dict: ...
```

#### 4.4.3 AgenticNode — 7 项能力

所有涉及 LLM 调用的 Node 继承 `AgenticNode`，自动获得 7 项 Harness 内置能力：

| # | 能力 | 说明 | 状态 |
|---|------|------|------|
| ① | **Session 管理** | Memory 按 node_name 隔离，`:memory:` (ephemeral) 或持久化 | ✅ |
| ② | **Tool 集成** | 统一管理 func tools + MCP servers，按名称路由 | ✅ |
| ③ | **PermissionManager** | 三级权限：allow/deny/ask，每次工具调用前自动检查 | ✅ |
| ④ | **SkillManager** | 加载 Skill 定义，注册为可调用工具 | ✅ |
| ⑤ | **ActionHistoryManager** | 环形缓冲区，记录每次 execute() 的 IO | ✅ |
| ⑥ | **Auto-Compaction** | 上下文达到 90% token 上限时自动摘要压缩 | ✅ |
| ⑦ | **Streaming** | SSE 流式推送中间 token | ✅ |

```python
class AgenticNode(BaseNode):
    def __init__(self, config: AgenticConfig):
        self.session = AdvancedSQLiteSession(...)        # ①
        self.tool_registry = ToolRegistry()              # ②
        self.permission_manager = PermissionManager(...) # ③
        self.skill_manager = SkillManager(...)           # ④
        self.action_history = ActionHistoryManager(...)  # ⑤
        self.compaction_threshold = 0.90                 # ⑥
        self.streaming_enabled = config.enable_streaming # ⑦
    
    async def setup_input(self, raw_input: dict) -> NodeInput:
        node_input = await super().setup_input(raw_input)
        # 权限预检 — 工具调用前检查，避免执行时才报错
        for tool_name in node_input.config.get("tools", []):
            if self.permission_manager.check(tool_name) == "deny":
                raise PermissionDeniedError(...)
        # auto-compaction — 上下文接近上限时触发
        if self._token_usage() >= self.compaction_threshold:
            node_input = await self._auto_compact(node_input)
        return node_input
```

#### 4.4.4 ExecuteSQLNode (完整实现)

**Phase 1 唯一完整实现的 Node** — 整个系统的安全执行基础。

**安全设计:**
- **13 个写操作拦截关键词:** INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, REPLACE, MERGE, GRANT, REVOKE, RENAME, VACUUM
- **7 个安全关键词:** SELECT, WITH, EXPLAIN, DESCRIBE, SHOW, PRAGMA, ANALYZE
- `_is_write_statement()` — 正则剥离注释后检查首词，同时检测多语句（分号分隔）
- 仅当 `config.allow_write=True` 时放行写操作

**多驱动支持 (优先级顺序):**
```python
async def execute(self, input: NodeInput) -> NodeOutput:
    sql = input.context.get("sql", input.query_text)
    
    # 只读检查
    if self._is_write_statement(sql) and not input.config.get("allow_write"):
        return NodeOutput(errors=["Write operation blocked"], metadata={"reason": "blocked"})
    
    conn = input.context["connection"]
    
    # 按连接类型分发
    if self._is_sqlite_connection(conn):
        result = self._execute_sqlite(conn, sql, max_rows)
    elif self._is_duckdb_connection(conn):
        result = self._execute_duckdb(conn, sql, max_rows)
    elif self._is_asyncpg_connection(conn):
        result = await self._execute_asyncpg(conn, sql, max_rows)
    else:
        result = self._execute_generic(conn, sql, max_rows)  # DB-API 2.0
    
    return NodeOutput(result=result)
```

#### 4.4.5 SchemaLinkingNode (Phase 2 完整实现)

将 NL 查询中的实体链接到数据库 Schema。Phase 2 实现使用关键词匹配 + 迭代 FK 扩展（无 RAG），Phase 3 将升级为 RAG 混合检索。

```python
class SchemaLinkingNode(AgenticNode):
    name = "schema_linking"
    
    async def execute(self, input: NodeInput) -> NodeOutput:
        # Step 1: NL 提取实体 (规则 + 关键词)
        entities = await self._extract_entities(input.query_text)
        # Step 2: 关键词匹配表名/列名 (精确匹配 + 模糊匹配)
        candidates = self._keyword_match(entities, schema)
        # Step 3: FK 扩展 (迭代 while-loop 直到稳定, 最多 5 轮)
        linked = self._fk_expand(candidates, schema)
        # Step 4: 列消歧 (多表同名列)
        result = self._column_disambiguate(linked, entities)
        # Step 5: 生成过滤后的 SchemaSnapshot
        linked_schema = schema.filter(tables=linked_tables)
        return NodeOutput(result=result, context={"linked_schema": linked_schema})
```

**Phase 3 升级:** RAG 混合检索 (BM25+LanceDB) 替代关键词匹配，提升语义匹配能力。

#### 4.4.6 其余 Node 概要

| Node | 用途 | Phase |
|------|------|-------|
| **ParseNLNode** | 语言检测 + 时间解析 + 意图分类 + 歧义检测 → SQR | 2 ✅ |
| **GenerateSQLNode** | 调用 Prompt Builder + LiteLLM 生成 SQL 候选 (temperature/multi_perspective) | 2 ✅ |
| **ValidateSQLNode** | 3 步验证管道 (语法→Schema 引用→类型兼容) | 2 ✅ |
| **SchemaLinkingNode** | 实体提取 + 关键词匹配 + 迭代 FK 扩展 + 列消歧 | 2 ✅ |
| **ExecuteSQLNode** | 只读拦截 + 多驱动执行 + EXPLAIN 集成 + 自愈重试 | 1-2 ✅ |
| **ReflectionNode** | 执行失败后分析错误 + 修正 SQL | 3 ✅ |
| **HybridSearchNode** | RAG 混合检索 (BM25+LanceDB+RRF) | 3 ✅ |
| **MetricResolveNode** | MetricFlow 指标 → SQL | 3 ✅ |
| **ExplainPlanNode** | EXPLAIN 执行计划 + LLM 解读 | 4 ✅ |
| **DialectTranslateNode** | sqlglot 方言转换 (PlainNode) | 4 ✅ |
| **FormatSQLNode** | SQL 格式化 (PlainNode) | 4 ✅ |
| **RespondNode** | 将执行结果/SQL/验证报告组装为自然语言回复 (LLM 可选, 无 router 走模板) | 审计修复 ✅ |

---

### 4.5 Harness 运行时 (`app/harness/`)

> **SPEC 参考:** §3.4

**Phase 3 状态:** 全部模块完整 ✅

#### 4.5.1 WorkflowRunner — 执行引擎

```python
class WorkflowRunner:
    """Harness 执行引擎 — 按 node_order 推进，不靠 LLM 跳转"""
    
    def __init__(self, plan: WorkflowPlan, node_registry: NodeRegistry):
        self.plan = plan
        self.node_registry = node_registry
        self.trace = WorkflowTrace()
    
    async def run(self, input_text: str, session_id: str = None) -> WorkflowResult:
        # 1. 初始化共享上下文
        shared_context = {"query_text": input_text, "session_id": session_id}
        node_index = 0
        
        # 2. 按 node_order 顺序推进（非 LLM 自由跳转）
        while node_index < len(self.plan.node_order):
            node_def = self.plan.node_order[node_index]
            node = self.node_registry.get(node_def.node)
            
            # 3. 执行节点
            node_input = await node.setup_input(
                {**shared_context, "config": node_def.config}
            )
            output = await node.execute(node_input)
            
            # 4. 质量评估
            evaluation = await self.evaluate_result(node_def, output)
            self.trace.record(node_def.id, output, evaluation)
            
            # 5. 失败处理: retry / skip / abort
            if not evaluation.passed:
                if node_def.on_failure == "abort":
                    return WorkflowResult(status="failed", trace=self.trace)
                elif node_def.on_failure == "skip":
                    node_index += 1
                    continue
            
            # 6. 更新共享上下文 → 下一节点
            shared_context = await node.update_context(output, shared_context)
            node_index += 1
        
        await self.trace.persist()
        return WorkflowResult(status="completed", context=shared_context, trace=self.trace)
```

#### 4.5.2 其他模块

| 模块 | 职责 | Phase 1 |
|------|------|---------|
| **ConfigLoader** | 加载 agent.yml → `AgentConfig` typed dataclass，mtime 检测热加载 | ✅ |
| **PlanLoader** | 加载 workflow.yml → `WorkflowPlan`，带缓存 | ✅ |
| **PermissionManager** | 三级管控 (allow/deny/ask)，规则 CRUD，默认级别 | ✅ |
| **ConstraintEnforcer** | 强制执行全局约束 (LLM 计数、重试上限、行数、超时、只读) | ✅ |
| **ActionHistoryManager** | 环形缓冲区，记录每次 execute() 的 ActionEntry | ✅ |
| **AdvancedSQLiteSession** | Memory 管理，SQLite 后端 (:memory:/磁盘)，TTL 过期，node_name 隔离 | ✅ |
| **SkillManager** | Skill 发现、加载、注册为工具 (`skill_manager.py` + SkillFuncTool) | ✅ |
| **Auto-Compaction** | 90% token 阈值触发确定性压缩 (`compaction.py` ContextCompactor) | ✅ |
| **ToolRegistry** | func tools + MCP tools 统一注册路由 (`tool_registry.py`) | ✅ |

**审计修复 (2026-07-17):** `WorkflowRunner.evaluate_result()` 已实现真实指标检查器（syntax_valid/schema_compliant/execution_success），`WorkflowTrace.record()`/`persist()` 已实现（JSONL 写入 `$DE_TRACE_DIR` 或 `data/traces/{date}/`），反射跳转 bug（revise 节点被跳过）已修复。

#### 4.5.3 Harness 五项不变量

| # | 不变量 | 实现方式 |
|---|--------|---------|
| 1 | **LLM 不决定下一步** | `WorkflowRunner.run()` 严格按 `node_order` 推进 |
| 2 | **权限不可绕过** | `PermissionManager.check()` 在工具调用前强制执行 |
| 3 | **只读优先** | `ExecuteSQLNode._is_write_statement()` 拦截 13 种写关键词 |
| 4 | **次数有上限** | `ConstraintEnforcer` 硬限制 LLM 调用和重试次数 |
| 5 | **执行可审计** | `WorkflowTrace` + `ActionHistoryManager` 完整记录 IO |

---

### 4.6 MCP 协议层 (`app/mcp/`)

> **SPEC 参考:** §3.3

**Phase 5 状态:** 全部 MCP 组件完整 ✅ — 5 个 Server (DB/Knowledge/Vector/Learning/对外暴露) + Client + Registry

#### 4.6.1 双向 MCP 架构

```
外部系统 ←── MCP 协议 ──→ [本系统 MCP Server]  ←──→  [本系统 MCP Client] ←──→ Database/Knowledge/Vector Server
 (Claude Desktop 等)       (对外暴露 NL2SQL)             (消费外部服务)
```

#### 4.6.2 四个 MCP Server

| Server | 工具 | 资源 | 状态 |
|--------|------|------|------|
| **Database MCP** | execute_read_query, list_tables, describe_table, search_schema, analyze_plan | schemas://{db_id}/tables, schemas://{db_id}/tables/{table} | ✅ (Phase 1) |
| **Knowledge MCP** | search_terms, match_rules, lookup_term | glossary://{domain}/terms, rules://{domain}/ | ✅ (Phase 3.15, port 8082) |
| **Vector MCP** | hybrid_search, knn_search, refresh_index | embeddings://{store}/ | ✅ (Phase 3.16, port 8083) |
| **Learning MCP** | record_feedback, submit_rating, extract_rule 等 6 工具 | 3 资源 | ✅ (Phase 5.6, port 8084, main.py 已注册) |

#### 4.6.3 对外暴露的 MCP Server

系统本身作为 MCP Server 向外部暴露 NL2SQL 能力：

| 工具 | 功能 | 输入 → 输出 |
|------|------|------------|
| `nl_query` | NL 转 SQL 并执行 | `{nl_text, domain?, db_id?}` → `{sql, columns, rows, explanation}` |
| `explain_sql` | 解释 SQL 逻辑 | `{sql}` → `{explanation_nl, steps[]}` |
| `search_schema` | 搜索 Schema | `{keywords, db_id}` → `{tables[], columns[]}` |
| `list_domains` | 列出可用领域 | `{}` → `{domains[]}` |
| `list_subagents` | 列出可用 Subagent | `{}` → `{subagents[]}` |

#### 4.6.4 MCPServerRegistry

```python
class MCPServerRegistry:
    _servers: dict[str, MCPServerConfig] = {}
    
    def register(self, config: MCPServerConfig): ...
    def get(self, server_id: str) -> MCPServerConfig: ...
    def list_all(self) -> list[MCPServerConfig]: ...
    def update_health(self, server_id: str, healthy: bool): ...

mcp_registry = MCPServerRegistry()  # 全局单例
```

---

### 4.7 核心引擎 (`app/core/`)

> **SPEC 参考:** §4.1, §4.4, §4.5

**Phase 2 状态:** 全部 ✅ 完成 — LanguageDetector, TimeParser, IntentClassifier, AmbiguityDetector, SQRBuilder, NLParser, PromptBuilder, SQLGenerator, SQLValidator (Step 1-3), SelfHealingRetry

核心引擎是 NL→SQL 管道的实现层，包含 5 个主要组件和完整的自愈重试机制。

#### 4.7.1 NL 解析器 — `NLParser`

**类设计:**

```python
from app.models.query import SQR, Entity, TimeRange, Condition, IntentType, Ambiguity

class NLParser:
    """NL 解析器 — 5 步管道将自然语言转为结构化查询表示 (SQR)"""
    
    def __init__(
        self,
        language_detector: LanguageDetector,
        time_parser: TimeParser,
        intent_classifier: IntentClassifier,
        ambiguity_detector: AmbiguityDetector,
        sqr_builder: SQRBuilder,
    ):
        self.language_detector = language_detector
        self.time_parser = time_parser
        self.intent_classifier = intent_classifier
        self.ambiguity_detector = ambiguity_detector
        self.sqr_builder = sqr_builder
    
    async def parse(self, nl_text: str, context: dict | None = None) -> SQR:
        """执行完整的 5 步解析管道，返回 SQR"""
        # Step 1-4 的具体实现见下文各子模块
        lang = self.language_detector.detect(nl_text)           # Step 1
        time_range = self.time_parser.extract(nl_text, lang)    # Step 2
        intent = self.intent_classifier.classify(nl_text, lang) # Step 3
        ambiguities = self.ambiguity_detector.detect(           # Step 4
            nl_text, intent, context
        )
        return self.sqr_builder.build(                          # Step 5
            nl_text=nl_text,
            language=lang,
            time_range=time_range,
            intent=intent,
            ambiguities=ambiguities,
        )
```

**a) LanguageDetector** — 基于 Unicode 范围检测，**无需 LLM**：

```python
class LanguageDetector:
    # 中文字符 Unicode 区间: 一-鿿, 㐀-䶿
    CJK_RANGES = [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)]
    
    def detect(self, text: str) -> str:
        cjk_count = sum(1 for c in text if any(lo <= ord(c) <= hi for lo, hi in self.CJK_RANGES))
        alpha_count = sum(1 for c in text if c.isalpha() and not any(...))
        ratio = cjk_count / max(alpha_count + cjk_count, 1)
        if ratio > 0.5:  return "zh"
        if ratio > 0.1:  return "mixed"
        return "en"
```

**b) TimeParser** — 规则引擎 + LLM 兜底：

```python
class TimeParser:
    """两层策略: 规则匹配 (快速/免费) → LLM 兜底 (复杂表达)"""
    
    RULES = {
        "zh": {
            r"上个月": lambda now: TimeRange(now.replace(month=now.month-1), now, "month"),
            r"去年同[期月]": lambda now: TimeRange(now.replace(year=now.year-1), now, "year"),
            r"最近(\d+)天": lambda m, now: TimeRange(now - timedelta(days=int(m.group(1))), now, "day"),
            # ... 20+ 中文时间模式
        },
        "en": {
            r"last month": lambda now: TimeRange(now.replace(month=now.month-1), now, "month"),
            r"year to date|ytd": lambda now: TimeRange(now.replace(month=1, day=1), now, "year"),
            r"last (\d+) days": lambda m, now: TimeRange(now - timedelta(days=int(m.group(1))), now, "day"),
            # ... 15+ 英文时间模式
        }
    }
    
    async def extract(self, text: str, lang: str) -> TimeRange | None:
        # L1: 规则匹配 (90%+ 命中率)
        for pattern, resolver in self.RULES.get(lang, {}).items():
            if m := re.search(pattern, text, re.IGNORECASE):
                return resolver(m, datetime.now())
        # L2: LLM 兜底 (复杂表达如 "the third week of last quarter")
        if self._needs_llm_fallback(text):
            return await self._llm_extract(text)
        return None
```

**c) IntentClassifier** — 正则+关键词字典，**无 LLM**，< 1ms：

```python
class IntentClassifier:
    PATTERNS = {
        IntentType.SELECT:     [r"\bSELECT\b", r"查询|查看|列出|find|list|show|get"],
        IntentType.AGGREGATE:  [r"\bCOUNT\b|\bSUM\b|\bAVG\b", r"统计|汇总|总计|count|total|sum|avg"],
        IntentType.JOIN:       [r"\bJOIN\b", r"关联|联合|join|together with"],
        IntentType.COMPARISON: [r"\bHAVING\b|大于|小于|高于|低于|比|>|<", r"compare|versus|vs"],
        IntentType.TIME_SERIES: [r"趋势|变化|走势|按月|按天|trend|monthly|daily|over time"],
        IntentType.FUNNEL:     [r"转化|漏斗|funnel|conversion"],
    }
    
    def classify(self, text: str, lang: str) -> IntentType:
        scores = {intent: sum(1 for p in pats if re.search(p, text, re.I))
                  for intent, pats in self.PATTERNS.items()}
        if max(scores.values()) == 0:
            return IntentType.UNKNOWN
        return max(scores, key=scores.get)
```

**d) AmbiguityDetector** — 4 类歧义检测，不解析，仅标记：

```python
class AmbiguityDetector:
    def detect(self, nl_text: str, intent: IntentType, 
               context: dict | None = None) -> list[Ambiguity]:
        ambiguities = []
        # 属性归属歧义: time-related noun 可能修饰多列
        ambiguities.extend(self._check_attribute_ambiguity(nl_text))
        # 聚合歧义: "平均" / "average" 可能有多种计算方式
        if intent == IntentType.AGGREGATE:
            ambiguities.extend(self._check_aggregation_ambiguity(nl_text))
        # 范围歧义: 程度词 "大/小/高/低" 无明确阈值
        ambiguities.extend(self._check_range_ambiguity(nl_text))
        # 时间参照歧义: "最近/recent" 无明确范围
        ambiguities.extend(self._check_temporal_ambiguity(nl_text))
        return ambiguities
```

**e) SQRBuilder** — 组装最终 SQR，不做推理，只做结构化：

```python
class SQRBuilder:
    def build(self, nl_text, language, time_range, intent, ambiguities) -> SQR:
        return SQR(
            raw_text=nl_text,
            language=language,
            intent=intent,
            entities=self._extract_entities(nl_text),
            time_range=time_range,
            conditions=self._extract_conditions(nl_text),
            order=self._extract_order(nl_text),
            ambiguities=ambiguities,
        )
```

#### 4.7.2 Prompt 构建器 — `PromptBuilder`

**类设计:**

```python
from enum import Enum

class PromptLevel(Enum):
    L1_SIMPLE = "simple"        # 单表 WHERE, ~1500 tokens
    L2_STANDARD = "standard"    # 多表 JOIN + 聚合, ~3500 tokens  
    L3_COMPLEX = "complex"      # CTE + 窗口函数, ~5500 tokens
    L4_SELF_HEAL = "self_heal"  # 错误修正, ~2000 tokens

class PromptBuilder:
    """多级 Prompt 构建器, 按查询复杂度选择模板"""
    
    def __init__(self, token_budget: dict[PromptLevel, int] | None = None):
        self.token_budgets = token_budget or {
            PromptLevel.L1_SIMPLE:   1500,
            PromptLevel.L2_STANDARD: 3500,
            PromptLevel.L3_COMPLEX:  5500,
            PromptLevel.L4_SELF_HEAL: 2000,
        }
    
    def select_level(self, sqr: SQR) -> PromptLevel:
        """根据 SQR 复杂度确定 Prompt 级别"""
        complexity = 0
        if len(sqr.entities) > 2:        complexity += 1
        if sqr.intent == IntentType.JOIN: complexity += 1
        if sqr.intent == IntentType.FUNNEL: complexity += 2
        if any(a.type == "aggregation" for a in sqr.ambiguities): complexity += 1
        if sqr.time_range:               complexity += 1
        if complexity <= 1: return PromptLevel.L1_SIMPLE
        if complexity <= 3: return PromptLevel.L2_STANDARD
        return PromptLevel.L3_COMPLEX
    
    def build(self, sqr: SQR, schema: SchemaSnapshot, domain: DomainConfig,
              history: list[ConversationTurn], similar_pairs: list[QueryPair]) -> str:
        """组装完整 Prompt, 动态填充组件"""
        level = self.select_level(sqr)
        budget = self.token_budgets[level]
        prompt = self._render_template(level)
        prompt = self._inject_schema(prompt, schema, budget)
        prompt = self._inject_glossary(prompt, domain, sqr, budget)
        prompt = self._inject_rules(prompt, domain, sqr, budget)
        prompt = self._inject_history(prompt, history, budget)
        prompt = self._inject_similar_queries(prompt, similar_pairs, budget)
        prompt = prompt.replace("{question}", sqr.raw_text)
        return prompt
    
    def estimate_tokens(self, text: str) -> int:
        """粗略 Token 估算: 英文 ~4 char/token, 中文 ~1.5 char/token"""
        en_chars = sum(1 for c in text if c.isascii())
        zh_chars = len(text) - en_chars
        return int(en_chars / 4 + zh_chars / 1.5)
```

Token 预算分配（L3 级别为例）：

| 组件 | 预算 | 说明 |
|------|------|------|
| System 指令 | ~300 | 角色和规则，固定 |
| Schema 注入 | ~2000 | 核心表 <8 张，超大 Schema 分层注入 |
| 术语表 | ~500 | 只注入 RAG 匹配的相关术语 |
| 业务规则 | ~500 | 只注入匹配的业务规则 |
| 历史相似查询 | ~1000 | 语义缓存命中，最多 3 个 |
| 对话历史 | ~1000 | 最近 3 轮压缩 |
| 用户问题 | ~200 | — |
| **总计** | **~5500** | — |

#### 4.7.3 SQL 生成器 — `SQLGenerator`

```python
from app.llm.lite_router import LiteLLMRouter

class SQLGenerator:
    """通过 LiteLLM 路由层调用 LLM 生成 SQL"""
    
    def __init__(self, router: LiteLLMRouter, prompt_builder: PromptBuilder):
        self.router = router
        self.prompt_builder = prompt_builder
    
    async def generate(
        self, sqr: SQR, schema: SchemaSnapshot, domain: DomainConfig,
        num_candidates: int = 3, strategy: str = "temperature"
    ) -> list[SQLCandidate]:
        """生成多个候选 SQL (默认 3 个)"""
        prompt = self.prompt_builder.build(sqr, schema, domain, [], [])
        if strategy == "temperature":
            return await self._temperature_sampling(prompt, num_candidates)
        else:
            return await self._multi_perspective(prompt, num_candidates)
    
    async def _temperature_sampling(self, prompt: str, n: int) -> list[SQLCandidate]:
        """策略 A: 单次调用 temperature=0.7, n=3, 多候选"""
        response = await self.router.complete(
            messages=[{"role": "user", "content": prompt}],
            model="claude-sonnet-4", temperature=0.7, n=n,
        )
        return [SQLCandidate(sql_text=c.text, confidence=c.score, ...) 
                for c in response.choices]
    
    async def _multi_perspective(self, prompt: str, n: int) -> list[SQLCandidate]:
        """策略 B: 3 次独立调用, 分别强调性能/可读性/功能完整"""
        perspectives = [
            "优先查询性能，确保使用索引",
            "优先代码可读性，使用清晰的 CTE 和别名",
            "优先功能完整性，确保覆盖所有边界条件",
        ]
        tasks = [
            self.router.complete(messages=[{"role": "user", "content": f"{p}\n\n{prompt}"}],
                                model="claude-sonnet-4", temperature=0.7)
            for p in perspectives[:n]
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        return [SQLCandidate(...) for r in responses if not isinstance(r, Exception)]
    
    async def generate_stream(self, sqr: SQR, ...) -> AsyncIterator[str]:
        """流式生成: 通过 SSE 实时推送 SQL token"""
        prompt = self.prompt_builder.build(sqr, ...)
        async for token in self.router.complete_stream(messages=[...], model="claude-sonnet-4"):
            yield token
```

**多候选策略选择:**

| 场景 | 推荐策略 | 原因 |
|------|---------|------|
| 简单查询 (<2 表) | `temperature` | 单次调用更快，歧义少 |
| 复杂查询 (≥3 表) | `multi_perspective` | 多维度覆盖，候选多样性强 |
| 流式输出 | `temperature` | 单次流式更简单 |
| 需要对比 | `multi_perspective` | 不同视角的差异更有价值 |

#### 4.7.4 SQL 验证器 — `SQLValidator`

```python
class SQLValidator:
    """5 步验证管道，每步独立可跳过"""
    
    def __init__(self, dialect_adapter: DialectAdapter):
        self.dialect = dialect_adapter
    
    async def validate(self, sql: str, schema: SchemaSnapshot, 
                       rules: list[BusinessRule]) -> ValidationReport:
        errors, warnings = [], []
        
        # Step 1: 语法解析
        syntax_ok, syntax_err = await self._check_syntax(sql)
        if syntax_err: errors.append(syntax_err)
        
        # Step 2: Schema 引用验证
        schema_ok, schema_err = await self._check_schema_refs(sql, schema)
        if schema_err: errors.extend(schema_err)
        
        # Step 3: 类型兼容检查
        type_ok, type_warn = await self._check_types(sql, schema)
        if type_warn: warnings.extend(type_warn)
        
        # Step 4: EXPLAIN 执行计划分析
        explain_ok, explain_warn = await self._analyze_explain(sql, schema)
        if explain_warn: warnings.extend(explain_warn)
        
        # Step 5: 业务规则校验
        rule_ok, rule_warn = await self._check_rules(sql, rules)
        if rule_warn: warnings.extend(rule_warn)
        
        passed = len(errors) == 0
        score = self._calculate_score(passed, len(warnings))
        return ValidationReport(passed=passed, score=score,
                                errors=errors, warnings=warnings)
    
    async def _check_syntax(self, sql: str) -> tuple[bool, str | None]:
        """使用 sqlparse 解析，捕获语法错误"""
        try:
            parsed = sqlparse.parse(sql)
            if not parsed or not parsed[0].tokens:
                return False, "SYNTAX_ERROR: Unable to parse SQL"
            return True, None
        except Exception as e:
            return False, f"SYNTAX_ERROR: {e}"
    
    async def _check_schema_refs(self, sql: str, schema: SchemaSnapshot) -> tuple[bool, list[str]]:
        """提取 SQL 中的表名/列名，与 Schema 比对"""
        errors = []
        tables = self._extract_table_names(sql)
        for table in tables:
            if table not in schema.table_names:
                errors.append(f"SCHEMA_ERROR: Table '{table}' not found")
        columns = self._extract_column_refs(sql)
        for col_ref in columns:
            table, col = col_ref if "." in col_ref else (None, col_ref)
            if not schema.has_column(table, col):
                errors.append(f"SCHEMA_ERROR: Column '{col_ref}' not found")
        return len(errors) == 0, errors
    
    async def _check_types(self, sql: str, schema: SchemaSnapshot) -> tuple[bool, list[str]]:
        """检查 WHERE/JOIN ON 中的类型兼容性, 聚合函数参数类型"""
        warnings = []
        # 提取比较表达式, 检查左右类型是否兼容
        comparisons = self._extract_comparisons(sql)
        for left, op, right in comparisons:
            left_type = schema.get_column_type(left)
            right_type = schema.get_column_type(right) if right in schema.all_columns else None
            if left_type and right_type and not self._types_compatible(left_type, right_type):
                warnings.append(f"TYPE_WARNING: Comparing {left_type} with {right_type} in '{left} {op} {right}'")
        return True, warnings
    
    async def _analyze_explain(self, sql: str, schema: SchemaSnapshot) -> tuple[bool, list[str]]:
        """EXPLAIN 分析: 检测 Seq Scan, 缺失索引, 嵌套循环"""
        # 实际执行 EXPLAIN 需要数据库连接，这里返回检查模式
        warnings = []
        # 检查是否缺少 WHERE 子句 (全表扫描风险)
        if not re.search(r'\bWHERE\b', sql, re.I) and re.search(r'\bFROM\s+\w+', sql, re.I):
            warnings.append("PERF_WARNING: No WHERE clause, potential full table scan")
        return True, warnings
```

**验证状态机:**

```
语法检查 ──失败──→ 严重错误, 阻断 (passed=false)
  │ 通过
  ▼
Schema 引用 ──失败──→ 表/列不存在, 阻断 (passed=false)
  │ 通过
  ▼
类型兼容 ──警告──→ 类型不匹配, 不阻断 (warnings++)
  │
  ▼
EXPLAIN 分析 ──警告──→ Seq Scan/缺失索引, 不阻断 (warnings++)
  │
  ▼
业务规则 ──警告──→ 违反规则, 不阻断 (warnings++)
  │
  ▼
ValidationReport {passed, score, errors[], warnings[]}
```

#### 4.7.5 Self-Healing Retry — `SelfHealingRetry`

```python
class SelfHealingRetry:
    """自愈重试循环 — 验证/执行失败后自动分析修正 (max 3 轮)"""
    
    MAX_ROUNDS = 3
    
    def __init__(self, generator: SQLGenerator, validator: SQLValidator):
        self.generator = generator
        self.validator = validator
    
    async def retry_until_valid(
        self, original_nl: str, failed_sql: str, error: str,
        schema: SchemaSnapshot, domain: DomainConfig
    ) -> SQLCandidate | None:
        """自愈重试循环"""
        current_error = error
        current_prompt = self._build_correction_prompt(original_nl, failed_sql, error)
        
        for round_num in range(1, self.MAX_ROUNDS + 1):
            # 1. 使用修正 Prompt 重新生成
            corrected = await self.generator.generate(
                SQR(raw_text=current_prompt), schema, domain, num_candidates=1
            )
            corrected_sql = corrected[0].sql_text
            
            # 2. 验证修正后的 SQL
            report = await self.validator.validate(corrected_sql, schema, [])
            if report.passed:
                return SQLCandidate(sql_text=corrected_sql, retry_round=round_num, ...)
            
            # 3. 准备下一轮修正
            current_error = "\n".join(report.errors)
            current_prompt = self._build_correction_prompt(
                original_nl, corrected_sql, current_error
            )
        
        return None  # 3 轮后仍失败, 返回 None
    
    def _build_correction_prompt(self, nl: str, sql: str, error: str) -> str:
        return f"""之前的 SQL 生成有误, 请修正:

原始问题: {nl}
生成的 SQL: {sql}
错误: {error}

请修正 SQL, 确保:
- 所有引用的表和列存在
- 类型兼容
- 保持与原查询语义一致"""
```

**自愈决策树:**

```
验证/执行失败
    │
    ├── 语法错误 → 修正方言语法 → 重试
    ├── Schema 引用错误 → 补充完整列名 → 重试
    ├── 类型不兼容 → 添加 CAST → 重试
    ├── 执行失败 → 分析错误消息 → 重试
    └── 3 轮后仍失败 → 返回错误给用户, 建议手动编写
```

#### 4.7.6 Phase 实现计划

| 步骤 | 内容 | Phase | 状态 |
|------|------|-------|------|
| 1 | `LanguageDetector` + `TimeParser` 规则库 (zh 37 + en 34 模式) | 2 | ✅ |
| 2 | `IntentClassifier` 7 分类 + `AmbiguityDetector` 4 类检测 | 2 | ✅ |
| 3 | `SQRBuilder` + `NLParser` 5 步管道集成 | 2 | ✅ |
| 4 | `PromptBuilder` L1-L4 模板 + Token 预算管理 | 2 | ✅ |
| 5 | `SQLGenerator` — LiteLLM 路由 + 多候选 (temperature/multi_perspective) + 流式 | 2 | ✅ |
| 6 | `SQLValidator` Step 1-3 (语法/Schema 引用/类型兼容) | 2 | ✅ |
| 7 | `SQLValidator` Step 4-5 (EXPLAIN/业务规则) | 3 | ✅ (2026-07-17 审计修复补齐) |
| 8 | `SelfHealingRetry` 完整循环 (错误分析 + 修正 + max 3 轮) | 2 | ✅ |
| 9 | 集成测试: SQLite E2E 管道, 10 核心 NL-SQL 对验证, 916 全量通过 | 2 | ✅ |

---

### 4.8 RAG 与领域知识引擎 (`app/knowledge/`)

> **SPEC 参考:** §4.2

**Phase 5 状态:** 全部完成 ✅ — 含 Phase 5 演化引擎 + MetricFlow 深度集成

#### 4.8.1 RAG 混合检索架构

三类 RAG 存储共享同一套混合检索引擎：

```
┌─────────────────────────────────────────────────────────┐
│                  RAG 存储模块体系                          │
│                                                          │
│  SchemaMetadataRAG    MetricRAG         DocumentStore    │
│  (表/列元数据)        (业务指标/KPI)    (平台文档)        │
│                                                          │
│  共享检索引擎:                                            │
│  ┌──────────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ BM25 稀疏检索     │  │ LanceDB 向量 │  │ RRF 融合   │ │
│  │ (jieba/whitespace)│  │ (3命名空间)  │  │ (k=60)     │ │
│  └──────────────────┘  └──────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────┘
```

#### 4.8.2 三类 RAG 存储

| 存储 | 检索对象 | BM25 索引字段 | LanceDB 向量 | 数据源 |
|------|---------|-------------|-------------|--------|
| **SchemaMetadataRAG** | 表/列元数据 | table_name + column_name + comment | 语义描述 embedding | DDL / Schema 提取器 |
| **MetricRAG** | 业务指标 | 指标名 + 别名 + 维度 | 指标描述 embedding | MetricFlow 定义 / 术语表 |
| **DocumentStore** | 平台文档 | title + keywords | 文档内容 embedding (按 512 token 分块) | 上传 / 预置 |

#### 4.8.3 混合检索算法

**BM25 公式:**
```
score_BM25(Q, D) = Σ IDF(t) · (f(t,D) · (k₁+1)) / (f(t,D) + k₁ · (1-b + b·|D|/avgdl))
参数: k₁=1.5, b=0.75
```

**RRF 融合 (默认):**
```
score_RRF(D) = Σ 1/(60 + rank_sparse(D)) + Σ 1/(60 + rank_dense(D))
```

**动态 α 权重 (备选加权线性融合):**
```
score_hybrid = α · norm(score_BM25) + (1-α) · norm(score_dense)
α 取值: 含明确表/列名→0.7 (BM25优先) | 抽象业务查询→0.3 (语义优先) | 默认→0.5
```

#### 4.8.4 Schema 分层检索 (大 Schema >30 表)

```
Step 1: NL 分析 → 提取关键词 + 判断抽象度 + 确定 α 权重
Step 2: RAG 混合检索 → 候选表集合 T1 (score≥0.3, ≤15 张)
Step 3: 术语映射 → 候选表集合 T2 → 合并到 T1
Step 4: FK 扩展 → 1 层外键扩展 → 最终候选 ≤8 张表
Step 5: 动态注入 → 候选表完整 Schema + 非候选表仅表名
```

#### 4.8.5 Evolvable Context 活知识库

**四类知识:**
| 类型 | 来源 | 捕获方式 | 演化触发 |
|------|------|---------|---------|
| Schema 元数据 | DDL / 连接探测 | 定时同步 + DDL 监听 | Schema 变更 |
| 参考 SQL | 用户确认的查询 | 自动保存 + 质量评分 | 用户确认后 |
| 语义模型 | MetricFlow + 术语映射 | 配置加载 + 自动发现 | 指标变更 |
| 业务指标 | MetricFlow + 业务规则 | 语义层解析 + 规则提取 | 规则审核通过 |

**演化循环:** 感知 (Sense) → 吸收 (Ingest) → 检索 (Retrieve) → 生成 (Generate) → 验证反馈 (Verify) →

#### 4.8.6 MetricFlow 语义层集成

```
NL "上个月营业收入?" → 匹配 MetricFlow 指标 "revenue"
    → MetricFlowEngine.query_metrics(metrics=["revenue"], dimensions=["date_month"], ...)
    → 跨方言 SQL: PG(SUM(amount-refund)) / Snowflake(DATE_TRUNC+SUM) / StarRocks(DATE_TRUNC+SUM)
```

**MetricFlow + sqlglot 互补:**
| 数据库 | MetricFlow 原生 | sqlglot 兜底 |
|--------|---------------|-------------|
| PG, MySQL, Snowflake, BigQuery, Databricks | ✅ | — |
| DuckDB | ⚠️ 部分 | ✅ |
| SQLite, StarRocks, Redshift, ClickHouse, Trino | ❌ | ✅ |

#### 4.8.7 领域管理

```
领域自动检测: NL 文本 → 关键词匹配 (domain.yaml keywords) 
    → 术语匹配 (glossary.yaml terms) → Schema 匹配
    → [score > 0.6 → 自动切换 | < 0.6 → 提示用户选择]
```

---

### 4.9 工作流编排 (`app/workflow/`)

> **SPEC 参考:** §4.9

**Phase 5 状态:** 全部完成 ✅ — 6/6 Plan YAML + PlanSelector (3 层路由 + 自适应优化) + Router + Monitor

#### 4.9.1 六种 Plan 模板

| 模板 ID | DAG 拓扑 | 适用场景 | 状态 |
|---------|---------|---------|------|
| `gensql_agentic` | schema_linking → generate_sql → validate_sql → execute_sql | 标准 NL→SQL 单轮 | ✅ |
| `ez_query` | schema_linking → generate_sql → execute_sql | 简单查询跳过验证 | ✅ |
| `reflection` | schema_linking → generate → execute → reflect → [revise → exec...] | 复杂查询需执行验证 | ✅ |
| `chat_agentic` | parse_nl → hybrid_search → generate_sql → validate_sql → respond | 多轮对话增量查询 | ✅ |
| `explore` | parse_nl → hybrid_search → format | Schema 探索 | ✅ |
| `metric_query` | metric_resolve → generate_sql → validate_sql → execute_sql | MetricFlow 指标查询 | ✅ |

#### 4.9.2 Plan 选择器 (Phase 3)

```
3 层路由决策:
  L0: 规则引擎 → has_join → gensql_agentic | complexity=high → reflection | 单表简单 → ez_query
  L1: 历史匹配 → 查找相似查询的 Plan 使用记录
  L2: LLM 分类 → 边界情况轻量 LLM 决策
```

#### 4.9.3 自适应优化 (Phase 5)

```
ez_query 编辑率 > 30% → 自动升级到 gensql_agentic
reflection 完成率 < 70% → 拆分为子查询推荐
某 Plan P95 耗时 > 阈值 → 触发优化告警
```

---

### 4.10 LiteLLM 路由层 (`app/llm/`)

> **SPEC 参考:** §4.4.0

**Phase 2 状态:** ✅ 完整实现 — RouterConfig + LiteLLMRouter + 3 层 Fallback + CostTracker + Mock 模式 + 流式输出  
~~**Phase 1 状态:** 未开始 ❌ (仅依赖声明在 pyproject.toml，0 行代码)~~  
**目标 Phase:** ✅ Phase 2 完成

#### 4.10.1 LiteLLMRouter 类设计

```python
from litellm import acompletion, completion
from dataclasses import dataclass, field

@dataclass
class ProviderConfig:
    """单个 LLM 提供商配置"""
    provider: str           # openai | anthropic | deepseek | qwen | ...
    api_key_env: str        # 环境变量名, 如 OPENAI_API_KEY
    models: list[str]       # 支持的模型列表
    base_url: str | None    # 自定义端点 (代理/私有部署)
    rate_limit_rpm: int     # 每分钟最大请求数

@dataclass  
class RouterConfig:
    """LiteLLM 路由总配置"""
    providers: dict[str, ProviderConfig]
    default_model: str = "claude-sonnet-4"
    fallback_chain: list[str] = field(default_factory=lambda: ["gpt-4.1", "deepseek-v3"])
    max_retries: int = 3
    timeout_seconds: int = 60

class LiteLLMRouter:
    """统一 LLM 路由 — 提供者注册/路由/降级/流式/成本追踪"""
    
    def __init__(self, config: RouterConfig):
        self.config = config
        self._provider_status: dict[str, bool] = {}  # provider → healthy?
        self._call_counter: dict[str, int] = {}       # provider → 调用计数
        self._cost_tracker = CostTracker()
    
    async def complete(
        self, messages: list[dict], model: str | None = None,
        temperature: float = 0.7, n: int = 1, **kwargs
    ) -> Any:
        """非流式调用, 带自动 Fallback"""
        model = model or self.config.default_model
        return await self._route_with_fallback(
            messages, model, temperature, n, stream=False, **kwargs
        )
    
    async def complete_stream(
        self, messages: list[dict], model: str | None = None,
        temperature: float = 0.7, **kwargs
    ) -> AsyncIterator[str]:
        """流式调用, 带自动 Fallback"""
        model = model or self.config.default_model
        return await self._route_with_fallback(
            messages, model, temperature, 1, stream=True, **kwargs
        )
    
    async def _route_with_fallback(
        self, messages: list[dict], model: str, temperature: float,
        n: int, stream: bool, **kwargs
    ) -> Any:
        """核心路由逻辑: 尝试首选 → 失败则沿 Fallback 链降级"""
        providers = self._get_provider_chain(model)
        last_error = None
        
        for provider_name, provider_model in providers:
            try:
                provider = self.config.providers[provider_name]
                response = await self._call_provider(
                    provider, provider_model, messages, temperature, n, stream, **kwargs
                )
                self._cost_tracker.record(provider_name, provider_model, response)
                return response
            except RateLimitError:
                continue  # 降级到下一个
            except Exception as e:
                last_error = e
                continue
        
        raise last_error or RuntimeError("All providers exhausted")
    
    def _get_provider_chain(self, model: str) -> list[tuple[str, str]]:
        """构建 Fallback 链: [(provider, model), ...]"""
        # 首选模型 → fallback_chain 中的模型
        primary_provider = self._find_provider_for_model(model)
        chain = [(primary_provider, model)]
        for fallback_model in self.config.fallback_chain:
            fb_provider = self._find_provider_for_model(fallback_model)
            chain.append((fb_provider, fallback_model))
        return chain
```

#### 4.10.2 Provider 适配器模式

```python
class BaseProviderAdapter:
    """统一 Provider 接口 — 所有提供商适配器的基类"""
    
    async def complete(self, model: str, messages: list[dict], 
                       temperature: float, **kwargs) -> dict:
        raise NotImplementedError
    
    async def complete_stream(self, model: str, messages: list[dict],
                              temperature: float, **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError

class AnthropicAdapter(BaseProviderAdapter):
    async def complete(self, model, messages, temperature, **kwargs):
        return await acompletion(
            model=f"anthropic/{model}",
            messages=messages,
            temperature=temperature,
            **kwargs,
        )

class OpenAIAdapter(BaseProviderAdapter):
    async def complete(self, model, messages, temperature, **kwargs):
        return await acompletion(
            model=f"openai/{model}",
            messages=messages,
            temperature=temperature,
            **kwargs,
        )

class DeepSeekAdapter(BaseProviderAdapter):
    async def complete(self, model, messages, temperature, **kwargs):
        return await acompletion(
            model=f"deepseek/{model}",
            messages=messages,
            api_base=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            temperature=temperature,
            **kwargs,
        )
```

#### 4.10.3 模型选择与 Fallback 链

| 工作流级别 | 首选模型 | Fallback 1 | Fallback 2 | 应用场景 |
|-----------|---------|-----------|-----------|---------|
| **express** | Claude Haiku / GPT-4.1-mini | GPT-4.1-mini | DeepSeek-V3 | 简单查询，成本优先 |
| **standard** | Claude Sonnet / GPT-4.1 | GPT-4.1 | DeepSeek-V3 | 标准查询，质量成本平衡 |
| **deep** | Claude Opus / GPT-4o | Claude Sonnet | GPT-4.1 | 复杂查询，质量优先 |
| **self_heal** | Claude Opus / o4-mini | Claude Sonnet | GPT-4.1 | 错误修正，精确推理 |

**Fallback 触发条件:** 限流 (429) / 超时 (>60s) / 服务端错误 (5xx) / 连接错误

```
首选模型 (claude-sonnet-4)
    ├── 限流/超时/5xx → 备选模型 (gpt-4.1)
    │       └── 限流/超时/5xx → 经济模型 (deepseek-v3)
    │               └── 失败 → 返回错误给用户
    └── 不可恢复错误 (4xx 认证/参数错误) → 直接返回错误
```

#### 4.10.4 成本追踪

```python
@dataclass
class CostRecord:
    timestamp: datetime
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float

class CostTracker:
    """记录每次 LLM 调用的 token 和成本"""
    
    PRICING = {
        ("anthropic", "claude-sonnet-4"):    (3.0,  15.0),   # $/1M input, $/1M output
        ("anthropic", "claude-haiku-4.5"):   (0.8,   4.0),
        ("openai",   "gpt-4.1"):             (2.0,   8.0),
        ("openai",   "gpt-4.1-mini"):        (0.15,  0.6),
        ("deepseek", "deepseek-v3"):         (0.27,  1.10),
    }
    
    def record(self, provider: str, model: str, response: Any):
        usage = response.usage
        price = self.PRICING.get((provider, model), (0, 0))
        cost = (usage.prompt_tokens * price[0] + 
                usage.completion_tokens * price[1]) / 1_000_000
        # 写入 cost_records 表并更新聚合统计
```

#### 4.10.5 语义缓存

```python
class SemanticCache:
    """L2 语义缓存: 相似度 > 0.95 复用历史结果"""
    
    def __init__(self, lance_db: LanceDBStore, threshold: float = 0.95):
        self.store = lance_db
        self.threshold = threshold
    
    async def lookup(self, query: str) -> SQLCandidate | None:
        embedding = await self._embed(query)
        results = await self.store.search(
            namespace="query_cache",
            vector=embedding,
            top_k=1,
        )
        if results and results[0].score >= self.threshold:
            return SQLCandidate(
                sql_text=results[0].metadata["sql"],
                confidence=results[0].score,
                from_cache=True,
            )
        return None
    
    async def store(self, query: str, candidate: SQLCandidate):
        embedding = await self._embed(query)
        await self.store.insert(
            namespace="query_cache",
            vector=embedding,
            metadata={"sql": candidate.sql_text, "confidence": candidate.confidence},
        )
```

#### 4.10.6 成本控制策略汇总

| 策略 | 说明 | 预估节省 | Phase |
|------|------|---------|-------|
| **查询对缓存** | 完全匹配的 NL→缓存 SQL (SHA256 hash) | ~20% | 2 |
| **语义缓存** | 相似度 > 0.95 复用 (LanceDB cosine) | ~15% | 3 |
| **Schema 缓存** | LRU 内存缓存，避免重复提取 Schema | 显著 | 2 |
| **模板匹配** | `"按 {col} 分组统计"` 等模式匹配，无 LLM 调用 | ~10% | 2 |
| **本地小模型兜底** | 简单查询走本地 Qwen2.5-7B 等 | 可变 | 5 |
| **Token 预算控制** | Prompt Builder 动态裁剪，避免超长上下文 | ~15% | 2 |

#### 4.10.7 Phase 实现计划

| 步骤 | 内容 | Phase | 状态 |
|------|------|-------|------|
| 1 | `RouterConfig` + `LiteLLMRouter` 类 | 2 | ✅ |
| 2 | Anthropic/OpenAI/DeepSeek 三个 Provider Adapter | 2 | ✅ |
| 3 | Fallback 链完整实现 (3 层) | 2 | ✅ |
| 4 | `CostTracker` + `cost_records` 表 | 2 | ✅ (含 Mock 模式) |
| 5 | `SemanticCache` (LanceDB) | 3 | ✅ (实现为 QueryPairCache L2 语义匹配, Phase 3.22) |
| 6 | 流式输出 + SSE 集成 | 2 | ✅ |
| 7 | 本地模型兜底 (Qwen2.5-7B) | 5 | ⏸ 延后 (MVP 范围外) |
| 8 | 集成测试: 模拟限流/超时/5xx | 2 | ✅ (48 tests) |

---

### 4.11 多 Agent 系统 (`app/agents/`)

> **SPEC 参考:** §4.11

**Phase 4 状态:** ✅ 全部完成 — 7 种 Agent + Orchestrator + 通信总线 + 5 种协作模式

#### 4.11.1 七种 Agent 角色

| Agent | 职责 | 工具集 |
|-------|------|--------|
| **Orchestrator** | 任务分解、Agent 选择、结果综合、冲突裁决 | handoff 到各 Agent, synthesize_results |
| **NL Understanding** | 语言检测、时间提取、意图分类、歧义检测 | parse_language, extract_time_entities |
| **Schema Retrieval** | RAG 混合检索、术语匹配、规则匹配 | hybrid_search, lookup_term, match_rules |
| **SQL Generation** | Prompt 构建、LiteLLM 调用、多候选、自愈重试 | llm_completion, build_prompt |
| **Validation** | 语法、Schema 引用、类型、EXPLAIN、业务规则校验 | validate_syntax, check_schema_refs, explain_plan |
| **Tool Execution** | MCP 工具网关、查询执行、结果缓存 | execute_query, call_mcp_tool, get_schema |
| **Feedback** | 收集反馈、提取 diff、触发学习管道 | record_feedback, extract_diff, trigger_learning |

#### 4.11.2 Agent 定义模式 (OpenAI Agents SDK)

```python
from agents import Agent, handoff, Runner, function_tool, RunContext

# ── NL Understanding Agent ──
@function_tool
async def parse_language(ctx: RunContext, text: str) -> dict:
    """检测输入语言"""
    return {"language": LanguageDetector().detect(text)}

@function_tool
async def extract_time_entities(ctx: RunContext, text: str, lang: str) -> list:
    """提取时间表达式"""
    return await TimeParser().extract_all(text, lang)

nl_agent = Agent(
    name="NL Understanding Agent",
    instructions="""你是 NL 理解专家。从用户自然语言中提取:
1. 语言 (zh/en/mixed)
2. 时间表达式 (映射到 TimeRange)
3. 查询意图 (SELECT/AGGREGATE/JOIN/COMPARISON/TIME_SERIES/FUNNEL)
4. 歧义标记 (属性归属/聚合/范围/时间)
输出结构化 SQR JSON。""",
    tools=[parse_language, extract_time_entities],
    model="claude-haiku-4.5",
)

# ── Schema Retrieval Agent ──
schema_agent = Agent(
    name="Schema Retrieval Agent",
    instructions="""你是 Schema 检索专家。给定 NL 查询和领域上下文:
1. 从 SQR 提取实体名和关键词
2. 在 SchemaMetadataRAG 中执行 BM25 + 向量混合检索
3. 通过外键扩展相关表 (1 层)
4. 匹配术语表中的业务术语
5. 返回候选表集合 (≤8 张) + 匹配的术语""",
    tools=[hybrid_search, lookup_term, match_rules],
    model="claude-haiku-4.5",
)

# ── SQL Generation Agent ──
sql_agent = Agent(
    name="SQL Generation Agent",
    instructions="""你是 SQL 生成专家。根据 Schema、术语表和业务规则生成正确的 SQL:
1. 只使用 Schema 中存在的表和列
2. 遵循业务规则中的约束
3. 使用术语表中的 SQL 表达式替代业务术语
4. 对聚合查询处理 NULL
5. 给出推理说明""",
    tools=[llm_completion, build_prompt],
    model="claude-sonnet-4",
)

# ── Validation Agent ──
validation_agent = Agent(
    name="Validation Agent",
    instructions="""你是 SQL 验证专家。严格检查生成的 SQL:
1. 语法是否正确 (sqlparse)
2. 引用的表和列是否存在 (Schema 对照)
3. 类型是否兼容 (比较表达式/聚合函数)
4. 执行计划是否高效 (EXPLAIN 分析)
5. 是否违反业务规则
输出 ValidationReport JSON""",
    tools=[validate_syntax, check_schema_refs, explain_plan],
    model="claude-haiku-4.5",
)

# ── Orchestrator Agent ──
@function_tool
async def synthesize_results(ctx: RunContext, candidates: list[dict]) -> dict:
    """综合多个 Agent 结果"""
    return {"best": candidates[0], "all": candidates, "explanation": "..."}

orchestrator = Agent(
    name="Orchestrator",
    instructions="""你是 NL2SQL 系统的调度器。核心流程:
1. 将 NL 查询分派给 NL Understanding Agent 解析
2. 将解析结果传给 Schema Retrieval Agent 检索
3. 将 Schema + 术语传给 SQL Generation Agent 生成
4. 将生成的 SQL 传给 Validation Agent 验证
5. 验证失败则回传修正 (max 3 轮)
6. 将最终 SQL + 报告返回用户""",
    handoffs=[
        handoff(nl_agent, tool_name_override="delegate_nl_analysis"),
        handoff(schema_agent, tool_name_override="delegate_schema_retrieval"),
        handoff(sql_agent, tool_name_override="delegate_sql_generation"),
        handoff(validation_agent, tool_name_override="delegate_validation"),
    ],
    tools=[synthesize_results],
    model="claude-sonnet-4",
)

# 运行
result = await Runner.run(orchestrator, nl_text)
```

#### 4.11.3 Agent 状态机

```
         ┌─────────────────────────────────┐
         │         Orchestrator            │
         │  task_decompose → dispatch →    │
         │  await_results → synthesize     │
         └──────────┬──────────────────────┘
                    │
     ┌──────────────┼──────────────┬──────────────┐
     ▼              ▼              ▼              ▼
 ┌───────┐     ┌───────┐     ┌───────┐     ┌───────┐
 │ idle  │     │ idle  │     │ idle  │     │ idle  │
 │  ▼    │     │  ▼    │     │  ▼    │     │  ▼    │
 │ busy  │     │ busy  │     │ busy  │     │ busy  │
 │  ▼    │     │  ▼    │     │  ▼    │     │  ▼    │
 │ done  │     │ error │     │ done  │     │ done  │
 │       │     │  ▼    │     │       │     │       │
 │       │     │ retry │     │       │     │       │
 └───────┘     └───────┘     └───────┘     └───────┘
   NL Agent    Schema Agent   SQL Agent   Validation Agent

Agent 状态: idle → busy → done | error → retry → busy (max 3) → done | failed
```

#### 4.11.4 通信总线设计

```python
from dataclasses import dataclass
from enum import Enum

class MessageType(Enum):
    HANDOFF = "handoff"        # Agent 间任务移交
    QUERY = "query"            # 查询请求
    RESULT = "result"          # 结果返回
    ERROR = "error"            # 错误报告
    CANCEL = "cancel"          # 取消请求

@dataclass
class AgentMessage:
    msg_id: str
    msg_type: MessageType
    from_agent: str
    to_agent: str
    payload: dict               # 结构化 JSON payload
    trace_id: str               # 全局 trace ID
    timestamp: datetime

class AgentBus:
    """Agent 通信总线 — 消息路由 + Handoff + 状态订阅"""
    
    def __init__(self):
        self._subscribers: dict[str, list[callable]] = {}
        self._message_log: list[AgentMessage] = []
    
    async def send(self, msg: AgentMessage) -> None:
        """发送消息到目标 Agent"""
        self._message_log.append(msg)
        for callback in self._subscribers.get(msg.to_agent, []):
            await callback(msg)
    
    def subscribe(self, agent_name: str, callback: callable) -> None:
        """订阅消息"""
        self._subscribers.setdefault(agent_name, []).append(callback)
    
    async def handoff(self, from_agent: str, to_agent: str, 
                      task: dict, trace_id: str) -> AgentMessage:
        """执行 Agent Handoff"""
        msg = AgentMessage(
            msg_id=uuid4().hex,
            msg_type=MessageType.HANDOFF,
            from_agent=from_agent,
            to_agent=to_agent,
            payload=task,
            trace_id=trace_id,
            timestamp=datetime.now(),
        )
        await self.send(msg)
        return msg
```

#### 4.11.5 五种协作模式

| 模式 | 实现方式 | 适用场景 |
|------|---------|---------|
| **Pipeline** | 串行 `handoff` 链 (Orchestrator → NL → Schema → SQL → Validate) | 大多数查询（默认） |
| **回传修正** | Validate error → `handoff` 回 SQL Agent → 再 Validate (max 3 轮) | 验证/执行失败后自愈 |
| **并行辩论** | `asyncio.gather(nl_agent, schema_agent, sql_agent)` → Orchestrator 综合 | 复杂查询多候选 |
| **Agent 协商** | 歧义 → Orchestrator 暂停 → 多 Agent 提案 → 投票 → 用户确认 | 语义模糊场景 |
| **多步流水线** | 子任务队列 → 每个子任务完整的 NL→SQL→Exec → 结果喂给下一步 | 多步骤 ETL 查询 |

#### 4.11.6 并发控制模型

```python
class AgentConcurrencyController:
    """单 Orchestrator + 并行子 Agent 的并发模型"""
    
    def __init__(self, max_parallel_agents: int = 4):
        self.semaphore = asyncio.Semaphore(max_parallel_agents)
    
    async def dispatch_parallel(self, agents: list[Agent], tasks: list[dict]) -> list[dict]:
        """并行分派任务到多个 Agent"""
        async def run_one(agent, task):
            async with self.semaphore:
                return await Runner.run(agent, task["input"])
        
        results = await asyncio.gather(
            *[run_one(a, t) for a, t in zip(agents, tasks)],
            return_exceptions=True
        )
        return [r for r in results if not isinstance(r, Exception)]
```

#### 4.11.7 人机协作模式

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| **完全自主** | confidence > 0.8, 无歧义, 只读查询 | Orchestrator 独立完成 |
| **关键节点确认** | 歧义 detected / 写操作 / confidence < 0.6 / 高风险 (DELETE/DROP 相关) | 暂停 → 用户确认 → 继续或取消 |
| **完全透明** | 用户显式启用 | 每个 Agent 的输入/输出流式展示，用户可随时介入或修改 |

#### 4.11.8 Phase 实现计划

| 步骤 | 内容 | Phase |
|------|------|-------|
| 1 | 6 个子 Agent 定义 (NL/Schema/SQL/Validate/Tool/Feedback) | 4 |
| 2 | Orchestrator + AgentBus + 消息格式 | 4 |
| 3 | Pipeline 协作模式 (默认) | 4 |
| 4 | 回传修正 + 并行辩论 | 4 |
| 5 | Agent 协商 + 多步流水线 | 5 |
| 6 | 人机协作 3 模式 | 4 |
| 7 | Agent 可观测性 (Trace + SSE) | 4 |
| 8 | 并发控制 + 超时熔断 | 5 |

---

### 4.12 Subagent 封装系统 (`app/subagent/`)

> **SPEC 参考:** §4.10

**Phase 4 状态:** ✅ 全部完成 — Manager + Router + 自适应调优

#### 4.12.1 概念

Subagent = 预配置、领域限定的多 Agent 系统实例，拥有独立的 Orchestrator 和裁剪后的 Agent 集合。

#### 4.12.2 三种交付渠道

| 渠道 | 访问方式 | 适用场景 |
|------|---------|---------|
| **Web UI** | `https://host/subagents/{name}` | 浏览器直接使用，独立聊天界面 |
| **REST API** | `POST /api/v1/subagents/{name}/query` | 嵌入其他系统 |
| **MCP Server** | 注册为独立 MCP 工具 `{prefix}_nl_query` | Claude Desktop 等 MCP Host |

#### 4.12.3 生命周期

```
创建 (注册 YAML 定义) → 初始化 (加载领域+Schema+MetricFlow+EvolvableContext)
    → 激活 (注册到 API/MCP/Web 路由) → 运行 (查询→学习→演化循环)
    → 停用/归档
```

---

### 4.13 持续学习系统 (`app/learning/`)

> **SPEC 参考:** §4.6

**Phase 5 状态:** ✅ 全部完成 — FeedbackCollector + QueryPairStore + PatternAnalyzer + Learning MCP Server

#### 4.13.1 反馈嵌入管道

```python
class FeedbackCollector:
    """全数据采集管道: 反馈 → embedding → LanceDB 入库 → 触发分析"""
    
    async def collect(self, turn_id: str, feedback: FeedbackData) -> None:
        # 1. 存储原始反馈
        await self.db.insert_feedback(turn_id, feedback)
        
        # 2. 生成 embedding (异步, 不阻塞响应)
        embedding = await self.embedder.embed(feedback.nl_original)
        
        # 3. 存入 LanceDB query_pairs 命名空间
        await self.lancedb.insert(
            namespace="query_pairs",
            vector=embedding,
            metadata={
                "nl": feedback.nl_original,
                "sql": feedback.sql_final,
                "rating": feedback.rating,
                "domain": feedback.domain_id,
            }
        )
        
        # 4. 触发后台模式分析 (满 5 条新反馈后)
        count = await self.db.count_pending_analysis(feedback.domain_id)
        if count >= 5:
            await self.trigger_pattern_analysis(feedback.domain_id)
```

#### 4.13.2 修正模式聚类

```python
from sklearn.cluster import DBSCAN
import numpy as np

class PatternAnalyzer:
    """使用 DBSCAN 聚类相似修正模式 (无需预设聚类数)"""
    
    def __init__(self, eps: float = 0.3, min_samples: int = 3):
        self.eps = eps                     # 邻域半径 (cosine距离)
        self.min_samples = min_samples     # 最小样本数 (≥3 才形成模式)
    
    async def analyze(self, domain_id: str) -> list[Pattern]:
        """分析领域内修正模式"""
        edits = await self.db.get_recent_edits(domain_id, limit=100)
        if len(edits) < self.min_samples:
            return []
        
        # 提取 diff 特征向量
        vectors = np.array([self._vectorize(e.diff_ops) for e in edits])
        
        # DBSCAN 聚类
        clustering = DBSCAN(eps=self.eps, min_samples=self.min_samples, 
                            metric="cosine").fit(vectors)
        
        patterns = []
        for cluster_id in set(clustering.labels_):
            if cluster_id == -1:  # 噪声点
                continue
            indices = np.where(clustering.labels_ == cluster_id)[0]
            cluster_edits = [edits[i] for i in indices]
            patterns.append(self._extract_pattern(cluster_edits))
        
        return patterns
    
    def _vectorize(self, diff_ops: list[DiffOp]) -> np.ndarray:
        """将 diff 操作序列转为特征向量 (操作类型 + 涉及表/列的 one-hot)"""
        # ... 特征工程逻辑
    
    def _extract_pattern(self, edits: list) -> Pattern:
        """从聚类中提取共性模式 (最常见的修改)"""
        # 统计所有编辑中最常见的操作 (如: SUM(price) → SUM(price*quantity))
        return Pattern(description=..., confidence=len(edits), ...)
```

#### 4.13.3 规则提取启发式

```python
class RuleExtractor:
    """自动提取候选规则: 同一模式 ≥3 次 → 候选规则 → 人工审核"""
    
    MIN_CONFIDENCE = 0.6
    MIN_SUPPORT = 3                    # 至少 3 个独立用户
    
    async def extract(self, pattern: Pattern) -> RuleCandidate | None:
        """从修正模式中提取候选规则"""
        if pattern.support_count < self.MIN_SUPPORT:
            return None
        if pattern.confidence < self.MIN_CONFIDENCE:
            return None
        
        # 提取 SQL 模板
        sql_template = self._generalize(pattern)
        
        return RuleCandidate(
            domain_id=pattern.domain_id,
            title=f"自动提取: {pattern.description}",
            pattern=pattern.description,
            sql_template=sql_template,
            confidence=pattern.confidence,
            support_count=pattern.support_count,
            source="auto_extraction",
            status="pending_review",
        )
    
    def _generalize(self, pattern: Pattern) -> str:
        """将具体修改泛化为 SQL 模板 (列名 → 占位符)"""
        # e.g., SUM(price) → SUM(price * quantity) 
        #   → SUM({amount_col}) 的通用模式: 金额 = 单价 × 数量
        return pattern.template
```

#### 4.13.4 查询对去重策略

```python
class QueryPairDeduplicator:
    """双重去重: SHA256 精确匹配 + Embedding 语义去重"""
    
    async def is_duplicate(self, nl: str, sql: str) -> bool:
        nl_hash = hashlib.sha256(nl.strip().lower().encode()).hexdigest()
        
        # L1: 精确匹配 (SHA256)
        if await self.db.query_pair_exists(nl_hash):
            return True
        
        # L2: 语义去重 (cosine > 0.98)
        embedding = await self.embedder.embed(nl)
        results = await self.lancedb.search(
            namespace="query_pairs",
            vector=embedding,
            top_k=1,
        )
        if results and results[0].score >= 0.98:
            return True
        
        return False
```

#### 4.13.5 语义缓存阈值策略

| 相似度 | 行为 | 说明 |
|--------|------|------|
| > 0.98 | 直接复用缓存 SQL | 几乎相同的查询 |
| 0.95-0.98 | 作为 few-shot 注入 Prompt | 高度相似但可能有细微差异 |
| 0.85-0.95 | 仅注入 Schema 上下文 | 同一领域，可参考 |
| < 0.85 | 正常生成流程 | 完全不同的查询 |

#### 4.13.6 Phase 实现计划

| 步骤 | 内容 | Phase |
|------|------|-------|
| 1 | `FeedbackCollector` — 全数据采集 + embedding 异步生成 | 5 |
| 2 | `QueryPairStore` — SHA256 去重 + 语义去重 | 5 |
| 3 | `PatternAnalyzer` — DBSCAN 聚类 | 5 |
| 4 | `RuleExtractor` — 启发式提取 + 人工审核 UI | 5 |
| 5 | `EvolvableContext` 自动演化 — Sense→Ingest→Retrieve→Verify 全自动化 | 5 |
| 6 | RAG 索引自动刷新 — DDL 监听 + 增量更新 | 5 |
| 7 | 质量仪表板 — 学习效果可视化 | 5 |

---

### 4.14 API 层 (`app/api/`)

> **SPEC 参考:** §6

**Phase 4 状态:** 全部 API 端点完整 ✅ (health + domains CRUD + databases CRUD + schema/search + query/generate/stream + feedback + learning + mcp + workflows)

#### 4.14.1 核心查询 API

| 方法 | 路径 | 说明 | Phase |
|------|------|------|-------|
| POST | `/api/v1/query` | 提交 NL 查询，返回 SQL + 结果 | 2 ✅ |
| POST | `/api/v1/query/stream` | SSE 流式端点 | 2 ✅ |
| POST | `/api/v1/query/generate` | 纯生成 SQL (无 schema/validate/execute) | 2 ✅ |
| POST | `/api/v1/query/explain` | 获取 SQL 自然语言解释 | 3 |
| POST | `/api/v1/execute` | 执行已生成 SQL (只读) | 2 ✅ (内嵌于 /query) |
| POST | `/api/v1/feedback` | 提交用户反馈 | 4 |
| GET | `/api/v1/sessions/{id}` | 获取对话会话 | 3 |
| GET | `/api/v1/sessions/{id}/versions` | 获取版本历史 | 4 |

#### 4.14.2 Schema 管理 API

| 方法 | 路径 | 说明 | Phase |
|------|------|------|-------|
| POST | `/api/v1/databases/test` | 测试数据库连接 | ✅ |
| POST | `/api/v1/databases/connect` | 添加数据库连接 | ✅ |
| GET | `/api/v1/databases` | 列出已连接数据库 | ✅ |
| GET | `/api/v1/schema/{db_id}` | 获取 Schema 快照 | ✅ |
| POST | `/api/v1/schema/{db_id}/refresh` | 刷新 Schema 缓存 | 3 |
| GET | `/api/v1/schema/{db_id}/search?q=` | 搜索表和列 | 3 |

#### 4.14.3 领域管理 API (Phase 3)

| 方法 | 路径 |
|------|------|
| GET | `/api/v1/domains` (✅) |
| POST | `/api/v1/domains` |
| PUT | `/api/v1/domains/{id}` |
| GET | `/api/v1/domains/{id}/glossary` |
| POST | `/api/v1/domains/{id}/glossary` |
| GET | `/api/v1/domains/{id}/rules` |
| POST | `/api/v1/domains/{id}/rules` |
| POST | `/api/v1/domains/detect` |

#### 4.14.4 其他 API 组 (Phase 3-4)

- **MCP 协议 API** (6 端点): servers, register, tools, resources, tool_call, resource_read
- **工作流 API** (5 端点): list, custom, simulate, stats, history
- **学习 API** (5 端点): stats, query-pairs, rule-candidates/review, quality, rag/refresh

#### 4.14.5 SSE 事件类型

```
event: workflow       → 工作流选择结果
event: stage          → 阶段变更 (parse/retrieve/generate/validate/execute)
event: token          → LLM token 实时推送
event: candidate      → SQL 候选完成
event: mcp_call       → MCP 工具调用记录
event: validation     → 验证结果
event: execution      → 执行结果
event: ambiguity      → 歧义通知
event: agent_trace    → Agent 决策追踪
event: agent_handoff  → Agent 间 Handoff
event: collaboration  → 协作模式切换
event: rag_result     → RAG 检索摘要
event: error          → 错误
event: done           → 完成
```

#### 4.14.6 FastAPI 中间件栈

```python
# app/api/__init__.py → create_app()
def create_app() -> FastAPI:
    app = FastAPI(title="NL2SQL Agent", version="0.1.0")
    
    # 中间件顺序 (后添加的先执行):
    app.add_middleware(RequestIDMiddleware)        # 1. 注入 X-Request-ID
    app.add_middleware(LoggingMiddleware)          # 2. 结构化日志 (请求/响应)
    app.add_middleware(AuthMiddleware)             # 3. 认证 (提取 API Key/用户)
    app.add_middleware(TenantMiddleware)           # 4. 租户上下文注入
    app.add_middleware(RateLimitMiddleware)        # 5. 限流
    app.add_middleware(DomainDetectMiddleware)     # 6. 领域自动检测
    
    return app
```

**各中间件职责:**

| 中间件 | 输入 | 输出 | Phase |
|--------|------|------|-------|
| **RequestID** | 请求头 `X-Request-ID` | 注入 `request.state.request_id`, 响应头回传 | 2 |
| **Logging** | 请求开始 | structlog 记录 method/path/duration/status | 2 |
| **Auth** | `Authorization` / `X-API-Key` 头 | 注入 `request.state.user` (User 对象) | 4 |
| **Tenant** | `X-Tenant-ID` 头 | 注入 `request.state.tenant_id` | 4 |
| **RateLimit** | IP / user / api_key | 通过或返回 429, 添加限流头 | 4 |
| **DomainDetect** | 查询文本 | 自动匹配领域, 注入 `request.state.domain_id` | 3 |

#### 4.14.7 统一错误码体系

| 错误码 | HTTP 状态 | 说明 | 模块 |
|--------|----------|------|------|
| `AUTH_INVALID_TOKEN` | 401 | Token 无效或过期 | Auth |
| `AUTH_INSUFFICIENT_PERMISSIONS` | 403 | 角色权限不足 | Auth |
| `AUTH_API_KEY_EXPIRED` | 401 | API Key 已过期 | Auth |
| `QUERY_EMPTY_INPUT` | 400 | NL 输入为空 | Query |
| `QUERY_DOMAIN_NOT_FOUND` | 404 | 指定的领域不存在 | Query |
| `QUERY_AMBIGUOUS` | 422 | 检测到歧义，需要用户澄清 | Query |
| `SCHEMA_TABLE_NOT_FOUND` | 404 | 表在 Schema 中不存在 | Schema |
| `SCHEMA_COLUMN_NOT_FOUND` | 404 | 列在表中不存在 | Schema |
| `SCHEMA_EXTRACTION_FAILED` | 502 | Schema 提取失败 (DB 连接问题) | Schema |
| `VALIDATION_SYNTAX_ERROR` | 422 | SQL 语法错误 | Validate |
| `VALIDATION_TYPE_MISMATCH` | 422 | SQL 类型不兼容 | Validate |
| `VALIDATION_RULE_VIOLATION` | 422 | 违反业务规则 | Validate |
| `EXECUTION_READ_ONLY` | 403 | 写操作被拦截 | Execute |
| `EXECUTION_TIMEOUT` | 504 | SQL 执行超时 | Execute |
| `EXECUTION_MAX_ROWS` | 413 | 结果超过行数限制 | Execute |
| `MCP_SERVER_UNREACHABLE` | 502 | MCP Server 不可达 | MCP |
| `MCP_TOOL_NOT_FOUND` | 404 | MCP Tool 未注册 | MCP |
| `LLM_PROVIDER_ERROR` | 502 | LLM 提供商所有模型都失败 | LLM |
| `LLM_RATE_LIMITED` | 429 | LLM 限流中 | LLM |
| `LIMIT_RATE_EXCEEDED` | 429 | API 限流触发 | Limit |
| `LIMIT_QUOTA_EXCEEDED` | 429 | 资源配额超限 | Limit |

#### 4.14.8 分页标准

```json
// 请求: cursor-based 分页
GET /api/v1/sessions?cursor=eyJpZCI6IjEyMyJ9&limit=20

// 响应
{
  "data": [...],
  "pagination": {
    "next_cursor": "eyJpZCI6IjE0MyJ9",
    "has_more": true,
    "limit": 20,
    "total": 125
  }
}
```

#### 4.14.9 标准错误响应

```json
{
  "error": {
    "code": "VALIDATION_SYNTAX_ERROR",
    "message": "SQL syntax error near 'FROM FROM'",
    "stage": "validate",
    "trace_id": "a1b2c3d4",
    "details": {
      "sql": "SELECT * FROM FROM users",
      "error_position": 15,
      "suggestion": "Remove duplicate FROM keyword"
    }
  }
}
```

---

### 4.15 Web UI (`app/web/`)

> **SPEC 参考:** §4.8

**Phase 1 状态:** 未开始 ❌  
**目标 Phase:** Phase 4

#### 4.15.1 Streamlit 页面结构

```
streamlit_app.py            # 主应用 (导航 + 路由)
api_client.py               # API 封装层 (所有端点 + SSE 消费)
components/
├── query_input.py          # 查询输入框 (多行 + 快捷键 Ctrl+Enter)
├── domain_selector.py      # 领域选择器 (搜索 + 自动检测)
├── sql_display.py          # SQL 展示区 (语法高亮 + 就地编辑)
├── candidate_switcher.py   # 多候选切换 (Tab/卡片)
├── result_table.py         # 执行结果表格 (分页 + 排序 + 导出)
├── history_sidebar.py      # 对话历史侧边栏
├── version_timeline.py     # SQL 版本时间轴
├── feedback_controls.py    # 反馈控件 (评分 1-5 + 文本)
├── settings_panel.py       # 设置面板 (连接 + 配置 + Schema)
└── subagent_chat.py        # Subagent 聊天组件
pages/
├── home.py                 # 查询主页面
├── domain_mgmt.py          # 领域配置管理
└── subagent_console.py     # Subagent 控制台
```

#### 4.15.2 API 客户端层

```python
# app/web/api_client.py
import streamlit as st
import httpx

class APIClient:
    """封装所有 API 端点 + SSE 消费"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self._headers = {"Authorization": f"Bearer {st.session_state.get('token', '')}"}
    
    async def submit_query(self, nl_text: str, domain_id: str = None,
                           db_id: str = None) -> dict:
        """POST /api/v1/query"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/query",
                json={"nl_text": nl_text, "domain_id": domain_id, "db_id": db_id},
                headers=self._headers, timeout=60.0,
            )
            return resp.json()
    
    async def submit_query_stream(self, nl_text: str, domain_id: str = None) -> AsyncIterator[dict]:
        """POST /api/v1/query/stream — SSE 流式消费"""
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/v1/query/stream",
                json={"nl_text": nl_text, "domain_id": domain_id},
                headers=self._headers, timeout=120.0,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[5:])
    
    # ... 其他端点的封装方法
```

#### 4.15.3 组件状态管理

```python
# Streamlit session_state 键设计
st.session_state.update({
    # 查询状态
    "current_query": "",           # 当前 NL 输入
    "current_sql": "",             # 当前选中的 SQL
    "candidates": [],              # 所有候选 SQL
    "selected_candidate": 0,       # 选中的候选索引
    
    # 会话状态
    "session_id": None,            # 当前会话 ID
    "session_history": [],         # 对话历史列表
    "domain_id": "default",        # 领域
    "db_id": None,                 # 数据库
    
    # UI 状态
    "is_streaming": False,         # 是否正在流式输出
    "show_explain": False,         # 是否显示执行计划
    "edit_mode": False,            # 是否在编辑 SQL
    
    # 认证状态
    "token": None,
    "user": None,
})
```

#### 4.15.4 组件数据流

```
[query_input]
     │ NL 输入 → ss.current_query
     ▼
[api_client.submit_query_stream()]
     │ SSE: token → sql_display 实时追加
     │ SSE: candidate → candidate_switcher 添加选项
     │ SSE: execution → result_table 更新表格
     │ SSE: rag_result → 侧边栏显示匹配表/术语
     │ SSE: error → st.error() 展示错误
     ▼
[sql_display] ← st.code_area(current_sql, language="sql", editable=True)
     │ 用户编辑 → current_sql 更新 → version_timeline 记录
     ▼
[result_table] ← st.dataframe(paginated_rows)
     │
[feedback_controls] ← rating_stars + feedback_text → api_client.submit_feedback()
```

#### 4.15.5 错误处理 UX

```python
# 统一的错误展示模式
def display_error(error: dict):
    code = error.get("code", "UNKNOWN")
    msg = error.get("message", "An error occurred")
    stage = error.get("stage", "")
    
    if stage == "validate" and code.startswith("SCHEMA_"):
        st.warning(f"🔍 Schema 问题: {msg}\n系统正在尝试修正...")
    elif code.startswith("LLM_"):
        st.error(f"🤖 LLM 服务异常: {msg}\n系统已尝试所有可用模型，请稍后重试")
    elif code.startswith("LIMIT_"):
        st.warning(f"⏱️ 请求受限: {msg}")
    elif code == "QUERY_AMBIGUOUS":
        # 展示歧义选项让用户选择
        options = error.get("details", {}).get("clarifications", [])
        choice = st.radio("请澄清您的意图:", options)
    else:
        st.error(f"❌ 错误 [{code}]: {msg}")
```

#### 4.15.6 Phase 实现计划

| 步骤 | 内容 | Phase |
|------|------|-------|
| 1 | `streamlit_app.py` 主框架 + `api_client.py` | 4 |
| 2 | query_input + domain_selector + sql_display + result_table | 4 |
| 3 | candidate_switcher + history_sidebar | 4 |
| 4 | feedback_controls + version_timeline | 4 |
| 5 | settings_panel (连接管理 + Schema 刷新) | 4 |
| 6 | pages/ (home + domain_mgmt + subagent_console) | 4 |
| 7 | SSE 流式消费 UI 集成 | 4 |
| 8 | 错误处理和重试 UX | 4 |

---

## 5. 数据流

### 5.1 主查询流程

```
用户输入 NL
    │
    ▼
[Workflow 选择] → Orchestrator 初始化
    │
    ├── handoff → NL Agent (语言检测 + 时间解析 + 意图分类 + 歧义检测) → SQR
    ├── handoff → Schema Agent (BM25+LanceDB 混合检索 + 术语匹配 + FK 扩展) → 候选表集合
    ├── handoff → SQL Agent (Prompt 构建 + LiteLLM 调用 + 多候选 + 流式输出)
    ├── handoff → Validation Agent (语法→Schema→类型→EXPLAIN→业务规则) → ValidationReport
    │       ├── 通过 → 继续
    │       └── 失败 → 回传 SQL Agent 修正 (max 3 轮)
    ├── handoff → Tool Agent (execute_query via MCP) → QueryResult
    │
    ▼
[返回用户] → SQL + 验证报告 + 结果预览 + 多候选 + 歧义确认 + Agent Trace
```

### 5.2 反馈学习流程

```
用户编辑 SQL / 评分 / 反馈
    │
    ▼
[反馈收集] → 记录 diff + 最终 SQL + 评分
    │
    ▼
[异步管道]
    ├── 查询对入库 (NL, SQL, embedding)
    ├── 评分聚合 (质量统计)
    └── 模式分析 (聚类修正模式)
            │
            ▼
        [规则提取] → 人工审核 → rules.yaml
```

---

## 6. Prompt 策略体系

> **SPEC 参考:** §8

### 6.1 分层策略

| 级别 | 场景 | 策略 | 推荐模型 |
|------|------|------|---------|
| L1 | 简单单表 WHERE | 直接生成，无 few-shot | Claude Haiku |
| L2 | 多表 JOIN + 聚合 | Schema + 术语 + 1 few-shot | Claude Sonnet |
| L3 | CTE + 窗口函数 | 完整 Schema + 规则 + 2-3 few-shot | Claude Opus |
| L4 | 自愈重试 | 原始 Prompt + 错误 + 修正指令 | Claude Opus/Sonnet |

### 6.2 基础 Prompt 模板

```
System:
你是一个 SQL 生成专家。将自然语言转换为 {dialect} SQL。
当前领域: {domain_name}，语言: {language}

== 数据库 Schema ==
{table_schemas}

== 业务术语表 ==
{relevant_terms}

== 业务规则 ==
{relevant_rules}

== 历史相似查询 ==
{similar_query_pairs}

== 对话历史 ==
{conversation_history}

== 用户问题 ==
{user_question}

== 要求 ==
1. 只生成 SELECT 查询
2. 严格使用 Schema 中存在的表和列名
3. 注意 NULL 处理
4. 对时间条件使用索引友好的写法

请生成 SQL 并附上简短推理说明。
```

### 6.3 防幻觉约束

每次 Prompt 嵌入 5 条硬约束：
```
1. 只使用上述 Schema 中列出的表和列名
2. 如果问题涉及的表不在列表中，回答"无法在已知表中找到{表名}"
3. 不要编造列名、表名或 JOIN 条件
4. 如果一个列名在多个表中出现，必须使用 表名.列名 格式
5. 聚合查询必须处理 NULL 值
```

### 6.4 错误修正 Prompt

```
之前的 SQL: {sql_text}
执行/验证错误: {error_message}

请修正这个 SQL。特别注意：
- {具体的修正提示}
- 确保所有引用的表和列存在
- 保持与原查询语义一致
```

### 6.5 多轮对话压缩

超过 3 轮时触发摘要：
```
对话历史摘要：
- 用户之前查询了 {topic1}，使用了 {table1} 的 {column1}
- 第二轮对结果进行了 {filter1} 过滤
- 上一轮生成了 {sql_prev}

用户最新问题：{current_question}
```

---

## 7. 数据存储设计

> **SPEC 参考:** §7

### 7.1 存储技术选型

| 存储类型 | 技术 | 用途 |
|---------|------|------|
| 主应用数据 | SQLite (本地) / PG (生产) | 会话、反馈、配置 |
| Session 存储 | SQLite (持久化) / :memory: (ephemeral) | 对话状态 |
| Memory 隔离 | 按 node_name 分目录 SQLite | 节点间互不干扰 |
| Workflow Trace | JSONL 文件 | 执行轨迹 |
| Schema 缓存 | SQLite + 内存 | 快速访问 |
| 向量存储 | **LanceDB** (3 命名空间) | RAG 密集检索 |
| BM25 索引 | SQLite FTS5 / Tantivy | RAG 稀疏检索 |
| 领域配置 | YAML 文件 | 版本控制友好 |
| 工作流定义 | YAML 文件 | 模板持久化 |
| LLM 响应缓存 | Redis (可选) / SQLite | 减少 API 调用 |
| MCP 注册表 | SQLite / 内存 | Server 注册信息 |

### 7.2 核心表结构

```sql
-- 会话
CREATE TABLE sessions (
    id UUID PK, domain_id VARCHAR(64), database_id VARCHAR(64),
    title VARCHAR(256), context_summary TEXT, turn_count INT,
    status VARCHAR(16) DEFAULT 'active', created_at, updated_at
);

-- 对话轮次
CREATE TABLE turns (
    id UUID PK, session_id UUID FK→sessions, turn_index INT,
    nl_input TEXT, nl_normalized TEXT, sqr_json JSONB,
    selected_sql TEXT, selected_candidate_id VARCHAR(64),
    execution_result JSONB, user_rating SMALLINT,
    status VARCHAR(16) DEFAULT 'completed', created_at
);

-- SQL 候选
CREATE TABLE sql_candidates (
    id VARCHAR(64) PK, turn_id UUID FK→turns,
    sql_text TEXT, confidence FLOAT, generation_mode VARCHAR(32),
    validation_report JSONB, is_selected BOOLEAN, rank SMALLINT, created_at
);

-- SQL 版本 (编辑历史)
CREATE TABLE sql_versions (
    id BIGSERIAL PK, turn_id UUID FK→turns, version_number INT,
    sql_text TEXT, parent_version INT, source VARCHAR(16),
    diff_from_parent TEXT, user_note TEXT, created_at
);

-- 查询对 (学习)
CREATE TABLE query_pairs (
    id BIGSERIAL PK, domain_id VARCHAR(64), nl_original TEXT,
    nl_normalized TEXT, sql_final TEXT, sql_hash VARCHAR(64) UNIQUE,
    language VARCHAR(10), intent VARCHAR(32), user_rating SMALLINT,
    edit_count INT, source VARCHAR(16), created_at, updated_at
);

-- 数据库连接
CREATE TABLE database_connections (
    id VARCHAR(64) PK, alias VARCHAR(128), db_type VARCHAR(32),
    host VARCHAR(256), port INT, database_name VARCHAR(128),
    username VARCHAR(128) ENCRYPTED, password VARCHAR(256) ENCRYPTED,
    ssl_enabled BOOLEAN, schema_cache JSONB, last_synced_at, created_at
);

-- 反馈记录
CREATE TABLE feedback_log (
    id BIGSERIAL PK, turn_id UUID FK→turns, candidate_id VARCHAR(64),
    rating SMALLINT, feedback_text TEXT, diff_ops JSONB,
    execution_time_ms INT, execution_success BOOLEAN, metadata JSONB, created_at
);

-- 规则候选
CREATE TABLE rule_candidates (
    id BIGSERIAL PK, domain_id VARCHAR(64), title VARCHAR(256),
    description TEXT, pattern TEXT, sql_template TEXT,
    source VARCHAR(32), confidence FLOAT, support_count INT,
    status VARCHAR(16) DEFAULT 'pending', created_at
);
```

### 7.3 存储路径布局

```
~/.data_engineer/
├── sessions/                     # 持久化 Session
│   ├── chat/{session_id}.db
│   └── {subagent}/{session_id}.db
├── memory/                       # 按 node_name 隔离
│   ├── chat/
│   └── {node_name}/
├── traces/{date}/{workflow_id}.jsonl
└── config/
    ├── agent.yml
    └── litellm.yaml
```

---

## 8. 实施路线图

> **SPEC 参考:** §11

### Phase 1: 基础骨架 (Week 1-2) — ✅ 完成

**目标:** 搭建可运行的项目框架，建立所有模块的公共契约。

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 1.1 | 项目初始化 | `pyproject.toml`, `.env.example`, 目录结构 | ✅ |
| 1.2 | 11 个数据模型 | `app/models/*.py` | ✅ |
| 1.3 | 配置层 | `settings.py`, `agent.yml`, 3 领域 YAML | ✅ |
| 1.4 | 数据库连接 (4/11) | `ConnectionFactory` + 4 驱动 | ✅ |
| 1.5 | Schema 提取 (3/11) | PG/SQLite/DuckDB 方言内省 | ✅ |
| 1.6 | Dialect Adapter (4/11) | sqlglot 翻译 + 4 完整预设 | ✅ |
| 1.7 | BaseNode + NodeRegistry | 统一执行接口 + 全局注册表 | ✅ |
| 1.8 | AgenticNode (骨架) | 7 能力框架 | ✅ |
| 1.9 | ExecuteSQLNode (完整) | 只读拦截 + 多驱动执行 | ✅ |
| 1.10 | Harness 运行时 (6/8) | Runner, ConfigLoader, PlanLoader, Permission, Constraint, ActionHistory | ✅ |
| 1.11 | MCP 定义 | DB Server 工具/资源 + Registry | ✅ |
| 1.12 | API 基础 (4 端点) | Health, Databases CRUD, Schema, Domains | ✅ |
| 1.13 | Workflow 模板 (2/6) | gensql_agentic.yml, ez_query.yml | ✅ |
| 1.14 | 种子测试 | 5 测试文件, 30 用例 | ✅ |

**交付物:** 63 源文件 (45 app/ + 11 tests/ + 2 YAML + 1 agent.yml + 3 domain YAML + 1 SQL), 30 测试通过, 1 完整 Node, 4 数据库, 6 端点

---

### Phase 2: NL→SQL 管道 (Week 3-4) — ✅ 完成

**目标:** 跑通基础 NL→SQL 管道，实现单轮查询的端到端流程。

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| 2.1 | **NL 解析器** — LanguageDetector (Unicode + jieba) | P0 | ✅ |
| 2.2 | **TimeParser** — 规则解析 (中文 37 + 英文 34 模式, L1/L2 双层策略) | P0 | ✅ |
| 2.3 | **IntentClassifier** — 加权关键词打分 + 优先级决胜, 7 类分类 | P0 | ✅ |
| 2.4 | **AmbiguityDetector** — 4 类歧义模式检测 + `Ambiguity[]` 输出 | P1 | ✅ |
| 2.5 | **SQR Builder + NLParser 集成** — 5 步管道 + SQR 组装 + 置信度评分 | P0 | ✅ |
| 2.6 | **ParseNLNode** — 包装 NL 解析器为 Node | P0 | ✅ |
| 2.7 | **PromptBuilder** — L1-L4 四级模板 + Token 预算管理 | P0 | ✅ |
| 2.8 | **LiteLLM Router** — RouterConfig + ProviderConfig + 3 层 Fallback + CostTracker + Mock 模式 | P0 | ✅ |
| 2.9 | **GenerateSQLNode** — SQLGenerator + temperature/multi_perspective 策略, Stream 输出 | P0 | ✅ |
| 2.10 | **ValidateSQLNode** — Step 1-3 (sqlglot 语法/Schema 引用/类型兼容) + 表别名解析 | P0 | ✅ |
| 2.11 | **SchemaLinkingNode** — 实体提取 + 关键词检索 + 迭代 FK 扩展 + 列消歧 | P0 | ✅ |
| 2.12 | **SelfHealingRetry** — ErrorAnalysis 分类 + 修正 Prompt 构建 + max 3 轮重试 | P1 | ✅ |
| 2.13 | **SQL Executor 增强** — EXPLAIN 集成 + PlanAnalysis 解析 + 静态 SQL 分析 | P1 | ✅ |
| 2.14 | **API: POST /query** — 5 步管道 (Parse→Link→Generate→Validate→Execute) + Pydantic 模型 | P0 | ✅ |
| 2.15 | **API: POST /query/stream** — SSE 流式端点 (parse/schema/token/validation/done/error 事件) | P1 | ✅ |
| 2.16 | **集成测试** — SQLite 端到端查询, 8 测试类, 77 测试, 10 核心 NL-SQL 对验证 | P0 | ✅ |

**交付物:** 完整 NL→SQL 单轮查询管道，5 个新 Node (ParseNL/SchemaLinking/GenerateSQL/ValidateSQL + SelfHealingRetry)，3 个 API 端点，916 测试全量通过

---

### Phase 3: RAG + 知识引擎 + 工作流 (Week 5-7) — ✅ 完成

**目标:** RAG 混合检索可用的 Schema 感知查询，MetricFlow 语义层集成。

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| 3.1 | **LanceDB 初始化** — 3 命名空间 (schema_metadata/metrics/documents) + Embedding 生成 | P0 | ✅ |
| 3.2 | **BM25 索引** — jieba(zh)/whitespace(en) 分词 + SQLite FTS5 倒排索引 | P0 | ✅ |
| 3.3 | **RRF 融合引擎** — k=60 + 加权线性融合备选 + 动态 α 权重 | P0 | ✅ |
| 3.4 | **SchemaMetadataRAG** — 表/列索引构建 + `find_relevant_tables()` + `find_columns()` | P0 | ✅ |
| 3.5 | **MetricRAG** — 指标索引 + `match_metrics()` + `suggest_dimensions()` | P1 | ✅ |
| 3.6 | **DocumentStore** — Markdown 标题分块 (≤512 tokens, 64 重叠) + content_type 分类 | P2 | ✅ |
| 3.7 | **HybridSearchNode** — 封装 RAG 检索为 Node | P0 | ✅ |
| 3.8 | **Schema 分层检索** — 5 步策略 (分析→RAG→术语→FK→注入) | P0 | ✅ |
| 3.9 | **Domain 管理器** — YAML 加载 + 切换 + 自动检测 (关键词/术语/Schema 3 层) | P0 | ✅ |
| 3.10 | **Glossary 管理器** — CRUD + 匹配 + 映射解析 | P0 | ✅ |
| 3.11 | **Rule 引擎** — 匹配 + 强制执行 | P1 | ✅ |
| 3.12 | **Evolvable Context 雏形** — 4 类知识自动采集 (Schema/参考SQL/语义模型/指标) | P1 | ✅ |
| 3.13 | **MetricFlow 集成** — 指标定义 + `query_metrics()` + 跨方言 SQL | P1 | ✅ |
| 3.14 | **MetricResolveNode** | P1 | ✅ |
| 3.15 | **Knowledge MCP Server** — 暴露术语/规则资源 | P2 | ✅ |
| 3.16 | **Vector MCP Server** — 暴露 hybrid_search/knn_search | P2 | ✅ |
| 3.17 | **Plan 选择器** — 3 层路由 (规则→历史→LLM) | P1 | ✅ |
| 3.18 | **4 Plan 模板** — reflection, chat_agentic, explore, metric_query | P1 | ✅ |
| 3.19 | **ReflectionNode** — 执行失败→分析→修正循环 | P1 | ✅ |
| 3.20 | **API: 领域 CRUD** — 8 个端点 | P1 | ✅ |
| 3.21 | **API: Schema 刷新和搜索** | P1 | ✅ |
| 3.22 | **历史查询对缓存** — 完全匹配 + 语义匹配 (LanceDB) | P2 | ✅ |

**交付物:** RAG 增强的 Schema 检索, MetricFlow 语义层, 6 个 Plan 模板, Evolvable Context 雏形, 1992 测试全量通过 ✅

---

### Phase 4: 前端 + 多 Agent + Subagent (Week 8-10) — ✅ 完成

**目标:** Web UI 可用，多 Agent 系统运行，Subagent 可部署。

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| 4.1 | **Streamlit 主应用** — 布局 + 导航 + 路由 | P0 | ✅ |
| 4.2 | **查询输入组件** — 多行 + 快捷键 + 领域选择 | P0 | ✅ |
| 4.3 | **SQL 展示组件** — 语法高亮 + 编辑 + 多候选切换 | P0 | ✅ |
| 4.4 | **结果预览组件** — 表格 + 分页 + 排序 | P0 | ✅ |
| 4.5 | **对话历史侧边栏** | P1 | ✅ |
| 4.6 | **反馈控件** — 评分 + 文本 | P1 | ✅ |
| 4.7 | **设置面板** — 连接管理 + Schema 刷新 | P2 | ✅ |
| 4.8 | **Orchestrator Agent** — 任务分解 + Agent 选择 + 冲突裁决 | P0 | ✅ |
| 4.9 | **6 个子 Agent** — NL/Schema/SQL/Validation/Tool/Feedback | P0 | ✅ |
| 4.10 | **Agent 通信总线** — 消息路由 + Handoff + 状态订阅 | P1 | ✅ |
| 4.11 | **5 种协作模式** — Pipeline/回传/并行辩论/协商/多步 | P1 | ✅ |
| 4.12 | **Agent 可观测性** — Trace 收集 + SSE agent_trace 事件 | P2 | ✅ |
| 4.13 | **Subagent 管理器** — 生命周期 + YAML 加载 + 隔离上下文 | P0 | ✅ |
| 4.14 | **Subagent Router** — API/Web/MCP 3 通道路由 | P1 | ✅ |
| 4.15 | **MCP Server 对外暴露** — 5 工具 (nl_query/explain_sql/...) + 4 资源 | P1 | ✅ |
| 4.16 | **MCP Client 集成** — 连接外部 MCP Server | P1 | ✅ |
| 4.17 | **版本时间轴 UI** | P2 | ✅ |
| 4.18 | **ExplainPlanNode** — EXPLAIN + LLM 解读 | P2 | ✅ |
| 4.19 | **DialectTranslateNode + FormatSQLNode** (PlainNode) | P2 | ✅ |

**交付物:** 完整 Web UI, 多 Agent 编排, 可部署的 Subagent, MCP 双向架构, 19/19 任务完成

---

### Phase 5: 持续学习 + 完善 (Week 11-14) — ✅ 完成

**目标:** 学习闭环运转，生产就绪。

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| 5.1 | **反馈收集器** — 全数据采集管道 (SQLite 持久化 + 统计/导出) | P0 | ✅ |
| 5.2 | **查询对存储** — semantic embedding + 相似缓存 (UPSERT + 批量导入) | P0 | ✅ |
| 5.3 | **模式分析器** — 聚类修正模式 + 趋势检测 (规则驱动, difflib + unified_diff) | P1 | ✅ |
| 5.4 | **规则提取 + 审核 UI** — 自动提取 + Streamlit 人工审核页面 | P1 | ✅ |
| 5.5 | **Evolvable Context 演化引擎** — 全自动化 evolve() + auto_ingest + 生命周期管理 | P1 | ✅ |
| 5.6 | **Learning MCP Server** — 6 工具 + 3 资源, port 8084 | P2 | ✅ |
| 5.7 | **Subagent 自适应调优** — 滚动成功率追踪 + 延迟监控 + 配置建议 | P2 | ✅ |
| 5.8 | **MetricFlow 深度集成** — 术语表自动生成指标 + 跨方言验证 + 血缘追踪 | P2 | ✅ |
| 5.9 | **Workflow 自适应优化** — 编辑率/验证通过率追踪 + 路由规则优化 | P2 | ✅ |
| 5.10 | **RAG 索引自动刷新** — Schema 哈希版本 + TTL 刷新 + 周期后台调度 | P1 | ✅ |
| 5.11 | **Dialect Adapter 完善** — 7 种剩余适配器完整实现 (11/11) | P1 | ✅ |
| 5.12 | **Node 生态** — 开发文档 + 模板文件 | P2 | ✅ |
| 5.13 | **质量仪表板** — Streamlit 多页指标可视化 (概览/学习状态/查询对/系统) | P2 | ✅ |
| 5.14 | **端到端测试** — MCP + 多 DB + 学习循环 E2E | P0 | ✅ |
| 5.15 | **性能优化** — @track_latency + @count_calls 装饰器 + PerformanceStore | P1 | ✅ |

**交付物:** 生产就绪系统, 完整测试覆盖 (2600), 77 新测试, 质量仪表板, 15/15 任务完成

---

### 里程碑与验收标准

| 里程碑 | 功能验收 | 性能验收 | 测试覆盖 |
|--------|---------|---------|---------|
| **M1** (Week 2) ✅ | ExecuteSQLNode + 4 DB + API 基础 | N/A (骨架阶段) | 30 测试, 覆盖 4 模块 |
| **M2** (Week 4) ✅ | 10 条 NL→SQL 在 SQLite 上端到端通过 | P95 < 5s (本地), 首 Token < 1s | ✅ 916 测试, 集成测试 77 |
| **M3** (Week 7) ✅ | RAG 检索 Recall@10 > 0.85, MetricFlow 指标查询可用 | RAG 检索 < 500ms, SQL 生成 P95 < 8s | ✅ 1992 测试, 覆盖率 ≥75% |
| **M4** (Week 10) ✅ | Web UI 完整可用, 多 Agent Pipeline 模式端到端 | P95 < 10s (含 RAG), 页面加载 < 2s | ✅ 2523 测试, 覆盖率 ≥80% |
| **M5** (Week 14) ✅ | 11 DB 适配器, 学习闭环, 质量仪表板 | P95 < 8s (含缓存), LLM 缓存命中率 >30% | ✅ 2600 测试, 覆盖率 ≥80% |
| **M6** (Week 16) | Docker/K8s 部署, 监控告警, Grafana 仪表板 | P95 < 5s (生产优化后), 可用性 >99.5% | 混沌测试 + 故障转移测试 |

**Phase 依赖关系:**

```
M1 (基础) ✅ → M2 (管道) ✅ → M3 (RAG/知识) ✅ → M4 (UI/Agent) ✅ → M5 (学习/完善) ✅ → M6 (生产部署) 📋
```

---

## 9. 测试策略

### 9.1 测试金字塔 (当前: 2600 全量通过)

```
         /\
        /E2E\        7 E2E 测试 (0.3%) — 完整 NL→SQL 管道, SQLite E2E, 学习循环
       /------\
      / 集成   \      ~350 测试 (13.5%) — Node 集成 + API + Core 模块交互 + RAG/MCP/Knowledge
     /----------\
    /   单元     \     ~2240 测试 (86.2%) — 模块独立测试 (mock LLM/MCP/DB)
   /--------------\

测试分布: test_core/ (478) + test_nodes/ (186) + test_knowledge/ (~200) + test_rag/ (~100)
         + test_learning/ (70) + test_integration/ (77) + test_llm/ (48) + test_api/ (44)
         + test_harness/ (53) + test_mcp/ (~30) + test_subagent/ (~40) + test_e2e/ (7)
         + test_models/ (24) + test_db/ (6) + test_web/ + test_workflow/
```

### 9.2 各模块测试

| 模块 | 测试类型 | 工具/策略 | 状态 |
|------|---------|----------|------|
| 数据模型 | 单元 | dataclass 实例化 + 序列化 | ✅ 24 tests |
| 数据库连接 | 集成 | SQLite :memory: | ✅ 6 tests |
| NL 解析器 | 单元 | 规则匹配 + 边界用例 | ✅ 458 tests |
| Prompt 构建器 | 单元 | 模板渲染 + Token 预算 | ✅ 58 tests |
| SQL 生成器 | 单元 | mock LiteLLM 响应 | ✅ 49 tests |
| SQL 验证器 | 单元 + 集成 | sqlglot + 真实 Schema | ✅ 50 tests |
| SelfHealingRetry | 单元 | mock 错误 + 重试计数 | ✅ 28 tests |
| LiteLLM Router | 单元 | Mock 模式 + Fallback 链 | ✅ 48 tests |
| SchemaLinkingNode | 集成 | 真实 SQLite Schema + 关键词匹配 | ✅ 38 tests |
| ExecuteSQLNode | 集成 | SQLite :memory: + EXPLAIN | ✅ 32 tests |
| GenerateSQLNode | 单元 | mock LLM + 多候选 | ✅ 49 tests |
| ParseNLNode | 单元 | NLParser 包装 | ✅ 36 tests |
| ValidateSQLNode | 单元 | mock Schema + sqlglot | ✅ 50 tests |
| API | 集成 | FastAPI TestClient | ✅ 44 tests |
| 权限管理 | 单元 | PermissionManager + ConstraintEnforcer | ✅ 53 tests |
| 集成 (E2E) | 集成 | SQLite 端到端 + 10 NL-SQL 对 | ✅ 77 tests |
| RAG 检索 | 集成 | LanceDB :memory: + 测试文档 | ✅ Phase 3 |
| Knowledge | 单元 + 集成 | DomainManager + RuleEngine + EvolvableContext | ✅ Phase 3 |
| MCP | 集成 | mock MCP transport | ✅ Phase 3 |
| MetricFlow | 单元 | 指标定义 + 跨方言生成 | ✅ Phase 3 |
| 多 Agent | 集成 | mock LLM agent 响应 | ✅ Phase 4 |
| Subagent | 单元 + 集成 | Manager + Router + 自适应调优 | ✅ Phase 4 |
| Web UI | 集成 | Streamlit 页面渲染 + 导航 | ✅ Phase 4 |
| 持续学习 | 单元 + 集成 | FeedbackCollector + QueryPairStore + PatternAnalyzer | ✅ Phase 5 (77 tests) |
| E2E | 集成 | 完整管道 + 学习循环 | ✅ Phase 5 (7 tests) |

### 9.3 Mock 策略

- **LLM 调用:** 全部 mock — 返回预定义 SQL 候选
- **MCP Server:** 使用 FastMCP test transport (无需网络)
- **数据库:** SQLite :memory: — 零配置、快速

### 9.4 测试数据

- **E-commerce 测试 Schema:** users (id, name, tier, created_at), products (id, name, price, category), orders (id, user_id, product_id, amount, status, created_at)
- **验证集:** 每领域 ≥50 条 (NL, SQL) 测试对，覆盖 6 类查询 (简单 20% / JOIN 25% / 聚合 20% / 时间 15% / CTE 10% / 边缘 10%)

---

## 10. 安全与隐私

### 10.1 SQL 执行安全

| 措施 | 实现 |
|------|------|
| 只读默认 | `ExecuteSQLNode` 拦截 13 种写关键词 |
| 写操作确认 | `allow_write=True` + 用户二次确认 |
| 超时保护 | `statement_timeout_ms=30000` |
| 行数限制 | `max_result_rows=1000` |
| 资源隔离 | 系统连接池与业务连接池分离 |

### 10.2 数据隐私

| 措施 | 说明 |
|------|------|
| Schema 脱敏 | 发送 LLM 前可选替换真实表名/列名 |
| PII 列标记 | 标记 phone/email/id_card，排除出 Prompt |
| 数据零发送 | 业务数据（行内容）不发送到 LLM API |
| 本地模型 | 关键路径支持本地 LLM 备选 |
| 日志脱敏 | 自动过滤敏感字段 |

### 10.3 连接安全

- 密码加密存储 (Fernet/AES-256)
- SSL/TLS 连接支持
- API Token 认证

### 10.4 审计日志

```python
class AuditLogger:
    """记录所有关键操作的审计日志"""
    
    async def log(self, action: str, actor: str, resource: str, 
                  result: str, trace_id: str, metadata: dict = None):
        await self.db.insert("audit_log", {
            "timestamp": datetime.now(),
            "actor": actor,            # user_id / api_key_hash
            "action": action,          # query.execute / config.update / user.create
            "resource": resource,      # /api/v1/query / domain:ecommerce
            "result": result,          # success / denied / error
            "trace_id": trace_id,
            "ip_address": request.client.host,
            "metadata": json.dumps(metadata or {}),
        })
```

**审计事件分类:**

| 事件类型 | 记录内容 | 保留 |
|---------|---------|------|
| NL 查询 | NL 文本、生成 SQL、执行结果、耗时 | 90 天 |
| SQL 执行 | 完整 SQL、数据库、行数、耗时 | 90 天 |
| 配置变更 | 变更前后 diff、操作人、时间 | 1 年 |
| 用户操作 | 登录/登出、API Key 创建/撤销 | 1 年 |
| 权限变更 | 角色变更、授权/撤销 | 永久 |

### 10.5 网络安全

| 措施 | 配置 |
|------|------|
| **TLS 加密** | API 端口 443 (Nginx 终止)，DB 连接 SSL/TLS |
| **CORS** | 默认仅允许已配置的源，开发环境可放宽 |
| **安全 Header** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` |
| **MCP 绑定** | MCP Server 默认仅 localhost，外部暴露需显式配置 |
| **API 端口分离** | 业务端口 8000，Metrics 端口 8080 (不对外暴露) |

### 10.6 安全开发生命周期 (SDL)

| 阶段 | 措施 |
|------|------|
| **开发** | Pre-commit: ruff + bandit 安全扫描 |
| **PR** | 依赖扫描 (pip-audit), 密钥检测 (detect-secrets) |
| **构建** | Docker 镜像扫描 (Trivy) |
| **部署** | 只读文件系统 (readOnlyRootFilesystem: true), 非 root 用户 |
| **运行** | 定期依赖扫描, 密钥轮换 (90 天), SQL 注入检测 |

### 10.7 密钥管理

```
密钥生命周期:
  生成 → 分发 (唯一展示) → 使用 → 轮换 (90 天) → 撤销 → 归档
```

- **API Key:** 生成后仅在响应中展示一次明文，存储 SHA-256 哈希
- **LLM Key:** 从环境变量加载，通过 Kubernetes Secret 注入
- **DB 密码:** 加密存储于 `database_connections` 表 (Fernet, 密钥来自环境变量)

---

## 11. 评估体系

### 11.1 评估指标与目标

| 维度 | 指标 | MVP 目标 |
|------|------|---------|
| 准确率 | SQL 语法正确且语义正确 | >80% |
| Schema 合规 | 引用表/列 100% 存在 | 100% |
| 用户满意度 | 评分 ≥4 (5 分制) | >70% |
| 首候选中选率 | 用户选第一候选 | >60% |
| 零编辑率 | 直接使用不修改 | >50% |
| 执行成功率 | SQL 执行不报错 | >90% |
| 响应时间 | 提交到首 token | <2s |
| 学习提升率 | 相同查询第二次比第一次 | >10% |

### 11.2 评估方法

1. **自动验证集** — 预置 (NL, SQL 标准答案) 测试集，Schema 变更后回归
2. **执行等价性** — 生成 SQL 与标准答案执行结果对比
3. **A/B 测试** — 多候选同时展示，记录选择分布
4. **人工标注** — 定期抽样审核生成质量
5. **EXPLAIN 分析** — 检查低效查询（全表扫描等）

---

## 12. 风险分析与缓解

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| LLM 幻觉生成不存在对象 | SQL 不可用 | 高 | Schema 验证层严格校验 + 重试 |
| 中文时间表达解析不准 | 时间过滤错误 | 高 | 规则优先 + LLM 兜底 + 用户确认 |
| 大 Schema 检索遗漏关键表 | JOIN 不完整 | 中 | 分层策略 + FK 扩展 + 手动指定 |
| 多轮对话上下文漂移 | 偏离原始意图 | 中 | 上下文摘要 + 版本管理 + 可重置 |
| 用户编辑 SQL 理解偏差 | 错误学习 | 中 | 规则人工审核 + 高置信度才生效 |
| LLM API 成本超预期 | 可持续性 | 中 | 多层缓存 + 本地兜底 + 模板匹配 |
| 方言差异处理不当 | 语法错误 | 中 | 方言感知 Prompt + 方言特定验证 |
| Schema 含敏感信息泄露 | 合规风险 | 低 | 脱敏选项 + PII 标记 + 零发送 |
| MCP Server 故障/不可用 | 管道中断 | 中 | 健康检查 + 熔断 + 降级直连 |
| RAG 融合参数不当 | 检索质量下降 | 中 | 动态 α 调节 + A/B 测试 |
| Workflow 路由误判 | 效率低/失败高 | 中 | LLM 兜底 + 降级 + 自适应 |
| MCP 协议版本不兼容 | 工具调用失败 | 低 | 版本协商 + 兼容层 + HTTP 直连 |
| MetricFlow 指标定义错误 | SQL 语义错误 | 中 | Dialect Adapter 验证 + 执行预览 |
| Subagent 上下文膨胀 | LLM 质量下降 | 中 | 摘要 + 归档 + 轮次上限 |
| LanceDB 与 Schema 不同步 | 检索过期信息 | 中 | DDL 监听 + 增量更新 + 版本校验 |
| LiteLLM 提供商不稳定 | 超时/失败 | 中 | Fallback 链 + 本地兜底 + 排队 |
| Orchestrator 任务分解错误 | 结果偏差 | 中 | 多分解策略 + 执行后验证 |
| Agent Handoff 死锁 | 无法产出 | 低 | 最大深度限制 + 超时熔断 + 单 Agent 降级 |
| 多 Agent 成本叠加 | 成本翻倍 | 中 | 按需调用 + 结果缓存 + 轻量级场景少 Agent |
| 过度依赖不审查 SQL | 错误决策 | 低 | 验证报告 + 高亮警告 + 只读默认 |

---

## 13. 项目结构总览

```
data_engineer/
├── main.py                            # 入口: 初始化和启动
├── pyproject.toml                     # 项目配置 + 依赖
├── .env.example                       # 环境变量模板
├── AGENTS.md                          # 全局项目上下文 (注入 200 行到 Prompt)
├── SPEC.md                            # 完整技术规范 (3710 行)
├── docs/
│   └── implementation-plan.md         # 本文档
│
├── app/
│   ├── api/                           # FastAPI 路由 ✅
│   │   ├── __init__.py                # ✅ create_app() 工厂
│   │   ├── health.py                  # ✅ GET /health
│   │   ├── query.py                   # ✅ POST /query + /query/generate + /query/stream
│   │   ├── schema.py                  # ✅ 6 端点 (databases CRUD + schema refresh/search)
│   │   ├── domains.py                 # ✅ 8 端点 (CRUD + glossary + rules + detect)
│   │   ├── feedback.py                # ✅ Phase 4
│   │   ├── learning.py                # ✅ Phase 5
│   │   ├── mcp_endpoints.py           # ✅ Phase 4
│   │   └── workflows.py               # ✅ Phase 4
│   │
│   ├── config/                        # 配置层 ✅
│   │   ├── settings.py                # Pydantic Settings
│   │   ├── agent.yml                  # 声明式 Agent 配置
│   │   ├── litellm.yaml               # ❌ (未创建 — LiteLLM 配置整合在 RouterConfig + agent.yml 中)
│   │   └── domains/                   # 领域 YAML
│   │       └── default/               # ✅ domain + glossary + rules
│   │
│   ├── models/                        # 数据模型 ✅ (11 文件)
│   │   ├── schema.py, query.py, domain.py, feedback.py
│   │   ├── workflow.py, subagent.py, mcp.py, conversation.py
│   │   ├── rag_schema.py, rag_metric.py, rag_document.py
│   │
│   ├── db/                            # 数据库层
│   │   ├── connections.py             # ✅ 4/11 驱动
│   │   ├── schema_extractor.py        # ✅ 3/11 提取器
│   │   ├── dialect_adapter.py         # ✅ 4/11 完整预设
│   │   └── migrations/                # ❌ Phase 5
│   │
│   ├── harness/                       # Harness 运行时 ✅
│   │   ├── runner.py                  # ✅ WorkflowRunner
│   │   ├── config_loader.py           # ✅ Agent 配置加载
│   │   ├── plan_loader.py             # ✅ Plan 模板加载
│   │   ├── permission.py              # ✅ 权限管理
│   │   ├── constraint.py              # ✅ 约束强制执行
│   │   ├── action_history.py          # ✅ 执行追踪
│   │   ├── session.py                 # ✅ Phase 4
│   │   ├── skill_manager.py           # ✅ Phase 4
│   │   └── compaction.py              # ✅ Phase 4
│   │
│   ├── nodes/                         # Node 系统 ✅
│   │   ├── base.py                    # ✅ BaseNode + NodeInput/Output
│   │   ├── registry.py                # ✅ NodeRegistry  
│   │   ├── agentic.py                 # ✅ AgenticNode (7 能力框架, Permission 集成)
│   │   ├── execute_sql.py             # ✅ 完整实现 (含 EXPLAIN 集成)
│   │   ├── schema_linking.py          # ✅ 完整实现 (关键词匹配 + FK 扩展 + SchemaRetriever 集成)
│   │   ├── generate_sql.py            # ✅ 完整实现 (SQLGenerator + 多候选 + 流式)
│   │   ├── validate_sql.py            # ✅ 完整实现 (3 步验证 + 表别名解析)
│   │   ├── parse_nl.py                # ✅ 完整实现 (5 步 NLParser 管道)
│   │   ├── reflection.py              # ✅ (关键词匹配错误分类 + 37 错误模式 + fix_hints)
│   │   ├── hybrid_search.py           # ✅ (4 目标模式 + update_context 便捷键)
│   │   ├── metric_resolve.py          # ✅ (MetricFlow 指标 → SQL)
│   │   ├── explain_plan.py            # ✅ Phase 4
│   │   ├── dialect_translate.py       # ✅ Phase 4
│   │   ├── format_sql.py              # ✅ Phase 4
│   │   └── _template.py               # ✅ Phase 5 (自定义 Node 模板)
│   │
│   ├── core/                          # 核心引擎 ✅ Phase 2
│   │   ├── language_detector.py       # ✅ Unicode CJK 范围检测
│   │   ├── time_parser.py             # ✅ 71 模式 (zh 37 + en 34, L1/L2 双层)
│   │   ├── intent_classifier.py       # ✅ 加权关键词打分 + 优先级决胜, 7 类型
│   │   ├── ambiguity.py               # ✅ 4 类歧义检测 (属性/聚合/范围/时间)
│   │   ├── sqr_builder.py             # ✅ SQR 组装 + 实体提取
│   │   ├── nlp_parser.py              # ✅ 5 步管道 + 置信度评分
│   │   ├── prompt_builder.py          # ✅ L1-L4 模板 + Token 预算管理
│   │   ├── sql_generator.py           # ✅ LiteLLM 路由 + 多候选 + 流式
│   │   ├── sql_validator.py           # ✅ Step 1-3 (sqlglot/Schema/类型)
│   │   └── self_heal.py              # ✅ ErrorAnalysis + 修正 Prompt + max 3 轮
│   │
│   ├── knowledge/                     # 领域知识引擎 ✅ Phase 3
│   │   ├── __init__.py
│   │   ├── domain_manager.py          # ✅ YAML 加载 + 切换 + 3 层自动检测
│   │   ├── glossary.py                # ✅ 术语 CRUD + 匹配 + 映射解析
│   │   ├── rule_engine.py             # ✅ 正则匹配 + 强制执行 + SQL 模板
│   │   ├── evolvable_context.py       # ✅ 4 类知识采集 + 生命周期管理
│   │   ├── schema_retriever.py        # ✅ 5 步分层检索 (分析→RAG→术语→FK→注入)
│   │   ├── metricflow_layer.py        # ✅ 指标 CRUD + query_metrics() + 跨方言 SQL
│   │   └── retrieval/                 # RAG 混合检索引擎
│   │       ├── lancedb_store.py       # ✅ 4 命名空间 (含 query_cache) + CRUD + 向量检索
│   │       ├── embedding.py           # ✅ EmbeddingProvider (OpenAI/BGE/Mock) + EmbeddingGenerator
│   │       ├── bm25_index.py          # ✅ jieba(zh)/whitespace(en) + SQLite FTS5
│   │       ├── rrf_fusion.py          # ✅ k=60 + 动态 α + 加权线性融合备选
│   │       ├── schema_rag.py          # ✅ 表/列索引 + find_relevant_tables()
│   │       ├── metric_rag.py          # ✅ 指标索引 + match_metrics() + suggest_dimensions()
│   │       └── document_store.py      # ✅ Markdown 分块 (≤512 tokens, 64 重叠) + content_type
│   │
│   ├── llm/                           # LiteLLM 路由层 ✅ Phase 2
│   │   └── router.py                  # ✅ RouterConfig + ProviderConfig + 3 层 Fallback + Mock 模式
│   │
│   ├── cache/                         # 查询对缓存 ✅ Phase 3
│   │   ├── __init__.py                # ✅ 导出 CachedQuery, QueryPairCache
│   │   └── query_cache.py             # ✅ L1 SHA256 完全匹配 (LRU) + L2 LanceDB 语义匹配 (cosine ≤ 0.05)
│   │
│   ├── agents/                        # 多 Agent 系统 ✅ Phase 4
│   │   ├── orchestrator.py            # ✅ Orchestrator Agent
│   │   ├── nl_agent.py                # ✅ NL Understanding Agent
│   │   ├── schema_agent.py            # ✅ Schema Retrieval Agent
│   │   ├── sql_agent.py               # ✅ SQL Generation Agent
│   │   ├── validation_agent.py        # ✅ Validation Agent
│   │   ├── tool_agent.py              # ✅ Tool Execution Agent
│   │   ├── feedback_agent.py          # ✅ Feedback Agent
│   │   ├── bus.py                     # ✅ Agent 通信总线
│   │   ├── trace.py                   # ✅ Agent Trace 收集
│   │   └── collaboration.py           # ✅ 5 种协作模式
│   │
│   ├── subagent/                      # Subagent 系统 ✅ Phase 4
│   │   ├── manager.py                 # ✅ 生命周期管理 + 自适应调优
│   │   ├── instance.py                # ✅ Subagent 实例
│   │   ├── router.py                  # ✅ API/Web/MCP 3 通道路由
│   │   └── configs/                   # ✅ Subagent YAML 配置
│   │
│   ├── mcp/                           # MCP 协议层 ✅
│   │   ├── registry.py                # ✅ MCPServerRegistry (5 Server 注册)
│   │   ├── servers/db_server.py       # ✅ DB MCP Server (4 工具 + 2 资源, port 8081)
│   │   ├── servers/knowledge_server.py # ✅ Knowledge MCP Server (3 工具 + 2 资源, port 8082)
│   │   ├── servers/vector_server.py   # ✅ Vector MCP Server (3 工具 + 1 资源, port 8083)
│   │   ├── servers/learning_server.py # ✅ Learning MCP Server (6 工具 + 3 资源, port 8084)
│   │   ├── fastmcp_client.py          # ✅ Phase 4
│   │   ├── fastmcp_server.py          # ✅ Phase 4
│   │   └── transport.py               # ✅ Phase 4
│   │
│   ├── workflow/                      # 工作流编排 ✅
│   │   ├── __init__.py
│   │   ├── plan_selector.py           # ✅ 3 层路由 + 自适应优化
│   │   ├── plans/
│   │   │   ├── gensql_agentic.yml     # ✅ 标准 NL→SQL (4 节点)
│   │   │   ├── ez_query.yml           # ✅ 快速通道 (3 节点, 跳过验证)
│   │   │   ├── reflection.yml         # ✅ 反射式生成 (6 节点, 含反思循环)
│   │   │   ├── chat_agentic.yml       # ✅ 对话式 (5 节点)
│   │   │   ├── explore.yml            # ✅ Schema 探索 (3 节点)
│   │   │   └── metric_query.yml       # ✅ 指标查询 (4 节点, MetricFlow)
│   │   ├── router.py                  # ✅ Phase 5 (自适应优化)
│   │   └── monitor.py                 # ✅ Phase 5
│   │
│   ├── learning/                      # 持续学习 ✅ Phase 5
│   │   ├── __init__.py                # ✅ 包入口 + 单例导出
│   │   ├── feedback_collector.py      # ✅ 反馈收集 (SQLite + 统计/导出)
│   │   ├── query_pair_store.py        # ✅ 查询对存储 (embedding + UPSERT)
│   │   └── pattern_analyzer.py        # ✅ 模式分析 + 候选规则提取
│   │
│   ├── rag/                           # RAG 索引管理 ✅ Phase 5
│   │   ├── __init__.py                # ✅ 包入口
│   │   └── index_refresher.py         # ✅ Schema 哈希版本 + TTL 刷新
│   │
│   ├── core/                          # 核心引擎 ✅
│   │   ├── performance.py             # ✅ Phase 5 (@track_latency + @count_calls)
│   │   └── ...                        # (其余文件见下)
│   │
│   └── web/                           # 前端 ✅ Phase 4
│       ├── streamlit_app.py           # ✅ 多页导航 (含 Rule Review + Dashboard)
│       ├── components/
│       │   ├── query_input.py         # ✅ 查询输入组件
│       │   ├── sql_display.py         # ✅ SQL 展示组件
│       │   ├── result_table.py        # ✅ 结果预览组件
│       │   └── subagent_chat.py       # ✅ Subagent 对话组件
│       └── pages/
│           ├── home.py                # ✅ 主页
│           ├── domain_mgmt.py         # ✅ 领域管理
│           ├── subagent_console.py    # ✅ Subagent 控制台
│           ├── rule_review.py         # ✅ Phase 5 (规则审核)
│           └── dashboard.py           # ✅ Phase 5 (质量仪表板)
│
├── tests/                             # 测试 (2600 全量通过)
│   ├── conftest.py                    # ✅ 共享 fixtures
│   ├── fixtures/
│   │   └── test_schema.sql            # ✅ E-commerce 测试数据 (users, products, orders)
│   ├── test_models/                   # ✅ test_schema.py (24 测试)
│   ├── test_db/                       # ✅ test_connections.py (6 测试)
│   ├── test_nodes/                    # ✅ 7 文件 (186 测试)
│   ├── test_harness/                  # ✅ test_permission.py (53 测试)
│   ├── test_core/                     # ✅ 10 文件 (478 测试)
│   ├── test_llm/                      # ✅ test_router.py (48 测试)
│   ├── test_api/                      # ✅ test_query_api.py (44 测试)
│   ├── test_integration/              # ✅ test_e2e_pipeline.py (77 测试, 8 类)
│   ├── test_knowledge/                # ✅ Phase 3 (~200 测试)
│   ├── test_rag/                      # ✅ Phase 3 (~100 测试)
│   ├── test_subagent/                 # ✅ Phase 4 (~40 测试)
│   ├── test_mcp/                      # ✅ Phase 3 (~30 测试)
│   ├── test_metricflow/               # ✅ Phase 3
│   ├── test_learning/                 # ✅ Phase 5 (70 测试: feedback_collector + query_pair_store + pattern_analyzer)
│   ├── test_web/                      # ✅ Phase 4
│   ├── test_workflow/                 # ✅ Phase 3-5
│   └── e2e/                           # ✅ Phase 5 (7 测试: pipeline + learning_loop)
│
└── scripts/                           # 工具脚本 ❌
    ├── seed_test_data.py
    ├── run_benchmark.py
    └── init_evolvable_context.py
```

**统计:** 63 源文件 (Phase 1) → ~140 (Phase 2) → ~185 (Phase 3) → ~220 (Phase 4) → ~250 (Phase 5) ✅

### 扩展指南

#### 如何添加新数据库适配器

```
3 步流程:
1. app/db/connections.py  → 实现 ConnectionFactory._connect_{db_type}()
2. app/db/schema_extractor.py → 实现 SchemaExtractor._extract_{db_type}()
3. app/db/dialect_adapter.py → 添加 DialectConfig preset
```

**示例 — 添加 ClickHouse:**
1. `pip install clickhouse-connect`
2. `_connect_clickhouse(config)`: return `clickhouse_connect.get_client(**config)`
3. `_extract_clickhouse(conn)`: `SELECT name, ... FROM system.tables WHERE database = currentDatabase()`
4. `DialectConfig(dialect="clickhouse", driver="clickhouse-connect", type_mapping={...})`

#### 如何添加新 Node

```
3 步流程:
1. 继承 BaseNode (无 LLM) 或 AgenticNode (需 LLM)
2. 实现 async def execute(self, input: NodeInput) → NodeOutput
3. 在 app/nodes/registry.py 中注册
```

**示例 — DataProfilerNode:**
```python
class DataProfilerNode(BaseNode):
    name = "data_profiler"
    
    async def execute(self, input: NodeInput) -> NodeOutput:
        conn = input.context["connection"]
        table = input.config.get("table")
        # 统计每个列: COUNT, COUNT DISTINCT, NULL%, MIN, MAX, AVG
        profile = await self._profile_table(conn, table)
        return NodeOutput(result=profile)
```

#### 如何添加新 Agent

```
3 步流程:
1. 在 app/agents/ 中定义 Agent (使用 OpenAI Agents SDK)
2. 注册到 Orchestrator 的 handoff 列表
3. 添加协作模式 (如果需要)
```

**示例 — CostOptimizerAgent:**
```python
cost_agent = Agent(
    name="Cost Optimizer Agent",
    instructions="分析 SQL 查询成本，建议优化策略",
    tools=[estimate_cost, suggest_index, suggest_rewrite],
    model="claude-haiku-4.5",
)
# 在 Orchestrator 中注册
orchestrator.handoffs.append(
    handoff(cost_agent, tool_name_override="optimize_cost")
)
```

---

## 14. 附录

### A. 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 启动服务
python main.py

# 健康检查
curl http://localhost:8000/health

# 连接 SQLite 测试数据库
curl -X POST http://localhost:8000/api/v1/databases/connect \
  -H "Content-Type: application/json" \
  -d '{"db_type": "sqlite", "config": {"path": "tests/fixtures/test_schema.sql"}}'

# 获取 Schema
curl http://localhost:8000/api/v1/schema/sqlite_test_db
```

### B. 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 内部对象 | dataclass | 轻量、快速、无需 ORM |
| API 边界 | Pydantic | 自动校验、序列化、OpenAPI |
| LLM 路由 | LiteLLM | 统一 10+ 提供商接口，模型可替换 |
| MCP 框架 | FastMCP | 声明式 API，比原生 mcp-python 简洁 |
| 向量数据库 | LanceDB | 零配置嵌入式，列式存储，多模态过滤 |
| Embedding 模型 | text-embedding-3-small / BGE-large-zh | 双语覆盖，本地+云端可选 |
| LLM 不决定下一步 | Harness 强制执行 | 可预测、可审计、可复现 |
| Memory 隔离 | 按 node_name 分目录 | 不同节点类型互不干扰 |

### C. 参考文档

- [SPEC.md](../SPEC.md) — 完整技术规范 (3710 行)
- [agent.yml](../app/config/agent.yml) — 声明式 Agent 配置
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [LiteLLM](https://github.com/BerriAI/litellm)
- [LanceDB](https://github.com/lancedb/lancedb)
- [MetricFlow](https://github.com/dbt-labs/metricflow)
- [MetricFlow](https://github.com/dbt-labs/metricflow)
- [sqlglot](https://github.com/tobymao/sqlglot)

---

## 15. 部署与运维 (Deployment & Operations)

### 15.1 容器化 — Docker

```dockerfile
# ── 构建阶段 ──
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv pip compile pyproject.toml -o requirements.txt
RUN uv pip install --no-cache-dir -r requirements.txt --target /deps

# ── 运行阶段 ──
FROM python:3.12-slim AS runtime
WORKDIR /app
RUN useradd -m -u 1000 appuser
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/
COPY app/ ./app/
COPY main.py pyproject.toml ./
USER appuser
EXPOSE 8000 8080
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 15.2 本地开发 — docker-compose

```yaml
version: "3.9"
services:
  app:
    build: .
    ports: ["8000:8000", "8080:8080"]
    volumes:
      - ./app:/app/app                    # 热加载
      - ./app/config/domains:/app/app/config/domains
      - sessions_data:/home/appuser/.data_engineer
    environment:
      - APP_ENV=development
      - DATABASE_URL=postgresql+asyncpg://nl2sql:nl2sql@db:5432/nl2sql
    env_file: .env
    depends_on: [db]

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: nl2sql
      POSTGRES_PASSWORD: nl2sql
      POSTGRES_DB: nl2sql
    volumes: [pg_data:/var/lib/postgresql/data]
    ports: ["5432:5432"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    profiles: ["cache"]                   # 可选

volumes:
  sessions_data:
  pg_data:
```

### 15.3 生产部署拓扑

```
                   ┌──────────────┐
                   │   Nginx/TLS  │  (SSL termination + rate limiting)
                   └──────┬───────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │ uvicorn │ │ uvicorn │ │ uvicorn │   (4 workers)
         │ worker1 │ │ worker2 │ │ worker3 │
         └────┬─────┘ └────┬─────┘ └────┬─────┘
              │            │            │
              └────────────┼────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │PostgreSQL│ │ Redis    │ │ LanceDB  │
        │(主+从)   │ │(缓存)    │ │(嵌入式)  │
        └──────────┘ └──────────┘ └──────────┘
```

### 15.4 Kubernetes 部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nl2sql-agent
spec:
  replicas: 3
  selector: {matchLabels: {app: nl2sql-agent}}
  template:
    metadata: {labels: {app: nl2sql-agent}}
    spec:
      containers:
      - name: app
        image: ghcr.io/org/nl2sql-agent:v1.0.0
        ports: [{containerPort: 8000}, {containerPort: 8080}]
        env:
        - name: APP_ENV
          value: "production"
        - name: DATABASE_URL
          valueFrom: {secretKeyRef: {name: nl2sql-secrets, key: database_url}}
        - name: ANTHROPIC_API_KEY
          valueFrom: {secretKeyRef: {name: nl2sql-secrets, key: anthropic_api_key}}
        resources:
          requests:  {cpu: "500m", memory: "1Gi"}
          limits:    {cpu: "2",    memory: "4Gi"}
        livenessProbe:
          httpGet: {path: /health, port: 8000}
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet: {path: /health/ready, port: 8000}
          initialDelaySeconds: 5
          periodSeconds: 10
        volumeMounts:
        - name: sessions
          mountPath: /home/appuser/.data_engineer
      volumes:
      - name: sessions
        persistentVolumeClaim: {claimName: nl2sql-sessions-pvc}
---
apiVersion: v1
kind: Service
metadata: {name: nl2sql-agent}
spec:
  selector: {app: nl2sql-agent}
  ports:
  - {name: http, port: 8000, targetPort: 8000}
  - {name: metrics, port: 8080, targetPort: 8080}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: nl2sql-agent-hpa}
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: nl2sql-agent}
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource: {name: cpu, target: {type: Utilization, averageUtilization: 70}}
```

### 15.5 环境分层

| 环境 | 数据库 | 缓存 | 日志级别 | 用途 |
|------|--------|------|---------|------|
| **development** | SQLite :memory: | 无 | DEBUG | 本地开发 |
| **staging** | PostgreSQL (单实例) | Redis (可选) | INFO | 集成测试 |
| **production** | PostgreSQL (主从) | Redis Cluster | WARNING | 线上服务 |

配置覆盖策略：

```bash
# agent.{env}.yml 覆盖 base agent.yml
APP_ENV=production → 加载 agent.yml + agent.production.yml (深度合并)
```

### 15.6 数据库迁移 — Alembic

```bash
# 初始设置
alembic init app/db/migrations

# 自动生成迁移
alembic revision --autogenerate -m "add users table"

# 升级/回滚
alembic upgrade head          # 开发/测试环境
alembic upgrade +1            # 生产环境 (逐步)
alembic downgrade -1          # 回滚一步

# CI 检查
alembic check                 # 检测 Schema 漂移 (PR 门禁)
```

迁移文件示例：

```python
# app/db/migrations/versions/20260715_001_initial_schema.py
revision = '20260715_001'
down_revision = None

def upgrade():
    op.create_table('sessions', ...)
    op.create_table('turns', ...)
    # ... 所有 §7 定义的 8 张表

def downgrade():
    op.drop_table('turns')
    op.drop_table('sessions')
```

---

## 16. 可观测性 (Observability)

### 16.1 结构化日志 — structlog

```python
# app/utils/logging.py
import structlog
import uuid

def setup_logging(app_env: str = "development"):
    """配置 structlog — JSON 输出 (生产) 或彩色控制台 (开发)"""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    
    if app_env == "production":
        structlog.configure(
            processors=shared_processors + [
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
    else:
        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(),
            ],
        )

def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

# ── 中间件注入 trace_id ──
from starlette.middleware.base import BaseHTTPMiddleware

class TraceIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = request.headers.get("X-Trace-ID", uuid.uuid4().hex[:16])
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response
```

### 16.2 指标采集 — Prometheus

```python
# app/utils/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# 查询指标
query_total = Counter(
    "nl2sql_query_total", "Total NL queries",
    ["workflow", "status", "domain"]
)
query_duration = Histogram(
    "nl2sql_query_duration_seconds", "Query duration",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30]
)

# LLM 调用指标
llm_calls_total = Counter(
    "nl2sql_llm_calls_total", "Total LLM calls",
    ["model", "provider", "status"]
)
llm_tokens_total = Counter(
    "nl2sql_llm_tokens_total", "Total LLM tokens",
    ["model", "type"]  # type: input / output
)

# 缓存指标
cache_hit_ratio = Gauge("nl2sql_cache_hit_ratio", "Cache hit ratio (0.0-1.0)")
active_sessions = Gauge("nl2sql_active_sessions", "Active sessions")

# 暴露 /metrics (端口 8080, 安全隔离)
def start_metrics_server(port: int = 8080):
    from prometheus_client import start_http_server
    start_http_server(port)
```

### 16.3 健康检查

```python
# app/api/health.py
@router.get("/health")
async def liveness():
    """存活检查 — 进程是否运行"""
    return {"status": "ok", "version": "0.1.0"}

@router.get("/health/ready")
async def readiness():
    """就绪检查 — 依赖是否可用"""
    checks = {}
    try:
        await db.check_connection()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    try:
        await llm_router.ping()
        checks["llm_providers"] = "ok"
    except Exception:
        checks["llm_providers"] = "degraded"
    
    all_ok = all(v == "ok" for v in checks.values())
    status = "ok" if all_ok else "degraded"
    return {"status": status, "checks": checks}
```

### 16.4 告警规则

| 告警 | PromQL | 阈值 | 级别 |
|------|--------|------|------|
| LLM 错误率高 | `rate(nl2sql_llm_calls_total{status="error"}[5m]) / rate(nl2sql_llm_calls_total[5m])` | >0.10 | Critical |
| 查询延迟高 | `histogram_quantile(0.95, rate(nl2sql_query_duration_seconds_bucket[5m]))` | >10s | Warning |
| API 错误率高 | `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])` | >0.05 | Critical |
| 缓存命中率低 | `nl2sql_cache_hit_ratio` | <0.30 | Warning |
| RAG 延迟高 | `histogram_quantile(0.95, rate(nl2sql_rag_latency_seconds_bucket[5m]))` | >1s | Warning |

### 16.5 Grafana 仪表板面板

```
┌─────────────────────────────────────────────────────────────┐
│ Row 1: 概览                                                 │
│ [查询量 (1h)] [查询成功率 (%)] [P95 延迟 (s)] [活跃会话]      │
├─────────────────────────────────────────────────────────────┤
│ Row 2: LLM 成本                                             │
│ [LLM 调用量 (按模型)] [Token 消耗 (按模型)] [成本 ($/h)]      │
├─────────────────────────────────────────────────────────────┤
│ Row 3: 查询性能                                             │
│ [查询延迟分布 (直方图)] [缓存命中率] [RAG 检索延迟]           │
├─────────────────────────────────────────────────────────────┤
│ Row 4: 错误                                                 │
│ [错误率 (5m)] [Top 10 错误消息] [Fallback 触发次数]           │
└─────────────────────────────────────────────────────────────┘
```

---

## 17. CI/CD 与开发生命周期 (CI/CD & Dev Lifecycle)

### 17.1 Pre-commit 钩子

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.9.0
    hooks:
      - id: mypy
        args: [--strict]
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: check-yaml
      - id: check-toml
      - id: end-of-file-fixer
```

### 17.2 GitHub Actions CI 管道

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv pip install -r requirements-dev.txt
      - run: ruff check app/ tests/
      - run: mypy app/ --strict

  test:
    needs: lint
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13"]
        db: [sqlite, postgresql]
    services:
      postgres:
        image: postgres:16-alpine
        env: {POSTGRES_USER: test, POSTGRES_PASSWORD: test, POSTGRES_DB: test}
        options: >-
          --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv pip install -r requirements-dev.txt
      - run: pytest tests/ -v --cov=app --cov-report=xml --cov-fail-under=80
      - uses: codecov/codecov-action@v3
        with: {file: ./coverage.xml}

  security:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install bandit pip-audit
      - run: bandit -r app/ -c pyproject.toml
      - run: pip-audit

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t nl2sql-agent:test .

  deploy:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: docker/build-push-action@v5
        with:
          tags: ghcr.io/org/nl2sql-agent:${{ github.ref_name }}
          push: true
      - run: kubectl set image deployment/nl2sql-agent app=ghcr.io/org/nl2sql-agent:${{ github.ref_name }}
```

### 17.3 发布管理

| 版本类型 | 示例 | 触发条件 |
|---------|------|---------|
| **Major** | v2.0.0 | 破坏性 API 变更, 架构重写 |
| **Minor** | v1.1.0 | 新功能 (新 Node/Agent/数据库适配器) |
| **Patch** | v1.0.1 | Bug 修复, 性能优化, 文档更新 |

Docker 镜像标签策略: `:latest`, `:v1.2.0`, `:v1.2`, `:sha-{7}` (Git SHA)

### 17.4 依赖管理

```toml
# pyproject.toml 中的工具配置
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
strict = true
python_version = "3.12"

[tool.bandit]
exclude_dirs = ["tests"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --cov=app --cov-report=term-missing"
```

---

## 18. 认证与授权 (Authentication & Authorization)

### 18.1 双重认证方式

| 方式 | 适用场景 | 实现 |
|------|---------|------|
| **API Key** | 机器间调用、CLI、CI/CD | `X-API-Key` Header, SHA-256 哈希存储 |
| **JWT/OAuth2** | Web UI 用户登录 | FastAPI OAuth2PasswordBearer, python-jose |

### 18.2 FastAPI 中间件栈

```python
# app/api/__init__.py → create_app()
from starlette.middleware.cors import CORSMiddleware
from app.middleware import TraceIDMiddleware, AuthMiddleware, RateLimitMiddleware

def create_app() -> FastAPI:
    app = FastAPI(title="NL2SQL Agent", version="0.1.0")
    
    # 中间件顺序: 外 → 内 (后添加先执行)
    app.add_middleware(TraceIDMiddleware)        # 1. 关联 ID
    app.add_middleware(CORSMiddleware, ...)       # 2. CORS
    app.add_middleware(AuthMiddleware)            # 3. 认证 (提取用户/角色)
    app.add_middleware(RateLimitMiddleware)       # 4. 限流 (最内层)
    
    # 路由注册
    app.include_router(health_router)
    app.include_router(query_router, dependencies=[Depends(require_role("viewer"))])
    app.include_router(admin_router, dependencies=[Depends(require_role("admin"))])
    return app
```

### 18.3 RBAC 权限矩阵

| 权限 | Admin | Developer | Viewer |
|------|-------|-----------|--------|
| 执行 NL 查询 | ✅ | ✅ | ✅ |
| 编辑领域配置 | ✅ | ✅ | ❌ |
| 管理数据库连接 | ✅ | ✅ | ❌ |
| 查看审计日志 | ✅ | ✅ | ✅ |
| 管理用户/API Key | ✅ | ❌ | ❌ |
| 创建/删除 Subagent | ✅ | ✅ | ❌ |
| 访问管理设置 | ✅ | ❌ | ❌ |

### 18.4 JWT 认证实现

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """从 JWT Token 提取当前用户"""
    payload = jwt.decode(token, SECRET_KEY, algorithms=["RS256"])
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return await user_repo.get(user_id)

def require_role(role: str):
    """依赖注入: 检查角色权限"""
    async def role_checker(user: User = Depends(get_current_user)):
        if user.role != role and user.role != "admin":
            raise HTTPException(status_code=403, detail=f"Requires {role} role")
        return user
    return role_checker
```

### 18.5 API Key 管理

```python
class APIKeyManager:
    """生成/验证/轮换/撤销 API Key"""
    
    @staticmethod
    def generate() -> tuple[str, str]:
        """返回 (明文key, SHA256哈希) — 明文仅显示一次"""
        raw = f"nl2sql_{secrets.token_urlsafe(32)}"
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hashed
    
    async def validate(self, key: str) -> User | None:
        hashed = hashlib.sha256(key.encode()).hexdigest()
        return await db.find_user_by_api_key(hashed)
```

---

## 19. 多租户与资源隔离 (Multi-Tenancy)

### 19.1 租户隔离模型

```
请求 (X-Tenant-ID: "acme_corp")
        │
        ▼
[AuthMiddleware] → 提取 tenant_id → 注入 request.state.tenant_id
        │
        ▼
[所有查询] → WHERE tenant_id = 'acme_corp' (数据隔离)
        │
        ▼
[资源分配] → tenant_configs["acme_corp"] (配额检查)
```

**三种隔离级别:**

| 级别 | 实现 | 适用场景 |
|------|------|---------|
| **逻辑隔离 (MVP)** | `tenant_id` 列 + 查询过滤 | SaaS 起步 |
| **Schema 隔离** | PostgreSQL schema per tenant + `search_path` | 中等规模 |
| **完全隔离 (Enterprise)** | 独立数据库 + 独立连接池 | 大客户/合规 |

### 19.2 资源配额

```yaml
# tenant_configs/acme_corp.yaml
tenant:
  id: "acme_corp"
  name: "ACME Corporation"
  quotas:
    max_concurrent_queries: 10
    max_queries_per_hour: 500
    max_llm_calls_per_day: 2000
    max_result_rows: 10000
    max_sessions: 50
    max_subagents: 5
  features:
    metricflow_enabled: true
    custom_agents_enabled: false
    rag_enabled: true
```

### 19.3 数据隔离实现

```sql
-- 所有核心表增加 tenant_id
ALTER TABLE sessions ADD COLUMN tenant_id VARCHAR(64) NOT NULL;
ALTER TABLE turns ADD COLUMN tenant_id VARCHAR(64) NOT NULL;
ALTER TABLE feedback_log ADD COLUMN tenant_id VARCHAR(64) NOT NULL;

-- 查询自动过滤
CREATE POLICY tenant_isolation ON sessions
    USING (tenant_id = current_setting('app.current_tenant_id'));
```

### 19.4 成本归因

```python
class TenantCostTracker:
    """按租户聚合 LLM 调用成本"""
    
    async def get_tenant_costs(self, tenant_id: str, 
                                from_date: date, to_date: date) -> dict:
        return await db.query("""
            SELECT model, provider,
                   SUM(prompt_tokens) as total_input_tokens,
                   SUM(completion_tokens) as total_output_tokens,
                   SUM(cost_usd) as total_cost_usd
            FROM llm_cost_records
            WHERE tenant_id = $1 AND date BETWEEN $2 AND $3
            GROUP BY model, provider
        """, tenant_id, from_date, to_date)
```

---

## 20. 高可用与灾备 (High Availability & Disaster Recovery)

### 20.1 无状态设计

- **API 层:** 完全无状态，可水平扩展至 N 个 Pod
- **状态下沉:** 所有会话/反馈/配置存储于 PostgreSQL + LanceDB (持久化)
- **会话亲和性:** 不需要 — 每个请求从 PG 加载会话上下文

### 20.2 数据库高可用

```
┌────────────────┐      ┌────────────────┐
│ PostgreSQL     │  WAL │ PostgreSQL     │
│ Primary        │─────►│ Standby (同步)  │
│ (读写)         │      │ (只读, 故障转移) │
└────────┬───────┘      └────────────────┘
         │ failover
         ▼
   Patroni / repmgr 自动提升 Standby → Primary
```

### 20.3 备份策略

| 数据 | 频率 | 工具 | 保留 |
|------|------|------|------|
| PostgreSQL 全量 | 每日 02:00 | `pg_dump -Fc` | 7 天 |
| PostgreSQL WAL | 持续归档 | `archive_command` | 7 天 |
| LanceDB 数据 | 每周日 | `tar czf lancedb_backup.tar.gz` | 4 周 |
| YAML 配置 | 每次变更 | Git 版本控制 | 永久 |
| Session Traces (JSONL) | 每日 | `tar + gzip` | 30 天 |

### 20.4 RPO/RTO 目标

| 指标 | 目标 | 实现方式 |
|------|------|---------|
| **RPO** (Recovery Point Objective) | < 1 小时 | PostgreSQL WAL 持续归档 + 每日全量 |
| **RTO** (Recovery Time Objective) | < 30 分钟 | Patroni 自动故障转移 + 预置 Standby |

### 20.5 灾难恢复流程

```
1. [检测] Prometheus 告警: 数据库不可用
2. [决策] 自动切换 (Patroni) 或手动触发
3. [切换] Standby 提升为 Primary
4. [更新] 应用重新连接新 Primary (连接串自动发现)
5. [恢复] 如果主从都不可用 → 从最新 pg_dump 恢复
6. [验证] 运行健康检查 + 核心 API 冒烟测试
7. [复盘] 记录事件时间线 + 根因分析
```

### 20.6 优雅降级

| 依赖故障 | 降级行为 |
|---------|---------|
| Redis 不可用 | 跳过缓存，直接调用 LLM (成本上升但功能正常) |
| LanceDB 不可用 | 回退到 BM25 纯稀疏检索 (召回率略降) |
| LLM 提供商全部不可用 | 模板匹配 + 缓存兜底 (有限功能) |
| PostgreSQL 不可用 | 只读缓存模式 (仅返回缓存结果) |
