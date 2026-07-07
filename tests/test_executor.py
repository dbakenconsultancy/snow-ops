"""Tests for executor.py — execute_statements progress output."""

import pytest
from unittest.mock import MagicMock

from snow_ops.executor import execute_statements


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
