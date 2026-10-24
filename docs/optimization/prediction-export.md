# 离线预测记录导出（阶段32—35）

工具只读操作者提供的文件；不调用模型、重新运行在线工作流或执行候选SQL。可选audit-db仅执行审计库的只读元数据查询。输出包含选定SQL，可能含业务信息，需由操作者选择私有目录并控制访问；不要把真实输出随代码公开上传。本阶段的仓库样例和证据全部是合成功能数据。

## 输入、选择与运行

responses.json是标准QueryResponse对象列表（即JSON查询响应或SSE done的JSON数据，即使一条也包在列表中）。不是原始SSE文本，不接受原始SDK choices或MCP工具投影来推断完整响应。阶段33虽为MCP增加候选来源投影，导出契约仍要求标准QueryResponse，含有question的案例必须核对nl_text。审计库没有SQL，无法替代responses文件。

bindings.json显式绑定案例集中的id、响应query_id、candidate_id和db_id，例如：

```json
[{"id":"sql1","query_id":"q_000000000001","candidate_id":"selected","db_id":"fixture_sqlite"}]
```

成功workflow必须精确选中一个sqlite候选；不使用primary_sql、不按confidence自动选择、不做方言翻译。多库/候选都由candidate_id+db_id消歧。失败workflow可不指定candidate_id，仍导出failed/sql=null；即使有可执行候选，也不能把整体失败挑成成功。案例有question时要求源nl_text精确相等；缺question明确not_available。该比对只核对提供文件，不证明实际模型上下文、数据库快照或业务正确性。

重复case id、同query_id绑定多个case、未知query/candidate、库不匹配、非SQLite成功候选和未完成workflow响应均拒绝。未绑定案例不生成伪预测，既有评测器继续把所有案例计入分母。

下面两条命令可用固定合成样例重跑，不产生模型请求：

```bash
python -m scripts.export_predictions --dataset tests/fixtures/retrieval_eval.json --responses tests/fixtures/prediction_export_responses.json --bindings tests/fixtures/prediction_export_bindings.json --output data/predictions.json
python -m scripts.evaluate_quality --dataset tests/fixtures/retrieval_eval.json --predictions data/predictions.json --output data/evaluation.json
```

可增加`--audit-db data/llm_usage.sqlite3`核对阶段31审计文件。该参数完全显式，不自动读取LLM_AUDIT_PATH，也不创建或写入审计库。指定后，任一审计缺失（含已淘汰）、outcome/预算/规范用量不一致都拒绝整次导出；不指定时audit_match=not_requested。指定且匹配为metadata_match，并保存规范审计记录的哈希。

审计v1没有SQL或其摘要，因此metadata_match不能认证SQL：操作者修改response候选SQL后，审计元数据仍可能匹配。sql_binding固定caller_supplied_not_verified_by_audit；哈希也不是签名，不能证明输入未被人为伪造。

## 输出与遥测

阶段35可选[候选摘要审计绑定](candidate-audit-binding.md)：启用后生成payload v2审计，导出成功候选必须匹配其摘要；--require-candidate-audit可要求成功行一定具备v2绑定且必须指定audit-db。绑定成功输出request-prediction-v2，provenance新增candidate_sha256/generation_source/candidate_finish_reason，audit_match=candidate_match、sql_binding=candidate_digest_matched；消费者重建摘要和完成语义后再接受。弱等级/失败行仍v1，不降级掩盖v2不匹配；旧格式及下述v1契约继续兼容。摘要核对不是签名或真实性认证，不从审计恢复SQL。

输出是兼容原评测器的预测列表，每条含id/sql/status/finish_reason/latency_ms/cost_usd，以及export_version=request-prediction-v1、provenance和export_sha256。只导出选定SQL和allowlist元数据，不复制自然语言问题、错误正文、结果行、数据库文件名、密钥或工具参数。失败SQL为null。

