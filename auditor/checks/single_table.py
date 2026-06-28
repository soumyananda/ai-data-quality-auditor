"""
auditor/checks/single_table.py

Eight single-table data-quality checks for the Olist e-commerce dataset.

Each check is a standalone function that accepts the full `tables` dict and
returns a (possibly empty) list of CheckResult objects.  `run_all_checks`
orchestrates them and flattens results, suppressing findings where
rows_affected == 0 so callers only receive actionable issues.
"""

from __future__ import annotations

import pandas as pd

from auditor.models import CheckResult
from auditor import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(affected: int, total: int) -> float:
    """Return percentage as a float in [0, 100], safe against zero division."""
    if total == 0:
        return 0.0
    return round(affected / total * 100, 4)


def _null_severity(pct: float) -> str:
    if pct > 5.0:
        return "HIGH"
    if pct >= 1.0:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Check 1 — Null check
# ---------------------------------------------------------------------------

def null_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For every (table, column) pair declared in config.NULL_CONSTRAINTS, count
    null values and emit a finding when at least one null is present.

    Severity ladder: >5 % → HIGH, 1–5 % → MEDIUM, <1 % → LOW.
    example_values: first ≤5 integer row indices (iloc position) that are null.
    """
    results: list[CheckResult] = []

    for table_name, columns in config.NULL_CONSTRAINTS.items():
        df = tables.get(table_name)
        if df is None:
            continue

        total = len(df)

        for col in columns:
            if col not in df.columns:
                continue

            null_mask = df[col].isna()
            affected = int(null_mask.sum())
            if affected == 0:
                continue

            pct = _pct(affected, total)
            # First 5 positional (iloc) indices where the value is null
            example_indices = null_mask[null_mask].index[:5].tolist()

            results.append(
                CheckResult(
                    check_name="null_check",
                    table=table_name,
                    column=col,
                    severity=_null_severity(pct),
                    rows_affected=affected,
                    total_rows=total,
                    pct_affected=pct,
                    example_values=example_indices,
                    raw_details={"null_count": affected},
                )
            )

    return results


# ---------------------------------------------------------------------------
# Check 2 — Duplicate check
# ---------------------------------------------------------------------------

def duplicate_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For each (table, key_columns) in config.UNIQUE_CONSTRAINTS, find rows
    whose key combination is duplicated.

    Severity: always HIGH — duplicates on declared unique keys are critical.
    example_values: up to 5 duplicate key combos as dicts.
    """
    results: list[CheckResult] = []

    for table_name, key_groups in config.UNIQUE_CONSTRAINTS.items():
        df = tables.get(table_name)
        if df is None:
            continue

        total = len(df)

        for key_cols in key_groups:
            # Silently skip keys whose columns aren't present
            missing = [c for c in key_cols if c not in df.columns]
            if missing:
                continue

            dup_mask = df.duplicated(subset=key_cols, keep=False)
            affected = int(dup_mask.sum())
            if affected == 0:
                continue

            pct = _pct(affected, total)

            dup_df = df[dup_mask][key_cols].drop_duplicates()
            examples = dup_df.head(5).to_dict(orient="records")

            results.append(
                CheckResult(
                    check_name="duplicate_check",
                    table=table_name,
                    column=None,
                    severity="HIGH",
                    rows_affected=affected,
                    total_rows=total,
                    pct_affected=pct,
                    example_values=examples,
                    raw_details={
                        "key_columns": key_cols,
                        "duplicate_key_count": len(dup_df),
                    },
                )
            )

    return results


# ---------------------------------------------------------------------------
# Check 3 — Range / domain check
# ---------------------------------------------------------------------------

