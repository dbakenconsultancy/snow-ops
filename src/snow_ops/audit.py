import hashlib
import re
import sys
from dataclasses import dataclass


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Single source of truth for the audit table layout. NOT NULL applies only at
# CREATE time — ALTER TABLE ADD COLUMN cannot add NOT NULL columns to a table
# that already holds rows.
_COLUMN_TYPES = {
    "script_name": "VARCHAR",
    "checksum": "VARCHAR",
    "executed_at": "TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()",
    "executed_by_user": "VARCHAR",
    "executed_by_role": "VARCHAR",
}

_NOT_NULL_COLUMNS = frozenset({"script_name", "checksum"})

_REQUIRED_COLUMNS = frozenset(_COLUMN_TYPES)


def _validate_identifier(name: str, label: str) -> None:
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid {label} {name!r}: only letters, digits, and underscores are allowed."
        )


@dataclass
class AuditConfig:
    schema: str = "public"
    table: str = "audit_log"

    def __post_init__(self) -> None:
        _validate_identifier(self.schema, "schema")
        _validate_identifier(self.table, "table")


def compute_checksum(rendered_sql: str) -> str:
    return hashlib.sha256(rendered_sql.encode("utf-8")).hexdigest()


def ensure_audit_table(cursor, config: AuditConfig, force: bool = False) -> None:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (config.schema.upper(), config.table.upper()),
    )
    rows = cursor.fetchall()

    if not rows:
        columns = ", ".join(
            f"{name} {col_type}" + (" NOT NULL" if name in _NOT_NULL_COLUMNS else "")
            for name, col_type in _COLUMN_TYPES.items()
        )
        cursor.execute(
            f"CREATE TABLE {config.schema}.{config.table} "
            f"({columns}, PRIMARY KEY (script_name, checksum))"
        )
        return

    existing = {row[0].lower() for row in rows}
    missing = _REQUIRED_COLUMNS - existing

    if not missing:
        return

    missing_list = ", ".join(sorted(missing))
    full_name = f"{config.schema}.{config.table}"

    if force:
        _alter_add_columns(cursor, config, missing)
        return

    if sys.stdin is None or not sys.stdin.isatty():
        raise RuntimeError(
            f"Audit table {full_name} is missing required columns: {missing_list}. "
            "Re-run with --force to add them automatically."
        )

    print(f"Audit table {full_name} is missing required columns: {missing_list}")
    print("Proceeding will ALTER the table to add missing columns, which may affect existing data.")
    try:
        answer = input("Proceed? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("y", "yes"):
        _alter_add_columns(cursor, config, missing)
    else:
        raise RuntimeError("Audit table migration declined. Re-run with --force to skip this prompt.")


def _alter_add_columns(cursor, config: AuditConfig, missing: set[str]) -> None:
    for col in sorted(missing):
        cursor.execute(
            f"ALTER TABLE {config.schema}.{config.table} ADD COLUMN {col} {_COLUMN_TYPES[col]}"
        )


def was_deployed(cursor, config: AuditConfig, script_name: str, checksum: str) -> bool:
    cursor.execute(
        f"SELECT 1 FROM {config.schema}.{config.table} "
        "WHERE script_name = %s AND checksum = %s LIMIT 1",
        (script_name, checksum),
    )
    return cursor.fetchone() is not None


def record_deployment(cursor, config: AuditConfig, script_name: str, checksum: str) -> None:
    cursor.execute(
        f"MERGE INTO {config.schema}.{config.table} AS target "
        "USING (SELECT %s AS script_name, %s AS checksum, "
        "CURRENT_USER() AS executed_by_user, CURRENT_ROLE() AS executed_by_role) AS source "
        "ON target.script_name = source.script_name AND target.checksum = source.checksum "
        "WHEN NOT MATCHED THEN INSERT (script_name, checksum, executed_by_user, executed_by_role) "
        "VALUES (source.script_name, source.checksum, source.executed_by_user, source.executed_by_role)",
        (script_name, checksum),
    )
