# 阶段47：类型债务清理与依赖维护

2026-10-31。起点是阶段44–46 一直保留的实测基线：`mypy app/` **178 项 / 41 文件**。本阶段把 `app/` 清到 **0 项 / 191 文件**，并在此过程中把 9 处"类型报错其实是功能早已坏掉"的地方逐一定性。依赖升级部分**未开始**，见文末边界。

## 口径

- 起点：在阶段46 工作区（`763a04e`）重建基线，用同一解释器与同一份已装依赖（`types-PyYAML 6.0.12.20260906`、`asyncpg-stubs 0.32.0`、`mypy 2.4.0`）复现 **178 项 / 41 文件**，与既有记录一致。
- 每批按模块依赖排序：先入口与真实执行/存储，再公开接口，再 Agent 与边缘模块。
- 每批用 `(文件, 错误码, 消息)` 三元组比对，不按行号比对（行号会随补注解漂移）。目标：消除项可列出，新增项必须为 0。
- 没有关闭 `strict`，没有放宽 `mypy.ini`/`pyproject` 配置，没有批量 `Any` 或批量 `type: ignore`。全阶段实际只保留 1 处定向 `# type: ignore[misc, assignment]`（`openai` 可选导入的模块级回退，原本就在，只修正了错误码）。

## 分批消除账

| 批次 | 范围 | 提交 | 类型项 |
|---|---|---|---|
| 1 | 应用入口：`api/__init__.py`、`bootstrap.py`、`auth/security.py`、`api/health.py` | `e63a748` | 178 → 167（−11） |
| 2 | 存储与检索边界：`storage/mysql_store.py`、`retrieval/lancedb_store.py`、`retrieval/embedding.py`、`rag/index_refresher.py`、`learning/query_pair_store.py` | `dcc4144` | 167 → 148（−19） |
| 3 | 查询与 SQL 工具路由：`api/query.py`、`api/sql_utils.py` | `d818b3a` | 148 → 130（−18） |
| 4 | 其余公开路由：schema/documents/settings/feedback/learning/mcp_api/subagents/workflows/auth/domains | `0e2806f` | 130 → 56（−74） |
| 5 | 多 Agent 层：`agents/trace.py`、`bus.py`、`schema_retriever.py`、`sql_generator.py`、`sql_validator.py`、`collaboration.py` | `10762ee` | 56 → 44（−12） |
| 6 | MCP 服务器：`mcp/fastmcp_server.py`、`mcp/servers/learning_server.py` | `0c8b956` | 44 → 33（−11） |
| 7 | 核心与节点：`core/*`、`db/dialect_adapter.py`、`nodes/*`、`subagent/manager.py` | `adabfe7` | 33 → 8（−25） |
| 8 | 知识层收口：`knowledge/metricflow_layer.py`、`knowledge/evolvable_context.py`、`agents/sql_validator.py` | `b382e5b` | 8 → 0（−8） |

合计消除 178 项，引入 0 项（两批中途各出现过 1 项，均在同批内修掉：`domains.py` 因修好 `Literal` 而变成"多余 ignore"、`schema_retriever.py` 因引错导入路径而报 `attr-defined`）。

## 三处"基础设施改写"而非补注解

**1. 第三方无 stub 的库改成局部声明**（`typings/`）

`pyarrow` 与 `sentence-transformers` 未带 `py.typed`。原先的处理是让 mypy 整文件跳过；本阶段改为按仓库既有 `jieba`/`aiomysql` 约定，只声明代码真正用到的表面：

- `typings/pyarrow/__init__.pyi`：`Schema`/`DataType` 与 `schema`、`utf8`、`bool_`、`int32`、`int64`、`float32`、`float64`、`timestamp`、`list_`。读表/RecordBatch/compute 未声明，因为类型化代码不调用。
- `typings/sentence_transformers/__init__.pyi`：`SentenceTransformer(name)` 与 `encode(list[str], ...)`。`encode` 在 numpy/torch 后端返回类型不同，保留 `Any` 由调用方逐元素转 float。

两处都在 `typings/README.md` 里写明"仅 mypy 可见、不代表可选依赖已安装、新增调用前先复核"。

**2. 装饰器用 `ParamSpec` 保签名**（`core/performance.py`）

`track_latency`/`count_calls` 原先是裸 `Callable`，既报 `type-arg` 又让被装饰函数丢签名。改为 `Callable[[Callable[_P, Any]], Callable[_P, Any]]`，返回类型仍为 `Any`（包装器运行时按协程性分支），参数类型保留。

**3. FastMCP 装饰器走真实类型**

`mcp/fastmcp_server.py` 的 9 个 `@mcp.tool`/`@mcp.resource` 全部报 `untyped-decorator`，根因是 `mcp: Any`。改为 `TYPE_CHECKING` 下导入 `FastMCP` 与 `Transport`，参数标注为真实类型后 9 项一并消除；`run_async` 覆写改用 `Transport | None` 与基类一致。

## 定性出来的 9 处真实缺陷

类型错误暴露的是"这段代码从来没跑通过"，不是注解缺失。已修 8 处、保留 1 处待独立改动：

