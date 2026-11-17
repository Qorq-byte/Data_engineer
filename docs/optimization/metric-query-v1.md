# 确定性指标查询 v1：计划与验收

日期：2026-11-17。前序本地验收为 4659 passed；保留当前工作区所有既有修改。

同日后续新增 [指标发现与 MCP 接口](metric-discovery-mcp.md)。本文件保留 v1 初始测试数字；
其中“没有发现 API/MCP 参数”是当时边界，最新接口与认证说明以该后续记录为准。

## 选择与范围

优先接通指标查询，因为它能复用现有 Harness 并用真实 SQLite 验证，无需云凭据。
直接开放旧引擎会带入跨表首表选择、任意公式和未消费的 refine 语义；先实现受限且明确的目录契约。
自主多 Agent 与租户迁移依赖产品边界，留在后续阶段。

服务端配置 `METRIC_CATALOG_PATH` 指向部署管理员维护的 YAML，默认空值禁用。
目录有 version，按 domain + database（已登记 db_id）分组，指标只允许裸表名/列名、
sum/count/avg/min/max 结构化聚合及每指标允许的维度。拒绝重复键、重复名称/绑定、未知字段和超限输入。
请求显式给出 metrics、metric_catalog_version，可选 metric_dimensions 与 metric_filters（字符串等值）。
多个指标必须来自同一表，维度和过滤列必须属于每个所选指标的允许集合；不支持任意公式、模板、JOIN 和时间粒度。
首版验收时仅支持单工作进程；后续已将目录纳入不可变发布包并接入共享运行时，
当前条件及实际证据见[共享 worker 指标目录](shared-metric-catalog.md)。

YAML 固定 `metric_resolve → validate_sql → execute_sql`，取消尚未实现的 refine 生成步骤。
解析节点用受验证的目录生成 SQLCandidate，校验与执行继续沿用原节点和权限；不调用 LLM。
目录快照在准入/创建会话前加载并验证，版本、内容哈希随响应回显。默认查询和自动计划选择不变。
目录的版本标签由管理员维护，每次修改必须升级版本；内容哈希标识此次实际使用的文件，不能仅用版本标签证明文件不可变。

## 分阶段交付

1. 目录/请求契约：dataclass 领域模型、严格文件读取及作用域校验；配置示例不作为生产指标签收。
2. 固定工作流接线：请求快照注入节点，SQL→候选→验证→真实执行，响应/JSON/SSE 证据和能力状态准确。
3. 本地验收：SQLite 的 SUM/COUNT/分组/过滤结果与直接查询一致；未知名称/越域/越库/旧版本/非法目录提前拒绝；
   无目录仍 400，执行默认关闭，执行时强制验证，行数限制有效，恶意过滤值不会成为 SQL；LLM 调用为 0。
4. 质量门禁与文档：锁定依赖环境执行相关回归、全量本地回归、ruff、mypy、README 检查，更新支持矩阵。

验证解释器：`.stage62/.venv/Scripts/python.exe`，工作目录为当前仓库根目录。
命令：`-m scripts.check_dependencies`、`-m pytest tests/test_knowledge/test_metric_catalog.py tests/test_api/test_metric_query.py -q`、
`-m pytest -m 'not real_database' -q`、`-m ruff check .`、`-m mypy app/`、`scripts/check_readmes.py`。

## 状态

目录、请求和固定计划接线已实现。相关回归 494 passed（包含本次新增的 37 项目录/SQL 与 28 项 API/安全测试）。
全仓 mypy：211 files 通过；锁定依赖检查 33 项通过。最终全量本地回归：
**4726 passed / 1 skipped / 145 deselected**，255.12s（Python 3.12.13，匹配 constraints.txt）。
唯一 skipped 为 Windows 不支持/未安装 uvloop；145 项外部数据库案例未运行。两项 Pydantic ReadOnly 提示为非失败警告。
ruff、前端函数契约/可重复构建/失败保留旧产物、四语言 README 链接检查通过。
机器可读证据：[metric-query-v1-2026-11-17.json](results/metric-query-v1-2026-11-17.json)，
包括源码/fixture/JUnit 哈希、版本、测试计数、失败原因与修复记录；原始日志位于 ignored `data/metric-query-v1/`。
Snowflake 实库及业务质量基线仍待外部条件。

全量回归期间另定位到评估器的 SQLite journal 消失竞态：产物枚举后 journal 在 stat 前已被提交线程删除。
`app/evaluation/nl2sql_runtime.py::artifact_bytes` 现只 stat 一次，仅容忍 FileNotFoundError；原文件总量上限不变，
其他异常照常失败。`tests/test_evaluation/test_artifact_inventory.py` 用真实 SQLite DELETE journal 重现删除时序，
另验证目录/文件计数和非法路径错误，2 项通过；包含该修复的评估/指标相关回归 85 passed。

## 配置和调用

由管理员在服务器保存 YAML，并设置 `METRIC_CATALOG_PATH` 后启动/重启单工作进程。
请求无法选择目录路径；没有上传或编辑目录的 HTTP API。更换内容应原子替换文件并升级 version。
请求在准入前取完整快照，运行中替换目录不会更换此次请求的定义。

下面仅为合成结构示例，`database` 必须改为连接 API 返回的实际 db_id；不会自动建立或解析数据库连接：

