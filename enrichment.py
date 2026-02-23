#!/usr/bin/env python3
"""enrichment.py - Heading-Based Profile Enrichment (PART 2, STEPS 3-5)

Orchestrates full attorney profile extraction from HTML using:
1. parser_sections.parse_sections()   — heading-based section map (no class selectors)
2. validators.*                       — per-field validation + sentinels
3. Playwright conditional escalation  — only when static parse is insufficient

Public API:
    enrich_profile(url, html, firm, *, session, logger, use_playwright)
        → AttorneyProfile

    ProfileEnricher(session, logger, enable_playwright)
        .enrich(url, html, firm) → AttorneyProfile

Playwright is invoked ONLY when:
- html is empty / too small (< 2000 bytes)
- No JSON-LD or embedded state detected
- Dynamic pagination or accordion expansion required (detected via heuristics)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import requests

# Core data structures (single source of truth in attorney_extractor)
from attorney_extractor import (
    AttorneyProfile,
    EducationRecord,
    DEGREE_PATTERNS,
    US_STATES,
)

# Heading-based section parser
from parser_sections import parse_sections, find_section, normalize_section_title

# Per-field validators
from validators import (
    validate_name,
    validate_title,
    validate_offices,
    validate_department,
    validate_practice_areas,
    validate_industries,
    validate_bar_admissions,
    validate_education,
    parse_education_text_blocks,
    parse_bar_admissions_text_blocks,
    extract_degree_from_text,
    extract_year_from_text,
    extract_school_from_text,
    ValidationReason,
)

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag
    from debug_logger import DebugLogger
try:
    from bs4 import BeautifulSoup, Tag  # noqa: F811
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False  # pyright: ignore[reportConstantRedefinition]
try:
    from debug_logger import DebugLogger  # noqa: F811
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False  # pyright: ignore[reportConstantRedefinition]


# ---------------------------------------------------------------------------
# Name validation (re-use pattern from attorney_extractor)
# ---------------------------------------------------------------------------

_VALID_NAME_RE = re.compile(r"^[A-Z][a-z]+(?:[\s][A-Z][a-z\.\-']+)+$")
_HEADER_TERMS: frozenset[str] = frozenset({
    "last name", "first name", "firm name", "attorney", "name", "title",
    "lawyer", "partner", "associate", "counsel", "full name", "contact",
})

# Heuristics that indicate a page requires JavaScript rendering
_DYNAMIC_PAGE_SIGNALS = [
    "window.__INITIAL_STATE__",
    "window.__NEXT_DATA__",
    "__NUXT__",
    "id=\"__NEXT_DATA__\"",
    "data-reactroot",
    "ng-app",
    "v-app",
    # Empty main content areas with known JS hydration containers
    "<div id=\"app\"></div>",
    "<div id=\"root\"></div>",
]

# Accordion / tab labels to expand in Playwright
_ACCORDION_LABELS = [
    "education", "admissions", "bar", "qualifications",
    "credentials", "professional background", "academic background",
    "experience", "practice", "industries",
]


# ---------------------------------------------------------------------------
# ProfileEnricher — main orchestration class
# ---------------------------------------------------------------------------

class ProfileEnricher:
    """Heading-based profile enrichment with conditional Playwright escalation.

    Extraction cascade:
    1. JSON-LD structured data (highest priority — structured, reliable)
    2. Embedded React/Next.js state objects
    3. Heading-based section parser (NO class selectors)
    4. Proximity / keyword fallback across full page text
    5. Playwright escalation (only if static extraction insufficient)

    All fields validated through validators.py before being stored on profile.
    Sentinels applied automatically:
        - industries → ["no industry field"] when absent
        - education  → [EducationRecord(degree="no JD", ...)] when absent
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        logger: DebugLogger | None = None,
        enable_playwright: bool = True,
        timeout: int = 10,
    ) -> None:
        self.session = session or requests.Session()
        self.logger = logger
        self.enable_playwright = enable_playwright
        self.timeout = timeout
        self._last_captured_json: list[dict[str, Any]] = []

        self.session.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def enrich(
        self,
        url: str,
        html: str,
        firm: str,
        *,
        force_playwright: bool = False,
    ) -> AttorneyProfile:
        """Extract a complete AttorneyProfile from profile HTML.
        Hybrid strategy:
        1. If html not provided, fetch with requests first.
        2. Check: len(html) < 10000 OR 'education' not in html.
        3. Only escalate to Playwright when evidence-based.
        4. Never default to Playwright.

        Args:
            url:   Full profile URL (used for context and logging)
            html:  Raw HTML string from caller (may be empty)
            firm:  Firm name (stored on profile)
            force_playwright: Always use Playwright regardless of static result
            AttorneyProfile with extraction_status set and sentinels applied.
        """
        profile = AttorneyProfile(firm=firm, profile_url=url)
        start_time = time.time()
        render_mode = "requests"

        # --- Step 1: Fetch with requests if html not already provided ---
        if not html:
            html = self._fetch_with_requests(url) or ""

        # --- Step 2: Hybrid check — escalate to Playwright only when necessary ---
        needs_playwright = force_playwright or self._needs_playwright_hybrid(html)
        if needs_playwright and self.enable_playwright:
            playwright_html = self._fetch_with_playwright(url)
            if playwright_html:
                html = playwright_html
                render_mode = "playwright"
        # --- Static extraction cascade ---
        if html and len(html) >= 10000:
            self._extract_all(profile, url, html)
        else:
            profile.diagnostics["html_too_small"] = True
            profile.extraction_status = "FAILED"
            profile.missing_fields = _all_field_names()
            if self.logger and LOGGER_AVAILABLE:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self.logger.log_profile_result(
                    url=url,
                    status=profile.extraction_status,
                    missing_fields=profile.missing_fields,
                    enrichment_render_mode_used=render_mode,
                    elapsed_ms=elapsed_ms,
                )
            return profile
        # --- Validate and apply sentinels ---
        self._validate_fields(profile)
        profile.calculate_status()
        if self.logger and LOGGER_AVAILABLE:
            elapsed_ms = int((time.time() - start_time) * 1000)
            self.logger.log_profile_result(
                url=url,
                status=profile.extraction_status,
                missing_fields=profile.missing_fields,
                enrichment_render_mode_used=render_mode,
                elapsed_ms=elapsed_ms,
            )

        return profile

    # ------------------------------------------------------------------
    # Hybrid Playwright escalation decision
    # ------------------------------------------------------------------

    def _fetch_with_requests(self, url: str) -> str | None:
        """Fetch profile page with requests. Returns HTML string or None on failure."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return None

    def _needs_playwright_hybrid(self, html: str) -> bool:
        """
        Return True ONLY when requests-fetched HTML is insufficient.
        Conditions (OR):
          1. HTML length < 10000 bytes
          2. 'education' not found in HTML (case-insensitive)
        Never returns True by default — must be evidence-based.
        """
        if not html:
            return True
        if len(html) < 10000:
            return True
        if "education" not in html.lower():
            return True
        return False
    def _playwright_reason(self, html: str) -> str:
        """Return a short string describing why Playwright was triggered."""
        if not html or len(html) < 10000:
            return f"html_too_short({len(html) if html else 0})"
        if "education" not in html.lower():
            return "no_education_found"
        return "unknown"

    # ------------------------------------------------------------------
    # Playwright fetch with accordion expansion
    # ------------------------------------------------------------------

    def _fetch_with_playwright(self, url: str) -> str | None:
        """Fetch URL with Playwright, expanding accordions/tabs before capture.

        Returns:
            Rendered HTML string, or None on failure / unavailability.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                # Intercept JSON responses (may yield richer data than DOM)
                captured_json: list[dict[str, Any]] = []

                def _on_response(response) -> None:
                    try:
                        if response.ok and "json" in response.headers.get(
                            "content-type", ""
                        ).lower():
                            try:
                                data = response.json()
                                captured_json.append({"url": response.url, "data": data})
                            except Exception:
                                pass
                    except Exception:
                        pass

                page.on("response", _on_response)

                page.goto(url, timeout=30_000, wait_until="networkidle")

                # Wait for primary content selector
                try:
                    page.wait_for_selector("h1, main, article", timeout=5_000)
                except Exception:
                    pass

                # Expand accordions / tabs
                for label in _ACCORDION_LABELS:
                    for selector in [
                        f"button:has-text('{label}')",
                        f"[role='tab']:has-text('{label}')",
                        f"[aria-controls]:has-text('{label}')",
                        f"a:has-text('{label}')",
                    ]:
                        try:
                            elements = page.locator(selector).all()
                            for element in elements:
                                if element.is_visible():
                                    element.click()
                                    page.wait_for_timeout(400)
                                    break
                        except Exception:
                            continue

                # Click "Read more" / "Show more" expand buttons
                for expand_text in ["read more", "show more", "see more", "expand"]:
                    try:
                        btn = page.locator(f"button:has-text('{expand_text}')").first
                        if btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(300)
                    except Exception:
                        pass

                html = page.content()
                page.close()
                context.close()
                browser.close()

                # Attach captured JSON to diagnostics storage (side-channel)
                # We store it as module-level state just for this call context
                self._last_captured_json = captured_json

                return html

        except Exception:
            return None

    # ------------------------------------------------------------------
    # Full extraction cascade
    # ------------------------------------------------------------------

    def _extract_all(self, profile: AttorneyProfile, url: str, html: str) -> None:
        """Run all extraction stages in priority order."""

        # --- STAGE 1: JSON-LD ---
        json_ld = _extract_json_ld(html)
        if json_ld:
            _merge_json_ld(profile, json_ld)
            profile.diagnostics["json_ld_found"] = True

        # --- STAGE 2: Embedded state objects ---
        embedded = _extract_embedded_state(html)
        if embedded:
            _merge_embedded_state(profile, embedded)
            profile.diagnostics["embedded_state_found"] = True

        # --- STAGE 3: Captured JSON (from Playwright intercept if available) ---
        captured = getattr(self, "_last_captured_json", [])
        if captured:
            _merge_captured_json(profile, captured)
            self._last_captured_json = []

        # --- STAGE 4: Heading-based section parser ---
        if BS4_AVAILABLE:
            section_map = parse_sections(html)
            profile.diagnostics["section_keys_found"] = list(section_map.keys())
            _extract_from_section_map(profile, section_map, url, html)

        # --- STAGE 5: Proximity / keyword fallback for still-missing fields ---
        _proximity_fallback(profile, html)

    # ------------------------------------------------------------------
    # Field validation pass
    # ------------------------------------------------------------------

    def _validate_fields(self, profile: AttorneyProfile) -> None:
        """Run all field validators and apply sentinels."""

        # Name
        name_clean, reason = validate_name(profile.full_name)
        if name_clean:
            profile.full_name = name_clean
        else:
            profile.full_name = None
            if reason:
                profile.diagnostics["full_name_reason"] = reason

        # Title
        title_clean, reason = validate_title(profile.title)
        if title_clean:
            profile.title = title_clean
        else:
            profile.title = None
            if reason:
                profile.diagnostics["title_reason"] = reason

        # Offices (US-only filter)
        offices_clean, reason = validate_offices(profile.offices)
        if offices_clean:
            profile.offices = offices_clean
        else:
            profile.offices = []
            if reason:
                profile.diagnostics["offices_reason"] = reason

        # Department
        dept_clean, reason = validate_department(profile.department)
        if dept_clean:
            profile.department = dept_clean
        else:
            profile.department = []
            if reason:
                profile.diagnostics["department_reason"] = reason

        # Practice areas
        pa_clean, reason = validate_practice_areas(profile.practice_areas)
        if pa_clean:
            profile.practice_areas = pa_clean
        else:
            profile.practice_areas = []
            if reason:
                profile.diagnostics["practice_areas_reason"] = reason

        # Industries — sentinel applied inside validator
        ind_clean, reason = validate_industries(profile.industries)
        profile.industries = ind_clean
        if reason:
            profile.diagnostics["industries_reason"] = reason

        # Bar admissions
        bar_clean, reason = validate_bar_admissions(profile.bar_admissions)
        if bar_clean:
            profile.bar_admissions = bar_clean
        else:
            profile.bar_admissions = []
            if reason:
                profile.diagnostics["bar_admissions_reason"] = reason

        # Education — sentinel applied inside validator
        edu_clean, reason = validate_education(cast(list[Any], profile.education))
        profile.education = cast(list[EducationRecord], edu_clean)
        if reason:
            profile.diagnostics["education_reason"] = reason


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def enrich_profile(
    url: str,
    html: str,
    firm: str,
    *,
    session: requests.Session | None = None,
    logger: DebugLogger | None = None,
    enable_playwright: bool = True,
    force_playwright: bool = False,
    timeout: int = 10,
) -> AttorneyProfile:
    """Convenience wrapper: enrich one attorney profile.

    Args:
        url:    Profile URL
        html:   Raw HTML (empty string → triggers Playwright fetch)
        firm:   Firm name
        session: Optional requests.Session to reuse
        logger: Optional DebugLogger instance
        enable_playwright: Allow Playwright escalation
        force_playwright:  Always use Playwright
        timeout: HTTP request timeout in seconds

    Returns:
        AttorneyProfile with extraction_status set.
    """
    enricher = ProfileEnricher(
        session=session,
        logger=logger,
        enable_playwright=enable_playwright,
        timeout=timeout,
    )
    return enricher.enrich(url, html, firm, force_playwright=force_playwright)


