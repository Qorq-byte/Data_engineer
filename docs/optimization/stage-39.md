# 阶段39：版本化SQL输出契约与反例验收

2026-10-26，基线阶段38最终c2f5050，最终Actions40209475879成功（Linux3975通过，71.13秒）。独立codex/versioned-sql-acceptance/.stage39；[先行计划](stage-39-plan.md)、[契约及命令](sql-output-contracts.md)。沿用用户选择的仓库合成SQLite，不改原主工作区，不自动合并main。

## 实现和数据边界

新增独立v2：4个问题明确输出列名/列序、过滤、排序和空集合行为，配合共享Schema的base/edge/empty三组数据。手写expected_rows与gold分别保存并先做12项自检，不从gold自动生成标签。反例覆盖零订单、同名用户、重复金额、退款与非completed状态、NULL/大小写/相似类别、多个匹配和空聚合。旧v1问题/gold/test_schema.sql原始字节不变，不追改阶段38的2/4。

共享SQLite查询原语保留旧rows接口、只读策略、错误码、2秒/1000行，新增列元数据并显式释放cursor/进度回调。独立v2评分检查精确列名/列序、结果行/行序；声明排序的题目要求最外层ORDER BY。缺预测、失败、非stop、截断、refusal/tools或空SQL仍占4题分母；每条预测在三库都匹配才计该题robust_match。报告有12项本地检查，不是12次模型生成或三份请求费用。

新增离线evaluate_sql_contracts CLI：严格JSON、输入/fixture/硬链接保护、原子报告、固定错误/退出2，成功写报告为0而非全题正确。无预测时仅标签自检成功，模型预测仍全部missing。输出不含预测SQL/结果行，解析器日志在CLI作用域屏蔽并finally恢复。v2准备与离线入口不导入SDK。

受控模型入口新增显式--profile synthetic-sqlite-model-v2，默认v1保持。准备清单绑定代码/配置/全部fixture/软件版本，gold和手写结果不进入问题或调用清单。独立进程/业务环境清理、base唯一连接、新session、execute=false及正式Harness保持；v2候选审计导出后将同一SQL复用于三个本地库。总4次/每题1次、32KiB消息/1024输出token、30秒请求/45秒题/180秒批次，无重试/fallback。不修改全局生成提示、工作流顺序、权限、线上JSON/SSE/MCP或旧用量/价格口径。

## 真实模型验收

[清单](results/stage-39-manifest.json) SHA为5e11339c44b4afc1893d101cfddb266dab01a60944c7698b39f15bcadb4fb96a，模型deepseek-v4-flash；Python3.12.13/LiteLLM1.106.2/SQLGlot30.18.0/Pydantic2.14.1。展示清单及固定资源上限后，仅执行一次完整批次，未追加补跑。

[脱敏结果](results/stage-39-live-summary.json)：**4次真实尝试、每题1次、4个candidate_match；12/12本地检查可执行且满足契约，4/4题在三库全部匹配**。请求llm_calls=[1,1,1,1]，观察attempt总数4。SDK观察prompt/completion为2764/474 token，4条usage_basis均sdk_normalized，缺测数0；这些是SDK观察值，不证明供应商原始字段或账单完整。真实费用及预测cost_usd保持null，不重复三份token/费用、不从旧路由器0推断免费。

原始响应、SQL、审计、trace只存忽略目录data/model-evaluations/stage39-contract-v2-20261026，未上传。公开只有无凭据清单、测试及脱敏数字。v2改变题意/Schema/数据/评分，**不能把4/4与v1的2/4相减称模型提升**；没有同任务对照、统计显著性或业务准确率结论。模型别名、SDK来源与本机摘要的可信边界仍在，未冻结供应商版本或提供签名。

## 失败回归、复查与完整验证

新增46项。[初始收集失败](results/stage-39-initial.txt)保留；随后真实SQLite验证18种错误SQL、错标签/列名、缺预测/非stop/只读、输出覆盖/非法JSON、profile/源摘要、新进程无SDK准备→4次合成SDK→12项检查→v2绑定、无gold进prompt、费用不重复、日志隐私及大整数边界。

[独立只读复查](results/stage-39-review.json)发现两项并已关闭：[两种排序伪阳性](results/stage-39-order-initial.txt)，原edge名称和数量顺序碰巧等于ID顺序；调整为Zulu/Alpha、订单数1/3/0/0并更新独立标签，使按名称或数量倒序必然失分。[EXPLAIN重复解析回显SQL](results/stage-39-privacy-initial.txt)，改为列/行匹配后才检查一次排序，并在离线CLI抑制解析器warning后恢复日志；直接函数与CLI回归均无合成私密标记泄漏。复查确认评分/费用分母/资源/v1适配未发现新问题，不等于完整安全审计。

修复前[首轮全量](results/stage-39-pre-review-pytest.txt)4015通过/1平台跳过；最终[195项联测](results/stage-39-targeted.txt)、[全量](results/stage-39-pytest.txt) **4020通过、1平台跳过、2项既有SDK警告，140.85秒**。不以修复前结果替代最终验收。

[Ruff](results/stage-39-ruff.txt)、[原核心strict36](results/stage-39-core-types.txt)、[原评测/审计加新模块strict17](results/stage-39-strict.txt)、[隔离入口与两个旧脚本strict4](results/stage-39-worker-types.txt)通过。抽取异常类后补显式重导出，保留旧导入和原strict，不放宽类型规则；入口沿既有--follow-imports=silent，不宣称所有依赖无债务。[全仓](results/stage-39-types-summary.json)仍178项，新增0。[182前端契约](results/stage-39-frontend.txt)、[完整构建/重复性/失败保留](results/stage-39-build.txt)通过。

[兼容对照](results/stage-39-compat.json)：v1两个fixture原始字节相同，阶段32预测SHA仍3a3b1266…，阶段36估算SHA仍5670d9cc…；同一Python/PYTHONHASHSEED=0和相同输入运行阶段38/39质量评测，除实测耗时值外报告全部字段一致，保留分母/采样数/null/完成与费用口径。仍为4案例/2预测/1匹配，费用null。独立手写标签[12/12自检](results/stage-39-gold-contracts.json)是离线合成SQL，不冒充真实模型结果。

全量/前端任务曾因自动审批服务额度而未执行；用户要求继续后正常审批重试成功，无绕过。期间误写一次旧测试文件名，用rg核实并更正，不计为已测试。

仍待真实业务标注/语义质量、embedding、PG/MySQL实际集成、生产容量/大Schema/故障恢复、真实账单与复杂价格、178项类型债务。可信本机/独立进程限制、源文件换行和软件版本变化需新清单、强杀/取消不保证服务端撤销计费等边界保持。

代码[fcd2a2a](https://github.com/Qorq-byte/Data_engineer/commit/fcd2a2a4394d511be418089c2f8f1262ab38b0f8)已普通推送codex/versioned-sql-acceptance。[Actions40229369994](https://github.com/Qorq-byte/Data_engineer/actions/runs/40229369994)成功，headSha精确一致；[结构化证据](results/stage-39-ci.json)、[Linux摘要](results/stage-39-ci-tests.txt)。干净环境**4021通过、2警告，79.50秒**，本地平台跳过项在Linux执行通过；182前端/完整构建、Ruff、所有原门禁和新增模块/入口strict均通过。

本次验收文档另行普通提交推送并核验最终精确SHA CI，不以代码CI替代最终文档提交验收。不自动合并main，原主工作区用户修改保留。
