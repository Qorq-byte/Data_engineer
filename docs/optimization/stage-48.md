# 阶段48：受限试用、干净环境部署与回退验收

2026-10-31。本阶段汇总阶段41–47证据，冻结支持范围，并在**没有 `.env`、没有 MySQL、全新虚拟环境**的干净副本里验证部署、readiness、升级与回退。支持范围与检查单见[发布检查单](release-checklist.md)，原始证据见[stage-48-clean-room.json](results/stage-48-clean-room.json)。

本阶段不改业务逻辑，只修了一处运维脚本缺陷（见 3.1），原因是它在干净环境演练中直接阻断了保留策略。

## 1. 干净环境

| 项 | 值 |
|---|---|
| 工作区 | `.stage48`，分支 `codex/release-trial`，基线 `3d6b363` |
| 虚拟环境 | 新建 `.venv-clean`，Python 3.12.13，`pip install -e .` 成功 |
| 配置来源 | 无 `.env`，全部用环境变量 |
| 主机 | Windows 11，24 逻辑核 |

**首次启动被拒绝**（预期行为，非缺陷）：

```
ValueError: JWT_SECRET must be configured with at least 32 characters
```

`settings.jwt_secret` 的默认值 `change-me-in-production` 只有 22 字符，因此默认配置在任何环境都无法启动。这是有意的 fail-fast，已写入检查单第 2 节。

带 32 字符以上 `JWT_SECRET` 与 `APP_PORT=18001` 重启后，12 秒内 `/health` 200，监听 `http://0.0.0.0:18001`。

## 2. 路由与 readiness

| 路径 | 状态 | 说明 |
|---|---|---|
| `/health` | 200 | 存活 |
| `/health/ready` | 503 | `storage:false`、`auth:false`、`rag:true`、`llm:true`，`connected_database_count:0` |
| `/docs` | 200 | — |
| `/api/v1/schema/databases` | 401 | 无令牌被拒 |
| `/`、`/login`、`/workspace` | 503 `FRONTEND_NOT_BUILT` | 构建前；显式降级，非空白页 |

干净环境没有 MySQL，`storage`/`auth` 必然为 false，`/health/ready` 因此 503。这与"服务崩溃"不同：进程存活、API 可响应、只是未就绪。`rag` 报 `ready` 且 `refresh_required_database_count:0`，说明上一轮索引替换标记已正确清理。

`curl` 访问 127.0.0.1 需要 `--noproxy '*'`，否则走本机代理得到 502。这是本机环境特性，不是服务缺陷，但排查时容易误判。

## 3. 前端构建与静态交付

`npm ci --ignore-scripts` 22 秒、0 依赖告警；`npm run check:frontend` 全部 11 组通过（三页脚本 + 12/37/34/29/29/41 项契约场景）；`npm run build:frontend` 输出 `Built 3 pages, 46 files, 2043810 bytes`。

产物核对不是只看状态码：

| 检查 | 结果 |
|---|---|
| manifest 条目 | 45（42 个 `assets/` + 3 个 HTML） |
| 本地重算 SHA-256 与字节 | 45/45 一致，总计 2,036,895 字节 |
| 42 个 assets 经 HTTP 取回后重算摘要 | 42/42 为 200 且摘要一致 |
| 构建后三页 | `/`、`/login`、`/workspace` 均 200 |

这证明"构建产物与清单一致、且服务实际吐出的字节就是清单记录的内容"，但不构成跨操作系统字节恒等的承诺。

### 3.1 演练中发现并修复的缺陷

`scripts/state_backup.py` 的 `prune` 分支写在 `--backup-dir` 必填校验之后，于是文档里的保留命令

```
python -m scripts.state_backup --action prune --backup-root .state_backups --keep 3
```

**恒以退出码 2 失败**，保留策略实际不可用。已把 `prune` 与 `backup`/`sources` 并列提前，`verify`/`restore` 仍在守卫之后；补 `test_cli_prune_does_not_require_backup_dir`（子进程调用，断言退出码 0 与删除集合）。`pytest tests/test_recovery` 13 项通过。

修复后干净环境实测：`.drill2` 下 4 个目录（2 份备份 + 2 个恢复产物），`--keep 1` 删除较旧的 `new`，保留 `old`，无 `manifest.json` 的恢复目录不动，退出码 0。

## 4. 备份与恢复

