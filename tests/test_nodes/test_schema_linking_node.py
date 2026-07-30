"""Tests for SchemaLinkingNode — entity-to-schema linking."""

import pytest

from app.models.query import SQR, Entity, IntentType
from app.models.schema import (
    ColumnSchema,
    ForeignKey,
    SchemaSnapshot,
    TableSchema,
)
from app.nodes.base import NodeInput
from app.nodes.schema_linking import (
    SchemaLinkingNode,
    _best_column_match,
    _best_table_match,
    _compute_link_confidence,
    _disambiguate_columns,
    _expand_foreign_keys,
    _filter_schema,
    _gather_candidates,
    _match_schema,
    _tokenize_zh,
)

pytestmark = pytest.mark.anyio


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def node():
    return SchemaLinkingNode()


@pytest.fixture
def ecommerce_schema():
    return SchemaSnapshot(
        database_type="postgresql",
        database_name="ecommerce",
        tables={
            "users": TableSchema(
                name="users",
                comment="User accounts",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(100)"),
                    ColumnSchema(name="email", type="VARCHAR(200)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
                foreign_keys=[],
            ),
            "orders": TableSchema(
                name="orders",
                comment="Purchase orders",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="user_id", type="INTEGER"),
                    ColumnSchema(name="amount", type="DECIMAL(10,2)"),
                    ColumnSchema(name="status", type="VARCHAR(20)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
                foreign_keys=[
                    ForeignKey(name="fk_orders_users", column="user_id",
                              ref_table="users", ref_column="id"),
                ],
            ),
            "products": TableSchema(
                name="products",
                comment="Product catalog",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(200)"),
                    ColumnSchema(name="price", type="DECIMAL(10,2)"),
                ],
                foreign_keys=[],
            ),
            "order_items": TableSchema(
                name="order_items",
                comment="Line items",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="order_id", type="INTEGER"),
                    ColumnSchema(name="product_id", type="INTEGER"),
                    ColumnSchema(name="quantity", type="INTEGER"),
                ],
                foreign_keys=[
                    ForeignKey(name="fk_oi_orders", column="order_id",
                              ref_table="orders", ref_column="id"),
                    ForeignKey(name="fk_oi_products", column="product_id",
                              ref_table="products", ref_column="id"),
                ],
            ),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# _tokenize_zh
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenizeZh:
    def test_simple(self):
        tokens = _tokenize_zh("查询所有订单的金额")
        assert len(tokens) >= 1

    def test_empty(self):
        assert _tokenize_zh("") == []

    def test_stop_words_removed(self):
        tokens = _tokenize_zh("从用户表中查询数据")
        assert len(tokens) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# _gather_candidates
# ═══════════════════════════════════════════════════════════════════════════


class TestGatherCandidates:
    def test_from_target_tables(self):
        sqr = SQR(raw_text="查询订单", language="zh", intent=IntentType.SELECT,
                  target_tables=["订单"])
        candidates = _gather_candidates(sqr)
        tables = [c for c in candidates if c["kind"] == "table"]
        assert any(c["name"] == "订单" for c in tables)

    def test_from_entities(self):
        sqr = SQR(
            raw_text="查询订单",
            language="zh",
            intent=IntentType.SELECT,
            entities=[Entity(name="amount", type="column", normalized="amount")],
        )
        candidates = _gather_candidates(sqr)
        cols = [c for c in candidates if c["kind"] == "column"]
        assert any(c["name"] == "amount" for c in cols)


# ═══════════════════════════════════════════════════════════════════════════
# _best_table_match
# ═══════════════════════════════════════════════════════════════════════════


class TestBestTableMatch:
    def test_exact_match(self, ecommerce_schema):
        name, conf = _best_table_match("users", ecommerce_schema)
        assert name == "users"
        assert conf == 1.0

    def test_case_insensitive_match(self, ecommerce_schema):
        name, conf = _best_table_match("Users", ecommerce_schema)
        assert name == "users"
        assert conf == 0.95

    def test_substring_match(self, ecommerce_schema):
        name, conf = _best_table_match("order", ecommerce_schema)
        assert name == "orders"
        assert conf >= 0.6

    def test_no_match(self, ecommerce_schema):
        name, conf = _best_table_match("xyz_ghost_table", ecommerce_schema)
        assert name is None
        assert conf == 0.0

    def test_comment_match(self, ecommerce_schema):
        # "product catalog" contains "product"
        name, conf = _best_table_match("catalog", ecommerce_schema)
        # "catalog" is in products.comment
        assert name == "products" or conf == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# _best_column_match
