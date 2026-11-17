# 阶段 67.1：Snowflake 连接器实现切片

日期：2026-11-12。此记录覆盖连接代码和本地契约接线，不代表 Snowflake 实库验收完成。

2026-11-17 补充：新增 51 项本地策略回归并修复 quoted namespace 大小写比较与表函数绕过；
新增四项显式 opt-in 的实库套件，但无凭据时仅跳过，不计实库通过。
完整 fixture、命令与权限/费用/取消验收条件见 [Snowflake 独立验收](snowflake-acceptance.md)。

## 已实现

- `ConnectionFactory` 可创建、登记、关闭 Snowflake 会话；能力报告区分连接器已实现与驱动是否安装。
- 工作台连接表单提供 account、user、password、warehouse、database、schema 与可选 role 字段；空白 role 不传给驱动。
- 可选 extra `.[snowflake]` 提供 Snowflake Python Connector 5.x。它不属于基础运行时依赖，也未纳入当前 `constraints.txt` 快照。
- 连接配置显式要求 account、user、password、warehouse、database 和 schema；可选 role/host。database、schema、warehouse 目前只接受未加引号的常规标识符。
- 会话启用 `ABORT_DETACHED_QUERY`，使用连接超时、请求超时、单个结果预取线程和服务端语句超时；要求部署使用 Snowflake 原生只读角色。
- 查询只接受单条 SELECT、CTE 或集合运算查询，经 Snowflake 方言 AST 校验；阻止越出配置 database/schema 的表引用，追加 `max_rows + 1` 限制以识别截断。
- 应用端查询截止时间会在提交、状态轮询和结果读取边界检查；超时或请求取消会发起服务端取消，并确认终态，否则关闭该连接会话。驱动网络调用仍受其 10 秒请求超时约束。
- 通过 `execute_async`、查询 ID 状态轮询和结果绑定完成非阻塞等待；请求超时或取消时调用绑定参数的 `SYSTEM$CANCEL_QUERY`，并确认查询结束。无法确认时退役本地连接并返回明确错误。
- Schema 读取从目标 database 的 `INFORMATION_SCHEMA.COLUMNS` 与 `TABLES` 读取表/列、类型、可空性、默认值和注释；列数量上限 20,000。
- SQL EXPLAIN 暂不支持。Snowflake 声明的键/外键关系尚未映射为可信关联证据。

## 未完成验收与限制

- 当前没有独立 Snowflake 验收账号或合成 fixture 环境，因此没有连接、元数据、查询、超时、取消、截断和关闭的实库证据；阶段 67.1 尚未验收完成。
- 本轮没有新增或运行自动化测试。
- 密码认证为当前实现范围；密钥对、OAuth/外部浏览器等认证方式未接入。
- schema/table 关系没有作为已验证外键提供，不能仅凭 Snowflake 元数据宣称 JOIN 关系可信。
- SQL 行数上限和超时限制返回量与运行时间，不限制 warehouse 计算额度或扫描字节。
- 可选驱动未包含在默认约束快照。部署前需单独固定依赖并在独立 Snowflake 账户上验收。

## 下一步

提供一次性 Snowflake 环境、合成表和只读账号后，按阶段 67 验收清单核对当前 database/schema、权限范围、元数据、只读策略、截断、超时/取消及资源释放。没有该环境时，本阶段保持“代码已实现、实库未验证”。

