"""
tests/test_cross_table.py

Unit tests for auditor/checks/cross_table.py.

Each test constructs minimal synthetic DataFrames with a known cross-table
anomaly and asserts the correct finding is returned.  Clean-data tests verify
no false positives.  No Kaggle data required.
"""
from __future__ import annotations

import pandas as pd
import pytest

from auditor.checks.cross_table import (
    referential_integrity_check,
    payment_order_reconciliation_check,
    geolocation_bounds_check,
    duplicate_payment_check,
    run_all_checks,
)
from auditor.models import CheckResult


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------

def _orders(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":                      [f"ord_{i:03d}" for i in range(n)],
        "customer_id":                   [f"cust_{i:03d}" for i in range(n)],
        "order_status":                  ["delivered"] * n,
        "order_purchase_timestamp":      pd.date_range("2018-01-01", periods=n),
        "order_estimated_delivery_date": pd.date_range("2018-01-15", periods=n),
    })


def _order_items(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":          [f"ord_{i:03d}" for i in range(n)],
        "order_item_id":     list(range(1, n + 1)),
        "product_id":        [f"prod_{i:03d}" for i in range(n)],
        "seller_id":         [f"seller_{i:03d}" for i in range(n)],
        "shipping_limit_date": pd.date_range("2018-01-12", periods=n),
        "price":             [50.0] * n,
        "freight_value":     [5.0] * n,
    })


def _order_payments(n: int = 5, value: float = 55.0) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":            [f"ord_{i:03d}" for i in range(n)],
        "payment_sequential":  [1] * n,
        "payment_type":        ["credit_card"] * n,
        "payment_installments": [1] * n,
        "payment_value":       [value] * n,
    })


def _sellers(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "seller_id":             [f"seller_{i:03d}" for i in range(n)],
        "seller_zip_code_prefix": ["01310"] * n,
        "seller_city":           ["Sao Paulo"] * n,
        "seller_state":          ["SP"] * n,
    })


def _products(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": [f"prod_{i:03d}" for i in range(n)],
    })


def _customers(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": [f"cust_{i:03d}" for i in range(n)],
    })


def _geolocation(lats: list, lngs: list) -> pd.DataFrame:
    n = len(lats)
    return pd.DataFrame({
        "geolocation_zip_code_prefix": [f"{i:05d}" for i in range(n)],
        "geolocation_lat":   lats,
        "geolocation_lng":   lngs,
        "geolocation_city":  ["Sao Paulo"] * n,
        "geolocation_state": ["SP"] * n,
    })


def _clean_tables() -> dict:
    return {
        "orders":        _orders(),
        "order_items":   _order_items(),
        "order_payments": _order_payments(),
        "sellers":       _sellers(),
        "products":      _products(),
        "customers":     _customers(),
        "order_reviews": pd.DataFrame({"review_id": [], "order_id": []}),
        "geolocation":   _geolocation([-10.0, -23.5], [-50.0, -46.6]),
    }


# ---------------------------------------------------------------------------
# referential_integrity_check
# ---------------------------------------------------------------------------

class TestReferentialIntegrityCheck:
    def test_detects_orphaned_order_items(self):
        orders = _orders(5)
        items = _order_items(5)
        # Drop order 0 from orders; items[0] becomes an orphan
        orders = orders[orders["order_id"] != "ord_000"].reset_index(drop=True)
        tables = {
            "orders": orders, "order_items": items,
            "sellers": _sellers(), "products": _products(),
            "order_payments": _order_payments(), "customers": _customers(),
            "order_reviews": pd.DataFrame({"review_id": [], "order_id": []}),
        }
        results = referential_integrity_check(tables)
        fk_findings = [
            r for r in results
            if "order_items" in r.check_name and "order_id" in r.check_name
        ]
        assert len(fk_findings) == 1
        assert fk_findings[0].rows_affected == 1

    def test_severity_high_when_pct_above_threshold(self):
        orders = _orders(10)
        items = _order_items(10)
        # Drop 2 orders → 2/10 = 20% orphan rate → HIGH
        orders = orders[~orders["order_id"].isin(["ord_000", "ord_001"])].reset_index(drop=True)
        tables = {
            "orders": orders, "order_items": items,
            "sellers": _sellers(), "products": _products(),
            "order_payments": _order_payments(), "customers": _customers(),
            "order_reviews": pd.DataFrame({"review_id": [], "order_id": []}),
        }
        results = referential_integrity_check(tables)
        fk_findings = [
            r for r in results
            if "order_items" in r.check_name and "order_id" in r.check_name
        ]
        assert fk_findings[0].severity == "HIGH"

    def test_clean_data_produces_no_findings(self):
        results = referential_integrity_check(_clean_tables())
        assert results == []

    def test_missing_table_does_not_raise(self):
        # Only orders present — all FK checks involving missing tables should skip
        results = referential_integrity_check({"orders": _orders()})
        assert isinstance(results, list)

    def test_null_foreign_keys_excluded_from_orphan_count(self):
        orders = _orders(5)
        items = _order_items(5)
        items.loc[0, "order_id"] = None  # null FK — should NOT be flagged here
        tables = {
            "orders": orders, "order_items": items,
            "sellers": _sellers(), "products": _products(),
            "order_payments": _order_payments(), "customers": _customers(),
            "order_reviews": pd.DataFrame({"review_id": [], "order_id": []}),
        }
        results = referential_integrity_check(tables)
        fk_findings = [
            r for r in results
            if "order_items" in r.check_name and "order_id" in r.check_name
        ]
        assert fk_findings == []  # null_check handles nulls; FK check should skip them


# ---------------------------------------------------------------------------
# payment_order_reconciliation_check
# ---------------------------------------------------------------------------

