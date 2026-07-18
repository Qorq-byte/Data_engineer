"""Time expression parser with two-tier strategy.

See SPEC §4.7.1 (b) and implementation-plan §4.7.1 for design rationale.

L1: Regex rule matching — 35+ patterns across zh/en, fast/free, ~90% hit rate.
L2: LLM fallback — complex expressions are routed through a LiteLLMRouter
    (when supplied) via ``extract_with_fallback()``; the router prompt asks
    for a strict JSON time range which is parsed tolerantly (returns ``None``
    on any malformed output, never raises).
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import datetime, timedelta
from typing import Any

from app.models.query import TimeRange


def _add_months(dt: datetime, months: int) -> datetime:
    """Add *months* to *dt*, clamping the day to the last valid day of the target month.

    Examples:
        Jan 31 + 1 month → Feb 28/29
        Mar 31 - 1 month → Feb 28/29
    """
    new_month = dt.month + months
    year = dt.year + (new_month - 1) // 12
    month = (new_month - 1) % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def _start_of_day(dt: datetime) -> datetime:
    """Return *dt* with time set to midnight (00:00:00.000000)."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(dt: datetime) -> datetime:
    """Return *dt* with time set to 23:59:59.999999."""
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def _start_of_week(dt: datetime) -> datetime:
    """Return the Monday (ISO week start) on or before *dt*, at midnight."""
    return _start_of_day(dt - timedelta(days=dt.weekday()))


def _start_of_quarter(dt: datetime) -> datetime:
    """Return the first day of the quarter containing *dt*."""
    month = (dt.month - 1) // 3 * 3 + 1
    return dt.replace(month=month, day=1)


def _response_content(response: Any) -> str:
    """Extract the assistant message text from a router response.

    Handles both the dict shape returned by ``LiteLLMRouter`` mock mode and
    litellm response objects. Returns ``""`` on any unexpected shape.
    """
    try:
        if isinstance(response, dict):
            return str(response["choices"][0]["message"]["content"])
        return str(response.choices[0].message.content)
    except (KeyError, IndexError, AttributeError, TypeError):
        return ""


# ── Chinese time patterns ────────────────────────────────────────────────

_ZH_PATTERNS: list[tuple[str, str | None, object]] = [
    # ── relative days ──
    (r"前天", None, "day_before_yesterday"),
    (r"昨天", None, "yesterday"),
    (r"今天", None, "today"),
    (r"后天", None, "day_after_tomorrow"),
    (r"明天", None, "tomorrow"),
    # ── relative weeks ──
    (r"本周", None, "this_week"),
    (r"上周", None, "last_week"),
    (r"下周", None, "next_week"),
    # ── relative months ──
    (r"上个月", None, "last_month"),
    (r"本月|这个月", None, "this_month"),
    (r"下个月", None, "next_month"),
    # ── relative quarters ──
    (r"本季度|本季", None, "this_quarter"),
    (r"上季度|上季", None, "last_quarter"),
    (r"下季度|下季", None, "next_quarter"),
    # ── relative years ──
    (r"去年同期", None, "same_period_last_year"),
    (r"去年", None, "last_year"),
    (r"今年|本年度?", None, "this_year"),
    (r"明年", None, "next_year"),
    # ── year-to-date ──
    (r"年初至今|本年至今|今年以来", None, "ytd"),
    # ── last N units ──
    (r"最近(\d+)天", "n", "last_n_days"),
    (r"过去(\d+)天", "n", "last_n_days"),
    (r"近(\d+)天", "n", "last_n_days"),
    (r"最近(\d+)周", "n", "last_n_weeks"),
    (r"过去(\d+)周", "n", "last_n_weeks"),
    (r"最近(\d+)个?月", "n", "last_n_months"),
    (r"过去(\d+)个?月", "n", "last_n_months"),
    (r"最近(\d+)年", "n", "last_n_years"),
    (r"过去(\d+)年", "n", "last_n_years"),
    # ── N units ago ──
    (r"(\d+)天前", "n", "n_days_ago"),
    (r"(\d+)周前", "n", "n_weeks_ago"),
    (r"(\d+)个?月前", "n", "n_months_ago"),
    (r"(\d+)年前", "n", "n_years_ago"),
    # ── next N units ──
    (r"未来(\d+)天", "n", "next_n_days"),
    (r"未来(\d+)周", "n", "next_n_weeks"),
    (r"未来(\d+)个?月", "n", "next_n_months"),
    # ── absolute dates ──
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日?", "ymd", "absolute_ymd"),
    (r"(\d{4})年(\d{1,2})月", "ym", "absolute_ym"),
    # ── quarter references ──
    (r"Q([1-4])(?:季度)?", "q", "absolute_quarter"),
    (r"第([一二三四1-4])季度", "q_cn", "absolute_quarter_cn"),
]


