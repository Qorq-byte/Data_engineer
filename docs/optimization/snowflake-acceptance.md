# Snowflake 独立验收

状态：**入口已提供，实库尚未验收**。本地 AST 策略测试不能验证驱动、权限、取消、计费或服务版本兼容性。
测试位置：`tests/real_db/test_snowflake.py`；本地策略：`tests/test_db/test_snowflake_source.py`。

## 外部准备

由账号管理员在专用的可丢弃数据库准备 `DE_ACCEPTANCE_FIXTURE` schema 和专用 warehouse，
使用低规格 warehouse、自动挂起、资源监控与平台费用限制；不要把生产账号或数据用于本套件。
管理员需预置以下数据（以下 SQL 是准备说明，测试不会自动运行 DDL 或授予权限）：

```sql
CREATE SCHEMA DE_ACCEPTANCE_FIXTURE;
CREATE TABLE DE_ACCEPTANCE_FIXTURE.ORDERS (
  ID NUMBER NOT NULL,
  NOTE VARCHAR(32) COMMENT 'Nullable acceptance note'
) COMMENT = 'Synthetic acceptance orders';
INSERT INTO DE_ACCEPTANCE_FIXTURE.ORDERS VALUES
  (10, 'a'), (20, NULL), (30, 'c'), (40, NULL), (50, 'e');
CREATE TABLE DE_ACCEPTANCE_FIXTURE.LONG_ROWS AS
  SELECT SEQ4() AS ID FROM TABLE(GENERATOR(ROWCOUNT => 10000));
```

为专用读角色仅授予 warehouse/database/schema 的 USAGE，以及上述两表的 SELECT；不要授予
INSERT/UPDATE/DELETE/CREATE、其他数据库或 schema 访问权限。指定测试用户的默认及显式 role，
确认其继承角色、PUBLIC 和 secondary roles 均不会带入写权限。当前适配器仅接受密码认证和未引号的命名空间，
账号策略必须允许此认证方式；SSO/MFA/key-pair/OAuth 尚未实现。

## 运行

安装 `.[snowflake]` 并对部署解析出的依赖另行锁定（基础 constraints.txt 尚不覆盖此 extra）。
仅在已批准本次云计算费用的专用环境，设置以下进程环境变量；密码应通过秘密注入，不放进命令行或报告：

- `DE_REAL_SNOWFLAKE_TESTS=1`（独立于其他数据库的开关）。
- `DE_SNOWFLAKE_ACCOUNT`、`DE_SNOWFLAKE_USER`、`DE_SNOWFLAKE_PASSWORD`。
- `DE_SNOWFLAKE_WAREHOUSE`、`DE_SNOWFLAKE_DATABASE`、`DE_SNOWFLAKE_SCHEMA=DE_ACCEPTANCE_FIXTURE`、`DE_SNOWFLAKE_ROLE`。
- `LLM_MOCK_MODE=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True`、`RAG_EMBEDDING_PROVIDER=mock`。

```powershell
.venv/Scripts/python.exe -m pytest tests/real_db/test_snowflake.py -q --tb=short -o junit_family=xunit1 --junitxml=data/snowflake-acceptance/report.xml
```

没有启用开关时应为 4 skipped；启用却缺设置/驱动或角色过宽应失败，不能跳过后称验收通过。
超时/取消案例使用三表交叉运算，只运行这套固定小批次。服务端语句超时、行数截断和请求超时均不是费用上限。
测试只读数据；唯一原生 INSERT 是用于证明数据库拒写的负例，若角色配置错误可能插入 ID=60，须在丢弃 fixture 前检查。

## 验收标准和归档

1. 元数据中的表/列名、类型、NULL、注释与 namespace 与 fixture 一致；外键仍不在支持范围。
2. API 直接执行与固定五节点 Harness 读到 10–50；max_rows=3 显示截断，LIMIT 2 无截断；无真实模型调用。
3. 应用拒绝跨 schema/database、quoted 小写越界、多语句、写操作和表函数。
4. 绕过应用解析的原生 INSERT 被只读角色拒绝，行数仍为 5。
5. 截止时间与 task.cancel() 后按 last_query_id 查询服务端终态，确认不再运行，再验证同一连接可读。
6. `ConnectionFactory.aclose()` 清理登记；取消无法确认时适配器应退役连接，该故障分支尚无实库故障注入证据。

归档 JUnit（含驱动与服务端版本）、Git/workspace diff 标识、依赖锁定文件、账号角色权限的脱敏审核、
warehouse 规格/超时参数、运行时间、4 项测试结果及取消终态；不要归档密码或连接 URL。
一次通过只认证这个部署、认证方式和版本，不能推广到所有 Snowflake 账号。

标识符规则核实于 2026-11-17：[Snowflake 官方标识符规范](https://docs.snowflake.com/en/sql-reference/identifiers-syntax)。
未引号名称折叠为大写；引号名称保留大小写，适配器按此执行 namespace 比较。

## 本轮证据

2026-11-17 本地策略测试先出现 4 failed / 47 passed；修复 quoted namespace 与表函数边界后为 51 passed。
实库未运行：未提供专用账号、fixture 和本批次费用条件。后续以真实 JUnit 替换此待验收状态。
