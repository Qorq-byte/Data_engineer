"""Unit tests for TimeParser — zh/en time expression extraction."""

from datetime import datetime

import pytest

from app.core.time_parser import TimeParser, _add_months, _start_of_quarter, _start_of_week

# Fixed reference time for deterministic tests: 2026-07-15 14:30:00 (Wednesday)
REF_TIME = datetime(2026, 7, 15, 14, 30, 0)


@pytest.fixture
def parser():
    return TimeParser()


# ═══════════════════════════════════════════════════════════════════════════
# Helper unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAddMonths:
    def test_add_positive(self):
        result = _add_months(datetime(2026, 1, 15), 3)
        assert result == datetime(2026, 4, 15)

    def test_subtract(self):
        result = _add_months(datetime(2026, 7, 15), -2)
        assert result == datetime(2026, 5, 15)

    def test_year_boundary_forward(self):
        result = _add_months(datetime(2026, 11, 15), 3)
        assert result == datetime(2027, 2, 15)

    def test_year_boundary_backward(self):
        result = _add_months(datetime(2026, 2, 15), -3)
        assert result == datetime(2025, 11, 15)

    def test_day_clamp_jan31_feb(self):
        """Jan 31 + 1 month → Feb 28 (2026 is not a leap year)."""
        result = _add_months(datetime(2026, 1, 31), 1)
        assert result == datetime(2026, 2, 28)

    def test_day_clamp_mar31_feb(self):
        """Mar 31 - 1 month → Feb 28."""
        result = _add_months(datetime(2026, 3, 31), -1)
        assert result == datetime(2026, 2, 28)

    def test_leap_year_feb29(self):
        """Feb 29, 2028 (leap year) + 12 months → Feb 28, 2029."""
        result = _add_months(datetime(2028, 2, 29), 12)
        assert result == datetime(2029, 2, 28)

    def test_day_31_to_30_day_month(self):
        """May 31 - 1 month → Apr 30."""
        result = _add_months(datetime(2026, 5, 31), -1)
        assert result == datetime(2026, 4, 30)


class TestStartOfWeek:
    def test_wednesday(self):
        """2026-07-15 is a Wednesday → Monday is 2026-07-13."""
        result = _start_of_week(datetime(2026, 7, 15))
        assert result == datetime(2026, 7, 13)

    def test_monday(self):
        result = _start_of_week(datetime(2026, 7, 13))
        assert result == datetime(2026, 7, 13)

    def test_sunday(self):
        """Sunday → Monday of same week."""
        result = _start_of_week(datetime(2026, 7, 19))
        assert result == datetime(2026, 7, 13)


class TestStartOfQuarter:
    def test_q1(self):
        assert _start_of_quarter(datetime(2026, 2, 10)) == datetime(2026, 1, 1)

    def test_q2(self):
        assert _start_of_quarter(datetime(2026, 5, 20)) == datetime(2026, 4, 1)

    def test_q3(self):
        assert _start_of_quarter(datetime(2026, 7, 15)) == datetime(2026, 7, 1)

    def test_q4(self):
        assert _start_of_quarter(datetime(2026, 11, 1)) == datetime(2026, 10, 1)


