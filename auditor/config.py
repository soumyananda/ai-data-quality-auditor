"""
config.py — Central configuration for the AI Data Quality Auditor.

All schema definitions, constraint manifests, and tuning constants live here.
Import from this module everywhere else; never hardcode table names or column
lists in auditor logic files.
"""

from typing import Dict, List, Tuple, Any

# ---------------------------------------------------------------------------
# TABLE_SCHEMAS
# Logical table name → {column_name: type_string}
# type_string values: "str", "int", "float", "datetime"
# ---------------------------------------------------------------------------

TABLE_SCHEMAS: Dict[str, Dict[str, str]] = {
    "orders": {
        "order_id": "str",
        "customer_id": "str",
        "order_status": "str",
        "order_purchase_timestamp": "datetime",
        "order_approved_at": "datetime",
        "order_delivered_carrier_date": "datetime",
        "order_delivered_customer_date": "datetime",
        "order_estimated_delivery_date": "datetime",
    },
    "order_items": {
        "order_id": "str",
        "order_item_id": "int",
        "product_id": "str",
        "seller_id": "str",
        "shipping_limit_date": "datetime",
        "price": "float",
        "freight_value": "float",
    },
    "order_payments": {
        "order_id": "str",
        "payment_sequential": "int",
        "payment_type": "str",
        "payment_installments": "int",
        "payment_value": "float",
    },
    "order_reviews": {
        "review_id": "str",
        "order_id": "str",
        "review_score": "int",
        "review_comment_title": "str",
        "review_comment_message": "str",
        "review_creation_date": "datetime",
        "review_answer_timestamp": "datetime",
    },
    "customers": {
        "customer_id": "str",
        "customer_unique_id": "str",
        "customer_zip_code_prefix": "str",
        "customer_city": "str",
        "customer_state": "str",
    },
    "sellers": {
        "seller_id": "str",
        "seller_zip_code_prefix": "str",
        "seller_city": "str",
        "seller_state": "str",
    },
    "products": {
        "product_id": "str",
        "product_category_name": "str",
        "product_name_lenght": "int",       # intentional typo from source data
        "product_description_lenght": "int",  # intentional typo from source data
        "product_photos_qty": "int",
        "product_weight_g": "float",
        "product_length_cm": "float",
        "product_height_cm": "float",
        "product_width_cm": "float",
    },
    "geolocation": {
        "geolocation_zip_code_prefix": "str",
        "geolocation_lat": "float",
        "geolocation_lng": "float",
        "geolocation_city": "str",
        "geolocation_state": "str",
    },
    "category_translation": {
        "product_category_name": "str",
        "product_category_name_english": "str",
    },
}

# ---------------------------------------------------------------------------
# FK_MANIFEST
# (child_table, child_col, parent_table, parent_col)
# Every tuple asserts that every non-null child_col value must appear in
# parent_col. The auditor's FK check will surface orphan rows.
# ---------------------------------------------------------------------------

FK_MANIFEST: List[Tuple[str, str, str, str]] = [
    # order_items references
    ("order_items", "order_id",  "orders",   "order_id"),
    ("order_items", "seller_id", "sellers",  "seller_id"),
    ("order_items", "product_id","products", "product_id"),
    # order_payments references
    ("order_payments", "order_id", "orders", "order_id"),
    # order_reviews references
    ("order_reviews", "order_id", "orders", "order_id"),
    # orders references customers (orders is the child here)
    ("orders", "customer_id", "customers", "customer_id"),
]

# ---------------------------------------------------------------------------
# NULL_CONSTRAINTS
# table → list of columns that must have no null values.
# Deliberately excludes optional free-text fields (review comments, etc.)
# ---------------------------------------------------------------------------

NULL_CONSTRAINTS: Dict[str, List[str]] = {
    "orders": [
        "order_id",
        "customer_id",
        "order_status",
        "order_purchase_timestamp",
        "order_estimated_delivery_date",
    ],
    "order_items": [
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    ],
    "order_payments": [
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_value",
    ],
    "order_reviews": [
        "review_id",
        "order_id",
        "review_score",
        "review_creation_date",
    ],
    "customers": [
        "customer_id",
        "customer_unique_id",
        "customer_zip_code_prefix",
        "customer_city",
        "customer_state",
    ],
    "sellers": [
        "seller_id",
        "seller_zip_code_prefix",
        "seller_city",
        "seller_state",
    ],
    "products": [
        "product_id",
    ],
    "geolocation": [
        "geolocation_zip_code_prefix",
        "geolocation_lat",
        "geolocation_lng",
        "geolocation_city",
        "geolocation_state",
    ],
    "category_translation": [
        "product_category_name",
        "product_category_name_english",
    ],
}

# ---------------------------------------------------------------------------
# UNIQUE_CONSTRAINTS
# table → list of column-lists. Each inner list defines one unique key.
# A composite key means the *combination* must be unique.
# ---------------------------------------------------------------------------

