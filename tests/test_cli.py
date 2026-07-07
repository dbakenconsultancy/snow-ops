"""Tests for cli.py — file discovery, var parsing, error formatting, main(), executor error handling."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, TemplateNotFound, TemplateSyntaxError, UndefinedError, StrictUndefined

from snow_ops.cli import _collect_sql_files, _parse_vars, _print_template_error, _resolve_connections_toml, main


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform")


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

    def test_dotdot_within_scripts_normalized(self, scripts):
        (scripts / "sub").mkdir()
        (scripts / "q.sql").write_text("SELECT 1")
        result = _collect_sql_files(scripts, ["sub/../q.sql"])
        assert result == [scripts / "q.sql"]

    def test_symlinked_scripts_dir_keeps_path_under_link(self, tmp_path):
        """Returned paths must stay under scripts_dir so callers can compute
        project-relative labels even when scripts/ is a symlink."""
        real = tmp_path / "real_scripts"
        real.mkdir()
        (real / "q.sql").write_text("SELECT 1")
        link = tmp_path / "scripts"
        _symlink_or_skip(link, real)
        result = _collect_sql_files(link, ["q"])
        assert result == [link / "q.sql"]


# ── _parse_vars ────────────────────────────────────────────────────────────────

class TestParseVars:
    def test_none_returns_empty_dict(self):
        assert _parse_vars(None) == {}

    def test_single_pair(self):
        assert _parse_vars(["env=prod"]) == {"env": "prod"}

    def test_multiple_pairs(self):
        assert _parse_vars(["a=1", "b=2"]) == {"a": "1", "b": "2"}

    def test_value_may_contain_equals(self):
        assert _parse_vars(["expr=a=b"]) == {"expr": "a=b"}

    def test_empty_value_allowed(self):
        assert _parse_vars(["key="]) == {"key": ""}

    def test_last_duplicate_wins(self):
        assert _parse_vars(["k=1", "k=2"]) == {"k": "2"}

    def test_missing_separator_exits(self):
        with pytest.raises(SystemExit):
            _parse_vars(["no_separator"])

    def test_empty_key_exits(self):
        with pytest.raises(SystemExit):
            _parse_vars(["=value"])


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

    def test_generic_template_error_falls_through(self, sql_file, tmp_path, capsys):
        from jinja2 import TemplateError
        _print_template_error(sql_file, TemplateError("boom"), tmp_path)
        out = capsys.readouterr().out
        assert "Template error" in out
        assert "boom" in out


# ── _print_connection_info ─────────────────────────────────────────────────────

class TestPrintConnectionInfo:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for suffix in ("ACCOUNT", "USER", "PASSWORD", "DATABASE", "SCHEMA", "WAREHOUSE", "ROLE"):
            monkeypatch.delenv(f"SNOWFLAKE_{suffix}", raising=False)

    def test_toml_mode_shows_file_and_connection_source(self, tmp_path, capsys):
        from snow_ops.cli import _print_connection_info
        toml = tmp_path / "connections.toml"
        _print_connection_info("dev", "--connection flag", tmp_path / ".env", set(), toml)
        out = capsys.readouterr().out
        assert "connections.toml" in out
        assert str(toml) in out
        assert "dev" in out
        assert "--connection flag" in out

    def test_env_mode_masks_password(self, tmp_path, monkeypatch, capsys):
        from snow_ops.cli import _print_connection_info
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2")
        _print_connection_info(None, "", tmp_path / ".env", set())
        out = capsys.readouterr().out
        assert "hunter2" not in out
        assert "***" in out

    def test_env_mode_distinguishes_os_env_from_dotenv(self, tmp_path, monkeypatch, capsys):
        from snow_ops.cli import _print_connection_info
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "alice")
        # ACCOUNT existed before load_dotenv, USER did not — so USER came from .env
        _print_connection_info(None, "", tmp_path / ".env", {"SNOWFLAKE_ACCOUNT"})
        out = capsys.readouterr().out
        assert "acct  (from OS environment)" in out
        assert "alice  (from .env)" in out

    def test_env_mode_reports_unset_vars_and_missing_dotenv(self, tmp_path, capsys):
        from snow_ops.cli import _print_connection_info
        _print_connection_info(None, "", tmp_path / ".env", set())
        out = capsys.readouterr().out
        assert "(not found)" in out
        assert "(not set)" in out

    def test_env_mode_shows_existing_dotenv_path(self, tmp_path, capsys):
        from snow_ops.cli import _print_connection_info
        dotenv = tmp_path / ".env"
        dotenv.write_text("")
        _print_connection_info(None, "", dotenv, set())
        out = capsys.readouterr().out
        assert str(dotenv) in out
        assert "(not found)" not in out


# ── _resolve_connections_toml ──────────────────────────────────────────────────

class TestResolveConnectionsToml:
    def test_explicit_path_returned_as_is(self, tmp_path):
        f = tmp_path / "my_connections.toml"
        f.write_text("")
        result = _resolve_connections_toml(f)
        assert result == f.resolve()

    def test_cwd_connections_toml_preferred_over_home(self, tmp_path, monkeypatch):
        cwd_file = tmp_path / "connections.toml"
        cwd_file.write_text("")
        monkeypatch.chdir(tmp_path)
        result = _resolve_connections_toml(None)
        assert result == cwd_file

    def test_falls_back_to_home_when_no_cwd_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # tmp_path has no connections.toml
        result = _resolve_connections_toml(None)
        from pathlib import Path
        assert result == Path.home() / ".snowflake" / "connections.toml"

    def test_explicit_path_takes_priority_over_cwd(self, tmp_path, monkeypatch):
        cwd_file = tmp_path / "connections.toml"
        cwd_file.write_text("")
        explicit = tmp_path / "other.toml"
        explicit.write_text("")
        monkeypatch.chdir(tmp_path)
        result = _resolve_connections_toml(explicit)
        assert result == explicit.resolve()


# ── executor error paths (no Snowflake required) ───────────────────────────────

class TestGetConnectionErrors:
    def test_missing_env_vars_raise_environment_error(self, monkeypatch):
        for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        from snow_ops.executor import get_connection
        with pytest.raises(EnvironmentError, match="SNOWFLAKE_ACCOUNT"):
            get_connection()

    def test_error_message_mentions_all_missing_vars(self, monkeypatch):
        for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        from snow_ops.executor import get_connection
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

        from snow_ops.executor import get_connection
        with pytest.raises(EnvironmentError, match="SNOWFLAKE_PASSWORD"):
            get_connection()


# ── main() ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "q.sql").write_text("SELECT {{ n | default('1') }};")
    return tmp_path


def _run_main(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["snow-ops", *argv])
    main()


class TestMainDryRun:
    def test_renders_and_prints_sql(self, project, monkeypatch, capsys):
        _run_main(monkeypatch, "--project-dir", str(project), "--dry-run")
        out = capsys.readouterr().out
        assert "SELECT 1;" in out
        assert "Dry run complete" in out

    def test_var_overrides_default(self, project, monkeypatch, capsys):
        _run_main(monkeypatch, "--project-dir", str(project), "--dry-run", "--var", "n=42")
        out = capsys.readouterr().out
        assert "SELECT 42;" in out

    def test_audit_flag_prints_checksum(self, project, monkeypatch, capsys):
        _run_main(monkeypatch, "--project-dir", str(project), "--dry-run", "--audit")
        out = capsys.readouterr().out
        assert "checksum:" in out

    def test_invalid_var_exits(self, project, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, "--project-dir", str(project), "--dry-run", "--var", "novalue")
        assert "Invalid --var entry" in capsys.readouterr().out

    def test_missing_scripts_dir_exits(self, tmp_path, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, "--project-dir", str(tmp_path), "--dry-run")
        assert "scripts/ directory not found" in capsys.readouterr().out

    def test_named_script_in_symlinked_scripts_dir(self, tmp_path, monkeypatch, capsys):
        """Regression: a symlinked scripts/ dir must not crash label computation."""
        real = tmp_path / "real_scripts"
        real.mkdir()
        (real / "q.sql").write_text("SELECT 1")
        proj = tmp_path / "proj"
        proj.mkdir()
        _symlink_or_skip(proj / "scripts", real)
        _run_main(monkeypatch, "--project-dir", str(proj), "--dry-run", "q.sql")
        out = capsys.readouterr().out
        assert "SELECT 1" in out
        assert "Dry run complete" in out


class TestMainExecution:
    @pytest.fixture
    def conn(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_CONNECTION_NAME", raising=False)
        conn = MagicMock()
        conn.cursor.return_value.rowcount = 1
        monkeypatch.setattr("snow_ops.cli.get_connection", lambda *a, **k: conn)
        return conn

    def test_happy_path_commits_and_closes(self, project, conn, monkeypatch, capsys):
        _run_main(monkeypatch, "--project-dir", str(project))
        out = capsys.readouterr().out
        assert "1 file(s) executed" in out
        conn.commit.assert_called_once()
        conn.cursor.return_value.close.assert_called_once()
        conn.close.assert_called_once()

    def test_execution_error_rolls_back_and_exits_1(self, project, conn, monkeypatch, capsys):
        monkeypatch.setattr(
            "snow_ops.cli.execute_statements", MagicMock(side_effect=RuntimeError("boom"))
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(project))
        assert exc_info.value.code == 1
        assert "Execution failed: boom" in capsys.readouterr().out
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_rollback_failure_does_not_mask_original_error(self, project, conn, monkeypatch, capsys):
        monkeypatch.setattr(
            "snow_ops.cli.execute_statements", MagicMock(side_effect=RuntimeError("boom"))
        )
        conn.rollback.side_effect = RuntimeError("connection already closed")
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(project))
        assert exc_info.value.code == 1
        assert "Execution failed: boom" in capsys.readouterr().out

    def test_close_failure_is_suppressed(self, project, conn, monkeypatch, capsys):
        conn.cursor.return_value.close.side_effect = RuntimeError("already closed")
        conn.close.side_effect = RuntimeError("already closed")
        _run_main(monkeypatch, "--project-dir", str(project))
        assert "1 file(s) executed" in capsys.readouterr().out

    def test_keyboard_interrupt_exits_130(self, project, conn, monkeypatch, capsys):
        monkeypatch.setattr(
            "snow_ops.cli.execute_statements", MagicMock(side_effect=KeyboardInterrupt)
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(project))
        assert exc_info.value.code == 130
        assert "Interrupted" in capsys.readouterr().out
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()


class TestMainInputErrors:
    def test_empty_scripts_dir_exits(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "scripts").mkdir()
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, "--project-dir", str(tmp_path), "--dry-run")
        assert "No .sql files found" in capsys.readouterr().out

    def test_template_error_exits_before_connecting(self, tmp_path, monkeypatch, capsys):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "q.sql").write_text("SELECT {{ undefined_variable }}")
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(tmp_path))
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "undefined_variable" in out
        assert "Connecting to Snowflake" not in out


class TestMainConnectionResolution:
    @pytest.fixture(autouse=True)
    def no_connection_name_env(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_CONNECTION_NAME", raising=False)

    def test_missing_connections_toml_exits(self, project, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_main(
                monkeypatch,
                "--project-dir", str(project),
                "--connection", "dev",
                "--connection-file-path", str(project / "nope.toml"),
            )
        assert exc_info.value.code == 1
        assert "connections.toml not found" in capsys.readouterr().out

    def test_named_connection_passed_to_get_connection(self, project, monkeypatch, capsys):
        toml = project / "connections.toml"
        toml.write_text("[dev]\n")
        conn = MagicMock()
        conn.cursor.return_value.rowcount = -1
        received = {}

        def fake_get_connection(name, toml_path):
            received["name"] = name
            received["toml_path"] = toml_path
            return conn

        monkeypatch.setattr("snow_ops.cli.get_connection", fake_get_connection)
        _run_main(
            monkeypatch,
            "--project-dir", str(project),
            "--connection", "dev",
            "--connection-file-path", str(toml),
        )
        assert received == {"name": "dev", "toml_path": toml.resolve()}
        out = capsys.readouterr().out
        assert "connections.toml" in out
        assert "1 file(s) executed" in out

    def test_get_connection_failure_exits_with_message(self, project, monkeypatch, capsys):
        monkeypatch.setattr(
            "snow_ops.cli.get_connection",
            MagicMock(side_effect=EnvironmentError("Missing required environment variables: SNOWFLAKE_ACCOUNT")),
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(project))
        assert exc_info.value.code == 1
        assert "Missing required environment variables" in capsys.readouterr().out


class TestMainAudit:
    ALL_COLUMNS = [
        ("script_name",), ("checksum",), ("executed_at",),
        ("executed_by_user",), ("executed_by_role",),
    ]

    @pytest.fixture
    def conn(self, monkeypatch):
        monkeypatch.delenv("SNOWFLAKE_CONNECTION_NAME", raising=False)
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.rowcount = 1
        cursor.fetchall.return_value = self.ALL_COLUMNS
        monkeypatch.setattr("snow_ops.cli.get_connection", lambda *a, **k: conn)
        return conn

    def _executed_sql(self, conn):
        return [call.args[0] for call in conn.cursor.return_value.execute.call_args_list]

    def test_new_script_executed_and_recorded(self, project, conn, monkeypatch, capsys):
        conn.cursor.return_value.fetchone.return_value = None
        _run_main(monkeypatch, "--project-dir", str(project), "--audit")
        assert "1 file(s) executed" in capsys.readouterr().out
        assert any("MERGE INTO" in sql for sql in self._executed_sql(conn))
        conn.commit.assert_called_once()

    def test_already_deployed_script_skipped(self, project, conn, monkeypatch, capsys):
        conn.cursor.return_value.fetchone.return_value = (1,)
        _run_main(monkeypatch, "--project-dir", str(project), "--audit")
        out = capsys.readouterr().out
        assert "Skipping" in out
        assert "0 file(s) executed, 1 skipped (already deployed)" in out
        assert not any("MERGE INTO" in sql for sql in self._executed_sql(conn))

    def test_invalid_audit_schema_exits(self, project, conn, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_main(
                monkeypatch, "--project-dir", str(project), "--audit", "--audit-schema", "bad-name"
            )
        assert exc_info.value.code == 1
        assert "Audit configuration error" in capsys.readouterr().out
        conn.close.assert_called_once()

    def test_migration_needed_without_force_exits_in_ci(self, project, conn, monkeypatch, capsys):
        # only one column present and stdin is not a tty (pytest capture) -> RuntimeError
        conn.cursor.return_value.fetchall.return_value = [("script_name",)]
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, "--project-dir", str(project), "--audit")
        assert exc_info.value.code == 1
        assert "--force" in capsys.readouterr().out

    def test_migration_applied_with_force(self, project, conn, monkeypatch, capsys):
        conn.cursor.return_value.fetchall.return_value = [("script_name",)]
        conn.cursor.return_value.fetchone.return_value = None
        _run_main(monkeypatch, "--project-dir", str(project), "--audit", "--force")
        assert "1 file(s) executed" in capsys.readouterr().out
        assert any("ALTER TABLE" in sql for sql in self._executed_sql(conn))
