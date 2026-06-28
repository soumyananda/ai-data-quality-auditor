# AI Data Quality Auditor

**An AI-powered data quality auditor that finds what rules miss — using Claude to explain *why* an anomaly matters, not just that it exists.**

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![Powered by Claude](https://img.shields.io/badge/Powered%20by-Claude%20Sonnet-orange)
![MIT License](https://img.shields.io/badge/License-MIT-green)

---

## The Problem

Data quality rules catch what you already know to look for. This tool is built for the "I have a new dataset and I don't know what's in it" gap — the exploratory audit before you've written a single expectation. It finds orphaned foreign keys, state-machine violations, coordinate transpositions, duplicate payment records, and encoding drift — then tells you *why* each one matters and what broke in your pipeline to cause it.

Great Expectations and dbt tests are powerful contracts for datasets you already understand. This tool is for the ones you don't yet.

---

## What It Detects

**Single-Table Checks (8)**

- **Null rate check** — Flags non-nullable columns with missing values; severity ladder based on percentage (>5% → HIGH, 1–5% → MEDIUM, <1% → LOW)
- **Duplicate key check** — Finds rows that violate declared unique constraints (e.g., duplicate `(order_id, payment_sequential)` in `order_payments`)
- **Range / domain check** — Detects values outside declared bounds or outside a valid set (e.g., `review_score` outside {1,2,3,4,5}; geolocation coordinates outside Brazil's bounding box)
- **Future date check** — Flags past-event timestamps (delivery dates, approval timestamps) that land after the dataset's known ceiling date
- **Negative monetary value check** — Detects negative `price`, `freight_value`, or `payment_value` — always HIGH severity; negative amounts indicate sign-error bugs
- **Category encoding drift check** — In the `products` table, finds Portuguese category names replaced by English equivalents from the translation table — a cross-table encoding contamination
- **Freight ratio check** — Identifies line items where `freight_value / price > 1.0`, indicating economically anomalous shipping cost data
- **State machine violation check** — Finds `orders` rows with a non-null `order_delivered_customer_date` but `order_status != 'delivered'` — impossible lifecycle state combinations

**Cross-Table Checks (4)**

- **Referential integrity** — Checks all 6 FK relationships (order_items → orders, sellers, products; order_payments → orders; order_reviews → orders; orders → customers). Severity scales with orphan rate (>1% → HIGH).
- **Payment / order-item reconciliation** — For each order, compares sum of `order_payments.payment_value` against sum of `order_items.(price + freight_value)`. Discrepancies > $0.50 are flagged (>2% of orders → HIGH).
- **Geolocation bounds check** — Flags coordinates outside Brazil's bounding box (lat −34 to +5, lng −74 to −34), including lat/lng swap detection.
- **Duplicate payment check** — Finds duplicate `(order_id, payment_sequential, payment_value)` triples in `order_payments`. Always HIGH — causes double-counting in revenue reporting.

**10 injected anomaly types** covering all of the above, reproducibly seeded for consistent demo output.

---

## Demo: Sample Report

**[View full sample report →](reports/sample/report_20260628_205948.md)** | **[JSON →](reports/sample/report_20260628_205948.json)**

Run against the Olist dataset with 10 injected anomalies: **20 findings, score 0/100 (FAIL), 9 HIGH · 10 MEDIUM · 1 LOW** across 6 tables.

### Raw data — Duplicate Payment Records

```
order_id                         | payment_sequential | payment_value
---------------------------------|--------------------|---------------
a9810da82917af2d9aefd1278f1dcfa0 | 1                  | 127.50
a9810da82917af2d9aefd1278f1dcfa0 | 1                  | 127.50   ← duplicate
ba78997921bbcdc1373bb41e913ab953 | 1                  |  89.00
ba78997921bbcdc1373bb41e913ab953 | 1                  |  89.00   ← duplicate
```

### After — Claude's analysis (actual output, F001)

```json
{
  "finding_id": "F001",
  "check_name": "duplicate_check",
  "anomaly_type": "Duplicate Composite Key Violation",
  "table": "order_payments",
  "column": null,
  "severity": "HIGH",
  "rows_affected": 3858,
  "pct_affected": 3.65,
  "plain_english_explanation": "The combination of order_id and payment_sequential, which should uniquely identify each payment installment for an order, is duplicated 1,929 times — producing 3,858 affected rows (3.65% of the table). This means the same payment step for the same order appears more than once, making it impossible to determine which record is the authoritative payment event without additional deduplication logic.",
  "likely_root_cause": "The most probable cause is an idempotency failure in the ETL or ingestion pipeline — specifically, the payment records are being re-inserted on each pipeline run rather than upserted, likely because the target table lacks a PRIMARY KEY or UNIQUE constraint on (order_id, payment_sequential) to reject duplicates at the database level.",
  "downstream_impact": "Any revenue reporting, GMV calculations, or payment reconciliation that aggregates payment_value from order_payments will be inflated by the duplicate rows — an order with two duplicate payment_sequential=1 rows will have its payment amount double-counted. Fraud detection models and customer lifetime value features trained on this table will also be corrupted.",
  "recommendation": "1. Immediately add a UNIQUE constraint on (order_id, payment_sequential) to prevent future duplicates and alert on violations. 2. Deduplicate by retaining one row per pair using MAX(ingestion_timestamp) as the tie-breaker. 3. Audit the ETL job to confirm it uses UPSERT rather than blind INSERT.",
  "llm_confidence": "HIGH"
}
```

This is what the LLM layer adds: not "duplicates detected" but a specific root cause hypothesis, the mechanism of corruption, and a three-step remediation plan. **[See all 20 findings →](reports/sample/report_20260628_205948.md)**

---

## Why an LLM, Not Just Rules

A rule can detect "3.2% of `order_items.seller_id` is NULL."

It cannot tell you:

> "This null pattern is consistent with a race condition between order ingestion and seller registration — orders are being created before the seller record is fully committed, producing nulls on every batch load that processes orders faster than sellers. The fix is to enforce seller record existence as a pre-condition in the order creation service, or to introduce a reconciliation job that backfills seller_id for recent orders against the sellers table."

That second paragraph — the root cause, the recurrence pattern, and the remediation path — is what the LLM adds. The deterministic check finds where the problem is. Claude explains what caused it and what to do next.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│         Olist CSV files (9 tables, ~100K rows)  │
└──────────────────────────┬──────────────────────┘
                           │
                      loader.py
                      (loads all 9 CSVs into DataFrames)
                           │
           ┌───────────────┴───────────────────┐
           │                                   │
  checks/single_table.py             config.FK_MANIFEST
  8 deterministic checks             4 referential integrity checks
  (null, duplicate, range,           (orphaned order_items, payments,
   future date, negative value,       reviews, and customer refs)
   category drift, freight ratio,
   state machine)
           │                                   │
           └───────────────┬───────────────────┘
                           │
                   CheckResult objects
                   (check_name, table, column,
                    severity, rows_affected,
                    pct_affected, example_values,
                    raw_details)
                           │
                   llm_analyzer.py
                   Claude Sonnet — one API call per finding
                   (why it matters, root cause,
                    downstream impact, recommendation,
                    confidence; disk-cached by context hash)
                           │
                  report_builder.py
                  (quality score 0-100, PASS/WARN/FAIL verdict,
                   sorted findings, bucketed action plan)
                           │
              ┌────────────┴────────────┐
              │                         │
     reports/*.json             reports/*.md
     (machine-readable,          (human-readable,
      CI-gate friendly)           stakeholder-ready)
```

---

## How to Run

```bash
git clone https://github.com/soumyananda/ai-data-quality-auditor
cd ai-data-quality-auditor
pip install -r requirements.txt

# Download the Olist Brazilian E-Commerce dataset from Kaggle:
# https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
# Place all 9 CSV files in data/raw/

cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY

make run        # inject 10 anomalies into clean data, then run the full audit
# or step by step:
make inject     # inject anomalies → data/injected/
make audit      # run the auditor against data/injected/ → reports/
make test       # run the test suite with coverage
```

Reports are written to `reports/` as both JSON (machine-readable) and Markdown (human-readable). LLM responses are cached in `.cache/llm_responses/` keyed by context hash — re-running against the same data skips API calls.

---

## The Checks

| Single-Table Checks (8) | Cross-Table Checks (4) |
|-------------------------|------------------------|
| Null rate — configurable per column | Referential integrity — all 6 FK relationships |
| Duplicate key — composite key support | Payment / order-item reconciliation |
| Range / domain validation | Geolocation bounds + lat/lng swap detection |
| Future date detection | Duplicate payment triple detection |
| Negative monetary value detection | |
| Category encoding drift | |
| Freight ratio anomaly | |
| Order state machine violation | |

All check configuration (which columns, which constraints, which FK relationships) lives in `auditor/config.py`. Adding a new check or constraint is a single-file edit.

---

## Connection to Production Experience

This project is a simplified prototype of the cross-table quality validation I built for Azure's commerce data platform at Microsoft, where a silent data anomaly in one of 12 upstream services could propagate through to financial reporting before triggering any alerts. That platform processed billions of daily records for thousands of Azure sellers; a single data quality incident in the revenue attribution pipeline took 3 days to trace and corrupted two months of reporting.

The production version included streaming anomaly detection, ML-based statistical outlier detection, SOX-compliant audit trails, and SLA enforcement across 12 services. This prototype demonstrates the detection and analysis layer in a standalone, reproducible form using a public dataset.

---

## Full Product Requirements Document

[Full Product Requirements Document →](PRD.md)

The PRD covers problem statement, user personas (Data Engineer, Data Platform PM, Analytics Engineer), four key use cases, success metrics, architecture decisions with explicit tradeoffs (why hybrid over pure-LLM; why custom checks over Great Expectations), and the V2 roadmap.

---

## About the Author

Principal Technical PM with 20 years at Microsoft building large-scale data and commerce platforms — including Azure Commerce (Principal PM, 2023–2025) and Azure Ecosystems (2017–2023). Led the cross-team data quality initiative across 12 commerce services that reduced data incidents 20% and lifted NPS 5 points. IIT Delhi (B.Tech CS) · IIM Ahmedabad (MBA). Currently targeting Senior/Principal Technical PM and TPM roles in AI, cloud data, and fintech.

[LinkedIn →](https://www.linkedin.com/in/soumyananda007)