# ── English time patterns ────────────────────────────────────────────────

_EN_PATTERNS: list[tuple[str, str | None, object]] = [
    # ── relative days ── (longer patterns first to avoid partial match)
    (r"day before yesterday", None, "day_before_yesterday"),
    (r"yesterday", None, "yesterday"),
    (r"today", None, "today"),
    (r"tomorrow", None, "tomorrow"),
    # ── relative weeks ──
    (r"this week", None, "this_week"),
    (r"last week", None, "last_week"),
    (r"next week", None, "next_week"),
    # ── relative months ──
    (r"last month", None, "last_month"),
    (r"this month", None, "this_month"),
    (r"next month", None, "next_month"),
    # ── relative quarters ──
    (r"this quarter", None, "this_quarter"),
    (r"last quarter", None, "last_quarter"),
    (r"next quarter", None, "next_quarter"),
    # ── relative years ──
    (r"same period last year", None, "same_period_last_year"),
    (r"last year", None, "last_year"),
    (r"this year", None, "this_year"),
    (r"next year", None, "next_year"),
    (r"year\s*to\s*date|ytd", None, "ytd"),
    (r"quarter\s*to\s*date|qtd", None, "qtd"),
    (r"month\s*to\s*date|mtd", None, "mtd"),
    # ── last N units ──
    (r"last (\d+) days?", "n", "last_n_days"),
    (r"past (\d+) days?", "n", "last_n_days"),
    (r"last (\d+) weeks?", "n", "last_n_weeks"),
    (r"past (\d+) weeks?", "n", "last_n_weeks"),
    (r"last (\d+) months?", "n", "last_n_months"),
    (r"past (\d+) months?", "n", "last_n_months"),
    (r"last (\d+) years?", "n", "last_n_years"),
    (r"past (\d+) years?", "n", "last_n_years"),
    # ── N units ago ──
    (r"(\d+) days? ago", "n", "n_days_ago"),
    (r"(\d+) weeks? ago", "n", "n_weeks_ago"),
    (r"(\d+) months? ago", "n", "n_months_ago"),
    (r"(\d+) years? ago", "n", "n_years_ago"),
    # ── next N units ──
    (r"next (\d+) days?", "n", "next_n_days"),
    (r"next (\d+) weeks?", "n", "next_n_weeks"),
    (r"next (\d+) months?", "n", "next_n_months"),
]


# ── L2 fallback signal words ─────────────────────────────────────────────
# If the text contains any of these but L1 missed, the expression is likely a
# complex time reference worth sending to the LLM (e.g. "上上个季度",
# "the week before last", "春节期间").

_TIME_SIGNAL_ZH: tuple[str, ...] = (
    "季度",
    "周",
    "月",
    "年",
    "天",
    "日",
    "时间",
    "期间",
    "前",
    "后",
    "以来",
    "至今",
    "假期",
    "春节",
)

_TIME_SIGNAL_EN: re.Pattern[str] = re.compile(
    r"\b(quarters?|weeks?|months?|years?|days?|dates?|time|periods?"
    r"|ago|last|next|past|recent(?:ly)?|since|until|between|before|after"
    r"|q[1-4]|ytd|qtd|mtd|holidays?)\b",
    re.IGNORECASE,
)


