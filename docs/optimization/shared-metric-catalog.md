# 共享 worker 指标目录封存：设计与验收

日期：2026-11-17，前序本地验收 4756 passed；保留已有工作区修改。

优先级：目录此前阻断共享 worker 上的指标查询；封存和已有协调框架可以独立完成，
收益是让已接线的指标能力进入可重复的多进程部署验收。无需业务数据或云凭据，
主要风险是各进程读取不同定义和运行中静默换版，以下版本、哈希及失败关闭契约直接覆盖这两项风险。

## 范围与契约

本阶段把已经存在的结构化指标目录纳入单机固定配置 worker 发布流程。
发布包仍兼容没有指标目录的 format_version=1；增加可选 metric_catalog 元数据（path/version/sha256）。
封存时只读解析目录，校验所有 domain/database 绑定均引用清单内的 db_id，不创建数据源或调用模型。
目录文件自动加入 files 哈希清单，路径使用绝对路径；发布包与配置目录相互覆盖必须拒绝。

启动在连接数据库前核对 METRIC_CATALOG_PATH 与封存目录一致、版本和哈希正确，加载不可变 dataclass 快照。
各 worker 使用同一发布包/环境/控制面配置指纹；将已验证的发布对象绑定到活跃共享运行时。
指标发现和查询均从活跃封存快照取定义，不因在线文件替换切换版本；缺少活跃运行时、封存漏项或内容漂移均拒绝。
共享查询继续经过原 MySQL 协调/会话/准入/审计流程及三节点 Harness，目录发现也要验证共享配置状态。
未配置目录的共享 worker 不会自动启用指标，普通单 worker 保留 v1 的按请求目录快照行为。

## 实施顺序及验收

1. 发布包：封存与恢复、旧包兼容、重复/未封存/路径不一致、无效目录、版本/hash/连接绑定错误、文件覆盖负例。
2. 运行时：启动前拒绝错误目录；仅从活跃发布对象获取快照，逐次检测目录内容漂移（包含保留大小/mtime的改写）；
   停服后不再可用，发现/查询与其他共享请求一致地拒绝异常。固定工作流和权限默认拒绝保持。
3. 本地验收：真实文件和 SQLite（不模拟数据库），独立进程读同一包/快照并核对 hash 和聚合结果；
   不能将此结果当作 MySQL 多 worker 部署验收。
4. 实库验收：一次性 MySQL 控制面和查询目标，两 serving worker 验证发现/JSON/SSE、共享会话、零模型调用、
   目录漂移后 readiness/发现/查询拒绝，以及新进程环境或目录与封存不一致时不能开始服务。
5. 质量门禁：匹配 constraints.txt 的 `.stage62/.venv/Scripts/python.exe` 运行 pytest、ruff、mypy、依赖和文档检查。

## 环境与边界

初始 Docker 引擎检查在沙箱内外均失败：dockerDesktopLinuxEngine 管道不存在。
随后通过 `docker desktop start --detach` 启动现有 Docker Desktop，恢复 Linux 引擎。
使用本机已有 mysql:8.4 与 postgres:16 镜像，不拉取镜像、不使用宿主或生产凭据。
runner 给临时容器设置唯一标签和 CPU/内存限制、动态回环端口、tmpfs；仅按完整 ID/标签清理本次容器。
Snowflake、生产业务指标签收、跨表/时间语义和租户隔离保持独立待办。

## 配置与验证命令

维护时停掉所有 worker。设置 `METRIC_CATALOG_PATH` 为服务端 YAML 的绝对路径，
与 `RAG_LANCEDB_DIR`、`RAG_BM25_PATH`、`JWT_SECRET` 一起用于构建和服务启动。
沿用[不可变 worker 配置流程](immutable-workers.md)：

```powershell
$env:METRIC_CATALOG_PATH='D:/deployment/metrics/catalog.yml'
python -m scripts.build_worker_release --profiles <私有连接文件> --output <新发布清单>
# 各进程使用同一代路径、环境、控制面与 SHARED_WORKER_NAMESPACE；目录变更必须重新封存。
```

未配置目录的旧包保持兼容；有目录却漏封存、错路径、错版本/hash 或绑定未封存 db_id 都不能启用。
启动加载和检查发生在创建共享 MySQL 连接池前。目录发现只返回显式 domain/db_id 的绑定；
需要 operator 身份，不创建会话。查询继续走原协调租约、权限、只读 SQL 和三节点生命周期。
JSON/发现的共享状态异常返回 503/SHARED_STATE_UNAVAILABLE；SSE HTTP 建立后返回同码 error 事件，
不能将传输层 200 当作成功。错误不回显目录路径或连接凭据。

