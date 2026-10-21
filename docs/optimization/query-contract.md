# 查询执行与流式契约

JSON `POST /api/v1/query`、SSE `POST /api/v1/query/stream` 和 MCP `nl_query` 共用 `app/workflow/query_service.py:run_query`。API 不能传入计划 ID；由 `request_query.yml` 和 `WorkflowRunner` 固定执行 parse_nl → schema_linking → generate_sql → validate_sql → execute_sql。节点不调用其他节点，LLM 只提供候选 SQL。

请求保留 nl_text/domain/database/dialect/num_candidates/execute/link_schema/model；`validate` 与 `run_validation` 都映射到同一字段。execute=true 时忽略关闭校验的请求，必须校验通过后执行。execute=false 可以只生成；link_schema=false 不连接数据库执行，execute=true 将返回失败。database 应使用稳定 db_id，不以显示名称合并数据库。

响应增加 session_id、status（completed/failed）、plan_id、workflow_ids、llm_calls；业务失败返回 HTTP 200 加 status=failed 和 errors，参数/认证错误使用 HTTP 4xx。必须检查 status，不能根据存在 primary_sql 判断执行成功。只有主候选校验/执行；其他候选的 validation/execution 为 null，不能声称已验证。多库顺序处理并共用一次请求的 LLM 预算；任一库失败时整体 failed，但保留其他库结果供排查。

SSE 事件为 workflow_start、实际 stage(started/completed/failed)、parse/schema/sql/validation/execution、可选 error，以及业务结果的最终 done。容量拒绝/请求截止/内部错误会发送 error 后结束，不保证 done；JSON 分别为503/504/500。done 携带完整查询响应；error 表示失败，不应自动重新提交。不会人为拆分 SQL 制造 token 事件。连接中断或用户取消不能推断后台执行完成，也不应自动重试；客户端保存 workflow_start 返回的 session_id 并在后续请求携带。取消传播到 Runner；工厂管理的原生连接中断并等待线程退出，其他驱动/直接 SQL 入口边界见 runtime-and-load.md。SSE 队列上限 64，关闭生成器会取消并等待生产任务。

会话当前保存在进程内，最多将最近 8 轮传入 prompt；同会话查询在进程内串行，具备 TTL/条数/轮数/版本上限；重启和多进程共享仍未提供，详见 runtime-and-load.md。客户端新建对话须清除 session_id；历史条目保存对应 ID。共享管理员工作台不提供不同管理员之间的私有历史隔离。

permissions 来自 agent.yml，deny/ask 阻断节点声明的工具；生成与执行节点的 required_tools 由代码固定声明，遗漏 YAML tools 不能跳过检查。生成工具明确 allow，是已认证管理员查询操作的授权范围；没有新增自动审批流程。请求级 ContextVar 预算覆盖实际模型尝试和 fallback，测试适配器由 BudgetedRouter 计数。预算耗尽/模型全部失败不会制造 fallback SQL 伪装成功。

mock 模式响应明确 warnings；未连接数据库的生成结果不能称为数据库答案。固定计划当前不执行旧 API 中散落的自动 self-heal、歧义追问、自动领域匹配、查询对学习或文档 RAG 扩展；不能把这些模块存在视为主链路已接通。后续增强必须以显式计划/节点接入并独立验证。Schema 检索及领域规则已接线并在第三阶段验证，真实模型正确率需要标注业务数据与凭据。

前端进度使用 `stage` 的真实状态；事件新增 `skipped` 布尔字段，禁用节点的 completed 不代表执行过。工作台默认请求执行，返回无 execution 时标记“SQL 已生成，未执行”，0行明确为空结果。客户端超时等待上限130秒（默认服务端120秒），停止等待不代表 SQL 已执行或必已取消；手动取消、切换历史/新会话和卸载组件都使旧响应失效。通用请求层自动附加管理员 Bearer；HTTP错误支持字符串和对象 detail，SSE失败不自动转 JSON 重跑。

`/workflows` 从 YAML 读取模板，并以 query_executable 标明当前入口；custom 仅更新进程内目录，不落盘、不改变查询计划。`/workflows/simulate` 是启发式展示，status=simulation 并返回 actual_query_plan；不参与真实流转。

## 阶段9：共享边界与机器可读错误

`app/workflow/contracts.py`提供QueryInput结构协议、QueryEventName/异步回调及纯dataclass错误分类器；Pydantic的QueryRequest/QueryResponse继续保留在API边界，不进入领域dataclass。事件负载仍是异构JSON，并未逐事件做完整Schema验证。

