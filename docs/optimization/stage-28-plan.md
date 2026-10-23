# 阶段28计划：离线SQL评测完成状态与统计口径

2026-10-24，基线3d74f96（Actions39954157256成功，远程SHA一致），独立codex/sql-evaluation-accounting。原主工作区pyproject.toml哈希E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3及docs/audits、隔离旧未跟踪文件保留，不合并main。

## 已确认与设计

scripts/evaluate_quality.py:evaluate_sql不读取预测完成状态，length的合法SQL仍执行；缺失预测已计全部cases分母，不重做。输入latency_ms/cost_usd仅原样透传，缺校验、覆盖率和汇总；execution_latency_ms包含未执行分支的近零耗时。保留只读AST、query_only、相同gold/预测的超时/行数保护和结果多重集/顺序比较。

方案比较：只在文档要求调用者过滤不能阻止错误统计；自动调用模型/对接在线workflow增加成本、依赖和凭据，不适合此阶段。选择外部预测文件边界修复与可复现统计，不改线上Harness/API/前端或引入新评测框架。brainstorming整理取舍，planning-with-files沿用本目录而不覆盖旧未跟踪计划；按用户既有授权先计划后实施。

## 顺序与验收

1. 失败回归先行：finish_reason=length的合法gold SQL不得执行/计正确；缺失预测执行延迟应为null；非法负/非有限/bool遥测不进入报告，留旧代码失败证据。
2. 预测可选status=completed/failed，failed优先标GENERATION_FAILED；复用阶段27check_completion拒绝明确非stop/refusal/tools，标INCOMPLETE_COMPLETION；缺完成元数据兼容为legacy_unknown，不能宣称完成。整条拒绝无SQL执行，错误描述脱敏。执行成功率/正确率分母仍为全部cases，包括缺失/生成失败/拒绝/执行失败。
3. 预测latency_ms/cost_usd必须有限非负数且非bool，null/缺失为未知；非法输入整次ValueError，不输出误导报告。汇总外部提供的生成延迟mean/P50/P95/P99（nearest-rank）、费用部分总额/均值，均报告sample_count/case_count/coverage/complete；未测统计为null，不补0。失败但有遥测的题目也计入汇总。执行延迟改为实际调用安全执行器的耗时，缺失/生成失败/完成状态拒绝/空SQL为null，单独汇总尝试样本。
4. 严格检查case/prediction结构、非空字符串ID/重复/未知ID、ordered和正的有限资源边界；报告版本、规范化输入哈希、SQLite fixture哈希与限制用于重跑比对，不写原始prompt/密钥。无预测仍sql_model_metrics=null，RAG仅原词法烟测。CLI写严格JSON，失败不覆盖现有报告。
5. 新增真实SQLite不执行证明、完整状态/元数据/费用缺测/失败分母/分位数/输入错误/CLI回归。固定合成预测重放前后相同哈希，只报告功能口径差异，不称模型质量提升。定向及全量pytest、Ruff、原核心strict36与本脚本独立strict、全仓mypy相对181项新增0、182前端及构建检查。
6. 更新rag-evaluation/README/阶段报告/进度与原始结果，审查diff/凭据/范围；代码提交普通推送，核对精确SHA/LinuxCI，再补记文档提交推送及最终CI。不强推/不合并main。

交付：评测器修复、可复现测试与固定功能烟测证据、输入/输出契约和阶段报告。真实生产预测/模型版本/业务标注仍缺，不虚构正确率、成本或性能；181类型存量与7构建期依赖告警保持。状态：计划完成，开始步骤1。

调整记录：独立审查发现纯completion导入会经llm包初始化加载LiteLLM，缺环境开关时尝试获取价格表；将原公共导出改按需加载，保持身份/导入兼容，独立进程禁止SDK导入验证。初始化SQL失败未close、超大整数资源边界在打开库后才溢出也一并最小修复，新增前置拒绝/关闭回归。为使评测脚本在默认导入追踪下纳入CI严格门禁，依据本机jieba0.42.1源码补项目实际使用的单参数lcut部分声明；没有静默导入或关闭检查。费用总和溢出也在SQLite打开前拒绝，失败遥测仍计入。下一步最终定向及全量验证。

步骤1–5完成：旧3项失败回归复现后修复，新增61项/最终定向314通过；全量3578通过、Windows uvloop1跳过（113.31秒），Ruff/strict36+评测脚本strict1/182前端契约/构建通过；全仓181→178项且新增0。独立复审无阻塞，相关11项通过；同输入人工6题功能重放修正错误计分，BM25小集不变，不代表模型质量结论。步骤6文档已整理，待提交/远程CI，原工作区改动保留。
