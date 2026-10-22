# 阶段16计划：Schema/RAG显式刷新同源与顺序

基准9ad0ff3及成功CI；继续codex/mvp-hardening独立工作树，现有origin不变，原工作区和未跟踪文件不动。计划先于实现。

## 问题与取舍

refresh_schema先内省再await embedding，之后索引按db_id删除/替换；未核对等待期间原连接/namespace/表列是否变化。启动和后台各有独立写入口，同库并发刷新也可能旧快照覆盖新快照。优先保证现有显式刷新可信，不通过每次DuckDB查询扫描全量表列来模拟Schema版本。

## 步骤与验收

1. 共享刷新协调器按db_id串行捕获来源→强制内省→索引准备→提交；启动、后台和手动入口接入。保留其他库可独立刷新，不加LLM全局锁，不改Harness。
2. SchemaMetadataRAG.index_schemas新增替换前校验回调，embedding/维数验证后、任何删除前执行；空Schema删除也必须校验。同实例同db_id替换串行。核对原注册句柄、fresh namespace状态和真实表列/索引/FK等结构，忽略行数估计/时间戳变化；冲突使缓存失效，不覆盖旧索引，HTTP409/SCHEMA_REFRESH_CONFLICT，显式重试。
3. embedding不可用/失败保留rag_indexed=false及warning；取消继续传播，旧数据不因等待阶段失败而删除。两后端写入失败仍如实失败，不虚构原子事务或自动回滚。
4. 先用真实SQLite及LanceDB/FTS5固定“embedding期间DDL”的旧实现失败，再覆盖重连/断开、DuckDB namespace和表列变化、空替换、取消/坏embedding、同库并发顺序/其他库独立及新请求恢复。以Event/回调制造确定性交错，不靠sleep。
5. 定向/全量pytest、Ruff、前端、核心strict（新协调器）通过；全仓mypy对197项基线比对，无新增诊断，保留存量失败。记录命令/环境/结果，不将mock embedding功能回归称为检索质量或性能评测。
6. 更新协议/运行/RAG说明、阶段报告/进度及证据，审查暂存和凭据后commit、普通push、核对远程SHA/Linux CI，补记远程证据；不强推、不合并main。

## 限制

原生连接应受应用lease管理，外部进程DDL不与双后端索引形成事务；不能承诺核对与跨系统持久写原子。普通DuckDB表列DDL仍依赖TTL或显式刷新，索引无catalog字段，切目标后必须刷新。两后端部分写失败、跨进程协调、真实embedding/PG/MySQL与生产容量需后续独立验证。

## 进度

- 计划已写入；Windows基线3198通过/uvloop1跳过，Linux3199通过，strict33源文件，全仓197项类型存量。
- 写入口复核发现IndexRefresher还保留独立重建路径，现纳入步骤1的共享协调；成功版本/时间仅在索引发布成功后记录。该类虽已实例化，周期schedule_refresh没有默认启动调用，不能称为已运行的自动DDL监视器。此调整用于消除遗漏写路径，不新增定时任务。
- 前端消费复核发现两处将HTTP200/业务失败计入索引成功，纳入步骤3的真实结果报告修正；用实际页面处理函数的Node契约检查覆盖12场景，加入既有check:frontend。协调器移到app/db，显式注入索引/转换函数以解除RAG单例循环依赖；strict规则和默认导入追踪不变。

- 步骤1–5本地完成：旧固定6例全部失败，首轮10通过；最终新增27项，定向71通过，全量3225通过/Windows uvloop1跳过（55.96秒）。扩展strict34源文件、全仓Ruff/前端编译/12页面处理函数契约通过；全仓197项且新增诊断0。新增404/500分类回归，详细调整和部分后端写限制见报告。步骤6文档/证据已整理，待提交和远程验收。

- 步骤6完成：26aef3e普通推送、远程SHA一致；Actions39732926863成功，Linux全量3226通过（37.70秒）、uvloop执行通过，strict34源文件/Ruff/前端含12契约/干净安装通过，远程证据已补记。
