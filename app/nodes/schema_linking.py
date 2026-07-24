"""SchemaLinkingNode — link NL entities to database schema objects.

See SPEC §4.12.5 and implementation-plan §4.4.5 for the full specification.

Phase 2 (MVP) — keyword matching + FK expansion:
  - Extracts table/column candidates from SQR entities and target_tables.
  - Matches candidates against schema via keyword/fuzzy matching.
  - Expands matched tables by 1 level of foreign keys (FK expansion).
  - Resolves ambiguous column references (which table?).

Phase 3 (RAG) — 5-step hierarchical retrieval via ``SchemaRetriever``:
  - When a ``SchemaRetriever`` is injected, the node delegates to
    its 5-step pipeline (分析 → RAG → 术语 → FK → 注入).
  - Falls back to Phase 2 keyword matching when no retriever is available.

Output context keys:
    ``linked_tables`` — ``list[str]`` of matched table names
    ``linked_columns`` — ``list[dict]`` of ``{table, column, confidence}``
    ``linked_schema`` — ``SchemaSnapshot`` filtered to relevant tables
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.query import SQR
from app.models.schema import SchemaSnapshot
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput

logger = logging.getLogger(__name__)

# ── Chinese→English term mapping for schema matching ────────────────
# Maps common Chinese NL terms to English table/column name candidates.
# This bridges the gap when the NL parser extracts Chinese tokens that
# don't match English schema names directly.

_ZH_TO_EN_MAP: dict[str, list[str]] = {
    # General terms
    "用户": ["user", "users", "username", "user_id", "user_session", "user_sessions"],
    "名字": ["name", "username", "full_name", "display_name", "first_name", "last_name"],
    "姓名": ["name", "full_name", "username", "first_name", "last_name"],
    "邮箱": ["email", "mail", "email_address"],
    "电话": ["phone", "telephone", "mobile", "phone_number"],
    "地址": ["address", "addr", "location"],
    "日期": ["date", "created_at", "updated_at", "timestamp"],
    "时间": ["time", "created_at", "updated_at", "timestamp"],
    "金额": ["amount", "price", "total", "cost", "fee"],
    "数量": ["count", "quantity", "qty", "amount", "num"],
    "状态": ["status", "state", "status_code"],
    "密码": ["password", "passwd", "pwd", "hash", "password_hash"],
    "令牌": ["token", "access_token", "refresh_token"],
    "角色": ["role", "role_name", "role_id"],
    "权限": ["permission", "perm", "access"],
    "订单": ["order", "orders", "order_id"],
    "产品": ["product", "products", "item", "goods"],
    "商品": ["product", "items", "goods"],
    "价格": ["price", "cost", "amount", "unit_price"],
    "库存": ["stock", "inventory", "quantity"],
    "分类": ["category", "type", "class", "group"],
    "部门": ["department", "dept", "org"],
    "员工": ["employee", "staff", "worker", "personnel"],
    "项目": ["project", "projects", "project_id", "project_file", "project_files"],
    "任务": ["task", "tasks", "todo", "job"],
    "消息": ["message", "messages", "msg", "ai_message", "ai_messages", "notification", "chat"],
    "评论": ["comment", "review", "feedback", "remark"],
    "日志": ["log", "logs", "audit", "history", "operation_log", "operation_logs"],
    "文件": ["file", "files", "project_file", "project_files", "document", "doc"],
    "图片": ["image", "photo", "picture", "img", "avatar"],
    "头像": ["avatar", "photo", "image", "picture"],
    "标签": ["tag", "label", "category"],
    "配置": ["config", "setting", "settings", "preference", "model_config", "model_configs"],
    "会话": ["session", "sessions", "conversation", "user_session", "user_sessions", "workflow_session", "workflow_sessions", "ai_conversation", "ai_conversations"],
    "对话": ["conversation", "conversations", "ai_conversation", "ai_conversations", "chat", "dialog"],
    "统计": ["stats", "statistics", "summary", "report", "reports", "token_usage"],
    "分析": ["analytics", "analysis", "report", "reports"],
    "查询": ["query", "search", "find", "select"],
    "删除": ["deleted", "is_deleted", "removed", "trash"],
    "创建": ["created", "created_at", "create_time"],
    "更新": ["updated", "updated_at", "update_time"],
    "Token": ["token", "tokens", "token_count", "total_tokens", "token_usage"],
    "token": ["token", "tokens", "token_count", "total_tokens", "token_usage"],
    "使用": ["usage", "used", "consumption", "token_usage"],
    "总量": ["total", "sum", "count", "aggregate"],
    "ID": ["id", "identifier", "uuid"],
    "id": ["id", "identifier", "uuid"],
    "关联": ["relation", "link", "ref", "reference"],
    # Additional common Chinese terms
    "编号": ["id", "code", "number", "no", "serial"],
    "描述": ["description", "desc", "detail", "summary", "remark"],
    "备注": ["remark", "note", "comment", "description", "memo"],
    "类型": ["type", "category", "kind", "class"],
    "等级": ["level", "grade", "rank", "tier"],
    "分数": ["score", "rating", "grade", "point"],
    "标题": ["title", "subject", "name", "heading"],
    "内容": ["content", "body", "text", "description"],
    "链接": ["link", "url", "href", "reference"],
    "版本": ["version", "ver", "release"],
    "标识": ["flag", "marker", "indicator", "status"],
    "来源": ["source", "origin", "from", "referrer"],
    "性别": ["gender", "sex"],
    "年龄": ["age", "years"],
    "生日": ["birthday", "birth_date", "dob"],
    "国家": ["country", "nation"],
    "城市": ["city", "town"],
    "邮编": ["zip", "postal", "zipcode", "postcode"],
    "手机": ["mobile", "phone", "cell", "cellphone"],
    "工资": ["salary", "wage", "income", "pay"],
    "折扣": ["discount", "off", "rebate"],
    "品牌": ["brand", "make", "manufacturer"],
    "颜色": ["color", "colour"],
    "尺寸": ["size", "dimension", "spec"],
    "重量": ["weight", "mass"],
    "已读": ["read", "is_read", "seen"],
    "激活": ["active", "is_active", "enabled", "activated"],
    "禁用": ["disabled", "is_disabled", "inactive", "banned"],
    "锁定": ["locked", "is_locked", "blocked"],
    "验证": ["verified", "is_verified", "validated", "confirmed"],
    "登录": ["login", "signin", "last_login", "logged_in"],
    "注册": ["register", "signup", "registered", "created"],
    "退出": ["logout", "signout", "exit"],
    "关注": ["follow", "subscribe", "following"],
    "点赞": ["like", "favorite", "thumb_up", "liked"],
    "分享": ["share", "shared", "forward"],
    "收藏": ["favorite", "bookmark", "collect", "saved"],
    "浏览": ["view", "browse", "visit", "read"],
    "搜索": ["search", "query", "find", "lookup"],
    "下载": ["download", "dl"],
    "上传": ["upload", "up"],
    "支付": ["payment", "pay", "paid", "charge"],
    "退款": ["refund", "return", "reimburse"],
    "发货": ["shipped", "delivery", "deliver", "dispatch"],
    "收货": ["received", "delivered", "arrived"],
    "取消": ["cancel", "cancelled", "void"],
    "完成": ["completed", "done", "finished", "success"],
    "失败": ["failed", "failure", "error", "fail"],
    "处理": ["processing", "process", "handled", "pending"],
    "等待": ["waiting", "pending", "queued"],
    "超时": ["timeout", "expired", "timed_out"],
    "分组": ["group", "category", "cluster", "segment"],
    "汇总": ["summary", "total", "aggregate", "sum"],
    "平均值": ["avg", "average", "mean"],
    "最大值": ["max", "maximum", "highest"],
    "最小值": ["min", "minimum", "lowest"],
    "计数": ["count", "total", "number"],
    "排名": ["rank", "ranking", "position", "order"],
    "列表": ["list", "listing", "catalog"],
    "详情": ["detail", "details", "info", "information"],
    "全部": ["all", "every", "total", "whole"],
    "最近": ["recent", "latest", "last", "newest"],
    "最早": ["earliest", "first", "oldest"],
    "今天": ["today", "current_date"],
    "昨天": ["yesterday"],
    "本周": ["week", "this_week", "weekly"],
    "本月": ["month", "this_month", "monthly"],
    "今年": ["year", "this_year", "annual", "yearly"],
    # ── New: Database/table-specific terms ──
    "知识库": ["knowledge_base", "knowledge", "kb"],
    "知识": ["knowledge", "info", "information", "knowledge_base"],
    "通知": ["notification", "notifications", "msg", "notice", "notify"],
    "报表": ["report", "reports", "report_data"],
    "报告": ["report", "reports"],
    "记忆": ["memory", "memories", "long_term_memory", "short_term_memory", "long_term_memories", "short_term_memories"],
    "长期": ["long_term", "long"],
    "短期": ["short_term", "short"],
    "操作": ["operation", "operations", "operation_log", "operation_logs", "action"],
    "动作": ["action", "operation"],
    "调用": ["call", "api_call", "invocation", "token_usage"],
    "API": ["api", "api_call"],
    "模型": ["model", "models", "model_config", "model_configs"],
    "工作流": ["workflow", "workflow_session", "workflow_sessions"],
    "问卷": ["questionnaire", "questionnaire_answer", "questionnaire_answers"],
    "答案": ["answer", "questionnaire_answer", "questionnaire_answers"],
    "摘要": ["summary", "conversation_summary", "conversation_summaries"],
    "短期记忆": ["short_term_memory", "short_term_memories"],
    "长期记忆": ["long_term_memory", "long_term_memories"],
    "对话摘要": ["conversation_summary", "conversation_summaries"],
    "项目文件": ["project_file", "project_files"],
    "用户会话": ["user_session", "user_sessions"],
    "操作记录": ["operation_log", "operation_logs"],
    "模型配置": ["model_config", "model_configs"],
    "工作流会话": ["workflow_session", "workflow_sessions"],
}


class SchemaLinkingNode(AgenticNode):
    """Link natural language entities to database schema tables and columns.

    Phase 2: keyword matching + FK expansion (no RAG).
    Phase 3: delegates to ``SchemaRetriever`` for 5-step RAG-powered retrieval
             when available; falls back to keyword matching otherwise.

    Usage::

        # Phase 2 style (keyword only)
        node = SchemaLinkingNode()
        output = await node.execute(NodeInput(
            query_text="查询用户的订单金额",
            context={"sqr": sqr, "schema": db_schema},
        ))

        # Phase 3 style (with RAG)
        from app.knowledge.schema_retriever import SchemaRetriever
        retriever = SchemaRetriever(schema_rag=...)
        node = SchemaLinkingNode(retriever=retriever)
    """

    name = "schema_linking"
    description = (
        "Link natural language entities to database tables and columns "
        "using keyword matching and FK expansion (Phase 2) or 5-step "
        "RAG-powered hierarchical retrieval (Phase 3)"
    )

    def __init__(self, retriever: Any = None) -> None:
        """Create a SchemaLinkingNode.

        Args:
            retriever: Optional ``SchemaRetriever`` for RAG-powered 5-step
                       retrieval.  When ``None``, falls back to Phase 2
                       keyword matching.
        """
        super().__init__()
        self._retriever = retriever

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Link entities from SQR to schema objects.

        Expected ``input.context`` keys:
            - ``sqr``: ``SQR`` from ParseNLNode (falls back to query_text).
            - ``schema``: ``SchemaSnapshot`` for the target database.

        Expected ``input.config`` keys:
            - ``fk_expand``: bool (default True) — enable FK expansion.
            - ``match_threshold``: float (default 0.6) — minimum confidence.
            - ``db_id``: str — database filter for RAG.
            - ``language``: str — language hint (default ``"auto"``).

        Returns:
            ``NodeOutput`` with linked tables, columns, and a filtered
            ``SchemaSnapshot`` in ``context["linked_schema"]``.
        """
        sqr: SQR | None = input.context.get("sqr")
        schema: SchemaSnapshot | None = input.context.get("schema")

        fk_expand = input.config.get("fk_expand", True)
        match_threshold = input.config.get("match_threshold", 0.6)
        db_id: str | None = input.config.get("db_id")
        language: str = input.config.get("language", "auto")

        if sqr is None:
            sqr = SQR(raw_text=input.query_text)

        if schema is None or not schema.tables:
            return NodeOutput(
                result={"tables": [], "columns": [], "confidence": 0.0},
                metadata={"status": "no_schema", "tables_linked": 0, "method": "none"},
                context={"linked_tables": [], "linked_columns": [], "linked_schema": None},
            )

        # ── Phase 3: RAG-powered 5-step retrieval ──────────────────
        if self._retriever is not None:
            try:
                result = await self._retriever.retrieve(
                    query=input.query_text,
                    schema=schema,
                    sqr=sqr,
                    db_id=db_id,
                    top_k=input.config.get("top_k", 10),
                    fk_expand=fk_expand,
                    match_threshold=match_threshold,
                    language=language,
                )
                linked_schema = result.filtered_schema
                resolved_columns: list[dict[str, Any]] = [
                    {
                        "name": c.name,
                        "table": c.table,
                        "confidence": c.confidence,
                        "candidates": c.candidates,
                    }
                    for c in result.columns
                ]
                return NodeOutput(
                    result={
                        "tables": result.tables,
                        "columns": resolved_columns,
                        "confidence": result.confidence,
                    },
                    metadata={
                        "status": "success",
                        "tables_linked": len(result.tables),
                        "columns_linked": len(resolved_columns),
                        "method": result.method,
                    },
                    context={
                        "linked_tables": result.tables,
                        "linked_columns": resolved_columns,
                        "linked_schema": linked_schema,
                    },
                )
            except Exception:
                # Fall through to keyword matching on error
                pass

        # ── Phase 2: keyword matching + FK expansion ───────────────
        candidates = _gather_candidates(sqr)
        matched_tables, matched_columns = _match_schema(
            candidates, schema, match_threshold
        )

        # ── Reverse table-name scan ────────────────────────────────
        # Scan ALL table names against the query text to catch tables
        # that the forward mapping (Chinese→English) missed.  This is
        # critical for tables like "knowledge_base", "notifications",
        # "reports", etc. whose Chinese terms might not be in the map.
        reverse_matched = _scan_table_names_reverse(input.query_text, schema)
        if reverse_matched:
            # Add tables found by reverse scan that aren't already matched
            existing = set(matched_tables)
            for t in reverse_matched:
                if t not in existing:
                    matched_tables.append(t)
                    existing.add(t)
                    logger.debug(
                        "Reverse scan found table '%s' for query '%s'",
                        t, input.query_text[:50],
                    )
            # Also add table candidates so column matching can find
            # columns in these newly-discovered tables.
            for t in reverse_matched:
                if t not in {c["name"] for c in candidates}:
                    candidates.append({"name": t, "kind": "table", "source": "reverse_scan"})
                    candidates.append({"name": t, "kind": "column", "source": "reverse_scan"})
            # Re-run matching with the expanded candidates — keep BOTH
            # tables and columns from the second pass
            second_tables, matched_columns = _match_schema(candidates, schema, match_threshold)
            existing_tbls_2 = set(matched_tables)
            for t in second_tables:
                if t not in existing_tbls_2:
                    matched_tables.append(t)
                    existing_tbls_2.add(t)

        if fk_expand:
            matched_tables = _expand_foreign_keys(matched_tables, schema)

        # ── Add tables from matched columns to matched_tables ──────
        # When the user asks about a COLUMN (e.g. "查询创建时间"),
        # table-name matching finds 0 tables but column matching finds
        # the column in many tables.  We need to add those tables to
        # matched_tables so the LLM sees them and generates SQL.
        if matched_columns:
            existing_tbls = set(matched_tables)
            for col_entry in matched_columns:
                col_all_tables = col_entry.get("all_tables", [])
                if not col_all_tables and col_entry.get("table"):
                    col_all_tables = [col_entry["table"]]
                for t in col_all_tables:
                    if t and t not in existing_tbls:
                        matched_tables.append(t)
                        existing_tbls.add(t)

        resolved_columns = _disambiguate_columns(
            matched_columns, matched_tables, schema
        )
        linked_schema = _filter_schema(schema, matched_tables)
        confidence = _compute_link_confidence(matched_tables, matched_columns, candidates)

        # ── Build multi-table hint ─────────────────────────────────
        # When a column exists in multiple tables, record every table so
        # the generator can produce one SQL per independent table group.
        multi_table_hint = _build_multi_table_hint(
            matched_columns, schema, query_text=input.query_text
        )

        return NodeOutput(
            result={
                "tables": matched_tables,
                "columns": resolved_columns,
                "confidence": confidence,
            },
            metadata={
                "status": "success",
                "tables_linked": len(matched_tables),
                "columns_linked": len(resolved_columns),
                "method": "keyword_match",
                "multi_table_hint": multi_table_hint,
            },
            context={
                "linked_tables": matched_tables,
                "linked_columns": resolved_columns,
                "linked_schema": linked_schema,
                "multi_table_hint": multi_table_hint,
            },
        )


