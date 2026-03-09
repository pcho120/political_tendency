#!/usr/bin/env python3
"""discovery.py - Alphabetical Attorney Discovery Engine
PART 1 of the AmLaw200 extraction system.
Implements:
  - SITEMAP_XML: walk robots.txt → sitemap → filter attorney URLs by path prefix
  - Alphabetical (A-Z) crawl with JSON API auto-detection
  - Page-based pagination driven by TotalSearchResults
  - HTML fallback for non-JSON responses
  - Automatic alphabet-nav detection via URL/link inspection
  - Deduplication of profile URLs
  - Playwright escalation only when static requests fail
    urls = discover_attorneys(firm_url, structure_info=...)  ->  list[str]
"""

from __future__ import annotations

import gzip
import json
import re
import string
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse, urlunparse, urljoin, parse_qs

import requests

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False  # pyright: ignore[reportConstantRedefinition]
    BeautifulSoup = None  # type: ignore[assignment,misc]

from debug_logger import DebugLogger

# ---------------------------------------------------------------------------
# Site structures cache (loaded once at module import)
# ---------------------------------------------------------------------------

_SITE_STRUCTURES_PATH = Path(__file__).parent / "site_structures.json"
_site_structures_cache: dict[str, dict[str, Any]] | None = None


def _load_site_structures() -> dict[str, dict[str, Any]]:
    """Load site_structures.json and index by normalised base URL."""
    global _site_structures_cache
    if _site_structures_cache is not None:
        return _site_structures_cache
    if not _SITE_STRUCTURES_PATH.exists():
        _site_structures_cache = {}
        return _site_structures_cache
    raw: list[dict[str, Any]] = json.loads(_SITE_STRUCTURES_PATH.read_text(encoding="utf-8"))
    cache: dict[str, dict[str, Any]] = {}
    for entry in raw:
        url = entry.get("url", "").rstrip("/")
        if url:
            cache[url] = entry
        # Also index by netloc for fuzzy lookup
        netloc = urlparse(url).netloc
        if netloc:
            cache[netloc] = entry
    _site_structures_cache = cache
    return _site_structures_cache


def lookup_structure(firm_url: str) -> dict[str, Any] | None:
    """Return the site_structures entry for *firm_url*, or None if not found."""
    structs = _load_site_structures()
    base = firm_url.rstrip("/")
    if base in structs:
        return structs[base]
    netloc = urlparse(base).netloc
    if netloc in structs:
        return structs[netloc]
    # Strip 'www.' prefix for looser match
    netloc_bare = netloc.removeprefix("www.")
    for key, val in structs.items():
        if urlparse(key).netloc.removeprefix("www.") == netloc_bare:
            return val
    return None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LETTERS = list(string.ascii_uppercase)  # A-Z

# Common directory paths tried when no alphabet nav is found
DIRECTORY_PATHS = [
    "/attorneys",
    "/lawyers",
    "/people",
    "/professionals",
    "/our-team",
    "/team",
    "/attorneys-advisors",
    "/lawyers-advisors",
]

# URL path patterns that strongly suggest an attorney profile
_PROFILE_PATH_RE = re.compile(
    r"/(?:attorney|lawyer|people|professional|person|bio|profile)s?/"
    r"[a-z0-9\-]{3,}",
    re.IGNORECASE,
)

# Pagination param names - tried in order when probing page-based APIs
_PAGE_PARAMS = ["Page", "page", "p", "pagenum", "pageNumber"]
_OFFSET_PARAMS = ["offset", "start", "from", "skip"]
_MAX_PAGES_PER_LETTER = 50
_DEFAULT_PAGE_SIZE = 10   # conservative fallback when page 1 returns 0 items

# Sitemap path keywords — used to filter sitemap URLs for attorney profiles
_ATTORNEY_PATH_RE = re.compile(
    r"/(lawyers?|attorneys?|people|professionals?|bio|profile|person|team|our-people)/",
    re.IGNORECASE,
)

# Non-English locale prefixes — skip these sitemaps first (prefer English)
_LOCALE_RE = re.compile(
    r"/(?:de|es|fr|ja|zh|ko|pt|ru|it|nl|pl|sv|tr|da|fi|ar|nl-nl|de-de|zh-hans|zh-hant)"
    r"(?:/|$)",
    re.IGNORECASE,
)