UNIQUE_CONSTRAINTS: Dict[str, List[List[str]]] = {
    "orders":         [["order_id"]],
    "order_payments": [["order_id", "payment_sequential"]],
    "order_reviews":  [["review_id"]],
    "products":       [["product_id"]],
    "sellers":        [["seller_id"]],
    "customers":      [["customer_id"]],
}

# ---------------------------------------------------------------------------
# RANGE_CONSTRAINTS
# (table, column) → {"min": val, "max": val}  |  {"valid_set": [...]}
# ---------------------------------------------------------------------------

RANGE_CONSTRAINTS: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("order_reviews",  "review_score"):         {"valid_set": [1, 2, 3, 4, 5]},
    ("order_payments", "payment_value"):         {"min": 0},
    ("order_payments", "payment_installments"):  {"min": 1, "max": 24},
    ("order_items",    "price"):                 {"min": 0},
    ("order_items",    "freight_value"):         {"min": 0},
    ("products",       "product_weight_g"):      {"min": 0},
    ("products",       "product_photos_qty"):    {"min": 0},
    ("geolocation",    "geolocation_lat"):       {"min": -34.0, "max": 5.0},
    ("geolocation",    "geolocation_lng"):       {"min": -74.0, "max": -34.0},
}

# ---------------------------------------------------------------------------
# DATE_COLUMNS
# table → list of columns that should be parsed as datetime
# ---------------------------------------------------------------------------

DATE_COLUMNS: Dict[str, List[str]] = {
    "orders": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "order_items": [
        "shipping_limit_date",
    ],
    "order_reviews": [
        "review_creation_date",
        "review_answer_timestamp",
    ],
}

# ---------------------------------------------------------------------------
# MAX_VALID_DATE — Olist dataset ceiling; anything after is suspect
# ---------------------------------------------------------------------------

MAX_VALID_DATE: str = "2018-10-17"

# ---------------------------------------------------------------------------
# QUALITY_SCORE_WEIGHTS
# Points deducted per finding, bucketed by severity.
# Score starts at 100 and is reduced proportionally to % rows affected.
# ---------------------------------------------------------------------------

QUALITY_SCORE_WEIGHTS: Dict[str, int] = {
    "HIGH":   15,
    "MEDIUM":  7,
    "LOW":     3,
}

# ---------------------------------------------------------------------------
# VERDICT_THRESHOLDS
# Final score → verdict string used in the report header
# ---------------------------------------------------------------------------

VERDICT_THRESHOLDS: Dict[str, int] = {
    "FAIL": 50,   # score < 50  → FAIL
    "WARN": 80,   # score < 80  → WARN (else PASS)
}

# ---------------------------------------------------------------------------
# TABLE_FILENAMES
# Logical table name → CSV filename in data/raw/ (or data/injected/)
# ---------------------------------------------------------------------------

TABLE_FILENAMES: Dict[str, str] = {
    "orders":              "olist_orders_dataset.csv",
    "order_items":         "olist_order_items_dataset.csv",
    "order_payments":      "olist_order_payments_dataset.csv",
    "order_reviews":       "olist_order_reviews_dataset.csv",
    "customers":           "olist_customers_dataset.csv",
    "sellers":             "olist_sellers_dataset.csv",
    "products":            "olist_products_dataset.csv",
    "geolocation":         "olist_geolocation_dataset.csv",
    "category_translation":"product_category_name_translation.csv",
}

# ---------------------------------------------------------------------------
# TABLE_DESCRIPTIONS
# One-sentence role description injected into LLM prompts for context.
# ---------------------------------------------------------------------------

TABLE_DESCRIPTIONS: Dict[str, str] = {
    "orders": (
        "The central fact table recording every customer order, its status, "
        "and key lifecycle timestamps from purchase through estimated delivery."
    ),
    "order_items": (
        "Line-item detail for each order, linking orders to specific products "
        "and sellers with individual price and freight values."
    ),
    "order_payments": (
        "Payment transaction records for orders, supporting multiple sequential "
        "payments and installment plans per order."
    ),
    "order_reviews": (
        "Post-delivery customer satisfaction reviews with numeric scores and "
        "optional free-text comments, anchored to a specific order."
    ),
    "customers": (
        "Customer master data including anonymized unique customer ID, zip code, "
        "city, and state — used to link orders to buyer geography."
    ),
    "sellers": (
        "Seller master data with location (zip, city, state) used to route "
        "logistics and attribute revenue to marketplace merchants."
    ),
    "products": (
        "Product catalog with category, physical dimensions, and weight — "
        "critical inputs for freight pricing and inventory management."
    ),
    "geolocation": (
        "Zip-code-level latitude/longitude lookup table covering both customer "
        "and seller zip codes, used for distance and logistics analysis."
    ),
    "category_translation": (
        "Mapping table that translates Portuguese product category names into "
        "English equivalents for international reporting."
    ),
}
