# 未完成领域实施路线图

基线提交为 `0cb5fe4`。工作区已完成 PostgreSQL 双租户双 worker 验收，并接通指标 v2、业务质量与容量验收工具、候选复核预览和租户子 Agent。正式业务对照与目标环境验收的证据见文末记录。

固定五节点查询、其余五个默认可执行计划、自动计划选择和已审批规则闭环均已有请求链路和回归证据。指标 v1 已接入 JSON、SSE 和 MCP，并支持共享 worker 封存目录。双租户单机多 worker 已有 MySQL 目标的实库验收。本路线图从这些能力继续推进。 [支持矩阵](../support-matrix.md) · [共享指标目录](shared-metric-catalog.md) · [不可变 worker 验收](immutable-workers.md)

## 1. 多租户隔离

**首批目标**是验收单机不可变 v2 在 PostgreSQL 查询目标上的双租户运行。发布包已经接受 PostgreSQL 配置，现有双租户进程测试使用 MySQL 查询目标。共享 MySQL 继续承载认证、控制面、会话和配额，现有 v1 发布包继续作为兼容性回归对象。 [发布包](../../app/harness/worker_release.py) · [双租户实库测试](../../tests/real_db/test_immutable_tenants.py)

1. **PR T1** 复用一次性数据库和 worker 启动夹具，为租户 2、3 准备 PostgreSQL 只读账号及不同数据。两方使用相同的 `db_id`、指标名和会话 ID，另为一方配置专属数据源。用 v2 发布构建器封存目标、索引和指标目录，启动两个独立 worker。优先复用现有 MySQL 场景断言，实际差异集中在查询目标夹具。
2. 用正式登录后的 JWT，通过 JSON、SSE、REST 指标发现和 MCP HTTP 检查同名资源返回各自的值。验证跨租户目标、会话、指标和 MCP 会话不可访问，普通账号及已撤销令牌无法沿用管理入口。逐个重启 worker 并恢复会话，验证发布漂移导致 readiness 和新查询拒绝服务。复跑 MySQL 双租户及 v1 恢复用例。 [租户入口与迁移证据](tenant-mysql-migration.md)
3. **后续部署范围**由实际需求决定。若目标需要跨主机或在线配置发布，先冻结拓扑、共享状态清单、发布一致性语义和恢复目标（RTO/RPO）。跨主机先处理发布包和索引分发、跨主机租约与会话，并演练断网、失联、重启和恢复。在线发布另做逐代切换、旧代在途请求处理与回退。租户指标 RAG 若要随领域管理联动，先确定指标事实来源，再验证更新、删除和重建只影响对应租户。 [工作模式与维护约束](immutable-workers.md) · [领域管理验收](tenant-mysql-migration.md#领域管理隔离)

**完成条件**：T1 有两个真实 worker 的 JUnit、数据库版本、发布代、正向结果和拒绝结果，旧版回归通过。更广拓扑按实际部署单独验收，要求越界用例无数据泄漏，故障和恢复满足签收的 RTO/RPO。

## 2. Snowflake 实库验收

**交付 S1**。适配器和请求入口已有实现，四项独立实库测试需在专用 Snowflake 环境执行。现有认证契约为密码认证，目标账号策略若要求其他认证方式，先把相应认证支持立为单独变更。 [独立验收说明](snowflake-acceptance.md)

1. 由平台负责人提供可丢弃的数据库、`DE_ACCEPTANCE_FIXTURE`、专用 warehouse、仅供读取的账号及本轮费用额度。按现有验收文档准备合成表和权限，锁定该环境的 `.[snowflake]` 依赖。
2. 先跑本地策略测试。完成环境准备后，以秘密注入配置并启用 `DE_REAL_SNOWFLAKE_TESTS=1`，运行 `python -m pytest tests/real_db/test_snowflake.py -q --junitxml=data/snowflake-acceptance/report.xml`。检查元数据、直接查询和 Harness 的结果与截断、原生权限拒写、查询超时及取消后的服务端终态和连接复用。若验收失败，按驱动兼容、账号权限或代码行为定位后复验。 [实库套件](../../tests/real_db/test_snowflake.py)
3. 归档提交号、驱动和服务端版本、脱敏权限审核、测试报告和取消终态，再更新支持矩阵的适用版本和认证方式。由平台负责人回收验收环境。

**完成条件**：四项实库用例全部通过，取消请求对应的语句已进入服务端终态，原生拒写后 fixture 行数仍为 5。验收凭据、fixture 或额度缺失时，S1 记录为待实库验收。

## 3. 指标工作流

**先交付 M1**。选一个业务域和一个实际查询目标，用 v1 验收一组单表指标。v1 通过显式指标名、版本、维度和等值筛选组装 SQL，沿固定的 `metric_resolve → validate_sql → execute_sql` 路径执行，已有 SQLite 和共享 MySQL 的工程证据。 [v1 契约](metric-query-v1.md) · [共享目录验收](shared-metric-catalog.md)

1. 业务负责人签收指标粒度、聚合方式、允许的维度与筛选、退款与取消口径、时区、空值和币种规则。数据负责人提供授权样本和独立参考 SQL 或期望结果。对超出 v1 表达能力的指标记录具体需求，首批目录采用可实现的口径。
2. 发布带版本和哈希的目录，绑定租户、域及真实 `db_id`。在实际目标库运行发现和查询，对照 gold 结果验证 JSON、SSE 与已经启用的 MCP 传输。复验旧版本请求、越域越库、无执行、目录更新与回退。使用共享 worker 时，以新发布代统一封存和切换。
3. **M2 按签收口径拆分**。时间聚合先明确时区、周起点和区间边界，再引入结构化粒度字段及目标方言用例。跨表指标先声明允许的连接关系、基数和聚合粒度，使用重复键、空关联和一对多样本核对汇总，再接入同一固定计划。每项扩展单独版本化，并跑 v1 兼容回归。自然语言映射如有需求，先复用作用域内的指标发现做候选确认，再提交显式选择。

**完成条件**：M1 的每项已签收口径在目标库都与独立参考结果一致，版本和哈希可追溯，越权被拒绝，`llm_calls=0`。M2 按新增语义和各目标方言分别签收。

### M2 日历分组与查找表维度

在服务端目录使用 `format: metric-catalog-v2`，并为启用日期分组的指标配置 `date_column`。请求的 `metric_grain` 支持 `day`、`week`、`month`。列类型为 `DATE`，以业务已归属的日历日期为准。SQLite 数据写入时使用 `YYYY-MM-DD`。周从周一开始，月从每月一日开始，空日期归入空分组。结果先返回 `period_start`，再返回普通维度和指标。SQLite 以 ISO 字符串表示日期；PostgreSQL 和 MySQL 使用原生 `DATE` 结果。

```yaml
format: metric-catalog-v2
version: example-v2
bindings:
  - domain: reporting
    database: orders_db
    lookup:
      fact_table: orders
      fact_key: branch_id
      table: branches
      key: id
      dimensions: {sector: sector_name}
    metrics:
      - name: total_amount
        table: orders
        column: amount
        aggregation: sum
        date_column: booked_on
        dimensions: [sector]
```

跨表维度从一个明确的整数键关联的查找表取得，查询使用 `LEFT JOIN`，无对应项归入空维度。解析用捕获的目标 Schema 检查查找键为单列主键。度量来自同一事实表，日期分组中所选指标共享一个日期列。业务负责人可分别确认主键粒度、缺失归属和聚合口径。SQLite 已用真实执行覆盖周界、月界、空键及重复键拒绝。其他目标库按发布范围实测。REST JSON、SSE 和 MCP 仍用固定 `metric_query` 工作流，验收文件可给用例加 `grain` 并在每行首列填写预期 `period_start`。 [验收命令](#m1-指标结果验收命令)

### M1 指标结果验收命令

业务负责人审核 v1 指标目录，数据负责人从同一受控快照独立计算预期结果。一个验收文件对应一个租户、业务域和已登记的 `db_id`。审核后冻结目录和用例，部署配置为目标库的原生只读账号。用目录原始字节的 SHA-256 小写十六进制值填入 `catalog_sha256`。

以下文件使用合成示例字段。每个用例按请求中的维度顺序和指标顺序提交预期行，保留目标 API 的 JSON 类型和目标库的分组行顺序。例如 MySQL 返回为字符串的 DECIMAL 应以 JSON 字符串填写。每份文件支持 1–32 个用例，每个用例最多 100 行，允许空结果。

```json
{
  "profile": "metric-acceptance-v1",
  "tenant_id": 1,
  "domain": "ecommerce",
  "database": "metric-db",
  "catalog_sha256": "<metrics.yml 的 SHA-256>",
  "cases": [
    {
      "id": "completed-orders-by-region",
      "metrics": ["order_count"],
      "dimensions": ["region"],
      "filters": {"status": "completed"},
      "expected_rows": [["East", 2], ["North", 2]]
    }
  ]
}
```

通过秘密注入在当前进程设置 `DE_METRIC_ACCEPTANCE_TOKEN`，使用目标租户的 operator JWT。部署地址使用 HTTPS，本机 HTTP 使用回环 IP。命令依次检查登录租户、目录发现、旧版本拒绝和 `execute=false`，再通过 JSON、SSE 执行各用例。每个结果必须通过校验、使用固定指标流程且模型调用为零。发生数据截断、列顺序或行结果不符时，验收失败。执行结束后再次核对目录版本与哈希。

```powershell
python -m scripts.run_metric_acceptance --base-url https://review.example.test `
  --catalog data/metric-review/metrics.yml --cases data/metric-review/cases.json `
  --output data/metric-review/report.json
```

部署启用了 MCP Streamable HTTP 时，给同一命令添加 `--mcp-url https://review.example.test/mcp/`，URL 须指向实际的协议端点。该端点使用与 REST 同一租户的 operator JWT，可以是独立进程。命令使用 FastMCP SDK 检查 `list_metrics` 与 `metric_query` 的结构化结果，覆盖初次和末次目录发现、旧版本拒绝、`execute=false` 以及逐用例结果比对。该传输的检查失败时，整份验收失败。stdio 验收和传统 MCP SSE 的认证边界见传输文档。 [MCP 验收](metric-mcp-transports.md)

退出码 `0` 表示全部已启用的检查通过，`1` 表示验收项失败并保存报告，`2` 表示输入或输出错误。报告记录部署地址、已启用的 MCP 端点、租户、目录版本、用例哈希和行结果哈希。报告使用固定错误码，便于在 CI 中判断失败阶段。

## 4. 业务准确率、检索召回与生产容量

已有合成质量报告可以作为评估工具的回归门禁。以下工作需要业务数据、标注与目标运行环境。 [合成基线](synthetic-quality-baseline-2026-11-18.md)

1. **PR Q1，评估入口**。复用现有 SQL contract 和排序计分逻辑，为批准的外部业务数据增加显式数据集输入。当前 `evaluate_acceptance` 的数据清单绑定仓库的 `acceptance_v3` fixture，扩展后保留原默认行为。先用小型脱敏样例验证清单与哈希、dev/holdout 划分、缺预测计分和报告重放。业务负责人可同时准备问题、Schema 快照、人工 gold SQL 或答案、相关性标签和无答案样本，按业务域、语言和复杂度分层。 [评估入口](../../scripts/evaluate_acceptance.py) · [样本加载](../../app/evaluation/acceptance_data.py)
2. **Q2，业务质量**。评测前冻结结果等价规则（排序、重复行、NULL、数值容差）、数据版本、模型与检索配置和通过阈值。用 dev 调整配置，冻结后用独立 holdout 跑正式请求链路。报告分别给出执行成功率、答案准确率、Recall@K 和无答案表现，记录分母、切片、缺失与错误分类及置信区间。按需离线验证响应 `confidence` 的可靠性和误差，达到预设门槛后再单独评审对外字段。
3. **Q3，容量**。平台负责人先冻结目标拓扑、软硬件、数据库与数据规模、模型/embedding 配额、SLO 和费用上限。在具备真实依赖的预生产 HTTP 部署上，以固定请求分布做冷启动、稳态、峰值和恢复测试。覆盖 JSON/SSE、不同租户并发、取消和断流，记录 p50/p95/p99、首事件延迟、有效吞吐、503、超时、资源、token 与费用。复用现有本地工具验证负载样本和统计口径，使用目标环境端到端报告签收。 [本地负载探针](../../scripts/load_test.py) · [规模探针](../../scripts/scale_workers.py)

**完成条件**：Q1 可在本地复现同一报告。Q2 由业务负责人签收冻结的 holdout、实测指标和门槛。Q3 由平台负责人签收各负载档的实测结果、SLO 达标范围及过载后恢复情况。

### Q1 业务数据集与离线重放

在受控目录准备 `sql.json`、`retrieval.json` 和由脱敏数据导出的 `snapshot.sqlite3`。关闭导出连接前完成检查点并将 SQLite 的 journal mode 设为 `DELETE`，发布独立快照文件。每个输入文件上限 16 MiB。字段可参考仓库的 [SQL 标注示例](../../tests/fixtures/acceptance_v3/sql.json) 和 [检索标注示例](../../tests/fixtures/acceptance_v3/retrieval.json)。

- SQL 文件使用 `version: business-acceptance-v1`、`fixtures: ["base"]`。预期结果只填 `expected_rows.base`，每份快照绑定一个 `db_id`。人工标注来源使用 `annotation.source: manual-business-facts`。`counterexamples` 可以为空。
- 检索文件使用 `version: business-retrieval-v1`、`annotation_source: manual-business-facts`。每道题填写相关与难负例文档、各自的标注理由以及反例排序，无答案题填写理由。相关文档须与问题的域和数据库一致。
- 两条评估轨道都需要 `dev` 和 `holdout` 样本。文件内用唯一题目 ID 将标注与预测关联。

在包目录生成原始字节的 SHA-256 清单，交由数据负责人和评估负责人共同冻结。修改任一文件后重新审核并生成清单。

```powershell
$files = @{}
foreach ($name in 'sql.json', 'retrieval.json', 'snapshot.sqlite3') {
    $files[$name] = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLowerInvariant()
}
@{ profile = 'business-acceptance-v1'; normalization = 'raw bytes'; files = $files } |
    ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath manifest.json
```

在仓库根目录运行。以下路径代表获准的样本包和已保存的预测，每个产物使用独立路径。

```powershell
python -m scripts.evaluate_acceptance --dataset-manifest data/business-eval/manifest.json `
  --split holdout --sql-predictions data/business-eval/predictions.json `
  --rankings data/business-eval/rankings.json --output data/business-eval/report.json
```

SQL 预测沿用 `id/sql/finish_reason` 评分契约，检索排名使用 `id/retrieved`。省略 `--sql-predictions` 时，所有选中 SQL 样本计为预测缺失。省略 `--rankings` 时，生成本地 BM25 词法基线。报告记录清单、输入和预测哈希。固定数据、预测与环境后可重放报告。

### Q2 留出集质量报告与门槛

`business_quality` 报告展示 SQL 执行成功率、SQLite 快照上的答案准确率、可回答问题的宏平均 Recall@K，以及无答案问题返回空列表的正确率。每项指标都给出分母、点估计与 95% 区间。二元比例用 Wilson 区间，宏平均 Recall@K 用 Hoeffding 界。区间依赖题目近似独立，零样本分组只记录分母。缺失、失败和无效预测保留在各自分母中。

报告按 dev/holdout 独立汇总，并在各 split 内按域、数据库和标签分组。标注时可使用 `lang:zh`、`complexity:join` 一类标签。同一题可以计入多个标签。阈值判定使用 holdout 整体的点估计和最小样本量。

在冻结 holdout 和 K 后，由业务负责人审核并保存 `thresholds.json`。以下是文件形状和示例数值，正式数值按该业务域签收。

```json
{
  "version": "business-quality-thresholds-v1",
  "manifest_sha256": "<manifest.json 的原始字节 SHA-256 小写十六进制>",
  "k": 3,
  "metrics": {
    "sql_execution_success_rate": {"min_score": 0.95, "min_count": 100},
    "sql_answer_accuracy": {"min_score": 0.85, "min_count": 100},
    "recall_at_k": {"min_score": 0.80, "min_count": 80},
    "no_answer_accuracy": {"min_score": 0.90, "min_count": 20}
  }
}
```

用正式请求链路留存的 SQL 预测和检索排名运行门槛判定。报告会记录实际使用的阈值文件哈希。

```powershell
python -m scripts.evaluate_acceptance --dataset-manifest data/business-eval/manifest.json `
  --split holdout --k 3 --sql-predictions data/business-eval/predictions.json `
  --rankings data/business-eval/rankings.json --thresholds data/business-eval/thresholds.json `
  --output data/business-eval/report.json
```

- 退出码 `0` 表示四项门槛均通过，或者已生成未启用门槛的评分报告。
- 退出码 `1` 表示至少一项低于分数或样本量门槛。报告仍保存，并标明各项判定。
- 退出码 `2` 表示输入或运行出错。保留已有报告。清单哈希、K、split 和两个预测输入都在门槛验收范围内。

#### Q2 查询接口结果采集

将同一份冻结 SQLite 快照注册为验收部署的查询目标，使用该租户的 operator JWT 设置 `DE_PLAN_CAPTURE_TOKEN`。先冻结部署与模型配置记录。`--baseline-only` 逐题发送一次固定 `request_query`，每次使用新会话。`--max-requests` 为已批准的查询请求数上限，至少等于所选 split 的 SQL 题数。采集先检查快照、身份和数据源，遇到不确定的传输错误时停止并保存已有记录。 [采集边界](#自动采集两组请求)

```powershell
$configHash = (Get-FileHash -LiteralPath data/business-eval/configuration.json -Algorithm SHA256).Hash.ToLowerInvariant()
python -m scripts.capture_query_plans --base-url https://review.example.test --tenant-id 1 `
  --dataset-manifest data/business-eval/manifest.json --configuration-sha256 $configHash `
  --split holdout --baseline-only --max-requests 100 --output data/business-eval/query-capture.json

python -m scripts.evaluate_acceptance --dataset-manifest data/business-eval/manifest.json `
  --split holdout --k 3 --query-capture data/business-eval/query-capture.json `
  --rankings data/business-eval/document-rankings.json --thresholds data/business-eval/thresholds.json `
  --output data/business-eval/report.json
```

采集模式下，执行成功要求 API 返回与主候选一致且未截断的执行结果。答案正确还要求主候选 SQL 在冻结快照上通过答案契约，且 API 返回的列和行符合相同契约。错行、缺响应与未发出的题目都保留在分母中。中止的采集可以评分，其门槛判定会记录 `capture_complete=false` 并退出 `1`。采集文件含完整响应，应保存在受控目录。评分报告保存采集配置与内容哈希、题目状态及汇总。

#### Q2 文档混合检索排名采集

用冻结的 `retrieval.json` 文档语料建临时索引。每次运行按 `(db_id, domain)` 分开索引，复用应用的文档分块、LanceDB 向量检索、BM25 和 RRF 合并。`--chunk-top-k` 指定保留的分块命中数，须大于等于 `--k`。按父文档首次命中去重，再保留最多 K 个文档。若前若干命中集中于同一文档，排名可能短于 K。无语料的作用域返回空排名，其余问题按实际命中评分。

```powershell
python -m scripts.capture_document_rankings --dataset-manifest data/business-eval/manifest.json `
  --split holdout --k 3 --chunk-top-k 10 --output data/business-eval/document-rankings.json

python -m scripts.evaluate_acceptance --dataset-manifest data/business-eval/manifest.json `
  --split holdout --k 3 --sql-predictions data/business-eval/predictions.json `
  --rankings data/business-eval/document-rankings.json --output data/business-eval/report.json
```

默认使用确定性 mock 向量验证流程。语义评估可添加 `--ollama-model embeddinggemma --ollama-dimension 768`，使用本机 `127.0.0.1:11434` 上已加载的模型。采集文件记录清单哈希、split、K、分块命中数和 embedding 模型配置。评分时核对清单、split、K 和样本覆盖，并把采集配置写入报告。采集成功退出 `0`，输入或运行失败退出 `2` 并保留旧文件。正式验收仍需审核的语料和留出集，以及冻结的目标检索配置。该入口衡量文档检索召回，Schema 链接及完整请求链路在各自的运行环境验收。

### Q3 预生产 HTTP 容量探针

平台负责人先记录目标发布代、单机或多 worker 拓扑、数据规模、模型和检索配置、费用预算与 SLO。随后冻结一份代表业务请求分布的清单。清单按顺序执行 1–8 档负载，每档提交最多 256 个请求、32 个在途请求。客户端按清单顺序循环选题，一个请求结束后由下一请求补位。

下面以合成订单展示配置形状，阈值由平台负责人按目标环境签收。每个阶段至少覆盖清单中的全部请求模板。若该阶段只验收完成延迟，可将 `max_sse_first_event_p95_ms` 设为 `null`。

```json
{
  "profile": "capacity-http-v1",
  "tenant_id": 1,
  "workload": [
    {"id": "orders-json", "transport": "json", "database": "reporting",
     "domain": "ecommerce", "nl_text": "列出最近十件已完成订单的编号"},
    {"id": "orders-sse", "transport": "sse", "database": "reporting",
     "domain": "ecommerce", "nl_text": "列出最近十件已完成订单的编号"}
  ],
  "phases": [
    {"id": "steady", "requests": 40, "concurrency": 2, "timeout_seconds": 20,
     "slo": {"min_success_rate": 0.95, "max_success_p95_ms": 5000,
             "max_sse_first_event_p95_ms": 1000}},
    {"id": "peak", "requests": 80, "concurrency": 8, "timeout_seconds": 20,
     "slo": {"min_success_rate": 0.90, "max_success_p95_ms": 8000,
             "max_sse_first_event_p95_ms": 1500}},
    {"id": "recovery", "requests": 40, "concurrency": 2, "timeout_seconds": 20,
     "slo": {"min_success_rate": 0.95, "max_success_p95_ms": 5000,
             "max_sse_first_event_p95_ms": 1000}}
  ]
}
```

单租户 v1 通过秘密注入获取 `DE_CAPACITY_ACCEPTANCE_TOKEN`。命令先核对租户和操作权限，再向已配置的数据源发起固定 `request_query`，经 Harness 完成校验和只读执行。每个档位都给出实际请求数、有效吞吐、p50/p95/p99、HTTP 状态、超时、SSE 断流和工作流失败。失败请求留在分母里，SSE 首事件用完整数据帧的到达时间，并单独列出缺失数。SLO 检查成功请求的 p95、总成功率及配置的 SSE 首事件 p95。

```powershell
python -m scripts.run_capacity_acceptance --base-url https://review.example.test `
  --manifest data/capacity-review/manifest.json --output data/capacity-review/report.json
```

退出码 `0` 表示全部门槛通过，`1` 表示身份核对或至少一个阶段未通过并保存报告，`2` 表示输入或输出错误。某档未达标时仍继续测恢复档。报告把已观测 token 用量与覆盖请求数一起保存。实际费用、服务端 CPU/RSS 和数据库负载由目标环境的账单与监控提供。正式签收还需在目标环境复跑，并记录冷启动和主动取消后的服务端资源回收。

#### 阶段容量与模型用量门槛

单租户和多租户清单都可在每个阶段的 `slo` 中加以下字段。按目标环境冻结门槛，省略或设为 `null` 可关闭对应检查。

```json
{
  "max_success_p99_ms": 8000,
  "min_successful_rps": 3,
  "max_total_llm_tokens": 200000
}
```

- `max_success_p99_ms` 使用成功请求的最近秩 P99。该阶段没有成功请求时判失败。
- `min_successful_rps` 使用成功请求数除以该阶段的墙钟时间，包含排队和处理失败请求消耗的时间。
- `max_total_llm_tokens` 使用该阶段全部请求已核对的 prompt 与 completion token 总和。超时、限流和断流等缺少完整用量的请求会降低覆盖数。只有完整快照明确记录零模型调用时，空调用明细才计为零 token。证据不全时总量显示为 `null`，该门槛判失败。已知小计与 `requests_with_accounted_tokens` 仍保留在 `llm_usage`。

报告把启用的门槛分别写入 `slo_checks`。token 门槛在阶段完成后判定，发起请求的数量仍由清单控制。

#### Q3 资源和账单联合验收

压测前，按清单原始字节的 SHA-256 冻结资源上限与总费用预算。以下数值为示例，资源上限适用于每个阶段，金额使用 USD 十进制字符串。

```json
{
  "profile": "capacity-review-limits-v1",
  "manifest_sha256": "<负载清单 SHA-256>",
  "max_cpu_percent": 80,
  "max_rss_mib": 2048,
  "max_db_active_connections": 32,
  "max_total_cost_usd": "5.00"
}
```

压测报告每个阶段包含 `started_at_utc` 和 `finished_at_utc`。平台负责人按这些时间窗核对各阶段的应用主机 CPU 峰值（百分比，取最忙的主机）、worker 同一时点进程 RSS 之和的峰值（MiB）、指定数据库在同一时点的活跃连接总数峰值，以及归属于该阶段的实际费用。复核时记录覆盖的主机、进程、数据库和费用口径。`source_sha256` 记录经审核的监控与账单证据包哈希，`http_report_sha256` 记录本轮 HTTP 报告的原始字节哈希。未确认的指标填 `null`，已确认零费用填 `"0"`，每个阶段至多一条记录。

```json
{
  "profile": "capacity-deployment-evidence-v1",
  "http_report_sha256": "<HTTP 容量报告 SHA-256>",
  "source_sha256": "<经审核的证据包 SHA-256>",
  "phases": [
    {"id": "steady", "peak_cpu_percent": 63.1, "peak_rss_mib": 1024,
     "peak_db_active_connections": 24, "actual_cost_usd": "0.125"}
  ]
}
```

```powershell
python -m scripts.review_capacity_acceptance --manifest data/capacity-review/manifest.json `
  --http-report data/capacity-review/report.json --limits data/capacity-review/limits.json `
  --evidence data/capacity-review/evidence.json --output data/capacity-review/final-review.json
```

HTTP 门槛、各阶段资源门槛及全程费用门槛均通过时退出 `0`。缺失、超限或 HTTP 门槛失败时退出 `1`，照常保存报告。部分费用仅计入已知小计，报告保留费用覆盖的阶段数。输入无效或证据与报告不匹配时退出 `2`，保留旧报告。可省略 `--evidence` 生成缺证据的判定报告。此命令离线工作，监控和账单采集与复核由目标环境负责人完成。

#### 多租户并发

使用同一命令传入 `capacity-http-v2` 清单，将顶层的 `tenant_id` 改为包含 2–16 个唯一租户 ID 的 `tenant_ids`，并在每条 workload 中指定所属的 `tenant_id`。例如将租户 2 的 JSON 请求和租户 3 的 SSE 请求交替列在清单中。每个声明的租户至少有一条请求模板，使用本租户的 `db_id`。命令从 `DE_CAPACITY_ACCEPTANCE_TOKEN_2`、`DE_CAPACITY_ACCEPTANCE_TOKEN_3` 等环境变量获取各自的 operator JWT。

任一 JWT 的实际租户或权限不符合声明时，整次试验在发起查询前结束。每档的 `concurrency` 是所有租户共享的在途请求上限。报告的 `by_tenant` 记录各租户的分母、成功率、延迟、失败和用量。可在阶段的 `slo` 中加入 `"min_success_rate_by_tenant": {"2": 0.95, "3": 0.90}`。配置了租户门槛的阶段需要同时满足整体与各指定租户门槛。

#### SSE 主动断流与服务端回收

使用单机不可变双 worker、一个查询槽和一个模型槽做可控回归。测试先预热两个 HTTP 入口，再让一条 SSE 查询进入阻塞的模拟模型调用。确认 MySQL 中查询锁、会话锁和模型锁已被占用，且两个 worker 均拒绝另一条查询。随后客户端主动关闭仍在运行的 SSE 连接。

断流后须在 8 秒内观察到模型任务退出、三把锁全部释放以及对应 query ID 的 `cancelled` 审计记录。被取消的请求不提交会话轮次。另一 worker 必须能复用该会话，原 worker 也必须能完成新查询。测试后的报告包含实测回收时间，预生产复验沿用证据链并按目标限额和回收 SLO 判定。

```powershell
python -m scripts.run_real_db_tests --filter sse_disconnect_cancels_model `
  --output data/sse-disconnect-20261120/verified.json
```

## 5. 多 Agent 编排与子 Agent

**先交付 A1 的单场景试点**。建议以“复杂查询的候选复核”为起点，以现有 `request_query` 或 `reflection` 作对照。现有子 Agent REST 已支持限定域、数据源和身份的 NL 查询，调用固定的 `request_query`。启动时注册的编排器仍属实验组件，非默认租户路由白名单当前也没有子 Agent 入口。 [子 Agent REST 边界](subagent-rest-boundary.md) · [租户路由准入](../../app/auth/access.py)

1. 用 Q2 的 dev 样本确认主要失败场景，先评估现有 `reflection` 对该场景的效果。若仍有可测缺口，再定义生成候选和复核意见的结构化输入输出、执行资格、失败行为与预算上限。
2. 以显式 opt-in 的固定 YAML 计划接入 Runner。角色输出仅作为上下文数据。候选复核后由固定校验及执行节点处理，沿用请求级模型预算、权限、只读策略、行数、超时、取消和审计。复核不可用时返回可追踪的失败或未执行结果，保持单次请求预算和追踪对应关系。
3. 用隔离目标验证端到端 trace、预算耗尽、复核失败、取消、并发和 `execute=false`。在同一 holdout 上对比正确率、p95 延迟、LLM 调用和成本，达到约定改善幅度及开销上限后启用试点。
4. **A2 按渠道逐个开放**。若上线范围含非默认租户，先把注册、查找、授权和追踪按认证租户隔离，验证同名子 Agent、同名数据源和普通账号的正反例，再开放路由。子 Agent 指标动作依赖 M1 的显式目录与权限校验。MCP 和工作台入口分别在相同作用域与动作契约通过后接入。

**完成条件**：trace 与固定计划一致，每个角色调用计入同一预算，只有授权且通过校验的候选能进入执行。质量提升和成本由对照评测证明。达不到试点门槛时保留实验状态。

### 候选复核预览与子 Agent 租户入口

在隔离预览部署设置 `CANDIDATE_REVIEW_ENABLED=true`，提交 `plan_id=candidate_review`、显式 `database` 和 `num_candidates=1`。生成与复核共用一个请求预算。复核只接收结构化通过或拒绝信息，拒绝、格式错误、预算不足或超时时停止执行。通过后由原校验与只读执行节点处理，`execute=false` 生成并校验，执行保持关闭。对照采集命令加 `--trial-plan candidate_review`，冻结的试点门槛文件增加 `"trial_plan": "candidate_review"`，账本继续按查询 ID 归集。正式试点需先验证业务改善。 [对照与门槛命令](#自动采集两组请求)

子 Agent 单 worker 通过 `tenant_id`、`workflow_enabled: true` 和显式 `db_id` 在受信 YAML 中注册。不可变共享 worker 通过 `--subagent-catalog` 封存注册目录。REST、已认证 MCP HTTP 与工作台自然语言入口复用租户作用域和 operator 权限。配置 `metric_query` 能力的子 Agent 可显式调用目录指标。 [授权与配置](subagent-rest-boundary.md)

### A1 现有计划的成对对照

先在同一份已冻结的业务 SQLite 快照上比较 `request_query` 和 `reflection`。两组各用相同题目、查询数据源及模型和检索配置，逐题从新会话经 `POST /api/v1/query` 发起。分别显式指定 `plan_id`，同时设置 `auto_plan=false`、`num_candidates=1` 和 `execute=true`。采集端保存完整最终 `QueryResponse`，另记录请求发出到读完响应的耗时。`dev` 用于定位问题，锁定配置后再生成独立 `holdout` 的两份采集文件。

每份采集文件使用以下结构。两组须填写相同的业务清单哈希和由部署方记录的基线配置哈希。采集方在运行前冻结共用的模型、检索、部署版本和快照配置，计算配置哈希。计划名用各自的 `plan_id` 字段记录。每个题目只保留预先约定的第一次尝试。若连最终响应也未取得，填 `response: null`，并在有计时时保留 `latency_ms`。

```json
{
  "profile": "query-plan-capture-v1",
  "manifest_sha256": "<业务清单原始字节 SHA-256>",
  "configuration_sha256": "<配置记录 SHA-256>",
  "plan_id": "request_query",
  "split": "holdout",
  "records": [
    {"id": "invoice-17", "latency_ms": 2000, "response": null}
  ]
}
```

```powershell
python -m scripts.compare_query_plans --dataset-manifest data/business-eval/manifest.json `
  --baseline data/business-eval/request-query.json --reflection data/business-eval/reflection.json `
  --split holdout --output data/business-eval/plan-comparison.json
```

命令核对题目、计划、主候选和 SQLite 数据源，再用冻结快照验证捕获行和主候选重放结果。按题报告两组均正确、改进、退化或均不正确，缺失保留在正确率分母中。p95 和 LLM 调用量分别只比较两组同时有该项观测的题目，并记录配对数。退出码 `0` 表示离线报告已生成，`2` 表示输入或输出失败并保留已有报告。正式试点用已审核的用量和账单核对费用，由业务负责人根据对照结果决定是否继续候选复核试点。 [用量估算入口](../../scripts/estimate_usage_cost.py)

#### 自动采集两组请求

在预生产服务注册同一份 SQLite 快照，保留冻结的部署配置记录。样本 ID 使用预测导出契约中的 128 字符上限。使用归属正确的 operator JWT，通过秘密注入设置 `DE_PLAN_CAPTURE_TOKEN`。采集命令先校验快照、标注和请求上限，随后核对租户和已登记数据源，再对所选 split 的每个题目顺序发送两次请求。两组的先后顺序逐题交替，每次请求使用新会话。

启动前批准 `--max-requests` 的查询 POST 数量，至少为题目数的两倍。以下命令的请求上限 80 对应最多 40 道题。模型和检索调用仍按目标服务的配额及费用预算管理。

```powershell
$configHash = (Get-FileHash -LiteralPath data/business-eval/configuration.json -Algorithm SHA256).Hash.ToLowerInvariant()
python -m scripts.capture_query_plans --base-url https://review.example.test --tenant-id 1 `
  --dataset-manifest data/business-eval/manifest.json --configuration-sha256 $configHash `
  --split holdout --max-requests 80 --output data/business-eval/paired-capture.json

python -m scripts.compare_query_plans --dataset-manifest data/business-eval/manifest.json `
  --capture data/business-eval/paired-capture.json --split holdout `
  --output data/business-eval/plan-comparison.json
```

采集文件包含完整请求结果，保存在业务方的受控目录。HTTP 503 等明确失败会留下空响应和失败计数。遇到客户端超时、传输中断、身份或响应绑定不符时，停止发新查询并保存已采集的部分。命令不重试请求。退出码 `0` 表示已尝试全部配对，`1` 表示提早停止并保存报告，`2` 表示输入或输出失败。离线对照保留所有计划题目作分母，并记录采集状态。

#### 试点门槛与费用证据

采集前由业务和平台负责人冻结 holdout、共用配置以及以下门槛。准确率差值用比例表示，例如 `0.02` 对应提升 2 个百分点。延迟、调用量和费用差值均按 `reflection` 减 `request_query` 计算。以下是示例门槛。

```json
{
  "profile": "query-plan-pilot-gate-v1",
  "manifest_sha256": "<业务清单原始字节 SHA-256>",
  "configuration_sha256": "<配置记录 SHA-256>",
  "limits": {
    "min_cases": 100,
    "min_accuracy_delta": 0.02,
    "max_regressed_cases": 2,
    "max_p95_delta_ms": 500,
    "max_llm_calls_delta": 50,
    "max_model_cost_delta_usd": "2.50"
  }
}
```

费用由运营方对账后按查询 ID 登记，覆盖两组全部请求的模型费用。已确认零费用的请求填 `"0"`。费用未知的请求省略该条记录，报告会显示覆盖数与已知小计。金额以 USD 十进制字符串填写。账本中的 `paired_capture_sha256` 可从同一采集文件生成的普通对照报告取得，`evidence_sha256` 填已审核的费用明细哈希。命令验证账本与采集文件的绑定、请求归属、重复和覆盖率。对账及签收由运营方完成。

```json
{
  "profile": "query-plan-billing-v1",
  "paired_capture_sha256": "<普通对照报告中的 paired_capture_sha256>",
  "currency": "USD",
  "evidence_sha256": "<已审核费用明细 SHA-256>",
  "entries": [
    {"query_id": "q_123456789abc", "cost_usd": "0.012"}
  ]
}
```

```powershell
python -m scripts.compare_query_plans --dataset-manifest data/business-eval/manifest.json `
  --capture data/business-eval/paired-capture.json --split holdout `
  --thresholds data/business-eval/pilot-limits.json --billing data/business-eval/billing.json `
  --output data/business-eval/pilot-report.json
```

门槛要求完整采集、完整时延和调用量配对，以及逐请求费用全覆盖。有任一门槛未通过时退出 `1`，并保存逐项判定；输入绑定错误时退出 `2`，已有报告保留。全部门槛通过时退出 `0`，报告供业务负责人签收。

## 实施顺序与验收记录

1. 首批并行推进 **T1** 和 **Q1**，以 **S1** 的既有验收说明同步准备 Snowflake 环境。业务方同期选定 **M1** 试点目录和 **Q2** 标注范围。
2. 条件齐备后按目标环境分别验收 **S1、M1、Q2**。若 M1 选定 PostgreSQL 不可变 worker 部署，把 T1 作为 M1 的环境门禁。**Q3** 在目标拓扑和 SLO 冻结后独立推进。
3. 以 **Q2** 的基线和已签收业务需求决定 **A1、A2、M2** 及更广租户拓扑的排期。各交付独立记录代码、入口、测试和外部验收证据。

每个 PR 先跑相关回归，再跑 `python -m pytest -m 'not real_database'`、`ruff check .` 和仓库核心 mypy 门禁。涉及工作台时补前端契约与构建检查。实库报告记录测试通过和跳过数量、提交、依赖版本和覆盖拓扑。质量及容量报告同时记录数据集哈希与门槛，签收后更新支持矩阵和 README 状态。 [仓库门禁](../../AGENTS.md) · [支持矩阵](../support-matrix.md)

## 首批实施记录

- **T1 完成**。确认 PostgreSQL 16.15 和 MySQL 8.4.13 查询目标均通过双租户双 worker 场景。首轮 PostgreSQL 指标测试发现 SQLGlot 方言名映射缺失，共享查询服务改为使用已有方言配置。修复后连同旧版 worker 和 MCP 实库回归共通过 9 项。一次性容器已清理，JUnit 与运行报告保存在 `data/postgres-immutable-20261120/`。 [实库复验命令](immutable-workers.md#双租户-postgresql-验收)
- **Q1 完成**。可导入 SHA-256 冻结的业务数据集，验证 dev/holdout 口径并离线评分。合成样本验证了重放一致性、缺失预测计入分母、输入变更后保留已有报告，以及禁止用输出覆盖数据集。全量本地回归通过 4,841 项、跳过 1 项。 [业务评估测试](../../tests/test_evaluation/test_business_acceptance.py)

## 第二批实施记录

- **Q2 评分与门槛已实现**。合成样本验证了分组、分母、置信区间、执行成功但答案错误、门槛与清单和 K 的绑定，以及未达标报告的保留。正式验收需要业务 holdout、已审核阈值以及在冻结的模型与检索配置下采集的预测和排名。 [Q2 复跑命令](#q2-留出集质量报告与门槛)
- **M1 与 S1 待外部输入**。M1 需要已签收的单表指标定义、目标库只读连接和独立预期结果。S1 需要专用 Snowflake 环境、测试数据与费用额度。

## 第三批实施记录

- **M1 的 REST 验收命令已实现**。在临时 SQLite 和回环 HTTP 服务上，用独立参考 SQL 验证 JSON 与 SSE 的分组、筛选和聚合结果，同时验证同版本目录漂移、旧版本拒绝、执行关闭和行截断。指标相关回归通过 121 项，JUnit 保存在 `data/metric-acceptance-20261120/metrics.junit.xml`。 [验收命令与输入格式](#m1-指标结果验收命令)
- 正式 M1 使用已审核的目录和预期结果在目标库复跑。 [MCP 传输验收](metric-mcp-transports.md)

## 第四批实施记录

- **M1 的 MCP Streamable HTTP 验收已接入**。在真实回环 FastMCP 服务上，用同一份合成 SQLite 参考结果验证 JSON 和事件流两种协议响应。回归覆盖旧版本拒绝、只校验、列与行结果、端点不可用、失败退出码和 JWT 不写入报告。指标相关回归通过 125 项。正式验收仍需审核后的目录、参考结果和可访问的目标部署。 [复跑命令](#m1-指标结果验收命令)

## 第五批实施记录

- **Q3 的单租户 HTTP 容量探针已实现**。在实际回环 HTTP 监听、SQLite 和模拟模型下，设服务端只有一个查询槽。预热后稳态与恢复档均成功 4/4。峰值档成功 1/4，其余分别记录为 1 个 HTTP 503 和 2 个 SSE 容量拒绝。定向测试也覆盖客户端超时、SSE 未结束与租户不匹配。当前组合回归 43 项通过，JUnit 位于 `data/capacity-acceptance-20261120/verified.junit.xml`。正式 Q3 仍需目标环境的负载、服务端资源和实际费用证据。 [复跑命令](#q3-预生产-http-容量探针)

## 第六批实施记录

- **Q3 已支持多租户并发**。本机 32 项回归通过，验证总成功率达标但单个租户未达标时判定失败，以及令牌租户不符时提前停止。MySQL 8.4.13 与 PostgreSQL 16.15 查询目标的双租户实库用例均通过。每个实库用例在双 worker 固定发布的一个 HTTP 入口运行混合负载，恢复档两个租户均通过。模型响应使用脚本化传输，测试容器已清理。验收报告位于 `data/capacity-multitenant-20261120/verified.json`。 [多租户配置](#多租户并发)

## 第七批实施记录

- **Q3 主动断流回收已通过实库回归**。在 MySQL 8.4.13 和两个本机不可变 worker 上，客户端关闭仍在阻塞模型调用中的 SSE。该次从开始关闭到确认模型退出、三把锁释放并读到取消审计耗时 62 毫秒。被取消的请求没有提交会话轮次，另一 worker 成功复用原会话，原 worker 成功执行新查询。3 项实库相关用例和 82 项本地回归通过，测试容器已清理。回收时间记录在 `data/sse-disconnect-20261120/verified.junit.xml`。正式签收仍需经目标入口复测断流，并结合真实模型、服务端资源和容量 SLO 验收。 [SSE 断流复跑命令](#sse-主动断流与服务端回收)

## 第八批实施记录

- **A1 的现有计划成对对照入口已实现**。用本地 FastAPI 和真实 SQLite 采集两组响应，模型采用脚本化回包。两道合成题中，各有一道改进和退化，`reflection` 多一次模型调用。回归覆盖实际结果与正确重放不一致、缺失样本保留、数据源越界拒绝以及清单、配置和 split 不符时保留上一份报告。完整评估套件通过 298 项，计划执行定向组合通过 12 项，JUnit 位于 `data/plan-comparison-20261120/`。正式试点仍需独立业务留出集、两组真实采集和费用证据。 [对照命令](#a1-现有计划的成对对照)

## 第九批实施记录

- **A1 成对采集与对照已贯通**。本机 FastAPI 和真实 SQLite 用一个冻结留出集交替执行两条固定计划，四次请求使用四个独立会话。单份采集文件可直接重放成对比较。另验证请求上限不足、标注与快照不一致、租户不符及超时停发。采集和评估组合通过 158 项，JUnit 位于 `data/plan-capture-20261120/verified.junit.xml`。本次使用脚本化模型和合成标注，正式试点仍需在获准环境采集真实响应和费用证据。 [采集命令](#自动采集两组请求)

## 第十批实施记录

- **A1 的试点判定与费用账本校验已实现**。成对对照报告支持冻结留出集的样本量、准确率差、退化题数、P95 延迟差、模型调用量差与模型费用差门槛。本地合成回归覆盖十进制费用临界值、采集中断、证据缺失、账本与采集文件不匹配，以及无效输入时保留上一份报告。33 项相关测试通过，JUnit 位于 `data/plan-pilot-20261120/final.junit.xml`。正式判定需要业务方冻结门槛，并在获准环境采集响应和对账。 [门槛及费用证据格式](#试点门槛与费用证据)

## 第十一批实施记录

- **Q2 的文档混合检索排名采集已接入离线评分**。用临时 LanceDB、SQLite FTS5 和确定性 mock 向量复跑合成语料，验证跨库跨域隔离、分块去重、空作用域、重复运行一致，以及清单、split 或 K 不符时保留旧评分。371 项相关测试通过，JUnit 位于 `data/document-rankings-20261120/verified.junit.xml`。语义召回仍需用经审核的留出集和冻结的实际 embedding 配置验收。 [采集和评分命令](#q2-文档混合检索排名采集)

## 第十二批实施记录

- **Q2 的单计划查询结果采集与联合评分已接通**。用回环 HTTP、真实 SQLite、脚本化模型响应与 mock 向量检索验证完整链路。覆盖服务错行、截断、缺响应、中止，以及清单或请求次数不符时保留旧报告。319 项相关测试通过，JUnit 位于 `data/query-quality-20261120/verified.junit.xml`。正式 Q2 仍需在获准部署中使用冻结的业务留出集、模型与检索配置复跑。 [采集和评分命令](#q2-查询接口结果采集)

## 第十三批实施记录

- **Q3 已支持阶段 P99、有效吞吐和模型 token 门槛**。本地合成回归覆盖单租户和多租户清单、token 上限边界、延迟和吞吐不达标，以及空用量有调用记录时拒绝通过。103 项相关测试通过，JUnit 位于 `data/capacity-slo-20261121/verified.junit.xml`。正式 Q3 仍需目标环境的负载复跑、服务端资源观测和账单费用证据。 [阶段门槛配置](#阶段容量与模型用量门槛)

## 第十四批实施记录

- **Q3 的资源和账单联合验收已接通**。HTTP 探针记录阶段 UTC 时间窗，离线验收核对同一运行的监控摘要、实际费用和冻结上限。本地合成回归验证金额临界值、缺证据、资源超限、HTTP 失败和报告绑定。63 项相关测试通过，JUnit 位于 `data/capacity-review-20261121/verified.junit.xml`。正式签收仍需目标部署的监控和账单。 [输入格式和复跑命令](#q3-资源和账单联合验收)

## 集中实施与正式验收状态

最终本地全量回归通过 4,892 项，跳过 1 项，170 项实库测试按本地命令排除。候选复核与采集最终定向回归通过 33 项。Ruff、核心 mypy、前端契约与可重复构建检查、四语言 README 链接均通过。报告保存在 `data/roadmap-closure/`。

- **T1 多租户隔离**。单机双租户双 worker 已有 MySQL、PostgreSQL 实库证据。跨主机和在线发布以选定的目标拓扑、版本切换和恢复要求为前提。 [实库记录](#首批实施记录)
- **S1 Snowflake**。本轮 51 项本地策略测试通过，4 项实库测试因专用 fixture 和费用授权缺失而跳过。接入专用只读角色、warehouse 与受控测试数据后执行实库套件。 [实库验收](snowflake-acceptance.md)
- **M1/M2 指标**。既有单表指标验收工具覆盖 REST JSON、SSE 和 MCP HTTP。v2 支持 DATE 的日、周、月分组与整数主键查找维度。真实 SQLite 覆盖日期边界、空关联和重复键拒绝，MySQL 与 PostgreSQL 的双 worker 用例实际执行了周粒度和跨表聚合。正式验收需要已签收的业务目录、独立预期结果和实际目标库。 [指标 v2](#m2-日历分组与查找表维度)
- **Q1/Q2/Q3 业务基线与容量**。冻结数据、采集、评分、阶段 SLO 和监控与费用复核已有入口。正式 Q2 需要业务留出集、真实模型和检索配置。正式 Q3 需要目标负载、预生产部署及该次运行的资源和账单。 [采集和验收](#4-业务准确率检索召回与生产容量)
- **A1/A2 多 Agent**。固定候选复核与成对对照已接通，预览需显式启用。租户子 Agent 接入 REST、MCP HTTP 与工作台自然语言查询，支持不可变共享 worker 的注册目录封存。正式试点需用冻结业务样本与费用证据证明收益。 [启用与验收](#候选复核预览与子-agent-租户入口)

共享 worker 收尾新增 124 项本地回归和 5 项实库组合测试。后者覆盖 MySQL、PostgreSQL 的同名子 Agent 隔离、独立 MCP HTTP 调用、同大小同时间戳的目录漂移、指标 v2 周分组与跨表聚合，以及旧发布包的会话恢复和维护禁写。测试使用合成数据与脚本化模型，所创建的数据库容器已清理。报告见 `data/sealed-subagents/combined.json`。 [共享 worker 的子 Agent 目录](immutable-workers.md#封存共享-worker-的子-agent-目录)
