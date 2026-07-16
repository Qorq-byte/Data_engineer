# SPEC.md — NL2SQL 数据工程智能体

> **项目名称:** NL2SQL Data Engineering Agent  
> **版本:** v0.1 (MVP)  
> **许可证:** MIT  
> **最后更新:** 2026-07-14

---

## 目录

1. [项目总览](#1-项目总览)
2. [核心术语](#2-核心术语)
3. [系统架构](#3-系统架构)
    - 3.1 [架构总览](#31-架构总览分层)
    - 3.2 [核心设计原则](#32-核心设计原则)
    - 3.3 [MCP 协议集成](#33-mcp-协议集成)
4. [模块详细设计](#4-模块详细设计)
    - 4.1 [NL 解析与查询理解层](#41-nl-解析与查询理解层)
    - 4.2 [领域知识引擎](#42-领域知识引擎)
        - 4.2.4 [RAG 混合检索策略](#424-rag-混合检索策略)
    - 4.3 [Schema 管理与检索](#43-schema-管理与检索)
    - 4.4 [SQL 生成引擎](#44-sql-生成引擎)
    - 4.5 [SQL 验证与执行器](#45-sql-验证与执行器)
    - 4.6 [持续学习系统](#46-持续学习系统)
    - 4.7 [对话与版本管理](#47-对话与版本管理)
    - 4.8 [前端与 API 层](#48-前端与-api-层)
    - 4.9 [选择工作流编排](#49-选择工作流编排)
    - 4.10 [Subagent 封装系统](#410-subagent-封装系统)
    - 4.11 [多 Agent 系统架构](#411-多-agent-系统架构)
5. [数据流](#5-数据流)
6. [API 设计](#6-api-设计)
7. [数据模型与存储](#7-数据模型与存储)
8. [LLM Prompt 策略体系](#8-llm-prompt-策略体系)
9. [安全与隐私](#9-安全与隐私)
10. [评估体系](#10-评估体系)
11. [MVP 路线图](#11-mvp-路线图)
12. [风险与缓解](#12-风险与缓解)

---

## 1. 项目总览

### 1.1 产品愿景

构建一个**数据工程智能体**，通过领域感知推理、RAG 混合语义检索、Evolvable Context 活知识库和持续学习，将自然语言（中/英双语）准确转换为 SQL 语句。

核心路径：`NL → 意图理解 → 语义检索(RAG) → SQL生成(多LLM) → 验证执行(多方言)`  
知识支撑：`Evolvable Context 活知识库(Schema元数据 + 参考SQL + 语义模型 + 业务指标)`  
交付方式：`API / Web UI / Subagent Chatbot / MCP Server`

支持 10+ LLM 提供商（OpenAI / Claude / Gemini / DeepSeek / Qwen 等），内置 SQLite / DuckDB / PostgreSQL / MySQL / Snowflake / StarRocks 等多数据库适配器，并通过 MetricFlow 语义层实现业务指标的统一管理和跨方言 SQL 生成。

### 1.2 核心理念

```
自然语言 → [意图理解] → [RAG混合检索] → [Evolvable Context检索] → [MetricFlow语义层]
    │                                                                    │
    │  ┌────────────────────────────────────────────────────────────┐   │
    │  │ LiteLLM 多LLM路由 (OpenAI/Claude/Gemini/DeepSeek/Qwen...)  │   │
    │  └────────────────────────────────────────────────────────────┘   │
    │                                                                    │
    ▼                                                                    ▼
  SQL生成 → [多方言适配器(SQLite/DuckDB/PG/MySQL/Snowflake/StarRocks)]
    │
    ▼
 [验证 + 执行预览] → 可执行查询
    ↑
 [持续学习 + Evolvable Context 自动演化]
 [Subagent 封装 → API/Web/MCP 交付]
```

### 1.3 关键特性（MVP）

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
| 🧠 持续学习 | 反馈打分 + 查询对存储 + 规则提取 + 定期微调 |
| 🌱 Evolvable Context | 活的知识库，自动捕获 Schema、参考 SQL、语义模型、指标 |
| 📐 MetricFlow 语义层 | 通过 MetricFlow 定义业务指标，生成跨方言标准 SQL |
| 🧩 多数据库适配 | 内置 SQLite/DuckDB + PG/MySQL + Snowflake/StarRocks 适配器 |
| 🔌 多 LLM 提供商 | OpenAI / Claude / Gemini / DeepSeek / Qwen 等 10+（LiteLLM 统一路由） |
| 🤖 Subagent 封装 | 成熟领域封装为 scoped chatbot，通过 API/Web/MCP 交付 |
| 🔄 MCP 双重角色 | 既是 MCP Server（对外暴露 NL2SQL），也是 MCP Client（消费外部工具） |

---

## 2. 核心术语

| 术语 | 定义 |
|------|------|
| **NL Query** | 自然语言查询，用户输入的中/英文问题 |
| **Domain** | 领域（如电商、金融），携带独立的术语表、规则和 Schema 映射 |
| **Term Glossary** | 业务术语表，将业务词汇映射到数据库对象和表达式 |
| **SQL Candidate** | 一次生成的单个 SQL 候选，包含 SQL 文本、置信度、推理说明 |
| **Query Context** | 对话上下文，包含历史问题、SQL、人工编辑差异 |
| **Schema Snapshot** | 数据库 Schema 的快照缓存（表、列、类型、注释、外键、索引） |
| **Query Pair** | (NL 原文, 最终确认 SQL) 的映射对，用于学习和缓存 |
| **Rule** | 从纠正模式中提取的业务规则（如 "营业收入 = 金额 - 退款"） |
| **Execution Plan** | SQL 执行计划的关键指标（扫描行数、JOIN 类型、耗时） |
| **Feedback** | 用户对结果的评分 + 文本反馈 + 编辑 diff |
| **Evolvable Context** | 活的知识库，持续捕获 Schema 元数据、参考 SQL、语义模型、业务指标，自动演化更新 |
| **Semantic Model** | 语义模型层，通过 MetricFlow 定义业务指标（如"营业收入 = SUM(amount - refund)"），生成跨方言 SQL |
| **Metric** | 业务指标，由 MetricFlow 定义（含名称、描述、计算逻辑、维度、时间粒度），是语义层的核心单元 |
| **Subagent** | 将成熟领域封装为 scoped chatbot，拥有独立的上下文和工具配置，通过 API/Web/MCP 交付 |
| **LiteLLM Router** | 统一多 LLM 提供商的调用路由层，支持 OpenAI / Claude / Gemini / DeepSeek / Qwen 等 10+ 模型 |
| **Dialect Adapter** | 数据库方言适配器，屏蔽不同数据库（SQLite / DuckDB / PG / MySQL / Snowflake / StarRocks 等）的 SQL 差异 |
| **WorkflowContext** | Workflow 级别的共享上下文，同一 Workflow 中的所有 Node 共享，不同 Workflow 完全隔离 |
| **AGENTS.md** | 项目根目录的全局上下文文件，前 200 行注入所有 Node 的 Prompt 系统段，文件变更时自动重载 |

---

## 3. 系统架构

### 3.1 架构总览（分层）

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                      交付层 (Delivery Layer)                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │
│  │  Web UI      │  │  REST API    │  │  MCP Server      │  │  Subagent Chatbot    │ │
│  │  (Streamlit) │  │  (FastAPI)   │  │  (对外暴露NL2SQL) │  │  (scoped domain)     │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘  └──────────────────────┘ │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼───────────────────────────────────────────────┐
│  ╔══════════════════════════════════════════════════════════════════════════════════╗  │
│  ║  Harness 控制平面: WorkflowRunner + Workflow Plan 模板 + agent.yml 配置驱动     ║  │
│  ║  LLM 不决定下一步 — Harness 决定。按 node_order 推进，可预测、可审计、可复现      ║  │
│  ╚══════════════════════════════════════════════════════════════════════════════════╝  │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼───────────────────────────────────────────────┐
│  ╔══════════════════════════════════════════════════════════════════════════════════╗  │
│  ║                    MCP 协议层 (FastMCP)                                        ║  │
│  ║  ┌──────────────────────┐  ┌───────────────────────┐  ┌──────────────────────┐ ║  │
│  ║  │ MCP Client           │  │ MCP Server Host       │  │ MCP Tool/Resource   │ ║  │
│  ║  │ (消费外部工具: DB/    │  │ (对外暴露 NL2SQL 为    │  │ Registry             │ ║  │
│  ║  │ Knowledge/Vector)    │  │ MCP 工具供其他应用)     │  │                      │ ║  │
│  ║  └──────────────────────┘  └───────────────────────┘  └──────────────────────┘ ║  │
│  ╚══════════════════════════════════════════════════════════════════════════════════╝  │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼───────────────────────────────────────────────┐
│                        核心引擎层 (Multi-Agent)                                               │
│                                                                                         │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                     Orchestrator Agent (调度器)                                   │  │
│  │  任务分解 → Agent 选择 → 任务分发 → 中间结果汇总 → 冲突裁决 → 最终结果综合        │  │
│  │  基于 OpenAI Agents SDK: handoff / guardrails / tool 编排                          │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│         │              │              │              │              │                  │
│  ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌───▼──────────────┐   │
│  │ NL 理解     │ │ Schema 检索 │ │ SQL 生成   │ │ 验证 Agent │ │ 工具调用 Agent   │   │
│  │ Agent       │ │ Agent      │ │ Agent      │ │            │ │ (MCP 工具网关)   │   │
│  │             │ │            │ │            │ │            │ │                  │   │
│  │ 职责:       │ │ 职责:      │ │ 职责:      │ │ 职责:      │ │ 职责:            │   │
│  │ ·语言检测   │ │ ·RAG检索   │ │ ·Prompt    │ │ ·语法验证  │ │ ·调用 MCP 工具   │   │
│  │ ·时间解析   │ │ ·LanceDB  │ │ ·LiteLLM  │ │ ·Schema   │ │ ·执行 SQL        │   │
│  │ ·意图分类   │ │ ·BM25     │ │ ·多候选    │ │ ·类型检查  │ │ ·获取 Schema     │   │
│  │ ·歧义检测   │ │ ·术语匹配  │ │ ·流式输出  │ │ ·执行计划  │ │ ·检索知识库       │   │
│  │ ·实体提取   │ │ ·规则匹配  │ │ ·自愈重试  │ │ ·业务规则  │ │ ·管理资源订阅     │   │
│  └─────────────┘ └───────────┘ └────────────┘ └────────────┘ └──────────────────┘   │
│         │              │              │              │              │                  │
│         └──────────────┴──────────────┴──────────────┴──────────────┘                  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │            Agent 通信总线 (Agent Bus)                                               │ │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐  │ │
│  │  │ 消息路由   │ │ Handoff    │ │ 状态订阅   │ │ 结果缓存   │ │ 事件广播       │  │ │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                         │
│  ┌──────▼────────────────▼────────────────────────▼──────────────────────────────────┐ │
│  │                   领域知识引擎 (Evolvable Context)                                  │ │
│  │                                                                                     │ │
│  │  ┌─────────────────────────────────────────────────────────────────────────────┐  │ │
│  │  │ RAG 混合检索引擎                                                             │  │ │
│  │  │  ┌───────────┐  ┌───────────┐  ┌────────────────┐  ┌────────────────────┐   │  │ │
│  │  │  │BM25索引   │  │LanceDB    │  │RRF融合         │  │动态 α 权重调节     │   │  │ │
│  │  │  │(稀疏检索) │  │(向量密集)  │  │(排名融合)       │  │(查询抽象度自适应)  │   │  │ │
│  │  │  └───────────┘  └───────────┘  └────────────────┘  └────────────────────┘   │  │ │
│  │  └─────────────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                                     │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────────┐    │ │
│  │  │Schema    │ │术语表    │ │历史查询库 │ │ 语义模型      │ │ MetricFlow       │    │ │
│  │  │管理器    │ │管理器    │ │(Pair库)   │ │ (Semantic     │ │ 指标定义/解析器  │    │ │
│  │  │          │ │          │ │           │ │  Model)       │ │                  │    │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘ └──────────────────┘    │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │              持续学习 + Evolvable Context 演化引擎                                  │ │
│  │  反馈收集 → 模式分析 → 规则提取 → 上下文自动演化 → 语义模型更新 → 周期性指标校准   │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │            选择工作流编排引擎                                                       │ │
│  │  复杂度分析 → 路由决策 → 工作流模板匹配 → 动态管道组装 (含 Subagent 路由)          │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │            LiteLLM 多 LLM 路由层                                                   │ │
│  │  OpenAI │ Claude │ Gemini │ DeepSeek │ Qwen │ Mistral │ Cohere │ Groq │ ... 10+  │ │
│  │  统一接口: 模型路由 / 退避重试 / 成本跟踪 / 流式 / 多候选                          │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────▼───────────────────────────────────────────────┐
│                     数据源连接层 (MCP Client + Dialect Adapters) — 11 种内置适配器       │
│                                                                                         │
│  ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐  │
│  │ SQLite     │ │ DuckDB   │ │ PostgreSQL │ │  MySQL   │ │ Snowflake  │ │StarRocks │  │
│  │(内置)      │ │(内置)    │ │ (主力)     │ │ (主力)   │ │            │ │          │  │
│  └────────────┘ └──────────┘ └────────────┘ └──────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────────────────┐  │
│  │ BigQuery   │ │ Redshift │ │ ClickHouse │ │Databricks│ │  Trino (via sqlglot)     │  │
│  └────────────┘ └──────────┘ └────────────┘ └──────────┘ └──────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │ Dialect Adapter 层: sqlglot 方言转换 / 类型映射(11套) / 函数映射 / 语法修正       │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 核心设计原则

1. **Pipeline 式处理** — NL → 理解 → 检索 → 生成 → 验证，每个阶段可独立改进
2. **流式优先** — LLM 输出流式传输到前端，减少用户等待焦虑
3. **多层缓存** — 查询对缓存 → Schema 缓存 → Embedding 缓存 → LLM 结果缓存
4. **松耦合领域** — 每个领域独立管理术语表、规则、语义模型和 Schema 映射
5. **可观测性** — 每次请求记录完整 trace（输入、候选、用户操作、反馈）
6. **MCP 标准化** — 通过 MCP 协议统一数据源连接和工具暴露，MCP Client 消费 + MCP Server 输出
7. **选择工作流** — 按查询复杂度、领域和用户角色路由到不同的处理管道
8. **RAG 混合检索** — BM25 稀疏 + 向量密集 + RRF 融合，兼顾精确匹配和语义泛化
9. **Evolvable Context** — 知识库持续自动演化，Schema 变更、用户反馈、新指标均触发上下文更新
10. **LLM 中性** — 通过 LiteLLM 统一 10+ 模型提供商，模型可替换、可路由、可降级
11. **方言适配** — 通过 Dialect Adapter 层屏蔽数据库差异，核心生成逻辑与方言解耦
12. **Subagent 优先** — 成熟领域封装为独立 Subagent，拥有隔离的上下文和工具链，通过多种渠道交付
13. **Harness 控制平面** — 四层架构（Workflow 编排 + WorkflowRunner 引擎 + Node 执行 + agent.yml 配置驱动）。LLM 不决定下一步——Harness 决定。执行路径可预测、可审计、可复现
14. **多 Agent 协作** — 核心 Pipeline 的每个阶段封装为独立 AI Agent，由 Orchestrator Agent 统一调度，支持 Agent 间通信和 Handoff

---

### 3.3 MCP 协议集成 (FastMCP)

#### 3.3.1 概述

MCP (Model Context Protocol) 是 Anthropic 推出的开放协议，标准化了 LLM 与外部工具、数据源之间的通信方式。本系统**既是 MCP Client，也是 MCP Server**，形成完整的双向 MCP 架构：

| 角色 | 方向 | 说明 |
|------|------|------|
| **MCP Client** | 消费外部服务 | 连接 Database MCP Server、Knowledge MCP Server、Vector MCP Server 等 |
| **MCP Server** | 对外提供服务 | 将系统的 NL2SQL 能力暴露为 MCP 工具，供 Claude Desktop、其他 Agent 或应用调用 |

实现基于 **FastMCP** 框架，它提供了比原生 mcp-python SDK 更简洁的声明式 API。

#### 3.3.2 架构定位

```
┌──────────────┐     ┌───────────────────────────────────────────┐
│  LLM 模型     │◄───►│            MCP Host (本系统)              │
│  (Claude/    │     │  ┌──────────┐  ┌───────────────────────┐  │
│   GPT等)     │     │  │MCP Client│  │ MCP Resource Manager  │  │
└──────────────┘     │  └─────┬────┘  └──────────┬────────────┘  │
                     │        │                   │                │
                     └────────┼───────────────────┼────────────────┘
                              │                   │
                     ┌────────▼───────────────────▼────────────────┐
                     │             MCP 服务器层                      │
                     │                                              │
                     │  ┌──────────────────┐  ┌──────────────────┐ │
                     │  │ Database MCP     │  │ Knowledge MCP    │ │
                     │  │ Server           │  │ Server           │ │
                     │  │                  │  │                  │ │
                     │  │ 资源: schemas:// │  │ 资源:            │ │
                     │  │ 工具:            │  │   glossary://    │ │
                     │  │  - query         │  │   rules://       │ │
                     │  │  - explain       │  │   history://     │ │
                     │  │  - list_tables   │  │ 工具:            │ │
                     │  │  - describe_table│  │   search_terms   │ │
                     │  └──────────────────┘  │   match_rules    │ │
                     │                         └──────────────────┘ │
                     │  ┌──────────────────┐  ┌──────────────────┐ │
                     │  │ Vector Store MCP │  │ Learning MCP     │ │
                     │  │ Server           │  │ Server           │ │
                     │  │ 资源:            │  │                  │ │
                     │  │   embeddings://  │  │ 工具:            │ │
                     │  │ 工具:            │  │   record_feedback│ │
                     │  │   hybrid_search  │  │   submit_rating  │ │
                     │  │   knn_search     │  │   extract_rule   │ │
                     │  └──────────────────┘  └──────────────────┘ │
                     └──────────────────────────────────────────────┘
```

#### 3.3.3 MCP 资源模型

| Resource URI | 内容 | 更新策略 |
|-------------|------|---------|
| `schemas://{db_id}/tables` | 数据库所有表列表及注释 | Schema 刷新时更新 |
| `schemas://{db_id}/tables/{table}` | 单表的完整列定义、索引、外键 | 同上 |
| `glossary://{domain_id}/terms` | 领域术语表全量 | 术语变更时更新 |
| `glossary://{domain_id}/terms/{term}` | 单条术语映射 | 同上 |
| `rules://{domain_id}/` | 领域业务规则集 | 规则审核通过后更新 |
| `history://{domain_id}/pairs` | 历史查询对（精选） | 异步更新 |
| `context://sessions/{id}` | 当前对话上下文摘要 | 每轮对话后更新 |

#### 3.3.4 MCP 工具定义

```python
# 数据库查询工具
{
    "name": "execute_read_query",
    "description": "对数据库执行只读 SELECT 查询，返回结果集",
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "只读 SQL 语句"},
            "max_rows": {"type": "integer", "default": 100}
        },
        "required": ["sql"]
    }
}

# Schema 探索工具
{
    "name": "search_schema",
    "description": "在数据库 Schema 中搜索相关表和列",
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["keyword", "semantic", "hybrid"]}
        },
        "required": ["keywords"]
    }
}

# 术语查询工具
{
    "name": "lookup_term",
    "description": "查找业务术语的 SQL 映射定义",
    "input_schema": {
        "type": "object",
        "properties": {
            "term": {"type": "string"},
            "language": {"type": "string", "enum": ["zh", "en"]}
        },
        "required": ["term"]
    }
}

# 执行计划分析工具
{
    "name": "analyze_plan",
    "description": "获取 SQL 的执行计划并分析潜在问题",
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string"}
        },
        "required": ["sql"]
    }
}
```

#### 3.3.5 MCP 的工作方式

1. **启动时** — 系统连接各个 MCP Server，获取可用工具和资源列表
2. **查询时** — LLM 请求通过 MCP Host 路由：
   - 需要 Schema → 请求 `schemas://` 资源
   - 需要查询数据 → 调用 `execute_read_query` 工具
   - 需要术语 → 调用 `lookup_term` 工具
3. **资源订阅** — 关键资源（如 Schema）支持订阅模式，变更时主动通知 Host
4. **LLM 原生交互** — 支持 MCP 的 LLM（如 Claude）可直接发现和使用这些工具，无需额外集成层

#### 3.3.6 MCP 对 NL2SQL 的价值

| 价值 | 说明 |
|------|------|
| **标准化** | 不同数据库类型统一接口，新增数据库只需实现 MCP Server |
| **安全性** | MCP Server 可实施细粒度权限控制（只读/写分离） |
| **可组合** | 可以灵活组合 Database MCP + Knowledge MCP + Vector MCP |
| **LLM 原生** | 支持 MCP 的 LLM 直接使用工具，减少定制代码 |
| **资源热更新** | Schema 变化时 MCP Server 推送通知，缓存自动失效 |
| **双向架构** | 既是 Server 对外暴露 NL2SQL，又是 Client 消费数据库/知识服务 |

#### 3.3.7 MCP Server（对外暴露 NL2SQL 能力）

除了作为 Client 消费外部服务，本系统本身也是一个 **MCP Server**，将 NL2SQL 能力暴露给其他应用：

```
┌──────────────────────┐     MCP 协议     ┌──────────────────────────┐
│ Claude Desktop       │ ◄──────────────► │ 本系统 NL2SQL MCP Server │
│ 或其他 MCP Host      │                  │                          │
│                      │                  │ 暴露工具:                │
│                      │                  │  - nl_query              │
│                      │                  │  - explain_sql           │
│                      │                  │  - search_schema         │
│                      │                  │  - list_domains          │
│                      │                  │                          │
│                      │                  │ 暴露资源:                │
│                      │                  │  - nlsql://schema/       │
│                      │                  │  - nlsql://domains/      │
│                      │                  │  - nlsql://history/      │
│                      │                  │  - nlsql://subagents/    │
│                      └──────────────────┘                          │
```

**对外暴露的 MCP 工具：**

| 工具名 | 功能 | 输入 | 输出 |
|--------|------|------|------|
| `nl_query` | NL 转 SQL 并执行 | `{nl_text, domain?, db_id?}` | `{sql, columns, rows, explanation}` |
| `explain_sql` | 解释 SQL 逻辑 | `{sql}` | `{explanation_nl, steps[]}` |
| `search_schema` | 搜索数据库 Schema | `{keywords, db_id}` | `{tables[], columns[]}` |
| `list_domains` | 列出可用领域 | `{}` | `{domains[]}` |
| `list_subagents` | 列出可用 Subagent | `{}` | `{subagents[]}` |

**FastMCP 实现示例：**

```python
from fastmcp import FastMCP

mcp = FastMCP("NL2SQL Agent")

@mcp.tool()
def nl_query(nl_text: str, domain: str = None, db_id: str = None) -> dict:
    """将自然语言转换为 SQL 并返回执行结果"""
    sqr = nlp_parser.parse(nl_text)
    context = evolvable_context.retrieve(sqr, domain)
    sql = sql_generator.generate(sqr, context, db_id)
    result = sql_executor.execute_read(sql, db_id)
    return {"sql": sql, "columns": result.columns, "rows": result.rows}

@mcp.resource("nlsql://schema/{db_id}")
def get_schema_resource(db_id: str) -> str:
    """获取数据库 Schema 描述"""
    schema = schema_manager.get_snapshot(db_id)
    return schema.format_for_llm()

mcp.run()  # 启动 MCP Server (stdio 或 SSE)
```

#### 3.3.8 MCP 双向架构总结

```
                   ┌───────────────────────────────────┐
                   │       外部系统 / Claude Desktop     │
                   └──────────────┬────────────────────┘
                                  │ MCP 协议
                                  ▼
                   ┌───────────────────────────────────┐
                   │                                   │
                   │      ┌──── MCP Server ──────┐     │
                   │      │  对外暴露 NL2SQL 工具  │     │
                   │      └──────────────────────┘     │
                   │                                   │
                   │        本系统 (NL2SQL Agent)      │
                   │                                   │
                   │      ┌──── MCP Client ──────┐     │
                   │      │  消费数据库/知识服务    │     │
                   │      └──────────────────────┘     │
                   │                                   │
                   └───────────────────────────────────┘
                                  │ MCP 协议
                                  ▼
         ┌───────────────────────────────────────────────────┐
         │   Database MCP  │  Knowledge MCP  │  Vector MCP  │
         └───────────────────────────────────────────────────┘
```

这种双向架构使本系统既能**集成外部数据源**（作为 Client），也能**被其他系统集成**（作为 Server），成为数据生态中的一个标准化节点。

---

### 3.4 Harness 运行时框架

#### 3.4.1 概述

Harness 是本系统的**运行时控制平面**。它通过四层架构确保 LLM 的行为被严格约束在预测、可审计、可复现的框架之内。核心设计哲学：**LLM 不决定下一步做什么——Harness 决定**。

```
┌─────────────────────────────────────────────────────────────────┐
│                        Harness 四层架构                           │
│                                                                   │
│  Layer 1: Workflow 编排层                                         │
│      workflow.yml Plan 模板 → DAG 拓扑序                          │
│        (reflection / chat_agentic / gensql_agentic / ...)        │
│                          │                                        │
│                          ▼                                        │
│  Layer 2: WorkflowRunner 执行引擎                                 │
│      按 node_order 推进 → evaluate_result 评估 → 失败处理        │
│      轨迹持久化 → 生命周期管理（初始化→推进→完成→清理）          │
│                          │                                        │
│                          ▼                                        │
│  Layer 3: Node 执行层                                             │
│      BaseNode (execute/setup_input/update_context)                 │
│          ├── AgenticNode (session/tool/permission/skill/          │
│          │               compaction/streaming/ActionHistory)      │
│          └── PlainNode (无 LLM 节点)                              │
│                          │                                        │
│                          ▼                                        │
│  Layer 4: 配置驱动层                                              │
│      agent.yml 声明式配置 (provider/node/model/tool/permission    │
│      /skill/mode)，LLM 不具备突破 harness 约束的能力              │
└─────────────────────────────────────────────────────────────────┘
```

#### 3.4.2 Layer 1 — Workflow 编排层

Workflow 通过 `workflow.yml` 声明的 **Plan 模板**定义 DAG 拓扑序，而非让 LLM 自由决定跳转。

**预置 Plan 模板：**

| 模板 ID | 名称 | DAG 拓扑 | 适用场景 |
|---------|------|---------|---------|
| `reflection` | 反射式生成 | generate → execute → reflect → [pass? → output : → revise → execute...] | 需要执行验证的复杂查询 |
| `chat_agentic` | 对话式 | understand → retrieve → generate → validate → respond | 多轮对话中的增量查询 |
| `gensql_agentic` | 直接生成 | schema_linking → generate_sql → validate_sql → execute_sql | 标准 NL→SQL，单轮 |
| `explore` | Schema 探索 | parse_nl → hybrid_search → format → respond | 探索数据库结构 |
| `metric_query` | 指标查询 | metric_resolve → generate_sql → validate_sql → execute_sql | MetricFlow 驱动的指标查询 |
| `ez_query` | 快速查询 | schema_linking → generate_sql → execute_sql | 简单查询，跳过验证 |

**workflow.yml 示例（gensql_agentic 模板）：**

```yaml
# workflows/gensql_agentic.yml
id: gensql_agentic
name: "标准 NL→SQL 生成"
description: "单轮自然语言查询转 SQL 执行"

plan:
  node_order:
    - id: link
      node: schema_linking
      on_failure: abort

    - id: gen
      node: generate_sql
      depends_on: [link]
      config:
        temperature: 0.3
        max_candidates: 1

    - id: validate
      node: validate_sql
      depends_on: [gen]

    - id: exec
      node: execute_sql
      depends_on: [validate]
      config:
        max_rows: 100
        timeout_ms: 30000

  # 失败处理策略
  failure_policy:
    max_retries: 1
    on_exhausted: abort  # abort | skip | fallback

  # 质量评估
  evaluation:
    enabled: true
    metrics: [syntax_valid, schema_compliant, execution_success]
```

**reflection 模板（含反思循环）：**

```yaml
# workflows/reflection.yml
id: reflection
name: "反射式生成（生成→执行→反思→修正）"
description: "适合需要执行验证来确保正确性的复杂查询"

plan:
  node_order:
    - id: link
      node: schema_linking

    - id: gen
      node: generate_sql
      depends_on: [link]

    - id: exec
      node: execute_sql
      depends_on: [gen]
      on_failure: skip  # 执行失败不中止，进入反思

    - id: reflect
      node: reflection
      depends_on: [gen, exec]
      config:
        reflection_prompt: "检查以下 SQL 是否正确..."
      on_pass: output
      on_fail: revise

    - id: revise
      node: generate_sql
      depends_on: [reflect]
      config:
        mode: self_heal
        previous_errors: "{{reflect.errors}}"
      # 修正后重新执行
      on_complete: goto_exec

  max_iterations: 3           # 反思循环最大轮次，防止无限循环
  on_exhausted: force_output  # 达到上限后强制执行 reasoning 给出最佳答案，或终止
```

#### 3.4.3 Layer 2 — WorkflowRunner 执行引擎

WorkflowRunner 是 Harness 的执行引擎，负责工作流的完整生命周期管理。

```python
class WorkflowRunner:
    """Harness 执行引擎——按 node_order 推进，不靠 LLM 跳转"""

    def __init__(self, plan: WorkflowPlan, node_registry: NodeRegistry):
        self.plan = plan
        self.node_registry = node_registry
        self.action_history = ActionHistoryManager()
        self.trace = WorkflowTrace()

    async def run(self, input_text: str, session_id: str = None) -> WorkflowResult:
        """执行完整工作流生命周期"""

        # 1. 初始化
        shared_context = {"query_text": input_text, "session_id": session_id}
        node_index = 0
        iteration = 0

        # 2. 按 node_order 顺序推进（非 LLM 自由跳转）
        while node_index < len(self.plan.node_order):
            if iteration >= self.plan.max_iterations:
                break

            node_def = self.plan.node_order[node_index]
            node = self.node_registry.get(node_def.node)

            # 3. 执行节点
            raw_input = {**shared_context, "config": node_def.config}
            node_input = await node.setup_input(raw_input)
            output = await node.execute(node_input)

            # 4. 质量评估
            evaluation = await self.evaluate_result(node_def, output)
            self.trace.record(node_def.id, output, evaluation)

            # 5. 失败处理
            if not evaluation.passed:
                if self.plan.failure_policy.can_retry(node_def, self.trace):
                    continue  # 重试当前节点
                elif node_def.on_failure == "abort":
                    return WorkflowResult(status="failed", trace=self.trace)
                elif node_def.on_failure == "skip":
                    node_index += 1
                    continue
            elif node_def.id == "reflect" and evaluation.needs_revision:
                node_index = self._find_node_index("revise")  # 跳转到修正节点

            # 6. 更新共享上下文
            shared_context = await node.update_context(output, shared_context)
            node_index += 1
            iteration += 1

        # 7. 轨迹持久化
        await self.trace.persist()
        return WorkflowResult(status="completed", context=shared_context, trace=self.trace)

    async def evaluate_result(self, node_def: NodeDef, output: NodeOutput) -> Evaluation:
        """对节点输出进行质量评估"""
        if not self.plan.evaluation.enabled:
            return Evaluation(passed=True)
        results = {}
        for metric in self.plan.evaluation.metrics:
            checker = METRIC_CHECKERS[metric]
            results[metric] = await checker.check(output)
        return Evaluation(
            passed=all(r.passed for r in results.values()),
            metrics=results
        )
```

**WorkflowRunner 的关键约束：**
- **按 `node_order` 顺序推进**，不依赖 LLM 的"判断"——无论 LLM 返回什么，下一步由 Plan 决定
- **evaluate_result** 在每个节点执行后自动评估质量，不合格则触发失败策略
- **轨迹持久化**：每个节点的输入/输出/评估结果全部记录，支持回放和审计
- **max_iterations** 硬上限防止无限循环（如 reflection 反复修正）

#### 3.4.4 Layer 3 — Node 执行层

详见 [§4.12 内置节点系统](#412-内置节点系统-built-in-node-system)，核心要点：

```
BaseNode
├── execute()           ← 必须实现
├── setup_input()       ← 内置（Schema校验+默认值+类型转换）
└── update_context()    ← 内置（写入共享上下文）

AgenticNode extends BaseNode
├── ① AdvancedSQLiteSession
├── ② Tool 集成 (func tools + MCP)
├── ③ PermissionManager (allow/deny/ask)
├── ④ SkillManager + SkillFuncTool
├── ⑤ ActionHistoryManager
├── ⑥ Auto-compaction (90% token 阈值)
└── ⑦ Streaming (SSE)
```

#### 3.4.5 Layer 4 — 配置驱动层（agent.yml）

```yaml
# agent.yml —— 声明式配置，LLM 无法突破 harness 约束
agent:
  name: "NL2SQL Agent"
  version: "1.0.0"

  # LLM 提供商
  provider:
    default: claude-sonnet-4
    fallback: [gpt-4.1, deepseek-v3]
    litellm_config: "../config/litellm.yaml"

  # 节点模型映射
  nodes:
    schema_linking:
      model: claude-haiku-4.5
      tools: [hybrid_search, lookup_term]
      permissions:
        hybrid_search: allow
        lookup_term: allow
      skills: [schema_explorer]

    generate_sql:
      model: claude-sonnet-4
      tools: [build_prompt, call_llm]
      permissions:
        call_llm: ask  # 弹窗确认
      streaming: true

    execute_sql:
      model: none  # 非 LLM 节点
      tools: [execute_read_query]
      permissions:
        execute_read_query: allow
        execute_write_query: deny  # 写操作默认拒绝

    reflection:
      model: claude-opus-4-8
      tools: [analyze_error, compare_results]
      permissions:
        analyze_error: allow

  # 技能系统
  skills:
    mode: auto           # auto | manual
    directory: "./skills"
    preload: [schema_explorer, sql_reviewer]

  # 工作流绑定
  workflow:
    default: gensql_agentic
    auto_select: true    # 根据查询复杂度自动选择模板

  # Memory 策略（按节点类型声明）
  memory:
    # 内置 subagent 默认不启用 memory——聚焦单次任务，不受历史干扰
    builtin_subagents:
      gen_sql: false
      schema_linking: false
      validate_sql: false
      execute_sql: false
      reflection: false

    # chat 节点默认启用 memory
    chat: true

    # 用户自定义 subagent 默认启用
    user_subagents:
      default: true

    # 存储模式：:memory: (ephemeral) 或路径 (持久化)
    storage:
      subagent: ":memory:"                    # Sub-agent 使用 ephemeral，零持久化开销
      top_level: "~/.data_engineer/sessions/" # 顶层交互持久化

    # Memory 按 node_name 隔离——不同节点类型的 memory 目录独立
    isolation: node_name                      # gen_sql 和 chat 的 memory 互不干扰

  # AGENTS.md 作为全局项目上下文（所有节点共享）
  global_context:
    source: "AGENTS.md"          # 项目根目录的 AGENTS.md
    inject_lines: 200            # 前 200 行注入 Prompt
    auto_reload: true            # 文件变更时自动重新加载

  # 全局约束（Harness 强制，LLM 不可绕过）
  constraints:
    max_llm_calls_per_query: 10
    max_node_retries: 3
    reflection_max_rounds: 3     # Reflection 最大轮次，防止无限循环
    reflection_on_exhausted: force_output  # 达到上限后强制执行 reasoning 或终止
    session_ttl_seconds: 3600
    read_only: true
    max_result_rows: 1000
    statement_timeout_ms: 30000
```

**配置驱动的核心原则：**
- 运行时行为完全由 `agent.yml` 决定，代码中无硬编码行为
- `permissions` 的三级管控（`allow`/`deny`/`ask`）由 Harness 强制执行
- `constraints` 是全局护栏，LLM **不具备**突破这些约束的能力——例如 `read_only: true` 时，即使 LLM 生成了 `DROP TABLE`，ExecuteSQLNode 也会拒绝执行
- 修改配置不需要重新部署代码——重启或热加载即可生效

#### 3.4.6 Harness 的不变量（Hard Invariants）

| 不变量 | 说明 |
|--------|------|
| LLM 不决定下一步 | 执行路径由 `node_order` 决定，LLM 输出只影响当前节点的结果质量 |
| 权限不可绕过 | `agent.yml` 中 `permissions: {X: deny}` 的工具，LLM 调用必定失败 |
| 只读优先 | `constraints.read_only: true` 时，任何写操作 SQL 都会在 ExecuteSQLNode 层被拦截 |
| 执行可审计 | 每个节点的 I/O 均持久化到 Trace，可完整回放 |
| 次数有上限 | `max_llm_calls_per_query` + `max_iterations` 硬限制，防止无限循环 |

#### 3.4.7 Harness 与 Agent / MCP 的分层关系

```
┌──────────────────────────────────────────────────────────────────────┐
│  Harness (控制平面)                                                   │
│                                                                       │
│  Agent 层 (智能执行)                                                   │
│  ┌───────────────────────────────────────────────────────────┐       │
│  │  Agent = Harness 管理的执行单元                             │       │
│  │  Agent 调用 Node (通过 NodeRegistry)                       │       │
│  │  Agent 不决定 "下一步做什么" —— 那是 WorkflowRunner 的职责  │       │
│  └───────────────────────────────────────────────────────────┘       │
│                                                                       │
│  Node 层 (原子能力，详见 §4.12)                                       │
│  ┌───────────────────────────────────────────────────────────┐       │
│  │  BaseNode → AgenticNode → 具体 Node                        │       │
│  │  Node 调用 MCP 工具 (通过 Tool Execution Agent)             │       │
│  └───────────────────────────────────────────────────────────┘       │
│                                                                       │
│  MCP 层 (外部通信，详见 §3.3)                                        │
│  ┌───────────────────────────────────────────────────────────┐       │
│  │  MCP Client: 消费 Database/Knowledge/Vector Server         │       │
│  │  MCP Server: 对外暴露 NL2SQL 能力                           │       │
│  └───────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

关键分层逻辑：
- **Harness 控制 Agent**（生命周期、权限、约束）
- **Agent 调用 Node**（通过 NodeRegistry 获取）
- **Node 调用 MCP Tool**（通过 Tool Execution Agent）

LLM 只存在于 Node 内部（AgenticNode 调用 LiteLLM），它看不到 Harness、Agent 和 MCP 层的边界——这些由框架透明管理。

#### 3.4.8 Context 与 Memory 管理

##### a) Context 是 Workflow 级别的

同一个 Workflow 中的所有 Node 共享一个 **WorkflowContext**，不同 Workflow 之间完全隔离。

```
Workflow A (gensql_agentic):                 Workflow B (chat_agentic):
┌─────────────────────────────┐            ┌─────────────────────────────┐
│ WorkflowContext_A            │            │ WorkflowContext_B            │
│  ├── query_text              │            │  ├── query_text              │
│  ├── schema_linking_result   │  完全隔离  │  ├── chat_history            │
│  ├── generated_sql           │ ◄────────► │  └── session_id             │
│  └── execution_result        │            └─────────────────────────────┘
└─────────────────────────────┘
```

- `update_context()` 方法将 Node 输出写入当前 Workflow 的 Context
- 下游 Node 通过 `setup_input()` 从 Context 读取上游结果
- Workflow 结束后 Context 自动归档（或丢弃，取决于 Session 模式）

##### b) Memory 策略

| 维度 | 规则 |
|------|------|
| **内置 subagent 默认不启用** | `gen_sql`、`schema_linking`、`validate_sql`、`execute_sql`、`reflection` 的 `memory: false`——聚焦单次任务，不受历史干扰 |
| **chat 节点默认启用** | 多轮对话需要上下文连续 |
| **用户自定义 subagent 默认启用** | `memory: true`，用户可按需关闭 |
| **ephemeral vs 持久化** | Sub-agent 使用 `:memory:` 模式（零持久化开销）；顶层交互持久化到 `~/.data_engineer/sessions/` |
| **按 node_name 隔离** | `chat` 和 `gen_sql` 的 memory 存储在不同目录，互不干扰 |
| **AGENTS.md 全局共享** | 所有 Node 共享 AGENTS.md 前 200 行作为全局项目上下文，注入 Prompt 系统段 |

##### c) 存储路径

```
~/.data_engineer/
├── sessions/                     # 持久化 Session（顶层交互）
│   ├── chat/
│   │   └── {session_id}.db
│   └── custom_subagent/
│       └── {session_id}.db
│
├── memory/                       # Memory 隔离存储
│   ├── chat/                     # chat 节点的 memory
│   ├── gen_sql/                  # gen_sql 节点的 memory（但默认 :memory:）
│   └── {custom_subagent}/        # 自定义 subagent 的 memory
│
├── traces/                       # WorkflowRunner 轨迹持久化
│   └── {date}/{workflow_id}.jsonl
│
└── config/
    ├── agent.yml
    └── litellm.yaml
```

> **设计理由:** Sub-agent 使用 `:memory:` 避免持久化开销——每次查询是独立的，前一次的表结构上下文不应影响本次的 Schema Linking；chat 节点需要持久化以维持多轮对话连续性。

##### d) AGENTS.md 全局项目上下文

`AGENTS.md` 位于项目根目录，是所有 Node 共享的**全局知识注入点**。内容示例：

```markdown
# AGENTS.md — 项目全局上下文

## 数据库概览
- PostgreSQL 14: 业务主库，存储 orders / users / products / payments
- 生产库地址: pg-prod.internal:5432/db_main
- 只读副本: pg-read.internal:5432/db_main

## 命名约定
- 表名: snake_case 复数 (orders, users, products)
- 时间列: created_at, updated_at, deleted_at (软删除)
- 金额列: 单位 "分" (INT) 或 "元" (DECIMAL)，注意区分

## 业务规则
- 订单状态: pending → confirmed → shipped → completed / cancelled
- 退款: 通过 refunds 表关联，不可直接修改 orders.amount
- 会员等级: bronze / silver / gold / platinum (users.tier)
```

**注入规则：**
- 前 **200 行**注入 Prompt 系统段（可根据 token 预算调整）
- 文件变更时自动重新加载（无需重启）
- 可在 `agent.yml` 中配置 `global_context.source` 和 `global_context.inject_lines`

---

## 4. 模块详细设计

### 4.1 NL 解析与查询理解层

#### 4.1.1 职责
- 接收用户原始 NL 输入
- 检测语言（中/英）
- 解析时间表达、实体、意图
- 检测歧义点
- 构建结构化查询表示（Structured Query Representation, SQR）

#### 4.1.2 流程

```
NL 输入 → 语言检测 → 分词/实体识别 → 时间解析 → 意图分类 → 歧义标记 → SQR
```

#### 4.1.3 组件

**a) Language Detector**
- 基于字符 Unicode 范围检测（中/英/混合）
- 输出 `language: "zh" | "en" | "mixed"`

**b) Time Expression Parser**
- 解析中文时间表达："上个月"、"本周一"、"去年同期"、"最近7天"、"Q2"
- 解析英文时间表达："last month"、"this quarter"、"year to date"
- 输出标准化时间范围 `TimeRange(start, end, unit, precision)`
- 策略：规则解析为主 + LLM 兜底

**c) Intent Classifier**
- 分类：`SELECT` | `AGGREGATE` | `JOIN` | `COMPARISON` | `TIME_SERIES` | `FUNNEL` | `UNKNOWN`
- 轻量分类器（正则 + 关键词），无需 LLM 调用

**d) Ambiguity Detector**
- 检测常见歧义模式：
  - 属性归属歧义："上个月的用户" → 注册时间 vs 活跃时间
  - 聚合歧义："平均客单价" → SUM/COUNT vs AVG
  - 范围歧义："大订单" → 金额>1000 vs 数量>100
  - 时间参照歧义："最近" → 7天 vs 30天 vs 自定义
- 输出歧义列表 `Ambiguity[]`，每项含 `aspect`, `options[]`, `default`

**e) Structured Query Representation (SQR)**

```python
@dataclass
class SQR:
    raw_text: str
    language: Literal["zh", "en", "mixed"]
    intent: IntentType
    entities: list[Entity]
    time_range: TimeRange | None
    target_tables: list[str]  # 猜测的目标表
    target_columns: list[str]  # 猜测的目标列
    conditions: list[Condition]
    order_by: list[OrderSpec]
    limit: int | None
    ambiguities: list[Ambiguity]
    confidence: float  # 0.0 - 1.0
```

#### 4.1.4 歧义交互协议

当 `AmbiguityDetector` 检测到歧义时：

1. 将歧义点附加到 API 响应中（不阻塞）
2. 前端展示确认气泡："你是指 A 还是 B？"
3. 用户确认后，修正的 NL 参数进入生成引擎
4. 用户也可选择"跳过，生成多个候选"

### 4.2 领域知识引擎

#### 4.2.1 职责
- **Evolvable Context** — 管理活的知识库，自动捕获 Schema 元数据、参考 SQL、语义模型、业务指标，并持续演化
- **MetricFlow 语义层** — 集成 MetricFlow 定义业务指标，通过语义模型生成跨方言 SQL
- 管理多领域的配置和知识
- 维护业务术语表
- 提供 Schema 检索（RAG 混合检索）
- 提供向量语义搜索（LanceDB）
- 管理历史查询对

#### 4.2.2 领域配置结构

```
domains/
├── ecommerce/
│   ├── domain.yaml          # 领域基础配置
│   ├── glossary.yaml        # 术语表
│   ├── rules.yaml           # 业务规则
│   └── schema_mapping.yaml  # Schema 映射
├── finance/
│   ├── domain.yaml
│   ├── glossary.yaml
│   ├── rules.yaml
│   └── schema_mapping.yaml
└── default/                 # 默认/通用领域
    └── ...
```

**domain.yaml 示例：**
```yaml
name: ecommerce
label:
  zh: 电商
  en: E-commerce
description:
  zh: 电商数据分析领域，包含用户、订单、商品、支付等核心表
  en: E-commerce analytics domain with users, orders, products, payments
databases:
  - alias: main_db
    connection: postgresql://...
keywords: [电商, 订单, 商品, 用户, ecommerce, order, product, user]
timezone: Asia/Shanghai
currency: CNY
```

**glossary.yaml 示例：**
```yaml
terms:
  - term: 营业收入
    term_en: revenue
    description: 订单中已完成的商品销售收入（不含退款）
    mapping:
      expression: "SUM(CASE WHEN orders.status = 'completed' THEN orders.amount - orders.refund_amount ELSE 0 END)"
      type: derived_column
    tags: [finance, kpi]

  - term: 客单价
    term_en: average_order_value
    description: 平均每笔订单的金额
    mapping:
      expression: "SUM(orders.amount) / COUNT(DISTINCT orders.id)"
      type: derived_column
      precision: 2  # 保留两位小数
    tags: [kpi, sales]

  - term: 活跃用户
    term_en: active_users
    description: 在指定时间范围内有登录或购买行为的用户
    mapping:
      table: users
      condition: "users.last_active_at >= {time_start}"
      type: filter_condition
    tags: [user, kpi]
```

**rules.yaml 示例：**
```yaml
rules:
  - id: revenue_calc
    description: 营业收入计算规则
    pattern: "营业收入|revenue"
    enforce:
      - "必须排除 refund_amount > 0 的订单"
      - "金额单位统一为元（分需转为元）"
    sql_template: "SUM(orders.amount - COALESCE(orders.refund_amount, 0))"

  - id: date_filter_with_index
    description: 日期过滤要利用索引
    pattern: "时间范围查询"
    enforce:
      - "避免使用函数包裹日期列：WHERE DATE(created_at) = ... 应改为 WHERE created_at >= ... AND created_at < ..."
```

#### 4.2.3 领域自动检测

```
输入: NL 查询文本
    1. 关键词匹配：遍历所有领域的 keywords 列表
    2. 术语匹配：在术语表中搜索 NL 中的词汇
    3. Schema 匹配：在领域的 Schema 描述中搜索
输出: 按置信度排序的 [Domain, score][] 列表
    如果最高分 > 阈值 (0.6) → 自动切换
    如果 < 阈值 → 提示用户手动选择
    用户可随时手动覆盖
```

#### 4.2.4 RAG 混合检索策略

##### 概述

RAG（Retrieval-Augmented Generation）混合检索结合了**稀疏检索**（关键词精确匹配）和**密集检索**（语义向量匹配）的优势，是领域知识引擎的核心检索能力。

本系统定义了**三类专门的 RAG 存储模块**，各自管理独立的索引生命周期：

```
┌─────────────────────────────────────────────────────────────────────┐
│                      RAG 存储模块体系                                  │
│                                                                     │
│  ┌─────────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  SchemaMetadataRAG   │  │   MetricRAG      │  │  DocumentStore │  │
│  │                     │  │                  │  │                │  │
│  │ 存储: 数据库表结构   │  │ 存储: 业务指标   │  │ 存储: 平台文档  │  │
│  │ ·表名/列名/注释     │  │ ·指标定义/公式   │  │ ·用户手册      │  │
│  │ ·数据类型/约束      │  │ ·维度/粒度       │  │ ·最佳实践      │  │
│  │ ·外键/索引          │  │ ·计算逻辑         │  │ ·FAQ           │  │
│  │ ·样本值             │  │ ·KPI 分类标签     │  │ ·SQL 编码规范  │  │
│  │                     │  │                  │  │                │  │
│  │ 数据源: 数据库 DDL   │  │ 数据源:          │  │ 数据源:        │  │
│  │ Schema 提取器        │  │ MetricFlow 定义  │  │ 用户上传/      │  │
│  │                     │  │ ·术语表转换       │  │ 平台预置       │  │
│  │ 更新: Schema 刷新时  │  │ 更新: 指标审核后  │  │ 更新: 文档变更  │  │
│  └─────────────────────┘  └─────────────────┘  └────────────────┘  │
│                                                                     │
│  三者共享同一套混合检索引擎 (BM25 + LanceDB + RRF)，但索引隔离：       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  LanceDB: 3 个命名空间 (schema_metadata / metrics / documents)  │ │
│  │  BM25:    3 个倒排索引                                          │ │
│  │  RRF:     按存储类型独立融合                                       │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

**三类检索对象与三个存储模块的对应关系：**

| 存储模块 | 检索对象 | 稀疏检索 (BM25) | 密集检索 (向量) | 用途 |
|---------|---------|----------------|----------------|------|
| **SchemaMetadataRAG** | Schema 元素（表/列） | 表名、列名、注释关键词 | 表/列语义描述 Embedding | 定位相关表和列 |
| **MetricRAG** | 业务指标 + 术语 | 指标名、别名、维度关键词 | 指标描述 Embedding | 匹配业务指标定义 |
| **DocumentStore** | 历史查询对 + 平台文档 | NL 查询原文关键词 | NL 查询语义 Embedding | 相似查询复用 + 知识增强 |

##### 检索算法

**a) 稀疏检索 (BM25)**

```
score_BM25(Q, D) = Σ (IDF(t) · (f(t, D) · (k₁ + 1)) / (f(t, D) + k₁ · (1 - b + b · |D| / avgdl)))
```

- 对 NL 查询进行分词（中文 jieba / 英文空格分词）
- 对每个检索对象构建倒排索引
- 擅长精确匹配专有名词：表名 `orders`、列名 `created_at`、术语 `客单价`
- 参数：`k₁=1.5, b=0.75`

**b) 密集检索 (向量)**

```
score_dense(Q, D) = cos(E(Q), E(D))
```

- 所有候选文档（表描述、列注释、术语定义、历史查询）预计算 Embedding
- 查询时实时 Embedding 用户输入
- 语义匹配：`"每个用户平均花了多少钱"` ↔ `客单价`
- Embedding 模型：`text-embedding-3-small` (OpenAI) / `BGE-large-zh` (本地中文优化)

**c) 混合融合策略**

采用 **倒排排名融合 (RRF)** 作为默认融合算法：

```
score_RRF(D) = Σ 1 / (k + rank_s(D)) + Σ 1 / (k + rank_d(D))
                       稀疏排名              密集排名
```

- 参数：`k=60` (标准值)
- 同时支持**加权线性融合**作为备选：

```
score_hybrid = α · norm(score_BM25) + (1-α) · norm(score_dense)
```

- `α` 动态可调：查询含明确表/列名时增大 `α`（偏向 BM25），抽象查询时减小 `α`（偏向语义）

##### 检索流程

```
用户 NL 查询
    │
    ▼
┌─────────────────────────────┐
│ 查询分析                     │
│ ├── 提取关键词（专有名词）    │
│ ├── 检测是否为术语引用       │
│ └── 判断查询抽象程度          │
└──────────┬──────────────────┘
           │
    ┌──────▼──────┐
    │ 是否含明确   │
    │ 表/列名？   ├──是──► α = 0.7 (BM25 优先)
    └──────┬──────┘
           │ 否
           ▼
    ┌──────────────────┐
    │ 是否抽象业务查询？├──是──► α = 0.3 (语义优先)
    └──────────────────┘
           │ 否
           ▼
        α = 0.5 (均衡)
           │
           ▼
┌────────────────────────────────────────────┐
│ 并行检索                                    │
│                                             │
│  ┌────────────────┐  ┌────────────────┐    │
│  │ BM25 索引检索   │  │ 向量数据库检索  │    │
│  │ (关键词匹配)    │  │ (语义相似度)    │    │
│  └────────┬───────┘  └────────┬───────┘    │
│           │                   │             │
│           ▼                   ▼             │
│  ┌────────────────────────────────────────┐ │
│  │ RRF 融合排名                            │ │
│  │ 合并 BM25 结果集和向量结果集            │ │
│  └────────────────┬───────────────────────┘ │
└───────────────────┬─────────────────────────┘
                    │
                    ▼
          Top-K 检索结果 (含分数)
          ├── Schema 相关表 (≤ 8)
          ├── 匹配术语项 (≤ 5)
          └── 相似历史查询 (≤ 3)
```

##### 多模态索引结构

```
检索索引总览:
┌───────────────────────────────────────────────────────────────┐
│                   混合检索索引                                   │
│                                                               │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐ │
│  │ BM25 倒排索引            │  │ 向量索引                      │ │
│  │                         │  │                              │ │
│  │ doc_id → [term, freq]  │  │ doc_id → embedding (1536维)  │ │
│  │ term → [doc_id, ...]   │  │                              │ │
│  │                         │  │ 索引类型: IVF / HNSW        │ │
│  │ 分词器: jieba (zh)     │  │ 距离度量: cosine            │ │
│  │         whitespace (en) │  │                              │ │
│  └─────────────────────────┘  └──────────────────────────────┘ │
│                                                               │
│  文档集合:                                                    │
│  ┌───────┬────────────┬───────────┬──────────┬─────────────┐  │
│  │doc_id │ type       │ content   │ keywords │ domain      │  │
│  ├───────┼────────────┼───────────┼──────────┼─────────────┤  │
│  │T001   │ table      │ orders    │ 订单     │ ecommerce   │  │
│  │C002   │ column     │ amount    │ 金额     │ ecommerce   │  │
│  │G003   │ glossary   │ 客单价    │ aov      │ ecommerce   │  │
│  │H004   │ query_pair │ ...       │ ...      │ ecommerce   │  │
│  └───────┴────────────┴───────────┴──────────┴─────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

##### 缓存的 Embedding 更新策略

| 变更类型 | 触发条件 | 更新范围 |
|---------|---------|---------|
| Schema 更新 | DDL 变更检测 / 手动刷新 | 增量：仅变更表/列 |
| 术语变更 | 术语 CRUD | 单条术语 |
| 历史查询新增 | 用户确认 SQL 后 | 新查询对 |
| 批量重建 | 升级 Embedding 模型 / 全量同步 | 全量 |

##### SchemaMetadataRAG（Schema 元数据 RAG 存储）

专门负责数据库 Schema 元数据的存储和检索，是 NL→SQL 管道的第一个知识入口。

**数据模型：**

```python
@dataclass
class SchemaDocument:
    doc_id: str                        # {db_id}.{schema}.{table}.{column}
    db_id: str                         # 数据库标识
    schema_name: str                   # Schema 名
    table_name: str                    # 表名
    column_name: str | None            # 列名（None 表示整表描述）
    data_type: str | None              # 数据类型
    comment: str | None                # 注释
    is_primary_key: bool               # 是否主键
    is_foreign_key: bool               # 是否外键
    fk_references: tuple | None        # 外键引用 (table, column)
    enum_values: list[str] | None      # 枚举值
    sample_values: list[Any]           # 抽样值
    table_row_count: int               # 表行数估计
    embedding_text: str                # 用于 Embedding 的文本
    embedding: list[float]             # LanceDB 向量
    tags: list[str]                    # 自动标注标签
    updated_at: datetime
```

**索引策略：** BM25 索引字段为 `table_name` + `column_name` + `comment`，LanceDB 向量字段为语义描述。

**检索接口：**

```python
class SchemaMetadataRAG:
    async def find_relevant_tables(self, nl_query: str, top_k: int = 8) -> list[TableCandidate]: ...
    async def find_columns(self, table_name: str, nl_context: str = None) -> list[ColumnSchema]: ...
    async def resolve_ambiguous_column(self, column_name: str, nl_query: str) -> list[ColumnCandidate]: ...
    async def refresh_index(self, db_id: str): ...
```

**更新触发：** 数据库连接时全量构建 → DDL 变更时增量更新 → 手动刷新

---

##### MetricRAG（业务指标 RAG 存储）

专门负责业务指标（KPI）的存储和检索，衔接 MetricFlow 语义层与 NL 查询。

**数据模型：**

```python
@dataclass
class MetricDocument:
    doc_id: str                        # 指标唯一标识
    domain: str                        # 所属领域
    name: str                          # 指标名（如 "revenue"）
    name_zh: str                       # 中文名（如 "营业收入"）
    aliases: list[str]                 # 别名
    description: str                   # 描述
    formula: str                       # 计算公式
    metric_type: str                   # derived | measure | ratio
    aggregation: str                   # sum | count | avg | count_distinct
    dimensions: list[str]              # 可下钻维度
    time_grain: str                    # 时间粒度
    precision: int                     # 小数精度
    sql_template: str                  # MetricFlow SQL 模板
    embedding_text: str                # Embedding 文本
    embedding: list[float]             # LanceDB 向量
    updated_at: datetime
    version: int                       # 指标版本
```

**与 MetricFlow 的关系：** MetricFlow 声明式定义 → 同步到 MetricRAG 向量化存储，共同构成语义层。

**检索接口：**

```python
class MetricRAG:
    async def match_metrics(self, nl_query: str, top_k: int = 5) -> list[MetricCandidate]: ...
    async def find_related_metrics(self, metric_name: str) -> list[MetricDocument]: ...
    async def suggest_dimensions(self, metric_name: str, nl_context: str = None) -> list[str]: ...
    async def refresh_from_metricflow(self): ...
```

**更新触发：** 术语表/MetricFlow 变更 → 手动重新导入 → 周期性刷新

---

##### DocumentStore（平台文档向量存储）

负责存储平台文档（用户手册、最佳实践、SQL 规范、FAQ 等），供 RAG 增强 LLM 上下文。

**数据模型：**

```python
@dataclass
class Document:
    doc_id: str
    domain: str | None                 # 关联领域
    title: str
    content: str                       # 文档正文（Markdown）
    content_type: str                  # user_guide | best_practice | sql_style | faq
    source: str                        # uploaded | builtin | generated
    chunk_index: int                   # 分块序号
    parent_doc_id: str | None
    embedding_text: str
    embedding: list[float]             # LanceDB 向量
    keywords: list[str]
    language: str                      # zh | en
    created_at: datetime
    updated_at: datetime
```

**分块策略：** Markdown 标题分割 → 每块 ≤512 tokens → 重叠窗口 64 tokens → 独立 Embedding。

**预置文档类型：**

| 类型 | 内容 | 来源 |
|------|------|------|
| `sql_style` | SQL 编码规范、命名约定 | 平台预置 |
| `best_practice` | NL→SQL 最佳实践、常见模式 | 平台预置 + 用户编写 |
| `user_guide` | 系统使用说明、术语说明 | 用户上传 |
| `faq` | 常见问题解答 | 用户积累 + LLM 生成 |

**检索接口：**

```python
class DocumentStore:
    async def search(self, query: str, content_types: list[str] = None, top_k: int = 3) -> list[Document]: ...
    async def upload_document(self, file, domain=None, content_type="user_guide"): ...
    async def list_documents(self, domain=None) -> list[DocumentSummary]: ...
    async def rebuild_index(self): ...
```

---

##### 三类 RAG 存储的协作关系

```
NL 查询: "上个月华东区的营业收入是多少？"
    │
    ├──► SchemaMetadataRAG: 找到 orders(amount, region, status) 表
    ├──► MetricRAG: 匹配到 "营业收入" → revenue 指标
    ├──► DocumentStore: 找到时间范围查询的最佳实践
    └──→ 三者结果合并 → 注入 LLM Prompt → 生成 SQL
```

> **RAG 混合检索在 Schema 检索中的应用详见 [4.3.3 分层检索策略](#433-分层检索策略大-schema-处理)**

#### 4.2.5 Evolvable Context（活的知识库）

##### 概述

Evolvable Context 是一个自动演化的活知识库，持续捕获和学习四类核心信息：

| 知识类型 | 来源 | 捕获方式 | 演化触发 |
|---------|------|---------|---------|
| **Schema 元数据** | 数据库 DDL / 连接探测 | 定时同步 + DDL 监听 | Schema 变更时增量更新 |
| **参考 SQL** | 用户确认的查询、历史执行 | 自动保存 + 质量评分筛选 | 用户确认后自动入库 |
| **语义模型** | MetricFlow 指标定义、术语映射 | 配置加载 + 自动发现 | 指标变更 / 新术语添加 |
| **业务指标** | MetricFlow 定义 + 业务规则 | 语义层解析 + 规则提取 | 规则审核通过 / 周期校准 |

##### 演化循环

```
┌─────────────────────────────────────────────────────────────────┐
│                       演化循环                                     │
│                                                                   │
│  1. 感知 (Sense)                                                  │
│     ├── Schema DDL 变更事件                                        │
│     ├── 用户新查询确认                                             │
│     ├── MetricFlow 指标更新                                        │
│     └── 用户反馈/编辑模式发现                                       │
│         │                                                          │
│         ▼                                                          │
│  2. 吸收 (Ingest)                                                  │
│     ├── 新 Schema 信息 → LanceDB 索引更新                          │
│     ├── 新参考 SQL → 查询对库 + Embedding 更新                     │
│     ├── 新指标定义 → 语义模型层重新编译                             │
│     └── 新规则 → rules.yaml + 规则引擎热加载                       │
│         │                                                          │
│         ▼                                                          │
│  3. 检索 (Retrieve)                                                │
│     ├── RAG 混合检索 (BM25 + LanceDB)                             │
│     ├── 语义模型查询 (MetricFlow API)                              │
│     └── 规则匹配 (规则引擎)                                        │
│         │                                                          │
│         ▼                                                          │
│  4. 生成 (Generate)                                                │
│     ├── Prompt 组装 (融合所有检索结果)                              │
│     ├── LiteLLM 路由调用                                           │
│     └── Dialect Adapter 方言适配                                   │
│         │                                                          │
│         ▼                                                          │
│  5. 验证 & 反馈 → 进入下一轮演化循环                                 │
└─────────────────────────────────────────────────────────────────┘
```

##### Evolvable Context 数据模型

```python
@dataclass
class EvolvableContext:
    version: int                              # 上下文版本号
    schema_metadata: dict[str, TableSchema]   # Schema 元数据 (schema://)
    reference_sqls: list[ReferenceSQL]        # 高质量参考 SQL
    semantic_models: list[SemanticModel]      # MetricFlow 语义模型
    business_metrics: list[MetricDefinition]  # 业务指标定义
    glossary_terms: list[GlossaryTerm]        # 术语表
    rules: list[BusinessRule]                 # 业务规则
    context_summary: str                      # LLM 可读的上下文摘要
    last_evolved_at: datetime                 # 最后演化时间

@dataclass
class ReferenceSQL:
    nl_input: str
    sql: str
    dialect: str
    domain: str
    quality_score: float    # 0-1, 基于用户评分 + 编辑次数
    execution_count: int
    embedding: list[float]  # LanceDB 向量
```

#### 4.2.6 MetricFlow 语义层集成

##### 概述

**MetricFlow**（dbt Labs 开源）是一个语义指标框架，允许用声明式方式定义业务指标，自动生成跨 SQL 方言的查询。本系统集成 MetricFlow 作为语义层核心：

```
用户 NL: "上个月的营业收入是多少？"
    │
    ▼
意图理解 → "营业收入" 匹配到 MetricFlow 指标 metric:revenue
    │
    ▼
MetricFlow API 调用:
    MetricFlowEngine.query_metrics(
        metrics=["revenue"],
        dimensions=["date_month"],
        time_constraint={"start": "2026-06-01", "end": "2026-06-30"}
    )
    │
    ▼
MetricFlow 生成跨方言 SQL:
    PostgreSQL:  SELECT date_month, SUM(amount - refund) ...
    Snowflake:   SELECT DATE_TRUNC('MONTH', date), SUM(amount - refund) ...
    StarRocks:   SELECT DATE_TRUNC('month', date), SUM(amount - refund) ...
```

##### MetricFlow 指标定义

指标定义存放在领域配置中，与术语表互补：

```yaml
# domains/ecommerce/metrics.yaml
semantic_model:
  name: ecommerce_metrics
  description: 电商核心业务指标

  entities:
    - name: order_id
      type: primary
      expr: id
    - name: user_id
      type: foreign
      expr: user_id

  measures:
    - name: order_amount
      agg: sum
      expr: amount
      description: 订单金额合计
    - name: refund_amount
      agg: sum
      expr: refund_amount
      description: 退款金额合计
    - name: order_count
      agg: count
      expr: id
      description: 订单数

  dimensions:
    - name: date_month
      type: time
      expr: DATE_TRUNC('month', created_at)
    - name: region
      type: categorical
      expr: region

metrics:
  - name: revenue
    description: 营业收入（去除退款后）
    type: derived
    expr: order_amount - refund_amount
    constraints:
      - "WHERE status = 'completed'"
    tags: [finance, kpi]

  - name: average_order_value
    description: 客单价
    type: derived
    expr: (order_amount - refund_amount) / order_count
    constraints:
      - "WHERE status = 'completed'"
    precision: 2
    tags: [kpi, sales]

  - name: active_users
    description: 活跃用户数
    type: measure
    measure: user_id
    agg: count_distinct
    tags: [user, growth]
```

##### MetricFlow 与 NL2SQL 管道的整合

```
NL 查询 → 意图理解
    │
    ├── 检测是否命中 MetricFlow 指标
    │    ├── 命中 → 调用 MetricFlowEngine.query_metrics()
    │    │   → MetricFlow 生成 SQL (跨方言)
    │    │   → 验证 → 执行
    │    │
    │    └── 未命中 → 走标准 NL2SQL 管道
    │        → RAG 混合检索 Schema
    │        → LLM 生成 SQL (LiteLLM)
    │        → Dialect Adapter 方言适配
    │        → 验证 → 执行
    │
    └── 部分命中（部分指标 + 部分自定义）
        → MetricFlow 生成指标部分 SQL
        → LLM 补全剩余逻辑
        → 合并 → 验证 → 执行
```

##### 跨方言 SQL 生成

MetricFlow 原生支持多方言，本系统通过 **sqlglot** 补充方言转换能力：

| 数据库 | MetricFlow 原生 | sqlglot 兜底 |
|--------|---------------|-------------|
| PostgreSQL | ✅ | — |
| MySQL | ✅ | — |
| Snowflake | ✅ | — |
| BigQuery | ✅ | — |
| Databricks | ✅ | — |
| DuckDB | ⚠️ 部分 | ✅ |
| SQLite | ❌ | ✅ |
| StarRocks | ❌ | ✅ |
| Redshift | ❌ | ✅ |
| ClickHouse | ❌ | ✅ |
| Trino | ❌ | ✅ |

全库支持通过 `Dialect Adapter` 层统一处理：

```python
@dataclass
class DialectAdapter:
    source_dialect: str    # 例如 "postgresql"
    target_dialect: str    # 例如 "starrocks"
    
    def translate(self, sql: str) -> str:
        """使用 sqlglot 进行方言转换"""
        return sqlglot.transpile(sql, 
            read=self.source_dialect, 
            write=self.target_dialect
        )[0]
    
    def validate(self, sql: str) -> ValidationReport:
        """方言特定验证"""
        ...
```

### 4.3 Schema 管理与检索

#### 4.3.1 职责
- 连接数据库并提取 Schema 信息
- 缓存 Schema 快照（可配置刷新间隔）
- 对 NL 查询关键词检索相关表和列
- 向量嵌入语义搜索相关 Schema 元素
- 管理大 Schema 的分层注入策略

#### 4.3.2 Schema 模型

```python
@dataclass
class ColumnSchema:
    name: str
    type: str  # int, varchar, timestamp, decimal...
    nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    references: tuple[str, str] | None  # (table, column)
    default: Any
    comment: str | None
    enum_values: list[str] | None  # 枚举类型值列表
    sample_values: list[Any]  # 抽样值，帮助 LLM 理解数据分布

@dataclass
class TableSchema:
    name: str
    comment: str | None
    columns: list[ColumnSchema]
    row_count_estimate: int
    indexes: list[IndexInfo]
    foreign_keys: list[ForeignKey]

@dataclass
class SchemaSnapshot:
    database_type: str
    database_name: str
    tables: dict[str, TableSchema]
    created_at: datetime
    version: int
```

#### 4.3.3 分层检索策略（大 Schema 处理）

当数据库表数 > 30 时，Schema 不能全部注入。采用**混合 RAG 驱动**的分层检索：

```
Step 1: NL 查询分析
    - 提取关键词（实体、表名候选）
    - 检测抽象程度，确定 RAG 融合权重 α（详见 4.2.4）
    - 判断是否需要时间解析
    - 输出: query_analysis (keywords, abstractness, α)

Step 2: RAG 混合检索 (Schema 域)
    - 并行执行:
      ├── BM25 稀疏检索: 关键词匹配表名、列名、注释
      └── 向量密集检索: 语义匹配表/列描述
    - RRF 融合排名 (k=60)
    - 按 domain_id 过滤（当前领域）
    - 输出: 候选表集合 T1 (score ≥ 0.3, top ≤ 15)

Step 3: 术语映射 (含混合检索)
    - 对 NL 中的候选业务术语执行 RAG 混合检索
    - 匹配到具体的表和列
    - 输出: 候选表集合 T2 (加权合并到 T1)

Step 4: 外键扩展
    - 对候选表的外键关联表进行 1 层扩展
    - 语义排序: 用向量检索对外键关联表做相关性排序
    - 输出: 最终候选表集合 T (通常 ≤ 8 张表)

Step 5: 动态注入
    - 将 T 中的表的完整 Schema 注入 Prompt
    - 对非 T 表，仅提供表名和简短描述（加上"如需使用请告知"提示）
```

#### 4.3.4 Schema 缓存策略

| 缓存类型 | TTL | 刷新触发 |
|---------|-----|---------|
| Schema 结构快照 | 1 小时 | 手动刷新 / 定时任务 |
| Embedding 向量 | 跟随 Schema | Schema 刷新时重建 |
| 表行数估计 | 30 分钟 | 每次查询后异步更新 |
| 列抽样值 | 1 天 | 按需刷新 |

### 4.4 SQL 生成引擎

#### 4.4.0 LiteLLM 多提供商路由层

SQL 生成引擎底层通过 **LiteLLM** 统一调用 10+ LLM 提供商：

```
┌──────────────────────────────────────────────────────────────────┐
│                      LiteLLM Router                                │
│                                                                   │
│  统一接口: litellm.completion(model="...", messages=[...])         │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  OpenAI  │ │  Claude  │ │  Gemini  │ │ DeepSeek │ │ Qwen   │ │
│  │ GPT-4o   │ │ Opus 4.8 │ │ 2.5 Pro  │ │   V3     │ │  2.5   │ │
│  │ GPT-4.1  │ │ Sonnet 4 │ │ 2.5 Flash │ │   R2     │ │  Plus  │ │
│  │ o3/o4-mini│ │ Haiku 4.5│ │          │ │          │ │        │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │ Mistral  │ │  Cohere  │ │   Groq   │ │  其他 10+ 提供商     │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘ │
│                                                                   │
│  核心功能:                                                         │
│  ├── 模型路由: 按工作流级别自动选择最佳模型                         │
│  ├── 退避重试: 限流/超时自动重试其他模型或提供商                    │
│  ├── 成本跟踪: 每次调用的 token 和费用记录                          │
│  ├── 流式支持: 所有提供商统一流式接口                              │
│  └── 模型 Fallback: 主模型失败 → 备选模型 → 经济模型               │
└──────────────────────────────────────────────────────────────────┘
```

**模型选择映射：**

| 工作流级别 | 首选模型 | Fallback | 原因 |
|-----------|---------|---------|------|
| express | Claude Haiku / GPT-4.1-mini | Gemini 2.5 Flash | 成本低、速度快 |
| standard | Claude Sonnet / GPT-4.1 | DeepSeek V3 | 平衡质量与成本 |
| deep | Claude Opus / GPT-4o | Gemini 2.5 Pro | 最复杂查询需要最强模型 |
| self_heal | Claude Opus / o4-mini | GPT-4o | 错误修正需要精确推理 |

#### 4.4.1 职责
- 构建 LLM Prompt（包含 Schema、上下文、历史、规则）
- 通过 LiteLLM 路由层调用 LLM API（流式或非流式）
- 支持多候选生成
- 自愈重试（分析错误 → 修正 → 重试）
- 成本优化（缓存/降级/LiteLLM Fallback）

#### 4.4.2 Prompt 构建策略

**Base Prompt 结构：**

```
System:
你是一个 SQL 生成专家。将自然语言问题转换为 {dialect} SQL。
当前领域: {domain_name}
语言: {language}

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
3. 注意 {language} 语义细节
4. 考虑 NULL 处理
5. 对时间条件使用索引友好的写法

请生成 SQL 并附上简短推理说明。
```

**上下文预算分配：**

| Prompt 组成部分 | Token 预算 |
|----------------|-----------|
| System 指令 | ~300 |
| Schema 注入 | ~2000 (核心表 < 8) |
| 术语表 | ~500 (只注入相关项) |
| 业务规则 | ~500 (只注入匹配规则) |
| 历史相似查询 | ~1000 (最多 3 个) |
| 对话历史 | ~1000 (最近 3 轮) |
| 用户问题 | ~200 |
| **总计** | **~5500** |

#### 4.4.3 多候选生成

有两种策略，按场景选择：

1. **温度采样法** — 单次调用 `n=3, temperature=0.7`，一次返回多个候选
2. **多维 Prompt 法** — 3 次独立调用，每次 Prompt 强调不同维度：
   - 候选 A: 性能优先（善用索引、避免复杂子查询）
   - 候选 B: 可读性优先（CTE、良好别名、注释）
   - 候选 C: 功能完整优先（不丢失任何语义条件）

#### 4.4.4 自愈重试流程

```
LLM 返回 SQL → 语法验证 → 通过 → Schema 验证 → 通过 → 执行 → 成功 → ✅

                         失败 ↓                   失败 ↓             失败 ↓
                      重试 (max=2)             重试 (max=1)       分析错误原因
                          ↓                       ↓                   ↓
                    修正 Prompt              补充 Schema          修正 Prompt
                    (回退到更简单策略)         (列出可用列名)      (加约束条件)
```

#### 4.4.5 流式输出

- 使用 SSE (Server-Sent Events) 或 WebSocket
- LLM 生成 SQL 时逐步推送到前端
- 用户可实时看到 SQL 构建过程
- 流式完成后再整个校验一次

#### 4.4.6 成本控制策略

| 策略 | 说明 | 预估节省 |
|------|------|---------|
| 查询对缓存 | 完全匹配的 NL 查询直接返回缓存 SQL | ~20% |
| 语义缓存 | 语义相似度 > 0.95 的查询复用结果 | ~15% |
| Schema 缓存 | 避免重复获取 Schema | 显著减少 API 调用 |
| 本地小模型兜底 | 简单查询走本地 SQLite/规则引擎 | 可变 |
| Template 匹配 | 匹配预定义模板（"按 {col} 分组统计"） | ~10% |

### 4.5 SQL 验证与执行器

#### 4.5.1 验证流水线

```
SQL 文本
  → Step 1: 语法解析 (sqlparse / 方言解析器)
     ├── 词法分析
     ├── 语法树构建
     └── 方言特性检查
  → Step 2: Schema 引用验证
     ├── 表存在性检查
     ├── 列存在性检查
     ├── 列-表归属检查（解决模糊列名）
     └── 函数存在性检查
  → Step 3: 类型兼容检查
     ├── WHERE/ON 条件两侧类型兼容
     ├── 聚合函数参数类型正确
     ├── ORDER BY 列在 SELECT 或 GROUP BY 中
     └── 数值精度检查（decimal 截断风险）
  → Step 4: 执行计划分析（EXPLAIN）
     ├── 扫描类型（Seq Scan vs Index Scan）
     ├── 预估行数
     ├── JOIN 类型（Nested Loop vs Hash Join vs Merge Join）
     └── 潜在全表扫描警告
  → Step 5: 业务规则校验
     ├── 检查是否违反 rules.yaml 约束
     ├── 检查数值计算是否精度正确
     └── 检查 WHERE 条件是否已遵守同名列歧义规则
  → 输出: ValidationReport
```

```python
@dataclass
class ValidationReport:
    passed: bool
    syntax_ok: bool
    syntax_errors: list[str]
    schema_valid: bool
    schema_errors: list[str]
    type_valid: bool
    type_errors: list[str]
    plan_analysis: PlanAnalysis | None
    business_valid: bool
    business_warnings: list[str]
    warnings: list[str]
    score: float  # 0.0 - 1.0 综合质量评分
```

#### 4.5.2 执行器

- 只读执行：仅允许 SELECT、WITH（只读 CTE）
- 写操作拦截：检测 DDL/DML 关键字，要求用户二次确认
- 结果限制：默认 LIMIT 100，可配置
- 超时保护：查询超时阈值（默认 30s，可配置）
- 并发控制：单用户最多 N 个并发查询

#### 4.5.3 Dialect Adapter（多数据库方言适配）

系统内置 **11 种数据库适配器**，覆盖主流 BI 场景，通过 **Dialect Adapter** 层实现统一 SQL 生成和执行：

| # | 数据库 | 类别 | 内置支持 | 驱动 | 方言转换 | 典型应用场景 |
|---|--------|------|---------|------|---------|------------|
| 1 | **SQLite** | 嵌入式 | ✅ 内置 | `sqlite3` | sqlglot | 零配置、本地开发和测试 |
| 2 | **DuckDB** | 嵌入式 OLAP | ✅ 内置 | `duckdb` | sqlglot | 本地分析引擎，替代 Pandas 做大数据 |
| 3 | **PostgreSQL** | 关系型 | ✅ 主力 | `asyncpg` / `psycopg2` | 原生 | 通用 OLTP + OLAP，最常用开源数据库 |
| 4 | **MySQL** | 关系型 | ✅ 主力 | `aiomysql` / `pymysql` | 原生 | Web 应用数据库，互联网行业标配 |
| 5 | **Snowflake** | 云数仓 | ✅ | `snowflake-connector` | MetricFlow 原生 | 企业级云数据仓库 |
| 6 | **StarRocks** | MPP 分析 | ✅ 适配器 | `mysql-connector` | sqlglot | 实时 OLAP 分析，替代 ClickHouse |
| 7 | **BigQuery** | 云数仓 | ✅ 适配器 | `google-cloud-bigquery` | MetricFlow 原生 | Google 生态数据仓库 |
| 8 | **Redshift** | 云数仓 | ✅ 适配器 | `redshift-connector` | sqlglot | AWS 生态数据仓库 |
| 9 | **ClickHouse** | MPP 分析 | ✅ 适配器 | `clickhouse-connect` | sqlglot | 海量日志/事件分析 |
| 10 | **Databricks** | 湖仓一体 | ✅ 适配器 | `databricks-sql-connector` | MetricFlow 原生 | Spark SQL 引擎，统一湖仓 |
| 11 | **Trino** | 联邦查询 | ✅ 适配器 | `trino-python-client` | sqlglot | 跨数据源联合查询 |

**适配器分级（兼容性矩阵）：**

| 级别 | 说明 | 数据库 |
|------|------|-------|
| **L1 原生** | 完整 Schema 提取 + 执行 + EXPLAIN，方言原生支持 | PostgreSQL、MySQL |
| **L2 内置** | 完整 Schema 提取 + 执行，sqlglot 方言转换，零配置 | SQLite、DuckDB |
| **L3 MetricFlow 原生** | MetricFlow 直接生成对应方言 SQL | Snowflake、BigQuery、Databricks |
| **L4 适配器** | 通过 sqlglot 转换 + 原生驱动执行，Schema 提取部分兼容 | StarRocks、Redshift、ClickHouse、Trino |

**Dialect Adapter 接口：**

```python
@dataclass
class DialectConfig:
    dialect: str                    # postgresql | mysql | sqlite | duckdb | snowflake | starrocks
    driver: str                     # 数据库驱动名
    type_mapping: dict[str, str]    # 通用类型 → 方言类型映射
    function_mapping: dict[str, str] # 通用函数 → 方言函数映射
    quote_char: str                 # 标识符引用符 (" 或 `)
    supports: set[str]              # 支持的特性: cte, window, json, array...

class DialectAdapter:
    def translate(self, sql: str, target_dialect: str) -> str: ...
    def validate(self, sql: str) -> ValidationReport: ...
    def format_sql(self, sql: str) -> str: ...
    def get_type_name(self, generic_type: str) -> str: ...
    def quote_identifier(self, name: str) -> str: ...
```

### 4.6 持续学习系统

#### 4.6.1 架构

```
用户反馈/编辑
    │
    ▼
┌──────────────────────┐
│  反馈收集器           │
│  - 评分 (1-5)        │
│  - 文本反馈           │
│  - 编辑 Diff          │
│  - 最终确认 SQL       │
└────────┬─────────────┘
         │
    ┌────▼────────────────────────────────────────┐
    │             学习管道                          │
    │                                              │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
    │  │查询对入库│  │模式分析  │  │规则提取  │   │
    │  │(NL, SQL) │  │(聚类同   │  │(从修正  │   │
    │  │+ 语义索引│  │类错误)   │  │中归纳)  │   │
    │  └──────────┘  └──────────┘  └──────────┘   │
    │                                              │
    │  ┌──────────────────────────────────────┐    │
    │  │ 周期任务: 模型微调准备 (导出训练数据) │    │
    │  └──────────────────────────────────────┘    │
    └──────────────────────────────────────────────┘
```

#### 4.6.2 查询对存储

```sql
CREATE TABLE query_pairs (
    id              BIGSERIAL PRIMARY KEY,
    domain_id       VARCHAR(64) NOT NULL,
    nl_original     TEXT NOT NULL,         -- 用户原始 NL
    nl_normalized   TEXT,                   -- 标准化后的 NL（去停用词等）
    sql_final       TEXT NOT NULL,          -- 最终确认的 SQL
    sql_hash        VARCHAR(64) NOT NULL,   -- SQL 的归一化哈希（去空格、大小写）
    language        VARCHAR(10),
    intent          VARCHAR(32),
    schema_version  INT,
    user_rating     SMALLINT,               -- 1-5
    edit_count      INT DEFAULT 0,          -- 编辑次数
    source          VARCHAR(32),            -- generated | human_written | edited
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(domain_id, sql_hash)
);

-- 向量存储（pgvector 或独立的向量数据库）
-- embedding: vector(1536)  -- NL 语义向量
```

#### 4.6.3 语义相似缓存

当新查询到来时，计算 embedding，在历史库中搜索 >0.95 相似度的匹配：

```
输入 NL → 向量化 → 查询对库相似搜索
    ├── >0.98 → 直接复用缓存 SQL（并告知用户）
    ├── 0.95-0.98 → 作为 few-shot 示例注入 prompt
    └── <0.95 → 正常生成
```

#### 4.6.4 模式分析与规则提取

**分析周期（每 N 个反馈 / 定时触发）：**

1. 收集过去窗口内的用户编辑 diff
2. 聚类相似的修正模式（例如，多个用户修正了 NULL 处理）
3. 提炼为候选规则
4. 推送到待确认列表
5. 用户审核后纳入 `rules.yaml`

**规则提取示例：**

```
观测：3 个用户将 "SUM(price)" 改为 "SUM(price * quantity)"
观测：2 个用户添加 "WHERE status != 'cancelled'"
→ 提取规则: 计算销售额时需乘以数量 + 排除取消订单
```

#### 4.6.5 反馈数据收集

```python
@dataclass
class FeedbackData:
    session_id: str
    query_id: str
    nl_input: str
    sql_generated: str
    sql_final: str | None       # 用户最终确认的版本
    rating: int | None          # 1-5
    feedback_text: str | None
    diff_operations: list[DiffOp]  # SQL 编辑操作序列
    execution_success: bool
    execution_time_ms: int
    user_cancelled: bool
    timestamp: datetime
```

### 4.7 对话与版本管理

#### 4.7.1 对话会话模型

```python
@dataclass
class ConversationTurn:
    turn_id: str
    nl_input: str
    sqr: SQR
    candidates: list[SQLCandidate]  # 多个候选
    selected_candidate_id: str      # 用户选择的候选
    edited_sql: str | None          # 用户手动编辑后的 SQL
    executed_result: QueryResult | None
    feedback: FeedbackData | None
    created_at: datetime

@dataclass
class ConversationSession:
    session_id: str
    domain: str
    database: str
    turns: list[ConversationTurn]
    context_summary: str  # LLM 提炼的对话摘要，用于长会话
    created_at: datetime
    updated_at: datetime
```

#### 4.7.2 版本管理

每一次用户编辑 SQL 或者切换候选都生成一个新版本：

```
版本 1: LLM 生成的原始 SQL
版本 2: 用户编辑后的 SQL (diff: ...)
版本 3: 用户再次编辑
...

用户操作:
- 回退到版本 N (保留后续版本)
- 对比版本 N 和版本 M
- 从版本 N 分支出新编辑
```

### 4.8 前端与 API 层

#### 4.8.1 Web UI 功能模块

| 模块 | 说明 |
|------|------|
| 查询输入框 | 多行文本输入，支持中英文，支持快捷键提交 |
| 领域选择器 | 下拉选择领域 + 自动检测提示 |
| SQL 展示区 | 语法高亮、行号、手动编辑功能 |
| 多候选切换 | Tab 或卡片式切换多个候选 SQL |
| 执行预览 | 结果表格展示，支持分页和排序 |
| 对话历史侧边栏 | 显示当前会话的对话轮次列表 |
| 版本时间轴 | 显示 SQL 的版本历史和分支 |
| 反馈控件 | 评分（1-5星）+ 文本反馈输入框 |
| 设置面板 | 数据库连接管理、领域配置、Schema 刷新 |

#### 4.8.2 API 端点

详见 [第 6 节 API 设计](#6-api-设计)

### 4.9 Workflow 编排系统

> **核心理念：** 声明式 Plan 模板驱动，WorkflowRunner 按 `node_order` 拓扑序推进，LLM 不决定执行路径。

#### 4.9.1 概述

"一刀切"的 NL→SQL 管道对所有查询使用相同的处理路径，导致简单查询响应慢、复杂查询能力不足。Workflow 编排系统按查询复杂度自动选择 Plan 模板，由 **WorkflowRunner**（详见 §3.4.3）按 DAG 拓扑序推进，**不依赖 LLM 自行跳转**。

#### 4.9.2 Plan 模板（workflow.yml）

每条 workflow.yml 定义一个 Plan 模板，声明 DAG 节点顺序、失败策略和评估规则：

| 模板 ID | 名称 | node_order 拓扑 | 适用场景 |
|---------|------|----------------|---------|
| `gensql_agentic` | 直接生成 | `schema_linking → generate_sql → validate_sql → execute_sql` | 标准 NL→SQL 单轮 |
| `reflection` | 反射式生成 | `schema_linking → generate_sql → execute_sql → reflection → [revise → exec...]` | 需要执行验证的复杂查询 |
| `chat_agentic` | 对话式 | `parse_nl → hybrid_search → generate_sql → validate_sql → respond` | 多轮对话增量查询 |
| `ez_query` | 快速查询 | `schema_linking → generate_sql → execute_sql` | 简单查询跳过验证 |
| `explore` | 探索 | `parse_nl → hybrid_search → format` | Schema 探索 |
| `metric_query` | 指标查询 | `metric_resolve → generate_sql → validate_sql → execute_sql` | MetricFlow 驱动 |

**完整示例见 §3.4.2**（`gensql_agentic.yml` 和 `reflection.yml`）。

#### 4.9.3 Plan 选择器

```
输入: NL 查询特征向量
    │
    ├── 规则引擎匹配 (L0): 快速路由
    │   has_join=true → gensql_agentic
    │   complexity=high → reflection
    │   is_metric_query=true → metric_query
    │   单表简单查询 → ez_query
    │
    ├── 历史相似匹配 (L1): 查找相似查询使用过的 Plan
    │
    └── LLM 分类兜底 (L2): 边界情况用轻量 LLM 决策
```

选中 Plan 后由 **WorkflowRunner**（§3.4.3）接管执行——初始化 → `node_order` 推进 → `evaluate_result` → 失败处理 → 轨迹持久化。

#### 4.9.4 evaluate_result 质量评估

WorkflowRunner 在每个节点执行后自动评估：

| 评估指标 | 检查内容 | 失败动作 |
|---------|---------|---------|
| `syntax_valid` | SQL 语法是否合法 | 触发失败策略（重试/跳过/中止） |
| `schema_compliant` | 引用表和列是否存在 | 同上 |
| `execution_success` | SQL 执行是否成功 | 触发 reflection（反思修正） |
| `type_compatible` | 条件两侧类型是否兼容 | 警告 + 继续 |

#### 4.9.5 工作流监控与自适应

```
监控指标: 各 Plan 完成率 / 耗时 P95 / 编辑率 / 降级触发率 / API 成本

自适应规则:
├── ez_query 编辑率 > 30% → 自动升级到 gensql_agentic
├── reflection 完成率 < 70% → 拆分查询推荐给用户
└── 某 Plan P95 耗时 > 阈值 → 触发优化告警
```

### 4.10 Subagent 封装系统

#### 4.10.1 概述

Subagent 是将成熟领域封装为 **scoped chatbot** 的机制。每个 Subagent 拥有独立的上下文、工具链和配置，通过 API / Web UI / MCP 三种渠道交付。适合的场景：

- **领域隔离** — 电商分析 Subagent、金融分析 Subagent，互不干扰
- **租户隔离** — 不同团队/客户的 Subagent 访问不同的数据库和指标
- **能力分层** — 面向业务人员的"简单问答"Subagent vs 面向数据分析师的"高级分析"Subagent

#### 4.10.2 Subagent 定义

```yaml
# subagents/ecommerce_analyst.yaml
name: ecommerce_analyst
label:
  zh: 电商数据分析助手
  en: E-commerce Data Analyst
domain: ecommerce
description: 专注于电商业务的日常数据查询和分析

context:
  databases:
    - alias: main_db
      connection_ref: "connections/ecommerce_pg"
  max_turns: 20                    # 最大对话轮次
  enable_sql_edit: true
  enable_execution: true
  enable_multi_candidate: true

capabilities:
  - nl_query                      # 自然语言查询
  - metric_query                  # 指标查询（MetricFlow）
  - explain                       # SQL 解释
  - recommend                     # 分析建议

delivery:
  - type: web                      # Web UI 子页面
    path: /subagents/ecommerce_analyst
  - type: api                      # REST API
    endpoint: /api/v1/subagents/ecommerce_analyst
  - type: mcp                      # MCP Server 注册
    tool_prefix: "ecommerce_"      # 工具名前缀

llm_config:
  model: claude-sonnet-4
  temperature: 0.3
  max_tokens: 4096

access_control:
  allowed_users: ["*"]             # 允许所有用户
  rate_limit: 100                  # 每小时最大请求数
```

#### 4.10.3 Subagent 生命周期

```
┌──────────────────────────────────────────────────────────┐
│ Subagent 生命周期                                         │
│                                                           │
│  创建 (注册定义)                                           │
│    │                                                       │
│    ▼                                                       │
│  初始化 (加载领域配置 + Schema + MetricFlow + Evolvable Context) │
│    │                                                       │
│    ▼                                                       │
│  激活 (注册到 API/MCP/Web 路由)                            │
│    │                                                       │
│    ▼                                                       │
│  运行 (处理查询 → 学习反馈 → 上下文演化循环)               │
│    │                                                       │
│    ├── 查询量监控                                           │
│    ├── 质量评分                                             │
│    └── 上下文版本管理                                       │
│    │                                                       │
│    ▼                                                       │
│  停用/归档                                                  │
└──────────────────────────────────────────────────────────┘
```

#### 4.10.4 Subagent 运行时架构

```
用户请求 (Web / API / MCP)
    │
    ▼
┌───────────────────────────────────────────────┐
│ Subagent Router                                │
│ ├── 根据请求路径/工具名路由到对应 Subagent      │
│ ├── 权限检查                                     │
│ └── 速率限制                                     │
└──────────┬────────────────────────────────────┘
           │
           ▼
┌───────────────────────────────────────────────┐
│ Subagent Instance                               │
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │ Scoped Context                            │   │
│  │ ├── domain: ecommerce                    │   │
│  │ ├── database: main_db                    │   │
│  │ ├── session: <<独立对话>>                 │   │
│  │ ├── EvolvableContext: <<隔离副本>>        │   │
│  │ ├── MetricFlow: <<预加载指标>>            │   │
│  │ └── llm_config: sonnet-4                 │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  核心能力:                                       │
│  ├── nl_query (通过主引擎，但注入 Subagent 上下文)│
│  ├── metric_query (直接调用 MetricFlow)         │
│  └── ...                                        │
└─────────────────────────────────────────────────┘
```

#### 4.10.5 三种交付渠道

| 渠道 | 访问方式 | 适用场景 |
|------|---------|---------|
| **Web UI** | `https://host/subagents/{name}` | 浏览器直接使用，独立聊天界面 |
| **REST API** | `POST /api/v1/subagents/{name}/query` | 嵌入其他系统、自动化脚本 |
| **MCP Server** | 注册为独立 MCP 工具 `{prefix}_nl_query` | Claude Desktop、其他 MCP Host |

**MCP 交付示例：**

```python
from fastmcp import FastMCP

# 为每个 Subagent 创建一个 MCP Server
subagent_mcp = FastMCP("Subagent: E-commerce")

@subagent_mcp.tool()
def ecommerce_nl_query(nl_text: str) -> dict:
    """电商数据分析：将自然语言转换为 SQL 查询"""
    subagent = subagent_manager.get("ecommerce_analyst")
    result = subagent.query(nl_text)
    return result.to_dict()

# 动态注册所有活跃 Subagent
for subagent in subagent_manager.list_active():
    mcp_server = subagent.create_mcp_server()
    mcp_server.register()  # 注册到网关
```

#### 4.10.6 Subagent 与主引擎的关系

```
主引擎 (通用 NL2SQL)
    │
    ├── 为 Subagent 提供核心能力
    │   ├── NL 解析
    │   ├── RAG 混合检索
    │   ├── Schema 管理
    │   └── SQL 验证/执行
    │
    └── Subagent 层
        ├── 隔离：每个 Subagent 独立的 Evolvable Context 副本
        ├── 定制：独立的术语表、指标、规则、对话历史
        ├── 路由：请求按 Subagent 名称分发
        └── 粒度控制：独立的 LLM 配置、权限、速率限制
```

#### 4.10.7 Subagent 与多 Agent 架构的关系

Subagent 是多 Agent 架构的一种**部署形态**：

```
多 Agent 架构 (内部)                 Subagent (对外交付)
┌──────────────────┐              ┌──────────────────────┐
│ Orchestrator     │──编排──►      │ 电商 Subagent        │
│ Agent            │              │ - 独立 Orchestrator  │
├──────────────────┤              │ - 裁剪的 Agent 集合  │
│ NL Agent         │              │ - 隔离的 Evolvable   │
│ Schema Agent     │              │   Context            │
│ SQL Agent        │              │ - 独立的 MCP 工具    │
│ Validate Agent   │              └──────────────────────┘
│ Tool Agent       │
└──────────────────┘              ┌──────────────────────┐
                                  │ 金融 Subagent        │
                                  │ - 同上，不同领域      │
                                  └──────────────────────┘
```

Subagent 本质上是一个**预配置、领域限定的多 Agent 系统实例**，拥有自己的 Orchestrator Agent 和裁剪后的 Agent 集合。

---

### 4.11 多 Agent 系统架构

#### 4.11.1 概述

多 Agent 系统是本系统的**智能调度核心**。它将 NL→SQL 的每个关键阶段封装为独立、自治的 AI Agent，由 Orchestrator Agent 统一协调，实现比固定 Pipeline 更灵活、更鲁棒的查询处理。

**设计目标：**
- **专注** — 每个 Agent 专注于一个职责领域，系统提示精简、工具集明确
- **可协商** — Agent 间可通信、讨论、纠正（例如验证 Agent 发现 SQL 问题后可回传生成 Agent 修正）
- **可插拔** — Agent 可独立升级、替换、A/B 测试
- **可观测** — 每个 Agent 的决策过程可追踪、可审计

#### 4.11.2 Agent 角色定义

| Agent | 身份标识 | 职责 | 工具集 | 依赖 |
|-------|---------|------|-------|------|
| **Orchestrator Agent** | `coordinator` | 任务分解、Agent 选择、中间结果综合、冲突裁决、最终输出组装 | `handoff` 到各 Agent、`synthesize_results` | 所有 Agent |
| **NL Understanding Agent** | `nl_understander` | 解析自然语言、时间实体提取、意图分类、歧义检测 | `parse_language`、`extract_time_entities` | 无 |
| **Schema Retrieval Agent** | `schema_retriever` | RAG 混合检索 Schema、术语匹配、规则匹配、历史查询查找 | `hybrid_search` (LanceDB+BM25)、`lookup_term`、`match_rules` | NL Understanding |
| **SQL Generation Agent** | `sql_generator` | 构建 Prompt、LiteLLM 调用、多候选生成、自愈重试 | `llm_completion` (LiteLLM)、`build_prompt` | Schema Retrieval |
| **Validation Agent** | `sql_validator` | 语法验证、Schema 引用验证、类型检查、执行计划分析、业务规则校验 | `validate_syntax`、`check_schema_refs`、`explain_plan` | SQL Generation |
| **Tool Execution Agent** | `tool_executor` | MCP 工具调用网关、查询执行、结果缓存 | `execute_query` (MCP)、`call_mcp_tool`、`get_schema` | Validation |
| **Feedback Agent** | `feedback_processor` | 收集用户反馈、提取编辑 diff、触发学习管道 | `record_feedback`、`extract_diff`、`trigger_learning` | 最终输出 |

每个 Agent 使用 **OpenAI Agents SDK** 的 `Agent` 类定义：

```python
from agents import Agent, function_tool, handoff

orchestrator_agent = Agent(
    name="Orchestrator",
    instructions="""你是一个 NL→SQL 系统的调度核心。
    1. 接收用户 NL 查询后，分析复杂度
    2. 按需 handoff 给专门 Agent
    3. 汇总各 Agent 结果生成最终输出
    4. 遇到冲突时（如验证 Agent 报错），协调修正""",
    handoffs=[
        handoff(nl_agent, tool_name_override="delegate_nl"),
        handoff(schema_agent, tool_name_override="delegate_schema"),
        handoff(sql_agent, tool_name_override="delegate_sql"),
        handoff(validation_agent, tool_name_override="delegate_validation"),
    ],
    tools=[synthesize_tool],
)

schema_agent = Agent(
    name="SchemaRetriever",
    instructions="使用混合检索 (BM25 + LanceDB) 找到与查询相关的表和列",
    tools=[hybrid_search_tool, lookup_term_tool, match_rules_tool],
)
```

#### 4.11.3 Orchestrator Agent 设计

Orchestrator Agent 是多 Agent 系统的核心决策者，负责：

##### a) 任务分解

```
输入: 用户 NL 查询 + 当前对话上下文
    │
    ▼
复杂度分析:
    ├── 简单查询 → NL Agent → Schema Agent → SQL Agent → Validate Agent
    ├── 中等查询 → 同上 + Validate Agent 可回传 SQL Agent 修正
    ├── 复杂查询 → 同上 + Tool Agent 执行 EXPLAIN → 反馈 SQL Agent 优化
    ├── 模糊查询 → NL Agent 标记歧义 → Orchestrator 返回歧义列表给用户
    │               → 用户澄清后继续
    └── 多步查询 → 分解为子任务序列，每个子任务走完整 Agent 流程
```

##### b) Agent 选择与 Handoff

通过 **OpenAI Agents SDK 的 handoff 机制**移交任务：

```
Orchestrator Agent:
    ├── 分析: 需要时间解析 + 地域过滤 + 指标映射
    ├── handoff → NL Understanding Agent → 返回结构化查询
    ├── handoff → Schema Retrieval Agent → 返回相关表和术语
    ├── handoff → SQL Generation Agent → 返回 SQL 候选
    ├── handoff → Validation Agent → 返回验证报告
    └── Orchestrator 综合 → 调用 Tool Agent 执行 → 最终响应
```

##### c) 冲突裁决

| 冲突场景 | 裁决策略 |
|---------|---------|
| Validation Agent 报语法错误 | 将错误信息注入 SQL Agent 重新生成（自愈） |
| SQL Agent 和 Schema Agent 对表名理解不一致 | Orchestrator 用 Schema 快照验证，纠正后重试 |
| 多候选间用户无明确偏好 | 调用 Tool Agent 执行所有候选取 Top-N 展示 |
| 时间解析有歧义 | 暂停流程，返回歧义选项给用户确认 |

##### d) 人机协作（Human-in-the-Loop）

```
模式 1: 完全自主 — Orchestrator 独立完成全流程，仅返回最终结果
模式 2: 关键节点确认 — 歧义/写操作/高风险查询时暂停，等待用户确认
模式 3: 完全透明 — 每个 Agent 的输出都流式展示，用户可随时介入纠正
```

#### 4.11.4 Agent 间通信与 Handoff 机制

```python
@dataclass
class AgentMessage:
    source: str                    # 发送方 Agent 名
    target: str                    # 接收方 Agent 名
    message_type: str              # request | response | error | correction
    payload: dict                  # 消息体
    context: dict                  # 共享上下文
    trace_id: str                  # 追踪 ID
    timestamp: datetime
```

**Tool Execution Agent 的网关角色**：

所有 Agent 通过 Tool Execution Agent 统一调用 MCP 工具，而非直接调用：
```
各 Agent → Tool Agent (统一限流/缓存/重试) → MCP Database/Knowledge/Vector Server
```
避免每个 Agent 都直接连接外部服务，实现统一的权限控制和审计。

#### 4.11.5 协作模式

| 模式 | 名称 | 描述 | 适用场景 |
|------|------|------|---------|
| 1 | **Pipeline 协作** | 链式单向传递 | 大多数查询（默认） |
| 2 | **回传修正** | Validate Agent 报错 → SQL Agent 修正 → 再验证 | 验证失败后的自愈 |
| 3 | **并行辩论** | 多个 SQL Agent 独立生成 → Orchestrator 综合 | 复杂查询的多候选 |
| 4 | **Agent 协商** | Agent 发现歧义 → Orchestrator 裁决 → 用户确认 | 语义模糊场景 |
| 5 | **多步流水线** | 查询分解为子任务，前一步结果喂给后一步 | 多步骤 ETL 查询 |

#### 4.11.6 与现有模块的关系

| 模块 | 与多 Agent 的关系 |
|------|-----------------|
| **工作流编排 (§4.9)** | 工作流模板 = 预定义的 Agent 协作模式 |
| **Subagent (§4.10)** | Subagent = 领域限定的多 Agent 实例，拥有自己的 Orchestrator |
| **MCP (§3.3)** | Tool Execution Agent 是 MCP Client 的统一网关 |
| **LiteLLM (§4.4.0)** | SQL Agent 和 Orchestrator Agent 的 LLM 后端 |
| **Evolvable Context (§4.2.5)** | 所有 Agent 共享的活知识库 |

#### 4.11.7 OpenAI Agents SDK 映射

| 多 Agent 概念 | SDK 实现 | 本系统中的应用 |
|-------------|---------|--------------|
| Agent 定义 | `Agent(handoffs, tools)` | 每个角色 = 一个 Agent 实例 |
| Handoff | `handoff(agent)` | Orchestrator→子 Agent 移交 |
| 工具绑定 | `function_tool()` | Agent 专属工具集 |
| 护栏 | `InputGuardrail` | 安全检查（Schema 验证、只读检查） |
| Runner | `Runner.run(agent, input)` | 多 Agent 系统入口 |

```python
from agents import Runner

async def process_query(nl_text: str, subagent_name: str = None):
    agent = subagent_manager.get_orchestrator(subagent_name) if subagent_name \
            else orchestrator_agent
    result = await Runner.run(agent, nl_text)
    return result.final_output
```

#### 4.11.8 Agent 可观测性

每个 Agent 决策过程通过 Trace 暴露：

```
Agent Trace: {
    "trace_id": "trace_abc123",
    "orchestrator": { "task_breakdown": [...], "conflict_resolutions": [...] },
    "agents": {
        "nl_understander": { "output": {...}, "latency_ms": 320 },
        "schema_retriever": { "tools_called": [...], "tables_found": [...] },
        "sql_generator": { "model": "claude-sonnet-4", "candidates": 2, "retries": 0 },
        "validation": { "passed": true, "warnings": [], "latency_ms": 45 }
    },
    "total_latency_ms": 3315,
    "llm_cost_usd": 0.012
}
```

Trace 数据实时推送前端（SSE `event: agent_trace`），持久化用于质量分析和自适应优化。

---

### 4.12 内置节点系统 (Built-in Node System)

> **核心理念：** Workflow + Node 双层抽象。Node 定义统一的执行接口（`execute` / `setup_input` / `update_context`）；Workflow 通过 `workflow.yml` 声明 Plan 模板编排成 DAG 拓扑序执行，**LLM 不决定下一步做什么——Harness 决定**。

#### 4.12.1 双层抽象概述

```
Workflow 层 (编排):
    workflow.yml Plan 模板 (reflection / chat_agentic / gensql_agentic / ...)
        │
        ▼
    DAG 拓扑序 (node_order: [A, B, C, ...])
        │
        ▼
Node 层 (执行原子):
    BaseNode ──► AgenticNode ──► 具体 Node (SchemaLinking / GenerateSQL / Execute / ...)
```

**设计目标：**
- **Workflow + Node 双层解耦** — Workflow 只管 DAG 拓扑序，Node 只管单步执行
- **开箱即用** — 核心 Node（SchemaLinkingNode / ExecuteSQLNode / ReflectionNode）内置
- **LLM 不自主跳转** — 执行路径由 workflow.yml 的 `node_order` 决定，可预测、可审计、可复现
- **Harness 内嵌能力** — AgenticNode 提供 session/tool/permission/skill/compaction/streaming，节点实现者无需关心

#### 4.12.2 BaseNode — 统一执行接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class NodeInput:
    query_text: str
    context: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)

@dataclass
class NodeOutput:
    result: Any
    metadata: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

class BaseNode(ABC):
    """所有 Node 的抽象基类——三个统一方法"""

    name: str
    description: str

    @abstractmethod
    async def execute(self, input: NodeInput) -> NodeOutput:
        """执行节点核心逻辑（子类必须实现）"""
        ...

    async def setup_input(self, raw_input: dict) -> NodeInput:
        """从上游 WorkflowRunner 传入的原始数据组装标准 NodeInput。
        内置 Schema 校验、默认值注入、类型转换——节点实现者无需手动处理。
        """
        ...

    async def update_context(self, output: NodeOutput, shared_context: dict) -> dict:
        """将本节点输出写入共享上下文。
        WorkflowRunner 在推进到下一个节点前调用此方法更新全局状态。
        """
        ...
```

#### 4.12.3 AgenticNode — 强化 Harness 节点

AgenticNode 继承自 BaseNode，为**所有涉及 LLM 调用的节点**注入 7 项 Harness 内置能力。节点实现者只需关注业务逻辑，框架能力由 Harness 层统一提供。

```python
class AgenticNode(BaseNode):
    """强化 Harness 节点——继承自 BaseNode，注入 7 项能力"""

    def __init__(self, config: AgenticConfig):
        # ① Session 管理
        self.session = AdvancedSQLiteSession(
            db_path=config.session_db_path,
            auto_save=True,
            session_ttl=config.session_ttl
        )

        # ② Tool 集成（func tools + MCP servers）
        self.tool_registry = ToolRegistry()
        self.tool_registry.register_func_tools(config.func_tools)
        self.tool_registry.register_mcp_servers(config.mcp_servers)

        # ③ 权限管控（三级：allow / deny / ask）
        self.permission_manager = PermissionManager(
            rules=config.permission_rules,  # [{tool: "execute_sql", level: "ask"}, ...]
            default_level=config.default_permission  # "deny" | "ask"
        )

        # ④ 技能系统
        self.skill_manager = SkillManager(skills_dir=config.skills_dir)
        self.skill_manager.register(SkillFuncTool())

        # ⑤ Action 历史追踪
        self.action_history = ActionHistoryManager(max_entries=config.max_action_history)

        # ⑥ 自动上下文压缩（达到 90% token 用量时触发）
        self.compaction_threshold = 0.90
        self.compaction_enabled = config.enable_auto_compaction

        # ⑦ Streaming 执行支持
        self.streaming_enabled = config.enable_streaming
```

**七项能力详解：**

| # | 能力 | 职责 | 节点实现者感知 |
|---|------|------|-------------|
| ① | **AdvancedSQLiteSession** | Memory 按节点类型控制：内置 subagent（gen_sql 等）默认 `memory: false`；chat 节点和用户自定义 subagent 默认 `memory: true`。支持 `:memory:` ephemeral 模式和 `~/.data_engineer/sessions/` 持久化模式，按 `node_name` 隔离存储 | 在 `agent.yml` 声明 `memory: true/false` 或 `memory: ":memory:"` 即可 |
| ② | **Tool 集成** | 统一管理 func tools + MCP servers，按名称路由 | 声明工具名即可，注册/调用由 Harness 处理 |
| ③ | **PermissionManager** | 三级权限：`allow`（静默通过）/ `deny`（拒绝+日志）/ `ask`（弹窗确认） | 工具调用前 Harness 自动检查 |
| ④ | **SkillManager + SkillFuncTool** | 加载 Skill 定义，将 Skill 函数注册为可调用工具 | 声明 `skills: ["my_skill"]` 即可 |
| ⑤ | **ActionHistoryManager** | 记录每次 `execute()` 的输入/输出/耗时/错误 | 零感知——Harness 自动写入 |
| ⑥ | **Auto-Compaction** | 上下文达到 90% token 上限时自动摘要压缩 | 零感知——Harness 自动触发 |
| ⑦ | **Streaming 执行** | SSE 流式推送中间 token | 设置 `enable_streaming: true` 即可 |

**AgenticNode 的 setup_input 增强：**

```python
class AgenticNode(BaseNode):
    async def setup_input(self, raw_input: dict) -> NodeInput:
        # 基类逻辑：Schema 校验 + 默认值 + 类型转换
        node_input = await super().setup_input(raw_input)

        # Agentic 增强：
        # ① 恢复或创建 Session
        session_id = raw_input.get("session_id")
        if session_id and not self.session.is_active(session_id):
            node_input.context["session_summary"] = self.session.get_summary(session_id)
        
        # ② 检查工具权限（预检，避免执行时才报错）
        for tool_name in node_input.config.get("tools", []):
            permission = self.permission_manager.check(tool_name, node_input)
            if permission == "deny":
                raise PermissionDeniedError(f"Tool '{tool_name}' denied by policy")

        # ③ 自动压缩（如果上下文接近 90% token 上限）
        if self.compaction_enabled and self._token_usage() >= self.compaction_threshold:
            node_input = await self._auto_compact(node_input)

        return node_input
```

#### 4.12.4 内置 Node 继承树

```
BaseNode (execute / setup_input / update_context)
    │
    ├── AgenticNode (7 项 Harness 能力)
    │   ├── SchemaLinkingNode      NL→Schema 实体链接 [内置]
    │   ├── GenerateSQLNode        NL→SQL 生成 (LiteLLM) [内置]
    │   ├── ExecuteSQLNode         安全 SQL 执行 [内置]
    │   ├── ValidateSQLNode        SQL 语法/类型/引用验证 [内置]
    │   ├── ParseNLNode            NL 解析 [内置]
    │   ├── ReflectionNode         SQL 质量反思—修正循环 [内置]
    │   ├── HybridSearchNode       RAG 混合检索 [内置]
    │   ├── MetricResolveNode      MetricFlow 指标解析 [内置]
    │   └── ExplainPlanNode        执行计划获取与解读 [内置]
    │
    └── PlainNode (非 LLM 节点，无需 Agentic 能力)
        ├── DialectTranslateNode   sqlglot 方言转换 [内置]
        ├── FormatSQLNode          格式化 [内置]
        └── (用户自定义 Node)       任意扩展
```

**内置 Node 清单：**

| Node ID | 类名 | 基类 | 类别 | 用途 |
|---------|------|------|------|------|
| `schema_linking` | SchemaLinkingNode | AgenticNode | 检索 | NL→Schema 实体链接 |
| `execute_sql` | ExecuteSQLNode | AgenticNode | 执行 | 安全 SQL 执行 + 结果返回 |
| `generate_sql` | GenerateSQLNode | AgenticNode | 生成 | NL→SQL |
| `validate_sql` | ValidateSQLNode | AgenticNode | 验证 | 多维度 SQL 验证 |
| `parse_nl` | ParseNLNode | AgenticNode | 解析 | 语言/时间/意图/歧义 |
| `reflection` | ReflectionNode | AgenticNode | 反思 | 执行失败后分析并修正 SQL |
| `hybrid_search` | HybridSearchNode | AgenticNode | 检索 | RAG (BM25+LanceDB+RRF) |
| `metric_resolve` | MetricResolveNode | AgenticNode | 语义 | MetricFlow 指标→SQL |
| `explain_plan` | ExplainPlanNode | AgenticNode | 分析 | EXPLAIN 并解读 |
| `dialect_translate` | DialectTranslateNode | PlainNode | 转换 | sqlglot 方言转换 |
| `format_sql` | FormatSQLNode | PlainNode | 工具 | SQL 格式化 |

#### 4.12.5 SchemaLinkingNode（内置，继承 AgenticNode）

```python
class SchemaLinkingNode(AgenticNode):
    name = "schema_linking"
    description = "NL→Schema 实体链接，自动享受 session/tool/permission/skill/compaction/streaming"

    def __init__(self, schema_metadata_rag: SchemaMetadataRAG, config: AgenticConfig):
        super().__init__(config)
        self.rag = schema_metadata_rag

    async def execute(self, input: NodeInput) -> NodeOutput:
        # Harness 已在 setup_input 中处理了 session/permission/compaction
        entities = await self._extract_entities(input.query_text)
        candidates = await self._hybrid_search(entities, input.context.get("domain"))
        linked = FKExpander().expand(candidates)
        result = ColumnDisambiguator().resolve(linked)
        return NodeOutput(result=result, metadata={"tables_linked": len(result.tables)})
```

#### 4.12.6 Node 注册表

```python
class NodeRegistry:
    _nodes: dict[str, BaseNode] = {}

    def register(self, node: BaseNode):
        self._nodes[node.name] = node

    def get(self, name: str) -> BaseNode:
        return self._nodes[name]

# 初始化
for node in [SchemaLinkingNode(...), ExecuteSQLNode(...), GenerateSQLNode(...),
             ValidateSQLNode(...), ParseNLNode(...), ReflectionNode(...),
             HybridSearchNode(...), MetricResolveNode(...), ExplainPlanNode(...),
             DialectTranslateNode(...), FormatSQLNode(...)]:
    node_registry.register(node)
```

#### 4.12.7 与现有模块的关系

| 已有模块 | Node | 关系 |
|---------|------|------|
| SchemaMetadataRAG | SchemaLinkingNode (AgenticNode) | SchemaMetadataRAG 为底层存储，Node 封装完整检索链 |
| Tool Execution Agent | ExecuteSQLNode (AgenticNode) | Tool Agent 调用 Node，Node 内化安全逻辑 |
| SQL Generation Engine | GenerateSQLNode (AgenticNode) | Prompt + LiteLLM 封装为 Node |
| 持续学习 (§4.6) | ReflectionNode (AgenticNode) | 失败时自动触发反思-修正循环 |

---

## 5. 数据流

### 5.1 主查询流程

```
用户输入 NL 查询
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ 0. 工作流选择 + Orchestrator Agent 初始化                         │
│    ├── 特征提取（长度、实体数、JOIN/聚合关键词等）               │
│    ├── 路由决策（规则 / 历史相似 / LLM 兜底）                   │
│    ├── 输出: 选中的 WorkflowTemplate (协作模式)                  │
│    └── Orchestrator Agent 根据模板初始化 Agent 协作模式          │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Orchestrator Agent — 多 Agent 协作调度                            │
│                                                                   │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐    │
│  │ handoff →   │    │ handoff →    │    │ handoff →        │    │
│  │ NL Agent    │───►│ Schema Agent │───►│ SQL Agent        │    │
│  │             │    │              │    │ (LiteLLM路由)    │    │
│  │ ·语言检测   │    │ ·RAG混合检索 │    │ ·Prompt构建      │    │
│  │ ·时间解析   │    │ ·BM25+LanceDB│    │ ·多候选生成      │    │
│  │ ·意图分类   │    │ ·术语/规则   │    │ ·流式输出        │    │
│  │ ·歧义检测   │    │ ·Evolvable   │    │ ·自愈重试        │    │
│  │ ·实体提取   │    │  Context     │    │                  │    │
│  └─────────────┘    └──────────────┘    └────────┬─────────┘    │
│                                                  │              │
│                          ┌───────────────────────┘              │
│                          ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ handoff → Validation Agent                                │   │
│  │  ├── 通过 → 继续                                         │   │
│  │  └── 失败 → 回传 SQL Agent 修正 (协作模式 2)              │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ handoff → Tool Execution Agent (MCP 工具网关)             │   │
│  │  ├── 调用 execute_query → 执行 SQL                       │   │
│  │  └── 返回 QueryResult                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  可选协作模式:                                                     │
│  ├── 模式 3 (并行辩论): 3 个 SQL Agent 并行 → Orchestrator 综合  │
│  ├── 模式 4 (Agent 协商): 发现歧义 → 用户确认 → 继续             │
│  └── 模式 5 (多步流水线): 子任务序列，每步走完整 Agent 流程       │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│ 6. 返回用户                                    │
│    ├── SQL 展示（流式输出过程已展示）           │
│    ├── Agent Trace（可观测性数据）              │
│    ├── 验证报告（警告、建议）                   │
│    ├── 结果预览表格                             │
│    ├── 歧义待确认（如有）                       │
│    ├── 多候选（如有）                           │
│    └── （附带工作流/协作模式信息）               │
└─────────────────────────────────────────────────┘
```

### 5.2 反馈学习流程

```
用户编辑 SQL / 评分 / 反馈
    │
    ▼
┌──────────────────────────────────────────────┐
│ 反馈收集                                       │
│ ├── 记录用户编辑 diff                          │
│ ├── 记录最终确认 SQL                            │
│ └── 记录评分 + 文本反馈                         │
└──────────────────┬───────────────────────────────┘
                   │
         ┌─────────▼─────────┐
         │ 异步管道          │
         └─────────┬─────────┘
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│查询对入库│ │评分聚合  │ │模式分析  │
│(NL, SQL) │ │(质量统计)│ │(聚类修正)│
└──────────┘ └──────────┘ └─────┬────┘
                                │
                          ┌─────▼────┐
                          │规则提取  │
                          │(人工审核)│
                          └──────────┘
```

---

## 6. API 设计

### 6.1 核心 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/query` | 提交 NL 查询，流式返回 SQL 生成过程 |
| POST | `/api/v1/query/stream` | 同上，SSE 流式接口 |
| POST | `/api/v1/query/candidates` | 生成多个候选 SQL |
| POST | `/api/v1/query/explain` | 获取 SQL 解释（自然语言说明 SQL 逻辑） |
| POST | `/api/v1/execute` | 执行已生成的 SQL（只读） |
| POST | `/api/v1/feedback` | 提交用户反馈 |
| GET  | `/api/v1/sessions/{id}` | 获取对话会话 |
| GET  | `/api/v1/sessions/{id}/versions` | 获取会话的版本历史 |

### 6.2 Schema 管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/databases/test` | 测试数据库连接 |
| POST | `/api/v1/databases/connect` | 添加数据库连接 |
| GET  | `/api/v1/databases` | 列出已连接数据库 |
| GET  | `/api/v1/schema/{db_id}` | 获取 Schema 快照 |
| POST | `/api/v1/schema/{db_id}/refresh` | 刷新 Schema 缓存 |
| GET  | `/api/v1/schema/{db_id}/search?q=...` | 搜索表和列 |

### 6.3 领域管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/v1/domains` | 列出所有领域 |
| POST | `/api/v1/domains` | 创建新领域 |
| PUT  | `/api/v1/domains/{id}` | 更新领域配置 |
| GET  | `/api/v1/domains/{id}/glossary` | 获取领域术语表 |
| POST | `/api/v1/domains/{id}/glossary` | 添加术语 |
| GET  | `/api/v1/domains/{id}/rules` | 获取业务规则 |
| POST | `/api/v1/domains/{id}/rules` | 添加业务规则 |
| POST | `/api/v1/domains/detect` | 自动检测 NL 所属领域 |

### 6.4 MCP 协议 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/v1/mcp/servers` | 列出所有已注册的 MCP Server |
| POST | `/api/v1/mcp/servers/register` | 注册新的 MCP Server |
| DELETE | `/api/v1/mcp/servers/{id}` | 移除 MCP Server |
| GET  | `/api/v1/mcp/servers/{id}/tools` | 获取 MCP Server 的可用工具列表 |
| GET  | `/api/v1/mcp/servers/{id}/resources` | 获取 MCP Server 的可用资源列表 |
| POST | `/api/v1/mcp/tools/{tool_id}/call` | 直接调用 MCP 工具（调试用） |
| GET  | `/api/v1/mcp/resources/{resource_uri}` | 读取 MCP 资源内容 |

### 6.5 工作流编排 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/v1/workflows` | 列出所有可用工作流模板 |
| POST | `/api/v1/workflows/custom` | 创建自定义工作流模板 |
| POST | `/api/v1/workflows/simulate` | 模拟路由决策（返回会选中的工作流） |
| GET  | `/api/v1/workflows/stats` | 工作流执行统计（各模板使用率、成功率） |
| GET  | `/api/v1/workflows/{id}/history` | 特定工作流的执行历史 |

### 6.6 学习与反馈 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/v1/learning/stats` | 学习统计数据 |
| GET  | `/api/v1/learning/query-pairs` | 查询历史对列表 |
| POST | `/api/v1/learning/rule-candidates/review` | 审核推荐规则 |
| GET  | `/api/v1/learning/quality` | 质量趋势数据 |
| POST | `/api/v1/learning/rag/refresh` | 手动触发 RAG 索引刷新 |

### 6.7 流式接口格式（SSE）

```
事件流:
event: workflow      # 工作流选择结果（首个事件）
data: {"workflow_id": "standard", "name": "标准查询", "estimated_cost": "medium", "stages": ["parse", "retrieve", "generate", "validate", "execute"]}

event: stage         # 工作流阶段变更
data: {"stage": "retrieve", "status": "started", "progress": 0.3}

event: token         # LLM 生成的 token（实时显示 SQL）
data: {"text": "SELECT", "type": "sql"}

event: token
data: {"text": " * ", "type": "sql"}

event: candidate     # 生成完成一个候选
data: {"id": "c1", "sql": "SELECT * FROM ...", "confidence": 0.92, "workflow": "standard"}

event: mcp_call      # MCP 工具调用记录（调试/监控用）
data: {"tool": "execute_read_query", "server": "postgres_main", "duration_ms": 45}

event: validation    # 验证结果
data: {"passed": true, "warnings": [...], "score": 0.95}

event: execution     # 执行结果
data: {"columns": [...], "rows": [...], "row_count": 100, "execution_time_ms": 230}

event: ambiguity     # 检测到歧义
data: {"aspect": "time_range", "options": [...], "question": "..."}

event: agent_trace   # Agent 决策过程追踪（多 Agent 的可观测性）
data: {"agent": "nl_understander", "status": "completed", "output": "{...}", "latency_ms": 320}

event: agent_trace
data: {"agent": "schema_retriever", "tools_called": ["hybrid_search", "lookup_term"], "tables_found": ["orders", "users"]}

event: agent_handoff # Agent 间 Handoff 事件
data: {"from": "orchestrator", "to": "sql_generator", "reason": "schema_ready", "context_summary": "..."}

event: collaboration # 协作模式切换
data: {"mode": "parallel_debate", "agents": ["sql_perf", "sql_readable", "sql_complete"], "status": "started"}

event: rag_result    # RAG 检索摘要（展示给用户的检索过程）
data: {"matched_tables": ["orders", "users"], "matched_terms": ["客单价"], "similar_queries_found": 2}

event: error         # 错误
data: {"code": "...", "message": "...", "stage": "generate"}

event: done          # 完成
data: {"query_id": "...", "workflow": "standard", "total_time_ms": 3200, "cost_estimate_usd": 0.015}
```

---

## 7. 数据模型与存储

### 7.1 存储选型

| 用途 | 技术选型 | 说明 |
|------|---------|------|
| 主应用数据 | SQLite (本地) / PostgreSQL (生产) | 会话、反馈、配置 |
| Session 存储 | SQLite（持久化） / :memory:（ephemeral） | `~/.data_engineer/sessions/`；Sub-agent 默认 :memory:，顶层 chat 持久化 |
| Memory 隔离 | 按 node_name 分目录的 SQLite | `~/.data_engineer/memory/{node_name}/`，不同节点类型互不干扰 |
| Workflow Trace | JSONL 文件 | `~/.data_engineer/traces/{date}/{workflow_id}.jsonl` |
| Schema 缓存 | SQLite + 内存缓存 | 快速访问 Schema |
| 向量存储 | **LanceDB** (主选) / pgvector (备选) | 语义搜索 Embedding，RAG 密集检索；列式存储，支持多模态和快速过滤 |
| BM25 全文索引 | SQLite FTS5 / Tantivy (生产) | RAG 稀疏检索（关键词精确匹配） |
| 领域配置 | YAML 文件 + 数据库 | 版本控制友好 |
| Evolvable Context | LanceDB + SQLite | 活知识库持久化（Schema 元数据+参考 SQL+语义模型+指标） |
| LLM 响应缓存 | Redis (可选) / SQLite | 减少重复 API 调用 |
| MCP 注册表 | SQLite / 内存 | MCP Server 注册信息和状态 |
| 工作流定义 | YAML 文件 + 数据库 | 工作流模板持久化 |
| Subagent 配置 | YAML + SQLite | Subagent 定义和状态存储 |

### 7.2 核心表结构

```sql
-- 会话管理
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id       VARCHAR(64) NOT NULL,
    database_id     VARCHAR(64),
    title           VARCHAR(256),
    context_summary TEXT,
    turn_count      INT DEFAULT 0,
    status          VARCHAR(16) DEFAULT 'active',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- 对话轮次
CREATE TABLE turns (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID REFERENCES sessions(id),
    turn_index          INT NOT NULL,
    nl_input            TEXT NOT NULL,
    nl_normalized       TEXT,
    sqr_json            JSONB,
    selected_sql        TEXT,
    selected_candidate_id VARCHAR(64),
    execution_result    JSONB,
    user_rating         SMALLINT,
    status              VARCHAR(16) DEFAULT 'completed',
    created_at          TIMESTAMP DEFAULT NOW()
);

-- SQL 候选
CREATE TABLE sql_candidates (
    id              VARCHAR(64) PRIMARY KEY,
    turn_id         UUID REFERENCES turns(id),
    sql_text        TEXT NOT NULL,
    confidence      FLOAT,
    generation_mode VARCHAR(32),  -- primary | alt_1 | alt_2
    validation_report JSONB,
    is_selected     BOOLEAN DEFAULT FALSE,
    rank            SMALLINT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- SQL 版本（编辑历史）
CREATE TABLE sql_versions (
    id              BIGSERIAL PRIMARY KEY,
    turn_id         UUID REFERENCES turns(id),
    version_number  INT NOT NULL,
    sql_text        TEXT NOT NULL,
    parent_version  INT,
    source          VARCHAR(16),  -- generated | human_edit | auto_correct
    diff_from_parent TEXT,
    user_note       TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 查询配对（学习）
CREATE TABLE query_pairs (
    id              BIGSERIAL PRIMARY KEY,
    domain_id       VARCHAR(64) NOT NULL,
    session_id      UUID REFERENCES sessions(id),
    nl_original     TEXT NOT NULL,
    nl_normalized   TEXT,
    sql_final       TEXT NOT NULL,
    sql_hash        VARCHAR(64),
    language        VARCHAR(10),
    intent          VARCHAR(32),
    user_rating     SMALLINT,
    edit_count      INT DEFAULT 0,
    execution_count INT DEFAULT 0,
    execution_time_ms_avg INT,
    source          VARCHAR(16),
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- 数据库连接配置
CREATE TABLE database_connections (
    id              VARCHAR(64) PRIMARY KEY,
    alias           VARCHAR(128),
    db_type         VARCHAR(32) NOT NULL,  -- postgresql | mysql
    host            VARCHAR(256),
    port            INT,
    database_name   VARCHAR(128),
    username        VARCHAR(128) ENCRYPTED,
    password        VARCHAR(256) ENCRYPTED,
    ssl_enabled     BOOLEAN DEFAULT FALSE,
    schema_cache    JSONB,
    last_synced_at  TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 反馈记录
CREATE TABLE feedback_log (
    id              BIGSERIAL PRIMARY KEY,
    turn_id         UUID REFERENCES turns(id),
    candidate_id    VARCHAR(64),
    rating          SMALLINT,
    feedback_text   TEXT,
    diff_ops        JSONB,        -- 编辑操作序列
    execution_time_ms INT,
    execution_success BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 规则候选（待审核）
CREATE TABLE rule_candidates (
    id                  BIGSERIAL PRIMARY KEY,
    domain_id           VARCHAR(64) NOT NULL,
    title               VARCHAR(256),
    description         TEXT,
    pattern             TEXT,
    sql_template        TEXT,
    source              VARCHAR(32),  -- auto_extracted | manual
    confidence          FLOAT,
    support_count       INT,          -- 支持该规则的样本数
    status              VARCHAR(16) DEFAULT 'pending',  -- pending | approved | rejected
    suggested_by        VARCHAR(64),
    reviewed_at         TIMESTAMP,
    created_at          TIMESTAMP DEFAULT NOW()
);
```

---

## 8. LLM Prompt 策略体系

### 8.1 分层 Prompt 策略

根据查询复杂度动态选择 Prompt 模板：

| 级别 | 适用场景 | Prompt 策略 | 推荐 Model |
|------|---------|-------------|-----------|
| L1 | 简单查询(单表WHERE) | 直接生成，无需 few-shot | Claude Haiku |
| L2 | 中等等(多表JOIN+聚合) | Schema + 相关术语 + 1个 few-shot | Claude Sonnet |
| L3 | 复杂查询(CTE+窗口) | 完整 Schema + 术语 + 规则 + 2-3 few-shot | Claude Opus |
| L4 | 自愈重试 | 原始 Prompt + 错误信息 + 修正指令 | Claude Opus/Sonnet |

### 8.2 防止幻觉的关键指令

每次 Prompt 中嵌入以下约束：

```
## 引用约束
1. 只使用上述 Schema 中列出的表和列名
2. 如果问题涉及的表不在 Schema 列表中，请回答"无法在已知表中找到{表名}"
3. 不要编造列名、表名或 JOIN 条件
4. 如果一个列名在多个表中出现，必须使用 表名.列名 格式
5. 对于聚合查询，注意 NULL 值的处理方式
```

### 8.3 错误修正 Prompt

当验证失败时，将错误信息注入新 Prompt：

```
之前的 SQL: {sql_text}
执行/验证错误: {error_message}

请修正这个 SQL。特别注意：
- {具体的修正提示}
- 确保所有引用的表和列存在
- 保持与原查询语义一致
```

### 8.4 多轮对话 Context 压缩

当对话超过 3 轮时，启用上下文摘要：

```
对话历史摘要：
- 用户之前查询了 {topic1}，使用了 {table1} 的 {column1}
- 第二轮对结果进行了 {filter1} 过滤
- 上一轮生成了 {sql_prev}

用户最新问题：{current_question}
```

---

## 9. 安全与隐私

### 9.1 SQL 执行安全

| 安全措施 | 说明 |
|---------|------|
| 只读默认 | 系统默认只允许 SELECT 语句执行 |
| DDL/DML 拦截 | INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 等被拦截 |
| 写操作确认 | 如需写操作，必须用户二次确认 + API 特殊参数 |
| 超时保护 | 所有查询设置 Statement Timeout（默认 30s） |
| 行数限制 | 默认 LIMIT 100，防止意外全表返回 |
| 资源隔离 | 系统连接池与业务连接池分离 |

### 9.2 数据隐私

| 措施 | 说明 |
|------|------|
| Schema 脱敏 | 发送到外部 LLM 前可对表名/列名进行脱敏替换 |
| 敏感列标记 | 在 Schema 中标记 PII 列（phone, email, id_card），LLM Prompt 中排除或做特殊处理 |
| 数据不发送 | 业务数据（行内容）不会发送到 LLM API，仅发送 Schema 结构 |
| 本地选项 | 关键技术路径支持本地模型作为备选 |
| 日志脱敏 | 日志系统自动过滤敏感字段 |

### 9.3 连接安全

- 数据库密码加密存储（Fernet 或 AES-256）
- 支持 SSL/TLS 连接数据库
- API 访问支持 Token 认证

---

## 10. 评估体系

### 10.1 评估维度

| 维度 | 指标 | 目标 (MVP) |
|------|------|-----------|
| **准确率** | 生成的 SQL 语法正确且语义正确 | >80% |
| **Schema 合规** | 引用的表和列 100% 存在于数据库中 | 100% |
| **用户满意度** | 用户评分 ≥ 4 (5分制) 的比例 | >70% |
| **首候选中选率** | 用户选择第一个候选的比例 | >60% |
| **零编辑率** | 用户直接使用生成 SQL 不修改的比例 | >50% |
| **执行成功率** | SQL 执行不报错的比例 | >90% |
| **响应时间** | 从提交到首 token 的时间 | <2s |
| **学习提升率** | 相同查询第二次比第一次准确率提升 | >10% |

### 10.2 评估方法

1. **自动验证集** — 预置 (NL, SQL 标准答案) 测试集，每次 Schema 变更后运行回归
2. **执行等价性** — 对比生成 SQL 与标准答案的执行结果是否一致
3. **A/B 测试** — 同时展示多个候选让用户选择，记录选择分布
4. **人工标注** — 定期抽样审核生成质量
5. **执行计划分析** — 检查是否产生了低效查询（全表扫描等）

### 10.3 测试数据集

MVP 阶段需构建验证集：

```
- 每个领域至少 50 条 (NL, SQL) 测试对
- 覆盖:
  - 简单查询 (20%)
  - 多表 JOIN (25%)
  - 聚合 + GROUP BY (20%)
  - 时间条件 (15%)
  - 子查询 / CTE (10%)
  - 边缘情况 (10%) — NULL 处理、边界值、复杂条件
```

---

## 11. MVP 路线图

### 阶段划分

#### 阶段 1：基础骨架（Week 1-2）

- [x] 项目初始化（结构、配置、CI）
- [x] **核心数据模型** — 实现所有核心 dataclass 和 ORM 模型
- [x] **数据库连接管理** — 4/11 种数据库适配器（SQLite/DuckDB + PostgreSQL/MySQL 驱动接口）
- [x] **Schema 提取器** — 多方言 Schema 内省 (3/11: SQLite/DuckDB + PG 信息模式)
- [x] **Node 系统** — BaseNode (execute/setup_input/update_context) + AgenticNode (7 能力框架骨架) + ExecuteSQLNode 完整实现
- [x] **Harness 基础** — WorkflowRunner 引擎 + workflow.yml Plan 模板 + agent.yml 配置驱动 (6/8 模块)
- [x] **FastMCP Server 基础** — 搭建 Database MCP Server（数据类定义），暴露 Schema 资源和查询工具
- [x] **基础 API** — FastAPI 项目搭建，6 端点 (health/domains/databases/schema)
- [x] **种子测试** — 5 测试文件, 30 用例通过，使用真实 SQLite

#### 阶段 2：NL → SQL 管道（Week 3-4）

- [x] **NL 解析器** — 语言检测、时间解析、意图分类、歧义检测
- [x] **Prompt 构建器** — 多级 Prompt 模板 (L1-L4)、上下文组装、Token 预算管理
- [x] **LiteLLM 路由层** — RouterConfig + ProviderConfig + 3 层 Fallback + CostTracker + Mock 模式
- [x] **LLM 调用器** — 流式/非流式 API 调用（LiteLLM 统一）、多候选生成 (SQLGenerator)
- [x] **SQL 验证器** — 语法验证 (sqlglot)、Schema 引用验证、类型检查 (Step 1-3)
- [x] **SQL 执行器** — 只读执行 (13 种写关键词拦截)、多驱动支持、EXPLAIN 集成
- [x] **自愈重试** — 错误分析和自动修正 (ErrorAnalysis + max 3 轮重试)
- [x] **FastMCP Client 集成** — 基础 MCP Registry + DB Server 数据类定义 (完整 Client 推至 Phase 3)

#### 阶段 3：领域知识引擎 + RAG + 工作流（Week 5-7）

- [ ] **领域配置系统** — YAML 配置加载、领域切换
- [ ] **术语表管理** — 术语 CRUD、映射解析、智能匹配
- [ ] **业务规则引擎** — 规则匹配和执行
- [ ] **LanceDB 向量存储** — LanceDB 初始化、Embedding 索引构建、相似搜索
- [ ] **BM25 稀疏检索** — 基于 SQLite FTS5 / Tantivy 的关键词搜索
- [ ] **RAG 混合检索融合** — LanceDB + BM25 的 RRF 融合引擎 + 动态 α 权重调节
- [ ] **Knowledge MCP Server** — 暴露术语和规则的 MCP 资源/工具（FastMCP）
- [ ] **Schema 分层检索** — LanceDB 向量 + BM25 + RRF 融合 + 外键扩展
- [ ] **历史查询对缓存** — 完全匹配 + 语义匹配（LanceDB）
- [ ] **MetricFlow 语义层基础** — MetricFlow 配置、指标定义、SQL 生成
- [ ] **Evolvable Context 雏形** — Schema 元数据 + 参考 SQL + 语义模型的自动采集
- [ ] **工作流引擎基础** — 工作流模板定义、DAG 解析器、节点调度
- [ ] **路由决策器** — 特征提取 + 规则引擎路由 + 历史相似路由
- [ ] **预定义工作流模板** — express / standard / deep / self_heal / explore

#### 阶段 4：前端 + Subagent（Week 8-10）

- [ ] **Streamlit Web UI** — 基础界面布局（查询输入、SQL 展示、结果预览）
- [ ] **对话管理 UI** — 历史列表、版本时间轴
- [ ] **工作流状态指示器** — 显示当前工作流类型和各阶段进度
- [ ] **MCP Server 对外暴露** — 通过 FastMCP 将 NL2SQL 暴露为 MCP 工具供外部使用
- [ ] **多 Agent 系统核心** — Orchestrator Agent 实现、Agent 角色定义、Handoff 流程（基于 OpenAI Agents SDK）
- [ ] **Agent 协作模式** — Pipeline 协作、回传修正、并行辩论、Agent 协商、多步流水线
- [ ] **Subagent 系统** — Subagent 定义、隔离上下文、三种交付渠道（API/Web/MCP）
- [ ] **Agent 可观测性** — Trace 收集、SSE agent_trace 事件、Agent 决策过程展示
- [ ] **反馈 UI** — 评分、文本反馈、一键纠错
- [ ] **多候选切换组件** — Tab/卡片切换
- [ ] **MetricFlow 指标查询 UI** — 业务指标可视化查询界面

#### 阶段 5：持续学习与完善（Week 11-14）

- [ ] **反馈收集系统** — 全数据采集管道
- [ ] **查询对入库** — (NL, SQL) 存储和索引（含用于 RAG 训练的 Embedding）
- [ ] **模式分析和规则提取** — 聚类、归纳、推荐
- [ ] **规则审核 UI** — 审核和确认规则
- [ ] **Evolvable Context 演化引擎** — 自动捕获 Schema 变更、指标更新、反馈模式
- [ ] **Learning MCP Server** — 暴露反馈记录和规则提取工具（FastMCP）
- [ ] **Subagent 自适应** — 基于使用量和工作流效果自动调整 Subagent 配置
- [ ] **MetricFlow 深度集成** — 指标定义自动从术语表生成，跨方言 SQL 验证
- [ ] **工作流自适应优化** — 根据用户编辑率自动调整工作流选择
- [ ] **RAG 索引自动刷新** — Schema 变更时触发 LanceDB 重建
- [ ] **Dialect Adapter 完善** — L3/L4 适配器完善（BigQuery/Redshift/ClickHouse/Databricks/Trino sqlglot 方言转换验证）
- [ ] **Node 生态** — 用户自定义 Node 开发文档 + 示例模板
- [ ] **质量仪表板** — 指标可视化（含工作流/RAG/Subagent 效果指标）
- [ ] **端到端测试** — 完整流程测试（含 MCP + 多数据库 + 多 LLM 集成测试）
- [ ] **性能优化** — 缓存调优、Prompt 优化、LanceDB 检索延迟优化

### MVP 里程碑

```
M1 (Week 2)  — Node 系统 + SchemaLinkingNode/ExecuteSQLNode + 11 DB 内置适配器 + FastMCP 可用
M2 (Week 4)  — NL → SQL 基础管道跑通（LiteLLM 多提供商），FastMCP 集成完成
M3 (Week 7)  — 领域知识引擎 + RAG (LanceDB) + MetricFlow 语义层 + 工作流编排可用
M4 (Week 10) — Web UI (Streamlit) + 多 Agent 系统 + Subagent 系统 + MCP 双向架构可用
M5 (Week 14) — Evolvable Context 演化循环闭环 + 多数据库适配完成，MVP 冻结
```

---

## 12. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| **LLM 幻觉生成不存在的 Schema 对象** | 生成不可用 SQL | 高 | Schema 验证层严格校验；N 次重试机制 |
| **中文时间表达解析不准确** | 时间过滤错误 | 高 | 规则优先 + LLM 兜底 + 最终用户确认 |
| **大 Schema 下检索遗漏关键表** | JOIN 不完整 | 中 | 分层策略 + 外键扩展 + 用户可手动指定表 |
| **多轮对话上下文漂移** | 后续查询偏离原始意图 | 中 | 上下文摘要 + 版本管理 + 用户可重置上下文 |
| **用户编辑 SQL 但系统理解偏差** | 错误学习 | 中 | 规则提取需人工审核 + 仅生效高置信度规则 |
| **LLM API 成本超预期** | 项目可持续性 | 中 | 多层缓存 + 本地小模型兜底 + 查询模板匹配 |
| **不同 SQL 方言差异处理不当** | 生成语法错误 | 中 | 方言感知 Prompt + 方言特定语法验证器 |
| **数据隐私泄露（Schema 含敏感信息）** | 合规风险 | 低 | 脱敏选项 + 敏感列标记 + 数据零发送原则 |
| **用户过度依赖系统不审查 SQL** | 错误决策 | 低 | 验证报告 + 警告高亮 + 只读默认 + 建议审查 |
| **MCP Server 故障/不可用** | 整个查询管道中断 | 中 | MCP Server 健康检查 + 熔断机制 + 降级到直连模式 |
| **RAG 混合检索融合参数不当** | BM25 或向量单边主导，检索质量下降 | 中 | 动态 α 调节 + 自动 A/B 测试不同融合策略 |
| **工作流路由误判** | 简单查询走了深度管道（浪费），复杂查询走了快速管道（失败率高） | 中 | 兜底 LLM 分类 + 降级机制 + 自适应修正规则 |
| **MCP 协议版本不兼容** | 工具调用失败 | 低 | 协议版本协商 + 兼容层 + 回退到 HTTP 直连 |
| **MetricFlow 指标定义错误** | 生成的指标 SQL 语义错误 | 中 | MetricFlow 生成后再经 Dialect Adapter 验证 + 执行预览确认 |
| **Subagent 上下文膨胀** | Subagent 长期运行积累过多上下文，LLM 质量下降 | 中 | 上下文摘要 + 定期归档 + 轮次上限 + 显式重置能力 |
| **LanceDB 索引与 Schema 不同步** | RAG 检索返回过期 Schema 信息 | 中 | DDL 变更监听触发器 + 增量索引更新 + 版本号校验 |
| **LiteLLM 提供商不稳定/限流** | 查询超时或失败 | 中 | 多提供商 Fallback 链 + 本地模型兜底 + 请求排队 |
| **Orchestrator Agent 任务分解错误** | 复杂查询分解为不合理子任务，结果偏差 | 中 | 多种分解策略 + 执行后验证 + 用户反馈纠正 |
| **Agent Handoff 循环/死锁** | Agent 间反复移交不决，无法产出结果 | 低 | 最大 Handoff 深度限制 + 超时熔断 + 降级到单 Agent 模式 |
| **多 Agent 成本叠加** | 每个 Agent 调用 LLM，总成本数倍于单次 | 中 | Orchestrator 按需调用 + Agent 结果缓存 + 简单查询走少 Agent 模式 |

---

## 附录

### A. 技术栈建议

> **基础语言：** Python 3.12+ — 使用最新类型注解（`type` 联合语法、`@dataclass`、`Self` 类型）

| 层次 | 技术选择 | 理由 |
|------|---------|------|
| 后端框架 | **FastAPI** | 异步支持、Streaming 响应、自动 OpenAPI 文档 |
| AI Agent SDK | **OpenAI Agents SDK** | 构建 Subagent 的 agent 编排、handoff、护栏机制 |
| LLM 统一路由 | **LiteLLM** | 统一 10+ 提供商（OpenAI/Claude/Gemini/DeepSeek/Qwen 等），模型路由/降级/成本跟踪 |
| MCP 框架 | **FastMCP** | 声明式 MCP Server/Client 构建，比原生 mcp-python SDK 更简洁 |
| 向量数据库 | **LanceDB** (主选) | 列式向量存储，原生支持多模态过滤 + 混合检索，零配置嵌入式 |
| 全文检索 | **SQLite FTS5** / **Tantivy** | BM25 稀疏索引，用于 RAG 混合检索 |
| 语义层 | **MetricFlow** (dbt Labs) | 声明式业务指标定义，自动生成跨方言 SQL |
| 数据库驱动 | **psycopg2**+**asyncpg** / **aiomysql** / **duckdb** / **sqlite3** / **snowflake-connector** | 多数据库原生驱动 |
| Schema 解析 | **sqlalchemy** inspect + **information_schema** | Schema 内省与元数据提取 |
| SQL 解析/转换 | **sqlglot** + **sqlparse** | 方言转换、语法解析、格式化 |
| Embedding | **text-embedding-3-small** (OpenAI) / **BGE-large-zh** (本地中文) | 语义搜索向量化 |
| 混合检索融合 | **自研 RRF 引擎** | BM25 + LanceDB 向量融合（RRF / 加权线性） |
| 前端 (MVP) | **Streamlit** | 快速构建交互式 Web UI，适合 MVP 阶段 |
| 前端 (生产) | **React** / **Vue 3** + **Ant Design** | 完整前端体验（如需） |
| 流式通信 | **SSE** / **WebSocket** | 实时 SQL 展示 |
| 配置管理 | **PyYAML** + **Pydantic Settings** | 类型安全的配置加载 |
| 测试 | **pytest** + **pytest-asyncio** | 后端测试 |

### B. 目录结构建议

```
data_engineer/
├── main.py                     # 入口
├── pyproject.toml              # 项目配置
├── SPEC.md                     # 本文档
│
├── app/
│   ├── __init__.py
│   ├── api/                    # API 路由
│   │   ├── __init__.py
│   │   ├── query.py            # 查询相关 API
│   │   ├── schema.py           # Schema 管理 API
│   │   ├── domains.py          # 领域管理 API
│   │   ├── feedback.py         # 反馈 API
│   │   ├── learning.py         # 学习相关 API
│   │   ├── mcp_endpoints.py    # MCP 协议管理 API
│   │   └── workflows.py        # 工作流编排 API
│   │
│   ├── core/                   # 核心引擎
│   │   ├── __init__.py
│   │   ├── nlp_parser.py       # NL 解析器
│   │   ├── time_parser.py      # 时间表达式解析
│   │   ├── ambiguity.py        # 歧义检测
│   │   ├── sql_generator.py    # SQL 生成引擎
│   │   ├── sql_validator.py    # SQL 验证器
│   │   ├── sql_executor.py     # SQL 执行器
│   │   ├── prompt_builder.py   # Prompt 构建器
│   │   └── self_heal.py        # 自愈重试
│   │
│   ├── harness/                # Harness 运行时框架
│   │   ├── __init__.py
│   │   ├── runner.py            # WorkflowRunner (生命周期+node_order推进+evaluate_result)
│   │   ├── plan_loader.py       # workflow.yml Plan 模板加载器
│   │   ├── config_loader.py     # agent.yml 配置加载器
│   │   ├── permission.py        # PermissionManager (allow/deny/ask)
│   │   ├── session.py           # AdvancedSQLiteSession
│   │   ├── skill_manager.py     # SkillManager + SkillFuncTool
│   │   ├── action_history.py    # ActionHistoryManager
│   │   ├── compaction.py        # Auto-compaction (90% token 阈值)
│   │   └── constraint.py        # 全局约束强制执行 (read_only/max_retries/...)
│   │
│   ├── mcp/                    # MCP 协议层 (FastMCP)
│   │   ├── __init__.py
│   │   ├── fastmcp_client.py
│   │   ├── fastmcp_server.py
│   │   ├── registry.py
│   │   ├── servers/
│   │   │   ├── __init__.py
│   │   │   ├── db_server.py
│   │   │   ├── knowledge_server.py
│   │   │   ├── vector_server.py
│   │   │   └── learning_server.py
│   │   └── transport.py
│   │
│   ├── workflow/               # 工作流编排 (Plan 模板)
│   │   ├── __init__.py
│   │   ├── router.py           # Plan 选择器（特征提取+规则路由）
│   │   ├── monitor.py          # 工作流监控和自适应
│   │   └── plans/              # workflow.yml Plan 模板
│   │       ├── gensql_agentic.yml
│   │       ├── reflection.yml
│   │       ├── chat_agentic.yml
│   │       ├── ez_query.yml
│   │       ├── explore.yml
│   │       └── metric_query.yml
│   │
│   ├── subagent/               # Subagent 封装系统
│   │   ├── __init__.py
│   │   ├── manager.py          # Subagent 管理器（生命周期+注册）
│   │   ├── instance.py         # Subagent 实例（scoped context）
│   │   ├── router.py           # Subagent 路由（API/Web/MCP）
│   │   └── configs/            # Subagent YAML 定义
│   │       ├── ecommerce_analyst.yaml
│   │       └── finance_analyst.yaml
│   │
│   ├── agents/                 # 多 Agent 系统 (OpenAI Agents SDK)
│   │   ├── __init__.py
│   │   ├── orchestrator.py     # Orchestrator Agent（任务分解+调度）
│   │   ├── nl_agent.py         # NL Understanding Agent
│   │   ├── schema_agent.py     # Schema Retrieval Agent
│   │   ├── sql_agent.py        # SQL Generation Agent
│   │   ├── validation_agent.py # Validation Agent
│   │   ├── tool_agent.py       # Tool Execution Agent (MCP 网关)
│   │   ├── feedback_agent.py   # Feedback Agent
│   │   ├── bus.py              # Agent 通信总线 (消息路由/Handoff)
│   │   ├── trace.py            # Agent 可观测性 (Trace 收集)
│   │   └── collaboration.py    # 协作模式定义 (5 种模式)
│   │
│   ├── nodes/                  # 内置节点系统
│   │   ├── __init__.py
│   │   ├── base.py             # BaseNode + NodeInput/NodeOutput
│   │   ├── registry.py         # NodeRegistry 全局注册
│   │   ├── schema_linking.py   # SchemaLinkingNode (NL->Schema)
│   │   ├── execute_sql.py      # ExecuteSQLNode (安全执行)
│   │   ├── generate_sql.py     # GenerateSQLNode
│   │   ├── validate_sql.py     # ValidateSQLNode
│   │   ├── parse_nl.py         # ParseNLNode
│   │   ├── hybrid_search.py    # HybridSearchNode
│   │   ├── dialect_translate.py
│   │   ├── explain_plan.py     # ExplainPlanNode
│   │   ├── format_sql.py
│   │   └── metric_resolve.py   # MetricResolveNode
│   │
│   ├── knowledge/              # 领域知识引擎 (Evolvable Context)
│   │   ├── __init__.py
│   │   ├── domain_manager.py   # 领域管理
│   │   ├── glossary.py         # 术语表管理
│   │   ├── rule_engine.py      # 规则引擎
│   │   ├── evolvable_context.py# Evolvable Context 活知识库核心
│   │   ├── schema_retriever.py # Schema 分层检索（RAG 驱动）
│   │   ├── metricflow_layer.py # MetricFlow 语义层集成
│   │   ├── retrieval/
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # RAG 存储基类（通用接口）
│   │   │   ├── schema_metadata_rag.py  # SchemaMetadataRAG（表/列元数据存储与检索）
│   │   │   ├── metric_rag.py    # MetricRAG（业务指标 KPI 存储与检索）
│   │   │   ├── document_store.py# DocumentStore（平台文档向量存储）
│   │   │   ├── bm25_index.py    # BM25 稀疏检索引擎（共享）
│   │   │   ├── lancedb_store.py # LanceDB 向量存储引擎（3 命名空间）
│   │   │   ├── hybrid_fusion.py # RRF / 加权融合引擎
│   │   │   └── embedding.py     # Embedding 生成和缓存
│   │   └── query_pair_cache.py # 历史查询对缓存
│   │
│   ├── learning/               # 持续学习
│   │   ├── __init__.py
│   │   ├── feedback_collector.py  # 反馈收集
│   │   ├── query_pair_store.py    # 查询对存储
│   │   ├── pattern_analyzer.py    # 模式分析
│   │   └── rule_extractor.py      # 规则提取
│   │
│   ├── models/                 # 数据模型
│   │   ├── __init__.py
│   │   ├── domain.py
│   │   ├── schema.py
│   │   ├── query.py
│   │   ├── feedback.py
│   │   ├── mcp.py              # MCP 注册表数据模型
│   │   ├── workflow.py         # 工作流数据模型
│   │   ├── subagent.py         # Subagent 数据模型
│   │   ├── rag_schema.py       # SchemaMetadataRAG (SchemaDocument)
│   │   ├── rag_metric.py       # MetricRAG (MetricDocument)
│   │   └── rag_document.py     # DocumentStore (Document)
│   │
│   ├── db/                     # 数据库层
│   │   ├── __init__.py
│   │   ├── connections.py      # 连接管理（多数据库）
│   │   ├── dialect_adapter.py  # Dialect Adapter（方言适配器）
│   │   ├── schema_extractor.py # Schema 提取（多方言）
│   │   └── migrations/         # 数据库迁移
│   │
│   ├── llm/                    # LiteLLM 路由层
│   │   ├── __init__.py
│   │   ├── lite_router.py      # LiteLLM Router（多提供商路由+Fallback）
│   │   ├── model_config.py     # 模型配置映射（工作流→模型）
│   │   ├── cost_tracker.py     # 成本跟踪
│   │   └── cache.py            # LLM 响应缓存
│   │
│   ├── config/                 # 配置
│   │   ├── __init__.py
│   │   ├── settings.py         # Pydantic Settings
│   │   ├── agent.yml           # 声明式 Agent 配置（provider/node/permission/skill/constraint）
│   │   ├── litellm.yaml        # LiteLLM 多提供商配置
│   │   └── domains/            # 领域 YAML 配置
│   │       ├── default/
│   │       ├── ecommerce/
│   │       └── finance/
│   │
│   └── web/                    # 前端 (Streamlit MVP)
│       ├── __init__.py
│       ├── streamlit_app.py    # Streamlit 主应用
│       ├── components/         # Streamlit 可复用组件
│       │   ├── query_input.py
│       │   ├── sql_display.py
│       │   ├── result_table.py
│       │   └── subagent_chat.py
│       └── pages/               # Streamlit 多页面
│           ├── home.py
│           ├── domain_mgmt.py
│           └── subagent_console.py
│
├── tests/
│   ├── conftest.py
│   ├── test_nlp_parser.py
│   ├── test_sql_generator.py
│   ├── test_sql_validator.py
│   ├── test_knowledge/
│   ├── test_subagent/
│   ├── test_mcp/
│   ├── test_metricflow/
│   ├── test_rag/
│   │   ├── test_schema_metadata_rag.py
│   │   ├── test_metric_rag.py
│   │   └── test_document_store.py
│   ├── test_nodes/
│   │   ├── test_base_node.py
│   │   ├── test_agentic_node.py
│   │   ├── test_schema_linking_node.py
│   │   └── test_execute_sql_node.py
│   ├── test_harness/
│   │   ├── test_workflow_runner.py
│   │   ├── test_permission_manager.py
│   │   └── test_config_loader.py
│   └── fixtures/               # 测试数据
│       ├── test_schema.sql
│       ├── test_metrics.yaml
│       ├── test_documents/     # DocumentStore 测试文档
│       │   ├── sql_style_guide.md
│       │   └── best_practices.md
│       └── test_subagent.yaml
│
├── scripts/
│   ├── seed_test_data.py       # 测试数据生成
│   ├── run_benchmark.py        # 基准测试
│   └── init_evolvable_context.py # Evolvable Context 初始化
│
└── docs/
    ├── architecture.md
    ├── user_guide.md
    ├── subagent_guide.md
    └── metricflow_integration.md
```
