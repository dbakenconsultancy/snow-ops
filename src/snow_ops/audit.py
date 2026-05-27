import hashlib
import re
import sys
from dataclasses import dataclass


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_REQUIRED_COLUMNS = {"script_name", "checksum", "executed_at"}

_COLUMN_TYPES = {
    "script_name": "VARCHAR",
    "checksum": "VARCHAR",
    "executed_at": "TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()",
}


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
        cursor.execute(
            f"CREATE TABLE {config.schema}.{config.table} ("
            "script_name  VARCHAR NOT NULL, "
            "checksum     VARCHAR NOT NULL, "
            "executed_at  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(), "
            "PRIMARY KEY (script_name, checksum)"
            ")"
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

    if not (sys.stdin is not None and sys.stdin.isatty()):
        raise RuntimeError(
            f"Audit table {full_name} is missing required columns: {missing_list}. "
            "Re-run with --force to add them automatically."
        )

    print(f"Audit table {full_name} is missing required columns: {missing_list}")
    print("Proceeding will ALTER the table to add missing columns, which may affect existing data.")
    answer = input("Proceed? [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        _alter_add_columns(cursor, config, missing)
    else:
        raise RuntimeError("Audit table migration declined. Re-run with --force to skip this prompt.")


def _alter_add_columns(cursor, config: AuditConfig, missing: set) -> None:
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
        "USING (SELECT %s AS script_name, %s AS checksum) AS source "
        "ON target.script_name = source.script_name AND target.checksum = source.checksum "
        "WHEN NOT MATCHED THEN INSERT (script_name, checksum) "
        "VALUES (source.script_name, source.checksum)",
        (script_name, checksum),
    )
