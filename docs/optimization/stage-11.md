# 阶段11：SQL校验作用域与可解释评测

日期2026-10-21，基准044f170；阶段10代码及补记CI均成功。按[先行计划](stage-11-plan.md)在独立工作树codex/mvp-hardening实施。

## 实施与代码依据

- `core/schema_validation.py`使用项目已有SQLGlot MappingSchema/qualify，在新解析的AST上按作用域解析CTE、派生表、输出别名、别名遮蔽和相关引用；源表回调阻断不存在的表，不允许到FROM之外的源表查找列，歧义列失败。源列引用信息保留大小写，类型用UNKNOWN避免把Schema引用阶段变为新的类型推断规则。可执行primary_sql不改写，实际三协议回归核对原文和SQLite结果。
- `core/sql_validator.py`硬Schema检查改用上述纯模块，原提取辅助函数保留供既有类型/提示分析；只读判断共用执行层require_read_query。skip_syntax只跳过详细诊断，不能放行副作用、写入或无法安全解析的语句；类型、性能和业务规则仍是原有提示。`_ReadKwargs`明确可选read字段，避免泛型字典展开污染SQLGlot参数类型。
- `nodes/validate_sql.py`在校验器异常时写回validation_passed=False/validation_report=None，避免旧上下文成功标记残留。计划、Runner、节点生命周期、权限、物理预算和执行边界保持。
- `core/sql_generator.py`统一提取字典/兼容对象的文本响应，畸形/空响应逐个忽略，有效兄弟候选保留，全部无效仍明确失败；CancelledError继续传播。首个有效候选标primary，保持只执行主候选的现有契约，不引入自动重试或动态流转。
- `scripts/evaluate_quality.py`增加逐项error_code/error_counts：缺失、空SQL、策略拒绝、SQLite执行错误、行数、截止及结果不符；正确为null。gold与预测共用只读/时间/行数执行边界，无效gold是数据集错误。执行成功与结果正确分开，顺序/重复行规则保留；输入提供的模型延迟/费用不冒充工具实测。
- `scripts/evaluate_sql_validation.py`与18例fixture建立功能决策回归，使用实际SQLite内省为源Schema、安全只读参考执行，并提供固定完整Git SHA的旧校验器重放。每次输出数据哈希和Python/SQLGlot版本，无模型调用、model_accuracy=null。
- `pyproject.toml`把原SQLGlot>=25.0.0锁到本机实际验证的30.18.0，避免宣称未验证的旧optimizer接口兼容；CI干净安装将复核。strict门禁增加纯Schema引用模块，未关闭规则。README/AGENTS/节点指南/查询和评测说明同步。

## 固定功能基线及评测自检

先以实际SQLite探针复现旧实现误判，再固定18例并通过Git原源码保存对照；实现与固定数据记录有交叉，但旧基准由不可变044f170源码重放，前后使用完全相同的评测/标签和SQLGlot30.18.0，不声称来自不同生产流量。

`python -m scripts.evaluate_sql_validation --baseline-stage10 --output before.json`；不加baseline参数输出当前实现。前者退出0仅表示重放完成，不代表案例通过；当前实现案例未全过退出1。规范化数据哈希`d94b860b398190b4434034b6e0ba7f5d01582a8ddca6d2a5691a52d4d668cac9`一致，旧实现**7/18**、本次**18/18**。[前](results/stage-11-validation-before.json)/[后](results/stage-11-validation-after.json)。覆盖合法CTE/派生表/输出别名/别名遮蔽/相关查询，非法越界/歧义列/缺失源表/命名空间/危险SQL；参考执行先过只读策略，不运行危险语句。

[最初探针](results/stage-11-probe-before.txt)/[修复后探针](results/stage-11-probe-after.txt)仅是早期诊断，完整验收以固定案例/最终回归为准。校验通过表示所实现检查没有发现问题，不表示SQL回答了自然语言业务问题。

[评测错误分类自检](results/stage-11-evaluator-codes.json)故意构造8种输入，7种错误类别各1，正确案例通过；超时后下一案例恢复。其执行率/正确率只属于自检，不是模型指标，real_model_metrics=null。真实NL2SQL预测仍需外部模型、业务标注、usage和版本记录；无预测时原命令sql_model_metrics继续null，本阶段未报告真实准确率、成本或性能提升。

