"""Tests for renderer.py — Jinja env, render_file, split_statements, globals."""

import pytest
from pathlib import Path
from jinja2 import TemplateRuntimeError, UndefinedError

from snow_ops.renderer import build_env, render_file, split_statements


@pytest.fixture
def env(tmp_path):
    return build_env(tmp_path)


# ── build_env ──────────────────────────────────────────────────────────────────

class TestBuildEnv:
    def test_plain_sql_passes_through(self, env):
        assert env.from_string("SELECT 1").render() == "SELECT 1"

    def test_variable_substitution(self, env):
        result = env.from_string("SELECT {{ col }}").render(col="user_id")
        assert result == "SELECT user_id"

    def test_strict_undefined_raises(self, env):
        with pytest.raises(UndefinedError):
            env.from_string("{{ missing }}").render()

    def test_trailing_newline_preserved(self, env):
        result = env.from_string("SELECT 1\n").render()
        assert result.endswith("\n")


# ── env_var global ─────────────────────────────────────────────────────────────

class TestEnvVar:
    def test_reads_from_environment(self, env, monkeypatch):
        monkeypatch.setenv("SD_TEST_VAR", "hello")
        assert env.from_string("{{ env_var('SD_TEST_VAR') }}").render() == "hello"

    def test_returns_default_when_missing(self, env):
        result = env.from_string("{{ env_var('SD_DEFINITELY_ABSENT', 'fallback') }}").render()
        assert result == "fallback"

    def test_raises_when_missing_and_no_default(self, env):
        with pytest.raises(TemplateRuntimeError, match="SD_DEFINITELY_ABSENT"):
            env.from_string("{{ env_var('SD_DEFINITELY_ABSENT') }}").render()

    def test_error_message_mentions_dotenv(self, env):
        with pytest.raises(TemplateRuntimeError, match=r"\.env"):
            env.from_string("{{ env_var('SD_DEFINITELY_ABSENT') }}").render()


# ── render_file ────────────────────────────────────────────────────────────────

class TestRenderFile:
    @pytest.fixture
    def scripts(self, tmp_path):
        d = tmp_path / "scripts"
        d.mkdir()
        return d

    def test_renders_plain_sql(self, tmp_path, scripts):
        f = scripts / "q.sql"
        f.write_text("SELECT 1")
        result = render_file(f, tmp_path, {}, build_env(tmp_path))
        assert result == "SELECT 1"

    def test_injects_context(self, tmp_path, scripts):
        f = scripts / "q.sql"
        f.write_text("SELECT {{ n }}")
        result = render_file(f, tmp_path, {"n": "42"}, build_env(tmp_path))
        assert result == "SELECT 42"

    def test_template_name_uses_posix_separator(self, tmp_path, scripts):
        """render_file must use as_posix() so Jinja finds the file on Windows too."""
        f = scripts / "q.sql"
        f.write_text("ok")
        result = render_file(f, tmp_path, {}, build_env(tmp_path))
        assert result == "ok"


# ── split_statements ───────────────────────────────────────────────────────────

class TestSplitStatements:
    def test_single_statement(self):
        assert split_statements("SELECT 1") == ["SELECT 1"]

    def test_multiple_statements(self):
        assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_trailing_semicolon_not_empty(self):
        assert split_statements("SELECT 1;") == ["SELECT 1"]

    def test_whitespace_trimmed(self):
        stmts = split_statements("  SELECT 1  ;  SELECT 2  ")
        assert stmts == ["SELECT 1", "SELECT 2"]

    def test_empty_string_returns_empty(self):
        assert split_statements("") == []

    def test_semicolon_inside_string_not_split(self):
        sql = "SELECT 'foo;bar' AS col FROM t; SELECT 1"
        stmts = split_statements(sql)
        assert len(stmts) == 2
        assert "foo;bar" in stmts[0]

    def test_semicolon_inside_comment_not_split(self):
        sql = "SELECT 1 -- ends here; not a split\n; SELECT 2"
        stmts = split_statements(sql)
        assert len(stmts) == 2
