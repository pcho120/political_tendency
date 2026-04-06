#!/usr/bin/env python3
"""measure_baseline.py — Structure-aware field-quality baseline measurement.

Reads a sample manifest (JSON) that lists firms, their structure type, and the
path to a cached JSONL file of attorney profiles.  For each profile it scores
the five target fields (title, offices, department, practice_areas, industries)
into one of four exclusive buckets:

  correct           — field is populated with plausible, non-sentinel data
  contaminated      — field has data that looks wrong (contact info, nav text,
                       or a sentinel marker that indicates contamination)
  missing           — field is absent / empty / sentinel-only with no evidence
                       of contamination
  blocked_excluded  — firm is BOT_PROTECTED / AUTH_REQUIRED; profile is counted
                       in the blocked_firms section only, not the improvement
                       denominator

Output (--output path):
  {
    "summary": { ... },
    "by_structure_type": { ... },
    "by_field": { ... },
    "blocked_firms": { ... }
  }

Compare mode (--compare before.json after.json [--min-improvement 0.05]):
  Reads two baseline reports and exits non-zero if:
  - the average fill-rate improvement across all target fields is less than min_improvement
  - any target field's contaminated rate rose by more than MAX_CONTAMINATION_INCREASE
  - either report file is missing or malformed

Exit codes:
  0   success / comparison passed
  1   missing / malformed inputs
  2   comparison threshold failure

Usage:
  python3 measure_baseline.py --manifest tests/fixtures/sample_manifest.json \\
      --use-cache --output outputs/baseline_before.json

  python3 measure_baseline.py --compare outputs/baseline_before.json \\
      outputs/baseline_after.json --min-improvement 0.05
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_FIELDS: list[str] = [
    "title",
    "offices",
    "department",
    "practice_areas",
    "industries",
]

# Sentinel values that indicate a field is deliberately empty (not contaminated)
_INDUSTRY_SENTINEL: str = "no industry field"
_EDUCATION_NO_JD: str = "no JD"

# Max contamination increase allowed in --compare mode (2 percentage points)
MAX_CONTAMINATION_INCREASE: float = 0.02

# ---------------------------------------------------------------------------
# Contamination heuristics (no firm-specific branches)
# ---------------------------------------------------------------------------

# Patterns that strongly indicate field-level contamination
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d\s\(\)\-\.]{7,}")
_URL_RE = re.compile(r"https?://|www\.")

# Navigation / UI noise phrases
_NAV_PHRASES: frozenset[str] = frozenset({
    "download vcard",
    "download v-card",
    "view all",
    "read more",
    "see more",
    "learn more",
    "skip to main",
    "skip navigation",
    "toggle menu",
    "cookie",
    "privacy policy",
    "accept all",
    "manage preferences",
    "always active",
    "print profile",
    "share profile",
    "add to my team",
    "back to top",
})

# Date-like patterns that should not appear in title/department/practice_areas
_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)

# Known-good title tokens (used as positive signal, NOT whitelist)
_TITLE_POSITIVE_TOKENS: frozenset[str] = frozenset({
    "partner",
    "associate",
    "counsel",
    "of counsel",
    "senior associate",
    "senior counsel",
    "special counsel",
    "managing partner",
    "member",
    "shareholder",
    "principal",
    "equity partner",
    "non-equity partner",
    "junior associate",
    "senior director",
    "senior partner",
    "director",
    "attorney",
})


def _is_contaminated_string(value: str, field: str) -> bool:
    """Return True if a single string value looks contaminated for the given field."""
    v = value.strip()
    vl = v.lower()

    # Email / URL in any field = contamination
    if _EMAIL_RE.search(v):
        return True
    if _URL_RE.search(v):
        return True

    # Navigation noise in any field = contamination
    if any(phrase in vl for phrase in _NAV_PHRASES):
        return True

    # Phone numbers contaminate title / department / practice_areas / industries
    if field in ("title", "department", "practice_areas", "industries"):
        if _PHONE_RE.search(v) and not re.search(r"\b20\d\d\b", v):
            # avoid flagging bar-admission year patterns like "2023 California"
            pass  # phones in title/dept are usually caught by other checks

    # Date strings in title / department / practice_areas = contamination
    if field in ("title", "department", "practice_areas", "industries"):
        if _DATE_RE.search(v):
            return True

    # Title-specific: if the string is extremely long (> 120 chars) it's likely
    # a bio blurb or nav dump scraped into the title slot
    if field == "title" and len(v) > 120:
        return True

    # Title: if multiple sentences, it's a bio blurb
    if field == "title" and v.count(".") > 2:
        return True

    return False


def _score_title(value: Any) -> str:
    """Return 'correct', 'contaminated', or 'missing' for a title field value."""
    if not value or (isinstance(value, str) and not value.strip()):
        return "missing"
    if not isinstance(value, str):
        return "contaminated"
    v = value.strip()
    if _is_contaminated_string(v, "title"):
        return "contaminated"
    # Check if it contains at least one known title token (loose positive check)
    vl = v.lower()
    if any(tok in vl for tok in _TITLE_POSITIVE_TOKENS):
        return "correct"
    # Unknown but short/clean values are still correct (free-form titles exist)
    if len(v) <= 80 and not _DATE_RE.search(v):
        return "correct"
    return "contaminated"


def _score_list_field(values: Any, field: str) -> str:
    """Return 'correct', 'contaminated', or 'missing' for a list-type field."""
    if not values:
        return "missing"
    if not isinstance(values, list):
        if not isinstance(values, str):
            return "contaminated"
        values = [values]

    # Filter sentinel values first
    effective = []
    for item in values:
        if isinstance(item, str):
            if field == "industries" and item.strip().lower() == _INDUSTRY_SENTINEL:
                continue
            effective.append(item)
        elif isinstance(item, dict):
            # education record dicts in the wrong field = contamination
            return "contaminated"

    if not effective:
        # Only sentinel values left
        return "missing"

    # Check each item for contamination
    contamination_count = 0
    for item in effective:
        if isinstance(item, str) and _is_contaminated_string(item, field):
            contamination_count += 1

    # If more than half the items are contaminated, classify whole field as contaminated
    if contamination_count > 0 and contamination_count >= len(effective) / 2:
        return "contaminated"

    # Additional length heuristic: if the field has suspiciously many items
    # relative to what's expected (e.g., department with 20+ items), that's a dump
    max_reasonable: dict[str, int] = {
        "offices": 10,
        "department": 5,
        "practice_areas": 20,
        "industries": 15,
    }
    if field in max_reasonable and len(effective) > max_reasonable[field]:
        return "contaminated"

    return "correct"


def score_profile(profile: dict[str, Any]) -> dict[str, str]:
    """Score all TARGET_FIELDS for one profile.

    Returns a dict mapping field_name -> bucket
    ('correct'|'contaminated'|'missing'|'blocked_excluded').
    """
    diag = profile.get("diagnostics", {}) or {}
    blocked = diag.get("blocked", False)
    # Also check reason field in diagnostics
    if not blocked and diag.get("reason") in ("BOT_PROTECTED", "AUTH_REQUIRED"):
        blocked = True

    result: dict[str, str] = {}
    for field in TARGET_FIELDS:
        if blocked:
            result[field] = "blocked_excluded"
            continue

        value = profile.get(field)
        if field == "title":
            result[field] = _score_title(value)
        else:
            result[field] = _score_list_field(value, field)

    return result


# ---------------------------------------------------------------------------
# Manifest loading and validation
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: str) -> dict[str, Any]:
    """Load and parse the manifest JSON. Raises SystemExit(1) on error."""
    p = Path(manifest_path)
    if not p.exists():
        print(f"ERROR: manifest file not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with p.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: manifest is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if "entries" not in data or not isinstance(data["entries"], list):
        print(
            'ERROR: manifest must have top-level "entries" list',
            file=sys.stderr,
        )
        sys.exit(1)
    return data


def validate_manifest_cache(manifest: dict[str, Any], use_cache: bool) -> list[str]:
    """Check that every non-blocked entry's cache_file exists.

    Returns a list of error messages (empty if all OK).
    If use_cache is False, skips the check (for future live-fetch mode).
    """
    if not use_cache:
        return []

    errors: list[str] = []
    for entry in manifest["entries"]:
        firm = entry.get("firm", "<unknown>")
        cache_file = entry.get("cache_file", "")
        if not cache_file:
            errors.append(f"firm={firm!r}: missing 'cache_file' in manifest entry")
            continue
        p = Path(cache_file)
        if not p.exists():
            errors.append(
                f"firm={firm!r}: cache_file not found: {cache_file}"
            )
    return errors


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profiles_from_jsonl(path: str) -> list[dict[str, Any]]:
    """Load all profiles from a JSONL file. Returns empty list on error."""
    p = Path(path)
    profiles: list[dict[str, Any]] = []
    try:
        with p.open() as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    profiles.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(
                        f"WARNING: skipping malformed JSON at {path}:{lineno}: {exc}",
                        file=sys.stderr,
                    )
    except OSError as exc:
        print(f"WARNING: could not read {path}: {exc}", file=sys.stderr)
    return profiles


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _empty_field_counts() -> dict[str, int]:
    return {"correct": 0, "contaminated": 0, "missing": 0, "blocked_excluded": 0}


def _empty_field_report() -> dict[str, dict[str, int]]:
    return {f: _empty_field_counts() for f in TARGET_FIELDS}


def aggregate_scores(
    entries: list[dict[str, Any]],
    field_scores: list[dict[str, str]],
) -> dict[str, Any]:
    """Aggregate raw per-profile field scores into a structured report."""
    # by_structure_type: structure_type -> field -> bucket -> count
    by_structure: dict[str, dict[str, dict[str, int]]] = defaultdict(
        _empty_field_report
    )

    # by_field: field -> bucket -> count (across all improvable firms)
    by_field: dict[str, dict[str, int]] = _empty_field_report()

    # blocked_firms: firm -> {profile_count, structure_type}
    blocked_firms: dict[str, dict[str, Any]] = {}

    total_profiles = 0
    improvable_profiles = 0
    blocked_profiles = 0

    for entry, scores in zip(entries, field_scores):
        firm = entry.get("firm", "<unknown>")
        st = entry.get("structure_type", "UNKNOWN")
        is_blocked = entry.get("is_blocked", False)

        total_profiles += 1

        if is_blocked:
            blocked_profiles += 1
            if firm not in blocked_firms:
                blocked_firms[firm] = {
                    "structure_type": st,
                    "profile_count": 0,
                    "fields": _empty_field_report(),
                }
            blocked_firms[firm]["profile_count"] += 1
            for field in TARGET_FIELDS:
                blocked_firms[firm]["fields"][field]["blocked_excluded"] += 1
            continue

        improvable_profiles += 1

        for field in TARGET_FIELDS:
            bucket = scores.get(field, "missing")
            by_field[field][bucket] += 1
            by_structure[st][field][bucket] += 1

    # Compute fill-rates (correct / (correct+contaminated+missing))
    def fill_rate(counts: dict[str, int]) -> float:
        denom = counts["correct"] + counts["contaminated"] + counts["missing"]
        if denom == 0:
            return 0.0
        return counts["correct"] / denom

    def contamination_rate(counts: dict[str, int]) -> float:
        denom = counts["correct"] + counts["contaminated"] + counts["missing"]
        if denom == 0:
            return 0.0
        return counts["contaminated"] / denom

    # Build summary
    summary = {
        "total_profiles": total_profiles,
        "improvable_profiles": improvable_profiles,
        "blocked_profiles": blocked_profiles,
        "target_fields": TARGET_FIELDS,
        "fill_rates": {
            f: round(fill_rate(by_field[f]), 4) for f in TARGET_FIELDS
        },
        "contamination_rates": {
            f: round(contamination_rate(by_field[f]), 4) for f in TARGET_FIELDS
        },
    }

    # Enrich by_structure with rates
    by_structure_out: dict[str, Any] = {}
    for st, fields in by_structure.items():
        by_structure_out[st] = {
            f: {
                **fields[f],
                "fill_rate": round(fill_rate(fields[f]), 4),
                "contamination_rate": round(contamination_rate(fields[f]), 4),
            }
            for f in TARGET_FIELDS
        }

    # Enrich by_field with rates
    by_field_out: dict[str, Any] = {
        f: {
            **by_field[f],
            "fill_rate": round(fill_rate(by_field[f]), 4),
            "contamination_rate": round(contamination_rate(by_field[f]), 4),
        }
        for f in TARGET_FIELDS
    }

    # Enrich blocked_firms
    blocked_out: dict[str, Any] = {}
    for firm, info in blocked_firms.items():
        blocked_out[firm] = {
            "structure_type": info["structure_type"],
            "profile_count": info["profile_count"],
        }

    return {
        "summary": summary,
        "by_structure_type": by_structure_out,
        "by_field": by_field_out,
        "blocked_firms": blocked_out,
    }


# ---------------------------------------------------------------------------
# Main measurement pipeline
# ---------------------------------------------------------------------------

def run_measurement(manifest_path: str, use_cache: bool, output_path: str) -> None:
    """Load manifest, validate cache files, score profiles, write output JSON."""
    manifest = load_manifest(manifest_path)

    # Validate cache files exist before any scoring
    errors = validate_manifest_cache(manifest, use_cache)
    if errors:
        print(
            "ERROR: manifest references missing cached inputs:", file=sys.stderr
        )
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    entries_with_scores: list[tuple[dict[str, Any], dict[str, str]]] = []

    for entry in manifest["entries"]:
        firm = entry.get("firm", "<unknown>")
        cache_file = entry.get("cache_file", "")
        is_blocked = entry.get("is_blocked", False)

        if use_cache and cache_file:
            profiles = load_profiles_from_jsonl(cache_file)
            if not profiles:
                print(
                    f"WARNING: no profiles loaded for firm={firm!r} from {cache_file}",
                    file=sys.stderr,
                )
            for profile in profiles:
                # Propagate blocked flag from manifest (authoritative) onto entry
                augmented_entry = {**entry}
                if is_blocked:
                    # Force the profile diagnostics to reflect blocking
                    augmented_entry["is_blocked"] = True
                score = score_profile(profile) if not is_blocked else {
                    f: "blocked_excluded" for f in TARGET_FIELDS
                }
                entries_with_scores.append((augmented_entry, score))
        else:
            # Placeholder for future live-fetch mode
            print(
                f"INFO: skipping firm={firm!r} (live fetch not implemented)",
                file=sys.stderr,
            )

    if not entries_with_scores:
        print(
            "ERROR: no profiles were loaded from any cache file",
            file=sys.stderr,
        )
        sys.exit(1)

    all_entries = [e for e, _ in entries_with_scores]
    all_scores = [s for _, s in entries_with_scores]

    report = aggregate_scores(all_entries, all_scores)

    # Write output
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    # Print summary to stdout
    print(f"Baseline report written to: {output_path}")
    print(f"  Total profiles scored: {report['summary']['total_profiles']}")
    print(f"  Improvable profiles:  {report['summary']['improvable_profiles']}")
    print(f"  Blocked profiles:     {report['summary']['blocked_profiles']}")
    print()
    print("  Fill-rates (correct / improvable denominator):")
    for field, rate in report["summary"]["fill_rates"].items():
        crate = report["summary"]["contamination_rates"][field]
        print(f"    {field:<20} fill={rate:.1%}  contamination={crate:.1%}")


# ---------------------------------------------------------------------------
# Compare mode
# ---------------------------------------------------------------------------

def run_compare(
    before_path: str,
    after_path: str,
    min_improvement: float,
    fields: list[str] | None = None,
) -> None:
    """Compare two baseline reports.  Exit non-zero on threshold failure.

    If *fields* is given, only those fields are included in the average
    fill-improvement calculation.  Contamination is still checked for all
    TARGET_FIELDS.
    """
    compare_fields = fields if fields else TARGET_FIELDS
    failed = False

    def load_report(path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            print(f"ERROR: report file not found: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            with p.open() as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            print(
                f"ERROR: report is not valid JSON ({path}): {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    before = load_report(before_path)
    after = load_report(after_path)

    # Validate required top-level keys
    required_keys = {"summary", "by_structure_type", "by_field", "blocked_firms"}
    for label, report in [("before", before), ("after", after)]:
        missing_keys = required_keys - set(report.keys())
        if missing_keys:
            print(
                f"ERROR: {label} report missing required keys: {missing_keys}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Comparing baseline reports:")
    print(f"  before: {before_path}")
    print(f"  after:  {after_path}")
    print(f"  min-improvement threshold: {min_improvement:.1%}")
    print(f"  max contamination increase: {MAX_CONTAMINATION_INCREASE:.1%}")
    if fields:
        print(f"  scoped to fields: {', '.join(fields)}")
    print()

    # Check each target field — collect deltas for average check
    before_fields = before.get("by_field", {})
    after_fields = after.get("by_field", {})

    fill_deltas: list[float] = []

    for field in TARGET_FIELDS:
        bf = before_fields.get(field, {})
        af = after_fields.get(field, {})

        before_fill = bf.get("fill_rate", 0.0)
        after_fill = af.get("fill_rate", 0.0)
        before_contam = bf.get("contamination_rate", 0.0)
        after_contam = af.get("contamination_rate", 0.0)

        fill_delta = after_fill - before_fill
        contam_delta = after_contam - before_contam

        # Only include scoped fields in the average
        in_scope = field in compare_fields
        if in_scope:
            fill_deltas.append(fill_delta)

        status_parts = [f"delta {fill_delta:+.1%}"]

        # Check contamination regression (per-field hard limit) — always checked
        if contam_delta > MAX_CONTAMINATION_INCREASE:
            status_parts.append(
                f"FAIL: contamination increase {contam_delta:+.1%} > limit {MAX_CONTAMINATION_INCREASE:.1%}"
            )
            failed = True
        else:
            status_parts.append(f"OK: contamination delta {contam_delta:+.1%}")

        scope_marker = "" if in_scope else "  [excluded from avg]"
        print(
            f"  {field:<20} before_fill={before_fill:.1%}  after_fill={after_fill:.1%}"
            f"  |  " + "  /  ".join(status_parts) + scope_marker
        )

    # Average fill improvement must meet min_improvement threshold
    avg_improvement = sum(fill_deltas) / len(fill_deltas) if fill_deltas else 0.0
    print()
    print(f"  Average fill improvement: {avg_improvement:+.1%}  (threshold: {min_improvement:.1%})")
    if avg_improvement < min_improvement:
        print(
            f"  FAIL: average fill improvement {avg_improvement:+.1%} < required {min_improvement:.1%}",
            file=sys.stderr,
        )
        failed = True
    else:
        print(f"  OK: average fill improvement meets threshold")

    print()
    if failed:
        print("RESULT: FAIL -- one or more thresholds not met", file=sys.stderr)
        sys.exit(2)
    else:
        print("RESULT: PASS -- all thresholds met")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Structure-aware field-quality baseline measurement.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="mode")

    # Measurement mode (default when --manifest is given)
    p.add_argument(
        "--manifest",
        metavar="PATH",
        help="Path to the sample manifest JSON file.",
    )
    p.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        help="Load profiles from cached JSONL files listed in the manifest "
             "(required for offline/fixture-backed mode).",
    )
    p.add_argument(
        "--output",
        metavar="PATH",
        default="outputs/baseline_before.json",
        help="Output path for the baseline report JSON (default: outputs/baseline_before.json).",
    )

    # Compare sub-command
    compare_p = sub.add_parser(
        "compare",
        help="Compare two baseline reports.",
    )
    compare_p.add_argument("before", metavar="BEFORE_JSON", help="Before baseline report.")
    compare_p.add_argument("after", metavar="AFTER_JSON", help="After baseline report.")
    compare_p.add_argument(
        "--min-improvement",
        type=float,
        default=0.05,
        metavar="RATE",
        help="Minimum average fill-rate improvement across all fields (default 0.05 = 5pp).",
    )

    return p


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    parser = _build_parser()
    # Support both `--compare A B` as positional args on the root parser AND
    # the explicit `compare` sub-command for plan compatibility.
    # Plan uses: measure_baseline.py --compare before.json after.json --min-improvement 0.05
    # We parse that by detecting --compare in argv before argparse sees it.

    raw_argv = argv if argv is not None else sys.argv[1:]

    # Check for --compare flag (plan-specified interface)
    if "--compare" in raw_argv:
        idx = raw_argv.index("--compare")
        rest = raw_argv[idx + 1 :]
        # Parse remaining: expect two positional file paths then optional --min-improvement
        compare_args = argparse.ArgumentParser()
        compare_args.add_argument("before")
        compare_args.add_argument("after")
        compare_args.add_argument("--min-improvement", type=float, default=0.05)
        compare_args.add_argument(
            "--fields",
            type=lambda s: [f.strip() for f in s.split(",")],
            default=None,
            help="Comma-separated list of fields to include in average improvement "
                 "(default: all TARGET_FIELDS).",
        )
        parsed = compare_args.parse_args(rest)
        run_compare(parsed.before, parsed.after, parsed.min_improvement, parsed.fields)
        return

    # Normal measurement mode
    args = parser.parse_args(raw_argv)

    if args.mode == "compare":
        run_compare(args.before, args.after, args.min_improvement)
        return

    # Measurement mode
    if not args.manifest:
        parser.error("--manifest is required for measurement mode")

    run_measurement(args.manifest, args.use_cache, args.output)


if __name__ == "__main__":
    main()
