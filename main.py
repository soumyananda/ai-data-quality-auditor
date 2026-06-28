"""
main.py — AI Data Quality Auditor entry point

Usage:
    python main.py [OPTIONS]

Options:
    --dataset-path PATH   Directory containing injected Parquet/CSV files.
                          [default: data/injected]
    --output-dir PATH     Directory where JSON and Markdown reports are written.
                          [default: reports]
    --no-cache            Bypass the LLM response cache and always call the API.
    --verbose             Stream each finding to stdout as it is discovered.
    --help                Show this message and exit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# Load .env before importing auditor modules so ANTHROPIC_API_KEY is present
load_dotenv()

# Re-export shared models so other modules can do `from auditor.models import …`
# The canonical definitions live in auditor/models.py; this re-export is
# purely for convenience (e.g. `from main import CheckResult` in notebooks).
from auditor.models import CheckResult, LLMAnalysis  # noqa: F401  re-export

import auditor.loader as loader
import auditor.checks.single_table as single_table_checks
import auditor.checks.cross_table as cross_table_checks
import auditor.llm_analyzer as llm_analyzer
import auditor.report_builder as report_builder


# ---------------------------------------------------------------------------
# Severity ordering used for display / sorting
# ---------------------------------------------------------------------------
_SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _count_by_severity(results: list[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in results:
        counts[r.severity] += 1
    return counts


def _overall_score(results: list[CheckResult]) -> int:
    """
    Derive a 0-100 data quality score from check results.

    Scoring rationale
    -----------------
    Each HIGH finding deducts 15 points, MEDIUM deducts 5, LOW deducts 1.
    Score is clamped to [0, 100].  A dataset with zero findings scores 100.
    """
    if not results:
        return 100
    deduction = 0
    weights = {"HIGH": 15, "MEDIUM": 5, "LOW": 1}
    for r in results:
        deduction += weights.get(r.severity, 0)
    return max(0, 100 - deduction)


def _verdict(score: int) -> str:
    if score >= 90:
        return "PASS — data quality is acceptable for production use."
    if score >= 70:
        return "WARN — notable issues detected; review before promotion."
    return "FAIL — critical data quality problems require immediate attention."


def _print_header() -> None:
    border = "=" * 60
    click.echo(border)
    click.echo("  AI Data Quality Auditor — powered by Claude")
    click.echo(border)
    click.echo()


def _print_findings(results: list[CheckResult]) -> None:
    """Print each finding to stdout as it is discovered (--verbose mode)."""
    sorted_results = sorted(results, key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    for r in sorted_results:
        icon = {"HIGH": "[HIGH]", "MEDIUM": "[MED] ", "LOW": "[LOW] "}.get(
            r.severity, "[???] "
        )
        click.echo(
            f"  {icon} {r.label} — "
            f"{r.rows_affected:,}/{r.total_rows:,} rows "
            f"({r.pct_affected:.1f}%)"
        )


def _print_summary(
    score: int,
    verdict: str,
    counts: dict[str, int],
    json_path: str,
    md_path: str,
) -> None:
    click.echo()
    click.echo("─" * 60)
    click.echo(f"  Score   : {score}/100")
    click.echo(f"  Verdict : {verdict}")
    click.echo(
        f"  Findings: "
        f"{counts['HIGH']} HIGH  "
        f"{counts['MEDIUM']} MEDIUM  "
        f"{counts['LOW']} LOW"
    )
    click.echo(f"  Report  : {md_path}")
    click.echo(f"  JSON    : {json_path}")
    click.echo("─" * 60)
    click.echo()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--dataset-path",
    default="data/injected",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Directory containing injected data files (Parquet or CSV).",
)
@click.option(
    "--output-dir",
    default="reports",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Directory where JSON and Markdown reports will be written.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Bypass the LLM response cache and always call the Anthropic API.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Print each finding to stdout as it is discovered.",
)
def main(
    dataset_path: str,
    output_dir: str,
    no_cache: bool,
    verbose: bool,
) -> None:
    """Run the AI Data Quality Auditor against an injected dataset."""

    # ------------------------------------------------------------------
    # 1. Header
    # ------------------------------------------------------------------
    _print_header()

    dataset_path_obj = Path(dataset_path)
    output_dir_obj = Path(output_dir)

    if not dataset_path_obj.exists():
        click.echo(
            f"ERROR: Dataset path '{dataset_path}' does not exist. "
            "Run `make inject` first.",
            err=True,
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Load data
    # ------------------------------------------------------------------
    click.echo(f"Loading data from: {dataset_path_obj.resolve()}")
    tables = loader.load_all(dataset_path_obj)
    click.echo(f"  Loaded {len(tables)} table(s): {', '.join(sorted(tables))}")
    click.echo()

    # ------------------------------------------------------------------
    # 3. Single-table checks
    # ------------------------------------------------------------------
    click.echo("Running single-table checks...")
    single_results: list[CheckResult] = single_table_checks.run_all_checks(tables)
    click.echo(f"  {len(single_results)} finding(s) from single-table checks.")

    if verbose and single_results:
        _print_findings(single_results)

    # ------------------------------------------------------------------
    # 4. Cross-table checks
    # ------------------------------------------------------------------
    click.echo("Running cross-table checks...")
    cross_results: list[CheckResult] = cross_table_checks.run_all_checks(tables)
    click.echo(f"  {len(cross_results)} finding(s) from cross-table checks.")

    if verbose and cross_results:
        _print_findings(cross_results)

    # ------------------------------------------------------------------
    # 5. Combine all CheckResult objects
    # ------------------------------------------------------------------
    all_results: list[CheckResult] = single_results + cross_results
    click.echo()
    click.echo(f"Total findings before LLM analysis: {len(all_results)}")

    # ------------------------------------------------------------------
    # 6. LLM analysis
    # ------------------------------------------------------------------
    use_cache = not no_cache
    click.echo()
    click.echo(
        f"Running LLM analysis "
        f"({'cached' if use_cache else 'no-cache'} mode)..."
    )
    analyses: list[LLMAnalysis] = llm_analyzer.analyze_all(
        all_results, use_cache=use_cache
    )
    click.echo(f"  {len(analyses)} analysis objects returned.")

    # ------------------------------------------------------------------
    # 7. Build and write reports
    # ------------------------------------------------------------------
    click.echo()
    click.echo(f"Writing reports to: {output_dir_obj.resolve()}")
    json_path, md_path = report_builder.build_and_write(
        all_results, analyses, output_dir_obj
    )

    # ------------------------------------------------------------------
    # 8. Print summary
    # ------------------------------------------------------------------
    score = _overall_score(all_results)
    verdict = _verdict(score)
    counts = _count_by_severity(all_results)
    _print_summary(score, verdict, counts, json_path, md_path)


if __name__ == "__main__":
    main()
