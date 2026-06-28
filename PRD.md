# Product Requirements Document
## AI Data Quality Auditor v1.0

**Author:** Soumya Nanda  
**Status:** v1.0 shipped  
**Last updated:** June 2026

---

## 1. Problem Statement

At Microsoft's Commerce data platform, I experienced a relatively large number of data quality incidents because we had not systematically built checks to detect quality issues across our data stack.  A data pipeline failure for a single upstream service would propagate silently through downstream services before appearing in Azure cost management reporting exposed to customers.

The root cause was a structural gap in how we validated data: every check we had was a rule someone had written in advance, triggered by a condition someone had already thought to look for. The corrupted records — seller IDs that went null during a partial pipeline replay — violated no pre-written rule because no one had written that rule yet.

This is the gap that existing data quality tooling does not fill. Great Expectations and dbt tests are powerful, but they require you to pre-specify every expectation before you know what's wrong. For a new dataset, a newly onboarded vendor feed, or a pipeline that just changed, that means running blind until something breaks downstream.

Large language models can reason about anomalies they were never programmed to find. Given a row count, a percentage, and example values, Claude can identify that "3.2% null seller_ids in order_items, concentrated in a two-hour window" is consistent with a race condition between order ingestion and seller registration — not a schema change, not a truncation bug. That interpretation — the root cause and recurrence pattern — is what separates a finding from an insight.

The AI Data Quality Auditor is built for the "I have a new dataset and I don't know what's in it" gap: the exploratory audit before you've written a single expectation. It runs deterministic Python checks to detect what is wrong, then calls Claude to explain why it matters, what broke in the pipeline to cause it, and what to do first.

---

## 2. User Personas

### Persona 1: Data Engineer — "What broke and where in the stack?"

**Context:** Owns the ingestion pipeline. Gets paged when downstream consumers report bad data. Blocked on writing exhaustive rule sets for every new dataset before they can catch anything.

**Pain points:**
- New dataset arrives from a vendor or upstream team. No schema documentation. No prior incidents to learn from.
- Writing Great Expectations suites for a new table takes hours and still only catches what you already know to look for.
- When something breaks, tracing a data issue through 8 relational tables to its source is multi-hour detective work.

**What they need from this tool:**
- Run a full audit in under 3 minutes without writing a single expectation first.
- Get a specific root cause hypothesis they can validate against pipeline logs — not "nulls detected" but "null pattern consistent with a race condition in batch load ordering."
- Know exactly which table, which column, and which rows are affected so they can write a targeted fix.

---

### Persona 2: Data Platform PM — "What's the business impact and how do I explain it?"

**Context:** Owns the data quality SLA. Accountable to finance, analytics, and product teams who depend on clean data. Cannot read Python or SQL but needs to communicate severity to stakeholders and prioritize remediation work.

**Pain points:**
- Engineering surfaces a "HIGH severity null rate finding." The PM has no way to translate that into business impact — does this affect revenue reporting? Customer-facing features? Both?
- Determining whether to declare an incident requires understanding downstream blast radius, which requires reading code they don't own.
- Quality scoring is informal and inconsistent across teams.

**What they need from this tool:**
- A plain-English summary of every finding that a non-technical stakeholder can read in a standup.
- A quality score (0–100) and PASS / WARN / FAIL verdict they can report upward without qualification.
- A prioritized action plan bucketed by remediation urgency (immediate / this sprint / monitoring).

---

### Persona 3: Analytics Engineer — "Which source columns are poisoned before I build on them?"

**Context:** Owns dbt models that transform raw source tables into analytics-ready datasets. Cannot afford to discover a data quality issue after building three downstream models on top of a corrupted column.

**Pain points:**
- No formal contract on what the source data guarantees. Referential integrity violations in source tables propagate silently into model output.
- Category encoding drift — Portuguese category names replaced by English equivalents — breaks string filters that power dashboards and ML features.
- State-machine violations in the orders table produce impossible combinations that corrupt funnel metrics downstream.

**What they need from this tool:**
- Know which columns have integrity violations before writing the first dbt model.
- Understand whether a finding is a source data issue (fix upstream) or a transformation issue (fix in dbt).
- Get specific example values for every finding so they can validate their model logic against known-bad inputs.

---

## 3. Key Use Cases

### UC1: Audit a new dataset before onboarding to production lakehouse

