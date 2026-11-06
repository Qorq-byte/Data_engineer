# 单机多 worker 固定配置模式（阶段 66.6）

该模式由 FastAPI lifespan 显式启动。各 worker 从同一份加密发布清单恢复 MySQL 或 PostgreSQL 只读目标，打开预先构建的 LanceDB 与 BM25 索引，并使用共享 MySQL 保存会话、查询和模型并发配额及供应商 RPM。模型流由持有租约的任务负责传输和清理。

1. 停止全部 worker。在单 worker 维护环境准备持久化设置、数据库只读账号和完整索引。处理未完成的索引替换标记，关闭索引写入进程，让 SQLite 写入完成检查点。
2. 设置 `RAG_LANCEDB_DIR` 与 `RAG_BM25_PATH` 为同机绝对路径。用与服务相同的 `JWT_SECRET` 执行 `python -m scripts.build_worker_release --profiles <私有连接文件> --output <新发布清单>`。连接文件为 JSON 数组，每项包含 `db_id`、`db_type` 和 `config`。`config` 需提供 `host`、`port`、`database`、`user` 和 `password`。索引中的数据库 ID 必须与清单一致。私有连接文件、发布清单和签名密钥分别保管。
3. 让所有 worker 使用相同的代码、环境、MySQL 设置库、`SHARED_WORKER_RELEASE` 绝对路径和 `SHARED_WORKER_NAMESPACE`。命名空间允许 1 至 64 个字母、数字、连字符或下划线。用 `python -m uvicorn app.api:create_app --factory --workers 2 --host 127.0.0.1` 启动并检查 `/health/ready`。
4. 维护时先停掉全部 worker，再修改设置、连接或索引。重新生成清单，并使用新命名空间统一启动。会话按命名空间隔离，新命名空间不会继承旧会话。回退时恢复上一代清单、索引、环境、MySQL 设置和命名空间。

启动校验发布文件内容和所有 worker 的配置指纹。运行期间检查文件元数据、索引文件清单及 MySQL 持久化设置，发现变化时暂停查询并使 readiness 返回 503。管理员应把文件和数据库 Schema 的变更安排在离线维护窗口。在线维护接口会在鉴权后返回 409，查询、只读 SQL 执行和会话版本操作仍可用。

验收使用一次性 MySQL 和合成模型传输，覆盖正常 lifespan、独立 worker 重启、会话恢复、配置变化、全局模型并发及流式租约断开。该阶段的目标数据库真实验收覆盖 MySQL。PostgreSQL 作为不可变目标的端到端部署仍需专项复核。

## 升级和回退演练（阶段 68A）

真实 MySQL 自动化演练启动两代各两个 worker，再启动两个回退 worker。它检查新一代能执行查询且只能看到本代会话。还原上一代索引文件、加密清单、持久化设置和命名空间后，两台回退 worker 都通过 readiness，并读到与升级前相同的会话。演练中的模型响应来自合成传输。

上一代清单记录了索引绝对路径和内容哈希。停服后保留清单和索引原件的字节副本，回退时恢复到原路径。通用 SQLite 备份工具可能重写文件字节，无法保证通过原清单哈希校验。此次演练保留了原 MySQL 实例中的旧命名空间，独立 MySQL 灾难恢复和跨版本代码回退仍需专项演练。

## 独立 MySQL 恢复演练（阶段 68B）

恢复验收用两台服务器 UUID 不同的一次性 MySQL。停掉源 worker 后，将设置、用户表、会话、发布指纹和共享配额表逻辑复制到第二台服务器的空库。切换回环代理后，两台新 worker 通过 readiness，读回原会话，并能继续查询和共享新增轮次。执行 `python -m scripts.run_real_db_tests --mysql-recovery --filter mysql_worker_disaster` 可复验。

恢复保留原数据库名称、连接地址、端口、凭据、命名空间及发布文件，逻辑连接地址的后端指向恢复实例。生产备份应覆盖整个控制面库，另外保管发布文件、索引原件、环境配置和密钥。此次演练只复制列出的合成控制面表，查询目标数据库及其业务数据、原代码版本回退和备份保留策略需分别验收。
