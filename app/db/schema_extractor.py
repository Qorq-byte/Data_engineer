"""Multi-dialect schema extractor.

Extracts TableSchema and SchemaSnapshot from live databases.
Phase 1: PostgreSQL, SQLite, DuckDB full implementation. Others: NotImplementedError.
"""

import logging
from datetime import datetime
from typing import Any

from app.db.connections import DatabaseType
from app.models.schema import (
    ColumnSchema,
    ForeignKey,
    IndexInfo,
    SchemaSnapshot,
    TableSchema,
)


class SchemaExtractor:
    """Extracts database schema via dialect-specific introspection."""

    @staticmethod
    async def extract(
        connection: Any, db_type: DatabaseType, db_name: str = ""
    ) -> SchemaSnapshot:
        """Extract full schema snapshot from a connected database.

        Args:
            connection: Native database connection (asyncpg, sqlite3, duckdb, etc.).
            db_type: Database type enum.
            db_name: Human-readable database name (defaults to db_type value).

        Returns:
            SchemaSnapshot with all tables and columns. PII columns are
            auto-flagged via :class:`~app.db.pii_detector.PiiDetector`
            (SPEC §9.2) so downstream prompt builders can mask them.
        """
        match db_type:
            case DatabaseType.POSTGRESQL:
                snapshot = await SchemaExtractor._extract_postgresql(
                    connection, db_name
                )
            case DatabaseType.SQLITE:
                snapshot = SchemaExtractor._extract_sqlite(connection, db_name)
            case DatabaseType.DUCKDB:
                snapshot = SchemaExtractor._extract_duckdb(connection, db_name)
            case DatabaseType.MYSQL:
                snapshot = await SchemaExtractor._extract_mysql(connection, db_name)
            case _:
                raise NotImplementedError(
                    f"Schema extraction for '{db_type}' is not yet implemented (Phase 2+)"
                )

        # Post-processing: auto-detect and flag PII columns (SPEC §9.2).
        # This is non-destructive — only sets ``is_pii`` / ``pii_type`` flags.
        try:
            from app.db.pii_detector import PiiDetector

            PiiDetector().annotate_snapshot(snapshot)
        except Exception:
            # PII detection must never break schema extraction
            pass

        return snapshot

    # ── PostgreSQL ──────────────────────────────────────────────

    @staticmethod
    async def _extract_postgresql(
        conn: Any, db_name: str
    ) -> SchemaSnapshot:
        """Extract schema from PostgreSQL using information_schema."""
        tables: dict[str, TableSchema] = {}

        # Get all user tables
        table_rows = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)

        for tr in table_rows:
            table_name = tr["table_name"]
            try:

                # Get columns
                col_rows = await conn.fetch("""
                    SELECT
                        column_name,
                        data_type,
                        is_nullable,
                        column_default,
                        col_description(
                            (SELECT c.oid FROM pg_class c
                             JOIN pg_namespace n ON n.oid = c.relnamespace
                             WHERE n.nspname = 'public' AND c.relname = $1), ordinal_position
                        ) as comment
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = $1
                    ORDER BY ordinal_position
                """, table_name)

                # Get primary keys
                pk_rows = await conn.fetch("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    WHERE tc.table_schema = 'public'
                        AND tc.table_name = $1
                        AND tc.constraint_type = 'PRIMARY KEY'
                """, table_name)
                pk_set = {r["column_name"] for r in pk_rows}

                # Get foreign keys
                fk_rows = await conn.fetch("""
                    SELECT
                        kcu.column_name,
                        ccu.table_name AS ref_table,
                        ccu.column_name AS ref_column,
                        tc.constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.table_schema = 'public'
                        AND tc.table_name = $1
                        AND tc.constraint_type = 'FOREIGN KEY'
                """, table_name)
                fk_set = {r["column_name"] for r in fk_rows}

                columns = []
                for cr in col_rows:
                    is_pk = cr["column_name"] in pk_set
                    is_fk = cr["column_name"] in fk_set
                    ref = None
                    if is_fk:
                        for fkr in fk_rows:
                            if fkr["column_name"] == cr["column_name"]:
                                ref = (fkr["ref_table"], fkr["ref_column"])
                                break

                    columns.append(
                        ColumnSchema(
                            name=cr["column_name"],
                            type=cr["data_type"],
                            nullable=(cr["is_nullable"] == "YES"),
                            is_primary_key=is_pk,
                            is_foreign_key=is_fk,
                            references=ref,
                            default=cr["column_default"],
                            comment=cr["comment"],
                        )
                    )

                # Get estimated row count
                row_count = 0
                try:
                    cnt_row = await conn.fetchrow(
                        'SELECT reltuples::bigint FROM pg_class WHERE relname = $1',
                        table_name,
                    )
                    if cnt_row:
                        row_count = cnt_row[0]
                except Exception:
                    pass

                # Get indexes
                idx_rows = await conn.fetch("""
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = $1
                """, table_name)
                indexes = []
                for ir in idx_rows:
                    is_unique = "UNIQUE" in (ir["indexdef"] or "").upper()
                    indexes.append(
                        IndexInfo(
                            name=ir["indexname"],
                            columns=[],  # Parsing indexdef is complex; stub for now
                            is_unique=is_unique,
                        )
                    )

                foreign_keys = []
                for fkr in fk_rows:
                    foreign_keys.append(
                        ForeignKey(
                            name=fkr["constraint_name"],
                            column=fkr["column_name"],
                            ref_table=fkr["ref_table"],
                            ref_column=fkr["ref_column"],
                        )
                    )

                tables[table_name] = TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=row_count,
                    indexes=indexes,
                    foreign_keys=foreign_keys,
                )
            except Exception:
                _logger = logging.getLogger(__name__)
                _logger.exception(
                    "Failed to extract schema for PostgreSQL table '%s'",
                    table_name,
                )

        return SchemaSnapshot(
            database_type="postgresql",
            database_name=db_name or "postgresql",
            tables=tables,
            created_at=datetime.now(),
        )

    # ── SQLite ──────────────────────────────────────────────────

    @staticmethod
    def _extract_sqlite(conn: Any, db_name: str) -> SchemaSnapshot:
        """Extract schema from SQLite using PRAGMA."""
        tables: dict[str, TableSchema] = {}
        cursor = conn.cursor()

        # Get all user tables (exclude sqlite_*)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        table_names = [r[0] for r in cursor.fetchall()]

        for table_name in table_names:
            try:
                # Columns via PRAGMA
                cursor.execute(f"PRAGMA table_info('{table_name}')")
                col_rows = cursor.fetchall()

                # Foreign keys via PRAGMA
                cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
                fk_rows = cursor.fetchall()

                fk_map: dict[str, tuple[str, str]] = {}
                for fkr in fk_rows:
                    if len(fkr) >= 5:
                        fk_map[fkr[3]] = (fkr[2], fkr[4])  # col -> (ref_table, ref_col)

                # Indexes via PRAGMA
                cursor.execute(f"PRAGMA index_list('{table_name}')")
                idx_rows = cursor.fetchall()

                columns = []
                for cr in col_rows:
                    if len(cr) < 6:
                        continue
                    col_name = cr[1]
                    col_type = cr[2]
                    is_pk = bool(cr[5])
                    ref = fk_map.get(col_name)

                    columns.append(
                        ColumnSchema(
                            name=col_name,
                            type=col_type or "TEXT",
                            nullable=not cr[3],
                            is_primary_key=is_pk,
                            is_foreign_key=(col_name in fk_map),
                            references=ref,
                            default=cr[4],
                        )
                    )

                # Row estimate
                row_count = 0
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM '{table_name}'")
                    row_count = cursor.fetchone()[0]
                except Exception:
                    pass

                indexes = []
                for ir in idx_rows:
                    if len(ir) < 3:
                        continue
                    idx_name = ir[1]
                    cursor.execute(f"PRAGMA index_info('{idx_name}')")
                    idx_cols = [r[2] for r in cursor.fetchall() if len(r) > 2]
                    indexes.append(
                        IndexInfo(
                            name=idx_name,
                            columns=idx_cols,
                            is_unique=bool(ir[2]),
                        )
                    )

                foreign_keys = []
                for fkr in fk_rows:
                    if len(fkr) < 5:
                        continue
                    foreign_keys.append(
                        ForeignKey(
                            name=f"fk_{fkr[0]}",
                            column=fkr[3],
                            ref_table=fkr[2],
                            ref_column=fkr[4],
                        )
                    )

                tables[table_name] = TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=row_count,
                    indexes=indexes,
                    foreign_keys=foreign_keys,
                )
            except Exception:
                _logger = logging.getLogger(__name__)
                _logger.exception(
                    "Failed to extract schema for SQLite table '%s'",
                    table_name,
                )

        return SchemaSnapshot(
            database_type="sqlite",
            database_name=db_name or "sqlite",
            tables=tables,
            created_at=datetime.now(),
        )

    # ── DuckDB ──────────────────────────────────────────────────

    @staticmethod
    def _extract_duckdb(conn: Any, db_name: str) -> SchemaSnapshot:
        """Extract schema from DuckDB using information_schema."""
        tables: dict[str, TableSchema] = {}

        table_rows = conn.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
        """).fetchall()

        for tr in table_rows:
            table_name = tr[0]
            try:
                col_rows = conn.execute(f"""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'main' AND table_name = '{table_name}'
                    ORDER BY ordinal_position
                """).fetchall()

                columns = []
                for cr in col_rows:
                    if len(cr) < 4:
                        continue
                    columns.append(
                        ColumnSchema(
                            name=cr[0],
                            type=cr[1] or "VARCHAR",
                            nullable=(cr[2] == "YES"),
                            default=cr[3],
                        )
                    )

                # Row estimate
                row_count = 0
                try:
                    cnt = conn.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()
                    row_count = cnt[0]
                except Exception:
                    pass

                tables[table_name] = TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=row_count,
                )
            except Exception:
                _logger = logging.getLogger(__name__)
                _logger.exception(
                    "Failed to extract schema for DuckDB table '%s'",
                    table_name,
                )

        return SchemaSnapshot(
            database_type="duckdb",
            database_name=db_name or "duckdb",
            tables=tables,
            created_at=datetime.now(),
        )

    # ── MySQL ──────────────────────────────────────────────────────

    @staticmethod
    async def _extract_mysql(conn: Any, db_name: str) -> SchemaSnapshot:
        """Extract schema from MySQL using information_schema.

        Uses aiomysql cursor to query information_schema.tables,
        information_schema.columns, and information_schema.key_column_usage.
        """
        tables: dict[str, TableSchema] = {}
        # The database name passed in may be a full db_id; extract the real
        # schema name from the connection config or use it as-is.
        # Query the actual database name from the connection.
        cursor = await conn.cursor()

        try:
            # Get current database name
            await cursor.execute("SELECT DATABASE()")
            row = await cursor.fetchone()
            # Explicitly handle NULL: MySQL returns (None,) when no DB selected
            schema_name = row[0] if row and row[0] is not None else db_name or "mysql"

            # Get all user tables in the current database
            await cursor.execute(
                "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS "
                "FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
                "ORDER BY TABLE_NAME",
                (schema_name,),
            )
            table_rows = await cursor.fetchall()

            _logger = logging.getLogger(__name__)

            for tr in table_rows:
                table_name = tr[0]
                table_comment = tr[1] if len(tr) > 1 and tr[1] else None
                row_estimate = int(tr[2]) if len(tr) > 2 and tr[2] else 0

                try:
                    # ── Columns ────────────────────────────────────
                    await cursor.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                        "COLUMN_COMMENT, COLUMN_KEY, ORDINAL_POSITION, "
                        "CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE "
                        "FROM information_schema.columns "
                        "WHERE table_schema = %s AND table_name = %s "
                        "ORDER BY ORDINAL_POSITION",
                        (schema_name, table_name),
                    )
                    col_rows = await cursor.fetchall()

                    # ── Primary keys ───────────────────────────────
                    pk_set: set[str] = set()
                    for cr in col_rows:
                        if len(cr) > 5 and cr[5] == "PRI":  # COLUMN_KEY
                            pk_set.add(cr[0])

                    # ── Foreign keys ───────────────────────────────
                    await cursor.execute(
                        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, "
                        "REFERENCED_COLUMN_NAME, CONSTRAINT_NAME "
                        "FROM information_schema.key_column_usage "
                        "WHERE table_schema = %s AND table_name = %s "
                        "AND REFERENCED_TABLE_NAME IS NOT NULL",
                        (schema_name, table_name),
                    )
                    fk_rows = await cursor.fetchall()

                    fk_map: dict[str, tuple[str, str]] = {}
                    foreign_keys: list[ForeignKey] = []
                    for fkr in fk_rows:
                        if len(fkr) < 4:
                            continue
                        col_name = fkr[0]
                        ref_table = fkr[1]
                        ref_column = fkr[2]
                        constraint_name = fkr[3]
                        fk_map[col_name] = (ref_table, ref_column)
                        foreign_keys.append(
                            ForeignKey(
                                name=constraint_name,
                                column=col_name,
                                ref_table=ref_table,
                                ref_column=ref_column,
                            )
                        )

                    # ── Build ColumnSchema list ────────────────────
                    columns: list[ColumnSchema] = []
                    for cr in col_rows:
                        if len(cr) < 5:
                            _logger.warning(
                                "Skipping column in table '%s.%s': expected >=5 columns, got %d: %s",
                                schema_name, table_name, len(cr), cr,
                            )
                            continue
                        col_name = cr[0]
                        data_type = cr[1]
                        # Build full type string with length/precision
                        if len(cr) > 7 and cr[7] is not None:  # CHARACTER_MAXIMUM_LENGTH
                            data_type = f"{data_type}({cr[7]})"
                        elif len(cr) > 8 and cr[8] is not None:  # NUMERIC_PRECISION
                            if len(cr) > 9 and cr[9] is not None and cr[9] > 0:
                                data_type = f"{data_type}({cr[8]},{cr[9]})"
                            else:
                                data_type = f"{data_type}({cr[8]})"

                        is_pk = col_name in pk_set
                        is_fk = col_name in fk_map
                        ref = fk_map.get(col_name)

                        columns.append(
                            ColumnSchema(
                                name=col_name,
                                type=data_type,
                                nullable=(cr[2] == "YES") if len(cr) > 2 else True,
                                is_primary_key=is_pk,
                                is_foreign_key=is_fk,
                                references=ref,
                                default=cr[3] if len(cr) > 3 else None,
                                comment=cr[4] if len(cr) > 4 and cr[4] else None,
                            )
                        )

                    # ── Indexes ────────────────────────────────────
                    indexes: list[IndexInfo] = []
                    try:
                        await cursor.execute(
                            "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE "
                            "FROM information_schema.statistics "
                            "WHERE table_schema = %s AND table_name = %s "
                            "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                            (schema_name, table_name),
                        )
                        idx_rows = await cursor.fetchall()
                        idx_map: dict[str, dict] = {}
                        for ir in idx_rows:
                            if len(ir) < 3:
                                continue
                            idx_name = ir[0]
                            col = ir[1]
                            non_unique = bool(ir[2])
                            if idx_name not in idx_map:
                                idx_map[idx_name] = {
                                    "name": idx_name,
                                    "columns": [],
                                    "is_unique": not non_unique,
                                }
                            idx_map[idx_name]["columns"].append(col)
                        indexes = [
                            IndexInfo(**v) for v in idx_map.values()
                        ]
                    except Exception:
                        pass  # SHOW INDEX may fail on some MySQL versions

                    tables[table_name] = TableSchema(
                        name=table_name,
                        comment=table_comment,
                        columns=columns,
                        row_count_estimate=row_estimate,
                        indexes=indexes,
                        foreign_keys=foreign_keys,
                    )
                except Exception:
                    _logger.exception(
                        "Failed to extract schema for table '%s.%s'",
                        schema_name, table_name,
                    )

        finally:
            await cursor.close()

        return SchemaSnapshot(
            database_type="mysql",
            database_name=schema_name,
            tables=tables,
            created_at=datetime.now(),
        )
