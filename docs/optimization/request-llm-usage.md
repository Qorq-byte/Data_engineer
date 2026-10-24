# 请求级 LLM 用量契约

JSON QueryResponse、SSE done 和 MCP nl_query 的 llm_usage 来自同一 run_query 请求内采集器。该字段只读，不能更改预算或工作流。llm_calls 仍是预算预留次数；attempt_count 是进入传输/适配器边界的观察次数，不保证网络已发送或供应商已计费。排队取消与预算拒绝不生成记录。

每条 attempts 包含请求内编号、provider/model、source、status、latency_ms、prompt_tokens、completion_tokens、missing_reason 和 finish_status。不包含 prompt、SQL、地址、工具参数或异常正文。最多保留 1000 次，超出计入 dropped_attempts；已有请求调用预算继续生效。ContextVar 随请求重置，并发子任务共享本请求采集器。

source 为 transport_boundary 或 adapter_boundary；注入适配器没有供应商级重试信息。原生路由逐次重试/fallback单独观察，候选和多个数据库共享摘要，不按候选重复加 token。合法非负整数（含 0）保留；bool、负数、浮点、字符串与缺失均为 null。usage_missing 和 usage_invalid_or_partial 区分缺失与非法/部分用量。

known_prompt_tokens/known_completion_tokens 只累加已知样本，空样本的部分和可为 0；必须和 missing_usage_count、usage_coverage、complete 一起读取，不能把部分和解释为实际总量。无尝试时 coverage=null。complete 仅表示已观察记录均有两类合法 token、已收尾且没有丢记录，不表示业务 SQL 成功或账单完整。

status 为 pending/completed/incomplete/failed/timeout/cancelled/closed。finish_status 为 stop/length/other/unknown；无元数据保留 unknown，不推断正常终止。流式成功等到完整消费才确定；末尾 usage 可以出现于 stop 后。提前关闭/取消仍关闭上游并释放并发槽。请求被基础设施取消时可能没有可交付响应，不能声称已经持久审计。

旧 CostTracker 仍是路由器级历史近似值（缺 usage/价格可能为零），不作为请求摘要来源；本阶段不新增费用估算、持久化或预测导出。合成传输/SQLite 回归不证明生产账单、负载性能或实际供应商 usage 覆盖率。
