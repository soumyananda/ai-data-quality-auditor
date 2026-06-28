"""
loader.py — CSV ingestion and pre-joined view construction for the
AI Data Quality Auditor.

Two public entry points:
  load_all(dataset_path)     → dict[table_name, DataFrame]
  get_joined_views(tables)   → dict[view_name, DataFrame]
"""

import os
from typing import Dict

import pandas as pd

from auditor.config import DATE_COLUMNS, TABLE_FILENAMES, TABLE_SCHEMAS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_path(dataset_path: str, filename: str) -> str:
    """Return the full file path, raising a clear error if it does not exist."""
    full_path = os.path.join(dataset_path, filename)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(
            f"Expected dataset file not found: {full_path}\n"
            f"Make sure the CSV files are placed in: {dataset_path}"
        )
    return full_path


def _cast_columns(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """
    Best-effort type coercion based on TABLE_SCHEMAS.

    - 'int' columns are cast with errors='coerce' so bad values become NaN
      rather than raising; the null-check auditor catches them later.
    - 'float' columns likewise.
    - 'str' columns are left as-is (pandas object dtype is fine).
    - 'datetime' columns are handled by the caller via DATE_COLUMNS.
    """
    schema = TABLE_SCHEMAS.get(table, {})
    for col, dtype in schema.items():
        if col not in df.columns:
            continue
        if dtype == "int":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif dtype == "float":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # str and datetime: no further casting needed here
    return df


def _parse_dates(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Parse all known datetime columns for a table using errors='coerce'."""
    date_cols = DATE_COLUMNS.get(table, [])
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Public: load_all
# ---------------------------------------------------------------------------

def load_all(dataset_path: str) -> Dict[str, pd.DataFrame]:
    """
    Load all 9 Olist CSVs from *dataset_path* into a dict of DataFrames.

    Parameters
    ----------
    dataset_path : str
        Absolute or relative path to the directory containing the CSV files.
        Typically ``data/raw/`` for the original dataset or
        ``data/injected/`` for the fault-injected variant.

    Returns
    -------
    dict[str, pd.DataFrame]
        Keys are logical table names as defined in ``TABLE_FILENAMES``.

    Raises
    ------
    FileNotFoundError
        If any expected CSV file is missing from *dataset_path*.
    """
    tables: Dict[str, pd.DataFrame] = {}

    for table_name, filename in TABLE_FILENAMES.items():
        full_path = _resolve_path(dataset_path, filename)

        df = pd.read_csv(
            full_path,
            dtype=str,          # read everything as str first; cast below
            low_memory=False,
        )

        df = _parse_dates(df, table_name)
        df = _cast_columns(df, table_name)

        print(f"Loading {table_name}... {len(df):,} rows")
        tables[table_name] = df

    return tables


# ---------------------------------------------------------------------------
# Public: get_joined_views
# ---------------------------------------------------------------------------

def get_joined_views(tables: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    Build three pre-joined analytical views from the already-loaded tables.

    Views
    -----
    orders_full
        orders LEFT JOIN order_items ON order_id
               LEFT JOIN order_payments ON order_id

        Useful for validating that every order has at least one payment and
        one item, and for revenue-vs-payment reconciliation.

    fulfillment_view
        orders LEFT JOIN order_items ON order_id
               LEFT JOIN sellers ON seller_id

        Useful for seller-level delivery SLA analysis and logistics checks.

    review_view
        orders LEFT JOIN order_reviews ON order_id

        Useful for post-delivery review lag analysis and score distribution
        checks across order lifecycle stages.

    Parameters
    ----------
    tables : dict[str, pd.DataFrame]
        Output of ``load_all()``.

    Returns
    -------
    dict[str, pd.DataFrame]
        Keys: ``"orders_full"``, ``"fulfillment_view"``, ``"review_view"``.

    Notes
    -----
    - All joins are LEFT joins so orders with no matching child rows are
      preserved — those gaps are themselves quality signals.
    - Suffix ``_items`` and ``_payments`` are added to disambiguate
      ``order_id`` duplicates that arise from the multi-item / multi-payment
      cardinality (though order_id is the join key and kept once).
    """
    orders        = tables["orders"]
    order_items   = tables["order_items"]
    order_payments= tables["order_payments"]
    sellers       = tables["sellers"]
    order_reviews = tables["order_reviews"]

    # ------------------------------------------------------------------
    # orders_full: orders ← items ← payments
    # ------------------------------------------------------------------
    # Step 1: orders + items (one row per item; orders with no items get NaN)
    orders_with_items = orders.merge(
        order_items,
        on="order_id",
        how="left",
        suffixes=("", "_item"),
    )

    # Step 2: join payments (one row per payment per item row)
    # We keep suffixes to distinguish payment_sequential from any future
    # item-level sequential numbering.
    orders_full = orders_with_items.merge(
        order_payments,
        on="order_id",
        how="left",
        suffixes=("", "_payment"),
    )

    # ------------------------------------------------------------------
    # fulfillment_view: orders ← items ← sellers
    # ------------------------------------------------------------------
    orders_with_items_f = orders.merge(
        order_items,
        on="order_id",
        how="left",
        suffixes=("", "_item"),
    )

    fulfillment_view = orders_with_items_f.merge(
        sellers,
        on="seller_id",
        how="left",
        suffixes=("", "_seller"),
    )

    # ------------------------------------------------------------------
    # review_view: orders ← reviews
    # ------------------------------------------------------------------
    review_view = orders.merge(
        order_reviews,
        on="order_id",
        how="left",
        suffixes=("", "_review"),
    )

    views = {
        "orders_full":      orders_full,
        "fulfillment_view": fulfillment_view,
        "review_view":      review_view,
    }

    for view_name, view_df in views.items():
        print(f"Built view '{view_name}': {len(view_df):,} rows × {len(view_df.columns)} columns")

    return views
