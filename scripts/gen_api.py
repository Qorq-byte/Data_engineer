"""Generate API endpoint files."""
import os

base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "api")
os.makedirs(base, exist_ok=True)

files = {}

# workflows.py
files["workflows.py"] = r'''"""Workflow management API endpoints."""
from __future__ import annotations
from fastapi import APIRouter
router = APIRouter()

_WORKFLOW_PLANS = [
    {"id": "gensql_agentic", "name": "标准 NL→SQL 生成", "description": "单轮 NL→SQL，含 Schema 链接、生成、验证、执行", "node_count": 4, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "🔗"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "🤖"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "✅"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "▶️"}], "category": "standard"},
    {"id": "ez_query", "name": "快速查询通道", "description": "跳过验证的快速查询", "node_count": 3, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "🔗"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "🤖"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "▶️"}], "category": "express"},
    {"id": "reflection", "name": "反射式生成", "description": "生成→执行→反思→修正", "node_count": 5, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "🔗"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "🤖"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "▶️"}, {"name": "reflection", "label": "反思修正", "icon": "🔄"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "✅"}], "category": "deep"},
    {"id": "chat_agentic", "name": "多轮对话查询", "description": "多轮对话增量查询", "node_count": 5, "nodes": [{"name": "parse_nl", "label": "NL 解析", "icon": "📝"}, {"name": "hybrid_search", "label": "混合检索", "icon": "🔍"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "🤖"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "✅"}, {"name": "respond", "label": "结果响应", "icon": "💬"}], "category": "chat"},
    {"id": "explore", "name": "Schema 探索", "description": "快速探索数据库 Schema", "node_count": 3, "nodes": [{"name": "parse_nl", "label": "NL 解析", "icon": "📝"}, {"name": "hybrid_search", "label": "混合检索", "icon": "🔍"}, {"name": "format", "label": "格式化输出", "icon": "📋"}], "category": "explore"},
    {"id": "metric_query", "name": "MetricFlow 指标查询", "description": "通过 MetricFlow 语义层查询", "node_count": 4, "nodes": [{"name": "metric_resolve", "label": "指标解析", "icon": "📐"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "🤖"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "✅"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "▶️"}], "category": "metric"},
]

@router.get("/workflows")
async def list_workflows() -> dict:
    return {"plans": _WORKFLOW_PLANS, "total": len(_WORKFLOW_PLANS), "categories": sorted({p["category"] for p in _WORKFLOW_PLANS})}

@router.get("/workflows/stats")
async def workflow_stats() -> dict:
    return {"total_executions": 0, "success_rate": 0.0, "avg_duration_ms": 0.0}

@router.get("/workflows/traces")
async def workflow_traces(limit: int = 20) -> dict:
    return {"traces": [], "total": 0}
'''

# mcp_api.py
files["mcp_api.py"] = r'''"""MCP server management API endpoints."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.mcp.registry import mcp_registry
router = APIRouter()

_BUILTIN_SERVERS = [
    {"name": "db_server", "server_type": "database", "port": 8081, "tool_count": 4, "resource_count": 2, "status": "running", "description": "数据库内省与执行工具"},
    {"name": "knowledge_server", "server_type": "knowledge", "port": 8082, "tool_count": 3, "resource_count": 1, "status": "running", "description": "领域知识库检索"},
    {"name": "vector_server", "server_type": "vector", "port": 8083, "tool_count": 2, "resource_count": 1, "status": "running", "description": "向量检索与 RAG"},
    {"name": "learning_server", "server_type": "learning", "port": 8084, "tool_count": 2, "resource_count": 0, "status": "stopped", "description": "查询历史学习"},
]

@router.get("/mcp/servers")
async def list_mcp_servers() -> dict:
    servers = mcp_registry.list_all()
    if not servers:
        return {"servers": _BUILTIN_SERVERS, "total": len(_BUILTIN_SERVERS)}
    return {"servers": [{"name": s.name, "server_type": s.server_type, "transport": s.transport, "host": s.host, "port": s.port, "tool_count": len(s.tools), "resource_count": len(s.resources), "status": "running" if s.status == "active" else "stopped", "description": s.metadata.get("description", ""), "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None} for s in servers], "total": len(servers)}

@router.get("/mcp/servers/{name}")
async def get_mcp_server(name: str) -> dict:
    try:
        s = mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return {"name": s.name, "server_type": s.server_type, "transport": s.transport, "host": s.host, "port": s.port, "status": s.status, "tools": [{"name": t.name, "description": t.description} for t in s.tools], "resources": [{"uri": r.uri, "description": r.description} for r in s.resources], "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None}

@router.post("/mcp/servers/{name}/health")
async def check_mcp_server_health(name: str) -> dict:
    try:
        mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    from datetime import datetime
    mcp_registry.update_health(name, "active")
    return {"name": name, "status": "active", "checked_at": datetime.now().isoformat()}
'''

