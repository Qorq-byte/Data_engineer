"""Ambiguity detector — 4 ambiguity types, regex-based, zero LLM cost.

See SPEC §4.7.1 (d) and implementation-plan §4.7.1 for design rationale.

Detects ambiguity signals in NL queries — marks but does NOT resolve them.
Resolution is deferred to the clarification phase (Phase 3 multi-agent).
"""

from __future__ import annotations

import re

from app.models.query import Ambiguity, IntentType

# ── Attribute ambiguity ───────────────────────────────────────────────
# Generic dimension nouns that could map to multiple columns.

_ATTRIBUTE_PATTERNS: list[tuple[str, str, list[str]]] = [
    # Chinese
    (
        r"(?:各个?|每个?|不同|按|根据|依照)\s*(?:的)?\s*(地区|区域|城市|省份)",
        "'{0}'可能对应表中多个地理列（如 city/province/region）",
        ["city", "province", "region"],
    ),
    (
        r"(?:按|根据|依照)\s*(名称|名字|标题)",
        "'{0}'是通用列名，可能对应多张表的同名列",
        ["name", "title", "label"],
    ),
    (
        r"(?:按|根据|依照)\s*(类型|类别|种类|分类)",
        "'{0}'可能对应不同粒度的分类列（如 category/subcategory/type）",
        ["category", "type", "subcategory"],
    ),
    (
        r"(?:按|根据|依照)\s*(日期|时间)",
        "'{0}'可能对应多个日期列（如 created_at/updated_at/order_date）",
        ["create_time", "update_time", "order_date"],
    ),
    (
        r"(?:按|根据|依照)\s*(状态)",
        "'{0}'可能对应不同状态列（如 status/state/order_status）",
        ["status", "state", "order_status"],
    ),
    (
        r"(?:按|根据|依照)\s*(级别|等级|层级)",
        "'{0}'可能对应不同等级列（如 level/tier/grade）",
        ["level", "tier", "grade"],
    ),
    (
        r"(?:按|根据|依照)\s*(ID|编号|代码|编码)",
        "'{0}'可能对应不同标识列（如 user_id/product_id/order_id）",
        ["user_id", "product_id", "order_id"],
    ),
    # English
    (
        r"\b(?:by|per|for each|different)\s+(region|city|cities|area|location)\b",
        "'{0}' could map to multiple geography columns (city/province/region)",
        ["city", "province", "region"],
    ),
    (
        r"\b(?:by|per)\s+(name|title)\b",
        "'{0}' is a generic name — could match columns across multiple tables",
        ["name", "title", "label"],
    ),
    (
        r"\b(?:by|per)\s+(type|category|kind|class)\b",
        "'{0}' could map to different granularity columns (category/subcategory)",
        ["category", "type", "subcategory"],
    ),
    (
        r"\b(?:by|per)\s+(date|time)\b",
        "'{0}' could map to multiple date columns (created_at/updated_at/…)",
        ["create_time", "update_time", "order_date"],
    ),
    (
        r"\b(?:by|per)\s+(status|state)\b",
        "'{0}' could map to different status columns (status/state/order_status)",
        ["status", "state", "order_status"],
    ),
    (
        r"\b(?:by|per)\s+(level|tier|grade)\b",
        "'{0}' could map to different level columns (level/tier/grade)",
        ["level", "tier", "grade"],
    ),
    (
        r"\b(?:by|per)\s+(id|code)\b",
        "'{0}' could map to different identifier columns (user_id/product_id/…)",
        ["user_id", "product_id", "order_id"],
    ),
]

# ── Aggregation ambiguity ─────────────────────────────────────────────
# Only triggered when intent == AGGREGATE.