# ---------------------------------------------------------------------------
# JSON-LD extraction (STAGE 1)
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str) -> dict[str, Any] | None:
    """Extract Person-typed JSON-LD block, returning first match."""
    try:
        blocks = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        for block in blocks:
            try:
                data = json.loads(block)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") in (
                        "Person", "http://schema.org/Person"
                    ):
                        return item
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return None


def _merge_json_ld(profile: AttorneyProfile, data: dict[str, Any]) -> None:
    """Merge JSON-LD Person fields into profile (skip already-populated fields)."""
    if not profile.full_name:
        name = data.get("name", "")
        if name and _looks_like_name(name):
            profile.full_name = name.strip()

    if not profile.title:
        job_title = data.get("jobTitle", "")
        if job_title:
            profile.title = job_title.strip()

    # Offices
    for loc_key in ("workLocation", "address"):
        loc = data.get(loc_key)
        if isinstance(loc, dict):
            city = loc.get("name") or loc.get("addressLocality", "")
            if city and city not in profile.offices:
                profile.offices.append(city.strip())
        elif isinstance(loc, str) and loc and loc not in profile.offices:
            profile.offices.append(loc.strip())

    # Practice areas (knowsAbout)
    knows = data.get("knowsAbout", [])
    if isinstance(knows, str):
        knows = [knows]
    for item in (knows if isinstance(knows, list) else []):
        if item and item not in profile.practice_areas:
            profile.practice_areas.append(str(item).strip())

    # Education (alumniOf)
    alumni = data.get("alumniOf", [])
    if isinstance(alumni, dict):
        alumni = [alumni]
    elif isinstance(alumni, str):
        alumni = [{"name": alumni}]
    for school_data in (alumni if isinstance(alumni, list) else []):
        school_name = (
            school_data.get("name", "") if isinstance(school_data, dict) else str(school_data)
        )
        if school_name:
            _add_edu_if_new(profile, EducationRecord(school=school_name.strip()))


