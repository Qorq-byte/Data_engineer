"""Intent classifier — regex + keyword scoring, zero LLM cost, < 1ms.

See SPEC §4.7.1 (c) and implementation-plan §4.7.1 for design rationale.

Classifies NL queries into 7 intent types with language-aware keyword matching.
"""

from __future__ import annotations

import re

from app.models.query import IntentType

# ── Pattern list: (IntentType, [(pattern, weight, language), ...]) ──────────
# language: "zh" | "en" | "both"
# weight: higher = stronger signal for that intent

_PATTERNS: dict[IntentType, list[tuple[str, int, str]]] = {
    IntentType.SELECT: [
        # Chinese
        (r"查询", 2, "zh"),
        (r"查看", 2, "zh"),
        (r"列出", 2, "zh"),
        (r"显示", 1, "zh"),
        (r"找一下?", 1, "zh"),
        (r"帮我查", 2, "zh"),
        (r"获取", 1, "zh"),
        (r"给我", 1, "zh"),
        (r"有哪些", 1, "zh"),
        (r"是什么", 1, "zh"),
        # English
        (r"\bfind\b", 2, "en"),
        (r"\blist\b", 2, "en"),
        (r"\bshow\b", 1, "en"),
        (r"\bget\b", 1, "en"),
        (r"\bretrieve\b", 2, "en"),
        (r"\bdisplay\b", 1, "en"),
        (r"\bfetch\b", 1, "en"),
        (r"\blook\s*up\b", 1, "en"),
        (r"\bsearch\b", 1, "en"),
        # SQL keywords (strong signal for SELECT)
        (r"\bSELECT\b", 3, "both"),
        (r"\bFROM\b", 2, "both"),
        (r"\bWHERE\b", 2, "both"),
    ],
    IntentType.AGGREGATE: [
        # Chinese
        (r"统计", 3, "zh"),
        (r"汇总", 3, "zh"),
        (r"总计", 3, "zh"),
        (r"合计", 2, "zh"),
        (r"平均", 2, "zh"),
        (r"求和", 2, "zh"),
        (r"计数", 2, "zh"),
        (r"最大值", 2, "zh"),
        (r"最小值", 2, "zh"),
        (r"排名", 2, "zh"),
        (r"分组", 2, "zh"),
        (r"各(个|种|类)", 1, "zh"),
        (r"每(个|种|类|一)", 1, "zh"),
        (r"分别", 1, "zh"),
        (r"按.*分组", 2, "zh"),
        # English
        (r"\bcount\b", 3, "en"),
        (r"\bsum\b", 3, "en"),
        (r"\baverage\b", 3, "en"),
        (r"\bavg\b", 3, "en"),
        (r"\btotal\b", 3, "en"),
        (r"\baggregate\b", 3, "en"),
        (r"\bmax\b", 2, "en"),
        (r"\bmin\b", 2, "en"),
        (r"\brank\b", 2, "en"),
        (r"\bgroup\s*by\b", 3, "en"),
        (r"\bsummary\b", 2, "en"),
        (r"\bbreakdown\b", 2, "en"),
        (r"\bhow many\b", 2, "en"),
        (r"\bhow much\b", 2, "en"),
        (r"per\b", 1, "en"),
        # SQL aggregate keywords (case-insensitive — covered by en patterns above)
    ],
    IntentType.JOIN: [
        # Chinese
        (r"关联", 3, "zh"),
        (r"联合", 2, "zh"),
        (r"连接", 1, "zh"),
        (r"一起", 1, "zh"),
        (r"对应.*表", 2, "zh"),
        (r"跨表", 2, "zh"),
        (r"多表", 2, "zh"),
        # Chinese — implicit multi-table patterns
        # "每X的Y" with aggregation (strong signal for JOIN + GROUP BY)
        (r"每(?:个|位|名|人|条|项).{1,8}的.{1,8}(?:总|平均|汇总|统计|金额|价格|排名|列表|详情|记录)", 2, "zh"),
        # "每X的Y" (simple entity-relationship, lower weight — could be single-table GROUP BY)
        (r"每(?:个|种|类|位|条|项|名).{1,6}的.{1,6}", 1, "zh"),
        # "谁" queries (who bought/ordered/... — clear person→action→object JOIN)
        (r"谁.{0,8}(?:购买|下单|订购|消费|支付|浏览|评价|投诉|退货|取消)", 2, "zh"),
        # Explicit relationship keywords (strong JOIN signal)
        (r".{1,4}(?:属于|归属|拥有|持有).{1,6}", 2, "zh"),
        (r".{1,4}(?:包含|包括).{1,4}", 1, "zh"),
        (r"(?:哪个|哪些|什么).{1,6}(?:下|有|包含|拥有)", 2, "zh"),
        # "分别" with aggregation — per-group breakdown often needs JOIN
        (r"分别.{0,4}(?:统计|查询|列出|显示|汇总|计算|查看)", 2, "zh"),
        # English — implicit relationship queries
        (r"\b(?:each|every|per)\s+\w+\s+(?:has|have|with|in)\s+\w+\s+(?:total|sum|count|avg)", 2, "en"),
        (r"\bwho\s+(?:has|have|made|placed|ordered|bought|purchased)", 2, "en"),
        (r"\bwhich\s+\w+\s+(?:has|have|contains?|belongs?\s+to)", 2, "en"),
        # English
        (r"\bjoin\b", 3, "en"),
        (r"\btogether with\b", 2, "en"),
        (r"\bacross\b", 1, "en"),
        (r"\brelated\b", 1, "en"),
        (r"\bcombine\b", 2, "en"),
        (r"\bcorrelate\b", 2, "en"),
        (r"\bmultiple tables?\b", 2, "en"),
        # SQL keywords (case-insensitive — covered by en patterns above)
    ],
    IntentType.COMPARISON: [
        # Chinese
        (r"对比", 3, "zh"),
        (r"比较", 3, "zh"),
        (r"大于", 2, "zh"),
        (r"小于", 2, "zh"),
        (r"高于", 2, "zh"),
        (r"低于", 2, "zh"),
        (r"超过", 1, "zh"),
        (r"相比", 2, "zh"),
        (r"哪个.*更", 2, "zh"),
        (r"差异", 1, "zh"),
        (r"\bVS\b", 2, "zh"),
        # English
        (r"\bcompare\b", 3, "en"),
        (r"\bversus\b", 3, "en"),
        (r"\bvs\b", 2, "en"),
        (r"\bgreater than\b", 2, "en"),
        (r"\bless than\b", 2, "en"),
        (r"\bhigher than\b", 2, "en"),
        (r"\blower than\b", 2, "en"),
        (r"\bmore than\b", 1, "en"),
        (r"\bdifference\b", 1, "en"),
        (r"\bcomparison\b", 3, "en"),
        (r"\bratio\b", 1, "en"),
        # Symbols
        (r">", 1, "both"),
        (r"<", 1, "both"),
        (r">=", 1, "both"),
        (r"<=", 1, "both"),
    ],
    IntentType.TIME_SERIES: [
        # Chinese
        (r"趋势", 3, "zh"),
        (r"变化", 2, "zh"),
        (r"走势", 3, "zh"),
        (r"按月", 3, "zh"),
        (r"按天", 3, "zh"),
        (r"按周", 3, "zh"),
        (r"按季度", 3, "zh"),
        (r"按年", 3, "zh"),
        (r"同比", 3, "zh"),
        (r"环比", 3, "zh"),
        (r"增长", 1, "zh"),
        (r"下降", 1, "zh"),
        (r"时间序列", 3, "zh"),
        (r"每天", 2, "zh"),
        (r"每月", 2, "zh"),
        (r"每年", 2, "zh"),
        (r"逐[日周月季年]", 3, "zh"),
        # English
        (r"\btrends?\b", 3, "en"),
        (r"\bover time\b", 3, "en"),
        (r"\bmonthly\b", 3, "en"),
        (r"\bdaily\b", 3, "en"),
        (r"\bweekly\b", 3, "en"),
        (r"\bquarterly\b", 3, "en"),
        (r"\byearly\b", 3, "en"),
        (r"\bgrowth\b", 1, "en"),
        (r"\bdecline\b", 1, "en"),
        (r"\bchange over\b", 2, "en"),
        (r"\btime series\b", 3, "en"),
        (r"\bhistorical\b", 2, "en"),
        (r"\bday over day\b", 3, "en"),
        (r"\bmonth over month\b", 3, "en"),
        (r"\byear over year\b", 3, "en"),
        (r"\bYoY\b", 3, "en"),
        (r"\bMoM\b", 3, "en"),
        (r"\bDoD\b", 3, "en"),
    ],
    IntentType.FUNNEL: [
        # Chinese
        (r"转化", 3, "zh"),
        (r"漏斗", 3, "zh"),
        (r"转化率", 3, "zh"),
        (r"留存", 3, "zh"),
        (r"流失", 3, "zh"),
        (r"留存率", 3, "zh"),
        (r"流失率", 3, "zh"),
        (r"跳出", 2, "zh"),
        # English
        (r"\bfunnel\b", 3, "en"),
        (r"\bconversion\b", 3, "en"),
        (r"\bretention\b", 3, "en"),
        (r"\bchurn\b", 3, "en"),
        (r"\battrition\b", 3, "en"),
        (r"\bdrop[\s-]*off\b", 3, "en"),
        (r"\bbounce\b", 2, "en"),
        (r"\bopt[\s-]*in\b", 2, "en"),
        (r"\bopt[\s-]*out\b", 2, "en"),
    ],
}

