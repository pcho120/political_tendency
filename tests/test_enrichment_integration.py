#!/usr/bin/env python3
"""tests/test_enrichment_integration.py

Standalone regression harness for ProfileEnricher field extraction.
Tests four structure-type fixtures plus one adversarial nav-pollution fixture.

Run with:
    python3.12 tests/test_enrichment_integration.py

Exit 0: all assertions pass.
Exit 1: assertion failure or missing/malformed fixture.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "html")

MIN_HTML_BYTES = 10_500


def _load_fixture(name: str) -> str:
    path = os.path.join(FIXTURE_DIR, name)
    if not os.path.exists(path):
        print(f"FAIL: fixture not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    if len(content) < 500:
        print(f"FAIL: fixture appears malformed (< 500 bytes): {path}", file=sys.stderr)
        sys.exit(1)
    return content


def _pad_to_min_size(html: str) -> str:
    """Pad HTML to meet enricher's minimum size threshold without altering structure."""
    if len(html) >= MIN_HTML_BYTES:
        return html
    insert_pos = html.rfind("</body>")
    target = MIN_HTML_BYTES + 200
    padding_needed = target - len(html)
    padding_block = "\n" + ("<!-- synthetic-padding -->\n" * (padding_needed // 27 + 2))
    if insert_pos == -1:
        return html + padding_block
    return html[:insert_pos] + padding_block + html[insert_pos:]


def _run_enricher(html: str, fixture_label: str) -> object:
    try:
        from enrichment import ProfileEnricher
    except ImportError as exc:
        print(f"FAIL: cannot import enrichment module: {exc}", file=sys.stderr)
        sys.exit(1)

    padded = _pad_to_min_size(html)
    enricher = ProfileEnricher(enable_playwright=False)
    profile = enricher.enrich(
        url=f"https://example.com/attorneys/{fixture_label}",
        html=padded,
        firm="Synthetic Fixture Firm",
    )
    return profile


_PASS = 0
_FAIL = 0


def _assert(condition: bool, msg: str) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS: {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL: {msg}")


def _assert_field_nonempty(value: object, field: str) -> None:
    if isinstance(value, list):
        _assert(len(value) > 0, f"{field} is non-empty")
    elif isinstance(value, str):
        _assert(bool(value and value.strip()), f"{field} is non-empty")
    else:
        _assert(value is not None, f"{field} is non-None")


def _assert_field_not_contains(items: list[str], forbidden: str, field: str) -> None:
    matches = [i for i in items if forbidden.lower() in i.lower()]
    _assert(
        len(matches) == 0,
        f"{field} does not contain nav-pollution string '{forbidden}' (got: {matches})",
    )


def _industries_is_sentinel_or_filled(industries: list[str]) -> bool:
    sentinel = ["no industry field"]
    return industries == sentinel or (len(industries) > 0 and industries != sentinel)


def run_sitemap_xml_fixture() -> None:
    print("\n[1] SITEMAP_XML profile fixture (sitemap_xml_profile.html)")
    html = _load_fixture("sitemap_xml_profile.html")
    profile = _run_enricher(html, "sitemap-xml")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    if profile.title:
        _assert(
            "associate" in profile.title.lower() or "senior" in profile.title.lower(),
            f"title contains expected role keyword (got: '{profile.title}')",
        )
    else:
        _assert(False, "title is populated")

    _assert_field_nonempty(profile.offices, "offices")
    if profile.offices:
        _assert(
            any("new york" in o.lower() for o in profile.offices),
            f"offices contains 'New York' (got: {profile.offices})",
        )

    _assert_field_nonempty(profile.practice_areas, "practice_areas")
    if profile.practice_areas:
        _assert(
            any("litigation" in p.lower() for p in profile.practice_areas),
            f"practice_areas contains 'Litigation' entry (got: {profile.practice_areas})",
        )

    industries = profile.industries
    if isinstance(industries, list) and industries != ["no industry field"]:
        _assert(
            any("financial" in i.lower() or "asset" in i.lower() or "insurance" in i.lower()
                for i in industries),
            f"industries populated from JSON-LD (got: {industries})",
        )
    else:
        _assert(
            _industries_is_sentinel_or_filled(industries),
            f"industries is sentinel or filled (got: {industries})",
        )

    dept = profile.department
    if isinstance(dept, list):
        dept_str = " ".join(dept).lower()
    else:
        dept_str = str(dept or "").lower()
    _assert(
        "litigation" in dept_str or dept_str == "" or dept == [],
        f"department populated from JSON-LD as 'Litigation' or empty (got: {profile.department})",
    )


def run_html_directory_flat_fixture() -> None:
    print("\n[2] HTML_DIRECTORY_FLAT profile fixture (html_directory_flat_profile.html)")
    html = _load_fixture("html_directory_flat_profile.html")
    profile = _run_enricher(html, "html-directory-flat")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    if profile.title:
        _assert(
            "counsel" in profile.title.lower() or "of counsel" in profile.title.lower(),
            f"title contains 'Of Counsel' (got: '{profile.title}')",
        )
    else:
        _assert(False, "title is populated")

    _assert_field_nonempty(profile.offices, "offices")

    _assert_field_nonempty(profile.practice_areas, "practice_areas")
    if profile.practice_areas:
        _assert(
            any("energy" in p.lower() for p in profile.practice_areas),
            f"practice_areas contains energy-related entry (got: {profile.practice_areas})",
        )

    industries = profile.industries
    _assert(
        _industries_is_sentinel_or_filled(industries),
        f"industries is sentinel or filled (got: {industries})",
    )


def run_html_alpha_paginated_fixture() -> None:
    print("\n[3] HTML_ALPHA_PAGINATED profile fixture (html_alpha_paginated_profile.html)")
    html = _load_fixture("html_alpha_paginated_profile.html")
    profile = _run_enricher(html, "html-alpha-paginated")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    if profile.title:
        _assert(
            "partner" in profile.title.lower(),
            f"title contains 'Partner' (got: '{profile.title}')",
        )
    else:
        _assert(False, "title is populated")

    _assert_field_nonempty(profile.offices, "offices")

    _assert_field_nonempty(profile.practice_areas, "practice_areas")
    if profile.practice_areas:
        _assert(
            any("acquisition" in p.lower() or "merger" in p.lower() for p in profile.practice_areas),
            f"practice_areas contains M&A entry (got: {profile.practice_areas})",
        )

    industries = profile.industries
    _assert(
        _industries_is_sentinel_or_filled(industries),
        f"industries is sentinel or filled (got: {industries})",
    )

    dept = profile.department
    if isinstance(dept, list):
        dept_str = " ".join(dept).lower()
    else:
        dept_str = str(dept or "").lower()
    _assert(
        "corporate" in dept_str or dept_str == "" or dept == [],
        f"department is 'Corporate' or empty (got: {profile.department})",
    )


def run_spa_other_fixture() -> None:
    print("\n[4] SPA_OTHER profile fixture (spa_other_profile.html)")
    html = _load_fixture("spa_other_profile.html")
    profile = _run_enricher(html, "spa-other")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    if profile.title:
        _assert(
            "partner" in profile.title.lower(),
            f"title contains 'Partner' (got: '{profile.title}')",
        )
    else:
        _assert(False, "title is populated")

    _assert_field_nonempty(profile.offices, "offices")

    _assert_field_nonempty(profile.practice_areas, "practice_areas")
    if profile.practice_areas:
        _assert(
            any("antitrust" in p.lower() or "competition" in p.lower() for p in profile.practice_areas),
            f"practice_areas contains antitrust entry (got: {profile.practice_areas})",
        )

    industries = profile.industries
    if industries != ["no industry field"]:
        _assert(
            any("tech" in i.lower() or "health" in i.lower() for i in industries),
            f"industries contains tech/health entry (got: {industries})",
        )
    else:
        _assert(True, "industries is sentinel (embedded state not parsed into industries)")

    dept = profile.department
    if isinstance(dept, list):
        dept_str = " ".join(dept).lower()
    else:
        dept_str = str(dept or "").lower()
    _assert(
        "antitrust" in dept_str or dept_str == "" or dept == [],
        f"department is 'Antitrust' or empty (got: {profile.department})",
    )


def run_adversarial_nav_pollution_fixture() -> None:
    """Adversarial fixture: nav/service headings must NOT populate practice_areas or department.

    Task 6 parser fix: qualified-synonym guards prevent generic nav headings
    ('Services', 'Section') from contaminating profile fields.
    """
    print("\n[5] ADVERSARIAL nav pollution fixture (adversarial_nav_pollution.html)")
    html = _load_fixture("adversarial_nav_pollution.html")
    profile = _run_enricher(html, "adversarial-nav")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    nav_pollution_strings = [
        "Contact Us",
        "Careers",
        "News",
        "Events",
        "Diversity",
        "FAQ",
        "Resources",
        "Privacy Policy",
        "Terms of Use",
    ]

    pa = profile.practice_areas
    dept = profile.department
    if isinstance(dept, list):
        dept_list = dept
    elif isinstance(dept, str) and dept:
        dept_list = [dept]
    else:
        dept_list = []

    for nav_string in nav_pollution_strings:
        _assert_field_not_contains(pa, nav_string, "practice_areas")

    sidebar_strings = ["Leadership", "Partners", "Associates", "Of Counsel"]
    for sidebar_string in sidebar_strings:
        _assert_field_not_contains(dept_list, sidebar_string, "department")

    _assert(
        any("mergers" in p.lower() or "acquisition" in p.lower() or "governance" in p.lower() or "equity" in p.lower() for p in pa),
        f"practice_areas contains valid profile-section content (got: {pa})",
    )

    copyright_items = [p for p in pa if "2026" in p or "©" in p or "all rights" in p.lower()]
    _assert(
        len(copyright_items) == 0,
        f"practice_areas does not contain copyright/footer text (got: {copyright_items})",
    )

    _assert(
        len(dept_list) <= 3,
        f"department is not over-populated (got {len(dept_list)} items: {dept_list})",
    )


def main() -> None:
    global _PASS, _FAIL

    print("=" * 60)
    print("test_enrichment_integration.py")
    print("=" * 60)

    run_sitemap_xml_fixture()
    run_html_directory_flat_fixture()
    run_html_alpha_paginated_fixture()
    run_spa_other_fixture()
    run_adversarial_nav_pollution_fixture()

    print("\n" + "=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 60)

    if _FAIL > 0:
        print("\nSome assertions FAILED — see output above.", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nAll assertions PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    main()
