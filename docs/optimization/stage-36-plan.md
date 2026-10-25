# 阶段36计划：离线请求用量费用估算

2026-10-25，用户授权规划后实施。从阶段35最终f4402c5建立codex/offline-usage-estimates/.stage36；保留原工作区，不强推、不合并main。使用brainstorming和planning-with-files，沿项目阶段设计文件约定。

## 方案与取舍

比较真实模型评测、签名审计、离线估算：前两者需要模型环境或额外密钥体系；采用已具备请求审计的离线估算。价格完全由操作者提供，不内置真实供应商价格、不联网查价、不调用SDK或业务SQL、不修改在线响应/预算/审计schema。只读显式选定query_id，不自动扫描全库。

新增纯模块app/evaluation/usage_costs.py及scripts/estimate_usage_cost.py，CLI要求--audit-db、重复--query-id、--prices、--output。最多100个唯一query_id，读取既有审计v1/v2；缺记录/非法版本/矛盾聚合直接拒绝整次输出。候选摘要不参与计价，不按候选/案例重复分摊。旧预测cost_usd仍null，不把人工估算冒充reported费用。

价格表flat-token-prices-v1，USD，显式label、as_of及prices数组；按provider/model精确匹配，拒绝重复项/未知字段/不支持币种或版本。prompt_usd_per_million、completion_usd_per_million为非负固定小数字符串或null，不用float，不支持科学记数/隐式汇率。最多1000项，数值最多9位整数/12位小数；日期只表示操作者标注的价格版本，不证明市场有效性。样例仅synthetic-fixture-only，不引用任何真实当前价格。

仅已收尾、transport_boundary、身份明确且usage_basis为response_fields/sdk_normalized的attempt可计价；bool/负数等非法token继续拒绝或保留缺测，合法0保留。adapter/unknown/redacted/mock等无法确认身份不自动猜价格；缺任一token/单价、pending、来源不明均列原因。取消/失败但有已观察token的尝试不退款；SDK归一化0仍只是SDK观察值。每条token超过有符号64位范围视为不可估算，不从部分token外推。

Decimal局部精度64做两类token的平坦每百万估算，输出规范十进制字符串，不提前按美分舍入。逐attempt estimated_usd为值/null；请求known_subtotal_usd仅累加完整可估算尝试，有样本且为0时保留0，无样本为null。estimated_total_usd仅全部观察尝试可估算、无dropped且预算/attempt计数一致时提供；空尝试/缺整个usage/不完整保持null。批次同理，不把部分和当总额。缓存/批处理/折扣/工具/图片/分层价/税费不在平坦公式内，不宣称账单完整。

报告usage-cost-estimate-v1保留pricebook规范哈希、每条audit规范哈希、query_id/outcome、计数、覆盖率、来源及不可估算原因；只输出allowlist元数据，无SQL/问题/会话/原始异常。有限JSON/重复键/大小/深度/输入别名保护/原子输出沿原导出策略；抽取共享文件工具并保留旧脚本兼容入口。将原用量严格验证器移到审计模块公用，保持旧导出语义。

## 实施与验收

1. 失败回归：精确十进制/合法0、两类单价缺测、未知身份/来源、重试/失败/取消/未收尾、dropped/预算差异、重复请求不双计、非法/重复价目、缺记录/旧新版审计、输出及输入保护、无SDK/网络/业务SQL。
2. 实现纯估算、共用严格用量验证/文件IO、CLI与合成价格样例；真实SDK合成传输→审计→独立估算进程，保留原预测费用null。
3. 定向/全量pytest、Ruff、原strict36+9及新增模块/CLI/文件工具strict；全仓相对178项无新增。182前端契约/完整构建。旧预测固定样例哈希一致。
4. 契约/README/总计划/进度/报告及原始证据，普通提交推送，核验代码精确SHA CI；补记文档后核验最终CI。不启用线上费用功能，不自动合并main。

自检：人工平坦价格和审计观察值只能复现算术，不是实时价格、实际计费或业务质量结论。真实模型与账单校准、复杂价格、签名审计另行规划。
