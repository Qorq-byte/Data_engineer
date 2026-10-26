# 阶段42：扩展并冻结SQL与检索验收集

2026-10-26。承接阶段41最终 `bb316ca`；独立 `codex/expanded-acceptance-set` / `.stage42`，不合并main。[先行计划](stage-42-plan.md)。本地验收完成，待普通提交后的精确SHA双CI；阶段43–48未实施。

## 交付与标签来源

新增独立 [acceptance_v3](../../tests/fixtures/acceptance_v3/README.md)：[30条SQL契约](../../tests/fixtures/acceptance_v3/sql.json)、[30条检索qrels与23份文档](../../tests/fixtures/acceptance_v3/retrieval.json)、三场景小型SQLite数据及[冻结manifest](../../tests/fixtures/acceptance_v3/manifest.json)。两条轨道分别20 dev/10公开holdout，按事先固定ID划分；报告列出两侧实际类别覆盖，稀有标签不保证两侧都有，标签为多标签计数而非互斥分类。公开保留集不是秘密测试集，不能宣称无偏业务质量。

复用仓库users/products/orders电商实体，独立数据涵盖匿名NULL用户、无订单用户、可空email/category、同名用户、同价订单、状态、退款、UTC月边界；base/edge/empty三个场景。SQL逐题写明问题、输出列、有序/多重集语义、ORDER BY要求、SELECT-only权限、库/领域、人工行事实推导、可执行错误SQL。所有预期行先从固定行事实计算，再运行gold核对，没有从待测模型输出或gold执行结果生成标签。检索逐条写明库/领域、相关及硬负例的事实理由、无答案理由和坏排名；不把搜索结果自动转为qrels。这些标签由本次编码任务编写并自动核对，不声称业务人员或独立人审已经完成。

[自检证据](results/stage-42-baseline.json)：**90次gold核对通过，30个可执行错误SQL均被至少一个场景击穿，30个坏排名均被识别**。反例不是语法错误冒充语义反例；多场景能击穿COUNT(*)占位、NOT IN/NULL、重复姓名、SUM口径、半开时间边界、TopK并列及DISTINCT错误。只投影相同amount时，内部ID排序不可观察，最终复核从该题文字删除不必要的内部排序要求；另有返回id+amount的并列排序题。该修订在首次发布冻结前完成，没有为提高分数修改标签。

manifest规范身份为 `ed4be1acd589350adf428491ae4db542ee686fd3bd212a3f2f3517dca6b45755`。六份输入以UTF-8字节仅CRLF→LF规范化，跨平台不因换行误报；报告另保留实际raw字节哈希、规范数据集哈希、预测哈希。校验只能发现漂移，不是签名或独立审查证明。冻结后内容变化应另立版本，不追改旧集分数。

## 离线工具与边界

新增 `python -m scripts.evaluate_acceptance --output data/acceptance-v3/report.json`，支持 `--split dev|holdout|all`、`--sql-predictions`、`--rankings`、`--k`。默认SQL预测为空，完整30题/90检查仍在分母，全部标记MISSING_PREDICTION；没有把gold正控当模型成绩。self_check校验全部公开标签，实际BM25只搜索所选分割；有答案与无答案分别报告。

SQL沿用共享只读策略、query_only、每查询2秒/1000行上限，比较列名与行；有序序列与无序多重集分开，后者仍保留重复行。缺失、生成失败、截断/拒答/工具调用、未确认完成及结果不符分别失败；绑定导出数据仍核验来源哈希。实际费用未知保持null。

检索基线使用真实SQLite FTS5 BM25，在排名前同时按db_id/domain过滤；外部结果的重复、未知ID及跨范围命中保留为违规并计零，不静默去重/过滤冒充正确。空qrels排序指标按原定义为0，另报无答案正确空返回率；缺预测不等于主动空返回。Recall@K/Precision@K/MRR/nDCG复用原函数语义（Precision分母为K）；原脚本显式重导出共享函数，旧接口兼容。

