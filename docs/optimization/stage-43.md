# 阶段43：真实 embedding 检索基线

2026-10-28，基线阶段42最终52bd7a8。独立分支 `codex/embedding-acceptance`，工作树 `.stage43`。已完成受限工具、离线控制及用户授权的一次成功真实embedding验收；远端精确SHA双CI正在验证。仅为冻结合成小集和本机环境基线，不代表业务质量或生产容量。

## 实现与证据

新增 `scripts/run_embedding_acceptance.py`：prepare仅读取冻结题集、源码、依赖版本与本地Ollama缓存manifest，不导入提供商SDK或建立HTTP客户端。execute要求完整清单的精确SHA，并重算源码、输入、模型、参数和预算身份。每次使用新运行目录，不支持对未知传输结果自动重试或恢复。

执行工具在发送前持久登记请求，严格按一次模型核验、53个单文本embedding、一次模型复核的顺序收集。固定本机环回地址、并发1、无HTTP重试/重定向/代理/回退/下载。Windows文件锁(5/32/33)仅允许最多5次原子发布尝试，间隔合计最多200ms；这不重试HTTP，最终写入失败立即停止。响应最多256KiB，向量必须为768维有限非零数值并规范化。最后模型复核成功后才发布完整向量产物。父进程监督worker600秒，另给5秒清理；不终止用户原有Ollama服务。64MiB在各阶段写入后检查，不是硬磁盘配额；Ollama CPU/GPU/RSS没有硬限制。未知tokens和actual_cost_usd保持null。

同23文档全局语料、30问题、K3，比较实际LanceDB余弦检索、实际FTS5 BM25和实际SchemaMetadataRAG RRF（k60、alpha0.7），各重复3次/90次计时。验证原始后端结果后才融合，拒绝缺失、重复、未知ID、跨库/领域或错误namespace；失败进入原定30题分母，保留错误和计时。不通过修改阈值制造无答案成功。

线上接口只有db_id过滤。验收适配器额外按冻结领域文档ID/benchmark_domain元数据预过滤，保留schema_name=main；这不代表线上原生domain API已验收。FTS5也索引metadata_json，本阶段BM25与hybrid共享增强元数据，不能与阶段42最小元数据词法分数直接相减。

向量回放只接受清单中的53个精确原文，并验证模型、输入顺序、维度和产物摘要；它不是生产语义缓存。索引身份绑定模型/清单/向量产物，复核双后端各23个完整ID，旧运行目录与旧索引不被覆盖。固定YAML通过真实Harness运行SchemaLinkingNode→GenerateSQLNode，记录六步生命周期、实际检索ID/namespace、生成提示词和trace；仅使用确定性Router，不调用真实LLM。

同时修复三个已复现的问题：Ollama错误维度/NaN/零向量未拒绝；SchemaSearchResult丢失schema_name；空SQR把UNKNOWN追加进查询。失败证据见results/stage-43-initial.txt、stage-43-namespace-initial.txt、stage-43-query-initial.txt。全量首次运行揭示旧测试断言依赖UNKNOWN缺陷，已更新为未指定意图时原文保持不变；保留stage-43-full-initial.txt。

## 执行清单与实际运行

首次清单`9660a0a8…901e47`由用户明确授权一次。该次止于模型元数据核验，记录1次请求预留、0次embedding POST、0次有效响应、1次未知传输结果；报告仅能给出MEASUREMENT_FAILED，不能事后捏造确切HTTP异常。随后只读检查确认当时无Ollama进程/11434监听。保留[首次失败报告](results/stage-43-attempt1-report.json)和请求日志，未自动重试。

补充脱敏错误分类和Windows文件锁的有限本地写入重试后，生成[第二份执行清单](results/stage-43-execution-manifest-r2.json)，SHA256为 `2776d64d6e26b829fb663dd412855cf4cd792639e9138e201467f9f47bfe7157`。用户明确授权“启动服务并按新清单执行一次”。启动前发现已有Ollama监听，故未创建/替换/终止该服务，直接执行此清单一次。

第二次53个embedding POST＋2次模型核验全部收到有效响应，未知传输结果0，无HTTP重试。前后模型摘要一致：`sha256:85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`，embeddinggemma:latest、768维、模型层621867104字节。53份冻结合成文本共3803UTF8字节。双索引各23文档，三种模式各30题、90个计时样本、0技术错误、0非法排名，固定Harness提示词证明通过。跨两次调用合计预留56次HTTP，其中55次验证成功、首次1次传输结果未知；embedding POST总共53次。

[真实报告](results/stage-43-report.json)、[请求账本](results/stage-43-requests.json)、[向量产物](results/stage-43-vectors.json)、[逐题与分层指标](results/stage-43-measurement.json)、[Schema链路证据](results/stage-43-schema-proof.json)均保留。导出产物已做纯离线重建回放，三个模式30题的全部逐题排名/评分与原始报告完全一致，见[产物复验](results/stage-43-published-replay.json)，没有新增HTTP或模型调用。本次实际向量产物摘要：`9d557b63c9f837ccdf0f03992b246845b7c7e642e0a8004e1d9bde2296ebc909`。未知tokens和实际成本保持null，不把本地推理记作已计量的0美元。

## 同集观察

24道可回答题：BM25和dense的Recall@3均为0.9791667、Precision@3为0.375、MRR为1、nDCG为0.9771912。hybrid的Recall@3为0.9583333、Precision@3为0.3611111、MRR为1、nDCG为0.9677623。6道无答案题，BM25正确空结果6/6，dense和hybrid各2/6。不能据此宣布向量或混合检索优于词法；不调整阈值追分，也不据此新增重排器或语义缓存。

各模式90次索引后精确向量回放测量，P50/P95/P99毫秒：BM25为0.193/0.403/2.411，dense为10.381/12.010/53.367，hybrid为10.805/13.712/15.317。它们不包含重新访问Ollama生成查询向量，不能当作完整线上端到端延迟。索引构建1068.391ms；53次无缓存embedding收集合计18220.169ms，P50为216.956ms、P95为275.851ms、最大4740.497ms。没有重置现有服务，不宣称模型冷启动。报告写入前产物1518703字节，低于64MiB检测预算，仍不是RSS或硬磁盘配额证明。

## 本地门禁

真实双库82项通过（PostgreSQL19/MySQL63），两个自有容器均已清理，见[双库证据](results/stage-43-real-services.json)。前端182项契约与三页重复构建/失败构建保留检查通过。Ruff通过。16项配置strict门禁通过；全仓类型诊断仍178项，按文件/错误码/消息比较新增0、消除0。最终全量离线4204项通过、1项Windows跳过、82项real_database独立运行。最初瞬时HTTP控制暴露Windows替换文件的占用错误，现已由有界原子发布重试修复，离线测试恢复瞬时响应验证；保留stage-43-windows-initial.txt和stage-43-before-lock-fix.txt。真实运行的HTTP重试仍为0。

尚未合并main；阶段44–48本次不实施。
