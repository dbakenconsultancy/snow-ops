"""Tests for cli.py — file discovery, error formatting, executor error handling."""

import pytest
from pathlib import Path
from jinja2 import Environment, TemplateNotFound, TemplateSyntaxError, UndefinedError, StrictUndefined

from snowdump.cli import _collect_sql_files, _print_template_error


# ── _collect_sql_files ─────────────────────────────────────────────────────────

class TestCollectSqlFiles:
    @pytest.fixture
    def scripts(self, tmp_path):
        d = tmp_path / "scripts"
        d.mkdir()
        return d

    def test_finds_all_sql_files(self, scripts):
        (scripts / "a.sql").write_text("SELECT 1")
        (scripts / "b.sql").write_text("SELECT 2")
        result = _collect_sql_files(scripts, None)
        assert len(result) == 2

    def test_ignores_non_sql_files(self, scripts):
        (scripts / "a.sql").write_text("SELECT 1")
        (scripts / "b.txt").write_text("not sql")
        result = _collect_sql_files(scripts, None)
        assert len(result) == 1

    def test_recursive_into_subdirectories(self, scripts):
        (scripts / "sub").mkdir()
        (scripts / "top.sql").write_text("SELECT 1")
        (scripts / "sub" / "deep.sql").write_text("SELECT 2")
        result = _collect_sql_files(scripts, None)
        assert len(result) == 2

    def test_sorted_alphabetically_by_full_path(self, scripts):
        (scripts / "01").mkdir()
        (scripts / "02").mkdir()
        (scripts / "01" / "a.sql").write_text("SELECT 1")
        (scripts / "02" / "b.sql").write_text("SELECT 2")
        result = _collect_sql_files(scripts, None)
        assert result[0].name == "a.sql"
        assert result[1].name == "b.sql"

    def test_named_file_returned(self, scripts):
        (scripts / "q.sql").write_text("SELECT 1")
        result = _collect_sql_files(scripts, ["q.sql"])
        assert len(result) == 1
        assert result[0].name == "q.sql"

    def test_extension_added_when_omitted(self, scripts):
        (scripts / "q.sql").write_text("SELECT 1")
        result = _collect_sql_files(scripts, ["q"])
        assert len(result) == 1

    def test_named_file_in_subdirectory(self, scripts):
        (scripts / "sub").mkdir()
        (scripts / "sub" / "q.sql").write_text("SELECT 1")
        result = _collect_sql_files(scripts, ["sub/q.sql"])
        assert len(result) == 1

    def test_path_traversal_rejected(self, scripts):
        (scripts.parent / "secret.sql").write_text("SECRET")
        with pytest.raises(SystemExit):
            _collect_sql_files(scripts, ["../secret.sql"])

    def test_missing_named_file_exits(self, scripts):
        with pytest.raises(SystemExit):
            _collect_sql_files(scripts, ["nonexistent.sql"])

    def test_directory_name_rejected(self, scripts):
        (scripts / "subdir").mkdir()
        with pytest.raises(SystemExit):
            _collect_sql_files(scripts, ["subdir"])


# ── _print_template_error ──────────────────────────────────────────────────────

class TestPrintTemplateError:
    @pytest.fixture
    def sql_file(self, tmp_path):
        return tmp_path / "query.sql"

    def test_not_found_shows_template_name(self, sql_file, tmp_path, capsys):
        exc = TemplateNotFound("modules/macros.sql")
        _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "modules/macros.sql" in out

    def test_not_found_shows_expected_path(self, sql_file, tmp_path, capsys):
        exc = TemplateNotFound("modules/macros.sql")
        _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert str(tmp_path) in out

    def test_not_found_reports_missing_file(self, sql_file, tmp_path, capsys):
        exc = TemplateNotFound("modules/missing.sql")
        _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "does not exist" in out

    def test_not_found_reports_unreadable_file(self, sql_file, tmp_path, capsys):
        (tmp_path / "modules").mkdir()
        f = tmp_path / "modules" / "present.sql"
        f.write_text("exists")
        exc = TemplateNotFound("modules/present.sql")
        _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "cannot be read" in out

    def test_syntax_error_shows_line_number(self, sql_file, tmp_path, capsys):
        env = Environment()
        try:
            env.from_string("SELECT *\nFROM t\nWHERE {% if x %}1")
        except TemplateSyntaxError as exc:
            _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "line 3" in out

    def test_syntax_error_shows_offending_line(self, sql_file, tmp_path, capsys):
        env = Environment()
        try:
            env.from_string("SELECT *\nFROM t\nWHERE {% if x %}1")
        except TemplateSyntaxError as exc:
            _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "WHERE {% if x %}1" in out

    def test_undefined_shows_variable_name(self, sql_file, tmp_path, capsys):
        env = Environment(undefined=StrictUndefined)
        try:
            env.from_string("{{ my_missing_var }}").render()
        except UndefinedError as exc:
            _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "my_missing_var" in out

    def test_undefined_hints_at_var_flag(self, sql_file, tmp_path, capsys):
        env = Environment(undefined=StrictUndefined)
        try:
            env.from_string("{{ x }}").render()
        except UndefinedError as exc:
            _print_template_error(sql_file, exc, tmp_path)
        out = capsys.readouterr().out
        assert "--var" in out


# ── executor error paths (no Snowflake required) ───────────────────────────────

class TestGetConnectionErrors:
    def test_missing_env_vars_raise_environment_error(self, monkeypatch):
        for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        from snowdump.executor import get_connection
        with pytest.raises(EnvironmentError, match="SNOWFLAKE_ACCOUNT"):
            get_connection()

    def test_error_message_mentions_all_missing_vars(self, monkeypatch):
        for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        from snowdump.executor import get_connection
        with pytest.raises(EnvironmentError) as exc_info:
            get_connection()
        msg = str(exc_info.value)
        assert "SNOWFLAKE_ACCOUNT" in msg
        assert "SNOWFLAKE_USER" in msg
        assert "SNOWFLAKE_PASSWORD" in msg

    def test_partial_missing_vars_listed(self, monkeypatch):
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "user")
        monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)

        from snowdump.executor import get_connection
        with pytest.raises(EnvironmentError, match="SNOWFLAKE_PASSWORD"):
            get_connection()
