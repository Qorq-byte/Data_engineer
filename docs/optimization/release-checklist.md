# 受限试用发布检查单与支持范围冻结

2026-11-05更新：纳入阶段55–60证据，适用于**私有单实例试用**。本文冻结"能承诺什么、不能承诺什么"，以及部署、升级、回退、备份的可执行步骤。证据与实测数字见[阶段48报告](stage-48.md)，原始记录见[stage-48-clean-room.json](results/stage-48-clean-room.json)。

**准备发布不等于已经发布。** 扩大访问面、接入真实业务库、对外宣称准确率或SLO，都需要届时明确的指令和本文列出的缺失证据。

## 1. 冻结的支持范围

### 1.1 部署形态

| 项 | 冻结内容 | 依据 |
|---|---|---|
| 进程模型 | **单进程单 worker**，禁用 `--workers`、gunicorn 多 worker、多副本共享同一 `data/` | 连接、Schema快照、会话、RAG配置与索引gate均为进程内状态；无跨进程广播 |
| 并发入口 | `uvicorn.Config(limit_concurrency=64)`，`reload=False` | [main.py](../../main.py) |
| 运行时 | CPython 3.12（干净环境实测 3.12.13） | 本轮全部验收 |
| 前端 | 必须部署前构建 `frontend_dist/`；只跑 API 可不构建 | [frontend-build.md](frontend-build.md) |
| 反向代理 | 可选，但须自行配置 `CORS_ORIGINS`；本机默认只允许 8001 | [security.md](security.md) |
| 网络出口 | 非必需。litellm 取远程价格表失败会退回本地副本并打印告警；模型调用本身需要出口 | 干净环境实测 |

不允许的形态：多 worker 共享索引目录、多个副本同时写入同一 LanceDB/SQLite、在线原地重建前端。

### 1.2 数据库

`DatabaseType` 共 11 种（`app/db/connections.py`）。支持程度**不是**同一档：

| 档位 | 类型 | 已实测内容 |
|---|---|---|
| 逐轮真实执行验收 | SQLite、DuckDB | 常驻测试；只读执行、超时、取消、authorizer |
| CI真实双库验收 | PostgreSQL 16、MySQL 8.4 | `tests/real_db`，每次推送执行；用例数随阶段增长，不沿用阶段40的历史数量 |
| 仅方言适配与注册 | Snowflake、StarRocks、BigQuery、Redshift、ClickHouse、Databricks、Trino | `PRESET_DIALECTS` 与连接注册；**无真实连接、无端到端SQL验收** |

试用期只能把前两档当作"已验证"。第三档需要连接真实实例单独验收后才能开放，且必须以数据库原生只读账户运行——应用层只读校验不是权限边界。

### 1.3 Schema 与文档规模

| 维度 | 实测上限 | 冻结建议 |
|---|---|---|
| 表 / 列 / 文档 | 1k（1000 表 / 12000 列 / 1000 文档）与 10k（10000 / 120000 / 10000）两轮均未触发停止线 | 试用按**每库不超过 1000 表**规划 |
| 索引构建 | 1k 约 0.46 s；10k 约 2.1 s（mock embedding，产物 2.06 / 27.54 MB） | 大规模首次建索引安排在维护窗口 |
| 查询编排 | 1k 表 pipeline P50 0.78 s；10k 表 P50 9.8 s 且事件循环滞后 P95 4.4 s | 10k 档不进入交互式试用 |

以上均为**合成 Schema + mock 向量 + 单进程本机**，不含真实模型与真实 embedding，因此是编排容量，不是端到端吞吐或生产 SLO。

### 1.4 资源预算

| 项 | 值 | 说明 |
|---|---|---|
| 常驻内存 | 1k 档 RSS 峰值 433 MB；10k 档 703 MB | 单进程、合成负载 |
| 建议机器 | ≥ 2 vCPU / 2 GB 可用内存，1k 表以内 | 留出前端静态文件与系统开销 |
| 状态体积 | `data/` 实测 585 KB（小库）；10k 档索引产物 68 MB | 随表数增长 |
| 备份上限 | 默认 512 MiB / 20000 文件，越线即中止 | `scripts/state_backup.py` |
| 并发上限 | 64（应用层），超出由 uvicorn 排队 | 与实测并发层级无关，不是压测结论 |

### 1.5 账号与权限

- 私有共享工作台，**不是多租户**。业务接口要求有效 Bearer JWT，且用户 ID 在 `ADMIN_USER_IDS`（JSON 数组，默认 `[1]`）。
- 无角色分级：只有"在白名单内"和"不在白名单内"两种结果（`app/auth/access.py:require_operator` → 403）。
- 注册由 `REGISTRATION_TOKEN` + `X-Registration-Token` 邀请控制；未配置即禁止注册。
- 撤销访问：把 ID 移出白名单即可阻断业务接口；全面撤销既有令牌需轮换 `JWT_SECRET`（LLM 提供商密钥由该密钥派生，轮换后需重新配置，不宣称透明迁移）。
- 不提供用户级资源隔离、写 SQL 审批、UDF 授权。

