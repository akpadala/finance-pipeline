"""
Generates a realistic, synthetic bank/credit-card transaction export.

Why this exists
----------------
Real bank CSV exports (Chase, Capital One, Amex, etc.) share a common shape:
a transaction date, a post date, a free-text description, a bank-assigned
"category" that is often inconsistent or missing, a debit/credit type, and
an amount. This script fabricates ~12 months of that kind of data so the
rest of the pipeline (ingestion -> dbt -> dashboard) has something real to
chew on, without needing anyone's actual financial data.

Deliberately noisy on purpose: the bank's own `category` column below is
inconsistent (blank sometimes, overly generic other times) -- exactly like
real exports. That's the reason the dbt layer builds its own categorization
logic instead of trusting the source data as-is. This is a genuine, common
data engineering problem: source systems rarely hand you clean dimensions.

No third-party packages are used here on purpose (only Python's standard
library), so this script also works before you've set up your virtualenv.

Usage:
    python3 scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta

# Fixed seed => everyone who runs this gets the identical dataset, so dbt
# test results and dashboard numbers are reproducible for anyone who
# clones the repo and runs it.
RANDOM_SEED = 42
random.seed(RANDOM_SEED)

START_DATE = date(2025, 9, 1)
END_DATE = date(2026, 8, 31)
OUTPUT_PATH = "data/raw/sample_transactions.csv"

MONTHLY_TAKE_HOME_PAY = 3200.00
PAYDAY_DAYS = (1, 15)  # paid twice a month


@dataclass
class Merchant:
    description: str          # what shows up on the statement
    bank_category: str        # the bank's own (messy) category label
    min_amount: float
    max_amount: float
    # how many times per month this merchant is typically hit
    monthly_frequency: float
    # if set, this merchant is a fixed recurring charge (subscriptions, rent)
    fixed_amount: float | None = None
    # restrict to specific months (1-12) for seasonal merchants; None = all year
    active_months: tuple[int, ...] | None = None


# The bank_category values are intentionally inconsistent -- some are
# specific, some are vague ("Other"), some are blank (""). This mirrors
# real exports and is the reason we build our own categorization in dbt.
MERCHANTS: list[Merchant] = [
    # --- Groceries ---
    Merchant("TRADER JOE'S #112", "Groceries", 25, 95, 4.0),
    Merchant("WHOLE FOODS MKT", "Groceries", 20, 110, 3.0),
    Merchant("STOP & SHOP", "Groceries", 15, 90, 3.0),

    # --- Dining / coffee ---
    Merchant("STARBUCKS STORE 4821", "Food & Drink", 4, 9, 6.0),
    Merchant("CHIPOTLE 0392", "Restaurants", 9, 16, 3.0),
    Merchant("SWEETGREEN", "Restaurants", 11, 17, 2.0),
    Merchant("DOORDASH*PANDA EXPRESS", "Food & Drink", 15, 35, 2.5),
    Merchant("UBER EATS", "", 12, 40, 2.0),
    Merchant("LOCAL PIZZA CO", "Restaurants", 18, 40, 1.5),

    # --- Recurring subscriptions (fixed amount, monthly) ---
    Merchant("NETFLIX.COM", "Subscription", 0, 0, 1.0, fixed_amount=15.49),
    Merchant("SPOTIFY USA", "Subscription", 0, 0, 1.0, fixed_amount=11.99),
    Merchant("AMAZON PRIME", "Shopping", 0, 0, 1.0, fixed_amount=14.99),
    Merchant("ADOBE CREATIVE CLOUD", "", 0, 0, 1.0, fixed_amount=20.99),
    Merchant("PLANET FITNESS", "Health & Fitness", 0, 0, 1.0, fixed_amount=24.99),
    Merchant("ICLOUD STORAGE", "", 0, 0, 1.0, fixed_amount=2.99),
    Merchant("NYTIMES SUBSCRIPTION", "Other", 0, 0, 1.0, fixed_amount=4.25),

    # --- Fixed monthly bills ---
    Merchant("RIVERSIDE APARTMENTS RENT", "Rent", 0, 0, 1.0, fixed_amount=1450.00),
    Merchant("NATIONAL GRID UTILITY", "Bills & Utilities", 60, 140, 1.0),
    Merchant("VERIZON WIRELESS", "Bills & Utilities", 0, 0, 1.0, fixed_amount=85.00),
    Merchant("XFINITY INTERNET", "Bills & Utilities", 0, 0, 1.0, fixed_amount=70.00),

    # --- Transportation ---
    Merchant("UBER TRIP", "Travel", 8, 35, 3.0),
    Merchant("SHELL OIL", "Gas", 30, 55, 2.5),
    Merchant("MBTA MOBILE TICKETING", "Travel", 2, 10, 4.0),

    # --- Shopping ---
    Merchant("AMAZON.COM*MARKETPLACE", "Shopping", 10, 120, 4.0),
    Merchant("TARGET", "Shopping", 15, 100, 2.0),
    Merchant("CVS PHARMACY", "", 8, 45, 2.0),

    # --- Entertainment / misc ---
    Merchant("AMC THEATRES", "Entertainment", 13, 40, 0.8),
    Merchant("STEAM GAMES", "Entertainment", 5, 60, 0.6),

    # --- Seasonal: travel spikes in summer + winter holidays ---
    Merchant("DELTA AIR LINES", "Travel", 150, 450, 0.3, active_months=(6, 7, 8, 12)),
    Merchant("AIRBNB", "Travel", 120, 500, 0.3, active_months=(6, 7, 8, 12)),

    # --- Seasonal: holiday shopping bump ---
    Merchant("BEST BUY", "Shopping", 40, 300, 0.4, active_months=(11, 12)),
]

# Occasional one-off big-ticket purchases to make the data feel real
ONE_OFF_EVENTS = [
    ("BEST BUY - LAPTOP", "Shopping", date(2025, 11, 28), -899.00),
    ("DELTA DENTAL - PROCEDURE", "Healthcare", date(2026, 2, 12), -410.00),
    ("SOUTHWEST AIRLINES", "Travel", date(2026, 3, 6), -276.40),
]


def month_range(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def random_day_in_month(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    days_in_month = (next_month - date(year, month, 1)).days
    return date(year, month, random.randint(1, days_in_month))


def generate_rows() -> list[dict]:
    rows: list[dict] = []

    for year, month in month_range(START_DATE, END_DATE):
        # --- Income: paycheck twice a month ---
        for pay_day in PAYDAY_DAYS:
            try:
                d = date(year, month, pay_day)
            except ValueError:
                continue
            if d < START_DATE or d > END_DATE:
                continue
            rows.append({
                "date": d,
                "description": "ACME CORP PAYROLL DIRECT DEP",
                "bank_category": "Income",
                "type": "Credit",
                "amount": round(MONTHLY_TAKE_HOME_PAY / 2, 2),
            })

        # --- Merchant-driven spending ---
        for merchant in MERCHANTS:
            if merchant.active_months and month not in merchant.active_months:
                continue

            # frequency can be fractional (e.g. 0.3x/month); treat as a
            # probability of occurring at all, then round up to whole hits
            freq = merchant.monthly_frequency
            whole_hits = int(freq)
            fractional = freq - whole_hits
            hits = whole_hits + (1 if random.random() < fractional else 0)

            for _ in range(hits):
                d = random_day_in_month(year, month)
                if d < START_DATE or d > END_DATE:
                    continue
                if merchant.fixed_amount is not None:
                    amount = merchant.fixed_amount
                else:
                    amount = round(random.uniform(merchant.min_amount, merchant.max_amount), 2)
                rows.append({
                    "date": d,
                    "description": merchant.description,
                    "bank_category": merchant.bank_category,
                    "type": "Sale",
                    "amount": -amount,  # spending is negative, like a real export
                })

    # --- One-off big purchases ---
    for description, bank_category, d, amount in ONE_OFF_EVENTS:
        rows.append({
            "date": d,
            "description": description,
            "bank_category": bank_category,
            "type": "Sale",
            "amount": amount,
        })

    rows.sort(key=lambda r: r["date"])
    return rows


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        # Shaped like a real Chase/Capital One credit-card export.
        writer.writerow(["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"])
        for r in rows:
            post_date = r["date"] + timedelta(days=random.choice([0, 1, 2]))
            writer.writerow([
                r["date"].isoformat(),
                post_date.isoformat(),
                r["description"],
                r["bank_category"],
                r["type"],
                f"{r['amount']:.2f}",
                "",  # Memo -- almost always blank in real exports
            ])


def main() -> None:
    rows = generate_rows()
    write_csv(rows, OUTPUT_PATH)
    total_spend = sum(-r["amount"] for r in rows if r["amount"] < 0)
    total_income = sum(r["amount"] for r in rows if r["amount"] > 0)
    print(f"Wrote {len(rows)} transactions to {OUTPUT_PATH}")
    print(f"  Date range: {START_DATE} to {END_DATE}")
    print(f"  Total income:  ${total_income:,.2f}")
    print(f"  Total spend:   ${total_spend:,.2f}")
    print(f"  Net:           ${total_income - total_spend:,.2f}")


if __name__ == "__main__":
    main()
