"""
auditor/checks/cross_table.py

Four cross-table data-quality checks for the Olist e-commerce dataset.

Each check is a standalone function accepting the full `tables` dict and
returning a (possibly empty) list of CheckResult objects.  `run_all_checks`
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


# ---------------------------------------------------------------------------
# Check 1 — Referential integrity
# ---------------------------------------------------------------------------

def referential_integrity_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For each (child_table, child_col, parent_table, parent_col) in
    config.FK_MANIFEST, find child rows whose foreign-key value is not present
    in the parent column.  Null child values are excluded (null_check handles
    those separately).

    Severity: >1 % orphans → HIGH, otherwise MEDIUM.
    check_name: f"referential_integrity_{child_table}_{child_col}"
    example_values: up to 5 orphaned key values.
    """
    results: list[CheckResult] = []

    for child_table, child_col, parent_table, parent_col in config.FK_MANIFEST:
        child_df = tables.get(child_table)
        parent_df = tables.get(parent_table)

        # Gracefully skip if either side is missing
        if child_df is None or parent_df is None:
            continue
        if child_col not in child_df.columns or parent_col not in parent_df.columns:
            continue

        total = len(child_df)

        parent_keys: set = set(parent_df[parent_col].dropna().unique())
        child_series = child_df[child_col].dropna()

        orphan_mask = ~child_series.isin(parent_keys)
        orphaned_values = child_series[orphan_mask]
        affected = len(orphaned_values)

        if affected == 0:
            continue

        pct = _pct(affected, total)
        severity = "HIGH" if pct > 1.0 else "MEDIUM"
        examples = orphaned_values.unique()[:5].tolist()

        results.append(
            CheckResult(
                check_name=f"referential_integrity_{child_table}_{child_col}",
                table=child_table,
                column=child_col,
                severity=severity,
                rows_affected=affected,
                total_rows=total,
                pct_affected=pct,
                example_values=examples,
                raw_details={
                    "parent_table": parent_table,
                    "parent_col": parent_col,
                    "orphaned_key_count": int(orphaned_values.nunique()),
                },
            )
        )

    return results


# ---------------------------------------------------------------------------
# Check 2 — Payment / order-item reconciliation
# ---------------------------------------------------------------------------