# Paths that look like directories/categories, not individual profiles
_CATEGORY_SUFFIX_RE = re.compile(
    r"/(our-work|our-offer|lateral-opportunities|your-career|career-advancement-program"
    r"|entry-level-or-lateral-attorneys|how-to-apply|technology-specialists"
    r"|attorney-professional-development|lateral-members|lateral-associates"
    r"|former-judicial-clerks|africa|asia|dubai|brussels|frankfurt"
    r"|asia-attorneys|europe-attorneys|lawyer-recruiting-contacts"
    r"|careers|join-us|search-results?|search-professionals?|alumni"
    r"|continuing-legal-education|cle|insights|news|events|press"
    r"|publications|articles|about-us?|contact|locations?"
    r"|awards?-rankings?|awards?|rankings?|firm|blog|resources|library"
    r"|practice-areas?|services|capabilities|thought-leadership)"
    r"(?:/|$)"
    r"|/(lawyers|professionals|attorneys|people|team)/?$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryResult:
    """Return value of discover_attorneys()."""
    firm_url: str
    profile_urls: list[str] = field(default_factory=list)
    strategy: str = "unknown"          # json_api | html_alphabet | html_directory | sitemap
    discovery_mode_used: str = "requests"  # "requests" | "playwright_scroll"
    total_discovered: int = 0
    letter_stats: dict[str, int] = field(default_factory=dict)   # letter → count
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SITEMAP_XML discovery  (111 firms)
# ---------------------------------------------------------------------------

def _derive_attorney_path_prefix(samples: list[str], firm_url: str) -> str | None:
    """
    Given a list of sample attorney profile URLs from site_structures.json,
    derive the common URL path prefix used to filter sitemap entries.

    Returns the prefix string (e.g. "/people", "/en/lawyers") or None.
    """
    if not samples:
        return None
    from urllib.parse import urlparse as _up

    def _profile_path(u: str) -> list[str]:
        path = _up(u).path
        parts = [p for p in path.strip("/").split("/") if p]
        return parts

    # Find the path segment(s) common across all sample URLs that look like
    # an attorney section (not just /en/ locale prefix alone).
    paths = [_profile_path(u) for u in samples if u]
    if not paths:
        return None

    # Walk segments in lockstep until they diverge
    min_len = min(len(p) for p in paths)
    common: list[str] = []
    for i in range(min_len):
        vals = {p[i] for p in paths}
        if len(vals) == 1:
            common.append(paths[0][i])
        else:
            break

    # The common prefix must be at least 1 segment and contain an attorney keyword
    _ATTY_SEG = re.compile(
        r"^(?:lawyers?|attorneys?|people|professionals?|bio|profile|person|team|"
        r"our-people|our-team|team-member|lawyers-advisors|members?)$",
        re.IGNORECASE,
    )
    # Pattern for a person-name slug (contains hyphens and digits, likely a name)
    _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-\._%]{4,}$", re.IGNORECASE)

    # Try full common prefix first, then peel back to find an attorney segment
    for length in range(len(common), 0, -1):
        prefix_segs = common[:length]
        if any(_ATTY_SEG.match(s) for s in prefix_segs):
            # Strip trailing single-character segments (alphabet sub-dirs like /a/, /b/)
            while prefix_segs and len(prefix_segs[-1]) == 1:
                prefix_segs = prefix_segs[:-1]
            # Strip trailing person-slug segments (e.g. /people/john-smith → /people)
            while prefix_segs and _SLUG_RE.match(prefix_segs[-1]) and not _ATTY_SEG.match(prefix_segs[-1]):
                prefix_segs = prefix_segs[:-1]
            # Strip trailing numeric-ID segments (e.g. /people/134273 → /people)
            while prefix_segs and re.match(r"^\d+$", prefix_segs[-1]):
                prefix_segs = prefix_segs[:-1]
            if prefix_segs:
                return "/" + "/".join(prefix_segs)
            break

    # Fallback: use just the first non-locale segment of the first sample
    firm_netloc = _up(firm_url).netloc
    for p in paths:
        for seg in p:
            if _ATTY_SEG.match(seg):
                # Return path up to and including this segment
                idx = p.index(seg)
                return "/" + "/".join(p[: idx + 1])

    return None


def _is_profile_url(url: str, prefix: str | None) -> bool:
    """
    Return True if *url* looks like an individual attorney profile
    (as opposed to a directory page, careers page, or asset).
    """
    path = urlparse(url).path

    # Must have the expected path prefix
    if prefix and not path.lower().startswith(prefix.lower()):
        return False

    # Skip known non-profile suffixes
    if _CATEGORY_SUFFIX_RE.search(path):
        return False

    # Skip static assets
    if re.search(r"\.(css|js|png|jpg|jpeg|gif|svg|pdf|zip|ico|woff|ttf)$", path, re.I):
        return False

    # Need at least 2 path segments
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return False

    # The last segment should look like a person slug (letters + hyphens/dots)
    last = parts[-1]
    # Numeric-only IDs are OK (e.g. Norton Rose uses /people/134273)
    if re.match(r"^\d+$", last):
        return True
    # Standard slug: letters, digits, hyphens (min 3 chars)
    if re.match(r"^[a-z0-9][a-z0-9\-\._%]{2,}$", last, re.I):
        return True

    return False


def _fetch_sitemap_urls(
    session: requests.Session,
    base_url: str,
    path_prefix: str | None,
    logger: DebugLogger,
    rate_delay: float = 0.3,
    timeout: int = 15,
) -> list[str]:
    """
    Fetch all attorney profile URLs from a firm's XML sitemap(s).

    Strategy:
    1. Fetch robots.txt to find Sitemap: directives.
    2. If none found, try /sitemap.xml, /sitemap_index.xml.
    3. Walk sitemap index → sub-sitemaps recursively (depth ≤ 3).
    4. Filter URLs by path_prefix and profile-URL heuristics.
    5. Also try numbered sitemap series (bio-sitemap1..20.xml, etc.)
       when the notes field hints at them.

    Returns deduplicated list of profile URLs.
    """
    parsed = urlparse(base_url)
    scheme = parsed.scheme
    netloc = parsed.netloc
    seen: set[str] = set()
    results: list[str] = []

    # ---- Step 1: Get sitemap URLs from robots.txt ----------------------
    robots_sitemaps: list[str] = []
    try:
        robots_url = f"{scheme}://{netloc}/robots.txt"
        resp = session.get(robots_url, timeout=timeout)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    val = line.split(":", 1)[1].strip()
                    if val:
                        robots_sitemaps.append(val)
    except Exception:
        pass

    # Prioritise English sitemaps (sort non-English to the end)
    def _en_first(u: str) -> int:
        return 1 if _LOCALE_RE.search(u) else 0

    robots_sitemaps.sort(key=_en_first)

    # Fallback candidate sitemap URLs
    fallback_sitemaps = [
        f"{scheme}://{netloc}/sitemap.xml",
        f"{scheme}://{netloc}/sitemap_index.xml",
        f"{scheme}://{netloc}/sitemap/professionals",
        f"{scheme}://{netloc}/page-sitemap.xml",
    ]

    candidate_sitemaps = robots_sitemaps if robots_sitemaps else fallback_sitemaps

    # ---- Step 2: Parse sitemap(s) recursively --------------------------
    def _parse_sitemap(sm_url: str, depth: int = 0) -> None:
        if depth > 3 or len(results) > 5000:
            return
        try:
            r = session.get(sm_url, timeout=timeout)
            if r.status_code != 200:
                return
            content = r.content
            if sm_url.endswith(".gz"):
                content = gzip.decompress(content)
        except Exception as exc:
            logger.error("Sitemap fetch error", url=sm_url, exc=exc)
            return

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return

        def _tag(el: ET.Element) -> str:
            t = el.tag
            return t.split("}", 1)[1].lower() if "}" in t else t.lower()

        sub_sitemaps: list[str] = []
        url_nodes: list[str] = []

        for child in root:
            ct = _tag(child)
            if ct == "sitemap":
                for gc in child:
                    if _tag(gc) == "loc" and gc.text:
                        sub_sitemaps.append(gc.text.strip())
            elif ct == "url":
                for gc in child:
                    if _tag(gc) == "loc" and gc.text:
                        url_nodes.append(gc.text.strip())

        # Filter and accumulate profile URLs
        for u in url_nodes:
            if u in seen:
                continue
            if _LOCALE_RE.search(urlparse(u).path):
                continue
            if _is_profile_url(u, path_prefix):
                seen.add(u)
                results.append(u)

        # Sort sub-sitemaps: English first, then recurse
        sub_sitemaps.sort(key=_en_first)
        for sub_url in sub_sitemaps:
            if len(results) > 5000:
                break
            if _LOCALE_RE.search(sub_url):
                continue
            time.sleep(rate_delay)
            _parse_sitemap(sub_url, depth + 1)

    for sm_url in candidate_sitemaps[:8]:
        _parse_sitemap(sm_url)
        time.sleep(rate_delay)
        if results:
            break  # found profiles — done with top-level candidates

    # ---- Step 3: Try numbered sitemap series if still empty or sparse --
    # E.g. bio-sitemap1.xml..bio-sitemap11.xml, people-sitemap1.xml..10.xml
    if len(results) < 10:
        _numbered_patterns = [
            (f"{scheme}://{netloc}/bio-sitemap{{n}}.xml", range(1, 15)),
            (f"{scheme}://{netloc}/people-sitemap{{n}}.xml", range(1, 15)),
            (f"{scheme}://{netloc}/attorney-sitemap{{n}}.xml", range(1, 10)),
            (f"{scheme}://{netloc}/lawyer-sitemap{{n}}.xml", range(1, 10)),
            (f"{scheme}://{netloc}/professional-sitemap{{n}}.xml", range(1, 10)),
        ]
        # Only probe if first numbered sitemap exists
        for pattern, num_range in _numbered_patterns:
            first_url = pattern.format(n=num_range.start)
            try:
                r = session.head(first_url, timeout=timeout)
                if r.status_code not in (200, 301, 302):
                    continue
            except Exception:
                continue
            # Exists — fetch all numbers until 404
            for n in num_range:
                url = pattern.format(n=n)
                try:
                    r = session.head(url, timeout=timeout)
                    if r.status_code not in (200, 301, 302):
                        break
                except Exception:
                    break
                time.sleep(rate_delay)
                _parse_sitemap(url)
            if results:
                break

    logger.info(
        "Sitemap discovery complete",
        base_url=base_url,
        path_prefix=path_prefix,
        total_found=len(results),
    )
    return results


