"""
Renders dashboard_html.render_dashboard_html() with hand-written sample
data and writes the result to a file -- lets you eyeball the exact HTML
app.py will embed, without needing the dbt/duckdb pipeline set up.

Run with:  python3 scripts/preview_dashboard_html.py [output_path]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
from dashboard_html import render_dashboard_html  # noqa: E402

SAMPLE_DATA = {
    "month_label": "August 2026",
    "net": -307,
    "total_income": 3200,
    "total_spent": 3507,
    "top_category_name": "Rent",
    "top_category_amt": 1450,
    "spark_values": [120, -80, 340, -210, 95, -307],
    "categories": [
        {"name": "Rent", "amt": 1450, "pct": 100},
        {"name": "Groceries", "amt": 412, "pct": 38},
        {"name": "Dining & Coffee", "amt": 268, "pct": 24},
        {"name": "Travel", "amt": 224, "pct": 20},
        {"name": "Shopping", "amt": 158, "pct": 14},
    ],
    "trend_months": ["Mar 2026", "Apr 2026", "May 2026", "Jun 2026", "Jul 2026", "Aug 2026", "Sep 2026"],
    "trend_values": [2890, 3120, 2760, 3340, 3005, 3507, 3180],
    "budget_rows": [
        {"category": "Groceries", "budgeted": 400, "actual": 412, "pct_fill": 103, "over": True},
        {"category": "Rent", "budgeted": 1450, "actual": 1450, "pct_fill": 100, "over": False},
        {"category": "Dining & Coffee", "budgeted": 300, "actual": 268, "pct_fill": 89, "over": False},
    ],
    "recurring_rows": [
        {"name": "Netflix", "initials": "NF", "category": "Subscriptions", "count": 12, "lifetime": 180},
        {"name": "Spotify", "initials": "SP", "category": "Subscriptions", "count": 12, "lifetime": 120},
    ],
}

if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "dashboard_preview6.html"
    html = render_dashboard_html(SAMPLE_DATA)
    with open(out_path, "w") as f:
        f.write(html)
    print(f"wrote {out_path} ({len(html):,} chars)")