# ---------------------------------------------------------------------------
# Embedded state extraction (STAGE 2)
# ---------------------------------------------------------------------------

def _extract_embedded_state(html: str) -> dict[str, Any] | None:
    """Extract React/Next.js embedded state objects."""
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'window\.__APOLLO_STATE__\s*=\s*({.*?});',
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    ]
    for pattern in patterns:
        try:
            for match in re.findall(pattern, html, re.DOTALL):
                try:
                    data = json.loads(match)
                    result = _find_attorney_data(data)
                    if result:
                        return result
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue
    return None


def _merge_embedded_state(profile: AttorneyProfile, data: dict[str, Any]) -> None:
    """Merge common embedded state keys into profile."""
    if not profile.full_name:
        for key in ("name", "fullName", "displayName"):
            if key in data:
                name = str(data[key])
                if _looks_like_name(name):
                    profile.full_name = name.strip()
                    break

    if not profile.title:
        for key in ("title", "position", "role", "jobTitle"):
            if key in data:
                profile.title = str(data[key]).strip()
                break

    for key in ("practices", "practiceAreas", "expertise", "specialties"):
        if key in data and not profile.practice_areas:
            items = data[key]
            if isinstance(items, list):
                for p in items:
                    if p:
                        profile.practice_areas.append(str(p).strip())
            elif isinstance(items, str) and items:
                profile.practice_areas.append(items.strip())
            break


