#!/usr/bin/env python3
"""Standalone title regression harness.

Runs direct validator checks without pytest so the current contamination
baseline can be pinned from the command line.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from validators import ValidationReason, validate_title


@dataclass(frozen=True)
class Case:
    raw: str
    firm_name: str
    expected_value: str | None
    expected_rejected: bool


CONTAMINATED_CASES: list[Case] = [
    Case("Knobbe Martens", "Knobbe Martens Olson & Bear LLP", None, True),
    Case("ArentFox Schiff", "ArentFox Schiff LLP", None, True),
    Case("Weil, Gotshal & Manges LLP", "Weil, Gotshal & Manges LLP", None, True),
]

VALID_CASES: list[Case] = [
    Case("Partner", "Knobbe Martens Olson & Bear LLP", "Partner", False),
    Case("Senior Associate", "ArentFox Schiff LLP", "Senior Associate", False),
    Case("Managing Partner", "Weil, Gotshal & Manges LLP", "Managing Partner", False),
    Case("Of counsel", "Example Law LLP", "Of Counsel", False),
    Case("Sr. Associate", "Example Law LLP", "Senior Associate", False),
    Case("Global Head of AI", "Example Law LLP", "Global Head of AI", False),
]


def _run_case(case: Case) -> tuple[bool, str | None]:
    value, reason = validate_title(case.raw, firm_name=case.firm_name)
    rejected = value is None
    if case.expected_rejected:
        return rejected, reason
    if value != case.expected_value:
        return True, reason
    return rejected, reason


def _print_group(name: str, cases: list[Case]) -> tuple[int, int]:
    passed = 0
    failed = 0
    print(f"== {name} ==")
    for case in cases:
        rejected, reason = _run_case(case)
        if case.expected_rejected == rejected:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
            failed += 1
        print(
            f"{status} raw={case.raw!r} firm={case.firm_name!r} "
            f"expected_rejected={case.expected_rejected} actual_rejected={rejected} reason={reason}"
        )
    print(f"{name} summary: pass={passed} fail={failed}")
    return passed, failed


def main() -> int:
    print(f"python_executable={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")

    contaminated_passed, contaminated_failed = _print_group("CONTAMINATED_CASES", CONTAMINATED_CASES)
    valid_passed, valid_failed = _print_group("VALID_CASES", VALID_CASES)

    all_contaminated_rejected = contaminated_passed == len(CONTAMINATED_CASES) and contaminated_failed == 0
    if all_contaminated_rejected:
        print(
            "NOTE: all contaminated cases already reject under current validator behavior "
            "(production bug was pre-fixed; harness confirms correct state)."
        )

    total_failures = contaminated_failed + valid_failed
    if total_failures:
        print(f"OVERALL: FAIL ({total_failures} mismatches)")
        return 1

    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