provenance标记case/query/candidate/db、方言、question_match、completion_source、完整dataset/案例集/输入responses/输入bindings的规范JSON哈希，以及请求范围llm_calls/llm_usage和审计匹配状态。输入哈希不是原始文件字节哈希：空白/字典键顺序/BOM不影响规范JSON，数组顺序影响身份。输出行哈希覆盖整行（除自身），用于复现和意外改动检测。

阶段33在线候选增加generation_source和finish_reason：非流式model_response只保存所用响应的stop/null，同响应alternatives共享该次字段；prepared为系统预制SQL，导出明确拒绝；stream_text为纯文本流，unknown为旧构造/未归属，后两者即使写入stop也不升级完成证明。无效generation_source拒绝；缺该字段的旧文件保持兼容。自愈保留实际修正候选来源，不能从原候选继承完成状态。这些来源表示代码路径，不证明真实模型调用或输入真实性。

所选候选明确提供finish_reason时保留stop/length（上述来源限制仍适用）；未知原因或非空refusal/tools/function_call转换为固定other，不导出拒绝正文/工具参数。stream_text/unknown不能隐藏显式length/refusal/tools。缺候选结束元数据为null和unavailable，不从请求attempt的stop/length推断：失败尝试可能是重试或其他候选。workflow completed不能替代模型结束证明。现有评测器保留legacy兼容，完成状态单列显示。request-prediction-v1行结构不变，responses_sha256覆盖候选新增字段，completion_source仍为candidate_fields/unavailable。

请求用量包含所有重试/候选/目标库，usage_scope固定entire_query_including_retries_candidates_and_targets。同query只允许归属一个case，不能重复分摊。缺整个usage或预算字段保留null，不补0；有效usage必须内部一致，缺测/SDK来源与合法0沿用第29—31阶段契约。provider/model可被既有allowlist脱敏。

候选latency原实现是策略耗时均摊，attempt耗时又可能并行；两者都不能冒充单个预测生成耗时。因此latency_ms和cost_usd始终null，不估价、不外推部分token、不加总并行耗时。评测reported遥测覆盖率为0，费用总额null；本工具零模型调用不等于外部生成免费。

## 消费验证、边界和旧格式

阶段34新增model_stream来源：仅候选自身从结构化流保留的stop可进入完成计数，沿用candidate_fields；原生流需完整消费及尾部校验，纯文本流/Mock仍stream_text/null。完整规则见[流事件契约](stream-completion.md)。不从请求usage借用结束状态，不改变v1导出结构或历史样例哈希。

阶段32评测器识别新export_version：核对行哈希、案例集哈希、来源格式和同query重复使用；完整evaluate入口额外核对dataset哈希。改动SQL/状态/来源而未更新哈希、错用案例集等会在预测SQL执行前拒绝；完整dataset不匹配在BM25烟测前拒绝。旧的普通预测列表继续按原契约评测，export_sha256为保留字段，不能只删除版本标识而保留它。

哈希不是安全认证。有意删除所有导出元数据后可作为旧格式输入，或重新计算哈希；系统不能识别这种人为改写。真实供应商结束状态、账单、输入真实性与数据库数据快照都需额外来源。

输入文件上限16MiB、JSON最多64层、对象列表/案例最多10000、SQL按UTF-8最多1MiB；重复JSON键、非有限数（含浮点溢出）、不一致usage和不支持的结构拒绝。所有验证和序列化完成后，使用同目录临时文件原子替换输出；错误保留旧输出并清理临时文件。输出不能覆盖输入dataset/responses/bindings/audit-db，含解析路径和硬链接别名。标准输出只打印条数/哈希，CLI运行错误使用固定信息，不回显SQL/路径/原始错误。

原子替换不承诺进程强杀/电源故障后的文件系统持久性，未提供签名、事务跨多个文件、自动采集、在线批量导出API或MCP补全。合成SQLite结果匹配仅是功能验证，不是模型质量或生产容量结论。
