# 阶段33计划：候选级生成来源与完成信息

2026-10-24，用户授权规划后实施。独立 codex/candidate-completion-provenance/.stage33，基线阶段32最终 f9954a2；堆叠承接未合并阶段29—32，不合并 main，不改主工作区。

## 设计

请求 attempt 可能包含解析、生成、多次采样及重试，不能归属所选 SQL。比较从 usage 猜测、扩展流协议、直接保存响应证据：采用第三条，保持现有工作流和流接口。SQLCandidate 增加 generation_source（model_response/prepared/stream_text/unknown，默认 unknown）与 finish_reason（stop/null，默认 null）。这些是代码路径来源和响应字段，不是签名或真实性认证。

非流式生成只在既有完成/拒绝/工具检查通过后从该次首个 choice 保存 stop；缺失仍 null。同一响应拆出的 alternatives 共享该次证据，不从其他响应或请求 usage 借用。两种采样策略模型名各次独立读取，不沿用前一次响应。自愈候选复制实际修正候选元数据，不丢失来源。

系统元数据查询和 prepared 分支标记 prepared，清空完成标记；复制 dataclass 避免污染传入候选。纯文本流标记 stream_text/null：EOF 不能代替候选级终止证据。旧构造默认 unknown/null。JSON、SSE 保留 DTO 字段，MCP 新增同一候选来源信息的只读 candidates 投影，排除候选 execution 以保持顶层100行限制，原顶层字段不变；不扩展 MCP 输入为预测导出格式。

导出仅消费显式选择候选：prepared 拒绝，未知 generation_source 值拒绝；model_response 可采用其 stop，stream_text/unknown 不因人为填写 stop 升格，但显式 length/refusal/tools 继续拒绝评测。缺 generation_source 的旧文件仍按阶段32兼容规则。保持 request-prediction-v1 行结构及 completion_source 取值，源文件哈希包含新增字段；费用、真实生成耗时仍 null。审计 schema v1 不变，不新增 SQL 持久化。

## 实施与验收

1. 先补失败回归：两策略/对象及字典响应/alternatives/缺失完成标记/并发隔离/模型名隔离、自愈、预制复制、纯文本流、JSON/SSE/MCP 同字段、所选候选导出及预制拒绝。
2. 实现 dataclass/生成/节点/DTO/MCP 与导出接线；不改 Harness 节点顺序、预算、重试、取消释放、默认关闭审计。
3. 定向与全量 pytest；Ruff；原有严格 mypy 门禁；全仓相对阶段32的178项无新增。前端182契约和构建。无需真实模型、外部数据库或视觉回归。
4. 更新运行与导出契约、总计划和阶段报告，保留失败和通过证据；提交推送，核验代码精确 SHA CI，补记结果后推送文档提交并核验最终 CI。不强推、不合并 main。

后续边界：流式候选级结构化完成协议、审计与 SQL 摘要绑定、真实模型评测、费用估算仍需独立规划。

实施完成：本地全量及代码精确SHA CI均通过，证据与边界见[阶段33报告](stage-33.md)。补记文档普通推送后再核验最终CI。