# ── Candidate gathering ───────────────────────────────────────────────


def _gather_candidates(sqr: SQR) -> list[dict[str, Any]]:
    """Gather table and column name candidates from an SQR.

    Returns a list of ``{"name": str, "kind": "table"|"column", "source": str}``.
    """
    candidates: list[dict[str, Any]] = []

    # From target_tables (explicitly mentioned tables)
    for t in sqr.target_tables:
        candidates.append({"name": t, "kind": "table", "source": "target_tables"})

    # From entities
    for entity in sqr.entities:
        kind = "table" if entity.type in ("table", "table_ref") else "column"
        candidates.append({"name": entity.name, "kind": kind, "source": "entity"})

    # From NL text: extract noun-like tokens as table AND column candidates
    text = sqr.raw_text
    for word in _tokenize_zh(text):
        if len(word) >= 2 and word not in {c["name"] for c in candidates}:
            # Add as BOTH table and column candidate — English NL tokens
            # like "users", "orders" often refer to table names, not just
            # columns. Without the table candidate, _match_schema only
            # searches columns and misses exact table-name matches.
            candidates.append({"name": word, "kind": "table", "source": "nl_text"})
            candidates.append({"name": word, "kind": "column", "source": "nl_text"})
            # Expand Chinese tokens to English candidates via mapping
            en_candidates = _ZH_TO_EN_MAP.get(word)
            if en_candidates:
                for en in en_candidates:
                    if en not in {c["name"] for c in candidates}:
                        # Add as BOTH table and column candidate — Chinese terms
                        # like "用户"(user), "订单"(order) often refer to tables
                        candidates.append({"name": en, "kind": "table", "source": "zh_en_map"})
                        candidates.append({"name": en, "kind": "column", "source": "zh_en_map"})

    return candidates


