[![CI](https://github.com/dbakenconsultancy/snow-ops/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dbakenconsultancy/snow-ops/actions/workflows/ci.yml)

# snow-ops

Render Jinja-templated SQL files and execute them against Snowflake.

SQL files live in `scripts/`. Reusable macros live in `modules/` (optional). Snowflake credentials come from environment variables or a `.env` file.

This project is inspired by Snowflake Labs tools such as [schemachange](https://github.com/snowflake-labs/schemachange) and [dlsync](https://github.com/snowflake-labs/dlsync). We wanted a tool that combines the strengths of both. For projects that rely heavily on macros, dlsync can feel too granular, while schemachange provides powerful templating but is tightly coupled to migration-style workflows.

That is where snow-ops comes in: the Jinja templating power of schemachange without the constraint of forced migrations.

---

## Installation

```bash
pip install snow-ops
```

**Requirements:** Python 3.10+

### Install from source

```bash
git clone https://github.com/dbakenconsultancy/snow-ops
cd snow-ops
pip install -e .
```

---

## Quick start

```bash
# Copy and fill in credentials
cp .env.example .env

# Preview rendered SQL without connecting
snow-ops --dry-run

# Run all scripts against Snowflake
snow-ops

# Run a specific script
snow-ops my_query.sql
```

---

## Project layout

```
my_project/
├── .env                        # Snowflake credentials (git-ignored)
├── scripts/                    # SQL files to execute
│   ├── 01_setup/
│   │   └── create_tables.sql
│   ├── 02_load/
│   │   └── load_events.sql
│   └── my_query.sql
└── modules/                    # Jinja macros (optional)
    └── macros.sql
```

`scripts/` is the only required folder. `modules/` is optional and can contain zero or more `.sql` files with reusable macros.

Files are discovered recursively across all subdirectories of `scripts/` and executed in alphabetical order of their full path. Naming subdirectories with a numeric prefix (e.g. `01_setup/`, `02_load/`) is a simple way to control execution order.

Run `snow-ops` from your project root, or point to it with `--project-dir`.

---

## CLI reference

```
snow-ops [--dry-run] [--project-dir DIR] [--connection NAME] [--connection-file-path FILE]
         [--var KEY=VALUE ...] [--audit] [--audit-schema SCHEMA] [--audit-table TABLE] [--force]
         [SCRIPT ...]
```

| Flag | Description |
|------|-------------|
| `SCRIPT ...` | One or more filenames to run (relative to `scripts/`, `.sql` extension optional). Default: all `*.sql` files in `scripts/` and its subdirectories, sorted alphabetically by full path. |
| `--dry-run` | Render templates and print the resulting SQL. No Snowflake connection is made. |
| `--connection NAME` | Named connection from `connections.toml`. Overrides `SNOWFLAKE_CONNECTION_NAME` and individual `SNOWFLAKE_*` variables. |
| `--connection-file-path FILE` | Explicit path to `connections.toml`. Overrides the default lookup order (current directory, then `~/.snowflake/`). |
| `--project-dir DIR` | Project root to use instead of the current directory. |
| `--var KEY=VALUE` | Pass a template variable. Repeatable. |
| `--audit` | Enable deployment audit tracking. See [Deployment audit](#deployment-audit). |
| `--audit-schema SCHEMA` | Schema for the audit table (default: `public`). Requires `--audit`. |
| `--audit-table TABLE` | Name of the audit table (default: `audit_log`). Requires `--audit`. |
| `--force` | Skip the interactive confirmation prompt when the audit table schema needs to be updated. Requires `--audit`. |
| `--version` | Print the installed version and exit. |

### Examples

```bash
# Render all scripts and print them
snow-ops --dry-run

# Run two specific scripts
snow-ops load_dim.sql load_fact.sql

# Pass template variables
snow-ops --var env=prod --var run_date=2024-06-01

# Run from a different project directory
snow-ops --project-dir /path/to/project --dry-run
```

---

## Deployment audit

Pass `--audit` to enable idempotent deployments. snow-ops computes a SHA-256 checksum of each **rendered** script and records the `(script_name, checksum)` pair in a Snowflake table after successful execution. On the next run, any script whose name and checksum are already recorded is skipped.

```bash
# First run — all scripts execute and are recorded
snow-ops --audit

# Second run — all scripts are skipped
snow-ops --audit
# Skipping   01_setup/create_tables.sql  (already deployed)
# Skipping   02_load/load_events.sql     (already deployed)
# 0 file(s) executed, 2 skipped (already deployed).
```

Changing a script's content produces a new checksum, so the updated version is executed on the next run even if the old version was already recorded.

The checksum is computed on the fully-rendered SQL (after Jinja processing, before statement splitting), so different `--var` values for the same file produce different checksums and are treated as distinct deployments.

### Audit table

The audit table is created automatically the first time `--audit` is used. Its default location is `public.audit_log`, configurable via `--audit-schema` and `--audit-table`:

```bash
snow-ops --audit --audit-schema myschema --audit-table deploy_log
```

Schema created on first use:

```sql
CREATE TABLE public.audit_log (
    script_name      VARCHAR NOT NULL,
    checksum         VARCHAR NOT NULL,
    executed_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    executed_by_user VARCHAR,
    executed_by_role VARCHAR,
    PRIMARY KEY (script_name, checksum)
)
```

### Schema migration

If the audit table already exists but is missing required columns, snow-ops will:

- **In an interactive terminal** — print the missing columns and ask for confirmation before issuing `ALTER TABLE ADD COLUMN`.
- **In a non-interactive environment (CI/CD)** — print an error and exit with a non-zero status. Re-run with `--force` to apply the changes without a prompt.

```bash
snow-ops --audit --force
```

---

## Jinja templating

Every `.sql` file in `scripts/` is rendered as a Jinja template before execution. This means you can use variables, conditionals, loops, and macros.

### Template variables

Pass variables at runtime with `--var`:

```sql
SELECT *
FROM {{ target_schema }}.orders
WHERE order_date >= '{{ run_date }}'
```

```bash
snow-ops --var target_schema=ANALYTICS --var run_date=2024-01-01
```

Use `| default(...)` to make a variable optional:

```sql
FROM {{ schema | default('PUBLIC') }}.orders
```

### Environment variables

Use `env_var()` to read from the OS environment or `.env` file. This keeps runtime secrets and config separate from script-level parameters:

```sql
-- Required — fails with a clear error if not set
FROM {{ env_var('TARGET_DATABASE') }}.{{ env_var('TARGET_SCHEMA') }}.orders

-- Optional — falls back to a default
FROM {{ env_var('TARGET_SCHEMA', 'PUBLIC') }}.orders
```

`env_var()` reads from environment variables loaded at startup (including your `.env` file). It raises a descriptive error if the variable is missing and no default is given.

### Importing macros

Place reusable macros in any `.sql` file inside `modules/` and import them at the top of your script:

```sql
{% from 'modules/macros.sql' import ref, surrogate_key %}

SELECT
    {{ surrogate_key(['user_id', 'order_date']) }} AS order_sk,
    user_id,
    order_date
FROM {{ ref('raw_orders', schema='RAW') }}
```

---

## Built-in macros

The included `modules/macros.sql` provides a set of ready-to-use macros. Import only what you need.

### `ref(table_name, schema=none, database=none)`

Generates a qualified table reference. Useful for swapping schemas between environments.

```sql
{{ ref('orders') }}                                  -- orders
{{ ref('orders', schema='RAW') }}                    -- RAW.orders
{{ ref('orders', schema='RAW', database='PROD') }}   -- PROD.RAW.orders
```

Raises a template error if `database` is given without `schema`.

---

### `surrogate_key(fields)`

Generates a deterministic MD5 surrogate key from a list of column names. Nulls are coerced to empty string before hashing.

```sql
{{ surrogate_key(['user_id', 'event_date', 'event_type']) }}
```

Renders as:

```sql
MD5(
    CONCAT_WS('|',
        COALESCE(CAST(user_id AS STRING), ''),
        COALESCE(CAST(event_date AS STRING), ''),
        COALESCE(CAST(event_type AS STRING), '')
    )
)
```

---

### `date_filter(col, start, end=none)`

Renders a date range predicate. The end date is exclusive.

```sql
WHERE {{ date_filter('order_date', '2024-01-01') }}
-- order_date >= '2024-01-01'

WHERE {{ date_filter('order_date', '2024-01-01', '2025-01-01') }}
-- order_date >= '2024-01-01' AND order_date < '2025-01-01'
```

---

### `limit_rows(n=100)`

Appends a `LIMIT` clause. Handy during development to avoid full-table scans.

```sql
{{ limit_rows() }}       -- LIMIT 100
{{ limit_rows(1000) }}   -- LIMIT 1000
```

---

### `column_list(columns)`

Renders a comma-separated column list from a sequence.

```sql
SELECT {{ column_list(['user_id', 'email', 'created_at']) }}
-- user_id,
-- email,
-- created_at
```

---

## Snowflake credentials

Two ways to authenticate — pick one.

### Option A — connections.toml (recommended)

Create a `connections.toml` file with one section per named connection:

```toml
[my_connection]
account = "xy12345.us-east-1"
user = "alice"
password = "secret"
database = "ANALYTICS"
schema = "PUBLIC"
warehouse = "COMPUTE_WH"
role = "TRANSFORMER"
```

Pass the connection name at runtime:

```bash
snow-ops --connection my_connection
```

Or set it once in `.env` so you never have to type it:

```bash
SNOWFLAKE_CONNECTION_NAME=my_connection
```

`--connection` takes priority over `SNOWFLAKE_CONNECTION_NAME`, which takes priority over individual variables.

#### connections.toml lookup order

When `--connection` (or `SNOWFLAKE_CONNECTION_NAME`) is used, snow-ops looks for `connections.toml` in this order:

1. The path given by `--connection-file-path` (if provided)
2. `connections.toml` in the **current working directory**
3. `~/.snowflake/connections.toml`

To use a project-local file, place `connections.toml` alongside your scripts and run snow-ops from that directory. To share a single file across projects, put it in `~/.snowflake/`.

```bash
# Use a specific file
snow-ops --connection my_connection --connection-file-path /etc/snowflake/connections.toml

# Pick up connections.toml from the current directory automatically
snow-ops --connection my_connection
```

### Option B — environment variables

Copy `.env.example` to `.env` and fill in your values — it is loaded automatically from the project directory.

```bash
cp .env.example .env
```

**Required**

| Variable | Description |
|----------|-------------|
| `SNOWFLAKE_ACCOUNT` | Account identifier (e.g. `xy12345.us-east-1`) |
| `SNOWFLAKE_USER` | Username |
| `SNOWFLAKE_PASSWORD` | Password |

**Optional**

| Variable | Description |
|----------|-------------|
| `SNOWFLAKE_DATABASE` | Default database |
| `SNOWFLAKE_SCHEMA` | Default schema |
| `SNOWFLAKE_WAREHOUSE` | Compute warehouse |
| `SNOWFLAKE_ROLE` | Role to assume |

---

Credentials are only needed for live execution. `--dry-run` works without them.

---

## Writing your own macros

Add a `.sql` file to `modules/` and define macros using standard Jinja2 syntax:

```sql
{%- macro my_macro(arg1, arg2='default') -%}
    -- your SQL fragment here
    {{ arg1 }} = '{{ arg2 }}'
{%- endmacro -%}
```

Import it in any script:

```sql
{% from 'modules/my_macros.sql' import my_macro %}

SELECT *
FROM orders
WHERE {{ my_macro('status', 'shipped') }}
```

The `modules/` folder is optional. Scripts that do not import from it work without it.

---

## Releasing (maintainers)

Releases are published to [PyPI](https://pypi.org/project/snow-ops/) automatically via GitHub Actions when a GitHub Release is created.

### Cutting a release

```bash
# 1. Bump the version in pyproject.toml
#    e.g. version = "0.2.0"

# 2. Commit and push
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push origin main

# 3. Create and push a tag
git tag v0.2.0
git push origin v0.2.0

# 4. On GitHub: Releases → Draft a new release
#    - Choose the tag you just pushed
#    - Write release notes
#    - Click "Publish release"
```

Publishing the GitHub Release triggers the `publish.yml` workflow, which builds the wheel and sdist, then uploads them to PyPI via OIDC (no token required).
