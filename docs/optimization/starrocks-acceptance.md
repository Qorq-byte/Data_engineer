# StarRocks 只读接入（阶段 67）

连接参数为 `host`、`port`、`user`、`password` 和 `database`。连接器使用 StarRocks 的 MySQL 协议，默认端口 9030，限定内部 `default_catalog` 的一个数据库。远程端点通过 API 使用 `secure: true` 和可校验的服务端证书。使用仅拥有目标表 SELECT 权限的数据库账号。

执行受单语句只读策略、服务器 `LIMIT` 和应用超时限制。禁止跨库表引用和用户查询提示，防止覆盖连接器设置的 `query_timeout`。超时或取消时，连接器使用同账号的独立会话发出 `KILL QUERY`，并弃用原连接。使用流式游标读取有限结果。外部 catalog 与 EXPLAIN 需额外验收。

验收使用官方镜像 `starrocks/allin1-ubuntu@sha256:7f6041f6d537162a2627668565630e3204756f5e85b60944de3acc2d3abd96f8`，服务端报告 `4.1.7-b81ff9c`。仅映射容器 9030 到回环动态端口。启动后，在空夹具使用管理员执行 `tests/fixtures/starrocks/seed.sql`，再运行：

```bash
DE_REAL_DB_TESTS=1 DE_REAL_STARROCKS_PORT=<mapped-port> \
LITELLM_LOCAL_MODEL_COST_MAP=True RAG_EMBEDDING_PROVIDER=mock \
python -m pytest tests/real_db/test_starrocks.py -q --strict-markers
```

实库用例覆盖元数据、直接 SQL、Harness、原生拒绝写入、截断、超时和取消后复用。Harness SQL 使用确定性合成响应。
