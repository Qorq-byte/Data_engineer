# Harness约束状态与部署配置

阶段23起，GET /api/v1/settings/harness只报告真实策略；PUT一律409 HARNESS_SETTINGS_READ_ONLY，不读取或写入设置存储。不再提供未接线的“保存成功”或关闭只读开关；原客户端必须停止发送该PUT。管理员鉴权仍为401/403边界。

GET返回status=ok、mode=read_only、editable_fields=[]、legacy_stored_settings_applied=false，以及limits各项{value,source}、workflow、scope、candidate_policy、direct_sql_scope、snapshot_scope。聚合GET /settings的harness段使用相同结构，旧扁平字段不再表示生效值；历史表不迁移/删除。配置或计划不可读取时503 HARNESS_POLICY_UNAVAILABLE，固定消息不泄漏内容/路径，不回退旧值。专用GET不依赖设置数据库；聚合GET仍需处理其他段（如RAG）错误。

## 实际来源和适用范围

- 查询服务describe_query_policy与实际查询使用同一_CONFIG_LOADER/_request_plan；当前固定计划app/workflow/plans/request_query.yml依次parse_nl→schema_linking→generate_sql→validate_sql→execute_sql，由WorkflowRunner控制，LLM不决定流转。节点没有互调。每目标工作流的max_iterations当前5，并非多库整次查询合计5。
- max_llm_calls来自app/config/agent.yml的constraints.max_llm_calls_per_query，查询各目标库共享预算；节点重试取计划failure_policy.max_retries与全局max_node_retries较小值并下限0，当前为0。不能仅展示agent.yml中的3就声称会重试3次。
- SQL行数为max(1,min(agent.yml max_result_rows, Settings.max_result_rows))，每次执行而非多库合计；语句超时毫秒同样取两个来源较小值并下限1。实际驱动取消能力不是该快照证明的健康指标。直接SQL没有工作流agent.yml覆盖，而使用请求/默认值与Settings上限。
- query_timeout_s来自Settings.query_timeout_seconds（默认120秒），是JSON/SSE等待run_query的总时限，包含排队/会话锁等待；MCP nl_query调用同一submit_query，当前请求1个候选。直接SQL不使用此HTTP查询总时限，不能将语句默认30000毫秒解释为整次请求30秒。
- session_ttl_s来自Settings.session_ttl_seconds，是内存查询会话按访问/清理触发的过期，不代表数据库历史删除。候选数由QueryRequest.num_candidates（当前1–5）指定，没有已接通的全局max_candidates保存项。
- 只读SQL检查在ExecuteSQLNode中无条件执行，Settings.read_only或agent.yml只读声明并不是关闭执行检查的开关。此页面仅描述这些实际消费路径，不声称ConstraintsConfig中的所有字段均已接线。

## 变更与恢复

先按source核对部署文件/环境，不改历史设置表。query_service使用仓库固定agent.yml路径；Settings.agent_config_path并非此请求路径的选择入口。文件加载器按mtime_ns重读，修改后重新GET核对；环境Settings通常需重启，再核对新进程。策略是当前进程读取时快照，已运行请求保留其预算，后续执行还可能读取当前Settings；本阶段没有增加整次查询的原子配置版本。多worker需分别部署/核对，无广播。文件或计划读取失败应修复来源，再点击重新读取；不会写入或自动重试保存。

页面读取中隐藏旧值，失败/畸形业务响应显示未知与重试，序号丢弃迟到旧响应。真实MySQL、生产模型、多进程、负载性能和语义检索质量没有在此阶段测量；既有类型债务和构建依赖告警保留。证据见[阶段23报告](stage-23.md)。