_AGGREGATION_PATTERNS: list[tuple[str, str, list[str]]] = [
    # Chinese
    (
        r"平均(?:值|数|金额|价格|工资|收入|年龄|时长|时间)?",
        "聚合方式不明确：'平均'可能指均值(MEAN)、中位数(MEDIAN)或移动平均",
        ["均值 AVG", "中位数 MEDIAN", "移动平均 ROLLING AVG"],
    ),
    (
        r"排名|排行|第.*名|前\d+名",
        "排名维度和排序方向未指定",
        ["按金额排名", "按数量排名", "按时间排名"],
    ),
    (
        r"(?:占比|比例|百分比|份额)",
        "分母/基准未明确",
        ["占总计比例", "占分类比例", "环比变化"],
    ),
    (
        r"分组|归类",
        "分组维度未指定或存在多个可选维度",
        [],
    ),
    # English
    (
        r"\baverage\b",
        "Aggregation method unclear — 'average' could be mean, median, or mode",
        ["AVG (mean)", "MEDIAN", "MODE"],
    ),
    (
        r"\brank(?:ing)?\b",
        "Ranking dimension and sort direction not specified",
        ["rank by value", "rank by count", "rank by date"],
    ),
    (
        r"\bpercent(?:age|ile)?\b|\bratio\b",
        "Denominator/baseline not specified for percentage",
        ["% of total", "% of category", "% change"],
    ),
    (
        r"\bgroup\b",
        "Grouping dimension not specified or multiple candidates exist",
        [],
    ),
]

# ── Range ambiguity ───────────────────────────────────────────────────
# Degree / threshold words without explicit criteria.

_RANGE_PATTERNS: list[tuple[str, str, list[str]]] = [
    # Chinese — degree word + noun
    (
        r"(大额|大单|大宗)(?:订单|交易|客户|采购)?",
        "程度词'{0}'无明确金额阈值",
        [],
    ),
    (
        r"(高价值|高净值|高客单价|优质|重点|VIP|核心)(?:客户|用户|订单|商品|产品)?",
        "'{0}'无明确判断标准和阈值",
        [],
    ),
    (
        r"(高价|高端|高档)(?:商品|产品|客户|市场)?",
        "程度词'{0}'无明确价格阈值",
        [],
    ),
    (
        r"(低价值|低端|低档|劣质)(?:客户|商品|产品)?",
        "程度词'{0}'无明确判断标准",
        [],
    ),
    (
        r"(?:高|低)(?:评分|评价|信用|信誉|热度|活跃度)",
        "程度词无明确分数阈值",
        [],
    ),
    # English
    (
        r"\bhigh[\s-]value\b",
        "'high-value' has no explicit value threshold",
        [],
    ),
    (
        r"\bpremium\b|\bVIP\b|\btop[\s-]tier\b",
        "'{0}' has no explicit qualification criteria",
        [],
    ),
    (
        r"\b(?:big|large|major)\s+(?:orders?|customers?|clients?|deals?)\b",
        "Degree word has no explicit size threshold",
        [],
    ),
    (
        r"\b(?:small|minor)\s+(?:orders?|customers?|clients?)\b",
        "Degree word has no explicit size threshold",
        [],
    ),
    (
        r"\b(?:high|low)[\s-](?:score|rat(?:ing|ed)|credit)\b",
        "Degree word has no explicit score threshold",
        [],
    ),
]

# ── Temporal ambiguity ────────────────────────────────────────────────
# Time references without explicit range boundaries.

_TEMPORAL_PATTERNS: list[tuple[str, str, list[str]]] = [
    # Chinese — "最近/近期" without explicit N + unit
    (
        r"最近(?!\s*(?:\d+|一|两|三|四|五|六|七|八|九|十|几|数|多)\s*个?\s*的?\s*(?:天|周|星期|月|季度|年|小时|分钟))",
        "'最近'未指定具体时间范围，系统将使用默认值",
        ["最近7天", "最近30天", "最近90天", "本月至今"],
    ),
    (
        r"近期|近来",
        "'{0}'未指定具体时间范围，系统将使用默认值",
        ["最近7天", "最近30天", "最近90天"],
    ),
    (
        r"过去(?!\s*(?:\d+|一|两|三|四|五|六|七|八|九|十|几|数|多)\s*个?\s*的?\s*(?:天|周|星期|月|季度|年|小时|分钟))",
        "'过去'未指定具体时间范围",
        ["过去7天", "过去30天", "过去90天"],
    ),
    (
        r"以往|历史上",
        "'{0}'未指定具体时间范围（历史数据的具体起止时间不明确）",
        ["过去30天", "过去90天", "过去一年"],
    ),
    (
        r"历史(?!\s*(?:\d+|一|两|几|数)\s*个?\s*的?\s*(?:天|周|月|季度|年))",
        "'历史'未指定具体时间范围",
        ["过去30天", "过去90天", "过去一年"],
    ),
    # English — "recent/lately" without explicit N + unit
    (
        r"\brecent(?:ly)?\b(?!\s+\d+\s*(?:day|week|month|year|hour)s?)",
        "'recent' does not specify a concrete time range",
        ["last 7 days", "last 30 days", "last 90 days", "month to date"],
    ),
    (
        r"\blately\b|\bof late\b",
        "'{0}' does not specify a concrete time range",
        ["last 7 days", "last 30 days", "last 90 days"],
    ),
    (
        r"\bpast\b(?!\s+\d+\s*(?:day|week|month|year|hour)s?)",
        "'past' does not specify a concrete time range",
        ["past 7 days", "past 30 days", "past 90 days"],
    ),
    (
        r"\bhistorical\b(?!\s+\d+\s*(?:day|week|month|year)s?)",
        "'historical' does not specify a concrete time range",
        ["past 30 days", "past 90 days", "past year"],
    ),
]


