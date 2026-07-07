"""Tests for executor.py — get_connection wiring and execute_statements progress output."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from snow_ops.executor import execute_statements, get_connection

_SNOWFLAKE_VARS = (
    "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_ROLE",
)


@pytest.fixture
def fake_connector(monkeypatch):
    """Stand-in for snowflake.connector so no real driver or network is needed."""
    connector = types.ModuleType("snowflake.connector")
    connector.connect = MagicMock(return_value="CONN")
    snowflake = types.ModuleType("snowflake")
    snowflake.connector = connector
    monkeypatch.setitem(sys.modules, "snowflake", snowflake)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    return connector


class TestGetConnection:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for var in _SNOWFLAKE_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_missing_connector_package_raises_runtime_error(self, monkeypatch):
        # a None entry in sys.modules makes the import fail as if uninstalled
        monkeypatch.setitem(sys.modules, "snowflake", None)
        monkeypatch.setitem(sys.modules, "snowflake.connector", None)
        with pytest.raises(RuntimeError, match="snowflake-connector-python"):
            get_connection()

    def test_named_connection_forwarded(self, fake_connector, tmp_path):
        toml = tmp_path / "connections.toml"
        result = get_connection("dev", toml)
        assert result == "CONN"
        fake_connector.connect.assert_called_once_with(
            connection_name="dev", connections_file_path=toml
        )

    def test_env_vars_required_only(self, fake_connector, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "alice")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
        result = get_connection()
        assert result == "CONN"
        fake_connector.connect.assert_called_once_with(
            account="acct", user="alice", password="secret"
        )

    def test_optional_env_vars_included_when_set(self, fake_connector, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "alice")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        monkeypatch.setenv("SNOWFLAKE_SCHEMA", "sch")
        monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
        monkeypatch.setenv("SNOWFLAKE_ROLE", "r")
        get_connection()
        kwargs = fake_connector.connect.call_args.kwargs
        assert kwargs["database"] == "db"
        assert kwargs["schema"] == "sch"
        assert kwargs["warehouse"] == "wh"
        assert kwargs["role"] == "r"

    def test_empty_optional_env_var_omitted(self, fake_connector, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "alice")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
        monkeypatch.setenv("SNOWFLAKE_ROLE", "")
        get_connection()
        assert "role" not in fake_connector.connect.call_args.kwargs


@pytest.fixture
def cursor():
    c = MagicMock()
    c.rowcount = -1
    return c


class TestExecuteStatements:
    def test_executes_each_statement_in_order(self, cursor):
        execute_statements(cursor, ["SELECT 1", "SELECT 2"])
        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert executed == ["SELECT 1", "SELECT 2"]

    def test_counter_shows_position_and_total(self, cursor, capsys):
        execute_statements(cursor, ["SELECT 1", "SELECT 2"])
        out = capsys.readouterr().out
        assert "[1/2]" in out
        assert "[2/2]" in out

    def test_short_statement_not_ellipsized(self, cursor, capsys):
        execute_statements(cursor, ["SELECT 1"])
        assert "..." not in capsys.readouterr().out

    def test_long_statement_truncated_with_ellipsis(self, cursor, capsys):
        stmt = "SELECT " + ", ".join(f"col_{i}" for i in range(30))
        execute_statements(cursor, [stmt])
        out = capsys.readouterr().out
        assert "..." in out
        assert stmt not in out

    def test_newlines_flattened_in_preview(self, cursor, capsys):
        execute_statements(cursor, ["SELECT 1\nFROM t"])
        assert "SELECT 1 FROM t" in capsys.readouterr().out

    def test_rowcount_printed_when_nonnegative(self, cursor, capsys):
        cursor.rowcount = 5
        execute_statements(cursor, ["DELETE FROM t"])
        assert "rows affected: 5" in capsys.readouterr().out

    def test_rowcount_hidden_when_negative(self, cursor, capsys):
        execute_statements(cursor, ["SELECT 1"])
        assert "rows affected" not in capsys.readouterr().out

    def test_rowcount_hidden_when_none(self, cursor, capsys):
        cursor.rowcount = None
        execute_statements(cursor, ["SELECT 1"])
        assert "rows affected" not in capsys.readouterr().out
