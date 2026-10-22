"""Deterministic, source-bounded one-hop schema expansion shared by linking paths."""

from app.models.schema import SchemaSnapshot


def expand_foreign_keys(tables: list[str], schema: SchemaSnapshot) -> list[str]:
    """Keep valid seeds, then add only their direct forward/reverse neighbours.

    Newly found neighbours never become expansion seeds. Missing FK targets
    cannot manufacture tables absent from the current schema snapshot.
    """
    seeds = list(dict.fromkeys(name for name in tables if name in schema.tables))
    seed_set = set(seeds)
    neighbours = {
        fk.ref_table
        for name in seeds
        for fk in schema.tables[name].foreign_keys
        if fk.ref_table in schema.tables
    }
    for name, table in schema.tables.items():
        if any(fk.ref_table in seed_set for fk in table.foreign_keys):
            neighbours.add(name)
    return seeds + sorted(neighbours - seed_set)
