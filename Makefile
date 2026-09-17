# Orchestration for the finance pipeline.
#
# This Makefile is a stand-in for a real orchestrator (Airflow/Dagster/
# Prefect) -- it defines the same thing a DAG would: an ordered chain of
# tasks, each depending on the last. `make pipeline` runs the whole chain
# in order, exactly the way an Airflow DAG with four tasks and
# `>>` dependencies between them would. Phase 2 in the README turns this
# exact chain into a real Airflow DAG once the sequential version here is
# solid -- that progression (script -> Makefile -> real orchestrator) is
# itself a normal, explainable evolution of a data pipeline.

export DBT_PROFILES_DIR := $(CURDIR)/finance_dbt

.PHONY: setup generate-data ingest dbt-seed dbt-run dbt-test dashboard pipeline clean docker-up docker-down

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

generate-data:
	python3 scripts/generate_sample_data.py

ingest:
	python3 ingestion/load_to_duckdb.py

dbt-seed:
	cd finance_dbt && dbt seed

dbt-run:
	cd finance_dbt && dbt run

dbt-test:
	cd finance_dbt && dbt test

dashboard:
	streamlit run dashboard/app.py

# The full chain, in dependency order -- this is the "DAG."
pipeline: generate-data ingest dbt-seed dbt-run dbt-test
	@echo ""
	@echo "Pipeline complete. Run 'make dashboard' to view it."

clean:
	rm -rf warehouse/finance.duckdb finance_dbt/target finance_dbt/logs

# Phase 3: the same pipeline + dashboard, containerized -- no local
# Python/dbt setup needed at all. See docker-compose.yml.
docker-up:
	docker compose up --build

docker-down:
	docker compose down
