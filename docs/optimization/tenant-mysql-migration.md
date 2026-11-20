# MySQL 租户存储迁移验收

2026-11-19。演练使用仓库的一次性 MySQL 测试环境和合成数据。

## 迁移顺序

停掉使用旧版表结构的 worker，先取得控制库的升级前备份。新版初始化使用 MySQL 命名锁串行化同库启动。锁由执行 DDL 的连接持有，连接中断时由 MySQL 释放。先为 `domains` 添加租户列和复合唯一键，再创建缺失的子表。保留旧唯一键和外键，直到替代约束建立。既有行使用默认租户 1。初始化可在同一数据库重复运行。

回退演练使用升级前的表定义和数据在独立库中恢复，然后检查旧唯一键和原始数据。MySQL 的 DDL 分步提交。若要回退生产版本，需要停止新版写入并从升级前备份恢复。升级后的写入需要单独处理。

## 可复验场景

- 迁移完整旧设置和领域表，重复初始化后确认租户 1 仍可读取原始设置、领域、术语和规则。
- 两个租户写入同名设置和规则。第二个租户缺少对应父领域时，MySQL 拒绝规则写入。删除第二个租户的领域只级联清理该租户的规则。
- 迁移缺失领域子表的旧控制库，验证两个独立连接并发初始化。
- 将升级前快照恢复到独立临时库，核对原始行和旧唯一键，再重新升级。

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_mysql_migration or settings_recovery or feedback_storage or shared_metric_catalog' --output data/tenant-mysql-acceptance/verified.json
```

控制库的旧库样本覆盖设置和领域表。当前发布范围仍为单管理员组。

## 实测结果

MySQL 8.4.13 共通过 24 项，其中 3 项为新增迁移与恢复用例，其余覆盖设置恢复、反馈持久化和共享 worker 指标。两个一次性容器均已清理。相关本地回归 76 项通过。报告和 JUnit 保存在 `data/tenant-mysql-acceptance/`。

## 认证库迁移与入口验收

认证库沿用同一套 MySQL schema 锁。旧 `users` 表补齐 `tenant_id`、`idx_tenant` 和缺失的头像列。租户列和索引结构通过校验后，账号接口才使用该连接池。遇到不兼容索引时，修复索引定义后可重跑。既有账号保留 ID 与凭据，租户默认值为 1。

旧 JWT 缺少租户声明时按租户 1 处理。显式租户声明必须为正整数。该轮按默认租户管理员白名单验收 REST 与指标 MCP。已有账号仍可登录并读取自身资料。账号迁移测试使用真实 MySQL 登录签发令牌，并用 SQLite 指标数据验证请求路径。

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_auth_mysql or tenant_mysql_migration' --output data/tenant-auth-acceptance/final.json
```

MySQL 8.4.13 上新增认证验收 3 项和既有控制库迁移验收 3 项全部通过，两个临时容器已清理。相关本地回归 71 项通过。

