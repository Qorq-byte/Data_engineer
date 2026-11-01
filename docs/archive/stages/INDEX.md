# Stage archive index

Timeline of the optimisation stages (阶段) that produced the current code base. **This is an index, not a copy.** Every report already lives in `docs/optimization/`, which is a superset of the per-stage snapshot directories (`.stage29/` … `.stage54/`) that were used while the work was in progress: those directories held a full project tree per stage (hundreds of megabytes each) and are deliberately **not** committed. Copying their markdown here would duplicate 100% of the content that is already tracked, so the archive only records where each stage landed and what it concluded.

Conventions: one row per stage report; `-plan.md` files are design notes written *before* the stage and are listed separately at the end. Dates come from the report header when present, otherwise from the commit that first added the file.

## Stages 1–53

| Stage | Date | Topic | Conclusion | Report |
|---:|---|---|---|---|
| 1 | 2026-10-20 | 执行与访问安全 | 单语句 AST 只读检查 + 全业务路由复用 JWT 管理员依赖 | [stage-1.md](../../optimization/stage-1.md) |
| 2 | 2026-10-20 | 固定 Harness 主链路与协议一致 | JSON/SSE/MCP 共用 request_query 计划与 query_service | [stage-2.md](../../optimization/stage-2.md) |
| 3 | 2026-10-20 | RAG 一致性与离线质量起点 | 文档长短替换同步删除旧分块，分块参数真正生效 | [stage-3.md](../../optimization/stage-3.md) |
| 4 | 2026-10-20 | 资源边界、恢复与可观测性 | 同连接 lease、Schema TTL 缓存、统一生命周期收口 | [stage-4.md](../../optimization/stage-4.md) |
| 5 | 2026-10-21 | 可信工作台、基础可访问性与交付入口 | 工作台公共 fetch 自动带认证头，取消与错误语义修正 | [stage-5.md](../../optimization/stage-5.md) |
| 6 | 2026-10-21 | 全仓 Ruff 清零与 CI 门禁 | 全仓 Ruff 清零并以 0.17.1 作为 CI 阻断门禁 | [stage-6.md](../../optimization/stage-6.md) |
| 7 | 2026-10-21 | Harness与模型严格类型边界 | 节点事件与 Runner 评估接口类型化（TypedDict） | [stage-7.md](../../optimization/stage-7.md) |
| 8 | 2026-10-21 | SQL生成/执行与预算适配契约 | CompletionRouter 协议化，生成/执行与预算适配分离 | [stage-8.md](../../optimization/stage-8.md) |
| 9 | 2026-10-21 | 跨JSON/SSE/MCP查询契约 | QueryInput/QueryStatus 统一三协议的失败与错误码 | [stage-9.md](../../optimization/stage-9.md) |
| 10 | 2026-10-21 | Schema Linking边界与可见回退 | FK 扩展限定源候选正/反一跳，无效命中可见回退 | [stage-10.md](../../optimization/stage-10.md) |
| 11 | 2026-10-21 | SQL校验作用域与可解释评测 | SQLGlot MappingSchema 按作用域解析 CTE/派生表/别名 | [stage-11.md](../../optimization/stage-11.md) |
| 12 | 2026-10-21 | 有来源依据的命名空间与内省隔离 | TableSchema 增 namespace 身份，内外省隔离同名表 | [stage-12.md](../../optimization/stage-12.md) |
| 13 | 2026-10-21 | 固定查询来源与拒绝过期连接 | ResolvedQueryTarget 冻结来源，过期连接明确失败 | [stage-13.md](../../optimization/stage-13.md) |
| 14 | 2026-10-21 | SQLite Schema版本与实际读取范围 | SchemaSnapshot 携带 schema_version，感知 SQLite DDL | [stage-14.md](../../optimization/stage-14.md) |
| 15 | 2026-10-22 | DuckDB解析环境与缓存来源 | DuckDBSourceState 冻结 catalog/search_path/临时对象 | [stage-15.md](../../optimization/stage-15.md) |
| 16 | 2026-10-22 | Schema/RAG显式刷新同源与顺序 | Schema/RAG 刷新由单一协调器按 db_id 串行提交 | [stage-16.md](../../optimization/stage-16.md) |
| 17 | 2026-10-22 | Schema 双索引失败补偿与检索隔离 | LanceDB 按 db_id 快照/恢复，写失败可回滚旧索引 | [stage-17.md](../../optimization/stage-17.md) |
| 18 | 2026-10-22 | 持久 Schema 索引启动验证与失败可见性 | 启动即校验持久索引来源，刷新失败可见 | [stage-18.md](../../optimization/stage-18.md) |
| 19 | 2026-10-22 | 管理员按库 Schema 索引清理与恢复 | 按库清理索引走同实例 gate，未知 scope 不再假成功 | [stage-19.md](../../optimization/stage-19.md) |
| 20 | 2026-10-22 | 工作台 Schema 索引状态与按库维护 | 工作台展示按库索引状态与恢复失败，遗留库可维护 | [stage-20.md](../../optimization/stage-20.md) |
| 21 | 2026-10-23 | 可复现的本地前端构建 | esbuild 构建期编译 JSX，生产页面不再依赖 CDN | [stage-21.md](../../optimization/stage-21.md) |
| 22 | 2026-10-23 | RAG配置与运行时生效闭环 | FusionWeights 推导互补 BM25 权重，保存即生效 | [stage-22.md](../../optimization/stage-22.md) |
| 23 | 2026-10-23 | Harness真实约束只读状态 | Harness 只读展示真实计划与运行配置，写接口 409 | [stage-23.md](../../optimization/stage-23.md) |
| 24 | 2026-10-23 | 数据库连接能力真实状态 | describe_capabilities 如实报告四类实现/七类未实现 | [stage-24.md](../../optimization/stage-24.md) |
| 25 | 2026-10-23 | LLM设置持久化与运行时发布 | LLM 设置候选—提交两段式，密钥不回显、删除同步路由 | [stage-25.md](../../optimization/stage-25.md) |
| 26 | 2026-10-23 | LLM输出参数上限与未支持选项 | max_output_tokens 在预算前校验，未支持选项明确拒绝 | [stage-26.md](../../optimization/stage-26.md) |
| 27 | 2026-10-23 | 模型输出完成状态 | check_completion 拒绝明确非 stop 与工具请求候选 | [stage-27.md](../../optimization/stage-27.md) |
| 28 | 2026-10-24 | 离线SQL评测完成状态与统计 | 截断 SQL 记 INCOMPLETE_COMPLETION，分母不缩水 | [stage-28.md](../../optimization/stage-28.md) |
| 29 | 2026-10-24 | 请求级 LLM 用量记录 | 请求级 llm_usage：三协议共用隔离摘要，不补零 | [stage-29.md](../../optimization/stage-29.md) |
| 30 | 2026-10-24 | SDK响应兼容与用量来源标识 | responses 归一化兼容 SDK 对象，异常前保留 usage | [stage-30.md](../../optimization/stage-30.md) |
| 31 | 2026-10-24 | 请求级用量持久审计 | 可选本机 SQLite 用量审计，默认关闭、不存问题与 SQL | [stage-31.md](../../optimization/stage-31.md) |
| 32 | 2026-10-24 | 离线预测导出与评测接线 | 按案例/查询/候选/库绑定导出预测，供离线评测消费 | [stage-32.md](../../optimization/stage-32.md) |
| 33 | 2026-10-24 | 候选级生成来源与完成信息 | 候选级 generation_source/finish_reason 贯通三协议 | [stage-33.md](../../optimization/stage-33.md) |
| 34 | 2026-10-24 | 流式候选完成证据 | 完整消费校验后才标记 model_stream/stop 完成候选 | [stage-34.md](../../optimization/stage-34.md) |
| 35 | 2026-10-25 | 候选摘要审计绑定 | 默认关闭的候选摘要 SHA256 绑定，不存 SQL 原文 | [stage-35.md](../../optimization/stage-35.md) |
| 36 | 2026-10-25 | 离线请求用量费用估算 | 操作者提供平坦价格做离线估算，缺测不补 0 | [stage-36.md](../../optimization/stage-36.md) |
| 37 | 2026-10-25 | 离线评测输入与报告边界 | 三个评测 CLI 统一 strict JSON、路径保护与原子写 | [stage-37.md](../../optimization/stage-37.md) |
| 38 | 2026-10-25 | 受控真实模型验收入口 | prepare/execute 分离的受控模型验收，禁重试与 fallback | [stage-38.md](../../optimization/stage-38.md) |
| 39 | 2026-10-26 | 版本化SQL输出契约与反例验收 | SQL 输出契约 v2：base/edge/empty 三组数据交叉自检 | [stage-39.md](../../optimization/stage-39.md) |
| 40 | 2026-10-26 | 真实PostgreSQL/MySQL集成验收 | PostgreSQL 16 / MySQL 8.4 临时容器 + 独立 CI 服务作业 | [stage-40.md](../../optimization/stage-40.md) |
| 41 | 2026-10-26 | MySQL 大结果读取与取消资源边界 | MySQL 服务端 max_rows+1 上限，取消时清理资源 | [stage-41.md](../../optimization/stage-41.md) |
| 42 | 2026-10-26 | 扩展并冻结SQL与检索验收集 | 冻结 acceptance_v3：30 条 SQL 契约 / 30 条检索 qrels | [stage-42.md](../../optimization/stage-42.md) |
| 43 | 2026-10-28 | 真实 embedding 检索基线 | 真实 embedding 检索基线，依赖本地 Ollama 缓存清单 | [stage-43.md](../../optimization/stage-43.md) |
| 44 | 2026-10-28 | 30题受限NL2SQL验收 | 30 题合成题集契约匹配率；明确不等于业务准确率 | [stage-44.md](../../optimization/stage-44.md) |
| 45 | 2026-10-31 | 大Schema规模与并发负载测量 | 冷提取/检索/索引构建/编排分层测量，含事件循环滞后 | [stage-45.md](../../optimization/stage-45.md) |
| 46 | 2026-10-31 | 单worker持久恢复与故障注入 | 三类持久数据边界 + 故障注入下的单 worker 恢复 | [stage-46.md](../../optimization/stage-46.md) |
| 47 | 2026-10-31 | 类型债务清理与依赖维护 | 类型债务按依赖顺序分批收敛，同步依赖维护 | [stage-47.md](../../optimization/stage-47.md) |
| 48 | 2026-10-31 | 受限试用、干净环境部署与回退验收 | 干净环境部署、受限试用与回退验收 | [stage-48.md](../../optimization/stage-48.md) |
| 49 | 2026-10-31 | Agent 术语/规则匹配修复与依赖快照 | Agent 术语/规则匹配修复，sqlglot 固定 30.18.0 | [stage-49.md](../../optimization/stage-49.md) |
| 50 | 2026-10-31 | 三处"看起来成功"的收口 | 三处“看起来成功”只改语义，未扩能力边界 | [stage-50.md](../../optimization/stage-50.md) |
| 51 | 2026-10-31 | 把每请求的全量 Schema 处理从热路径上拿掉 | 编排 P50 761→122 ms（6.2×），移除每请求全量 Schema | [stage-51.md](../../optimization/stage-51.md) |
| 52 | 2026-11-01 | 关键词 Schema 链接不再逐候选重扫全库 | Schema 链接 1k 表 48.8→7.7 ms（6.3×），不再逐候选重扫 | [stage-52.md](../../optimization/stage-52.md) |
| 53 | 2026-11-01 | 多开进程到底有没有用——测出来，以及它撞出的一个真缺陷 | 4 进程 3.2×/3.1× 吞吐，同进程 asyncio 仅 1.0× | [stage-53.md](../../optimization/stage-53.md) |

