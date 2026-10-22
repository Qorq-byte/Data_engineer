# 阶段9：跨JSON/SSE/MCP查询契约

日期2026-10-21，基准5286a64；阶段8代码与补记CI均成功。依据[先行计划](stage-9-plan.md)实施，继续独立分支codex/mvp-hardening，不合并main。

## 实施结果与代码依据

- `app/workflow/contracts.py`：QueryInput结构请求协议、限定事件名及可await回调；QueryStatus仅completed/failed；错误负载TypedDict、不可变QueryFailure dataclass和共享分类器。模块不依赖FastAPI/Pydantic，领域模型仍为dataclass。
- `workflow/query_service.py`：请求、事件回调、注入Router及计划返回使用已有明确契约。容量异常移到contracts并继续从service导入可用，既有调用者保持兼容。未改计划、节点、Runner流转或资源/权限/预算逻辑；异构结果和上下文仍动态。
- `api/query.py:submit_query/submit_query_stream`：最终响应统一经过QueryResponse校验，SSE done按Pydantic JSON模式序列化，过滤额外字段并统一Decimal/时间值。JSON原先未捕获的内部故障现为结构化500；JSON/SSE共用截止504、容量503、内部500分类，容量带1秒显式重试提示。基础设施失败使用固定公开文本；内部异常仍记入服务端日志，不宣称日志已全面脱敏。
- `mcp/fastmcp_server.py:_register_nl_query`：保留原SQL/结果/error等字段和100行上限，正常响应增加查询/会话/计划/trace/预算标识与status；业务失败增加WORKFLOW_FAILED。异常包含稳定error_code、http_status、errors；参数错误INVALID_REQUEST/422，内部异常不再回传str(exc)，保留error_type兼容字段。MCP调用仍经过生产API与统一服务，未复制业务工作流。
- `.github/workflows/checks.yml`：原标准strict门禁增加contracts模块，保留默认导入追踪及全仓Ruff/全量测试。README/AGENTS/节点指南同步命令；[query-contract](query-contract.md)记录公开字段、兼容及限制。

## 验证环境与实测

本机Windows/Python3.13.9、Node24，mypy2.4.0目标Python3.12、Ruff0.17.1；沿用阶段8隔离类型依赖（types-PyYAML6.0.12.20260906、asyncpg-stubs/开发驱动0.32、本地aiomysql0.3.2声明）。运行测试仍使用原site-packages的LiteLLM1.102.0/asyncpg0.31.0。测试LLM和embedding为mock；SQLite/DuckDB/LanceDB/FTS5为真实本地实现。FastMCP使用其Client/Server进程内传输，不是外部部署联调。

- `python -m pytest tests/test_api/test_query_contract.py tests/test_api/test_runtime_limits.py tests/test_harness/test_request_invariants.py tests/test_mcp/test_fastmcp_server.py -q`：**71 passed，3 warnings，16.82秒**，[记录](results/stage-9-target.txt)。随后新增原生单元格序列化用例单独**1 passed（6.70秒）**，[记录](results/stage-9-native-cell.txt)，也已纳入最终全量。
- `python -m pytest -q`：**2994 passed，1 skipped，4 warnings，52.50秒**，[日志](results/stage-9-pytest.txt)。比阶段8新增12项；Windows继续明确跳过uvloop1项，由Linux CI补足，未关闭任何测试。
- 新增用例覆盖三协议截止/容量/未知内部故障/畸形响应四类故障矩阵；实际SQLite成功MCP投影与JSON一致；三入口危险DELETE均失败且订单仍5行；MCP空请求422；两个validate别名；JSON/SSE消费取消并等待生产任务关闭；Decimal("12.50")/datetime序列化与额外字段过滤。既有实际wait_for截止、容量恢复、Runner取消/预算及权限用例继续通过。
- `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py`：**28源文件无错误**，[日志](results/stage-9-core.txt)。
- `mypy app/ --output json`：仍**201项/43文件，退出1**；与当前基准[阶段8诊断](results/stage-8-after.jsonl)按文件/错误码/消息多重集比较，新诊断0。[最终诊断](results/stage-9-after.jsonl)/[摘要](results/stage-9-summary.json)。本阶段目标是运行时契约一致性，未将类型债务数量不变冒充全仓类型通过。
- `ruff check .`全仓通过，`npm run check:frontend`三页五段通过，[Ruff](results/stage-9-ruff.txt)/[前端](results/stage-9-frontend.txt)。本阶段无UI改动，不重复浏览器或压测，无性能/准确率提升声明。
- `git diff --check`通过；原主工作区pyproject.toml SHA256保持E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3，未触碰用户改动或上传运行数据/凭据。

命令设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock及忽略目录DE_TRACE_DIR；本机失效.venv仍使用D:/Users/anaconda/python.exe加原site-packages。类型依赖/Ruff隔离路径沿用阶段8，CI将按pyproject干净安装。

## 范围调整与限制

首次新增测试错误地将实际sqlite3.Row与tuple直接比较，改为核对实际COUNT数值5后重跑通过，没有放松SQL约束。全仓类型首次新增一个局部容器声明诊断，补明确dict参数后回归为201项、新增0；未加ignore。进一步核对发现仅model_dump默认模式仍可能让Decimal经jsonable_encoder变浮点，改为JSON模式，并加入三传输实测对照。

最终done统一校验，不等于所有中间事件字段逐一完成Schema验证；中间execution仍走通用JSON编码，最终值以done为准。MCP参数少于HTTP请求，仅投影结果，不追求两者全部键相同；只有生成了响应才保证查询标识，早期基础设施失败可能没有ID。MCP status/error_code是工具结果字典，不改MCP传输isError。HTTP参数/模型422及全站其他错误格式不在此次统一范围。取消不是业务完成或失败证明，已有传播规则保持。

全仓API/MCP/RAG仍201项静态债务；生产日志权限/敏感数据、真实LLM/外部数据库及外部MCP网络故障需对应环境验证。本阶段未增加自动重试、租户/分布式会话或动态工作流。

下一候选阶段先核对主链路Schema Linking/检索及其验证覆盖，针对实际缺口单独规划；真实语义质量与生产容量继续需要业务样本和运行环境，不以静态清理替代评测。

## Git与远程验收

代码提交[04adbdfd99ebc963e11a0582490f33086876669b](https://github.com/Qorq-byte/Data_engineer/commit/04adbdfd99ebc963e11a0582490f33086876669b)已普通推送，ls-remote核对一致；未合并main、未强推。

[Actions39554593571](https://github.com/Qorq-byte/Data_engineer/actions/runs/39554593571) **success**：Linux/Python3.12干净安装、前端编译、全仓Ruff、扩展strict门禁均通过（28源文件无错误）；全量 **2995 passed，1 warning，29.73秒**，uvloop实际执行通过，补足Windows跳过项。[远程证据](results/stage-9-ci.json)保存对应headSha和各步骤。

阶段9代码远程验收完成。CI只执行明确的类型门禁，不代表全仓201项已解决；后续文档补记仅记录本次已验证代码结果。
