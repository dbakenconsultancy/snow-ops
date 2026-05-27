"""Tests for modules/macros.sql — all five built-in macros."""

import pytest
from jinja2 import TemplateRuntimeError

from snow_ops.renderer import build_env
from tests.conftest import PROJECT_DIR

IMPORT = "{% from 'modules/macros.sql' import ref, surrogate_key, limit_rows, column_list, date_filter %}"


@pytest.fixture(scope="module")
def env():
    return build_env(PROJECT_DIR)


def render(env, expr: str, **ctx) -> str:
    return env.from_string(IMPORT + expr).render(**ctx).strip()


# ── ref ────────────────────────────────────────────────────────────────────────

class TestRef:
    def test_table_only(self, env):
        assert render(env, "{{ ref('orders') }}") == "orders"

    def test_schema_and_table(self, env):
        assert render(env, "{{ ref('orders', schema='RAW') }}") == "RAW.orders"

    def test_database_schema_table(self, env):
        result = render(env, "{{ ref('orders', schema='RAW', database='PROD') }}")
        assert result == "PROD.RAW.orders"

    def test_database_without_schema_raises(self, env):
        with pytest.raises(TemplateRuntimeError, match="schema is required"):
            render(env, "{{ ref('orders', database='PROD') }}")


# ── surrogate_key ──────────────────────────────────────────────────────────────

class TestSurrogateKey:
    def test_wraps_in_md5(self, env):
        result = render(env, "{{ surrogate_key(['id']) }}")
        assert "MD5(" in result

    def test_uses_concat_ws_with_pipe(self, env):
        result = render(env, "{{ surrogate_key(['id']) }}")
        assert "CONCAT_WS('|'" in result

    def test_coalesces_each_field(self, env):
        result = render(env, "{{ surrogate_key(['a', 'b']) }}")
        assert result.count("COALESCE") == 2

    def test_all_fields_present(self, env):
        result = render(env, "{{ surrogate_key(['user_id', 'event_date', 'event_type']) }}")
        assert "user_id" in result
        assert "event_date" in result
        assert "event_type" in result


# ── limit_rows ─────────────────────────────────────────────────────────────────

class TestLimitRows:
    def test_default_is_100(self, env):
        assert render(env, "{{ limit_rows() }}") == "LIMIT 100"

    def test_custom_value(self, env):
        assert render(env, "{{ limit_rows(500) }}") == "LIMIT 500"

    def test_zero_allowed(self, env):
        assert render(env, "{{ limit_rows(0) }}") == "LIMIT 0"


# ── date_filter ────────────────────────────────────────────────────────────────

class TestDateFilter:
    def test_start_only(self, env):
        result = render(env, "{{ date_filter('event_date', '2024-01-01') }}")
        assert "event_date >= '2024-01-01'" in result
        assert "AND" not in result

    def test_with_end_is_exclusive(self, env):
        result = render(env, "{{ date_filter('event_date', '2024-01-01', '2025-01-01') }}")
        assert "event_date >= '2024-01-01'" in result
        assert "event_date < '2025-01-01'" in result

    def test_end_uses_less_than_not_lte(self, env):
        result = render(env, "{{ date_filter('col', '2024-01-01', '2024-12-31') }}")
        assert "<=" not in result
        assert "< '2024-12-31'" in result


# ── column_list ────────────────────────────────────────────────────────────────

class TestColumnList:
    def test_single_column(self, env):
        result = render(env, "{{ column_list(['id']) }}")
        assert "id" in result

    def test_multiple_columns_present(self, env):
        result = render(env, "{{ column_list(['a', 'b', 'c']) }}")
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_no_trailing_comma(self, env):
        result = render(env, "{{ column_list(['x', 'y']) }}")
        # last column must not be followed by a comma
        lines = [l.strip() for l in result.splitlines() if l.strip()]
        assert not lines[-1].endswith(",")
