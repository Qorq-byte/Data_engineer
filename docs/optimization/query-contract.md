# 查询执行与流式契约

JSON `POST /api/v1/query`、SSE `POST /api/v1/query/stream` 和 MCP `nl_query` 共用 `app/workflow/query_service.py:run_query`。API 不能传入计划 ID；由 `request_query.yml` 和 `WorkflowRunner` 固定执行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql。节点不调用其他节点，LLM 只提供候选 SQL。

请求保留 nl_text/domain/database/dialect/num_candidates/execute/link_schema/model；`validate` 与 `run_validation` 都映射到同一字段。execute=true 时忽略关闭校验的请求，必须校验通过后执行。execute=false 可以只生成；link_schema=false 不连接数据库执行，execute=true 将返回失败。database 应使用稳定 db_id，不以显示名称合并数据库。

响应增加 session_id、status（completed/failed）、plan_id、workflow_ids、llm_calls；业务失败返回 HTTP 200 加 status=failed 和 errors，参数/认证错误使用 HTTP 4xx。必须检查 status，不能根据存在 primary_sql 判断执行成功。只有主候选校验/执行；其他候选的 validation/execution 为 null，不能声称已验证。多库顺序处理并共用一次请求的 LLM 预算；任一库失败时整体 failed，但保留其他库结果供排查。

SSE 事件为 workflow_start、实际 stage(started/completed/failed)、parse/schema/sql/validation/execution、可选 error 和最终 done。done 携带完整查询响应；error 表示失败，不应自动重新提交。不会人为拆分 SQL 制造 token 事件。连接中断或用户取消不能推断后台执行完成，也不应自动重试；客户端保存 workflow_start 返回的 session_id 并在后续请求携带。取消会传播到 Runner；同步数据库调用的即时取消能力仍受限，第四阶段处理。SSE 队列上限 64，关闭生成器会取消并等待生产任务。

会话当前保存在进程内，最多将最近 8 轮传入 prompt；重启、多进程、并发同会话隔离、TTL 和容量控制尚待第四阶段完善。客户端新建对话须清除 session_id；历史条目保存对应 ID。共享管理员工作台不提供不同管理员之间的私有历史隔离。

permissions 来自 agent.yml，deny/ask 阻断节点声明的工具。生成工具明确 allow，是已认证管理员查询操作的授权范围；没有新增自动审批流程。请求级 ContextVar 预算覆盖实际模型尝试和 fallback，测试适配器由 BudgetedRouter 计数。预算耗尽/模型全部失败不会制造 fallback SQL 伪装成功。

mock 模式响应明确 warnings；未连接数据库的生成结果不能称为数据库答案。固定计划当前不执行旧 API 中散落的自动 self-heal、歧义追问、自动领域匹配、查询对学习或文档 RAG 扩展；不能把这些模块存在视为主链路已接通。后续增强必须以显式计划/节点接入并独立验证。Schema 检索及领域规则接线在第三阶段验证，真实模型正确率需要标注业务数据与凭据。

`/workflows` 从 YAML 读取模板，并以 query_executable 标明当前入口；custom 仅更新进程内目录，不落盘、不改变查询计划。`/workflows/simulate` 是启发式展示，status=simulation 并返回 actual_query_plan；不参与真实流转。
