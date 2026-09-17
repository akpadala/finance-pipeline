"""
Streamlit budget dashboard.

Reads exclusively from the dbt marts (mart_monthly_spending,
mart_budget_vs_actual, dim_merchant) -- never from raw or staging, and
does no aggregation of its own. That separation matters: if a number on
this dashboard is wrong, the fix belongs in a dbt model (where it's
tested and version-controlled), not in app code. The app's only job is
to query already-correct numbers and draw them.

Run with:  streamlit run dashboard/app.py   (from the repo root)
"""

import os

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "warehouse", "finance.duckdb")

st.set_page_config(page_title="Personal Finance Pipeline", page_icon="\U0001F4B0", layout="wide")


@st.cache_resource
def get_connection():
    if not os.path.exists(DB_PATH):
        st.error(
            "No warehouse found yet. From the repo root, run:\n\n"
            "1. `python3 scripts/generate_sample_data.py`\n"
            "2. `python3 ingestion/load_to_duckdb.py`\n"
            "3. `cd finance_dbt && dbt seed && dbt run`\n\n"
            "then restart this dashboard."
        )
        st.stop()
    return duckdb.connect(DB_PATH, read_only=True)


@st.cache_data(ttl=60)
def load_marts():
    con = get_connection()
    monthly = con.execute("select * from marts.mart_monthly_spending").df()
    budget = con.execute("select * from marts.mart_budget_vs_actual").df()
    merchants = con.execute(
        "select * from marts.dim_merchant where is_recurring_merchant order by lifetime_spend desc"
    ).df()
    monthly["month"] = pd.to_datetime(monthly["month"])
    budget["month"] = pd.to_datetime(budget["month"])
    return monthly, budget, merchants


monthly_df, budget_df, recurring_df = load_marts()

st.title("Personal Finance Pipeline")
st.caption(
    "Every number below comes from a dbt model, not this app. "
    "See finance_dbt/models/marts/ to see exactly how each is computed."
)

# ---- Month selector ----
available_months = sorted(monthly_df["month"].dt.to_period("M").unique(), reverse=True)
month_labels = [m.strftime("%B %Y") for m in available_months]
selected_label = st.sidebar.selectbox("Month", month_labels, index=0)
selected_month = available_months[month_labels.index(selected_label)]

st.sidebar.markdown("---")
st.sidebar.caption(
    "Data source: data/raw/*.csv → DuckDB (raw) → dbt (staging → "
    "intermediate → marts) → this dashboard."
)

month_data = monthly_df[monthly_df["month"].dt.to_period("M") == selected_month]
month_budget = budget_df[budget_df["month"].dt.to_period("M") == selected_month]

# ---- KPI row ----
total_spent = month_data["total_spent"].sum()
total_income = month_data["total_income"].sum()
net = total_income - total_spent
top_category_row = month_data.loc[month_data["total_spent"].idxmax()] if not month_data.empty else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total spent", f"${total_spent:,.0f}")
col2.metric("Total income", f"${total_income:,.0f}")
col3.metric("Net", f"${net:,.0f}", delta=f"{'surplus' if net >= 0 else 'deficit'}")
if top_category_row is not None:
    col4.metric("Top category", top_category_row["category"], f"${top_category_row['total_spent']:,.0f}")

st.markdown("---")

left, right = st.columns([1, 1])

with left:
    st.subheader(f"Spending by category — {selected_label}")
    spend_only = month_data[month_data["category"] != "Income"].sort_values("total_spent", ascending=False)
    if spend_only.empty:
        st.info("No spending recorded this month.")
    else:
        fig = px.bar(
            spend_only,
            x="total_spent",
            y="category",
            orientation="h",
            labels={"total_spent": "Spent ($)", "category": ""},
        )
        fig.update_layout(yaxis=dict(categoryorder="total ascending"), height=420)
        st.plotly_chart(fig, width='stretch')

with right:
    st.subheader("Monthly spending trend")
    trend = (
        monthly_df[monthly_df["category"] != "Income"]
        .groupby("month", as_index=False)["total_spent"]
        .sum()
        .sort_values("month")
    )
    fig = px.line(trend, x="month", y="total_spent", markers=True, labels={"total_spent": "Total spent ($)"})
    fig.update_layout(height=420)
    st.plotly_chart(fig, width='stretch')

st.markdown("---")
st.subheader(f"Budget vs. actual — {selected_label}")

if month_budget.empty:
    st.info("No budget data for this month.")
else:
    display_df = month_budget[[
        "category", "budgeted_amount", "actual_spent", "variance", "pct_of_budget", "is_over_budget"
    ]].sort_values("variance", ascending=False)

    def highlight_over(row):
    # Explicit text color alongside the background -- without it, a
    # dark Streamlit theme renders light default text on these light
    # fills and the numbers become unreadable.
        style = "background-color: #ffe3e3; color: #7a1f1f" if row["is_over_budget"] \
            else "background-color: #e3ffe8; color: #1f6b2f"
        return [style] * len(row)

    st.dataframe(
        display_df.style.apply(highlight_over, axis=1).format({
            "budgeted_amount": "${:,.2f}",
            "actual_spent": "${:,.2f}",
            "variance": "${:,.2f}",
            "pct_of_budget": "{:.0f}%",
        }),
        width='stretch',
        hide_index=True,
    )

    over_budget_categories = display_df[display_df["is_over_budget"]]["category"].tolist()
    if over_budget_categories:
        st.warning(f"Over budget in: {', '.join(over_budget_categories)}")
    else:
        st.success("Under budget in every category this month.")

st.markdown("---")
st.subheader("Detected recurring charges")
st.caption(
    "Flagged by dbt when the same merchant + amount shows up in 3+ distinct months "
    "(see int_transactions_categorized.sql). Handy for spotting subscriptions you forgot about."
)
if recurring_df.empty:
    st.info("No recurring charges detected yet.")
else:
    st.dataframe(
        recurring_df[["merchant_name", "category", "lifetime_spend", "transaction_count"]].rename(columns={
            "merchant_name": "Merchant", "category": "Category",
            "lifetime_spend": "Lifetime spend", "transaction_count": "# transactions",
        }).style.format({"Lifetime spend": "${:,.2f}"}),
        width='stretch',
        hide_index=True,
    )
