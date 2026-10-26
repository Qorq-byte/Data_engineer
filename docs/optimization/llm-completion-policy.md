# 模型完成状态与SQL执行边界

阶段27在SQLGenerator（非流式）和真实LiteLLM流式适配器检查模型结束状态。该检查不替代SQL语法、Schema、只读和原生数据库执行约束，也不证明业务语义正确。

## 非流式

- 只消费第一choice（现有每调用n=1）；finish_reason=stop允许进入原有校验，任何明确非stop原因均拒绝，包括length、content_filter、tool_calls/function_call和未知值。
- message中非空refusal/tool_calls/function_call即使标stop也拒绝。整条响应及其中所有ALTERNATIVE一并舍弃，不从截断输出中抢救前缀。
- 缺少finish_reason或值为null的历史/自定义适配器继续兼容。不能把这些响应视为有完整性证明；生产provider必须用契约测试核对实际元数据。
- temperature/multi_perspective均保留独立调用的有效候选；SQLGenerator.generate的可选warnings列表由调用方逐请求创建。GenerateSQLNode将诊断放入metadata.warnings/context.generation_warnings，查询服务合并到公开warnings；无全局诊断状态。
- 没有有效候选时节点generation_error，现有Runner终止；明确length会提示达到输出上限、缩短请求或检查配置后显式重试。文本/未知原因/工具参数不进入新增诊断。批次中的其他传输异常仍遵循原行为，不把它们当完整性错误。

## 流式

LiteLLMRouter._stream_to_async_gen输出的每段文本都是暂定内容，只有正常stop且上游正常结束才整体成功。明确非stop、refusal/工具请求、非文本内容、stop后继续文本/重复终止均失败；usage空choices允许。没有stop即EOF也失败。GenerateSQLNode只在完整迭代成功后创建候选，失败时已累积文本丢弃。

SQLGenerator、BudgetedRouter、LiteLLMRouter逐层finally关闭支持aclose的上游；适配器关闭原生stream，限流包装释放并发槽。异常、取消和主动提前关闭都不拼接新响应，不自动提高token或重试/fallback；已计数的物理调用不退还预算。路由原有的连接建立失败重试/fallback继续受原预算限制。

Mock和自定义CompletionRouter.complete_stream只提供文本，协议没有finish_reason；不新增伪造标记，也不把它们当真实provider验证。公开/api/v1/query/stream目前是工作流SSE事件流，其生成节点沿用非流式模型调用；它和直接GenerateSQLNode streaming配置的模型token流是两条不同路径，均分别测试。

## 接口与操作

JSON/SSE终态沿用status=failed、primary_sql=null、candidates=[]、execution/validation=null，错误保存在errors；SSE另发WORKFLOW_FAILED，不发校验/执行事件。MCP nl_query单候选沿用status=failed、sql=""、rows=[]、error_code=WORKFLOW_FAILED。HTTP业务失败状态及鉴权不变。

存在独立完整候选时JSON/SSE允许completed并附Discarded incomplete SQL response警告；现有工作台消费done.errors/warnings并展示，无新页面字段。MCP当前固定num_candidates=1，不具多候选部分成功入口。多数据库查询可能已有其他数据库执行结果时，整体失败不撤销先前只读执行，不承诺跨库原子性。

收到输出上限错误后先审查请求和设置，不默认把上限加到最大值。上限是每调用请求参数，非总费用预算。真实provider的结束原因规范化、忽略参数行为、业务结果正确率、成本和并发性能仍需生产等价环境评测；本阶段测试使用真实Router/SQLite与合成传输，不发外部模型请求。

阶段33在通过上述检查后，将所用非流式choice的stop/null与generation_source=model_response保存在各自SQLCandidate。alternatives共享该响应证据，自愈继承修正响应，缺失不补stop。模型文本流仍为stream_text/null；JSON/SSE/MCP投影保留这些字段。预制SQL为prepared/null并被[预测导出](prediction-export.md)拒绝，旧构造为unknown/null。这不改变拒绝/重试/取消释放政策，也不证明真实provider调用。

阶段34通过可选结构化流接口贯通原生终态证据：完整消费含usage尾部并关闭后才交付model_stream/stop；临时文本不能直接变成完成候选。旧文本适配器/Mock仍stream_text/null，公开SSE仍为工作流事件流。新增[流事件契约](stream-completion.md)说明检查与继承兼容；原截断/拒绝门禁、预算和消费失败不重试的政策不变。
