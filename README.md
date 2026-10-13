# Personal Finance Data Pipeline

[![CI](https://github.com/akpadala/finance-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/akpadala/finance-pipeline/actions/workflows/ci.yml)

An end-to-end ELT pipeline for personal spending data. Bank transaction
exports are loaded into DuckDB, modeled with dbt into a tested star
schema, orchestrated with Airflow, validated against an independent
PySpark implementation, categorized with a scikit-learn model, and served
through a Streamlit budget dashboard.

**Stack:** Python, SQL, DuckDB, dbt, PySpark, Airflow, scikit-learn,
Streamlit, Docker, GitHub Actions

```
 bank CSV exports
       │
       ▼
 ingestion (Python) ──► DuckDB raw layer
                              │
                              ▼
                      dbt: staging ► intermediate ► marts (star schema)
                              │            30+ data quality tests
              ┌───────────────┼──────────────────────┐
              ▼               ▼                      ▼
     PySpark parity     ML batch scoring      Streamlit dashboard
     check vs dbt       (scikit-learn)
```

Every step runs as a task in an Airflow DAG and in CI on every push. See
[`docs/architecture.md`](docs/architecture.md) for the full data flow and
the reasoning behind each layer.

---

## Features

- **Layered dbt models.** Staging (typing, surrogate keys), intermediate
  (rule-based categorization, merchant normalization, recurring-charge
  detection), and marts (fact and dimension tables plus reporting tables).
- **Data quality tests.** `not_null`, `unique`, `accepted_values`, and
  `relationships` tests run on every pipeline run; a failing test fails
  the run.
- **Idempotent ingestion.** Re-running a load never duplicates rows.
- **Orchestration.** An Airflow DAG with retries and a failure-alert
  callback, running on a Dockerized LocalExecutor stack.
- **Spark parity check.** A PySpark job recomputes the monthly spending
  mart from Parquet and fails the pipeline if any row differs from the dbt
  output.
- **ML categorization.** A TF-IDF + logistic regression classifier,
  deployed as a batch-scoring step that writes predictions, confidence
  scores, and a review flag for low-confidence predictions.
- **Encrypted data handling.** Real bank exports can be encrypted at rest
  (PBKDF2 + Fernet), and the dashboard can process an uploaded file in an
  isolated, temporary database that is deleted after use.
- **CI.** GitHub Actions runs the full pipeline, all dbt tests, the Spark
  parity check, and model scoring on every push and pull request.

---

## Quickstart

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
make pipeline                      # generate sample data, ingest, dbt seed/run/test
make dashboard                     # opens http://localhost:8501
```

`make pipeline` generates a year of synthetic transactions (fixed random
seed, so results are reproducible), loads them into DuckDB, builds every
dbt model, and runs every test. Without `make`, run the commands in the
`Makefile`'s `pipeline` target in order. When running `dbt` directly, set
`DBT_PROFILES_DIR` to the `finance_dbt` folder first.

### Spark parity check and model scoring

These need Java 17+ for Spark and the extra requirements:

```bash
pip install -r spark/requirements.txt -r ml/requirements.txt
python spark/monthly_spending_spark.py   # PySpark vs dbt parity check
python ml/score_transactions.py          # batch-score transactions with the model
python ml/train_categorizer.py           # (optional) retrain and evaluate the model
```

Results can be queried in DuckDB:

```sql
select * from spark.monthly_spending order by month, category;
select * from ml.transaction_predictions where needs_review;
```

### Docker (no local Python setup)

```bash
docker compose up --build
```

Runs the pipeline in one container, then starts the dashboard once the
pipeline has completed successfully. Open `http://localhost:8501`.

### Airflow

```bash
cd airflow
docker compose up airflow-init     # first run only
docker compose up
```

Open `http://localhost:8080` (local login: `admin` / `admin`), enable the
`finance_pipeline` DAG, and trigger a run. Task graph:

```
ingest_transactions → dbt_seed → dbt_run → dbt_test → spark_monthly_parity → score_transactions
```

---

## Using your own transactions

All three options expect a CSV with these columns (`Memo` optional):

```
Transaction Date, Post Date, Description, Category, Type, Amount
```

- **Plaintext:** place the CSV in `data/raw/` and rerun `make pipeline`.
  Everything in `data/raw/` except the sample file is gitignored.
- **Encrypted at rest:** `python scripts/encrypt_bank_csv.py <file.csv>`
  writes an encrypted copy to `data/private/` (gitignored) and removes the
  plaintext. Ingestion decrypts it in memory. The key is derived from a
  passphrase with PBKDF2 (480,000 iterations, random per-file salt) and is
  never stored.
- **Dashboard upload:** the dashboard's "Try with your own data" option
  encrypts an uploaded file on receipt with a session-only key, runs it
  through the same dbt models in an isolated temporary database, and
  deletes everything afterward. This protects data at rest; it is not
  end-to-end encryption, since the app must decrypt the data to process
  it. See `dashboard/secure_upload.py`.

Budget targets are set in `finance_dbt/seeds/category_budgets.csv`.

---

## Design decisions

- **DuckDB as the warehouse.** A columnar OLAP engine with zero
  infrastructure. The dbt models avoid DuckDB-specific behavior, so
  targeting Snowflake or BigQuery is a `profiles.yml` change, not a rewrite.
- **Rules first, ML second.** Rule-based categorization is transparent and
  covers the known merchants. The classifier is trained on the rule
  labels and handles merchants the rules have never seen, with
  low-confidence predictions flagged for review rather than trusted.
- **Honest model evaluation.** The dataset has few distinct merchants, so a
  random train/test split mostly measures memorization. The training
  script reports that split as such and evaluates generalization
  separately on merchants absent from training.
- **Cross-engine validation.** The Spark job is an independent
  implementation of the monthly spending mart; agreement with dbt is a
  data-consistency check. At this data size DuckDB is faster. Spark is the
  scale-out path, and the same code runs on a cluster by changing the
  master URL.
- **Sequential writes.** The Spark and scoring tasks run one after another
  because DuckDB allows a single writer at a time.

---

## Project structure

```
finance-pipeline/
├── ingestion/            CSV → DuckDB raw layer (idempotent), encryption helpers
├── finance_dbt/          dbt project: staging, intermediate, marts, seeds, tests
├── spark/                PySpark parity job
├── ml/                   classifier training and batch scoring
├── airflow/              DAG, Dockerfile, and Airflow docker-compose stack
├── dashboard/            Streamlit app, secure upload, tests
├── scripts/              sample data generator, encryption CLI
├── docs/architecture.md  data flow and design rationale
├── .github/workflows/    CI
├── Dockerfile, docker-compose.yml
└── Makefile
```
