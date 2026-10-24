# 阶段32计划：离线预测导出与评测接线

2026-10-24，用户授权先规划后实施。独立codex/sql-prediction-export/.stage32，基线阶段31最终fb03cb0，堆叠承接未合并阶段29—31，不自动合并main，保留主工作区用户改动。

## 设计与取舍

比较在线自动保存SQL、从审计恢复SQL、显式本地文件导出：自动保存会改变隐私与运行边界；审计库刻意无SQL无法恢复；采用第三条，只处理操作者提供的标准QueryResponse列表（JSON或SSE done）和显式绑定。MCP精简投影无候选信息不支持推断，已有在线流程/审计schema不扩展。

CLI输入dataset、responses、bindings，输出单个平铺预测JSON列表，兼容既有evaluate_quality；每条新增export_version和allowlist provenance/hash，避免双文件提交一致性问题。bindings的id对应案例，query_id定位响应，成功预测必须candidate_id+db_id精确选择，不自动挑primary或最高分。重复案例或同query_id绑定多个案例拒绝，避免请求用量重复分摊。多候选/多库只选择一个，完整请求用量保留一次并注明scope。案例有question时要求源nl_text精确相等；该比对仅核对提供文件，不证明真实模型上下文或业务数据快照相同。

成功候选仅支持sqlite方言，SQL原样导出，不执行、不修复；失败workflow仍导出failed且无SQL，不能从失败候选中挑成功。缺失案例不补造预测，评测全案例分母不变。结束原因只能来自所选候选明确字段；缺失为null/legacy_unknown，绝不从请求attempt推断。显式length/refusal/tools保留安全拒绝标记，不复制原始拒绝/参数。候选平均latency不映射为生成耗时，费用/生成latency均null，不引入价格估算。

可选audit-db按query_id只读核对outcome、预算计数和规范用量；配置后任一记录缺失/冲突则整个导出拒绝，不冒称已审计。元数据匹配仅证明这些字段一致：审计未存SQL/其摘要，不能认证导出SQL或防止输入人为伪造。未请求审计明确not_requested。导出包含SQL，操作者应控制文件访问；不把它写回只含元数据的审计库。

新模块app/evaluation/predictions.py提供构建/验证；复用审计用量allowlist，公开其纯快照函数，原审计行为不变。export_version=request-prediction-v1；provenance包含案例集/完整dataset/输入响应与bindings哈希、查询/候选/库标识、completion_source、请求范围用量和审计匹配状态；行内容有规范JSON哈希。哈希用于复现/意外改动检测，不是签名。既有评测器对新导出行核验案例集/行哈希，完整evaluate入口额外核对dataset哈希；旧平铺预测仍按原契约接受。

输入文件最多16MiB，列表/案例最多10000、SQL最多1MiB；严格拒绝重复JSON键和非有限值。全部验证/序列化后同目录临时文件原子替换输出；输出不可覆盖dataset/responses/bindings/audit-db（含路径别名）。错误保留已有输出，固定脱敏错误不回显SQL/输入内容/路径，不新增后台任务或网络调用。

## 实施与验收

1. 失败回归先行：显式选择、重复绑定/未知ID/库不匹配/非SQLite、失败/缺预测分母、结束标记未知与拒绝、usage部分量/缺测/SDK来源、审计缺失/冲突、哈希/案例集不匹配、CLI旧输出/输入保护及无SDK/无SQL执行。
2. 实现纯导出与版本化验证，CLI本机文件适配，评测器兼容接线。保留Harness、模型选择、节点/预算/重试/只读政策和默认关闭审计。
3. 真实Router+合成SDK传输/SQLite查询/审计文件→显式导出→真实SQLite只读评测；复跑全部LLM/审计/评测回归。仅功能烟测，不报真实模型正确率/费用/并发性能提升。
4. 定向与全量pytest、Ruff、原strict36/LLM2/审计2/评测1与新增导出模块/脚本strict门禁；全仓相对178无新增；182前端契约及完整构建，无页面改动不重复视觉检查。
5. 更新导出/评测契约、运行说明、总计划/进度、阶段报告和原始证据；敏感信息/范围检查后提交普通推送；代码精确SHA CI通过后补记文档提交并核验最终CI。不强推、不合并main。

边界补充：SQL上限按UTF-8字节计算；文件JSON显式限制64层嵌套，避免不同Python构建的递归上限差异；迭代器栈只随深度增长。

实施完成：本地与代码精确SHA CI均通过；完整回归、设计限制、调整及证据见[阶段32报告](stage-32.md)。