# ═══════════════════════════════════════════════════════════════════════════
# Chinese patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseRelativeDays:
    def test_today(self, parser):
        result = parser.extract("查询今天的订单", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 15, 0, 0, 0)
        assert result.end == datetime(2026, 7, 15, 23, 59, 59, 999999)

    def test_yesterday(self, parser):
        result = parser.extract("昨天销售额", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 14, 0, 0, 0)
        assert result.end == datetime(2026, 7, 14, 23, 59, 59, 999999)

    def test_day_before_yesterday(self, parser):
        result = parser.extract("前天的数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 13, 0, 0, 0)

    def test_tomorrow(self, parser):
        result = parser.extract("明天的计划", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 7, 16, 0, 0, 0)

    def test_day_after_tomorrow(self, parser):
        result = parser.extract("后天的安排", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 7, 17, 0, 0, 0)


class TestChineseRelativeWeeks:
    def test_this_week(self, parser):
        result = parser.extract("本周销售情况", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 13)  # Monday
        assert result.end == REF_TIME

    def test_last_week(self, parser):
        result = parser.extract("上周订单数", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 6)
        assert result.end == datetime(2026, 7, 12, 23, 59, 59, 999999)

    def test_next_week(self, parser):
        result = parser.extract("下周计划", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 20)
        assert result.end == datetime(2026, 7, 26, 23, 59, 59, 999999)


class TestChineseRelativeMonths:
    def test_last_month(self, parser):
        result = parser.extract("上个月的收入", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 6, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_this_month(self, parser):
        result = parser.extract("本月销售额", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 7, 1)
        assert result.end == REF_TIME

    def test_this_month_alt(self, parser):
        """这个月 as alternative to 本月."""
        result = parser.extract("这个月的业绩", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 7, 1)

    def test_next_month(self, parser):
        result = parser.extract("下个月的目标", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 8, 1)
        assert result.end == datetime(2026, 8, 31, 23, 59, 59, 999999)


class TestChineseRelativeQuarters:
    def test_this_quarter(self, parser):
        result = parser.extract("本季度营收", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 7, 1)
        assert result.end == REF_TIME

    def test_this_quarter_alt(self, parser):
        """本季 as short form."""
        result = parser.extract("本季营收", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"

    def test_last_quarter(self, parser):
        result = parser.extract("上季度数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 4, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_last_quarter_alt_short(self, parser):
        result = parser.extract("上季数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"

    def test_next_quarter(self, parser):
        result = parser.extract("下季度预测", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 10, 1)
        assert result.end == datetime(2026, 12, 31, 23, 59, 59, 999999)


class TestChineseRelativeYears:
    def test_last_year(self, parser):
        result = parser.extract("去年总收入", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2025, 1, 1)
        assert result.end == datetime(2025, 12, 31, 23, 59, 59, 999999)

    def test_this_year(self, parser):
        result = parser.extract("今年目标", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2026, 1, 1)
        assert result.end == REF_TIME

    def test_this_year_alt(self, parser):
        result = parser.extract("本年度预算", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"

    def test_next_year(self, parser):
        result = parser.extract("明年规划", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2027, 1, 1)

    def test_same_period_last_year(self, parser):
        result = parser.extract("去年同期对比", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2025, 7, 15, 14, 30, 0)


class TestChineseYearToDate:
    def test_year_to_date(self, parser):
        result = parser.extract("年初至今的销售额", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2026, 1, 1)
        assert result.end == REF_TIME

    def test_year_to_date_alt1(self, parser):
        result = parser.extract("本年至今统计", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 1, 1)

    def test_year_to_date_alt2(self, parser):
        result = parser.extract("今年以来汇总", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 1, 1)


class TestChineseLastNUnits:
    def test_last_7_days(self, parser):
        result = parser.extract("最近7天订单", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 8, 14, 30, 0)
        assert result.end == REF_TIME

    def test_last_30_days_guo_qu(self, parser):
        result = parser.extract("过去30天数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 6, 15, 14, 30, 0)

    def test_last_days_jin(self, parser):
        """近N天 pattern."""
        result = parser.extract("近15天活跃用户", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 6, 30, 14, 30, 0)

    def test_last_4_weeks(self, parser):
        result = parser.extract("最近4周趋势", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 6, 17, 14, 30, 0)

    def test_last_weeks_guo_qu(self, parser):
        result = parser.extract("过去2周数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"

    def test_last_3_months(self, parser):
        result = parser.extract("最近3个月销售", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 4, 15, 14, 30, 0)

    def test_last_3_months_no_ge(self, parser):
        """最近3月 without 个."""
        result = parser.extract("最近3月数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"

    def test_last_months_guo_qu(self, parser):
        result = parser.extract("过去6个月报表", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 1, 15, 14, 30, 0)

    def test_last_2_years(self, parser):
        result = parser.extract("最近2年财务数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2024, 7, 15, 14, 30, 0)

    def test_last_years_guo_qu(self, parser):
        result = parser.extract("过去5年趋势", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2021, 7, 15, 14, 30, 0)


class TestChineseNAgo:
    def test_3_days_ago(self, parser):
        result = parser.extract("3天前的记录", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 12, 0, 0, 0)
        assert result.end == datetime(2026, 7, 12, 23, 59, 59, 999999)

    def test_2_weeks_ago(self, parser):
        result = parser.extract("2周前的数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 1, 0, 0, 0)

    def test_1_month_ago(self, parser):
        result = parser.extract("1个月前统计", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 6, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_1_month_ago_no_ge(self, parser):
        result = parser.extract("1月前统计", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"

    def test_5_years_ago(self, parser):
        result = parser.extract("5年前数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2021, 1, 1)
        assert result.end == datetime(2021, 12, 31, 23, 59, 59, 999999)


class TestChineseNextNUnits:
    def test_next_7_days(self, parser):
        result = parser.extract("未来7天预测", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == REF_TIME
        assert result.end == datetime(2026, 7, 22, 14, 30, 0)

    def test_next_2_weeks(self, parser):
        result = parser.extract("未来2周安排", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == REF_TIME

    def test_next_3_months(self, parser):
        result = parser.extract("未来3个月计划", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == REF_TIME
        assert result.end == datetime(2026, 10, 15, 14, 30, 0)


class TestChineseAbsoluteDates:
    def test_full_date(self, parser):
        result = parser.extract("2024年3月15日的数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2024, 3, 15, 0, 0, 0)
        assert result.end == datetime(2024, 3, 15, 23, 59, 59, 999999)

    def test_full_date_no_ri(self, parser):
        """YYYY年MM月DD without 日."""
        result = parser.extract("2024年3月15数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "day"

    def test_year_month_only(self, parser):
        result = parser.extract("2024年6月的报表", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2024, 6, 1)
        assert result.end == datetime(2024, 6, 30, 23, 59, 59, 999999)


class TestChineseQuarterRefs:
    def test_q1(self, parser):
        result = parser.extract("Q1季度数据", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 1, 1)
        assert result.end == datetime(2026, 3, 31, 23, 59, 59, 999999)

    def test_q3(self, parser):
        result = parser.extract("Q3预测", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 7, 1)

    def test_di_san_quarter(self, parser):
        result = parser.extract("第三季度报表", "zh", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 7, 1)

    def test_di_yi_quarter(self, parser):
        result = parser.extract("第一季度总结", "zh", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 1, 1)


# ═══════════════════════════════════════════════════════════════════════════
# English patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishRelativeDays:
    def test_today(self, parser):
        result = parser.extract("orders from today", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 15, 0, 0, 0)

    def test_yesterday(self, parser):
        result = parser.extract("yesterday sales", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 14, 0, 0, 0)

    def test_day_before_yesterday(self, parser):
        result = parser.extract("day before yesterday data", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 13, 0, 0, 0)

    def test_tomorrow(self, parser):
        result = parser.extract("tomorrow forecast", "en", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 7, 16, 0, 0, 0)


class TestEnglishRelativeWeeks:
    def test_this_week(self, parser):
        result = parser.extract("this week revenue", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 13)

    def test_last_week(self, parser):
        result = parser.extract("last week orders", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 6)
        assert result.end == datetime(2026, 7, 12, 23, 59, 59, 999999)

    def test_next_week(self, parser):
        result = parser.extract("next week plan", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 7, 20)


class TestEnglishRelativeMonths:
    def test_last_month(self, parser):
        result = parser.extract("last month revenue", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 6, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_this_month(self, parser):
        result = parser.extract("this month targets", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 7, 1)

    def test_next_month(self, parser):
        result = parser.extract("next month goals", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 8, 1)
        assert result.end == datetime(2026, 8, 31, 23, 59, 59, 999999)


class TestEnglishRelativeQuarters:
    def test_this_quarter(self, parser):
        result = parser.extract("this quarter results", "en", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 7, 1)

    def test_last_quarter(self, parser):
        result = parser.extract("last quarter earnings", "en", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 4, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_next_quarter(self, parser):
        result = parser.extract("next quarter projection", "en", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 10, 1)


class TestEnglishRelativeYears:
    def test_last_year(self, parser):
        result = parser.extract("last year total", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2025, 1, 1)
        assert result.end == datetime(2025, 12, 31, 23, 59, 59, 999999)

    def test_this_year(self, parser):
        result = parser.extract("this year budget", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2026, 1, 1)

    def test_next_year(self, parser):
        result = parser.extract("next year forecast", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2027, 1, 1)


class TestEnglishToDate:
    def test_ytd(self, parser):
        result = parser.extract("YTD revenue", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2026, 1, 1)
        assert result.end == REF_TIME

    def test_year_to_date_full(self, parser):
        result = parser.extract("year to date summary", "en", REF_TIME)
        assert result is not None
        assert result.start == datetime(2026, 1, 1)

    def test_qtd(self, parser):
        result = parser.extract("QTD sales", "en", REF_TIME)
        assert result is not None
        assert result.unit == "quarter"
        assert result.start == datetime(2026, 7, 1)

    def test_mtd(self, parser):
        result = parser.extract("MTD orders", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 7, 1)


class TestEnglishLastNUnits:
    def test_last_7_days(self, parser):
        result = parser.extract("last 7 days sales", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 8, 14, 30, 0)

    def test_last_1_day(self, parser):
        """Singular 'day'."""
        result = parser.extract("last 1 day data", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"

    def test_past_30_days(self, parser):
        result = parser.extract("past 30 days trends", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 6, 15, 14, 30, 0)

    def test_last_4_weeks(self, parser):
        result = parser.extract("last 4 weeks activity", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"
        assert result.start == datetime(2026, 6, 17, 14, 30, 0)

    def test_past_2_weeks(self, parser):
        result = parser.extract("past 2 weeks", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"

    def test_last_3_months(self, parser):
        result = parser.extract("last 3 months revenue", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 4, 15, 14, 30, 0)

    def test_past_6_months(self, parser):
        result = parser.extract("past 6 months analysis", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"

    def test_last_2_years(self, parser):
        result = parser.extract("last 2 years financials", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2024, 7, 15, 14, 30, 0)

    def test_past_5_years(self, parser):
        result = parser.extract("past 5 years trends", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"


class TestEnglishNAgo:
    def test_3_days_ago(self, parser):
        result = parser.extract("3 days ago record", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == datetime(2026, 7, 12, 0, 0, 0)
        assert result.end == datetime(2026, 7, 12, 23, 59, 59, 999999)

    def test_2_weeks_ago(self, parser):
        result = parser.extract("2 weeks ago data", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"

    def test_1_month_ago(self, parser):
        result = parser.extract("1 month ago stats", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == datetime(2026, 6, 1)
        assert result.end == datetime(2026, 6, 30, 23, 59, 59, 999999)

    def test_3_years_ago(self, parser):
        result = parser.extract("3 years ago records", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2023, 1, 1)
        assert result.end == datetime(2023, 12, 31, 23, 59, 59, 999999)


class TestEnglishNextNUnits:
    def test_next_7_days(self, parser):
        result = parser.extract("next 7 days forecast", "en", REF_TIME)
        assert result is not None
        assert result.unit == "day"
        assert result.start == REF_TIME
        assert result.end == datetime(2026, 7, 22, 14, 30, 0)

    def test_next_2_weeks(self, parser):
        result = parser.extract("next 2 weeks plan", "en", REF_TIME)
        assert result is not None
        assert result.unit == "week"

    def test_next_3_months(self, parser):
        result = parser.extract("next 3 months roadmap", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.start == REF_TIME


class TestEnglishSamePeriod:
    def test_same_period_last_year(self, parser):
        result = parser.extract("same period last year comparison", "en", REF_TIME)
        assert result is not None
        assert result.unit == "year"
        assert result.start == datetime(2025, 7, 15, 14, 30, 0)


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_no_time_expression(self, parser):
        """Text with no time expression returns None."""
        result = parser.extract("查询华东地区的订单数量", "zh", REF_TIME)
        assert result is None

    def test_no_time_expression_en(self, parser):
        result = parser.extract("find orders by region", "en", REF_TIME)
        assert result is None

    def test_empty_string(self, parser):
        assert parser.extract("", "zh") is None

    def test_unknown_language_falls_back(self, parser):
        """Unknown language code tries both zh and en patterns."""
        result = parser.extract("上个月订单 last month", "fr", REF_TIME)
        assert result is not None  # matches either zh or en pattern

    def test_mixed_language_zh_first(self, parser):
        """Mixed language tries zh patterns first."""
        result = parser.extract("查询上个月 orders", "mixed", REF_TIME)
        assert result is not None
        assert result.unit == "month"
        assert result.raw_expression == "上个月"

    def test_first_match_wins_zh(self, parser):
        """When multiple time expressions exist, first match is returned."""
        result = parser.extract("昨天到今天的数据", "zh", REF_TIME)
        assert result is not None
        # "昨天" appears before "今天"
        assert "昨天" in result.raw_expression

    def test_first_match_wins_en(self, parser):
        """First match wins in English too."""
        result = parser.extract("yesterday and today sales", "en", REF_TIME)
        assert result is not None
        assert "yesterday" in result.raw_expression

    def test_default_reference_time(self, parser):
        """Without explicit reference_time, use datetime.now()."""
        result = parser.extract("今天", "zh")
        assert result is not None
        assert result.unit == "day"

    def test_raw_expression_preserved(self, parser):
        result = parser.extract("上个月", "zh", REF_TIME)
        assert result is not None
        assert result.raw_expression == "上个月"

    def test_case_insensitive_en(self, parser):
        result = parser.extract("LAST MONTH revenue", "en", REF_TIME)
        assert result is not None
        assert result.unit == "month"

    def test_ytd_variants(self, parser):
        """Various YTD spellings."""
        for text in ["YTD sales", "YtD revenue", "ytd report"]:
            result = parser.extract(text, "en", REF_TIME)
            assert result is not None, f"Failed for: {text}"
            assert result.start == datetime(2026, 1, 1)

    def test_numeric_in_text_not_matched_as_n(self, parser):
        """Numbers not part of a time pattern should not affect matching.

        "2024年销售额" has no month digit after 年, so no pattern matches
        (we don't have a year-only extractor yet). The '10' in 前10名 is
        correctly not treated as a time expression.
        """
        result = parser.extract("查询2024年销售额前10名", "zh", REF_TIME)
        # No time pattern matches — "2024年销售额" is not "2024年N月"
        # This is correct: "10" in 前10名 is not "10 days"
        assert result is None

    def test_timestamp_with_time_keywords_en(self, parser):
        """SQL-like input with time keywords in different context."""
        # "last" here is not a time expression pattern
        result = parser.extract(
            "SELECT * FROM orders WHERE created_at > last_updated", "en", REF_TIME
        )
        # "last_updated" doesn't match any pattern → None
        assert result is None

    def test_quarter_boundary_last_quarter(self, parser):
        """Last quarter when currently in Q1."""
        ref = datetime(2026, 2, 10)  # Q1
        result = parser.extract("上季度", "zh", ref)
        assert result is not None
        assert result.start == datetime(2025, 10, 1)
        assert result.end == datetime(2025, 12, 31, 23, 59, 59, 999999)

    def test_january_last_month(self, parser):
        """Last month in January → December of previous year."""
        ref = datetime(2026, 1, 15)
        result = parser.extract("上个月", "zh", ref)
        assert result is not None
        assert result.start == datetime(2025, 12, 1)
        assert result.end == datetime(2025, 12, 31, 23, 59, 59, 999999)

    def test_december_next_month(self, parser):
        """Next month in December → January of next year."""
        ref = datetime(2026, 12, 10)
        result = parser.extract("下个月", "zh", ref)
        assert result is not None
        assert result.start == datetime(2027, 1, 1)
