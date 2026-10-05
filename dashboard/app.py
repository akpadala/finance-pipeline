"""
Streamlit budget dashboard.

Reads from the dbt marts (mart_monthly_spending, mart_budget_vs_actual,
dim_merchant) -- never from raw or staging, and does no aggregation of
its own (that's transform.py's job, reshaping already-correct numbers;
the actual math lives in dbt, tested and version-controlled). If a number
on this dashboard is wrong, the fix belongs in a dbt model, not here.

Layout: the whole page body (nav, hero, charts, panels) is one
self-contained HTML document built by dashboard_html.py and rendered via
st.components.v1.html() -- see that module's docstring for why (sticky
nav + smooth-scroll sections aren't achievable with native Streamlit
layout, and components.html() always renders inside an iframe, which has
its own implications spelled out there). This file's job is just: load
data, let the user optionally swap in their own upload instead of the
sample data (see secure_upload.py), and hand the result to the renderer.

Run with:  streamlit run dashboard/app.py   (from the repo root)
"""

import os
import sys

import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion"))

import dashboard_html  # noqa: E402
import load_to_duckdb  # noqa: E402
import secure_upload  # noqa: E402
import transform  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "warehouse", "finance.duckdb")

st.set_page_config(page_title="Personal Finance Pipeline", page_icon=":bar_chart:", layout="wide")


def _inject_outer_css() -> None:
    # Everything that used to live here (hero-card, panel, cat-row, etc.)
    # now lives inside dashboard_html.CSS, rendered inside the iframe.
    # This is just enough to make Streamlit's own chrome -- the sidebar,
    # the page background behind the iframe -- match that page instead of
    # clashing with it, and to keep the sidebar in the same no-emoji,
    # Inter-only style as the rest of the app.
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: #2B2B26; }
        .stApp { background-color: #F7F3EA; }
        /* padding-top used to be 1.2rem, which wasn't enough clearance below
        Streamlit's own fixed header bar -- the top of our sticky nav (and
        the hero section behind it) was getting rendered partly underneath
        it. This is a guess at a safe value, not measured against every
        Streamlit version; nudge it further if there's still a sliver cut off. */
        .block-container { padding-top: 3rem !important; padding-bottom: 0 !important; max-width: 1440px; }
        [data-testid="stSidebar"] { background-color: #FFFFFF; border-right: 1px solid #E3DDC9; }
        [data-testid="stCaptionContainer"], .stCaption { color: #6B6358 !important; }
        #MainMenu, footer { visibility: hidden; }
        .side-brand { font-weight: 800; color: #1F3328; font-size: 1.0rem; margin-bottom: 4px; letter-spacing: -0.01em; }
        .side-sub { font-size: 0.78rem; color: #6B6358; margin-bottom: 18px; }
        iframe { border: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_connection():
    if not os.path.exists(DB_PATH):
        st.error(
            "No warehouse found yet. From the repo root, run:\n\n"
            "1. `python3 scripts/generate_sample_data.py`\n"
            "2. `python3 ingestion/load_to_duckdb.py`\n"
            "3. `cd finance_dbt && dbt seed && dbt run`\n\n"
            "then restart this dashboard. (Or upload your own CSV in the sidebar --\n"
            "that path doesn't need the shared warehouse set up at all.)"
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


_inject_outer_css()

st.sidebar.markdown('<div class="side-brand">Personal Finance</div>', unsafe_allow_html=True)
st.sidebar.markdown(
    '<div class="side-sub">Every number traces back to a tested dbt model.</div>',
    unsafe_allow_html=True,
)

# ---- Upload: try the dashboard against your own data ----
with st.sidebar.expander("Try with your own data", expanded=False):
    st.caption(
        "Upload a CSV of your own transactions to see this dashboard built from your "
        "real numbers instead of the sample data below. Your file is encrypted the "
        "moment it's received, processed through the exact same dbt models in an "
        "isolated, disposable database, and permanently deleted right after -- "
        "nothing is saved, logged, or shared. Full details in the README."
    )
    with st.expander("Expected CSV columns"):
        st.code(", ".join(load_to_duckdb.REQUIRED_COLUMNS) + ", Memo (optional)")
        st.caption("Rename your bank export's columns to match this shape before uploading.")

    uploaded = st.file_uploader("Bank CSV", type="csv", label_visibility="collapsed")

    if uploaded is not None:
        file_id = getattr(uploaded, "file_id", None) or (uploaded.name, uploaded.size)
        if st.session_state.get("_upload_file_id") != file_id:
            with st.spinner("Encrypting and processing your transactions..."):
                try:
                    marts = secure_upload.process_uploaded_csv(uploaded.getvalue(), uploaded.name)
                    st.session_state["_upload_marts"] = marts
                    st.session_state["_upload_filename"] = uploaded.name
                except secure_upload.UploadProcessingError as e:
                    st.session_state.pop("_upload_marts", None)
                    st.error(str(e))
            st.session_state["_upload_file_id"] = file_id

    if st.session_state.get("_upload_marts") is not None:
        st.success(f"Showing your data from **{st.session_state.get('_upload_filename', 'your upload')}**.")
        if st.button("Clear my data", use_container_width=True):
            secure_upload.clear_session_upload()
            st.rerun()

# ---- Pick the data source: an active upload, or the shared warehouse ----
using_upload = st.session_state.get("_upload_marts") is not None
if using_upload:
    monthly_df, budget_df, recurring_df = st.session_state["_upload_marts"]
else:
    monthly_df, budget_df, recurring_df = load_marts()

st.sidebar.markdown("---")
available_months = transform.available_months(monthly_df)
if not available_months:
    st.warning("No data to show yet.")
    st.stop()

month_labels = [m.strftime("%B %Y") for m in available_months]
selected_label = st.sidebar.selectbox("Month", month_labels, index=0)
selected_month = available_months[month_labels.index(selected_label)]

st.sidebar.markdown("---")
if using_upload:
    st.sidebar.caption("Data source: your uploaded CSV -> an isolated, disposable DuckDB database -> dbt -> this dashboard.")
else:
    st.sidebar.caption("Data source: data/raw/*.csv -> DuckDB (raw) -> dbt (staging -> intermediate -> marts) -> this dashboard.")

data = transform.build_view_data(monthly_df, budget_df, recurring_df, selected_month)

# The nav is rendered separately from the dashboard body, and directly into
# Streamlit's own page (not into the iframe below) -- see dashboard_html.py's
# module docstring for why that's what makes it genuinely sticky. Its
# behavior (click-to-scroll, highlighting the active link) comes from a
# second, invisible (height=0) component -- st.markdown can't reliably run
# <script> tags, only st.components.v1.html() actually can; see
# render_topnav_script_html()'s docstring.
st.markdown(dashboard_html.render_topnav_html(), unsafe_allow_html=True)
st.components.v1.html(dashboard_html.render_topnav_script_html(), height=0)

html = dashboard_html.render_dashboard_html(data)

# height is just a starting guess -- the HTML's own resize script (see the
# bottom of dashboard_html._BODY_TEMPLATE) immediately corrects this to the
# real content height via window.frameElement, and scrolling=False means
# there's never a separate scrollbar inside this box: the outer Streamlit
# page scrolls through the whole dashboard as one continuous page.
st.components.v1.html(html, height=1900, scrolling=False)
