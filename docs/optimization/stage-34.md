# 阶段34：流式候选完成证据

2026-10-24，基线阶段33最终339ede8；独立codex/stream-completion-evidence/.stage34。先行设计见[计划](stage-34-plan.md)，接口见[流事件契约](stream-completion.md)。承接尚未合并main的阶段29—33，保留原工作区改动，不强推、不自动合并。

## 实现与结果

新增纯dataclass StreamEvent及可选complete_stream_events/generate_stream_events，文本接口保持str。原生SDK仍检查length/拒绝/工具/非文本/stop后内容，完整消费含usage尾部并完成关闭流程后才交付model_stream/stop。终止事件验证器暂存一个end，拒绝重复/缺失/后续内容/错误对象及字段，错误不回显payload；不会从EOF或请求usage推断stop。

预算适配器、SQLGenerator和GenerateSQLNode逐层转发事件，节点只在成功完整消费后创建候选，同响应alternatives共享自身证据。旧文本路由/Mock仍stream_text/null；继承事件方法但覆盖文本方法时保持旧路径，包括实例方法覆盖和自定义生成器。主动实现事件方法的适配器可声明model_stream/stop，但不是provider认证。

预算依据实际绑定方法判断原生/注入路径，原生物理边界计数一次，注入事件/文本入口计一次；覆盖事件能力但继承文本方法不会漏计。事件不携token，原SDK usage观察仍只执行一次，合法0保留，缺usage仍null。取消/超时/提前关闭/非法尾部不产生成功候选，物理流关闭并发槽释放，不重试或拼接新响应。原连接建立重试、预算上限及Harness顺序/权限/只读约束不变。

SQLCandidate/DTO新增允许model_stream，JSON/SSE/MCP沿既有候选投影保留；显式导出接受所选候选自身的stop，prepared仍拒绝，stream_text/unknown仍不升级，v1结构与历史合成样例哈希不变。公开SSE仍为工作流事件流，不切换生产调用方式；审计schema v1不改，不保存SQL、不估价。

## 验证

新增29项回归。首轮[22项失败回归](results/stage-34-initial.txt)为11失败/11通过，确认候选证据丢失及事件能力缺失；实现后22通过，再补7项适配器、超时/提前关闭和并发隔离回归。[关联452项](results/stage-34-target.txt)通过；最终增加取消时attempt状态与预算断言后一并全量验收。

最终本地[全量pytest](results/stage-34-pytest.txt) **3796通过、1平台跳过、2警告，112.76秒**。真实LiteLLM ModelResponseStream/Usage经合成SDK传输→BudgetedRouter→生成器→节点→CandidateResult→显式导出，确认stop及单调用预算/attempt、尾usage合法0/缺测。stop后阻塞尾部期间不交付end；取消/超时/非法尾部/主动关闭后复用唯一并发槽，成本成功标记为false。交错请求的正常stop不能补全另一个无标记请求。原生和生成器文本API输出仍只有str。

[Ruff](results/stage-34-ruff.txt)、[核心strict36](results/stage-34-core-types.txt)、[流模块及原LLM/审计/导出/评测strict8](results/stage-34-stream-types.txt)通过；CI新增流模块独立strict门禁。实现中AsyncIterator标注不含aclose的错误，已改为实际AsyncGenerator返回类型，不用ignore屏蔽。全仓mypy相对阶段33仍178项，按file/code/message多重集新增0，[摘要](results/stage-34-types-summary.json)。

[182前端函数契约](results/stage-34-frontend.txt)、[三页完整构建](results/stage-34-build.txt)、重复性/故意坏JS保留旧产物均通过。复用Python3.12验收环境及锁文件离线npm缓存；Windows异步测试需沙箱外socketpair。无页面修改，不重复视觉检查。未调用外部模型或业务数据库。

## 限制与交付

来源只表示代码路径及所观察字段，不认证真实provider、操作者文件、SQL语义、账单或生产并发性能。外部SDK关闭沿原有best-effort流程，调用方须完整消费或aclose，不跨任务共享迭代器。原始文本流仍未知；公开SSE无新的token流线路。SQL摘要审计绑定、真实模型评测与费用估算后续独立规划。

代码[b7c4d54](https://github.com/Qorq-byte/Data_engineer/commit/b7c4d5498a85714f33cb9fce43c88f54c1827ad8)已普通推送codex/stream-completion-evidence。[Actions40097746926](https://github.com/Qorq-byte/Data_engineer/actions/runs/40097746926)成功，headSha精确匹配，见[结构化证据](results/stage-34-ci.json)和[Linux测试摘要](results/stage-34-ci-tests.txt)。干净环境 **3797通过、2警告，40.00秒**；本地跳过的平台项在Linux实际执行。182前端契约、完整构建、Ruff和原strict门禁及新增流模块strict全部通过。

本次补记验收文档另行提交推送并核验最终CI，不以代码CI代替最终提交验收。主工作区原改动和main保持原状，不强推、不自动合并。
