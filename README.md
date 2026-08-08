# NL2SQL Data Engineering Agent

基于 LLM 的自然语言 → SQL 数据工程智能体。支持**中英双语**输入，通过 **RAG 混合语义检索**、**Evolvable Context 活知识库**和**持续学习**，将自然语言准确转换为多方言 SQL 并执行。

> 版本: v0.1 · 许可证: MIT · Python ≥ 3.12

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [Web 工作台使用指南](#web-工作台使用指南)
- [API 调用方式](#api-调用方式)
- [配置说明](#配置说明)
- [支持的数据库](#支持的数据库)
- [支持的 LLM](#支持的-llm)
- [项目结构](#项目结构)
- [开发指南](#开发指南)

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🌐 中英双语输入 | 自动检测语言，NL 和术语表支持双语 |
| 🔍 Schema 感知 | 自动读取表结构、注释、外键、类型 |
| 📖 业务术语表 | 业务术语 → SQL 字段/表达式的映射 |
| 💬 多轮对话 | 上下文继承，逐步完善的对话式查询 |
| 🛡️ 只读优先 | 默认只生成 SELECT，写操作需确认 |
| 🔁 多候选输出 | 歧义时展示多个候选 SQL |
| 📊 执行预览 | 生成后自动执行并展示结果预览 |
| 🧠 持续学习 | 反馈打分 + 查询对存储 + 规则提取 + 质量趋势 |
| 🧩 多数据库适配 | SQLite / DuckDB / PostgreSQL / MySQL / Snowflake / StarRocks |
| 🔌 多 LLM 提供商 | OpenAI / Claude / Gemini / DeepSeek / Qwen 等 10+（LiteLLM 统一路由） |
| 🔄 MCP 双重角色 | 既是 MCP Server（对外暴露 NL2SQL），也是 MCP Client（消费外部工具） |
| 🗃️ MySQL 持久化 | Write-Through 模式，用户数据不丢失 |

---

## 系统架构

### 请求流程

```
POST /api/v1/query → PlanLoader → WorkflowRunner (node_order)
  → schema_linking → generate_sql → validate_sql → execute_sql
  → ConstraintEnforcer + PermissionManager
  → WorkflowTrace (持久化)
```

### 核心设计原则

1. **LLM 不决定下一步** — WorkflowRunner 驱动固定的 YAML 节点序列
2. **只读默认** — 所有 SQL 通过关键词黑名单过滤
3. **权限不可绕过** — 每个工具调用都经过 PermissionManager
4. **硬性重试/迭代限制** — 每次执行有最大重试和反思轮次

### 分层架构

| 层 | 目录 | 职责 |
|---|---|---|
| **API** | `app/api/` | FastAPI 路由（12 个模块） |
| **Node** | `app/nodes/` | 计算单元（BaseNode → AgenticNode，12 个内置节点） |
| **Harness** | `app/harness/` | 运行时引擎（配置、计划、运行器、会话、权限） |
| **DB** | `app/db/` | ConnectionFactory、SchemaExtractor、DialectAdapter |
| **Model** | `app/models/` | 纯 `@dataclass` 数据结构 |
| **MCP** | `app/mcp/` | FastMCP 双重角色（server + client） |
| **Config** | `app/config/` | YAML 驱动的 Agent 和工作流配置 |
| **RAG** | `app/rag/` | 混合检索（BM25 + 向量 + RRF 融合） |
| **Learning** | `app/learning/` | 反馈收集、模式分析、查询对存储 |
| **Storage** | `app/storage/` | MySQL 持久化（Write-Through 模式） |

---

## 快速开始

### 环境要求

- Python 3.12+
- （可选）MySQL 8.0+（用于用户数据持久化）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd data_engineer

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"
```

### 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，至少配置 LLM API Key（选择一个即可）
# OPENAI_API_KEY=sk-xxx
# DEEPSEEK_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-xxx
```

> 开发/离线模式：设置 `LLM_MOCK_MODE=1` 可以使用 Mock LLM 不调用真实 API。

### 启动服务

```bash
# 启动 FastAPI 服务（默认端口 8001）
python main.py

# 或使用 uvicorn 直接启动（支持热重载）
uvicorn app.api:create_app --host 0.0.0.0 --port 8001 --factory --reload
```

### 访问页面

| 页面 | 地址 | 用途 |
|------|------|------|
| 落地页 | http://localhost:8001/ | 项目介绍，导航入口 |
| 登入/注册 | http://localhost:8001/login | 创建账号或登入 |
| 工作台 | http://localhost:8001/workspace | 主操作界面 |
| API 文档 | http://localhost:8001/docs | Swagger 交互式文档 |
| ReDoc | http://localhost:8001/redoc | 替代 API 文档 |

---

## Web 工作台使用指南

工作台是主操作界面，左侧为**可悬停展开的侧边栏**，包含 9 个面板：

### 1. 智能查询（ChatPanel）

核心功能，将自然语言转换为 SQL 并执行。

**操作步骤：**
1. 在底部输入框输入自然语言查询（中文或英文）
2. 按 `Enter` 或点击发送按钮
3. 系统逐步执行 Pipeline，实时显示进度
4. 结果展示：SQL 代码、执行结果表格、验证报告

**查询示例：**

| 输入 | 系统处理 |
|------|---------|
| "上个月销售额最高的 10 个产品" | 时间解析 → 聚合 SUM → 关联 products/orders |
| "每个用户的平均客单价" | 术语"客单价" → AVG(orders.amount) |
| "Show me top 5 products by revenue" | 英文识别 → 聚合排序 |

**结果卡片包含：** 原始输入回显、语言/意图/置信度、关联表、生成的 SQL、验证报告（语法/类型/语义）、执行结果表格。

---

### 2. 数据 Schema（SchemaPanel）

浏览已连接数据库的表结构。

- 自动加载已连接数据库列表
- 点击数据库展开完整表结构（表名、注释、列信息、外键关系）
- 支持搜索框关键词筛选表/列

---

### 3. 数据库连接（ConnectPanel）

连接外部数据库，支持 MySQL、PostgreSQL、SQLite、DuckDB 等。

- 填写连接信息（主机、端口、用户名、密码、数据库名）
- 点击"测试连接"验证
- 连接成功后自动提取 Schema

---

### 4. 领域知识（DomainPanel）

管理业务术语和规则。

- **术语表（Glossary）**：业务词汇到 SQL 表达式的映射（如"客单价" → `AVG(orders.amount)`）
- **业务规则（Rules）**：领域特定的 SQL 约束（如金额精度、时区设置）
- 支持创建新领域和添加术语
- 领域自动检测功能

---

### 5. SQL 工作台（WorkbenchPanel）

手动编写和执行 SQL 的开发工具。

- SQL 编辑器（语法高亮）
- 方言选择器（PostgreSQL / MySQL / SQLite / DuckDB 等）
- 执行 / EXPLAIN / 格式化 / 方言翻译 / 验证 功能
- 查询结果表格展示

---

### 6. MCP 服务（McpPanel）

查看已注册的 MCP 服务器列表。

内置 4 个 MCP Server：
| Server | 功能 |
|--------|------|
| `db_server` | 数据库查询、Explain、表列表 |
| `knowledge_server` | 术语搜索、规则匹配 |
| `vector_server` | 混合检索、语义搜索 |
| `learning_server` | 反馈记录、评分提交、规则提取 |

---

### 7. 工作流（WorkflowPanel）

查看工作流模板和执行统计。

**预置 6 个工作流：**

| 工作流 | 节点数 | 用途 |
|--------|--------|------|
| 标准 NL→SQL | 4 | 完整 Pipeline（默认） |
| 快速查询 | 3 | 跳过验证的快速通道 |
| 反射式生成 | 5 | 生成→执行→反思→修正 |
| 多轮对话 | 5 | 增量对话查询 |
| Schema 探索 | 3 | 快速探索数据库 Schema |
| MetricFlow 指标 | 4 | 语义层指标查询 |

**统计面板：** 总执行次数、成功率、平均耗时，基于真实查询数据实时更新。最近执行追踪列表展示每次查询的详情。

---

### 8. 持续学习（LearningPanel）

反馈驱动的规则提取与质量监控。

- **学习统计**：总查询数、反馈记录、平均评分
- **规则候选**：从反馈模式中自动提取的规则，支持人工审核（通过/拒绝）
- **质量趋势**：30 天准确率、查询数、反馈数趋势图

---

### 9. 系统设置（SettingsPanel）

系统配置管理。

- **LLM 设置**：管理 LLM 提供商、API Key、模型选择
- **Harness 设置**：运行时配置
- **数据库设置**：连接管理
- **RAG 检索设置**：重建 Schema 索引、刷新所有 RAG 索引、文档上传
- **系统状态**：API 在线状态、版本号

---

### 页面流

```
落地页 (/) → 点击"开始体验" → 登入/注册 (/login) → 工作台 (/workspace)
```

---

## API 调用方式

除了 Web 界面，也可以直接通过 HTTP API 使用。完整 API 文档见 `http://localhost:8001/docs`。

### 核心查询 API

```bash
# NL → SQL 完整管线
curl -X POST http://localhost:8001/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "nl_text": "上个月销售额最高的10个产品",
    "domain": "ecommerce",
    "dialect": "postgresql",
    "num_candidates": 1,
    "execute": false,
    "validate": true,
    "link_schema": true
  }'
```

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `nl_text` | string | 是 | 自然语言查询文本 |
| `domain` | string | 否 | 领域名称（默认 `default`） |
| `dialect` | string | 否 | SQL 方言（默认 `ansi`） |
| `database` | string | 否 | 数据库标识符 |
| `num_candidates` | int | 否 | SQL 候选数（1-5，默认 1） |
| `execute` | bool | 否 | 是否执行 SQL（默认 false） |
| `validate` | bool | 否 | 是否运行验证（默认 true） |
| `link_schema` | bool | 否 | 是否运行 Schema 链接（默认 true） |

### 流式查询（SSE）

```bash
curl -X POST http://localhost:8001/api/v1/query/stream \
  -H "Content-Type: application/json" \
  -d '{"nl_text": "查询所有用户", "dialect": "postgresql"}'
```

### 其他 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/query` | POST | NL→SQL 完整管线 |
| `/api/v1/query/stream` | POST | SSE 流式查询 |
| `/api/v1/query/generate` | POST | 仅生成 SQL（无执行） |
| `/api/v1/execute` | POST | 直接执行 SQL |
| `/api/v1/databases` | GET | 已连接数据库列表 |
| `/api/v1/databases/connect` | POST | 连接数据库 |
| `/api/v1/schema/{db_id}` | GET | 获取 Schema |
| `/api/v1/domains` | GET | 领域列表 |
| `/api/v1/domains/{id}/glossary` | GET | 术语表 |
| `/api/v1/domains/{id}/rules` | GET | 业务规则 |
| `/api/v1/workflows` | GET | 工作流模板列表 |
| `/api/v1/workflows/stats` | GET | 工作流统计 |
| `/api/v1/workflows/traces` | GET | 执行追踪记录 |
| `/api/v1/learning/stats` | GET | 学习统计 |
| `/api/v1/learning/quality` | GET | 质量趋势 |
| `/api/v1/learning/rule-candidates` | GET | 规则候选 |
| `/api/v1/feedback` | POST | 提交反馈 |
| `/api/v1/auth/login` | POST | 用户登入 |
| `/api/v1/auth/register` | POST | 用户注册 |
| `/api/v1/settings/rag` | GET/PUT | RAG 设置 |
| `/api/v1/settings/llm` | GET/PUT | LLM 设置 |
| `/api/v1/sql/explain` | POST | SQL 解释 |
| `/api/v1/sql/translate` | POST | SQL 方言翻译 |
| `/api/v1/sql/format` | POST | SQL 格式化 |
| `/api/v1/sql/validate` | POST | SQL 验证 |
| `/health` | GET | 健康检查 |

---

## 配置说明

### 环境变量（`.env`）

```bash
# LLM API Keys（至少配置一个）
OPENAI_API_KEY=sk-xxx
DEEPSEEK_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-xxx
GOOGLE_API_KEY=xxx

# DeepSeek 兼容端点（私有部署/代理时覆盖）
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Mock 模式（开发/离线用，不调用真实 LLM）
LLM_MOCK_MODE=1

# RAG 语义检索 — 本地 Ollama 嵌入（无需 API Key）
RAG_EMBEDDING_PROVIDER=ollama       # ollama | openai | bge | mock
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_EMBEDDING_MODEL=embeddinggemma
OLLAMA_EMBEDDING_DIM=768

# MySQL 用户数据库（用于持久化，可选）
AUTH_MYSQL_HOST=127.0.0.1
AUTH_MYSQL_PORT=3306
AUTH_MYSQL_USER=root
AUTH_MYSQL_PASSWORD=
AUTH_MYSQL_DATABASE=nl2sql_auth

# JWT 认证
JWT_SECRET=change-me-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440
```

> **本地语义检索(Ollama):** `RAG_EMBEDDING_PROVIDER=ollama` 时,向量检索由本机
> Ollama 提供(默认模型 `embeddinggemma`,768 维),无需 API Key;首次使用前执行
> `ollama pull embeddinggemma` 并保持 `ollama serve` 运行。切换不同维度的嵌入
> 模型时,系统会自动重建 LanceDB 向量表并重新索引。

### Agent 配置（`app/config/agent.yml`）

YAML 驱动的 Agent 配置，包含：
- LLM 提供商和默认模型
- 默认工作流
- 约束条件（只读模式等）
- 重试和超时设置

### 工作流计划（`app/workflow/plans/`）

每个工作流一个 YAML 文件，定义节点顺序和参数。

---

## 数据存储架构

项目采用**混合存储**模式，所有数据存储于运行服务器的**本机**。多用户通过浏览器访问同一台服务器，共享同一份数据。

### 存储层次

| 存储类型 | 位置 | 内容 | 重启后 |
|---------|------|------|--------|
| **YAML 文件** | `app/config/domains/*.yml` | 领域配置、术语映射、业务规则 | 保留 |
| **JSON 文件** | `data/mcp_stopped_servers.json` | MCP 服务停止状态 | 保留 |
| **JSONL 文件** | `data/traces/YYYYMMDD/*.jsonl` | 工作流执行追踪日志（按天归档） | 保留 |
| **BM25 索引** | `data/bm25.db*` | 关键词检索索引 | 保留 |
| **LanceDB 向量库** | `data/lancedb/*.lance` | 语义向量检索（schema_metadata, metrics） | 保留 |
| **用户头像** | `data/avatars/` | 用户上传头像文件 | 保留 |
| **MySQL 数据库** | 已连接的外部 MySQL 实例 | 领域、术语、规则、查询对、反馈、学习统计 | 保留 |
| **内存** | 服务器进程 RAM | 数据库连接、学习统计缓存、领域缓存 | **丢失** |

### 关键特性

- **Write-Through 持久化** — 大多数 API 端点采用"先写内存，再写 MySQL/文件"模式，保证数据一致性
- **无用户隔离** — 所有用户共享同一份数据（领域、查询历史、学习数据等）
- **内存数据易失** — 数据库连接信息（`ConnectionFactory`）、学习统计缓存（`_learning_store`）在服务器重启后清空，需重新连接数据库
- **MySQL 可选** — 不配置 `AUTH_MYSQL_*` 时，回退到 YAML 文件 + 内存模式

---

## 支持的数据库

| 数据库 | 适配器 | 状态 |
|--------|--------|------|
| SQLite | `sqlite3` | 完整支持 |
| DuckDB | `duckdb` | 完整支持 |
| PostgreSQL | `asyncpg` / `psycopg2` | 完整支持 |
| MySQL | `aiomysql` / `pymysql` | 完整支持 |
| Snowflake | `snowflake-connector` | 计划中 |
| StarRocks | `starrocks` | 计划中 |

---

## 支持的 LLM

通过 LiteLLM 统一路由，支持 10+ 提供商：

OpenAI · Anthropic Claude · Google Gemini · DeepSeek · Qwen · 以及任何 OpenAI 兼容端点

---

## 项目结构

```
data_engineer/
├── app/
│   ├── agents/          # 多 Agent 系统（编排器、理解器、生成器等）
│   ├── api/             # FastAPI 路由（12 个模块）
│   ├── auth/            # 用户认证（JWT + MySQL）
│   ├── cache/           # 查询缓存
│   ├── config/          # YAML 配置（agent、领域、子代理）
│   ├── core/            # 核心引擎（NL 解析、意图分类、SQL 生成/验证）
│   ├── db/              # 数据库连接和 Schema 提取
│   ├── harness/         # 运行时引擎（计划、运行器、权限、约束）
│   ├── knowledge/       # 知识引擎（领域管理、术语表、规则引擎、MetricFlow）
│   ├── learning/        # 持续学习（反馈收集、模式分析、查询对存储）
│   ├── llm/             # LLM 工厂和路由（LiteLLM）
│   ├── mcp/             # MCP 服务器和客户端
│   ├── models/          # 数据模型（dataclass）
│   ├── nodes/           # 计算节点（12 个内置节点）
│   ├── rag/             # RAG 引擎（混合检索、索引刷新）
│   ├── storage/         # MySQL 持久化
│   ├── subagent/        # 子代理封装系统
│   ├── web/             # Streamlit Web UI（备用）
│   └── workflow/        # 工作流计划和选择器
├── data/                # 运行时数据（trace、索引、头像、MCP 状态）
│   ├── avatars/         # 用户头像
│   ├── traces/          # 工作流追踪日志（按天归档）
│   ├── lancedb/         # 向量检索索引
│   └── bm25.db*         # 关键词检索索引
├── docs/                # 文档
├── frontend_design/     # 前端页面（React + Tailwind）
├── scripts/             # 工具脚本
├── tests/               # 测试（单元 + 集成 + E2E）
├── main.py              # 应用入口
├── pyproject.toml       # 项目配置
├── SPEC.md              # 完整技术规格
└── .env.example         # 环境变量模板
```

---

## 开发指南

### 运行测试

```bash
# 全部测试
pytest

# 指定模块
pytest tests/test_api/
pytest tests/test_nodes/

# 带覆盖率
pytest --cov=app --cov-report=html
```

### 代码质量

```bash
# Lint
ruff check .

# 类型检查
mypy app/
```

### 添加新的工作流节点

参考 [node-development.md](docs/node-development.md) 了解如何创建自定义节点并注册到 Pipeline。

### 实现计划

参考 [implementation-plan.md](docs/implementation-plan.md) 了解各阶段实现进度。

---

## License

MIT