# ---------------------------------------------------------------------------
# Captured JSON merge (STAGE 3 — Playwright intercept)
# ---------------------------------------------------------------------------

def _merge_captured_json(profile: AttorneyProfile, payloads: list[dict[str, Any]]) -> None:
    """Merge attorney data from Playwright-intercepted JSON payloads."""
    for payload in payloads:
        data = payload.get("data", {})
        attorney = _find_attorney_data(data)
        if not attorney:
            continue

        if not profile.full_name:
            for key in ("name", "fullName", "displayName"):
                if key in attorney:
                    name = str(attorney[key])
                    if _looks_like_name(name):
                        profile.full_name = name.strip()
                        break

        if not profile.title:
            for key in ("title", "position", "jobTitle"):
                if key in attorney:
                    profile.title = str(attorney[key]).strip()
                    break

        _merge_list_field(
            profile.offices, attorney,
            ("office", "location", "officeLocation"),
        )
        _merge_list_field(
            profile.practice_areas, attorney,
            ("practiceAreas", "practices", "expertise"),
        )
        _merge_list_field(
            profile.industries, attorney,
            ("industries", "sectors"),
        )
        _merge_list_field(
            profile.bar_admissions, attorney,
            ("barAdmissions", "admissions", "licenses"),
        )

        if not profile.education:
            edu = attorney.get("education") or attorney.get("schools")
            if isinstance(edu, list):
                for e in edu:
                    if isinstance(e, dict):
                        year_val = e.get("year")
                        year_num = year_val if isinstance(year_val, int) else None
                        if year_num is None and year_val is not None:
                            try:
                                year_num = int(str(year_val))
                            except (ValueError, TypeError):
                                pass
                        _add_edu_if_new(profile, EducationRecord(
                            degree=str(e["degree"]) if e.get("degree") else None,
                            school=str(e["school"]) if e.get("school") else None,
                            year=year_num,
                        ))