def range_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For each (table, column) in config.RANGE_CONSTRAINTS:
      - valid_set  → flag values not in the set
      - min / max  → flag values outside [min, max] (NaN-safe: NaN is ignored
                     here; null_check handles missing values separately)

    Severity: >3 % → HIGH, otherwise MEDIUM.
    example_values: up to 5 out-of-range actual values.
    """
    results: list[CheckResult] = []

    for (table_name, col), constraint in config.RANGE_CONSTRAINTS.items():
        df = tables.get(table_name)
        if df is None or col not in df.columns:
            continue

        series = df[col].dropna()  # NaN-aware: skip nulls (handled by null_check)
        total = len(df)

        valid_set = constraint.get("valid_set")
        if valid_set is not None:
            valid_set_converted = set(valid_set)
            bad_mask_series = ~series.isin(valid_set_converted)
        else:
            lo = constraint.get("min")
            hi = constraint.get("max")
            bad_mask_series = pd.Series(False, index=series.index)
            if lo is not None:
                bad_mask_series = bad_mask_series | (series < lo)
            if hi is not None:
                bad_mask_series = bad_mask_series | (series > hi)

        bad_values = series[bad_mask_series]
        affected = len(bad_values)
        if affected == 0:
            continue

        pct = _pct(affected, total)
        severity = "HIGH" if pct > 3.0 else "MEDIUM"
        examples = bad_values.head(5).tolist()

        results.append(
            CheckResult(
                check_name="range_check",
                table=table_name,
                column=col,
                severity=severity,
                rows_affected=affected,
                total_rows=total,
                pct_affected=pct,
                example_values=examples,
                raw_details={"constraint": constraint},
            )
        )

    return results


# ---------------------------------------------------------------------------
# Check 4 — Future date check
# ---------------------------------------------------------------------------

def future_date_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For each table's date columns declared in config.DATE_COLUMNS, look for
    values that represent past events but land after config.MAX_VALID_DATE.

    Only the columns in _PAST_EVENT_COLS are meaningful to check — columns
    like order_estimated_delivery_date are intentionally forward-looking.

    Severity: MEDIUM.
    example_values: up to 5 future timestamps as ISO-format strings.
    """
    _PAST_EVENT_COLS = {
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "review_answer_timestamp",
    }

    max_date = pd.Timestamp(config.MAX_VALID_DATE)
    results: list[CheckResult] = []

    for table_name, date_cols in config.DATE_COLUMNS.items():
        df = tables.get(table_name)
        if df is None:
            continue

        total = len(df)

        for col in date_cols:
            if col not in _PAST_EVENT_COLS:
                continue
            if col not in df.columns:
                continue

            parsed = pd.to_datetime(df[col], errors="coerce")
            future_mask = parsed > max_date
            affected = int(future_mask.sum())
            if affected == 0:
                continue

            pct = _pct(affected, total)
            examples = (
                parsed[future_mask]
                .head(5)
                .dt.strftime("%Y-%m-%d %H:%M:%S")
                .tolist()
            )

            results.append(
                CheckResult(
                    check_name="future_date_check",
                    table=table_name,
                    column=col,
                    severity="MEDIUM",
                    rows_affected=affected,
                    total_rows=total,
                    pct_affected=pct,
                    example_values=examples,
                    raw_details={"max_valid_date": config.MAX_VALID_DATE},
                )
            )

    return results


# ---------------------------------------------------------------------------
# Check 5 — Negative value check
# ---------------------------------------------------------------------------

def negative_value_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    Scan every loaded table for columns named price, freight_value, or
    payment_value that contain negative numbers.

    Severity: always HIGH — negative monetary amounts indicate data corruption
    or sign-error bugs upstream.
    example_values: up to 5 negative values.
    """
    _MONEY_COLS = {"price", "freight_value", "payment_value"}
    results: list[CheckResult] = []

    for table_name, df in tables.items():
        total = len(df)

        for col in _MONEY_COLS:
            if col not in df.columns:
                continue

            series = pd.to_numeric(df[col], errors="coerce").dropna()
            bad_mask = series < 0
            affected = int(bad_mask.sum())
            if affected == 0:
                continue

            pct = _pct(affected, total)
            examples = series[bad_mask].head(5).tolist()

            results.append(
                CheckResult(
                    check_name="negative_value_check",
                    table=table_name,
                    column=col,
                    severity="HIGH",
                    rows_affected=affected,
                    total_rows=total,
                    pct_affected=pct,
                    example_values=examples,
                    raw_details={"min_observed": float(series[bad_mask].min())},
                )
            )

    return results


# ---------------------------------------------------------------------------
# Check 6 — Category drift check
# ---------------------------------------------------------------------------

def category_drift_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    In the `products` table, verify that product_category_name values are in
    Portuguese (not English).  English category names that have leaked in from
    the translation table are flagged.

    Access the translation table via tables["category_translation"].

    Severity: MEDIUM.
    example_values: up to 5 mismatched (English) category names found in products.
    """
    products = tables.get("products")
    translation = tables.get("category_translation")

    if products is None or translation is None:
        return []
    if "product_category_name" not in products.columns:
        return []
    if "product_category_name_english" not in translation.columns:
        return []

    total = len(products)

    english_names: set[str] = set(
        translation["product_category_name_english"].dropna().str.strip()
    )

    product_cats = products["product_category_name"].dropna().str.strip()
    # Flag product category names that appear in the English column of the
    # translation table — these are English values that have drifted in.
    bad_mask = product_cats.isin(english_names)
    affected = int(bad_mask.sum())
    if affected == 0:
        return []

    pct = _pct(affected, total)
    examples = product_cats[bad_mask].unique()[:5].tolist()

    return [
        CheckResult(
            check_name="category_drift_check",
            table="products",
            column="product_category_name",
            severity="MEDIUM",
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={"english_names_count": len(english_names)},
        )
    ]


