"""Unit tests for LanguageDetector."""

import pytest

from app.core.language_detector import LanguageDetector


@pytest.fixture
def detector():
    return LanguageDetector()


class TestCJKDetection:
    """CJK character detection."""

    def test_single_cjk_unified(self, detector):
        assert detector._is_cjk("中") is True  # U+4E2D

    def test_single_cjk_ext_a(self, detector):
        assert detector._is_cjk("㐀") is True  # U+3400

    def test_single_ascii_letter(self, detector):
        assert detector._is_cjk("a") is False

    def test_single_digit(self, detector):
        assert detector._is_cjk("5") is False


class TestLanguageDetect:
    """Language detection by CJK ratio."""

    # ── Pure Chinese ──
    def test_pure_chinese_simple(self, detector):
        assert detector.detect("查询所有订单") == "zh"

    def test_pure_chinese_long(self, detector):
        assert detector.detect("请帮我查一下上个月华东地区的营业收入是多少") == "zh"

    def test_chinese_with_punctuation(self, detector):
        assert detector.detect("查询订单（包含退款）——按月统计") == "zh"

    def test_chinese_with_numbers(self, detector):
        assert detector.detect("2024年销售额前10名") == "zh"

    # ── Pure English ──
    def test_pure_english_simple(self, detector):
        assert detector.detect("find all orders") == "en"

    def test_pure_english_long(self, detector):
        assert detector.detect(
            "Show me the total revenue by region for last month"
        ) == "en"

    # ── Mixed ──
    def test_mixed_cjk_dominant(self, detector):
        # CJK: 6 chars (查询所有订单), Alpha: 5 chars (users) → ratio ≈ 55% → "zh"
        text = "查询所有users订单"
        result = detector.detect(text)
        assert result == "zh"

    def test_mixed_balanced(self, detector):
        text = "查询 user 的 order 数量"
        result = detector.detect(text)
        # CJK: 查询, 的, 数量 = 4 chars; alpha: user, order = 8 chars
        # ratio = 4/12 ≈ 33% → "mixed"
        assert result == "mixed"

    def test_mixed_english_dominant(self, detector):
        text = "find 订单 and 用户 data"
        result = detector.detect(text)
        # CJK: 订单, 用户 = 4 chars; alpha: find, and, data = 11 chars
        # ratio = 4/15 ≈ 27% → "mixed"
        assert result == "mixed"

    # ── Edge cases ──
    def test_empty_string(self, detector):
        assert detector.detect("") == "en"

    def test_whitespace_only(self, detector):
        assert detector.detect("   \t\n  ") == "en"

    def test_numeric_only(self, detector):
        assert detector.detect("12345 67890") == "en"

    def test_punctuation_only(self, detector):
        assert detector.detect("!@#$%^&*()") == "en"

    def test_single_chinese_char(self, detector):
        assert detector.detect("我") == "zh"

    def test_single_english_word_with_chinese_char(self, detector):
        text = "hello 世界"
        # CJK: 2; alpha: 5; ratio = 2/7 ≈ 29% → "mixed"
        assert detector.detect(text) == "mixed"

    # ── SQL-like input ──
    def test_sql_keywords(self, detector):
        assert detector.detect("SELECT * FROM orders WHERE status = 'active'") == "en"

    def test_chinese_with_sql(self, detector):
        text = "SELECT * FROM orders WHERE 状态 = 'active'"
        result = detector.detect(text)
        assert result in ("en", "mixed")
