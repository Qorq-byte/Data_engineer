# ClickHouse 只读接入（阶段 67）

应用通过 HTTP 或 HTTPS 连接单个数据库。连接配置使用 `host`、`port`、`user`、`password` 和 `database`。TLS 端点通过 API 配置 `secure: true`，默认端口 8443，证书按系统信任链验证。回环 HTTP 夹具使用独立映射端口，默认 HTTP 端口为 8123。

账号需仅拥有目标表的 SELECT 权限。原生用户配置需启用 `readonly=1`、`cancel_http_readonly_queries_on_client_close=1`，并设置正数的 `max_execution_time`。连接会检查这三个原生设置。执行只接受一个 SELECT，拒绝跨库表、外部表函数、请求内的 SETTINGS 和 FORMAT。查询返回有限行数，单次响应读取上限 8 MiB。暂未验收集合运算、LIMIT BY 和 EXPLAIN。

本地验收使用 ClickHouse 25.8.9.20 官方 Linux 二进制、一次性回环数据库和合成数据。检查元数据与列可空性、直接 SQL 执行、Harness 查询、原生拒绝写入、应用拒绝不安全语句、行数截断、超时、取消与后续连接复用。Harness 生成阶段使用确定性合成响应。

在 Linux x86-64 上安装仓库的开发依赖后，执行 `bash scripts/run_clickhouse_acceptance.sh <clickhouse-binary>` 可复验。脚本启动回环实例，使用独立临时数据目录与合成账号，完成后停止进程。常规实库 CI 继续覆盖 MySQL 与 PostgreSQL。ClickHouse 用例仅在明确启用独立夹具时执行。
