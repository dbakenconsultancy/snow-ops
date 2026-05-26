{#-
  modules/macros.sql
  Reusable Jinja macros for SQL templates.
  Import with: {% from 'modules/macros.sql' import <macro_name> %}
-#}

{#- Wraps a table reference; swap in environment-specific schemas here -#}
{%- macro ref(table_name, schema=none, database=none) -%}
    {%- if database and not schema -%}
        {{- raise("ref('" ~ table_name ~ "'): schema is required when database is provided") -}}
    {%- elif database and schema -%}{{ database }}.{{ schema }}.{{ table_name }}
    {%- elif schema -%}{{ schema }}.{{ table_name }}
    {%- else -%}{{ table_name }}
    {%- endif -%}
{%- endmacro -%}


{#- Generates a deterministic surrogate key from a list of column names -#}
{%- macro surrogate_key(fields) -%}
    MD5(
        CONCAT_WS('|',
            {%- for f in fields %}
            COALESCE(CAST({{ f }} AS STRING), '')
            {%- if not loop.last %},{% endif %}
            {%- endfor %}
        )
    )
{%- endmacro -%}


{#- Adds a LIMIT clause; useful during development -#}
{%- macro limit_rows(n=100) -%}
LIMIT {{ n }}
{%- endmacro -%}


{#- Renders a comma-separated column list from a sequence -#}
{%- macro column_list(columns) -%}
    {%- for col in columns %}
    {{ col }}{% if not loop.last %},{% endif %}
    {%- endfor %}
{%- endmacro -%}


{#- Date spine helper: filters a column to a date range -#}
{%- macro date_filter(col, start, end=none) -%}
    {{ col }} >= '{{ start }}'
    {%- if end %} AND {{ col }} < '{{ end }}'{% endif %}
{%- endmacro -%}