CLI保护全部冻结输入/预测路径及其硬链接，不覆盖输入；JSON非法、漂移或发布失败保留旧报告，异常固定脱敏消息。新鲜子进程验证不导入litellm/OpenAI SDK，即使设置模拟凭据也不调用模型。未修改Harness固定节点顺序、权限、预算、线上JSON/SSE/MCP或受控真实模型profile。

## 同集词法基线

[全体报告](results/stage-42-baseline.json)、[开发集](results/stage-42-dev.json)、[公开保留集](results/stage-42-holdout.json)在K=3下：24条有答案问题Recall=**0.979167**、Precision=**0.375**、MRR=**1.0**、nDCG=**0.977191**；6条无答案正确空返回6/6。dev的16条有答案Recall=1，holdout的8条有答案Recall=0.9375；两侧分别4/2条无答案均正确空返回。全体宏平均包含无答案的零排序指标，不与answerable-only混用。

这是围绕已知字段与规则编写的合成词法基线，不能外推语义召回、真实embedding或业务准确率，也不能与v1/v2不同题集分数相减。自检不是SQL模型准确率；此次模型/embedding调用均0，actual_cost_usd为null。真实检索质量与耗时基线留阶段43。

## 验证与兼容

[先行失败](results/stage-42-initial.txt) **2失败**，证明旧通用预检接受未知/跨域相关ID；新独立profile预检拒绝，未追改旧集格式。[定向](results/stage-42-targeted.txt)108通过，[最终新增集复核](results/stage-42-final-targeted.txt) **96通过**（最终文案澄清后）。覆盖标签结构/行宽/NaN/布尔/大数/权限/分割/重复ID、反例存活、ORDER BY与多重集、缺预测、完成状态、错误排名、双过滤、manifest漂移、输出输入别名/硬链接、原子写失败和无SDK子进程。

[完整离线](results/stage-42-pytest.txt) **4147通过、1 Windows平台跳过、82真实项明确排除、2既有SDK警告，165.89秒**；之后仅澄清SQL30可观察排序文字，gold/数据/预期行不变，新96项及90次gold/全部反例重新通过。[真实双库](results/stage-42-real-services.pytest.txt) **82通过、0跳过，15.27秒**，PG19/MySQL63；[服务证据](results/stage-42-real-services.json)记录PostgreSQL16.15、MySQL8.4.13、随机环回端口、原资源限制与两个容器removed=true。3项既有进程列表弃用警告未屏蔽；只用专用合成库。

[Ruff](results/stage-42-ruff.txt)、[15组配置内strict含新增模块](results/stage-42-strict.txt)全部通过；[全仓类型比较](results/stage-42-types-summary.json)仍178项，新增0/消除0。[182前端契约](results/stage-42-frontend.txt)、[完整构建](results/stage-42-build.txt)通过，页面未改动，无新增浏览器UI验收。复用阶段41的Python3.12.13环境和已补齐的Windows tzdata；[npm安装](results/stage-42-npm.txt)实测仍有3项moderate构建期依赖告警，本阶段未升级。

[兼容证据](results/stage-42-compat.json)核对旧v1/v2所有输入规范字节、受控模型代码和4次上限均不变；旧v2完整12检查报告相同。stage32预测身份仍3a3b1266…、stage36费用报告身份仍5670d9cc…；旧quality报告除实测latency外语义相同。费用自哈希比较曾误包含report_sha256字段，修正核对脚本口径后匹配，未修改旧报告。沙箱子进程停滞及诊断脚本默认GBK读取中文的问题只影响本地工具，正式代码显式UTF-8；异常与修正记录在本地工作日志，没有扩大运行权限或费用范围。

## 后续

本地结果不代替最终GitHub验收：普通提交推送后核验同SHA的MVP及真实数据库工作流，补记文档后再核验最终SHA，不自动合并或发布。阶段43下一步先形成真实embedding的provider/模型版本、精确调用/输入规模/费用资源上限与预检manifest；本次不启动真实调用，也不扩大阶段44受控模型的四次上限。