| 步骤 | 结果 |
|---|---|
| `--action sources` | 3 类：`lancedb`(tree)、`bm25`(sqlite)、`audit`(未创建) |
| 备份 | 41 文件 / 222,586 字节，`present:["lancedb","bm25"]` |
| 校验 | `ok:true`，41 文件，无 `MISSING`/`DIGEST_MISMATCH` |
| 恢复 | `checked_files:41`，落盘 41 文件，恢复的 `bm25` 可直接打开（3 个 FTS5 表 + shadow 表齐全） |
| 保留 | 见 3.1 |

## 5. 升级与回退（跨版本）

在同一工作区用 `git checkout` 切换版本，`frontend_dist/` 与 `data/` 保留不动——这正好模拟"静态目录与数据跨版本保留"的场景（两个提交都没有改前端源码，产物内容相同）。

| 方向 | 动作 | 结果 |
|---|---|---|
| 升级：旧代码读新备份 | `3d6b363` 校验 `dcc2888` 产出 | `ok:true`，41 文件 |
| 升级：旧代码恢复新备份 | 同上 restore | 41 文件落盘，SQLite 可打开 |
| 回退：旧版本服务 | `3d6b363` 启动 | 三页 200、`/health` 200、`/health/ready` 503（与新版一致） |
| 回退：旧版本产出备份 | 51 文件 / 275,539 字节（启动时多写若干 LanceDB 事务文件） | — |
| 升级：新代码读旧备份 | `dcc2888` 校验并恢复 | `ok:true`，51 文件全部落盘 |

双向可用，备份格式在两个相邻版本间兼容。**未覆盖**：跨越多版本的回退、回退时静态资源内容不同的情形（本次两版本前端一致，无法验证"只回退 HTML"的错误做法会造成什么后果）。

## 6. 阶段47遗留：依赖告警重新核验

同一份 `package-lock.json`（最后改动 `1510dd5`，2026-10-23）在两个时间点得到不同结论：

| 日期 | `npm audit` |
|---|---|
| 2026-10-23（阶段21记录） | 7 项：5 high、2 moderate（braces 链、postcss-selector-parser/postcss-nested 链） |
| 2026-10-31（本阶段） | **0 项** |

中间没有升级任何构建依赖，也没有改锁文件、没加忽略规则。差异只能来自 npm 注册表侧的 advisory 数据（撤回或范围调整）。

因此**不能**把 0 当成"构建工具链已清理"的证据。对外仍按阶段21口径表述：这些依赖只在构建期执行、不进入浏览器包、不被 Python 服务执行，构建不可信提交仍有构建期拒绝服务风险，只能在隔离且资源受限的可信环境构建。Python 依赖升级本身仍未开始，按规划与类型修复分开处理。

## 7. 运行时版本漂移

`pyproject.toml` 只写了下界（`fastapi>=0.115.0` 等），没有 Python 锁文件。干净环境解析到：

| 包 | 阶段44清单冻结 | 干净环境实测 |
|---|---|---|
| Python | 3.12.13 | 3.12.13 |
| pydantic | 2.14.1 | **2.14.2** |
| litellm | 1.106.4 | **1.106.6** |
| fastapi / uvicorn | — | 0.162.0 / 0.54.0 |
| lancedb / sqlglot | — | 0.41.0 / 30.18.0 |

阶段44的 30 题批次是在 2.14.1 / 1.106.4 上跑出的 26/30，**没有**在新组合上重跑。发布检查单把"加锁或重跑"列为发布前必须决定的事项，本阶段不擅自改依赖版本。

离线也验证了：litellm 连续三次抓取远程价格表失败后保留本地副本并继续运行，服务不受影响，只是日志有告警。

## 8. 未执行

- MySQL 可达时的 `storage`/`auth` ready 路径（干净环境无 MySQL）。
- 多 worker / 多副本。
- 服务运行中原地重建前端。
- Python 依赖升级、真实模型与真实 embedding 的容量测量、业务标注。

## 9. 结论

阶段41–47的证据可以支撑**私有单实例、白名单操作者、每库 ≤1000 表、单进程**的受限试用；部署、readiness、备份恢复、升级回退都在干净环境跑通并被记录。不能支撑多租户、多 worker、7 种未实测方言、任何业务准确率或生产 SLO。实际发布与扩大访问面按届时明确指令处理。