A data engineer receives a new vendor dataset. Before registering it as a source table in the production lakehouse and building any downstream models on it, they need to know: are the primary keys actually unique? Are all foreign key references resolvable? Are monetary values non-negative? Are category labels consistent with expected encoding?

Running `make run` on the new dataset surfaces all violations in under 3 minutes. The report gives a PASS / WARN / FAIL verdict with specific remediation steps before a single downstream model is built.

**Success signal:** No HIGH severity findings slip through to downstream consumers for any dataset that has been audited first.

---

### UC2: Regression audit after an ETL pipeline change

A data engineer modifies the order payment ingestion pipeline to support installment plans. The change introduces an idempotency bug that causes some payment events to be written twice.

Running the auditor against the post-change dataset flags duplicate records on (order_id, payment_sequential) with HIGH severity. The LLM identifies the most probable cause as an at-least-once delivery guarantee without idempotency checks — pointing the engineer directly at the pipeline logic to fix.

**Success signal:** Regression-caused anomalies are detected in the first post-change audit run, not in a downstream finance report.

---

### UC3: Vendor data validation before ingesting third-party data

A marketplace PM is onboarding a new third-party seller data feed. Before trusting seller location data to power logistics routing, they need to verify coordinate accuracy and referential integrity between the seller feed and the existing customer geography tables.

