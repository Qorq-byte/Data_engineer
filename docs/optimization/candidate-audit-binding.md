# 候选摘要审计绑定（阶段35）

此功能把保存的候选与本机审计记录核对，检测SQL/库/完成信息替换。只保存SHA256摘要，不保存SQL、问题、库名或候选标识原文。摘要不是加密，简单SQL可被枚举猜测；也不是签名、SQL正确性、真实provider调用、原始问题上下文或源库快照的证明。

## 开启与版本

默认LLM_AUDIT_CANDIDATE_DIGESTS=false，既有部署保持payload schema_version=1。明确设置LLM_AUDIT_PATH到私有审计路径，再设置LLM_AUDIT_CANDIDATE_DIGESTS=true并重启，正常写入payload v2。只开启摘要而审计路径为空仍disabled，不创建文件或计算摘要。不修改用户.env，不新增公开写入API。

SQLite表和PRAGMA user_version继续为1，无结构迁移；同库可存v1/v2记录，关闭摘要后新请求回到v1，旧v2仍可由本版本读取。管理员原GET接口返回对应payload版本，HTTP权限/状态码不变。旧二进制只认识payload v1，不能承诺回退后读取v2；运维回退应先备份并保留新版本读取工具。保留条数、幂等/同ID冲突、0.2秒锁等待及取消等待线程关闭政策不变。

v2在原字段外新增candidate_digest_version=candidate-binding-v1、candidate_digests。列表只含排序、不重复的64位小写hex，最多1000条，不单独存身份字段。绑定对象采用规范JSON：ensure_ascii=false、sort_keys=true、separators=(",",":"), UTF-8；字段为binding_version、query_id、id、db_id、dialect、sql_text、generation_source、finish_reason。SQL按精确文本绑定，空白/注释/Unicode字形变化也改变摘要，不执行或改写SQL。来源缺省unknown，finish缺省null；非空拒绝/工具字段不能纳入绑定。

身份文本有UTF-8字节上限（候选128、库256、方言128）；单条SQL最多1MiB，全部SQL最多16MiB，最多1000候选，重复候选id+db_id拒绝。正常工作流返回后才捕获最终候选；业务失败有候选时也记录。取消/基础设施异常没有正常返回候选时列表为空，不能据此认定没有生成过SQL。超限或计算/写入失败返回llm_audit.failed和固定warning，不覆盖工作流结果、取消或异常，不回显原文。

## 导出与强制绑定

原--audit-db仍先核对query_id、outcome、预算及规范用量。读取v1仅metadata_match，不能核对SQL；读取v2时成功预测必须匹配所选candidate_id+db_id及其候选摘要，缺失/不一致/非法摘要元数据直接拒绝，不能自动降级。

```bash
python -m scripts.export_predictions --dataset data/cases.json --responses data/responses.json --bindings data/bindings.json --output data/predictions.json --audit-db data/llm_usage.sqlite3 --require-candidate-audit
```

--require-candidate-audit必须同时提供--audit-db，成功预测还必须有v2候选绑定；即使没有该选项，提供v2审计后遇到候选不匹配仍拒绝。该选项只强化成功SQL行，失败workflow仍核对审计元数据、导出failed/sql=null并计入失败分母；不把失败候选挑成成功。不指定失败candidate_id可避免为无SQL预测选择候选，指定prepared仍按旧规则拒绝模型评测。错误保留已有输出，CLI固定脱敏错误及原子替换政策不变。

成功绑定行使用export_version=request-prediction-v2，audit_match=candidate_match、sql_binding=candidate_digest_matched。原行字段不变，provenance增加candidate_sha256、generation_source、candidate_finish_reason；后两者保留绑定时的原始候选证据，row.finish_reason仍按来源规则解释（unknown/stream_text不升级stop）。缺省字段按相同unknown/null规范化，避免删除缺省值改变完成来源解释。

未请求审计/v1元数据核对/失败预测仍使用request-prediction-v1并明确未核对SQL；旧平铺格式及v1消费兼容，原合成样例哈希不变。批次可以同时有v1失败和v2成功行。费用与生成耗时始终null，整请求usage仍只归属一个案例，不重复分摊。

## 消费验证与信任边界

v2消费器要求成功SQL行及candidate_match等级，重建候选摘要核对SQL/身份/库/方言/完成信息，再核对候选到row.finish_reason的映射、行及数据集哈希。只重算行哈希无法掩盖候选摘要冲突；缺字段/降级等级或不支持版本拒绝。v1不得携带v2候选证明字段。

离线消费器不会重新读取审计库；audit_sha256是导出时审计记录的哈希引用。能同时重写预测与全部哈希的操作者仍可伪造文件，能修改审计库者同样可重算摘要。需要重新核对保存的响应时，应使用可信审计文件重新运行导出；签名/防篡改日志不在本阶段。真实导出仍含SQL，应保存在私有目录，不把业务数据提交仓库。
