# 阶段40：真实PostgreSQL/MySQL集成验收

2026-10-26，基线阶段39最终834468e，最终Actions40252286603成功；独立codex/real-database-acceptance/.stage40。[先行计划](stage-40-plan.md)、[运行及边界](real-database-acceptance.md)。保留原主工作区，不自动合并main；本阶段没有真实LLM请求。

## 已实现

新增显式启用的真实双库套件、本地受限临时容器工具和独立CI服务作业。固定fixture数据库、公开disposable测试凭据、管理员seed/SELECT-only应用账号分离；不读取现有业务DSN，不使用本机既有MySQL。Docker环回随机端口/CPU内存限制/tmpfs/专用随机标签，完整ID及标签核对后清理，启动失败的已创建容器也会发现核验。无默认端口猜测、无SQLite替代或连接失败后假跳过。

实测修复：PG SELECT账号约束内省改用关系OID/列序号的pg_catalog查询，防同名约束混合并保留复合列配对；PG空结果用prepare属性保留列。PG/MySQL EXPLAIN从错误的同步cursor路径改为异步驱动执行，计划/查询共享异步截止。MySQL优化器SQL备注在leased会话用sql_notes临时关闭并恢复，不使用全局warnings过滤。重连等待旧租约被取消时，未发布的新连接/池也释放；关闭使用shield/drain等待资源收尾。

固定Harness、节点生命周期、权限/只读及线上JSON/SSE/MCP未改变，生成在集成测试中用确定性合成Router以排除模型随机性。普通与EXPLAIN锁等待的超时/取消、关闭在途查询、并发、重连旧目标、账号直接写拒绝和完整run_query均由真实驱动/服务验证。

## 失败证据与本地真实验收

[首轮准确回归](results/stage-40-initial.txt)为**4失败/2通过**：PG只读角色PK/FK不可见、PG空结果丢列、PG/MySQL异步EXPLAIN空计划。[当次服务/清理](results/stage-40-initial-services.json)记录专用服务和两容器移除。更早的测试工具自身字段断言及MySQL空参数百分号问题已更正，不冒充产品缺陷。

进一步保留[EXPLAIN SQL备注1失败/1通过](results/stage-40-note-initial.txt)及[取消重连2失败](results/stage-40-cancel-initial.txt)后再修复。用真实连接观察候选生命周期，不用fake数据库证明清理；失败回归自身finally也释放候选/容器。复合外键、失败会话恢复、实际在途关闭再补入最终套件。

[最终真实测试](results/stage-40-real-tests.txt) **38通过、0跳过**，PG/MySQL各19项。[结构化证据](results/stage-40-real-services.json)记录PostgreSQL16.15（Debian构建）、MySQL8.4.13、实际image ID、临时端口、JUnit计数及两个容器removed=true；本地Python3.12.13、asyncpg0.32.0/aiomysql0.3.2/PyMySQL1.2.3。官方tag按版本系列选取，记录值才是本次实测版本，不外推所有16/8.4发布。

三条本地MySQL警告来自测试用进程列表状态查询的弃用提示，未隐藏；优化器SQL正文回显回归已关闭。只读账号拒绝直接DELETE，普通/EXPLAIN取消均释放租约，MySQL句柄关闭后显式重连，PG复用验证5行不变。另对PG持锁事务的统计快照主动刷新，避免监测陈旧状态造成CI时序误判；[10项锁/关闭定向复核](results/stage-40-lock-recheck.json)通过且容器移除。没有真实模型调用、账单或业务质量结论。

## 完整兼容检查

新增**54项**：16项离线安全启用/容器所有权/业务环境白名单边界，38项真实双库。本地真实pytest子进程用空工作目录/环境白名单定位源码，避免宿主.env或业务设置影响验收；在此条件下38项仍全部通过。[离线全量](results/stage-40-pytest.txt) **4036通过、1平台跳过、38明确排除、2既有SDK警告，204.35秒**；排除项在独立真实作业全部运行，不称全部数据库测试离线通过。

[Ruff](results/stage-40-ruff.txt)、[原核心strict36](results/stage-40-core-types.txt)、[原评测/新增工具strict18](results/stage-40-strict.txt)、[原入口strict4](results/stage-40-worker-types.txt)通过，未放宽规则。[全仓类型](results/stage-40-types-summary.json)仍178项，新增0；原入口仍沿既有follow-imports=silent，不声称所有依赖无债务。[182前端契约](results/stage-40-frontend.txt)、[完整构建/重复性/失败保留](results/stage-40-build.txt)通过。

[兼容证据](results/stage-40-compat.json)：阶段32预测SHA仍3a3b1266…，阶段36估算SHA仍5670d9cc…；阶段39完整固定契约报告相同，v1/v2问题/标签未改。不因数据库集成把旧费用null变0，不回填候选费用，不新增LLM调用。

## 范围与后续

PG内省限public；跨schema外键、生产search_path/临时对象、估算行数和索引列的简化仍保留。MySQLmax_rows限制返回行，普通游标可能先缓冲完整结果，未证明大查询内存/网络边界。计划分析仍启发式，未承诺完整MySQL计划解释。关闭/取消是客户端资源语义，不证明服务器立即终止；网络分区、进程强杀、生产容量/TLS/高可用未实测。

业务标注/语义质量、真实embedding、生产大Schema/负载恢复、真实账单/复杂价格、178项类型债务留后续。本阶段将普通提交推送，核验同一代码SHA的MVP及真实服务两工作流，再补记文档并核验最终SHA，不合并main。

首次代码0aee8c1的MVP已成功，但真实工作流在作业创建前因配置位置无效而失败，未运行数据库用例。经gstack /browse核对[GitHub官方上下文规则](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)，job.services动态端口移至测试步骤env（job级env不能用job上下文）。该配置修复单独提交，并重新核验同一修复SHA的两个工作流；不把本地通过或仅MVP绿色当完整远程验收。
