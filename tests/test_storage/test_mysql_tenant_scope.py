"""Static contract tests for the first tenant-scoped MySQLStore slice."""

from __future__ import annotations

import inspect

from app.storage.mysql_store import _CREATE_TABLES_SQL, MySQLStore


def test_new_schema_uses_composite_tenant_keys() -> None:
    schema = "\n".join(_CREATE_TABLES_SQL)

    assert schema.count("tenant_id INT NOT NULL DEFAULT 1") == 11
    assert "uk_tenant_section" in schema
    assert "uk_tenant_provider" in schema
    assert "uk_tenant_db_id" in schema
    assert "uk_tenant_name" in schema
    assert "uk_tenant_rule" in schema
    assert "FOREIGN KEY (tenant_id, domain_name)" in schema
    assert "uk_tenant_feedback_id" in schema
    assert "uk_tenant_rule_id" in schema
    assert "uk_tenant_date" in schema


def test_settings_and_connections_filter_by_tenant() -> None:
    methods = (
        MySQLStore.get_settings,
        MySQLStore.get_setting,
        MySQLStore.save_setting,
        MySQLStore.delete_setting,
        MySQLStore.get_api_key,
        MySQLStore.save_api_key,
        MySQLStore.delete_api_key,
        MySQLStore.get_all_api_keys,
        MySQLStore.save_connection,
        MySQLStore.get_connections,
        MySQLStore.get_connection,
        MySQLStore.delete_connection,
        MySQLStore.get_domains,
        MySQLStore.get_domain,
        MySQLStore.save_domain,
        MySQLStore.delete_domain,
        MySQLStore.set_active_domain,
        MySQLStore.get_glossary_terms,
        MySQLStore.save_glossary_term,
        MySQLStore.delete_glossary_term,
        MySQLStore.get_rules,
        MySQLStore.save_rule,
        MySQLStore.delete_rule,
        MySQLStore.save_query,
        MySQLStore.get_query_history,
        MySQLStore.save_feedback,
        MySQLStore.get_feedback_stats,
        MySQLStore.get_recent_feedback,
        MySQLStore.get_learning_stats,
        MySQLStore.get_rule_candidates,
        MySQLStore.save_rule_candidate,
        MySQLStore.update_rule_candidate_status,
        MySQLStore.save_quality_trend,
        MySQLStore.get_quality_trends,
        MySQLStore.save_query_pair,
        MySQLStore.get_query_pairs,
    )
    source = "\n".join(inspect.getsource(method) for method in methods)

    assert "get_current_tenant_id()" in source
    assert source.count("tenant_id = %s") >= 5