class TimeParser:
    """Extract time-range expressions from NL text.

    Two-tier strategy:
        L1 — regex rule matching (fast, free, ~90% hit rate).
        L2 — LLM fallback for complex expressions, used only when a router is
             supplied and only via the async ``extract_with_fallback()`` API.

    Usage::

        parser = TimeParser()
        tr = parser.extract("查询上个月华东地区的订单", "zh")
        # → TimeRange(start=2026-06-01, end=2026-07-01, unit="month", ...)

        parser = TimeParser(router=LiteLLMRouter(config))
        tr = await parser.extract_with_fallback("上上个季度的销售额", "zh")
    """

    def __init__(self, router: Any | None = None) -> None:
        """Create a parser.

        Args:
            router: Optional ``LiteLLMRouter`` instance for the L2 LLM
                fallback. Typed as ``Any`` to avoid a hard dependency on
                ``app.llm``; only ``router.complete()`` is used. When ``None``,
                ``extract_with_fallback()`` behaves exactly like ``extract()``.
        """
        self._router = router
        self._zh_compiled: list[tuple[re.Pattern[str], str | None, object]] = [
            (re.compile(pattern, re.IGNORECASE), group, resolver)
            for pattern, group, resolver in _ZH_PATTERNS
        ]
        self._en_compiled: list[tuple[re.Pattern[str], str | None, object]] = [
            (re.compile(pattern, re.IGNORECASE), group, resolver)
            for pattern, group, resolver in _EN_PATTERNS
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self, text: str, lang: str, reference_time: datetime | None = None
    ) -> TimeRange | None:
        """Extract a time range from *text*.

        Args:
            text: Raw NL input string.
            lang: Language code from LanguageDetector (``"zh"``, ``"en"``, ``"mixed"``).
            reference_time: Reference point for relative expressions
                (defaults to ``datetime.now()``). Useful for testing.

        Returns:
            A ``TimeRange`` if a time expression is found, ``None`` otherwise.
        """
        if reference_time is None:
            reference_time = datetime.now()

        # For mixed-language input, try both pattern sets (zh first)
        candidates: list[tuple[re.Pattern[str], str | None, object]] = []
        if lang in ("zh", "mixed"):
            candidates.extend(self._zh_compiled)
        if lang in ("en", "mixed"):
            candidates.extend(self._en_compiled)
        if not candidates:
            candidates = self._zh_compiled + self._en_compiled

        for pattern, group, resolver in candidates:
            m = pattern.search(text)
            if m:
                return self._resolve(pattern.pattern, group, resolver, m, reference_time)

        return None  # L1 miss — use extract_with_fallback() for the L2 LLM tier

    async def extract_with_fallback(
        self, text: str, lang: str, now: datetime | None = None
    ) -> TimeRange | None:
        """Extract a time range with two-tier strategy: L1 rules, then L2 LLM.

        L1 (``extract()``) is always tried first; on a hit its result is
        returned unchanged. On an L1 miss the LLM fallback runs only when a
        router was supplied at construction time *and* the text contains
        time-signal words (:meth:`_needs_llm_fallback`).

        Args:
            text: Raw NL input string.
            lang: Language code (``"zh"``, ``"en"``, ``"mixed"``).
            now: Reference point for relative expressions
                (defaults to ``datetime.now()``).

        Returns:
            A ``TimeRange`` if either tier succeeds, ``None`` otherwise.
            Never raises on LLM/parse failures.
        """
        if now is None:
            now = datetime.now()

        result = self.extract(text, lang, reference_time=now)
        if result is not None:
            return result

        if self._router is None or not self._needs_llm_fallback(text):
            return None

        return await self._llm_extract(text, now)

    # ------------------------------------------------------------------
    # L2 — LLM fallback
    # ------------------------------------------------------------------

    def _needs_llm_fallback(self, text: str) -> bool:
        """Return True if *text* contains time-signal words (worth an LLM call).

        Called only after an L1 miss: a signal word present but unmatched by
        the regex tier suggests a complex time expression.
        """
        if any(signal in text for signal in _TIME_SIGNAL_ZH):
            return True
        return _TIME_SIGNAL_EN.search(text) is not None

    async def _llm_extract(self, text: str, now: datetime) -> TimeRange | None:
        """Ask the LLM router to resolve *text* into a JSON time range.

        Tolerant by design: any router error, malformed JSON, missing field,
        or bad date format yields ``None`` instead of raising.
        """
        if self._router is None:
            return None

        prompt = (
            "You are a time expression parser. Extract the time range referenced "
            f"in the text below. Today is {now:%Y-%m-%d}.\n"
            f"Text: {text}\n"
            "Respond with ONLY a JSON object of the form "
            '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", '
            '"granularity": "day|week|month|quarter|year"}. '
            'If the text contains no time reference, respond with {"start": null, '
            '"end": null, "granularity": null}.'
        )
        try:
            response = await self._router.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
        except Exception:
            return None

        content = _response_content(response)
        if not content:
            return None

        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match is None:
            return None
        try:
            payload = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        start_raw = payload.get("start")
        end_raw = payload.get("end")
        if not isinstance(start_raw, str) or not isinstance(end_raw, str):
            return None
        try:
            start = datetime.strptime(start_raw, "%Y-%m-%d")
            end = datetime.strptime(end_raw, "%Y-%m-%d")
        except ValueError:
            return None

        granularity = payload.get("granularity")
        unit = granularity if isinstance(granularity, str) and granularity else "day"

        return TimeRange(
            start=_start_of_day(start),
            end=_end_of_day(end),
            unit=unit,
            raw_expression=text,
        )

    # ------------------------------------------------------------------
    # L1 resolver dispatch
    # ------------------------------------------------------------------

    def _resolve(
        self,
        pattern: str,
        group: str | None,
        resolver: object,
        match: re.Match[str],
        now: datetime,
    ) -> TimeRange:
        """Dispatch a regex match to the correct resolver."""
        raw = match.group(0)
        n: int | None = None
        n_y: int | None = None
        n_mo: int | None = None
        n_d: int | None = None
        if group is not None and match.lastindex and match.lastindex >= 1:
            if group in ("n", "q"):
                n = int(match.group(1))
            if group in ("ymd", "ym"):
                n_y = int(match.group(1))
                n_mo = int(match.group(2))
            if group in ("ymd",):
                n_d = int(match.group(3))

        if resolver == "today":
            return TimeRange(
                start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                end=now.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "yesterday":
            d = now - timedelta(days=1)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=d.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "day_before_yesterday":
            d = now - timedelta(days=2)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=d.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "tomorrow":
            d = now + timedelta(days=1)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=d.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "day_after_tomorrow":
            d = now + timedelta(days=2)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=d.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )

        # ── weeks ──
        elif resolver == "this_week":
            start = _start_of_week(now)
            return TimeRange(start=start, end=now, unit="week", raw_expression=raw)
        elif resolver == "last_week":
            end = _end_of_day(_start_of_week(now) - timedelta(days=1))
            start = _start_of_day(end - timedelta(days=6))
            return TimeRange(start=start, end=end, unit="week", raw_expression=raw)
        elif resolver == "next_week":
            start = _start_of_day(_start_of_week(now) + timedelta(days=7))
            end = _end_of_day(start + timedelta(days=6))
            return TimeRange(start=start, end=end, unit="week", raw_expression=raw)

        # ── months ──
        elif resolver == "this_month":
            start = _start_of_day(now.replace(day=1))
            return TimeRange(start=start, end=now, unit="month", raw_expression=raw)
        elif resolver == "last_month":
            end = _end_of_day(now.replace(day=1) - timedelta(days=1))
            start = _start_of_day(end.replace(day=1))
            return TimeRange(start=start, end=end, unit="month", raw_expression=raw)
        elif resolver == "next_month":
            start = _start_of_day(_add_months(now.replace(day=1), 1))
            end = _end_of_day(_add_months(start, 1) - timedelta(days=1))
            return TimeRange(start=start, end=end, unit="month", raw_expression=raw)

        # ── quarters ──
        elif resolver == "this_quarter":
            start = _start_of_day(_start_of_quarter(now))
            return TimeRange(start=start, end=now, unit="quarter", raw_expression=raw)
        elif resolver == "last_quarter":
            end = _end_of_day(_start_of_quarter(now) - timedelta(days=1))
            start = _start_of_day(_start_of_quarter(end))
            return TimeRange(start=start, end=end, unit="quarter", raw_expression=raw)
        elif resolver == "next_quarter":
            start = _start_of_day(_add_months(_start_of_quarter(now), 3))
            end = _end_of_day(_add_months(start, 3) - timedelta(days=1))
            return TimeRange(start=start, end=end, unit="quarter", raw_expression=raw)

        # ── years ──
        elif resolver == "this_year":
            start = _start_of_day(now.replace(month=1, day=1))
            return TimeRange(start=start, end=now, unit="year", raw_expression=raw)
        elif resolver == "last_year":
            end = _end_of_day(now.replace(month=1, day=1) - timedelta(days=1))
            start = _start_of_day(end.replace(month=1, day=1))
            return TimeRange(start=start, end=end, unit="year", raw_expression=raw)
        elif resolver == "next_year":
            start = _start_of_day(now.replace(year=now.year + 1, month=1, day=1))
            end = _end_of_day(
                start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
            )
            return TimeRange(start=start, end=end, unit="year", raw_expression=raw)
        elif resolver == "same_period_last_year":
            start = now.replace(year=now.year - 1)
            return TimeRange(start=start, end=now, unit="year", raw_expression=raw)

        # ── to-date ──
        elif resolver == "ytd":
            start = _start_of_day(now.replace(month=1, day=1))
            return TimeRange(start=start, end=now, unit="year", raw_expression=raw)
        elif resolver == "qtd":
            start = _start_of_day(_start_of_quarter(now))
            return TimeRange(start=start, end=now, unit="quarter", raw_expression=raw)
        elif resolver == "mtd":
            start = _start_of_day(now.replace(day=1))
            return TimeRange(start=start, end=now, unit="month", raw_expression=raw)

        # ── last N units ──
        elif resolver == "last_n_days":
            assert n is not None
            start = now - timedelta(days=n)
            return TimeRange(start=start, end=now, unit="day", raw_expression=raw)
        elif resolver == "last_n_weeks":
            assert n is not None
            start = now - timedelta(weeks=n)
            return TimeRange(start=start, end=now, unit="week", raw_expression=raw)
        elif resolver == "last_n_months":
            assert n is not None
            start = _add_months(now, -n)
            return TimeRange(start=start, end=now, unit="month", raw_expression=raw)
        elif resolver == "last_n_years":
            assert n is not None
            start = now.replace(year=now.year - n)
            return TimeRange(start=start, end=now, unit="year", raw_expression=raw)

        # ── N units ago ──
        elif resolver == "n_days_ago":
            assert n is not None
            d = now - timedelta(days=n)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=d.replace(hour=23, minute=59, second=59, microsecond=999999),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "n_weeks_ago":
            assert n is not None
            d = now - timedelta(weeks=n)
            return TimeRange(
                start=d.replace(hour=0, minute=0, second=0, microsecond=0),
                end=(d + timedelta(days=6)).replace(
                    hour=23, minute=59, second=59, microsecond=999999
                ),
                unit="week",
                raw_expression=raw,
            )
        elif resolver == "n_months_ago":
            assert n is not None
            d = _start_of_day(_add_months(now.replace(day=1), -n))
            end = _end_of_day(_add_months(d, 1) - timedelta(days=1))
            return TimeRange(start=d, end=end, unit="month", raw_expression=raw)
        elif resolver == "n_years_ago":
            assert n is not None
            start = _start_of_day(now.replace(year=now.year - n, month=1, day=1))
            end = _end_of_day(start.replace(month=12, day=31))
            return TimeRange(start=start, end=end, unit="year", raw_expression=raw)

        # ── next N units ──
        elif resolver == "next_n_days":
            assert n is not None
            start = now
            end = now + timedelta(days=n)
            return TimeRange(start=start, end=end, unit="day", raw_expression=raw)
        elif resolver == "next_n_weeks":
            assert n is not None
            start = now
            end = now + timedelta(weeks=n)
            return TimeRange(start=start, end=end, unit="week", raw_expression=raw)
        elif resolver == "next_n_months":
            assert n is not None
            start = now
            end = _add_months(now, n)
            return TimeRange(start=start, end=end, unit="month", raw_expression=raw)

        # ── absolute dates ──
        elif resolver == "absolute_ymd":
            assert n_y is not None and n_mo is not None and n_d is not None
            d = _start_of_day(now.replace(year=n_y, month=n_mo, day=n_d))
            return TimeRange(
                start=d,
                end=_end_of_day(d),
                unit="day",
                raw_expression=raw,
            )
        elif resolver == "absolute_ym":
            assert n_y is not None and n_mo is not None
            start = _start_of_day(now.replace(year=n_y, month=n_mo, day=1))
            end = _end_of_day(_add_months(start, 1) - timedelta(days=1))
            return TimeRange(start=start, end=end, unit="month", raw_expression=raw)

        # ── absolute quarter ──
        elif resolver == "absolute_quarter":
            assert n is not None
            start = _start_of_day(now.replace(month=(n - 1) * 3 + 1, day=1))
            end = _end_of_day(_add_months(start, 3) - timedelta(days=1))
            return TimeRange(start=start, end=end, unit="quarter", raw_expression=raw)
        elif resolver == "absolute_quarter_cn":
            q_map = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
            q_str = match.group(1)
            q = q_map.get(q_str, 1)
            start = _start_of_day(now.replace(month=(q - 1) * 3 + 1, day=1))
            end = _end_of_day(_add_months(start, 3) - timedelta(days=1))
            return TimeRange(start=start, end=end, unit="quarter", raw_expression=raw)

        # fallback (shouldn't reach here)
        return TimeRange(raw_expression=raw)
