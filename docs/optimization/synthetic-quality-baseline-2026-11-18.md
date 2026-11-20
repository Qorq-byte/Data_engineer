# 合成质量与容量基线（2026-11-18）

本阶段统一运行已有离线探针，输出见
[机器可读汇总](results/synthetic-quality-baseline-2026-11-18.json)。命令是：

```powershell
.stage62/.venv/Scripts/python.exe -m scripts.run_synthetic_baseline `
  --output data/synthetic-baseline-20261118/aggregate.json `
  --requests 20 --rows 1000
```

解释器为 Python 3.12.13，所有数据来自仓库 fixture 和临时 SQLite；LLM、embedding、云数据库调用均为 0。
每个子报告有独立 SHA256，汇总报告记录数据集版本、来源、参数和限制。

## 结果

- 检索：`synthetic-smoke-v1`，7 查询/8 文档，BM25 FTS5，K=3；Recall@3 **1.0**、Precision@3 **0.380952**、MRR **1.0**、nDCG **1.0**。这是词法合成集结果，不是语义召回或业务准确率。
- Schema linking：`schema-linking-boundary-v1`，12/12 边界用例通过；验证表集合、列合法性、来源范围和警告可见性，不模拟真实检索器。
- SQL validator：`sql-validation-boundary-v1`，18/18 通过；使用临时 SQLite 交叉验证只读策略和 Schema 判断，不等同模型准确率。
- SQL contract：4 个问题、3 个 SQLite 场景，gold 自检通过；无预测输入，robust match **0/4** 和缺失预测不能解释为模型准确率 0。
- 容量探针：direct SQL 与 fixed-SQL pipeline，各 20 请求、并发 1/2/5/10、每场景预热 3 次、行上限 1000；运行在进程内 ASGI 和内存 SQLite，固定脚本 SQL，无模型/RAG/lifespan。并发 10 的结果包含真实背压状态，不能外推生产吞吐、延迟、成本或 SLO。

## 适用范围与待补数据

这份报告只证明评估工具可重复运行、分母完整、失败分类和容量探针输出可追溯。它不建立业务答案准确率、语义召回、模型置信度校准或生产容量基线。

要完成业务质量验收，需要脱敏问题集、独立人工 gold SQL/结果、dev/holdout 切分、相关性标注和预先批准的阈值。要完成生产容量验收，需要目标部署拓扑、模型/embedding 预算、并发与延迟 SLO、错误/取消规则和成本边界。
