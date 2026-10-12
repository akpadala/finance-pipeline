# Extends the official Airflow image with just what this project's DAG
# needs (duckdb, dbt-duckdb, pandas, plus Java + PySpark for the Spark
# parity task and scikit-learn for the model-scoring task). Built with the *repo root* as the
# Docker build context (see docker-compose.yaml's `context: ..`) so it can
# see airflow/requirements.txt alongside everything else.
FROM apache/airflow:2.10.3-python3.11

# PySpark runs on the JVM, so the image needs Java. Installing system
# packages requires root; switch back to the airflow user afterwards, which
# is what the official image expects to run as.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER airflow

COPY airflow/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
