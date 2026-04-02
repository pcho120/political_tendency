#!/usr/bin/env python3
"""Standalone validator regression harness.

Runs direct validator checks without pytest so title and office policy rules
can be pinned from the command line.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validators import ValidationReason, validate_offices, validate_title


@dataclass(frozen=True)
class TitleCase:
    name: str
    raw: str | None
    firm_name: str
    expected_value: str | None
    expected_reason: str | None


@dataclass(frozen=True)
class OfficeCase:
    name: str
    raw: list[str]
    expected_value: list[str]
    expected_reason: str | None


TITLE_CASES: list[TitleCase] = [
    TitleCase(
        name="valid_title_acceptance",
        raw=" Senior Associate ",
        firm_name="Example Law LLP",
        expected_value="Senior Associate",
        expected_reason=None,
    ),
    TitleCase(
        name="title_alias_normalization",
        raw="Of counsel",
        firm_name="Example Law LLP",
        expected_value="Of Counsel",
        expected_reason=None,
    ),
    TitleCase(
        name="title_sr_alias_normalization",
        raw="Sr. Associate",
        firm_name="Example Law LLP",
        expected_value="Senior Associate",
        expected_reason=None,
    ),
    TitleCase(
        name="firm_name_contamination_rejection",
        raw="Knobbe Martens",
        firm_name="Knobbe Martens Olson & Bear LLP",
        expected_value=None,
        expected_reason=ValidationReason.CONTAMINATED,
    ),
    TitleCase(
        name="email_contamination_rejection",
        raw="partner@firm.com",
        firm_name="Example Law LLP",
        expected_value=None,
        expected_reason=ValidationReason.CONTAMINATED,
    ),
    TitleCase(
        name="empty_input_rejection",
        raw=None,
        firm_name="Example Law LLP",
        expected_value=None,
        expected_reason=ValidationReason.NOT_FOUND,
    ),
]

OFFICE_CASES: list[OfficeCase] = [
    OfficeCase(
        name="us_office_acceptance",
        raw=["New York", "Boston, MA"],
        expected_value=["New York", "Boston, MA"],
        expected_reason=None,
    ),
    OfficeCase(
        name="non_us_office_acceptance_with_policy_signal",
        raw=["London, UK"],
        expected_value=["London, UK"],
        expected_reason=ValidationReason.INTERNATIONAL_OFFICE,
    ),
    OfficeCase(
        name="office_contamination_rejection",
        raw=["London, UK", "partner@firm.com"],
        expected_value=["London, UK"],
        expected_reason=ValidationReason.INTERNATIONAL_OFFICE,
    ),
]


def _print_result(kind: str, case_name: str, ok: bool, details: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"{status} [{kind}:{case_name}] {details}")


def _run_title_case(case: TitleCase) -> bool:
    value, reason = validate_title(case.raw, firm_name=case.firm_name)
    ok = value == case.expected_value and reason == case.expected_reason
    details = (
        f"raw={case.raw!r} firm_name={case.firm_name!r} "
        f"expected_value={case.expected_value!r} actual_value={value!r} "
        f"expected_reason={case.expected_reason!r} actual_reason={reason!r}"
    )
    _print_result("title", case.name, ok, details)
    return ok


def _run_office_case(case: OfficeCase, non_us_policy: str) -> bool:
    value, reason = validate_offices(case.raw)
    expected_value = case.expected_value
    expected_reason = case.expected_reason

    ok = value == expected_value and reason == expected_reason
    details = (
        f"raw={case.raw!r} expected_value={expected_value!r} actual_value={value!r} "
        f"expected_reason={expected_reason!r} actual_reason={reason!r} "
        f"policy={non_us_policy}"
    )
    _print_result("offices", case.name, ok, details)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--non-us-policy",
        choices=["reject", "accept"],
        default="reject",
        help="Expected non-US office policy direction.",
    )
    args = parser.parse_args()

    print(f"python_executable={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")
    print(f"non_us_policy={args.non_us_policy}")

    failures = 0
    for case in TITLE_CASES:
        if not _run_title_case(case):
            failures += 1

    for case in OFFICE_CASES:
        if not _run_office_case(case, args.non_us_policy):
            failures += 1

    if failures:
        print("OVERALL: FAIL")
        print("REGRESSION RULES BROKEN: title validation and/or office policy")
        return 1

    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
