# 阶段39计划：版本化SQL输出契约与反例验收

2026-10-26，用户要求规划并实施。基线阶段38最终c2f5050（最终CI40209475879成功）；独立codex/versioned-sql-acceptance/.stage39，保留原主工作区修改及未合并main状态。沿用用户已选择的仓库合成SQLite数据，不接入业务数据。使用brainstorming/planning-with-files；已比较修改旧标签、放宽旧评分、新建版本三条路径，选择独立v2，不回改v1历史分数。

## 设计

1. 新增固定v2标注：4个独立案例ID，中文题意明确列名、列序、排序、过滤与空集合行为。订单ID升序；completed订单amount合计/不扣退款/无匹配0；用户ID与订单数/包含零订单/按ID升序；只取category精确等于unknown的商品ID/排除NULL及其他类别。gold SQL与手写expected rows相互核验，不用模型生成标签。
2. 独立Schema和base/edge/empty三组固定数据：edge包含零订单用户、同名用户、重复金额、非completed订单、NULL/unknownish/Unknown类别及两个unknown匹配；empty覆盖空聚合。Schema一致。v1 retrieval_eval.json/test_schema.sql和原指标/旧样例字节不变。v2与v1任务不同，不能直接将两次分数相减称提升。
3. 从旧评测器提取共享的安全SQLite查询原语，增加返回列名，旧_rows适配及错误码/限时/行数/只读保持。新v2评测先在三库核验gold的列名/行及手写标签，初始化也finally关闭；同一预测每库本地执行，精确核对列名/顺序/值/排序。失败/缺预测/截断/缺stop保留4题分母，报告12个检查及每题三库全部匹配指标，不把12次SQL检查当12次LLM调用或费用。
4. 新离线CLI严格JSON/输入路径保护/原子报告/固定错误；准备与离线评测不导入SDK。准备可做本地gold自检，gold/expected rows不进入prompt/公开调用清单。清单包含新数据/全部fixture摘要、版本、4个问题及3组本地评测范围。保存预测的provenance仍按既有v2审计绑定校验，不能跨数据集重用。
5. 受控模型CLI显式--profile选择v2，默认v1兼容；沿用隔离进程/新目录/每题1次，总4次、32KiB消息/1024输出token/30秒请求/45秒题/180秒批次，retry0/fallback空。供模型的仅base Schema和明确问题，三组数据不重复生成；正式Harness/权限/路由/JSON/SSE/MCP不变。真实费用未知保持null，不三倍分摊token或费用。

## 实施顺序与验收

- 先失败回归：零订单下INNER JOIN/COUNT(*)、按同名分组、SUM DISTINCT/扣退款/漏状态、空集捷径/类别反向、漏排序/多列/错别名、错gold/标签在SDK前拒绝；安全/取消/缺测与完整分母。
- 再实现共享原语、v2数据/标注自检、评测/离线CLI、profile接入。实际SDK合成传输走新进程完整4题→v2审计→12项评测，证明未把gold/expected放入prompt且仅4次尝试。定向复核v1历史指标/样例。
- 给出具体v2调用清单和资源上限后，仅在本阶段4次范围执行一次真实DeepSeek批次；失败不补跑/不换Mock。若凭据/网络受阻如实记录，不把离线响应冒充真实模型。
- 全量pytest、Ruff、原核心/评测strict、新模块strict及隔离入口门禁、全仓178类型存量无新增，182前端/完整构建。业务质量、真实embedding、PG/MySQL、生产容量、账单、类型债务不在本阶段声称完成。
- 文档/脱敏证据，提交普通推送，精确代码SHA CI；补记验收文档后核验最终SHA CI，不自动合并main。原始真实响应/trace/审计只存忽略目录。

## 本地及真实验收结果

新增46项，195项定向联测通过；最终4020通过/1平台跳过/2既有警告（140.85秒），Ruff、核心strict36/评测等strict17/入口strict4、182前端/完整构建通过。全仓178债务新增0，v1原始字节/样例哈希/同输入报告（排除实测耗时）不变。独立复查的排序巧合与EXPLAIN日志回显均先复现再修复。真实DeepSeek一次4题/4次调用，4个候选绑定、12/12本地契约检查、4/4 robust，费用null；无补跑或模型提升结论。详见stage-39.md与脱敏证据。待推送及精确代码/最终文档CI，不合并main。