# ---------------------------------------------------------------------------
# Heading-based section map extraction (STAGE 4)
# ---------------------------------------------------------------------------

def _extract_from_section_map(
    profile: AttorneyProfile,
    section_map: dict[str, list[str]],
    url: str,
    html: str,
) -> None:
    """Fill profile fields from the heading-based section map."""

    # --- Name (from hero h1) ---
    if not profile.full_name:
        for candidate in find_section(section_map, "name"):
            if _looks_like_name(candidate):
                profile.full_name = candidate
                break

    # --- Title ---
    if not profile.title:
        # Check section_map["title"] from og:title parsing
        for candidate in find_section(section_map, "title"):
            if candidate:
                profile.title = candidate
                break

        # Fallback: look for known title keywords just below the h1
        if not profile.title:
            profile.title = _extract_title_proximity(html)

    # --- Offices ---
    for text in find_section(section_map, "offices"):
        if text and text not in profile.offices:
            profile.offices.append(text)

    # --- Departments ---
    for text in find_section(section_map, "departments"):
        if text and text not in profile.department:
            profile.department.append(text)

    # --- Practice Areas ---
    for text in find_section(section_map, "practice_areas"):
        if text and text not in profile.practice_areas:
            profile.practice_areas.append(text)

    # --- Industries ---
    for text in find_section(section_map, "industries"):
        if text and text not in profile.industries:
            profile.industries.append(text)

    # --- Bar Admissions ---
    raw_bars = find_section(section_map, "bar_admissions")
    parsed_states = parse_bar_admissions_text_blocks(raw_bars)
    for state in parsed_states:
        if state not in profile.bar_admissions:
            profile.bar_admissions.append(state)

    # --- Education ---
    raw_edu = find_section(section_map, "education")
    new_records = parse_education_text_blocks(raw_edu)
    for rec in new_records:
        _add_edu_if_new(profile, cast(EducationRecord, cast(object, rec)))


# ---------------------------------------------------------------------------
# Proximity / keyword fallback extraction (STAGE 5)
# ---------------------------------------------------------------------------

def _proximity_fallback(profile: AttorneyProfile, html: str) -> None:
    """Keyword proximity search for fields still missing after section parsing.

    Scans the full page text for known labels and extracts adjacent content.
    Only fills fields that are still empty.
    """
    if not BS4_AVAILABLE:
        return

    assert BeautifulSoup is not None  # guarded above by BS4_AVAILABLE check
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(separator="\n")

    # --- Name fallback: first h1 ---
    if not profile.full_name:
        h1 = soup.find("h1")
        if h1:
            candidate = h1.get_text(strip=True)
            if _looks_like_name(candidate):
                profile.full_name = candidate

    # --- Title fallback: proximity to known title keywords ---
    if not profile.title:
        title = _extract_title_proximity(html)
        if title:
            profile.title = title

    # --- Practice areas fallback ---
    if not profile.practice_areas:
        items = _proximity_list_items(
            soup, ["practice", "expertise", "specialt", "service"]
        )
        profile.practice_areas.extend(items)

    # --- Bar admissions fallback ---
    if not profile.bar_admissions:
        items = _proximity_list_items(soup, ["bar admission", "admitted", "bar"])
        states = parse_bar_admissions_text_blocks(items)
        profile.bar_admissions.extend(states)
        # Also try full-text state scan as last resort
        if not profile.bar_admissions:
            from validators import _extract_states_from_text
            states_all = _extract_states_from_text(full_text)
            profile.bar_admissions.extend(states_all)

    # --- Education fallback ---
    if not profile.education:
        items = _proximity_list_items(soup, ["education", "academic"])
        records = parse_education_text_blocks(items)
        for rec in records:
            _add_edu_if_new(profile, cast(EducationRecord, cast(object, rec)))


