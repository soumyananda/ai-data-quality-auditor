"""
tests/test_single_table.py

Unit tests for auditor/checks/single_table.py.

Each test constructs a minimal synthetic DataFrame with a known anomaly,
calls the relevant check function, and asserts the correct finding is returned.
Tests for clean data verify no findings are emitted (no false positives).
No Kaggle data required — all fixtures are built inline.
"""
from __future__ import annotations

import pandas as pd
import pytest

from auditor.checks.single_table import (
    null_check,
    duplicate_check,
    range_check,
    negative_value_check,
    future_date_check,
    freight_ratio_check,
    state_machine_check,
    category_drift_check,
    run_all_checks,
)
from auditor.models import CheckResult


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------

def _orders(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":                      [f"ord_{i:03d}" for i in range(n)],
        "customer_id":                   [f"cust_{i:03d}" for i in range(n)],
        "order_status":                  ["delivered"] * n,
        "order_purchase_timestamp":      pd.date_range("2018-01-01", periods=n),
        "order_approved_at":             pd.date_range("2018-01-02", periods=n),
        "order_delivered_carrier_date":  pd.date_range("2018-01-05", periods=n),
        "order_delivered_customer_date": pd.date_range("2018-01-10", periods=n),
        "order_estimated_delivery_date": pd.date_range("2018-01-15", periods=n),
    })


def _order_items(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":          [f"ord_{i:03d}" for i in range(n)],
        "order_item_id":     list(range(1, n + 1)),
        "product_id":        [f"prod_{i:03d}" for i in range(n)],
        "seller_id":         [f"seller_{i:03d}" for i in range(n)],
        "shipping_limit_date": pd.date_range("2018-01-12", periods=n),
        "price":             [50.0 + i for i in range(n)],
        "freight_value":     [5.0] * n,
    })


def _order_payments(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id":            [f"ord_{i:03d}" for i in range(n)],
        "payment_sequential":  [1] * n,
        "payment_type":        ["credit_card"] * n,
        "payment_installments": [1] * n,
        "payment_value":       [55.0 + i for i in range(n)],
    })


def _order_reviews(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({
        "review_id":              [f"rev_{i:03d}" for i in range(n)],
        "order_id":               [f"ord_{i:03d}" for i in range(n)],
        "review_score":           [4] * n,
        "review_comment_title":   ["Good"] * n,
        "review_comment_message": ["Great product"] * n,
        "review_creation_date":   pd.date_range("2018-01-15", periods=n),
        "review_answer_timestamp": pd.date_range("2018-01-16", periods=n),
    })


def _products(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "product_id":               [f"prod_{i:03d}" for i in range(n)],
        "product_category_name":    ["informatica_acessorios"] * n,
        "product_name_lenght":      [30] * n,
        "product_description_lenght": [200] * n,
        "product_photos_qty":       [3] * n,
        "product_weight_g":         [500.0] * n,
        "product_length_cm":        [20.0] * n,
        "product_height_cm":        [10.0] * n,
        "product_width_cm":         [15.0] * n,
    })


def _translation() -> pd.DataFrame:
    return pd.DataFrame({
        "product_category_name":         ["informatica_acessorios", "beleza_saude"],
        "product_category_name_english": ["computers_accessories",  "health_beauty"],
    })


def _clean_tables() -> dict:
    return {
        "orders":               _orders(),
        "order_items":          _order_items(),
        "order_payments":       _order_payments(),
        "order_reviews":        _order_reviews(),
        "products":             _products(),
        "category_translation": _translation(),
    }


# ---------------------------------------------------------------------------
# null_check
# ---------------------------------------------------------------------------

class TestNullCheck:
    def test_detects_nulls_in_constrained_column(self):
        orders = _orders()
        orders.loc[0, "customer_id"] = None
        orders.loc[1, "customer_id"] = None
        results = null_check({"orders": orders})
        matches = [r for r in results if r.column == "customer_id"]
        assert len(matches) == 1
        assert matches[0].rows_affected == 2
        assert matches[0].table == "orders"
        assert matches[0].check_name == "null_check"

    def test_severity_scales_with_null_rate(self):
        # > 5% should be HIGH
        orders = _orders(100)
        orders.loc[:5, "customer_id"] = None  # 6 nulls = 6%
        results = null_check({"orders": orders})
        matches = [r for r in results if r.column == "customer_id"]
        assert matches[0].severity == "HIGH"

    def test_clean_data_produces_no_findings(self):
        assert null_check({"orders": _orders()}) == []

    def test_missing_table_does_not_raise(self):
        results = null_check({})
        assert isinstance(results, list)

    def test_pct_affected_is_correct(self):
        orders = _orders(20)
        orders.loc[0, "order_status"] = None  # 1/20 = 5%
        results = null_check({"orders": orders})
        matches = [r for r in results if r.column == "order_status"]
        assert matches[0].pct_affected == pytest.approx(5.0, rel=0.01)


