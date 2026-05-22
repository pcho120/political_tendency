#!/usr/bin/env python3
"""probe_structures.py
AmLaw200 사이트 구조 탐지기.

각 사이트에 대해 다음을 탐지:
  1. robots.txt + sitemap URL 존재 여부
  2. 알파벳 디렉토리 경로 (/attorneys, /lawyers, /people, /professionals 등)
  3. JSON API 응답 여부 (?letter=A)
  4. Cloudflare/bot-wall 감지
  5. SPA 신호 (Next.js, React, Vue 등)
  6. 페이지 내 alphabet nav 존재 여부
  7. 결과를 구조 유형으로 분류 후 site_structures.json 저장

Usage:
    python3 probe_structures.py                    # 전체 200개
    python3 probe_structures.py --max-firms 20     # 상위 20개만
    python3 probe_structures.py --workers 10       # 병렬 워커 수
    python3 probe_structures.py --resume           # 이미 탐지된 항목은 스킵
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEOUT = 10
RATE_DELAY = 0.3
OUTPUT_FILE = Path("site_structures.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Attorney directory probe paths
DIRECTORY_PATHS = [
    "/attorneys",
    "/lawyers",
    "/people",
    "/professionals",
    "/our-team",
    "/team",
    "/attorneys-advisors",
    "/en/lawyers",
    "/en/people",
    "/en/professionals",
]

# Alphabet probe: try ?letter=A to detect JSON API
ALPHA_PROBE_PATHS = [
    "/lawyers?letter=A",
    "/attorneys?letter=A",
    "/people?letter=A",
    "/professionals?letter=A",
    "/en/lawyers?letter=A",
    "/en/attorneys?letter=A",
    "/en/people?letter=A",
]

# SPA signals in HTML
SPA_SIGNALS = [
    "window.__NEXT_DATA__",
    "__NUXT__",
    "data-reactroot",
    "ng-app",
    "id=\"app\"",
    "id=\"root\"",
    "window.__INITIAL_STATE__",
    "__APOLLO_STATE__",
]

# Cloudflare signals
CF_BLOCK_SIGNALS = [
    "cf-ray",          # response header key
    "cloudflare",
    "attention required",
    "just a moment",
    "enable javascript and cookies",
]

# ---------------------------------------------------------------------------
# Structure types (taxonomy)
# ---------------------------------------------------------------------------

# Numeric type codes → human-readable labels
STRUCTURE_TYPES = {
    "JSON_API_ALPHA":       "JSON API with alphabet filter (?letter=A) — e.g. Latham, DLA Piper",
    "HTML_ALPHA_PAGINATED": "HTML directory with A-Z nav + pagination — e.g. Gibson Dunn",
    "HTML_ALPHA_SCROLL":    "HTML directory with A-Z nav + infinite scroll — e.g. Kirkland",
    "HTML_DIRECTORY_FLAT":  "Plain HTML directory (no alphabet, no scroll) — e.g. Paul Weiss",
    "SITEMAP_XML":          "Attorney URLs discoverable via XML sitemap — e.g. Jones Day, Sidley",
    "SPA_NEXTJS":           "Next.js/React SPA — requires Playwright rendering",
    "SPA_OTHER":            "Other SPA framework — requires Playwright rendering",
    "BOT_PROTECTED":        "Cloudflare or hard bot-wall — cannot scrape legally",
    "AUTH_REQUIRED":        "Login / paywalled — cannot scrape",
    "UNKNOWN":              "Structure not determined — needs manual investigation",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SiteStructure:
    rank: int
    firm: str
    url: str

    # Raw signals
    http_status: int = 0
    redirected_to: str = ""
    is_bot_protected: bool = False
    is_auth_required: bool = False
    cf_detected: bool = False

    robots_txt_found: bool = False
    sitemap_urls_in_robots: list[str] = field(default_factory=list)
    sitemap_has_attorney_urls: bool = False
    sitemap_attorney_sample: list[str] = field(default_factory=list)

    directory_path_found: str = ""       # first path that returned 200
    directory_status: int = 0
    directory_html_size: int = 0
    has_alphabet_nav: bool = False
    has_pagination: bool = False
    has_infinite_scroll_signals: bool = False

    json_api_path: str = ""             # path that returned JSON for ?letter=A
    json_api_sample_keys: list[str] = field(default_factory=list)
    json_api_total_count: int = 0

    spa_framework: str = ""             # "nextjs" | "nuxt" | "react" | "vue" | ""
    page_title: str = ""
    attorney_url_pattern: str = ""       # regex pattern seen in links

    # Classification
    structure_type: str = "UNKNOWN"
    confidence: float = 0.0
    notes: str = ""

    probe_seconds: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Probing logic
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(session: requests.Session, url: str) -> requests.Response | None:
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        return r
    except Exception:
        return None


def _head(session: requests.Session, url: str) -> requests.Response | None:
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        return r
    except Exception:
        return None


def _detect_bot_block(resp: requests.Response) -> tuple[bool, bool]:
    """Returns (is_bot_protected, is_auth_required)."""
    code = resp.status_code
    if code in (401, 403, 407):
        # Check if it's CF specifically
        headers_lower = {k.lower(): v.lower() for k, v in resp.headers.items()}
        if "cf-ray" in headers_lower or code == 403:
            text_lower = resp.text[:2000].lower()
            if any(sig in text_lower for sig in ["just a moment", "enable javascript and cookies", "attention required"]):
                return True, False
        if code == 401:
            return False, True
        # 403 without CF signals — could be directory listing denial, not full block
    if code == 200:
        text_lower = resp.text[:3000].lower()
        # Strong Cloudflare challenge page
        if ("just a moment" in text_lower or "enable javascript and cookies" in text_lower):
            return True, False
    return False, False


def _detect_spa(html: str) -> str:
    """Returns framework name or empty string."""
    for sig in SPA_SIGNALS:
        if sig.lower() in html.lower():
            if "next" in sig.lower() or "NEXT" in sig:
                return "nextjs"
            if "nuxt" in sig.lower() or "NUXT" in sig:
                return "nuxt"
            if "react" in sig.lower() or "reactroot" in sig.lower():
                return "react"
            if "ng-app" in sig:
                return "angular"
            return "react"  # generic fallback for __INITIAL_STATE__, __APOLLO_STATE__
    return ""


def _detect_alphabet_nav(html: str) -> bool:
    """True if page has A-Z filter links."""
    if re.search(r'[?&]letter=[A-Z]', html, re.IGNORECASE):
        return True
    soup = BeautifulSoup(html, "html.parser")
    alpha_links = soup.find_all("a", href=re.compile(r'[?&]letter=[A-Z]', re.IGNORECASE))
    if len(alpha_links) >= 5:
        return True
    return False


def _detect_pagination(html: str) -> bool:
    """True if page has next-page pagination."""
    if re.search(r'rel=["\']next["\']', html, re.IGNORECASE):
        return True
    if re.search(r'[?&]page=2', html):
        return True
    soup = BeautifulSoup(html, "html.parser")
    next_btn = soup.find(string=re.compile(r'\bnext\b', re.IGNORECASE))
    if next_btn:
        return True
    return False


def _detect_infinite_scroll(html: str) -> bool:
    """True if page has infinite-scroll signals."""
    signals = [
        "infinitescroll", "infinite-scroll", "loadmore", "load-more",
        "loadMore", "onscroll", "intersectionobserver",
        "data-page", "data-total-pages",
    ]
    html_lower = html.lower()
    return any(s.lower() in html_lower for s in signals)


def _extract_attorney_url_pattern(urls: list[str], base_url: str) -> str:
    """Derive a regex pattern from discovered attorney profile URLs."""
    domain = urlparse(base_url).netloc
    paths = []
    for u in urls[:20]:
        p = urlparse(u).path
        if p:
            paths.append(p)
    if not paths:
        return ""
    # Find common prefix
    from os.path import commonprefix
    prefix = commonprefix(paths)
    if len(prefix) > 3:
        return prefix.rstrip("/") + "/*"
    return ""


def _probe_sitemap(session: requests.Session, base_url: str) -> tuple[bool, list[str], bool, list[str]]:
    """
    Returns (robots_found, sitemap_urls_in_robots, has_attorney_urls, attorney_sample).
    """
    domain = urlparse(base_url).netloc
    scheme = urlparse(base_url).scheme
    robots_url = f"{scheme}://{domain}/robots.txt"
    sitemap_urls: list[str] = []
    robots_found = False

    resp = _get(session, robots_url)
    if resp and resp.status_code == 200:
        robots_found = True
        for line in resp.text.splitlines():
            if line.lower().startswith("sitemap:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    sitemap_urls.append(val)

    if not sitemap_urls:
        sitemap_urls = [
            f"{scheme}://{domain}/sitemap.xml",
            f"{scheme}://{domain}/sitemap_index.xml",
        ]

    # Sort English sitemaps first (Jones Day fix)
    _non_en = {'de','es','fr','ja','zh','pt','it','nl','pl','ru','sv','ko','ar',
               'zh-hans','zh-hant','cs','hu','uk','vi','tr','da','fi','nb'}
    sitemap_urls.sort(key=lambda u: (
        1 if re.search(r'/(' + '|'.join(_non_en) + r')(?:/|$)', u, re.IGNORECASE) else 0
    ))

    attorney_urls: list[str] = []
    _ATTORNEY_KEYWORDS = re.compile(
        r'/(lawyers?|attorneys?|people|professionals?|bio|profile|person|team)/',
        re.IGNORECASE,
    )
    _LOCALE_RE = re.compile(
        r'/(?:de|es|fr|ja|zh|ko|pt|ru|it|nl|pl|sv|tr|da|fi|ar)(?:/|$)',
        re.IGNORECASE,
    )

    def _parse_sm(content: bytes, depth: int = 0) -> None:
        if len(attorney_urls) >= 10 or depth > 3:
            return
        try:
            root = ET.fromstring(content)
        except Exception:
            return
        def _tag(el: ET.Element) -> str:
            t = el.tag
            return t.split("}", 1)[1].lower() if "}" in t else t.lower()

        sub_sitemaps: list[str] = []
        for child in root:
            ct = _tag(child)
            if ct == "sitemap":
                for gc in child:
                    if _tag(gc) == "loc" and gc.text:
                        sub_sitemaps.append(gc.text.strip())
            elif ct == "url":
                for gc in child:
                    if _tag(gc) == "loc" and gc.text:
                        u = gc.text.strip()
                        if _ATTORNEY_KEYWORDS.search(u) and not _LOCALE_RE.search(u):
                            attorney_urls.append(u)
                            if len(attorney_urls) >= 10:
                                return

        for sub_url in sub_sitemaps[:5]:
            if len(attorney_urls) >= 10:
                break
            # Sort: en first
            try:
                sr = session.get(sub_url, timeout=TIMEOUT)
                if sr.status_code == 200:
                    sc = sr.content
                    if sub_url.endswith(".gz"):
                        sc = gzip.decompress(sc)
                    _parse_sm(sc, depth + 1)
            except Exception:
                pass
            time.sleep(RATE_DELAY)

    for sm_url in sitemap_urls[:5]:
        try:
            sr = session.get(sm_url, timeout=TIMEOUT)
            if sr.status_code == 200:
                content = sr.content
                if sm_url.endswith(".gz"):
                    content = gzip.decompress(content)
                _parse_sm(content)
                if attorney_urls:
                    break
        except Exception:
            pass
        time.sleep(RATE_DELAY)

    return robots_found, sitemap_urls[:10], bool(attorney_urls), attorney_urls[:5]


def _probe_json_api(session: requests.Session, base_url: str) -> tuple[str, list[str], int]:
    """
    Returns (path_that_returned_json, sample_keys, total_count).
    """
    for probe_path in ALPHA_PROBE_PATHS:
        url = base_url.rstrip("/") + probe_path
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            ct = r.headers.get("content-type", "").lower()
            body = r.text.strip()
            is_json = "json" in ct or body.startswith("{") or body.startswith("[")
            if not is_json:
                continue
            data = r.json()
            # Extract top-level keys and total
            sample_keys: list[str] = []
            total = 0
            if isinstance(data, dict):
                sample_keys = list(data.keys())[:10]
                for k in ("TotalSearchResults", "totalSearchResults", "total", "Total", "count"):
                    if k in data:
                        try:
                            total = int(data[k])
                            break
                        except Exception:
                            pass
                # Unwrap common wrappers
                for k in ("results", "data", "attorneys", "lawyers", "people", "items"):
                    if k in data and isinstance(data[k], list) and data[k]:
                        sample_keys = list(data[k][0].keys())[:10] if isinstance(data[k][0], dict) else sample_keys
                        break
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                sample_keys = list(data[0].keys())[:10]
                for k in ("TotalSearchResults", "totalSearchResults"):
                    if k in data[0]:
                        try:
                            total = int(data[0][k])
                            break
                        except Exception:
                            pass
            return probe_path, sample_keys, total
        except Exception:
            continue
        time.sleep(RATE_DELAY * 0.5)
    return "", [], 0


def _probe_directory(session: requests.Session, base_url: str) -> tuple[str, int, int, bool, bool, bool, str]:
    """
    Returns (path, status, html_size, has_alpha, has_pagination, has_scroll, page_title).
    """
    for path in DIRECTORY_PATHS:
        url = base_url.rstrip("/") + path
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code not in (200, 301, 302):
                continue
            # Follow redirects
            if r.status_code in (301, 302):
                url = r.headers.get("Location", url)
                r = session.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            html = r.text
            if len(html) < 1000:
                continue

            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            page_title = title_tag.get_text(strip=True) if title_tag else ""

            has_alpha = _detect_alphabet_nav(html)
            has_pag = _detect_pagination(html)
            has_scroll = _detect_infinite_scroll(html)

            return path, r.status_code, len(html), has_alpha, has_pag, has_scroll, page_title
        except Exception:
            continue
        time.sleep(RATE_DELAY * 0.5)
    return "", 0, 0, False, False, False, ""


def _classify(s: SiteStructure) -> tuple[str, float, str]:
    """Classify into a STRUCTURE_TYPE based on probed signals."""

    if s.is_bot_protected or s.cf_detected:
        return "BOT_PROTECTED", 0.95, "Cloudflare or bot-wall detected"

    if s.is_auth_required:
        return "AUTH_REQUIRED", 0.95, "Login required (401)"

    if s.http_status == 0:
        return "UNKNOWN", 0.0, "Site unreachable"

    # JSON API with ?letter=A
    if s.json_api_path:
        return "JSON_API_ALPHA", 0.95, f"JSON API at {s.json_api_path}"

    # Sitemap has attorney URLs
    if s.sitemap_has_attorney_urls:
        return "SITEMAP_XML", 0.90, f"Sitemap has attorney URLs: {s.sitemap_attorney_sample[:2]}"

    # HTML directory found
    if s.directory_path_found:
        # SPA
        if s.spa_framework in ("nextjs", "nuxt"):
            return "SPA_NEXTJS", 0.85, f"SPA ({s.spa_framework}) at {s.directory_path_found}"
        if s.spa_framework:
            return "SPA_OTHER", 0.80, f"SPA ({s.spa_framework}) at {s.directory_path_found}"
        # Alphabet nav
        if s.has_alphabet_nav:
            if s.has_infinite_scroll_signals and not s.has_pagination:
                return "HTML_ALPHA_SCROLL", 0.85, "A-Z nav + infinite scroll signals"
            return "HTML_ALPHA_PAGINATED", 0.85, "A-Z nav + pagination"
        # Plain directory
        if s.directory_html_size > 5000:
            return "HTML_DIRECTORY_FLAT", 0.70, f"Plain HTML directory at {s.directory_path_found}"

    # Check homepage for SPA
    if s.spa_framework:
        if s.spa_framework in ("nextjs", "nuxt"):
            return "SPA_NEXTJS", 0.65, f"SPA ({s.spa_framework}) on homepage"
        return "SPA_OTHER", 0.60, f"SPA ({s.spa_framework}) on homepage"

    return "UNKNOWN", 0.30, "No clear structure detected"


# ---------------------------------------------------------------------------
# Main probe function per firm
# ---------------------------------------------------------------------------

def probe_firm(rank: int, firm: str, url: str) -> SiteStructure:
    s = SiteStructure(rank=rank, firm=firm, url=url)
    t0 = time.time()
    session = _session()

    try:
        # 1. Homepage probe
        resp = _get(session, url)
        if resp is None:
            s.error = "homepage unreachable"
            s.structure_type, s.confidence, s.notes = _classify(s)
            s.probe_seconds = round(time.time() - t0, 2)
            return s

        s.http_status = resp.status_code
        if resp.url != url:
            s.redirected_to = resp.url

        # Bot detection
        bot, auth = _detect_bot_block(resp)
        s.is_bot_protected = bot
        s.is_auth_required = auth

        if s.http_status == 200:
            html = resp.text
            s.spa_framework = _detect_spa(html)
            soup = BeautifulSoup(html[:5000], "html.parser")
            t = soup.find("title")
            s.page_title = t.get_text(strip=True)[:120] if t else ""

        if s.is_bot_protected or s.is_auth_required:
            s.structure_type, s.confidence, s.notes = _classify(s)
            s.probe_seconds = round(time.time() - t0, 2)
            return s

        # 2. Sitemap probe
        (s.robots_txt_found,
         s.sitemap_urls_in_robots,
         s.sitemap_has_attorney_urls,
         s.sitemap_attorney_sample) = _probe_sitemap(session, url)
        time.sleep(RATE_DELAY)

        # 3. JSON API probe
        s.json_api_path, s.json_api_sample_keys, s.json_api_total_count = _probe_json_api(session, url)
        time.sleep(RATE_DELAY)

        # 4. Directory probe (only if no JSON API found)
        if not s.json_api_path:
            (s.directory_path_found,
             s.directory_status,
             s.directory_html_size,
             s.has_alphabet_nav,
             s.has_pagination,
             s.has_infinite_scroll_signals,
             title) = _probe_directory(session, url)
            if title and not s.page_title:
                s.page_title = title

        # 5. Classify
        s.structure_type, s.confidence, s.notes = _classify(s)

    except Exception as exc:
        s.error = str(exc)
        s.structure_type = "UNKNOWN"

    s.probe_seconds = round(time.time() - t0, 2)
    return s


# ---------------------------------------------------------------------------
# Load firm list
# ---------------------------------------------------------------------------

def load_firms(xlsx_path: str, max_firms: int | None = None) -> list[tuple[int, str, str]]:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    firms = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=1):
        if row[0] and row[2]:
            firms.append((i, str(row[0]), str(row[2])))
    if max_firms:
        firms = firms[:max_firms]
    return firms


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(results: list[SiteStructure]) -> None:
    from collections import Counter
    counts = Counter(r.structure_type for r in results)
    total = len(results)

    print("\n" + "=" * 70)
    print(f"  STRUCTURE PROBE COMPLETE — {total} firms")
    print("=" * 70)
    print(f"\n{'Type':<28} {'Count':>6}  {'%':>5}  Description")
    print("-" * 70)
    for stype, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        desc = STRUCTURE_TYPES.get(stype, "")[:35]
        print(f"  {stype:<26} {count:>6}  {pct:>4.0f}%  {desc}")

    print("\n--- Firms by type ---")
    by_type: dict[str, list[str]] = {}
    for r in results:
        by_type.setdefault(r.structure_type, []).append(r.firm)
    for stype in sorted(by_type.keys()):
        print(f"\n[{stype}]")
        for firm in by_type[stype]:
            print(f"  • {firm}")

    print(f"\nOutput: {OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Probe AmLaw200 site structures")
    parser.add_argument("--xlsx", default="AmLaw200_2025 Rank_gross revenue_with_websites.xlsx")
    parser.add_argument("--max-firms", type=int, default=None)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--resume", action="store_true", help="Skip already-probed firms")
    parser.add_argument("--firm", help="Probe single firm (partial match)")
    args = parser.parse_args()

    firms = load_firms(args.xlsx, args.max_firms)

    if args.firm:
        firms = [(r, n, u) for r, n, u in firms if args.firm.lower() in n.lower()]
        print(f"Filtering to {len(firms)} firm(s) matching '{args.firm}'")

    # Load existing results for resume
    existing: dict[str, dict] = {}
    if args.resume and OUTPUT_FILE.exists():
        with OUTPUT_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        existing = {item["firm"]: item for item in data}
        print(f"Resuming — {len(existing)} firms already probed")

    results: list[SiteStructure] = []
    todo = [(r, n, u) for r, n, u in firms if n not in existing]
    done = [SiteStructure(**existing[n]) for r, n, u in firms if n in existing]
    results.extend(done)

    print(f"Probing {len(todo)} firms with {args.workers} workers...")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe_firm, rank, name, url): name
            for rank, name, url in todo
        }
        completed = 0
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = SiteStructure(
                    rank=0, firm=name, url="",
                    error=str(exc), structure_type="UNKNOWN"
                )
            results.append(result)
            completed += 1
            elapsed = time.time() - t0
            print(
                f"  [{completed:>3}/{len(todo)}] {result.firm[:35]:<35} "
                f"→ {result.structure_type:<24} "
                f"({result.probe_seconds:.1f}s)  [{elapsed:.0f}s total]"
            )

    # Sort by rank
    results.sort(key=lambda x: x.rank)

    # Save JSON
    output = [asdict(r) for r in results]
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print_summary(results)

    elapsed = time.time() - t0
    print(f"\nTotal probe time: {elapsed:.0f}s for {len(results)} firms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