```yaml
version: example-v1
bindings:
  - domain: ecommerce
    database: db_id_from_connect
    metrics:
      - name: order_amount_sum
        table: orders
        column: amount
        aggregation: sum
        dimensions: [region, status]
      - name: order_count
        table: orders
        column: '*'
        aggregation: count
        dimensions: [region, status]
```

`order_amount_sum` 只表示 amount 的 SUM；没有隐含取消订单、退款、税费或货币口径。
业务方必须确认这些定义；本项目不把示例当作可发布的“营收”口径。

通过带 operator JWT 的 `POST /api/v1/query` 或 `/api/v1/query/stream` 提交同一请求体：

```json
{
  "nl_text": "按区域查看已完成订单金额与数量",
  "plan_id": "metric_query",
  "auto_plan": false,
  "domain": "ecommerce",
  "database": "db_id_from_connect",
  "num_candidates": 1,
  "metrics": ["order_amount_sum", "order_count"],
  "metric_catalog_version": "example-v1",
  "metric_dimensions": ["region"],
  "metric_filters": {"status": "completed"},
  "execute": true
}
```

`nl_text` 为说明文字，不负责推断指标或过滤条件；执行语义只来自显式字段和目录。
`execute` 默认 false；即便 `run_validation=false`，该计划也会对捕获的真实 Schema 执行校验。
必须保持 `link_schema=true`，不能用 schema_ddl/schema_notes 替换；不接受模型覆盖或多个候选。
HTTP 400 表示计划/目录/选择条件不满足；不支持的请求字段（例如公式、目录路径、metric_time_grain）为 422。
运行期 Schema 校验或权限拒绝仍沿用 HTTP 200 + status=failed，不会执行 SQL。
目录未配置/损坏、版本不符、缺指标或越域/越库在准入和创建会话前拒绝。

响应的 `plan_output.metric_catalog` 回显 version、sha256、domain、database、metrics；
`plan_output.node_names` 为三个实际计划节点，`candidates[0].generation_source="prepared"`，`llm_calls=0`。
JSON/SSE 的 SQL、结果和目录快照保持一致，SSE 额外使用 resolve 阶段和原有 sql 事件。
`/api/v1/workflows` 动态报告目录有效性，不需要重新导入模块；auto_plan 候选仍仅 request_query/ez_query。

## v1 数据边界与代码证据

- `app/knowledge/metric_catalog.py`：最大 1 MiB UTF-8 YAML，最多 100 绑定/每绑定 100 指标；拒绝别名、重复键、重复定义及未知字段。
  请求最多 8 指标、32 维度/过滤列；名称为 1–128 位 ASCII SQL 标识符，区分大小写；过滤值为至多 1024 字符的字符串等值。
  只允许 sum/count/avg/min/max；仅 count 可以用 `*`，其他聚合要求明确列。
- SQL 用 SQLGlot AST 构造，标识符加引号、过滤值作为字符串字面量；仍经过原只读策略。
  原策略对反斜线及歧义注释的拒绝继续适用，不为指标路径放宽。
- `app/workflow/metric_runtime.py`：预准入作用域/版本和连接检查；`plan_runtime.py` 注入不可变选择快照。
- `app/nodes/metric_resolve.py`：选择快照转为候选及目录证据；旧 MetricFlowEngine 的组件接口保留为实验/内部用途。
- `app/workflow/plans/metric_query.yml`：固定 resolve→validate→execute；Runner 决定生命周期，没有节点互调或 LLM 流程控制。
- `app/harness/runner.py`：语法评估遵循节点输出的方言；`execute_sql.py` 根据实际 MySQL 驱动识别只读解析方言，保留默认拒绝。
- 实际执行证据只有 SQLite；PostgreSQL/MySQL/DuckDB/Snowflake 有 SQL 渲染、解析/只读和评估检查，不能等同各实库端到端认证。
- 无跨表 JOIN、任意公式/模板、时间粒度、别名推断、NULL/范围过滤或子 Agent 指标动作。
  独立指标目录发现 API 与 MCP 指标参数已在同日后续阶段接入，见文首链接。
  共享 worker 的封存及跨进程一致性由上述后续阶段单独验收；缺少封存或活跃运行时仍拒绝。
  业务数据准确率、租户隔离和费用/容量 SLO 均不由此阶段证明。

## 验证数据与方法

`tests/fixtures/metric_catalog.yml` 为本仓库合成目录，关联 `tests/fixtures/test_schema.sql` 的真实临时 SQLite：
5 条订单（含已完成及取消）、3 用户、4 商品。将五种聚合以及分组/过滤结果与同库直接 SQL 对比；
测试危险字符串保持为数据，未知/越界/过期/非法输入拒绝，零 LLM 预算也能完成合法指标请求。
接口测试还覆盖目录快照、即时版本更换、目录有效性展示、Schema 错误、无执行、截断、权限默认拒绝、持久化 trace 和 JSON/SSE。
这些是工程契约与合成结果证据，不是模型准确率、语义召回或生产业务基线。

## 收尾与下一步

本阶段规划、实现、接线和本地测试完成，未调用外部模型或数据库，未部署/发布。
生产启用前需业务方签收目录定义与版本策略，用实际目标库独立核对结果。
目录封存到共享 worker 发布包、指标发现/MCP 接口已在后续阶段继续接入，跨表/时间语义需先另行设计。
Snowflake 凭据/fixture/费用条件、业务标注集/生产 SLO 和租户拓扑仍为外部待办。
