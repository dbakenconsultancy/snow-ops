import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError, UndefinedError

from snow_ops import __version__
from snow_ops.audit import (
    AuditConfig,
    compute_checksum,
    ensure_audit_table,
    record_deployment,
    was_deployed,
)
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
        "--var",
        action="append",
        metavar="KEY=VALUE",
        help="Template variable passed to every SQL file (repeatable).",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Enable deployment audit tracking. Scripts are skipped if their rendered "
        "checksum was already recorded in the audit table.",
    )
    parser.add_argument(
        "--audit-schema",
        default="public",
        metavar="SCHEMA",
        help="Schema for the audit table (default: public). Requires --audit.",
    )
    parser.add_argument(
        "--audit-table",
        default="audit_log",
        metavar="TABLE",
        help="Name of the audit table (default: audit_log). Requires --audit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive prompts when the audit table schema needs migration. Requires --audit.",
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

    checksums: dict[Path, str] = (
        {sql_file: compute_checksum(sql) for sql_file, sql in rendered.items()}
        if args.audit
        else {}
    )

    if args.dry_run:
        for sql_file, sql in rendered.items():
            label = sql_file.relative_to(scripts_dir).as_posix()
            print(f"\n{'=' * 60}")
            print(f"-- {label}")
            if args.audit:
                print(f"-- checksum: {checksums[sql_file]}")
            print("=" * 60)
            print(sql)
        print("\nDry run complete — no statements executed.")
        return

    connection_name = args.connection or os.getenv("SNOWFLAKE_CONNECTION_NAME")

    print("\nConnecting to Snowflake ...")
    try:
        conn = get_connection(connection_name)
    except (EnvironmentError, RuntimeError) as exc:
        print(exc)
        sys.exit(1)

    cursor = None
    try:
        cursor = conn.cursor()

        audit_config: AuditConfig | None = None
        if args.audit:
            try:
                audit_config = AuditConfig(schema=args.audit_schema, table=args.audit_table)
                ensure_audit_table(cursor, audit_config, force=args.force)
            except ValueError as exc:
                print(f"Audit configuration error: {exc}")
                sys.exit(1)
            except RuntimeError as exc:
                print(str(exc))
                sys.exit(1)

        skipped = 0
        executed = 0
        for sql_file, sql in rendered.items():
            label = sql_file.relative_to(scripts_dir).as_posix()

            if audit_config is not None:
                checksum = checksums[sql_file]
                if was_deployed(cursor, audit_config, label, checksum):
                    print(f"\nSkipping   {label}  (already deployed)")
                    skipped += 1
                    continue

            statements = split_statements(sql)
            print(f"\nExecuting  {label}  ({len(statements)} statement(s))")
            execute_statements(cursor, statements)
            executed += 1

            if audit_config is not None:
                record_deployment(cursor, audit_config, label, checksum)

        conn.commit()
        parts = [f"{executed} file(s) executed"]
        if skipped:
            parts.append(f"{skipped} skipped (already deployed)")
        print(f"\n{', '.join(parts)}.")
    except Exception as exc:
        conn.rollback()
        print(f"\nExecution failed: {exc}")
        sys.exit(1)
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()
