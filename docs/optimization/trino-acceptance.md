# Trino 只读接入（阶段 67）

连接参数为 `host`、`port`、`user`、`database`（`catalog.schema`）及可选 `password`。不带凭据的 HTTP 限于回环地址。远程连接使用 `secure: true` 和可校验的 HTTPS 证书，支持 HTTP Basic 认证。部署需配置服务端只读账号与目标 schema 的 SELECT 权限。

连接器通过同源的 Trino statement 接口分页，并在超时或取消时向已获得的续页端点发送 DELETE。提交时附带服务端查询运行上限。仅允许单条 SELECT，并限定表引用的 catalog 与 schema。执行受结果行数和 8 MiB 总响应预算约束。集合运算、EXPLAIN 和部署专用认证机制需额外验收。

验收使用 Trino 484 的官方容器和 `tests/fixtures/trino`。将该目录的 `fixture.properties`、`access-control.properties`、`rules.json` 分别挂载到 `/etc/trino/catalog/fixture.properties`、`/etc/trino/access-control.properties`、`/etc/trino/stage67-rules.json`。只将容器 8080 映射到回环动态端口。启动后以夹具管理员 `stage67_admin` 执行 `seed.sql`，并运行：

```bash
DE_REAL_DB_TESTS=1 DE_REAL_TRINO_PORT=<mapped-port> \
LITELLM_LOCAL_MODEL_COST_MAP=True RAG_EMBEDDING_PROVIDER=mock \
python -m pytest tests/real_db/test_trino.py -q --strict-markers
```

实库用例检查命名空间、列元数据、原生只读、结果截断、Harness、超时和取消后的连接复用。Harness SQL 来自固定合成响应。测试账号和配置仅用于独立夹具。