### Stage 44 addendum

| Stage | Date | Topic | Conclusion | Report |
|---:|---|---|---|---|
| 44 (live) | 2026-10-31 | 阶段44真实30题NL2SQL批次执行报告 | 一次性真实付费批次执行记录，分数仅对题集内契约有效 | [stage-44-live-acceptance.md](../../optimization/stage-44-live-acceptance.md) |

## Design notes written before each stage

41 `stage-NN-plan.md` files record the plan agreed before implementation. They are useful for understanding why a change was scoped the way it was, not for judging completion — completion is evidenced by the report and the code.

| Stage | Plan |
|---:|---|
| 5 | [stage-5-plan.md](../../optimization/stage-5-plan.md) |
| 6 | [stage-6-plan.md](../../optimization/stage-6-plan.md) |
| 7 | [stage-7-plan.md](../../optimization/stage-7-plan.md) |
| 8 | [stage-8-plan.md](../../optimization/stage-8-plan.md) |
| 9 | [stage-9-plan.md](../../optimization/stage-9-plan.md) |
| 10 | [stage-10-plan.md](../../optimization/stage-10-plan.md) |
| 11 | [stage-11-plan.md](../../optimization/stage-11-plan.md) |
| 12 | [stage-12-plan.md](../../optimization/stage-12-plan.md) |
| 13 | [stage-13-plan.md](../../optimization/stage-13-plan.md) |
| 14 | [stage-14-plan.md](../../optimization/stage-14-plan.md) |
| 15 | [stage-15-plan.md](../../optimization/stage-15-plan.md) |
| 16 | [stage-16-plan.md](../../optimization/stage-16-plan.md) |
| 17 | [stage-17-plan.md](../../optimization/stage-17-plan.md) |
| 18 | [stage-18-plan.md](../../optimization/stage-18-plan.md) |
| 19 | [stage-19-plan.md](../../optimization/stage-19-plan.md) |
| 20 | [stage-20-plan.md](../../optimization/stage-20-plan.md) |
| 21 | [stage-21-plan.md](../../optimization/stage-21-plan.md) |
| 22 | [stage-22-plan.md](../../optimization/stage-22-plan.md) |
| 23 | [stage-23-plan.md](../../optimization/stage-23-plan.md) |
| 24 | [stage-24-plan.md](../../optimization/stage-24-plan.md) |
| 25 | [stage-25-plan.md](../../optimization/stage-25-plan.md) |
| 26 | [stage-26-plan.md](../../optimization/stage-26-plan.md) |
| 27 | [stage-27-plan.md](../../optimization/stage-27-plan.md) |
| 28 | [stage-28-plan.md](../../optimization/stage-28-plan.md) |
| 29 | [stage-29-plan.md](../../optimization/stage-29-plan.md) |
| 30 | [stage-30-plan.md](../../optimization/stage-30-plan.md) |
| 31 | [stage-31-plan.md](../../optimization/stage-31-plan.md) |
| 32 | [stage-32-plan.md](../../optimization/stage-32-plan.md) |
| 33 | [stage-33-plan.md](../../optimization/stage-33-plan.md) |
| 34 | [stage-34-plan.md](../../optimization/stage-34-plan.md) |
| 35 | [stage-35-plan.md](../../optimization/stage-35-plan.md) |
| 36 | [stage-36-plan.md](../../optimization/stage-36-plan.md) |
| 37 | [stage-37-plan.md](../../optimization/stage-37-plan.md) |
| 38 | [stage-38-plan.md](../../optimization/stage-38-plan.md) |
| 39 | [stage-39-plan.md](../../optimization/stage-39-plan.md) |
| 40 | [stage-40-plan.md](../../optimization/stage-40-plan.md) |
| 41 | [stage-41-plan.md](../../optimization/stage-41-plan.md) |
| 42 | [stage-42-plan.md](../../optimization/stage-42-plan.md) |
| 43 | [stage-43-plan.md](../../optimization/stage-43-plan.md) |
| 44 | [stage-44-plan.md](../../optimization/stage-44-plan.md) |
| 45 | [stage-45-plan.md](../../optimization/stage-45-plan.md) |

## What is *not* archived

| Excluded | Why |
|---|---|
| `.stage29/` … `.stage54/` | Full project snapshots (app + node_modules + data + traces) taken to keep each stage isolated. Hundreds of MB each; reproduced by `git checkout <stage commit>`. |
| `.post40-plan/` | Scratch tree for post-stage-40 planning. Copy of the repo, no unique documents. |
| `.workbuddy/` | Local agent working memory, not project documentation. |
| `docs/optimization/results/*` | Raw measurement artefacts. Already tracked in place; not duplicated here. |

To revisit a stage exactly as it was, check out its commit (`git log --oneline` shows one `docs(stage-NN)` commit per stage) instead of keeping a snapshot directory.