## 2. 部署前置（缺一即不可启动或不可用）

1. `.env`（不跟踪）中设置至少 32 字符的随机 `JWT_SECRET`。**默认值 `change-me-in-production` 只有 22 字符，在所有环境被拒绝启动**，干净环境实测 `ValueError: JWT_SECRET must be configured with at least 32 characters`。
2. 配置 `REGISTRATION_TOKEN` 用于开户，开户后按实际账户 ID 写入 `ADMIN_USER_IDS`。
3. 配置 `CORS_ORIGINS`（JSON 数组）为实际前端来源。
4. MySQL 用于业务存储与账号；不可达时 `storage`/`auth` 为 `false`、`/health/ready` 返回 503，`/health` 仍 200。这是**降级而非崩溃**，但不能当作可用。
5. 前端：`npm ci --ignore-scripts && npm run build:frontend`，产物与源码同版本一起发布。
6. 端口：默认 8001，`APP_PORT` 覆盖。验证时注意本机代理会拦截 127.0.0.1（用 `curl --noproxy '*'`）。
7. 安装依赖时带快照：`python -m pip install -e . -c constraints.txt`。不带的话会解析到与本轮验收不同的版本（阶段48 已实测到 pydantic/litellm 漂移），详见[依赖快照与升级口径](dependency-lock.md)。

## 3. 发布检查单

部署前：

- [ ] 目标提交的 **MVP checks** 与 **Real database acceptance** 两套 CI 均为 success，记录运行 ID。
- [ ] `JWT_SECRET`（≥32 字符）、`REGISTRATION_TOKEN`、`CORS_ORIGINS`、`ADMIN_USER_IDS` 均已配置且未入库。
- [ ] MySQL 业务库与账号库可连；只读账户用于被查询的源库。
- [ ] `npm ci --ignore-scripts && npm run check:frontend && npm run build:frontend` 成功，保留源码 SHA + 整个 `frontend_dist/`，按 `manifest.json` 核对摘要。
- [ ] `pytest -m 'not real_database'`、`ruff check .`、`mypy app/` 本地或 CI 通过。
- [ ] 记录本次运行时版本（Python / pydantic / litellm / lancedb / sqlglot），与阶段44评测清单的差异已知情。

启动后：

- [ ] `/health` 200。
- [ ] `/health/ready` 按预期：`storage`、`auth`、`rag`、`llm` 全为 true 才算完整可用；`refresh_required_database_count` 为 0。
- [ ] `/`、`/login`、`/workspace` 均 200；缺产物时应是 503 `FRONTEND_NOT_BUILT`，不是空白页。
- [ ] `/assets/` 下任取一文件 200 且摘要与 manifest 一致。
- [ ] 未带令牌访问 `/api/v1/schema/databases` 返回 401；换非白名单账号返回 403。
- [ ] 停服窗口内跑一次 `--action backup`，再用 `--action verify` 得到 `ok: true`。
- [ ] 浏览器主路径（连接→查询→结果/错误→重试→历史，桌面与手机宽度）已核对：`pwsh -NoProfile -File scripts/browser_qa.ps1` 与 `scripts/browser_qa_main_path.ps1` 均以退出码 0 结束。两者需先启动 `python -m scripts.browser_qa_server`；它只绑 127.0.0.1:8018 且用临时密钥，**不是生产服务**。

## 4. 升级与回退

**升级**（实测：`3d6b363` → `dcc2888`）

1. 停服，或准备独立版本目录（禁止在线原地改写正在服务的目录）。
2. 在停服窗口执行 `--action backup --backup-dir <新目录>`。
3. 切源码到新提交，重装依赖（`pip install -e . -c constraints.txt`，随后`pip check`及`python -m scripts.check_dependencies --runtime-only`），按需重建 `frontend_dist/`。
4. 启动，重复"启动后"检查单。
5. 确认新代码能 `verify` 并通过旧版本产生的备份（实测 51 文件全部一致）。

**回退**

1. 停服，把源码与**对应版本的** `frontend_dist/` 一起恢复；不要只回退 HTML 或只回退带哈希脚本。
2. 必要时 `--action restore --backup-dir <备份> --state-root data`；目标已存在必须显式 `--overwrite`。
3. 启动，重复"启动后"检查单。
4. 若索引残留 `index-pending-*.json` 标记，`readiness` 会停在 `degraded`，需重建该库索引后才能恢复。

**保留策略**：`--action prune --backup-root <根> --keep N` 只删除带 `manifest.json` 的目录，保留最新 N 份。该命令只需备份根，不需要 `--backup-dir`（阶段48修复，此前恒退出码 2）。

