#!/usr/bin/env python3
"""coverage_engine.py - Per-Firm Coverage Metrics & Legal Completeness Tracking

Computes and stores extraction coverage metrics for each law firm.

Key concepts
------------
- expected_total: how many attorneys we expect (parsed from site count,
  sitemap count, or external directory count, in that priority order)
- coverage_ratio: extracted_attorney_count / expected_total
- missing_fields_ratio: fraction of extracted profiles with ≥1 missing field
- blocked_ratio: blocked_count / max(discovered_attorney_count, 1)
- needs_additional_sources: True when coverage < 1.0 OR missing_fields_ratio > 0
- legally_incomplete: True when a source is legally inaccessible
  (reason: BOT_BLOCKED | ROBOTS_DISALLOW | AUTH_REQUIRED | NO_PUBLIC_DIRECTORY)

Output
------
coverage_metrics.json  — written by save_run_metrics()
  [
    {firm: ..., expected_total: ..., coverage_ratio: ..., ...},
    ...
  ]
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from attorney_extractor import AttorneyProfile

# ---------------------------------------------------------------------------
# Legal incompleteness reasons
# ---------------------------------------------------------------------------
LEGAL_REASON_BOT_BLOCKED = "BOT_BLOCKED"
LEGAL_REASON_ROBOTS_DISALLOW = "ROBOTS_DISALLOW"
LEGAL_REASON_AUTH_REQUIRED = "AUTH_REQUIRED"
LEGAL_REASON_NO_PUBLIC_DIRECTORY = "NO_PUBLIC_DIRECTORY"

LEGALLY_INCOMPLETE_REASONS = [
    LEGAL_REASON_BOT_BLOCKED,
    LEGAL_REASON_ROBOTS_DISALLOW,
    LEGAL_REASON_AUTH_REQUIRED,
    LEGAL_REASON_NO_PUBLIC_DIRECTORY,
]

# Patterns that indicate a "total count" embedded in page text.
# e.g. "Showing 1–20 of 423 attorneys", "Total: 850 lawyers", etc.
_COUNT_PATTERNS = [
    re.compile(r"(?:of|total[:\s]+)\s*(\d[\d,]+)\s*(?:attorney|lawyer|professional|people|result)", re.I),
    re.compile(r"(\d[\d,]+)\s*(?:attorney|lawyer|professional|people|result)", re.I),
    re.compile(r"showing\s+\d+[-–]\d+\s+of\s+(\d[\d,]+)", re.I),
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass
class CoverageMetrics:
    """Serialisable coverage metrics for one firm."""

    firm: str

    # How many attorneys we expected to find (None = unknown)
    expected_total: int | None = None

    # Source that determined expected_total
    # "official_directory" | "sitemap" | "external_directory" | "unknown"
    expected_total_source: str = "unknown"

    # How many profile URLs were discovered
    discovered_attorney_count: int = 0

    # How many profiles were successfully extracted (any field populated)
    extracted_attorney_count: int = 0

    # Fraction of extracted profiles with ≥1 missing field (0.0–1.0)
    missing_fields_ratio: float = 0.0

    # Fraction of discovered URLs that were blocked/failed (0.0–1.0)
    blocked_ratio: float = 0.0

    # extracted / expected (None if expected_total is unknown)
    coverage_ratio: float | None = None

    # True → we should attempt more sources
    needs_additional_sources: bool = False

    # True → legal barrier prevents full extraction
    legally_incomplete: bool = False

    # Reason for legal incompleteness (from LEGALLY_INCOMPLETE_REASONS)
    legally_incomplete_reason: str | None = None

    # Free-form notes from the extraction run
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class CoverageEngine:
    """Compute and aggregate per-firm coverage metrics."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self,
        firm: str,
        profiles: list[AttorneyProfile],
        *,
        discovered_urls: int = 0,
        blocked_count: int = 0,
        expected_total: int | None = None,
        expected_total_source: str = "unknown",
        official_directory_text: str | None = None,
        sitemap_url_count: int | None = None,
        external_directory_count: int | None = None,
        legally_incomplete: bool = False,
        legally_incomplete_reason: str | None = None,
        notes: list[str] | None = None,
    ) -> CoverageMetrics:
        """Compute CoverageMetrics for one firm.

        Parameters
        ----------
        firm:
            Firm name.
        profiles:
            All AttorneyProfile objects extracted (may be empty).
        discovered_urls:
            Total profile URLs discovered (before extraction).
        blocked_count:
            How many profile fetches were blocked / failed hard.
        expected_total:
            Caller-supplied expected count (highest priority if provided).
        expected_total_source:
            Label for caller-supplied expected count.
        official_directory_text:
            Raw text from the firm's attorney directory page.  The engine
            will attempt to parse a total count from it.
        sitemap_url_count:
            Number of attorney profile URLs found in sitemaps.
        external_directory_count:
            Number of profiles found in external directories (Martindale etc.).
        legally_incomplete / legally_incomplete_reason:
            Set when a legal barrier was hit.
        notes:
            Free-form notes to attach.
        """
        metrics = CoverageMetrics(firm=firm, notes=list(notes or []))
        metrics.legally_incomplete = legally_incomplete
        if legally_incomplete_reason in LEGALLY_INCOMPLETE_REASONS:
            metrics.legally_incomplete_reason = legally_incomplete_reason
        elif legally_incomplete_reason:
            metrics.notes.append(f"unknown legal reason: {legally_incomplete_reason}")

        # --- Expected total resolution (priority order) ---
        resolved_total, resolved_source = self._resolve_expected_total(
            caller_total=expected_total,
            caller_source=expected_total_source,
            directory_text=official_directory_text,
            sitemap_count=sitemap_url_count,
            external_count=external_directory_count,
        )
        metrics.expected_total = resolved_total
        metrics.expected_total_source = resolved_source

        # --- Counts ---
        metrics.discovered_attorney_count = max(discovered_urls, len(profiles))
        metrics.extracted_attorney_count = len(profiles)
        metrics.blocked_count = blocked_count  # type: ignore[attr-defined]  # extra field OK at runtime

        # --- Ratios ---
        if profiles:
            with_missing = sum(1 for p in profiles if p.missing_fields)
            metrics.missing_fields_ratio = round(with_missing / len(profiles), 4)
        else:
            metrics.missing_fields_ratio = 0.0 if not legally_incomplete else 1.0

        denom = max(metrics.discovered_attorney_count, 1)
        metrics.blocked_ratio = round(blocked_count / denom, 4)

        if resolved_total and resolved_total > 0:
            metrics.coverage_ratio = round(metrics.extracted_attorney_count / resolved_total, 4)
        else:
            metrics.coverage_ratio = None

        # --- needs_additional_sources ---
        coverage_full = (
            metrics.coverage_ratio is not None and metrics.coverage_ratio >= 1.0
        )
        fields_complete = metrics.missing_fields_ratio == 0.0
        metrics.needs_additional_sources = (not coverage_full) or (not fields_complete)

        return metrics

    def save_run_metrics(
        self,
        firms_metrics: list[CoverageMetrics],
        output_path: str | Path = "coverage_metrics.json",
    ) -> None:
        """Write aggregated coverage metrics to a JSON file.

        The file is a JSON array of CoverageMetrics dicts.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        records: list[dict] = []
        for m in firms_metrics:
            d = m.to_dict()
            # Remove runtime-only attributes not in the dataclass
            d.pop("blocked_count", None)
            records.append(d)

        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_expected_total(
        self,
        caller_total: int | None,
        caller_source: str,
        directory_text: str | None,
        sitemap_count: int | None,
        external_count: int | None,
    ) -> tuple[int | None, str]:
        """Return (expected_total, source_label) using priority order:

        1. Caller-supplied (highest — already computed by extraction pipeline)
        2. Official directory page text (parsed count)
        3. Sitemap URL count
        4. External directory count
        """
        if caller_total is not None and caller_total > 0:
            return caller_total, caller_source or "official_directory"

        if directory_text:
            parsed = self._parse_total_from_text(directory_text)
            if parsed:
                return parsed, "official_directory"

        if sitemap_count is not None and sitemap_count > 0:
            return sitemap_count, "sitemap"

        if external_count is not None and external_count > 0:
            return external_count, "external_directory"

        return None, "unknown"

    @staticmethod
    def _parse_total_from_text(text: str) -> int | None:
        """Try to extract a total attorney count from directory page text."""
        for pattern in _COUNT_PATTERNS:
            m = pattern.search(text)
            if m:
                raw = m.group(1).replace(",", "")
                try:
                    val = int(raw)
                    if val > 0:
                        return val
                except ValueError:
                    continue
        return None


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

def _demo():
    from attorney_extractor import AttorneyProfile, EducationRecord

    profiles = [
        AttorneyProfile(
            firm="Test Firm", profile_url="https://testfirm.com/people/alice",
            full_name="Alice Smith", title="Partner",
            offices=["New York"], practice_areas=["Litigation"],
            bar_admissions=["New York"],
            education=[EducationRecord(degree="JD", school="Harvard", year=2005)],
        ),
        AttorneyProfile(
            firm="Test Firm", profile_url="https://testfirm.com/people/bob",
            full_name="Bob Jones", title="Associate",
            offices=["Chicago"],
            # missing practice_areas, bar_admissions, education, department, industries
        ),
    ]
    for p in profiles:
        p.calculate_status()

    engine = CoverageEngine()
    metrics = engine.compute(
        firm="Test Firm",
        profiles=profiles,
        discovered_urls=50,
        blocked_count=5,
        official_directory_text="Showing 1-20 of 420 attorneys at Test Firm",
        sitemap_url_count=380,
    )

    print(json.dumps(metrics.to_dict(), indent=2))
    print(f"\ncoverage_ratio: {metrics.coverage_ratio}")
    print(f"needs_additional_sources: {metrics.needs_additional_sources}")
    print(f"missing_fields_ratio: {metrics.missing_fields_ratio}")

    # Test save
    engine.save_run_metrics([metrics], output_path="coverage_metrics_demo.json")
    print("\nWrote coverage_metrics_demo.json")


if __name__ == "__main__":
    _demo()
