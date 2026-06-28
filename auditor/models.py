"""
auditor/models.py

Shared data models used across all auditor modules.
Import from here to keep a single source of truth for CheckResult and LLMAnalysis.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict


@dataclass
class CheckResult:
    """
    The output of a single data-quality check run against one table (and
    optionally one column).

    Attributes
    ----------
    check_name:     Unique identifier for the check, e.g. "null_rate" or
                    "referential_integrity:orders->customers".
    table:          Name of the primary table being checked.
    column:         Column name when the check is column-scoped, else None.
    severity:       "HIGH", "MEDIUM", or "LOW" — set by the check itself
                    based on the magnitude of the finding.
    rows_affected:  Absolute count of rows that triggered this finding.
    total_rows:     Total rows in the table at check time.
    pct_affected:   rows_affected / total_rows expressed as a percentage
                    (0.0 – 100.0).
    example_values: A short list (≤10 items) of representative bad values or
                    row identifiers to aid investigation.
    raw_details:    Arbitrary dict carrying check-specific metrics, counts,
                    histograms, or breakdowns for the LLM analysis step.
    """

    check_name: str
    table: str
    column: Optional[str]
    severity: str  # HIGH / MEDIUM / LOW
    rows_affected: int
    total_rows: int
    pct_affected: float
    example_values: List[Any]
    raw_details: Dict

    def __post_init__(self) -> None:
        valid_severities = {"HIGH", "MEDIUM", "LOW"}
        if self.severity not in valid_severities:
            raise ValueError(
                f"CheckResult.severity must be one of {valid_severities}, "
                f"got {self.severity!r}"
            )
        if not (0.0 <= self.pct_affected <= 100.0):
            raise ValueError(
                f"CheckResult.pct_affected must be between 0 and 100, "
                f"got {self.pct_affected}"
            )

    @property
    def label(self) -> str:
        """Human-readable label combining table, column, and check name."""
        if self.column:
            return f"{self.table}.{self.column} [{self.check_name}]"
        return f"{self.table} [{self.check_name}]"


@dataclass
class LLMAnalysis:
    """
    The structured output produced by the LLM analyzer for a single
    CheckResult.

    Attributes
    ----------
    anomaly_type:               Short category label, e.g. "Missing Data",
                                "Statistical Outlier", "Referential Integrity".
    plain_english_explanation:  One or two sentences a non-technical
                                stakeholder can understand.
    likely_root_cause:          The most probable upstream cause of this
                                anomaly (pipeline bug, schema change, ETL
                                truncation, etc.).
    downstream_impact:          What breaks or degrades if this is not fixed
                                (dashboards, ML features, billing, etc.).
    recommendation:             Concrete, actionable next step to remediate
                                or monitor this issue.
    confidence:                 "HIGH", "MEDIUM", or "LOW" — how confident
                                the LLM is in its analysis given the evidence.
    finding_id:                 Optional stable identifier linking this
                                analysis back to its CheckResult, set by the
                                report builder.
    """

    anomaly_type: str
    plain_english_explanation: str
    likely_root_cause: str
    downstream_impact: str
    recommendation: str
    confidence: str  # HIGH / MEDIUM / LOW
    finding_id: str = ""

    def __post_init__(self) -> None:
        valid_confidences = {"HIGH", "MEDIUM", "LOW"}
        if self.confidence not in valid_confidences:
            raise ValueError(
                f"LLMAnalysis.confidence must be one of {valid_confidences}, "
                f"got {self.confidence!r}"
            )
