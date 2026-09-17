"""
Ingestion layer: raw bank-export CSVs -> DuckDB `raw` schema.

This is the "E" and the first half of the "L" in ELT: Extract the CSVs
sitting in data/raw/, do the *minimum* cleaning needed to load them safely
(no business logic here -- categorization, budgeting math, etc. all lives
downstream in dbt), and Load them into a DuckDB warehouse file.

Why DuckDB: it's a real embedded OLAP database (columnar, SQL, fast
aggregations) with zero setup -- no server to run, just a file on disk.
It speaks the same SQL dialect family as Snowflake/BigQuery/Postgres, so
everything you learn here (and every dbt model you write against it)
transfers directly if this pipeline later points at a "real" cloud
warehouse instead. That's exactly what Phase 3 in the README does.

Design choices worth being able to explain in an interview:
  - Idempotent loads: re-running this script for the same source file does
    NOT duplicate rows. We delete-then-insert per source_file, so you can
    safely re-run the pipeline (e.g. after fixing a bug) without corrupting
    the raw layer. This mirrors how real ingestion jobs are expected to be
    safely re-runnable ("idempotent") in production.
  - Raw layer is a near-literal copy of the source, plus lineage columns
    (_source_file, _loaded_at). We do NOT rename columns to business names
    or drop "bad" rows here -- that's dbt's job in the staging layer, where
    it's version-controlled, tested, and easy to see exactly what changed
    and why. Keeping raw truly raw means you can always re-derive
    everything downstream from scratch.
  - Any CSV dropped into data/raw/ gets picked up automatically, so this
    is also how you load your *own* real bank exports later: export a CSV
    from your bank, drop it in data/raw/, and rerun this script.
"""

from __future__ import annotations

import glob
import os
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "warehouse", "finance.duckdb")
RAW_DATA_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "*.csv")

# The columns we expect any bank export dropped into data/raw/ to have.
# Real exports vary bank to bank -- if you add your own, rename its
# columns to match this shape before dropping it in (see README "Using
# your own real transactions").
REQUIRED_COLUMNS = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"]


def _read_and_validate_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required column(s): {missing}")

    # Light, structural cleaning only -- no business rules here.
    df["Transaction Date"] = pd.to_datetime(df["Transaction Date"], errors="raise").dt.date
    df["Post Date"] = pd.to_datetime(df["Post Date"], errors="raise").dt.date
    df["Amount"] = pd.to_numeric(df["Amount"], errors="raise")
    df["Description"] = df["Description"].astype(str).str.strip()
    df["Category"] = df["Category"].fillna("").astype(str).str.strip()
    df["Memo"] = df["Memo"].fillna("").astype(str).str.strip() if "Memo" in df.columns else ""

    before = len(df)
    df = df.drop_duplicates()
    if len(df) != before:
        print(f"  dropped {before - len(df)} exact-duplicate row(s) within {os.path.basename(path)}")

    df["_source_file"] = os.path.basename(path)
    df["_loaded_at"] = datetime.now(timezone.utc)

    return df


def load_all() -> None:
    csv_paths = sorted(glob.glob(RAW_DATA_GLOB))
    if not csv_paths:
        print(f"No CSV files found matching {RAW_DATA_GLOB}. Run scripts/generate_sample_data.py first,")
        print("or drop your own bank export CSV into data/raw/.")
        sys.exit(1)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw.transactions (
            transaction_date DATE,
            post_date        DATE,
            description      VARCHAR,
            category         VARCHAR,
            type              VARCHAR,
            amount            DOUBLE,
            memo              VARCHAR,
            _source_file      VARCHAR,
            _loaded_at        TIMESTAMP
        )
    """)

    total_loaded = 0
    for path in csv_paths:
        print(f"Loading {path} ...")
        df = _read_and_validate_csv(path)
        source_file = os.path.basename(path)

        # Idempotency: clear out any prior load of this exact file before
        # re-inserting, so reruns never duplicate data.
        con.execute("DELETE FROM raw.transactions WHERE _source_file = ?", [source_file])

        df = df.rename(columns={
            "Transaction Date": "transaction_date",
            "Post Date": "post_date",
            "Description": "description",
            "Category": "category",
            "Type": "type",
            "Amount": "amount",
            "Memo": "memo",
        })[["transaction_date", "post_date", "description", "category", "type",
            "amount", "memo", "_source_file", "_loaded_at"]]

        con.execute("INSERT INTO raw.transactions SELECT * FROM df")
        print(f"  loaded {len(df)} row(s) from {source_file}")
        total_loaded += len(df)

    row_count = con.execute("SELECT COUNT(*) FROM raw.transactions").fetchone()[0]
    con.close()

    print(f"\nDone. Inserted {total_loaded} row(s) this run.")
    print(f"raw.transactions now has {row_count} total row(s) in {DB_PATH}")


if __name__ == "__main__":
    load_all()
