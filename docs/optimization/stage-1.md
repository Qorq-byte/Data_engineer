# 阶段1：执行与访问安全

状态：已提交并推送，[24c396f](https://github.com/Qorq-byte/Data_engineer/commit/24c396fcf0bc4e788604d0991ca76a386e18558b)，远程SHA核对一致。独立分支codex/mvp-hardening，原main工作区未修改。

## 完成内容

- SQL改为单语句AST只读检查，检查嵌套写入/INTO/锁/会话赋值/外部函数和文件表名；补齐MySQL可执行注释、无空白双横线、Unicode空白、嵌套注释等解析差异防线；节点allow_write无法授权写入。
- DuckDB关闭外部访问；SQLite执行期间authorizer、progress deadline与busy_timeout，DuckDB interrupt，异步驱动应用deadline；行数从正确配置读取并受全局上限限制，PG不再先fetch所有行，DuckDB列名规范化。
- 所有业务路由复用JWT管理员依赖；私有共享工作台范围明确。所有环境拒绝默认/短JWT签名密钥，要求标准token claims，注册邀请默认关闭，CORS明确来源。
- 领域ID和resolve路径约束，完整设置出口也去除密钥/密文；功能测试用真实签名token，不用全局鉴权覆盖。领域CRUD测试改用临时配置目录，避免测试生成文件写入源码树。
- MCP迁移到FastMCP公开接口，修复失效导入/业务调用/资源序列化；真实SQLite工具回归断言结果，MCP默认loopback/stdio。依赖声明与实际使用的公开API主版本匹配。

## 验证记录

环境：Windows，已有Anaconda Python3.13.9，PYTHONPATH指向原项目已有site-packages；LLM_MOCK_MODE=1、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True；不连接真实模型或业务数据库。

- `python -m pytest -q --disable-warnings --tb=short`：**2893 passed，4 warnings，84.72秒**，无跳过或失败。日志见[stage-1-pytest.txt](results/stage-1-pytest.txt)。
- `python -m pytest tests/test_mcp/ -q --tb=short`：184 passed，8.80秒；新MCP单文件35 passed。
- 独立安全复核：14条对抗SQL拒绝、6条普通SELECT/CTE/注释/EXPLAIN接受，DuckDB external_access=false；相关payload另纳入正式测试。
- 新增安全模块、认证客户端、安全回归测试及MCP文件Ruff通过；全仓Ruff仍有存量问题（不关闭规则，继续清理）。此前全仓192项，首次阶段检查153项；最终计数见同目录日志。
- 严格mypy阶段过程中报告285项/62个文件；最终复查超过180秒没有输出，终止并记录为未通过，保留两份日志。后续重写查询主线和稳定性阶段继续定位并收敛，未关闭任何检查规则。
- `git diff --check`通过。提交只选择源码、测试、脱敏评估和文档；不提交.env、data、缓存、临时日志或本地凭据。

## 调整及限制

从原计划增加了注册邀请和所有环境拒绝弱JWT密钥：因为默认development同样可被公开密钥伪造，单加管理员依赖不足以关闭P0风险。增加词法差异与DuckDB外部访问保护，依据安全复核可重复的绕过输入。

真实PG/MySQL服务端只读账号/事务、网络取消和MCP HTTP传输鉴权未验证。SQLite/DuckDB仍有同步工作，不能把阶段1的CPU查询deadline说成已证明事件循环可及时取消；并发与取消在阶段4验证。MVP不支持多租户、任意UDF或写SQL审批。

下一阶段：固定YAML/WorkflowRunner统一JSON与SSE，真实失败状态、请求预算、会话/db_id和追踪。
