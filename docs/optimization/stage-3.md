# 阶段3：RAG 一致性与离线质量起点

## 实际交付

- 文档长→短替换同步删除 LanceDB/FTS5 旧分块，正文全块入索引，分块大小/重叠参数生效；embedding 批次数量/维度异常在清理旧数据前拒绝。
- Schema 完整刷新显式 replace_db_id（包括空快照），局部索引仍保留其他列/库；Schema 刷新响应单独报告 rag_indexed。
- 文档/指标的领域过滤、Schema 的库过滤在两端检索 top K 前执行，再做 RRF；参数绑定/字符串转义处理带单引号标识。
- 不兼容维度/同维度不同模型/未知来源旧索引不自动删表；新配置支持两个后端显式迁移至新路径，保留原数据。
- SchemaRetriever 真正注入固定计划；API 回归断言 db_id 传递、相关 Schema 列进入生成 prompt，而不相关列不进入。mock embedding 与不可用退化明确提示。
- 新增合成标注集和 scripts.evaluate_quality：真实 BM25 检索指标、可选预测 SQL 的实际执行/结果比较；缺模型预测时不输出准确率。

## 验证记录

环境沿用阶段2：Windows / Python3.13.9 / 既有 site-packages；测试无真实外部模型，索引使用临时真实 LanceDB 与 SQLite FTS5。

- `python -m pytest tests/test_rag tests/test_knowledge tests/test_api/test_query_contract.py -q --tb=short`：558 passed（20.31秒）。
- 完整回归与最终定向命令、结果见 results/stage-3-pytest.txt、stage-3-final-target.txt；最终 **2917 passed, 4 warnings（61.36秒）**；补充路径配置后定向308 passed（24.20秒）。
- `ruff check app/knowledge/retrieval app/rag app/workflow/query_service.py app/api/schema.py app/config/settings.py scripts/evaluate_quality.py tests/test_rag/test_index_consistency.py tests/test_rag/test_lancedb_dim_migration.py tests/test_rag/test_quality_evaluator.py tests/test_api/test_query_contract.py --output-format concise`：通过，见 stage-3-ruff.txt。没有修改检查规则。
- `python -m scripts.evaluate_quality --output docs/optimization/results/stage-3-retrieval.json`：真实 FTS5 BM25，8 文档/7 查询、K=3；Recall/MRR/NDCG=1，Precision≈0.381。仅为词法烟测结果，不是实际业务/语义召回结论，不存在真实模型准确率或优化前对比。
- 评测器回归区分可执行但错误结果、正确结果、危险 SQL；危险语句拒绝，缺失预测按失败计；排名指标检查延后命中会降低 MRR/NDCG。

## 限制与后续

迁移、使用方式和完整指标解释见 rag-evaluation.md。没有跨后端原子事务、向量语义标注集、真实模型预测、多方言金标、交叉编码器重排，不能宣称全部 RAG 质量已验证。文档/指标检索不自动注入每个 SQL prompt；该范围没有冒充已接通功能。下一阶段处理单进程资源竞争、模型故障恢复、生命周期与可观测性，并运行明确范围的本地负载实验。
