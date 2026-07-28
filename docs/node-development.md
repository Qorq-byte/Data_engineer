# Node 开发指南

如何为 NL2SQL 数据工程 Agent 创建自定义节点。

## 概述

Node（节点）是 NL2SQL 管道中的原子计算单元。每个节点遵循由 `WorkflowRunner` 管理的三步生命周期：

```
setup_input(context) → execute(node_input) → update_context(output, context)
```

**Harness 不变量**：节点之间绝不互相调用。工作流计划 YAML 定义了执行顺序 `node_order`，由 `WorkflowRunner` 按序驱动。

## BaseNode 与 AgenticNode 对比

| 特性 | BaseNode | AgenticNode |
|---------|----------|-------------|
| LLM 访问 | 否 | 是（通过 LiteLLM + Harness） |
| 适用场景 | 纯计算、规则引擎、数据库操作 | NL 理解、SQL 生成、验证 |
| `execute()` | 自定义逻辑 | 自动调用 `_call_llm(prompt)` |
| `tools` | 不适用 | 在 `agent.yml` 中定义 |
| `permissions` | 不适用 | 由 `PermissionManager` 强制执行 |

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
        result = node_input.context.get("execute_sql_output", {})
        rows = result.get("rows", [])

        issues = []
        if not rows:
            issues.append("empty_result")
        if len(rows) > 10000:
            issues.append("large_result_set")

        return NodeOutput(
            node_name=self.name,
            ok=True,
            data={"issues": issues, "row_count": len(rows)},
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
# app/nodes/registry.py
from app.nodes.data_quality import DataQualityNode
node_registry.register("data_quality", DataQualityNode())
```

### 4. 创建工作流计划

```yaml
# app/workflow/plans/quality_check.yml
id: quality_check
name: "质量检查管道"
node_order:
  - generate_sql
  - execute_sql
  - data_quality
```

## 测试

使用共享的 `tests/conftest.py` fixtures：

```python
import pytest
from app.nodes.data_quality import DataQualityNode

class TestDataQualityNode:
    @pytest.mark.asyncio
    async def test_detects_empty_result(self):
        node = DataQualityNode()
        node_input = node.setup_input({
            "execute_sql_output": {"rows": [], "columns": ["a"]},
        })
        output = await node.execute(node_input)
        assert output.ok
        assert "empty_result" in output.data["issues"]

    @pytest.mark.asyncio
    async def test_clean_result(self):
        node = DataQualityNode()
        node_input = node.setup_input({
            "execute_sql_output": {"rows": [[1], [2]], "columns": ["a"]},
        })
        output = await node.execute(node_input)
        assert output.data["issues"] == []
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
node_order:
  - name: data_quality
    config:
      max_rows: 5000
      threshold: 0.90
```

## 最佳实践

1. **单一职责**：一个节点 = 一个定义明确的任务
2. **幂等性**：相同输入运行两次同一节点应产生相同输出
3. **上下文键约定**：使用 `{node_name}_output` 作为上下文键的命名规范
4. **错误处理**：返回 `NodeOutput(ok=False, error="...")` —— 绝不抛出异常
5. **无副作用**：不要修改全局状态或文件
6. **可测试性**：将纯逻辑提取到节点类中；避免在 `__init__` 中耦合外部服务
