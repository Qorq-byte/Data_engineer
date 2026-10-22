# 阶段2：固定 Harness 主链路与协议一致

## 实际交付

- 移除 query.py 中重复的 JSON/SSE 手写流水线，统一 request_query 计划与 query_service；MCP 复用同一入口。
- Runner 预检查节点/依赖/未知指标/空计划；节点错误不受关闭评估影响，迭代耗尽不能成功；生命周期和实际阶段回调留痕。注册表实例独立，避免请求装配互相覆盖。
- 请求级工具权限、物理 LLM 调用预算（包括 fallback）；生成全部失败不返回制造的 SQL。执行前强制校验；数据库候选携带 db_id。
- JSON/SSE 共用响应及会话历史；SSE 有界队列并传递取消，不伪造 token 进度。前端保留会话、消费终态、失败/取消不自动另发 JSON 请求、离线不自动生成演示答案。
- 工作流目录读取实际 YAML，区分活动入口/模板/目录自定义/模拟；同步 AGENTS 架构与只读策略说明。

## 验证环境与方法

Windows，Python 3.13.9（D:/Users/anaconda/python.exe），复用原项目 .venv/Lib/site-packages；LLM_MOCK_MODE 由 conftest 开启，RAG_EMBEDDING_PROVIDER=mock，LITELLM_LOCAL_MODEL_COST_MAP=True，DE_TRACE_DIR 指向忽略的临时目录。不调用真实模型，不使用生产数据。

1. `python -m pytest tests/test_api tests/test_harness/test_runner_evaluation.py tests/test_workflow tests/test_mcp/test_fastmcp_server.py -q --disable-warnings --tb=short`：345 passed。
2. 新增契约/不变量第一轮：10 passed。真实 SQLite 返回 orders ID 1–5，JSON/SSE SQL、校验、结果、预算和会话一致；按实际 stage 断言五节点顺序；危险 SQL 即使 validate=false 也不执行，原数据保留。
3. 增补物理 fallback 预算与取消传播测试；最终全量 **2905 passed, 4 warnings，47.15秒**，详见 results/stage-2-pytest.txt。
4. 改动执行模块及新增测试 `ruff check`：All checks passed，见 results/stage-2-ruff.txt。全仓存量 lint/type 债务延续阶段1记录，不宣称全局静态检查通过。
5. Babel standalone 7.29.0 对 workspace.html 的实际 text/babel 模块执行 React preset 编译：1 block passed。原受版本控制的 node_modules 缺少 obug/dist/node.js，故在忽略目录隔离安装验证器；同时修复 JS 块中的 TypeScript 类型声明。无浏览器视觉通过结论，完整可复现前端依赖与浏览器验收在阶段5。

## 范围调整与限制

将前端重复提交修复提前到阶段2，因为它直接破坏一次请求的预算与 trace 语义。将前端离线伪结果移除提前处理，避免新的后端失败被前端掩盖。自动 self-heal 等旧手写旁路不带入固定链路，当前以失败关闭保障安全；后续增强须明确声明节点和计划，不能重新建立 API 内隐式循环。RAG、并发资源、真实模型与远程数据库验证尚未验收；详见 query-contract.md 和实施计划。

## 后续

验收后提交并普通推送 codex/mvp-hardening；下一阶段修复 RAG 两端索引一致性、完整正文检索、范围过滤、Schema prompt 接线，并建立可重跑离线评测。推送记录写入 progress.md。
