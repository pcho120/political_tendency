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

_VALID_NAME_RE = re.compile(
    r"^"
    r"(?:Dr\.?\s+|Prof\.?\s+|Hon\.?\s+)?"     # optional honorific
    r"(?:[A-Z]\.?\s+)?"                         # optional leading initial: "J. " or "J "
    r"[A-ZÀ-Ö][a-zA-ZÀ-öø-ÿ\u0100-\u024F\-']+"  # first name (Unicode Latin)
    r"(?:"
        r"\s+"
        r"(?:[A-ZÀ-Ö]\.?|[A-ZÀ-Ö][a-zA-ZÀ-öø-ÿ\u0100-\u024F\.\-']+|[a-z]{1,4})"
    r")+"
    r"$"
)
_HEADER_TERMS: frozenset[str] = frozenset({
    "last name", "first name", "firm name", "attorney", "name", "title",
    "lawyer", "partner", "associate", "counsel", "full name", "contact",
    "practice areas", "practice area", "professionals", "our people",
    "people", "attorneys", "lawyers", "team", "biography", "profile",
    "who are you looking for", "search results", "search professionals",
    "meet our team", "our attorneys", "our lawyers", "legal team",
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

        # --- STAGE 0: CSS-class-based extraction (highest fidelity, site-specific) ---
        if BS4_AVAILABLE:
            _extract_from_css_classes(profile, html, url=url)

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
        title_clean, reason = validate_title(profile.title, firm_name=profile.firm or "")
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
# CSS-class-based extraction (STAGE 0)
# Handles structured markup from major law firm site patterns.
# Only fills fields that are still empty on the profile.
# ---------------------------------------------------------------------------

def _extract_from_css_classes(profile: AttorneyProfile, html: str, url: str = "") -> None:
    """Extract profile fields using known CSS class patterns.

    Covers common patterns across AmLaw200 firms:
    - Kirkland-style:   profile-heading__*, listing-services__*, normalized-rte-list
    - Skadden-style:    profile-header-name/position, offices-related-office
    - Baker Botts:      bio-card-name/title, bio-contract-geo
    - Cooley:           h1[class*=name], div.eyebrow, div.locations
    - Paul Hastings:    qtph-profprofile-name/title/primaryoffice-txt
    - Milbank:          attorney-header__name, attorney-office
    - Paul Weiss:       div.pageTitle > h1, div.location-block-1
    - Generic:          h1[class*=name], [class*=position], [class*=location]
    - Cahill Gordon:    div.bio-contact > p.position  (URL-scoped: cahill.com)
    - Troutman Pepper:  div.general > h1 ~ p          (URL-scoped: troutman.com)
    - Susman Godfrey:   section.page-header h1 ~ text  (URL-scoped: susmangodfrey.com)
    - Sullivan & Cromwell: div.bio-hero-panel p[class*=BioHeroPanel_subtitle]  (URL-scoped: sullcrom.com)
    - Weil Gotshal:     header.bio-bar-header span.h3 > span  (URL-scoped: weil.com)
    - Saul Ewing:       se-profile-hero[main-title][primary-office-location]  (URL-scoped: saul.com)
    """
    if not BS4_AVAILABLE:
        return

    assert BeautifulSoup is not None  # guarded by BS4_AVAILABLE
    soup = BeautifulSoup(html, "lxml")

    # ---- Name ----
    if not profile.full_name:
        for selector_class in [
            "profile-heading__name-label",   # Kirkland
            "profile-heading__name",
            "profile-header-name",           # Skadden
            "bio-card-name",                 # Baker Botts
            "attorney-header__name",         # Milbank
            "qtph-profprofile-name-txt",     # Paul Hastings
            "attorney-name",
            "bio-name",
            "lawyer-name",
            "professional-name",
        ]:
            el = soup.find(class_=selector_class)
            if el:
                raw = el.get_text(separator=" ", strip=True)
                # Strip professional suffix like ", P.C." BEFORE _clean_name_text
                # (cleaning adds spaces after periods which breaks suffix detection)
                raw = re.sub(r",\s*P\.?\s*C\.?\s*$", "", raw).strip()
                raw = re.sub(r",\s*(?:Jr\.|Sr\.|III|II|IV)\s*$", "", raw,
                             flags=re.IGNORECASE).strip()
                text = _clean_name_text(raw)
                name_clean = text
                if _looks_like_name(name_clean) or (name_clean and 3 < len(name_clean) < 80
                        and not any(ch.isdigit() for ch in name_clean)
                        and name_clean.lower() not in _HEADER_TERMS):
                    profile.full_name = name_clean
                    break

    # Cooley: <h1 class="name h2">
    if not profile.full_name:
        for h1 in soup.find_all("h1"):
            classes = h1.get("class") or []
            if "name" in classes:
                raw = h1.get_text(separator=" ", strip=True)
                raw = re.sub(r",\s*P\.?\s*C\.?\s*$", "", raw).strip()
                text = _clean_name_text(raw)
                if _looks_like_name(text):
                    profile.full_name = text
                    break

    # Paul Weiss: <div class="pageTitle"><h1>First <br/> Last</h1></div>
    if not profile.full_name:
        page_title_div = soup.find(class_="pageTitle")
        if page_title_div:
            h1 = page_title_div.find("h1")
            if h1:
                text = _clean_name_text(h1.get_text(separator=" ", strip=True))
                if _looks_like_name(text):
                    profile.full_name = text

    # Generic fallback: any h1 on the page (works for Latham-style pages with plain <h1>)
    if not profile.full_name:
        for h1 in soup.find_all("h1"):
            raw = h1.get_text(separator=" ", strip=True)
            raw = re.sub(r",\s*P\.?\s*C\.?\s*$", "", raw).strip()
            text = _clean_name_text(raw)
            if _looks_like_name(text):
                profile.full_name = text
                break

    # ---- Title / Position ----
    if not profile.title:
        for selector_class in [
            "profile-heading__position",     # Kirkland
            "profile-header-position",       # Skadden (may contain "Title, Department")
            "bio-card-title",                # Baker Botts
            "qtph-profprofile-title-txt",    # Paul Hastings (may contain "Title, Department")
            "attorney-title",                # generic (Latham-style)
            "bio-title",
            "bio-hero-title",               # generic hero variant
            "profile-title",                # generic profile variant
            "person-title",                 # generic person variant
            "lawyer-title",
            "lawyer-position",              # generic lawyer variant
            "professional-title",
            "staff-title",                  # generic staff variant
            "attorney-position",
        ]:
            el = soup.find(class_=selector_class)
            if el:
                raw = el.get_text(strip=True)
                if raw and len(raw) < 120:
                    raw = _clean_title_candidate(raw, profile.full_name) or ""
                if raw:
                    # Some firms encode "Title, Department" in one field — split on first comma
                    parts = [p.strip() for p in raw.split(",", 1)]
                    profile.title = parts[0]
                    # If we got a department as the second part, store it
                    if not profile.department and len(parts) > 1 and parts[1]:
                        profile.department.append(parts[1])
                    break

        # Cooley: <div class="eyebrow -vert-line-sandwich">Partner</div>
        # Take only the first eyebrow that is not an email link
        if not profile.title:
            for el in soup.find_all(class_="eyebrow"):
                text = el.get_text(strip=True)
                text = _clean_title_candidate(text, profile.full_name) or ""
                if text and "@" not in text and len(text) < 80:
                    profile.title = text
                    break

        # Cahill Gordon: <div class="bio-contact"><h1>Name</h1><p class="position">Associate</p>
        if not profile.title and "cahill.com" in url:
            bio = soup.find(class_="bio-contact")
            if bio:
                pos = bio.find("p", class_="position")
                if pos:
                    t = pos.get_text(strip=True)
                    t = _clean_title_candidate(t, profile.full_name) or ""
                    if t and len(t) < 100:
                        profile.title = t

        # Troutman Pepper: <div class="general"><h1>Name</h1><p>Associate</p>
        if not profile.title and "troutman.com" in url:
            general = soup.find(class_="general")
            if general:
                h1 = general.find("h1")
                if h1:
                    nxt = h1.find_next_sibling("p")
                    if nxt:
                        t = nxt.get_text(strip=True)
                        t = _clean_title_candidate(t, profile.full_name) or ""
                        if t and len(t) < 100:
                            profile.title = t

        # Susman Godfrey: <section class="page-header"><h1>Name</h1>...sibling "Associate"
        if not profile.title and "susmangodfrey.com" in url:
            ph = soup.find("section", class_="page-header")
            if ph:
                h1 = ph.find("h1")
                if h1:
                    for sib in h1.next_siblings:
                        t = sib.get_text(strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                        t = _clean_title_candidate(t, profile.full_name) or ""
                        if t and len(t) < 100 and "@" not in t and not t[0].isdigit():
                            profile.title = t
                            break

        # Sullivan & Cromwell: <div class="bio-hero-panel">...<p class="BioHeroPanel_subtitle__HASH">Associate</p>
        if not profile.title and "sullcrom.com" in url:
            hero = soup.find(class_="bio-hero-panel")
            if hero:
                sub = hero.find(
                    lambda tag: tag.name == "p"
                    and any("BioHeroPanel_subtitle" in c for c in (tag.get("class") or []))
                )
                if sub:
                    t = sub.get_text(strip=True)
                    t = _clean_title_candidate(t, profile.full_name) or ""
                    if t and len(t) < 100:
                        profile.title = t

        # Saul Ewing: <se-profile-hero main-title="Partner" primary-office-location="Harrisburg">
        if "saul.com" in url:
            hero_el = soup.find("se-profile-hero")
            if hero_el:
                if not profile.title:
                    for attr in ("main-title", "title", "role"):
                        val = _attr_text(hero_el.get(attr))
                        val = _clean_title_candidate(val, profile.full_name) or ""
                        if val and len(val) < 100:
                            profile.title = val
                            break
                if not profile.offices:
                    for attr in ("primary-office-location", "office", "location"):
                        val = _attr_text(hero_el.get(attr))
                        if val and len(val) < 60:
                            profile.offices.append(val)
                            break

        # Generic regex-based class pattern fallback for title
        if not profile.title:
            _title_class_re = re.compile(r'title|position|role', re.I)
            _title_skip_tags = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                          'body', 'html', 'head', 'nav', 'footer'})
            title_el = soup.find(True, class_=_title_class_re)
            if title_el and title_el.name not in _title_skip_tags:
                text = title_el.get_text(strip=True)
                text = _clean_title_candidate(text, profile.full_name) or ""
                if (text and 2 < len(text) < 80
                        and text.lower() not in _HEADER_TERMS
                        and "@" not in text
                        and not text[0].isdigit()):
                    profile.title = text

    # ---- Offices ----
    if not profile.offices:
        # Kirkland: <a class="profile-heading__location-link">Chicago</a>
        # Cooley: <div class="locations">San Francisco</div>
        # Baker Botts: <div class="bio-contract-geo">Austin</div>
        # Milbank: <div class="attorney-office ...">New York</div>  (more specific than attorney-office-container)
        for selector_class in [
            "profile-heading__location-link",
            "bio-contract-geo",              # Baker Botts
            "attorney-office",               # Milbank (also matches attorney-office-container — handled below)
            "bio-office",
            "bio-hero-office",               # generic hero variant
            "profile-office",                # generic profile variant
            "person-office",                 # generic person variant
            "lawyer-office",
            "lawyer-location",               # generic lawyer variant
            "staff-office",                  # generic staff variant
            "office-name",
            "vcard-office",                  # Covington
            "location-name",                 # K&L Gates
            "office-link",                   # Greenberg Traurig
            "office",                        # Baker McKenzie (<div class="office">)
        ]:
            els = soup.find_all(class_=selector_class)
            if els:
                for el in els:
                    # Skip containers (Milbank uses attorney-office-container for the full block)
                    el_classes = set(el.get("class") or [])
                    if "container" in " ".join(el_classes).lower():
                        continue
                    text = el.get_text(strip=True)
                    if text and text not in profile.offices:
                        profile.offices.append(text)
                if profile.offices:
                    break  # stop after first matching class pattern

        # Cooley: <div class="locations">San Francisco</div>
        if not profile.offices:
            el = soup.find(class_="locations")
            if el:
                # Weil has a <li class="locations"> that is the full nav — skip if too long
                text = el.get_text(strip=True)
                if len(text) < 100:
                    profile.offices.append(text)

        # Paul Hastings: <div class="qtph-profprofile-primaryoffice-txt">San Francisco...</div>
        if not profile.offices:
            el = soup.find(class_="qtph-profprofile-primaryoffice-txt")
            if el:
                first_line = el.get_text(separator="\n", strip=True).split("\n")[0].strip()
                if first_line and len(first_line) < 80:
                    profile.offices.append(first_line)

        # Skadden: <div class="offices-related-office">Abu Dhabi<br>T:...</div>
        if not profile.offices:
            for el in soup.find_all(class_="offices-related-office"):
                first_line = el.get_text(separator="\n", strip=True).split("\n")[0].strip()
                if first_line and first_line not in profile.offices:
                    profile.offices.append(first_line)

        # Paul Weiss: <div class="location-block-1">Washington, DC<br>...</div>
        if not profile.offices:
            el = soup.find(class_="location-block-1") or soup.find(class_="location-block")
            if el:
                first_line = el.get_text(separator="\n", strip=True).split("\n")[0].strip()
                if first_line and len(first_line) < 80:
                    profile.offices.append(first_line)

        # MoFo: <div class="profile-hero__details--title-location">
        #   <span>Partner</span><span>|</span><span>Austin</span><span>•</span><span>Palo Alto</span>
        # Spans after the bullet divider are offices; skip spans that look like titles/bullets.
        if not profile.offices:
            hero_loc = soup.find(class_="profile-hero__details--title-location")
            if hero_loc:
                spans = [s.get_text(strip=True) for s in hero_loc.find_all("span")]
                after_bullet = False
                for span in spans:
                    if span in ("|", "•", "·", "/"):
                        after_bullet = True
                        continue
                    if after_bullet and span and len(span) < 60:
                        if span not in profile.offices:
                            profile.offices.append(span)

        # Gibson Dunn: <div class="contact-details"> contains <a href="/office/city-name">City</a>
        # Husch Blackwell: <div class="bioInfoWrap__line office1"> contains <a href="/offices/City_ST">City</a>
        # Troutman Pepper: <div class="info phone"> contains <a href="/office/city/">City</a>
        # Generic: any <a> whose href matches /office(s)/ or /locations?/ pattern, not in nav/footer
        if not profile.offices:
            _office_href_pat = re.compile(r"/offices?/|/locations?/", re.IGNORECASE)
            _noise_tag_names = {"footer", "nav", "header"}
            _noise_exact_tokens = {"footer", "nav", "menu", "sitemap", "navigation"}
            _noise_prefix_tokens = ("footer", "nav-", "navigation-", "site-nav", "site-footer")
            for a in soup.find_all("a", href=True):
                href = _attr_text(a.get("href", ""))
                if not _office_href_pat.search(href):
                    continue
                text = a.get_text(strip=True)
                if not text or len(text) > 60:
                    continue
                # Skip pure nav/menu/footer links (e.g. "Locations", "Offices", "Our Offices")
                if text.lower() in ("locations", "offices", "our offices", "all offices",
                                    "all locations", "office locations", "find an office"):
                    continue
                # Walk up to see if we're inside a footer/nav/menu element
                in_noise = False
                for parent in a.parents:
                    tag_name = getattr(parent, "name", None)
                    if tag_name in _noise_tag_names:
                        in_noise = True
                        break
                    # Check class tokens using exact match or prefix (avoid substring false positives)
                    for cls_tok in (parent.get("class") or []):
                        tok_lower = cls_tok.lower()
                        if tok_lower in _noise_exact_tokens:
                            in_noise = True
                            break
                        if any(tok_lower.startswith(p) for p in _noise_prefix_tokens):
                            in_noise = True
                            break
                    if in_noise:
                        break
                if in_noise:
                    continue
                if text not in profile.offices:
                    profile.offices.append(text)

        # White & Case: hero-title container → <div class="...fs-5...">Counsel, Hamburg</div>
        # The div text is "Title, Office" or just "Title" — split on last comma
        if not profile.offices or not profile.title:
            hero_title = soup.find(class_="hero-title")
            if hero_title:
                for div in hero_title.find_all("div"):
                    cls = " ".join(div.get("class") or [])
                    if "fs-5" in cls:
                        raw = div.get_text(strip=True)
                        if raw and len(raw) < 100:
                            # Split "Title, City" on last comma
                            comma_idx = raw.rfind(",")
                            if comma_idx > 0:
                                _wc_title = raw[:comma_idx].strip()
                                _wc_office = raw[comma_idx + 1:].strip()
                            else:
                                _wc_title = raw.strip()
                                _wc_office = ""
                            _wc_title = _clean_title_candidate(_wc_title, profile.full_name) or ""
                            if not profile.title and _wc_title:
                                profile.title = _wc_title
                            if not profile.offices and _wc_office:
                                profile.offices.append(_wc_office)
                        break

        # Weil Gotshal: <header class="bio-bar-header"><span class="h3">Title<span>City</span></span>
        if not profile.offices and "weil.com" in url:
            bbh = soup.find("header", class_="bio-bar-header")
            if bbh:
                h3_span = bbh.find("span", class_="h3")
                if h3_span:
                    for span in h3_span.find_all("span"):
                        if not span.get("class"):
                            city = span.get_text(strip=True)
                            if city and len(city) < 60 and city not in profile.offices:
                                profile.offices.append(city)

        # Sullivan & Cromwell: <div class="bio-loc"><p class="sc-font-secondary ...">New York</p>
        if not profile.offices and "sullcrom.com" in url:
            bio_loc = soup.find(class_="bio-loc")
            if bio_loc:
                p = bio_loc.find("p")
                if p:
                    city = p.get_text(strip=True)
                    if city and len(city) < 60 and city not in profile.offices:
                        profile.offices.append(city)

        # Generic regex-based class pattern fallback for office
        if not profile.offices:
            _office_class_re = re.compile(r'office|location|city', re.I)
            _office_skip_tags = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                           'body', 'html', 'head', 'nav', 'footer'})
            office_el = soup.find(True, class_=_office_class_re)
            if office_el and office_el.name not in _office_skip_tags:
                # Skip containers (e.g. attorney-office-container) and elements with too many children
                el_classes_str = " ".join(office_el.get("class") or []).lower()
                if "container" not in el_classes_str and "list" not in el_classes_str:
                    text = office_el.get_text(strip=True)
                    if (text and 2 < len(text) < 80
                            and "@" not in text
                            and not re.match(r'^\+?\d[\d\s\-().]+$', text)):  # skip phone numbers
                        profile.offices.append(text)

    # ---- Practice Areas ----
    if not profile.practice_areas:
        # Kirkland: <ul class="listing-services__items"><li class="listing-services__item">
        # This is the expanded list (preferred over the collapsed specialty tags)
        listing_ul = soup.find(class_="listing-services__items")
        if listing_ul:
            for li in listing_ul.find_all("li", class_="listing-services__item"):
                text = li.get_text(strip=True)
                # Skip section headings like "Transactional", "Litigation" which appear
                # as h3.listing-services__heading — those go to department
                if text and text not in profile.practice_areas:
                    profile.practice_areas.append(text)
        else:
            # Fallback: collapsed specialty links
            for el in soup.find_all(class_="profile-heading__specialty"):
                text = el.get_text(strip=True)
                if text and text not in profile.practice_areas:
                    profile.practice_areas.append(text)

    if not profile.practice_areas:
        for block in soup.find_all(True, class_=re.compile(r'practice|expertise|industry|service', re.I)):
            for candidate in _extract_block_items(block, allow_links=True):
                if candidate not in profile.practice_areas:
                    profile.practice_areas.append(candidate)

    # ---- Department (Kirkland group heading: "Transactional", "Litigation", etc.) ----
    if not profile.department:
        # Kirkland: <h3 class="listing-services__heading">Transactional</h3>
        for selector_class in ["listing-services__heading", "practice-group__heading"]:
            els = soup.find_all(class_=selector_class)
            if els:
                for el in els:
                    text = el.get_text(strip=True)
                    # Filter out generic headings
                    if text and text.lower() not in {"practices", "services",
                            "expertise", "industries", "sectors"} and text not in profile.department:
                        profile.department.append(text)
                if profile.department:
                    break

    # ---- Department (generic CSS patterns) ----
    if not profile.department:
        _dept_generic_headings = {"practices", "services", "expertise",
                                  "industries", "sectors", "overview"}
        # Generic: elements with class names containing 'department', 'practice-group', 'dept'
        for el in soup.find_all(True, class_=re.compile(
                r'department|practice[-_]?group|dept', re.I)):
            text = el.get_text(strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if (text and 2 < len(text) < 80
                    and text.lower() not in _dept_generic_headings
                    and text not in profile.department):
                profile.department.append(text)
        # Also check data attributes: data-department, data-dept
        if not profile.department:
            for attr_name in ("data-department", "data-dept"):
                for el in soup.find_all(True, attrs={attr_name: True}):
                    val = _attr_text(el.get(attr_name))
                    if val and 2 < len(val) < 80 and val not in profile.department:
                        profile.department.append(val)
                if profile.department:
                    break

    # ---- Education ----
    if not profile.education:
        _extract_normalized_rte_list_education(profile, soup)

    if not profile.education:
        edu_blocks: list[str] = []
        for block in soup.find_all(True, class_=re.compile(r'education|academic|credential', re.I)):
            edu_blocks.extend(_extract_block_items(block))
        for rec in parse_education_text_blocks(edu_blocks):
            _add_edu_if_new(profile, cast(EducationRecord, rec))

    # ---- Bar Admissions ----
    if not profile.bar_admissions:
        _extract_normalized_rte_list_bar(profile, soup)

    if not profile.bar_admissions:
        bar_blocks: list[str] = []
        for block in soup.find_all(True, class_=re.compile(r'admission|bar', re.I)):
            bar_blocks.extend(_extract_block_items(block, allow_links=True))
        for state in parse_bar_admissions_text_blocks(bar_blocks):
            if state not in profile.bar_admissions:
                profile.bar_admissions.append(state)


def _extract_block_items(block: Tag, *, allow_links: bool = False) -> list[str]:
    """Extract short content items from a semantic content block."""
    results: list[str] = []
    seen: set[str] = set()
    if allow_links:
        link_texts = [
            re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            for node in block.find_all("a")
        ]
        for text in link_texts:
            if _is_semantic_block_item(text) and text not in seen:
                seen.add(text)
                results.append(text)
        if results:
            return results

    for node in block.find_all(["li", "p", "span", "div"]):
        if node.find(["li", "p", "div"], recursive=False):
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if _is_semantic_block_item(text) and text not in seen:
            seen.add(text)
            results.append(text)
    return results


def _is_semantic_block_item(text: str) -> bool:
    """Return True when text looks like a short profile field item."""
    if not text or len(text) > 120:
        return False
    lowered = text.lower()
    if lowered in _HEADER_TERMS:
        return False
    if lowered in {
        "practices & industries", "practices", "industries", "education",
        "bar admissions", "publications", "read more", "expand biography",
        "read more expand biography", "government experience", "clerkships",
        "view more",
    }:
        return False
    if "@" in text or text.startswith("Tel:"):
        return False
    if re.search(r"^\+?\d[\d\s().-]+$", text):
        return False
    if re.search(r"\b(avenue|street|road|suite|floor|plaza|parkway|drive|boulevard|lane|court)\b", lowered):
        return False
    if re.search(r"\b(admission|education|publication|biography|experience|represented|advises|focuses|named|ranked|recognized)\b", lowered):
        return False
    return True


def _extract_normalized_rte_list_education(
    profile: AttorneyProfile, soup: BeautifulSoup
) -> None:
    """Parse education from Kirkland-style normalized-rte-list__title sections.

    Structure:
        <h4 class="normalized-rte-list__title">Education</h4>
        <ul>
          <li>University of Illinois Chicago School of Law J.D. magna cum laude 2023 ...</li>
          <li>University of Illinois at Urbana-Champaign B.A., Political Science 2015</li>
        </ul>
    """
    for h4 in soup.find_all("h4", class_="normalized-rte-list__title"):
        heading_text = h4.get_text(strip=True).lower()
        if "education" not in heading_text:
            continue
        container = h4.find_parent()
        if not container:
            continue
        for li in container.find_all("li"):
            raw = li.get_text(" ", strip=True)
            # Parse degree, school, year from the combined li text
            rec = _parse_edu_li(raw)
            if rec:
                _add_edu_if_new(profile, rec)
        break  # only first Education section


def _parse_edu_li(text: str) -> EducationRecord | None:
    """Parse a single education list item into an EducationRecord.

    Example inputs:
      'University of Illinois Chicago School of Law J.D. magna cum laude 2023'
      'University of Illinois at Urbana-Champaign B.A., Political Science 2015'
      'Georgetown University Walsh School of Foreign Service B.S.F.S. International Politics 2017'
    """
    if not text or len(text) < 5:
        return None

    # Extract year (4-digit 18xx-20xx)
    year_match = re.search(r'\b((?:18|19|20)\d{2})\b', text)
    year = int(year_match.group(1)) if year_match else None

    # Extract degree abbreviation
    degree: str | None = None
    # Ordered from most specific to least
    degree_patterns = [
        (r'\bLL\.?M\.?\b', "LLM"),
        (r'\bJ\.?D\.?\b', "JD"),
        (r'\bM\.?B\.?A\.?\b', "MBA"),
        (r'\bPh\.?D\.?\b', "PhD"),
        (r'\bB\.?S\.?F\.?S\.?\b', "BSFS"),
        (r'\bB\.?S\.?\b', "BS"),
        (r'\bB\.?A\.?\b', "BA"),
        (r'\bM\.?S\.?\b', "MS"),
        (r'\bM\.?A\.?\b', "MA"),
    ]
    for pattern, label in degree_patterns:
        if re.search(pattern, text):
            degree = label
            break

    # Extract school name: everything before the degree token (or end of meaningful text)
    # Strip the degree abbreviation and anything after it for school extraction
    school_text = text
    if degree:
        # Remove the degree token and everything following it
        school_text = re.split(
            r'\b(?:LL\.?M|J\.?D|M\.?B\.?A|Ph\.?D|B\.?S\.?F\.?S|B\.?S|B\.?A|M\.?S|M\.?A)\.?\b',
            text, maxsplit=1
        )[0].strip().rstrip(",").strip()
    # Remove trailing year if still present
    school_text = re.sub(r'\s*\b(?:18|19|20)\d{2}\b.*$', '', school_text).strip()
    school = school_text if len(school_text) > 3 else None

    if not school and not degree:
        return None
    return EducationRecord(degree=degree, school=school, year=year)


def _extract_normalized_rte_list_bar(
    profile: AttorneyProfile, soup: BeautifulSoup
) -> None:
    """Parse bar admissions from Kirkland-style normalized-rte-list__title sections.

    Structure:
        <h4 class="normalized-rte-list__title">Admissions & Qualifications</h4>
        <ul>
          <li>2023 Illinois</li>
          <li>2021 New York</li>
        </ul>
    """
    from validators import _extract_states_from_text  # local import avoids circular

    for h4 in soup.find_all("h4", class_="normalized-rte-list__title"):
        heading_text = h4.get_text(strip=True).lower()
        if not any(kw in heading_text for kw in ("admission", "qualified", "bar", "licens")):
            continue
        container = h4.find_parent()
        if not container:
            continue
        for li in container.find_all("li"):
            raw = li.get_text(" ", strip=True)
            states = _extract_states_from_text(raw)
            for state in states:
                if state not in profile.bar_admissions:
                    profile.bar_admissions.append(state)
        break  # only first admissions section


# ---------------------------------------------------------------------------
# JSON-LD extraction (STAGE 1)
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str) -> dict[str, Any] | None:
    """Extract Person-typed JSON-LD block, returning first match.

    Handles:
    - Top-level @type: Person
    - Nested Person inside @type: ProfilePage (mainEntity)
    - Array of JSON-LD blocks
    """
    _PERSON_TYPES = ("Person", "http://schema.org/Person", "schema:Person")

    def _find_person(obj: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 3:
            return None
        if isinstance(obj, dict):
            if obj.get("@type") in _PERSON_TYPES:
                return obj
            # Check common nested keys
            for key in ("mainEntity", "about", "author", "creator"):
                nested = obj.get(key)
                if nested:
                    result = _find_person(nested, depth + 1)
                    if result:
                        return result
        elif isinstance(obj, list):
            for item in obj:
                result = _find_person(item, depth + 1)
                if result:
                    return result
        return None

    try:
        blocks = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        for block in blocks:
            try:
                data = json.loads(block)
                person = _find_person(data)
                if person:
                    return person
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

    for loc_key in ("workLocation", "address"):
        loc = data.get(loc_key)
        if isinstance(loc, dict):
            city = loc.get("name") or loc.get("addressLocality", "")
            if city and city not in profile.offices:
                profile.offices.append(city.strip())
        elif isinstance(loc, str) and loc and loc not in profile.offices:
            profile.offices.append(loc.strip())

    if not profile.department:
        for dept_key in ("department", "group", "practiceGroup", "division"):
            dept_val = data.get(dept_key)
            if isinstance(dept_val, str) and dept_val.strip():
                profile.department.append(dept_val.strip())
                break
            elif isinstance(dept_val, list):
                for d in dept_val:
                    if d and str(d).strip() and str(d).strip() not in profile.department:
                        profile.department.append(str(d).strip())
                if profile.department:
                    break
        if not profile.department:
            member_of = data.get("memberOf")
            if isinstance(member_of, dict):
                dept = member_of.get("department", "")
                if dept and isinstance(dept, str) and dept.strip():
                    profile.department.append(dept.strip())
            elif isinstance(member_of, list):
                for org in member_of:
                    if isinstance(org, dict):
                        dept = org.get("department", "")
                        if dept and isinstance(dept, str) and dept.strip():
                            if dept.strip() not in profile.department:
                                profile.department.append(dept.strip())

    if not profile.industries:
        for ind_key in ("industries", "industry", "sectors", "clientSectors", "focusIndustries"):
            ind_val = data.get(ind_key)
            if isinstance(ind_val, str) and ind_val.strip():
                profile.industries.append(ind_val.strip())
                break
            elif isinstance(ind_val, list):
                for i in ind_val:
                    if i and str(i).strip() and str(i).strip() not in profile.industries:
                        profile.industries.append(str(i).strip())
                if profile.industries:
                    break

    knows = data.get("knowsAbout", [])
    if isinstance(knows, str):
        knows = [knows]
    for item in (knows if isinstance(knows, list) else []):
        if item and item not in profile.practice_areas:
            profile.practice_areas.append(str(item).strip())

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

    if not profile.department:
        _merge_list_field(
            profile.department, data,
            ("department", "group", "practiceGroup", "division"),
        )

    if not profile.industries:
        _merge_list_field(
            profile.industries, data,
            ("industries", "industry", "sectors", "clientSectors"),
        )


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
            if not candidate:
                continue
            candidate_clean = _clean_title_candidate(candidate, profile.full_name)
            if candidate_clean:
                profile.title = candidate_clean
                break

        if not profile.title and BS4_AVAILABLE:
            assert BeautifulSoup is not None
            soup = BeautifulSoup(html, "lxml")
            detail_row = soup.find(class_=re.compile(r"\bdetail-row-1\b", re.I))
            if detail_row:
                texts = [
                    re.sub(r"\s+", " ", node.get_text(strip=True))
                    for node in detail_row.find_all(["div", "span", "p"], recursive=True)
                ]
                for candidate in texts:
                    cleaned = _clean_title_candidate(candidate, profile.full_name)
                    if cleaned and len(cleaned) <= 80 and "@" not in cleaned:
                        profile.title = cleaned
                        break

        # Fallback: look for known title keywords just below the h1
        if not profile.title:
            profile.title = _extract_title_proximity(html)

    # --- Offices ---
    # Skip section-parser office extraction for Weil (section parser returns full firm office
    # directory from page nav, not the individual attorney's office)
    if not profile.offices and "weil.com" not in url:
        for text in find_section(section_map, "offices"):
            if text and text not in profile.offices:
                profile.offices.append(text)

    # --- Departments ---
    if not profile.department:
        for text in find_section(section_map, "departments"):
            if text and text not in profile.department:
                profile.department.append(text)

    # --- Practice Areas ---
    if not profile.practice_areas:
        for text in find_section(section_map, "practice_areas"):
            if text and text not in profile.practice_areas:
                profile.practice_areas.append(text)

    # --- Industries ---
    if not profile.industries:
        for text in find_section(section_map, "industries"):
            if text and text not in profile.industries:
                profile.industries.append(text)

    # --- Bar Admissions ---
    if not profile.bar_admissions:
        raw_bars = find_section(section_map, "bar_admissions")
        parsed_states = parse_bar_admissions_text_blocks(raw_bars)
        for state in parsed_states:
            if state not in profile.bar_admissions:
                profile.bar_admissions.append(state)

    # --- Education ---
    if not profile.education:
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

    if not profile.full_name:
        h1 = soup.find("h1")
        if h1:
            candidate = _clean_name_text(h1.get_text(separator=" ", strip=True))
            if _looks_like_name(candidate):
                profile.full_name = candidate

    if not profile.title:
        title = _extract_title_proximity(html)
        if title:
            profile.title = title

    if not profile.practice_areas:
        items = _proximity_list_items(
            soup, ["practice", "expertise", "specialt", "service"]
        )
        profile.practice_areas.extend(items)

    if not profile.department:
        items = _proximity_list_items(
            soup, ["department", "practice group", "industry group", "division"]
        )
        if items:
            for item in items:
                if item not in profile.department:
                    profile.department.append(item)

    if not profile.industries:
        items = _proximity_list_items(
            soup, ["industry", "industries", "sector", "market focus"]
        )
        if items:
            for item in items:
                if item not in profile.industries:
                    profile.industries.append(item)

    if not profile.bar_admissions:
        items = _proximity_list_items(soup, ["bar admission", "admitted", "bar"])
        states = parse_bar_admissions_text_blocks(items)
        profile.bar_admissions.extend(states)
        if not profile.bar_admissions:
            from validators import _extract_states_from_text
            states_all = _extract_states_from_text(full_text)
            profile.bar_admissions.extend(states_all)

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


def _clean_title_candidate(text: str, full_name: str | None = None) -> str | None:
    """Remove title candidates that are placeholders or duplicated names."""
    candidate = re.sub(r"\s+", " ", text.strip())
    if not candidate:
        return None
    if re.search(r"\{\{.*?\}\}|\[\[.*?\]\]", candidate):
        return None
    compact = re.sub(r"[\s,.-]+", "", candidate).lower()
    if full_name:
        full_name_compact = re.sub(r"[\s,.-]+", "", full_name).lower()
        if compact == full_name_compact:
            return None
    if _looks_like_name(_clean_name_text(candidate)):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_name_text(text: str) -> str:
    """Normalize a raw name string extracted from HTML.

    Handles:
    - Zero-width / formatting Unicode characters (Goulston-style CMS injection)
    - Missing space between period and capital (Cravath: 'Robert E.Novick')
    - Leading/trailing whitespace and punctuation
    - Credential suffixes (MBA, Ph.D., CIPM, etc.) after the name
    """
    import unicodedata
    # Strip zero-width and formatting characters (Unicode category Cf)
    cleaned = "".join(
        c for c in text
        if unicodedata.category(c) not in ("Cf",) and c != "\ufeff"
    )
    # Strip credential/degree suffixes after a comma FIRST (before period-space fix)
    # Handles: "Bing Ai, Ph.D."  "Mallory Acheson, CIPM, CIPP/E"  "Kevin Bielawski, MBA"
    cleaned = re.sub(
        r",\s*(?:[A-Z]{2,}|[A-Z][a-z]*\.[A-Z]\.?|LL\.[A-Z]\.?)[^\n]*$",
        "",
        cleaned,
    ).strip()
    # Strip audio/aria garbage: "Play Audio Recording of Name"
    cleaned = re.sub(r"\s*Play Audio.*$", "", cleaned, flags=re.IGNORECASE).strip()
    # Fix missing space: "E.Novick" → "E. Novick"
    cleaned = re.sub(r"(\.)([A-Z])", r"\1 \2", cleaned)
    # Normalize multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _attr_text(value: Any) -> str:
    """Normalize BeautifulSoup attribute values to plain text."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item).strip() for item in value if str(item).strip()).strip()
    return str(value).strip()


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
