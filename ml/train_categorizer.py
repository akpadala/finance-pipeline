"""
ML-based transaction categorization: a scoped-down version of the ML
piece from the original project idea, built as an add-on to a pipeline
that already works without it -- not as the foundation everything else
depends on.

What this is NOT: a replacement for the rule-based macro in
finance_dbt/macros/categorize_transaction.sql. The rules get essentially
every transaction in this dataset right (see the validation numbers in
docs/architecture.md) precisely because they were written by hand to
match this dataset's actual merchants. Their real weakness is merchants
they've never seen -- a new coffee shop, a store the rules don't mention.

What this IS: a text classifier trained on the categories the rules
already assigned (treating those as ground truth), so it learns the
*general pattern* of which words show up in which categories, not just
the literal keyword list. That lets it generalize to descriptions the
rules would call "Uncategorized" -- which is exactly where it's applied
below, plus a handful of hand-picked "the rules have never seen this
merchant" examples to demonstrate that generalization concretely.

Approach: TF-IDF over character n-grams (not whole words). Character
n-grams handle the messiness of real merchant strings -- store numbers,
abbreviations, typos, "TRADER JOE'S #112" vs "TRADER JOES #048" -- better
than word-level TF-IDF, because it's matching on substrings ("joe", "trad")
rather than requiring an exact token match. Logistic regression on top
because it's fast, interpretable (you can inspect which n-grams drive
which category), and a completely standard, defensible baseline for text
classification.

A real limitation, surfaced honestly rather than hidden: this dataset has
only ~30 distinct merchant strings total, each repeated across many
months. A random transaction-level train/test split puts the *same*
merchants in both train and test (just different dates), so its accuracy
mostly measures whether the model memorized merchants it has already
seen -- not whether it generalizes to new ones. That's why this script
reports that split's accuracy labeled explicitly as a memorization check,
and treats the NOVEL_EXAMPLES predictions below (merchants genuinely
absent from training) as the real generalization test. Expect those to
be noticeably worse and lower-confidence -- with this few unique
merchants to learn from, that's the model correctly telling you it
doesn't know, not a bug. More/varied labeled data over time is the actual
fix, not a different model.

Usage (from the repo root, with ml/requirements.txt installed):
    python3 ml/train_categorizer.py
"""

import os

import duckdb
import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "warehouse", "finance.duckdb")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "categorizer.joblib")

# Below this confidence, a prediction gets flagged rather than trusted
# outright -- the production-pattern equivalent of "route to a human /
# fall back to Uncategorized" instead of silently shipping a guess.
CONFIDENCE_THRESHOLD = 0.50

# Merchants the rule-based macro has never seen -- none of these keywords
# appear in finance_dbt/macros/categorize_transaction.sql. If the model
# is actually generalizing (rather than having memorized the training
# keywords), it should still land these in roughly the right category.
NOVEL_EXAMPLES = [
    "PANERA BREAD #4471",
    "COSTCO WHOLESALE #0221",
    "T-MOBILE AUTOPAY",
    "REGAL CINEMAS",
    "HERTZ RENT A CAR",
    "URGENT CARE COPAY",
]