class AmbiguityDetector:
    """Detect 4 types of ambiguity in NL queries.

    Uses regex patterns to flag potential ambiguity — marks but does
    **not** resolve. Resolution (e.g., asking the user, applying defaults)
    happens downstream.

    Usage::

        detector = AmbiguityDetector()
        ambiguities = detector.detect(
            "统计大额订单的平均金额", IntentType.AGGREGATE
        )
        # → [
        #     Ambiguity(aspect="range", description="…"),
        #     Ambiguity(aspect="aggregation", description="…"),
        # ]
    """

    def __init__(self) -> None:
        self._attr = _compile(_ATTRIBUTE_PATTERNS)
        self._agg = _compile(_AGGREGATION_PATTERNS)
        self._range = _compile(_RANGE_PATTERNS)
        self._temp = _compile(_TEMPORAL_PATTERNS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        nl_text: str,
        intent: IntentType,
        context: dict | None = None,  # noqa: ARG002  reserved for Phase 3
    ) -> list[Ambiguity]:
        """Detect ambiguity signals in *nl_text*.

        Args:
            nl_text: Raw NL input string.
            intent: Classified intent type (aggregation checks only fire
                    for ``AGGREGATE``).
            context: Reserved for schema-aware checks in Phase 3.

        Returns:
            List of ``Ambiguity`` objects (empty if nothing detected).
        """
        results: list[Ambiguity] = []
        results.extend(self._check_attribute_ambiguity(nl_text))
        if intent == IntentType.AGGREGATE:
            results.extend(self._check_aggregation_ambiguity(nl_text))
        results.extend(self._check_range_ambiguity(nl_text))
        results.extend(self._check_temporal_ambiguity(nl_text))
        return results

    # ------------------------------------------------------------------
    # Checkers
    # ------------------------------------------------------------------

    def _check_attribute_ambiguity(self, text: str) -> list[Ambiguity]:
        """Generic dimension nouns that could map to multiple columns."""
        return _match_all(self._attr, "attribute", text)

    def _check_aggregation_ambiguity(self, text: str) -> list[Ambiguity]:
        """Aggregation method / grouping dimension not specified."""
        return _match_all(self._agg, "aggregation", text)

    def _check_range_ambiguity(self, text: str) -> list[Ambiguity]:
        """Degree / threshold words without explicit criteria."""
        return _match_all(self._range, "range", text)

    def _check_temporal_ambiguity(self, text: str) -> list[Ambiguity]:
        """Time references without explicit range boundaries."""
        return _match_all(self._temp, "temporal", text)


# ── Module-private helpers ─────────────────────────────────────────────


def _compile(
    patterns: list[tuple[str, str, list[str]]],
) -> list[tuple[re.Pattern[str], str, list[str]]]:
    """Pre-compile regex patterns for performance."""
    return [(re.compile(p, re.IGNORECASE), desc, list(opts)) for p, desc, opts in patterns]


def _match_all(
    compiled: list[tuple[re.Pattern[str], str, list[str]]],
    aspect: str,
    text: str,
) -> list[Ambiguity]:
    """Run all compiled patterns against *text*, return matching Ambiguities."""
    results: list[Ambiguity] = []
    for pattern, desc, options in compiled:
        m = pattern.search(text)
        if m:
            # Use the first capture group if present, otherwise the full match.
            matched_word = m.group(1) if m.lastindex else m.group(0)
            results.append(
                Ambiguity(
                    aspect=aspect,
                    description=desc.format(matched_word),
                    options=list(options),
                )
            )
    return results
