# RAG 更新与质量评测

## 已接通的范围

固定查询计划的 schema_linking 节点现在注入 SchemaRetriever，检索始终携带 db_id；GenerateSQLNode 优先使用 linked_schema。源数据库 Schema 仍用于最终校验，不能用检索缺失的 Schema 断言 SQL 合法。领域业务规则传给 ValidateSQLNode；没有将 LLM 授权为流程控制器。

文档、Schema 和指标的稀疏检索现在在 SQLite JSON 过滤后再 LIMIT；向量检索显式 prefilter=true。Lance 字符串值统一转义，BM25 使用绑定参数。文档正文全块进入 embedding_text 和 FTS5，分块实例参数实际生效。完整 Schema 刷新使用 replace_db_id：只替换目标库，空快照也删除旧列；单文档 index_schema 仍是局部更新。文档缩短会清除两个后端的全部旧 parent_doc_id 分块。

此阶段没有新增重排模型，也没有将文档/指标检索自动附加到所有 SQL prompt。文档搜索接口已有真实检索；主查询目前接通 Schema RAG，文档/指标增强仍需明确计划节点与业务数据支持。RRF 不是交叉编码器重排。mock embedding 基于哈希，不能用于语义准确率评估，响应会提示。

双后端更新不是跨库原子事务。关键清理失败会报错而非宣称成功；Schema 按库替换现在有阶段17的进程内补偿和失败隔离，其他 namespace 与进程崩溃仍需重建同一范围索引。多进程并发刷新、索引快照切换和持久故障恢复仍需更完整的运维方案；MVP 使用单进程。

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

## 阶段10：源Schema边界与功能回归

两条Schema Linking路径共用`knowledge/schema_graph.py:expand_foreign_keys`：保留有效直接候选的顺序、去重，再按名称追加原始候选的双向一跳邻居；新邻居不会继续扩展，缺失FK目标不制造表。列匹配所属表也作为直接候选。过去代码遍历整个连通分量，现按明确的一跳契约收窄；多跳JOIN未直接命中的远端表可能缺少列详情，需要真实问题评测，不能宣称统一改善所有JOIN查询。

`SchemaRetriever`在消费边界再次核对命中db_id（调用者指定时）、当前Schema中的表和列，过滤过期/跨库结果，不将它们保留在search_results或linked_columns；这不能替代授权或索引前置过滤。db_id未指定的独立调用没有新增数据库授权边界。源Schema不完整时可能牺牲召回，应先刷新Schema。未候选表在retriever路径仍保留已有名称/注释stub，不宣称完全去除所有无关上下文。

检索异常产生SCHEMA_RAG_UNAVAILABLE，失效命中产生SCHEMA_RAG_HITS_REJECTED。合并RAG/术语/关键词后仍无关联，或检索器自身异常时，节点使用原有中文/关键词匹配，产生SCHEMA_LINKING_FALLBACK。诊断在节点result/metadata/context可见，统一服务汇总至最终warnings；不暴露原异常或跨库命中内容。取消继续传播，不回退成成功。现有confidence仍为启发式分数，非校准概率。

复跑功能边界：`python -m scripts.evaluate_schema_linking --output linking.json`。`tests/fixtures/schema_linking_eval.json`含6个合成表、12个固定案例，覆盖多起点/环/缺失引用、库/表/列失效、空/异常检索和中文回退；通过真实retriever/node消费明确注入的检索fixture。输出逐项检查、表/列与详细Schema规模、规范化JSON的dataset_sha256；任一案例不通过退出1。这是功能回归，无真实检索排名、模型或embedding调用，不报告语义Recall/SQL准确率。

同一标注集在阶段9原实现为2/12，修复后12/12；直接命中users的合成例详细列12→5。中文案例因orders.user_id也是直接列匹配，标签明确包含其一跳order_items；修订标签后已用原实现重放基线，前后数据哈希一致。原FTS5/BM25小集阶段10重跑指标与阶段3相同，不能据此声称提高业务检索质量或容量。逐项结果和验证限制见[阶段10报告](stage-10.md)。

## 阶段11：SQL校验与执行评测的区分