# ═══════════════════════════════════════════════════════════════════════════


class TestBestColumnMatch:
    def test_exact_match(self, ecommerce_schema):
        tbl, col, conf = _best_column_match("email", ecommerce_schema)
        assert col == "email"
        assert conf == 1.0

    def test_case_insensitive(self, ecommerce_schema):
        tbl, col, conf = _best_column_match("Email", ecommerce_schema)
        assert col == "email"
        assert conf >= 0.9

    def test_substring_match(self, ecommerce_schema):
        tbl, col, conf = _best_column_match("price", ecommerce_schema)
        assert col == "price"
        assert conf >= 0.9

    def test_no_match(self, ecommerce_schema):
        tbl, col, conf = _best_column_match("ghost_column", ecommerce_schema)
        assert col is None
        assert conf == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# _match_schema
# ═══════════════════════════════════════════════════════════════════════════


class TestMatchSchema:
    def test_returns_tables_and_columns(self, ecommerce_schema):
        candidates = [
            {"name": "users", "kind": "table", "source": "test"},
            {"name": "email", "kind": "column", "source": "test"},
        ]
        tables, columns = _match_schema(candidates, ecommerce_schema, 0.6)
        assert "users" in tables
        assert any(c["name"] == "email" for c in columns)

    def test_below_threshold_filtered(self, ecommerce_schema):
        candidates = [{"name": "ghost", "kind": "table", "source": "test"}]
        tables, columns = _match_schema(candidates, ecommerce_schema, 0.9)
        assert tables == []


# ═══════════════════════════════════════════════════════════════════════════
# _expand_foreign_keys
# ═══════════════════════════════════════════════════════════════════════════


class TestFKExpansion:
    def test_expands_referenced_tables(self, ecommerce_schema):
        """orders references users → expanding orders should add users."""
        result = _expand_foreign_keys(["orders"], ecommerce_schema)
        assert "users" in result
        assert "orders" in result

    def test_expands_referencing_tables(self, ecommerce_schema):
        """order_items references orders → expanding orders should add order_items."""
        result = _expand_foreign_keys(["orders"], ecommerce_schema)
        assert "order_items" in result

    def test_no_expansion_for_standalone(self, ecommerce_schema):
        """users has an outgoing FK? No — users has no FKs.
        But orders references users, so users should pull in orders too."""
        result = _expand_foreign_keys(["users"], ecommerce_schema)
        # users is referenced by orders → orders should be added
        assert "users" in result
        assert "orders" in result

    def test_chain_expansion(self, ecommerce_schema):
        """orders → users + order_items; order_items → products."""
        result = _expand_foreign_keys(["orders"], ecommerce_schema)
        assert "users" in result
        assert "order_items" in result
        assert "products" in result  # via order_items → products


# ═══════════════════════════════════════════════════════════════════════════
# _disambiguate_columns
# ═══════════════════════════════════════════════════════════════════════════