# ---------------------------------------------------------------------------
# duplicate_check
# ---------------------------------------------------------------------------

class TestDuplicateCheck:
    def test_detects_duplicate_order_id(self):
        orders = _orders()
        dup = orders.iloc[[0]].copy()
        orders = pd.concat([orders, dup], ignore_index=True)
        results = duplicate_check({"orders": orders})
        assert any(r.table == "orders" for r in results)

    def test_detects_duplicate_composite_key(self):
        payments = _order_payments()
        dup = payments.iloc[[0]].copy()
        payments = pd.concat([payments, dup], ignore_index=True)
        results = duplicate_check({"order_payments": payments})
        assert any(r.table == "order_payments" for r in results)

    def test_duplicate_severity_is_always_high(self):
        orders = _orders()
        orders = pd.concat([orders, orders.iloc[[0]].copy()], ignore_index=True)
        results = duplicate_check({"orders": orders})
        for r in results:
            assert r.severity == "HIGH"

    def test_clean_data_produces_no_findings(self):
        assert duplicate_check({"orders": _orders(), "order_payments": _order_payments()}) == []


# ---------------------------------------------------------------------------
# range_check
# ---------------------------------------------------------------------------

class TestRangeCheck:
    def test_detects_review_score_above_ceiling(self):
        reviews = _order_reviews()
        reviews.loc[0, "review_score"] = 6
        results = range_check({"order_reviews": reviews})
        matches = [r for r in results if r.column == "review_score"]
        assert matches[0].rows_affected == 1

    def test_detects_review_score_below_floor(self):
        reviews = _order_reviews()
        reviews.loc[0, "review_score"] = 0
        results = range_check({"order_reviews": reviews})
        matches = [r for r in results if r.column == "review_score"]
        assert matches[0].rows_affected == 1

    def test_detects_multiple_out_of_range_values(self):
        reviews = _order_reviews()
        reviews.loc[0, "review_score"] = 6
        reviews.loc[1, "review_score"] = 0
        results = range_check({"order_reviews": reviews})
        matches = [r for r in results if r.column == "review_score"]
        assert matches[0].rows_affected == 2

    def test_valid_scores_produce_no_findings(self):
        results = range_check({"order_reviews": _order_reviews()})
        assert not any(r.column == "review_score" for r in results)


# ---------------------------------------------------------------------------
# negative_value_check
# ---------------------------------------------------------------------------

class TestNegativeValueCheck:
    def test_detects_negative_payment_value(self):
        payments = _order_payments()
        payments.loc[0, "payment_value"] = -99.0
        results = negative_value_check({"order_payments": payments})
        matches = [r for r in results if r.column == "payment_value"]
        assert matches[0].rows_affected == 1
        assert matches[0].severity == "HIGH"

    def test_detects_negative_item_price(self):
        items = _order_items()
        items.loc[0, "price"] = -10.0
        results = negative_value_check({"order_items": items})
        assert any(r.column == "price" for r in results)

    def test_detects_negative_freight_value(self):
        items = _order_items()
        items.loc[0, "freight_value"] = -1.0
        results = negative_value_check({"order_items": items})
        assert any(r.column == "freight_value" for r in results)

    def test_clean_data_produces_no_findings(self):
        results = negative_value_check({
            "order_payments": _order_payments(),
            "order_items": _order_items(),
        })
        assert results == []


# ---------------------------------------------------------------------------
# future_date_check
# ---------------------------------------------------------------------------

