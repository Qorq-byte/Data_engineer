# 阶段4：资源边界、恢复与可观测性

完成同连接 lease、工厂原生 SQL/Schema 线程执行与取消清理、深拷贝 Schema TTL 缓存/显式失效、重复连接替换关闭、await 异步连接/池关闭、统一 lifespan 与幂等目录注册、后台任务关闭；保留单进程架构，不引入分布式锁/任务平台。

主查询加入请求容量与截止、同会话串行、TTL/条数/轮数/版本限制；模型调用加入实际并发/RPM/截止/有界重试/冷却恢复，禁止 LiteLLM 隐式重试绕过预算。调用记录有界保留、累计统计不丢失。Trace 包含终态，避免结果行/输入全文摘要，写入失败可观测。增加 /health/ready 区分存活、初始化与实际执行条件。

验证：Windows/Python3.13.9，真实临时 SQLite/DuckDB，无外部模型；第一轮现有相关回归146 passed，新故障/并发回归26 passed。最终全量 `python -m pytest -q --tb=short`：**2942 passed, 4 warnings（63.13秒）**，日志 results/stage-4-pytest.txt。最终定向59 passed（8.80秒），日志stage-4-final-target.txt。改动模块/新增测试/脚本 Ruff 通过，日志 stage-4-ruff.txt；全仓存量 lint/type 债务未以关闭规则处理。

可重跑负载入口 scripts/load_test.py；1万行合成 SQLite、直接执行与固定 SQL 流水线、并发1/2/5/10、每层100请求，结果/前一轮对照见 results/stage-4-load*.json。并发1/2/5流水线全部正确；并发10超过8槽位，92次503、8次成功，拒绝计入错误率，不将快速失败计作成功吞吐。详细指标、噪声解释、部署配置与后续生产压测方案见 runtime-and-load.md。

范围调整：根据检查发现复用 ConfigLoader/PlanLoader 才能让原有缓存生效，纳入本阶段；保留文件变化检测及请求快照。两个启动入口的 catalog 初始化纳入 lifespan，保留既有节点/MCP/助手目录，避免移除 main 预初始化后出现功能回退。版本和模型调用历史的容量限制纳入本阶段，防止长时间运行无界增长。

限制：仅单进程/单 serving loop；真实 PG/MySQL、provider、长稳态未验证，不能据本地实验认定生产瓶颈。RAG 的大规模索引与普通 HTTP 断连语义仍有明确边界，详见运行说明。下一阶段完成可信前端交互、可访问性、脚本/CI 与最终使用文档。

最终复核补充：Runner 合并代码 required_tools 防止 tools 漏声明跳过权限，独立执行也绑定调用预算；强化旧 E2E 后发现 sql 键被验证节点忽略、非查询 AST 被当作合法 SQL，已兼容输入键并拒绝裸表达式/空 SELECT/多语句。新增真实五节点 E2E 与四类非法 AST 回归，未弱化测试。
