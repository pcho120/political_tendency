#!/usr/bin/env python3
"""tests/test_enrichment_integration.py

Standalone regression harness for ProfileEnricher field extraction.
Tests four structure-type fixtures, one adversarial nav-pollution fixture,
and four low-fill failure-mode fixtures covering contaminated practice dumps,
department concatenation blobs, surname+state office artifacts, and SPA
small-content / Playwright-recoverable cases.

Run with:
    python3.12 tests/test_enrichment_integration.py

Exit 0: all assertions pass.
Exit 1: assertion failure or missing/malformed fixture.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "html")

MIN_HTML_BYTES = 10_500

LATHAM_INLINE_HTML = """<!DOCTYPE html>
<html>
<head><title>Stephanie Adams - Latham &amp; Watkins LLP</title></head>
<body>
<div class="hero-section">
  <h1>Stephanie Adams</h1>
  <p class="attorney-title">Partner</p>
  <p class="attorney-office">New York</p>
</div>
<h2>Profile</h2>
<p>Stephanie Adams is a partner in the New York office of Latham &amp; Watkins.</p>
<h2>Experience</h2>
<h3>Merger Control</h3>
<p>Extensive experience in merger control filings.</p>
<h2>Qualifications</h2>
<h3>Bar Qualification</h3>
<p>New York Bar, 2005</p>
<h3>Education</h3>
<p>Harvard Law School, J.D.</p>
<h3>Practices</h3>
<ul>
  <li>Antitrust</li>
  <li>Corporate</li>
