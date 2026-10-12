"""
PySpark batch job: recompute the monthly spending mart with Spark and check
that it matches what dbt built.

Where this sits in the pipeline:

    ingest -> dbt seed -> dbt run -> dbt test -> [THIS: spark parity check] -> score

What it does, step by step:
  1. EXPORT: DuckDB writes two tables out as Parquet files in
     warehouse/spark_input/ -- the categorized transactions (one row per
     transaction) and the category dimension. Parquet is the standard
     columnar file format for handing data to Spark; a real data lake (S3,
     ADLS, HDFS) is mostly Parquet files.
  2. TRANSFORM IN SPARK: a local Spark session reads both Parquet files,
     joins them, truncates each transaction date to its month, and groups
     by (month, category, category_group) to get transaction counts, total
     spend, and total income -- the exact same logic as
     finance_dbt/models/marts/mart_monthly_spending.sql, written in the
     Spark DataFrame API instead of SQL.
  3. PARITY CHECK: compare Spark's result to dbt's mart_monthly_spending
     row by row. Counts must match exactly; money totals must match to
     the cent. Any missing row or mismatch fails the job (non-zero exit),
     which in Airflow fails the task and fires the DAG's alert callback.
  4. LOAD: write Spark's result to DuckDB as spark.monthly_spending so it
     can be queried and compared by hand.

Why do this at all, when dbt already builds this table?
  - Two independent implementations of the same business logic that agree
    is a strong data-consistency check: a bug in either one shows up as a
    parity failure instead of silently reaching the dashboard.
  - DuckDB is single-machine. If this pipeline's data outgrew one laptop
    (years of transactions across many users), the heavy aggregation is
    exactly the step you'd move to Spark on a cluster. This job is that
    migration path, running in local mode today: the same code runs on a
    cluster by changing only the master URL below (local[*] -> a YARN,
    Kubernetes, or Databricks cluster).

Honest scope note (say this in an interview rather than overselling it):
the sample data is about a year of one person's transactions, so Spark is
not *faster* than DuckDB here -- Spark's startup alone takes longer than
the whole DuckDB query. The point is the pattern: Parquet hand-off,
DataFrame transformations, a reproducible cross-engine validation, and an
orchestrated task that fails loudly when the numbers disagree.

Requirements: Java 17+ on PATH (or JAVA_HOME set) and `pip install -r
spark/requirements.txt`. The Airflow Docker image installs both.

Usage (from the repo root, after `make pipeline`):
    python spark/monthly_spending_spark.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(REPO_ROOT, "warehouse", "finance.duckdb")
SPARK_INPUT_DIR = os.path.join(REPO_ROOT, "warehouse", "spark_input")
TRANSACTIONS_PARQUET = os.path.join(SPARK_INPUT_DIR, "transactions.parquet")
CATEGORIES_PARQUET = os.path.join(SPARK_INPUT_DIR, "dim_category.parquet")

# Money totals are compared to the cent. Counts are compared exactly.
MONEY_TOLERANCE = 0.005

# Make Spark's Python workers use the same interpreter (and virtualenv) that
# is running this script. Without this, on Windows especially, Spark can
# pick up a different Python and fail with confusing version errors.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)


def export_to_parquet(con: duckdb.DuckDBPyConnection) -> None:
    """Step 1: DuckDB -> Parquet, the hand-off format Spark reads."""
    os.makedirs(SPARK_INPUT_DIR, exist_ok=True)
    # Forward slashes work on Windows too, and avoid backslash-escaping
    # problems inside the SQL string literal.
    tx_path = TRANSACTIONS_PARQUET.replace("\\", "/")
    cat_path = CATEGORIES_PARQUET.replace("\\", "/")
    con.execute(f"""
        COPY (
            select transaction_id, transaction_date, category, amount, amount_abs, is_income
            from intermediate.int_transactions_categorized
        ) TO '{tx_path}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            select category, category_group from marts.dim_category
        ) TO '{cat_path}' (FORMAT PARQUET)
    """)


def build_spark_session():
    """A local Spark session using every CPU core on this machine."""
    from pyspark.sql import SparkSession

    return (
        SparkSession.builder
        .appName("finance-monthly-spending")
        # local[*] = run Spark inside this one process, one worker thread
        # per CPU core. On a cluster this is the only line that changes.
        .master("local[*]")
        # The data is small, so don't split every shuffle into the default
        # 200 partitions -- that's tuned for cluster-sized data and just
        # adds overhead here.
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )


def compute_monthly_spending(spark) -> pd.DataFrame:
    """Step 2: the mart_monthly_spending logic, in the Spark DataFrame API."""
    from pyspark.sql import functions as F

    # file:// URIs avoid Windows drive-letter paths (C:\...) being misread
    # by Spark's Hadoop file layer; they work the same on Mac/Linux.
    transactions = spark.read.parquet(Path(TRANSACTIONS_PARQUET).as_uri())
    categories = spark.read.parquet(Path(CATEGORIES_PARQUET).as_uri())

    monthly = (
        transactions
        # Inner join, same as the dbt mart's join to dim_category.
        .join(categories, on="category", how="inner")
        .withColumn("month", F.trunc("transaction_date", "month"))
        .groupBy("month", "category", "category_group")
        .agg(
            F.count(F.lit(1)).alias("transaction_count"),
            # Spending counts only non-income rows; income only income rows
            # -- same CASE WHEN logic as the SQL model.
            F.sum(F.when(~F.col("is_income"), F.col("amount_abs")).otherwise(F.lit(0.0)))
                .alias("total_spent"),
            F.sum(F.when(F.col("is_income"), F.col("amount")).otherwise(F.lit(0.0)))
                .alias("total_income"),
        )
        .orderBy("month", "category")
    )

    # The result is one row per month x category (a few hundred rows), so
    # collecting it to the driver is cheap. With big data you would write
    # it out from Spark instead of collecting.
    result = monthly.toPandas()
    result["month"] = pd.to_datetime(result["month"])
    result["transaction_count"] = result["transaction_count"].astype("int64")
    result["total_spent"] = result["total_spent"].astype("float64")
    result["total_income"] = result["total_income"].astype("float64")
    return result


def load_dbt_mart(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute("""
        select month, category, category_group, transaction_count, total_spent, total_income
        from marts.mart_monthly_spending
    """).df()
    df["month"] = pd.to_datetime(df["month"])
    df["transaction_count"] = df["transaction_count"].astype("int64")
    df["total_spent"] = df["total_spent"].astype("float64")
    df["total_income"] = df["total_income"].astype("float64")
    return df


def compare(spark_df: pd.DataFrame, dbt_df: pd.DataFrame) -> list[str]:
    """Step 3: row-by-row parity check. Returns a list of problems (empty = match)."""
    keys = ["month", "category"]
    merged = spark_df.merge(
        dbt_df, on=keys, how="outer", suffixes=("_spark", "_dbt"), indicator=True
    )

    problems: list[str] = []

    for _, row in merged[merged["_merge"] != "both"].iterrows():
        side = "Spark" if row["_merge"] == "left_only" else "dbt"
        problems.append(f"{row['month']:%Y-%m} / {row['category']}: only present in {side} output")

    both = merged[merged["_merge"] == "both"]
    for _, row in both.iterrows():
        label = f"{row['month']:%Y-%m} / {row['category']}"
        if row["category_group_spark"] != row["category_group_dbt"]:
            problems.append(f"{label}: category_group {row['category_group_spark']!r} (Spark) "
                            f"vs {row['category_group_dbt']!r} (dbt)")
        if row["transaction_count_spark"] != row["transaction_count_dbt"]:
            # The outer merge turns integer columns into floats, so cast back
            # for a readable message.
            problems.append(f"{label}: transaction_count {int(row['transaction_count_spark'])} (Spark) "
                            f"vs {int(row['transaction_count_dbt'])} (dbt)")
        for col in ("total_spent", "total_income"):
            a, b = row[f"{col}_spark"], row[f"{col}_dbt"]
            if abs(a - b) > MONEY_TOLERANCE:
                problems.append(f"{label}: {col} {a:.2f} (Spark) vs {b:.2f} (dbt)")

    return problems


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No warehouse found at {DB_PATH}. Run `make pipeline` first.")
        return 1

    con = duckdb.connect(DB_PATH)
    try:
        export_to_parquet(con)
        dbt_df = load_dbt_mart(con)
    finally:
        con.close()

    spark = build_spark_session()
    spark.sparkContext.setLogLevel("WARN")
    try:
        spark_df = compute_monthly_spending(spark)
    finally:
        spark.stop()

    problems = compare(spark_df, dbt_df)

    # Step 4: persist Spark's output next to dbt's for manual inspection.
    con = duckdb.connect(DB_PATH)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS spark")
        con.register("spark_result", spark_df)
        con.execute("CREATE OR REPLACE TABLE spark.monthly_spending AS SELECT * FROM spark_result")
    finally:
        con.close()

    print(f"Spark computed {len(spark_df)} month x category rows; "
          f"dbt mart has {len(dbt_df)}.")
    print(f"Spark totals: spent ${spark_df['total_spent'].sum():,.2f}, "
          f"income ${spark_df['total_income'].sum():,.2f}, "
          f"{int(spark_df['transaction_count'].sum())} transactions.")

    if problems:
        print(f"\nPARITY CHECK FAILED: {len(problems)} difference(s) between Spark and dbt:")
        for p in problems[:25]:
            print(f"  - {p}")
        if len(problems) > 25:
            print(f"  ... and {len(problems) - 25} more")
        return 1

    print("\nPARITY CHECK PASSED: Spark and dbt agree on every month x category row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
