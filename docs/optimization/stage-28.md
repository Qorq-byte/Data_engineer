# 阶段28：离线SQL评测完成状态与统计

2026-10-24，基线3d74f96（阶段27最终Actions39954157256成功，精确SHA一致），独立codex/sql-evaluation-accounting。按[先行计划](stage-28-plan.md)完成实现、本地及精确SHA远程验收。不合并main，原主工作区pyproject.toml/docs/audits和隔离旧未跟踪文件保留。

## 问题与实现

旧evaluate_sql未消费finish_reason/status，合法但明确截断的SQL仍可能计正确；缺失预测分母本来已正确，不改变。latency_ms/cost_usd无校验/汇总/覆盖率，未执行题目也记录近零执行耗时。3项旧代码回归全部失败：[证据](results/stage-28-before.txt)。

- scripts/evaluate_quality.py：复用check_completion，明确未完成记INCOMPLETE_COMPLETION，status=failed优先GENERATION_FAILED，整条预测不进入执行；缺元数据legacy_unknown保留兼容。全部题目仍为成功/正确率分母，新增计数/完成状态分布。保留只读AST/query_only、gold相同预算、超时恢复、重复行/顺序比较。
- 同脚本：有限非负reported遥测、缺测null、失败样本照计、覆盖率/partial total和nearest-rank分位；execution_latency_ms仅计安全执行器尝试，排除gold与比较，未尝试null。案例/预测/fixture哈希、SQLite/limits和measurement_scope防止把外部费用或小样本时延当实测基线。JSON严格序列化后才写文件，坏输入不覆盖原报告。
- app/llm/__init__.py：独立审查发现completion导入经包初始化载入SDK并可能联网取价格表；公共五个导出按需加载，纯检查无SDK副作用，类型检查仍显式导入。独立进程删除价格表环境开关并禁止litellm导入，证明离线评测可运行；没有在工具内设置环境掩盖问题。
- SQLite初始化纳入finally；超大整数资源参数、费用总和溢出统一在打开库前拒绝。typings/jieba声明实际使用的单参数lcut，依据本机0.42.1源码，不使用Any兜底或关闭导入检查；CI新加mypy scripts/evaluate_quality.py，原核心strict36保留。

没有更改在线计划、Runner/节点流转、权限、SQL只读或页面。输入兼容、状态优先级、哈希和恢复说明见[评测契约](sql-evaluation.md)。新导入副作用与资源边界是审查后的计划调整，理由和验证在计划中保留。

## 验证与证据

Windows Python3.13.9/Node24，Ruff0.17.1/mypy2.4.0；沿用原.venv site-packages、隔离type-deps/driver-types。主pytest使用LITELLM_LOCAL_MODEL_COST_MAP=True/RAG_EMBEDDING_PROVIDER=mock及conftest合成认证/LLM_MOCK_MODE，追踪只写忽略目录；离线子进程明确移除前述价格表开关并禁止SDK导入。无生产密钥或外部模型请求。

- 新增61项，`python -m pytest tests/test_rag/test_prediction_accounting.py tests/test_rag/test_quality_evaluator.py tests/test_core/test_sql_quality_boundaries.py tests/test_llm -q`：**314通过、3警告，14.88秒**，[日志](results/stage-28-targeted.txt)。覆盖dict元数据、refusal/tools/status、全部题目分母、零/缺测/失败遥测、负值/NaN/Inf/bool/字符串/求和溢出、结构/ID、分位/哈希、CLI原报告保留、无SDK导入、公开导出身份、初始化关闭和超大限制。spy证明拒绝项只执行gold，不尝试预测；独立审查相关11项再验通过（包含在61项内，不额外累加）。
- `python -m pytest -q`：**3578通过、1跳过、4警告，113.31秒**，[日志](results/stage-28-pytest.txt)，相对3517新增61项；Windows uvloop已由下述Linux实际执行补验。`ruff check .`通过，[日志](results/stage-28-ruff.txt)；原核心**strict36零错误**，[日志](results/stage-28-core.txt)；新增脚本独立strict1文件零错误，[日志](results/stage-28-evaluator-types.txt)。
- `mypy app --output json`仍退出1，**181→178项/41文件**，按规范file/code/message多重集新增0/移除3（jieba两处导入和随之有效的赋值ignore），[诊断](results/stage-28-after.jsonl)/[摘要](results/stage-28-summary.json)。全仓类型仍未通过，不弱化门禁。
- `npm run check:frontend`：5脚本、**182实际函数契约**通过，[日志](results/stage-28-frontend.txt)；`npm run check:frontend:build`：三页46文件、资源/字体/许可证/清单完整性、重复构建与故意坏JS保留旧产物通过，[日志](results/stage-28-build.txt)。页面无改动，未重复视觉测试；7项构建期依赖告警保留。

`tests/fixtures/sql_prediction_accounting.json`固定6题5预测，规范JSON哈希相同，阶段27固定SHA原脚本重放为执行4/6、正确3/6；本版执行2/6、正确1/6，差异仅来自拒绝显式生成失败和length，全部题目分母仍6，reported费用1为2/6样本的部分总额。[完整输入/前后记录](results/stage-28-accounting.json)包含原SHA和哈希，均为人工功能预测，不是模型效果或性能前后对比。可用测试模块重跑当前预期，用本地`git show 3d74f96:scripts/evaluate_quality.py`加载旧函数并对同fixture调用evaluate_sql重放基线；缺原Git历史应报错，不在线寻找替代。

原真实FTS5词法8文档/7查询K3复跑，Recall/MRR/NDCG仍1、Precision约0.380952，与同输入旧脚本一致；无预测时SQL指标仍null。[记录](results/stage-28-retrieval.json)。不把词面烟测或人工SQL统计推广为真实RAG/模型质量。

初版独立mypy暴露jieba缺声明，先补最小实际类型表面，未使用silent或忽略检查；vendor可选参数HMM会违反命名lint，声明最终只覆盖项目实际单参数调用，未错误重命名第三方关键字。初始化/限制问题来自独立只读审查；该审查首次默认临时目录受沙箱限制，改为工作区内独立临时目录后11项通过，环境错误没有计为产品失败。

## 限制与下一步

无生产等价模型预测/usage、业务标注、多方言结果语义、真实费用账单或负载环境。缺完成元数据仍兼容未知；单条外部遥测是否覆盖重试/多候选由采集器保证，本工具不能证明。全量评测只使用合成SQLite fixture；数字分位不是并发吞吐/尾延迟结论。浮点容差/时区仍待业务数据设计，RAG语义指标未测。178类型存量、7构建期依赖告警保留。

下一候选：先核对在线查询的LLM调用/usage/费用及工作流trace是否能为离线预测导出提供可信来源，再独立规划最小采集范围；不在此阶段启动外部模型评测或更改Harness流转。

## Git与远程验收

代码[e0cf146](https://github.com/Qorq-byte/Data_engineer/commit/e0cf1464e2fce695f0af73e18542814bbb209e20)已普通推送codex/sql-evaluation-accounting，远程SHA一致；[Actions39990182348](https://github.com/Qorq-byte/Data_engineer/actions/runs/39990182348)成功，headSha精确匹配。Linux干净安装、182前端契约/完整构建及重复性/故意坏JS保留/Ruff/strict36与评测脚本strict1均通过，全量**3579 passed、1 warning，58.13秒**，uvloop实际执行；[结构化证据](results/stage-28-ci.json)。阶段28代码验收完成，补记文档另行提交推送并核验最终CI；不强推、不合并main，原工作区改动保留。