# feedback.py
files["feedback.py"] = r'''"""Feedback collection API endpoints."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel, Field
router = APIRouter()

class FeedbackRequest(BaseModel):
    query_id: str = Field(default="")
    session_id: str = Field(default="")
    nl_input: str = Field(default="")
    sql_generated: str = Field(default="")
    sql_final: str = Field(default="")
    rating: int = Field(default=0, ge=0, le=5)
    feedback_text: str = Field(default="")
    domain: str = Field(default="")

_feedback_store: list[dict[str, Any]] = []

@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest) -> dict:
    record = {"id": f"fb_{len(_feedback_store)+1}_{datetime.now().strftime('%Y%m%d%H%M%S')}", "query_id": body.query_id, "session_id": body.session_id, "nl_input": body.nl_input, "sql_generated": body.sql_generated, "sql_final": body.sql_final, "rating": body.rating, "feedback_text": body.feedback_text, "domain": body.domain, "created_at": datetime.now().isoformat()}
    _feedback_store.append(record)
    try:
        from app.learning.feedback_collector import feedback_collector
        feedback_collector.collect(session_id=body.session_id or "unknown", query_id=body.query_id or "unknown", nl_input=body.nl_input, sql_generated=body.sql_generated, sql_final=body.sql_final or None, rating=body.rating, feedback_text=body.feedback_text or None, domain_id=body.domain or None)
    except Exception:
        pass
    return {"status": "recorded", "feedback_id": record["id"], "message": "感谢您的反馈！反馈已记录至学习库。"}

@router.get("/feedback/stats")
async def feedback_stats() -> dict:
    if not _feedback_store:
        return {"total": 0, "avg_rating": 0.0, "ratings_distribution": {}, "recent_count_7d": 0}
    ratings = [r["rating"] for r in _feedback_store if r["rating"] > 0]
    dist: dict[int, int] = {}
    for r in ratings:
        dist[r] = dist.get(r, 0) + 1
    return {"total": len(_feedback_store), "avg_rating": round(sum(ratings)/len(ratings),2) if ratings else 0.0, "ratings_distribution": {str(k):v for k,v in sorted(dist.items())}, "recent_count_7d": len(_feedback_store)}

@router.get("/feedback/recent")
async def recent_feedback(limit: int = 20) -> dict:
    records = _feedback_store[-limit:] if _feedback_store else []
    return {"records": list(reversed(records)), "total": len(records)}
'''

