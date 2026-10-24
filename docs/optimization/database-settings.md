# 数据库连接能力与设置契约

阶段24起，GET /api/v1/settings/database与聚合GET /settings的database段只读报告ConnectionFactory能力；旧dialects_enabled/通用池大小/连接超时不是生效来源。PUT固定409 DATABASE_SETTINGS_READ_ONLY，不消费body、不读取或更新旧存储，不关闭/重连任何连接。旧客户端须停止该写请求；管理员401/403边界保持。

响应status=ok、mode=read_only、editable_fields=[]、legacy_stored_settings_applied=false。connectors包含11个type，每项implemented、driver_module、module_discoverable、management、registered_connections；connection_pool_size/connection_timeout_s均null，表示没有已接通的通用值，不是0或5/10。scope指当前进程注册表，configuration_source指实现和驱动默认。内部报告失败503 DATABASE_CAPABILITIES_UNAVAILABLE，固定消息不泄漏异常内容；不回退默认能力。专用GET不依赖设置数据库、不探测网络/创建连接/导入驱动；聚合GET仍会处理其他设置段，可能因其他依赖失败。

## 能力边界

app/db/connections.py:_connect实际连接实现为SQLite、DuckDB、PostgreSQL、MySQL；其余Snowflake、StarRocks、BigQuery、Redshift、ClickHouse、Databricks、Trino明确NotImplementedError。SQL翻译/Schema方言设计与连接实现是不同能力，四类实现也不代表已在部署环境完成真实服务联调。

SQLite/DuckDB原生单连接；PostgreSQL为asyncpg单异步连接；MySQL每次建池后获取并持有一个连接，池随连接关闭，不是每个查询从共享池重新获取。并发访问仍沿用现有连接lease。没有接入旧表通用pool_size或timeout；本阶段不改变这些生命周期或默认参数，不测吞吐和容量。

module_discoverable由importlib.util.find_spec得出：true仅说明模块可发现，不保证能导入、依赖完整、服务可达或能执行SQL；false表示未发现；null表示发现未知或未实现类型不适用，以implemented区分。报告不回显主机、端口、库名、ID、别名、口令。registered_connections仅统计当前_connections中且有_infos元数据的句柄；不调用健康查询，失效句柄在显式清理前也可能仍计数。进程/worker各自独立，无持久快照或广播。

## 使用与恢复

先安装实际连接类型所需驱动，再在数据库连接管理入口传入该类型实现消费的参数（SQLite/DuckDB path；PostgreSQL/MySQL host/port/user/password/database）。不要通过旧设置表禁用方言或调整池/超时；不支持的参数不会因保存而生效。已有认证/持久化存储连接池不在此ConnectionFactory视图范围内。

页面显示连接实现状态、驱动发现、管理方式和注册数，只允许重新读取。读取中隐藏旧值，503/畸形200显示未知，修复环境后重读；迟到旧响应不覆盖新状态。模块发现和注册数不能替代显式连接/执行验证。未实施真实PostgreSQL/MySQL服务联调、多进程一致性或压测；原有7项构建期依赖告警/类型存量保留。详见[阶段24报告](stage-24.md)。
