

# NL2SQL Data Engineering Agent — 项目进度日志

> **开始日期:** 2026-07-15
> **当前阶段:** ✅ 全部完成 (含审计修复)
> **总体状态:** ✅ Phase 1-5 完成 (86/86 任务) + 2026-07-17 审计修复 14 项, 2904 测试

---

## Phase 1: 基础骨架 (Week 1-2) — ✅ 完成

| # | 任务 | 状态 | 完成日期 |
|---|------|------|---------|
| 1.1 | 项目初始化 — pyproject.toml, .env.example, 目录结构 | ✅ | - |
| 1.2 | 11 个数据模型 — app/models/*.py | ✅ | - |
| 1.3 | 配置层 — settings.py, agent.yml, 3 领域 YAML | ✅ | - |
| 1.4 | 数据库连接 (4/11) — ConnectionFactory + 4 驱动 | ✅ | - |
| 1.5 | Schema 提取 (3/11) — PG/SQLite/DuckDB 方言内省 | ✅ | - |
| 1.6 | Dialect Adapter (4/11) — sqlglot 翻译 + 4 完整预设 | ✅ | - |
| 1.7 | BaseNode + NodeRegistry — 统一执行接口 + 全局注册表 | ✅ | - |
| 1.8 | AgenticNode (骨架) — 7 能力框架 | ✅ | - |
| 1.9 | ExecuteSQLNode (完整) — 只读拦截 + 多驱动执行 | ✅ | - |
| 1.10 | Harness 运行时 (6/8) — Runner, ConfigLoader, PlanLoader, Permission, Constraint, ActionHistory | ✅ | - |
| 1.11 | MCP 定义 — DB Server 工具/资源 + Registry | ✅ | - |
| 1.12 | API 基础 (4 端点) — Health, Databases CRUD, Schema, Domains | ✅ | - |
| 1.13 | Workflow 模板 (2/6) — gensql_agentic.yml, ez_query.yml | ✅ | - |
| 1.14 | 种子测试 — 5 测试文件, 30 用例 | ✅ | - |

**Phase 1 交付物:** 63 源文件, 30 测试通过, 1 完整 Node, 4 数据库, 6 端点

---

## Phase 2: NL→SQL 管道 (Week 3-4) — ✅ 完成

**目标:** 跑通基础 NL→SQL 管道，实现单轮查询的端到端流程。

| # | 任务 | 优先级 | 状态 | 完成日期 |
|---|------|--------|------|---------|
| 2.1 | NL 解析器 — LanguageDetector (Unicode + jieba) | P0 | ✅ 完成 | 2026-07-15 |
| 2.2 | TimeParser — 规则解析 (中文 37 + 英文 34 模式, 2层策略) | P0 | ✅ 完成 | 2026-07-15 |
| 2.3 | IntentClassifier — 正则+关键词 7 类分类 (加权打分 + 优先级) | P0 | ✅ 完成 | 2026-07-15 |
| 2.4 | AmbiguityDetector — 4 类歧义模式检测 | P1 | ✅ 完成 | 2026-07-15 |
| 2.5 | SQR Builder + NLParser 集成 — 5 步管道 + SQR 组装 | P0 | ✅ 完成 | 2026-07-15 |
| 2.6 | ParseNLNode — 包装 NL 解析器为 Node | P0 | ✅ 完成 | 2026-07-15 |
| 2.7 | PromptBuilder — L1/L2 模板 + Token 预算管理 | P0 | ✅ 完成 | 2026-07-15 |
| 2.8 | LiteLLM Router — 提供商集成 + 模型路由 + Fallback | P0 | ✅ 完成 | 2026-07-15 |
| 2.9 | GenerateSQLNode — Stream 输出 + 多候选 | P0 | ✅ 完成 | 2026-07-15 |
| 2.10 | ValidateSQLNode — Step 1-3 (语法/Schema/类型) | P0 | ✅ 完成 | 2026-07-15 |
| 2.11 | SchemaLinkingNode — 实体提取 + 关键词检索 + FK 扩展 | P0 | ✅ 完成 | 2026-07-15 |
| 2.12 | SelfHealingRetry — 错误分析 + Prompt 修正 + 重试 | P1 | ✅ 完成 | 2026-07-15 |
| 2.13 | SQL Executor 增强 — EXPLAIN 集成 | P1 | ✅ 完成 | 2026-07-15 |
| 2.14 | API: POST /query — 完整 NL→SQL 端点 | P0 | ✅ 完成 | 2026-07-15 |
| 2.15 | API: POST /query/stream — SSE 流式端点 | P1 | ✅ 完成 | 2026-07-15 |
| 2.16 | 集成测试 — SQLite/PG 端到端查询 | P0 | ✅ 完成 | 2026-07-15 |


---

## Phase 3: RAG + 知识引擎 + 工作流 (Week 5-7) — ✅ 完成

**目标:** RAG 混合检索可用的 Schema 感知查询，MetricFlow 语义层集成。

| # | 任务 | 优先级 | 状态 | 完成日期 |
|---|------|--------|------|---------|
| 3.1 | **LanceDB 初始化** — 3 命名空间 (schema_metadata/metrics/documents) + Embedding 生成器 (OpenAI/BGE/Mock) | P0 | ✅ 完成 | 2026-07-16 |
| 3.2 | BM25 索引 — jieba(zh)/whitespace(en) 分词 + SQLite FTS5 | P0 | ✅ 完成 | 2026-07-16 |
| 3.3 | RRF 融合引擎 — k=60 + 动态 α 权重 | P0 | ✅ 完成 | 2026-07-16 |
| 3.4 | SchemaMetadataRAG — 表/列索引构建 + `find_relevant_tables()` | P0 | ✅ 完成 | 2026-07-16 |
| 3.5 | MetricRAG — 指标索引 + `match_metrics()` + `suggest_dimensions()` | P1 | ✅ 完成 | 2026-07-16 |
| 3.6 | DocumentStore — Markdown 分块 (≤512 tokens, 64 重叠) + content_type 分类 | P2 | ✅ 完成 | 2026-07-16 |
| 3.7 | HybridSearchNode — 封装 RAG 检索为 Node (4 目标模式 + update_context 便捷键) | P0 | ✅ 完成 | 2026-07-16 |
| 3.8 | Schema 分层检索 — 5 步策略 (分析→RAG→术语→FK→注入) + SchemaRetriever + SchemaLinkingNode 升级 | P0 | ✅ 完成 | 2026-07-16 |
| 3.9 | Domain 管理器 — YAML 加载 + 切换 + 3 层自动检测 (关键词/术语/Schema) | P0 | ✅ 完成 | 2026-07-16 |
| 3.10 | Glossary 管理器 — CRUD + 匹配 + 映射解析 + SchemaRetriever 集成 | P0 | ✅ 完成 | 2026-07-16 |
| 3.11 | Rule 引擎 — 匹配 + 强制执行 | P1 | ✅ 完成 | 2026-07-16 |
| 3.12 | Evolvable Context 雏形 — 4 类知识自动采集 | P1 | ✅ 完成 | 2026-07-16 |
| 3.13 | MetricFlow 集成 — 指标定义 + `query_metrics()` + 跨方言 SQL | P1 | ✅ 完成 | 2026-07-16 |
| 3.14 | MetricResolveNode | P1 | ✅ 完成 | 2026-07-16 |
| 3.15 | Knowledge MCP Server — 暴露术语/规则资源 | P2 | ✅ 完成 | 2026-07-16 |
| 3.16 | Vector MCP Server — 暴露 hybrid_search/knn_search | P2 | ✅ 完成 | 2026-07-16 |
| 3.17 | Plan 选择器 — 3 层路由 (规则→历史→LLM) | P1 | ✅ 完成 | 2026-07-16 |
| 3.18 | 4 Plan 模板 — reflection, chat_agentic, explore, metric_query | P1 | ✅ 完成 | 2026-07-16 |
| 3.19 | ReflectionNode — 执行失败→分析→修正循环 | P1 | ✅ 完成 | 2026-07-16 |
| 3.20 | API: 领域 CRUD — 8 个端点 | P1 | ✅ 完成 | 2026-07-16 |
| 3.21 | API: Schema 刷新和搜索 | P1 | ✅ 完成 | 2026-07-16 |
| 3.22 | 历史查询对缓存 — 完全匹配 + 语义匹配 (LanceDB) | P2 | ✅ 完成 | 2026-07-16 |

---

## Phase 4: 前端 + 多 Agent + Subagent (Week 8-10) — ✅ 完成

**目标:** Web UI 可用，多 Agent 系统运行，Subagent 可部署。

| # | 任务 | 优先级 | 状态 | 完成日期 |
|---|------|--------|------|---------|
| 4.1 | Streamlit 主应用 | P0 | ✅ | 2026-07-17 |
| 4.2 | 查询输入组件 | P0 | ✅ | 2026-07-17 |
| 4.3 | SQL 展示组件 | P0 | ✅ | 2026-07-17 |
| 4.4 | 结果预览组件 | P0 | ✅ | 2026-07-17 |
| 4.5 | 对话历史侧边栏 | P1 | ✅ | 2026-07-17 |
| 4.6 | 反馈控件 | P1 | ✅ | 2026-07-17 |
| 4.7 | 设置面板 | P2 | ✅ | 2026-07-17 |
| 4.8 | Orchestrator Agent | P0 | ✅ | 2026-07-17 |
| 4.9 | 6 个子 Agent | P0 | ✅ | 2026-07-17 |
| 4.10 | Agent 通信总线 | P1 | ✅ | 2026-07-17 |
| 4.11 | 5 种协作模式 | P1 | ✅ | 2026-07-17 |
| 4.12 | Agent 可观测性 | P2 | ✅ | 2026-07-17 |
| 4.13 | Subagent 管理器 | P0 | ✅ | 2026-07-17 |
| 4.14 | Subagent Router | P1 | ✅ | 2026-07-17 |
| 4.15 | MCP Server 对外暴露 | P1 | ✅ | 2026-07-17 |
| 4.16 | MCP Client 集成 | P1 | ✅ | 2026-07-17 |
| 4.17 | 版本时间轴 UI | P2 | ✅ | 2026-07-17 |
| 4.18 | ExplainPlanNode | P2 | ✅ | 2026-07-17 |
| 4.19 | DialectTranslateNode + FormatSQLNode | P2 | 🔄 进行中 | — |
| 4.19a | DialectTranslateNode | P2 | ✅ | 2026-07-17 |
| 4.19b | FormatSQLNode | P2 | ✅ | 2026-07-17 |

---

## 进度摘要


| 阶段 | 状态 | 完成率 |
|------|------|--------|
| Phase 1 | ✅ 完成 | 14/14 (100%) |
| Phase 2 | ✅ 完成 | 16/16 (100%) |
| Phase 3 | ✅ 完成 | 22/22 (100%) |
| Phase 4 | ✅ 完成 | 19/19 (100%) |
| Phase 5 | ✅ 完成 | 15/15 (100%) |

---

## Phase 5: 持续学习与完善 (Week 11-14) — ✅ 完成

| # | 任务 | 优先级 | 状态 | 完成日期 |
|---|------|--------|------|---------|
| 5.1 | 反馈收集系统 — FeedbackCollector + SQLite 持久化 | P0 | ✅ | 2026-07-17 |
| 5.2 | 查询对入库 — QueryPairStore + Embedding 生成 | P0 | ✅ | 2026-07-17 |
| 5.3 | 模式分析和规则提取 — PatternAnalyzer + CandidateRule | P0 | ✅ | 2026-07-17 |
| 5.4 | 规则审核 UI — Streamlit rule_review 页面 | P1 | ✅ | 2026-07-17 |
| 5.5 | Evolvable Context 演化引擎 — evolve() + auto_ingest | P0 | ✅ | 2026-07-17 |
| 5.6 | Learning MCP Server — 6 工具 + 3 资源 (port 8084) | P1 | ✅ | 2026-07-17 |
| 5.7 | Subagent 自适应 — track_usage + suggest_config_changes | P1 | ✅ | 2026-07-17 |
| 5.8 | MetricFlow 深度集成 — 5 新方法 | P1 | ✅ | 2026-07-17 |
| 5.9 | 工作流自适应优化 — record_outcome + optimize_rules | P1 | ✅ | 2026-07-17 |
| 5.10 | RAG 索引自动刷新 — IndexRefresher + schedule_refresh | P1 | ✅ | 2026-07-17 |
| 5.11 | Dialect Adapter 完善 — 7 方言完成 (11/11) | P1 | ✅ | 2026-07-17 |
| 5.12 | Node 生态 — 开发文档 + 代码模板 | P2 | ✅ | 2026-07-17 |
| 5.13 | 质量仪表板 — Streamlit dashboard 页面 | P2 | ✅ | 2026-07-17 |
| 5.14 | 端到端测试 — 2 测试文件, 7 测试 | P2 | ✅ | 2026-07-17 |
| 5.15 | 性能优化 — PerformanceStore + 装饰器 | P2 | ✅ | 2026-07-17 |

---

## 审计修复 (2026-07-17) — ✅ 完成

> 对照 SPEC.md / implementation-plan.md 全面审计,发现 14 处"标记完成但实为桩代码/缺失"的项并全部修复。

| # | 问题 | 修复 |
|---|------|------|
| R1 | 🐛 Runner 反射跳转 bug (`continue` 只作用于 for 循环, revise 节点被跳过) | 重写跳转逻辑, 直接定位 revise 索引后 `continue` while 循环 |
| R2 | `WorkflowTrace.record()/persist()` 空方法 (审计不变量失效) | JSONL 持久化至 `$DE_TRACE_DIR` 或 `data/traces/{date}/` |
| R3 | `evaluate_result` 指标全部硬编码通过 | 实现 syntax_valid (sqlglot) / schema_compliant / execution_success 检查器 |
| R4 | explore.yml format 步骤用 hybrid_search 占位 | 改为已实现的 `format_sql` 节点 |
| R5 | chat_agentic.yml respond 步骤用 generate_sql 占位 | 新建 `RespondNode` (模板/LLM 双模式) 并接入 |
| R6 | PlanSelector L2/L3 桩 | L2: PlanHistoryStore (SQLite + Jaccard 相似); L3: LLM 分类 (含降级) |
| R7 | AgenticNode 7 能力中 5 项返回 None | 全部接线: Session/ToolRegistry/Skill/History/Compaction + 权限预检记录 denied_tools |
| R8 | AdvancedSQLiteSession 字典桩 | 真实 SQLite 双表实现 (:memory:/磁盘, TTL, node_name 隔离) |
| R9 | SkillManager 文件不存在 | 新建 `harness/skill_manager.py` (SkillDef/SkillManager/SkillFuncTool) |
| R10 | Auto-Compaction 文件不存在 | 新建 `harness/compaction.py` (ContextCompactor, 90% 阈值确定性压缩) |
| R11 | SQLValidator Step 4-5 未实现 | Step 4 静态性能启发式 (5 类) + Step 5 业务规则校验 (复用 RuleEngine) |
| R12 | TimeParser L2 LLM 兜底桩 | `extract_with_fallback()` + 信号词判定 + JSON 解析容错 |
| R13 | BGE Embedding 抛 NotImplementedError | sentence-transformers 延迟加载真实实现 (缺依赖给安装指引) |
| R14 | Learning MCP Server 未在 main.py 注册 | 补 `learning_mcp_config` 导出并注册 (4 MCP Server) |

**新增文件:** `app/harness/skill_manager.py`, `app/harness/compaction.py`, `app/harness/tool_registry.py`, `app/nodes/respond.py`, `app/workflow/history_store.py` + 10 个测试文件
**测试:** 2600 → 2904 (新增 304), 全部通过

---

## 变更日志

| 日期 | 变更 | 详情 |
|------|------|------|
| 2026-07-15 | 文档创建 | 初始化进度跟踪文档，开始 Phase 2 |
| 2026-07-15 | LanguageDetector 完成 | app/core/language_detector.py, 21 测试, CJK Unicode 范围检测 |
| 2026-07-15 | TimeParser 完成 | 37 中文 + 34 英文模式 (L1 规则匹配), 118 测试, L2 LLM 兜底桩代码 |
| 2026-07-15 | IntentClassifier 完成 | 加权关键词打分 + 优先级决胜, 7 类型 (含 UNKNOWN), 120 测试 |
| 2026-07-15 | AmbiguityDetector 完成 | 4 类歧义检测 (属性归属/聚合方式/范围阈值/时间参照), 103 测试, 纯规则引擎 |
| 2026-07-15 | SQR Builder + NLParser 完成 | 5 步管道集成 + Limit/Order/Table/Entity 提取 + 置信度评分, 86 测试 |
| 2026-07-15 | ParseNLNode 完成 | 包装 NLParser 为 BaseNode, 36 测试, 5 节点注册 |
| 2026-07-15 | PromptBuilder 完成 | L1–L4 四级模板 + Token 预算管理 + QueryPair 模型, 58 测试 |
| 2026-07-15 | LiteLLM Router 完成 | RouterConfig/ProviderConfig + 3 层 Fallback + CostTracker + Mock 模式, 48 测试 |
| 2026-07-15 | GenerateSQLNode 完成 | SQLGenerator + 重写 GenerateSQLNode stub, temperature/multi_perspective 策略, 49 测试 |
| 2026-07-15 | ValidateSQLNode 完成 | Step 1-3 (sqlglot 语法/Schema 引用/类型兼容) + 表别名解析 + 字面量类型推断, 50 测试 |
| 2026-07-15 | SchemaLinkingNode 完成 | 实体提取 + 关键词匹配 + 迭代 FK 扩展 + 列消歧 + 过滤 Schema, 38 测试 |
| 2026-07-15 | SelfHealingRetry 完成 | ErrorAnalysis 分类 + 修正 Prompt 构建 + max 3 轮重试 + Mock 集成, 28 测试 |
| 2026-07-15 | SQL Executor EXPLAIN 完成 | EXPLAIN 执行 (SQLite/DuckDB/PG) + PlanAnalysis 解析 + 静态 SQL 分析, 22 测试 |
| 2026-07-15 | API POST /query 完成 | 5 步管道 (Parse→Link→Generate→Validate→Execute) + Pydantic 模型 + 22 测试 |
| 2026-07-15 | API POST /query/stream 完成 | SSE 流式端点 (parse/schema/token/validation/done/error 事件), 10 测试 |
| 2026-07-15 | Phase 2 集成测试完成 | 77 测试 (8 测试类), SQLite 端到端, Schema 提取/链接/执行/验证/API, 10 核心 NL-SQL 对验证, 全量 916 测试通过 |
| 2026-07-16 | Phase 3.1 LanceDB 初始化完成 | EmbeddingProvider (OpenAI/BGE/Mock) + EmbeddingGenerator + LanceDBStore (3 命名空间 + CRUD + 向量检索) + schema_doc/metric_doc/document 转换器, 48 测试, 全量 964 测试通过 |
| 2026-07-16 | Phase 3.2 BM25 索引完成 | BM25Tokenizer (jieba zh + whitespace en + 停用词过滤 + 单字过滤) + BM25Index (SQLite FTS5 + 命名空间隔离 + CRUD), 62 测试, 全量 1026 测试通过 |
| 2026-07-16 | Phase 3.3 RRF 融合引擎完成 | RRFFusion (k=60 + 动态 α ∈ [0,1] + 分数公式验证) + rank_map 构建 + metadata 合并 + 确定性平局, 35 测试, 全量 1061 测试通过 |
| 2026-07-16 | Phase 3.4 SchemaMetadataRAG 完成 | SchemaMetadataRAG (混合索引 + find_relevant_tables() + db_id 过滤) + SchemaSearchResult + get_distinct_tables/get_columns_for_table, 25 测试, 全量 1086 测试通过 |
| 2026-07-16 | Phase 3.5 MetricRAG 完成 | MetricRAG (混合索引 + match_metrics() + domain 过滤) + MetricSearchResult + suggest_dimensions/get_metric_names, 34 测试, 全量 1120 测试通过 |
| 2026-07-16 | Phase 3.6 DocumentStore 完成 | DocumentStore (Markdown 标题分块 ≤512 tokens/64重叠 + content_type 分类) + DocumentSearchResult + get_parent_documents/count_chunks, 41 测试, 全量 1161 测试通过 |
| 2026-07-16 | Phase 3.7 HybridSearchNode 完成 | HybridSearchNode (4 目标模式: schema/metrics/documents/all + update_context 便捷键) + HybridSearchOutput, 27 测试, 全量 1188 测试通过 |
| 2026-07-16 | Phase 3.8 Schema 分层检索完成 | SchemaRetriever (5 步: 分析→RAG→术语→FK→注入) + SchemaRetrievalResult + SchemaLinkingNode 升级 (注入 retriever 则用 RAG, 否则 keyword 兜底), 20 测试, 全量 1208 测试通过 |
| 2026-07-16 | Phase 3.9 Domain 管理器完成 | DomainManager (YAML 加载 + 热更新 + set_active/get_active + 3 层检测 L1关键词/L2术语/L3Schema + detect_best 阈值 0.6) + ecommerce/finance 领域 YAML, 38 测试, 全量 1246 测试通过 |
| 2026-07-16 | Phase 3.10 Glossary 管理器完成 | GlossaryManager (全局/域 CRUD + 精确/子串/模糊匹配 + resolve() 集成 SchemaRetriever + resolve_expression()) + 从 DomainManager 加载, 50 测试, 全量 1296 测试通过 |
| 2026-07-16 | Phase 3.11 Rule 引擎完成 | RuleEngine (全局/域 CRUD + 正则模式匹配 + get_enforcements() 指令提取 + apply_sql_template()) + RuleMatch + 从 DomainManager 加载, 60 测试, 全量 1356 测试通过 |
| 2026-07-16 | Phase 3.12 Evolvable Context 完成 | EvolvableContext (4 知识类型: SCHEMA/REFERENCE_SQL/SEMANTIC_MODEL/METRIC + 摄入/检索/生命周期 active→stale→archived + stats + 关键词评分) + KnowledgeItem, 72 测试, 全量 1428 测试通过 |
| 2026-07-16 | Phase 3.13 MetricFlow 集成完成 | MetricFlowEngine (CRUD + load_from_domains 从 Glossary 派生指标 + query_metrics 生成完整 SELECT + 维度/时间粒度/过滤/排序/限制 + DialectAdapter 翻译) + MetricDef + MetricSQL, 52 测试, 全量 1480 测试通过 |
| 2026-07-16 | Phase 3.14 MetricResolveNode 完成 | MetricResolveNode (包装 MetricFlowEngine 为 BaseNode + NodeInput config 驱动 + update_context 便捷键 metric_sql/sql/resolved_metrics) + MetricResolveOutput, 47 测试, 全量 1527 测试通过 |
| 2026-07-16 | Phase 3.15 Knowledge MCP Server 完成 | Knowledge MCP Server (3 工具: search_terms/match_rules/lookup_term + 2 资源: glossary://{domain}/terms, rules://{domain}/ + MCPServerConfig port 8082) + Registry 集成, 61 测试, 全量 1588 测试通过 |
| 2026-07-16 | Phase 3.16 Vector MCP Server 完成 | Vector MCP Server (3 工具: hybrid_search/knn_search/refresh_index + 1 资源: embeddings://{store}/ + MCPServerConfig port 8083) + 三服务器共存验证 + Registry 集成, 59 测试, 全量 1647 测试通过 |
| 2026-07-16 | Phase 3.17 Plan 选择器 完成 | PlanSelector (3 层路由 L1 规则/L2 历史/L3 LLM + 14 条 L1 规则 4 条 gensql_agentic 10 条 ez_query + PlanLoader 集成 + 计划存在性过滤 + select/select_sync) + SelectionResult, 52 测试, 全量 1699 测试通过 |
| 2026-07-16 | Phase 3.18 4 Plan 模板 完成 | 4 新 Plan YAML: reflection (6 节点 反射循环), chat_agentic (5 节点 对话式), explore (3 节点 只探索不执行), metric_query (4 节点 MetricFlow) + 跨计划一致性验证 (ID 唯一性/DAG 依赖/注册名验证/节点数极值), 50 测试, 全量 1749 测试通过 |
| 2026-07-16 | Phase 3.19 ReflectionNode 完成 | ReflectionNode (BaseNode, 非 LLM, 关键词匹配错误分类: syntax/schema/type/execution/timeout/empty_result + 7 种 fix_strategy + fix_hints 生成 + needs_revision 标记) + WorkflowRunner evaluate_result 增强 (needs_revision 传播) + reflection.yml 更新 (使用 reflection 节点) + 错误模式 37 条, 98 测试, 全量 1847 测试通过 |
| 2026-07-16 | Phase 3.20 API: 领域 CRUD 完成 | 8 端点: GET/POST /domains, PUT /domains/{id}, GET/POST /domains/{id}/glossary, GET/POST /domains/{id}/rules, POST /domains/detect + Pydantic 请求模型 + DomainManager/GlossaryManager/RuleEngine 模块级单例集成 + 文件持久化 (YAML) + 54 测试, 全量 1901 测试通过 |
| 2026-07-16 | Phase 3.21 API: Schema 刷新和搜索 完成 | 2 端点: POST /schema/{db_id}/refresh (重新提取完整 Schema 快照) + GET /schema/{db_id}/search?q= (表名/列名/注释大小写不敏感搜索 + search_tables/search_columns/search_comments 过滤 + limit 1-100) + check_same_thread=False SQLite 线程安全修复 + 38 测试, 全量 1939 测试通过 |
| 2026-07-16 | Phase 3.22 历史查询对缓存 完成 | QueryPairCache (L1 SHA256 完全匹配 + OrderedDict LRU 淘汰 + L2 LanceDB 语义匹配 cosine distance ≤ 0.05) + CachedQuery 数据模型 + query_cache 第 4 命名空间 (PyArrow schema) + 53 测试, 全量 1992 测试通过 |
| 2026-07-16 | Phase 1-3 审计与修复 | ① main.py 注册 8 个 Node (含 Phase 3 的 HybridSearch/MetricResolve/Reflection) + 3 个 MCP Server (DB/Knowledge/Vector) + PlanSelector; ② agent.yml auto_select: true; ③ runner.py evaluate_result 文档更新; ④ implementation-plan.md 项目结构/状态/Phase 3 标记更新; ⑤ progress-log.md 阶段状态更新; ⑥ ruff lint 自动修复 7 文件 (unused imports/UP015/UP017/I001) |
| 2026-07-17 | Phase 4.18 ExplainPlanNode 完成 | ExplainPlanNode (AgenticNode, EXPLAIN 执行 + LLM 解读) + ExplainPlanOutput + _format_explain_rows/_merge_plan_analyses/_parse_llm_json 辅助函数 + agent.yml 注册 + main.py 注册, 39 测试, 全量 2031 测试通过 |
| 2026-07-17 | Phase 4.19a DialectTranslateNode 完成 | DialectTranslateNode (PlainNode, sqlglot 方言转换) + DialectTranslateOutput + _auto_detect_dialect/_normalize_dialect/_to_sqlglot_dialect 辅助函数 + 11 方言全对测试 + agent.yml 注册 + main.py 注册, 51 测试, 全量 2082 测试通过 |
| 2026-07-17 | Phase 4.19b FormatSQLNode 完成 | FormatSQLNode (PlainNode, sqlparse 格式化) + FormatSQLOutput + keyword_case/indent_width/reindent/strip_comments 配置 + agent.yml 注册 + main.py 注册, 29 测试, 全量 2111 测试通过 |
| 2026-07-17 | Phase 4.15+4.16 MCP 双向架构 完成 | fastmcp_server.py (5 工具 + 4 资源, port 8090) + fastmcp_client.py (MCPClientManager + MCPClientConnection) + transport.py (SSE/stdio 工厂) + 51 测试, 全量 2162 测试通过 |
| 2026-07-17 | Phase 4.8-4.12 Module C: 多 Agent 系统 完成 | 13 文件 (BaseAgent/AgentBus/AgentTraceCollector/OrchestratorAgent + 6 子 Agent + 5 协作模式) + 182 测试, 全量 2344 测试通过. 自定义轻量框架, 包裹现有核心模块. agent.yml 新增 agents 配置段. |
| 2026-07-17 | Phase 4.13-4.14 Module D: Subagent 系统 完成 | 4 文件 (SubagentInstance/SubagentManager/SubagentRouter + ecommerce_analyst.yaml) + 60 测试, 全量 2404 测试通过. YAML 驱动配置, 3 通道路由, 生命周期管理. agent.yml 新增 subagents 配置段. |
| 2026-07-17 | Phase 4.1-4.7+4.17 Module E: Web UI 完成 | 10 文件 (streamlit_app + 7 components + 2 pages) + main.py --web 入口 + 119 测试, 全量 2523 测试通过. Streamlit 组件化 UI: 查询输入/SQL展示/结果预览/对话历史/反馈/设置/版本时间轴. 直接调用核心模块, 无需启动 FastAPI. |
| 2026-07-17 | Phase 5: 持续学习与完善 完成 | 12 新文件 + 8 修改文件, 77 新测试, 全量 2600 测试通过. Module A: learning pipeline (FeedbackCollector/QueryPairStore/PatternAnalyzer + rule_review UI). Module B: evolution (EvolvableContext.evolve/SubagentMetrics/WorkflowAdaptive/IndexRefresher). Module C: Learning MCP Server + MetricFlow deep integration. Module D: 7 方言补全 (11/11). Module E: Node 文档/模板, Dashboard UI, E2E 测试, PerformanceStore. MVP 冻结 (M5 里程碑). |
| 2026-07-17 | 全面审计与修复 (R1-R14) | 对照 SPEC/进度日志审计发现 14 处未完成项并全部修复 (详见上方"审计修复"表). 7 个并行子任务: Session SQLite 化 (22 测试)/SkillManager+Compaction (55 测试)/Validator Step 4-5 (41 测试)/TimeParser L2+BGE (29 测试)/PlanSelector L2/L3 (49 测试)/RespondNode+Plan 修正+Learning MCP 注册 (27 测试)/Trace 持久化+Runner 反射 bug+指标检查器 (43 测试) + AgenticNode 7 能力接线+ToolRegistry (37 测试). 全量 2904 测试通过. |