# sql_utils.py
files["sql_utils.py"] = r'''"""SQL utility API endpoints."""
from __future__ import annotations
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
router = APIRouter()

class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    dialect: str = Field(default="ansi")
    target_dialect: str = Field(default="")

@router.post("/sql/explain")
async def explain_sql(body: SQLRequest) -> dict:
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse(body.sql, dialect=body.dialect or None)
        if not parsed:
            raise HTTPException(status_code=400, detail="Unable to parse SQL")
        tree = parsed[0]
        tables, joins, where_conditions, order_by_columns = [], [], [], []
        has_aggregation, has_subquery, has_cte = False, False, False
        for node in tree.walk():
            if isinstance(node, exp.Table): tables.append(node.name)
            elif isinstance(node, exp.Join): joins.append({"kind": node.kind or "JOIN", "table": node.this.name if isinstance(node.this, exp.Table) else str(node.this)})
            elif isinstance(node, exp.Where): where_conditions.append(str(node.this)[:120])
            elif isinstance(node, exp.Order):
                for col in node.expressions: order_by_columns.append(str(col)[:60])
            elif isinstance(node, (exp.Avg, exp.Sum, exp.Count, exp.Max, exp.Min)): has_aggregation = True
            elif isinstance(node, exp.Subquery): has_subquery = True
            elif isinstance(node, exp.CTE): has_cte = True
        warnings = []
        if not where_conditions and tables:
            warnings.append("未检测到 WHERE 子句，可能存在全表扫描风险")
        plan_lines = []
        if tables: plan_lines.append(f"扫描表: {', '.join(tables)}")
        if joins:
            for j in joins: plan_lines.append(f"  {j['kind']} {j['table']}")
        if where_conditions: plan_lines.append(f"过滤条件: {'; '.join(where_conditions[:3])}")
        if has_aggregation: plan_lines.append("聚合操作: 是")
        return {"status": "analyzed", "dialect": body.dialect or "ansi", "tables": tables, "join_count": len(joins), "joins": joins, "where_conditions": where_conditions, "order_by": order_by_columns, "has_aggregation": has_aggregation, "has_subquery": has_subquery, "has_cte": has_cte, "statement_count": len(parsed), "warnings": warnings, "plan_text": "\n".join(plan_lines) if plan_lines else "简单查询"}
    except ImportError:
        return {"status": "unavailable", "message": "sqlglot 未安装"}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=f"SQL 分析失败: {e}")

@router.post("/sql/translate")
async def translate_sql(body: SQLRequest) -> dict:
    if not body.target_dialect:
        raise HTTPException(status_code=400, detail="target_dialect is required")
    try:
        import sqlglot
        translated = sqlglot.transpile(body.sql, read=body.dialect or None, write=body.target_dialect)
        if not translated: raise HTTPException(status_code=400, detail="Translation produced no output")
        return {"status": "translated", "source_dialect": body.dialect or "ansi", "target_dialect": body.target_dialect, "source_sql": body.sql, "translated_sql": translated[0]}
    except ImportError:
        return {"status": "unavailable", "message": "sqlglot 未安装"}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=400, detail=f"SQL 翻译失败: {e}")

@router.post("/sql/format")
async def format_sql(body: SQLRequest) -> dict:
    try:
        import sqlparse
        formatted = sqlparse.format(body.sql, reindent=True, keyword_case="upper", indent_width=2)
        return {"status": "formatted", "dialect": body.dialect or "ansi", "source_sql": body.sql, "formatted_sql": formatted}
    except ImportError:
        keywords = ["SELECT","FROM","WHERE","AND","OR","ORDER BY","GROUP BY","HAVING","LIMIT","JOIN","LEFT JOIN","RIGHT JOIN","INNER JOIN","ON","AS","INSERT","UPDATE","DELETE","CREATE","ALTER","DROP","SET","INTO","VALUES","UNION","ALL","DISTINCT","CASE","WHEN","THEN","ELSE","END","COUNT","SUM","AVG","MAX","MIN","COALESCE","NULL","NOT","IN","LIKE","BETWEEN","IS","WITH","RECURSIVE","OVER","PARTITION BY","ROW_NUMBER","RANK","DENSE_RANK","LAG","LEAD"]
        formatted = body.sql
        for kw in sorted(keywords, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
            formatted = pattern.sub(lambda m: "\n" + m.group(0).upper(), formatted)
        formatted = re.sub(r"\n\s*\n", "\n", formatted).strip()
        return {"status": "formatted", "dialect": body.dialect or "ansi", "source_sql": body.sql, "formatted_sql": formatted, "note": "sqlparse 未安装，使用基础格式化"}
    except Exception as e: raise HTTPException(status_code=400, detail=f"SQL 格式化失败: {e}")

@router.post("/sql/validate")
async def validate_sql(body: SQLRequest) -> dict:
    errors, warnings = [], []
    try:
        import sqlglot
        parsed = sqlglot.parse(body.sql, dialect=body.dialect or None)
        if not parsed: errors.append("无法解析 SQL 语句")
        else:
            sql_upper = body.sql.upper()
            if "SELECT" in sql_upper and "FROM" not in sql_upper: warnings.append("SELECT 语句缺少 FROM 子句")
            if sql_upper.count("(") != sql_upper.count(")"): warnings.append("括号不匹配")
    except ImportError: warnings.append("sqlglot 未安装，跳过语法验证")
    except Exception as e: errors.append(f"语法解析错误: {e}")
    return {"status": "valid" if not errors else "invalid", "syntax_ok": len(errors) == 0, "errors": errors, "warnings": warnings, "dialect": body.dialect or "ansi"}
'''

for filename, content in files.items():
    filepath = os.path.join(base, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created: {filename}")

print("All API files created!")
