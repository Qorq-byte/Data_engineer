# 离线SQL预测评测契约（阶段28）

`python -m scripts.evaluate_quality --predictions predictions.json --output result.json`接收外部生成的预测，不调用模型、不运行线上工作流。省略--predictions时sql_model_metrics=null；传空列表时所有题目MISSING_PREDICTION。使用合成SQLite fixture、相同gold/预测的只读AST、query_only、2秒截止和1000行边界。结果相等仍只证明该数据快照上的结果匹配，不证明业务语义。

## 输入与完成状态

预测是列表，元素为非空字符串id及可选sql/status/finish_reason/refusal/tool_calls/function_call/latency_ms/cost_usd；与案例id对应，重复/未知ID拒绝。案例id/gold_sql为非空字符串，ordered如果提供必须bool。预测sql为空或非字符串记EMPTY_SQL，不丢出分母。

```json
[
  {"id":"sql1","sql":"SELECT id FROM orders ORDER BY id","status":"completed","finish_reason":"stop","latency_ms":120,"cost_usd":0.001},
  {"id":"sql2","status":"failed","latency_ms":300,"cost_usd":0.002},
  {"id":"sql3","sql":"SELECT 1","finish_reason":"length"}
]
```

status仅允许completed/failed（缺省兼容），failed优先记GENERATION_FAILED，即便有可执行SQL也不执行。其余预测复用app/llm/completion.py的check_completion：明确非stop、非空refusal/tool_calls/function_call记INCOMPLETE_COMPLETION，丢弃整条输出，不自动重试。finish_reason缺省/null为legacy_unknown；status=completed不能替代正常结束证明。未知原因/拒绝正文/工具参数不回显到新增错误。输入是平铺预测，不接受原始provider choices嵌套结构作为SQL；原始响应应由采集器转换并保存在独立受控记录中。

completion_status为missing/generation_failed/rejected/stop/legacy_unknown，completion_counts显式显示覆盖情况。该状态描述生成，不等于execution_success或result_correct；正常stop仍须只读检查、执行及结果比较。错误码继续包括MISSING_PREDICTION、EMPTY_SQL、SQL_POLICY_REJECTED、SQL_EXECUTION_ERROR、SQL_RESULT_LIMIT、SQL_TIMEOUT、RESULT_MISMATCH。非法gold是数据集错误，中止评测，不归模型失败。

## 指标和计时

report_version=sql-evaluation-v2；case_count始终是全部案例，prediction_count是实际提供预测。execution_success_count/result_correct_count及其rate均以全部案例为分母，缺失/生成失败/截断/空SQL/执行失败都算失败。结果错但执行成功仍计执行成功；无序比较是多重集，保留重复行，ordered=true比较顺序。

latency_ms/cost_usd是外部报告，必须为有限非负JSON数值（不接受bool、字符串、NaN/Infinity），缺省/null表示未知，有效0照常计样本。任何非法值、总费用溢出或不受支持的资源边界都在evaluate_sql打开预测执行库前ValueError；失败预测的有效费用/生成延迟也进入统计，避免只统计成功。组合CLI会先完成独立BM25烟测（同样使用SQLite），再调用SQL评测；前述门禁针对SQL评测库，不承诺在所有检索工作前拒绝输入。输入错误不覆盖原报告。

- reported_generation_latency_ms：sample_count、case_count、coverage、complete、mean、p50/p95/p99及percentile_method=nearest_rank，分位下标ceil(p*n)-1。两样本0和100的P50为0、P95/P99为100，不用插值。小样本尾分位不作为生产性能结论。
- reported_cost_usd：sample_count/case_count/coverage/complete、total和mean。coverage不足1时total只是已报告样本的部分总额，不外推全部题目成本。无样本的总额/均值/分位均null，不能把未知补0。
- details.execution_latency_ms仅计实际进入安全执行器的预测尝试，包含只读策略、SQLite执行和有界fetch，排除gold及结果比较。缺失/生成失败/完成状态拒绝/空SQL为null。execution_attempt_latency_ms汇总这些尝试；SQL_POLICY_REJECTED也在尝试样本中，不能称其为纯数据库时延，更不是端到端延迟或负载分位。

外层embedding_or_llm_cost_usd=0只表示本工具没有模型/embedding调用；不是外部生成预测的费用。measurement_scope同时说明零调用和外部reported成本。真实成本基线还需模型版本、prompt版本、usage、重试/多候选/失败全部物理尝试的完整采集，单条预测的遥测总口径由采集器负责，本工具不能核验账单或自动补漏。

## 可复现性与运行边界

外层dataset_sha256及SQL结果cases_sha256/predictions_sha256使用UTF-8、sort_keys=true、ensure_ascii=false、separators=(",",":")、禁止非有限数值的规范JSON；数组顺序属于输入身份，字典键顺序不影响哈希。sqlite_fixture_sha256为tests/fixtures/test_schema.sql原始字节哈希。报告limits包含SQLite方言、截止和max_rows；max_rows须正整数且小于当前Python的sys.maxsize，timeout须正的有限数值，bool/超大整数预先拒绝。

离线导入不加载Router/LiteLLM；app.llm公共五个Router/配置/成本导出改按需加载，实际使用它们时才保留原SDK行为。无需通过设置环境变量掩盖导入副作用。SQLite连接初始化失败也在finally关闭。

`tests/fixtures/sql_prediction_accounting.json`是6题固定功能集，不含真实模型输出。重跑`pytest tests/test_rag/test_prediction_accounting.py -q`可验证预期计数、分母和覆盖率；阶段28保存同输入哈希、阶段27固定SHA重放与当前输出的[功能对照](results/stage-28-accounting.json)，旧执行4/6、正确3/6改为执行2/6、正确1/6，是拒绝错误计分，不是模型退化或质量提升。保留原7查询/8文档BM25烟测，未运行真实模型/dense/reranker、真实标注或压测。


阶段32新增显式离线[预测导出](prediction-export.md)。新export_version行消费前验证行/案例集哈希与来源格式，完整evaluate额外核验dataset哈希；旧普通列表保持原语义。缺候选finish仍legacy_unknown，请求用量只作来源元数据，生成latency/费用不从策略均摊或部分token猜测。
