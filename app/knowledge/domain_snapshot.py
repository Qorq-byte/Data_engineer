"""Convert persisted domain snapshots to the shared query and API models."""

from typing import Any

from app.models.domain import BusinessRule, DomainConfig, GlossaryTerm, TermMapping


def _glossary_term(row: dict[str, Any]) -> GlossaryTerm:
    expression = row["expression"]
    return GlossaryTerm(
        term=row["term"], term_en=row["term_en"] or "", description=row["description"] or "",
        mapping=TermMapping(
            expression=expression, type=row["mapping_type"] or "derived_column",
            table=row["mapping_table"], condition=row["mapping_condition"],
            precision=row["mapping_precision"],
        ) if expression else None,
        tags=row["tags"] or [],
    )


def domain_from_snapshot(row: dict[str, Any]) -> DomainConfig:
    name = row["name"]
    glossary = sorted(row["glossary"] or [], key=lambda term: term["term"])
    rules = sorted(row["rules"] or [], key=lambda rule: rule["id"])
    return DomainConfig(
        name=name,
        label={"zh": row["label_zh"] or "", "en": row["label_en"] or ""},
        description={"zh": row["description_zh"] or "", "en": row["description_en"] or ""},
        keywords=row["keywords"] or [],
        databases=row["db_configs"] or [],
        timezone=row["timezone"] or "UTC",
        currency=row["currency"] or "USD",
        glossary=[_glossary_term(term) for term in glossary],
        rules=[
            BusinessRule(
                id=rule["id"], description=rule["description"] or "", pattern=rule["pattern"] or "",
                enforce=rule["enforce"] or [], sql_template=rule["sql_template"] or "",
                domain_id=name,
            )
            for rule in rules
        ],
    )