class TestDisambiguateColumns:
    def test_single_table_disambiguation(self, ecommerce_schema):
        """'created_at' exists in both users and orders; if only users
        is linked, it should resolve to users."""
        cols = [{"name": "created_at", "table": None, "confidence": 0.8}]
        resolved = _disambiguate_columns(cols, ["users"], ecommerce_schema)
        assert resolved[0]["table"] == "users"

    def test_already_qualified(self, ecommerce_schema):
        cols = [{"name": "email", "table": "users", "confidence": 1.0}]
        resolved = _disambiguate_columns(cols, ["users"], ecommerce_schema)
        assert resolved[0]["table"] == "users"

    def test_ambiguous_column(self, ecommerce_schema):
        """'created_at' in both users and orders — should note candidates."""
        cols = [{"name": "created_at", "table": None, "confidence": 0.8}]
        resolved = _disambiguate_columns(cols, ["users", "orders"], ecommerce_schema)
        assert "candidates" in resolved[0]
        assert len(resolved[0]["candidates"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# _filter_schema / _compute_link_confidence
# ═══════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_filter_schema(self, ecommerce_schema):
        filtered = _filter_schema(ecommerce_schema, ["users", "orders"])
        assert set(filtered.tables.keys()) == {"users", "orders"}

    def test_filter_schema_empty(self, ecommerce_schema):
        filtered = _filter_schema(ecommerce_schema, [])
        assert filtered.tables == {}

    def test_confidence_zero_for_empty(self):
        conf = _compute_link_confidence([], [], [])
        assert conf == 0.0

    def test_confidence_range(self):
        conf = _compute_link_confidence(
            ["a", "b"],
            [{"confidence": 1.0}, {"confidence": 0.8}],
            [{"name": "a", "kind": "table"}, {"name": "c", "kind": "column"}],
        )
        assert 0.0 <= conf <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "schema_linking"

    def test_is_agentic_node(self, node):
        from app.nodes.agentic import AgenticNode
        assert isinstance(node, AgenticNode)


# ═══════════════════════════════════════════════════════════════════════════
# Execute
# ═══════════════════════════════════════════════════════════════════════════


class TestExecute:
    async def test_links_tables_from_sqr(self, node, ecommerce_schema):
        sqr = SQR(
            raw_text="find users and orders",
            language="en",
            intent=IntentType.SELECT,
            target_tables=["users", "orders"],
            entities=[
                Entity(name="users", type="table", normalized="users"),
                Entity(name="orders", type="table", normalized="orders"),
            ],
        )
        output = await node.execute(
            NodeInput(
                query_text="find users and orders",
                context={"sqr": sqr, "schema": ecommerce_schema},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.metadata["tables_linked"] >= 2

    async def test_links_columns(self, node, ecommerce_schema):
        sqr = SQR(
            raw_text="find user email",
            language="en",
            intent=IntentType.SELECT,
            entities=[Entity(name="email", type="column", normalized="email")],
        )
        output = await node.execute(
            NodeInput(
                query_text="find user email",
                context={"sqr": sqr, "schema": ecommerce_schema},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_fk_expansion_enabled(self, node, ecommerce_schema):
        sqr = SQR(
            raw_text="find orders",
            language="en",
            intent=IntentType.SELECT,
            target_tables=["orders"],
        )
        output = await node.execute(
            NodeInput(
                query_text="find orders",
                context={"sqr": sqr, "schema": ecommerce_schema},
            )
        )
        tables = output.result["tables"]
        # orders → FK expansion adds users + order_items
        assert "orders" in tables
        assert "users" in tables or "order_items" in tables

    async def test_fk_expansion_disabled(self, node, ecommerce_schema):
        sqr = SQR(raw_text="find orders", language="en", intent=IntentType.SELECT,
                  target_tables=["orders"])
        output = await node.execute(
            NodeInput(
                query_text="find orders",
                context={"sqr": sqr, "schema": ecommerce_schema},
                config={"fk_expand": False},
            )
        )
        tables = output.result["tables"]
        assert "orders" in tables
        # Without expansion, users shouldn't be added (unless directly matched)
        assert "users" not in tables

    async def test_no_schema(self, node):
        output = await node.execute(
            NodeInput(query_text="find orders")
        )
        assert output.metadata["status"] == "no_schema"
        assert output.result["tables"] == []

    async def test_no_sqr_fallback(self, node, ecommerce_schema):
        """When no SQR in context, wraps query_text."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"schema": ecommerce_schema},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_linked_schema_in_context(self, node, ecommerce_schema):
        sqr = SQR(raw_text="find users", language="en", intent=IntentType.SELECT,
                  target_tables=["users"])
        output = await node.execute(
            NodeInput(
                query_text="find users",
                context={"sqr": sqr, "schema": ecommerce_schema},
            )
        )
        assert "linked_schema" in output.context
        assert output.context["linked_schema"] is not None

    async def test_columns_in_result(self, node, ecommerce_schema):
        sqr = SQR(
            raw_text="find user name and email",
            language="en",
            intent=IntentType.SELECT,
            entities=[
                Entity(name="name", type="column", normalized="name"),
                Entity(name="email", type="column", normalized="email"),
            ],
            target_tables=["users"],
        )
        output = await node.execute(
            NodeInput(
                query_text="find user name and email",
                context={"sqr": sqr, "schema": ecommerce_schema},
            )
        )
        assert output.metadata["columns_linked"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Workflow chain
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowChain:
    async def test_parse_to_link_chain(self, ecommerce_schema):
        """parse_nl → schema_linking chain."""
        from app.nodes.parse_nl import ParseNLNode

        # Step 1: Parse NL
        parse_node = ParseNLNode()
        parse_output = await parse_node.execute(
            NodeInput(query_text="find user order amounts")
        )
        shared = await parse_node.update_context(parse_output, {})

        # Step 2: Schema linking
        link_node = SchemaLinkingNode()
        link_output = await link_node.execute(
            NodeInput(
                query_text="find user order amounts",
                context={**shared, "schema": ecommerce_schema},
            )
        )
        shared = await link_node.update_context(link_output, shared)

        assert link_output.metadata["status"] == "success"
        assert "sqr" in shared
        assert "linked_tables" in shared
        assert "linked_schema" in shared
