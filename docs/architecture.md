# Architecture

## Data flow

```
 data/raw/*.csv                 raw layer                 dbt: staging            dbt: intermediate         dbt: marts                 dashboard
 (bank export,          →   DuckDB raw.transactions  →   stg_transactions   →   int_transactions_   →   dim_category           →   Streamlit
  or generated               (ingestion/                 (rename/type,          categorized             dim_merchant               (reads marts
  sample data)                load_to_duckdb.py))         surrogate key)         (categorize, clean     fct_transactions            only, no
                                                                                   merchant name,         mart_monthly_spending       aggregation
                                                                                   flag recurring)        mart_budget_vs_actual       in app code)
```

Each arrow is a real, separate, inspectable step. That's the point of an
ELT pipeline over "one big script": if a number on the dashboard looks
wrong, you can query any layer directly in DuckDB and see exactly where
it diverges from your expectation, instead of re-reading 200 lines of
pandas to find the bug.

## Why each layer exists

**Raw (`raw.transactions`)** — a near-literal copy of whatever CSV you
dropped in, plus lineage columns (`_source_file`, `_loaded_at`). No
business logic. This is your safety net: if you ever discover a bug in
the categorization rules, you can fix the dbt model and rerun — you never
need to re-ingest, because raw never threw anything away.

**Staging (`stg_transactions`)** — one dbt model per source table.
Renames columns to consistent snake_case, fixes types, generates a
surrogate `transaction_id` (bank exports don't give you one), and turns
blank strings into real `NULL`s. Still one row per transaction — no
aggregation, no business rules.

**Intermediate (`int_transactions_categorized`)** — where the actual
business logic lives: the rule-based `categorize_transaction` macro
assigns a clean category, merchant names get normalized (`TRADER JOE'S
#112` → `TRADER JOE'S`), and a window function flags likely recurring
charges. This is the layer you'd point to if someone asked "where does
the categorization actually happen?"

**Marts (`dim_category`, `dim_merchant`, `fct_transactions`,
`mart_monthly_spending`, `mart_budget_vs_actual`)** — a small star schema
(fact table + dimension tables) plus two reporting tables built
specifically to answer the two questions this project exists to answer:
"where did my money go?" and "am I over budget?" Marts are materialized
as real tables (not views), so the dashboard queries pre-computed numbers
instead of re-running the whole chain on every page load.

**Dashboard (`dashboard/app.py`)** — Streamlit, reads only from marts.
Zero aggregation logic in the app itself — if you ever swap Streamlit for
something else (a BI tool, a different frontend), the marts underneath it
don't change.

## Why DuckDB instead of a "real" cloud warehouse

DuckDB is a real OLAP (columnar, analytics-oriented) SQL database — the
same category of engine as Snowflake, BigQuery, or Redshift — but it runs
embedded, as a single file on disk, with no server, no account, and no
cost. For a project anyone should be able to clone and run in five
minutes, that tradeoff is the right one: the SQL you
write against DuckDB is the same SQL (and the same dbt models) you'd
write against Snowflake, so nothing here is a "toy" version of the real
skill. Pointing this same dbt project at a real cloud warehouse is a
config change in `profiles.yml`, not a rewrite.

## Design decisions

- **Idempotent ingestion.** Rerunning `load_to_duckdb.py` on the same
  file never duplicates rows (delete-then-insert per source file). Real
  pipelines get rerun after failures constantly; a pipeline that can't be
  safely rerun is a liability.
- **Rules over ML for categorization, on purpose.** A `CASE WHEN`
  keyword-matching macro is simple, transparent, and gets this dataset
  right ~100% of the time (validated — see the repo's data). It has a
  real, known failure mode (unseen merchants, mixed-use merchants like
  CVS), which the trained text classifier in `ml/` is there to cover.
- **Config-as-data for budgets.** Your budget targets live in a dbt
  *seed* (`seeds/category_budgets.csv`), not hardcoded in SQL. Changing
  your budget is editing a CSV, not a code change.
- **Tests, not vibes.** Every model has `not_null`/`unique`/
  `accepted_values`/`relationships` tests. `dbt test` is part of the
  pipeline (`make pipeline`), not an afterthought — a broken model fails
  the run instead of silently shipping bad numbers to the dashboard.

## After the marts: Spark parity check and model scoring

Two more steps run after `dbt test` (Airflow DAG, CI, and `make
spark-parity` / `make score`):

```
 DuckDB marts + intermediate  →  Parquet (warehouse/spark_input/)  →  PySpark (local[*])
                                                                     recompute monthly mart
                                                                     compare with dbt → fail on any difference
                                                                     → spark.monthly_spending

 intermediate transactions    →  saved scikit-learn model  →  ml.transaction_predictions
                                 (ml/model/categorizer.joblib)  (prediction, confidence, needs_review)
```

They run one after another, not in parallel, because both write to the
same DuckDB file and DuckDB allows one writer at a time.
