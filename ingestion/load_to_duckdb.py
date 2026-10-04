"""
Ingestion layer: raw bank-export CSVs -> DuckDB `raw` schema.

This is the "E" and the first half of the "L" in ELT: Extract the CSVs
sitting in data/raw/ (plus any encrypted real exports in data/private/),
do the *minimum* cleaning needed to load them safely (no business logic
here -- categorization, budgeting math, etc. all lives downstream in dbt),
and Load them into a DuckDB warehouse file.

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
    is also how you load your own real bank exports -- *or* see
    "Using your own real transactions, encrypted" in the README for the
    encrypted path (data/private/*.csv.enc, via scripts/encrypt_bank_csv.py)
    if you'd rather your real data never sit on disk as plaintext at all.
  - This module is also imported programmatically (not just run as a
    script) by dashboard/secure_upload.py, which loads one uploaded CSV
    into an ephemeral, per-session database rather than the shared
    warehouse -- see load_all()'s db_path/csv_paths/encrypted_paths
    parameters below.
"""

from __future__ import annotations

import glob
import io
import os
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import crypto_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "warehouse", "finance.duckdb")
RAW_DATA_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "*.csv")
PRIVATE_DATA_GLOB = os.path.join(os.path.dirname(__file__), "..", "data", "private", "*.csv.enc")

# The columns we expect any bank export dropped into data/raw/ (or
# encrypted into data/private/) to have. Real exports vary bank to bank --
# if you add your own, rename its columns to match this shape first. See
# README "Using your own real transactions."
REQUIRED_COLUMNS = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"]


def _clean_dataframe(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """Structural cleaning only -- no business rules. Shared by the plaintext
    and decrypted-in-memory paths so they behave identically."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{source_name} is missing required column(s): {missing}")

    df = df.copy()
    df["Transaction Date"] = pd.to_datetime(df["Transaction Date"], errors="raise").dt.date
    df["Post Date"] = pd.to_datetime(df["Post Date"], errors="raise").dt.date
    df["Amount"] = pd.to_numeric(df["Amount"], errors="raise")
    df["Description"] = df["Description"].astype(str).str.strip()
    df["Category"] = df["Category"].fillna("").astype(str).str.strip()
    df["Memo"] = df["Memo"].fillna("").astype(str).str.strip() if "Memo" in df.columns else ""

    before = len(df)
    df = df.drop_duplicates()
    if len(df) != before:
        print(f"  dropped {before - len(df)} exact-duplicate row(s) within {source_name}")

    df["_source_file"] = source_name
    df["_loaded_at"] = datetime.now(timezone.utc)

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
    return df


def _read_and_validate_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    return _clean_dataframe(df, os.path.basename(path))


def _read_and_validate_encrypted_csv(path: str, decrypt_fn) -> pd.DataFrame:
    """Decrypts straight into memory -- the plaintext bytes exist only as a
    local variable in this process, and are never written back to disk.
    decrypt_fn(blob: bytes) -> bytes lets callers supply either the
    passphrase-derived decryption used by the local CLI workflow (the
    default, via crypto_utils) or a different key model entirely -- e.g.
    dashboard/secure_upload.py passes a random per-session Fernet key
    instead of a human passphrase, since there's no human around to type
    one for an uploaded file."""
    with open(path, "rb") as f:
        blob = f.read()
    plaintext = decrypt_fn(blob)
    df = pd.read_csv(io.BytesIO(plaintext))
    source_name = os.path.basename(path).removesuffix(".enc")
    return _clean_dataframe(df, source_name)


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
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


def _load_dataframe(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, source_file: str) -> int:
    # Idempotency: clear out any prior load of this exact file before
    # re-inserting, so reruns never duplicate data.
    con.execute("DELETE FROM raw.transactions WHERE _source_file = ?", [source_file])
    con.execute("INSERT INTO raw.transactions SELECT * FROM df")
    return len(df)


def load_all(
    db_path: str | None = None,
    csv_paths: list[str] | None = None,
    encrypted_paths: list[str] | None = None,
    passphrase: str | None = None,
    decrypt_fn=None,
) -> int:
    """Loads plaintext CSVs and/or encrypted CSVs into the `raw.transactions`
    table of the DuckDB file at db_path.

    All parameters are optional and default to the normal CLI behavior
    (scan data/raw/*.csv and data/private/*.csv.enc, write to the shared
    warehouse, prompt for a passphrase if needed). dashboard/secure_upload.py
    instead calls this with an explicit db_path pointing at an ephemeral,
    per-session temp file, a single encrypted_paths entry, and its own
    decrypt_fn (a random per-session key, not a human passphrase) -- so an
    uploaded file never touches the shared warehouse, and its plaintext
    never touches disk at all (only its ciphertext does, briefly, in an
    isolated temp dir that's deleted right after).

    Returns the total row count inserted this call.
    """
    db_path = db_path or DB_PATH
    if csv_paths is None:
        csv_paths = sorted(glob.glob(RAW_DATA_GLOB))
    if encrypted_paths is None:
        encrypted_paths = sorted(glob.glob(PRIVATE_DATA_GLOB))

    if not csv_paths and not encrypted_paths:
        print(f"No CSV files found matching {RAW_DATA_GLOB} or {PRIVATE_DATA_GLOB}.")
        print("Run scripts/generate_sample_data.py, drop your own bank export CSV into")
        print("data/raw/, or encrypt one into data/private/ with scripts/encrypt_bank_csv.py.")
        return 0

    if encrypted_paths and decrypt_fn is None:
        if passphrase is None:
            passphrase = crypto_utils.prompt_passphrase()
        decrypt_fn = lambda blob: crypto_utils.decrypt_bytes(blob, passphrase)  # noqa: E731

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    _ensure_schema(con)

    total_loaded = 0
    try:
        for path in csv_paths:
            print(f"Loading {path} ...")
            df = _read_and_validate_csv(path)
            n = _load_dataframe(con, df, os.path.basename(path))
            print(f"  loaded {n} row(s) from {os.path.basename(path)}")
            total_loaded += n

        for path in encrypted_paths:
            print(f"Loading (decrypting) {path} ...")
            df = _read_and_validate_encrypted_csv(path, decrypt_fn)
            source_name = os.path.basename(path).removesuffix(".enc")
            n = _load_dataframe(con, df, source_name)
            print(f"  loaded {n} row(s) from {source_name} (decrypted in memory)")
            total_loaded += n

        row_count = con.execute("SELECT COUNT(*) FROM raw.transactions").fetchone()[0]
    finally:
        con.close()

    print(f"\nDone. Inserted {total_loaded} row(s) this run.")
    print(f"raw.transactions now has {row_count} total row(s) in {db_path}")
    return total_loaded


if __name__ == "__main__":
    # Unlike load_all() itself (which is also called programmatically by
    # the dashboard's upload feature, where sys.exit would kill the whole
    # Streamlit server), the CLI entry point should fail loudly if there
    # was nothing to load -- that's almost always a setup mistake, and
    # letting dbt silently run against an empty raw table would hide it.
    loaded = load_all()
    if loaded == 0:
        sys.exit(1)