# ---------------------------------------------------------------------------
# Check 7 — Freight ratio check
# ---------------------------------------------------------------------------

def freight_ratio_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    In `order_items`, compute freight_value / price for each row.
    Flag rows where the ratio exceeds 1.0 AND price > 0 (to avoid division
    by zero and zero-price artefacts).

    Severity: LOW — economically unusual but not necessarily wrong.
    raw_details: mean and max ratio among flagged rows.
    example_values: up to 5 rows as dicts with order_id, price, freight_value, ratio.
    """
    df = tables.get("order_items")
    if df is None:
        return []

    required = {"price", "freight_value"}
    if not required.issubset(df.columns):
        return []

    total = len(df)

    work = df.copy()
    work["price"] = pd.to_numeric(work["price"], errors="coerce")
    work["freight_value"] = pd.to_numeric(work["freight_value"], errors="coerce")

    eligible = work[(work["price"] > 0) & work["price"].notna() & work["freight_value"].notna()].copy()
    eligible["_ratio"] = eligible["freight_value"] / eligible["price"]

    flagged = eligible[eligible["_ratio"] > 1.0]
    affected = len(flagged)
    if affected == 0:
        return []

    pct = _pct(affected, total)

    example_cols = [c for c in ["order_id", "price", "freight_value"] if c in flagged.columns]
    examples_df = flagged[example_cols + ["_ratio"]].head(5).copy()
    examples_df = examples_df.rename(columns={"_ratio": "ratio"})
    examples = examples_df.to_dict(orient="records")

    return [
        CheckResult(
            check_name="freight_ratio_check",
            table="order_items",
            column=None,
            severity="LOW",
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={
                "mean_ratio_flagged": round(float(flagged["_ratio"].mean()), 4),
                "max_ratio": round(float(flagged["_ratio"].max()), 4),
            },
        )
    ]


# ---------------------------------------------------------------------------
# Check 8 — State machine check (single-table version)
# ---------------------------------------------------------------------------

def state_machine_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    In the `orders` table, find rows where order_delivered_customer_date is
    not null but order_status is NOT 'delivered'.  A non-null delivery date
    implies the order reached the customer, making any other status value
    inconsistent with the lifecycle state machine.

    Severity: HIGH.
    example_values: up to 5 rows as dicts showing order_id, order_status,
                    order_delivered_customer_date.
    """
    df = tables.get("orders")
    if df is None:
        return []

    required = {"order_delivered_customer_date", "order_status"}
    if not required.issubset(df.columns):
        return []

    total = len(df)

    has_delivery_date = df["order_delivered_customer_date"].notna()
    bad_status = ~df["order_status"].isin(["delivered"])
    flagged_mask = has_delivery_date & bad_status

    affected = int(flagged_mask.sum())
    if affected == 0:
        return []

    pct = _pct(affected, total)

    example_cols = [
        c for c in ["order_id", "order_status", "order_delivered_customer_date"]
        if c in df.columns
    ]
    examples = df[flagged_mask][example_cols].head(5).to_dict(orient="records")

    return [
        CheckResult(
            check_name="state_machine_check",
            table="orders",
            column=None,
            severity="HIGH",
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={
                "bad_statuses_observed": (
                    df[flagged_mask]["order_status"]
                    .value_counts()
                    .head(10)
                    .to_dict()
                ) if "order_status" in df.columns else {},
            },
        )
    ]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run_all_checks(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    Run all 8 single-table checks and return every finding where at least one
    row was affected.  Clean check results (rows_affected == 0) are suppressed.

    Parameters
    ----------
    tables : dict[str, pd.DataFrame]
        Mapping of logical table name → loaded DataFrame.

    Returns
    -------
    list[CheckResult]
        Flat list of all non-zero findings across all checks, in check order.
    """
    check_fns = [
        null_check,
        duplicate_check,
        range_check,
        future_date_check,
        negative_value_check,
        category_drift_check,
        freight_ratio_check,
        state_machine_check,
    ]

    results: list[CheckResult] = []
    for fn in check_fns:
        results.extend(fn(tables))

    return results