def payment_order_reconciliation_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    For each order_id, compare:
      - payment total  : sum of order_payments.payment_value
      - items total    : sum of order_items.(price + freight_value)

    Flag orders where |payment_total - items_total| > $0.50.

    Severity: >2 % mismatched orders → HIGH, otherwise MEDIUM.
    example_values: up to 5 dicts showing order_id, payment_total,
                    items_total, difference.
    """
    payments_df = tables.get("order_payments")
    items_df = tables.get("order_items")

    if payments_df is None or items_df is None:
        return []

    required_pay = {"order_id", "payment_value"}
    required_items = {"order_id", "price", "freight_value"}
    if not required_pay.issubset(payments_df.columns):
        return []
    if not required_items.issubset(items_df.columns):
        return []

    pay_totals = (
        payments_df.groupby("order_id")["payment_value"]
        .sum()
        .rename("payment_total")
    )

    items_work = items_df.copy()
    items_work["price"] = pd.to_numeric(items_work["price"], errors="coerce").fillna(0)
    items_work["freight_value"] = pd.to_numeric(items_work["freight_value"], errors="coerce").fillna(0)
    items_work["line_total"] = items_work["price"] + items_work["freight_value"]
    items_totals = (
        items_work.groupby("order_id")["line_total"]
        .sum()
        .rename("items_total")
    )

    reconciled = pd.concat([pay_totals, items_totals], axis=1).dropna()
    reconciled["difference"] = (reconciled["payment_total"] - reconciled["items_total"]).abs()

    tolerance: float = 0.50  # orders differing by more than $0.50 are flagged
    flagged = reconciled[reconciled["difference"] > tolerance]

    total = len(reconciled)
    affected = len(flagged)

    if affected == 0:
        return []

    pct = _pct(affected, total)
    severity = "HIGH" if pct > 2.0 else "MEDIUM"

    examples_df = flagged.reset_index().rename(columns={"index": "order_id"})
    examples_df = examples_df.head(5)[["order_id", "payment_total", "items_total", "difference"]]
    examples_df = examples_df.round(2)
    examples = examples_df.to_dict(orient="records")

    return [
        CheckResult(
            check_name="payment_order_reconciliation_check",
            table="order_payments",
            column=None,
            severity=severity,
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={
                "tolerance_usd": tolerance,
                "mean_discrepancy": round(float(flagged["difference"].mean()), 4),
                "max_discrepancy": round(float(flagged["difference"].max()), 4),
            },
        )
    ]


# ---------------------------------------------------------------------------
# Check 3 — Geolocation bounds check
# ---------------------------------------------------------------------------

def geolocation_bounds_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    In the `geolocation` table:
      - Valid latitude  : -34 to +5   (Brazil's north-south span)
      - Valid longitude : -74 to -34  (Brazil's west-east span)

    Flags rows where EITHER coordinate is out of bounds.
    Also flags rows where lat and lng appear to be swapped (lat value is in
    the lng valid range AND lng value is in the lat valid range).

    Severity: MEDIUM.
    example_values: up to 5 rows showing zip, lat, lng, and which violation
                    was detected.
    """
    df = tables.get("geolocation")
    if df is None:
        return []

    lat_col = "geolocation_lat"
    lng_col = "geolocation_lng"
    zip_col = "geolocation_zip_code_prefix"

    required = {lat_col, lng_col}
    if not required.issubset(df.columns):
        return []

    total = len(df)

    work = df.copy()
    work[lat_col] = pd.to_numeric(work[lat_col], errors="coerce")
    work[lng_col] = pd.to_numeric(work[lng_col], errors="coerce")

    lat_lo, lat_hi = -34.0, 5.0
    lng_lo, lng_hi = -74.0, -34.0

    lat_oob = (work[lat_col] < lat_lo) | (work[lat_col] > lat_hi)
    lng_oob = (work[lng_col] < lng_lo) | (work[lng_col] > lng_hi)
    oob_mask = lat_oob | lng_oob

    # Swap detection: lat is in lng's valid range AND lng is in lat's valid range
    lat_in_lng_range = (work[lat_col] >= lng_lo) & (work[lat_col] <= lng_hi)
    lng_in_lat_range = (work[lng_col] >= lat_lo) & (work[lng_col] <= lat_hi)
    swap_mask = lat_in_lng_range & lng_in_lat_range

    combined_mask = oob_mask | swap_mask
    affected = int(combined_mask.sum())

    if affected == 0:
        return []

    pct = _pct(affected, total)

    # Build example records with a human-readable violation tag
    flagged_df = work[combined_mask].copy()
    flagged_df["violation"] = "out_of_bounds"
    is_swap = swap_mask[combined_mask]
    flagged_df.loc[is_swap[is_swap].index, "violation"] = "likely_swapped"

    example_cols = [c for c in [zip_col, lat_col, lng_col, "violation"] if c in flagged_df.columns]
    examples = flagged_df[example_cols].head(5).to_dict(orient="records")

    return [
        CheckResult(
            check_name="geolocation_bounds_check",
            table="geolocation",
            column=None,
            severity="MEDIUM",
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={
                "lat_out_of_bounds": int(lat_oob.sum()),
                "lng_out_of_bounds": int(lng_oob.sum()),
                "likely_swapped": int(swap_mask.sum()),
                "lat_bounds": [lat_lo, lat_hi],
                "lng_bounds": [lng_lo, lng_hi],
            },
        )
    ]


# ---------------------------------------------------------------------------
# Check 4 — Duplicate payment check
# ---------------------------------------------------------------------------

def duplicate_payment_check(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    Find orders in `order_payments` where the same
    (order_id, payment_sequential, payment_value) triple appears more than once.

    Severity: always HIGH — duplicate payment records cause double-counting in
    revenue reporting and financial reconciliation.
    example_values: up to 5 duplicate triples as dicts.
    """
    df = tables.get("order_payments")
    if df is None:
        return []

    key_cols = ["order_id", "payment_sequential", "payment_value"]
    missing = [c for c in key_cols if c not in df.columns]
    if missing:
        return []

    total = len(df)

    dup_mask = df.duplicated(subset=key_cols, keep=False)
    affected = int(dup_mask.sum())

    if affected == 0:
        return []

    pct = _pct(affected, total)

    dup_df = df[dup_mask][key_cols].drop_duplicates()
    examples = dup_df.head(5).to_dict(orient="records")

    return [
        CheckResult(
            check_name="duplicate_payment_check",
            table="order_payments",
            column=None,
            severity="HIGH",
            rows_affected=affected,
            total_rows=total,
            pct_affected=pct,
            example_values=examples,
            raw_details={
                "key_columns": key_cols,
                "unique_duplicate_triples": len(dup_df),
            },
        )
    ]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run_all_checks(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """
    Run all 4 cross-table checks and return every finding where at least one
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
        referential_integrity_check,
        payment_order_reconciliation_check,
        geolocation_bounds_check,
        duplicate_payment_check,
    ]

    results: list[CheckResult] = []
    for fn in check_fns:
        results.extend(fn(tables))

    return results