## 本机验证

Windows/Python3.13.9、Node24；SQLGlot30.18.0、Ruff0.17.1、mypy2.4.0目标Python3.12。沿用隔离类型声明（types-PyYAML、asyncpg-stubs/开发驱动0.32及本地aiomysql0.3.2声明）；运行测试仍用原site-packages（LiteLLM1.102.0、asyncpg0.31.0），LLM/embedding强制mock。本地SQLite/DuckDB/LanceDB/FTS5是真实实现；MCP是进程内FastMCP Client/Server，未做外部部署联调。

- 定向：`pytest tests/test_core/test_sql_quality_boundaries.py tests/test_core/test_sql_validator.py tests/test_core/test_sql_validator_perf_rules.py tests/test_core/test_sql_generator.py tests/test_rag/test_quality_evaluator.py tests/test_nodes/test_validate_sql_node.py tests/test_api/test_query_contract.py -q`：**203 passed，3 warnings，16.32秒**，[日志](results/stage-11-target.txt)。最终追加原SQL不改写断言后单独复跑三协议CTE/歧义2项通过（13.15秒），[记录](results/stage-11-api-final.txt)。
- 最终全量`python -m pytest -q`：**3075 passed，1 skipped，4 warnings，53.48秒**，[日志](results/stage-11-pytest.txt)。新增56项：18决策案例、必需只读/大小写/4项基础方言与限定引用/旧状态清除，14项畸形兄弟响应、全部无效/取消/兼容对象，评测7类失败与gold数据错误/超时恢复，以及2项三协议主流程。Windows uvloop1项明确跳过，由Linux补足。
- `mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py app/workflow/contracts.py app/knowledge/schema_graph.py app/core/schema_validation.py`：**30源文件无错误**，[记录](results/stage-11-core.txt)，默认追踪导入。
- 全仓`mypy app/ --output json`：**198→197项，42文件，仍退出1**，按文件/错误码/消息多重集比较新增0。[诊断](results/stage-11-after.jsonl)/[摘要](results/stage-11-summary.json)。门禁通过不等于整个SQLValidator/API/RAG已通过strict。
- 全仓Ruff与前端三页五段编译通过，[Ruff](results/stage-11-ruff.txt)/[前端](results/stage-11-frontend.txt)。无UI行为改动，不重复浏览器或生产压测。
- `git diff --check`通过；原主工作区pyproject.toml SHA256仍E9DEAEC2052B3410FC0A3C54374DA73D3F41173D4FDDFD8FF78833A87F1011F3。仅优化工作树的依赖声明修改，用户改动及临时文件不提交。

本机.venv解释器仍失效，用D:/Users/anaconda/python.exe加原site-packages；设置LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、DE_TRACE_DIR为忽略目录。CI按锁定依赖干净安装。

## 调整与兼容限制

第一轮全量只有旧节点skip_syntax测试失败，因其要求无效SQL通过；连同核心旧测试改为明确只读拒绝/False上下文，并补合法跳过用例，重跑全量通过。首次用dict[str,str]展开read参数引入11项类型诊断，改为精确TypedDict后无新增，不靠Any/ignore掩盖。SQLGlot版本固定和MySQL同库限定支持是核对实际依赖/Schema信息后的范围补充。

命名空间：仅SQLite main和与快照database_name相同的MySQL数据库限定能由当前模型证明；其他显式schema/catalog（包括未建模的PostgreSQL public）保守失败。这可能拒绝原来接受的合法限定SQL，须先补命名空间元数据和实际数据库回归再放开，不能用另一个同名表冒充来源。未限定的PostgreSQL/MySQL/SQLite及quoted identifiers有解析测试，不代表在线多方言验收。

资格化只用于引用检查，不是SQL语义/类型/业务结果的完整证明；类型提示仍有既有独立分析边界，未知UDF按现有只读策略拒绝。SQLGlot版本升级需重新跑固定集。统计/模拟模型费用、浮点容差、真实NL2SQL/PG/MySQL与生产容量仍未验收。

下一候选阶段先核对数据库命名空间/方言元数据和验证前置条件，再按实际缺口规划，不直接扩大执行权限或LLM流程控制。

## Git与远程验收

本地验收完成；提交、普通推送和对应Linux CI按实际结果补记。远程未完成前不声明阶段远程验收通过。