The auditor runs geolocation bounds checks (latitude must be within Brazil's bounds: -34° to 5°) and flags coordinate transpositions where latitude and longitude values were swapped. The LLM explains that swapped coordinates would route logistics requests to invalid locations, producing failed deliveries.

**Success signal:** Coordinate anomalies are caught before seller data is used in any logistics calculation.

---

### UC4: Incident post-mortem — build a detection library for a known failure mode

After a revenue attribution incident caused by duplicate payment records, the data platform team wants to ensure the same failure mode is detectable in future. They run the auditor against a snapshot of the dataset that caused the incident and use the findings to validate that the duplicate_check would have caught it.

The report becomes the baseline for a new monitoring rule and provides the exact evidence needed for a post-mortem root cause analysis.

**Success signal:** Every post-mortem produces at least one new detection rule. The auditor's findings match the incident's actual root cause in the post-mortem review.

---

## 4. Success Metrics

| Metric | Target | Rationale |
|--------|--------|-----------|
| Detection rate for HIGH severity anomalies | >90% | Missing a HIGH severity finding causes downstream damage before it is caught |
| False positive rate | <10% | False positives erode trust; engineers stop reading reports that cry wolf |
| Time to report for full Olist dataset (100K orders, 8 tables) | <3 minutes | Must be fast enough to run as a pre-commit gate or pipeline step |
| Actionability | 100% of findings have a concrete recommendation | Enforced by schema — `recommendation` field is required in `LLMAnalysis` |
| Quality score reproducibility | Deterministic given same input | Rules-based checks are fully deterministic; LLM responses are cached on disk |

**Quality score formula (v1 heuristic):** `score = max(0, 100 − (HIGH × 15 + MEDIUM × 7 + LOW × 3))`

The severity weights (15 / 7 / 3) are calibrated against incident blast radius observed in production data platform operations: HIGH findings (referential integrity breaks, duplicate financial records, negative payment values) have historically required multi-day remediation and corrupted downstream financial reporting; MEDIUM findings (encoding drift, future timestamps) typically require hours and affect a narrower downstream surface; LOW findings (excessive freight ratios) are anomaly signals worth monitoring but rarely block consumption. These weights are a v1 heuristic and should be recalibrated against labeled incident data once the tool has been running in production for 90+ days.

---

## 5. Architecture Decisions and Tradeoffs

### Decision 1: Rules-based detection + LLM interpretation (hybrid), not pure LLM or pure rules

**Choice:** Deterministic Python checks produce `CheckResult` objects (what, where, how many). Claude receives those objects and produces `LLMAnalysis` (why it matters, root cause, recommendation).

**Why not pure LLM:** LLMs are unreliable at counting rows, computing percentages, or consistently detecting every null in a 100K-row DataFrame. A language model given raw CSV data will miss findings that a `df.isna().sum()` call catches in milliseconds. Detection must be deterministic.

**Why not pure rules:** A rule can detect "3.2% of order_items.seller_id is NULL." It cannot tell you "this null pattern is consistent with a race condition between order ingestion and seller registration — orders are being created before the seller record is fully committed, producing nulls on every batch load that processes orders faster than sellers." The root cause hypothesis, the downstream blast radius description, and the remediation priority are all LLM contributions.

**Tradeoff accepted:** The LLM analysis is only as good as the context bundle passed to it. If a check does not capture the right raw details, the LLM cannot infer them. The `raw_details` field in `CheckResult` must be thoughtfully populated per check.

---

### Decision 2: Custom detection layer, not Great Expectations or dbt tests

**Choice:** All 12 checks are implemented as Python functions in `auditor/checks/`.

**Why not Great Expectations:** Great Expectations requires the user to define an expectation suite before running validation. This is ideal for stable datasets with known schemas where you are enforcing a contract. It is the wrong tool for exploratory quality analysis on an unfamiliar dataset — you cannot write expectations for failure modes you have not yet discovered.

**Why not dbt tests:** dbt tests are transformation-time validations that run inside the dbt graph. They are excellent for testing model output but require dbt to be the transformation layer. This tool is intentionally upstream of any transformation layer, validating raw source data before it enters a lakehouse.

**Tradeoff accepted:** Custom checks require maintenance as dataset schemas evolve. All check configuration (NULL_CONSTRAINTS, UNIQUE_CONSTRAINTS, RANGE_CONSTRAINTS, FK_MANIFEST) lives in `auditor/config.py` to make schema changes require a single-file edit.

---

### Decision 3: Anthropic Claude API, not a locally hosted LLM

**Choice:** `claude-sonnet-4-6` via the Anthropic Python SDK, with disk-based response caching to minimize repeat API calls.

**Why Claude over a local LLM:** The quality of root-cause explanation and downstream impact analysis is the primary value-add of the LLM layer. Local models (Llama, Mistral, Phi) produce materially worse natural-language reasoning for this use case — their root cause hypotheses are generic ("possible upstream issue") where Claude produces specific, technically accurate explanations grounded in the data engineering context provided in the system prompt.

**Why Claude over GPT-4o / Gemini:** This is a portfolio tool demonstrating integration with Anthropic's API. The system prompt, response parsing, and retry logic are designed to work with the Anthropic SDK's interface. Switching models would require minimal code changes.

**Tradeoff accepted:** API dependency means the tool requires a network connection and an API key. For air-gapped environments or cost-sensitive production deployments, the LLM layer should be replaced with a local model or skipped (the `CheckResult` layer still produces valid, actionable output without it). Disk caching mitigates repeat costs for development iteration.

---

## 5a. Scale Considerations

*This v1 tool processes CSV files in-memory with pandas — appropriate for exploratory audits on datasets up to ~1M rows. This section describes what changes at production lakehouse scale and frames v2 design decisions.*

### What works unchanged at scale

The LLM layer is inherently scale-agnostic. `llm_analyzer.py` receives aggregated `CheckResult` objects — row counts, percentages, and example values — not raw data. A finding from a 50-row DataFrame and a finding from a 500M-row BigQuery table produce identical context bundles. Analysis quality does not degrade with data volume.

### What breaks at scale and how to fix it

**In-memory FK lookups (`cross_table.py`):** The referential integrity check loads both child and parent tables into memory and performs a Python `isin()` lookup. At 500M rows in a parent table, this exhausts memory. At BigQuery or Databricks scale, this check becomes a SQL `NOT EXISTS` subquery pushed down to the query engine — the Python layer never touches raw rows.

**Full-table null scans (`single_table.py`):** Scanning every column for nulls via `df.isna().sum()` is a full in-memory table scan. The production equivalent is `COUNT(*) WHERE column IS NULL` with partition pruning applied to the relevant time window, run directly against the warehouse.

**The practical rewrite:** At scale, the detection layer (`checks/`) becomes a SQL generator — each check function emits a parameterized SQL query that runs against a BigQuery, Delta, or Snowflake table via connector. The `CheckResult` schema remains identical. The LLM layer and report layer are unchanged. This is the key architectural property: the analysis pipeline above the detection layer requires no modification to scale.

### API cost at scale

At ~15 findings per audit run this tool makes ~15 Claude API calls. At 500 tables audited nightly, that is ~7,500 calls per run. Mitigations: (1) disk cache prevents re-analysis of unchanged findings across runs; (2) a `--min-severity MEDIUM` flag skips LLM analysis for LOW findings when cost is a concern; (3) a v2 optimization would batch findings by anomaly type in a single LLM call rather than one call per finding.

---

## 6. V2 Roadmap

### V2.1: Streaming mode with filesystem watcher

**What:** Use `watchdog` or `inotify` to watch a data directory for new or modified CSV files and trigger an incremental audit automatically.

**Rationale:** The current tool requires explicit invocation. Production use cases (vendor data drops, nightly ETL outputs) benefit from automated triggering. A streaming mode would allow the auditor to function as a lightweight data quality daemon without requiring pipeline integration.

---

### V2.2: dbt integration — read manifest, write findings as test failures

**What:** Parse `manifest.json` from a dbt project to discover source tables and their column lineage. Write audit findings back as dbt test failures that surface in `dbt test` output and CI pipelines.

**Rationale:** Analytics engineers who use dbt as their primary development environment want findings surfaced where they already work, not in a separate report. This integration would allow the auditor to act as a pre-transformation gate within an existing dbt workflow, with findings visible in the same CI run that validates model logic.

---

### V2.3: Audit history with SQLite persistence and quality score trending

**What:** Persist every audit run to a local SQLite database. Surface a quality score trend chart (score over time per table) and flag when scores are declining across runs.

**Rationale:** A single point-in-time audit score is useful. A trend line is actionable. Declining quality scores for a specific table after a pipeline change are a leading indicator of a regression. This feature converts the auditor from a one-shot tool into a quality monitoring system with memory.

---

### V2.4: Custom anomaly plugin API

**What:** Define a public interface for user-defined checks that can be registered alongside the built-in 12. A plugin provides a check function, a human-readable name, and a severity ladder. The runner discovers and executes plugins automatically.

**Rationale:** The built-in check library covers the most common anomaly classes. Domain-specific datasets have domain-specific failure modes (e.g., a telecom dataset might need IMEI format validation; a payments dataset might need BIN prefix checks). A plugin API extends the tool without requiring changes to the core library.

---

### V2.5: Slack and PagerDuty webhook on FAIL verdict

**What:** When the audit produces a FAIL verdict (score < 50), POST a structured summary to a configured Slack webhook or PagerDuty event API. Include finding count by severity, quality score, and a link to the full report.

**Rationale:** The current tool requires a human to check the report. FAIL-verdict audits should interrupt the team without requiring anyone to remember to check. This closes the loop from detection to notification and allows the auditor to function as a lightweight data SLA enforcement mechanism.

---

## Appendix A: Lakehouse Integration — Unity Catalog

*This appendix describes how AI Data Quality Auditor findings would integrate into a Unity Catalog-based lakehouse environment — the target architecture for production lakehouse deployments.*

### Discovery: Which tables to audit

Unity Catalog exposes table metadata via `information_schema.tables`. A catalog-aware runner queries this to enumerate all tables in a target schema, then audits each one. Tables can be filtered by tag (e.g., `quality_audit = enabled`) or by last-modified timestamp — Delta's `DESCRIBE HISTORY` returns per-commit timestamps, enabling incremental audits that only re-scan tables with new commits since the last run.

### Where audit results live

Findings are written to a dedicated `data_quality` schema in Unity Catalog as Delta tables:

- `data_quality.audit_runs` — one row per audit run: run_id, table_name, score, verdict, timestamp
- `data_quality.findings` — one row per finding: run_id, finding_id, check_name, table, column, severity, rows_affected, pct_affected, anomaly_type, recommendation, needs_review

This makes quality scores queryable alongside the data they describe. An analytics engineer can join `data_quality.findings` against their source table's lineage graph in Unity Catalog to understand which upstream sources have open HIGH severity findings before running a dbt transformation.

### Table-level quality tags

Unity Catalog supports table-level tags via `ALTER TABLE ... SET TAGS`. On audit completion, the runner writes the current quality score and verdict as tags:

```sql
ALTER TABLE {catalog}.{schema}.{table}
SET TAGS ('quality_score' = '{score}', 'quality_verdict' = '{verdict}', 'last_audited' = '{timestamp}');
```

These tags are visible in Catalog Explorer and queryable via `information_schema.table_tags`, giving data consumers a quality signal without opening the full audit report.

### ACID transaction handling with Delta

Delta Lake's transaction log records every table write as a versioned commit. A Unity Catalog-aware auditor would: (1) read the latest commit version via `DESCRIBE HISTORY table LIMIT 1`; (2) compare against the last-audited commit version stored in `data_quality.audit_runs`; (3) skip the audit if no new commits have landed. This prevents redundant re-auditing of unchanged data and enables efficient incremental operation as a scheduled Databricks Job or Unity Catalog workflow.
