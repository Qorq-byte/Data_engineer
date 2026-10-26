# 流式候选完成证据（阶段34）

`LiteLLMRouter.complete_stream_events`和`SQLGenerator.generate_stream_events`是可选事件能力，原`complete_stream`/`generate_stream`继续输出str。纯模块`app.llm.streaming`不加载SDK。结构化事件是内部Python dataclass，不是新增HTTP/SSE线路或JSON输入协议。

## 事件与消费

`StreamEvent(kind="delta", text=...)`表示临时文本；`kind="end"`表示已消费的终态证据，text必须为空。原生SDK验证成功后为generation_source=model_stream、finish_reason=stop；纯文本兼容层为stream_text/null，不把EOF当成模型stop。事件不携usage、耗时、价格或工具参数；usage仍由物理传输边界采集。

消费注入路由/生成器时使用`events_for(target, text_method_name, event_method_name, ...)`：检测可选方法，缺失则适配文本，覆盖文本但只继承父类事件方法时保留文本路径，包含实例级覆盖。显式覆盖事件方法表示适配器主动声明支持；这不是provider真实性认证。旧CompletionRouter协议无需增加必选方法。

```python
from app.llm.streaming import events_for

stream = events_for(router, "complete_stream", "complete_stream_events", messages=[])
try:
    async for event in stream:
        if event.kind == "delta":
            provisional_text = event.text
        else:
            finish_reason = event.finish_reason
finally:
    await stream.aclose()
```

事件验证器只保存一个终态事件，等待上游EOF并关闭后才交付；重复end、end后任何事件、缺end、错误对象/字段/来源/标记均拒绝，错误不回显payload。原生SDK仍执行原来的length/其他非stop、拒绝/工具、非文本、stop后内容/重复stop检查。SDK尾部usage仍可出现于stop之后，先采集并消费，不直接转成事件；尾部异常不能被stop掩盖。

## 候选、预算与资源

GenerateSQLNode仅在完整事件流成功返回后创建候选，同响应alternatives共享该次model_stream/stop。旧文本适配器及Mock为stream_text/null；自定义文本生成器覆盖也不绕过。异常/取消/超时不留下成功候选；空SQL等后续校验沿用原政策。

预算适配器根据实际绑定的方法识别原生/注入路径：原生物理边界自行计数，注入事件或文本入口计一次。只继承原生文本方法却覆盖事件能力时仍按注入调用计数，不漏计。事件转发不启动第二次模型调用，不重复记录attempt或token；显式事件适配器只观察结束标记，无法提供的usage仍null。限流/usage/成本成功状态沿既有物理调用生命周期，未完整消费不标成功。

每层finally关闭可关闭的上游，取消继续传播BaseException；超时/消费异常不触发新的路由重试、fallback或拼接响应。已发生的调用不退还预算。连接建立阶段原有重试/fallback继续受原预算限制。需完整消费或明确aclose；不得跨任务共享同一个有状态迭代器。

## 公开协议与导出

SQLCandidate和CandidateResult新增允许model_stream来源，finish仍stop/null。JSON/SSE done/MCP沿既有投影保留，不新增公开请求开关。公开SSE仍由固定工作流驱动并沿用非流式生成；本阶段启用的是已有节点streaming配置及直接调用场景，不改变生产入口行为。

显式预测导出允许所选model_stream候选的stop，request-prediction-v1结构不变；来源哈希覆盖新增值。prepared仍拒绝，stream_text/unknown即使写stop也不升级，无来源旧文件兼容。费用/生成耗时仍null，审计schema v1不保存SQL或其摘要，哈希不提供认证。

合成真实SDK传输/SQLite功能回归不能代表真实provider规范化、质量、账单或生产并发性能验收。SQL摘要审计绑定、真实模型评测与费用估算继续独立规划。
