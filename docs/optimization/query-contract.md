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

表字典仍以简单表名为键，不支持跨schema同名表集合、跨catalog联查授权或完整search_path解析。内省明确固定namespace，但未限定SQL仍按数据库会话的search_path/临时表/当前catalog解析；阶段14为带来源版本的SQLite增加原生main读取守卫，DuckDB解析状态保护见阶段15；其他方言与无来源绑定路径仍有此限制。快照校验不是数据库权限边界。应用连接须固定到所内省的环境，改连接目标后刷新Schema和RAG；不可在共享连接任意切换USE/search_path。原生只读及权限守卫继续生效，不等于对象级授权已实现。Schema管理GET接口的现有投影未扩展，这些元数据用于内部生成/验证/索引。

畸形/空候选响应按调用隔离，存在有效文本候选时保留它；全部无效仍失败，取消继续传播，不自动重试。第一个有效候选作为primary，其余候选仍不自动执行。ValidateSQLNode异常会写回validation_passed=False和空报告，避免旧上下文的成功状态残留。[阶段11报告](stage-11.md)包含SQLite及三协议CTE/歧义路径验证。

阶段13将Schema、database_name、dialect和原生句柄一起解析为ResolvedQueryTarget；JSON/SSE/MCP共享服务不再在Runner开始前按db_id重新获取连接。执行节点持有现有连接lease后确认注册表中的同db_id仍指向原句柄；若已重连或断开，输出connection_changed和固定提示“Connection target changed”，没有execution结果，不自动切到新库。JSON仍HTTP200/status=failed，SSE产生WORKFLOW_FAILED/error并以failed done结束且没有execution事件，MCP保留WORKFLOW_FAILED和空rows；不是新增HTTP基础设施错误码。用户可明确发起新查询使用新目标，旧请求不自动重试。

直接SQL API也将db_id传给执行守卫；调用者自管、未提供db_id的原生连接保留既有行为。_resolve_schema/_resolve_all_schemas内部兼容投影保留旧返回结构和首库/全部库选择规则，但不应用于有执行能力的来源绑定。未知连接/内省失败warning改为固定类别文本，避免公开原始驱动异常；未新增公开异常详情字段。阶段13尚未处理单句柄上的USE/search_path/temp DDL及外部DDL漂移；其中SQLite部分已由阶段14补充，其他方言及剩余竞态见下文和[阶段13报告](stage-13.md)。

阶段14：实际SQLite快照增加可选source_schema_version（main.schema_version，非模型version），裁剪保留。缓存仍按句柄/TTL/深拷贝管理，但命中前核对main版本，DDL变化即重新内省。带来源版本的EXPLAIN/结果执行都在原生worker内核对；版本不同输出schema_changed及“Schema source changed”，按现有WORKFLOW_FAILED业务失败投影，不自动刷新重执行。用户可重新发起请求获取新Schema。内省前后版本不同也拒绝快照。

这些绑定查询的SQLite authorizer只接受main实际读；对数据库名为None的优化读取，先确认对象存在于main且未被temp同名对象遮蔽，其他来源拒绝。预先存在的temp同名对象也不能被当作main数据；main限定SQL仍可用。正常CTE/聚合/索引读取保持，SQL原文不改写。source_schema_version=None的旧快照与直接SQL不启用新增main限制，仍按既有只读/超时/注册身份规则执行；这是兼容边界，不是多库对象授权完成。DuckDB/PG/MySQL未应用SQLite版本机制。详见[阶段14报告](stage-14.md)。

阶段15为实际DuckDB快照增加可选duckdb_source_state，包含catalog/schema名和原生OID、search_path及用户临时对象集合；裁剪保留。缓存命中前在lease/native worker核对，内省前后状态不同则拒绝混合快照。切库、namespace重建或同名catalog重新挂载后不再仅依赖TTL；进行中请求状态变化返回schema_changed及固定“Schema source changed”，不自动切库、SET或重试。

绑定执行暂要求当前schema为main且没有用户临时表/视图，预存不支持环境返回source_scope_unsupported及“DuckDB source scope unsupported”；这些是节点状态，三协议仍按WORKFLOW_FAILED业务失败投影，无execution结果/事件。即使SQL完整限定或临时对象无关也保守拒绝；移除临时对象/恢复main后显式新请求可恢复。USE到另一个catalog的main可以由新请求重新取源，不能迁移旧请求。绑定EXPLAIN用原始句柄，避免DuckDB cursor()的独立会话语义，最多1000条计划行，失败不走无守卫回退。

None状态的旧快照/直接SQL保留既有只读范围，不应用上述main/temp约束。此状态不是表列DDL/数据版本，不能证明持久视图内部跨库血缘或对象权限；普通表列DDL仍需TTL或显式refresh，RAG也需按目标db_id刷新。无新的多schema支持/自动catalog索引迁移，详见[阶段15报告](stage-15.md)。