class TestPaymentOrderReconciliationCheck:
    def test_detects_payment_exceeding_items_total(self):
        items = _order_items(3)       # price=50, freight=5 → total per order = 55
        payments = _order_payments(3, value=55.0)
        payments.loc[0, "payment_value"] = 110.0  # ord_000: payment 110 vs items 55 → diff 55
        results = payment_order_reconciliation_check({
            "order_items": items, "order_payments": payments
        })
        assert len(results) == 1
        assert results[0].rows_affected == 1

    def test_detects_underpayment(self):
        items = _order_items(3)
        payments = _order_payments(3, value=55.0)
        payments.loc[0, "payment_value"] = 1.0   # way under items total of 55
        results = payment_order_reconciliation_check({
            "order_items": items, "order_payments": payments
        })
        assert results[0].rows_affected == 1

    def test_within_tolerance_not_flagged(self):
        items = _order_items(3)
        payments = _order_payments(3, value=55.0)
        payments.loc[0, "payment_value"] = 55.30  # diff = 0.30 < $0.50 tolerance
        results = payment_order_reconciliation_check({
            "order_items": items, "order_payments": payments
        })
        assert results == []

    def test_clean_data_produces_no_findings(self):
        results = payment_order_reconciliation_check({
            "order_items": _order_items(),
            "order_payments": _order_payments(),
        })
        assert results == []

    def test_missing_table_does_not_raise(self):
        results = payment_order_reconciliation_check({"order_items": _order_items()})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# geolocation_bounds_check
# ---------------------------------------------------------------------------

class TestGeolocationBoundsCheck:
    def test_detects_lat_out_of_bounds(self):
        # Brazil lat valid range: -34 to 5. lat=50 is outside.
        tables = {"geolocation": _geolocation([50.0], [-50.0])}
        results = geolocation_bounds_check(tables)
        assert len(results) == 1
        assert results[0].rows_affected == 1

    def test_detects_lng_out_of_bounds(self):
        # Brazil lng valid range: -74 to -34. lng=-10 is outside.
        tables = {"geolocation": _geolocation([-10.0], [-10.0])}
        results = geolocation_bounds_check(tables)
        assert len(results) == 1

    def test_detects_swapped_coordinates(self):
        # lat=-50 falls in lng valid range (-74 to -34); lng=-10 falls in lat valid range (-34 to 5)
        # Both are out of their own bounds → flagged
        tables = {"geolocation": _geolocation([-50.0], [-10.0])}
        results = geolocation_bounds_check(tables)
        assert len(results) == 1

    def test_valid_brazil_coordinates_no_findings(self):
        lats = [-10.0, -23.5, -3.1, 2.5]
        lngs = [-50.0, -46.6, -60.0, -44.3]
        tables = {"geolocation": _geolocation(lats, lngs)}
        results = geolocation_bounds_check(tables)
        assert results == []

    def test_multiple_bad_rows_counted(self):
        lats = [-10.0, 50.0, 80.0]   # rows 1 and 2 out of bounds
        lngs = [-50.0, -50.0, -50.0]
        tables = {"geolocation": _geolocation(lats, lngs)}
        results = geolocation_bounds_check(tables)
        assert results[0].rows_affected == 2

    def test_missing_table_does_not_raise(self):
        results = geolocation_bounds_check({})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# duplicate_payment_check
# ---------------------------------------------------------------------------

class TestDuplicatePaymentCheck:
    def test_detects_exact_duplicate_triple(self):
        payments = _order_payments(3)
        dup = payments.iloc[[0]].copy()   # exact duplicate of row 0
        payments = pd.concat([payments, dup], ignore_index=True)
        results = duplicate_payment_check({"order_payments": payments})
        assert len(results) == 1
        assert results[0].rows_affected >= 1
        assert results[0].severity == "HIGH"

    def test_same_order_different_sequential_not_flagged(self):
        payments = pd.DataFrame({
            "order_id":            ["ord_001", "ord_001"],
            "payment_sequential":  [1, 2],        # different sequential — installments
            "payment_type":        ["credit_card", "credit_card"],
            "payment_installments": [1, 1],
            "payment_value":       [30.0, 25.0],
        })
        results = duplicate_payment_check({"order_payments": payments})
        assert results == []

    def test_clean_data_produces_no_findings(self):
        results = duplicate_payment_check({"order_payments": _order_payments()})
        assert results == []

    def test_multiple_duplicates_all_counted(self):
        payments = _order_payments(3)
        # Duplicate rows 0 and 1
        dups = payments.iloc[[0, 1]].copy()
        payments = pd.concat([payments, dups], ignore_index=True)
        results = duplicate_payment_check({"order_payments": payments})
        assert results[0].rows_affected >= 2

    def test_missing_table_does_not_raise(self):
        results = duplicate_payment_check({})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# run_all_checks — integration
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_returns_list_of_check_results(self):
        results = run_all_checks(_clean_tables())
        assert isinstance(results, list)
        assert all(isinstance(r, CheckResult) for r in results)

    def test_clean_data_produces_no_findings(self):
        results = run_all_checks(_clean_tables())
        assert results == []

    def test_detects_injected_fk_violation(self):
        tables = _clean_tables()
        # Remove ord_000 from orders — order_items[0] becomes orphaned
        tables["orders"] = tables["orders"][
            tables["orders"]["order_id"] != "ord_000"
        ].reset_index(drop=True)
        results = run_all_checks(tables)
        assert len(results) > 0

    def test_detects_injected_payment_mismatch(self):
        tables = _clean_tables()
        tables["order_payments"].loc[0, "payment_value"] = 999.0
        results = run_all_checks(tables)
        assert any("reconciliation" in r.check_name for r in results)
