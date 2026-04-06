#!/usr/bin/env python3
"""external_directory_extractor.py - External Directory Fallback Pipeline

Supplements attorney data for BLOCKED or under-covered firms using publicly
accessible legal directories.

CONSTRAINTS:
- No bot evasion / CAPTCHA bypass
- No authentication or session token reverse engineering
- Respects robots.txt (checked by ComplianceEngine before calling this)
- No IP rotation / stealth automation
- Only publicly accessible content loadable in a normal browser

DATA SOURCES (in priority order):
1. Justia Lawyer Directory (public, no auth, paginated)
2. Martindale-Hubbell (public organization + attorney pages via allowed sitemap URLs)
3. California State Bar (CSLB public search — CA attorneys only)
4. Texas State Bar (TBL public search — TX attorneys only)
5. JSON-LD extraction from any profile HTML (via extruct)

Usage:
    extractor = ExternalDirectoryExtractor()
    attorneys = extractor.extract_by_firm("Kirkland & Ellis", max_results=200)
    count = extractor.estimate_count("Kirkland & Ellis")
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from attorney_extractor import AttorneyProfile, EducationRecord

try:
    import extruct
    EXTRUCT_AVAILABLE = True
except ImportError:
    EXTRUCT_AVAILABLE = False
    extruct = None  # type: ignore[assignment]

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limit constants (generous — legal, non-abusive)
# ---------------------------------------------------------------------------
_DELAY_JUSTIA = 1.0        # 1 req/sec
_DELAY_MARTINDALE = 3.0    # honor Martindale robots + conservative crawl delay
_DELAY_CALBAR = 1.5
_DELAY_TXBAR = 1.5
_MAX_PAGES = 40            # never paginate > 40 pages per source
_MAX_EMPTY_PAGES = 3       # stop after N consecutive pages with 0 results

_MARTINDALE_BASE_URL = "https://www.martindale.com"
_MARTINDALE_ALLOWED_PREFIXES = ("/organization/", "/attorney/")
_MARTINDALE_SITEMAP_URLS = (
    f"{_MARTINDALE_BASE_URL}/sitemap_browse.xml",
    f"{_MARTINDALE_BASE_URL}/sitemap_new_profiles.xml",
    f"{_MARTINDALE_BASE_URL}/sitemap_profiles.xml",
)
_FIRM_STOP_WORDS = {
    'llp', 'llc', 'pc', 'p.c', 'pllc', 'lpa', 'apc', 'pa',
    'law', 'firm', 'group', 'office', 'offices', 'attorneys', 'lawyers',
    'the', 'and', '&', 'of', 'co', 'company',
}


# ---------------------------------------------------------------------------
# Search-form page detector (PROBLEM 1 FIX)
# ---------------------------------------------------------------------------

# Labels that appear on search/directory form pages but NOT on real profiles
_SEARCH_FORM_LABELS = re.compile(
    r'\b(?:Last\s+Name|First\s+Name|Firm\s+Name|City|State)\b',
    re.IGNORECASE,
)
# Input fields that signal a search form template
_SEARCH_FORM_INPUTS = re.compile(
    r'<input[^>]+name=["\'](Last\s*Name|First\s*Name|Firm\s*Name)["\']',
    re.IGNORECASE,
)
# Signals that confirm a real attorney profile page
_PROFILE_CONFIRM_PATTERNS = re.compile(
    r'\b(?:Admitted|Bar\s+Admission|Practice\s+Areas?|Education)\b',
    re.IGNORECASE,
)
_FULL_NAME_PATTERN = re.compile(
    r'\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+\b'
)


def _is_search_form_page(html: str) -> bool:
    """Return True if the HTML looks like a search/directory form page
    rather than an actual attorney profile page.

    Rejects pages that:
    - Contain input fields named 'Last Name', 'First Name', or 'Firm Name'
    - OR contain 3+ label matches for search field names
    AND lack:
    - At least one confirmed profile signal (Admitted / Bar / Practice Areas / Education)
    - AND at least one full-name pattern (FirstName LastName)
    """
    # Hard reject: search form input fields present
    if _SEARCH_FORM_INPUTS.search(html):
        label_count = len(_SEARCH_FORM_LABELS.findall(html))
        if label_count >= 2:
            return True  # definitely a search form

    # Soft reject: many label matches, no profile confirmation
    label_count = len(_SEARCH_FORM_LABELS.findall(html))
    if label_count >= 3:
        has_profile_signal = bool(_PROFILE_CONFIRM_PATTERNS.search(html))
        has_full_name = bool(_FULL_NAME_PATTERN.search(html))
        if not (has_profile_signal and has_full_name):
            return True

    return False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExternalResult:
    """Summary stats returned alongside attorney profiles."""
    source: str
    profiles_found: int
    estimated_total: int | None
    pages_fetched: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

class ExternalDirectoryExtractor:
    """Extracts attorney data from publicly accessible legal directories."""

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = 10,
        log_fn: Any = None,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._log = log_fn or (lambda m: log.info(m))
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_by_firm(
        self,
        firm_name: str,
        max_results: int = 200,
    ) -> tuple[list[AttorneyProfile], list[ExternalResult]]:
        """
        Extract attorneys from all external directories for a given firm.

        Returns (attorneys, result_summaries).
        Merges results from all sources, deduplicating by name.
        """
        attorneys: list[AttorneyProfile] = []
        summaries: list[ExternalResult] = []
        seen_names: set[str] = set()

        sources = [
            ("justia", self._extract_from_justia),
            ("martindale", self._extract_from_martindale),
            ("calbar", self._extract_from_calbar),
            ("txbar", self._extract_from_txbar),
        ]

        for source_name, extractor_fn in sources:
            if len(attorneys) >= max_results:
                break
            try:
                profiles, summary = extractor_fn(firm_name, max_results - len(attorneys))
                summaries.append(summary)
                for p in profiles:
                    name_key = (p.full_name or "").lower().strip()
                    if name_key and name_key not in seen_names:
                        seen_names.add(name_key)
                        attorneys.append(p)
                self._log(f"  [ExternalDir/{source_name}] +{len(profiles)} attorneys")
            except Exception as exc:
                self._log(f"  [ExternalDir/{source_name}] ERROR: {exc}")
                summaries.append(ExternalResult(
                    source=source_name, profiles_found=0,
                    estimated_total=None, pages_fetched=0,
                    errors=[str(exc)],
                ))

        return attorneys[:max_results], summaries

    def estimate_count(self, firm_name: str) -> int | None:
        """
        Quickly estimate how many attorneys a firm has from external directories.
        Returns the highest count found, or None if all sources fail.
        """
        estimates: list[int] = []

        # Justia quick search
        count = self._justia_estimate_count(firm_name)
        if count:
            estimates.append(count)

        # Martindale quick search
        count = self._martindale_estimate_count(firm_name)
        if count:
            estimates.append(count)

        return max(estimates) if estimates else None

    # ------------------------------------------------------------------
    # Justia
    # ------------------------------------------------------------------

    def _extract_from_justia(
        self, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], ExternalResult]:
        """
        Justia Lawyer Directory: https://www.justia.com/lawyers/search
        Paginated via ?page=N
        """
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        empty_streak = 0
        base_search = f"https://www.justia.com/lawyers/search?q={quote_plus(firm_name)}"

        for page in range(1, _MAX_PAGES + 1):
            if len(attorneys) >= max_results:
                break
            url = f"{base_search}&page={page}" if page > 1 else base_search
            try:
                time.sleep(_DELAY_JUSTIA)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    errors.append(f"HTTP {resp.status_code} at page {page}")
                    break
                pages_fetched += 1
                soup = BeautifulSoup(resp.text, 'html.parser')

                # Grab total estimate from first page
                if page == 1:
                    estimated_total = self._parse_total_count(resp.text)

                # Find profile links
                profile_urls = self._justia_profile_links(soup)
                if not profile_urls:
                    empty_streak += 1
                    if empty_streak >= _MAX_EMPTY_PAGES:
                        break
                    continue
                empty_streak = 0

                for purl in profile_urls:
                    if len(attorneys) >= max_results:
                        break
                    time.sleep(_DELAY_JUSTIA)
                    profile = self._extract_justia_profile(firm_name, purl)
                    if profile and profile.full_name:
                        attorneys.append(profile)

            except Exception as exc:
                errors.append(f"page {page}: {exc}")
                break

        summary = ExternalResult(
            source="justia",
            profiles_found=len(attorneys),
            estimated_total=estimated_total,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        return attorneys, summary

    def _justia_profile_links(self, soup: BeautifulSoup) -> list[str]:
        links = []
        for a in soup.find_all('a', href=True):
            href = _href_to_str(a.get('href'))
            if not href:
                continue
            # Justia profile pattern: /lawyers/{state}/{city}/{name}-{id}
            if re.search(r'/lawyers/[a-z\-]+/[a-z\-]+/[a-z\-]+-\d+', href):
                full = f"https://www.justia.com{href}" if href.startswith('/') else href
                if full not in links:
                    links.append(full)
        return links

    def _justia_estimate_count(self, firm_name: str) -> int | None:
        try:
            url = f"https://www.justia.com/lawyers/search?q={quote_plus(firm_name)}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return self._parse_total_count(resp.text)
        except Exception:
            pass
        return None

    def _extract_justia_profile(self, firm_name: str, profile_url: str) -> AttorneyProfile | None:
        try:
            resp = self.session.get(profile_url, timeout=self.timeout)
            if resp.status_code != 200:
                return None

            # PROBLEM 1 FIX: reject search form / directory template pages
            if _is_search_form_page(resp.text):
                log.debug("Rejected search form page: %s", profile_url)
                return None

            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.diagnostics["data_source"] = "external_directory"
            profile.diagnostics["directory_name"] = "Justia"
            profile.diagnostics["original_source_url"] = profile_url

            # Try JSON-LD first (most reliable)
            if EXTRUCT_AVAILABLE:
                ld_data = _extract_json_ld(resp.text, profile_url)
                if ld_data:
                    _apply_json_ld_to_profile(profile, ld_data)

            # Fall back to BeautifulSoup HTML parsing
            if not profile.full_name:
                soup = BeautifulSoup(resp.text, 'html.parser')
                _parse_justia_html(profile, soup)

            profile.calculate_status()
            return profile if profile.full_name else None

        except Exception:
            return None

    # ------------------------------------------------------------------
    # Martindale-Hubbell
    # ------------------------------------------------------------------

    def _extract_from_martindale(
        self, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], ExternalResult]:
        """
        Martindale-Hubbell extraction using allowed sitemap + organization pages.
        """
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        seen_keys: set[str] = set()

        for page_url in self._martindale_candidate_urls(firm_name)[:_MAX_PAGES]:
            if len(attorneys) >= max_results:
                break
            try:
                resp = self._martindale_get(page_url)
                if resp.status_code != 200:
                    errors.append(f"HTTP {resp.status_code} at {page_url}")
                    continue

                pages_fetched += 1
                if estimated_total is None:
                    estimated_total = self._parse_total_count(resp.text)

                for profile in self._martindale_profiles_from_response(firm_name, page_url, resp.text):
                    if len(attorneys) >= max_results:
                        break
                    profile_key = (profile.profile_url or profile.full_name or "").lower().strip()
                    if not profile_key or profile_key in seen_keys:
                        continue
                    seen_keys.add(profile_key)
                    attorneys.append(profile)

            except Exception as exc:
                errors.append(f"{page_url}: {exc}")

        summary = ExternalResult(
            source="martindale",
            profiles_found=len(attorneys),
            estimated_total=estimated_total,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        return attorneys, summary

    def _extract_martindale_html(
        self, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], ExternalResult]:
        """Backward-compatible wrapper around the compliant Martindale path flow."""
        return self._extract_from_martindale(firm_name, max_results)

    def _martindale_get(self, url: str) -> requests.Response:
        time.sleep(_DELAY_MARTINDALE)
        return self.session.get(url, timeout=self.timeout)

    def _martindale_candidate_urls(self, firm_name: str) -> list[str]:
        candidates = self._martindale_sitemap_candidates(firm_name)
        if candidates:
            return candidates

        fallback_urls: list[str] = []
        for slug in _martindale_slug_variants(firm_name):
            fallback_urls.append(f"{_MARTINDALE_BASE_URL}/organization/{slug}/")
        return fallback_urls

    def _martindale_sitemap_candidates(self, firm_name: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for sitemap_url in _MARTINDALE_SITEMAP_URLS:
            try:
                resp = self._martindale_get(sitemap_url)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, 'xml')
                for loc in soup.find_all('loc'):
                    url = loc.get_text(strip=True)
                    if not url or url in seen:
                        continue
                    if not _is_martindale_allowed_url(url):
                        continue
                    if _martindale_url_matches_firm(url, firm_name):
                        seen.add(url)
                        candidates.append(url)
            except Exception:
                continue
            if candidates:
                break
        return candidates

    def _martindale_profiles_from_response(
        self,
        firm_name: str,
        url: str,
        html: str,
    ) -> list[AttorneyProfile]:
        if _is_martindale_attorney_url(url):
            profile = self._extract_martindale_profile_page(firm_name, url, html)
            return [profile] if profile else []
        soup = BeautifulSoup(html, 'html.parser')
        return self._martindale_html_cards(soup, firm_name)

    def _extract_martindale_profile_page(
        self,
        firm_name: str,
        profile_url: str,
        html: str,
    ) -> AttorneyProfile | None:
        if not _is_martindale_allowed_url(profile_url):
            return None

        profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
        profile.diagnostics["data_source"] = "external_directory"
        profile.diagnostics["directory_name"] = "Martindale"
        profile.diagnostics["original_source_url"] = profile_url

        if EXTRUCT_AVAILABLE:
            ld_data = _extract_json_ld(html, profile_url)
            if ld_data:
                works_for = _json_ld_firm_name(ld_data)
                if works_for and not _firm_names_match(works_for, firm_name):
                    return None
                _apply_json_ld_to_profile(profile, ld_data)

        soup = BeautifulSoup(html, 'html.parser')

        if not profile.full_name:
            name_el = soup.find(['h1', 'h2'], class_=re.compile(r'name|title', re.I)) or soup.find('h1')
            if name_el:
                profile.full_name = name_el.get_text(strip=True)

        if not profile.title:
            title_el = soup.find(class_=re.compile(r'title|position|role', re.I))
            if title_el:
                profile.title = title_el.get_text(strip=True)

        org_text = _martindale_extract_firm_text(soup)
        if org_text and not _firm_names_match(org_text, firm_name):
            return None

        if profile.full_name:
            profile.calculate_status()
            return profile
        return None

    def _martindale_html_cards(
        self, soup: BeautifulSoup, firm_name: str
    ) -> list[AttorneyProfile]:
        """Parse attorney cards from Martindale organization HTML."""
        profiles = []
        card_selectors = [
            {'class': re.compile(r'result[-_]item|attorney[-_]card|lawyer[-_]card|profile[-_]card', re.I)},
            {'class': re.compile(r'card|listing|result', re.I)},
        ]
        cards = []
        for selector in card_selectors:
            cards = soup.find_all(['div', 'li', 'article'], class_=selector['class'])
            if cards:
                break

        for card in cards:
            try:
                result_firm = _martindale_extract_firm_text(card)
                if result_firm and not _firm_names_match(result_firm, firm_name):
                    continue

                profile = AttorneyProfile(firm=firm_name, profile_url="")
                profile.diagnostics["data_source"] = "external_directory"
                profile.diagnostics["directory_name"] = "Martindale"

                # Name
                name_el = card.find(['h2', 'h3', 'h4'], class_=re.compile(r'name|title', re.I)) \
                           or card.find('a', class_=re.compile(r'name|attorney', re.I))
                if name_el:
                    profile.full_name = name_el.get_text(strip=True)

                # Profile URL
                link = card.find('a', href=True)
                if link:
                    href = _href_to_str(link.get('href'))
                    if not href:
                        continue
                    full_url = urljoin(_MARTINDALE_BASE_URL, href) if href.startswith('/') else href
                    if _is_martindale_allowed_url(full_url):
                        profile.profile_url = full_url

                # Location
                loc_el = card.find(class_=re.compile(r'location|city|address|office', re.I))
                if loc_el:
                    loc_text = loc_el.get_text(strip=True)
                    if _is_us_location(loc_text):
                        profile.offices.append(loc_text)

                # Title
                title_el = card.find(class_=re.compile(r'title|position|role', re.I))
                if title_el:
                    profile.title = title_el.get_text(strip=True)

                for department in _string_list_from_value(
                    _node_strings(card, re.compile(r'department|group|practice-group', re.I))
                ):
                    if department not in profile.department:
                        profile.department.append(department)

                for practice in _string_list_from_value(
                    _node_strings(card, re.compile(r'practice|service', re.I))
                ):
                    if practice not in profile.practice_areas:
                        profile.practice_areas.append(practice)

                for industry in _string_list_from_value(
                    _node_strings(card, re.compile(r'industry|sector', re.I))
                ):
                    if industry not in profile.industries:
                        profile.industries.append(industry)

                if profile.full_name:
                    profile.calculate_status()
                    profiles.append(profile)
            except Exception:
                continue

        return profiles

    def _martindale_estimate_count(self, firm_name: str) -> int | None:
        for url in self._martindale_candidate_urls(firm_name):
            try:
                resp = self._martindale_get(url)
                if resp.status_code != 200:
                    continue
                total = self._parse_total_count(resp.text)
                if total:
                    return total
                profiles = self._martindale_profiles_from_response(firm_name, url, resp.text)
                if profiles:
                    return len(profiles)
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # California State Bar
    # ------------------------------------------------------------------

    def _extract_from_calbar(
        self, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], ExternalResult]:
        """
        California State Bar public search.
        URL: https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch
        Only returns California-admitted attorneys — useful supplement.
        Paginated via form POST / GET with LastName search by firm token.
        """
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None

        # CA Bar search by firm name tokens (first significant word)
        # We search by each word in firm name to get broadest results
        firm_tokens = _firm_search_tokens(firm_name)

        for token in firm_tokens[:2]:  # top 2 tokens to avoid too many requests
            if len(attorneys) >= max_results:
                break
            try:
                token_attorneys, token_errors, token_pages, token_total = \
                    self._calbar_search_token(token, firm_name, max_results - len(attorneys))
                attorneys.extend(token_attorneys)
                errors.extend(token_errors)
                pages_fetched += token_pages
                if token_total and (estimated_total is None or token_total > estimated_total):
                    estimated_total = token_total
            except Exception as exc:
                errors.append(f"calbar token '{token}': {exc}")

        summary = ExternalResult(
            source="calbar",
            profiles_found=len(attorneys),
            estimated_total=estimated_total,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        return attorneys, summary

    def _calbar_search_token(
        self, token: str, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], list[str], int, int | None]:
        """Search CA Bar for attorneys associated with a firm name token."""
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        empty_streak = 0

        # CA Bar has a "FirmName" search field
        base_url = "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch"
        params_base = {"FirmName": token, "StatusType": "A"}  # Active only

        for page in range(1, _MAX_PAGES + 1):
            if len(attorneys) >= max_results:
                break
            params = dict(params_base)
            if page > 1:
                params["CurrentPage"] = str(page)

            try:
                time.sleep(_DELAY_CALBAR)
                resp = self.session.get(base_url, params=params, timeout=self.timeout)
                if resp.status_code != 200:
                    errors.append(f"CALBAR HTTP {resp.status_code}")
                    break
                pages_fetched += 1
                soup = BeautifulSoup(resp.text, 'html.parser')

                if page == 1:
                    estimated_total = self._parse_total_count(resp.text)

                profiles = self._calbar_parse_results(soup, firm_name)
                if not profiles:
                    empty_streak += 1
                    if empty_streak >= _MAX_EMPTY_PAGES:
                        break
                    continue
                empty_streak = 0
                attorneys.extend(profiles[:max_results - len(attorneys)])

            except Exception as exc:
                errors.append(f"CALBAR page {page}: {exc}")
                break

        return attorneys, errors, pages_fetched, estimated_total

    def _calbar_parse_results(
        self, soup: BeautifulSoup, firm_name: str
    ) -> list[AttorneyProfile]:
        """Parse CA Bar search results table."""
        profiles = []
        # CA Bar results are in a table
        table = soup.find('table', class_=re.compile(r'result|attorney|search', re.I)) \
                or soup.find('table')
        if not table:
            return profiles

        rows = table.find_all('tr')
        for row in rows[1:]:  # skip header
            cols = row.find_all(['td', 'th'])
            if len(cols) < 2:
                continue
            try:
                profile = AttorneyProfile(firm=firm_name, profile_url="")
                profile.diagnostics["data_source"] = "external_directory"
                profile.diagnostics["directory_name"] = "CalBar"

                # CA Bar table structure: Name | Bar Number | City | County | Status
                name_col = cols[0].get_text(strip=True)
                if name_col and len(name_col) > 3:
                    profile.full_name = name_col

                if len(cols) >= 3:
                    city = cols[2].get_text(strip=True)
                    if city:
                        profile.offices.append(f"{city}, CA")

                # Bar admission
                profile.bar_admissions.append("California")

                # Profile link
                link = cols[0].find('a', href=True)
                if link:
                    href = _href_to_str(link.get('href'))
                    if not href:
                        continue
                    profile.profile_url = urljoin(
                        "https://apps.calbar.ca.gov", href
                    ) if href.startswith('/') else href

                if profile.full_name:
                    profile.calculate_status()
                    profiles.append(profile)
            except Exception:
                continue

        return profiles

    # ------------------------------------------------------------------
    # Texas State Bar
    # ------------------------------------------------------------------

    def _extract_from_txbar(
        self, firm_name: str, max_results: int
    ) -> tuple[list[AttorneyProfile], ExternalResult]:
        """
        Texas State Bar public search.
        URL: https://www.texasbar.com/AM/Template.cfm?Section=Find_A_Lawyer
        Only returns Texas-admitted attorneys.
        """
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        empty_streak = 0

        firm_tokens = _firm_search_tokens(firm_name)

        for token in firm_tokens[:2]:
            if len(attorneys) >= max_results:
                break
            try:
                base_url = "https://www.texasbar.com/AM/Template.cfm"
                params = {
                    "Section": "Find_A_Lawyer",
                    "Template": "/CustomSource/MemberDirectory/Result_form_client.cfm",
                    "FirmName": token,
                    "SortOrder": "LN",
                    "Limit": min(100, max_results - len(attorneys)),
                }
                time.sleep(_DELAY_TXBAR)
                resp = self.session.get(base_url, params=params, timeout=self.timeout)
                if resp.status_code != 200:
                    errors.append(f"TXBAR HTTP {resp.status_code}")
                    continue
                pages_fetched += 1

                if not estimated_total:
                    estimated_total = self._parse_total_count(resp.text)

                soup = BeautifulSoup(resp.text, 'html.parser')
                profiles = self._txbar_parse_results(soup, firm_name)
                if profiles:
                    attorneys.extend(profiles[:max_results - len(attorneys)])

            except Exception as exc:
                errors.append(f"txbar token '{token}': {exc}")

        summary = ExternalResult(
            source="txbar",
            profiles_found=len(attorneys),
            estimated_total=estimated_total,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        return attorneys, summary

    def _txbar_parse_results(
        self, soup: BeautifulSoup, firm_name: str
    ) -> list[AttorneyProfile]:
        """Parse TX Bar search results."""
        profiles = []
        # TX Bar results are typically in a table or list
        table = soup.find('table', class_=re.compile(r'result|attorney|search|directory', re.I)) \
                or soup.find('table', id=re.compile(r'result|attorney', re.I)) \
                or soup.find('table')
        if not table:
            return profiles

        rows = table.find_all('tr')
        for row in rows[1:]:
            cols = row.find_all(['td', 'th'])
            if len(cols) < 2:
                continue
            try:
                profile = AttorneyProfile(firm=firm_name, profile_url="")
                profile.diagnostics["data_source"] = "external_directory"
                profile.diagnostics["directory_name"] = "TXBar"

                # TX Bar: Name | City | County | Status | Bar Number
                name_text = cols[0].get_text(strip=True)
                if name_text and len(name_text) > 3:
                    profile.full_name = name_text

                if len(cols) >= 2:
                    city = cols[1].get_text(strip=True)
                    if city:
                        profile.offices.append(f"{city}, TX")

                profile.bar_admissions.append("Texas")

                link = cols[0].find('a', href=True)
                if link:
                    href = _href_to_str(link.get('href'))
                    if not href:
                        continue
                    profile.profile_url = urljoin(
                        "https://www.texasbar.com", href
                    ) if href.startswith('/') else href

                if profile.full_name:
                    profile.calculate_status()
                    profiles.append(profile)
            except Exception:
                continue

        return profiles

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _parse_total_count(self, text: str) -> int | None:
        """
        Extract total attorney count from page text using multiple patterns.
        Returns the largest plausible value (5–15000).
        """
        patterns = [
            re.compile(r'(\d[\d,]+)\s+(?:attorney|lawyer|professional|result|record)s?', re.I),
            re.compile(r'(?:total|showing|found|of)\s*:?\s*(\d[\d,]+)', re.I),
            re.compile(r'"total"\s*:\s*(\d+)', re.I),
            re.compile(r'"totalCount"\s*:\s*(\d+)', re.I),
            re.compile(r'"numFound"\s*:\s*(\d+)', re.I),
            re.compile(r'(\d[\d,]+)\s+(?:match|member)es?', re.I),
        ]
        candidates = []
        for pat in patterns:
            for m in pat.finditer(text):
                try:
                    val = int(m.group(1).replace(',', ''))
                    if 5 <= val <= 15000:
                        candidates.append(val)
                except ValueError:
                    pass
        return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# JSON-LD extraction (extruct)
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str, base_url: str) -> dict | None:
    """
    Extract the first Person-type JSON-LD block from HTML using extruct.
    Returns the Person dict or None.
    """
    if not EXTRUCT_AVAILABLE:
        return _extract_json_ld_regex(html)

    try:
        data = extruct.extract(  # type: ignore[union-attr]
            html,
            base_url=base_url,
            syntaxes=['json-ld'],
            uniform=True,
        )
        json_ld_items = data.get('json-ld', [])
        for item in json_ld_items:
            type_val = item.get('@type', '')
            if isinstance(type_val, list):
                types = type_val
            else:
                types = [type_val]
            if any(t in ('Person', 'Attorney', 'LegalService', 'Lawyer') for t in types):
                return item
        # Fallback: return first item if any
        return json_ld_items[0] if json_ld_items else None
    except Exception:
        return _extract_json_ld_regex(html)


def _extract_json_ld_regex(html: str) -> dict | None:
    """Fallback: regex-based JSON-LD extraction when extruct is unavailable."""
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.I,
    )
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get('@type') in ('Person', 'Attorney', 'Lawyer'):
                        return item
            elif isinstance(data, dict):
                if data.get('@type') in ('Person', 'Attorney', 'Lawyer'):
                    return data
        except Exception:
            continue
    return None


def _apply_json_ld_to_profile(profile: AttorneyProfile, ld: dict) -> None:
    """Apply JSON-LD Person data to an AttorneyProfile (non-destructive)."""
    if not profile.full_name:
        profile.full_name = ld.get('name', '')

    if not profile.title:
        profile.title = ld.get('jobTitle', '') or ld.get('title', '')

    # Email / telephone are intentionally skipped (not required fields)

    # Office / address
    addr = ld.get('address') or ld.get('workLocation', {})
    if isinstance(addr, dict):
        parts = [
            addr.get('addressLocality', ''),
            addr.get('addressRegion', ''),
        ]
        office = ', '.join(p for p in parts if p)
        if office and _is_us_location(office):
            if office not in profile.offices:
                profile.offices.append(office)
    elif isinstance(addr, list):
        for a in addr:
            if isinstance(a, dict):
                parts = [a.get('addressLocality', ''), a.get('addressRegion', '')]
                office = ', '.join(p for p in parts if p)
                if office and _is_us_location(office) and office not in profile.offices:
                    profile.offices.append(office)

    # Practice areas
    for key in ('knowsAbout', 'hasCredential', 'makesOffer'):
        items = ld.get(key, [])
        if isinstance(items, str):
            items = [items]
        for item in items:
            if isinstance(item, str) and len(item) > 3:
                if item not in profile.practice_areas:
                    profile.practice_areas.append(item)
            elif isinstance(item, dict):
                name = item.get('name', '')
                if name and name not in profile.practice_areas:
                    profile.practice_areas.append(name)

    # Education
    for edu_item in (ld.get('alumniOf') or []):
        if isinstance(edu_item, str):
            if not any(e.school == edu_item for e in profile.education):
                profile.education.append(EducationRecord(school=edu_item))
        elif isinstance(edu_item, dict):
            school = edu_item.get('name', '')
            degree = edu_item.get('description', '') or edu_item.get('credential', '')
            year_str = edu_item.get('endDate', '') or edu_item.get('year', '')
            year = None
            if year_str:
                m = re.search(r'\b(19|20)\d{2}\b', str(year_str))
                if m:
                    year = int(m.group(0))
            if school and not any(e.school == school for e in profile.education):
                profile.education.append(EducationRecord(degree=degree or None, school=school, year=year))

    profile.diagnostics['json_ld_applied'] = True


# ---------------------------------------------------------------------------
# Justia HTML fallback parser
# ---------------------------------------------------------------------------

def _parse_justia_html(profile: AttorneyProfile, soup: BeautifulSoup) -> None:
    """Parse a Justia attorney profile page with BeautifulSoup."""
    # Name: usually in h1 or .attorney-name class
    for selector in [{'class': re.compile(r'attorney.name|lawyer.name|profile.name', re.I)}, 'h1']:
        el = soup.find(selector) if isinstance(selector, str) else soup.find(class_=selector['class'])
        if el:
            profile.full_name = el.get_text(strip=True)
            break

    # Title
    for cls_pat in [re.compile(r'position|title|role|attorney.type', re.I)]:
        el = soup.find(class_=cls_pat)
        if el and not profile.title:
            profile.title = el.get_text(strip=True)
            break

    # Office location(s)
    for cls_pat in [re.compile(r'office.location|address|location|city', re.I)]:
        for el in soup.find_all(class_=cls_pat):
            text = el.get_text(strip=True)
            if _is_us_location(text) and text not in profile.offices:
                profile.offices.append(text)

    # Practice areas
    pa_header = soup.find(string=re.compile(r'practice\s+area', re.I))
    if pa_header:
        parent = pa_header.find_parent(['section', 'div', 'ul'])
        if parent:
            for item in parent.find_all(['li', 'a']):
                text = item.get_text(strip=True)
                if 3 < len(text) < 80 and text not in profile.practice_areas:
                    profile.practice_areas.append(text)

    # Bar admissions
    bar_header = soup.find(string=re.compile(r'bar\s+admission', re.I))
    if bar_header:
        parent = bar_header.find_parent(['section', 'div', 'ul'])
        if parent:
            for item in parent.find_all(['li', 'span']):
                text = item.get_text(strip=True)
                if _is_us_bar_admission(text) and text not in profile.bar_admissions:
                    profile.bar_admissions.append(text)

    # Education
    edu_header = soup.find(string=re.compile(r'education', re.I))
    if edu_header:
        parent = edu_header.find_parent(['section', 'div', 'ul'])
        if parent:
            for item in parent.find_all(['li', 'div']):
                text = item.get_text(strip=True)
                rec = _parse_education_text(text)
                if rec and not any(e.school == rec.school for e in profile.education):
                    profile.education.append(rec)


# ---------------------------------------------------------------------------
# Martindale JSON helpers
# ---------------------------------------------------------------------------

def _json_search_total(data: dict) -> int | None:
    """Extract total count from Martindale-style JSON search response."""
    for key in ['total', 'totalCount', 'numFound', 'count', 'hits', 'resultCount']:
        val = data.get(key)
        if isinstance(val, int) and 5 <= val <= 15000:
            return val
    # Check nested
    for wrapper_key in ['response', 'data', 'results', 'result']:
        sub = data.get(wrapper_key)
        if isinstance(sub, dict):
            result = _json_search_total(sub)
            if result:
                return result
    return None


def _json_search_results(data: dict) -> list[dict]:
    """Extract attorney records list from Martindale-style JSON response."""
    for key in ['docs', 'results', 'items', 'lawyers', 'attorneys', 'data']:
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
    # Check nested
    for wrapper_key in ['response', 'result', 'data']:
        sub = data.get(wrapper_key)
        if isinstance(sub, dict):
            result = _json_search_results(sub)
            if result:
                return result
    return []


def _martindale_item_to_profile(item: dict, firm_name: str) -> AttorneyProfile | None:
    """Convert a Martindale JSON search result item to AttorneyProfile."""
    try:
        result_firm = (
            item.get('firmName') or item.get('firm') or item.get('organizationName') or
            item.get('companyName') or item.get('employer') or item.get('organization') or ''
        )
        if result_firm and not _firm_names_match(str(result_firm), firm_name):
            return None

        profile = AttorneyProfile(firm=firm_name, profile_url="")
        profile.diagnostics["data_source"] = "external_directory"
        profile.diagnostics["directory_name"] = "Martindale"

        # Common Martindale JSON field names
        profile.full_name = (
            item.get('fullName') or item.get('full_name') or
            item.get('name') or item.get('displayName') or
            f"{item.get('firstName', '')} {item.get('lastName', '')}".strip()
        )
        profile.title = item.get('title') or item.get('position') or item.get('jobTitle', '')

        # URL
        url = item.get('profileUrl') or item.get('url') or item.get('href', '')
        if url:
            full_url = url if url.startswith('http') else f"{_MARTINDALE_BASE_URL}{url}"
            if _is_martindale_allowed_url(full_url):
                profile.profile_url = full_url

        # Office / city
        city = item.get('city') or item.get('location') or ''
        state = item.get('state') or item.get('stateCode') or ''
        address = item.get('address') or {}
        if isinstance(address, dict):
            city = city or address.get('city', '') or address.get('addressLocality', '')
            state = state or address.get('state', '') or address.get('addressRegion', '')
        if city or state:
            office = f"{city}, {state}".strip(', ')
            if _is_us_location(office):
                profile.offices.append(office)

        for department in _string_list_from_value(
            item.get('department') or item.get('departments') or item.get('practiceGroups')
        ):
            if department not in profile.department:
                profile.department.append(department)

        # Practice areas
        for practice in _string_list_from_value(item.get('practiceAreas') or item.get('areas')):
            if practice not in profile.practice_areas:
                profile.practice_areas.append(practice)

        for industry in _string_list_from_value(item.get('industries') or item.get('industryFocus')):
            if industry not in profile.industries:
                profile.industries.append(industry)

        for admission in _string_list_from_value(item.get('barAdmissions') or item.get('admissions')):
            if admission not in profile.bar_admissions:
                profile.bar_admissions.append(admission)

        for education_item in item.get('education') or item.get('schools') or []:
            record = _education_record_from_value(education_item)
            if record and not any(
                existing.school == record.school and existing.degree == record.degree and existing.year == record.year
                for existing in profile.education
            ):
                profile.education.append(record)

        return profile if profile.full_name else None
    except Exception:
        return None


def _martindale_slug_variants(firm_name: str) -> list[str]:
    base_slug = _slugify_for_url(firm_name)
    variants = [base_slug]
    trimmed_tokens = [token for token in _firm_tokens(firm_name) if token not in {'law', 'firm', 'group'}]
    if trimmed_tokens:
        variants.append('-'.join(trimmed_tokens))
    deduped: list[str] = []
    for variant in variants:
        if variant and variant not in deduped:
            deduped.append(variant)
    return deduped


def _slugify_for_url(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower())
    return slug.strip('-')


def _firm_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r'[a-z0-9]+', value.lower()) if token not in _FIRM_STOP_WORDS]


def _firm_names_match(candidate: str, target: str) -> bool:
    candidate_tokens = set(_firm_tokens(candidate))
    target_tokens = set(_firm_tokens(target))
    if not candidate_tokens or not target_tokens:
        return False
    overlap = candidate_tokens & target_tokens
    required_overlap = min(len(target_tokens), 2)
    return len(overlap) >= required_overlap


def _is_martindale_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.lower() not in {'martindale.com', 'www.martindale.com'}:
        return False
    return any(parsed.path.startswith(prefix) for prefix in _MARTINDALE_ALLOWED_PREFIXES) or url in _MARTINDALE_SITEMAP_URLS


def _is_martindale_attorney_url(url: str) -> bool:
    return urlparse(url).path.startswith('/attorney/')


def _martindale_url_matches_firm(url: str, firm_name: str) -> bool:
    slug = urlparse(url).path.strip('/').split('/')[-1]
    if not slug:
        return False
    return _firm_names_match(slug.replace('-', ' '), firm_name)


def _martindale_extract_firm_text(node: Any) -> str:
    el = node.find(class_=re.compile(r'firm|organization|company|employer', re.I))
    if el:
        return el.get_text(strip=True)
    text = node.get_text(' ', strip=True)
    match = re.search(r'(?:Firm|Organization|Company)\s*:?\s*([^|•]+)', text, re.I)
    return match.group(1).strip() if match else ''


def _node_strings(node: Any, class_pattern: re.Pattern[str]) -> list[str]:
    values: list[str] = []
    for el in node.find_all(class_=class_pattern):
        text = el.get_text(' ', strip=True)
        if text:
            values.append(text)
    return values


def _string_list_from_value(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        parts = re.split(r'\s*[,;|/]\s*', value)
        values.extend(part.strip() for part in parts if part.strip())
    elif isinstance(value, dict):
        name = value.get('name') or value.get('label') or value.get('value') or ''
        if isinstance(name, str) and name.strip():
            values.append(name.strip())
    elif isinstance(value, list):
        for item in value:
            values.extend(_string_list_from_value(item))
    deduped: list[str] = []
    for item in values:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _education_record_from_value(value: Any) -> EducationRecord | None:
    if isinstance(value, str):
        return _parse_education_text(value) or EducationRecord(school=value)
    if isinstance(value, dict):
        school = value.get('school') or value.get('name') or value.get('institution') or ''
        degree = value.get('degree') or value.get('credential') or value.get('description')
        year_value = value.get('year') or value.get('graduationYear') or value.get('endDate')
        year = None
        if year_value:
            match = re.search(r'\b(19|20)\d{2}\b', str(year_value))
            if match:
                year = int(match.group(0))
        if school:
            return EducationRecord(degree=degree or None, school=school, year=year)
    return None


def _href_to_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return str(value[0]) if value else ''
    return ''


def _json_ld_firm_name(ld: dict) -> str:
    works_for = ld.get('worksFor') or ld.get('affiliation') or {}
    if isinstance(works_for, dict):
        return str(works_for.get('name', '')).strip()
    if isinstance(works_for, list):
        for item in works_for:
            if isinstance(item, dict) and item.get('name'):
                return str(item['name']).strip()
            if isinstance(item, str) and item.strip():
                return item.strip()
    if isinstance(works_for, str):
        return works_for.strip()
    return ''


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------

_US_STATE_CODES_SET = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'DC', 'D.C.',
}

_NON_US_FAST_REJECT = {
    'london', 'england', 'united kingdom', 'uk', 'hong kong', 'singapore',
    'tokyo', 'japan', 'beijing', 'shanghai', 'paris', 'france', 'germany',
    'berlin', 'munich', 'frankfurt', 'dubai', 'abu dhabi', 'riyadh',
    'sydney', 'australia', 'toronto', 'canada', 'brussels', 'amsterdam',
    'madrid', 'milan', 'rome', 'moscow', 'korea', 'seoul',
}

_US_STATE_NAMES_LOWER = {
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'new hampshire', 'new jersey', 'new mexico', 'new york',
    'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
    'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
    'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
    'west virginia', 'wisconsin', 'wyoming', 'district of columbia',
}


def _is_us_location(text: str) -> bool:
    """Return True if text refers to a US location."""
    if not text:
        return False
    lower = text.strip().lower()

    for indicator in _NON_US_FAST_REJECT:
        if indicator in lower:
            return False

    # "City, ST" pattern
    m = re.search(r',\s*([A-Za-z]{2})(?:\s+\d{5})?$', text.strip())
    if m:
        code = m.group(1).upper()
        if code in _US_STATE_CODES_SET:
            return True
        if code in {'UK', 'AU', 'DE', 'FR', 'JP', 'CN', 'SG', 'AE', 'QA', 'HK', 'CA'}:
            return False

    for state in _US_STATE_NAMES_LOWER:
        if state in lower:
            return True

    return False


def _is_us_bar_admission(text: str) -> bool:
    """Return True if text looks like a US state bar admission."""
    lower = text.lower()
    for state in _US_STATE_NAMES_LOWER:
        if state in lower:
            return True
    # State code pattern
    m = re.search(r'\b([A-Z]{2})\b', text)
    if m and m.group(1) in _US_STATE_CODES_SET:
        return True
    return False


def _parse_education_text(text: str) -> EducationRecord | None:
    """Parse 'J.D., Harvard Law School (2010)' style text."""
    if not text or len(text) < 5:
        return None
    try:
        degree_match = re.search(
            r'\b(J\.?D\.?|LL\.?M\.?|LL\.?B\.?|B\.?A\.?|B\.?S\.?|M\.?B\.?A\.?|M\.?A\.?|M\.?S\.?|Ph\.?D\.?)\b',
            text
        )
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        degree = degree_match.group(0) if degree_match else None
        year = int(year_match.group(0)) if year_match else None

        school_text = text
        if degree_match:
            school_text = school_text.replace(degree_match.group(0), '')
        if year_match:
            school_text = school_text.replace(year_match.group(0), '')
        school_text = re.sub(r'[,\(\)\[\]]', ' ', school_text)
        school_text = re.sub(r'\s+', ' ', school_text).strip(' -–')

        if school_text and len(school_text) >= 5:
            return EducationRecord(degree=degree, school=school_text, year=year)
    except Exception:
        pass
    return None


def _firm_search_tokens(firm_name: str) -> list[str]:
    """
    Extract the most distinctive tokens from a firm name for bar searches.
    Skips common suffixes: LLP, LLC, PC, & etc.
    Returns up to 3 tokens ordered by specificity.
    """
    SKIP_WORDS = {
        'llp', 'llc', 'pc', 'pa', 'pllc', 'lpa', 'apc',
        'law', 'group', 'firm', 'offices', 'attorneys', 'lawyers',
        '&', 'and', 'the', 'of',
    }
    words = re.split(r'[\s,&]+', firm_name)
    tokens = [w.strip(' .,') for w in words if w.strip(' .,').lower() not in SKIP_WORDS and len(w.strip(' .,')) >= 3]
    # Return longest tokens first (more specific)
    tokens.sort(key=len, reverse=True)
    return tokens[:3]