| # | 位置 | 事实 | 处理 |
|---|---|---|---|
| 1 | `learning/query_pair_store.py` | `EmbeddingGenerator()` 少 `provider`，构造必抛 → 被 `except` 吞成"嵌入不可用"哨兵，学习库从未写入过向量 | 新增 `create_embedding_provider()`（provider 选择从 `rag/engine.py` 抽出，引擎改为委托，测试仍可 monkeypatch 引擎侧函数），两条路径共用同一 provider |
| 2 | `knowledge/evolvable_context.py` | `from app.rag.index_refresher import index_refresher`，该模块级单例不存在 → 必 `AttributeError` | 改用 `app.rag.get_index_refresher()` |
| 3 | `api/query.py` | `GET /sessions/{id}` 与 `/sessions/{id}/versions` 调用 `MySQLStore.get_session*`，方法不存在（MySQL 也没有会话表），异常被 `except Exception: pass` 吞掉，实际一直走内存 | 删除死分支，docstring 明说会话是本 worker 进程内状态（与阶段46 单 worker 结论一致） |
| 4 | `api/feedback.py`、`api/learning.py` | 用 `store.xxx()` 触发 `AttributeError` 来"探测"MySQL 不可用 | 显式 `if store is not None`，行为不变但不再借用异常控制流、也不再打误导性告警 |
| 5 | `api/domains.py` | `TermAddRequest.mapping_type: str` 与 `TermMapping.type` 的 `Literal` 不一致，靠 `# type: ignore` 压住 | 改为同一 `Literal`，非法值现在由 FastAPI 返回 422；顺手删掉多余 ignore |
| 6 | `api/mcp_api.py` | `RegisterServerRequest.server_type` 默认 `"custom"`，而 `MCPServerConfig.server_type` 的联合类型里没有 `custom`（是 `external`） | 请求模型改用同一 `Literal`，默认 `external`；非法值 422 |
| 7 | `mcp/servers/learning_server.py` | `handle_approve_rule` 导入不存在的 `domain_manager`，又用 `BusinessRule` 不接受的 `name`/`enforcement`/`domain` 关键字，每次调用都返回原始 TypeError 文本 | 明确改为"规则持久化未实现"并说明缺什么（YAML 写入器、`suggested_enforcement: dict` → `enforce: list[str]` 的映射）；同时删掉从未可观测的 `target.status` 变更 |
| 8 | `knowledge/metricflow_layer.py` | `auto_generate_metrics_from_glossary` 同样导入不存在的单例 → 恒返回 0；`suggest_dimensions` 用缺参构造 `LanceDBStore`/`SchemaMetadataRAG` 并迭代协程 → 恒走 except | 前者改用构造时注入的 `DomainManager`；后者无调用方，改为返回空并写明原因 |
| 9 | `agents/schema_retriever.py` | 术语/规则匹配 `await` 两个**同步**方法且用错关键字（`domain_id=` 对 `domain=`），异常被 `contextlib.suppress` 吞掉 → 两处匹配恒为空 | **未修**。修它会改变 Agent 输出，须单独验证；只把局部列表标成真实元素类型，缺陷记在此处 |

另有 3 处变量复用/类型收窄的隐性 bug 随批次清掉：`core/sql_validator.py` 用 `col` 复用 `ColumnSchema` 循环变量接收可能为 `None` 的查表结果；`nodes/schema_linking.py` 用 `all_matches` 同时装 2 元组与 3 元组（正是 `misc` 两条的来源）；`subagent/manager.py` 的列表分支递归调用联合返回类型的方法，靠 `type: ignore[arg-type]` 压住。

## 验证

| 项 | 结果 |
|---|---|
| `mypy app/` | **Success: no issues found in 191 source files**（基线 178 项 / 41 文件） |
| `ruff check app tests scripts typings` | 全通过 |
| 本地全量 `pytest` | **4248 通过 / 3 失败 / 83 跳过**，与阶段46 完全一致 |

3 项失败仍是 Windows 平台既有问题，与本次改动无关：`test_cli_refuses_hardlink_input_alias`、`test_concurrent_writers_do_not_mix_records`、`test_concurrent_request_audits_keep_their_own_tokens`（阶段46 已用未改动工作区复现）。

本地环境仍需 `tzdata` 才能跑 RAG 相关用例（Windows 上 `pyarrow` 报 `ArrowInvalid: The zoneinfo module or pytz package must be installed`）。未改依赖声明，Linux CI 自带系统时区数据。

CI 门禁新增 `mypy app/`（在 `ruff check .` 之后、原有逐文件门禁之前），逐文件门禁保留以便回归时定位到阶段。

## 边界

- **依赖升级未开始。** 规划要求"依赖升级与类型修复分开提交、先锁快照再逐项升级、重新核验官方兼容资料与当前告警"。本阶段只做了类型债务，`pyproject.toml` 未改，7 项构建期依赖告警仍是旧结论、未重新核对，不能引用为当前漏洞数。
- 只有 `app/` 达到 strict 全清；`scripts/` 仍由逐文件门禁覆盖，`tests/` 未纳入 `mypy`。
- 第 9 项（Agent 术语/规则匹配）仍是死路径，本阶段刻意不改语义。
- 类型清除不等于行为验证：除上述 9 处定性外，本阶段没有新增功能测试；第 1、3、4、5、6、7、8 项的行为变化（新增向量写入、422 校验、显式未实现）都只靠既有 4248 项回归兜底，未做真实环境复核。

关键改动：[`typings/`](../../typings)、[`app/knowledge/retrieval/embedding.py`](../../app/knowledge/retrieval/embedding.py)、[`app/core/performance.py`](../../app/core/performance.py)、[`app/mcp/fastmcp_server.py`](../../app/mcp/fastmcp_server.py)。
