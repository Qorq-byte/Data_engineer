# 真实PostgreSQL/MySQL验收

第40阶段增加独立的 `real_database` 套件，使用真实asyncpg/aiomysql、SELECT-only账号和专用fixture库。仅模型生成步骤注入确定性SQL响应；数据库、Schema、锁等待和执行均使用真实服务。本套件不衡量模型质量，也不调用LLM/embedding。

## 本地运行

需本机Docker Linux引擎及可用Python3.12环境，先显式准备官方测试镜像：

```powershell
docker pull postgres:16
docker pull mysql:8.4
python -m scripts.run_real_db_tests --output data/real-db-acceptance/report.json
```

工具只使用已缓存镜像，启动时解析并固定实际image ID；postgres:16/mysql:8.4是版本系列，不是永久不变的完整版本。每次报告记录实际服务器版本、镜像/容器ID、端口、测试数及清理状态。首次可用 `--filter empty_result` 做定向检查，完整验收应不指定filter。

两个容器分别限1 CPU、768MiB/1024MiB内存，数据目录为512MiB tmpfs，映射环回随机端口，没有宿主数据挂载。用户名/密码为源码公开的disposable fixture专用值，不读取.env或应用业务连接配置。数据库固定de_stage40_fixture，已有表时拒绝初始化；初始化管理员与应用stage40_reader分离，应用仅SELECT。PostgreSQL覆盖public命名空间及同名约束的其他schema，MySQL按实际DATABASE()选择来源。

本地pytest子进程使用新工作目录和环境白名单，只把测试端口及固定mock开关传入，源码通过绝对配置/PYTHONPATH定位；不继承业务DATABASE_URL、MYSQL_*或模型密钥，工作目录无宿主.env。JUnit和日志写在明确output旁，临时workspace在子进程结束后清理。

finally先发现本次专用随机标签的完整ID，再逐个核对ID/标签后删除，覆盖docker run已创建但启动失败的情况。身份不符拒绝删除并返回非0；不按容器名称前缀或通配符清理，不删除镜像缓存。报告旁保存pytest日志和JUnit，关闭失败不会作为整体成功。进程强杀、Docker守护进程失联等仍需根据本次报告/标签人工核对，不保证掉电清理。

也可向独立服务直接运行套件，必须显式设置 `DE_REAL_DB_TESTS=1`、`DE_REAL_PG_PORT`、`DE_REAL_MYSQL_PORT` 并提供同样的空fixture库/测试账号。主机固定127.0.0.1，拒绝猜默认3306/5432；未启用时跳过，已启用但缺端口/服务会失败。不要指向业务数据库。普通离线命令为：

```powershell
pytest -m "not real_database"
```

## 检查范围和修复

每库19项：普通/空结果列、Decimal/NULL、返回行上限、应用只读与数据库直接写拒绝、SELECT账号主外键/复合列配对/索引/namespace、真实异步EXPLAIN、EXPLAIN错误后的会话复用、真实锁等待下普通执行/EXPLAIN的超时与取消、单句柄并发、关闭等待真实在途查询、重连不转用旧目标、取消未发布候选的连接/池释放、关闭注册/缓存/池以及正式Harness链路。

PostgreSQL约束内省改用pg_catalog关系OID和数组序号匹配，保留information_schema对可见表的筛选；避免SELECT-only角色看不到table_constraints，也避免同名约束/复合外键错配。空结果列从prepared statement属性读取，保留现有结果结构。

异步EXPLAIN通过真实驱动执行；EXPLAIN和最终查询共享该节点的异步截止，取消MySQL关闭句柄后需显式重连，旧请求不会静默改用新句柄。MySQL EXPLAIN的优化器Note可能含完整SQL，因此只在已持有租约的该会话暂设sql_notes=0并恢复；不跨await修改进程warnings。恢复失败则丢弃连接。此处理只针对EXPLAIN备注，不是全局屏蔽数据库错误/警告。

重连等待旧租约时，如果任务取消，新建立但尚未发布的候选也会释放。注册连接和候选共用shield/drain收尾，重复取消不应提前丢弃仍在关闭的句柄。真实测试分别验证PostgreSQL取消后可复用，MySQL关闭后显式aclose/重连，以及取消重连时旧目标保持原身份。

## CI及边界

原MVP作业明确运行离线标记之外的测试；独立 `.github/workflows/real-databases.yml` 每次push/PR启动PostgreSQL16和MySQL8.4服务，显式开启真实套件，失败阻断该工作流，并保存JUnit。不能只看MVP绿色而忽略真实服务作业；阶段验收核对同一精确SHA的两个工作流。

这只证明固定公开小表和实际记录的版本/驱动组合。PostgreSQL内省仍限public，跨schema FK/生产search_path及临时对象防护不据此宣称完整；索引列/估计行数仍有现有简化。阶段41为普通MySQL SELECT增加服务端N+1结果上限，保留较小LIMIT及原始列名；1k/10k合成结果证明驱动缓冲行数有界，详见[报告](stage-41.md)。非整数字面量/参数化顶层LIMIT或无法安全定位时明确失败，不无界回退。EXPLAIN独立处理；该行数限制不构成服务器计算、网络字节或任意行宽的内存上限。EXPLAIN解析仍为通用启发式，不保证完整MySQL计划分析。取消释放客户端资源不证明服务端工作或计费立即撤销。

未覆盖生产负载、大Schema、网络分区/进程强杀恢复、TLS/高可用、连接池容量调优、其他服务器版本及业务数据。Harness固定节点顺序/权限/只读、线上协议、LLM用量和费用契约保持。
