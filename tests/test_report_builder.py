"""
tests/test_report_builder.py

Unit tests for auditor/report_builder.py.

Tests cover: quality score formula, verdict thresholds, findings sorting,
needs_review flag, JSON structure, Markdown generation, and recommendation
bucketing.  No API calls — LLMAnalysis objects are constructed directly.
"""
from __future__ import annotations

import json
import os

import pytest

from auditor.models import CheckResult, LLMAnalysis
from auditor import report_builder as rb
from auditor.report_builder import build_and_write


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _result(
    severity: str = "HIGH",
    check_name: str = "test_check",
    table: str = "orders",
    column: str = "order_id",
    rows: int = 100,
    total: int = 1000,
) -> CheckResult:
    return CheckResult(
        check_name=check_name,
        table=table,
        column=column,
        severity=severity,
        rows_affected=rows,
        total_rows=total,
        pct_affected=round(rows / total * 100, 4),
        example_values=["val_a", "val_b"],
        raw_details={"context": "unit test"},
    )


def _analysis(confidence: str = "HIGH") -> LLMAnalysis:
    return LLMAnalysis(
        anomaly_type="Test Anomaly",
        plain_english_explanation="This is a synthetic test finding.",
        likely_root_cause="Synthetic root cause for testing.",
        downstream_impact="Synthetic downstream impact.",
        recommendation="Step 1. Step 2. Step 3.",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Quality score — _compute_score
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_no_findings_returns_100(self):
        assert rb._compute_score([]) == 100

    def test_one_high_deducts_15(self):
        assert rb._compute_score([_result("HIGH")]) == 85

    def test_one_medium_deducts_7(self):
        assert rb._compute_score([_result("MEDIUM")]) == 93

    def test_one_low_deducts_3(self):
        assert rb._compute_score([_result("LOW")]) == 97

    def test_mixed_severities_deduct_correctly(self):
        results = [_result("HIGH"), _result("MEDIUM"), _result("LOW")]
        assert rb._compute_score(results) == 75  # 100 - 15 - 7 - 3

    def test_score_floored_at_zero(self):
        results = [_result("HIGH")] * 20  # 20 × 15 = 300 → clamped to 0
        assert rb._compute_score(results) == 0

    def test_multiple_highs_accumulate(self):
        results = [_result("HIGH")] * 3
        assert rb._compute_score(results) == 55  # 100 - 45


# ---------------------------------------------------------------------------
# Verdict — _verdict
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_100_is_pass(self):
        assert rb._verdict(100) == "PASS"

    def test_80_is_pass(self):
        assert rb._verdict(80) == "PASS"

    def test_79_is_warn(self):
        assert rb._verdict(79) == "WARN"

    def test_50_is_warn(self):
        assert rb._verdict(50) == "WARN"

    def test_49_is_fail(self):
        assert rb._verdict(49) == "FAIL"

    def test_0_is_fail(self):
        assert rb._verdict(0) == "FAIL"


# ---------------------------------------------------------------------------
# needs_review flag
# ---------------------------------------------------------------------------

class TestNeedsReview:
    def test_low_confidence_sets_needs_review_true(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis("LOW")], str(tmp_path))
        with open(json_path) as f:
            report = json.load(f)
        assert report["findings"][0]["needs_review"] is True

    def test_high_confidence_sets_needs_review_false(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis("HIGH")], str(tmp_path))
        with open(json_path) as f:
            report = json.load(f)
        assert report["findings"][0]["needs_review"] is False

    def test_medium_confidence_sets_needs_review_false(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis("MEDIUM")], str(tmp_path))
        with open(json_path) as f:
            report = json.load(f)
        assert report["findings"][0]["needs_review"] is False

    def test_summary_counts_needs_human_review(self, tmp_path):
        results = [_result(), _result(), _result()]
        analyses = [_analysis("LOW"), _analysis("HIGH"), _analysis("LOW")]
        json_path, _ = build_and_write(results, analyses, str(tmp_path))
        with open(json_path) as f:
            report = json.load(f)
        assert report["summary"]["needs_human_review"] == 2

    def test_markdown_contains_low_confidence_callout(self, tmp_path):
        _, md_path = build_and_write([_result()], [_analysis("LOW")], str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        assert "Needs human review" in content
        assert "LLM confidence is LOW" in content

    def test_markdown_callout_absent_for_high_confidence(self, tmp_path):
        _, md_path = build_and_write([_result()], [_analysis("HIGH")], str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        assert "LLM confidence is LOW" not in content


# ---------------------------------------------------------------------------
# JSON report structure
# ---------------------------------------------------------------------------

class TestJsonReportStructure:
    def test_required_top_level_keys(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            report = json.load(f)
        for key in ("report_metadata", "summary", "findings", "recommendations"):
            assert key in report

    def test_report_metadata_fields(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            meta = json.load(f)["report_metadata"]
        for field in ("report_id", "generated_at", "dataset", "llm_model",
                      "total_tables_scanned", "total_rows_scanned"):
            assert field in meta

    def test_summary_fields(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            summary = json.load(f)["summary"]
        for field in ("overall_quality_score", "verdict", "total_findings",
                      "findings_by_severity", "tables_with_issues",
                      "most_critical_finding", "needs_human_review"):
            assert field in summary

    def test_finding_fields(self, tmp_path):
        json_path, _ = build_and_write([_result()], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            finding = json.load(f)["findings"][0]
        for field in ("finding_id", "check_name", "anomaly_type", "table", "column",
                      "severity", "rows_affected", "total_rows", "pct_affected",
                      "plain_english_explanation", "likely_root_cause",
                      "downstream_impact", "recommendation", "llm_confidence",
                      "needs_review"):
            assert field in finding

    def test_finding_ids_are_sequential(self, tmp_path):
        results = [_result("HIGH"), _result("MEDIUM"), _result("LOW")]
        analyses = [_analysis()] * 3
        json_path, _ = build_and_write(results, analyses, str(tmp_path))
        with open(json_path) as f:
            ids = [f["finding_id"] for f in json.load(f)["findings"]]
        assert ids == ["F001", "F002", "F003"]

    def test_findings_sorted_high_before_medium_before_low(self, tmp_path):
        # Input order: LOW, HIGH, MEDIUM — output should be HIGH, MEDIUM, LOW
        results = [_result("LOW"), _result("HIGH"), _result("MEDIUM")]
        analyses = [_analysis()] * 3
        json_path, _ = build_and_write(results, analyses, str(tmp_path))
        with open(json_path) as f:
            severities = [f["severity"] for f in json.load(f)["findings"]]
        assert severities == ["HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Recommendations bucketing
# ---------------------------------------------------------------------------

class TestRecommendationBucketing:
    def test_high_goes_to_immediate(self, tmp_path):
        json_path, _ = build_and_write([_result("HIGH")], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            recs = json.load(f)["recommendations"]
        assert len(recs["immediate"]) == 1
        assert len(recs["short_term"]) == 0
        assert len(recs["monitoring"]) == 0

    def test_medium_goes_to_short_term(self, tmp_path):
        json_path, _ = build_and_write([_result("MEDIUM")], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            recs = json.load(f)["recommendations"]
        assert len(recs["short_term"]) == 1

    def test_low_goes_to_monitoring(self, tmp_path):
        json_path, _ = build_and_write([_result("LOW")], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            recs = json.load(f)["recommendations"]
        assert len(recs["monitoring"]) == 1

    def test_mixed_findings_bucketed_correctly(self, tmp_path):
        results = [_result("HIGH"), _result("HIGH"), _result("MEDIUM"), _result("LOW")]
        analyses = [_analysis()] * 4
        json_path, _ = build_and_write(results, analyses, str(tmp_path))
        with open(json_path) as f:
            recs = json.load(f)["recommendations"]
        assert len(recs["immediate"]) == 2
        assert len(recs["short_term"]) == 1
        assert len(recs["monitoring"]) == 1

    def test_recommendation_has_priority_field(self, tmp_path):
        json_path, _ = build_and_write([_result("HIGH")], [_analysis()], str(tmp_path))
        with open(json_path) as f:
            item = json.load(f)["recommendations"]["immediate"][0]
        assert "priority" in item
        assert item["priority"] == 1


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

class TestMarkdownReport:
    def test_markdown_file_is_created(self, tmp_path):
        _, md_path = build_and_write([_result()], [_analysis()], str(tmp_path))
        assert os.path.exists(md_path)

    def test_markdown_has_required_sections(self, tmp_path):
        _, md_path = build_and_write([_result()], [_analysis()], str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        for section in ("# Data Quality Audit Report", "## Summary",
                        "## Findings", "## Action Plan"):
            assert section in content

    def test_markdown_includes_finding_details(self, tmp_path):
        results = [_result(severity="HIGH", table="order_payments", column="payment_value")]
        _, md_path = build_and_write(results, [_analysis()], str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        assert "order_payments" in content
        assert "payment_value" in content

    def test_low_confidence_review_section_present(self, tmp_path):
        results = [_result()]
        analyses = [_analysis("LOW")]
        _, md_path = build_and_write(results, analyses, str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        assert "Needs Human Review" in content

    def test_no_review_section_when_all_confident(self, tmp_path):
        results = [_result(), _result()]
        analyses = [_analysis("HIGH"), _analysis("MEDIUM")]
        _, md_path = build_and_write(results, analyses, str(tmp_path))
        content = open(md_path, encoding="utf-8").read()
        assert "Needs Human Review" not in content


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_mismatched_lengths_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            build_and_write([_result(), _result()], [_analysis()], str(tmp_path))

    def test_output_dir_created_if_absent(self, tmp_path):
        new_dir = str(tmp_path / "nested" / "reports")
        build_and_write([_result()], [_analysis()], new_dir)
        assert os.path.isdir(new_dir)

    def test_both_files_written(self, tmp_path):
        json_path, md_path = build_and_write([_result()], [_analysis()], str(tmp_path))
        assert os.path.exists(json_path)
        assert os.path.exists(md_path)
        assert json_path.endswith(".json")
        assert md_path.endswith(".md")
