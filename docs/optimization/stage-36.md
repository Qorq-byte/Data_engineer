# 阶段36：离线请求用量费用估算

2026-10-25，基线阶段35最终f4402c5；独立codex/offline-usage-estimates/.stage36。先行设计见[计划](stage-36-plan.md)，接口和重跑步骤见[估算契约](usage-cost-estimates.md)。承接尚未合并main的阶段29—35，不改原工作区，不强推、不自动合并。

## 结果与范围

新增纯模块app/evaluation/usage_costs.py及CLI scripts/estimate_usage_cost.py，只读显式选择的审计v1/v2和操作者平坦价格表。无默认真实价格、不在线查价、没有LLM/embedding/业务SQL调用，不改在线响应、预算、重试或审计schema。最多100个唯一请求，重复ID/缺记录/未知版本/矛盾摘要拒绝整次输出。

flat-token-prices-v1只支持USD，两类每百万token单价为受限非负固定小数字符串或null；拒绝重复项、未知字段、float/bool/非有限值/科学记数和无效日期。provider/model精确匹配，不猜别名。价格表日期是操作者标注，不是市场有效性证明；缓存/批处理/工具/图片/折扣/分层/税费等不在公式内。

局部Decimal精度64精确计算，金额为规范小数字符串，不提前按美分舍入。只计可确认的transport_boundary及已有合法token和单价；adapter/不明身份/usage_basis未知/pending/超范围token列原因。合法0保留，未知不补0，包括0 token遇到未知价格。失败/取消但有观察token不按状态退款；sdk_normalized仍只是SDK观察值。

逐attempt、整请求及批次明确区分完整总额与已知部分和；无样本为null，dropped/未定/预算计数不一致时总额为null。一个请求只计一次，不按候选重复分摊。旧预测cost_usd/生成耗时仍null，不把人工平坦估算当reported账单。

新增共享artifact文件工具，旧脚本入口/替换钩子与临时文件前缀保留；用量严格验证器移到审计模块共用。有限JSON、重复键/非有限值/深度/大小检查、输入路径/硬链接保护、原子替换及旧输出保留继续有效；新CLI额外保护审计wal/shm/journal路径。报告只复制allowlist元数据，不含SQL/问题/会话/原始异常，保留价格/审计/报告规范哈希。

## 验证与复现

新增50项。首轮[42项失败回归](results/stage-36-initial.txt)全失败，实现后与旧导出合计96项通过；补6项真实SDK两候选/替换失败/硬链接/sidecar/别名回归，最终[关联563项](results/stage-36-target.txt)通过，再补2项0 token未知价格及批次部分和纳入全量。

本地[全量pytest](results/stage-36-pytest.txt) **3889通过、1平台跳过、2警告，122.34秒**。真实LiteLLM SDK合成传输产生两次候选调用→审计v1/v2→独立CLI估算进程，整请求只估一次、2尝试为0.000014合成USD；同请求选择候选导出仍cost_usd=null。CLI只读库字节不变，重复/缺记录/输入别名拒绝，替换失败保留旧报告并清理临时文件。新进程阻断litellm/mysql/httpx/requests导入后仍可加载工具，无外部模型/价格/业务DB请求。

[核心strict36](results/stage-36-core-types.txt)、[估算/文件工具/CLI及原审计/LLM/导出strict12](results/stage-36-strict.txt)、[Ruff](results/stage-36-ruff.txt)通过；CI新增3文件strict门禁。全仓mypy相对阶段35仍178项，按file/code/message多重集新增0，[摘要](results/stage-36-types-summary.json)。[182前端函数契约](results/stage-36-frontend.txt)、[三页完整构建](results/stage-36-build.txt)、重复性/故意坏JS保留旧产物通过；无页面改动不重复视觉检查。复用Python3.12验收环境及锁文件离线npm缓存，Windows异步测试使用沙箱外socketpair。

重跑阶段32固定输入，[兼容证据](results/stage-36-compat.json)仍与原2条预测完全相等，哈希3a3b12662c3d27ebddddee556c805504bba5ddf9adb6f52ae532763b7182d4f8不变。

[固定合成审计参数](../../tests/fixtures/usage_cost_audits.json)和[人工合成价格](../../tests/fixtures/flat_token_prices.json)可按契约重建单独SQLite库，再运行CLI，得到[样例报告](results/stage-36-cost-estimate.json)：2请求、3尝试、2可估算（含一个合法0）、已知部分和0.000007、批次总额null。固定耗时/时间和价格仅复现功能口径，不是当前官方价格、性能或真实支付费用；没有用样例宣称账单校准。

## 限制与交付

报告billing_verified=false，价格来自操作者、用量来自所观察审计，不证明原始provider字段完整、实际计费或真实业务质量。哈希不是签名；复杂价格、真实模型质量及账单对账、签名/防篡改审计后续独立规划。原Harness/权限/只读和请求用量语义保持。

本地验收完成，代码推送及最终文档提交的精确SHA CI随后补记。
