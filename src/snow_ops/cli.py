import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError, UndefinedError

from snow_ops import __version__
from snow_ops.executor import execute_statements, get_connection
from snow_ops.renderer import build_env, render_file, split_statements


def _print_template_error(sql_file: Path, exc: TemplateError, project_dir: Path) -> None:
    if isinstance(exc, TemplateNotFound):
        expected = project_dir / exc.name
        print(f"  Cannot load template: {exc.name}")
        print(f"  Expected at:  {expected}")
        if expected.exists():
            print("  File exists but cannot be read — check file permissions.")
        else:
            print("  File does not exist at that path.")
    elif isinstance(exc, TemplateSyntaxError):
        print(f"  Syntax error in {sql_file.name}, line {exc.lineno}: {exc.message}")
        if exc.source:
            lines = exc.source.splitlines()
            if exc.lineno and 0 < exc.lineno <= len(lines):
                print(f"  {lines[exc.lineno - 1]}")
                print(f"  {'~' * len(lines[exc.lineno - 1].rstrip())}")
    elif isinstance(exc, UndefinedError):
        print(f"  Undefined variable in {sql_file.name}: {exc}")
        print("  Hint: pass it with --var KEY=VALUE")
    else:
        print(f"  Template error in {sql_file.name}: {exc}")


def _collect_sql_files(scripts_dir: Path, names: list[str] | None) -> list[Path]:
    if names:
        files = []
        for name in names:
            p = scripts_dir / name
            if not p.suffix:
                p = p.with_suffix(".sql")
            p = p.resolve()
            if not p.is_relative_to(scripts_dir.resolve()):
                print(f"Path escapes scripts directory: {name!r}")
                sys.exit(1)
            if not p.is_file():
                print(f"File not found: {p}")
                sys.exit(1)
            files.append(p)
        return files
    return sorted(scripts_dir.rglob("*.sql"))


def _resolve_connections_toml(connection_file_path: Path | None) -> Path:
    if connection_file_path is not None:
        return connection_file_path.resolve()
    cwd_candidate = Path.cwd() / "connections.toml"
    if cwd_candidate.is_file():
        return cwd_candidate
    return Path.home() / ".snowflake" / "connections.toml"


def _print_connection_info(
    connection_name: str | None,
    connection_source: str,
    dotenv_file: Path,
    pre_dotenv_keys: set[str],
    toml_path: Path | None = None,
) -> None:
    if connection_name:
        print(f"  Source:      connections.toml")
        print(f"  Config file: {toml_path}")
        print(f"  Connection:  {connection_name}  (from {connection_source})")
    else:
        print("  Source:      environment variables")
        if dotenv_file.is_file():
            print(f"  .env file:   {dotenv_file}")
        else:
            print(f"  .env file:   {dotenv_file}  (not found)")

        for suffix in ("ACCOUNT", "USER", "PASSWORD", "DATABASE", "SCHEMA", "WAREHOUSE", "ROLE"):
            key = f"SNOWFLAKE_{suffix}"
            val = os.getenv(key)
            label = suffix.capitalize()
            if val:
                src = "OS environment" if key in pre_dotenv_keys else ".env"
                display = "***" if suffix == "PASSWORD" else val
                print(f"  {label:<10} {display}  (from {src})")
            else:
                print(f"  {label:<10} (not set)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="snow-ops",
        description="Render Jinja-templated SQL files and execute them on Snowflake.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rendered SQL without connecting to Snowflake.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project root containing scripts/ and modules/ (default: current directory).",
    )
    parser.add_argument(
        "--connection",
        metavar="NAME",
        help="Named connection from connections.toml. "
        "Overrides SNOWFLAKE_CONNECTION_NAME and individual SNOWFLAKE_* variables.",
    )
    parser.add_argument(
        "--connection-file-path",
        type=Path,
        metavar="FILE",
        help="Path to connections.toml. "
        "Defaults to connections.toml in the current directory, "
        "then ~/.snowflake/connections.toml.",
    )
    parser.add_argument(
        "--var",
        action="append",
        metavar="KEY=VALUE",
        help="Template variable passed to every SQL file (repeatable).",
    )
    parser.add_argument(
        "scripts",
        nargs="*",
        metavar="SCRIPT",
        help="SQL files to run (default: all *.sql in scripts/). "
        "Names relative to scripts/, .sql extension optional.",
    )
    args = parser.parse_args()

    project_dir: Path = args.project_dir.resolve()
    pre_dotenv_keys: set[str] = set(os.environ.keys())
    load_dotenv(project_dir / ".env")
    scripts_dir = project_dir / "scripts"

    if not scripts_dir.is_dir():
        print(f"scripts/ directory not found under {project_dir}")
        sys.exit(1)

    context: dict[str, str] = {}
    for entry in args.var or []:
        key, sep, value = entry.partition("=")
        if not sep or not key:
            print(f"Invalid --var entry: {entry!r} (expected KEY=VALUE)")
            sys.exit(1)
        context[key] = value

    sql_files = _collect_sql_files(scripts_dir, args.scripts or None)
    if not sql_files:
        print(f"No .sql files found in {scripts_dir}")
        sys.exit(1)

    # Render all files before touching Snowflake — fail fast on template errors
    env = build_env(project_dir)
    rendered: dict[Path, str] = {}
    for sql_file in sql_files:
        label = sql_file.relative_to(scripts_dir).as_posix()
        print(f"Rendering  {label} ...")
        try:
            rendered[sql_file] = render_file(sql_file, project_dir, context, env)
        except TemplateError as exc:
            _print_template_error(sql_file, exc, project_dir)
            sys.exit(1)

    if args.dry_run:
        for sql_file, sql in rendered.items():
            label = sql_file.relative_to(scripts_dir).as_posix()
            print(f"\n{'=' * 60}")
            print(f"-- {label}")
            print("=" * 60)
            print(sql)
        print("\nDry run complete — no statements executed.")
        return

    connection_name = args.connection or os.getenv("SNOWFLAKE_CONNECTION_NAME")
    connection_source = "--connection flag" if args.connection else "SNOWFLAKE_CONNECTION_NAME"
    if connection_name:
        toml_path = _resolve_connections_toml(args.connection_file_path)
        if not toml_path.is_file():
            print(f"connections.toml not found: {toml_path}")
            sys.exit(1)
    else:
        toml_path = None

    print("\nConnecting to Snowflake ...")
    _print_connection_info(connection_name, connection_source, project_dir / ".env", pre_dotenv_keys, toml_path)
    try:
        conn = get_connection(connection_name, toml_path)
    except (EnvironmentError, RuntimeError) as exc:
        print(exc)
        sys.exit(1)

    cursor = None
    try:
        cursor = conn.cursor()
        for sql_file, sql in rendered.items():
            statements = split_statements(sql)
            label = sql_file.relative_to(scripts_dir).as_posix()
            print(f"\nExecuting  {label}  ({len(statements)} statement(s))")
            execute_statements(cursor, statements)
        conn.commit()
        print("\nAll files executed successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"\nExecution failed: {exc}")
        sys.exit(1)
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()
