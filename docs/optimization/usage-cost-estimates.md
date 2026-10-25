# 离线请求用量费用估算（阶段36）

此工具只读操作者选定的本机审计记录和显式价格表，输出独立估算报告；不调用LLM、在线查价、业务SQL或旧CostTracker，不改审计库和在线API。默认没有真实价格表，不自动启用任何计费功能。样例只用于合成功能验收，不能作为供应商当前价格使用。

## 运行与输入

```bash
python -m scripts.estimate_usage_cost --audit-db data/llm_usage.sqlite3 --query-id q_000000000001 --prices data/flat_prices.json --output data/usage_estimate.json
```

--query-id可重复指定不同请求，最多100个，重复ID拒绝；只读取显式选择，不列出/扫描全库。审计v1/v2均可，缺记录/未来版本/非法候选摘要版本/矛盾用量摘要直接拒绝整次输出。v2候选摘要与费用无关，不按SQL候选或评测案例分摊请求用量。报告不把候选绑定解释为费用认证。

价格表使用flat-token-prices-v1、currency=USD、label、as_of及prices数组。日期须合法YYYY-MM-DD，仅表示操作者标注的价格版本，不证明当前或历史适用性。每项只接受provider/model、prompt_usd_per_million、completion_usd_per_million，最多1000项；按身份精确匹配，无别名、前缀转换或fallback。重复/未知字段/其他币种或版本拒绝。

两类单价必须为非负固定小数字符串或null（缺价格），不接收JSON数字、bool、科学记数或非有限值；至多9位整数/12位小数。合法0是显式零价格，null不能替成0。缓存、批处理、折扣、工具、图片、分层价格、税费及汇率不在平坦公式内；价格表不能添加这些字段来暗示支持。

## 计量、缺测与精度

仅已收尾、transport_boundary、provider/model身份明确且usage_basis为response_fields/sdk_normalized的attempt可估算。adapter_boundary、unknown/redacted/mock等身份、未知usage_basis、pending均不可估算；模型别名不猜。两类token须都有合法观察值且不超过有符号64位上限；非法摘要拒绝，已规范化的缺测保留null，不从部分token外推。

每attempt的公式是 `(prompt_tokens × prompt单价 + completion_tokens × completion单价) / 1,000,000`。Decimal在局部精度64计算，保留完整小数，输出规范十进制字符串，不按美分提前舍入；覆盖率为比例值，金额不用float。合法0 token或显式0单价可产生"0"，但0 token遇到未知价格仍保持null。SDK归一化0只是SDK观察值，不能证明原始provider字段存在。

失败/timeout/cancelled/closed但有两类已观察token的attempt仍参与估算，不按状态退款；pending不可估算。每条unavailable_reasons列出所有阻断条件。known_subtotal_usd只累加完整可估算尝试，有样本但和为0保留"0"，没有样本为null；不是部分token的成本推算。

请求estimated_total_usd仅在全部观察尝试可估算、没有dropped、llm_calls等于attempt_count+dropped时提供；无尝试、缺整个usage、不完整/计数不符均null。批次只有所有请求均具总额时才有总额，否则仍只报告已知部分和。priced_attempt_coverage的分母为已记录attempt+dropped；缺整个usage或没有分母时为null。每个query_id只算一次，不把重试、多个候选和多库用量再按候选重复相加。

## 输出、安全和旧接口

usage-cost-estimate-v1报告含estimate_scope=flat_prompt_completion_tokens_entire_query、billing_verified=false，记录人工价格表label/as_of/规范哈希、每条audit规范哈希、query_id/outcome、attempt来源/usage_basis/价格/计数、不可估算原因及请求/批次金额。报告自身有规范JSON SHA256，空白和键顺序不影响身份。哈希用于复现和改动检测，不是签名；操作者能修改数据及全部哈希，不能据此证明账单真实。

仅输出allowlist，不复制问题、SQL、会话或原始异常。价格文件最多16MiB，拒绝重复JSON键、非有限数值、过深结构；输出不得覆盖价格文件、审计库或其wal/shm/journal路径（含别名/硬链接）。全部验证后同目录临时文件原子替换；失败保留旧报告并清理临时文件，CLI固定错误不回显路径/SQL/私密输入。SQLite读取仍沿只读接口，不创建缺失审计文件。

原预测导出的cost_usd和生成耗时保持null；不能把整请求人工平坦估算写成某候选的reported费用。审计schema、在线JSON/SSE/MCP、Harness、预算和重试不变。共同的严格用量验证器和文件IO复用，原导出入口与固定v1样例保持兼容。

## 合成样例重跑

[flat_token_prices.json](../../tests/fixtures/flat_token_prices.json)标注synthetic-fixture-only，针对不存在的fixture模型；金额是任意合成单价，不是实时或官方价格。[usage_cost_audits.json](../../tests/fixtures/usage_cost_audits.json)是UsageAuditRecord构造参数列表，固定时间/耗时仅为复现输入，不是负载性能测量。

先在仓库根目录用Python建立单独合成库（请勿指向已有业务/审计文件）：

```python
import json
from pathlib import Path
from app.audit.store import UsageAuditRecord, UsageAuditStore
store = UsageAuditStore("data/fixture_usage_costs.sqlite3")
for row in json.loads(Path("tests/fixtures/usage_cost_audits.json").read_text(encoding="utf-8")):
    store.write(UsageAuditRecord(**row))
```

```bash
python -m scripts.estimate_usage_cost --audit-db data/fixture_usage_costs.sqlite3 --query-id q_000000000001 --query-id q_000000000002 --prices tests/fixtures/flat_token_prices.json --output data/fixture_usage_estimate.json
```

结果应与[阶段36样例](results/stage-36-cost-estimate.json)一致：2请求、3观察尝试、2个可估算（含一个合法0），已知部分和"0.000007"，批次总额null。它只验证算法与缺测口径，不是实际支付费用或供应商账单校准。