def discover_attorneys_from_sitemap(
    firm_url: str,
    structure_info: dict[str, Any],
    session: requests.Session,
    logger: DebugLogger,
    rate_delay: float = 0.5,
    timeout: int = 15,
) -> list[str]:
    """
    High-level entry point for SITEMAP_XML firms.

    Uses sample URLs from structure_info to derive the path prefix,
    then delegates to _fetch_sitemap_urls().
    """
    samples = structure_info.get("sitemap_attorney_sample", [])
    path_prefix = _derive_attorney_path_prefix(samples, firm_url)

    logger.info(
        "SITEMAP_XML strategy",
        firm_url=firm_url,
        derived_prefix=path_prefix,
        sample_count=len(samples),
    )

    # Use the (possibly corrected) URL from site_structures.json
    effective_url = structure_info.get("url", firm_url).rstrip("/")

    urls = _fetch_sitemap_urls(
        session=session,
        base_url=effective_url,
        path_prefix=path_prefix,
        logger=logger,
        rate_delay=rate_delay,
        timeout=timeout,
    )

    # If path_prefix gave no results, retry without the prefix constraint
    if not urls and path_prefix:
        logger.warn(
            "No results with path prefix — retrying without prefix constraint",
            path_prefix=path_prefix,
        )
        urls = _fetch_sitemap_urls(
            session=session,
            base_url=effective_url,
            path_prefix=None,
            logger=logger,
            rate_delay=rate_delay,
            timeout=timeout,
        )

    return urls


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def discover_attorneys(
    firm_url: str,
    *,
    session: requests.Session | None = None,
    logger: DebugLogger | None = None,
    timeout: int = 15,
    rate_delay: float = 0.5,
    structure_info: dict[str, Any] | None = None,
) -> list[str]:
    """
    Discover all attorney profile URLs for a firm.

    Parameters
    ----------
    firm_url : str
        Base URL of the firm (e.g. "https://www.kirkland.com").
    session : requests.Session | None
        Optional pre-configured session (headers, proxies, etc.).
    logger : DebugLogger | None
        Structured logger; creates a default one if None.
    timeout : int
        HTTP request timeout in seconds.
    rate_delay : float
        Seconds to wait between requests.
    structure_info : dict | None
        Entry from site_structures.json (pre-loaded by caller).  When provided,
        the function routes to the correct strategy immediately instead of probing.

    Returns
    -------
    list[str]
        Deduplicated list of attorney profile URLs.
    """
    sess = session or _default_session()
    firm_netloc = urlparse(firm_url).netloc

    # Resolve structure_info from cache if not supplied
    if structure_info is None:
        structure_info = lookup_structure(firm_url)

    _logger = logger or DebugLogger(firm=firm_netloc)

    structure_type = (structure_info or {}).get("structure_type", "UNKNOWN")

    # ------------------------------------------------------------------
    # Route by structure type
    # ------------------------------------------------------------------

    # BOT_PROTECTED / AUTH_REQUIRED — skip immediately
    if structure_type in ("BOT_PROTECTED", "AUTH_REQUIRED"):
        _logger.warn(
            f"Firm is {structure_type} — skipping discovery",
            firm_url=firm_url,
        )
        return []

    # SITEMAP_XML — use sitemap-based discovery
    if structure_type == "SITEMAP_XML":
        assert structure_info is not None
        urls = discover_attorneys_from_sitemap(
            firm_url=firm_url,
            structure_info=structure_info,
            session=sess,
            logger=_logger,
            rate_delay=rate_delay,
            timeout=timeout,
        )
        if urls:
            _logger.info(
                "SITEMAP_XML discovery complete",
                total=len(urls),
                sample=urls[:3],
            )
            return urls
        # Fall through to legacy engine if sitemap returned nothing
        _logger.warn("Sitemap returned no URLs — falling back to legacy engine")

    # All other types (JSON_API_ALPHA, HTML_ALPHA_PAGINATED, HTML_DIRECTORY_FLAT,
    # SPA_OTHER, SPA_NEXTJS, UNKNOWN) — use the existing probe-and-crawl engine
    engine = _DiscoveryEngine(
        firm_url=firm_url,
        session=sess,
        logger=_logger,
        timeout=timeout,
        rate_delay=rate_delay,
    )
    result = engine.run()
    urls = result.profile_urls

    # ------------------------------------------------------------------
    # Diagnostic verification block
    # ------------------------------------------------------------------
    urls_a = [u for u in urls if "/lawyers/a/" in u.lower()]
    urls_b = [u for u in urls if "/lawyers/b/" in u.lower()]
    count_a = len(urls_a)
    count_b = len(urls_b)
    _logger.info(
        "Discovery letter coverage",
        count_a=count_a,
        count_b=count_b,
        sample_a=urls_a[:5],
        sample_b=urls_b[:5],
    )
    if count_a == 0 or count_b == 0:
        _logger.warn("Letter A or B missing from final dataset", count_a=count_a, count_b=count_b)

    return urls


