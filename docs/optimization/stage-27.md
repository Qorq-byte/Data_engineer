# 阶段27：模型输出完成状态

2026-10-23，基线ba237a5，独立codex/llm-completion-boundary。按[先行计划](stage-27-plan.md)完成实现与本地验证，待提交/远程验收；阶段26最终Actions39950763699已精确核验成功。

## 确认的问题与实施

SQLGenerator._completion_text忽略finish_reason，真实流式适配器丢弃终止元数据；故length返回的语法合法前缀可成为候选。旧代码3个回归全部失败，[失败证据](results/stage-27-before.txt)。SQL语法检查无法发现丢失业务条件，选择边界拒绝而非自动提高token重试。

- app/llm/completion.py：纯判定函数兼容dict/对象，拒绝明确非stop、refusal/工具调用，新增诊断不包含正文/工具参数/未知provider原因。核心strict门禁新增此模块（36源文件）。
- app/core/sql_generator.py：两策略在抽取/切分前检查，舍弃整条未完成响应，保留独立完整候选；无有效候选抛安全错误，请求内warnings显示舍弃，无共享可变状态。
- app/nodes/generate_sql.py与app/workflow/query_service.py：现有generation_error/Runner终止语义；部分候选诊断存metadata/context并合并公开warnings。固定YAML/节点职责/只读/工具权限不改。
- app/llm/router.py：真实LiteLLM流须stop和正常EOF，允许usage空choices，拒绝结束后继续内容/重复终止、非文本及缺标记；上限错误可操作。SQLGenerator→BudgetedRouter→Router逐层finally关闭，适配器关闭SDK流（本机LiteLLM源码具aclose），限流包装释放槽。已产出文本后不重试/fallback、不增加token、不退调用预算。

详见[契约与操作边界](llm-completion-policy.md)。非流式缺结束元数据仍兼容；Mock/自定义文本流无法证明结束状态。公开SSE是工作流事件，当前节点使用非流式模型；另测直接模型流配置，不混淆两条路径。

## 验证

Windows Python3.13.9/Node24，Ruff0.17.1/mypy2.4.0；沿用原.venv site-packages及隔离type-deps/driver-types，LITELLM_LOCAL_MODEL_COST_MAP=True、RAG_EMBEDDING_PROVIDER=mock、conftest合成认证与LLM_MOCK_MODE。追踪输出仅忽略目录。真实Router传输测试显式mock_mode=false、拦截litellm.acompletion，无网络/生产凭据。

- 新增61项：完成状态50、真实SQLite三协议11。覆盖合法但截断的SQL、两策略独立候选/整条alternative、字典/对象、缺省兼容、refusal/工具、未知原因脱敏、stop/usage/异常EOF、取消/主动关闭、后续请求恢复、单槽再次可用、物理预算与无自动重试；节点在流中收到合法前缀后失败也无候选。
- `python -m pytest tests/test_llm tests/test_core/test_sql_generator.py tests/test_nodes/test_generate_sql_node.py tests/test_api/test_query_contract.py tests/test_harness/test_request_invariants.py -q`初版定向**305通过，15.60秒**，[日志](results/stage-27-targeted.txt)。其后补4个节点/并发诊断/全链关闭测试并修复关闭传播；提交前又补5个false/0/空集合等非文本流回归并将检查移到真值判断之前。两次最终全量分别3512/3517通过，交付以3517项版本为准。JSON/SSE/MCP失败无执行、SQLite trace无目标SQL；SSE无validation/execution事件，下一次stop恢复五行。JSON/SSE保留完整兄弟并警告；MCP固定单候选不伪造多候选入口。
- `python -m pytest -q`最终**3517通过、1跳过、4警告，108.45秒**，[日志](results/stage-27-pytest.txt)。Windows uvloop待下述Linux补验；本机新增61项与基线3456一致。`ruff check .`通过，[日志](results/stage-27-ruff.txt)；CI同等核心mypy **36源文件零错误**，[日志](results/stage-27-core.txt)。
- `mypy app --output json`仍退出1，**181项/42文件**，按file/code/message多重集对比新增0/移除0：[诊断](results/stage-27-after.jsonl)、[摘要](results/stage-27-summary.json)。没有降低原类型门禁。
- `npm run check:frontend`：5脚本、**182实际函数契约**通过，[日志](results/stage-27-frontend.txt)；`npm run check:frontend:build`完整三页46文件/本地字体许可证/完整性/字节重复性/故意坏JS保留旧构建通过，[日志](results/stage-27-build.txt)。无页面实现变化，未重复视觉审核；workspace真实错误/警告消费者位于done分支及消息warnings渲染处，未新增页面契约。

首次默认沙箱无法创建pytest临时目录，是环境错误而非产品回归；正常本机权限后3个旧代码回归才复现。初次新增API测试误用sql字段/假设warnings只有一项，实际JSON/SSE为primary_sql、MCP为空字符串且有RAG降级警告；修正新fixture断言并保留原警告，非修改生产接口或弱化已有测试。旧token策略流式spy补正常stop标记，原输出/参数断言全部保留。初扫猜测不存在路径和一次文档标题补丁失败已按实际文件修正，无错误作为验收依据。

## 限制与后续

缺元数据非流式兼容不代表完整；未知原因保守拒绝可能使未适配provider需要独立契约核验。真实模型结束原因规范化/上限执行、业务正确率/成本/性能、MySQL及多worker未实测。上限不是总费用预算；CostTracker的调用记录不等于SQL质量指标。已有181类型错误和7构建期依赖告警保留。原主工作区pyproject.toml哈希不变、docs/audits及隔离旧未跟踪文件未改动/提交。

下一候选：先检查离线NL2SQL评测是否记录完成状态、失败分母和每题成本/延迟，再独立规划；不在本阶段追加评测体系重构。

## Git与远程验收

本地检查完成；提交前做精确范围/凭据/diff检查，普通推送后核验精确SHA的LinuxCI，再补记文档提交并核验；不合并main、不强推。
