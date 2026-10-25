# 离线评测文件与输入边界（阶段37）

evaluate_quality、evaluate_schema_linking、evaluate_sql_validation复用阶段36的严格JSON读取、路径保护及原子写入工具。此变更不运行真实模型/embedding或外部业务数据库，不改变Harness、SQL只读规则、评测分母和报告文件格式。

## 文件与命令

三个入口的JSON输入每文件最多16MiB、64层容器，拒绝重复键/非有限值，接受UTF-8 BOM。写报告前检查输出不得与数据集、预测文件或其硬链接/路径别名相同；quality/sql_validation还保护底层tests/fixtures/test_schema.sql。全部读取/校验及评测成功后，同目录临时文件原子替换报告；输入/IO/可分类评测错误保留旧报告、清理临时文件，不创建或截断输入。

```bash
python -m scripts.evaluate_quality --dataset tests/fixtures/retrieval_eval.json --predictions docs/optimization/results/stage-32-predictions.json --output data/quality.json
python -m scripts.evaluate_schema_linking --dataset tests/fixtures/schema_linking_eval.json --output data/linking.json
python -m scripts.evaluate_sql_validation --dataset tests/fixtures/sql_validation_eval.json --output data/validation.json
```

sql_validation新增可选--dataset，缺省仍原合成fixture；其他默认数据路径不变。--baseline-stage10仍载入固定Git SHA的历史validator，在文件保护/数据预检后才读取历史代码，历史回放仍按原规则退出0。浅克隆若缺历史对象会明确失败，不自动下载或换基线。

CLI退出规则：quality成功产出报告为0，不代表SQL预测全部正确；两个节点评测器有效报告中有决策不匹配仍写报告并退出1。输入/IO/可分类评测错误退出2，固定错误不回显数据/SQL/路径或traceback。历史CLI传播ValueError所造成的退出1/traceback改为上述退出2，程序调用evaluate仍抛ValueError。

stdout统一为counts和哈希摘要（dataset_sha256、report_sha256，及适用的query_count/document_count/case_count/passed和sql_计数），不再输出详细SQL错误、命中、scope或完整统计树；完整内容仍在报告文件，报告自身不新增此摘要字段。读取旧stdout的自动化需改读报告，文件格式和旧哈希算法未变。运行耗时不是复现常量，报告hash用于标识此次输出，不是签名。

## 结构与资源预检

app/evaluation/inputs.py是无SDK的纯预检模块，evaluate入口及CLI均使用；不修改输入或gold标签。文档/检索查询/节点案例/SQL案例/预测至多10000，k必须为1–1000整数（非bool）。节点cases和检索queries非空，空检索文档、空Schema允许；纯检索不强制SQL案例存在，传预测时仍要求SQL案例非空。

必填对象/列表/文本、短标识和域、布尔标记按结构检查，重复案例/文档/查询ID或集合标签拒绝。长文本/SQL最多1MiB UTF-8，短标识/域最多512字节、版本256字节。模型预测非字符串或空SQL继续计EMPTY_SQL；含NUL的非法模型SQL仍进原只读门禁计失败，仅超限字符串作为整批输入错误，不借预检移出失败分母。

Schema最多1000表，每表列/引用列表及每案例命中/expected列表至多1000，总列/引用各最多10000。引用须三段字符串；命中字段与SchemaSearchResult构造参数匹配，拒绝会与固定doc_id/rrf_score冲突的字段及未知字段，布尔/rank/raw类型明确。失效/跨库命中、缺表FK、错误expected标签等仍可作为功能边界数据，预检不要求其属于当前Schema或自动改成“正确标签”。

quality在BM25构造前完成数据结构、预测ID/遥测、费用溢出及导出v1/v2来源/哈希预检；来源不匹配保留原先优先拒绝行为。gold SQL的真实安全执行仍在SQL评测阶段，不能把结构预检等同业务gold正确性检查。SQL验证fixture的初始化、Schema建立和逐案例循环都受finally关闭连接保护。

## 类型和验证边界

原核心/导出/审计/费用strict门禁不变，新预检模块独立strict。两个旧节点评测脚本依赖13项存量类型错误（包含在既有178项全仓债务中）；新增脚本门禁使用--follow-imports=silent，仅严格检查脚本入口，依赖仍由全仓无新增核对，不声称这些债务已修复。

这些检查只保证输入/文件/资源与可复现口径，不证明真实模型质量、业务标注、生产时延或provider费用。原v1/v2预测与独立费用估算契约保持。
