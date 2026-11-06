# 阶段 68 发布门禁核对

2026-11-06。本记录汇总已执行的验收，供后续发布候选逐项核对。历史的[受限试用检查单](release-checklist.md)仍提供单 worker 的配置、启动、备份和回退步骤。

## 已取得的证据

- **浏览器链路。** 基线 `edcc099` 与修复前端 `58c684d` 在一次性 MySQL 上用本地 Ollama 生成 SQL。登录、选库、真实 SQL 五行结果和 390×844 历史重开均通过，浏览器候选来源与三次模型调用的持久化审计一致。检索使用测试嵌入。修复后嵌入告警仍可见，只有模拟生成显示 MOCK 标签。
- **模型容量。** [Ollama 嵌入容量首轮](ollama-capacity-probe.md)已实测，结果对应固定输入、模型和本机环境。DeepSeek 容量工具提交 `edcc099` 的 [MVP checks](https://github.com/Qorq-byte/Data_engineer/actions/runs/42168250569) 与 [Real database acceptance](https://github.com/Qorq-byte/Data_engineer/actions/runs/42168250564) 通过。付费并发实测结果仍需单独归档。
- **固定配置多 worker。** [同版本代际回退、独立 MySQL 恢复、跨代码版本回退](immutable-workers.md)均有一次性 MySQL 演练。前提是停服维护、独立发布清单和索引、单机同配置的 worker 以及共享 MySQL 控制面。

## 候选发布检查

1. 冻结候选提交和对应的前端构建清单，记录该 SHA 的常规 CI 和实库 CI。
2. 在候选配置下核对认证白名单、readiness、只读查询、审计，以及工作台查询结果和历史。
3. 为候选生成数据库版本、模型、索引和回退清单。回退时使用成套源码、前端产物、配置、索引和兼容的控制面库。
4. 按候选支持范围确认实库和业务质量证据。SQLite、DuckDB、PostgreSQL 和 MySQL 有现有只读验收。固定配置多 worker 的部署证据以 MySQL 为目标。其他方言需逐一具备独立实库证据才进入发布范围。
5. 业务准确率和语义检索质量需要脱敏业务题集、Schema 与人工答案。真实供应商容量需要完成固定批次并复核统计。按实际证据填写发布结论。
