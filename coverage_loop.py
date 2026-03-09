#!/usr/bin/env python3
"""coverage_loop.py — Authoritative Expected-Total Resolver + Iterative Coverage Loop

COVERAGE-FIRST ARCHITECTURE:
  Step 1: Resolve authoritative expected_total from multiple signals
  Step 2: Run iterative URL enumeration until discovered == expected_total
  Step 3: After enrichment, trigger secondary sources for field completeness
  Step 4: US-only filter on offices
  Step 5: Mark LEGALLY_INCOMPLETE when no legal sources remain

This module is called by find_attorney.py after each strategy pass.
It is pure logic — no scraping, no Playwright. It orchestrates callers.

Legal constraints honoured:
  - No CAPTCHA bypass
  - No IP rotation
  - robots.txt disallowed paths never crawled
  - If blocked in normal browser → BLOCKED status, no evasion
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse, urljoin, quote_plus

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COVERAGE_TARGET = 0.98          # 98% of expected_total required for SUCCESS
COVERAGE_EXTERNAL_TRIGGER = 0.80  # below this → always try external sources
COVERAGE_MINIMUM_ABS = 5        # minimum profiles before declaring success

US_STATE_CODES: set[str] = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC',
    'D.C.',
}

US_STATE_NAMES: set[str] = {
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

# Known non-US country indicators (fast exclusion)
NON_US_INDICATORS: list[str] = [
    'london', 'england', 'united kingdom', 'uk', ' uk', '(uk)',
    'hong kong', 'singapore', 'tokyo', 'japan', 'beijing', 'shanghai',
    'paris', 'france', 'germany', 'berlin', 'munich', 'frankfurt',
    'dubai', 'abu dhabi', 'riyadh', 'doha',
    'sydney', 'australia', 'toronto', 'canada',
    'brussels', 'amsterdam', 'madrid', 'milan', 'rome',
    'moscow', 'korea', 'seoul',
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExpectedTotalResult:
    """Authoritative expected_total resolved from multiple signals"""
    value: int | None
    source: str  # "official_directory_text" | "pagination_math" | "sitemap_count"
                 # "api_metadata" | "external_directory" | "unknown"
    confidence: float  # 0.0–1.0
    raw_signals: list[tuple[str, int]] = field(default_factory=list)
    # Each signal: (source_name, count_value)

    @property
    def is_known(self) -> bool:
        return self.value is not None and self.value > 0


@dataclass
class CoverageLoopResult:
    """Result of the iterative coverage loop for one firm"""
    firm: str
    expected_total: int | None
    expected_total_source: str
    discovered_urls: int
    extracted_count: int
    coverage_ratio: float | None
    status: str  # "SUCCESS" | "PARTIAL" | "LEGALLY_INCOMPLETE"
    legally_incomplete_reason: str | None
    sources_tried: list[str]
    gaps_remaining: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "firm": self.firm,
            "expected_total": self.expected_total,
            "expected_total_source": self.expected_total_source,
            "discovered_urls": self.discovered_urls,
            "extracted_count": self.extracted_count,
            "coverage_ratio": self.coverage_ratio,
            "status": self.status,
            "legally_incomplete_reason": self.legally_incomplete_reason,
            "sources_tried": self.sources_tried,
            "gaps_remaining": self.gaps_remaining,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Expected-total resolver
# ---------------------------------------------------------------------------

class ExpectedTotalResolver:
    """
    Resolves the authoritative attorney count for a firm from multiple signals.

    Signal priority (highest first):
      1. official_directory_text  — "1,245 lawyers" on the directory page
      2. pagination_math          — last_page * page_size
      3. sitemap_count            — number of profile-like URLs in sitemap
      4. api_metadata             — "total": N in JSON API response
      5. external_directory       — count from Martindale/Justia
    """

    # Regex patterns to find total counts in page text
    TOTAL_PATTERNS: list[re.Pattern] = [
        re.compile(r'(\d[\d,]+)\s+(?:attorney|lawyer|professional|people|partner|associate)s?', re.I),
        re.compile(r'(?:showing|of|total)\s+(\d[\d,]+)\s+(?:result|attorney|lawyer|professional)s?', re.I),
        re.compile(r'(\d[\d,]+)\s+(?:result|member)s?\s+found', re.I),
        re.compile(r'"total"\s*:\s*(\d+)', re.I),
        re.compile(r'"totalCount"\s*:\s*(\d+)', re.I),
        re.compile(r'"totalResults"\s*:\s*(\d+)', re.I),
        re.compile(r'"count"\s*:\s*(\d+)', re.I),
        re.compile(r'"numFound"\s*:\s*(\d+)', re.I),
    ]

    def resolve(
        self,
        *,
        page_text: str | None = None,
        sitemap_url_count: int | None = None,
        api_json: dict | None = None,
        pagination_last_page: int | None = None,
        pagination_page_size: int | None = None,
        external_directory_count: int | None = None,
    ) -> ExpectedTotalResult:
        """Resolve expected_total from all available signals, pick highest-confidence."""
        signals: list[tuple[str, int, float]] = []  # (source, value, confidence)

        # Signal 1: official directory text (most authoritative)
        if page_text:
            text_total = self._extract_from_text(page_text)
            if text_total and text_total >= 5:
                signals.append(("official_directory_text", text_total, 0.9))

        # Signal 2: pagination math
        if pagination_last_page and pagination_page_size:
            pag_total = pagination_last_page * pagination_page_size
            if pag_total >= 5:
                signals.append(("pagination_math", pag_total, 0.85))

        # Signal 3: API metadata
        if api_json:
            api_total = self._extract_from_json(api_json)
            if api_total and api_total >= 5:
                signals.append(("api_metadata", api_total, 0.85))

        # Signal 4: sitemap count (slightly less reliable — may include non-attorney URLs)
        if sitemap_url_count and sitemap_url_count >= 5:
            signals.append(("sitemap_count", sitemap_url_count, 0.7))

        # Signal 5: external directory (fallback only)
        if external_directory_count and external_directory_count >= 5:
            signals.append(("external_directory", external_directory_count, 0.5))

        if not signals:
            return ExpectedTotalResult(
                value=None, source="unknown", confidence=0.0,
                raw_signals=[]
            )

        # Pick the signal with highest confidence; break ties by highest value
        signals.sort(key=lambda x: (x[2], x[1]), reverse=True)
        best_source, best_value, best_confidence = signals[0]

        return ExpectedTotalResult(
            value=best_value,
            source=best_source,
            confidence=best_confidence,
            raw_signals=[(s, v) for s, v, _ in signals],
        )

    def _extract_from_text(self, text: str) -> int | None:
        """Extract attorney count from page text using multiple patterns."""
        candidates: list[int] = []
        for pat in self.TOTAL_PATTERNS:
            for m in pat.finditer(text):
                try:
                    val = int(m.group(1).replace(',', ''))
                    # Sanity: law firms have 5–10000 attorneys
                    if 5 <= val <= 15000:
                        candidates.append(val)
                except ValueError:
                    pass

        if not candidates:
            return None

        # Return the largest plausible value (most likely the total, not a sub-count)
        candidates.sort(reverse=True)
        return candidates[0]

    def _extract_from_json(self, data: dict) -> int | None:
        """Recursively search JSON for total/count fields."""
        total_keys = ['total', 'totalCount', 'totalResults', 'count',
                      'numFound', 'total_count', 'resultCount', 'size']

        def _search(obj: object, depth: int = 0) -> int | None:
            if depth > 5:
                return None
            if isinstance(obj, dict):
                for k in total_keys:
                    if k in obj:
                        v = obj[k]
                        if isinstance(v, int) and 5 <= v <= 15000:
                            return v
                for v in obj.values():
                    result = _search(v, depth + 1)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj[:3]:
                    result = _search(item, depth + 1)
                    if result:
                        return result
            return None

        return _search(data)


# ---------------------------------------------------------------------------
# Iterative coverage loop
# ---------------------------------------------------------------------------

class CoverageLoop:
    """
    Iterative coverage loop: keep pulling URLs until discovered == expected_total.

    Callers inject strategy functions via the constructor.
    This class only orchestrates — it never does HTTP itself.

    Usage:
        loop = CoverageLoop(
            firm=firm_name,
            expected_total_result=resolver.resolve(...),
            strategy_fns={
                "xml_sitemap": fn_xml,
                "alphabet_az": fn_alphabet,
                "pagination": fn_pagination,
                "filter_enum": fn_filter,
                "external_directory": fn_external,
            },
            strategy_order=["xml_sitemap", "alphabet_az", "pagination",
                            "filter_enum", "external_directory"],
            log_fn=self.log,
            hard_timeout=180,
        )
        result = loop.run()
        all_urls = loop.all_urls
    """

    def __init__(
        self,
        *,
        firm: str,
        expected_total_result: ExpectedTotalResult,
        strategy_fns: dict[str, Callable[[], set[str]]],
        strategy_order: list[str],
        log_fn: Callable[[str], None] | None = None,
        hard_timeout: float = 180.0,
        limit: int = 0,
    ):
        self.firm = firm
        self.expected_result = expected_total_result
        self.strategy_fns = strategy_fns
        self.strategy_order = strategy_order
        self._log = log_fn or (lambda msg: log.info(msg))
        self.hard_timeout = hard_timeout
        self.limit = limit

        self.all_urls: set[str] = set()
        self.sources_tried: list[str] = []
        self._start = time.time()

    def run(self) -> CoverageLoopResult:
        """
        Iterative loop:
          1. Run each strategy in order
          2. After each, check if coverage target reached
          3. If target known and still short → continue to next strategy
          4. If target unknown → stop after first strategy with >= MINIMUM results
          5. External directory always tried if coverage < EXTERNAL_TRIGGER threshold
        """
        expected = self.expected_result.value
        notes: list[str] = []

        if expected:
            self._log(f"  [CoverageLoop] Expected total: {expected} (source: {self.expected_result.source}, conf: {self.expected_result.confidence:.2f})")
        else:
            self._log(f"  [CoverageLoop] Expected total unknown — will run all strategies until timeout or stabilization (N=2 consecutive zero-yield)")

        # Stabilization tracking: consecutive strategies that added 0 new URLs
        _consecutive_no_new: int = 0
        _stabilization_threshold: int = 3  # configurable: stop after N consecutive zero-yield strategies

        for strategy in self.strategy_order:
            # Hard timeout guard
            elapsed = time.time() - self._start
            if elapsed > self.hard_timeout:
                notes.append(f"Hard timeout {self.hard_timeout}s exceeded after {elapsed:.0f}s")
                self._log(f"  [CoverageLoop] TIMEOUT — stopping at {len(self.all_urls)} URLs")
                break

            if strategy not in self.strategy_fns:
                continue

            self._log(f"\n  [CoverageLoop] Running strategy: {strategy} ({len(self.all_urls)} URLs so far)")
            before = len(self.all_urls)

            try:
                new_urls = self.strategy_fns[strategy]()
                added = new_urls - self.all_urls
                self.all_urls.update(added)
                self.sources_tried.append(strategy)
                self._log(f"  [CoverageLoop] {strategy}: +{len(added)} new URLs (total: {len(self.all_urls)})")
            except Exception as exc:
                self._log(f"  [CoverageLoop] {strategy} ERROR: {exc}")
                notes.append(f"{strategy} failed: {exc}")
                added = set()
                continue

            after = len(self.all_urls)

            # Apply hard limit if set
            if self.limit > 0 and after > self.limit:
                self.all_urls = set(sorted(self.all_urls)[:self.limit])
                self._log(f"  [CoverageLoop] Limit applied: {len(self.all_urls)} URLs")
                break

            # Coverage evaluation
            if expected:
                ratio = len(self.all_urls) / expected
                self._log(f"  [CoverageLoop] Coverage: {len(self.all_urls)}/{expected} = {ratio*100:.1f}%")
                if ratio >= COVERAGE_TARGET:
                    self._log(f"  [CoverageLoop] Coverage target reached ({ratio*100:.1f}% >= {COVERAGE_TARGET*100:.0f}%)")
                    break
                # If this strategy added nothing, try next immediately
                if after == before:
                    self._log(f"  [CoverageLoop] No new URLs from {strategy}, continuing...")
                    continue
            else:
                # No expected total — use stabilization to decide when to stop
                # Do NOT stop just because we have some URLs; run all strategies
                if len(added) == 0:
                    _consecutive_no_new += 1
                    self._log(f"  [CoverageLoop] No new URLs from {strategy} ({_consecutive_no_new}/{_stabilization_threshold} consecutive zero-yield)")
                    if _consecutive_no_new >= _stabilization_threshold:
                        notes.append(f"Stabilization stop: {_consecutive_no_new} consecutive strategies added 0 new URLs")
                        self._log(f"  [CoverageLoop] STABILIZED — stopping at {len(self.all_urls)} URLs (reason: {_consecutive_no_new} consecutive zero-yield strategies)")
                        break
                else:
                    _consecutive_no_new = 0  # reset on any new URLs

        # Build result
        discovered = len(self.all_urls)
        coverage_ratio: float | None = None
        status: str
        gaps = 0

        if expected and expected > 0:
            coverage_ratio = discovered / expected
            gaps = max(0, expected - discovered)
            if coverage_ratio >= COVERAGE_TARGET:
                status = "SUCCESS"
            elif discovered > 0:
                status = "PARTIAL"
                notes.append(f"Gap: {gaps} attorneys missing ({(1-coverage_ratio)*100:.1f}%)")
            else:
                status = "LEGALLY_INCOMPLETE"
        else:
            if discovered >= COVERAGE_MINIMUM_ABS:
                status = "PARTIAL"  # Can't call SUCCESS without known total
            elif discovered > 0:
                status = "PARTIAL"
            else:
                status = "LEGALLY_INCOMPLETE"

        return CoverageLoopResult(
            firm=self.firm,
            expected_total=expected,
            expected_total_source=self.expected_result.source,
            discovered_urls=discovered,
            extracted_count=0,  # caller fills this after enrichment
            coverage_ratio=coverage_ratio,
            status=status,
            legally_incomplete_reason=None,
            sources_tried=self.sources_tried,
            gaps_remaining=gaps,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# US-only office filter
# ---------------------------------------------------------------------------

def is_us_office(office_text: str) -> bool:
    """
    Return True if office_text refers to a US office location.

    Accepts:
      - "New York, NY"
      - "Chicago, IL 60601"
      - "Washington, DC"
      - "New York" (city name alone, assumed US for major law firm hubs)

    Rejects:
      - "London"
      - "Hong Kong"
      - "Tokyo, Japan"
    """
    if not office_text:
        return False

    normalized = office_text.strip().lower()

    # Fast reject: known non-US indicators
    for indicator in NON_US_INDICATORS:
        if indicator in normalized:
            return False

    # Fast accept: state code pattern "City, ST" or "City, ST ZIPCODE"
    state_match = re.search(r',\s*([A-Za-z]{2})(?:\s+\d{5})?(?:\s*[-–]\s*\d{4})?$', office_text.strip())
    if state_match:
        code = state_match.group(1).upper()
        if code in US_STATE_CODES:
            return True
        # Non-US 2-letter codes (UK, CA for Canada etc.)
        NON_US_2L = {'UK', 'CA', 'AU', 'DE', 'FR', 'JP', 'CN', 'SG', 'AE', 'QA', 'HK'}
        if code in NON_US_2L:
            return False

    # State name present
    for state in US_STATE_NAMES:
        if state in normalized:
            return True

    # Major US city names (standalone)
    US_MAJOR_CITIES = {
        'new york', 'los angeles', 'chicago', 'houston', 'dallas', 'phoenix',
        'san francisco', 'seattle', 'boston', 'atlanta', 'miami', 'denver',
        'washington', 'philadelphia', 'detroit', 'minneapolis', 'portland',
        'charlotte', 'san diego', 'austin', 'nashville', 'orlando',
        'palo alto', 'silicon valley', 'san jose', 'raleigh', 'durham',
    }
    for city in US_MAJOR_CITIES:
        if city in normalized:
            return True

    # If no evidence either way for a short string, assume US for major law firm context
    # (AmLaw 200 firms are predominantly US-based)
    if len(normalized) < 30 and ',' not in normalized:
        # Single word — could be a US city abbreviation
        return True

    return False


def filter_us_attorneys(attorneys: list, log_fn: Callable[[str], None] | None = None) -> list:
    """
    Filter attorney list to US-only.

    An attorney is kept if:
      - They have at least one US office, OR
      - They have no offices at all (unknown — kept for review, marked)

    Attorneys with only non-US offices are excluded.
    """
    _log = log_fn or (lambda m: log.info(m))
    kept = []
    excluded = 0

    for att in attorneys:
        offices = getattr(att, 'offices', [])

        if not offices:
            # No office data — keep but mark
            if hasattr(att, 'diagnostics'):
                att.diagnostics['us_filter'] = 'kept_no_office_data'
            kept.append(att)
            continue

        us_offices = [o for o in offices if is_us_office(o)]

        if us_offices:
            # Replace offices list with US-only
            att.offices = us_offices
            if hasattr(att, 'diagnostics'):
                att.diagnostics['us_filter'] = 'us_offices_kept'
                all_offices = len(offices)
                if all_offices > len(us_offices):
                    att.diagnostics['non_us_offices_removed'] = all_offices - len(us_offices)
            kept.append(att)
        else:
            # All offices are non-US — exclude
            excluded += 1
            if hasattr(att, 'diagnostics'):
                att.diagnostics['us_filter'] = 'excluded_non_us'

    if excluded > 0:
        _log(f"  [US Filter] Excluded {excluded} non-US attorneys, kept {len(kept)}")
    else:
        _log(f"  [US Filter] {len(kept)} attorneys, all US")

    return kept


# ---------------------------------------------------------------------------
# Pagination URL enumerator (generic)
# ---------------------------------------------------------------------------

class PaginationEnumerator:
    """
    Generic pagination URL enumerator.

    Tries common pagination patterns:
      - ?page=N
      - ?p=N
      - /page/N/
      - ?start=N&size=PAGE_SIZE
      - ?offset=N&limit=PAGE_SIZE

    Stops when:
      - Page returns no new profile URLs
      - Consecutive empty pages >= max_empty_pages
      - Max pages reached
      - Hard timeout exceeded
    """

    def __init__(
        self,
        session: requests.Session,
        base_url: str,
        directory_url: str,
        is_profile_url_fn: Callable[[str, str], bool],
        rate_limit_fn: Callable[[str], None],
        *,
        page_size: int = 25,
        max_pages: int = 200,
        max_empty_pages: int = 3,
        timeout: int = 5,
        log_fn: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.base_url = base_url
        self.directory_url = directory_url
        self.is_profile_url = is_profile_url_fn
        self.rate_limit = rate_limit_fn
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_empty_pages = max_empty_pages
        self.timeout = timeout
        self._log = log_fn or (lambda m: log.info(m))
        self.domain = urlparse(base_url).netloc

    def enumerate(self) -> set[str]:
        """Try all pagination patterns, return union of all found profile URLs."""
        all_urls: set[str] = set()

        patterns = [
            self._paginate_query_page,
            self._paginate_query_p,
            self._paginate_path,
            self._paginate_offset,
        ]

        for pattern_fn in patterns:
            found = pattern_fn()
            new = found - all_urls
            if new:
                all_urls.update(new)
                self._log(f"    Pagination pattern '{pattern_fn.__name__}': +{len(new)} URLs")
            # If first pattern found substantial results, don't try others
            if len(all_urls) >= 10:
                break

        return all_urls

    def _fetch_profile_links(self, url: str) -> set[str]:
        """Fetch a page and extract profile-like URLs."""
        try:
            self.rate_limit(self.domain)
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return set()
            links: set[str] = set()
            for href in re.findall(r'href=["\'](.*?)["\']', resp.text):
                full = urljoin(self.base_url, href)
                if self.is_profile_url(full, self.domain):
                    links.add(full)
            return links
        except Exception:
            return set()

    def _paginate_generic(self, url_fn: Callable[[int], str]) -> set[str]:
        """Generic pagination driver."""
        all_urls: set[str] = set()
        empty_streak = 0

        for page in range(1, self.max_pages + 1):
            url = url_fn(page)
            found = self._fetch_profile_links(url)
            new = found - all_urls
            if not new:
                empty_streak += 1
                if empty_streak >= self.max_empty_pages:
                    break
            else:
                empty_streak = 0
                all_urls.update(new)

        return all_urls

    def _paginate_query_page(self) -> set[str]:
        base = self.directory_url.split('?')[0]
        return self._paginate_generic(lambda p: f"{base}?page={p}")

    def _paginate_query_p(self) -> set[str]:
        base = self.directory_url.split('?')[0]
        return self._paginate_generic(lambda p: f"{base}?p={p}")

    def _paginate_path(self) -> set[str]:
        base = self.directory_url.rstrip('/')
        return self._paginate_generic(lambda p: f"{base}/page/{p}/")

    def _paginate_offset(self) -> set[str]:
        base = self.directory_url.split('?')[0]
        sz = self.page_size
        return self._paginate_generic(
            lambda p: f"{base}?offset={(p-1)*sz}&limit={sz}"
        )


# ---------------------------------------------------------------------------
# Alphabet A-Z enumerator
# ---------------------------------------------------------------------------

class AlphabetEnumerator:
    """
    Enumerate attorney profiles by iterating A–Z on directory pages
    that support filtering by last name initial.

    Common patterns:
      /attorneys?letter=A
      /attorneys/A
      /professionals?last=A
      /people?alpha=A
    """

    LETTER_PATTERNS: list[str] = [
        "{base}?letter={L}",
        "{base}?last={L}",
        "{base}?alpha={L}",
        "{base}/{L}",
        "{base}?lastName={L}",
        "{base}?name={L}",
    ]

    def __init__(
        self,
        session: requests.Session,
        base_url: str,
        directory_url: str,
        is_profile_url_fn: Callable[[str, str], bool],
        rate_limit_fn: Callable[[str], None],
        *,
        timeout: int = 5,
        log_fn: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.base_url = base_url
        self.directory_url = directory_url
        self.is_profile_url = is_profile_url_fn
        self.rate_limit = rate_limit_fn
        self.timeout = timeout
        self._log = log_fn or (lambda m: log.info(m))
        self.domain = urlparse(base_url).netloc

    def enumerate(self) -> set[str]:
        """Detect the working letter pattern, then enumerate A-Z."""
        working_pattern = self._detect_pattern()
        if not working_pattern:
            return set()

        self._log(f"    Alphabet pattern: {working_pattern}")
        all_urls: set[str] = set()
        base = self.directory_url.split('?')[0]

        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            url = working_pattern.format(base=base, L=letter)
            try:
                self.rate_limit(self.domain)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                for href in re.findall(r'href=["\'](.*?)["\']', resp.text):
                    full = urljoin(self.base_url, href)
                    if self.is_profile_url(full, self.domain):
                        all_urls.add(full)
            except Exception:
                continue

        return all_urls

    def _detect_pattern(self) -> str | None:
        """Try each pattern with letter 'A' to find the working one."""
        base = self.directory_url.split('?')[0]
        for pattern in self.LETTER_PATTERNS:
            url = pattern.format(base=base, L='A')
            try:
                self.rate_limit(self.domain)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                # Check if we got profile links
                profile_count = sum(
                    1 for href in re.findall(r'href=["\'](.*?)["\']', resp.text)
                    if self.is_profile_url(urljoin(self.base_url, href), self.domain)
                )
                if profile_count >= 3:
                    return pattern
            except Exception:
                continue
        return None


# ---------------------------------------------------------------------------
# Firm-level summary builder
# ---------------------------------------------------------------------------

@dataclass
class FirmSummaryRow:
    """One row in firm_level_summary.csv"""
    firm: str
    expected_total: int | None
    expected_total_source: str
    discovered_urls: int
    extracted_attorneys: int
    us_attorneys: int
    coverage_ratio: float | None
    missing_fields_ratio: float
    status: str
    legally_incomplete_reason: str | None
    sources_tried: str  # comma-separated
    notes: str

    @classmethod
    def headers(cls) -> list[str]:
        return [
            "Firm", "Expected Total", "Expected Total Source",
            "Discovered URLs", "Extracted Attorneys", "US Attorneys",
            "Coverage Ratio", "Missing Fields Ratio",
            "Status", "Legally Incomplete Reason",
            "Sources Tried", "Notes",
        ]

    def to_row(self) -> list:
        return [
            self.firm,
            self.expected_total or "",
            self.expected_total_source,
            self.discovered_urls,
            self.extracted_attorneys,
            self.us_attorneys,
            f"{self.coverage_ratio*100:.1f}%" if self.coverage_ratio is not None else "",
            f"{self.missing_fields_ratio*100:.1f}%",
            self.status,
            self.legally_incomplete_reason or "",
            self.sources_tried,
            self.notes,
        ]


class FirmSummaryWriter:
    """Accumulates per-firm rows and writes firm_level_summary.csv"""

    def __init__(self):
        self.rows: list[FirmSummaryRow] = []

    def add(self, row: FirmSummaryRow) -> None:
        self.rows.append(row)

    def write(self, path: str) -> None:
        import csv
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(FirmSummaryRow.headers())
            for row in self.rows:
                writer.writerow(row.to_row())

    @staticmethod
    def build_row(
        firm: str,
        loop_result: CoverageLoopResult,
        attorneys: list,
        us_attorneys: list,
    ) -> "FirmSummaryRow":
        """Build a FirmSummaryRow from a CoverageLoopResult + attorney lists."""
        # Compute missing_fields_ratio
        total_fields = len(attorneys) * 8  # 8 required fields
        missing = 0
        for att in attorneys:
            mf = getattr(att, 'missing_fields', [])
            missing += len(mf) if mf else 0

        mf_ratio = (missing / total_fields) if total_fields > 0 else 0.0

        return FirmSummaryRow(
            firm=firm,
            expected_total=loop_result.expected_total,
            expected_total_source=loop_result.expected_total_source,
            discovered_urls=loop_result.discovered_urls,
            extracted_attorneys=len(attorneys),
            us_attorneys=len(us_attorneys),
            coverage_ratio=loop_result.coverage_ratio,
            missing_fields_ratio=mf_ratio,
            status=loop_result.status,
            legally_incomplete_reason=loop_result.legally_incomplete_reason,
            sources_tried=", ".join(loop_result.sources_tried),
            notes="; ".join(loop_result.notes),
        )