`python -m scripts.evaluate_sql_validation --output after.json`运行18个固定SQL功能案例，SQLite fixture内省为源Schema，SQLValidator决定与安全参考执行分别记录。参考执行先经只读策略，再用query_only、2秒进度截止和1000行上限，不执行危险SQL。追加`--baseline-stage10`从本地固定Git SHA加载旧校验器，使用同一案例和数据哈希；不修改检出文件，缺失Git历史时失败，不自动换基准或联网。旧代码重放成功退出不意味着案例通过，新实现案例不全通过退出1。它测校验决策，不是NL→SQL模型准确率；model_accuracy保持null。

`evaluate_quality --predictions`的SQL执行评测继续区分execution_success和result_correct，并增加逐项error_code及error_counts：MISSING_PREDICTION、EMPTY_SQL、SQL_POLICY_REJECTED、SQL_EXECUTION_ERROR、SQL_RESULT_LIMIT、SQL_TIMEOUT、RESULT_MISMATCH；正确结果为null。错误码描述评测层原因，与在线HTTP错误码不是同一套状态。结果不符仍可execution_success=True；顺序/多重集比较规则保持，浮点容差/日期语义未新增。

gold SQL也复用相同只读、时间和行数边界；非法/执行失败/超限gold使评测以数据集错误中止，不记为模型失败，也不无界fetchall。重复case/prediction或未知预测ID拒绝。Python入口可用正的有限timeout_seconds和max_rows缩小预算，CLI默认仍2秒/1000行。预测的latency_ms/cost_usd仍是调用者提供值，不是本工具测出的模型成本；未传预测时sql_model_metrics继续null。阶段11记录的错误类别自检使用故意构造的预测，不能当作模型执行率/正确率。

SQLGlot依赖固定为实际验证的30.18.0，使作用域接口与校验语义可复现；升级时重跑固定SQL案例、SQL策略和方言回归。实现与未验证范围见[阶段11报告](stage-11.md)。

## 阶段12：namespace来源与索引兼容

snapshot_to_schema_docs优先使用TableSchema.schema_name；只有未知时采用既有参数fallback（默认public）。生成器提示词展示已知Schema/Catalog，裁剪和未候选stub保留元数据。当前Schema有明确namespace时，retriever再拒绝schema_name缺失或不符的同库命中；未知namespace的旧Schema保留既有行为。SCHEMA_RAG_HITS_REJECTED语义扩展到namespace，前缀及三协议warnings不变。

SQLite/DuckDB旧public文档ID会改为main，需要按db_id替换索引：连接应用后使用已有`POST /api/v1/schema/{db_id}/refresh`，检查`rag_indexed=true`；false时按warnings处理并排查RAG初始化，不能将Schema刷新当作索引成功。该入口调用index_schemas(replace_db_id=db_id)清除旧库文档后建新文档，不要手动仅追加新ID。RAG初始化也会对已连接库执行替换索引。失败/旧命中被过滤可触发既有关键词回退，诊断须保留。

SchemaDocument/索引仍没有catalog字段，不支持同一db_id在多个catalog切换后并存检索；db_id必须稳定绑定实际连接目标，切换目标必须刷新，不能用显示名称冒充身份。此阶段测来源与过滤契约，未重跑真实embedding、标注Recall/Precision/MRR/NDCG或模型准确率，不能声称检索质量提升。详情见[阶段12报告](stage-12.md)。

## 阶段16：刷新来源与失败状态

推荐调用`POST /api/v1/schema/{db_id}/refresh`，成功必须检查rag_indexed=true；409/SCHEMA_REFRESH_CONFLICT表示embedding等待后来源已变化，旧索引尚未删除，恢复目标后显式重试。200且rag_indexed=false是未索引成功，不能把Schema已提取当作检索索引已更新。后台、启动和IndexRefresher复用同一来源检查；未默认开启周期监听。

真实SQLite/DuckDB、LanceDB和FTS5回归覆盖源DDL/重连/断开、空替换、取消、同库顺序与其他库独立；embedding使用16维MockEmbeddingProvider，并非真实语义检索评测。sentinel文档是否保留只验证替换一致性，不产生Recall/Precision/MRR/NDCG或NL2SQL准确率结论。阶段16测得的写到一半不一致已由阶段17补充进程内恢复；普通表列TTL、catalog索引字段、真实标注与性能基线仍需后续阶段。[历史实施证据](stage-16.md)。

## 阶段17补充：双索引写失败恢复（当前行为）

