{% from 'modules/macros.sql' import ref, surrogate_key, limit_rows, date_filter %}
-- ============================================================
-- example.sql
-- Demonstrates Jinja macros and template variables.
-- Run with:
--   snow-ops --dry-run
--   snow-ops --dry-run --var start_date=2024-06-01 --var target_schema=ANALYTICS
-- ============================================================

CREATE OR REPLACE TABLE {{ target_schema | default('PUBLIC') }}.stg_events AS
SELECT
    {{ surrogate_key(['user_id', 'event_date', 'event_type']) }} AS event_sk,
    user_id,
    event_date,
    event_type,
    CURRENT_TIMESTAMP()                                           AS loaded_at
FROM {{ ref('raw_events') }}
WHERE {{ date_filter('event_date', start_date | default('2024-01-01')) }}
{{ limit_rows(1000) }};
