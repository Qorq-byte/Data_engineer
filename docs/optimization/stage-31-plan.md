# 阶段31计划：请求级用量持久审计

2026-10-24，按用户“规划好下一步，然后开始实施”授权。独立codex/llm-usage-audit/.stage31，基线阶段30最终24c3577；阶段29/30尚未合并main，因此堆叠分支，不隐式合并。保留原主工作区用户改动及旧工作区。

## 方案与范围

比较扩展MySQL历史表、本机SQLite审计、直接JSONL日志：MySQL路线依赖外部环境且旧历史含问题/SQL；JSONL的原子重复写入、检索与保留轮转更复杂；采用独立本机SQLite作为单机私有共享工作台最小持久审计。不能宣称多机共享账本或不可篡改审计。

LLM_AUDIT_PATH为空默认关闭，配置后启用；LLM_AUDIT_MAX_RECORDS默认1000、范围1–10000。不修改用户.env或自动启用生产存储。按query_id保存schema_version=1、UTC时间、请求结果、预算次数、脱敏session引用及现有用量快照；不保存prompt、SQL、原始session_id、异常正文/密钥/地址。provider/model限定标识字符和长度，异常标识redacted；session_ref为SHA256伪名，不宣称匿名化。

请求取得admission/session gate后建立状态，_run_query复用collector并在预算finally回填调用次数。成功、业务失败、基础设施异常、取消都在退出前尝试保存；排队/容量拒绝不生成调用审计。取消沿原路径传播，上游资源先释放，SQLite worker在取消时等待关闭，不留下后台写任务。SQLite busy等待有限，但底层文件系统挂起/进程崩溃/强制终止不保证审计落盘；不宣称完整事件账本。取消在写入开始后发生时，已记录的workflow outcome不代表响应成功交付。

独立文件连接每次打开/关闭，写入和按条数保留清理在同一事务，唯一query_id：相同快照重复写入幂等，不同快照冲突拒绝覆盖。最大单条JSON 1MiB、最多1000个attempt；保留上限约束逻辑记录数，不承诺自动缩小文件或固定磁盘占用。未知schema或非审计SQLite文件拒绝修改，读取不创建文件。无费用估算/预测导出/监控或新的模型调用。

JSON/SSE done/MCP新增llm_audit状态persisted/disabled/failed，查询执行结果保持原语义。提供GET /api/v1/usage-audits/{query_id}，复用管理员认证；关闭/不可读503、过期或无记录404、非法ID422。不增加写API，不泄露路径或底层错误。不扩展为多租户授权。

## 顺序与验收

1. 真实SQLite回归先行：落盘重读/新进程读取、0/null和SDK来源、allowlist脱敏、并发、幂等冲突、事务保留、锁/坏文件/未知版本与读取无副作用。三协议正常和失败、取消资源释放/异常审计/写失败/关闭/认证边界。保存旧版本缺功能的红灯证据。
2. 实现最小审计存储及配置、请求状态和finally落盘，保持Harness固定计划/节点生命周期/调用预算/SQL只读和异常传播。所有I/O线程等待收尾；无后台轮询、无fire-and-forget。
3. 三协议与受保护只读API接线；默认关闭、写失败可见，不将内存摘要当落盘成功。真实Router/合成传输及真实SQLite检验关联，不发生产模型请求。
4. 定向/全量pytest、Ruff、strict36/评测strict1/LLM边界strict2、新模块类型门禁；全仓mypy相对178无新增；182前端契约和完整构建。无页面改动不重复视觉检查。记录所有环境/未测边界。
5. 用量/查询契约、环境示例/运行说明、阶段报告及证据更新，diff/凭据检查后提交普通推送；精确SHA CI成功后补记文档提交并核验最终CI。不强推、不自动合并main。

类型门禁范围调整：独立API模块的默认导入追踪触发现有app.api初始化循环的存量错误；只读入口收敛至已有app/api/query.py，不扩大API模块集合或关闭导入追踪。新增storage/usage_audit和workflow/query_audit独立strict2，API改动以全仓178无新增和协议回归保护。

进一步边界调整：现有app.storage包初始化会主动导入MySQL backend。SQLite审计改为独立app/audit/store.py，避免纯审计依赖外部驱动与该包存量类型错误；不改MySQL导出或关闭检查。最终strict2为app/audit/store.py和app/workflow/query_audit.py。

审查补充：快照构造异常纳入审计失败隔离；HTTP截止测试按实际进入provider次数验证，不强制在截止前产生调用。测试conftest默认关闭审计，专用用例只启用tmp_path文件，避免继承宿主审计路径。

实施完成：本地及代码精确SHA CI均通过，完整边界、调整、失败证据和验收见[阶段31报告](stage-31.md)。
