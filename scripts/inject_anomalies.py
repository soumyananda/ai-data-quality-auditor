"""
inject_anomalies.py
-------------------
Reads clean Olist CSVs from data/raw/, injects 10 synthetic anomalies,
and writes corrupted CSVs to data/injected/.

All random operations use random.seed(42) for full reproducibility.

Usage:
    python scripts/inject_anomalies.py
    python scripts/inject_anomalies.py --source data/raw --output data/injected
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from tabulate import tabulate

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

random.seed(42)
np.random.seed(42)

# ---------------------------------------------------------------------------
# CSV filenames expected in the source directory
# ---------------------------------------------------------------------------

OLIST_FILES: List[str] = [
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "product_category_name_translation.csv",
]

# Short alias → filename mapping (used by anomaly functions)
TABLE_ALIAS: Dict[str, str] = {
    "orders":           "olist_orders_dataset.csv",
    "order_items":      "olist_order_items_dataset.csv",
    "order_payments":   "olist_order_payments_dataset.csv",
    "order_reviews":    "olist_order_reviews_dataset.csv",
    "products":         "olist_products_dataset.csv",
    "sellers":          "olist_sellers_dataset.csv",
    "customers":        "olist_customers_dataset.csv",
    "geolocation":      "olist_geolocation_dataset.csv",
    "category_trans":   "product_category_name_translation.csv",
}

# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_tables(source_dir: Path) -> Dict[str, pd.DataFrame]:
    """Load all 9 Olist CSVs into a dict keyed by alias."""
    tables: Dict[str, pd.DataFrame] = {}
    for alias, filename in TABLE_ALIAS.items():
        filepath = source_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(
                f"Expected file not found: {filepath}\n"
                "Download the Olist dataset from Kaggle and place the CSVs in data/raw/."
            )
        tables[alias] = pd.read_csv(filepath, low_memory=False)
        print(f"  Loaded {alias:20s} → {len(tables[alias]):>8,} rows  ({filename})")
    return tables


# ---------------------------------------------------------------------------
# Anomaly 1 – orphaned_order_items
# ---------------------------------------------------------------------------


def inject_orphaned_order_items(tables: Dict[str, pd.DataFrame]) -> int:
    """
    Drop 3% of orders that have children in order_items.
    Leaves order_items rows referencing non-existent order_ids.
    """
    orders = tables["orders"]
    items = tables["order_items"]

    parent_ids = set(orders["order_id"].unique())
    child_ids = set(items["order_id"].unique())
    eligible_ids = list(parent_ids & child_ids)

    n = max(1, int(len(eligible_ids) * 0.03))
    drop_ids = random.sample(eligible_ids, n)

    tables["orders"] = orders[~orders["order_id"].isin(drop_ids)].reset_index(drop=True)
    return n


# ---------------------------------------------------------------------------
# Anomaly 2 – duplicate_payment_records
# ---------------------------------------------------------------------------


def inject_duplicate_payment_records(tables: Dict[str, pd.DataFrame]) -> int:
    """
    For 2% of orders that have exactly one payment row, insert an exact duplicate.
    """
    payments = tables["order_payments"]

    # Find orders with exactly one payment row
    counts = payments.groupby("order_id").size()
    single_payment_ids = counts[counts == 1].index.tolist()

    n = max(1, int(len(single_payment_ids) * 0.02))
    dup_ids = random.sample(single_payment_ids, n)

    dup_rows = payments[payments["order_id"].isin(dup_ids)].copy()
    tables["order_payments"] = pd.concat(
        [payments, dup_rows], ignore_index=True
    )
    return n


# ---------------------------------------------------------------------------
# Anomaly 3 – future_dated_timestamps
# ---------------------------------------------------------------------------


def inject_future_dated_timestamps(tables: Dict[str, pd.DataFrame]) -> int:
    """
    For 1.5% of delivered orders, set order_delivered_customer_date to a
    random date in 2019 (which would be 'in the future' relative to the
    training-era 2016-2018 data).
    """
    orders = tables["orders"].copy()
    orders["order_delivered_customer_date"] = pd.to_datetime(
        orders["order_delivered_customer_date"], errors="coerce"
    )

    delivered_mask = orders["order_status"] == "delivered"
    eligible_idx = orders[delivered_mask].index.tolist()

    n = max(1, int(len(eligible_idx) * 0.015))
    chosen_idx = random.sample(eligible_idx, n)

    # Generate random dates between 2019-01-01 and 2019-12-31
    start = pd.Timestamp("2019-01-01")
    end = pd.Timestamp("2019-12-31")
    delta_days = (end - start).days

    future_dates = [
        start + pd.Timedelta(days=random.randint(0, delta_days))
        for _ in chosen_idx
    ]

    orders.loc[chosen_idx, "order_delivered_customer_date"] = future_dates
    tables["orders"] = orders
    return n


# ---------------------------------------------------------------------------
# Anomaly 4 – negative_payment_values
# ---------------------------------------------------------------------------


def inject_negative_payment_values(tables: Dict[str, pd.DataFrame]) -> int:
    """Flip the sign of payment_value for 1% of order_payments rows."""
    payments = tables["order_payments"].copy()
    eligible_idx = payments.index.tolist()

    n = max(1, int(len(eligible_idx) * 0.01))
    chosen_idx = random.sample(eligible_idx, n)

    payments.loc[chosen_idx, "payment_value"] = (
        payments.loc[chosen_idx, "payment_value"] * -1
    )
    tables["order_payments"] = payments
    return n


# ---------------------------------------------------------------------------
# Anomaly 5 – null_seller_ids
# ---------------------------------------------------------------------------


def inject_null_seller_ids(tables: Dict[str, pd.DataFrame]) -> int:
    """Set seller_id to NaN for 4% of order_items rows."""
    items = tables["order_items"].copy()
    eligible_idx = items.index.tolist()

    n = max(1, int(len(eligible_idx) * 0.04))
    chosen_idx = random.sample(eligible_idx, n)

    items.loc[chosen_idx, "seller_id"] = np.nan
    tables["order_items"] = items
    return n


# ---------------------------------------------------------------------------
# Anomaly 6 – out_of_range_review_scores
# ---------------------------------------------------------------------------


def inject_out_of_range_review_scores(tables: Dict[str, pd.DataFrame]) -> int:
    """Replace review_score with 0, 6, or 7 for 2% of order_reviews rows."""
    reviews = tables["order_reviews"].copy()
    eligible_idx = reviews.index.tolist()

    n = max(1, int(len(eligible_idx) * 0.02))
    chosen_idx = random.sample(eligible_idx, n)

    invalid_scores = [0, 6, 7]
    reviews.loc[chosen_idx, "review_score"] = [
        random.choice(invalid_scores) for _ in chosen_idx
    ]
    tables["order_reviews"] = reviews
    return n


# ---------------------------------------------------------------------------
# Anomaly 7 – status_delivery_mismatch
# ---------------------------------------------------------------------------


def inject_status_delivery_mismatch(tables: Dict[str, pd.DataFrame]) -> int:
    """
    For 3% of orders that already have a delivery date, set order_status to
    an in-progress state so status contradicts the delivery record.
    """
    orders = tables["orders"].copy()
    orders["order_delivered_customer_date"] = pd.to_datetime(
        orders["order_delivered_customer_date"], errors="coerce"
    )

    has_delivery_mask = orders["order_delivered_customer_date"].notna()
    eligible_idx = orders[has_delivery_mask].index.tolist()

    n = max(1, int(len(eligible_idx) * 0.03))
    chosen_idx = random.sample(eligible_idx, n)

    mismatch_statuses = ["processing", "shipped", "invoiced"]
    orders.loc[chosen_idx, "order_status"] = [
        random.choice(mismatch_statuses) for _ in chosen_idx
    ]
    tables["orders"] = orders
    return n


# ---------------------------------------------------------------------------
# Anomaly 8 – category_encoding_drift
# ---------------------------------------------------------------------------


def inject_category_encoding_drift(tables: Dict[str, pd.DataFrame]) -> int:
    """
    Replace Portuguese product_category_name with its English translation
    for 5% of products rows, simulating encoding drift across data sources.
    """
    products = tables["products"].copy()
    translation = tables["category_trans"]

    # Build Portuguese → English mapping
    pt_to_en: Dict[str, str] = dict(
        zip(
            translation["product_category_name"],
            translation["product_category_name_english"],
        )
    )

    # Only eligible if a translation exists for the current category
    eligible_mask = products["product_category_name"].isin(pt_to_en)
    eligible_idx = products[eligible_mask].index.tolist()

    n = max(1, int(len(eligible_idx) * 0.05))
    chosen_idx = random.sample(eligible_idx, n)

    products.loc[chosen_idx, "product_category_name"] = (
        products.loc[chosen_idx, "product_category_name"].map(pt_to_en)
    )
    tables["products"] = products
    return n


# ---------------------------------------------------------------------------
# Anomaly 9 – geolocation_coordinate_swap
# ---------------------------------------------------------------------------


def inject_geolocation_coordinate_swap(tables: Dict[str, pd.DataFrame]) -> int:
    """Swap lat and lng for 2% of geolocation rows."""
    geo = tables["geolocation"].copy()
    eligible_idx = geo.index.tolist()

    n = max(1, int(len(eligible_idx) * 0.02))
    chosen_idx = random.sample(eligible_idx, n)

    lat_vals = geo.loc[chosen_idx, "geolocation_lat"].values.copy()
    lng_vals = geo.loc[chosen_idx, "geolocation_lng"].values.copy()

    geo.loc[chosen_idx, "geolocation_lat"] = lng_vals
    geo.loc[chosen_idx, "geolocation_lng"] = lat_vals
    tables["geolocation"] = geo
    return n


# ---------------------------------------------------------------------------
# Anomaly 10 – excessive_freight_ratio
# ---------------------------------------------------------------------------


def inject_excessive_freight_ratio(tables: Dict[str, pd.DataFrame]) -> int:
    """
    For 2.5% of order_items where price > 0, set freight_value to
    price * uniform(1.5, 3.0), making shipping cost exceed item price.
    """
    items = tables["order_items"].copy()
    eligible_mask = items["price"] > 0
    eligible_idx = items[eligible_mask].index.tolist()

    n = max(1, int(len(eligible_idx) * 0.025))
    chosen_idx = random.sample(eligible_idx, n)

    multipliers = [random.uniform(1.5, 3.0) for _ in chosen_idx]
    items.loc[chosen_idx, "freight_value"] = (
        items.loc[chosen_idx, "price"].values * multipliers
    )
    tables["order_items"] = items
    return n


# ---------------------------------------------------------------------------
# Injection pipeline
# ---------------------------------------------------------------------------

# Each entry: (anomaly_name, function, affected_table_alias, display_table_name)
ANOMALY_PIPELINE: List[Tuple[str, object, str, str]] = [
    ("orphaned_order_items",       inject_orphaned_order_items,       "orders",         "orders"),
    ("duplicate_payment_records",  inject_duplicate_payment_records,  "order_payments", "order_payments"),
    ("future_dated_timestamps",    inject_future_dated_timestamps,    "orders",         "orders"),
    ("negative_payment_values",    inject_negative_payment_values,    "order_payments", "order_payments"),
    ("null_seller_ids",            inject_null_seller_ids,            "order_items",    "order_items"),
    ("out_of_range_review_scores", inject_out_of_range_review_scores, "order_reviews",  "order_reviews"),
    ("status_delivery_mismatch",   inject_status_delivery_mismatch,   "orders",         "orders"),
    ("category_encoding_drift",    inject_category_encoding_drift,    "products",       "products"),
    ("geolocation_coordinate_swap",inject_geolocation_coordinate_swap,"geolocation",    "geolocation"),
    ("excessive_freight_ratio",    inject_excessive_freight_ratio,    "order_items",    "order_items"),
]


def run_pipeline(
    tables: Dict[str, pd.DataFrame]
) -> List[Dict]:
    """Execute all anomaly injections in sequence. Returns summary rows."""
    # Snapshot row counts before any mutation so we can compute percentages
    original_counts: Dict[str, int] = {
        alias: len(df) for alias, df in tables.items()
    }

    summary_rows = []

    for anomaly_name, fn, table_alias, display_table in ANOMALY_PIPELINE:
        print(f"  Injecting {anomaly_name}...", end=" ", flush=True)
        rows_affected = fn(tables)
        pct = rows_affected / original_counts[table_alias] * 100
        print(f"{rows_affected:,} rows affected")

        summary_rows.append(
            {
                "Anomaly": anomaly_name,
                "Table": display_table,
                "Rows": rows_affected,
                "Pct": f"{pct:.2f}%",
            }
        )

    return summary_rows


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_tables(tables: Dict[str, pd.DataFrame], output_dir: Path) -> None:
    """Write all DataFrames to output_dir using the original filenames."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for alias, filename in TABLE_ALIAS.items():
        out_path = output_dir / filename
        tables[alias].to_csv(out_path, index=False)
        print(f"  Wrote {alias:20s} → {out_path}")


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------


