# 指标 MCP 跨进程传输验收

日期：2026-11-18；起点 main `affe17d`，保留本地日志与工作笔记。

## 目标、优先级与边界

指标工具已实现并有进程内 ASGI 测试；共享目录已完成 REST/MySQL 演练。
下一项优先补齐独立客户端和传输边界，复用现有工厂与 Harness，无需云凭据或业务数据。
仅增加可重复的合成验收宿主和必要缺陷修复，不把 MCP 默认挂到 API、扩大网络监听或改变五个旧工具的认证策略。
其他方案包括直接扩展业务指标语义、继续添加云连接器；它们需要新的业务范围或外部环境，因此后置。

## 阶段计划

1. stdio 独立进程：测试专用宿主仅加载显式临时 SQLite 和合成目录，客户端用真实 SDK。
   前置：锁定 Python 环境。交付：子进程 fixture/生命周期及验收用例。
   验收：默认工具拒绝、显式信任后发现/只校验/执行、固定节点/零 LLM、旧版本/越界拒绝、子进程正常退出。
2. HTTP/MySQL：显式在现有测试 worker 内组合正常应用与 MCP lifespan，临时回环监听，真实外部 SDK 客户端。
   前置：本地 Docker 与已有 MySQL 镜像。交付：双 worker 网络用例与必要修复。
   验收：合法/缺失/过期/非管理员 JWT、目录及 SQL 结果与 REST 一致、会话可跨进程读取、重启恢复、内容及控制面漂移拒绝。
   不通过宿主信任参数绕过 HTTP 身份；MCP 传输成功不等于工具成功，检查结构化错误码。
3. 收尾：相关回归、全量本地测试、ruff/mypy/依赖与文档检查；同步支持矩阵、四语言 README 和聚合证据。
   数据限仓库 SQLite fixture 与一次性 MySQL 的五条合成订单，不调用真实模型；记录未选和跳过项。

## 验证命令

使用 `.stage62/.venv/Scripts/python.exe`：

```powershell
$py='.stage62/.venv/Scripts/python.exe'
& $py -m pytest tests/test_mcp -q
& $py -m scripts.run_real_db_tests --filter 'metric_mcp' --output data/metric-mcp-transports/real-db.json
& $py -m pytest -m 'not real_database' -q
& $py -m ruff check .
& $py -m mypy app
& $py -m scripts.check_dependencies
& $py scripts/check_readmes.py
```

## 状态与外部条件

规划完成，实施完成，收尾完成。Docker 可用，stdio/SQLite 与 HTTP/MySQL 均已运行；不以跳过当实库通过。

最终结果：MCP 本地回归 **210 passed**；stdio 独立进程 **3 passed**（79.21s，45 秒冷启动有界超时）；
真实 MySQL 8.4.13 HTTP MCP **3 passed / 149 deselected**（192.32s）。完整非实库回归
**4777 passed / 1 skipped / 152 deselected**（457.83s）；唯一跳过为 Windows uvloop。
全仓 Ruff、Mypy（215 app 文件）、33 项依赖约束、README 链接检查均通过。
证据已归档至 [metric-mcp-transports-2026-11-18.json](results/metric-mcp-transports-2026-11-18.json)。
Snowflake 专用账号/fixture/warehouse 范围、生产目录签收、业务标注与容量 SLO 仍待外部条件。
跨表/时间语义、自主多 Agent、租户迁移需要独立产品设计；本轮不实现 TLS、代理和 OAuth。
