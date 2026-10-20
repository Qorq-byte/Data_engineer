# 私有工作台安全配置

本轮MVP采用私有共享工作台，业务资源由管理员共享，不是多租户系统。所有 `/api/v1` 业务接口要求有效Bearer JWT且用户ID在 `ADMIN_USER_IDS` 中；登录/注册/个人账户接口保留各自认证规则。健康与静态页面公开。

1. 在未跟踪的 `.env` 设置至少32字符的随机 `JWT_SECRET`，默认值和短值在所有环境拒绝启动。不要把真实值写入配置模板、文档或Git。
2. 配置随机 `REGISTRATION_TOKEN`，调用 `/api/v1/auth/register` 时通过 `X-Registration-Token` 请求头提供邀请。未配置邀请即禁止注册，避免首次注册者自动占据管理员ID。完成受控开户后可清空邀请；管理员ID以实际账户ID为准写入 `ADMIN_USER_IDS`（JSON数组）。
3. `CORS_ORIGINS` 为明确的前端来源JSON数组。默认只允许本机8001；反向代理部署应配置自己的来源。
4. JWT要求exp/iat/sub/username，并验证签名和过期。移出管理员名单会阻断该身份的业务访问；需要全面撤销已有令牌时轮换签名密钥。

SQL执行只接受单条经过AST检查的只读查询；非ANALYZE的EXPLAIN仅在内部查询安全时允许。写入CTE、多语句、锁、外部表函数、未知UDF和可执行数据库注释均拒绝。为避免数据库与解析器的词法差异，含反斜杠、嵌套注释、`--`后无ASCII空白/控制字符的SQL也拒绝；普通注释仍可用。DuckDB连接禁用外部访问并拒绝文件表名。节点 `allow_write` 不授予写权限。SQLite在执行期间另有authorizer和progress deadline；DuckDB有interrupt deadline；异步驱动有应用超时，MySQL超时会关闭连接。仍应使用数据库原生只读账户，并在真实PG/MySQL环境验证服务端超时与取消。

行数由节点配置读取并受 `MAX_RESULT_ROWS` 限制；超时受 `STATEMENT_TIMEOUT_MS` 限制。数据库读取行数上限不等于查询扫描量上限。SQLite执行连接由应用管理，执行时临时安装的authorizer/progress handler不支持与调用者自行设置的回调共存。

领域ID仅允许字母开头、字母数字下划线短横线、最多64字符，resolve后必须位于domains目录。设置响应不返回API key明文或加密值。LLM加密密钥当前仍由JWT_SECRET派生；轮换前应准备重新配置提供商密钥，不宣称透明密钥迁移。

尚未提供用户级资源隔离、UDF授权、写SQL审批或生产PG/MySQL验收。不能通过增加管理员ID就将本系统视为多租户服务。