def print_summary(summary_rows: List[Dict], output_dir: Path) -> None:
    """Print a formatted anomaly injection summary table."""
    headers = ["Anomaly", "Table", "Rows", "Pct"]
    rows = [
        [r["Anomaly"], r["Table"], f"{r['Rows']:,}", r["Pct"]]
        for r in summary_rows
    ]

    table_str = tabulate(rows, headers=headers, tablefmt="double_grid")

    # Replace the auto-generated title line with our branded header
    divider = "╔" + "═" * 74 + "╗"
    title   = "║" + " ANOMALY INJECTION SUMMARY".center(74) + "║"
    sep     = "╠" + "═" * 74 + "╣"

    print()
    print(divider)
    print(title)
    print(sep)
    print(table_str)
    print()
    print(f"Injected data written to: {output_dir}/")
    print("Ready to audit. Run: python main.py")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject synthetic anomalies into Olist CSV data."
    )
    parser.add_argument(
        "--source",
        type=str,
        default="data/raw",
        help="Directory containing clean Olist CSVs (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/injected",
        help="Directory to write corrupted CSVs (default: data/injected)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = Path(args.source)
    output_dir = Path(args.output)

    print(f"\n{'='*60}")
    print("  AI Data Quality Auditor — Anomaly Injector")
    print(f"{'='*60}")
    print(f"  Source : {source_dir.resolve()}")
    print(f"  Output : {output_dir.resolve()}")
    print(f"  Seed   : 42 (reproducible)")
    print(f"{'='*60}\n")

    # 1. Load
    print("[1/3] Loading source tables...")
    tables = load_tables(source_dir)

    # 2. Inject
    print("\n[2/3] Running anomaly injections...")
    summary_rows = run_pipeline(tables)

    # 3. Write
    print("\n[3/3] Writing injected tables...")
    write_tables(tables, output_dir)

    # Summary
    print_summary(summary_rows, output_dir)


if __name__ == "__main__":
    main()