def load_training_data(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute("""
        select description, merchant_name, category
        from intermediate.int_transactions_categorized
        where category != 'Uncategorized'
    """).df()
    return df


def print_flagged(desc: str, pred: str, conf: float) -> None:
    flag = "" if conf >= CONFIDENCE_THRESHOLD else "  <- LOW CONFIDENCE, would route to manual review"
    print(f"  {desc:35s} -> {pred:20s} (confidence {conf:.0%}){flag}")


def load_uncategorized(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select transaction_id, description
        from intermediate.int_transactions_categorized
        where category = 'Uncategorized'
    """).df()


def build_pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])


def main() -> None:
    if not os.path.exists(DB_PATH):
        print(f"No warehouse found at {DB_PATH}. Run `make pipeline` (or the dbt commands) first.")
        return

    con = duckdb.connect(DB_PATH, read_only=True)
    labeled = load_training_data(con)
    uncategorized = load_uncategorized(con)
    con.close()

    n_merchants = labeled["merchant_name"].nunique()
    print(f"Loaded {len(labeled)} rule-labeled transactions across "
          f"{labeled['category'].nunique()} categories and only {n_merchants} distinct merchants.")
    print(f"(Average of {len(labeled) / n_merchants:.0f} transactions per merchant -- "
          f"a random split below will see the same merchants in both train and test.)")

    # ---- Train/test split ----
    # NOTE: this is a random split over *transactions*, not merchants. With
    # so few distinct merchants repeated many times, the same merchant
    # shows up on both sides of the split. The accuracy below is therefore
    # a memorization check ("does it recognize merchants it's already
    # seen"), not a generalization test. See the NOVEL_EXAMPLES section
    # further down for the test that actually matters.
    X_train, X_test, y_train, y_test = train_test_split(
        labeled["description"], labeled["category"],
        test_size=0.2, random_state=42, stratify=labeled["category"],
    )

    eval_pipeline = build_pipeline()
    eval_pipeline.fit(X_train, y_train)
    y_pred = eval_pipeline.predict(X_test)

    print(f"\nHeld-out accuracy on ALREADY-SEEN merchants (memorization check, "
          f"not generalization -- see note above): {accuracy_score(y_test, y_pred):.1%}")
    print("\nClassification report (same caveat applies):")
    print(classification_report(y_test, y_pred, zero_division=0))

    # ---- Refit on ALL labeled data for the model we actually deploy ----
    # Standard practice: the train/test split above exists purely to
    # measure performance honestly; once you trust that number, you
    # retrain on everything you've got before using the model for real.
    final_pipeline = build_pipeline()
    final_pipeline.fit(labeled["description"], labeled["category"])

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(final_pipeline, MODEL_PATH)
    print(f"\nSaved trained model to {MODEL_PATH}")

    # ---- Apply to transactions the rules actually couldn't categorize ----
    if len(uncategorized) == 0:
        print("\nNo 'Uncategorized' transactions in the current warehouse "
              "(the rules caught everything in this dataset -- expected, "
              "since the rules were written against it).")
    else:
        preds = final_pipeline.predict(uncategorized["description"])
        confidences = final_pipeline.predict_proba(uncategorized["description"]).max(axis=1)
        print(f"\nSuggested categories for {len(uncategorized)} previously-Uncategorized transaction(s):")
        for desc, pred, conf in zip(uncategorized["description"], preds, confidences):
            print_flagged(desc, pred, conf)

    # ---- The test that actually matters: merchants never seen at all ----
    print(f"\nPredictions on merchants the rule-based macro has never encountered "
          f"(the real generalization test -- flagged below {CONFIDENCE_THRESHOLD:.0%} confidence):")
    novel_preds = final_pipeline.predict(NOVEL_EXAMPLES)
    novel_confidences = final_pipeline.predict_proba(NOVEL_EXAMPLES).max(axis=1)
    n_low_conf = 0
    for desc, pred, conf in zip(NOVEL_EXAMPLES, novel_preds, novel_confidences):
        print_flagged(desc, pred, conf)
        if conf < CONFIDENCE_THRESHOLD:
            n_low_conf += 1

    print(f"\n{n_low_conf}/{len(NOVEL_EXAMPLES)} novel-merchant predictions fell below the "
          f"{CONFIDENCE_THRESHOLD:.0%} confidence threshold. With only {n_merchants} distinct "
          f"merchants to learn from, that's expected, not a bug -- the model is correctly "
          f"signaling low confidence on genuinely unfamiliar input rather than guessing "
          f"confidently and wrong. The fix for this in a real deployment isn't a fancier "
          f"model, it's more labeled transaction history over time.")


if __name__ == "__main__":
    main()