def _tokenize_zh(text: str) -> list[str]:
    """Simple Chinese tokenizer — uses jieba if available, falls back to regex.

    Jieba provides proper word segmentation for Chinese text, which is
    essential for matching NL terms to database table/column names.
    """
    import re

    # Try jieba first for proper Chinese word segmentation
    try:
        import jieba

        # Remove common stop words and punctuation
        stop_pattern = re.compile(r"[，。！？、：；（）「」『』\"'…—\s]+")
        cleaned = stop_pattern.sub(" ", text)
        words = jieba.lcut(cleaned)
        tokens = [w.strip() for w in words if len(w.strip()) >= 2]
        if tokens:
            return tokens
    except ImportError:
        pass

    # Fallback: split on common delimiters
    cleaned = re.sub(r"[，。！？、的了吗呢么在与和或从按按照以]", " ", text)
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]
    return tokens


# ── Reverse table-name scan ────────────────────────────────────────────
# Build a reverse map: English term → list of Chinese terms that map to it.
# This lets us scan table names (English) and find which Chinese query terms
# might refer to them, even when the forward map is incomplete.

_EN_TO_ZH_MAP: dict[str, list[str]] = {}
for _zh, _en_list in _ZH_TO_EN_MAP.items():
    for _en in _en_list:
        _EN_TO_ZH_MAP.setdefault(_en.lower(), []).append(_zh)
