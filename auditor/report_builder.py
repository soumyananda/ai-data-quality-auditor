"""
auditor/report_builder.py

Assembles the final audit report in two formats:

* JSON — machine-readable, suitable for downstream systems or CI gates.
* Markdown — human-readable, ready to paste into a README, Notion doc, or PR.

Public API
----------
build_and_write(check_results, analyses, output_dir) -> (json_path, md_path)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Tuple

from tabulate import tabulate

from auditor.models import CheckResult, LLMAnalysis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATASET_NAME = "Olist Brazilian E-Commerce"
DATASET_VERSION = "2018-10"
AUDITOR_VERSION = "1.0.0"
LLM_MODEL = "claude-sonnet-4-6"

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_SEVERITY_ICON = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
_EFFORT = {"HIGH": "hours", "MEDIUM": "days", "LOW": "weeks"}


# ---------------------------------------------------------------------------
# Quality score helpers
# ---------------------------------------------------------------------------

def _compute_score(check_results: List[CheckResult]) -> int:
    weights = {"HIGH": 15, "MEDIUM": 7, "LOW": 3}
    deductions = sum(weights.get(r.severity, 0) for r in check_results)
    return max(0, 100 - deductions)


def _verdict(score: int) -> str:
    if score >= 80:
        return "PASS"
    if score >= 50:
        return "WARN"
    return "FAIL"


# ---------------------------------------------------------------------------
# Sorting helper
# ---------------------------------------------------------------------------

def _sorted_indices(check_results: List[CheckResult]) -> List[int]:
    """Return original indices sorted HIGH → MEDIUM → LOW."""
    return sorted(
        range(len(check_results)),
        key=lambda idx: _SEVERITY_ORDER.get(check_results[idx].severity, 99),
    )


# ---------------------------------------------------------------------------
# JSON report builder
# ---------------------------------------------------------------------------

def _build_json_report(
    check_results: List[CheckResult],
    analyses: List[LLMAnalysis],
) -> dict:
    score = _compute_score(check_results)
    verdict = _verdict(score)
    sorted_idx = _sorted_indices(check_results)

    # Severity counts
    severity_counts: Dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in check_results:
        severity_counts[r.severity] = severity_counts.get(r.severity, 0) + 1

    # Most critical finding
    high_results = [r for r in check_results if r.severity == "HIGH"]
    most_critical = (high_results[0].check_name if high_results else check_results[0].check_name) if check_results else ""

    # Build findings in sorted order
    findings = []
    for rank, orig_idx in enumerate(sorted_idx):
        r = check_results[orig_idx]
        a = analyses[orig_idx]
        finding_id = f"F{rank + 1:03d}"
        findings.append(
            {
                "finding_id": finding_id,
                "check_name": r.check_name,
                "anomaly_type": a.anomaly_type,
                "table": r.table,
                "column": r.column,
                "severity": r.severity,
                "rows_affected": r.rows_affected,
                "total_rows": r.total_rows,
                "pct_affected": round(r.pct_affected, 2),
                "example_values": r.example_values,
                "plain_english_explanation": a.plain_english_explanation,
                "likely_root_cause": a.likely_root_cause,
                "downstream_impact": a.downstream_impact,
                "recommendation": a.recommendation,
                "llm_confidence": a.confidence,
                "needs_review": a.confidence == "LOW",
            }
        )

    # Recommendations bucketed by severity
    immediate: list = []
    short_term: list = []
    monitoring: list = []
    for f in findings:
        entry = {
            "finding_id": f["finding_id"],
            "action": f["recommendation"],
            "estimated_effort": _EFFORT.get(f["severity"], "unknown"),
        }
        if f["severity"] == "HIGH":
            entry["priority"] = len(immediate) + 1
            immediate.append(entry)
        elif f["severity"] == "MEDIUM":
            entry["priority"] = len(short_term) + 1
            short_term.append(entry)
        else:
            entry["priority"] = len(monitoring) + 1
            monitoring.append(entry)

    return {
        "report_metadata": {
            "report_id": str(uuid.uuid4()),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "dataset": DATASET_NAME,
            "dataset_version": DATASET_VERSION,
            "auditor_version": AUDITOR_VERSION,
            "llm_model": LLM_MODEL,
            "total_tables_scanned": len(set(r.table for r in check_results)),
            "total_rows_scanned": sum(r.total_rows for r in check_results),
        },
        "summary": {
            "overall_quality_score": score,
            "verdict": verdict,
            "total_findings": len(check_results),
            "findings_by_severity": severity_counts,
            "tables_with_issues": sorted(list(set(r.table for r in check_results))),
            "most_critical_finding": most_critical,
            "needs_human_review": sum(1 for a in analyses if a.confidence == "LOW"),
        },
        "findings": findings,
        "recommendations": {
            "immediate": immediate,
            "short_term": short_term,
            "monitoring": monitoring,
        },
    }


# ---------------------------------------------------------------------------
# Markdown report builder
# ---------------------------------------------------------------------------

def _verdict_badge(verdict: str) -> str:
    badges = {"PASS": "✅ PASS", "WARN": "⚠️ WARN", "FAIL": "❌ FAIL"}
    return badges.get(verdict, verdict)


def _build_markdown(report: dict) -> str:
    meta = report["report_metadata"]
    summary = report["summary"]
    findings = report["findings"]
    recs = report["recommendations"]

    lines: List[str] = []

    # ------------------------------------------------------------------ title
    lines += [
        "# Data Quality Audit Report",
        "",
        f"**Dataset:** {meta['dataset']}  "
        f"**Generated:** {meta['generated_at']}  "
        f"**Model:** {meta['llm_model']}  "
        f"**Report ID:** `{meta['report_id']}`",
        "",
        "---",
        "",
    ]

    # --------------------------------------------------------------- summary
    lines += ["## Summary", ""]

    verdict_display = _verdict_badge(summary["verdict"])
    sev = summary["findings_by_severity"]
    summary_rows = [
        ["Overall Quality Score", f"{summary['overall_quality_score']} / 100"],
        ["Verdict", verdict_display],
        ["Total Findings", summary["total_findings"]],
        ["HIGH Severity", sev.get("HIGH", 0)],
        ["MEDIUM Severity", sev.get("MEDIUM", 0)],
        ["LOW Severity", sev.get("LOW", 0)],
        ["Tables Scanned", meta["total_tables_scanned"]],
        ["Total Rows Scanned", f"{meta['total_rows_scanned']:,}"],
        ["Tables with Issues", ", ".join(summary["tables_with_issues"]) or "None"],
        ["Most Critical Finding", summary["most_critical_finding"]],
    ]
    lines.append(tabulate(summary_rows, headers=["Metric", "Value"], tablefmt="pipe"))
    lines += ["", "---", ""]

    # -------------------------------------------------------------- findings
    lines += ["## Findings", ""]

    for f in findings:
        icon = _SEVERITY_ICON.get(f["severity"], "")
        col_display = f"`{f['column']}`" if f["column"] else "_N/A_"
        pct = f["pct_affected"]

        lines += [
            f"### [{f['finding_id']}] {f['anomaly_type']} — {icon} {f['severity']}",
            "",
            f"**Table:** `{f['table']}` | **Column:** {col_display} | "
            f"**Rows affected:** {f['rows_affected']:,} ({pct}%)",
            "",
        ]

        if f["example_values"]:
            examples = ", ".join(str(v) for v in f["example_values"])
            lines += [f"**Example values:** `{examples}`", ""]

        if f.get("needs_review"):
            lines += [
                "> ⚠️ **Needs human review** — LLM confidence is LOW for this finding. "
                "The detection (row count, affected column) is deterministic and reliable. "
                "The root cause interpretation below should be verified manually before acting on it.",
                "",
            ]

        lines += [
            f"**What it is:** {f['plain_english_explanation']}",
            "",
            f"**Likely root cause:** {f['likely_root_cause']}",
            "",
            f"**Downstream impact:** {f['downstream_impact']}",
            "",
            f"**Recommendation:** {f['recommendation']}",
            "",
            f"**LLM confidence:** {f['llm_confidence']}",
            "",
            "---",
            "",
        ]

    # ---------------------------------------------------------- action plan
    lines += ["## Action Plan", ""]

    lines += ["### Immediate (fix before downstream use)", ""]
    if recs["immediate"]:
        for item in recs["immediate"]:
            lines.append(
                f"{item['priority']}. [{item['finding_id']}] {item['action']} "
                f"_(estimated effort: {item['estimated_effort']})_"
            )
    else:
        lines.append("_No immediate actions required._")
    lines.append("")

    lines += ["### Short-term (this sprint)", ""]
    if recs["short_term"]:
        for item in recs["short_term"]:
            lines.append(
                f"{item['priority']}. [{item['finding_id']}] {item['action']} "
                f"_(estimated effort: {item['estimated_effort']})_"
            )
    else:
        lines.append("_No short-term actions required._")
    lines.append("")

    lines += ["### Add to monitoring pipeline", ""]
    if recs["monitoring"]:
        for item in recs["monitoring"]:
            lines.append(
                f"{item['priority']}. [{item['finding_id']}] {item['action']} "
                f"_(estimated effort: {item['estimated_effort']})_"
            )
    else:
        lines.append("_No monitoring items._")
    lines.append("")

    # Needs human review bucket — LOW-confidence findings regardless of severity
    review_findings = [f for f in findings if f.get("needs_review")]
    if review_findings:
        lines += ["### ⚠️ Needs Human Review (LLM confidence: LOW)", ""]
        lines.append(
            "_The following findings have reliable detection (row counts are exact) "
            "but low-confidence LLM interpretation. Verify the root cause manually._"
        )
        lines.append("")
        for i, f in enumerate(review_findings, start=1):
            lines.append(
                f"{i}. [{f['finding_id']}] **{f['anomaly_type']}** on `{f['table']}` — "
                f"{f['rows_affected']:,} rows affected. {f['recommendation']}"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_and_write(
    check_results: List[CheckResult],
    analyses: List[LLMAnalysis],
    output_dir: str,
) -> Tuple[str, str]:
    """
    Build the JSON and Markdown audit reports and write them to *output_dir*.

    Parameters
    ----------
    check_results:
        All CheckResult objects produced by the check layer.
    analyses:
        Parallel list of LLMAnalysis objects from the LLM layer.
    output_dir:
        Directory where report files will be written (created if absent).

    Returns
    -------
    (json_path, md_path) — absolute paths to the two report files.
    """
    if len(check_results) != len(analyses):
        raise ValueError(
            f"check_results ({len(check_results)}) and analyses "
            f"({len(analyses)}) must be the same length."
        )

    os.makedirs(output_dir, exist_ok=True)

    report = _build_json_report(check_results, analyses)
    markdown = _build_markdown(report)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"report_{timestamp}.json")
    md_path = os.path.join(output_dir, f"report_{timestamp}.md")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)

    return json_path, md_path
