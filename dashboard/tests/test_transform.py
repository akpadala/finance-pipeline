"""
Plain-assertion tests for dashboard/transform.py -- deliberately not
pytest, since the goal is a test anyone can run with nothing but the
stdlib + pandas installed (no test framework needed), matching how the
rest of this project avoids extra dependencies where it can.

Run with:  python3 dashboard/tests/test_transform.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402

from transform import available_months, build_view_data  # noqa: E402


def _sample_marts():
    monthly_df = pd.DataFrame([
        {"month": "2026-07-01", "category": "Income", "category_group": "Income",
         "transaction_count": 2, "total_spent": 0, "total_income": 3200},
        {"month": "2026-07-01", "category": "Rent", "category_group": "Housing",
         "transaction_count": 1, "total_spent": 1450, "total_income": 0},
        {"month": "2026-07-01", "category": "Groceries", "category_group": "Food",
         "transaction_count": 10, "total_spent": 380, "total_income": 0},
        {"month": "2026-08-01", "category": "Income", "category_group": "Income",
         "transaction_count": 2, "total_spent": 0, "total_income": 3200},
        {"month": "2026-08-01", "category": "Rent", "category_group": "Housing",
         "transaction_count": 1, "total_spent": 1450, "total_income": 0},
        {"month": "2026-08-01", "category": "Groceries", "category_group": "Food",
         "transaction_count": 12, "total_spent": 412, "total_income": 0},
        {"month": "2026-08-01", "category": "Dining & Coffee", "category_group": "Food",
         "transaction_count": 8, "total_spent": 268, "total_income": 0},
    ])
    monthly_df["month"] = pd.to_datetime(monthly_df["month"])

    budget_df = pd.DataFrame([
        {"month": "2026-08-01", "category": "Rent", "budgeted_amount": 1450, "actual_spent": 1450,
         "variance": 0, "pct_of_budget": 100.0, "is_over_budget": False},
        {"month": "2026-08-01", "category": "Groceries", "budgeted_amount": 400, "actual_spent": 412,
         "variance": 12, "pct_of_budget": 103.0, "is_over_budget": True},
    ])
    budget_df["month"] = pd.to_datetime(budget_df["month"])

    recurring_df = pd.DataFrame([
        {"merchant_name": "Netflix", "category": "Subscriptions", "transaction_count": 12,
         "lifetime_spend": 180.0, "is_recurring_merchant": True},
        {"merchant_name": "Spotify", "category": "Subscriptions", "transaction_count": 12,
         "lifetime_spend": 120.0, "is_recurring_merchant": True},
    ])

    return monthly_df, budget_df, recurring_df


def test_available_months_sorted_desc():
    monthly_df, _, _ = _sample_marts()
    months = available_months(monthly_df)
    assert [str(m) for m in months] == ["2026-08", "2026-07"], months


def test_available_months_empty():
    assert available_months(pd.DataFrame(columns=["month"])) == []


def test_net_and_totals():
    monthly_df, budget_df, recurring_df = _sample_marts()
    months = available_months(monthly_df)
    data = build_view_data(monthly_df, budget_df, recurring_df, months[0])  # August
    assert data["total_income"] == 3200.0
    assert data["total_spent"] == 2130.0  # 1450 + 412 + 268
    assert data["net"] == 1070.0
    assert data["month_label"] == "August 2026"


def test_sparkline_reflects_income_not_just_spend():
    # Regression test: an earlier version of the sparkline math only
    # looked at the Income category rows' total_spent column, which is
    # always 0 there by construction (mart_monthly_spending.sql splits
    # spend/income into separate columns, never both on one row). That
    # silently dropped income from the sparkline entirely, so a $3,200
    # paycheck month could still render as a steep negative dip. Net
    # per month must equal total_income - total_spent for *that* month.
    monthly_df, budget_df, recurring_df = _sample_marts()
    months = available_months(monthly_df)
    data = build_view_data(monthly_df, budget_df, recurring_df, months[0])
    # oldest -> newest: Jul (3200 - 1830 = 1370), Aug (3200 - 2130 = 1070)
    assert data["spark_values"] == [1370.0, 1070.0], data["spark_values"]


def test_categories_sorted_desc_with_pct_of_max():
    monthly_df, budget_df, recurring_df = _sample_marts()
    months = available_months(monthly_df)
    data = build_view_data(monthly_df, budget_df, recurring_df, months[0])
    names = [c["name"] for c in data["categories"]]
    assert names == ["Rent", "Groceries", "Dining & Coffee"], names
    assert data["categories"][0]["pct"] == 100.0  # Rent is the max
    assert "Income" not in names  # income is never shown as a spending category


def test_budget_rows_and_over_flag():
    monthly_df, budget_df, recurring_df = _sample_marts()
    months = available_months(monthly_df)
    data = build_view_data(monthly_df, budget_df, recurring_df, months[0])
    by_cat = {b["category"]: b for b in data["budget_rows"]}
    assert by_cat["Groceries"]["over"] is True
    assert by_cat["Rent"]["over"] is False
    assert by_cat["Groceries"]["actual"] == 412.0


def test_recurring_rows_initials():
    monthly_df, budget_df, recurring_df = _sample_marts()
    months = available_months(monthly_df)
    data = build_view_data(monthly_df, budget_df, recurring_df, months[0])
    by_name = {r["name"]: r for r in data["recurring_rows"]}
    assert by_name["Netflix"]["initials"] == "N"
    assert by_name["Netflix"]["lifetime"] == 180.0


def test_empty_month_handled_gracefully():
    monthly_df, budget_df, recurring_df = _sample_marts()
    # A month with no budget rows and no spending at all (e.g. first month
    # of real use, before any budget has been set) shouldn't raise.
    empty_month = pd.DataFrame(columns=monthly_df.columns)
    empty_month["month"] = pd.to_datetime(empty_month["month"])
    combined = pd.concat([monthly_df, pd.DataFrame([{
        "month": pd.Timestamp("2026-09-01"), "category": "Income", "category_group": "Income",
        "transaction_count": 0, "total_spent": 0, "total_income": 0,
    }])], ignore_index=True)
    months = available_months(combined)
    data = build_view_data(combined, budget_df, recurring_df, months[0])  # Sep: all zeros
    assert data["total_spent"] == 0.0
    assert data["categories"] == []
    assert data["top_category_name"] is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print()
    if failures:
        print(f"{failures}/{len(tests)} test(s) failed.")
        sys.exit(1)
    else:
        print(f"All {len(tests)} test(s) passed.")
