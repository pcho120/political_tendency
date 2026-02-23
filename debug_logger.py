#!/usr/bin/env python3
"""debug_logger.py - Structured Debug Logging for AmLaw200 Extraction System

Provides per-firm, per-letter, and per-profile structured logging with
console output and optional JSONL file persistence.

Usage:
    logger = DebugLogger(firm="Kirkland & Ellis", output_dir=Path("debug_reports"))
    logger.log_discovery_letter(letter="A", total_results=42, extracted=42)
    logger.log_profile_result(url="https://...", status="SUCCESS", missing=[])
    logger.flush()
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Root logging setup — respects existing handlers
# ---------------------------------------------------------------------------

def _setup_root_logger() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s",
                              datefmt="%H:%M:%S")
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)

_setup_root_logger()

log = logging.getLogger("amlaw.debug")


# ---------------------------------------------------------------------------
# Structured log entries
# ---------------------------------------------------------------------------

@dataclass
class LetterDiscoveryEntry:
    """One A-Z letter crawl result."""
    firm: str
    letter: str
    total_search_results: int          # TotalSearchResults from API / HTML
    extracted_count: int               # profiles parsed from this letter
    page_count: int = 1                # how many pages / offsets iterated
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: list[str] = field(default_factory=list)


@dataclass
class FirmDiscoverySummary:
    """Aggregated discovery summary for one firm."""
    firm: str
    base_url: str
    strategy: str                      # "json_api" | "html_alphabet" | "html_directory" | "sitemap"
    discovery_mode_used: str = "requests"  # "requests" | "playwright_scroll"
    total_unique_profiles: int = 0
    letters_crawled: list[str] = field(default_factory=list)
    letter_entries: list[LetterDiscoveryEntry] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ProfileExtractionEntry:
    """Single profile extraction result."""
    firm: str
    profile_url: str
    status: str                        # SUCCESS | PARTIAL | FAILED
    missing_fields: list[str] = field(default_factory=list)
    extraction_mode: str = "requests"  # requests | playwright | api
    enrichment_render_mode_used: str = "requests"  # "requests" | "playwright"
    elapsed_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main logger class
# ---------------------------------------------------------------------------

class DebugLogger:
    """
    Structured logger for one firm's extraction run.

    Thread-safety: not thread-safe. Use one instance per firm per run.

    Parameters
    ----------
    firm : str
        Human-readable firm name (used in log lines and file names).
    output_dir : Path | None
        If provided, flushes JSONL logs here on flush() or __exit__.
    verbose : bool
        If True, prints INFO-level messages to stdout.
    """

    def __init__(
        self,
        firm: str,
        output_dir: Path | None = None,
        verbose: bool = True,
    ) -> None:
        self.firm = firm
        self.output_dir = Path(output_dir) if output_dir else None
        self.verbose = verbose
        self._log = logging.getLogger(f"amlaw.{_safe_name(firm)}")

        # In-memory accumulators
        self._discovery_summary: FirmDiscoverySummary | None = None
        self._letter_entries: list[LetterDiscoveryEntry] = []
        self._profile_entries: list[ProfileExtractionEntry] = []

    # ------------------------------------------------------------------
    # Discovery logging
    # ------------------------------------------------------------------

    def start_discovery(self, base_url: str, strategy: str) -> None:
        """Call once at the beginning of discovery for this firm."""
        self._discovery_summary = FirmDiscoverySummary(
            firm=self.firm,
            base_url=base_url,
            strategy=strategy,
        )
        self._info(f"Discovery started | strategy={strategy} | url={base_url}")

    def log_discovery_letter(
        self,
        letter: str,
        total_search_results: int,
        extracted_count: int,
        page_count: int = 1,
        notes: list[str] | None = None,
    ) -> None:
        """Log the result of crawling one letter (A-Z)."""
        entry = LetterDiscoveryEntry(
            firm=self.firm,
            letter=letter,
            total_search_results=total_search_results,
            extracted_count=extracted_count,
            page_count=page_count,
            notes=notes or [],
        )
        self._letter_entries.append(entry)

        if self._discovery_summary:
            if letter not in self._discovery_summary.letters_crawled:
                self._discovery_summary.letters_crawled.append(letter)
            self._discovery_summary.letter_entries.append(entry)

        self._info(
            f"Letter={letter} | total_results={total_search_results} "
            f"extracted={extracted_count} pages={page_count}"
        )

    def finish_discovery(
        self,
        total_unique_profiles: int,
        elapsed_seconds: float,
        errors: list[str] | None = None,
    ) -> None:
        """Call once after all letters have been crawled."""
        if self._discovery_summary:
            self._discovery_summary.total_unique_profiles = total_unique_profiles
            self._discovery_summary.elapsed_seconds = elapsed_seconds
            self._discovery_summary.errors = errors or []
        self._info(
            f"Discovery complete | unique_profiles={total_unique_profiles} "
            f"elapsed={elapsed_seconds:.1f}s errors={len(errors or [])}"
        )

    # ------------------------------------------------------------------
    # Profile extraction logging
    # ------------------------------------------------------------------

    def log_profile_result(
        self,
        url: str,
        status: str,
        missing_fields: list[str] | None = None,
        extraction_mode: str = "requests",
        enrichment_render_mode_used: str = "requests",
        elapsed_ms: int = 0,
        notes: list[str] | None = None,
    ) -> None:
        """Log the result of extracting one attorney profile."""
        entry = ProfileExtractionEntry(
            firm=self.firm,
            profile_url=url,
            status=status,
            missing_fields=missing_fields or [],
            extraction_mode=extraction_mode,
            enrichment_render_mode_used=enrichment_render_mode_used,
            elapsed_ms=elapsed_ms,
            notes=notes or [],
        )
        self._profile_entries.append(entry)
        level = logging.DEBUG if status == "SUCCESS" else (
            logging.WARNING if status == "PARTIAL" else logging.ERROR
        )
        self._log.log(
            level,
            f"Profile {status} | mode={extraction_mode} | render={enrichment_render_mode_used} | "
            f"missing={missing_fields or []} | {_short_url(url)}",
        )

    # ------------------------------------------------------------------
    # Error / warning helpers
    # ------------------------------------------------------------------

    def warn(self, message: str, **kwargs: Any) -> None:
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items())
        self._log.warning(f"{message}{' | ' + extra if extra else ''}")

    def error(self, message: str, exc: Exception | None = None, **kwargs: Any) -> None:
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items())
        msg = f"{message}{' | ' + extra if extra else ''}"
        if exc:
            self._log.error(msg, exc_info=exc)
            if self._discovery_summary:
                self._discovery_summary.errors.append(f"{message}: {exc}")
        else:
            self._log.error(msg)

    def info(self, message: str, **kwargs: Any) -> None:
        self._info(message, **kwargs)

    # ------------------------------------------------------------------
    # Flush to disk
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Write all accumulated entries to JSONL files in output_dir."""
        if not self.output_dir:
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_name(self.firm)

        # Discovery summary
        if self._discovery_summary:
            summary_path = self.output_dir / f"{safe}_discovery.json"
            summary_path.write_text(
                json.dumps(asdict(self._discovery_summary), indent=2),
                encoding="utf-8",
            )
            self._info(f"Wrote discovery summary → {summary_path}")

        # Letter entries JSONL
        if self._letter_entries:
            letters_path = self.output_dir / f"{safe}_letters.jsonl"
            with letters_path.open("w", encoding="utf-8") as fh:
                for entry in self._letter_entries:
                    fh.write(json.dumps(asdict(entry)) + "\n")

        # Profile entries JSONL
        if self._profile_entries:
            profiles_path = self.output_dir / f"{safe}_profiles.jsonl"
            with profiles_path.open("w", encoding="utf-8") as fh:
                for entry in self._profile_entries:
                    fh.write(json.dumps(asdict(entry)) + "\n")

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "DebugLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.flush()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _info(self, message: str, **kwargs: Any) -> None:
        if not self.verbose:
            return
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items())
        self._log.info(f"{message}{' | ' + extra if extra else ''}")


