#!/usr/bin/env python3
"""Cross-path comparison harness for main-path vs alternate-path JSONL outputs.

Compares the five target fields (title, offices, department, practice_areas,
industries) between two JSONL files produced by the two extraction pipelines
(run_pipeline.py and find_attorney.py).

Key behaviours:
- Blocked firms (BOT_PROTECTED / AUTH_REQUIRED via manifest or diagnostics) are
  excluded from the regression denominator and reported separately.
- Per-field fill-rate gap is compared against a configurable tolerance (default
  0.10 = 10 percentage points).
- Exit 0 when every target field is within tolerance; non-zero otherwise.
- Report is machine-readable JSON with deterministic ordering.

Usage:
  python3 compare_paths.py \\
      --main-jsonl outputs/run_pipeline_sample.jsonl \\
      --alt-jsonl  outputs/find_attorney_sample.jsonl \\
      --fields     title,offices,department,practice_areas,industries \\
      --report     outputs/cross_path_diff.json \\
      --max-gap    0.10
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FIELDS: list[str] = [
    "title",
    "offices",
    "department",
    "practice_areas",
    "industries",
]
DEFAULT_MAX_GAP: float = 0.10

# Sentinel values that indicate "no data available" (not genuinely filled).
# These must match the sentinel convention established in Task 1 / measure_baseline.py.
_LIST_SENTINELS: set[str] = {"no industry field"}

# Blocked-firm reason values.
_BLOCK_REASONS: set[str] = {"BOT_PROTECTED", "AUTH_REQUIRED"}

# ---------------------------------------------------------------------------
# Field presence helpers
# ---------------------------------------------------------------------------


def _field_is_filled(value: Any, field: str) -> bool:
    """Return True when *value* contains meaningful data for *field*.

    Rules (consistent with measure_baseline.py scoring conventions):
    - scalar string: non-empty and not a URL/email/date-only artefact.
    - list: non-empty after removing sentinel strings; at least one item.
    - None / empty string / empty list → not filled.
    """
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, list):
        cleaned = [
            item for item in value
            if isinstance(item, str) and item not in _LIST_SENTINELS and item.strip()
        ]
        return bool(cleaned)

    # Non-string, non-list (dict, int, etc.) — treat as not meaningfully filled.
    return False


# ---------------------------------------------------------------------------
# Blocked-firm detection
# ---------------------------------------------------------------------------


def _profile_is_blocked(record: dict[str, Any]) -> bool:
    """Return True when this profile record belongs to a blocked firm.

    Checks `diagnostics.blocked` + `diagnostics.reason` (established in Task 1).
    """
    diag = record.get("diagnostics")
    if not isinstance(diag, dict):
        return False
    return bool(diag.get("blocked")) and diag.get("reason") in _BLOCK_REASONS


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load all records from a JSONL file; skip blank lines."""
    records: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")
    with p.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {lineno} of {path}: {exc}"
                ) from exc
    return records


# ---------------------------------------------------------------------------
# Profile key derivation
# ---------------------------------------------------------------------------


def _profile_key(record: dict[str, Any]) -> str:
    """Derive a stable profile key from (firm, full_name) or (firm, profile_url).

    The key is used to pair records across both paths.  We prefer (firm,
    profile_url) when profile_url is present and non-empty, then fall back to
    (firm, full_name).  Both components are normalised to lowercase-stripped
    strings for comparison.
    """
    firm = (record.get("firm") or "").strip().lower()
    profile_url = (record.get("profile_url") or "").strip()
    full_name = (record.get("full_name") or "").strip().lower()

    if profile_url:
        return f"{firm}||{profile_url}"
    if full_name:
        return f"{firm}||name:{full_name}"
    return f"{firm}||unknown:{id(record)}"


# ---------------------------------------------------------------------------
# Per-firm fill-rate computation
# ---------------------------------------------------------------------------


def _compute_firm_field_rates(
    records: list[dict[str, Any]],
    fields: list[str],
    *,
    blocked_firms: set[str],
) -> dict[str, dict[str, dict[str, int | float]]]:
    """Return per-firm, per-field fill counts and rates.

    Returns:
        {firm: {field: {"filled": N, "total": N, "fill_rate": float}}}

    Blocked firms are excluded from computation; their entries appear in a
    separate section of the report rather than here.
    """
    firm_field_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"filled": 0, "total": 0})
    )

    for rec in records:
        firm = (rec.get("firm") or "unknown").strip()
        if firm.lower() in blocked_firms:
            continue
        if _profile_is_blocked(rec):
            continue
        for field in fields:
            value = rec.get(field)
            filled = _field_is_filled(value, field)
            firm_field_counts[firm][field]["total"] += 1
            if filled:
                firm_field_counts[firm][field]["filled"] += 1

    # Convert to rates.
    result: dict[str, dict[str, dict[str, int | float]]] = {}
    for firm, field_data in firm_field_counts.items():
        result[firm] = {}
        for field, counts in field_data.items():
            total = counts["total"]
            filled = counts["filled"]
            result[firm][field] = {
                "filled": filled,
                "total": total,
                "fill_rate": round(filled / total, 4) if total > 0 else 0.0,
            }
    return result


