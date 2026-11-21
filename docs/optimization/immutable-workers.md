# 单机多 worker 固定配置模式（阶段 66.6）

该模式由 FastAPI lifespan 显式启动。各 worker 从同一份加密发布清单恢复 MySQL 或 PostgreSQL 只读目标，打开预先构建的 LanceDB 与 BM25 索引，并使用共享 MySQL 保存会话、查询和模型并发配额及供应商 RPM。模型流由持有租约的任务负责传输和清理。

1. 停止全部 worker。在单 worker 维护环境准备持久化设置、数据库只读账号和完整索引。处理未完成的索引替换标记，关闭索引写入进程，让 SQLite 写入完成检查点。
2. 设置 `RAG_LANCEDB_DIR` 与 `RAG_BM25_PATH` 为同机绝对路径。用与服务相同的 `JWT_SECRET` 执行 `python -m scripts.build_worker_release --profiles <私有连接文件> --output <新发布清单>`。连接文件为 JSON 数组，每项包含 `db_id`、`db_type` 和 `config`。`config` 需提供 `host`、`port`、`database`、`user` 和 `password`。索引中的数据库 ID 必须与清单一致。私有连接文件、发布清单和签名密钥分别保管。
3. 让所有 worker 使用相同的代码、环境、MySQL 设置库、`SHARED_WORKER_RELEASE` 绝对路径和 `SHARED_WORKER_NAMESPACE`。命名空间允许 1 至 64 个字母、数字、连字符或下划线。用 `python -m uvicorn app.api:create_app --factory --workers 2 --host 127.0.0.1` 启动并检查 `/health/ready`。
4. 维护时先停掉全部 worker，再修改设置、连接或索引。重新生成清单，并使用新命名空间统一启动。会话按命名空间隔离，新命名空间不会继承旧会话。回退时恢复上一代清单、索引、环境、MySQL 设置和命名空间。

启动校验发布文件内容和所有 worker 的配置指纹。运行期间检查文件元数据、索引文件清单及 MySQL 持久化设置，发现变化时暂停查询并使 readiness 返回 503。管理员应把文件和数据库 Schema 的变更安排在离线维护窗口。在线维护接口会在鉴权后返回 409，查询、只读 SQL 执行和会话版本操作仍可用。

可选指标目录必须在构建发布包与服务启动时设置同一 `METRIC_CATALOG_PATH` 绝对路径。
构建器自动把目录加入文件清单，封存 path/version/SHA256，且所有绑定的 database 必须是清单内的 db_id。
没有指标目录的旧 format_version=1 发布包继续可用，但不能仅设置环境变量便启用指标。
启用后各 worker 从活跃发布对象获取同一不可变快照；发现、查询和 readiness 检测内容变化，
包括保留大小/mtime 的改写。目录升级须停服、重新封存并按新代统一启动。
详细契约和本次证据见[共享指标目录](shared-metric-catalog.md)。

