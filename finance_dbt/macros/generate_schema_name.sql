{#
  By default, dbt prepends your target's default schema to any custom
  schema you set (schema: marts -> ends up as "main_marts" for the
  DuckDB "main" target schema). That's a sensible default for teams
  sharing one warehouse across many dbt projects, but for this project
  it just makes the schema names in the dashboard's SQL not match reality.

  This is dbt's own documented override pattern: use the custom schema
  name exactly as configured in dbt_project.yml (staging/intermediate/
  marts/seeds), with no prefix. See:
  https://docs.getdbt.com/docs/build/custom-schemas
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}

        {{ default_schema }}

    {%- else -%}

        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}