Schema 按 db_id 完整替换在 embedding/来源校验后保存两边原始记录，写失败分别补偿；完整恢复旧内容也不报新索引成功，返回 rag_indexed=false 和 previous index restored。补偿失败则实例记录 unavailable_databases，目标库及无范围检索拒绝，关键词降级有既有 warning；只有成功的完整替换/删除/清空解除对应隔离。普通 upsert、其他 namespace、跨进程及崩溃恢复没有此保证。历史阶段3/16写后失败边界以[阶段17报告](stage-17.md)的当前实现为准，历史测量保留。

恢复保存原始向量、时间戳、FTS 已分词文本及元数据，不重跑 embedding。测试注入前后写入故障并比较完整内容；这是功能/故障恢复验证，不是 Recall@K、MRR、SQL 结果正确率或性能基线。持久部署异常重启后应先通过共享刷新入口逐库全量刷新并确认 rag_indexed=true，再开放检索；实例内隔离标记不持久化，不承诺自动检测既有损坏。大索引额外内存/同步 I/O 成本仍待按运行说明实测。

## 阶段18：每次启动重新验证持久 Schema 索引

引擎初始化现先枚举两后端全部库ID及当前连接库并标为待刷新，使用共享来源校验入口完整刷新；成功才解除对应标记，未验证内容不会因重启而被读取。没有持久故障日志或实时损坏检测；断开的遗留库也必须保持待刷新，健康库可指定检索。普通低级自建SchemaMetadataRAG兼容调用不自动执行启动策略。

受保护 `/api/v1/learning/rag/status` 显示 schema_index 的 refresh_required_databases、recovery_failed_databases 与历史startup_schema_results；修复后看当前列表，不把历史false或tracked_databases当即时状态。阶段18时遗留库需全量清理的限制已由阶段19按库HTTP操作补足。文档/指标namespace尚无本阶段的完整性验证，Schema状态ready不代表所有RAG数据/模型健康或语义准确。历史验证见[阶段18报告](stage-18.md)。

## 阶段19：按库清理/重建闭环

管理员可用 `POST /api/v1/learning/rag/schema/{db_id}/clear` 清除单库Schema索引，已断开遗留库也适用；成功返回两后端真实删除数，仅失效该库缓存/隔离，其他库与文档指标不改。需重建时再显式schema refresh并核对rag_indexed=true。重复清理0/0合法；后续新刷新可建索引，清理不是永久禁用。操作与完整刷新共享同库顺序，并复用原始记录补偿。

503及restored结果需明确处理，不能将已补偿当作删除成功；没有引擎时不自动删文件。旧全量clear拒绝定向参数/未知scope防止误清全部，维护窗口语义仍保留。阶段19验证包含真实持久双后端、权限、故障、并发和重启，是功能一致性验证，不是语义/性能基线。具体步骤与限制见[阶段19报告](stage-19.md)。

## 阶段20：工作台状态与单库维护

系统设置→RAG检索现显示当前待刷新/恢复失败以及连接状态，已断开遗留库可单独清理，连接库可重建。状态读取失败显示未知并禁用维护，恢复读取后由管理员显式操作；清理后的新刷新仍能建索引。已上传文档/指标不因单库清理删除；全量维护折叠并保留明确确认。

本轮37个新实际页面函数场景和11个gstack浏览器场景验证接口消费/隔离/保留，使用真实临时索引和mock embedding；注入的读取/provider失败单独记录。不新增Recall/Precision/MRR/NDCG或模型答案正确率结论。详情见[阶段20报告](stage-20.md)。

## 阶段22：权重真实生效验证

阶段22以真实LanceDB/FTS5和可控正交向量制造dense/sparse排名冲突，alpha=0与1分别选出预期不同文档；另验证三类在途检索在embedding等待期间保留旧融合器。这些是参数接线/隔离回归，不是语义Recall@K、Precision、MRR/NDCG或SQL结果正确率。评测真正的权重收益仍需相同标注集、模型/索引版本及调用Top-K，记录两组权重、召回/排序指标、延迟与成本，再做成对比较。当前没有统一阈值、Top-K覆盖或在线模型切换；实际provider/model/dimension和运行权重可从设置接口读取，不能用旧保存字段给评测环境贴标签。详见[阶段22报告](stage-22.md)。
