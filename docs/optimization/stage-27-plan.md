# 阶段27计划：拒绝未完成的模型SQL输出

2026-10-23，基线ba237a5（阶段26最终Actions39950763699成功且远程SHA一致）。独立分支codex/llm-completion-boundary；保留原工作区修改与隔离目录旧未跟踪文件，不合并main。

## 依据与设计取舍

SQLGenerator._completion_text仅取第一choice文本，GenerateSQLNode会将其转为候选；LiteLLMRouter._stream_to_async_gen仅产出delta.content并丢弃finish_reason。明确length的合法SQL前缀因此可能执行。仅依赖SQL语法校验不能发现缺失业务筛选；自动提高token重试增加成本且未经评测。本阶段在响应边界拒绝非正常结束，保持固定计划、节点隔离和调用预算。

非流式：拒绝明确非stop结束原因、拒绝/refusal和工具调用；兼容旧适配器缺省finish_reason，但这不证明输出完整。独立候选中的完整响应保留，不完整响应整条丢弃（包括其中看似完整的alternative），请求内warnings说明舍弃；全部失败保留可操作且脱敏的错误。无共享可变诊断状态。

真实LiteLLM流式：要求最终stop；length/过滤/工具调用/未知原因拒绝，正常EOF但缺结束标记也拒绝。结束后的usage空choices允许；异常/取消/提前关闭须关闭上游并释放并发槽。已产生文本后不自动重试/fallback。Mock/第三方CompletionRouter文本协议没有结束元数据，不伪称已验证它们的结束标记。

## 执行顺序与验收

1. 添加旧代码失败回归：length且SQL语法合法、流式length及无终止标记；保留失败日志。
2. 实现无敏感正文的共享完成状态判定；生成器两策略过滤/报告，节点与共享查询服务消费请求内warnings；流式边界检查并清理资源。保持现有错误码WORKFLOW_FAILED和页面错误/警告展示。
3. 覆盖字典/对象响应、缺省兼容、异常原因不泄露、独立有效候选、alternative整条拒绝、流式stop/length/EOF/usage/取消/关闭、并发槽与调用次数。真实SQLite通过JSON/SSE/MCP验证失败无候选/无校验执行，后续请求恢复；成功兄弟候选正常执行并可见warnings。无需真实密钥。
4. 定向及全量pytest、Ruff、核心strict35（增加新边界模块）、全仓mypy对比181项基线无新增、182前端契约及构建检查。没有页面实现变更时不重复视觉审核；API/SSE/MCP契约验证消费现有展示字段。
5. 更新阶段报告、运行契约、README及进度，记录命令/环境/限制；敏感信息与diff范围检查后提交普通推送。核对远程精确SHA和LinuxCI，补记证据文档提交再推送核验。

交付：边界修复、可复现回归、响应契约、阶段报告及CI证据。生产provider兼容、真实正确率/费用/性能不在本阶段作结论；全仓类型存量及7项构建依赖告警保留。初扫猜测test_query_service.py/ci.yml不存在，已用rg定位实际test_query_contract.py/checks.yml，不据猜测判断缺失。

步骤1–4完成：旧3项失败回归复现后通过，新增61项，初版定向305通过；最终全量3517通过/Windows uvloop1跳过（108.45秒）。Ruff、扩展strict36、182前端契约/构建通过，全仓181项/42文件且新增0。关闭传播补充与测试fixture修正见stage-27.md。步骤5文档已整理，待提交/远程CI；不合并main。