JSON响应和SSE done均经过QueryResponse校验，status只允许completed/failed，过滤未公开字段；SSE使用`model_dump(mode="json")`，与JSON路由的Pydantic序列化保持一致。Decimal单元格保留十进制字符串（例如"12.50"），时间值为ISO字符串；这不是所有驱动类型都已验证，非法响应类型会按内部错误处理。中间execution事件仍采用通用JSON编码，客户端应以done的最终结构为准，不能假定中间事件与done逐字相同。

截止为QUERY_TIMEOUT/HTTP504，容量为QUERY_CAPACITY/HTTP503，未知内部异常或响应模型校验失败为INTERNAL_ERROR/HTTP500。JSON位于detail的code/message；SSE在HTTP200流中发送error的code/message后结束，没有done。容量同时提供retry_after_seconds=1，JSON保留Retry-After: 1；这是显式再次请求的提示，不授权客户端自动重放。基础设施错误的message为固定公开文本，不回传原异常细节；服务端仍记录内部异常，日志访问和敏感信息管理仍需部署侧约束。参数/模型校验的既有HTTP422格式保留，未改写全站错误处理。

MCP nl_query保留sql/columns/rows/row_count/truncated/errors/warnings及失败时的error，新增status、query_id/session_id/plan_id/workflow_ids/llm_calls。只有实际生成响应才保证查询标识存在；请求校验/截止/容量/内部失败可能没有标识。业务失败为status=failed/error_code=WORKFLOW_FAILED，保留SQL供排查，不能据此认为已执行；基础设施失败另有http_status和稳定error_code，容量带retry_after_seconds；无效请求为INVALID_REQUEST/422。error_type为兼容保留的异常类别名称，不含原异常文本。结果仍最多100行。MCP这是工具结果字典的业务状态，未将其改成MCP传输协议的isError机制。

取消属于BaseException传播路径，不转换为上述普通错误；SSE关闭时取消并等待生产任务。实际Runner/权限/预算/校验与只读执行仍共用，未新增节点或重试。阶段9的SQLite端到端、故障注入及FastMCP进程内传输记录见[报告](stage-9.md)；真实外部MCP客户端和真实数据库网络故障需单独验收。

阶段10增加Schema Linking降级诊断：SCHEMA_RAG_UNAVAILABLE（检索失败）、SCHEMA_RAG_HITS_REJECTED（失效库/表/列命中被丢弃）、SCHEMA_LINKING_FALLBACK（没有有效关联或检索器失败，改用现有关键词匹配）。它们是warnings，不代表整个查询失败；SSE的schema事件也包含warnings，最终JSON/SSE done/MCP均保留汇总。只读执行和原Schema SQL校验继续决定执行是否成功。警告不含原始异常文本；关联有效性仍需业务标注评测。

阶段11的SQLValidator按SQLGlot作用域检查源表、CTE、派生表、别名和列，不再到未参与查询的表中寻找同名列；歧义列阻断。优化只作用于独立校验AST，发送给数据库的primary_sql保持原文。只读策略与执行层共用，内部配置skip_syntax=True不能放行写入/副作用或不可安全解析的SQL；其含义收窄为跳过详细语法诊断。skip_schema仍供固定元数据路径使用，不关闭只读策略。类型/性能/业务规则仍按原设计为提示，不据此证明结果正确。

阶段12在TableSchema追加可选schema_name/catalog_name（未知为None）：实际内省覆盖PG public、SQLite main、MySQL所查数据库、DuckDB当前catalog的main。校验仅在限定名与这些元数据一致时通过；限定列必须匹配当前作用域来源，错误schema/catalog或未证明的限定仍报SCHEMA_ERROR。旧快照保留SQLite main/MySQL同库fallback；缺失元数据的PG/DuckDB限定继续拒绝。标识符按方言处理大小写，不改写primary_sql。真实SQLite/DuckDB已验证，PG/MySQL仅离线解析，未完成在线验收。

表字典仍以简单表名为键，不支持跨schema同名表集合、跨catalog联查授权或完整search_path解析。内省明确固定namespace，但未限定SQL仍按数据库会话的search_path/临时表/当前catalog解析；快照校验不是数据库权限边界。应用连接须固定到所内省的环境，改连接目标后刷新Schema和RAG；不可在共享连接任意切换USE/search_path。原生只读及权限守卫继续生效，不等于对象级授权已实现。Schema管理GET接口的现有投影未扩展，这些元数据用于内部生成/验证/索引。

畸形/空候选响应按调用隔离，存在有效文本候选时保留它；全部无效仍失败，取消继续传播，不自动重试。第一个有效候选作为primary，其余候选仍不自动执行。ValidateSQLNode异常会写回validation_passed=False和空报告，避免旧上下文的成功状态残留。[阶段11报告](stage-11.md)包含SQLite及三协议CTE/歧义路径验证。
