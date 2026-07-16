# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

```bash
# 安装（可编辑模式 + 开发依赖）
pip install -e ".[dev]"

# 启动应用
python main.py                        # FastAPI 监听 :8000，启动时打印初始化步骤

# 测试
pytest                                # 运行全部测试（asyncio_mode=auto）
pytest tests/test_nodes/              # 运行单个目录的测试
pytest -k "test_execute"              # 按关键字筛选测试

# Lint 与类型检查
ruff check .                          # lint（行宽 100，py312，规则 E/F/I/N/W/UP/B/C4/SIM）
mypy app/                             # 严格模式
```

## 架构

**NL2SQL 数据工程 Agent**——自然语言 → 领域感知的 SQL 生成。当前为 MVP（v0.1），架构已完整设计，但除 `ExecuteSQLNode` 外大部分节点仍为桩代码。

### 核心约束：Harness 不变量（最重要的规则）

LLM **绝不**决定下一步工作流走向。`app/workflow/plans/*.yml` 中的计划文件定义了固定的 `node_order`（节点执行顺序）；由 `WorkflowRunner`（`app/harness/runner.py`）驱动顺序执行。每个节点遵循三步生命周期：`setup_input()` → `execute()` → `update_context()`。节点之间互不调用。

### 请求处理流程

```
POST /api/v1/query → PlanLoader 加载工作流计划
  → WorkflowRunner 按 node_order 遍历（例如 schema_linking → generate_sql → validate_sql → execute_sql）
  → 每个节点依次执行：setup → execute → update_context → evaluate
  → ConstraintEnforcer（read_only, max_llm_calls, max_rows）+ PermissionManager（allow/deny/ask）
  → 持久化 WorkflowTrace
```

### 核心分层

| 层 | 目录 | 职责 |
|---|---|---|
| **API** | `app/api/` | FastAPI 工厂函数（`create_app()`），路由涵盖 query、schema、health、domains |
| **Node** | `app/nodes/` | 计算单元。`BaseNode` → `AgenticNode`（LLM 驱动，具备 7 项 Harness 能力）。已实现：`ExecuteSQLNode`，其余为桩代码 |
| **Harness** | `app/harness/` | 运行时引擎：配置加载（热更新）、计划加载、工作流执行器、会话、权限、约束、操作历史 |
| **DB** | `app/db/` | `ConnectionFactory`（11 种数据库，4 种完整实现）、`SchemaExtractor`（按方言内省）、`DialectAdapter`（基于 sqlglot 的 SQL 翻译） |
| **Model** | `app/models/` | 纯 `@dataclass`（非 Pydantic）。涵盖工作流、查询（SQR）、Schema、领域、会话、MCP、RAG、子 Agent |
| **MCP** | `app/mcp/` | FastMCP 双角色：Server（对外暴露 NL2SQL）+ Client（消费 DB 工具）。`db_server` 在 8081 端口暴露 4 个工具 + 2 个资源 |
| **Config** | `app/config/` | `agent.yml`（LLM 提供商、节点映射、约束、技能、记忆）、`settings.py`（pydantic-settings，读取 `.env`）、领域 YAML |
| **Workflow** | `app/workflow/plans/` | YAML 模板：`gensql_agentic.yml`（完整流水线）、`ez_query.yml`（快速通道，跳过校验） |

### 设计决策

- **Python 3.12+**——使用 `match`/`case`、`StrEnum`、`X | None` 联合类型语法
- **Model 使用纯 dataclass**，不用 Pydantic（Pydantic 仅用于 settings）
- **默认安全**：通过关键字黑名单（`_is_write_statement()` 检查 12 种写入关键字）强制只读 SQL；PermissionManager 对未列出的工具默认拒绝
- **多方言**：11 种数据库共享 `TableSchema`/`SchemaSnapshot` 模型；通过 `DialectAdapter` 实现方言特定的提取与执行
- **全局单例**充当依赖注入容器：`node_registry`、`mcp_registry`、`settings`、`ConnectionFactory`（类级别状态）
- **测试使用真实 SQLite**（内存或临时文件），不 mock。`tests/conftest.py` 中的 fixture 提供完整电商 Schema（users、products、orders），数据从 `tests/fixtures/test_schema.sql` 加载
- **声明式配置**：`agent.yml` 和工作流计划均为 YAML；`ConfigLoader` 支持基于文件 mtime 的热更新

### MCP 双角色

本系统同时作为 MCP **Server**（将 NL2SQL 作为工具/资源暴露给外部 AI 客户端）和 MCP **Client**（消费外部的数据库、知识库、向量检索等 MCP 工具）。

### 代码中的实体类型

- **Node（节点）**——计算单元（schema_linking、generate_sql、execute_sql、validate_sql）
- **Workflow Plan（工作流计划）**——YAML 定义的节点序列及评估配置
- **Domain（领域）**——YAML 定义的术语表 + 业务规则 + 术语映射，按领域划分
- **Skill（技能）**——agent.yml 中定义的工具组，受 PermissionManager 管控
- **Subagent（子 Agent）**——按领域隔离的限定范围对话机器人，具有独立上下文和工具链（Phase 2+）
- **MCP Server**——命名的 FastMCP 实例，暴露工具和资源
