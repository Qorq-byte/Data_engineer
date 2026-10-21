# Node 开发指南

如何为 NL2SQL 数据工程 Agent 创建自定义节点。

## 概述

Node（节点）是 NL2SQL 管道中的原子计算单元。每个节点遵循由 `WorkflowRunner` 管理的三步生命周期：

```
setup_input(raw_input) → execute(node_input) → update_context(output, context)
```

**Harness 不变量**：节点之间绝不互相调用。工作流计划 YAML 定义了执行顺序 `node_order`，由 `WorkflowRunner` 按序驱动。

## BaseNode 与 AgenticNode 对比

| 特性 | BaseNode | AgenticNode |
|---------|----------|-------------|
| LLM 访问 | 无自动接线，调用仍须遵守预算 | 提供Harness基础能力，具体调用由子类实现 |
| 适用场景 | 纯计算、规则引擎、数据库操作 | NL 理解、SQL 生成、验证 |
| `execute()` | 子类实现 | 子类实现，不自动调用LLM或选择流程 |
| `tools` | 代码 `required_tools` 与节点配置 | 同左，另有工具管理器 |
| `permissions` | Runner统一检查 | 同左，节点工具调用也须检查 |

**何时使用 BaseNode：** 数据转换、规则匹配、SQL 执行、格式转换、缓存、指标解析。

**何时使用 AgenticNode：** 任何需要 LLM 推理的节点 —— NL 解析、SQL 生成、验证解读、错误分析。

## 分步指南：创建 DataQualityNode

### 1. 创建节点文件

复制 `app/nodes/_template.py` 到 `app/nodes/data_quality.py`：

```python
from app.nodes.base import BaseNode, NodeInput, NodeOutput

class DataQualityNode(BaseNode):
    name = "data_quality"
    description = "检查结果数据是否存在质量问题"

    async def execute(self, node_input: NodeInput) -> NodeOutput:
        result = node_input.context.get("last_execution")
        rows = result.get("rows", []) if isinstance(result, dict) else []

        issues = []
        if not rows:
            issues.append("empty_result")
        if len(rows) > 10000:
            issues.append("large_result_set")

        return NodeOutput(
            result={"issues": issues, "row_count": len(rows)},
            context={"quality_issues": issues},
        )
```

### 2. 在 agent.yml 中注册

```yaml
agent:
  nodes:
    data_quality:
      model: none  # 非 LLM 节点
      tools: []
      permissions: {}
```

### 3. 在 NodeRegistry 中注册

```python
from app.nodes.data_quality import DataQualityNode
from app.nodes.registry import NodeRegistry

registry = NodeRegistry()
registry.register(DataQualityNode())
```

### 4. 创建工作流计划

```yaml
# app/workflow/plans/quality_check.yml
id: quality_check
name: "质量检查管道"
plan:
  node_order:
    - id: quality
      node: data_quality
  evaluation:
    enabled: true
    metrics: [execution_success]
```

用 `PlanLoader().load("quality_check")` 和 `WorkflowRunner(plan, registry)` 驱动此独立示例；`run(..., last_execution=execution_result)` 可传入已取得的数据。`execution_result`使用`app.db.execution_types.ExecutionResult`字典：`columns`、`rows`、`row_count`、`truncated`；ExecuteSQLNode也按此结构设置`last_execution`，不是QueryResult实例。仅编辑YAML或在启动目录注册不会自动把新节点接入HTTP主查询：`query_service.py`有请求独立registry，HTTP/MCP仍固定 `request_query.yml`，变更须显式实现、审查及回归。

## 测试

使用共享的 `tests/conftest.py` fixtures：

```python
import pytest
from app.nodes.data_quality import DataQualityNode

class TestDataQualityNode:
    @pytest.mark.asyncio
    async def test_detects_empty_result(self):
        node = DataQualityNode()
        node_input = await node.setup_input({
            "context": {"last_execution": {"columns": ["a"], "rows": [], "row_count": 0, "truncated": False}},
        })
        output = await node.execute(node_input)
        assert not output.errors
        assert "empty_result" in output.result["issues"]

    @pytest.mark.asyncio
    async def test_clean_result(self):
        node = DataQualityNode()
        node_input = await node.setup_input({
            "context": {"last_execution": {"columns": ["a"], "rows": [[1], [2]], "row_count": 2, "truncated": False}},
        })
        output = await node.execute(node_input)
        assert output.result["issues"] == []
```

## 配置

节点级别的配置通过 `NodeInput.config` 传入：

```python
async def execute(self, node_input: NodeInput) -> NodeOutput:
    max_rows = node_input.config.get("max_rows", 10000)
    threshold = node_input.config.get("threshold", 0.95)
    ...
```

配置值在工作流计划 YAML 中设置：

```yaml
plan:
  node_order:
    - id: quality
      node: data_quality
      config:
        max_rows: 5000
        threshold: 0.90
```

## 最佳实践

1. **单一职责**：一个节点 = 一个定义明确的任务
2. **幂等性**：相同输入运行两次同一节点应产生相同输出
3. **上下文键约定**：按上下游实际契约传递；SQL执行节点使用 `last_execution`，不是自动生成 `{node_name}_output`
4. **错误处理**：可返回 `NodeOutput(errors=["原因"])`；Runner也捕获节点异常并依计划决定重试/失败，取消须继续传播
5. **无副作用**：不要修改全局状态或文件
6. **可测试性**：将纯逻辑提取到节点类中；避免在 `__init__` 中耦合外部服务

## Runner事件回调

```python
from app.harness.events import NodeEvent, NodeEventStatus

async def on_event(status: NodeEventStatus, data: NodeEvent) -> None:
    print(data["node_id"], status)
    output = data.get("output")  # started时省略；失败时可以为None
    if output is not None and output.errors:
        print("节点返回错误")
```

将此async回调传给 `runner.run(..., on_node_event=on_event)`。事件只报告started/completed/failed，不决定下一节点。`mypy app/harness app/models app/nodes/execute_sql.py app/nodes/generate_sql.py app/llm/budget.py`是strict核心门禁，保留默认导入追踪；它不等于全仓类型检查通过。

## 模型适配契约

`GenerateSQLNode`和`SQLGenerator`接受`app.llm.protocols.CompletionRouter`：只读`config`、非流式`complete`返回字典、`complete_stream`返回字符串异步迭代器。无需继承具体LiteLLMRouter。调用选项使用与原生Router一致的messages/model/temperature/n及provider关键字。HTTP固定工作流注入BudgetedRouter；包装器只公开这些接口，物理预算仍由原生Router逐次计数，注入适配器由包装器计数。错误响应和非字符串流片段明确拒绝，异常与取消继续传播。此声明不保证任意第三方适配器自动合规，新增适配器须实际回归。
