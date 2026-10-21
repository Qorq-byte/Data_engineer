# 阶段9执行计划：跨JSON/SSE/MCP查询契约

日期2026-10-21；基准5286a64，阶段8代码及文档CI均成功。继续codex/mvp-hardening独立工作树；原主工作区未提交改动与临时文件不动。

## 目标与依据

已确认：query.py的JSON响应经QueryResponse而SSE done直接发送服务字典；两个入口分别维护超时/容量/内部错误分支；MCP nl_query异常主要为str(exc)，缺少统一错误码/状态，内部异常也可能暴露细节；query_service请求和事件回调为Any。收敛这些实际契约，优先提高调用方判断成功/失败的可靠性，不大范围重构API或变更执行架构。

## 顺序与范围

1. 明确契约：保持QueryRequest和QueryResponse为API边界的Pydantic类，查询服务使用最小结构请求协议，事件名/回调有类型；共享错误负载和分类器使用纯dataclass/TypedDict，不依赖FastAPI。保留validate/run_validation、路由和业务失败HTTP200语义。
2. 实施：JSON与SSE done均经同一QueryResponse校验和投影；共享截止/容量/内部异常分类，内部错误对外泛化、服务端记录；取消继续传播。MCP保留sql/columns/rows/error等旧字段，新增status、error_code、HTTP状态及查询标识，复用分类结果，保留100行上限。
3. 验证：真实SQLite成功/业务失败比较JSON与SSE完整字段集合，并经实际FastMCP调用验证投影；故障注入覆盖截止、容量、内部异常、无效响应、取消和请求别名。检查错误后SSE终止、不伪造done或自动重试，敏感异常文本不外泄。保留已有权限/预算/只读及队列取消回归。
4. 检查：原扩展strict门禁增加无传输依赖的contracts模块，保留默认导入追踪；全仓mypy复测，与阶段8的201项同环境比较并明确剩余。全仓Ruff、前端编译、定向与全量pytest通过；不关闭检查或将全部API类型债务冒充清零。
5. 交付：更新query-contract、阶段报告/进度和命令说明，保存真实命令/环境/日志；审查diff及敏感内容，独立提交、普通push、核对远程SHA和Linux CI。达到验收后再进入下一阶段。

## 验收与限制

正常JSON/SSE最终响应遵循同一模型；三协议对截止/容量/内部故障输出稳定错误码，内部异常不含注入敏感文本；MCP业务失败status=failed。取消不改为普通错误；新增验证不能改变固定YAML/Runner顺序或放宽权限/SQL/预算。类型门禁/定向/全量/Ruff/前端及远程CI通过，文档符合实测。

不新增重试、租户系统、分布式会话、动态工作流或全量API重写。事件负载异构JSON仍保留动态值，此阶段不宣称对所有事件字段完成Schema校验。真实LLM/PG/MySQL与生产容量仍需对应环境。全量基线Windows2982通过/uvloop1跳过，Linux2983通过；全仓mypy201项仍失败。

## 进度

- 步骤1–4本地完成：12项新增回归；最终全量2994通过、Windows uvloop1跳过；扩展strict28文件零错误，全仓仍201项且无新增诊断；Ruff/前端通过。
- 范围补充：SSE最终序列化采用JSON模式并覆盖Decimal/时间三传输对照；不宣称中间事件的完整Schema校验。步骤5文档/证据已整理，等待提交推送与远程CI。
