# 阶段23计划：Harness真实约束只读状态

2026-10-23，基线87b911d（阶段22最终Actions39912481496成功），从其新建codex/harness-policy-status。main不合并；原工作区pyproject.toml/docs/audits与隔离工作区旧未跟踪计划文件保留。使用brainstorming比较方案、planning-with-files沿用本目录计划/报告/进度，按用户明确授权先规划再实施。

## 代码依据与取舍

settings.py的Harness PUT只更新_settings_store并调用吞错_persist_section，没有运行消费者。query_service._run_query通过同一_CONFIG_LOADER读取agent.yml预算，_request_plan读取固定计划；Runner重试取计划/预算较小值，当前计划0。ExecuteSQLNode行数与超时还取Settings上限；读写检查无条件执行。查询HTTP总时限、会话TTL由Settings决定，候选数由请求1–5指定；旧表的30秒query_timeout_s并不能代表HTTP默认120秒。页面可关闭只读、编辑并提示保存成功，具有误导性。

比较：A（采用）最小只读真实状态，停止无效保存，明确来源和适用范围；B局部热更新需定义文件/环境/表优先级及请求版本，容易留下部分无效字段；C统一所有配置来源及跨进程广播超过MVP收益。本阶段不改实际约束行为，仅修正接口与展示。后续动态配置需单独设计，不能开放关闭只读开关。

## 实施顺序

1. 旧代码失败回归：Harness PUT不应再声称应用、GET应返回真实值且忽略历史表；保存失败证据。无生产数据库/模型依赖。
2. 在查询服务增加只读策略快照函数，直接使用同一_CONFIG_LOADER/_request_plan与实际Settings，不另造加载器。返回JSON/SSE request_query范围、固定节点序列/计划上限，LLM预算、实际节点重试上限、SQL行/超时上限、HTTP总时限、内存会话TTL、HTTP候选范围及对应来源；直接SQL单独标注。不从agent.yml的仅声明字段推断执行行为，不暴露路径绝对值/密钥/旧表内容。快照为读取时状态，非历史请求审计或数据库健康。
3. GET /settings/harness只读真实快照、mode=read_only、editable_fields=[]、legacy_stored_settings_applied=false；配置/计划读取失败固定503且不返回旧值。PUT不接收可编辑模型，无论旧字段/空体/未知字段均409 HARNESS_SETTINGS_READ_ONLY，不读写数据库或更新内存。鉴权沿用管理员依赖；聚合设置用相同视图覆盖历史表。
4. 页面移除保存/只读切换/数字编辑，显示有来源的只读状态卡、固定流程与范围说明；独立加载/error/序号处理，不吞失败，不显示默认假值。提供重新读取，迟到旧响应不覆盖新状态。原RAG/LLM/数据库交互不改。
5. 覆盖真实临时SQLite历史设置不消费、配置/计划热加载、实际执行只读与行数/超时来源、预算/重试/工作流回归、401/403、聚合与失败不泄漏；实际页面函数契约、gstack浏览器桌面/375px/故障恢复。运行全量pytest、Ruff、strict35、全仓mypy相对188项无新增、83既有契约及新增、构建完整性/重复性/失败保留。
6. 更新阶段报告、接口/部署/README及进度，说明兼容变化（PUT409）、来源优先级、未做压测/语义质量/MySQL/跨进程验证及现有类型/依赖债务。范围/凭据审查后普通提交推送独立分支，核对精确SHA/Linux CI；远程证据另行文档提交推送并核对CI。

## 验收与交付

旧表任意值不伪装运行约束；写请求无副作用且不称成功；不同配置来源/单位/协议范围可核对；读取失败未知且可恢复；固定计划和Runner继续决定流转，节点不互调，LLM无路由权；只读安全不弱化。交付接口、只读页面、回归与阶段/运维文档、验证日志和远程提交证据。本阶段不增运行配置热更新、不统一全仓配置、不改SQL执行器/计划/权限/并发容量，不以单测耗时推断性能。

## 进度与调整

已确认基线/远程与工作区，方案和步骤已在编码前确定。初期rg使用了不存在的constraints.py/ci.yml等路径，已依据rg --files定位为constraint.py/checks.yml；属于检索路径修正，没有据此判定模块缺失。下一步先失败回归，再实现。

步骤1–5完成：旧2回归失败后2通过，24新增Python/定向45/全量3366通过且Windows uvloop1跳过；新29前端契约/共112、构建检查、strict35/Ruff通过，全仓186项/42文件（基线188、新增0）。gstack最终7场景及桌面/手机已验证；中断和Windows服务恢复、测试输入/格式修正详见stage-23.md。步骤6文档与证据已整理，待审查提交/推送/远程CI。

步骤6代码6e40463已推送，Actions39921014380精确SHA成功；Linux3367通过（36.82秒，uvloop实际执行）、112前端/构建/Ruff/strict35通过。验收证据补入stage-23.md/results/stage-23-ci.json，远程补记文档另行提交推送；原工作区改动保留，main不合并。