class TestFutureDateCheck:
    def test_detects_future_delivery_date(self):
        orders = _orders()
        orders["order_delivered_customer_date"] = pd.to_datetime(
            orders["order_delivered_customer_date"]
        )
        orders.loc[0, "order_delivered_customer_date"] = pd.Timestamp("2025-01-01")
        results = future_date_check({"orders": orders})
        assert any(r.column == "order_delivered_customer_date" for r in results)

    def test_clean_dates_produce_no_findings(self):
        assert future_date_check({"orders": _orders()}) == []

    def test_missing_table_does_not_raise(self):
        results = future_date_check({})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# freight_ratio_check
# ---------------------------------------------------------------------------

class TestFreightRatioCheck:
    def test_detects_freight_exceeding_price(self):
        items = _order_items()
        items.loc[0, "price"] = 10.0
        items.loc[0, "freight_value"] = 30.0  # ratio = 3.0 > 1.0
        results = freight_ratio_check({"order_items": items})
        assert len(results) == 1
        assert results[0].rows_affected == 1
        assert results[0].severity == "LOW"

    def test_normal_freight_produces_no_findings(self):
        assert freight_ratio_check({"order_items": _order_items()}) == []

    def test_zero_price_rows_are_excluded(self):
        items = _order_items()
        items.loc[0, "price"] = 0.0
        items.loc[0, "freight_value"] = 999.0
        results = freight_ratio_check({"order_items": items})
        assert results == []

    def test_exactly_at_threshold_not_flagged(self):
        items = _order_items()
        items.loc[0, "price"] = 10.0
        items.loc[0, "freight_value"] = 10.0  # ratio = 1.0, not > 1.0
        results = freight_ratio_check({"order_items": items})
        assert results == []


# ---------------------------------------------------------------------------
# state_machine_check
# ---------------------------------------------------------------------------

class TestStateMachineCheck:
    def test_detects_delivered_date_with_wrong_status(self):
        orders = _orders()
        orders["order_delivered_customer_date"] = pd.to_datetime(
            orders["order_delivered_customer_date"]
        )
        orders.loc[0, "order_status"] = "processing"  # delivered date present but wrong status
        results = state_machine_check({"orders": orders})
        assert len(results) == 1
        assert results[0].rows_affected == 1
        assert results[0].severity == "HIGH"

    def test_multiple_mismatches_all_counted(self):
        orders = _orders()
        orders["order_delivered_customer_date"] = pd.to_datetime(
            orders["order_delivered_customer_date"]
        )
        orders.loc[0, "order_status"] = "processing"
        orders.loc[1, "order_status"] = "shipped"
        results = state_machine_check({"orders": orders})
        assert results[0].rows_affected == 2

    def test_correct_status_produces_no_findings(self):
        assert state_machine_check({"orders": _orders()}) == []

    def test_null_delivery_date_not_flagged(self):
        orders = _orders()
        orders["order_delivered_customer_date"] = None
        orders["order_status"] = "processing"
        results = state_machine_check({"orders": orders})
        assert results == []


# ---------------------------------------------------------------------------
# category_drift_check
# ---------------------------------------------------------------------------

class TestCategoryDriftCheck:
    def test_detects_english_category_name(self):
        products = _products()
        products.loc[0, "product_category_name"] = "computers_accessories"  # English
        results = category_drift_check({
            "products": products,
            "category_translation": _translation(),
        })
        assert len(results) == 1
        assert results[0].rows_affected == 1

    def test_all_portuguese_names_produce_no_findings(self):
        results = category_drift_check({
            "products": _products(),
            "category_translation": _translation(),
        })
        assert results == []

    def test_missing_translation_table_does_not_raise(self):
        results = category_drift_check({"products": _products()})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# run_all_checks — integration
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_returns_list_of_check_results(self):
        results = run_all_checks(_clean_tables())
        assert isinstance(results, list)
        assert all(isinstance(r, CheckResult) for r in results)

    def test_clean_data_produces_no_high_findings(self):
        results = run_all_checks(_clean_tables())
        high = [r for r in results if r.severity == "HIGH"]
        assert len(high) == 0

    def test_multiple_anomalies_all_detected(self):
        tables = _clean_tables()
        # Inject 3 distinct anomalies
        tables["orders"].loc[0, "customer_id"] = None          # null
        tables["order_reviews"].loc[0, "review_score"] = 6     # range
        tables["order_payments"].loc[0, "payment_value"] = -1  # negative
        results = run_all_checks(tables)
        check_names = {r.check_name for r in results}
        assert "null_check" in check_names
        assert "range_check" in check_names
        assert "negative_value_check" in check_names
