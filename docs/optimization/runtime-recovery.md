# 运行时状态与恢复契约（单 worker）

2026-10-31。本文说明哪些状态真的持久、重启后如何重建、崩溃如何可见，以及运维可执行的备份/恢复/降级步骤。只覆盖**单 worker**；多 worker 的共享状态与失效协议未设计、未验收。

## 一、事实来源清单

| 来源 | 位置 | 是否持久 | 重启后的处理 | 失败时的可见性 |
|---|---|---|---|---|
| 数据库连接注册 | 进程内 `ConnectionFactory` | 否 | 必须由启动配置或注册接口重建 | 无连接时 `readiness` 为 `not_ready`（`connected_database_count=0`） |
| Schema 快照 | 进程内 + `SchemaExtractor` 缓存 | 否 | 重新内省；缓存不跨进程 | 内省失败时该库不可查询，错误随请求返回 |
| 向量索引（LanceDB） | `RAG_LANCEDB_DIR`（默认 `data/lancedb`） | **是** | 打开并校验；不一致标记为需刷新 | `readiness.rag.status=degraded/unavailable`，`refresh_required_database_count>0` |
| 稀疏索引（BM25/FTS5） | `RAG_BM25_PATH`（默认 `data/bm25.db`） | **是** | 与向量索引同生命周期，必须一起重建 | 同上；单边重建会破坏一致性 |
| 索引写入标记 | 索引目录同级 `index-pending-*.json` | **是** | 存在即表示上次替换未成功 | 该库进入 `refresh_required`，不参与检索 |
| LLM 设置 | 外部 MySQL store | 是（外部） | 启动时读回并重建路由 | 读取失败时 `components.llm=false`，实际回落到只读/降级 |
| 用量审计 | `LLM_AUDIT_PATH`（可空） | **是（可选）** | 空值即关闭审计 | 审计写失败使该请求标记 `audit_failed`，不伪造成功 |
| 会话 | 进程内 | 否 | 重启即失效 | 旧 `session_id` 不被恢复，不做静默续接 |
| Trace | `DE_TRACE_DIR`（可选） | 是（可选） | 目录缺失则重建 | trace 写失败不影响查询结果，但会缺失证据 |

**结论**：真正需要备份的只有三类——向量/稀疏索引、用量审计、trace。连接、Schema 快照、会话都属于"重启必须重建"的进程内状态，把它们的恢复称为"持久恢复"是错的。

## 二、启动顺序（与代码一致）

`app/api/__init__.py::_lifespan`

1. `initialize_catalogs()`：加载领域与工作流配置。
2. storage（MySQL store）→ auth（MySQL）→ 各自 10 秒超时，失败只降级不阻塞启动。
3. RAG：`initialize_rag_engine()` → 打开 LanceDB 与 BM25 → 扫描 `index-pending-*.json` → 把残留标记的库加入 `refresh_required` → 对索引中已存在但当前无连接的库同样标记 → 尝试重建已连接库的 Schema。
4. LLM：读持久化设置并重建路由；失败则发布明确的 mock 路由，`components.llm=false`。旧路由或环境配置不能掩盖此次读取失败；后续成功保存或重载设置可恢复动态 readiness。
5. `app.state.initialized = true`；`readiness` 才开始可能返回 200。

关闭顺序是上一步的逆序：background → subagents → databases → auth → storage → RAG → router。任何一步异常只记录，不阻断其余关闭。

## 三、崩溃如何可见

索引替换（`SchemaMetadataRAG.index_schemas(..., replace_db_id=...)`）在开始前写入 `index-pending-<hash>.json`，**只有两边后端都返回成功后才删除**。

- 进程在写入中死亡 → 标记残留 → 下次启动该库进入 `refresh_required`，不参与检索。
- 写入抛异常且补偿成功 → 索引内容已回滚，但标记仍保留；这是有意的保守选择：宁可要求一次完整刷新，也不依赖"补偿一定成功"的假设。
- 成功替换 → 标记删除，`refresh_required` 清除。

`readiness` 因此在重启后如实报告 `degraded`，而不是把残留索引当作可用。

## 四、备份

```bash
python -m scripts.state_backup --action sources --state-root data
python -m scripts.state_backup --action backup --state-root data --backup-root .state_backups
```

