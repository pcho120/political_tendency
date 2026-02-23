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
2. Martindale-Hubbell (public search, no auth, paginated)
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
_DELAY_MARTINDALE = 1.5    # 1 req/1.5s
_DELAY_CALBAR = 1.5
_DELAY_TXBAR = 1.5
_MAX_PAGES = 40            # never paginate > 40 pages per source
_MAX_EMPTY_PAGES = 3       # stop after N consecutive pages with 0 results


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
            href = a['href']
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
        Martindale-Hubbell: https://www.martindale.com/search/
        API-based JSON endpoint; paginated via start parameter.
        """
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        empty_streak = 0

        # Martindale uses a JSON search API
        api_base = "https://www.martindale.com/api/search"
        page_size = 25

        for page in range(1, _MAX_PAGES + 1):
            if len(attorneys) >= max_results:
                break
            start = (page - 1) * page_size
            params = {
                "q": firm_name,
                "start": start,
                "rows": page_size,
                "type": "lawyer",
            }
            try:
                time.sleep(_DELAY_MARTINDALE)
                resp = self.session.get(api_base, params=params, timeout=self.timeout)
                if resp.status_code != 200:
                    # Fallback to HTML search if JSON API returns non-200
                    if page == 1:
                        html_attorneys, html_summary = self._extract_martindale_html(firm_name, max_results)
                        return html_attorneys, html_summary
                    errors.append(f"HTTP {resp.status_code} at page {page}")
                    break

                pages_fetched += 1
                try:
                    data = resp.json()
                except Exception:
                    # Not JSON — try HTML fallback on page 1
                    if page == 1:
                        html_attorneys, html_summary = self._extract_martindale_html(firm_name, max_results)
                        return html_attorneys, html_summary
                    break

                # Extract total from first page
                if page == 1:
                    estimated_total = _json_search_total(data)

                profiles_data = _json_search_results(data)
                if not profiles_data:
                    empty_streak += 1
                    if empty_streak >= _MAX_EMPTY_PAGES:
                        break
                    continue
                empty_streak = 0

                for item in profiles_data:
                    if len(attorneys) >= max_results:
                        break
                    profile = _martindale_item_to_profile(item, firm_name)
                    if profile and profile.full_name:
                        attorneys.append(profile)

            except Exception as exc:
                errors.append(f"page {page}: {exc}")
                if page == 1:
                    # Attempt HTML fallback
                    try:
                        html_attorneys, html_summary = self._extract_martindale_html(firm_name, max_results)
                        return html_attorneys, html_summary
                    except Exception:
                        pass
                break

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
        """HTML fallback for Martindale when JSON API is unavailable."""
        attorneys: list[AttorneyProfile] = []
        errors: list[str] = []
        pages_fetched = 0
        estimated_total: int | None = None
        empty_streak = 0

        base_url = f"https://www.martindale.com/search/#q={quote_plus(firm_name)}&con=13"

        for page in range(1, _MAX_PAGES + 1):
            if len(attorneys) >= max_results:
                break
            url = f"{base_url}&page={page}" if page > 1 else base_url
            try:
                time.sleep(_DELAY_MARTINDALE)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    errors.append(f"HTTP {resp.status_code} at page {page}")
                    break
                pages_fetched += 1
                soup = BeautifulSoup(resp.text, 'html.parser')

                if page == 1:
                    estimated_total = self._parse_total_count(resp.text)

                profile_cards = self._martindale_html_cards(soup, firm_name)
                if not profile_cards:
                    empty_streak += 1
                    if empty_streak >= _MAX_EMPTY_PAGES:
                        break
                    continue
                empty_streak = 0
                attorneys.extend(profile_cards[:max_results - len(attorneys)])

            except Exception as exc:
                errors.append(f"page {page}: {exc}")
                break

        summary = ExternalResult(
            source="martindale_html",
            profiles_found=len(attorneys),
            estimated_total=estimated_total,
            pages_fetched=pages_fetched,
            errors=errors,
        )
        return attorneys, summary

    def _martindale_html_cards(
        self, soup: BeautifulSoup, firm_name: str
    ) -> list[AttorneyProfile]:
        """Parse attorney cards from Martindale search results HTML."""
        profiles = []
        # Martindale result cards use various class patterns
        card_selectors = [
            {'class': re.compile(r'result[-_]item|attorney[-_]card|lawyer[-_]card|profile[-_]card', re.I)},
            {'class': re.compile(r'card|listing|result', re.I)},
        ]
        cards = []
        for selector in card_selectors:
            cards = soup.find_all(['div', 'li', 'article'], selector)
            if cards:
                break

        for card in cards:
            try:
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
                    href = link['href']
                    profile.profile_url = urljoin("https://www.martindale.com", href) if href.startswith('/') else href

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

                if profile.full_name:
                    profile.calculate_status()
                    profiles.append(profile)
            except Exception:
                continue

        return profiles

    def _martindale_estimate_count(self, firm_name: str) -> int | None:
        try:
            url = f"https://www.martindale.com/search/#q={quote_plus(firm_name)}&con=13"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return self._parse_total_count(resp.text)
        except Exception:
            pass
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
                params["CurrentPage"] = page

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
                    href = link['href']
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
                    href = link['href']
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
        data = extruct.extract(
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
            profile.profile_url = url if url.startswith('http') else \
                f"https://www.martindale.com{url}"

        # Office / city
        city = item.get('city') or item.get('location') or ''
        state = item.get('state') or item.get('stateCode') or ''
        if city or state:
            office = f"{city}, {state}".strip(', ')
            if _is_us_location(office):
                profile.offices.append(office)

        # Practice areas
        for pa in (item.get('practiceAreas') or item.get('areas') or []):
            if isinstance(pa, str) and pa not in profile.practice_areas:
                profile.practice_areas.append(pa)
            elif isinstance(pa, dict):
                name = pa.get('name', '')
                if name and name not in profile.practice_areas:
                    profile.practice_areas.append(name)

        return profile if profile.full_name else None
    except Exception:
        return None


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
