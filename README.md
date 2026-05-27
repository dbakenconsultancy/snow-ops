# snow-ops

Render Jinja-templated SQL files and execute them against Snowflake.

SQL files live in `scripts/`. Reusable macros live in `modules/` (optional). Snowflake credentials come from environment variables or a `.env` file.

---

## Installation

```bash
git clone https://github.com/Dikootje/snow-ops
cd snow-ops
pip install -e .
```

**Requirements:** Python 3.10+

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
snow-ops [--dry-run] [--project-dir DIR] [--var KEY=VALUE ...] [SCRIPT ...]
```

| Flag | Description |
|------|-------------|
| `SCRIPT ...` | One or more filenames to run (relative to `scripts/`, `.sql` extension optional). Default: all `*.sql` files in `scripts/` and its subdirectories, sorted alphabetically by full path. |
| `--dry-run` | Render templates and print the resulting SQL. No Snowflake connection is made. |
| `--connection NAME` | Named connection from `connections.toml`. Overrides `SNOWFLAKE_CONNECTION_NAME` and individual `SNOWFLAKE_*` variables. |
| `--project-dir DIR` | Project root to use instead of the current directory. |
| `--var KEY=VALUE` | Pass a template variable. Repeatable. |
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

The Snowflake connector reads named connections from `~/.snowflake/connections.toml`:

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
