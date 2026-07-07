import os
from pathlib import Path


def get_connection(connection_name: str | None = None, connections_file: Path | None = None):
    """Return a Snowflake connection.

    If connection_name is given, the named connection is loaded from
    connections.toml. Resolution order: connections_file argument, then
    connections.toml in the current directory, then ~/.snowflake/connections.toml.

    Otherwise, credentials are read from environment variables:
      Required: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD
      Optional: SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE

    Raises EnvironmentError if required variables are missing.
    Raises RuntimeError if the connector package is not installed.
    """
    try:
        import snowflake.connector  # deferred to keep module-load time fast
    except ImportError as exc:
        raise RuntimeError(
            "Snowflake support requires the 'snowflake-connector-python' package. "
            "Install it and rerun the command."
        ) from exc

    if connection_name:
        return snowflake.connector.connect(
            connection_name=connection_name,
            connections_file_path=connections_file,
        )

    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your credentials, "
            "or use --connection to reference a connections.toml entry."
        )

    params: dict = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "password": os.environ["SNOWFLAKE_PASSWORD"],
    }
    for key in ("database", "schema", "warehouse", "role"):
        if val := os.getenv(f"SNOWFLAKE_{key.upper()}"):
            params[key] = val

    return snowflake.connector.connect(**params)


def execute_statements(cursor, statements: list[str]) -> None:
    total = len(statements)
    for i, stmt in enumerate(statements, 1):
        preview = stmt[:80].replace("\n", " ")
        ellipsis = "..." if len(stmt) > 80 else ""
        print(f"    [{i}/{total}] {preview}{ellipsis}")
        cursor.execute(stmt)
        if cursor.rowcount is not None and cursor.rowcount >= 0:
            print(f"           rows affected: {cursor.rowcount}")
