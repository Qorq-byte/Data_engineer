"""NL Parser — 5-step pipeline from natural language to SQR.

See SPEC §4.7.1 and implementation-plan §4.7.1 for the full design.

Pipeline:
    1. LanguageDetector  → language (zh / en / mixed)
    2. TimeParser        → TimeRange | None
    3. IntentClassifier  → IntentType
    4. AmbiguityDetector → list[Ambiguity]
    5. SQRBuilder        → SQR
"""

from __future__ import annotations

from app.core.ambiguity import AmbiguityDetector
from app.core.intent_classifier import IntentClassifier
from app.core.language_detector import LanguageDetector
from app.core.sqr_builder import SQRBuilder
from app.core.time_parser import TimeParser
from app.models.query import SQR


class NLParser:
    """Orchestrate the 5-step NL→SQR parsing pipeline.

    All five steps are deterministic rules — no LLM calls.
    The pipeline runs synchronously (``async`` signature is reserved
    for Phase 3 when LLM-based entity extraction may be added).

    Usage::

        parser = NLParser()
        sqr = await parser.parse("统计上个月大额订单按地区排名")

        # Create with custom components:
        parser = NLParser(
            language_detector=my_detector,
            time_parser=my_parser,
            intent_classifier=my_classifier,
            ambiguity_detector=my_detector,
            sqr_builder=my_builder,
        )
    """

    def __init__(
        self,
        language_detector: LanguageDetector | None = None,
        time_parser: TimeParser | None = None,
        intent_classifier: IntentClassifier | None = None,
        ambiguity_detector: AmbiguityDetector | None = None,
        sqr_builder: SQRBuilder | None = None,
    ) -> None:
        self.language_detector = language_detector or LanguageDetector()
        self.time_parser = time_parser or TimeParser()
        self.intent_classifier = intent_classifier or IntentClassifier()
        self.ambiguity_detector = ambiguity_detector or AmbiguityDetector()
        self.sqr_builder = sqr_builder or SQRBuilder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(self, nl_text: str, context: dict | None = None) -> SQR:
        """Run the full 5-step NL→SQR pipeline.

        Args:
            nl_text: Raw natural language input string.
            context: Optional context dict (reserved for Phase 3:
                    schema info, conversation history, domain config).

        Returns:
            A fully populated ``SQR`` instance.
        """
        # Step 1 — language detection
        lang = self.language_detector.detect(nl_text)

        # Step 2 — time expression extraction
        time_range = self.time_parser.extract(nl_text, lang)

        # Step 3 — intent classification
        intent = self.intent_classifier.classify(nl_text, lang)

        # Step 4 — ambiguity detection
        ambiguities = self.ambiguity_detector.detect(nl_text, intent, context)

        # Step 5 — assemble SQR
        sqr = self.sqr_builder.build(
            nl_text=nl_text,
            language=lang,
            intent=intent,
            time_range=time_range,
            ambiguities=ambiguities,
            context=context,
        )

        return sqr
