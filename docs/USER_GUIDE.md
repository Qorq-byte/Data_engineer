# NL2SQL 数据工程智能体 — 用户使用手册

> 版本: v0.1 · 更新: 2026-07-19

---

## 目录

1. [概述](#1-概述)
2. [访问方式](#2-访问方式)
3. [落地页](#3-落地页)
4. [注册与登入](#4-注册与登入)
5. [工作台概览](#5-工作台概览)
6. [智能查询 — ChatPanel](#6-智能查询--chatpanel)
7. [数据 Schema — SchemaPanel](#7-数据-schema--schemapanel)
8. [领域知识 — DomainPanel](#8-领域知识--domainpanel)
9. [SQL 工作台 — WorkbenchPanel](#9-sql-工作台--workbenchpanel)
10. [MCP 服务 — McpPanel](#10-mcp-服务--mcppanel)
11. [工作流 — WorkflowPanel](#11-工作流--workflowpanel)
12. [系统设置 — SettingsPanel](#12-系统设置--settingspanel)
13. [通过 API 调用](#13-通过-api-调用)

---

## 1. 概述

NL2SQL 是一个**将自然语言自动转换为 SQL 并执行**的数据工程工具。

**能做什么：**
- 输入中文或英文的问题，自动生成 SQL 并展示查询结果
- 浏览数据库的表结构（Schema）
- 管理业务术语到 SQL 的映射规则
- 手动编写和执行 SQL
- 管理和监控 MCP 服务与工作流

**典型场景：**

> 输入：_"上个月销售额最高的 10 个产品是哪些？"_
>
> 系统自动：识别语言 → 定位 orders/products 表 → 生成 `SELECT ... FROM products JOIN orders ... ORDER BY SUM(amount) DESC LIMIT 10` → 校验语法 → 返回结果表格

---

## 2. 访问方式

确保服务已启动（服务端执行 `python main.py`），浏览器打开：

| 页面 | 地址 | 用途 |
|------|------|------|
| 落地页 | http://localhost:8000/ | 项目介绍，导航入口 |
| 登入/注册 | http://localhost:8000/login | 创建账号或登入 |
| 工作台 | http://localhost:8000/workspace | 主操作界面 |

**页面流程：** 落地页 → 点击"开始体验" → 登入/注册 → 自动跳转工作台

---

## 3. 落地页

打开 `http://localhost:8000/` 看到黑色全屏页面：

**导航栏**（顶部）：关于 \| 能力 \| 使用指南 \| 开始体验

**向下滚动浏览：**

| 区域 | 内容 |
|------|------|
| **Hero 标题** | "Hi, i'm NL2SQL" + 项目简介 |
| **关于项目** | 详细介绍，文字随滚动逐字展现 |
| **核心能力** | 5 张白底卡片：意图理解 / 语义检索 / SQL 生成 / 验证执行 / 持续学习 |
| **使用方法** | 3 张粘性滚动卡片：Web 工作台 / REST API / MCP 集成 |

**进入登入页：** 点击任意 **"开始体验"** 按钮，跳转到 `/login`。

---

## 4. 注册与登入

页面使用 3D 翻转卡片：

| 模式 | 操作 |
|------|------|
| **正面 — Log in** | 输入 Email + Password → 点击 "Let's go!" |
| **反面 — Sign up** | 点击中间开关翻面 → 输入 Username + Email + Password + 确认密码 → 注册 |

**注册要求：**
- 用户名 ≥ 2 个字符
- 邮箱格式有效
- 密码 ≥ 6 个字符
- 两次密码一致

**成功后：** 自动跳转到工作台 `/workspace`。

**已登入状态：** 下次打开 `/login` 会自动检测已有 Token，直接跳转工作台。

---

## 5. 工作台概览

工作台是主操作界面，左侧为**可悬停展开的侧边栏**，包含 7 个面板入口：

| 面板 | 图标说明 | 功能 |
|------|---------|------|
| **智能查询** | 💬 | 自然语言 → SQL，核心功能 |
| **数据 Schema** | 🗄️ | 浏览数据库表结构 |
| **领域知识** | 📚 | 查看业务术语和规则 |
| **SQL 工作台** | ⚡ | 手动编写和执行 SQL |
| **MCP 服务** | 🔌 | MCP 服务器管理 |
| **工作流** | 🔀 | 工作流模板和执行监控 |
| **系统设置** | ⚙️ | 系统状态、健康检查 |

---

## 6. 智能查询 — ChatPanel

### 操作步骤

1. 在底部输入框输入自然语言查询（中文或英文）
2. 按 `Enter` 或点击发送按钮
3. 系统逐步执行 Pipeline，实时显示进度：
   - `schema_linking` → 定位相关表和列
   - `generate_sql` → 大模型生成 SQL
   - `validate_sql` → 语法和 Schema 验证
   - `execute_sql` → 执行 SQL 预览结果
4. 结果区域展示：SQL 代码、执行结果表格、验证报告

### 查询示例

**中文：**

| 输入 | 系统处理 |
|------|---------|
| "上个月的订单总金额是多少" | 时间解析 "上个月" → 聚合 SUM → orders 表 |
| "每个用户的平均客单价" | 术语 "客单价" → AVG(orders.amount) |
| "2024 年销售额最高的 10 个产品" | 时间过滤 → JOIN products → ORDER BY SUM DESC LIMIT 10 |
| "本周新增了多少用户" | 时间解析 "本周" → 聚合 COUNT |
| "各地区的订单数和总金额" | GROUP BY 地区 → COUNT + SUM |

**英文：**

| 输入 | 系统处理 |
|------|---------|
| "Show me top 5 products by revenue" | intent=AGGREGATE → revenue metric |
| "How many new users signed up last week?" | time parse "last week" → COUNT |
| "List all orders with status pending" | filter WHERE status = 'pending' |

### 结果解读

每次查询返回的消息卡片包含：

| 区域 | 说明 |
|------|------|
| **原始输入** | 回显你的 NL 文本 |
| **检测信息** | 语言 (zh/en)、意图 (SELECT/AGGREGATE/JOIN 等)、置信度 |
| **关联表** | 系统自动定位到的相关数据库表 |
| **SQL 代码** | 最终生成的 SQL 语句 |
| **验证报告** | 语法检查、Schema 检查、类型检查结果 |
| **执行结果** | 数据表格（仅当开启 execute 时显示） |

### API 在线状态

ChatPanel 标题栏右侧有在线状态指示灯：
- 🟢 **"API 在线"** — 后端正常运行
- 🔴 **"离线模式"** — 后端不可达，使用本地 Mock 数据演示

---

## 7. 数据 Schema — SchemaPanel

### 操作步骤

1. 点击左侧 **"数据 Schema"** 进入面板
2. 页面自动加载已连接数据库列表
3. 点击某个数据库，展开其完整表结构：
   - 表名、注释、预估行数
   - 所有列的：名称、类型、是否主键、是否外键、注释
   - 外键关联信息
4. 使用搜索框输入关键词，筛选匹配的表/列

### 数据来源

Schema 数据从后端 `GET /api/v1/databases` 和 `GET /api/v1/schema/{db_id}` 实时获取。

---

## 8. 领域知识 — DomainPanel

### 操作步骤

1. 点击左侧 **"领域知识"** 进入面板
2. 下拉框选择领域（如 "ecommerce"、"finance"）
3. 切换查看：
   - **术语表** (Glossary) — 业务词汇到 SQL 的映射
   - **业务规则** (Rules) — 领域特定的 SQL 约束

### 术语表示例（电商领域）

| 术语 | SQL 映射 |
|------|---------|
| 订单金额 | `SUM(orders.amount) - SUM(refunds.amount)` |
| 客单价 | `AVG(orders.amount)` |
| 复购率 | `COUNT(DISTINCT CASE WHEN order_count > 1 ...)` |
| 活跃用户 | `users.last_active_at >= NOW() - INTERVAL '30 days'` |

### 业务规则示例

| 规则 | 说明 |
|------|------|
| `ecom_price_precision` | 金额字段保留 2 位小数 |
| `ecom_timezone` | 时间字段使用 Asia/Shanghai 时区 |
| `fin_compliance` | 风控查询必须包含时间范围限制 |

> 数据来源：`GET /api/v1/domains`、`GET /api/v1/domains/{id}/glossary`、`GET /api/v1/domains/{id}/rules`

---

## 9. SQL 工作台 — WorkbenchPanel

### 操作步骤

1. 点击左侧 **"SQL 工作台"** 进入面板
2. 在 SQL 编辑区输入 SQL 语句
3. 下拉框选择目标**数据库方言**（支持 11 种：PostgreSQL / MySQL / SQLite / DuckDB / Snowflake 等）
4. 点击 **"执行"** 按钮
5. 结果以表格形式展示在下方的 **"查询结果"** 区域

### 示例

```sql
-- 输入以下 SQL：
SELECT
    DATE_TRUNC('month', created_at) AS month,
    COUNT(*) AS order_count,
    SUM(amount) AS total_revenue
FROM orders
WHERE created_at >= '2024-01-01'
GROUP BY DATE_TRUNC('month', created_at)
ORDER BY month;
```

> SQL 通过 `POST /api/v1/query`（execute=true）发送执行。

---

## 10. MCP 服务 — McpPanel

### 查看 MCP Server

点击左侧 **"MCP 服务"** 查看已注册的 MCP 服务器列表，每个 Server 显示：
- 名称和描述
- 提供的工具列表
- 暴露的资源列表

**内置的 4 个 MCP Server：**

| Server | 功能 |
|--------|------|
| `db_server` | 数据库查询、Explain、表列表、表结构 |
| `knowledge_server` | 术语搜索、规则匹配 |
| `vector_server` | 混合检索、语义搜索 |
| `learning_server` | 反馈记录、评分提交、规则提取 |

> 数据来源：`GET /api/v1/mcp/servers`（该路由目前为预留，后端未实现时降级显示 Mock 数据）

---

## 11. 工作流 — WorkflowPanel

### 查看工作流模板

点击左侧 **"工作流"** 进入面板，查看系统预置的 5 个工作流模板：

| 工作流 | 节点顺序 | 用途 |
|--------|---------|------|
| **快速查询** `ez_query` | schema_linking → generate_sql → execute_sql | 简单查询，跳过验证 |
| **标准生成** `gensql_agentic` | schema_linking → generate_sql → validate_sql → execute_sql | 标准 NL→SQL（默认） |
| **反思生成** `reflection` | generate → execute → reflect → [revise → execute...] | 带自愈修正的复杂查询 |
| **对话式** `chat_agentic` | parse_nl → hybrid_search → generate_sql → validate_sql → respond | 多轮对话 |
| **指标查询** `metric_query` | metric_resolve → generate_sql → validate_sql → execute_sql | MetricFlow 驱动 |

每个工作流展示完整的 DAG 节点链和每个节点的说明。

> 数据来源：`GET /api/v1/workflows` 和 `GET /api/v1/workflows/stats`（预留路由，后端未实现时降级为 Mock 数据）

---

## 12. 系统设置 — SettingsPanel

### 查看系统状态

点击左侧 **"系统设置"** 进入面板：

| 信息项 | 说明 |
|--------|------|
| **API 状态** | 在线/离线，通过 `GET /health` 检测 |
| **版本号** | 当前系统版本 |
| **数据库连接** | 已连接数据库数量和状态 |
| **工作流模板数** | 可用工作流模板总量 |

---

## 13. 通过 API 调用

除 Web 界面外，你还可以直接通过 HTTP API 使用各项功能。完整 API 文档见 `http://localhost:8000/docs` (Swagger)。

### 核心查询

```bash
# NL → SQL 完整管线
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "nl_text": "上个月销售额最高的10个产品",
    "domain": "ecommerce",
    "dialect": "postgresql",
    "execute": false
  }'
```

**返回示例：**

```json
{
  "query_id": "q_abc123def456",
  "nl_text": "上个月销售额最高的10个产品",
  "language": "zh",
  "intent": "AGGREGATE",
  "confidence": 0.92,
  "primary_sql": "SELECT p.name, SUM(o.amount) AS revenue ...",
  "validation": {
    "passed": true,
    "score": 0.95,
    "syntax_ok": true,
    "schema_valid": true,
    "type_valid": true
  },
  "linked_tables": ["products", "orders"],
  "warnings": []
}
```

### 流式生成（SSE）

```bash
curl -X POST http://localhost:8000/api/v1/query/stream \
  -H "Content-Type: application/json" \
  -d '{"nl_text": "统计每天的订单数"}'
```

事件流：`parse` → `schema` → `token`（逐词输出 SQL） → `validation` → `done`

### 仅生成 SQL（轻量）

```bash
curl -X POST http://localhost:8000/api/v1/query/generate \
  -H "Content-Type: application/json" \
  -d '{"nl_text": "SELECT all users", "dialect": "mysql"}'
```

### Schema 查询

```bash
# 列出已连接数据库
curl http://localhost:8000/api/v1/databases

# 获取 Schema 快照
curl http://localhost:8000/api/v1/schema/{db_id}

# 搜索表和列
curl "http://localhost:8000/api/v1/schema/{db_id}/search?q=order"

# 刷新 Schema 缓存
curl -X POST http://localhost:8000/api/v1/schema/{db_id}/refresh
```

### 添加数据库连接

```bash
curl -X POST http://localhost:8000/api/v1/databases/connect \
  -H "Content-Type: application/json" \
  -d '{
    "db_type": "sqlite",
    "config": {"path": "/path/to/db.sqlite"},
    "alias": "my_db"
  }'
```

### 领域管理

```bash
# 列出所有领域
curl http://localhost:8000/api/v1/domains

# 查看某领域的术语表
curl http://localhost:8000/api/v1/domains/ecommerce/glossary

# 查看某领域的业务规则
curl http://localhost:8000/api/v1/domains/ecommerce/rules

# 自动检测 NL 所属领域
curl -X POST http://localhost:8000/api/v1/domains/detect \
  -H "Content-Type: application/json" \
  -d '{"query_text": "最近一周的订单数据"}'
```

### 认证

```bash
# 注册
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "email": "demo@example.com", "password": "demo123456"}'

# 登入
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo@example.com", "password": "demo123456"}'

# 获取当前用户信息
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <token>"
```
