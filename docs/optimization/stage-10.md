# 阶段10：Schema Linking边界与可见回退

日期2026-10-21，基准055c265；阶段9代码和补记CI均成功。按[先行计划](stage-10-plan.md)在独立工作树codex/mvp-hardening实施。

## 代码与实际行为

- `knowledge/schema_graph.py:expand_foreign_keys`替代node和retriever内两份传递扩展：仅将原始候选的正向/反向一跳邻居加入；候选去重保序、邻居排序，缺失FK引用/未知候选不制造表。两个旧私有函数名通过导入别名保留，节点之间未新增调用。
- `knowledge/schema_retriever.py:retrieve`对RAG命中核对调用者指定的db_id、当前表和列，不合格命中不进入关联列或search_results；检索异常、命中丢弃均有固定公开warnings。词汇表匹配列所属表也进入一跳起点。`_build_filtered_schema`不再为源Schema不存在的表制造stub，未候选的真实表仍保留原有名称/注释stub。
- `nodes/schema_linking.py:execute`只在retriever有实际表/列关联时采用结果；否则使用原有中文/关键词路径，并记录回退原因。关键词列匹配所属表先加入起点再做一跳扩展。警告同处result/metadata/context；普通异常回退，CancelledError继续传播。
- `workflow/query_service.py`汇总schema_linking_warnings到最终warnings，重复公开文本去重；JSON/SSE done/MCP保留警告，SSE schema事件也可见。最终SQL校验仍依据原Schema，计划、Runner、权限、只读、调用预算与超时不改。
- `scripts/evaluate_schema_linking.py`与`tests/fixtures/schema_linking_eval.json`提供可复跑功能边界案例；失败退出1。`scripts/replay_schema_linking_baseline.py`从固定完整Git SHA读取旧node/retriever，只在独立Python进程加载，不修改检出文件；与新实现使用同一评测入口和标注。需要本地Git历史包含055c265，缺失时不会自动联网或换基准。
- CI原strict门禁增加纯schema_graph模块，README/AGENTS/节点指南及RAG/接口说明同步，未新增依赖或关闭规则。

## 合成回归与真实FTS5烟测

`python -m scripts.replay_schema_linking_baseline --output before.json` 和 `python -m scripts.evaluate_schema_linking --output after.json`。前者成功退出仅表示旧代码重放完成，不表示旧案例通过；后者全部通过才退出0。

固定6表/12案例包括多起点、环、缺失FK、跨库/缺失表/过期列、空/异常RAG和中文回退。调用真实SchemaRetriever/SchemaLinkingNode，但检索结果是明确注入的边界fixture；不测dense、BM25排名或真实业务准确率。规范化JSON哈希均为`549b4882ff39ba81463f122da8e05dbbcccf5156cf6c2951490a631ca495346c`。阶段9旧实现**2/12**，本次**12/12**；[前](results/stage-10-linking-before.json)/[后](results/stage-10-linking-after.json)/[基线重放日志](results/stage-10-baseline-replay.txt)。重放与首次记录的表集合、列、检查和计数一致；旧关键词路径从set输出，跨进程表顺序不同，不能声称旧输出逐字一致。新路径顺序有明确断言。

直接命中users案例的详细Schema列**12→5**，保留users和一跳orders；环案例12→7。多起点案例仍可能涵盖大部分图，双向一跳不是固定总表数上限。只描述合成上下文规模，不推断token、模型正确率、生产延迟或容量。

`python -m scripts.evaluate_quality --output retrieval.json`重跑原有7查询/8文档真实SQLite FTS5、K=3：Recall=1，Precision≈0.380952，MRR=1，NDCG=1，与阶段3相同。[完整记录](results/stage-10-retrieval.json)。很小的词面烟测，不是语义质量提升；未运行真实LLM，sql_model_metrics仍null。单次检索耗时仅原样记录，无压测结论。

## 工程验证

Windows/Python3.13.9、Node24，Ruff0.17.1、mypy2.4.0目标Python3.12；沿用阶段9隔离类型工具（types-PyYAML6.0.12.20260906、asyncpg-stubs/开发驱动0.32及本地aiomysql0.3.2声明）。运行测试用原site-packages（LiteLLM1.102.0、asyncpg0.31.0），LLM/embedding强制mock，SQLite/DuckDB/LanceDB/FTS5为真实本地实现；FastMCP为Client/Server进程内传输。

- 定向 `python -m pytest tests/test_knowledge/test_schema_linking_boundaries.py tests/test_knowledge/test_schema_retriever.py tests/test_nodes/test_schema_linking_node.py tests/test_api/test_query_contract.py -q`：**103 passed，3 warnings，11.56秒**，[日志](results/stage-10-target.txt)。
- 最终全量 `python -m pytest -q`：**3019 passed，1 skipped，4 warnings，51.90秒**，[日志](results/stage-10-pytest.txt)。新增25项：12案例、5个确定顺序/多起点图检查、混合有效/无效命中、术语列起点、未知表不制造stub、2条取消路径、自定义检索器异常、2种跨JSON/SSE/实际FastMCP降级警告。旧传递扩展测试改为断言远端表不出现，未删除验证。Windows无uvloop的1项跳过由Linux CI补足。
- 标准 `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py`：**29源文件无错误**，[日志](results/stage-10-core.txt)，保留默认导入追踪。
- 全仓 `mypy app/ --output json`：**201项/43文件→198项/42文件，仍退出1**。相同声明环境，以文件/错误码/消息多重集比较新增0。[诊断](results/stage-10-after.jsonl)/[摘要](results/stage-10-summary.json)。未将整个SchemaRetriever/RAG/API描述为已通过strict。
- 全仓 `ruff check .`、前端三页五段 `npm run check:frontend`通过，[Ruff](results/stage-10-ruff.txt)/[前端](results/stage-10-frontend.txt)。无UI行为改动，无新浏览器实验。
- `git diff --check`通过，原主工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，用户改动与临时文件不提交。

本机.venv解释器仍失效，用D:/Users/anaconda/python.exe加原site-packages；设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录。类型工具隔离路径沿用阶段9，CI按pyproject干净安装。

## 调整、兼容及限制

首次合成标注忽略“用户”会直接匹配orders.user_id，因此中文回退的正确一跳集合包含order_items。已补标签说明并用旧Git源码重放同一修订数据，前后哈希一致，不只更新新实现的预期。新增测试先后遗漏Entity.normalized和SchemaSearchResult.db_id构造参数；修正夹具后保留显式空db_id拒绝断言，定向及全量重跑通过，没有放松生产检查。

一跳代替整图是有意兼容变化，复杂多跳JOIN可能缺少远端表详情；应以多个直接候选和真实业务标注评测覆盖，不宣称所有查询改善。db_id未指定的独立调用不提供新授权，过滤仍不能替代前置范围/权限；源Schema过期可能牺牲召回。RRF/匹配confidence仍为启发式，未校准；领域术语新接线/重排/完整事件Schema/跨进程恢复未在此阶段实施。真实embedding/LLM、PG/MySQL和业务标注仍需资源，不能用这组合成通过率代替语义Recall或SQL正确率。

下一候选阶段先复核SQL校验/生成错误分类和评测覆盖，按实际缺口另立计划；不直接引入重排或扩大工作流控制权。

## Git与远程验收

本地验收完成；提交、普通推送及对应Linux CI按实际结果补记。远程未完成前不宣称阶段远程验收通过。