## 5. 受限试用结论

可以支持：

- 单个团队、白名单内的操作者，在单台机器上以单进程方式使用。
- 面向 SQLite / DuckDB / PostgreSQL / MySQL 的只读取数问答，每库 ≤1000 表。
- 有停服窗口的手工备份与恢复。

暂不支持或不承诺：

- 多租户、按用户隔离资源、对外公开注册。
- 多 worker / 多副本 / 水平扩展。
- 7 种未实测方言的生产可用性。
- 任何业务准确率、延迟、吞吐或可用性 SLO。阶段44的 26/30 是**合成题集契约匹配率**，不是业务准确率。

## 6. 仍然缺失的证据

| 缺口 | 影响 | 补齐方式 |
|---|---|---|
| 当前运行时的业务质量证据 | 阶段63A已在SQLGlot30.29.0/Pydantic2.14.2/LiteLLM1.106.6上完成真实合成基线27/30；业务标注仍缺失 | 接收业务标注后单独验收，后续运行时变化需复测 |
| 阶段49历史依赖告警（不是当前审计结论） | 阶段49当时的 litellm 1.106.6 要求 `filelock<4.0`，filelock 也没有 3.x 回移修复；项目不直接 import filelock | 等上游放开约束；多用户主机上不要把锁目录暴露给不可信本地用户 |
| SQLGlot升级后的真实模型质量 | 阶段62已适配30.29.0并通过新旧152项判定对比及完整离线回归；阶段44真实模型分数仍绑定旧环境 | 阶段63A已按新清单复测，见current-runtime-baselines.md；不迁移旧分数 |
| 无业务标注 | 无法给出任何业务准确率 | 需要真实业务问题与人工标注 |
| 无真实模型/真实 embedding 的容量数据 | 阶段45 是 mock 编排容量 | 需要真实提供商配额与影子流量 |
| 完整部署的MySQL readiness | 阶段57已在新进程、独立MySQL的真实storage/auth下验证成功与失败状态，RAG在探针中关闭，查询库为SQLite | 仍需目标部署环境完整检查，不能把探针当作真实模型连通性 |
| 浏览器主路径只在合成夹具上验证 | 临时 JWT、内存 SQLite、脚本化模型，没接真实库与真实模型 | 在有真实库/模型的环境重跑同一批脚本 |
| 7 种方言无真实连接验收 | 只能按"未验证"对待 | 逐个连接真实实例跑 `tests/real_db` 等价用例 |
| 前端构建依赖审计口径不稳 | 同一 lockfile 在 10-23 报 7 项、10-31 报 0 项，差异来自注册表侧数据 | 只能按"构建工具链风险未消除"表述，不能宣称已清理 |

阶段47 曾把 `agents/schema_retriever.py` 的术语/规则匹配死路径留作"待修"，该项已在阶段49修复（同步调用 + 失败写入 metadata，5 项回归测试，见[阶段49](stage-49.md)）。

阶段50 把三处"看起来成功"改成显式失败或显式降级：`tool_executor` 在没有 MCP 客户端时返回 `ERROR`，不再用 `{"stub": True}` 加 `status=ok` 冒充执行完成；PostgreSQL 索引列改由 `pg_index` + `pg_get_indexdef` 取真实列名与顺序，不再是 `columns=[]`；`FeedbackAgent` 与 `POST /api/v1/feedback` 共用同一个 MySQL 写入并回报 `storage` / `persisted`，不再一边不问就答"已记录"。阶段56已补齐REST与Agent反馈的真实MySQL写入、关闭重开读取与SQL失败回退；Agent仍不触发学习管线（明确返回`learning_pipeline=False`）。详见[阶段50](stage-50.md)。


## 7. 阶段55–60证据收口

- 55（`4ebe7aa`）：直接依赖快照接入两条Linux CI，干净安装、pip check与版本核对通过；传递依赖仍浮动。
- 56（`066ab36`）：反馈真实MySQL成功与回退验收，修复反馈ID及提示语。
- 57（`a1da738`）：提交后发布前退出、新进程读取恢复，真实storage/auth readiness验收。
- 58（`d424dbc`）：共享设置结构校验，损坏记录不得部分发布。
- 59（`55a373b`）：非空密文解密失败不得静默采用环境密钥。
- 60（`0fb0bca`）：汇总设置只读组装，失败不污染内存，成功GET不发布运行时。

最终阶段60的[常规CI](https://github.com/Qorq-byte/Data_engineer/actions/runs/41961890262)和[真实双库CI](https://github.com/Qorq-byte/Data_engineer/actions/runs/41961890297)均通过，包含上述累计变更。阶段55–60尚未合并main；阶段44的真实模型分数没有在新运行时重跑。后续依赖与验收条件见[剩余执行计划](remaining-execution-plan.md)。