# ---------------------------------------------------------------------------
# Module-level convenience logger (firm-agnostic)
# ---------------------------------------------------------------------------

class RunLogger:
    """
    Top-level run logger that aggregates across all firms.

    Writes a single run_summary.jsonl after completion.
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else None
        self._firms: dict[str, dict[str, Any]] = {}
        self._log = logging.getLogger("amlaw.run")

    def record_firm(
        self,
        firm: str,
        discovered: int,
        extracted: int,
        success: int,
        partial: int,
        failed: int,
        elapsed_s: float,
    ) -> None:
        self._firms[firm] = {
            "firm": firm,
            "discovered": discovered,
            "extracted": extracted,
            "success": success,
            "partial": partial,
            "failed": failed,
            "elapsed_s": round(elapsed_s, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._log.info(
            f"[{firm}] discovered={discovered} extracted={extracted} "
            f"SUCCESS={success} PARTIAL={partial} FAILED={failed} "
            f"({elapsed_s:.1f}s)"
        )

    def flush(self) -> None:
        if not self.output_dir or not self._firms:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "run_summary.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for entry in self._firms.values():
                fh.write(json.dumps(entry) + "\n")
        self._log.info(f"Run summary written → {path}")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _safe_name(firm: str) -> str:
    """Convert firm name to filesystem-safe slug."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", firm.lower()).strip("_")[:60]


def _short_url(url: str) -> str:
    """Shorten URL for log readability."""
    if len(url) > 80:
        return url[:40] + "…" + url[-30:]
    return url
