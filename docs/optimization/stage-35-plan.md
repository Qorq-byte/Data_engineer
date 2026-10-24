# 阶段35计划：候选摘要审计绑定

2026-10-24，用户授权先规划后实施。从阶段34最终da2fac1建立codex/candidate-audit-binding/.stage35，保留原工作区，不合并main。使用brainstorming和planning-with-files，沿本项目逐阶段设计文件约定。

## 方案与边界

对比保存SQL原文、只核对请求用量、保存候选摘要：采用第三条。新增LLM_AUDIT_CANDIDATE_DIGESTS=false；仅审计已启用且该开关显式开启时捕获。SHA256摘要不是加密，低熵SQL可能被枚举猜测；不开启既有部署，不编辑用户.env。不保存问题/SQL/库名/候选标识原文或工具参数。

纯模块app/audit/candidates.py计算candidate-binding-v1域分隔规范JSON的SHA256，绑定query_id、candidate_id、db_id、dialect、精确SQL文本（不归一化空白）、generation_source与finish_reason。拒绝非空refusal/tool_calls/function_call和非法字段；只处理已序列化最终响应候选，不从请求attempt重建。候选至多1000、SQL每条至多1MiB、总SQL至多16MiB；重复候选身份拒绝，摘要列表排序且不重复。

开启后记录payload schema_version=2，新增candidate_digest_version及candidate_digests（仅64位hex列表）；SQLite表结构及PRAGMA user_version仍为1，无结构迁移，同库可读v1/v2记录。关闭保持v1。正常返回后在既有finally审计路径生成摘要；异常/取消未返回最终候选时列表为空，不推断没有生成过SQL。摘要失败/超限只让审计failed并固定warning，不改变工作流结果；既有幂等/冲突/保留/锁超时/取消等待资源释放不变。

有audit-db时仍先核对outcome/预算/规范用量。v1保持metadata_match，不能认证SQL；v2成功响应必须匹配所选候选摘要，缺失/不一致拒绝，不降级。新增--require-candidate-audit（纯函数同名bool），要求配置audit-db并且成功预测有v2绑定；失败workflow只核对审计元数据，仍无SQL并计入失败分母。

绑定成功使用request-prediction-v2：原行结构不变，provenance新增candidate_sha256、generation_source、candidate_finish_reason，audit_match=candidate_match、sql_binding=candidate_digest_matched。消费器重建候选摘要并核对SQL与完成标记映射、行/数据集哈希；v1与原平铺预测兼容，旧固定样例哈希不变。未要求/未达到绑定的行仍v1明确弱等级。v2仅成功SQL行，避免失败行无SQL却宣称候选验证。哈希不提供签名、数据库快照、问题上下文或业务正确性认证。

## 实施与验收

1. 失败回归：真实SQLite混合v1/v2、摘要不存原文/精确UTF8/字段变更/重复与上限；JSON/SSE/MCP正常与失败响应、默认关闭/仅开摘要不建库；坏摘要不覆盖查询；取消审计仍收尾。
2. 实现纯摘要、可选采集、payload兼容读取、显式导出强制/不降级和v2消费校验；非法/未来版本、缺候选、SQL/库/来源/完成字段替换、CLI旧输出保护均回归。
3. 真实SDK合成传输→SQLite查询→审计→导出→评测及管理员读取，无外部模型/DB调用；原Harness/预算/重试/只读约束保持。
4. 关联及全量pytest、Ruff、原strict36+8及新摘要模块strict，全仓相对178项无新增；182前端契约/完整构建。
5. 契约/README/总计划/报告与证据，普通提交推送，核验代码精确SHA CI；补记验收文档再推送并核验最终CI。不强推、不合并main。

自检：费用、SQL原文持久化、审计签名、真实模型评测与多租户权限不纳入本阶段；没有把摘要匹配称为认证。旧服务只认识payload v1，不承诺回退旧二进制后读取v2，回退应先备份并保留新版本读取工具。

2026-10-25实施完成：本地全量及代码精确SHA CI均通过，结果见[阶段35报告](stage-35.md)。补记验收文档普通推送后再核验最终CI。
