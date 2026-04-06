#!/usr/bin/env python3
"""Standalone field merger RED regression harness.

Pins list-field merge behavior before production fixes land.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attorney_extractor import AttorneyProfile
from field_merger import FieldMerger


@dataclass(frozen=True)
class MergeCase:
    label: str
    base: AttorneyProfile
    supplement: AttorneyProfile
    source_url: str
    source_type: str
    expected_practice_areas: list[str]


def _run_case(case: MergeCase) -> bool:
    merger = FieldMerger()
    merged = merger.merge_all([
        (case.base, case.base.profile_url or case.source_url + "/base", "external_directory"),
        (case.supplement, case.supplement.profile_url or case.source_url + "/supplement", case.source_type),
    ])
    ok = merged.practice_areas == case.expected_practice_areas
    print(
        f"{'PASS' if ok else 'FAIL'} | {case.label} | "
        f"expected_practice_areas={case.expected_practice_areas!r} actual_practice_areas={merged.practice_areas!r}"
    )
    return ok


def test_list_merge_preserves_lower_precedence_practice_areas() -> None:
    assert _run_case(CASE)


CASE = MergeCase(
    label="list merge should preserve lower-precedence practice areas",
    base=AttorneyProfile(
        firm="Example Law LLP",
        profile_url="https://example.com/attorneys/base",
        practice_areas=["Litigation"],
        offices=["New York"],
    ),
    supplement=AttorneyProfile(
        firm="Example Law LLP",
        profile_url="https://example.com/attorneys/supplement",
        practice_areas=["Corporate", "IP"],
        offices=["New York", "Boston"],
    ),
    source_url="https://example.com/attorneys/supplement",
    source_type="profile_core",
    # merge_all sorts by descending precedence: profile_core (100) leads,
    # then external_directory (30) union-dedup adds "Litigation".
    # Result: higher-prec values first, then lower-prec additions.
    expected_practice_areas=["Corporate", "IP", "Litigation"],
)


def main() -> int:
    print(f"python_executable={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")
    ok = _run_case(CASE)
    if ok:
        print("OVERALL: PASS")
        return 0
    print("OVERALL: FAIL")
    print("REGRESSION RULES BROKEN: list fields are still overwritten by higher-precedence merges")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
