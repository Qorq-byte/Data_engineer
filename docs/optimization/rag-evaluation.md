# RAG 更新与质量评测

## 已接通的范围

固定查询计划的 schema_linking 节点现在注入 SchemaRetriever，检索始终携带 db_id；GenerateSQLNode 优先使用 linked_schema。源数据库 Schema 仍用于最终校验，不能用检索缺失的 Schema 断言 SQL 合法。领域业务规则传给 ValidateSQLNode；没有将 LLM 授权为流程控制器。

文档、Schema 和指标的稀疏检索现在在 SQLite JSON 过滤后再 LIMIT；向量检索显式 prefilter=true。Lance 字符串值统一转义，BM25 使用绑定参数。文档正文全块进入 embedding_text 和 FTS5，分块实例参数实际生效。完整 Schema 刷新使用 replace_db_id：只替换目标库，空快照也删除旧列；单文档 index_schema 仍是局部更新。文档缩短会清除两个后端的全部旧 parent_doc_id 分块。

此阶段没有新增重排模型，也没有将文档/指标检索自动附加到所有 SQL prompt。文档搜索接口已有真实检索；主查询目前接通 Schema RAG，文档/指标增强仍需明确计划节点与业务数据支持。RRF 不是交叉编码器重排。mock embedding 基于哈希，不能用于语义准确率评估，响应会提示。

双后端更新不是跨库原子事务。关键清理失败会报错而非宣称成功；进程崩溃/第二后端写入失败后应重跑同一范围索引。多进程并发刷新、索引快照切换和故障恢复需更完整的运维方案；MVP 使用单进程，第四阶段处理进程内资源边界。

## 模型/索引版本迁移

LanceDB 不再遇到不同维度就删表。运行引擎记录 provider 类、model_name、维度和 document-fulltext-v2 版本；同维度换模型也拒绝使用旧索引。非空旧索引没有版本清单时，不会自动认领。底层 LanceDBStore 不传 embedding_id 的独立兼容调用只保护维度；生产引擎始终传版本。

迁移方法：停止索引写入，保留旧向量目录、BM25 文件和原始文档；设置 RAG_LANCEDB_DIR、RAG_BM25_PATH 为两个新的路径，再启动/刷新已连接 Schema，重新导入原始文档和术语。仅换路径不会迁移上传内容；重建后用已知查询和两端计数验证，再保留旧路径供回退。切勿以删除旧索引作为启动前提。相同模型名背后的权重变更无法自动识别，运营方应使用不可变模型版本名并更换索引路径。

Schema 刷新响应增加 rag_indexed 与 warnings：Schema 内省成功不能冒充索引刷新成功。RAG 未初始化时主查询显式提示并退到关键词 Schema Linking，不宣称 RAG 已生效。

## 可重跑离线基线

`python -m scripts.evaluate_quality --output docs/optimization/results/stage-3-retrieval.json`

数据为 tests/fixtures/retrieval_eval.json：8 个人工构造文档、7 个中英查询、领域与相关文档 ID，另含 4 个电商 SQL 问题和 gold SQL，使用现有合成 SQLite fixture。它是功能烟测集，不是业务标注数据，也不能用于选择生产语义模型。

当前评测仅测真实 FTS5 BM25，K=3：Recall@3=1，Precision@3≈0.381，MRR@3=1，NDCG@3=1。Precision 以固定 K 为分母，少于 K 条也计空位，相关文档通常只有 1–2 个。数据与实现共同构成很小的可重跑起点，不能推广为系统召回率或与原实现做定量提升比较。单次平均检索耗时记录在 JSON 中，未预热/未压测，不能据此给出吞吐结论；没有 embedding/LLM 调用，调用成本为 0。

真实模型 SQL 指标目前为 null。准备预测文件 `[ {"id":"sql1","sql":"...","latency_ms":123,"cost_usd":0.001}, ... ]` 后，运行 `python -m scripts.evaluate_quality --predictions predictions.json --output result.json`。缺失预测按失败计算；重复/未知 ID 拒绝。执行器只接收只读 AST，SQLite query_only、2 秒进度超时、1000 行上限；分别报告执行成功率和结果正确率。结果比较保留重复行，对注明 ordered 的查询比较顺序；其他查询按多重集比较。生成时延/费用是输入记录，执行时延为本机实测；必须保存模型版本、prompt、usage 原始记录才能形成可信成本基线。浮点容差、日期/时区、其他数据库方言仍待业务评测扩展。

## 真实质量评测前置条件

先收集至少 100–200 个脱敏真实业务问题，由业务人员标注相关 Schema/术语/文档、可接受 SQL 与结果。按库/领域隔离训练和评测，覆盖同义词、歧义、长正文、跨表 JOIN、聚合、空结果、权限边界、不同方言与 Schema 变更；固定数据快照和模型/索引版本。先测 BM25、dense、RRF，再决定是否增加重排；在同一数据上比较 Recall/Precision@5/10、MRR/NDCG、端到端执行与结果正确率、P50/P95 延迟、token/费用。错误按召回缺失、Schema Linking、生成、校验误判、执行环境分类，避免只优化执行成功率。
