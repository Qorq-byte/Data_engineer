# NL2SQL 数据工程 Agent

**用自然语言向数据库提问，拿到可审查、可校验、可解释的 SQL —— 或者一句明确的「做不到」。**

[English](README.md) · **简体中文** · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

[![MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/checks.yml)
[![Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml/badge.svg?branch=main)](https://github.com/Qorq-byte/Data_engineer/actions/workflows/real-databases.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3DA639.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](app/api/)
[![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933.svg?logo=nodedotjs&logoColor=white)](package.json)
[![SQL: read-only](https://img.shields.io/badge/SQL-read--only-0B7285.svg)](app/security/sql_policy.py)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-4C9A5A.svg)](CONTRIBUTING.md)
[![Security policy](https://img.shields.io/badge/security-policy-4B5563.svg)](SECURITY.md)

一个把自然语言问题翻译成**经过校验的只读 SQL** 的 Agent Harness。模型负责写 SQL；跑哪个节点、
能碰什么、返回多少行，由固定的 YAML 计划决定。每次请求返回的是逐项证据，而不是一个置信度分数 ——
某项检查做不了时，它会直接说明。

<a href="assets/runtime-flow-static.svg"><img src="assets/runtime-flow.svg" width="1200" alt="请求生命周期：入口面、请求绑定、固定 YAML 计划驱动五个节点、四道护栏、条件执行与统一响应契约"></a>

*动态示意图，不是运行记录。[静态版](assets/runtime-flow-static.svg) ·
[图里画了什么](docs/runtime-flow.md)。*

## 特性

- **控制流是确定的。** 五个节点按 `request_query.yml` 的固定顺序执行，节点之间互不调用；某个节点
  失败就中断，不会改道。
- **只读被强制三次。** 单语句 AST 策略、只读驱动会话，以及一条写入路径都没有的 `execute_sql`。
- **给证据，不给置信度。** 每个候选记录结构来源、假设，以及逐项检查的通过 / 未通过 / 未验证；
  `confidence` 按设计恒为 `0`。
- **结构感知的生成。** 表、列、键、注释、业务术语表，以及 BM25 + 向量经 RRF 融合的检索。
- **7 个只读连接器。** SQLite、DuckDB、PostgreSQL、MySQL 在 CI 中验收；ClickHouse、Trino、
  StarRocks 有独立验收。
- **一份契约，三种出口。** Web 工作台、JSON/SSE 接口与 MCP 服务返回同一个 `QueryResponse`。

## 快速开始

需要 **Python 3.12+**、**Node.js 22+**（仅构建前端）和一个可连通的 **MySQL 8.0+** 认证库。

```bash
git clone https://github.com/Qorq-byte/Data_engineer.git
cd Data_engineer

python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt

npm ci --ignore-scripts && npm run build:frontend    # 页面由 frontend_dist/ 提供

cp .env.example .env                                 # JWT_SECRET（32 位以上）、REGISTRATION_TOKEN、ADMIN_USER_IDS
python main.py                                       # http://localhost:8001 · API 文档在 /docs
```

打开 <http://localhost:8001>，用 `.env` 里的邀请码注册，然后在工作台里连接数据库。也可以直接调接口：

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"nl_text":"按月统计收入","database":"<db_id>","execute":false}'
```

没有配置模型 Key 时路由会退化为 mock 模式：响应结构完整，但 SQL 是占位符，校验会标出问题。
没有本机 Ollama 时，在 `.env` 里设 `RAG_EMBEDDING_PROVIDER=mock`。注册、连接、请求字段与响应语义见
[docs/usage.md](docs/usage.md)。

## 文档

详细参考以英文撰写；README 保持中英一一对应。

- [使用说明](docs/usage.md) —— 注册、连接目标库、请求字段、响应证据、其他接口。
- [架构](docs/architecture.md) —— Harness 不变量、分层、目录结构、设计取舍。
- [支持矩阵](docs/support-matrix.md) —— 特性状态、数据库、模型。
- [运行流程](docs/runtime-flow.md) —— 图里声称了什么，以及如何重新生成。
- [查询契约](docs/optimization/query-contract.md) —— 状态码与错误语义。
- [部署与恢复](docs/optimization/immutable-workers.md) —— 固定配置多 worker 的约束。

## 现状与边界

这是一个面向单管理员组的 pre-1.0 MVP。缺口照实写：

- **只有 `request_query` 可执行。** 另外 6 个计划是模板；运行时选计划启动时构造，但请求链路从不调用。
- **没有租户隔离。** 一个管理员组共享一份数据。
- **没有经过校准的准确率、召回率或容量基线。** 合成套件评的是契约匹配，不是业务正确性。
- **云数据库只做了一半。** Snowflake 有连接器但缺真库验收；BigQuery、Redshift、Databricks 只有方言定义。

逐项状态见[支持矩阵](docs/support-matrix.md)。

## 贡献

环境搭建、分支模型、提交约定，以及新增节点 / 计划的骨架代码见 [CONTRIBUTING.md](CONTRIBUTING.md)。
参与即表示同意[行为准则](CODE_OF_CONDUCT.md)。

## 安全

请按 [SECURITY.md](SECURITY.md) 私下报告漏洞，不要在公开 Issue 里贴。密钥不得出现在 Issue、
PR、fixture 或文档中。

## 许可证

[MIT](LICENSE) © 2026 Majiaqi。
