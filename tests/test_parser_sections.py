#!/usr/bin/env python3
"""Standalone parser section regression harness.

Exercises heading normalization and boundary handling without pytest.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import parser_sections


@dataclass(frozen=True)
class NormalizeCase:
    raw: str
    expected: str
    label: str


@dataclass(frozen=True)
class ParseCase:
    label: str
    html: str
    key: str
    expected_contains: tuple[str, ...] = ()
    expected_not_contains: tuple[str, ...] = ()


NORMALIZE_POSITIVE_CASES: list[NormalizeCase] = [
    NormalizeCase("Practice Areas & Expertise", "practice_areas", "canonical practice areas"),
    NormalizeCase("Bar Admissions & Courts", "bar_admissions", "canonical admissions"),
    NormalizeCase("Industries and Markets", "industries", "canonical industries"),
]

RISKY_POSITIVE_CASES: list[NormalizeCase] = [
    NormalizeCase("Practice Services", "practice_areas", "service synonym"),
    NormalizeCase("Litigation Group", "departments", "group synonym"),
    NormalizeCase("Tax Section", "departments", "section synonym"),
]

RISKY_ADVERSARIAL_CASES: list[NormalizeCase] = [
    NormalizeCase("Client Services Team", "client_services_team", "service false-positive guard"),
    NormalizeCase("Working Group", "working_group", "group false-positive guard"),
    NormalizeCase("Section 1: Contact", "section_1_contact", "section false-positive guard"),
]

BOUNDARY_CASE = ParseCase(
    label="nested heading stop boundary",
    html="""
        <html><body>
          <h2>Practice Areas</h2>
          <div>
            <p>Core Alpha</p>
            <h3>Subsection</h3>
            <div>
              <p>Nested Beta</p>
            </div>
          </div>
          <h2>Biography</h2>
          <p>Should stay out</p>
        </body></html>
    """,
    key="practice_areas",
    expected_contains=("Core Alpha", "Nested Beta"),
    expected_not_contains=("Should stay out",),
)


def _report(result: bool, label: str, detail: str) -> tuple[int, int]:
    status = "PASS" if result else "FAIL"
    print(f"{status} | {label} | {detail}")
    return (1, 0) if result else (0, 1)


def _run_normalize_case(case: NormalizeCase) -> tuple[int, int]:
    actual = parser_sections.normalize_section_title(case.raw)
    return _report(actual == case.expected, case.label, f"raw={case.raw!r} expected={case.expected!r} actual={actual!r}")


def _run_adversarial_case(case: NormalizeCase) -> tuple[int, int]:
    actual = parser_sections.normalize_section_title(case.raw)
    return _report(actual == case.expected, case.label, f"raw={case.raw!r} expected={case.expected!r} actual={actual!r}")


def _run_parse_case(case: ParseCase) -> tuple[int, int]:
    soup = parser_sections.BeautifulSoup(case.html, "html.parser")
    heading = soup.find(lambda tag: getattr(tag, "name", None) in {"h1", "h2", "h3", "h4", "h5", "h6"})
    if heading is None:
        return _report(False, case.label, "missing heading fixture")

    blocks = parser_sections._collect_content_after(soup, heading, parser_sections._tag_heading_level(heading) or 2)
    ok = True
    details: list[str] = [f"blocks={blocks!r}"]
    for text in case.expected_contains:
        if text not in blocks:
            ok = False
            details.append(f"missing={text!r}")
    for text in case.expected_not_contains:
        if text in blocks:
            ok = False
            details.append(f"unexpected={text!r}")
    section_map = parser_sections.parse_sections(case.html)
    mapped = section_map.get(case.key, [])
    if any(text not in mapped for text in case.expected_contains):
        ok = False
        details.append(f"mapped={mapped!r}")
    if any(text in mapped for text in case.expected_not_contains):
        ok = False
        details.append(f"mapped={mapped!r}")
    return _report(ok, case.label, " ".join(details))


def main() -> int:
    print(f"python_executable={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")

    total_pass = 0
    total_fail = 0

    print("== normalize positive ==")
    for case in NORMALIZE_POSITIVE_CASES:
        passed, failed = _run_normalize_case(case)
        total_pass += passed
        total_fail += failed

    print("== risky positive ==")
    for case in RISKY_POSITIVE_CASES:
        passed, failed = _run_normalize_case(case)
        total_pass += passed
        total_fail += failed

    print("== risky adversarial ==")
    for case in RISKY_ADVERSARIAL_CASES:
        passed, failed = _run_adversarial_case(case)
        total_pass += passed
        total_fail += failed

    print("== boundary ==")
    passed, failed = _run_parse_case(BOUNDARY_CASE)
    total_pass += passed
    total_fail += failed

    print(f"SUMMARY pass={total_pass} fail={total_fail}")
    if total_fail:
        print("OVERALL: FAIL")
        return 1

    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
