"""Tests for audit.py — checksum, AuditConfig, ensure_audit_table, was_deployed, record_deployment."""

import pytest
from unittest.mock import MagicMock, patch, call

from snow_ops.audit import (
    AuditConfig,
    compute_checksum,
    ensure_audit_table,
    record_deployment,
    was_deployed,
)


@pytest.fixture
def cursor():
    return MagicMock()


@pytest.fixture
def config():
    return AuditConfig(schema="public", table="audit_log")


# ── compute_checksum ───────────────────────────────────────────────────────────

class TestComputeChecksum:
    def test_returns_64_char_hex(self):
        result = compute_checksum("SELECT 1")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert compute_checksum("SELECT 1") == compute_checksum("SELECT 1")

    def test_different_sql_different_checksum(self):
        assert compute_checksum("SELECT 1") != compute_checksum("SELECT 2")

    def test_whitespace_sensitive(self):
        assert compute_checksum("SELECT 1") != compute_checksum("SELECT 1\n")

    def test_empty_string(self):
        result = compute_checksum("")
        assert len(result) == 64


# ── AuditConfig ────────────────────────────────────────────────────────────────

class TestAuditConfig:
    def test_defaults(self):
        c = AuditConfig()
        assert c.schema == "public"
        assert c.table == "audit_log"

    def test_custom_values(self):
        c = AuditConfig(schema="myschema", table="my_log")
        assert c.schema == "myschema"
        assert c.table == "my_log"

    def test_invalid_schema_raises(self):
        with pytest.raises(ValueError, match="schema"):
            AuditConfig(schema="my-schema")

    def test_invalid_table_raises(self):
        with pytest.raises(ValueError, match="table"):
            AuditConfig(table="my table")

    def test_dot_in_schema_raises(self):
        with pytest.raises(ValueError):
            AuditConfig(schema="db.public")

    def test_semicolon_raises(self):
        with pytest.raises(ValueError):
            AuditConfig(table="t; DROP TABLE t--")


# ── ensure_audit_table ─────────────────────────────────────────────────────────

class TestEnsureAuditTable:
    def test_creates_table_when_not_exists(self, cursor, config):
        cursor.fetchall.return_value = []
        ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert any("CREATE TABLE" in c for c in calls)

    def test_no_action_when_all_columns_present(self, cursor, config):
        cursor.fetchall.return_value = [
            ("script_name",), ("checksum",), ("executed_at",)
        ]
        ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert not any("CREATE TABLE" in c or "ALTER TABLE" in c for c in calls)

    def test_compatible_with_extra_columns(self, cursor, config):
        cursor.fetchall.return_value = [
            ("script_name",), ("checksum",), ("executed_at",), ("extra_col",)
        ]
        ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert not any("ALTER TABLE" in c for c in calls)

    def test_force_alters_missing_columns(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        ensure_audit_table(cursor, config, force=True)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert any("ALTER TABLE" in c for c in calls)

    def test_non_tty_raises_runtime_error(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(RuntimeError, match="--force"):
                ensure_audit_table(cursor, config, force=False)

    def test_stdin_none_raises_runtime_error(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("snow_ops.audit.sys") as mock_sys:
            mock_sys.stdin = None
            mock_sys.exit = pytest.fail  # should not be called
            with pytest.raises(RuntimeError, match="--force"):
                ensure_audit_table(cursor, config, force=False)

    def test_tty_yes_alters_table(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="y"):
            mock_stdin.isatty.return_value = True
            ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert any("ALTER TABLE" in c for c in calls)

    def test_tty_yes_uppercase_accepted(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="Y"):
            mock_stdin.isatty.return_value = True
            ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert any("ALTER TABLE" in c for c in calls)

    def test_tty_no_exits(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="n"):
            mock_stdin.isatty.return_value = True
            with pytest.raises(SystemExit):
                ensure_audit_table(cursor, config, force=False)

    def test_tty_empty_answer_exits(self, cursor, config):
        cursor.fetchall.return_value = [("script_name",)]
        with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value=""):
            mock_stdin.isatty.return_value = True
            with pytest.raises(SystemExit):
                ensure_audit_table(cursor, config, force=False)

    def test_column_names_compared_case_insensitively(self, cursor, config):
        cursor.fetchall.return_value = [
            ("SCRIPT_NAME",), ("CHECKSUM",), ("EXECUTED_AT",)
        ]
        ensure_audit_table(cursor, config, force=False)
        calls = [str(c) for c in cursor.execute.call_args_list]
        assert not any("ALTER TABLE" in c for c in calls)

    def test_uses_custom_schema_and_table(self, cursor):
        custom = AuditConfig(schema="myschema", table="my_log")
        cursor.fetchall.return_value = []
        ensure_audit_table(cursor, custom, force=False)
        create_call = str(cursor.execute.call_args_list[-1])
        assert "myschema" in create_call
        assert "my_log" in create_call


# ── was_deployed ───────────────────────────────────────────────────────────────

class TestWasDeployed:
    def test_returns_true_when_row_found(self, cursor, config):
        cursor.fetchone.return_value = (1,)
        assert was_deployed(cursor, config, "scripts/init.sql", "abc123") is True

    def test_returns_false_when_no_row(self, cursor, config):
        cursor.fetchone.return_value = None
        assert was_deployed(cursor, config, "scripts/init.sql", "abc123") is False

    def test_passes_script_name_and_checksum_as_params(self, cursor, config):
        cursor.fetchone.return_value = None
        was_deployed(cursor, config, "my_script.sql", "deadbeef")
        args = cursor.execute.call_args[0]
        assert args[1] == ("my_script.sql", "deadbeef")

    def test_query_uses_correct_table(self, cursor, config):
        cursor.fetchone.return_value = None
        was_deployed(cursor, config, "s.sql", "x")
        sql = cursor.execute.call_args[0][0]
        assert "public.audit_log" in sql


# ── record_deployment ──────────────────────────────────────────────────────────

class TestRecordDeployment:
    def test_inserts_script_name_and_checksum(self, cursor, config):
        record_deployment(cursor, config, "scripts/init.sql", "abc123")
        args = cursor.execute.call_args[0]
        assert args[1] == ("scripts/init.sql", "abc123")

    def test_insert_targets_correct_table(self, cursor, config):
        record_deployment(cursor, config, "s.sql", "x")
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO public.audit_log" in sql

    def test_executed_at_not_in_params(self, cursor, config):
        record_deployment(cursor, config, "s.sql", "x")
        args = cursor.execute.call_args[0]
        assert len(args[1]) == 2
