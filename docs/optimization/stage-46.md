# 阶段46：单worker持久恢复与故障注入

2026-10-31。本阶段补齐"崩溃后不静默信任残留状态"的可执行路径，并用**真实子进程重启**验证，而不是在同进程里重新初始化单例。

## 状态事实来源

完整清单与降级步骤见[运行时恢复契约](runtime-recovery.md)。要点：

- 真正持久、需要备份的只有三类：向量索引（LanceDB）、稀疏索引（BM25/FTS5）、用量审计（可选）与 trace（可选）。
- 数据库连接、Schema 快照、会话都是**进程内**状态，重启必须重建。把它们称为"可恢复"是错的，本阶段不在文档里这么写。
- LLM 设置的事实来源是外部 MySQL store；本地无该服务，其"落盘成功但未发布"的中断场景**未验证**，明确列为未覆盖。

## 新增能力

**1. 索引替换标记（崩溃可见）**

`SchemaMetadataRAG` 新增可选 `state_dir`。生产初始化（`app/rag/engine.py`）把它设为索引目录的父目录，于是在 `index_schemas(..., replace_db_id=...)` 开始时写入 `index-pending-<hash>.json`，**仅当向量与稀疏两边都成功返回后才删除**。

- 进程在写入中死亡 → 标记残留 → 下次构造时该库自动进入 `refresh_required`，`readiness` 报 `degraded`。
- 写入抛异常且补偿成功 → 标记仍保留。这是有意的保守选择：不把"补偿成功"当作可依赖的前提。
- 未传 `state_dir` 时行为与之前完全一致，既有 900+ 项 RAG 测试不受影响。

**2. 备份 / 校验 / 恢复 / 清理**（`app/recovery.py`、`scripts/state_backup.py`）

- SQLite 源用 `VACUUM INTO` 快照，不会拷到半写页。
- 目录源复制后逐文件 SHA-256 写入 manifest；文档明确声明目录复制非原子，必须在停服或索引不写入的窗口执行。
- `verify` 只报告 `MISSING:` / `DIGEST_MISMATCH:`，不修复。
- `restore` 先校验，任何不一致直接拒绝（`RESTORE_REJECTED_UNVERIFIED`）；目标已存在必须显式 `--overwrite`。
- `prune` 只删带 manifest 的目录，保留最新 N 份。
- 默认上限 512 MiB / 20000 文件，越线即中止，不产出半份备份。

## 验证

**进程内（真实文件、真实 SQLite）**

| 项 | 断言 |
|---|---|
| 备份快照 | manifest 记录 3 文件；备份库可直接打开且行数一致 |
| 非空目标拒绝 | `BACKUP_TARGET_NOT_EMPTY` |
| 限额中止 | `max_files=1` 触发 `BACKUP_FILE_LIMIT_EXCEEDED` |
| 篡改检出 | 改 1 字节 + 删 1 文件 → `DIGEST_MISMATCH` 与 `MISSING` 同时报告，且 verify 不改写文件 |
| 未验证恢复 | 损坏备份 → `RESTORE_REJECTED_UNVERIFIED` |
| 覆盖保护 | 目标存在 → `RESTORE_TARGET_EXISTS`；加 `--overwrite` 后恢复且行数一致 |
| 保留策略 | `keep=2` 删除较旧两份，非备份目录不动 |
| 标记阻断 | 构造时存在残留标记 → `pending_replacements=("probe",)` 且进入 `refresh_required` |
| 成功清标记 | 成功替换后标记删除，新实例 `pending_replacements=()` |
| 失败留标记 | 替换抛错后标记保留，新实例仍 `refresh_required` |

**真实子进程重启**

| 场景 | 结果 |
|---|---|
| 正常索引 4 文档后退出 | 退出码 0，`lancedb_count=4 / bm25_count=4`，无残留标记 |
| 400 文档索引写入中 `os._exit(9)` | 退出码 9，残留 1 个标记；**新进程**启动成功，`readiness.rag.status=degraded`、`refresh_required_database_count≥1` |

崩溃后被终止的那个进程留下空索引（0 文档）—— 说明它死在写入之前；即使如此，标记仍正确阻止了"空索引被当作可用"。

## 回归

新增 12 项测试。全量本地：**4248 通过 / 3 失败 / 83 跳过**。

3 项失败是 Windows 平台既有问题，与本次改动无关：`test_cli_refuses_hardlink_input_alias`、`test_concurrent_writers_do_not_mix_records`、`test_concurrent_request_audits_keep_their_own_tokens`。已用未改动的阶段44 工作区复现同样 3 项失败。RAG 相关 924 项全通过。

本地环境另有一处非代码问题：Windows 上 `pyarrow` 需要 `tzdata`，缺失时 140 项 RAG 测试报 `ArrowInvalid: The zoneinfo module or pytz package must be installed`。安装 `tzdata` 后恢复。**未修改依赖声明**，因为 Linux CI 自带系统时区数据。

## 边界

- 仅单 worker。多 worker 共享索引的失效协议未设计、未验收。
- 索引重建在两个后端之间仍不是事务，只保证"失败可见 + 可回滚尝试"。
- 备份目录本身的异地副本、加密、保留期策略不在本阶段范围。
- LLM 设置的写中断场景未验证（依赖外部 MySQL）。

关键代码：[app/recovery.py](../../app/recovery.py)、[scripts/state_backup.py](../../scripts/state_backup.py)、[schema_rag.py 的标记逻辑](../../app/knowledge/retrieval/schema_rag.py)；运维口径见[运行时恢复契约](runtime-recovery.md)。
