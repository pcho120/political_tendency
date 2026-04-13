#!/usr/bin/env python3
"""JSONL deduplication utility.

Usage:
    python dedup_jsonl.py <input.jsonl> [--output <output.jsonl>]

Deduplicates records by profile_url (fallback: firm+full_name).
Keeps the most complete record (most non-empty, non-sentinel fields).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


_SENTINEL_VALUES = {"no industry field", "no JD", "unknown"}


def _record_score(record: dict) -> int:
    """Count non-empty, non-sentinel field values."""
    count = 0
    for value in record.values():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            if any(str(item) not in _SENTINEL_VALUES for item in value):
                count += 1
        elif str(value) not in _SENTINEL_VALUES:
            count += 1
    return count


def _record_key(record: dict) -> str:
    url = record.get("profile_url", "")
    if isinstance(url, str):
        url = url.strip()
    else:
        url = str(url).strip()
    if url:
        return url
    firm = record.get("firm", "")
    name = record.get("full_name", "")
    return f"{firm}||{name}"


def _deduplicate(records: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    scores: dict[str, int] = {}
    for record in records:
        key = _record_key(record)
        score = _record_score(record)
        current_score = scores.get(key)
        if current_score is None or score > current_score:
            best[key] = record
            scores[key] = score
    return list(best.values())


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_deduped.jsonl")


def dedup_jsonl(input_path: Path, output_path: Path) -> dict:
    best: dict[str, dict] = {}
    total_lines = 0
    malformed = 0
    firm_counts: dict[str, int] = defaultdict(int)

    with input_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            total_lines += 1
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed line %d: %s", lineno, exc)
                malformed += 1
                continue

            if not isinstance(record, dict):
                log.warning("Skipping malformed line %d: expected object", lineno)
                malformed += 1
                continue

            key = _record_key(record)
            existing = best.get(key)
            if existing is None or _record_score(record) > _record_score(existing):
                best[key] = record

    with output_path.open("w", encoding="utf-8") as f:
        for record in best.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            firm_counts[record.get("firm", "?")] += 1

    unique = len(best)
    duplicates = total_lines - malformed - unique
    return {
        "input_lines": total_lines,
        "unique": unique,
        "duplicates_removed": duplicates,
        "malformed_skipped": malformed,
        "per_firm": dict(firm_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate a JSONL file by profile_url.")
    parser.add_argument("input", help="Input JSONL file path")
    parser.add_argument("--output", help="Output JSONL file path (default: <input>_deduped.jsonl)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} does not exist", file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _default_output_path(input_path)

    stats = dedup_jsonl(input_path, output_path)
    print(f"Input lines:       {stats['input_lines']}")
    print(f"Unique records:    {stats['unique']}")
    print(f"Duplicates removed:{stats['duplicates_removed']}")
    if stats["malformed_skipped"]:
        print(f"Malformed skipped: {stats['malformed_skipped']}")
    print(f"Output:            {output_path}")
    print("\nPer-firm counts:")
    for firm, count in sorted(stats["per_firm"].items()):
        print(f"  {firm}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