初次验收使用一次性 MySQL 和合成模型传输，覆盖正常 lifespan、独立 worker 重启、会话恢复、配置变化、全局模型并发及流式租约断开。PostgreSQL 查询目标的后续证据见 [双租户 PostgreSQL 验收](#双租户-postgresql-验收)。

## 升级和回退演练（阶段 68A）

真实 MySQL 自动化演练启动两代各两个 worker，再启动两个回退 worker。它检查新一代能执行查询且只能看到本代会话。还原上一代索引文件、加密清单、持久化设置和命名空间后，两台回退 worker 都通过 readiness，并读到与升级前相同的会话。演练中的模型响应来自合成传输。

上一代清单记录了索引绝对路径和内容哈希。停服后保留清单和索引原件的字节副本，回退时恢复到原路径。通用 SQLite 备份工具可能重写文件字节，无法保证通过原清单哈希校验。此次演练保留了原 MySQL 实例中的旧命名空间。

## 独立 MySQL 恢复演练（阶段 68B）

恢复验收用两台服务器 UUID 不同的一次性 MySQL。停掉源 worker 后，将设置、用户表、会话、发布指纹和共享配额表逻辑复制到第二台服务器的空库。切换回环代理后，两台新 worker 通过 readiness，读回原会话，并能继续查询和共享新增轮次。执行 `python -m scripts.run_real_db_tests --mysql-recovery --filter mysql_worker_disaster` 可复验。

恢复保留原数据库名称、连接地址、端口、凭据、命名空间及发布文件，逻辑连接地址的后端指向恢复实例。生产备份应覆盖整个控制面库，另外保管发布文件、索引原件、环境配置和密钥。此次演练只复制列出的合成控制面表，查询目标数据库及其业务数据和备份保留策略需分别验收。

## 跨代码版本回退（阶段 68C）

在回环一次性 MySQL 9.7.0 上，从 Git 归档解包 `9d1120b00c29a1105ecc9f049528d7124a4fa692` 与 `b401c2c8ee86316d94ae281fa37d180e6b6747f4`。两个版本的共享 MySQL 限流模块内容不同。使用旧版本启动两个 worker 并写入会话，停服后切换到候选版本的代码、清单、索引、设置和命名空间。候选代可执行同一只读查询，并在本代两个 worker 之间共享新会话。

停止候选代并恢复旧版本设置与命名空间后，从旧代码、原路径的旧清单和索引重新启动两个 worker。两个进程均通过 readiness，读取到升级前相同的会话，看不到候选代会话，并可继续查询和共享新会话。三个阶段都验证了真实 MySQL 返回有序的五条合成记录。使用 Python 3.12.13 和仓库 CI 约束依赖复跑通过。模型传输使用合成响应。

该证据适用于上述提交组合、兼容的控制面表和单机停服切换。另一次演练验证了多租户候选版本迁移后，以独立 MySQL 恢复旧表结构并重新启动旧版。[多租户迁移回退演练](tenant-mysql-migration.md#多租户迁移回退演练)

## 会话兼容性补充

默认租户沿用原版会话 ID 和 MySQL 摘要。部署命名空间保持相同时，原版尚未过期的会话快照可以直接读取，续写也使用相同的存储键。其他租户使用独立的会话键。快照读写和协调锁使用同一摘要，回收过期记录时保留仍持有锁的快照。共享会话容量仍由整个部署命名空间共用。

测试以原版键格式为迁移基准。此前开发阶段的字符串前缀格式缺少可靠的归属标记，已有此类测试数据时需依据备份和已知归属单独整理。

MySQL 8.4.13 使用一次性合成数据库分两批验收。会话与协调锁通过 15 项，共享 worker 通过 8 项，覆盖 JSON、SSE 和 MCP 的跨进程会话可见性。两个批次的临时容器均已清理。

```powershell
python -m scripts.run_real_db_tests --filter 'shared_sessions or mysql_coordination' --output data/tenant-session-acceptance/sessions.json
python -m scripts.run_real_db_tests --filter shared_query_runtime --output data/tenant-session-acceptance/shared-workers.json
```

## v2 租户发布包

发布构建器也接受按租户 ID 分组的连接文件。键 `"1"` 对应默认租户，至少再提供一个租户分组。每组使用 v1 的连接数组格式，最多封存 64 个连接。同一 `db_id` 可以出现在不同租户分组中。指标目录按租户 ID 与数据库 ID 的组合核对绑定。

```json
{
  "1": [{"db_id":"reporting","db_type":"mysql","config":{"host":"db.example.test","port":3306,"database":"default_app","user":"default_reader","password":"<credential>"}}],
  "2": [{"db_id":"reporting","db_type":"mysql","config":{"host":"db.example.test","port":3306,"database":"tenant_2","user":"tenant_2_reader","password":"<credential>"}}]
}
```

停掉全部 worker 后准备各租户的 MySQL 模型配置、领域、术语、规则和各自的索引。使用上面的发布构建命令封存连接，并为 v2 分配新命名空间。所有 worker 设置相同的 `TENANT_ADMIN_USER_IDS`，名单须与清单中的非默认租户一致。模型端点通过 `TENANT_LLM_ENDPOINTS` 声明，连接来源由发布包决定，`TENANT_QUERY_PROFILES_PATH` 留空。v1 数组格式仍适用于默认租户。

启动时固定已纳入租户的 MySQL 模型和领域快照。发布指纹包含这些快照、加密连接清单及进程配置。运行时发现漂移后暂停查询和租户配置读取，readiness 返回 `503`。工作台提供查询、Schema、领域和模型查看，在线配置写入返回 `409`。非默认租户查询使用发布中的领域规则，进程本地审批规则可在停服维护时整理到领域目录。

同一发布命名空间的 worker 恢复共享会话。同名会话和模型供应商 RPM 按租户隔离，查询和模型并发上限由部署统一分配。MCP HTTP 可挂载到已启动的 worker，也可使用相同配置独立启动。标准输入输出 MCP 的运行方式沿用原有行为。

```powershell
python -m scripts.run_real_db_tests --filter 'immutable_tenants or tenant_llm_runtime' --output data/immutable-tenant-acceptance/verified.json
python -m scripts.run_real_db_tests --filter 'immutable_workers or metric_mcp_network' --output data/immutable-tenant-acceptance/default-workers.json
```

MySQL 8.4.13 上首轮租户组合通过 3 项，默认 v1 组合通过 7 项。本地回归通过 840 项。实库覆盖两个独立 worker、独立启动的 MCP HTTP、重启、配置拒绝和真实只读目标。模型传输使用脚本化响应。

### 双租户 PostgreSQL 验收

单机 v2 使用 MySQL 8.4.13 处理认证、控制面和共享会话，两个非默认租户分别连接 PostgreSQL 16.15 的独立只读目标。两个 worker 通过正式登录验证了同名数据源、指标和会话的租户隔离，覆盖 JSON、SSE、REST 指标发现及 MCP HTTP。两进程分别重启后恢复会话。配置漂移时拒绝新请求，旧令牌和跨租户 MCP 会话均被拒绝。模型响应使用脚本化传输，生产容量仍需专项验收。

指标工作流使用 SQLGlot 的 `postgres` 方言名，连接器发布清单沿用 `postgresql`。两个数据库目标均验证了指标解析、校验、实际执行、目录哈希和零模型调用。该次组合验收通过 9 项，包含 MySQL 默认 v1 和 MCP HTTP 回归。重复验证命令如下。

```powershell
python -m scripts.run_real_db_tests --filter 'immutable_tenants or immutable_workers or metric_mcp_network' --output data/postgres-immutable-20261120/verified.json
```

### 封存共享 worker 的子 Agent 目录

需要在共享部署启用子 Agent 时，在离线维护窗口用 `--subagent-catalog` 把受信 JSON 目录纳入新发布代。文件采用 `shared-subagents-v1`，最多容纳 128 个定义。以下示例为租户 2 的只读查询入口。

```json
{
  "profile": "shared-subagents-v1",
  "subagents": [{
    "name": "analyst",
    "tenant_id": 2,
    "domain": "ecommerce",
    "database_ids": ["reporting"],
    "capabilities": ["nl_query", "metric_query"],
    "allowed_users": ["2"],
    "enable_execution": true,
    "enable_multi_candidate": false
  }]
}
```

```powershell
python -m scripts.build_worker_release --profiles <私有连接文件> `
  --subagent-catalog <子 Agent 目录 JSON> --output <新发布清单>
```

构建器核对每个租户和数据库 ID 与连接清单匹配，拒绝租户内重名。`allowed_users` 使用用户 ID 字符串，空数组拒绝全部，`["*"]` 表示范围内所有已授权 operator。非默认租户在 worker 启动时核对领域快照，`default` 可使用该部署的默认领域行为。执行指标还需封存匹配的指标目录。

REST、认证 MCP HTTP 与工作台按认证租户读取发布代的注册快照。worker 日常校验目录的 SHA-256，发现内容变化即拒绝查询并使 readiness 返回 `503`，即使文件大小和修改时间相同。旧发布代可继续运行既有查询，子 Agent 列表为空。目录更新使用新发布代和版本切换流程。

两个 worker 在 MySQL 和 PostgreSQL 只读目标上验证了同名子 Agent 返回各自租户结果。独立 MCP HTTP 使用同一目录，目录改写使两个 worker 同时拒绝和退出就绪，指标 v2 的周分组与跨表总额与合成预期一致。复跑命令如下。

```powershell
python -m scripts.run_real_db_tests --filter immutable_tenants_queries_sessions_drift_and_restart `
  --output data/sealed-subagents/verified.json
```
