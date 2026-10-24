# 阶段35：候选摘要审计绑定

2026-10-25验收，基线阶段34最终da2fac1；独立codex/candidate-audit-binding/.stage35。先行设计见[计划](stage-35-plan.md)，运行约定见[绑定契约](candidate-audit-binding.md)。承接尚未合并main的阶段29—34，保留原工作区用户改动，不强推、不自动合并。

## 实现与结果

新增默认关闭的LLM_AUDIT_CANDIDATE_DIGESTS。仅已有审计路径启用且此开关开启时，正常返回后对最终候选计算candidate-binding-v1 SHA256，绑定query_id、候选id、db_id、方言、精确SQL及来源/finish。列表最多1000、SQL单条1MiB/总16MiB、身份字段按UTF-8字节限制，重复身份和非法证据拒绝；仅保存排序的hex摘要，不保存SQL/库名/候选标识原文。用户.env未修改。

payload v2新增摘要版本和列表，SQLite表及user_version仍1，混合v1/v2无需结构迁移；关闭仍写v1。正常业务失败有候选时照常捕获，取消/error未返回最终候选时为空，不能据此断言未生成SQL。摘要/写入失败仅审计failed和固定warning，查询结果、原异常及取消不被覆盖；资源释放、取消等待线程、幂等/冲突/保留政策沿用。

指定audit-db仍先核对请求结果/预算/规范用量；v2成功候选必须匹配，缺失/不一致不得降级。新增--require-candidate-audit要求成功行有v2绑定且需审计库；失败workflow仍failed/sql=null并按元数据核对，计入原全部案例分母。v1仍明确metadata_match，不宣称核对SQL。

成功绑定输出request-prediction-v2，新增候选摘要及来源/原finish的最小provenance，audit_match=candidate_match、sql_binding=candidate_digest_matched；消费者重建摘要和完成语义，并继续核对行/数据集哈希。缺省字段按unknown/null规范化，纯文本/unknown不补stop，prepared仍拒绝。旧v1及普通平铺格式兼容，生成耗时/费用仍null。公开JSON/SSE/MCP llm_audit结构不变，管理员读取v1/v2。

## 验证

新增43项回归。首轮[37项失败回归](results/stage-35-initial.txt)全失败；实现后37项通过，再补6项取消、业务失败、坏库只读及缺省字段兼容，并加强CLI缺审计时保留旧输出的断言。中途6项测试因两份fixture各自产生动态latency导致元数据不一致，以及简化dataset缺检索字段而失败，已使用同一快照和完整合成dataset纠正。最终[关联515项](results/stage-35-target.txt)通过。

本地[全量pytest](results/stage-35-pytest.txt) **3839通过、1平台跳过、2警告，116.03秒**。真实LiteLLM SDK合成传输→SQLite查询→v2审计→独立CLI进程强制绑定→真实SQLite评测，2题保留全分母、结果匹配1；替换SQL导致导出拒绝并保留旧文件。三协议正常候选摘要一致，管理员读取v2；业务失败只导出元数据等级，取消先资源收尾再保存空最终候选列表。真实SQLite混合版本、幂等/冲突/保留和坏版本只读拒绝均回归。

重跑阶段32固定输入，[旧预测兼容证据](results/stage-35-compat.json)与原2条预测完全相等，规范哈希仍3a3b12662c3d27ebddddee556c805504bba5ddf9adb6f52ae532763b7182d4f8，版本均v1。

[Ruff](results/stage-35-ruff.txt)、[核心strict36](results/stage-35-core-types.txt)、[审计/导出/LLM等strict9](results/stage-35-strict.txt)通过，CI新增摘要模块独立strict门禁。全仓mypy相对阶段34仍178项，按file/code/message多重集新增0，[摘要](results/stage-35-types-summary.json)。[182前端函数契约](results/stage-35-frontend.txt)、[三页完整构建](results/stage-35-build.txt)、重复性/故意坏JS保留旧产物通过。复用已验收Python3.12及锁文件离线npm缓存，Windows异步测试在沙箱外使用socketpair；无页面改动不重复视觉检查，无外部模型或业务数据库请求。

## 信任与后续边界

摘要不是加密或签名，低熵SQL可被猜测；能同时修改预测、审计及其哈希者仍可伪造。消费器只检查导出文件内部一致性，不重新读取审计库；重新核对响应应使用可信本机审计文件再运行导出。摘要不绑定原始问题、数据库快照、真实provider账单或SQL正确性。旧二进制只识别payload v1，不承诺回退后读取v2；回退前需备份并保留新版本读取工具。

Harness固定节点顺序、权限、预算/重试、只读SQL及SSE工作流事件方式保持。真实模型质量评测、费用估算、签名/防篡改审计及多租户隔离后续独立规划。

本地验收已完成；代码推送及最终文档提交的精确SHA CI结果随后补记。