del _zh, _en_list, _en


def _scan_table_names_reverse(
    query_text: str, schema: SchemaSnapshot
) -> list[str]:
    """Scan ALL table names against the query text via reverse lookup.

    For each table name in the schema, split it into parts (by underscore)
    and check if any part maps (via the reverse map) to a Chinese term
    that appears in *query_text*.

    This catches cases where the forward map (Chinese→English) is missing
    a term but the table name still contains a recognizable English word
    that maps back to a Chinese term in the query.

    Returns a list of matched table names.
    """
    if not query_text or schema is None:
        return []

    query_lower = query_text.lower()

    # Also tokenize the query to get individual Chinese terms
    query_tokens = set(_tokenize_zh(query_text))
    query_tokens_lower = {t.lower() for t in query_tokens}

    matched: list[str] = []

    for table_name in schema.tables:
        # Split table name into meaningful parts
        parts = table_name.replace("-", "_").split("_")
        for part in parts:
            part_lower = part.lower().strip()
            if len(part_lower) < 3:
                continue

            # 1. Check if this English part has a Chinese reverse mapping
            zh_terms = _EN_TO_ZH_MAP.get(part_lower, [])
            for zh in zh_terms:
                if zh in query_text or zh.lower() in query_tokens_lower:
                    if table_name not in matched:
                        matched.append(table_name)
                    break

            # 2. Also check if the English part directly appears in the query
            # (e.g., user types "token" in a Chinese query)
            if part_lower in query_lower and len(part_lower) >= 4:
                if table_name not in matched:
                    matched.append(table_name)

            # 3. Check if any part of the query (English tokens) matches
            # the table name part
            for qtok in query_tokens_lower:
                if len(qtok) >= 4 and (qtok == part_lower or qtok in part_lower or part_lower in qtok):
                    if table_name not in matched:
                        matched.append(table_name)
                    break

    return matched