# ---------------------------------------------------------------------------
# Aggregate fill-rate computation
# ---------------------------------------------------------------------------


def _compute_aggregate_fill_rate(
    firm_rates: dict[str, dict[str, dict[str, int | float]]],
    fields: list[str],
) -> dict[str, float]:
    """Aggregate per-firm per-field fill rates into a global per-field rate.

    Uses raw counts (filled / total) to avoid firm-size bias when aggregating.
    """
    totals: dict[str, int] = {f: 0 for f in fields}
    filleds: dict[str, int] = {f: 0 for f in fields}

    for firm_data in firm_rates.values():
        for field in fields:
            if field in firm_data:
                totals[field] += firm_data[field]["total"]
                filleds[field] += firm_data[field]["filled"]

    result: dict[str, float] = {}
    for field in fields:
        t = totals[field]
        result[field] = round(filleds[field] / t, 4) if t > 0 else 0.0
    return result


# ---------------------------------------------------------------------------
# Blocked-firm detection from records
# ---------------------------------------------------------------------------


def _collect_blocked_firms(
    main_records: list[dict[str, Any]],
    alt_records: list[dict[str, Any]],
) -> tuple[set[str], list[dict[str, Any]]]:
    """Collect firms marked as blocked across both JSONL inputs.

    Returns:
        (blocked_firm_names_lower_set, blocked_firm_info_list)
    """
    blocked_names: dict[str, str] = {}  # lowercase → original name

    for rec in main_records + alt_records:
        if _profile_is_blocked(rec):
            firm = (rec.get("firm") or "unknown").strip()
            reason = (
                rec.get("diagnostics", {}).get("reason") or "UNKNOWN"
            )
            blocked_names[firm.lower()] = firm
            # Store reason (prefer most specific).
            if firm.lower() not in blocked_names:
                blocked_names[firm.lower()] = firm

    blocked_info: list[dict[str, Any]] = [
        {"firm": original, "reason": _first_block_reason(original, main_records + alt_records)}
        for lower, original in blocked_names.items()
    ]

    return set(blocked_names.keys()), blocked_info


def _first_block_reason(
    firm: str,
    records: list[dict[str, Any]],
) -> str:
    """Return the first block reason found for the given firm name."""
    firm_lower = firm.lower()
    for rec in records:
        if (rec.get("firm") or "").strip().lower() == firm_lower:
            diag = rec.get("diagnostics", {})
            if diag.get("blocked"):
                return diag.get("reason", "UNKNOWN")
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-firm comparison
# ---------------------------------------------------------------------------


