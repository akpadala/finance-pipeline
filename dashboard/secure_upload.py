"""
Lets someone try the dashboard against their own transactions instead of
the bundled sample data, without their data ever being written to this
app's shared warehouse, committed anywhere, or logged.

What actually happens to an uploaded file, in order:

  1. It's read into memory and encrypted immediately with a random key
     that exists only in this Streamlit session's server-side memory
     (st.session_state) -- never written to disk, never logged, not
     derived from anything guessable.
  2. The *ciphertext* (never the plaintext) is written to a fresh,
     uniquely-named, owner-only-permission (0700) temp directory --
     effectively a disposable sandbox that nothing else on the machine
     can read.
  3. ingestion/load_to_duckdb.py decrypts it straight into memory and
     loads it into a throwaway DuckDB file that lives in that same temp
     directory -- a plaintext copy of the upload is never written to disk
     at any point.
  4. The real dbt project (the same models that process the sample data)
     runs against that throwaway database via subprocess, with its own
     isolated target/log paths so it can't collide with -- or leak into
     -- the shared warehouse's dbt run.
  5. The resulting marts (already-aggregated monthly/budget/recurring
     numbers -- never raw per-transaction rows) are read into pandas.
  6. The entire temp directory is deleted, whether or not the run
     succeeded.

Limits (see README "Using your own transactions"):
this is encryption *at rest* for the brief window the data is on disk --
it is not end-to-end encryption, because this server process necessarily
has the key and can read the data to process it. And if this app is ever
deployed somewhere shared/public, that deployment should default to the
sample data and treat this upload path as a clearly-labeled "try it"
feature, not a place for anyone's actual account numbers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import duckdb
import pandas as pd
import streamlit as st
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion"))
import load_to_duckdb  # noqa: E402

FINANCE_DBT_DIR = os.path.join(os.path.dirname(__file__), "..", "finance_dbt")
DBT_TIMEOUT_SECONDS = 180


class UploadProcessingError(Exception):
    """Raised for anything that goes wrong turning an uploaded CSV into
    marts -- bad schema, dbt failure, timeout. The message is written to
    be shown to the user directly via st.error()."""


def _session_key() -> bytes:
    """A random Fernet key, generated once per browser session and held
    only in st.session_state (server RAM) -- never persisted, never
    derived from anything about the user or file, so it's useless outside
    this one session's process memory."""
    if "_upload_fernet_key" not in st.session_state:
        st.session_state["_upload_fernet_key"] = Fernet.generate_key()
    return st.session_state["_upload_fernet_key"]


def _run_dbt(subcommand: list[str], db_path: str, tempdir: str) -> None:
    env = dict(os.environ)
    env["DBT_DUCKDB_PATH"] = db_path
    cmd = [
        "dbt", *subcommand,
        "--project-dir", FINANCE_DBT_DIR,
        "--profiles-dir", FINANCE_DBT_DIR,
        "--target-path", os.path.join(tempdir, "target"),
        "--log-path", os.path.join(tempdir, "logs"),
    ]
    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=DBT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise UploadProcessingError(
            f"Processing your data timed out after {DBT_TIMEOUT_SECONDS}s (dbt {' '.join(subcommand)})."
        ) from e
    except OSError as e:
        # Most commonly FileNotFoundError if the `dbt` executable isn't on
        # PATH in this environment -- a deployment/setup problem, not the
        # user's. Surfaced as UploadProcessingError (rather than left to
        # propagate raw) so app.py's except clause catches it and shows a
        # clean message instead of a stack trace with internal paths in it.
        raise UploadProcessingError(
            "This environment can't run dbt right now, so uploads can't be processed. "
            "(The sample data below doesn't need dbt to run live -- only new uploads do.)"
        ) from e

    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-25:])
        raise UploadProcessingError(
            f"dbt {' '.join(subcommand)} failed while processing your upload:\n\n{tail}"
        )


