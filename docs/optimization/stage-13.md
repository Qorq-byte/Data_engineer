# 阶段13：固定查询来源与拒绝过期连接

依据[先行计划](stage-13-plan.md)，在15e51b2基准及其成功CI之后实施。继续codex/mvp-hardening独立工作树；原工作区及临时文件不动，固定计划/WorkflowRunner/节点生命周期未改。

## 已确认问题与实现

原`query_service._run_query`先从`_resolve_all_schemas`得到Schema，在Runner开始前却再次`ConnectionFactory.get(db_id)`。单库解析还在内省await之后重新获取info。两库同名表在此窗口重连，旧Schema可以被用于验证新连接查询，甚至混合两种方言；缓存本身按句柄区分并不能防止这种消费错误。

- 新`app/db/query_targets.py`定义冻结的`ResolvedQueryTarget`，一起携带Schema、database_name、dialect、原生connection；在首次await之前捕获句柄与info字段，只从该句柄提取Schema。目标结构冻结不代表数据库或其Schema不可变，不提供历史事务快照。
- `app/api/query.py`旧`_resolve_schema/_resolve_all_schemas`改为共享解析器的兼容投影，保留首库/全部库选择与旧结构，按db_id区分，不按显示名去重。未知/失败warning使用固定类别文本，避免公开原生驱动异常；取消属于BaseException，继续传播。
- `app/workflow/query_service.py`使用完整目标并始终向Runner传原句柄，不再在生成前重新取注册连接。LLM不决定节点流转，节点不互相调用。
- `app/db/connections.py:is_current`只比较注册身份；`ExecuteSQLNode.execute`在现有lease内检查context.db_id，一旦变化返回`connection_changed`和固定提示，不触碰旧闭合句柄、不获取替换连接、不自动重试。直接SQL API也传入db_id；调用者自管且不提供db_id的连接仍可用。
- 现有aclose/替换和缓存失效机制沿用；没有增加生成期数据库执行锁或全请求全局锁。核心strict门禁增加新来源模块，CI/README/AGENTS/节点指南同步，未关闭默认导入追踪。

## 验证与实际结果

环境沿用阶段12：Windows/Python3.13.9、Node24、SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0；本机失效.venv解释器由D:/Users/anaconda/python.exe加原site-packages替代。运行设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录；类型检查另加既有type-deps/driver-types。LLM/embedding为mock，SQLite/DuckDB等本地实现真实运行。

- 先在旧代码写入3项JSON/SSE/MCP竞态测试：真实SQLite原库orders有1–5，新库同名表仅999；在cached_extract完成后、返回前通过真实工厂重连。旧请求错误完成、3项均失败，[原日志](results/stage-13-race-before.txt)。相同场景修复后**3 passed，12.58秒**，[修复后](results/stage-13-race-after.txt)；旧请求failed且无执行结果，随后新请求completed并返回[[999]]，原请求没有被迁移。
- `pytest tests/test_db tests/test_api/test_query_contract.py tests/test_nodes/test_execute_sql_node.py -q`：**99 passed、1 skipped、3 warnings，15.52秒**，[记录](results/stage-13-target.txt)。新增13项：三协议竞态3项；真实SQLite→SQLite/DuckDB替换或断开3项；相同显示名/空白显式ID/兼容投影1项；无目标2项；其他db_id不受影响1项；执行lease之前替换/断开2项；调用者自管连接1项。交错用回调和asyncio.Event控制，不靠固定sleep；旧缓存已失效、新目标重新内省，且方言/名称保持原来源。
- 最终`python -m pytest -q`：**3131 passed、1 skipped、4 warnings，65.22秒**，[日志](results/stage-13-pytest.txt)。Windows uvloop跳过项由LinuxCI补足。没有弱化现有测试或安全断言。
- 核心`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py app/db/query_targets.py`：**31源文件零错误**，[记录](results/stage-13-core.txt)。全仓`mypy app --output json`仍退出1：197项/42文件，与阶段12按文件/错误码/消息多重集比较新增0，[诊断](results/stage-13-after.jsonl)/[摘要](results/stage-13-summary.json)。不是全仓类型通过。
- 全仓`ruff check .`、`npm run check:frontend`通过，[Ruff](results/stage-13-ruff.txt)/[前端](results/stage-13-frontend.txt)。没有UI行为改动，不重复浏览器QA；无生产压测，未报告容量/延迟提升。
- `git diff --check`通过；原主工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，原有未跟踪文件及本地依赖/数据不纳入提交。

## 契约与限制

查询业务失败沿用JSON HTTP200/status=failed、SSE WORKFLOW_FAILED/error后failed done（无execution事件）、MCP WORKFLOW_FAILED/空rows；connection_changed是执行节点状态，不是新增HTTP错误码。提示要求显式新查询，不自动重试。其他未替换目标可继续；多库请求仍按既有规则任一失败则整体failed。生成但不执行仍可能输出基于原Schema的SQL，不代表当前库答案。

本阶段解决注册连接被替换/断开的来源混用，不解决同一句柄上的USE/search_path/temp对象、外部DDL/缓存TTL、对象权限或跨进程目标注册；应用连接仍需固定配置，变更后刷新Schema/RAG。异步应用必须使用await aclose/aclose_all，不能将同步close当作并发租约协议。真实PG/MySQL网络故障和生产部署仍无环境，未声称在线验证。

共享来源解析是替代原重复代码的必要调整，未新增框架或配置。后续候选先实证同句柄namespace/缓存漂移，选择最小范围并先行规划；不因固定句柄就宣称名称解析一致性全部完成。运行约束见[运行说明](runtime-and-load.md)，协议见[查询契约](query-contract.md)。

## Git与远程验收

代码提交[71d49e1030ad046ff0f2e9565cde68eb6d040e18](https://github.com/Qorq-byte/Data_engineer/commit/71d49e1030ad046ff0f2e9565cde68eb6d040e18)已普通推送到codex/mvp-hardening，ls-remote核对SHA一致；未强推或合并main。

[Actions39622481064](https://github.com/Qorq-byte/Data_engineer/actions/runs/39622481064) **success**：Linux/Python3.12干净安装、前端编译、全仓Ruff、扩展strict31源文件门禁通过；全量 **3132 passed，1 warning，35.43秒**，uvloop实际执行通过。[远程证据](results/stage-13-ci.json)保存对应headSha及各步骤。阶段13代码远程验收完成；全仓类型债务和同句柄漂移等限制仍按上述记录保留。