- SQLite 源用 `VACUUM INTO` 生成快照，不会拷到半写页。
- 目录源（LanceDB）先复制后逐文件计算 SHA-256，写入 `manifest.json`。
- **目录复制不是原子的**：必须在服务停止或索引不写入的窗口执行，工具不会假装它安全。
- 默认上限：512 MiB / 20000 文件，越线即中止，不产出半份备份。

## 五、校验、恢复、保留

```bash
python -m scripts.state_backup --action verify  --backup-dir .state_backups/20261031-160000
python -m scripts.state_backup --action restore --backup-dir ... --state-root data [--overwrite]
python -m scripts.state_backup --action prune   --backup-root .state_backups --keep 3
```

- `verify` 重算所有摘要并列出 `MISSING:` / `DIGEST_MISMATCH:`，**不修复**。
- `restore` 先校验；任何不一致直接拒绝（`RESTORE_REJECTED_UNVERIFIED`），不会把损坏字节写回生产目录。目标已存在时必须显式 `--overwrite`。
- `prune` 只删除带 `manifest.json` 的目录，保留最新 N 份。

## 六、降级与修复步骤

| 症状 | 判定 | 操作 |
|---|---|---|
| `readiness.rag.status = degraded` 且 `refresh_required_database_count > 0` | 有库的索引不可信 | 对该库执行一次完整 Schema 刷新；成功后计数归零 |
| `recovery_failed_database_count > 0` | 索引写入失败且补偿失败 | 清空该库索引后完整重建；不要单边重建 |
| BM25 与 LanceDB 文档数不一致 | 双索引部分写 | 视为不可用，两库一起重建 |
| `readiness.status = not_ready`，`connected_database_count=0` | 没有注册连接 | 按部署配置重新注册连接；这不是索引问题 |
| 审计库损坏 | 打开失败 | 保持 `LLM_AUDIT_PATH` 为空以关闭审计，或从备份恢复；查询不因此中断 |

## 七、阶段57：LLM设置恢复验收

`tests/real_db/test_settings_recovery.py` 使用独立临时 MySQL 数据库覆盖加密保存、关闭重开连接后加载、删除 provider 和实际 SQL 写入拒绝。跨进程探针在真实提交后、发布路由前退出；另一新进程运行实际 lifespan，核对设置、合成密钥恢复和 storage/auth/llm readiness。无效输出上限、providers非字典与不兼容表结构须返回 not_ready，不能沿用探针预置的旧路由。

探针只使用合成凭据，拦截模型调用；RAG在该探针中明确关闭，查询库为本地SQLite。readiness通过只说明当前配置与启动状态符合既有条件，不代表模型网络连通、业务库健康或完整端到端质量。离线回归另覆盖成功PUT/重载后的readiness恢复。

阶段58增加共享结构校验：API读取和路由工厂在消费密钥前核对模型列表、布尔开关、文本字段、非负整数RPM与输出上限，禁用或无密钥的provider也必须通过。损坏记录返回固定的`LLM_SETTINGS_READ_FAILED`，不回显配置值、不替换现有内存和路由；启动时按上述规则降级。缺失字段沿用默认值，已有可空文本/模型字段继续接受，API把`models:null`投影为空列表，RPM为0仍表示不限速，旧的忽略字段仍不参与路由。真实MySQL重启用例另覆盖嵌套字段类型损坏。

阶段59明确密钥来源：启用provider的非空持久化密文必须成功解密，否则路由构建失败，不能静默使用环境密钥；仅空值或未配置时保留环境回退。更换JWT_SECRET前必须保留原密钥或安排重新配置provider密钥。损坏密文可由设置API提交新的provider密钥覆盖，或删除该provider；普通设置保存不会绕过解密错误，错误响应不返回密文或原始异常。

## 八、未覆盖（不得当作已验证）

阶段60将汇总设置GET改为只读组装：在同一worker的LLM/RAG设置锁内读取，批量读取异常或超时返回`SETTINGS_READ_FAILED`，后续分区读取失败也不把部分数据写入内存。成功读取只描述持久化配置与当前运行时，不发布路由或修改内存。该锁不构成跨进程或数据库外部写者的事务快照。

- MySQL进程或主机崩溃、断电后的存储耐久性不在阶段57进程退出探针范围内。
- 多 worker 共享索引的失效协议未设计。
- 备份目录本身的持久化与异地副本不在工具范围内。
- 索引重建的原子性跨两个后端仍不是事务，只保证"失败可见 + 可回滚尝试"。
