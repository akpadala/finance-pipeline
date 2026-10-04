"""
Turns the three dbt marts (as pandas DataFrames, queried exactly the way
load_marts() in app.py does) into the plain-dict shape
dashboard_html.render_dashboard_html() expects.

Why this is its own module: it's shared by both app.py's normal path
(reading the shared warehouse built from sample data, or your own local
data -- see scripts/encrypt_bank_csv.py) and secure_upload.py's path
(reading an ephemeral per-session database built from an uploaded CSV).
Those two paths should never compute these numbers differently -- the
whole point of the dbt-first design (see app.py's module docstring) is
that there's exactly one place spend/budget/recurring math happens, and
it isn't here or in app.py, it's in the dbt models. This module just
reshapes already-correct numbers for the template.
"""

from __future__ import annotations

import pandas as pd


def available_months(monthly_df: pd.DataFrame) -> list:
    """Sorted (most recent first) list of pandas Period[M] present in
    monthly_df -- what the month selector is built from."""
    if monthly_df.empty:
        return []
    return sorted(monthly_df["month"].dt.to_period("M").unique(), reverse=True)


def build_view_data(
    monthly_df: pd.DataFrame,
    budget_df: pd.DataFrame,
    recurring_df: pd.DataFrame,
    selected_month,
) -> dict:
    """selected_month is a pandas Period[M] (one of available_months()'s
    entries). Returns the dict shape dashboard_html.render_dashboard_html()
    expects -- see that function's docstring for the exact keys."""
    month_label = selected_month.strftime("%B %Y")
    month_data = monthly_df[monthly_df["month"].dt.to_period("M") == selected_month]
    month_budget = budget_df[budget_df["month"].dt.to_period("M") == selected_month]

    total_spent = float(month_data["total_spent"].sum())
    total_income = float(month_data["total_income"].sum())
    net = total_income - total_spent

    spend_only = month_data[month_data["category"] != "Income"].sort_values(
        "total_spent", ascending=False
    )
    # Excludes the Income row on purpose: "top category" means top
    # *spending* category. Without this filter, a month with no spending
    # recorded yet (just an Income row) would idxmax onto Income itself
    # and show it back as "top category" -- caught by
    # dashboard/tests/test_transform.py::test_empty_month_handled_gracefully.
    top_category_row = spend_only.iloc[0] if not spend_only.empty else None

    # last 6 months of net, oldest -> newest, for the hero sparkline.
    # Each mart row already carries total_spent/total_income correctly
    # split (a row is one or the other, never both), so summing both
    # columns across every row in the month -- not just the "Income"
    # category rows -- is what actually gives income minus spend. (An
    # earlier version of this only looked at the Income category rows'
    # total_spent column, which is always 0 there by construction -- see
    # mart_monthly_spending.sql -- so the sparkline silently ignored
    # income entirely. Caught by dashboard/tests/test_transform.py.)
    net_by_month = (
        monthly_df.groupby("month")
        .apply(lambda g: g["total_income"].sum() - g["total_spent"].sum())
        .sort_index()
    )
    spark_values = [float(v) for v in net_by_month.tail(6).tolist()]

    categories = []
    if not spend_only.empty:
        max_val = spend_only["total_spent"].max()
        for _, r in spend_only.iterrows():
            pct = (r["total_spent"] / max_val * 100) if max_val else 0
            categories.append({"name": r["category"], "amt": float(r["total_spent"]), "pct": float(pct)})

    trend = (
        monthly_df[monthly_df["category"] != "Income"]
        .groupby("month", as_index=False)["total_spent"]
        .sum()
        .sort_values("month")
    )
    trend_months = trend["month"].dt.strftime("%b %Y").tolist()
    trend_values = [float(v) for v in trend["total_spent"].tolist()]

    budget_rows = []
    if not month_budget.empty:
        display_df = month_budget[[
            "category", "budgeted_amount", "actual_spent", "variance", "pct_of_budget", "is_over_budget"
        ]].sort_values("variance", ascending=False)
        for _, r in display_df.iterrows():
            budget_rows.append({
                "category": r["category"],
                "budgeted": float(r["budgeted_amount"]),
                "actual": float(r["actual_spent"]),
                "pct_fill": float(r["pct_of_budget"]),
                "over": bool(r["is_over_budget"]),
            })

    recurring_rows = []
    for _, r in recurring_df.iterrows():
        name = str(r["merchant_name"])
        initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
        recurring_rows.append({
            "name": name,
            "initials": initials,
            "category": r["category"],
            "count": int(r["transaction_count"]),
            "lifetime": float(r["lifetime_spend"]),
        })

    return {
        "month_label": month_label,
        "net": net,
        "total_income": total_income,
        "total_spent": total_spent,
        "top_category_name": top_category_row["category"] if top_category_row is not None else None,
        "top_category_amt": float(top_category_row["total_spent"]) if top_category_row is not None else None,
        "spark_values": spark_values,
        "categories": categories,
        "trend_months": trend_months,
        "trend_values": trend_values,
        "budget_rows": budget_rows,
        "recurring_rows": recurring_rows,
    }
