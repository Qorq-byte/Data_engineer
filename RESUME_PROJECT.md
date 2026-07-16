# 实习简历 — 项目经历

---

## NL2SQL 数据工程智能体（个人项目）

**技术栈：** Python / FastAPI / LiteLLM / LanceDB / SQLGlot / FastMCP / Streamlit / DuckDB / PostgreSQL / MySQL

**项目简介：** 基于大语言模型的自然语言转SQL智能系统，支持中英双语输入，通过RAG语义检索与多Agent协作将用户的自然语言问题自动转换为可执行SQL并返回查询结果。

### 核心工作

- **多Agent协作架构：** 设计并实现Orchestrator调度器 + 5个专职Agent（NL理解、Schema检索、SQL生成、SQL验证、工具调用），通过Agent Bus实现消息路由与Handoff机制
- **RAG混合检索引擎：** 实现BM25稀疏检索 + LanceDB向量密集检索 + RRF排名融合的三路混合检索策略，动态α权重根据查询抽象度自适应调节
- **多LLM路由层：** 基于LiteLLM统一接入OpenAI / Claude / Gemini / DeepSeek / Qwen等10+模型提供商，支持模型路由、退避重试、流式输出与多候选生成
- **多数据库方言适配：** 内置SQLite / DuckDB / PostgreSQL / MySQL / Snowflake / StarRocks等11种数据库适配器，基于SQLGlot实现方言转换与类型映射
- **MCP双向协议集成：** 基于FastMCP实现MCP Client（消费数据库/知识库/向量服务）与MCP Server（对外暴露NL2SQL能力），支持Claude Desktop等MCP Host直接调用
- **Harness控制平面：** 设计四层架构（Workflow编排 → WorkflowRunner引擎 → Node执行 → agent.yml配置驱动），LLM不决定执行路径，确保行为可预测、可审计、可复现
- **持续学习系统：** 实现用户反馈收集 → 模式分析 → 规则自动提取 → Evolvable Context知识库自动演化的闭环学习机制
- **Evolvable Context活知识库：** 持续捕获Schema元数据、参考SQL、语义模型、业务指标，支持MetricFlow语义层统一定义业务指标并生成跨方言SQL

### 项目亮点

- 全栈自主设计开发，涵盖NLP解析、知识检索、SQL生成验证、执行反馈等完整Pipeline
- 支持多轮对话上下文继承、歧义检测与多候选SQL输出
- 默认只读安全策略 + PII检测 + 权限三级管控（allow/deny/ask）
- 完整测试覆盖，包含单元测试、集成测试与E2E测试