# ── Schema matching ────────────────────────────────────────────────────


def _match_schema(
    candidates: list[dict[str, Any]],
    schema: SchemaSnapshot,
    threshold: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Match candidates against schema tables and columns.

    Returns ``(matched_tables, matched_columns)`` where each column
    entry is ``{"name": str, "table": str | None, "confidence": float,
    "all_tables": list[str]}``.  When a column exists in multiple tables,
    *all_tables* lists every table that contains it — this enables the
    downstream generator to produce one SQL candidate per table group.
    """
    matched_tables: dict[str, float] = {}  # name → best confidence
    matched_columns: dict[str, dict[str, Any]] = {}  # key → {name, table, confidence, all_tables}

    for candidate in candidates:
        name = candidate["name"]
        kind = candidate["kind"]

        if kind == "table":
            all_matches = _all_table_matches(name, schema, threshold)
            for t_name, t_conf in all_matches:
                if t_name not in matched_tables or t_conf > matched_tables[t_name]:
                    matched_tables[t_name] = t_conf

        elif kind == "column":
            all_matches = _all_column_matches(name, schema, threshold)
            if all_matches:
                # Best match determines the primary table
                best_tbl, best_col, best_conf = all_matches[0]
                key = f"{best_tbl}.{best_col}" if best_tbl else best_col
                # Only include tables with HIGH confidence (>= 0.8) in
                # all_tables.  Substring matches (0.65/0.6) produce too
                # many false positives — e.g. "name" matching "username"
                # in knowledge_base, model_configs, etc.
                all_tables = list(dict.fromkeys(
                    m[0] for m in all_matches if m[0] and m[2] >= 0.8
                ))
                if key not in matched_columns or best_conf > matched_columns[key]["confidence"]:
                    matched_columns[key] = {
                        "name": best_col,
                        "table": best_tbl,
                        "confidence": best_conf,
                        "all_tables": all_tables,
                    }

    return list(matched_tables.keys()), list(matched_columns.values())


def _best_table_match(name: str, schema: SchemaSnapshot) -> tuple[str | None, float]:
    """Find the best table match for *name*."""
    name_lower = name.lower().strip()

    for t_name, t in schema.tables.items():
        # Exact match
        if t_name == name:
            return t_name, 1.0
        # Case-insensitive exact
        if t_name.lower() == name_lower:
            return t_name, 0.95
        # Comment match
        if t.comment and name_lower in t.comment.lower():
            return t_name, 0.7

    # Substring / partial match
    for t_name in schema.tables:
        t_lower = t_name.lower()
        if name_lower in t_lower or t_lower in name_lower:
            return t_name, 0.65

    return None, 0.0


def _best_column_match(
    name: str, schema: SchemaSnapshot
) -> tuple[str | None, str | None, float]:
    """Find the best column match for *name* across all tables.

    Returns ``(table_name, column_name, confidence)``.
    """
    name_lower = name.lower().strip()

    best_table: str | None = None
    best_col: str | None = None
    best_conf = 0.0

    for t_name, t in schema.tables.items():
        for col in t.columns:
            col_lower = col.name.lower()
            conf = 0.0

            if col.name == name:
                conf = 1.0
            elif col_lower == name_lower:
                conf = 0.95
            elif col.comment and name_lower in col.comment.lower():
                conf = 0.75
            elif name_lower in col_lower:
                conf = 0.65
            elif col_lower in name_lower:
                conf = 0.6

            if conf > best_conf:
                best_conf = conf
                best_table = t_name
                best_col = col.name

    return best_table, best_col, best_conf


def _all_table_matches(
    name: str, schema: SchemaSnapshot, threshold: float
) -> list[tuple[str, float]]:
    """Return ALL table matches for *name* above *threshold*, sorted by confidence."""
    name_lower = name.lower().strip()
    results: list[tuple[str, float]] = []

    for t_name, t in schema.tables.items():
        conf = 0.0
        if t_name == name:
            conf = 1.0
        elif t_name.lower() == name_lower:
            conf = 0.95
        elif t.comment and name_lower in t.comment.lower():
            conf = 0.7
        elif name_lower in t_name.lower() or t_name.lower() in name_lower:
            conf = 0.65

        if conf >= threshold:
            results.append((t_name, conf))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _all_column_matches(
    name: str, schema: SchemaSnapshot, threshold: float
) -> list[tuple[str, str, float]]:
    """Return ALL column matches for *name* across all tables, sorted by confidence.

    Returns ``[(table_name, column_name, confidence), ...]`` — one entry per
    table that contains a matching column.  This allows the downstream generator
    to discover that a term like "name" maps to *users.name*, *employees.name*,
    and *contacts.name* simultaneously.
    """
    name_lower = name.lower().strip()
    results: list[tuple[str, str, float]] = []

    for t_name, t in schema.tables.items():
        for col in t.columns:
            col_lower = col.name.lower()
            conf = 0.0

            if col.name == name:
                conf = 1.0
            elif col_lower == name_lower:
                conf = 0.95
            elif col.comment and name_lower in col.comment.lower():
                conf = 0.75
            elif name_lower in col_lower:
                conf = 0.65
            elif col_lower in name_lower:
                conf = 0.6

            if conf >= threshold:
                results.append((t_name, col.name, conf))

    results.sort(key=lambda x: x[2], reverse=True)
    return results


def _build_multi_table_hint(
    matched_columns: list[dict[str, Any]],
    schema: SchemaSnapshot | None = None,
    query_text: str = "",
) -> dict[str, Any]:
    """Build a hint structure that tells the generator about multi-table alternatives.

    When a column like "name" is matched in *users*, *employees*, and *contacts*,
    this returns::

        {
            "has_alternatives": True,
            "column_groups": {
                "name": {
                    "best_table": "users",
                    "all_tables": ["users", "employees", "contacts"],
                }
            },
            "independent_tables": ["users", "employees", "contacts"],
        }

    *independent_tables* lists tables that can answer the query on their own
    (i.e. they contain all the columns the user asked about without needing JOINs).

    When *schema* is provided and no columns are matched but the schema has many
    tables, a heuristic hint is generated:
    1. First, a direct keyword scan of *query_text* against all schema columns
       is performed (using jieba tokenization + Chinese→English mapping).
    2. If that also fails, all tables are added as candidates.
    """
    has_alternatives = False
    column_groups: dict[str, dict[str, Any]] = {}
    all_independent: set[str] = set()

    for col in matched_columns:
        col_name = col.get("name", "")
        all_tables: list[str] = col.get("all_tables", [])
        if not all_tables and col.get("table"):
            all_tables = [col["table"]]

        if len(all_tables) > 1:
            has_alternatives = True

        if col_name:
            column_groups[col_name] = {
                "best_table": col.get("table"),
                "all_tables": all_tables,
            }
        for t in all_tables:
            all_independent.add(t)

    # ── Fallback: when no columns matched, try direct keyword scan ──
    if not all_independent and schema is not None and len(schema.tables) >= 2:
        # Try direct keyword scan against query text
        if query_text:
            direct_matches = _direct_keyword_scan(query_text, schema)
            if direct_matches:
                has_alternatives = True
                for col_name, tables in direct_matches.items():
                    column_groups[col_name] = {
                        "best_table": tables[0] if tables else "",
                        "all_tables": tables,
                    }
                    for t in tables:
                        all_independent.add(t)

        # No fallback: if no matches found, leave independent_tables empty
        # so the caller can skip SQL generation for this database

    return {
        "has_alternatives": has_alternatives,
        "column_groups": column_groups,
        "independent_tables": sorted(all_independent),
    }


# ── FK expansion ───────────────────────────────────────────────────────


def _direct_keyword_scan(
    query_text: str, schema: SchemaSnapshot
) -> dict[str, list[str]]:
    """Scan the NL query text directly against all schema columns.

    Uses jieba tokenization + Chinese→English mapping to find matching
    columns across all tables.  Returns a dict mapping column_name →
    list of table names that contain that column.

    This is a fallback used when the SQR-based matching returns 0 results
    (e.g., when the NL parser fails to extract entities from Chinese text).

    IMPORTANT: If no table name matches any query token, returns {}
    because the query is likely irrelevant to this database.
    """
    if not query_text or schema is None:
        return {}

    # Tokenize the query
    candidates: list[str] = list(_tokenize_zh(query_text))

    # Expand with Chinese→English mapping
    expanded: list[str] = []
    for token in candidates:
        expanded.append(token)
        en_variants = _ZH_TO_EN_MAP.get(token, [])
        expanded.extend(en_variants)
        # Also try lowercase
        token_lower = token.lower()
        if token_lower != token:
            expanded.append(token_lower)
            en_variants_lower = _ZH_TO_EN_MAP.get(token_lower, [])
            expanded.extend(en_variants_lower)

    # Deduplicate
    expanded = list(dict.fromkeys(expanded))

    # ── Step 1: Check if any table name matches a query token ──
    # If no table name matches, the query is likely irrelevant to this database
    table_matched = False
    for token in expanded:
        token_lower = token.lower().strip()
        if len(token_lower) < 2:
            continue
        for t_name in schema.tables:
            t_lower = t_name.lower()
            if (
                t_lower == token_lower
                or (len(token_lower) >= 3 and token_lower in t_lower)
                or (len(t_lower) >= 3 and t_lower in token_lower)
            ):
                table_matched = True
                break
        if table_matched:
            break

    if not table_matched:
        return {}

    # ── Step 2: Scan all schema columns for matches ──
    result: dict[str, list[str]] = {}
    for token in expanded:
        token_lower = token.lower().strip()
        if len(token_lower) < 2:
            continue
        for t_name, table in schema.tables.items():
            for col in table.columns:
                col_lower = col.name.lower()
                # Exact match or token is substring of column name
                if (
                    col_lower == token_lower
                    or (len(token_lower) >= 3 and token_lower in col_lower)
                ):
                    if col.name not in result:
                        result[col.name] = []
                    if t_name not in result[col.name]:
                        result[col.name].append(t_name)

    return result


def _expand_foreign_keys(
    tables: list[str], schema: SchemaSnapshot
) -> list[str]:
    """Expand the table list by 1 level of foreign key references.

    For each table in *tables*, add any tables it references via FK
    and any tables that reference it.  Iterates until the set stabilises
    (handles chains like orders → order_items → products).
    """
    expanded = set(tables)

    while True:
        size_before = len(expanded)

        # Forward: tables referenced by FK in current set
        for t_name in list(expanded):
            t = schema.get_table(t_name)
            if t is None:
                continue
            for fk in t.foreign_keys:
                expanded.add(fk.ref_table)

        # Reverse: tables that reference any table in current set
        for other_name, other_t in schema.tables.items():
            for fk in other_t.foreign_keys:
                if fk.ref_table in expanded:
                    expanded.add(other_name)

        if len(expanded) == size_before:
            break  # converged

    return list(expanded)


# ── Column disambiguation ──────────────────────────────────────────────


def _disambiguate_columns(
    columns: list[dict[str, Any]],
    linked_tables: list[str],
    schema: SchemaSnapshot,
) -> list[dict[str, Any]]:
    """Resolve ambiguous columns to specific tables.

    When a column name appears in multiple linked tables, pick the one
    with the highest confidence.  When a column has no table yet but
    appears in only one linked table, assign it.
    """
    resolved: list[dict[str, Any]] = []

    for col in columns:
        if col.get("table"):
            # Already qualified — keep if table is linked
            if col["table"] in linked_tables:
                resolved.append(col)
            else:
                # Try to find the column in a linked table
                found = False
                for t_name in linked_tables:
                    t = schema.get_table(t_name)
                    if t and t.get_column(col["name"]):
                        resolved.append({**col, "table": t_name})
                        found = True
                        break
                if not found:
                    resolved.append(col)  # keep as-is
        else:
            # Unqualified — find tables that contain this column
            candidates = []
            for t_name in linked_tables:
                t = schema.get_table(t_name)
                if t and t.get_column(col["name"]):
                    candidates.append(t_name)

            if len(candidates) == 1:
                resolved.append({**col, "table": candidates[0]})
            elif len(candidates) > 1:
                # Ambiguous — keep as unresolved but note candidates
                resolved.append({**col, "table": None, "candidates": candidates})
            else:
                # Not found in any linked table
                resolved.append(col)

    return resolved


# ── Filtered schema ────────────────────────────────────────────────────


def _filter_schema(
    schema: SchemaSnapshot, tables: list[str]
) -> SchemaSnapshot:
    """Return a SchemaSnapshot containing only the *tables*."""
    filtered = {
        name: t for name, t in schema.tables.items() if name in tables
    }
    return SchemaSnapshot(
        database_type=schema.database_type,
        database_name=schema.database_name,
        tables=filtered,
    )


# ── Confidence ─────────────────────────────────────────────────────────


def _compute_link_confidence(
    tables: list[str],
    columns: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> float:
    """Compute an overall linking confidence score."""
    if not candidates:
        return 0.0
    # Coverage ratio: how many candidates were matched?
    matched_count = len(tables) + len(columns)
    candidate_count = len(candidates)
    coverage = min(matched_count / max(candidate_count, 1), 1.0)

    # Average column confidence
    col_avg = (
        sum(c.get("confidence", 0.0) for c in columns) / max(len(columns), 1)
        if columns
        else 1.0
    )

    return round(0.4 * coverage + 0.6 * col_avg, 3)