NL2SQL FastMCP 普通工具和资源的认证由 HTTP 层统一执行。[MCP HTTP operator gate](metric-mcp-transports.md#mcp-http-operator-gate)

## 数据源连接、文档检索与用量审计

连接工厂以租户和数据库 ID 登记连接、元数据与建连锁。查询目标解析只返回当前租户的连接。请求持有另一租户或已替换的句柄时，执行节点拒绝使用该句柄。关闭单个连接只释放当前租户的资源，进程停机会释放所有连接。

本地用两个租户的同名 SQLite 连接验证并发登记、Schema 读取、只读执行、替换及关闭。文档检索用真实 LanceDB 和 BM25 验证同名文档的检索、替换和清理，向量由测试嵌入生成。审计通过查询审计写入流程验证同一查询 ID 的读写与保留策略，默认租户继续使用原审计文件。

MySQL 8.4.13 迁移回归 4 项通过。新增场景将旧版数据源配置归入默认租户，再验证第二个租户可以用相同 ID 创建、更新和删除自己的配置。两个临时容器已清理。

```powershell
python -m scripts.run_real_db_tests --filter tenant_mysql_migration --output data/tenant-connection-acceptance/mysql.json
```

该轮双租户场景使用受信运行时上下文，工作台和 MCP HTTP 按默认租户验收。

## RAG 索引、恢复状态与查询缓存

默认租户保留现有 LanceDB 和 SQLite FTS5 表名。其他租户在 Schema、指标和文档索引中使用各自的表。文档删除按当前租户删除正文及分块，存储异常由删除接口返回。早期未分区的 Schema 和指标索引按默认租户旧格式读入。如果曾用共享表写入其他租户的数据，发布前需要从原始数据按租户重建索引。

Schema 刷新按租户和数据库 ID 加锁。进程内待刷新状态、恢复失败、版本和最后刷新时间按租户记录。默认租户继续识别原版中断标记，其他租户的标记在文件名中携带租户 ID。索引重新打开时只用当前租户的标记决定该租户哪些数据库需要重新刷新，发布封存仍检查全部中断标记。

查询对缓存的内存 LRU、命中统计和 LanceDB 向量查询按租户隔离。容量上限应用到每个租户，清理内存缓存时保留向量记录。查询对缓存尚未接入主查询流水线。

验收使用两个受信租户、两个同名 SQLite 数据库、真实 LanceDB 和 SQLite FTS5，以及测试嵌入。覆盖同名 Schema、指标和文档的读写与清理，Schema 写入和补偿失败后重开索引的恢复范围，以及同文问题命中不同租户的缓存。

相关本地回归 725 项通过。一次性 MySQL 8.4.13 上的默认租户不可变 worker 启动、重启和维护拒绝回归 2 项通过，报告保存在 `data/tenant-rag-acceptance/immutable-workers.json`。临时容器已清理。

## 多租户迁移回退演练

1. 从迁移前提交 `c92fe9a51c5d256a981f84365e6e31bcd55bd0cf` 解包旧代码。启动两个旧 worker，注册并登录合成账号，执行只读查询并确认会话在两个进程间可见。停掉旧 worker，备份控制库全部基础表的 DDL 与行数据，以及原发布清单和索引文件。
2. 从当前工作区启动两个候选 worker。候选版迁移同一控制库，验证原账号和领域数据归属默认租户，保留旧 JWT 的默认租户访问。使用候选发布命名空间写入新会话，验证第二租户的账号登录和工作台拒绝，再停止全部候选 worker。
3. 将升级前的逻辑备份导入独立 MySQL 实例的空库，逐表核对 DDL、列元数据和行数据。将旧发布清单和索引按原路径恢复，通过回环代理保持旧控制库地址。重启两个旧 worker，验证 readiness、原账号登录、原设置、索引状态和会话。确认候选账号与会话没有进入回退状态，执行新查询并由另一进程读回追加的结果。

```powershell
python -m scripts.run_real_db_tests --mysql-recovery --filter tenant_migration_rollback --output data/tenant-rollback-acceptance/final.json
```

演练使用同一个合成库承载认证和控制面表，模型传输使用合成响应，查询目标留在源 MySQL，由只读账号访问。正式回退还需涵盖独立部署的认证库、控制库与外部存储。备份恢复点在升级前，候选期间新增的数据需要按业务规则另行保留或合并。演练保留签名密钥和旧 JWT，候选期间签发令牌的撤销仍需专项验收。

MySQL 8.4.13 实库回退演练通过，三个阶段共启动六个 worker。相关 MySQL 回归 8 项通过，本地回归 32 项通过。实库报告保存在 `data/tenant-rollback-acceptance/`，两批测试创建的临时容器均已清理。

## 租户归属变更与 JWT 撤销

认证库迁移为 `users` 增加默认值为 `0` 的 `token_version`，并在同一 MySQL schema 锁内创建和校验 `BEFORE UPDATE` 触发器。迁移账号需要 `TRIGGER` 权限。修改 `tenant_id` 时，触发器在同一条语句内推进版本，现有账号可通过一次原子更新完成手动撤销。

```sql
UPDATE users SET token_version = token_version + 1 WHERE id = ?
```

REST 和 MCP HTTP 在每次受保护请求时读取当前账号的租户与令牌版本。旧 JWT 缺少租户和版本时分别按 `1` 和 `0` 核验。租户变更、手动撤销或账号删除一经提交，后续请求使用旧 JWT 返回 `401`。已打开的 MCP 会话也会在下次请求时重新核验。认证库不可用时返回 `503`。该轮还验证了非默认租户可登录和访问自身账号。

独立 MCP HTTP 通过 lifespan 初始化认证库，挂载到已初始化的工作台时复用其认证池。本地 stdio 的宿主信任沿用现有规则。版本核验发生在请求入口，已经通过核验的在途请求和已打开的 SSE 流沿用原生命周期。

还原较早的认证备份会还原其中的令牌版本，恢复到早期代码还会移除按版本核验。回退后如需继续保证先前撤销的令牌失效，需在开放流量前处理签名密钥和依赖该密钥的发布清单及配置。

MySQL 8.4.13 三批最终共 9 项通过，分别覆盖认证与往返迁移、MCP 网络和共享会话权限、旧版回退。认证验收通过实际接口改密和注销，并确认注销后的令牌失效。相关本地回归 785 项通过。报告保存在 `data/tenant-revocation-acceptance/`。

## 查询领域知识隔离

查询服务在生成 SQL 前捕获当前租户的领域配置。默认租户继续使用现有 YAML。其他租户的领域、术语和规则由同一条租户限定的 MySQL 语句读取，领域缺失时在调用模型前返回 `400`。`default` 可表示无需领域指导，认证后的查询仍受现有连接与 SQL 执行约束。领域存储不可用时返回 `DOMAIN_CONFIG_UNAVAILABLE`，JSON、SSE 与 MCP 使用同一个错误码。

已审批规则的 SQLite 文件和缓存按租户分开。默认租户保留原文件，其他租户使用带租户 ID 后缀的文件。同一规则 ID 可以由不同租户独立审批与撤回，查询按实际解析到的领域名称读取审批规则。

该轮双租户验收使用 MySQL 真实登录、同名领域和不同的 SQLite 数据。默认租户走 REST，第二租户在验证 JWT 后通过受信上下文调用同一查询服务，模型使用脚本化响应。两边分别检查最终提示、校验告警和执行结果。

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_auth_mysql or tenant_mysql_migration' --output data/tenant-query-domain-acceptance/final.json
```

最终本地相关回归 444 项通过，MySQL 8.4.13 实库回归 9 项通过，临时容器已清理。实库用例还覆盖同一领域的大小写变体、访问仅属默认租户的领域及存储不可用。

## 领域管理隔离

非默认租户的领域管理和查询共用 MySQL 领域快照。领域、术语和规则的增删改查及领域检测均按当前租户访问。MCP 的 `nlsql://domains/` 资源在受信租户上下文中也从同一目录读取。默认租户沿用现有 YAML 路径与接口行为。

迁移增加领域的数据库提示和术语映射的表名、条件、精度字段，旧值和旧 YAML 保持兼容。新增或改名术语和规则时锁定同租户的父领域。重复标识返回 `409`，改名冲突时原记录保留。删除领域时，由租户复合外键清理它的术语和规则。存储不可用时，非默认租户的管理接口返回 `503`。

管理变更提交后，下一次查询能读取更新后的领域配置。非默认租户的管理写入当前只更新 MySQL 目录，独立的指标 RAG 索引同步仍待接线。该轮双租户管理用例使用真实 MySQL 登录及受信测试入口，后续已改用正式管理员准入。

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_domain_management or tenant_auth_mysql or tenant_mysql_migration' --output data/tenant-domain-management-acceptance/final.json
python -m scripts.run_real_db_tests --mysql-recovery --filter tenant_migration_rollback --output data/tenant-domain-management-acceptance/rollback.json
```

本地相关回归 512 项通过。MySQL 8.4.13 的领域管理、认证和迁移回归 10 项通过，新增字段后的完整旧版回退另有 1 项通过。临时容器已清理。

## 运行时模型设置隔离

非默认租户的模型列表、默认模型、备用模型和输出上限从其 MySQL 设置记录恢复。模型改动在租户设置行锁内合并并准备，提交后才更新进程中的路由。每次查询读取当前的持久化配置，进程重启后可重新构建，也能读取其他 worker 已提交的变更。默认租户沿用既有的 LiteLLM 路由与环境配置。

非默认租户需要配置 OpenAI 兼容端点、模型和自己的加密密钥。客户端按请求设置端点和认证头，禁用继承的代理配置与自动重定向。模型设置不可用时，需要模型的查询返回 `LLM_CONFIG_UNAVAILABLE`，指定租户无权使用的模型返回 `LLM_MODEL_UNAVAILABLE`。确定性 `metric_query` 保持零模型调用。共享 worker 的同名供应商限流按租户计数，模型并发上限仍由部署统一控制。

该轮双租户验收使用真实 MySQL 登录、独立 SQLite 数据和模拟模型端点。正式管理员准入与模型出站限制见后续记录。

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_llm_runtime or mysql_rate_limit or tenant_auth_mysql or settings_recovery' --output data/tenant-model-acceptance/verified.json
```

本地相关回归 1,334 项通过，最终传输改动后定向复跑 664 项通过。MySQL 8.4.13 最终组合 25 项通过，临时容器已清理。

## 租户管理员与 REST、MCP 入口

该轮以单 worker 部署验收 `TENANT_ADMIN_USER_IDS` 按租户和用户 ID 启用管理员。每次请求仍由 MySQL 核验账号租户和令牌版本。REST 仅在已隔离的查询、元数据、领域、会话读取和模型设置路由中接受非默认租户管理员。数据源由部署端预配。

MCP Streamable HTTP 与 SSE 会话绑定租户、用户 ID 和令牌版本。跨管理员复用会话 ID 返回 `404`，撤销令牌后需要重新登录和建立会话。独立 MCP HTTP 服务启用租户后会加载控制库和配套数据源，并关闭自己打开的连接。

模型请求按 `TENANT_LLM_ENDPOINTS` 核对该租户的精确 HTTPS base URL。发送时校验 DNS 返回的每个地址，连接固定到通过检查的公网 IP，TLS 继续校验批准的域名。部署使用共用的 HTTP 向量服务时，非默认租户的 Schema、指标与文档查询使用各自的本地 BM25 索引。公网供应商使用真实密钥的验收留待目标部署。

该轮验收使用真实 MySQL 账号、同名只读数据源和模拟模型响应，覆盖不同租户结果、未授权账号、跨租户会话、域名与路径不匹配及混合 DNS 应答。[配置示例](../usage.md#tenant-operator-setup)

```powershell
python -m scripts.run_real_db_tests --filter 'tenant_llm_runtime or tenant_auth_mysql or tenant_domain_management or tenant_mysql_migration or mysql_rate_limit' --output data/tenant-operator-acceptance/final.json
python -m scripts.run_real_db_tests --filter 'metric_mcp_network or actual_startup_restores_connections' --output data/tenant-operator-acceptance/default-workers-final.json
```

MySQL 8.4.13 的租户组合 17 项通过，默认不可变 worker 组合 4 项通过，临时容器已清理。本地 DNS 限制 18 项、最终检索和查询组合 701 项通过。

## 租户浏览器工作台

页面通过 `GET /api/v1/auth/workspace` 核验当前账号及访问级别。租户管理员可使用智能查询、已分配数据源的 Schema、领域知识和模型设置。普通账号进入个人设置。默认租户管理员保有系统入口。Schema 在当前租户的已加载快照中搜索，领域匹配为空时可按无领域查询。尚未配置模型的租户可从空白设置添加获准的模型端点。

查询历史按租户 ID 和账号 ID 保存在当前浏览器。早期无归属的本地记录保留在原存储项中，工作台历史视图只读取所属账号的分区。浏览器跨标签页更换登录后重新核验权限，旧页面的请求仍使用加载时的凭据。

本地相关回归通过 736 项，MySQL 8.4.13 真实登录及租户组合通过 8 项。gstack 浏览器使用合成账号、SQLite 和脚本化模型，覆盖模型保存、Schema 查询、普通账号、同租户与跨租户历史隔离及窄屏。默认管理员查询、重试和历史回归通过。前端可重复构建及产物完整性检查通过。

单机不可变多 worker 的租户模式已通过 MySQL 集成验收。发布、维护和证据见 [v2 租户发布包](immutable-workers.md#v2-租户发布包)。
