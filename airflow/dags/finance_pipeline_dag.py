"""
Airflow DAG for the finance pipeline.

This replaces the Makefile chain with a real orchestrator. The first four
steps are the exact same steps, in the exact same order, that
`make pipeline` runs --
the difference is what's running them: instead of you typing `make
pipeline` in a terminal, Airflow's scheduler runs this on a schedule,
retries failed tasks automatically, keeps a queryable history of every
run, and gives you a UI to see what happened and why. That's the concrete
answer to "why not just a script?" -- a script has none of that for free.

Task dependency graph (this is what "orchestration" means in practice):

    ingest_transactions -> dbt_seed -> dbt_run -> dbt_test
        -> spark_monthly_parity -> score_transactions

The last two tasks extend the core ELT chain:
  - spark_monthly_parity recomputes the monthly spending mart with PySpark
    from Parquet and fails if it disagrees with dbt's version (a
    cross-engine data-consistency check; see spark/monthly_spending_spark.py).
  - score_transactions runs the trained ML categorizer over every
    transaction and saves predictions + confidence to the warehouse (batch
    inference; see ml/score_transactions.py).
They run one after another rather than in parallel because both write to
the same DuckDB file, and DuckDB allows only one writer at a time.

Each arrow means "the task on the right only starts if the task on the
left succeeded." If dbt_run fails, dbt_test never runs -- there's no
point testing models that didn't build, and you don't want a half-broken
pipeline silently continuing.
"""

import logging
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# Where docker-compose.yaml mounts the repo root inside the container
# (see the `..:/opt/airflow/project` volume).
PROJECT_DIR = "/opt/airflow/project"
DBT_DIR = f"{PROJECT_DIR}/finance_dbt"


def alert_on_failure(context):
    """
    Data-quality / pipeline-failure alerting.

    This is intentionally left as a *logged* alert rather than wired to a
    real Slack webhook or SMTP server, because that would require secrets
    (a webhook URL, an app password) that don't belong committed to a
    public portfolio repo. What's here is the real pattern used in
    production: Airflow calls this function automatically, with full
    context about which task failed, whenever any task in the DAG fails
    -- including dbt_test, which is exactly the "a data quality check
    caught a real problem" case you want to be alerted about.

    To make this a real Slack alert: sign up for a Slack webhook URL,
    store it as an Airflow Variable or Connection (not hardcoded), and
    replace the logging.error call below with a `requests.post(...)` to
    that webhook using this same context.
    """
    task_instance = context["task_instance"]
    logging.error(
        "PIPELINE ALERT: task '%s' in DAG '%s' failed (run: %s). "
        "In production this would page/Slack/email someone -- see the "
        "alert_on_failure docstring in this DAG for how to wire that up.",
        task_instance.task_id,
        task_instance.dag_id,
        context["dag_run"].run_id,
    )


default_args = {
    "owner": "akshita",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": alert_on_failure,
}

with DAG(
    dag_id="finance_pipeline",
    description="Ingest bank transactions, transform with dbt, validate with dbt tests and a Spark parity check, then score with the ML categorizer.",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["finance", "dbt", "elt", "spark", "ml"],
) as dag:

    ingest_transactions = BashOperator(
        task_id="ingest_transactions",
        bash_command=f"cd {PROJECT_DIR} && python3 ingestion/load_to_duckdb.py",
    )

    dbt_seed = BashOperator(
        task_id="dbt_seed",
        bash_command=f"cd {DBT_DIR} && dbt seed",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd {DBT_DIR} && dbt run",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {DBT_DIR} && dbt test",
    )

    spark_monthly_parity = BashOperator(
        task_id="spark_monthly_parity",
        bash_command=f"cd {PROJECT_DIR} && python3 spark/monthly_spending_spark.py",
    )

    score_transactions = BashOperator(
        task_id="score_transactions",
        bash_command=f"cd {PROJECT_DIR} && python3 ml/score_transactions.py",
    )

    (
        ingest_transactions
        >> dbt_seed
        >> dbt_run
        >> dbt_test
        >> spark_monthly_parity
        >> score_transactions
    )