```powershell
$py='.stage62/.venv/Scripts/python.exe'
& $py -m pytest tests/test_harness/test_worker_metric_catalog.py tests/test_harness/test_worker_release.py tests/test_api/test_metric_discovery.py tests/test_api/test_metric_query.py tests/test_mcp/test_metric_tools.py -q
& $py -m scripts.run_real_db_tests --filter 'shared_metric_catalog or actual_startup_restores_connections_and_restart_keeps_session' --output data/shared-metric-catalog/real-db-final.json
& $py -m pytest -q -m 'not real_database' --junitxml=data/shared-metric-catalog/local.xml
& $py -m ruff check .
& $py -m mypy app
& $py scripts/check_dependencies.py
& $py scripts/check_readmes.py
npm run check:frontend
npm run check:frontend:build
```

## 实现、接线与实际验证

- `app/harness/worker_release.py`：可选封存元数据、db_id 交叉检查、文件哈希与快照；
  `scripts/build_worker_release.py` 自动读取目录配置。旧的 format_version=1 无目录发布包仍可恢复。
- `app/harness/worker_bootstrap.py`：启动前覆盖校验和目录冻结、活跃 runtime 绑定；
  readiness/共享准入检查内容，包括保留大小/mtime 的目录与发布包改写。
- `app/harness/metric_catalog_runtime.py`：活跃快照唯一入口；`plan_runtime.py`、`metric_runtime.py`、
  JSON/SSE 与 MCP 接线共用它。停服后活跃运行时撤销，不能从磁盘临时加载另一份目录继续执行。
- 本地新增 13 项真实文件/SQLite/独立 Python 进程测试和 1 项 API 生命周期负例：
  旧包、缺失描述、版本/hash/清单/未知字段错误、内容改写、删除、输出覆盖、db_id 越界、
  独立进程结果一致、启动初始化之前拒绝、无活跃 runtime 时的发现与 JSON/SSE 拒绝。
- 真实 MySQL：4 项新指标用例及 1 项既有无目录启动/重启回归，**5 passed / 144 deselected**，136.69s。
  引擎版本 **8.4.13**；两个独立 HTTP serving 进程使用正常 lifespan、真实控制面/会话/只读查询连接、
  预构建 LanceDB/BM25，替换进程重启后恢复同一目录和会话。5 条合成订单，COUNT=5、SUM(id)=150；
  MySQL 的 SUM 返回 DECIMAL，JSON 按原契约保留字符串 `"150"`。此路径模型调用数为零。
  保留 size/mtime 的改写使两进程 readiness/发现/查询拒绝；错误目录的新进程不能服务；控制面漂移同样拒绝发现。
  本轮两个容器均已校验身份后删除。PostgreSQL 容器为既有 runner 的伴随服务，本轮没有选择 PostgreSQL 测试。
- 首轮实库 3 passed / 2 failed 是测试断言与既有 DECIMAL/SSE 契约不符，修正后复跑上述全部用例通过。
  独立 SQLite 子进程最初缺少 Windows USERNAME，修正环境保留项后相关组曾 77 passed；新增负例由最终回归覆盖。

## 最终门禁及适用范围

全量本地回归 **4770 passed / 1 skipped / 149 deselected**，309.05s；唯一跳过为 Windows 不支持的 uvloop，
149 项为未选择的外部数据库测试，本阶段 MySQL 实库结果单独列在上文。两项既有 Pydantic ReadOnly 提示不影响通过。
最终指标相关回归 **79 passed**；机器可读源码/fixture/JUnit 哈希和适用范围见
[本阶段证据](results/shared-metric-catalog-2026-11-17.json)。原始日志、失败记录和私有发布包保留在 ignored data/，不写入文档或公共聚合报告。
已通过全仓 ruff、mypy（215 app 文件）、33 项依赖约束、前端实际函数契约/重复构建/失败保留旧产物和四语言文档链接检查。
Python 3.12.13 来自 `.stage62/.venv`，加载当前工作区；原 `.venv` 的 18 项依赖偏差仍原样保留。

已验证边界是单机固定配置、多进程 MySQL 控制面与 MySQL 只读目标上的合成指标；不是业务口径签收、
生产容量/质量、任意多副本、租户隔离或所有数据库的指标认证。共享 MCP 接线复用该运行时，
本轮多进程 HTTP 演练覆盖 REST JSON/SSE，未另启外部 MCP/stdio 客户端、TLS/代理或 OAuth 链路。
跨表/时间语义、子 Agent 指标动作、Snowflake 等目标验收仍需独立范围与数据条件；未部署、发布或合并。