# ── Tie-breaking priority: lower index = higher priority ────────────────────
_PRIORITY: list[IntentType] = [
    IntentType.FUNNEL,
    IntentType.TIME_SERIES,
    IntentType.AGGREGATE,
    IntentType.COMPARISON,
    IntentType.JOIN,
    IntentType.SELECT,
]


class IntentClassifier:
    """Classify NL query into one of 7 intent types.

    Uses regex + keyword weighted scoring. Language-aware: applies only
    patterns matching the detected language (zh/en/mixed → both).

    Usage::

        classifier = IntentClassifier()
        intent = classifier.classify("统计上个月各地区的销售额", "zh")
        # → IntentType.AGGREGATE
    """

    def __init__(self) -> None:
        # Pre-compile patterns for performance
        self._compiled: dict[IntentType, list[tuple[re.Pattern[str], int, str]]] = {}
        for intent, patterns in _PATTERNS.items():
            self._compiled[intent] = [
                (re.compile(p, re.IGNORECASE), w, lang) for p, w, lang in patterns
            ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, text: str, lang: str) -> IntentType:
        """Classify *text* into an intent type.

        Args:
            text: Raw NL input string.
            lang: Language code from LanguageDetector
                (``"zh"``, ``"en"``, or ``"mixed"``).

        Returns:
            The best-matching ``IntentType``, or ``IntentType.UNKNOWN``
            if no pattern matches.
        """
        scores: dict[IntentType, int] = {}

        for intent, patterns in self._compiled.items():
            score = 0
            for pattern, weight, pat_lang in patterns:
                if self._lang_match(pat_lang, lang) and pattern.search(text):
                    score += weight
            if score > 0:
                scores[intent] = score

        if not scores:
            return IntentType.UNKNOWN

        # Return the intent with the highest score.
        # On tie, use priority ordering (more specific intents first).
        best_intent = max(
            scores,
            key=lambda i: (scores[i], -_PRIORITY.index(i) if i in _PRIORITY else 0),
        )
        return best_intent

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lang_match(pat_lang: str, text_lang: str) -> bool:
        """Check whether *pat_lang* applies to *text_lang*."""
        if pat_lang == "both":
            return True
        if text_lang == "mixed":
            return True  # mixed input → apply all language patterns
        return pat_lang == text_lang
