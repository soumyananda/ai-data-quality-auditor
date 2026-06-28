"""
auditor/llm_analyzer.py

LLM-powered analysis layer.  For each CheckResult, builds a context bundle,
calls Claude via the Anthropic SDK (with disk-based caching and exponential
backoff on rate-limit / transient errors), and returns a list of LLMAnalysis
objects in the same order as the input list.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import List

import anthropic
from dotenv import load_dotenv

from auditor.models import CheckResult, LLMAnalysis

load_dotenv()

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-6"
TEMPERATURE = 0
MAX_TOKENS = 1024
CACHE_DIR = ".cache/llm_responses"

SYSTEM_PROMPT = (
    "You are a senior data platform engineer reviewing a data quality audit "
    "report for a Brazilian e-commerce platform.\n\n"
    "Your job is to:\n"
    "1. Explain in plain English what the detected anomaly means and why it is dangerous\n"
    "2. Identify the most likely root cause (pipeline bug, schema gap, ETL timing issue, etc.)\n"
    "3. Describe what downstream systems or decisions this anomaly corrupts\n"
    "4. Provide a concrete, actionable recommendation\n\n"
    "Be specific. Reference the actual column names, values, and percentages provided. "
    "Do not give generic advice.\n\n"
    "Return ONLY valid JSON matching this exact schema. No markdown fences, no preamble, "
    "no explanation outside the JSON:\n"
    "{\n"
    '  "anomaly_type": "string — short name for this class of anomaly",\n'
    '  "plain_english_explanation": "string — 2-3 sentences explaining what this is and why it matters",\n'
    '  "likely_root_cause": "string — 1-2 sentences on the most probable technical cause",\n'
    '  "downstream_impact": "string — 1-2 sentences on what breaks downstream",\n'
    '  "recommendation": "string — 2-3 concrete, actionable steps to fix this",\n'
    '  "confidence": "HIGH or MEDIUM or LOW"\n'
    "}"
)

# Retry settings
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 5


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(context_bundle: dict) -> str:
    """Return a 16-char hex SHA-256 digest of the context bundle."""
    payload = json.dumps(context_bundle, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _load_from_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _write_to_cache(key: str, data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Context bundle builder
# ---------------------------------------------------------------------------

def _build_context_bundle(result: CheckResult) -> dict:
    return {
        "dataset": "Olist Brazilian E-Commerce (100K orders, 8 relational tables)",
        "check_name": result.check_name,
        "table": result.table,
        "column": result.column,
        "severity": result.severity,
        "rows_affected": result.rows_affected,
        "total_rows": result.total_rows,
        "pct_affected": round(result.pct_affected, 2),
        "example_values": result.example_values[:5],
        "raw_details": result.raw_details,
    }


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(context_bundle: dict) -> str:
    return (
        "Analyze this data quality finding:\n\n"
        + json.dumps(context_bundle, indent=2, default=str)
        + "\n\nReturn the JSON analysis now."
    )


# ---------------------------------------------------------------------------
# Fallback LLMAnalysis for JSON parse failures
# ---------------------------------------------------------------------------

def _fallback_analysis(raw_text: str) -> dict:
    return {
        "anomaly_type": "Parse Error",
        "plain_english_explanation": raw_text,
        "likely_root_cause": "LLM returned a non-JSON response; manual review required.",
        "downstream_impact": "Unknown — analysis could not be parsed.",
        "recommendation": "Re-run the analyzer. If the problem persists, inspect the raw LLM output.",
        "confidence": "LOW",
    }


# ---------------------------------------------------------------------------
# Single-result analysis with retry + cache
# ---------------------------------------------------------------------------

def _analyze_one(result: CheckResult, use_cache: bool) -> dict:
    """
    Return a raw dict (matching the LLMAnalysis fields) for a single
    CheckResult.  Uses cache when available; otherwise calls the Claude API
    with exponential backoff.
    """
    context_bundle = _build_context_bundle(result)
    key = _cache_key(context_bundle)

    if use_cache:
        cached = _load_from_cache(key)
        if cached is not None:
            return cached

    user_prompt = _build_user_prompt(context_bundle)

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text: str = response.content[0].text.strip()
            break
        except (anthropic.RateLimitError, anthropic.APIError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
                print(
                    f"    [retry {attempt + 1}/{MAX_RETRIES}] "
                    f"{type(exc).__name__} — waiting {backoff}s ..."
                )
                time.sleep(backoff)
            else:
                raise
    else:
        # Should not be reached because the final attempt re-raises, but
        # satisfies type checker.
        raise RuntimeError("Retry loop exhausted") from last_exc

    try:
        parsed: dict = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = _fallback_analysis(raw_text)

    # Ensure confidence is a valid value to prevent downstream validation errors
    if parsed.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
        parsed["confidence"] = "LOW"

    if use_cache:
        _write_to_cache(key, parsed)

    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_all(
    check_results: List[CheckResult],
    use_cache: bool = True,
) -> List[LLMAnalysis]:
    """
    Analyze every CheckResult with the LLM and return a parallel list of
    LLMAnalysis objects.

    Parameters
    ----------
    check_results:
        Results produced by the data-quality check layer.
    use_cache:
        When True (default), skip the API call for any result whose context
        bundle hash already exists on disk.

    Returns
    -------
    List[LLMAnalysis] in the same order as *check_results*.
    """
    n = len(check_results)
    analyses: List[LLMAnalysis] = []

    for i, result in enumerate(check_results, start=1):
        print(f"  Analyzing [{i}/{n}] {result.check_name} on {result.table}...")
        data = _analyze_one(result, use_cache=use_cache)
        analyses.append(
            LLMAnalysis(
                anomaly_type=data.get("anomaly_type", "Unknown"),
                plain_english_explanation=data.get("plain_english_explanation", ""),
                likely_root_cause=data.get("likely_root_cause", ""),
                downstream_impact=data.get("downstream_impact", ""),
                recommendation=data.get("recommendation", ""),
                confidence=data.get("confidence", "LOW"),
            )
        )

    return analyses