def process_uploaded_csv(file_bytes: bytes, filename: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Runs the full encrypt -> isolate -> ingest -> dbt -> read-marts ->
    shred pipeline for one uploaded CSV's bytes. Returns (monthly_df,
    budget_df, recurring_df) in exactly the shape load_marts() in app.py
    returns, so the rest of the app treats an upload identically to the
    sample-data warehouse from that point on.

    Raises UploadProcessingError with a message safe to show the user
    directly (no stack traces, no internal paths) on any failure. Always
    cleans up its temp directory, success or failure.
    """
    if not file_bytes:
        raise UploadProcessingError("That file is empty.")
    if len(file_bytes) > 25 * 1024 * 1024:
        raise UploadProcessingError("That file is larger than the 25MB limit for this demo.")

    key = _session_key()
    ciphertext = Fernet(key).encrypt(file_bytes)
    # Drop our only plaintext reference now that it's encrypted -- from
    # here on the only path back to plaintext is through ingestion's
    # decrypt_fn, which exists only for the duration of this function.
    del file_bytes

    tempdir = tempfile.mkdtemp(prefix="fp_upload_")
    try:
        os.chmod(tempdir, 0o700)
        enc_path = os.path.join(tempdir, "upload.csv.enc")
        with open(enc_path, "wb") as f:
            f.write(ciphertext)

        db_path = os.path.join(tempdir, "session.duckdb")
        decrypt_fn = lambda blob: Fernet(key).decrypt(blob)  # noqa: E731

        try:
            n_rows = load_to_duckdb.load_all(
                db_path=db_path, csv_paths=[], encrypted_paths=[enc_path], decrypt_fn=decrypt_fn,
            )
        except ValueError as e:
            # _clean_dataframe's column-validation error -- genuinely the
            # user's problem (wrong export format), not a bug, so it's
            # safe and useful to show verbatim.
            raise UploadProcessingError(
                f"That CSV doesn't match the expected format: {e}\n\n"
                f"Expected columns: {', '.join(load_to_duckdb.REQUIRED_COLUMNS)} "
                "(plus an optional Memo column). See the README's "
                '"Using your own transactions" section for how to reshape a real bank export.'
            ) from e
        except InvalidToken as e:
            raise UploadProcessingError("Could not decrypt the upload (internal error).") from e
        except Exception as e:
            raise UploadProcessingError(f"Couldn't read that CSV: {e}") from e

        if n_rows == 0:
            raise UploadProcessingError("No rows were loaded from that file.")

        _run_dbt(["seed"], db_path, tempdir)
        _run_dbt(["run"], db_path, tempdir)

        try:
            con = duckdb.connect(db_path, read_only=True)
            try:
                monthly_df = con.execute("select * from marts.mart_monthly_spending").df()
                budget_df = con.execute("select * from marts.mart_budget_vs_actual").df()
                recurring_df = con.execute(
                    "select * from marts.dim_merchant where is_recurring_merchant order by lifetime_spend desc"
                ).df()
            finally:
                con.close()
        except duckdb.Error as e:
            raise UploadProcessingError(f"Couldn't read back the processed data: {e}") from e

        monthly_df["month"] = pd.to_datetime(monthly_df["month"])
        budget_df["month"] = pd.to_datetime(budget_df["month"])

        if monthly_df.empty:
            raise UploadProcessingError(
                "Your file loaded, but produced no spending data -- double-check the "
                "Transaction Date and Amount columns."
            )

        return monthly_df, budget_df, recurring_df

    finally:
        # Always shred the temp dir -- ciphertext, plaintext-in-the-db,
        # dbt logs, all of it -- whether processing succeeded or raised.
        shutil.rmtree(tempdir, ignore_errors=True)


def clear_session_upload() -> None:
    """Wipes everything this module put in session_state -- the
    encryption key included, not just the cached results."""
    for key in ("_upload_fernet_key", "_upload_marts", "_upload_file_id", "_upload_filename"):
        st.session_state.pop(key, None)