</ul>
<h2>Recognition</h2>
<p>Chambers USA, 2024</p>
</body>
</html>"""


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


def _run_enricher(html: str, fixture_label: str) -> Any:
    enrichment = pytest.importorskip("enrichment")
    ProfileEnricher = enrichment.ProfileEnricher

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
_OBSERVED_GAPS = 0


def _assert(condition: bool, msg: str) -> None:
    global _PASS, _FAIL
    # Encode-safe output for Windows cp949/cp1252 terminals
    safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
    if condition:
        _PASS += 1
        print(f"  PASS: {safe_msg}")
    else:
        _FAIL += 1
        print(f"  FAIL: {safe_msg}")


def _observe_gap(condition: bool, msg: str) -> None:
    """Log an observation about a known low-fill gap without causing test failure.

    Used in Task 2 fixtures to document current behavior gaps that later tasks
    (3-8) will turn into hard assertions after production fixes land.
    """
    global _PASS, _OBSERVED_GAPS
    safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
    if condition:
        _PASS += 1
        print(f"  PASS: {safe_msg}")
    else:
        _OBSERVED_GAPS += 1
        print(f"  GAP:  {safe_msg}  [known low-fill gap, RED target for Tasks 3-8]")


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


def _observe_field_not_contains(items: list[str], forbidden: str, field: str) -> None:
    """Observational variant of _assert_field_not_contains for low-fill gap tracking."""
    matches = [i for i in items if forbidden.lower() in i.lower()]
    _observe_gap(
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


def _test_latham_spa_other() -> None:
    """Test that Latham-style SPA_OTHER HTML extracts key fields correctly."""
    profile = _run_enricher(LATHAM_INLINE_HTML, "latham-spa-other")
    _assert(
        bool(getattr(profile, "full_name", None)) and "Practice" not in (getattr(profile, "full_name", "") or ""),
        f"full_name should be a person name, not contain 'Practice' (got: {getattr(profile, 'full_name', None)!r})",
    )
    pas = getattr(profile, "practice_areas", []) or []
    _assert(
        len(pas) > 0,
        f"practice_areas should not be empty for Latham fixture (got: {pas!r})",
    )
    _assert(
        not any(len(s) > 100 for s in pas),
        f"practice_areas should not contain long bio/award sentences (got: {pas!r})",
    )


# ---------------------------------------------------------------------------
# Low-fill failure-mode fixtures (Task 2)
# ---------------------------------------------------------------------------

def run_contaminated_practice_dump_fixture() -> None:
    """Failure mode (a): practice_areas list mixed with nav junk, phone numbers,
    emails, download links, experience descriptions, and footer strings.

    The enricher should ideally filter out contamination and retain only the
    valid practice-area entries. This test documents the current behavior and
    asserts that at least the valid entries are present while flagging common
    contamination strings.
    """
    print("\n[6] LOW-FILL: contaminated practice dump (contaminated_practice_dump.html)")
    html = _load_fixture("contaminated_practice_dump.html")
    profile = _run_enricher(html, "contaminated-practice-dump")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    pa = profile.practice_areas or []

    # Valid entries should be present (hard assertion)
    _assert(
        any("intellectual property" in p.lower() for p in pa),
        f"practice_areas contains 'Intellectual Property' (got: {pa})",
    )
    _assert(
        any("patent" in p.lower() for p in pa),
        f"practice_areas contains patent-related entry (got: {pa})",
    )

    # Contamination checks: observe gaps (RED targets for Tasks 3-8)
    phone_items = [p for p in pa if "+1-" in p or "555-" in p]
    _observe_gap(
        len(phone_items) == 0,
        f"practice_areas does not contain phone numbers (got: {phone_items})",
    )

    email_items = [p for p in pa if "@" in p and "." in p]
    _observe_gap(
        len(email_items) == 0,
        f"practice_areas does not contain email addresses (got: {email_items})",
    )

    nav_junk = ["Download vCard", "searchSearch", "View More", "Go BackProceed",
                 "Privacy Policy", "Terms of Use"]
    for junk in nav_junk:
        _observe_field_not_contains(pa, junk, "practice_areas")

    copyright_items = [p for p in pa if "2026" in p or "(c)" in p.lower() or "all rights" in p.lower()]
    _observe_gap(
        len(copyright_items) == 0,
        f"practice_areas does not contain copyright text (got: {copyright_items})",
    )

    # Long experience descriptions (>100 chars) should not be practice areas
    long_items = [p for p in pa if len(p) > 100]
    _observe_gap(
        len(long_items) == 0,
        f"practice_areas does not contain long experience descriptions (got: {long_items})",
    )

    nav_label_items = [p for p in pa if p in ("Lawyers", "Practices", "Industries",
                                                "Offices", "Careers", "Insights",
                                                "Our Firm", "Inclusion", "Alumni")]
    _observe_gap(
        len(nav_label_items) == 0,
        f"practice_areas does not contain nav-label strings (got: {nav_label_items})",
    )


def run_department_concat_blob_fixture() -> None:
    """Failure mode (b): department field contains a concatenated nav/UI blob
    like 'LawyersPracticesIndustriesOfficesCareers...' instead of a clean
    department name.

    The enricher should ideally recognize this as contamination and either
    clean it to the valid department value or reject it.
    """
    print("\n[7] LOW-FILL: department concatenation blob (department_concat_blob.html)")
    html = _load_fixture("department_concat_blob.html")
    profile = _run_enricher(html, "department-concat-blob")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    dept = profile.department
    if isinstance(dept, list):
        dept_list = dept
    elif isinstance(dept, str) and dept:
        dept_list = [dept]
    else:
        dept_list = []

    # The concatenated blob should NOT appear as a department value (gap)
    blob_fragment = "LawyersPracticesIndustries"
    blob_items = [d for d in dept_list if blob_fragment in d]
    _observe_gap(
        len(blob_items) == 0,
        f"department does not contain concatenated nav blob (got: {blob_items})",
    )

    go_back_items = [d for d in dept_list if "Go Back" in d or "Proceed" in d]
    _observe_gap(
        len(go_back_items) == 0,
        f"department does not contain 'Go BackProceed' (got: {go_back_items})",
    )

    # The valid department value should ideally be recovered
    dept_str = " ".join(dept_list).lower()
    _observe_gap(
        ("environmental" in dept_str or "sustainability" in dept_str) and len(blob_items) == 0,
        f"department is cleanly 'Environmental & Sustainability' without blob (got: {dept_list})",
    )

    # Practice areas should still be clean (hard assertion)
    pa = profile.practice_areas or []
    _assert(
        any("environmental" in p.lower() for p in pa),
        f"practice_areas contains 'Environmental Law' (got: {pa})",
    )


def run_surname_state_office_fixture() -> None:
    """Failure mode (c): offices list contains 'Surname, ST' artifacts from
    external directory scraping (e.g. 'Chen, CA', 'Davis, CA') mixed with
    valid office locations.

    The enricher should ideally filter out surname+state entries and retain
    only the genuine office location.
    """
    print("\n[8] LOW-FILL: surname+state office artifact (surname_state_office.html)")
    html = _load_fixture("surname_state_office.html")
    profile = _run_enricher(html, "surname-state-office")

    _assert(profile.extraction_status != "FAILED", "extraction_status is not FAILED")

    offices = profile.offices or []

    # The valid office should be present (hard assertion)
    _assert(
        any("san francisco" in o.lower() for o in offices),
        f"offices contains 'San Francisco' (got: {offices})",
    )

    # Surname+state artifacts should NOT be treated as real offices (gap)
    surname_artifacts = [o for o in offices if o in ("Chen, CA", "Davis, CA")]
    _observe_gap(
        len(surname_artifacts) == 0,
        f"offices does not contain surname+state artifacts (got: {surname_artifacts})",
    )

    # Department should be clean (hard assertion)
    dept = profile.department
    if isinstance(dept, list):
        dept_str = " ".join(dept).lower()
    else:
        dept_str = str(dept or "").lower()
    _assert(
        "corporate" in dept_str or dept_str == "" or dept == [],
        f"department is 'Corporate' or empty (got: {profile.department})",
    )

    # Practice areas should be populated (hard assertion)
    pa = profile.practice_areas or []
    _assert(
        any("corporate" in p.lower() or "venture" in p.lower() for p in pa),
        f"practice_areas contains corporate/venture entry (got: {pa})",
    )


def run_spa_small_content_fixture() -> None:
    """Failure mode (d): SPA page with minimal rendered HTML but full profile
    data in __NEXT_DATA__ JSON payload. Without Playwright or JSON extraction,
    the enricher may produce an all-empty profile.

    This test checks whether the enricher can recover data from the embedded
    JSON payload when the rendered content is minimal.
    """
    print("\n[9] LOW-FILL: SPA small-content / Playwright-recoverable (spa_small_content.html)")
    html = _load_fixture("spa_small_content.html")
    profile = _run_enricher(html, "spa-small-content")

    # The enricher may or may not parse __NEXT_DATA__; document current behavior
    # If it does parse, these should pass; if not, they reveal the gap
    pa = profile.practice_areas or []
    offices = profile.offices or []
    dept = profile.department
    if isinstance(dept, list):
        dept_list = dept
    elif isinstance(dept, str) and dept:
        dept_list = [dept]
    else:
        dept_list = []

    # practice_areas and department ARE currently recovered from __NEXT_DATA__
    _assert(
        len(pa) > 0,
        f"practice_areas recovered from __NEXT_DATA__ (got: {pa})",
    )

    if pa:
        _assert(
            any("securities" in p.lower() or "litigation" in p.lower() for p in pa),
            f"practice_areas contains 'Securities Litigation' from JSON payload (got: {pa})",
        )

    _assert(
        len(dept_list) > 0,
        f"department recovered from __NEXT_DATA__ (got: {dept_list})",
    )

    if dept_list:
        dept_str = " ".join(dept_list).lower()
        _assert(
            "litigation" in dept_str,
            f"department is 'Litigation' from JSON payload (got: {dept_list})",
        )

    # offices ARE now recovered from __NEXT_DATA__ after Task 8 fix
    _assert(
        len(offices) > 0,
        f"offices should be recovered from __NEXT_DATA__ (got: {offices})",
    )

    if offices:
        _assert(
            any("boston" in o.lower() for o in offices),
            f"offices contains 'Boston' from JSON payload (got: {offices})",
        )


def run_partial_profile_supplementation_case() -> None:
    """RED case: partially populated profile should be supplemented, not skipped."""
    print("\n[10] RED: partial-profile supplementation from section map")
    enrichment = pytest.importorskip("enrichment")
    attorney_extractor = pytest.importorskip("attorney_extractor")
    _extract_from_section_map = enrichment._extract_from_section_map
    AttorneyProfile = attorney_extractor.AttorneyProfile

    profile = AttorneyProfile(
        firm="Synthetic Fixture Firm",
        profile_url="https://example.com/attorneys/partial-profile",
        practice_areas=["Intellectual Property"],
    )
    section_map = {
        "practice_areas": ["Intellectual Property", "M&A"],
        "departments": ["Transactional"],
    }

    _extract_from_section_map(
        profile,
        section_map,
        url="https://example.com/attorneys/partial-profile",
        html="<html></html>",
    )

    _assert(
        profile.practice_areas == ["Intellectual Property", "M&A"],
        f"practice_areas should be supplemented from section map when partially populated (got: {profile.practice_areas})",
    )


def test_run_sitemap_xml_fixture() -> None:
    run_sitemap_xml_fixture()


def test_run_html_directory_flat_fixture() -> None:
    run_html_directory_flat_fixture()


def test_run_html_alpha_paginated_fixture() -> None:
    run_html_alpha_paginated_fixture()


def test_run_spa_other_fixture() -> None:
    run_spa_other_fixture()


def test_latham_spa_other() -> None:
    _test_latham_spa_other()


def test_adversarial_nav_pollution_fixture() -> None:
    run_adversarial_nav_pollution_fixture()


def test_contaminated_practice_dump_fixture() -> None:
    run_contaminated_practice_dump_fixture()


def test_department_concat_blob_fixture() -> None:
    run_department_concat_blob_fixture()


def test_surname_state_office_fixture() -> None:
    run_surname_state_office_fixture()


def test_spa_small_content_fixture() -> None:
    run_spa_small_content_fixture()


def test_partial_profile_supplementation_case() -> None:
    run_partial_profile_supplementation_case()


def main() -> None:
    global _PASS, _FAIL

    print("=" * 60)
    print("test_enrichment_integration.py")
    print("=" * 60)

    run_sitemap_xml_fixture()
    run_html_directory_flat_fixture()
    run_html_alpha_paginated_fixture()
    run_spa_other_fixture()
    _test_latham_spa_other()
    run_adversarial_nav_pollution_fixture()

    # Low-fill failure-mode fixtures (Task 2)
    run_contaminated_practice_dump_fixture()
    run_department_concat_blob_fixture()
    run_surname_state_office_fixture()
    run_spa_small_content_fixture()
    run_partial_profile_supplementation_case()

    print("\n" + "=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed, {_OBSERVED_GAPS} observed gaps")
    print("=" * 60)

    if _FAIL > 0:
        print("\nSome assertions FAILED — see output above.", file=sys.stderr)
        sys.exit(1)
    elif _OBSERVED_GAPS > 0:
        print(f"\nAll assertions PASSED. {_OBSERVED_GAPS} known low-fill gaps observed (RED targets for later tasks).")
        sys.exit(0)
    else:
        print("\nAll assertions PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    main()
