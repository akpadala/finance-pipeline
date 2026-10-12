"""
Batch inference: run the trained categorizer on every transaction, every
pipeline run, and save the predictions to the warehouse.

ml/train_categorizer.py trains and evaluates the model and saves it to
ml/model/categorizer.joblib. On its own, that's a model sitting in a file.
This script is the step that puts it to work inside the pipeline: Airflow
runs it after dbt has built and tested the warehouse, so every new batch
of transactions gets a model prediction automatically.

    ingest -> dbt seed -> dbt run -> dbt test -> spark parity check -> [THIS: score]

What it does:
  1. Loads the saved model artifact. If none exists yet (fresh clone,
     first run), it trains one from the rule-labeled data using the exact
     same pipeline definition as train_categorizer.py and saves it, so the
     DAG never fails just because nobody ran the training script by hand.
  2. Scores every transaction in intermediate.int_transactions_categorized
     and records, per transaction: the rule-based category, the model's
     predicted category, the model's confidence, whether the two agree,
     and a needs_review flag when confidence is below the threshold.
  3. Writes the result to ml.transaction_predictions (replaced on each
     run, so re-running the pipeline is idempotent -- same as ingestion).
  4. Prints a short monitoring summary: how many rows were scored, how
     often the model agrees with the rules, how many predictions were
     flagged for review, and suggested categories for anything the rules
     left Uncategorized.

Why "agreement with the rules" is the metric worth watching: the rules are
the current source of truth, and the model was trained on them. If
agreement suddenly drops after new data arrives, either the model has gone
stale or the new data looks different from what it was trained on (data
drift) -- both are signals to retrain or update the rules.

To force a fresh model (e.g. after the rules change):
    python ml/train_categorizer.py      # retrain + evaluate + save
    python ml/score_transactions.py     # score with the new model

Usage (from the repo root, after `make pipeline`):
    python ml/score_transactions.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import duckdb
import joblib
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from train_categorizer import (  # noqa: E402
    CONFIDENCE_THRESHOLD,
    DB_PATH,
    MODEL_PATH,
    build_pipeline,
    load_training_data,
)


def load_or_train_model():
    """Use the saved model if there is one; otherwise train and save one."""
    if os.path.exists(MODEL_PATH):
        print(f"Loaded model artifact from {os.path.relpath(MODEL_PATH)}")
        return joblib.load(MODEL_PATH)

    print("No saved model found -- training one from the rule-labeled transactions.")
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        labeled = load_training_data(con)
    finally:
        con.close()

    model = build_pipeline()
    model.fit(labeled["description"], labeled["category"])
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    print(f"Trained on {len(labeled)} transactions and saved to {os.path.relpath(MODEL_PATH)}")
    return model


def score(model, transactions: pd.DataFrame) -> pd.DataFrame:
    """Predict a category + confidence for each transaction description."""
    probabilities = model.predict_proba(transactions["description"])
    best = probabilities.argmax(axis=1)

    scored = transactions.rename(columns={"category": "rule_category"}).copy()
    scored["predicted_category"] = model.classes_[best]
    scored["confidence"] = probabilities.max(axis=1).round(4)
    scored["agrees_with_rules"] = scored["predicted_category"] == scored["rule_category"]
    scored["needs_review"] = scored["confidence"] < CONFIDENCE_THRESHOLD
    scored["scored_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return scored[[
        "transaction_id", "transaction_date", "description",
        "rule_category", "predicted_category", "confidence",
        "agrees_with_rules", "needs_review", "scored_at",
    ]]


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No warehouse found at {DB_PATH}. Run `make pipeline` first.")
        return 1

    model = load_or_train_model()

    con = duckdb.connect(DB_PATH)
    try:
        transactions = con.execute("""
            select transaction_id, transaction_date, description, category
            from intermediate.int_transactions_categorized
        """).df()

        if transactions.empty:
            print("No transactions to score.")
            return 0

        scored = score(model, transactions)

        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.register("scored", scored)
        con.execute("CREATE OR REPLACE TABLE ml.transaction_predictions AS SELECT * FROM scored")
    finally:
        con.close()

    # ---- Monitoring summary ----
    categorized = scored[scored["rule_category"] != "Uncategorized"]
    agreement = categorized["agrees_with_rules"].mean() if len(categorized) else float("nan")
    flagged = int(scored["needs_review"].sum())

    print(f"\nScored {len(scored)} transactions -> ml.transaction_predictions")
    print(f"Agreement with rule-based categories: {agreement:.1%} "
          f"(on the {len(categorized)} transactions the rules could categorize)")
    print(f"Flagged for review (confidence < {CONFIDENCE_THRESHOLD:.0%}): {flagged}")

    uncategorized = scored[scored["rule_category"] == "Uncategorized"]
    if len(uncategorized):
        print(f"\nSuggested categories for {len(uncategorized)} transaction(s) the rules missed:")
        for _, row in uncategorized.head(20).iterrows():
            flag = "  <- needs review" if row["needs_review"] else ""
            print(f"  {row['description'][:35]:35s} -> {row['predicted_category']:20s} "
                  f"({row['confidence']:.0%}){flag}")
    else:
        print("No Uncategorized transactions this run (the rules caught everything).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
