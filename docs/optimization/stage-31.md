# 阶段31：请求级用量持久审计

2026-10-24，按用户授权先规划后实施，独立codex/llm-usage-audit/.stage31，基线阶段30最终24c3577。阶段29/30尚未合并main，本分支堆叠承接；原主工作区pyproject.toml/docs/audits和旧工作区保留，不自动合并main。设计取舍和验收见[先行计划](stage-31-plan.md)。

## 实现

采用独立本机SQLite审计：相比扩展MySQL历史无需外部数据库和含问题/SQL的旧表；相比JSONL，事务、唯一ID和按条数保留更直接。LLM_AUDIT_PATH为空默认关闭，显式配置后启用，不修改用户.env。LLM_AUDIT_MAX_RECORDS默认1000，范围1–10000。

- app/audit/store.py：版本1元数据快照，记录query_id、UTC时间、workflow outcome、预算次数、session SHA256引用和allowlist用量；不复制完整响应，不写问题/SQL/密钥/异常正文/地址。标识非法则redacted；缺测/null、合法0与SDK来源保留；聚合按attempt重算。最多1000次尝试，单条JSON1MiB；事务内唯一ID、同快照幂等、不同快照拒绝覆盖、按插入顺序保留N条。读mode=ro，不创建文件；未知版本/非审计schema拒绝修改。每操作独立连接并关闭。
- app/workflow/query_audit.py与query_service：取得admission/session gate后创建请求状态；既有collector复用，预算finally回填次数。成功/业务失败/异常/取消均在退出时尝试保存；排队取消/容量拒绝不新增记录。先沿Runner释放LLM/DB资源，再保存最终快照；线程I/O在取消（含重复取消）后仍等待收尾，无fire-and-forget。审计失败（含快照构造）只返回failed/固定warning，不替换原结果或异常。
- JSON/SSE done/MCP共用llm_audit={status,query_id}，状态persisted/disabled/failed独立于workflow status。现有查询路由新增管理员GET /api/v1/usage-audits/{query_id}，匿名401/非管理员403、非法422、关闭或不可读503、未找到/淘汰404；无写API和列表接口。

Harness固定YAML/节点生命周期、模型选择、预算、重试、SQL只读和原业务/基础设施错误码未改。原MySQL历史和WorkflowTrace继续独立，不把新审计说成全系统账本。契约/配置/运行说明见[审计说明](usage-audit.md)、[用量契约](request-llm-usage.md)、[查询契约](query-contract.md)、README与.env.example。

## 回归与审查调整

实现前21项新功能回归：12失败、9因配置/模块缺失的setup错误，[原始日志](results/stage-31-before.txt)。不是21项旧查询功能故障。首版21项通过，[日志](results/stage-31-targeted-initial.txt)；扩展到40项新增回归：真实文件/新进程重读、allowlist/未知/SDK来源、幂等冲突/保留/线程并发、事务回滚、坏库/未知版本/锁/大小上限、三协议成功/业务失败/关闭/写失败、并发请求分账、取消资源关闭、容量与会话排队、HTTP504、认证和重复取消worker关闭。

审查发现快照构造在失败隔离try之外，可能覆盖成功结果，补红灯证明后修复，[证据](results/stage-31-snapshot-before.txt)。首轮全量1失败/3678通过/1跳过：HTTP20ms测试假定provider必定已进入，实际在Schema准备时截止，正确记录0次。改为与实际进入次数一致；另有同步等待provider已进入后取消的独立用例，仍要求1次/资源关闭。首轮日志保留[初次全量](results/stage-31-pytest-initial.txt)，不隐瞒为已通过。

类型边界调整：单独新增app.api模块的strict会展开现有API初始化循环的存量错误，入口收敛到已有query.py；app.storage包初始化又会加载MySQL，故审计模块独立到app/audit，而非改变MySQL导出或添加Any/忽略规则。最终新增strict2通过，原strict36、LLM边界strict2、评测strict1通过。全仓178项诊断指纹无新增；[摘要](results/stage-31-types-summary.json)。中间一次模块移动的旧import在collection时发现并修正，不计成产品验收。

前端182函数契约及三页46文件完整构建/重复构建/故意坏JS保留旧产物通过；无页面改动不重复视觉测试。Windows asyncio socketpair仍需沙箱外验证；复用已验收Python3.12环境及本地npm锁文件缓存，无生产模型/外部数据库请求。conftest强制审计默认关闭，专用用例只配置临时文件，避免继承宿主审计路径。

最终定向 **362通过、2警告，18.41秒**，[日志](results/stage-31-targeted.txt)；最终全量 **3680通过、1跳过、2警告，109.57秒**，[日志](results/stage-31-pytest.txt)，包含全部40项新增回归。独立分支已提交推送，代码精确SHA CI通过，详见下方远程验收。

## 限制与后续

审计默认关闭；单机私有共享工作台的管理员可读，不是多租户、加密或不可篡改账本。session_ref仅伪名，低熵ID可被猜测。只按条数保留，不做按天TTL/VACUUM或固定磁盘上限；GET404不证明原请求不存在。没有列表：需保留响应/SSE的query_id，无ID中断请求由本机运维检查库。

锁等待0.2秒不含线程排队，也不能中断挂起文件系统；取消会等待worker收尾，不能承诺严格响应截止。崩溃/强制退出/磁盘满或文件丢失可缺审计，无持久重试队列。completed表示workflow已完成，不等于客户端已收到；SDK归一化usage仍不能证明原始供应商字段完整或账单真实。多进程并发写、多机共享、生产负载/磁盘故障未测；费用估算、预测导出继续独立后续。

远程首轮Actions40072883286：4失败/3677通过，四项均为JSON/SSE路径脱敏断言过宽；Linux源库database_name包含合法SQLite文件路径，测试误把源库与审计库共同父目录当成审计路径。改为独立audit-private-only目录精确验证其不泄露，未改查询响应或放宽审计字段约束。首次CI日志保留stage-31-ci-initial-failure.txt，修正后重新核验。

## Git与远程验收

代码1b4566d与测试跨平台修正 [2cc417c](https://github.com/Qorq-byte/Data_engineer/commit/2cc417c708fc1e886550f3c1dac93b48fec50e81) 均普通推送codex/llm-usage-audit；[Actions40073192813](https://github.com/Qorq-byte/Data_engineer/actions/runs/40073192813) 成功，headSha精确匹配，[结构化证据](results/stage-31-ci.json)。Linux干净安装 **3681通过、2警告，46.23秒**，平台项实际执行；前端182契约/完整构建、Ruff、原strict36、LLM边界strict2、审计strict2和评测strict1均通过。修正目录后的本地40项审计回归也通过（10.45秒），见stage-31-audit-final.txt。本次补记文档独立提交推送并核验最终CI；不强推、不自动合并main。
