# Extends the official Airflow image with just what this project's DAG
# needs (duckdb, dbt-duckdb, pandas). Built with the *repo root* as the
# Docker build context (see docker-compose.yaml's `context: ..`) so it can
# see airflow/requirements.txt alongside everything else.
FROM apache/airflow:2.10.3-python3.11

COPY airflow/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
