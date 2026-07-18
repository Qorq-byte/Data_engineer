"""Language detector based on Unicode range analysis.

See SPEC §4.1.3 and implementation-plan §4.7.1 for design rationale.

Uses CJK character ratio — no LLM call needed, O(n) single-pass, < 1ms.
"""

from app.models.query import Language


class LanguageDetector:
    """Detect input language by CJK Unicode range character ratio.

    Detection rules:
        - CJK ratio > 0.5  → "zh"    (predominantly Chinese)
        - CJK ratio > 0.1  → "mixed" (bilingual / code-switched)
        - CJK ratio ≤ 0.1  → "en"    (English or no CJK characters)

    CJK ranges covered (Unicode blocks commonly used in Chinese text):
        - CJK Unified Ideographs          U+4E00–U+9FFF
        - CJK Unified Ideographs Ext-A    U+3400–U+4DBF
        - CJK Compatibility Ideographs    U+F900–U+FAFF
    """

    CJK_RANGES: list[tuple[int, int]] = [
        (0x4E00, 0x9FFF),  # CJK Unified Ideographs
        (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
        (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    ]

    def _is_cjk(self, char: str) -> bool:
        """Check whether a single character falls within any of the CJK ranges."""
        cp = ord(char)
        return any(lo <= cp <= hi for lo, hi in self.CJK_RANGES)

    def detect(self, text: str) -> Language:
        """Return the detected language of *text*.

        Args:
            text: Raw NL input string (may be empty).

        Returns:
            ``"zh"``, ``"en"``, or ``"mixed"``.
        """
        if not text:
            return "en"

        cjk_count = 0
        alpha_count = 0

        for char in text:
            if self._is_cjk(char):
                cjk_count += 1
            elif char.isalpha():
                alpha_count += 1

        total = cjk_count + alpha_count
        if total == 0:
            return "en"  # purely numeric / punctuation → treat as English

        ratio = cjk_count / total

        if ratio > 0.5:
            return "zh"
        if ratio > 0.1:
            return "mixed"
        return "en"
