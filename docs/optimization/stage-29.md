# 阶段29：请求级 LLM 用量记录

2026-10-24，从 origin/main 00640ac 创建独立 codex/request-llm-usage，工作区 .stage29。原主工作区 pyproject.toml、docs/audits 与既有工作区均保留。按 [阶段计划](stage-29-plan.md)实施，不合并 main。

## 实现与边界

新增 app/llm/usage.py：独立 ContextVar、有界 collector、每次尝试的来源/完成状态/单调耗时、可空 token、缺测原因、覆盖率及脱离内部记录的摘要。预算拒绝与排队取消不计 provider 尝试。原生路由逐次重试/fallback记录，适配器单独标来源，原生 BudgetedRouter 不双计。多库和多候选共用请求采集器，finally 重置。

正常/异常/超时/关闭/取消均收尾，流式末尾 usage 可在 stop 后到达；失败有 usage 时仍保留，length 和工具/拒绝状态不能记为正常完成。无 finish 信息保留 unknown。流式上游和并发槽随原生命周期释放；旧 CostTracker 的流式成功记录也移到消费结束。Collector不改变 Harness 固定计划、节点生命周期、预算、重试和 SQL 只读约束。

JSON QueryResponse、SSE done 和 MCP nl_query 共用 llm_usage 投影。详细字段及部分和/覆盖率语义见 [契约](request-llm-usage.md)。本阶段不新增费用估算、价格表、持久化、预测导出或模型调用。旧 CostTracker 仍是历史近似统计，不代表请求账单。

## 验证

基线 00640ac 合成重放复现两项缺口：请求观察为0（期望1），流式消费前成功记录为1（期望0），见 [基线证据](results/stage-29-before.txt)。该重放在实现后进行，用原 SHA 源码独立模块执行，不把它描述为先于实现的红灯测试。

定向 LLM/协议/缓存/SQL生成回归：363通过，见 [日志](results/stage-29-targeted.txt)。新增28项回归覆盖字典/对象usage、0与非法值、并发交错/上下文重置、重试/fallback、失败usage、预算拒绝、取消/提前关闭、排队取消、流末usage/缺结束标记/截断、有界与摘要副本、三协议真实SQLite、多库多候选和全部生成失败。

前端182函数契约和三页46文件完整构建（重复构建/坏JS保留旧产物）通过，见 [前端](results/stage-29-frontend.txt)/[构建](results/stage-29-build.txt)。页面未改，未重复视觉测试。Ruff、原核心strict36、新模块strict1、评测脚本strict1通过。全仓mypy维持178项，无新增（按 file/code/message 多重集比较），见 [类型摘要](results/stage-29-types-summary.json)。全量 **3606通过、1跳过、1警告，118.21秒**，见 [日志](results/stage-29-pytest.txt)。最终源码的受影响模块363项再次通过（21.42秒）。远程CI结果待推送后补记。

## 验证环境与首次失败

默认 Python3.11 无 pytest，旧 .venv 解释器路径失效，Anaconda DLL 启动失败；改为独立 Python3.12.13 .check-venv 安装依赖。沙箱内 Windows asyncio 初始化在 socketpair accept 阻塞，异步回归改在沙箱外运行；无生产模型请求。npm 全局缓存复制失败，使用独立 .npm-cache 后安装成功。

首次全量 3466通过/132失败/1跳过。向量缓存失败在未修改基线中复现，错误包含 Windows 缺少时区数据；补 tzdata，并将本机 LanceDB/PyArrow/DuckDB 对齐阶段28版本0.38.0/25.0.1/1.5.5后重跑。另修复一项新采集适配器读取旧测试 Router 缺失 config 的兼容性问题。原始失败日志保留为 stage-29-pytest-initial.txt，不计作验收通过。最终CI使用Linux干净安装验证项目声明依赖。

生产 provider 的 usage 形状/覆盖率、取消后的远端消耗、真实账单、缓存/推理 token 口径和并发性能未测；没有质量或费用提升承诺。178类型存量仍未清零，构建依赖告警不属于本阶段修复范围。