def _build_per_firm_section(
    main_rates: dict[str, dict[str, dict[str, int | float]]],
    alt_rates: dict[str, dict[str, dict[str, int | float]]],
    fields: list[str],
) -> list[dict[str, Any]]:
    """Build the per-firm diff section for the report.

    For each firm present in either path, emit the per-field fill rates and
    the gap (alt_fill_rate - main_fill_rate).  Missing firms in one path get
    fill_rate=null.
    """
    all_firms = sorted(set(main_rates.keys()) | set(alt_rates.keys()))
    rows: list[dict[str, Any]] = []

    for firm in all_firms:
        firm_row: dict[str, Any] = {"firm": firm, "fields": {}}
        for field in fields:
            main_field = main_rates.get(firm, {}).get(field)
            alt_field = alt_rates.get(firm, {}).get(field)
            main_rate = main_field["fill_rate"] if main_field else None
            alt_rate = alt_field["fill_rate"] if alt_field else None

            if main_rate is not None and alt_rate is not None:
                gap = round(alt_rate - main_rate, 4)
            else:
                gap = None

            firm_row["fields"][field] = {
                "main_fill_rate": main_rate,
                "alt_fill_rate": alt_rate,
                "gap": gap,
            }
        rows.append(firm_row)

    return rows


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_report(
    fields: list[str],
    main_jsonl: str,
    alt_jsonl: str,
    max_gap: float,
    main_records: list[dict[str, Any]],
    alt_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the full machine-readable report dict."""

    # Blocked-firm handling.
    blocked_firm_set, blocked_firm_info = _collect_blocked_firms(
        main_records, alt_records
    )

    # Per-firm fill rates (blocking excluded).
    main_rates = _compute_firm_field_rates(
        main_records, fields, blocked_firms=blocked_firm_set
    )
    alt_rates = _compute_firm_field_rates(
        alt_records, fields, blocked_firms=blocked_firm_set
    )

    # Aggregate per-field fill rates.
    main_agg = _compute_aggregate_fill_rate(main_rates, fields)
    alt_agg = _compute_aggregate_fill_rate(alt_rates, fields)

    # Per-field diff section.
    per_field: dict[str, dict[str, Any]] = {}
    breaches: list[str] = []
    for field in fields:
        main_rate = main_agg.get(field, 0.0)
        alt_rate = alt_agg.get(field, 0.0)
        gap = round(alt_rate - main_rate, 4)
        within = abs(gap) <= max_gap
        if not within:
            breaches.append(field)
        per_field[field] = {
            "main_fill_rate": main_rate,
            "alt_fill_rate": alt_rate,
            "gap": gap,
            "abs_gap": round(abs(gap), 4),
            "within_threshold": within,
        }

    # Per-firm section.
    per_firm = _build_per_firm_section(main_rates, alt_rates, fields)

    # Threshold section.
    threshold_section: dict[str, Any] = {
        "max_gap": max_gap,
        "fields_checked": fields,
        "fields_in_breach": breaches,
        "all_within_threshold": len(breaches) == 0,
    }

    # Blocked firms section.
    blocked_section: dict[str, Any] = {
        "excluded_count": len(blocked_firm_info),
        "firms": blocked_firm_info,
    }

    return {
        "schema_version": "1",
        "inputs": {
            "main_jsonl": str(main_jsonl),
            "alt_jsonl": str(alt_jsonl),
            "main_record_count": len(main_records),
            "alt_record_count": len(alt_records),
        },
        "per_field": per_field,
        "per_firm": per_firm,
        "threshold": threshold_section,
        "blocked_firms_excluded": blocked_section,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare main-path and alternate-path JSONL outputs for target fields. "
            "Exits 0 when all fields are within --max-gap tolerance; non-zero otherwise."
        )
    )
    parser.add_argument(
        "--main-jsonl",
        required=True,
        metavar="PATH",
        help="Main-path JSONL (run_pipeline.py output).",
    )
    parser.add_argument(
        "--alt-jsonl",
        required=True,
        metavar="PATH",
        help="Alternate-path JSONL (find_attorney.py output).",
    )
    parser.add_argument(
        "--fields",
        default=",".join(DEFAULT_FIELDS),
        metavar="F1,F2,...",
        help=(
            "Comma-separated list of fields to compare.  "
            f"Default: {','.join(DEFAULT_FIELDS)}"
        ),
    )
    parser.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help="Write machine-readable JSON report to this path.  If omitted, prints to stdout.",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=DEFAULT_MAX_GAP,
        metavar="FLOAT",
        help=(
            "Maximum allowed absolute fill-rate gap between paths per field "
            f"(default: {DEFAULT_MAX_GAP}).  Breach of any field causes non-zero exit."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    if not fields:
        print("ERROR: --fields must specify at least one field name.", file=sys.stderr)
        return 2

    # Load inputs.
    try:
        main_records = _load_jsonl(args.main_jsonl)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading --main-jsonl: {exc}", file=sys.stderr)
        return 2

    try:
        alt_records = _load_jsonl(args.alt_jsonl)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR loading --alt-jsonl: {exc}", file=sys.stderr)
        return 2

    # Build report.
    report = _build_report(
        fields=fields,
        main_jsonl=args.main_jsonl,
        alt_jsonl=args.alt_jsonl,
        max_gap=args.max_gap,
        main_records=main_records,
        alt_records=alt_records,
    )

    # Serialise.
    report_json = json.dumps(report, indent=2, ensure_ascii=False)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_json + "\n", encoding="utf-8")
        print(f"Report written to {report_path}")
    else:
        print(report_json)

    # Determine exit code.
    all_within = report["threshold"]["all_within_threshold"]
    if not all_within:
        breaches = report["threshold"]["fields_in_breach"]
        print(
            f"THRESHOLD BREACH: {len(breaches)} field(s) exceed --max-gap {args.max_gap}: "
            + ", ".join(breaches),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
