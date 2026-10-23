# 阶段26计划：LLM输出参数上限与未支持选项

2026-10-23，基线1a80342（阶段25最终Actions39947927462成功），独立codex/llm-token-policy；main不合并，原工作区pyproject.toml/docs/audits和隔离旧未跟踪文件保留。使用brainstorming比较方案、planning-with-files沿用本目录记录，按用户授权先规划后实施。

## 已确认与取舍

factory.build_router_from_settings没有读取routing_strategy/temperature/max_tokens；RouterConfig无这些配置，_get_provider_chain按主模型/fallback顺序，SQLGenerator三处显式temperature=0.3。页面保存三个值并反馈应用会误导。仅全部只读能止误导但缺少输出成本控制；同时实现自适应路由或全局温度覆盖缺真实评测且影响既有生成，超出MVP。本阶段只接max_tokens为输出请求参数上限，另两项只读并拒绝写，不改既有模型选择/调用方温度。

## 顺序和验收

1. 旧代码保存max_tokens未进入RouterConfig、未支持策略/温度仍接受的失败回归先行留证。
2. RouterConfig新增可选max_output_tokens，原独立/环境路由默认None保持现状，设置生成路由默认4096、严格正整数范围1–32768。公开complete/stream在预算/外部调用前规范max_tokens/max_completion_tokens：正整数且非bool，仅一个有效别名，调用较小值保留/较大值截到上限，未指定补max_tokens；显式None视为未指定，双有效别名拒绝，extra_body内同类覆盖拒绝。到真实LiteLLM传输前复用规范化，避免私有调用旁路；重试/fallback沿用同一选项，不给LLM节点流转权。Mock仅校验参数，不把合成文本当真实token约束证据。
3. LLM API max_tokens仅严格整数；routing_strategy/temperature写入任意值（含null）拒绝整次请求，旧表两项保留但公开明确unsupported_fields/legacy_ignored_fields及行为来源。GET/PUT/DELETE共同报告实际runtime.max_output_tokens（无实例或未设上限为null），配置值不冒充运行值；源字段形状/旧无效上限要明确失败而非静默默认。继承阶段25候选→持久化→发布、取消/未知/仅内存、脱敏与权限边界。
4. 页面移除策略选择与温度滑块，不发送这两个字段；显示固定主模型/fallback及调用方温度说明，输出上限整数输入、已配置/当前运行/仅内存和漂移可辨。严格读取/保存/删除响应，不能以HTTP200或配置值代替运行值，保留四写入口恢复语义。
5. 真实SQLite保存/冷恢复/只读与输入边界、真实Router+LiteLLM传输spy（无网络）验证非流/流/别名/重试/fallback/预算计数及显式温度不变；原测试不弱化，确有兼容变化的测试改为新业务契约。新增实际页面函数契约/gstack桌面/375px/非法响应。运行pytest全量、Ruff、strict35，全仓mypy相对181项新增0，既有174前端及新增、完整构建/重复性/失败保留。
6. 更新接口/部署/README/阶段报告和证据；凭据/范围审查后普通提交推送，核对精确SHA/Linux CI，再补记文档提交推送/核对CI。

边界：输出参数上限是单次调用（n候选的每条输出），不是整次查询token/费用预算，mock不代表真实限制。真实provider参数兼容、返回截断、NL2SQL正确率、成本及性能待真实环境评测，无跨worker广播；181类型存量/7构建期依赖告警保留。初期rg猜测不存在的runtime_budget测试已用rg --files修正，未据此判定能力缺失。下一步先失败回归。

步骤1–5完成：旧3例失败→3通过，新增40项/定向179/全量3456通过、Windows uvloop1跳过（104.14秒）；前端182契约/构建/Ruff/strict35通过，全仓181项/42文件且新增0。gstack12场景及375px通过；兼容性测试调整保留原断言目的，详见stage-26.md。步骤6文档/证据已整理，待提交/远程CI。
