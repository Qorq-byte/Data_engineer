# 阶段38：受控真实模型验收入口

2026-10-25，从阶段37最终1d79864建立codex/controlled-model-evaluation/.stage38。用户选择仓库4题合成SQLite集；[先行计划](stage-38-plan.md)、[入口契约](controlled-model-evaluation.md)。不改原主工作区，不自动合并main。

## 实现

新增纯清单与独立CLI执行。prepare不导入SDK/settings、不读凭据或联网；execute核对SHA、代码/配置/数据/fixture内容及运行版本，独占新目录，不自动续跑。只允许公共DeepSeek endpoint、明确模型和4题固定合成集；仅读取指定凭据，不整体载入env文件。清空业务环境后，在新workspace初始化正式run_query，唯一连接为新建只读fixture SQLite，无旧会话/领域/RAG初始化。各题新session、num_candidates=1、execute=false；gold只进独立只读SQL评测器。

额外调用方预算与Harness请求预算先全部检查再统一预留，同对象不双计、无await。原生传输前context guard检查provider实例、模型、n、32KiB消息和1024输出token，拒绝参数改路由/凭据和流式调用。总4次/每题1次，retry=0/fallback空，请求30秒/每题45秒/批次调用窗口180秒。固定节点顺序、权限、只读及三步生命周期保持；默认线上行为不变。

复用请求用量、v2候选审计、显式绑定导出和原评测器。失败/取消/超时/审计失败/未运行分别记录，完整4题分母；审计失败不生成虚假强绑定预测。数据库/SDK收尾关闭，供应商异常正文不进入终端、trace或报告。

## 真实模型结果

环境Python3.12.13、LiteLLM1.106.2、SQLGlot30.18.0、Pydantic2.14.1，真实模型名deepseek-v4-flash。[最终清单](results/stage-38-manifest.json) SHA为42b9a4410a0268129dbde6b430dbb1099a2bf704de760ecc30153ab413e1d1dd。仅一次完整4题供应商批次，无重试/fallback/补跑。

[脱敏汇总](results/stage-38-live-summary.json)：**4次真实尝试，每题1次；4个stop、4个candidate_match；4/4可执行，2/4与原gold结果一致**。保留2项RESULT_MISMATCH，未改问题/gold提升分数。SDK观察prompt/completion为3100/1159 token，4项均有usage且basis均sdk_normalized，不能证明原始供应商字段或账单完整。真实费用和预测cost_usd仍null，不使用路由器累计0或离线价格冒充账单。

离线查看两个差异，未追加模型调用：sql3保留LEFT JOIN/用户分组，但比gold多返回名称列；原问题没有明确输出列，严格不匹配不能单独证明业务答案错误。sql4将类别条件解释为排除unknown，与gold空集不同，原中文描述需澄清。fixture中每个用户都有订单，未覆盖零订单用户。下一步应先审核题意/输出列和反例，再建设业务集；不能将本轮50%外推业务准确率，不追改当前固定基线。

原始响应、预测SQL、审计与trace仅存忽略目录data/model-evaluations/stage38-synthetic-20261025-fixed，未提交。公开只含无凭据清单及数字。模型别名可变，清单/摘要不是签名或供应商版本冻结证明。

首次[启动清单](results/stage-38-startup-manifest.json) a0cb5518…在worker导入API时失败：清理USERNAME使Windows的getpass尝试导入pwd；尚未发模型请求，供应商调用0。保留原目录，新加全新子进程端到端合成回归，保留必要系统身份变量后用新清单/新目录运行，总真实调用仍4。CLI另增固定startup_failed阶段检查点，初始化错误不回显原始异常。

## 回归与限制

新增30项，覆盖清单/来源/参数/目录拒绝、无SDK准备、预算原子/去重/并发/共享上限、正式Harness到强绑定指标、异常脱敏、取消/超时关闭、审计失败和全新进程初始化。[初始失败](results/stage-38-initial.txt)及[首轮全量3失败](results/stage-38-full-initial.txt)保留：策略曾误拒原生自动插入的max_tokens=1024，修复仅接受这个固定规范参数，其他覆盖仍拒绝；不是前序API状态泄漏。

[新增模块30项](results/stage-38-target-worker.txt)、[预算适配器联测45项](results/stage-38-targeted.txt)通过；45项先于最后1项新进程回归，该项随后纳入最终全量。[最终本地pytest](results/stage-38-pytest.txt) **3974通过、1平台跳过、2项既有SDK警告，148.65秒**。

[Ruff](results/stage-38-ruff.txt)、[核心strict36](results/stage-38-core-types.txt)、[评测审计等strict14](results/stage-38-strict.txt)、[纯清单strict1](results/stage-38-manifest-types.txt)、[隔离入口strict2](results/stage-38-worker-types.txt)通过。两个旧脚本的既有入口检查与原门禁保持；全仓178项债务无新增。新增运行入口使用--follow-imports=silent，不声称其全部既有依赖无类型债务。

[182前端契约](results/stage-38-frontend.txt)、[完整构建/重复性/失败保留](results/stage-38-build.txt)通过，无页面变更不重复视觉检查。[固定旧样例](results/stage-38-compat.json)完全一致：阶段32预测SHA仍3a3b1266…，阶段36估算SHA仍5670d9cc…，未知费用/完成标记/绑定语义保持。

仍待：题意/输出规范与业务标注、真实embedding/语义质量、PG/MySQL集成、生产并发/大Schema/恢复、真实账单/复杂价格、178项类型债务。隔离依赖可信本机代码和独立进程，无恶意代码沙箱、强杀/服务端计费撤销保证或签名证明。正常finished只表示工具产出报告，不表示SQL全正确。

代码[720d8f8](https://github.com/Qorq-byte/Data_engineer/commit/720d8f880eb8849b0be5fcf2085304283c6c64ce)已普通推送。[Actions40209047055](https://github.com/Qorq-byte/Data_engineer/actions/runs/40209047055)成功，headSha精确一致；[结构化证据](results/stage-38-ci.json)、[Linux摘要](results/stage-38-ci-tests.txt)。干净环境**3975通过、2警告、73.78秒**，本地平台跳过项在Linux执行通过；前端182契约/完整构建、Ruff、原核心strict及新增入口检查均通过。

本次补记验收文档及诊断证据空白清理将另行提交推送，核验最终精确SHA CI，不以代码CI替代文档提交验收。不合并main，原主工作区用户修改保留。