阶段16统一手动Schema刷新、连接后后台索引、启动索引及IndexRefresher的来源生命周期；同db_id刷新按顺序捕获/强制内省/准备embedding/提交。embedding完成、任何索引删除之前，重新核对注册句柄、namespace状态和当前可提取的表列结构（忽略行数估计和时间戳）；空Schema删除也核对。冲突返回HTTP409，detail为{code: SCHEMA_REFRESH_CONFLICT, message: Schema source changed during refresh; retry explicitly}，不删除旧索引，缓存失效，要求显式重试，不自动写重试。仅连接捕获不存在为404，内部内省错误为固定500；不能将驱动KeyError误报缺连接。

原200响应status=refreshed/rag_indexed/warnings字段保留。embedding不可用/失败或写后端失败仍可能200，但rag_indexed=false及warning；这不表示索引成功。取消继续传播。前端索引重建只将rag_indexed===true计为成功，RAG刷新只将refreshed===true计为成功；false/缺字段/409不再显示全部完成，部分失败保留统计。

提交前来源/embedding校验失败保留旧索引。阶段16的双后端部分写限制已由下述阶段17补充进程内补偿，仍不承诺跨LanceDB/FTS5原子事务。IndexRefresher只在成功发布后记录版本/时间，其周期调度没有默认启动调用，不能当作实时DDL监听已接通。详见[阶段16报告](stage-16.md)。

阶段17：按库完整替换前保存两边原始记录，写入失败尝试分别恢复；补偿成功仍为本次更新失败，HTTP200/rag_indexed=false，固定 warning `Schema index update failed; previous index restored`。恢复失败或旧状态本来不可信时，warning 为 `Schema index recovery failed; retrieval unavailable until successful explicit refresh`，目标库及无范围 RAG 检索拒绝；健康库指定检索独立可用。共享刷新不公开原始后端异常，不自动重试。

显式全量刷新 rag_indexed=true 后才确认对应库可用；upsert 和其他库刷新不能解除隔离。Schema Linking 沿用带 warning 的关键词降级，不使用不可信索引。低级 API 暴露 `SchemaIndexWriteError.restored` 与实例 `unavailable_databases`，不是新增 HTTP 响应字段。此隔离不跨进程持久化，重启不等于恢复；异常退出的持久部署应在开放检索前逐库完整刷新。详情及低级写路径边界见[阶段17报告](stage-17.md)。

阶段18由引擎启动自动将两边持久库ID及当前连接库标为待刷新，成功的完整替换后解除；旧数据不再因重启清掉failure标记而可直接读取。初始化结束前不发布全局实例，失败/取消清理候选句柄。未能重新连接的遗留库保持待刷新，指定健康库仍可检索，SQL路径沿用带warning的关键词降级。

受保护 `GET /api/v1/learning/rag/status` 保留刷新器原字段，新增 `schema_index={status: ready|degraded, refresh_required_databases: [...], recovery_failed_databases: [...], startup_schema_results: {db_id: bool}, scope: ...}`。顶层status=ok/degraded/error；引擎不可用只返回固定 `RAG engine unavailable`。startup_schema_results 是历史，后续修复查看当前两个列表；不把tracked_databases当已验证库数。公开 `/health/ready` 仅增加rag汇总状态/计数，动态更新components.rag，不暴露库ID/路径；RAG可降级，整体就绪条件不变，不调用额外后端/模型探活。详见[阶段18报告](stage-18.md)。

阶段19新增管理员 `POST /api/v1/learning/rag/schema/{db_id}/clear`，无需原连接仍存在；仅清目标Schema索引，与完整刷新串行，成功只失效目标刷新缓存。200响应 `{status: cleared, scope: schema, db_id, removed: {lancedb_count, bm25_count}, cleared_at}`，计数为完成时的真实删除量，重复/未知ID幂等0/0。无引擎503/detail.code=RAG_UNAVAILABLE；准备/写失败503/SCHEMA_INDEX_CLEAR_FAILED，固定message及restored=true/false/null；不泄漏原异常、不自动重试。清理后新的完整刷新可重新建索引。

旧全量clear仅接受JSON body的scope=all/schema/metrics/documents，拒绝query、db_id等未知body字段及非法scope，422/detail.code=INVALID_RAG_CLEAR_REQUEST，拒绝发生在任何删除前；缺body仍兼容all。全量入口是维护窗口操作，不用于按库恢复。断开数据库响应新增rag_index_cleared/warnings，关闭前先标记索引待刷新，索引清理失败不改变已断开事实，可用新按库入口重试。权限仍401/403，具体并发/补偿和使用步骤见[阶段19报告](stage-19.md)。