# ---------------------------------------------------------------------------
# Internal engine
# ---------------------------------------------------------------------------

class _DiscoveryEngine:
    """Orchestrates the full discovery flow for one firm."""

    def __init__(
        self,
        firm_url: str,
        session: requests.Session | None,
        logger: DebugLogger | None,
        timeout: int,
        rate_delay: float,
    ) -> None:
        self._base = firm_url.rstrip("/")
        self._parsed = urlparse(self._base)
        self._firm_name = self._parsed.netloc
        self._session = session or _default_session()
        self._logger = logger or DebugLogger(firm=self._firm_name)
        self._timeout = timeout
        self._rate_delay = rate_delay
        self._seen_urls: set[str] = set()

    def run(self) -> DiscoveryResult:
        result = DiscoveryResult(firm_url=self._base)
        self._logger.start_discovery(base_url=self._base, strategy="detecting")
        # --- Step 1: Probe the directory endpoint for JSON / alphabet nav ---
        dir_url, probe_resp = self._probe_directory()
        if probe_resp is None:
            result.errors.append("Could not probe any directory URL")
            # Last resort: Playwright scroll
            urls = self._playwright_scroll_discovery(dir_url=self._base)
            result.profile_urls = urls
            result.strategy = "playwright"
            result.discovery_mode_used = "playwright_scroll"
            result.total_discovered = len(urls)
            self._logger.finish_discovery(len(urls), 0.0, result.errors)
            self._log_discovery_mode("playwright_scroll")
            return result

        t0 = time.time()
        # --- Step 2: Detect response type ---
        is_json = _is_json_content_type(probe_resp)
        has_alphabet = _detect_alphabet_nav(probe_resp, dir_url)
        # --- Step 3: Static requests crawl ---
        grand_total_search_results = 0
        letter_stats: dict[str, int] = {}
        errors: list[str] = []
        playwright_triggered = False
        if is_json:
            result.strategy = "json_api"
            self._logger.info("JSON API detected", dir_url=dir_url)
            urls, letter_stats, errors, grand_total_search_results, playwright_triggered = self._crawl_json_api(dir_url, probe_resp)
        elif has_alphabet:
            result.strategy = "html_alphabet"
            self._logger.info("HTML alphabet navigation detected", dir_url=dir_url)
            urls, letter_stats, errors, grand_total_search_results, playwright_triggered = self._crawl_html_alphabet(dir_url, probe_resp)
        else:
            # Try straight directory crawl first
            urls, grand_total_search_results = self._crawl_html_directory(dir_url, probe_resp)
            if urls:
                result.strategy = "html_directory"
            else:
                # Escalate immediately — no results at all
                self._logger.warn("Static crawl empty — escalating to Playwright scroll")
                urls = self._playwright_scroll_discovery(dir_url=dir_url)
                result.strategy = "playwright"
                result.discovery_mode_used = "playwright_scroll"
                self._log_discovery_mode("playwright_scroll")
        # ------------------------------------------------------------------
        # Playwright escalation: restart full A-Z when infinite scroll detected
        # mid-loop (playwright_triggered=True) or when final count < 50.
        # Mid-loop case: crawl methods already called _run_playwright_full_alphabet()
        #   and returned Playwright results — just mark the mode.
        # Post-loop case: count < 50 but no mid-loop trigger — escalate now.
        # ------------------------------------------------------------------
        anchor_count = len(self._seen_urls)
        if playwright_triggered:
            # urls already contains Playwright results — just update the mode
            result.strategy = "playwright_scroll"
            result.discovery_mode_used = "playwright_scroll"
        elif result.discovery_mode_used != "playwright_scroll" and anchor_count < 50:
            self._logger.warn(
                f"Infinite scroll suspected — only {anchor_count} profiles extracted "
                "via requests (< 50 threshold). Restarting full alphabet in Playwright mode.",
            )
            pw_urls = self._run_playwright_full_alphabet(dir_url=dir_url)
            if pw_urls:
                result.strategy = "playwright_scroll"
                result.discovery_mode_used = "playwright_scroll"
                urls = pw_urls
            else:
                self._logger.warn("Playwright scroll returned no URLs; keeping requests results")
        self._log_discovery_mode(result.discovery_mode_used)
        elapsed = time.time() - t0
        result.profile_urls = list(self._seen_urls)  # deduplicated
        result.total_discovered = len(result.profile_urls)
        result.letter_stats = letter_stats
        result.errors = errors
        self._logger.finish_discovery(
            total_unique_profiles=result.total_discovered,
            elapsed_seconds=elapsed,
            errors=errors,
        )
        return result

    # ------------------------------------------------------------------
    # Probing
    # ------------------------------------------------------------------

    def _probe_directory(self) -> tuple[str, requests.Response | None]:
        """
        Try directory paths and return the first responding URL + response.

        Priority:
          1. /lawyers?letter=A  (alphabet JSON)
          2. /attorneys, /lawyers, /people, ...
        """
        # First: try base paths with letter=A probe to detect JSON API
        alphabet_probes = [
            f"{self._base}/lawyers?letter=A",
            f"{self._base}/attorneys?letter=A",
            f"{self._base}/people?letter=A",
            f"{self._base}/professionals?letter=A",
        ]
        for url in alphabet_probes:
            resp = self._get(url)
            if resp and resp.status_code == 200 and len(resp.text) > 500:
                return url, resp

        # Second: plain directory paths
        for path in DIRECTORY_PATHS:
            url = self._base + path
            resp = self._get(url)
            if resp and resp.status_code == 200 and len(resp.text) > 500:
                return url, resp

        return self._base, None

    # ------------------------------------------------------------------
    # JSON API crawl
    # ------------------------------------------------------------------

    def _crawl_json_api(
        self,
        dir_url: str,
        initial_resp: requests.Response,
    ) -> tuple[list[str], dict[str, int], list[str], int, bool]:
        """
        Crawl a JSON-returning directory using A-Z letters with pagination.
        - ?letter=A pagination via TotalSearchResults + offset/page
        - Any response with a JSON array of {Name, Url, Position, ...}
        Returns (urls, letter_stats, errors, grand_total_search_results, playwright_triggered).
        If infinite scroll is detected mid-loop, calls _run_playwright_full_alphabet()
        directly and returns its results with playwright_triggered=True.
        """
        all_urls: list[str] = []
        letter_stats: dict[str, int] = {}
        errors: list[str] = []
        grand_total_search_results: int = 0
        # Parse base URL to determine param name for letter filtering
        parsed = urlparse(dir_url)
        qs = parse_qs(parsed.query)
        letter_param = "letter"  # default; inspect initial URL
        for k in qs:
            if k.lower() in ("letter", "l", "alpha", "char"):
                letter_param = k
                break
        for letter_index, letter in enumerate(LETTERS):
            letter_urls, count, err = self._crawl_json_letter(
                dir_url=dir_url,
                letter=letter,
                letter_param=letter_param,
            )
            if err:
                errors.append(f"Letter {letter}: {err}")
            all_urls.extend(letter_urls)
            letter_stats[letter] = len(letter_urls)
            if count > grand_total_search_results:
                grand_total_search_results = count
            for u in letter_urls:
                self._seen_urls.add(u)
            self._logger.log_discovery_letter(
                letter=letter,
                total_search_results=count,
                extracted_count=len(letter_urls),
            )
            # After processing A, B, C: if < 10 profiles found total,
            # this is almost certainly an infinite-scroll firm — escalate immediately.
            if letter_index >= 2 and len(self._seen_urls) < 10:
                pw_urls = self._run_playwright_full_alphabet(dir_url=dir_url)
                return pw_urls, letter_stats, errors, grand_total_search_results, True
            time.sleep(self._rate_delay)
        return all_urls, letter_stats, errors, grand_total_search_results, False

    def _crawl_json_letter(
        self,
        dir_url: str,
        letter: str,
        letter_param: str,
    ) -> tuple[list[str], int, str | None]:
        """
        Crawl all pages for one letter via JSON API.
        Page-based strategy (Page=1, Page=2, ...):
          1. Fetch Page=1 to get TotalSearchResults and page_size.
          2. Compute total_pages = ceil(TotalSearchResults / page_size).
          3. Fetch remaining pages Page=2..total_pages.
          4. Stop early if a page returns empty Results.
        Returns (urls, total_search_results, error_or_None).
        """
        import math
        urls: list[str] = []
        total_search_results: int = 0

        # Build base query params (strip any existing pagination/letter keys)
        parsed = urlparse(dir_url)
        base_qs = {
            k: v[0]
            for k, v in parse_qs(parsed.query).items()
            if k.lower() not in {
                letter_param.lower(), "page", "offset", "start", "from", "skip",
            }
        }

        # ---- Page 1 ------------------------------------------------
        p1_params = {**base_qs, letter_param: letter, "Page": "1"}
        p1_url = urlunparse(parsed._replace(query=urlencode(p1_params)))
        resp = self._get(p1_url)
        if not resp or resp.status_code != 200:
            return [], 0, f"HTTP error on page 1 for letter {letter}"

        data = _parse_json_response(resp)
        if data is None:
            return [], 0, f"Non-JSON response on page 1 for letter {letter}"

        page_urls, total_search_results = _extract_urls_from_json(data, self._base)
        page_size = len(page_urls)

        if total_search_results == 0:
            # Letter has no attorneys - log and return empty
            return [], 0, None

        for u in page_urls:
            if u not in self._seen_urls:
                urls.append(u)
                self._seen_urls.add(u)

        if page_size == 0:
            # Got total > 0 but empty page - unexpected; stop safely
            return urls, total_search_results, None

        import math
        total_pages = math.ceil(total_search_results / page_size)
        total_pages = min(total_pages, _MAX_PAGES_PER_LETTER)

        # ---- Pages 2..total_pages -----------------------------------
        for page in range(2, total_pages + 1):
            time.sleep(self._rate_delay)
            params = {**base_qs, letter_param: letter, "Page": str(page)}
            page_url = urlunparse(parsed._replace(query=urlencode(params)))
            resp = self._get(page_url)
            if not resp or resp.status_code != 200:
                break

            data = _parse_json_response(resp)
            if data is None:
                break

            page_urls, _ = _extract_urls_from_json(data, self._base)
            if not page_urls:
                # Empty Results - stop early
                break

            for u in page_urls:
                if u not in self._seen_urls:
                    urls.append(u)
                    self._seen_urls.add(u)

        return urls, total_search_results, None

    # ------------------------------------------------------------------
    # HTML alphabet crawl
    # ------------------------------------------------------------------

    def _crawl_html_alphabet(
        self,
        dir_url: str,
        initial_resp: requests.Response,
    ) -> tuple[list[str], dict[str, int], list[str], int, bool]:
        """Crawl A-Z links in HTML directory pages.
        Returns (urls, letter_stats, errors, grand_total_search_results, playwright_triggered).
        If infinite scroll is detected mid-loop, calls _run_playwright_full_alphabet()
        directly and returns its results with playwright_triggered=True.
        """
        all_urls: list[str] = []
        letter_stats: dict[str, int] = {}
        errors: list[str] = []
        grand_total_search_results: int = 0
        letter_url_tpl = _detect_letter_url_template(initial_resp, dir_url)
        for letter_index, letter in enumerate(LETTERS):
            if letter_url_tpl:
                letter_url = letter_url_tpl.format(letter=letter)
            else:
                # Construct from dir_url
                parsed = urlparse(dir_url)
                new_qs = f"letter={letter}"
                letter_url = urlunparse(parsed._replace(query=new_qs))
            letter_urls, count, err = self._crawl_html_letter_pages(letter_url)
            if err:
                errors.append(f"Letter {letter}: {err}")
            all_urls.extend(letter_urls)
            letter_stats[letter] = len(letter_urls)
            if count > grand_total_search_results:
                grand_total_search_results = count
            for u in letter_urls:
                self._seen_urls.add(u)
            self._logger.log_discovery_letter(
                letter=letter,
                total_search_results=count,
                extracted_count=len(letter_urls),
            )
            # After processing A, B, C: if < 10 profiles found total,
            # this is almost certainly an infinite-scroll firm — escalate immediately.
            if letter_index >= 2 and len(self._seen_urls) < 10:
                pw_urls = self._run_playwright_full_alphabet(dir_url=dir_url)
                return pw_urls, letter_stats, errors, grand_total_search_results, True
            time.sleep(self._rate_delay)
        return all_urls, letter_stats, errors, grand_total_search_results, False

    def _crawl_html_letter_pages(
        self,
        letter_url: str,
    ) -> tuple[list[str], int, str | None]:
        """Crawl all paginated HTML pages for one letter."""
        urls: list[str] = []
        total = 0
        page = 1

        parsed_base = urlparse(letter_url)

        while page <= _MAX_PAGES_PER_LETTER:
            if page == 1:
                page_url = letter_url
            else:
                # Insert page param
                qs_dict = dict(parse_qs(parsed_base.query))
                qs_dict["page"] = [str(page)]
                new_qs = urlencode({k: v[0] for k, v in qs_dict.items()})
                page_url = urlunparse(parsed_base._replace(query=new_qs))

            resp = self._get(page_url)
            if not resp or resp.status_code != 200:
                break

            page_urls, page_total = _extract_urls_from_html(resp.text, self._base)
            if page_total > total:
                total = page_total

            new_urls = [u for u in page_urls if u not in self._seen_urls]
            urls.extend(new_urls)

            if not new_urls or not _has_next_page(resp.text, page):
                break

            page += 1
            time.sleep(self._rate_delay)

        return urls, total, None

    # ------------------------------------------------------------------
    # Plain HTML directory crawl (no A-Z)
    # ------------------------------------------------------------------

    def _crawl_html_directory(
        self,
        dir_url: str,
        initial_resp: requests.Response,
    ) -> tuple[list[str], int]:
        """Extract profile URLs from a non-alphabetical HTML directory.
        Returns (urls, total_count_from_page).
        """
        urls: list[str] = []
        total: int = 0
        page = 1
        parsed_base = urlparse(dir_url)
        current_resp = initial_resp
        while page <= _MAX_PAGES_PER_LETTER:
            page_urls, page_total = _extract_urls_from_html(current_resp.text, self._base)
            if page_total > total:
                total = page_total
            new_urls = [u for u in page_urls if u not in self._seen_urls]
            urls.extend(new_urls)
            for u in new_urls:
                self._seen_urls.add(u)
            if not new_urls or not _has_next_page(current_resp.text, page):
                break
            page += 1
            qs_dict = dict(parse_qs(parsed_base.query))
            qs_dict["page"] = [str(page)]
            new_qs = urlencode({k: v[0] for k, v in qs_dict.items()})
            next_url = urlunparse(parsed_base._replace(query=new_qs))
            current_resp = self._get(next_url)
            if not current_resp or current_resp.status_code != 200:
                break
            time.sleep(self._rate_delay)

        return urls, total

    # ------------------------------------------------------------------
    # Playwright full-alphabet runner (dedicated escalation entry point)
    # ------------------------------------------------------------------

    def _run_playwright_full_alphabet(self, *, dir_url: str) -> list[str]:
        """
        Clear all partial requests results and run a complete A-Z Playwright
        scroll pass starting from letter A.

        Called when infinite scroll is detected — either mid-loop or after the
        full requests pass returns fewer than 50 profiles.

        Returns the deduplicated URL list from Playwright.
        Results are intentionally NOT mixed with any prior requests results.
        """
        self._seen_urls.clear()
        self._logger.warn(
            "Restarting full alphabet in Playwright mode (A-Z)",
            dir_url=dir_url,
        )
        return self._playwright_scroll_discovery(dir_url=dir_url)

    # ------------------------------------------------------------------
    # Playwright scroll discovery (A-Z inner loop)
    # ------------------------------------------------------------------
    def _playwright_scroll_discovery(self, *, dir_url: str) -> list[str]:
        """
        A-Z Playwright scroll discovery.
        For each letter A-Z:
          1. Build letter URL from dir_url (?letter=X param).
          2. Navigate with Playwright.
          3. Auto-scroll until DOM height stops increasing.
          4. Extract all profile URLs from intercepted JSON, then HTML fallback.
        Returns deduplicated list of profile URLs.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._logger.warn("Playwright not installed — cannot do scroll discovery")
            return []
        parsed = urlparse(dir_url)
        base_qs = {
            k: v[0]
            for k, v in parse_qs(parsed.query).items()
            if k.lower() not in ("letter", "l", "alpha", "char", "page", "p")
        }

        urls: list[str] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                for letter in LETTERS:
                    letter_params = {**base_qs, "letter": letter}
                    letter_url = urlunparse(
                        parsed._replace(query=urlencode(letter_params))
                    )
                    pw_page = context.new_page()
                    captured_json: list[dict[str, Any]] = []
                    def on_response(
                        response: Any,
                        _cj: list[dict[str, Any]] = captured_json,
                    ) -> None:
                        try:
                            if (
                                response.ok
                                and "json" in response.headers.get("content-type", "").lower()
                            ):
                                data = response.json()
                                _cj.append({"url": response.url, "data": data})
                        except Exception:
                            pass

                    pw_page.on("response", on_response)

                    try:
                        pw_page.goto(letter_url, timeout=30_000, wait_until="networkidle")
                    except Exception:
                        pw_page.close()
                        time.sleep(self._rate_delay)
                        continue
                    # Auto-scroll until DOM height stabilises
                    prev_height = 0
                    for _ in range(40):
                        curr_height = cast(int, pw_page.evaluate("document.body.scrollHeight"))
                        if curr_height == prev_height:
                            break
                        prev_height = curr_height
                        pw_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        pw_page.wait_for_timeout(1500)
                    html = pw_page.content()
                    # --- DOM direct extraction fallback (for sites like Kirkland) ---
                    # After scroll + html acquisition, extract anchors directly from the rendered DOM.
                    try:
                        dom_hrefs = pw_page.eval_on_selector_all(
                            "a[href]",
                            "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
                        )
                    except Exception:
                        dom_hrefs = []

                    # Normalize + filter to likely profile URLs.
                    for href in dom_hrefs:
                        href = str(href)
                        if href.startswith("/lawyers/"):
                            u = self._base + href
                        elif href.startswith("http"):
                            u = href
                        else:
                            continue

                        # Strong filter: prefer the exact Kirkland pattern /lawyers/{letter}/...
                        # but keep a softer fallback for other firms.
                        low = u.lower()
                        if f"/lawyers/{letter.lower()}/" in low or "/lawyers/" in low:
                            if u not in self._seen_urls:
                                urls.append(u)
                                self._seen_urls.add(u)

                    pw_page.close()
                    before = len(self._seen_urls)

                    # === 여기서 URL 추출 ===

                    after = len(self._seen_urls)

                    self._logger.log_discovery_letter(
                        letter=letter,
                        total_search_results=0,
                        extracted_count=(after - before),
                    )
                    # Extract from intercepted JSON first
                    json_found = False
                    for item in captured_json:
                        page_urls, _ = _extract_urls_from_json(item["data"], self._base)
                        for u in page_urls:
                            if u not in self._seen_urls:
                                urls.append(u)
                                self._seen_urls.add(u)
                                json_found = True
                    # HTML fallback when no JSON captured
                    if not json_found:
                        page_urls, _ = _extract_urls_from_html(html, self._base)
                        for u in page_urls:
                            if u not in self._seen_urls:
                                urls.append(u)
                                self._seen_urls.add(u)
                    self._logger.log_discovery_letter(
                        letter=letter,
                        total_search_results=0,
                        extracted_count=len(urls),
                    )
                    time.sleep(self._rate_delay)
                context.close()
                browser.close()
        except Exception as exc:
            self._logger.error("Playwright A-Z scroll error", exc=exc)
        return urls

    def _log_discovery_mode(self, mode: str) -> None:
        """Set discovery_mode_used on FirmDiscoverySummary and emit log line."""
        if self._logger._discovery_summary is not None:
            self._logger._discovery_summary.discovery_mode_used = mode
        self._logger.info(
            f"Discovery mode: {mode}",
            firm=self._firm_name,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, url: str) -> requests.Response | None:
        """Safe GET with error handling."""
        try:
            resp = self._session.get(url, timeout=self._timeout)
            return resp
        except Exception as exc:
            self._logger.error("GET failed", exc=exc, url=url)
            return None

    def _build_pagination_params(self, page: int, fetched_so_far: int) -> dict[str, str]:
        """Build page params for HTML alphabet crawl (not used by JSON API path)."""
        return {"page": str(page)}


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _is_json_content_type(resp: requests.Response) -> bool:
    """Return True if response content-type is JSON."""
    ct = resp.headers.get("content-type", "").lower()
    if "json" in ct:
        return True
    # Some firms return JSON with text/html content-type — probe the body
    body = resp.text.strip()
    return body.startswith("{") or body.startswith("[")


def _parse_json_response(resp: requests.Response) -> Any:
    """Parse JSON from response, returning None on failure."""
    try:
        return resp.json()
    except Exception:
        # Sometimes JSON is wrapped in a text/html response
        body = resp.text.strip()
        if body.startswith("{") or body.startswith("["):
            import json
            try:
                return json.loads(body)
            except Exception:
                pass
    return None


def _extract_urls_from_json(
    data: Any,
    base_url: str,
) -> tuple[list[str], int]:
    """
    Extract profile URLs and total count from a JSON payload.

    Handles:
    - Array of {Name, Url, Position, TotalSearchResults}
    - Nested {results: [...], total: N}
    - Direct URL strings
    - Objects with "url" or "profileUrl" keys
    """
    urls: list[str] = []
    total: int = 0

    if isinstance(data, dict):
        # TotalSearchResults at top level
        for key in ("TotalSearchResults", "totalSearchResults", "total", "Total",
                    "count", "Count", "totalCount"):
            if key in data and isinstance(data[key], (int, str)):
                try:
                    total = int(data[key])
                    break
                except (ValueError, TypeError):
                    pass

        # Unwrap common wrapper keys
        for key in ("results", "data", "attorneys", "lawyers", "people",
                    "professionals", "items", "Records", "records"):
            if key in data and isinstance(data[key], list):
                sub_urls, sub_total = _extract_urls_from_json(data[key], base_url)
                urls.extend(sub_urls)
                if sub_total > total:
                    total = sub_total
                return urls, total

        # Treat this dict as a single record
        url = _extract_url_from_record(data, base_url)
        if url:
            urls.append(url)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # Check for TotalSearchResults in each item
                for key in ("TotalSearchResults", "totalSearchResults"):
                    if key in item:
                        try:
                            t = int(item[key])
                            if t > total:
                                total = t
                        except (ValueError, TypeError):
                            pass

                url = _extract_url_from_record(item, base_url)
                if url:
                    urls.append(url)
            elif isinstance(item, str) and item.startswith("http"):
                urls.append(item)

    return urls, total


def _extract_url_from_record(record: dict[str, Any], base_url: str) -> str | None:
    """
    Extract a profile URL from a JSON record dict.

    Tries common field names: Url, url, profileUrl, href, path, slug.
    Appends base_url for relative paths.
    """
    for key in ("Url", "url", "profileUrl", "profile_url", "ProfileUrl",
                "href", "link", "Link", "path", "Path", "slug", "Slug"):
        val = record.get(key)
        if val and isinstance(val, str) and len(val) > 3:
            # Absolute URL
            if val.startswith("http"):
                if _looks_like_profile_url(val):
                    return val
                return val  # return anyway — caller can filter
            # Relative path
            if val.startswith("/"):
                return base_url.rstrip("/") + val
    return None


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _detect_alphabet_nav(resp: requests.Response, dir_url: str) -> bool:
    """
    Return True if the response has A-Z navigation links or ?letter= param.
    """
    # URL already has ?letter= param → already using alphabet nav
    if "letter=" in dir_url.lower():
        return True

    if not BS4_AVAILABLE:
        # Regex fallback
        text = resp.text
        return bool(re.search(r'[?&]letter=[A-Z]', text, re.IGNORECASE))

    assert BeautifulSoup is not None  # guarded above by BS4_AVAILABLE check
    soup = BeautifulSoup(resp.text, "html.parser")

    # Look for links like ?letter=A through ?letter=Z
    letter_links = soup.find_all(
        "a",
        href=re.compile(r"[?&]letter=[A-Z]", re.IGNORECASE),
    )
    if len(letter_links) >= 10:  # At least 10 letters visible → alphabet nav
        return True

    # Look for aria or data attributes indicating alphabet nav
    alpha_elems = soup.find_all(
        attrs={"data-letter": re.compile(r"^[A-Z]$")},
    )
    if alpha_elems:
        return True

    return False


def _detect_letter_url_template(resp: requests.Response, dir_url: str) -> str | None:
    """
    Extract a URL template for alphabet navigation.

    Returns template string with {letter} placeholder, e.g.:
      "https://example.com/lawyers?letter={letter}"
    """
    if not BS4_AVAILABLE:
        # Regex fallback
        m = re.search(
            r'href=["\']([^"\']*[?&]letter=)[A-Z]([^"\']*)["\']',
            resp.text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1) + "{letter}" + m.group(2)
        return None


    assert BeautifulSoup is not None  # guarded above by BS4_AVAILABLE check
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(cast(Any, a).get("href", ""))
        if re.search(r"[?&]letter=[A-Z]", href, re.IGNORECASE):
            # Replace the specific letter with {letter} placeholder
            tpl = re.sub(
                r"([?&]letter=)[A-Z]",
                r"\g<1>{letter}",
                href,
                flags=re.IGNORECASE,
            )
            # Make absolute if relative
            if not tpl.startswith("http"):
                base = urlparse(dir_url)
                tpl = f"{base.scheme}://{base.netloc}" + tpl
            return tpl

    return None


def _extract_urls_from_html(
    html: str,
    base_url: str,
) -> tuple[list[str], int]:
    """
    Extract attorney profile URLs from HTML directory page.

    Returns (urls, total_count_from_page).
    """
    urls: list[str] = []
    total = 0

    # Try to parse total from page text (e.g. "Showing 1–20 of 423 attorneys")
    count_patterns = [
        re.compile(r"(?:of|total[:\s]+)\s*(\d[\d,]+)\s*(?:attorney|lawyer|professional)", re.I),
        re.compile(r"(\d[\d,]+)\s*(?:attorney|lawyer|professional)s?\s+found", re.I),
        re.compile(r"showing\s+\d+[-–]\d+\s+of\s+(\d[\d,]+)", re.I),
    ]
    for pat in count_patterns:
        m = pat.search(html)
        if m:
            try:
                total = int(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    if not BS4_AVAILABLE:
        # Regex fallback: find all href links that look like profiles
        raw_urls = re.findall(r'href=["\']([^"\']+)["\']', html)
        for href in raw_urls:
            if not href.startswith("http"):
                href = base_url.rstrip("/") + href if href.startswith("/") else href
            if _looks_like_profile_url(href):
                if href not in urls:
                    urls.append(href)
        return urls, total


    assert BeautifulSoup is not None  # guarded above by BS4_AVAILABLE check
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(cast(Any, a).get("href", ""))
        if not href.startswith("http"):
            if href.startswith("/"):
                href = base_url.rstrip("/") + href
            else:
                continue
        if _looks_like_profile_url(href) and href not in urls:
            urls.append(href)

    return urls, total


def _has_next_page(html: str, current_page: int) -> bool:
    """
    Return True if HTML contains a 'next page' link.
    """
    # Look for rel="next"
    if re.search(r'rel=["\']next["\']', html, re.IGNORECASE):
        return True

    # Look for page=N+1 link
    next_page = current_page + 1
    if re.search(rf'[?&]page={next_page}', html):
        return True

    # Look for "Next" button text
    if re.search(r'(?:>|\s)(?:next|›|»|→)\s*(?:<|$)', html, re.IGNORECASE):
        return True

    return False


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

def _looks_like_profile_url(url: str) -> bool:
    """
    Return True if URL looks like an attorney profile page
    (not a list page, admin page, external site, or asset).
    """
    if not url or len(url) < 10:
        return False

    parsed = urlparse(url)

    # Must have a path
    path = parsed.path.lower()
    if not path or path == "/":
        return False

    # Skip static assets
    if re.search(r'\.(css|js|png|jpg|jpeg|gif|svg|pdf|zip|ico|woff|ttf)$', path):
        return False

    # Skip known non-profile paths
    skip_patterns = [
        r'/news/', r'/insights/', r'/publications/', r'/events/',
        r'/careers/', r'/contact', r'/about', r'/search',
        r'/services/', r'/practices/', r'/industries/', r'/locations/',
        r'#', r'/blog/', r'/feed/', r'/sitemap', r'/wp-content/',
    ]
    for pat in skip_patterns:
        if re.search(pat, path, re.IGNORECASE):
            return False

    # Positive signal: path matches common attorney profile patterns
    if _PROFILE_PATH_RE.search(path):
        return True

    # Fallback: path has at least 2 segments and looks like a slug
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        last_part = parts[-1]
        # Slug-like: hyphens, mostly letters
        if re.match(r'^[a-z][a-z0-9\-]{4,}$', last_part):
            return True

    return False


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

def _default_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session
