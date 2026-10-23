# 阶段24：数据库连接能力真实状态

2026-10-23，依据[先行计划](stage-24-plan.md)实施；基线5761992（Actions39921359522成功），分支codex/database-capability-status，不合并main。

## 依据与实现

旧settings.py数据库GET/PUT透传未被ConnectionFactory消费的方言开关、池/超时值；workspace.html读取失败仍展示默认启用方言和池5/10秒。旧2项[失败](results/stage-24-before.txt)后最小修复[2通过](results/stage-24-first.txt)。采用真实只读能力报告，未增加动态池配置或重构连接生命周期。

- app/db/connections.py:describe_capabilities从枚举、对应实现/驱动声明和当前注册表生成快照；四类实现、七类未实现，find_spec仅模块可发现性，失败明确未知，未导入驱动或建立连接。只返回按类型计数，不回显ID/主机/别名等。源分派_create/_connect和aclose均未改。
- app/api/settings.py:get_database_settings报告只读视图，失败固定503 DATABASE_CAPABILITIES_UNAVAILABLE；update_database_settings固定409 DATABASE_SETTINGS_READ_ONLY，无body消费及旧表读写。聚合GET覆盖历史database段，管理员边界保持；兼容变化和范围见[配置契约](database-settings.md)。
- workspace.html:databaseCapabilitiesSnapshot/fetchDatabaseCapabilities校验完整11类型、结构/发现状态/数量，独立读取/未知错误/重试/序号保护。移除旧默认启用、假池/超时输入；展示实现与模块发现的差别、管理方式、当前进程注册数及非健康说明。原LLM/RAG/Harness交互不改。

Harness计划/Runner及节点调用未变，SQL只读和权限未放宽。mysql建池后持有单连接的现状如实报告，不称本阶段改善高并发。

## 验证与环境

Windows/Python3.13.9、Node24、Ruff0.17.1、mypy2.4.0，沿用已有site-packages与type-deps/driver-types。测试LLM_MOCK_MODE=1（conftest）、RAG_EMBEDDING_PROVIDER=mock、LITELLM_LOCAL_MODEL_COST_MAP=True，DE_TRACE_DIR为忽略目录，无生产凭据/模型依赖。

- 新增24项Python回归：真实SQLite/DuckDB创建、SELECT及注册/关闭计数；7类真实分派拒绝、模块发现缺失/异常、固定503不泄漏、无设置存储/连接调用、聚合/401/403。python -m pytest tests/test_api/test_database_settings.py tests/test_db/test_connections.py tests/test_db/test_mysql_pool_boundary.py -q：**30通过，16.84秒**，[日志](results/stage-24-targeted.txt)。现有MySQL测试仅真实驱动空池上下文，不连接服务，不是事务/网络验收。
- python -m pytest -q：**3390通过、1跳过、4警告，106.41秒**，[日志](results/stage-24-pytest.txt)。Windows uvloop跳过待Linux补验；测试耗时不是性能结论。Ruff通过，[日志](results/stage-24-ruff.txt)；沿用CI核心mypy命令**35文件零错误**，[日志](results/stage-24-core.txt)。全仓mypy app --output json仍退出1，**184项/42文件**，基线186、新增0/移除2（两个API返回类型）：[诊断](results/stage-24-after.jsonl)/[摘要](results/stage-24-summary.json)。未缩小检查或声称全仓类型通过。
- npm run check:frontend：5段源码编译、既有112+新**29实际函数契约，共141项**，[日志](results/stage-24-frontend.txt)。缺字段、重复类型、畸形/负数、只读/发现状态、成功/失败未知/恢复/迟到响应、仅GET及无旧回退结构；npm run check:frontend:build三页本地资源/字体/许可证、SHA256、重复构建与编译失败保留通过，[日志](results/stage-24-build.txt)，Expected identifier为故意负例。
- gstack /browse最终**9场景**：[证据](results/stage-24-browser.json)。正常4实现/7未实现、驱动未发现/未知、注入报告503/畸形200未知、真实临时SQLite注册1后释放0并恢复、375px无溢出/按钮在视口、实际PUT409且旧SQLite设置仍49/99（非生效值）。所有操作仅本机FastAPI :8024，lifespan关闭，SQLite设置协议/临时连接，合成认证、QA故障与同源CSP均只在忽略目录。0外部资源；[桌面](results/stage-24-desktop.png)/[手机](results/stage-24-mobile.png)已查看，不宣称完整无障碍审计。[控制台](results/stage-24-console.txt)保留未认证前401、mock就绪503、显式PUT409与注入503，无React运行异常。仅1次人工验证PUT，无页面自动保存。

## 调整与限制

初期按名称猜测路由路径，rg --files与路由文本定位到schema.py；没有据此判定功能缺失。Ruff提示try/except/pass改为suppress三个预期发现异常，未吞所有报告异常，未关闭规则。一次内联Python修改package.json因PowerShell引号语法失败，改为精确patch后重新执行141契约与构建检查，原失败后仅旧112通过的输出不计最终验收。gstack沿用随包Node兼容服务及独立状态，待预览就绪才导航，未使用其他浏览器工具。

能力声明以实际分派及回归校对，不是运行时自省出端到端能力；未来新增连接实现须同步能力视图和测试。模块可发现不等于驱动可用，注册数不是健康；未知元数据的句柄不计入按类型统计。真实MySQL/PostgreSQL服务、生产多worker、压测/RAG语义质量未测，184项类型债务和既有7项构建期依赖告警保留。原工作区pyproject.toml/docs/audits与隔离旧未跟踪文件保留。

## Git与下一步

待范围/凭据审查后提交推送，核对精确SHA/Linux CI，再补记远程证据；不合并main。下一候选核对LLM设置持久化失败的反馈与恢复路径，独立规划后再实施。