def _proximity_list_items(soup: BeautifulSoup, keywords: list[str]) -> list[str]:
    """Find content in <li>, <p>, <a>, <dd> elements following a heading
    whose text contains any of the given keywords.

    Returns deduplicated text blocks.
    """
    results: list[str] = []
    seen: set[str] = set()

    for header in soup.find_all(["h2", "h3", "h4", "h5", "strong", "b", "dt"]):
        header_text = header.get_text(strip=True).lower()
        if not any(kw in header_text for kw in keywords):
            continue

        for sibling in header.find_all_next():
            if isinstance(sibling, Tag) and sibling.name in ("h2", "h3", "h4", "h5") and sibling is not header:
                break
            if isinstance(sibling, Tag) and sibling.name in ("li", "a", "dd", "p"):
                text = re.sub(r"\s+", " ", sibling.get_text()).strip()
                if text and 3 <= len(text) <= 300 and text not in seen:
                    seen.add(text)
                    results.append(text)

    return results


def _extract_title_proximity(html: str) -> str | None:
    """Extract attorney title via keyword proximity search in raw HTML.

    Scans for known title keywords ("Partner", "Associate", etc.) appearing
    in short text nodes immediately after the name area.

    Returns first plausible match or None.
    """
    title_keywords = [
        "Partner", "Associate", "Counsel", "Of Counsel",
        "Senior Associate", "Managing Partner", "Senior Partner",
        "Member", "Shareholder", "Principal", "Special Counsel",
        "Senior Counsel", "Junior Associate",
    ]

    if not BS4_AVAILABLE:
        # Regex fallback
        for kw in title_keywords:
            match = re.search(
                rf'<(?:span|div|p|td)[^>]*>[^<]*{re.escape(kw)}[^<]*</(?:span|div|p|td)>',
                html,
                re.IGNORECASE,
            )
            if match:
                text = re.sub(r"<[^>]+>", "", match.group()).strip()
                if text and len(text) <= 120:
                    return text
        return None

    assert BeautifulSoup is not None  # guarded above by BS4_AVAILABLE check
    soup = BeautifulSoup(html, "html.parser")
    for kw in title_keywords:
        elem = soup.find(string=re.compile(rf'\b{re.escape(kw)}\b', re.IGNORECASE))
        if elem:
            parent = elem.parent
            if parent:
                text = parent.get_text(strip=True)
                if text and len(text) <= 120:
                    return text

    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _looks_like_name(text: str) -> bool:
    """Return True if text passes the strict person-name heuristic."""
    if not text:
        return False
    text = text.strip()
    if len(text) < 4 or len(text) > 100:
        return False
    if any(ch in text for ch in ["_", "#", "{", "}"]):
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if text.lower() in _HEADER_TERMS:
        return False
    return bool(_VALID_NAME_RE.match(text))


def _add_edu_if_new(profile: AttorneyProfile, rec: EducationRecord) -> None:
    """Append an EducationRecord only if no identical (school, degree) pair exists."""
    for existing in profile.education:
        if existing.school == rec.school and existing.degree == rec.degree:
            return
    profile.education.append(rec)


def _merge_list_field(
    target: list[str],
    data: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    """Merge a list-or-str field from data dict into target list."""
    for key in keys:
        if key in data and not target:
            val = data[key]
            if isinstance(val, list):
                target.extend(str(v).strip() for v in val if v)
            elif isinstance(val, str) and val:
                target.append(val.strip())
            break


def _find_attorney_data(obj: object, depth: int = 0) -> dict[str, Any] | None:
    """Recursively search nested JSON for a dict that looks like attorney data."""
    if depth > 6:
        return None

    if isinstance(obj, dict):
        attorney_indicators = {
            "name", "fullName", "title", "position",
            "practiceAreas", "barAdmissions", "education",
        }
        keys = set(obj.keys())
        if len(keys & attorney_indicators) >= 2:
            return obj
        for value in obj.values():
            result = _find_attorney_data(value, depth + 1)
            if result:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = _find_attorney_data(item, depth + 1)
            if result:
                return result

    return None


def _all_field_names() -> list[str]:
    return [
        "full_name", "title", "offices", "department",
        "practice_areas", "industries", "bar_admissions", "education",
    ]
