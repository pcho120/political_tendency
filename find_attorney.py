#!/usr/bin/env python3
"""find_attorney.py - High-Coverage Attorney Scraper (Option B)

Strategy:
1. FAST PATH: Sitemap + robots.txt + requests (30s per firm)
2. FALLBACK: Stabilization-based Playwright discovery with:
   - Pagination, load-more, infinite scroll (stabilization)
   - Filter enumeration (practice/office)
   - API detection + full enumeration
   - Coverage auditing (expected vs actual)

Usage:
  python find_attorney.py "Company list_with_websites.xlsx"
  python find_attorney.py --debug-firm "Kirkland & Ellis" --headful true
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from openpyxl import load_workbook

# Configuration
DEFAULT_SHEET_NAME = "Attorneys"
MAX_WORKERS = 4
REQUEST_TIMEOUT = 10
MIN_ATTORNEYS_THRESHOLD = 5
DEFAULT_STABILIZATION = 3
DEFAULT_MAX_SCROLL_SECONDS = 120
RATE_LIMIT_DELAY = 0.5  # seconds between requests per domain


@dataclass
class DiscoveryMetrics:
    """Track discovery coverage metrics"""
    directory_url: str = ""
    expected_total: int = 0
    discovered_unique: int = 0
    discovered_by_dom: int = 0
    discovered_by_pagination: int = 0
    discovered_by_loadmore: int = 0
    discovered_by_scroll: int = 0
    discovered_by_filters: int = 0
    discovered_by_api: int = 0
    failure_notes: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "directory_url": self.directory_url,
            "expected_total": self.expected_total,
            "discovered_unique": self.discovered_unique,
            "discovered_by_dom": self.discovered_by_dom,
            "discovered_by_pagination": self.discovered_by_pagination,
            "discovered_by_loadmore": self.discovered_by_loadmore,
            "discovered_by_scroll": self.discovered_by_scroll,
            "discovered_by_filters": self.discovered_by_filters,
            "discovered_by_api": self.discovered_by_api,
            "failure_notes": self.failure_notes,
            "coverage_ratio": (
                self.discovered_unique / self.expected_total
                if self.expected_total > 0
                else 0.0
            ),
        }


class AttorneyFinder:
    def __init__(
        self,
        *,
        limit: int,
        sheet_name: str,
        max_firms: int,
        workers: int,
        debug_firm: str = "",
        debug_domain: str = "",
        headful: bool = False,
        stabilization: int = DEFAULT_STABILIZATION,
        max_scroll_seconds: int = DEFAULT_MAX_SCROLL_SECONDS,
    ) -> None:
        self.limit = limit
        self.sheet_name = sheet_name
        self.max_firms = max_firms
        self.workers = workers
        self.debug_firm = debug_firm
        self.debug_domain = debug_domain
        self.headful = headful
        self.stabilization = stabilization
        self.max_scroll_seconds = max_scroll_seconds
        
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        
        # Create debug directory
        self.debug_dir = Path("debug_reports")
        self.debug_dir.mkdir(exist_ok=True)
        
        # Track last request time per domain for rate limiting
        self.domain_last_request: dict[str, float] = {}

    def log(self, msg: str) -> None:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            print(msg.encode("utf-8", errors="ignore").decode("utf-8"), flush=True)

    def _rate_limit(self, domain: str) -> None:
        """Enforce per-domain rate limiting"""
        last = self.domain_last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self.domain_last_request[domain] = time.time()

    def run(self, excel_path: str) -> int:
        if not os.path.exists(excel_path):
            raise FileNotFoundError(excel_path)
        if not excel_path.lower().endswith(".xlsx"):
            raise ValueError("Input must be an .xlsx file")

        wb = load_workbook(excel_path)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        if "Official Website" not in headers or "Firm" not in headers:
            for sheet in wb.worksheets:
                sheet_headers = [cell.value for cell in sheet[1]]
                if "Official Website" in sheet_headers and "Firm" in sheet_headers:
                    ws = sheet
                    headers = sheet_headers
                    break

        try:
            url_col_idx = headers.index("Official Website") + 1
            firm_col_idx = headers.index("Firm") + 1
        except ValueError as e:
            raise ValueError(
                "File must have 'Firm' and 'Official Website' columns."
            ) from e

        if self.sheet_name in wb.sheetnames:
            del wb[self.sheet_name]
        out_ws = wb.create_sheet(self.sheet_name)
        out_ws.append(
            ["Firm", "Attorney Name", "Title", "Practice Area", "Office", "Profile URL"]
        )

        firms = []
        for row in ws.iter_rows(min_row=2, values_only=False):
            firm_name = row[firm_col_idx - 1].value
            base_url = row[url_col_idx - 1].value
            if not firm_name or not base_url:
                continue
            
            # Debug mode filters
            if self.debug_firm and self.debug_firm.lower() not in firm_name.lower():
                continue
            if self.debug_domain:
                domain = urlparse(str(base_url)).netloc
                if self.debug_domain.lower() not in domain.lower():
                    continue
            
            firms.append((firm_name, str(base_url)))
            if self.max_firms > 0 and len(firms) >= self.max_firms:
                break

        if not firms:
            self.log("No firms to process (check debug filters)")
            return 0

        total_processed = 0

        # Process sequentially to manage Playwright browser lifecycle
        for firm, url in firms:
            try:
                attorneys, metrics = self.process_firm(firm, url)
                for att in attorneys:
                    out_ws.append(
                        [
                            firm,
                            att.get("name", ""),
                            att.get("title", ""),
                            att.get("practice", ""),
                            att.get("office", ""),
                            att.get("url", ""),
                        ]
                    )
                    total_processed += 1
                
                # Save metrics
                self._save_metrics(firm, metrics)
                
                self.log(f"[OK] {firm}: {len(attorneys)} attorneys")
                wb.save(excel_path)
            except Exception as e:
                self.log(f"[ERROR] {firm}: {e}")
                import traceback
                traceback.print_exc()

        self.log(f"\nTotal: {total_processed} attorneys")
        return total_processed

    def _save_metrics(self, firm_name: str, metrics: DiscoveryMetrics) -> None:
        """Save discovery metrics to JSON"""
        safe_name = re.sub(r'[^\w\s-]', '', firm_name).strip().replace(' ', '_')
        metrics_path = self.debug_dir / f"{safe_name}_metrics.json"
        
        with open(metrics_path, 'w', encoding='utf-8') as f:
            json.dump(metrics.to_dict(), f, indent=2)
        
        self.log(f"  Metrics saved: {metrics_path}")

    def process_firm(self, firm_name: str, base_url: str) -> tuple[list[dict], DiscoveryMetrics]:
        """Process one firm: fast path first, Playwright fallback"""
        self.log(f"\nProcessing: {firm_name}...")
        start = time.time()
        
        metrics = DiscoveryMetrics()

        # FAST PATH: Sitemap + requests
        attorneys = self._try_fast_path(base_url, metrics)

        # FALLBACK: If insufficient data, use Playwright
        if len(attorneys) < MIN_ATTORNEYS_THRESHOLD:
            self.log(
                f"  Fast path insufficient ({len(attorneys)} found), trying Playwright..."
            )
            playwright_attorneys, metrics = self._try_playwright_path(base_url, metrics)
            if playwright_attorneys:
                attorneys = playwright_attorneys

        # Deduplicate
        seen = set()
        unique = []
        for att in attorneys:
            url = att.get("url", "")
            name = att.get("name", "")
            key = url if url else name
            if key and key not in seen:
                seen.add(key)
                unique.append(att)

        metrics.discovered_unique = len(unique)

        if self.limit > 0:
            unique = unique[: self.limit]

        elapsed = time.time() - start
        self.log(f"  Done: {len(unique)} attorneys ({elapsed:.1f}s)")
        
        # Coverage audit
        if metrics.expected_total > 0:
            ratio = metrics.discovered_unique / metrics.expected_total
            self.log(
                f"  Coverage: {metrics.discovered_unique}/{metrics.expected_total} ({ratio*100:.1f}%)"
            )
            if ratio < 0.98:
                metrics.failure_notes.append(
                    f"Low coverage: {ratio*100:.1f}% (expected {metrics.expected_total}, got {metrics.discovered_unique})"
                )
        
        return unique, metrics

    def _try_fast_path(self, base_url: str, metrics: DiscoveryMetrics) -> list[dict]:
        """Fast path: Sitemap + parallel requests"""
        attorneys = []

        try:
            # Try sitemap (with robots.txt check)
            profile_urls = self._extract_profile_urls_from_sitemap(base_url)
            if profile_urls:
                self.log(f"  Sitemap: found {len(profile_urls)} URLs")

                # Parallel metadata fetch
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = {
                        executor.submit(self._fetch_profile_metadata, url): url
                        for url in profile_urls[
                            : self.limit * 3 if self.limit > 0 else len(profile_urls)
                        ]
                    }
                    for future in as_completed(futures):
                        try:
                            att = future.result()
                            if att and att.get("name"):
                                attorneys.append(att)
                        except Exception:
                            pass
        except Exception as e:
            self.log(f"  Fast path error: {e}")
            metrics.failure_notes.append(f"Fast path error: {e}")

        return attorneys

    def _try_playwright_path(
        self, base_url: str, metrics: DiscoveryMetrics
    ) -> tuple[list[dict], DiscoveryMetrics]:
        """Playwright fallback with stabilization-based discovery"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.log("  Playwright not available, skipping")
            metrics.failure_notes.append("Playwright not installed")
            return [], metrics

        attorneys = []
        all_profile_urls = set()
        captured_api_data = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not self.headful)

            context = browser.new_context()

            # Block heavy resources
            def handle_route(route):
                if route.request.resource_type in [
                    "image",
                    "font",
                    "media",
                    "stylesheet",
                ]:
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", handle_route)

            page = context.new_page()

            # Intercept API responses
            def handle_response(response):
                try:
                    url_lower = response.url.lower()
                    if any(
                        kw in url_lower
                        for kw in [
                            "api",
                            "graphql",
                            "search",
                            "people",
                            "attorneys",
                            "lawyers",
                            "professionals",
                        ]
                    ):
                        if response.ok and "json" in response.headers.get(
                            "content-type", ""
                        ).lower():
                            try:
                                data = response.json()
                                records = self._extract_attorneys_from_json(
                                    data, base_url
                                )
                                captured_api_data.append(
                                    {"url": response.url, "records": records}
                                )
                            except Exception:
                                pass
                except Exception:
                    pass

            page.on("response", handle_response)

            # Find best directory URL
            dir_url, expected_total = self._find_best_directory_url(page, base_url)
            if not dir_url:
                self.log("  Could not find directory page")
                metrics.failure_notes.append("No directory page found")
                page.close()
                context.close()
                browser.close()
                return [], metrics

            metrics.directory_url = dir_url
            metrics.expected_total = expected_total
            self.log(f"  Directory: {dir_url}")
            if expected_total > 0:
                self.log(f"  Expected total: {expected_total}")

            # Navigate to directory
            page.goto(dir_url, timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            # DISCOVERY PHASE 1: Try API enumeration first (fastest + highest coverage)
            api_urls = self._enumerate_api_if_available(
                captured_api_data, base_url, metrics
            )
            all_profile_urls.update(api_urls)

            # DISCOVERY PHASE 2: Exhaust pagination/load-more/scroll (baseline, fast)
            if metrics.discovered_by_api == 0:  # Only if API didn't work
                baseline_urls = self._exhaust_directory_content(page, base_url, metrics)
                all_profile_urls.update(baseline_urls)

            # DISCOVERY PHASE 3: Enumerate filters (slower, only if needed)
            if (
                metrics.discovered_by_api == 0 
                and metrics.expected_total > 0
                and len(all_profile_urls) < metrics.expected_total * 0.5
            ):  # Only if we're missing >50% of expected attorneys
                self.log(f"  Low coverage ({len(all_profile_urls)}/{metrics.expected_total}), trying filter enumeration...")
                filter_urls = self._enumerate_filters(page, base_url, metrics)
                all_profile_urls.update(filter_urls)

            # Save debug artifacts if low coverage
            if metrics.expected_total > 0:
                ratio = len(all_profile_urls) / metrics.expected_total
                if ratio < 0.98 or len(all_profile_urls) < MIN_ATTORNEYS_THRESHOLD:
                    self._save_debug_artifacts(page, base_url, metrics)

            page.close()
            context.close()
            browser.close()

        # Enrich URLs with metadata
        self.log(f"  Enriching {len(all_profile_urls)} profile URLs...")
        attorneys = self._enrich_profile_urls(list(all_profile_urls))

        return attorneys, metrics

    def _find_best_directory_url(self, page, base_url: str) -> tuple[str, int]:
        """Find directory page with most profile links"""
        probe_paths = [
            "/people",
            "/professionals",
            "/attorneys",
            "/lawyers",
            "/our-people",
            "/our-team",
            "/team",
            "/find-a-professional",
            "/find-a-lawyer",
            "/attorney-search",
        ]

        best_url = ""
        best_score = 0
        expected_total = 0

        for path in probe_paths:
            try:
                target = urljoin(base_url, path)
                self.log(f"  Probing: {target}")
                response = page.goto(
                    target, timeout=20000, wait_until="networkidle"
                )
                if not response or not response.ok:
                    self.log(f"    Status: {response.status if response else 'No response'}")
                    continue

                page.wait_for_timeout(2000)  # Extra wait for SPAs

                # Count profile-like links
                links = page.evaluate(
                    """
                    () => {
                        const anchors = Array.from(document.querySelectorAll('a'));
                        return anchors.map(a => a.href).filter(Boolean);
                    }
                    """
                )

                profile_count = sum(
                    1
                    for href in links
                    if self._is_profile_like_url(href, urlparse(base_url).netloc)
                )

                self.log(f"    Found {len(links)} total links, {profile_count} profile-like")
                
                # Debug: Show sample URLs
                if len(links) > 0 and profile_count == 0:
                    self.log(f"    Sample URLs:")
                    for href in links[:5]:
                        self.log(f"      {href}")

                # Check for total count indicators
                total = self._extract_expected_total(page)

                if profile_count > best_score:
                    best_score = profile_count
                    best_url = target
                    expected_total = total
                    self.log(f"    [BEST] New best: {profile_count} profiles")

            except Exception as e:
                self.log(f"    Error: {e}")

        if best_url:
            self.log(f"  Best directory: {best_url} ({best_score} profiles)")
        return best_url, expected_total

    def _extract_expected_total(self, page) -> int:
        """Extract expected attorney count from directory page"""
        try:
            text = page.inner_text("body")
            
            # Patterns: "of 1234", "Showing 1-50 of 1234", "1234 Professionals", etc.
            patterns = [
                r"of\s+(\d{2,5})\s+(?:results|professionals|attorneys|lawyers|people)",
                r"showing\s+\d+\s*[-–]\s*\d+\s+of\s+(\d{2,5})",
                r"(\d{2,5})\s+(?:professionals|attorneys|lawyers|people|results)",
                r"total[:\s]+(\d{2,5})",
                r"(\d{3,5})\s+results",
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    if 10 <= count <= 10000:  # Sanity check
                        self.log(f"  Detected total count: {count}")
                        return count
        except Exception:
            pass
        
        return 0

    def _enumerate_api_if_available(
        self, captured_api_data: list[dict], base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """If API detected, enumerate all pages via requests"""
        if not captured_api_data:
            return set()

        self.log("  Detected API, attempting full enumeration...")
        all_urls = set()

        for api_data in captured_api_data:
            api_url = api_data["url"]
            initial_records = api_data["records"]
            
            if not initial_records:
                continue

            # Add initial records
            for rec in initial_records:
                if rec.get("url"):
                    all_urls.add(rec["url"])

            # Try to paginate API
            paginated_urls = self._paginate_api_endpoint(api_url, base_url)
            all_urls.update(paginated_urls)

        metrics.discovered_by_api = len(all_urls)
        if metrics.discovered_by_api > 0:
            self.log(f"  API enumeration: {metrics.discovered_by_api} URLs")

        return all_urls

    def _paginate_api_endpoint(self, api_url: str, base_url: str) -> set[str]:
        """Attempt to paginate API endpoint"""
        all_urls = set()
        
        parsed = urlparse(api_url)
        query_params = parse_qs(parsed.query)
        
        # Common pagination params
        page_param = None
        for param in ["page", "pageNumber", "pageNum", "p"]:
            if param in query_params:
                page_param = param
                break
        
        offset_param = None
        for param in ["offset", "start", "skip", "from"]:
            if param in query_params:
                offset_param = param
                break
        
        limit_param = None
        for param in ["limit", "size", "pageSize", "count", "take"]:
            if param in query_params:
                limit_param = param
                break
        
        if not page_param and not offset_param:
            # Can't paginate
            return all_urls
        
        # Try paginating
        page = 1
        offset = 0
        limit = int(query_params.get(limit_param, ["50"])[0]) if limit_param else 50
        stabilization_counter = 0
        
        domain = urlparse(base_url).netloc
        
        for attempt in range(200):  # Safety cap
            if stabilization_counter >= self.stabilization:
                break
            
            # Build paginated URL
            new_params = query_params.copy()
            if page_param:
                new_params[page_param] = [str(page)]
                page += 1
            elif offset_param:
                new_params[offset_param] = [str(offset)]
                offset += limit
            
            new_query = urlencode(new_params, doseq=True)
            paginated_url = urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
            )
            
            try:
                self._rate_limit(domain)
                resp = self.session.get(paginated_url, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    break
                
                data = resp.json()
                records = self._extract_attorneys_from_json(data, base_url)
                
                if not records:
                    stabilization_counter += 1
                else:
                    stabilization_counter = 0
                    for rec in records:
                        if rec.get("url"):
                            all_urls.add(rec["url"])
                
            except Exception:
                break
        
        return all_urls

    def _enumerate_filters(
        self, page, base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """Enumerate single-facet filters (Practice, Office, etc.)"""
        self.log("  Attempting filter enumeration...")
        all_urls = set()

        try:
            # Detect filter UI
            filter_groups = page.evaluate(
                """
                () => {
                    // Look for common filter patterns
                    const filters = [];
                    
                    // Pattern 1: Select dropdowns
                    const selects = Array.from(document.querySelectorAll('select'));
                    for (const select of selects) {
                        const label = select.previousElementSibling?.innerText || select.id || '';
                        if (/practice|office|location|industry/i.test(label + select.name + select.id)) {
                            const options = Array.from(select.options)
                                .filter(opt => opt.value && opt.value !== '')
                                .map(opt => ({value: opt.value, text: opt.text}));
                            if (options.length > 0) {
                                filters.push({
                                    type: 'select',
                                    element: select,
                                    label: label,
                                    options: options
                                });
                            }
                        }
                    }
                    
                    // Pattern 2: Clickable filter buttons/links
                    const filterContainers = Array.from(document.querySelectorAll('[class*="filter"], [class*="facet"]'));
                    for (const container of filterContainers) {
                        const label = container.querySelector('label, h3, h4, .label')?.innerText || '';
                        if (/practice|office|location/i.test(label)) {
                            const clickables = Array.from(container.querySelectorAll('a, button, [role="button"]'))
                                .filter(el => el.innerText.trim())
                                .map(el => ({text: el.innerText.trim(), selector: el.tagName}));
                            if (clickables.length > 1) {
                                filters.push({
                                    type: 'clickable',
                                    label: label,
                                    items: clickables
                                });
                            }
                        }
                    }
                    
                    return filters;
                }
                """
            )

            if not filter_groups:
                self.log("  No filters detected")
                return all_urls

            self.log(f"  Found {len(filter_groups)} filter groups")

            # Enumerate each filter group
            for group_idx, group in enumerate(filter_groups[:1]):  # Limit to 1 group for speed
                filter_type = group.get("type")
                
                if filter_type == "select":
                    # Handle select dropdowns
                    options = group.get("options", [])
                    for idx, opt in enumerate(options[:10]):  # Cap at 10 values per filter
                        try:
                            self.log(f"  Filter {group_idx+1}/{len(filter_groups[:1])}, option {idx+1}/{min(10, len(options))}: {opt['text'][:40]}")
                            
                            # Select option
                            page.select_option(
                                f"select:has-text('{group['label']}')", opt["value"]
                            )
                            page.wait_for_timeout(800)
                            
                            # Exhaust content with quick extraction (no deep exhaustion)
                            urls = self._extract_profile_urls_from_page(page, base_url)
                            all_urls.update(urls)
                            
                            # Reset (reload page)
                            page.reload()
                            page.wait_for_timeout(800)
                        except Exception as e:
                            self.log(f"  Filter option error: {e}")

                elif filter_type == "clickable":
                    # Handle clickable filters
                    items = group.get("items", [])
                    for idx, item in enumerate(items[:10]):  # Cap at 10
                        try:
                            self.log(f"  Filter {group_idx+1}/{len(filter_groups[:1])}, item {idx+1}/{min(10, len(items))}: {item['text'][:40]}")
                            
                            # Click filter
                            page.click(f"text={item['text']}")
                            page.wait_for_timeout(800)
                            
                            # Quick extraction (no deep exhaustion)
                            urls = self._extract_profile_urls_from_page(page, base_url)
                            all_urls.update(urls)
                            
                            # Reset
                            page.reload()
                            page.wait_for_timeout(800)
                        except Exception as e:
                            self.log(f"  Filter item error: {e}")

            metrics.discovered_by_filters = len(all_urls)
            if metrics.discovered_by_filters > 0:
                self.log(f"  Filter enumeration: {metrics.discovered_by_filters} URLs")

        except Exception as e:
            self.log(f"  Filter enumeration error: {e}")
            metrics.failure_notes.append(f"Filter enumeration error: {e}")

        return all_urls

    def _exhaust_directory_content(
        self, page, base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """Exhaust pagination, load-more, and infinite scroll with stabilization"""
        all_urls = set()
        start_time = time.time()
        
        # Phase 1: Pagination (highest priority)
        pagination_urls = self._handle_pagination_stabilized(page, base_url, metrics)
        all_urls.update(pagination_urls)
        
        # Phase 2: Load-more buttons
        if time.time() - start_time < self.max_scroll_seconds:
            loadmore_urls = self._handle_loadmore_stabilized(page, base_url, metrics)
            all_urls.update(loadmore_urls)
        
        # Phase 3: Infinite scroll
        if time.time() - start_time < self.max_scroll_seconds:
            scroll_urls = self._handle_scroll_stabilized(page, base_url, metrics)
            all_urls.update(scroll_urls)
        
        # Phase 4: Extract all visible links
        dom_urls = self._extract_profile_urls_from_page(page, base_url)
        all_urls.update(dom_urls)
        metrics.discovered_by_dom = len(dom_urls)
        
        return all_urls

    def _handle_pagination_stabilized(
        self, page, base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """Handle pagination with stabilization"""
        all_urls = set()
        stabilization_counter = 0
        page_num = 1
        max_pages = 50  # Safety cap
        
        while stabilization_counter < self.stabilization and page_num <= max_pages:
            try:
                prev_count = len(all_urls)
                
                # Extract URLs from current page
                urls = self._extract_profile_urls_from_page(page, base_url)
                all_urls.update(urls)
                
                # Check for next button
                next_button = None
                for selector in [
                    'a[rel="next"]',
                    'a:has-text("Next")',
                    'button:has-text("Next")',
                    '.pagination a:has-text("›")',
                    '.pagination a:has-text("»")',
                    f'a:has-text("{page_num + 1}")',
                ]:
                    try:
                        if page.locator(selector).count() > 0:
                            next_button = selector
                            break
                    except Exception:
                        pass
                
                if not next_button:
                    break
                
                # Click next
                page.click(next_button, timeout=5000)
                page.wait_for_timeout(1500)
                page_num += 1
                
                # Check stabilization
                if len(all_urls) == prev_count:
                    stabilization_counter += 1
                else:
                    stabilization_counter = 0
                    
            except Exception:
                break
        
        metrics.discovered_by_pagination = len(all_urls)
        if metrics.discovered_by_pagination > 0:
            self.log(f"  Pagination: {metrics.discovered_by_pagination} URLs ({page_num} pages)")
        
        return all_urls

    def _handle_loadmore_stabilized(
        self, page, base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """Handle load-more buttons with stabilization"""
        all_urls = set()
        stabilization_counter = 0
        clicks = 0
        max_clicks = 30  # Safety cap
        
        while stabilization_counter < self.stabilization and clicks < max_clicks:
            try:
                prev_count = len(all_urls)
                
                # Extract URLs
                urls = self._extract_profile_urls_from_page(page, base_url)
                all_urls.update(urls)
                
                # Find load-more button
                load_more = None
                for selector in [
                    'button:has-text("Load More")',
                    'button:has-text("Show More")',
                    'a:has-text("Load More")',
                    'a:has-text("Show More")',
                    '[class*="load-more"]',
                    '[class*="show-more"]',
                ]:
                    try:
                        btn = page.locator(selector)
                        if btn.count() > 0 and btn.first.is_visible():
                            load_more = selector
                            break
                    except Exception:
                        pass
                
                if not load_more:
                    break
                
                # Click
                page.click(load_more, timeout=5000)
                page.wait_for_timeout(1500)
                clicks += 1
                
                # Check stabilization
                if len(all_urls) == prev_count:
                    stabilization_counter += 1
                else:
                    stabilization_counter = 0
                    
            except Exception:
                break
        
        metrics.discovered_by_loadmore = len(all_urls)
        if metrics.discovered_by_loadmore > 0:
            self.log(
                f"  Load-more: {metrics.discovered_by_loadmore} URLs ({clicks} clicks)"
            )
        
        return all_urls

    def _handle_scroll_stabilized(
        self, page, base_url: str, metrics: DiscoveryMetrics
    ) -> set[str]:
        """Handle infinite scroll with stabilization"""
        all_urls = set()
        stabilization_counter = 0
        scrolls = 0
        max_scrolls = 20  # Safety cap
        
        while stabilization_counter < self.stabilization and scrolls < max_scrolls:
            try:
                prev_count = len(all_urls)
                
                # Extract URLs
                urls = self._extract_profile_urls_from_page(page, base_url)
                all_urls.update(urls)
                
                # Scroll to bottom
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
                scrolls += 1
                
                # Check stabilization
                if len(all_urls) == prev_count:
                    stabilization_counter += 1
                else:
                    stabilization_counter = 0
                    
            except Exception:
                break
        
        metrics.discovered_by_scroll = len(all_urls)
        if metrics.discovered_by_scroll > 0:
            self.log(f"  Scroll: {metrics.discovered_by_scroll} URLs ({scrolls} scrolls)")
        
        return all_urls

    def _extract_profile_urls_from_page(self, page, base_url: str) -> set[str]:
        """Extract all profile-like URLs from current page"""
        try:
            links = page.evaluate(
                """
                () => {
                    const anchors = Array.from(document.querySelectorAll('a'));
                    return anchors.map(a => a.href).filter(Boolean);
                }
                """
            )
            
            domain = urlparse(base_url).netloc
            profile_urls = set()
            all_candidate_count = 0
            
            for href in links:
                all_candidate_count += 1
                if self._is_profile_like_url(href, domain):
                    profile_urls.add(href)
            
            if all_candidate_count > 0 and len(profile_urls) == 0:
                # Log first few URLs to help debug
                self.log(f"  DEBUG: Found {all_candidate_count} total links, 0 profile URLs. Sample links:")
                for href in list(links)[:5]:
                    self.log(f"    {href}")
            
            return profile_urls
        except Exception as e:
            self.log(f"  Error extracting URLs: {e}")
            return set()

    def _is_profile_like_url(self, url: str, expected_domain: str) -> bool:
        """Check if URL looks like attorney profile"""
        try:
            parsed = urlparse(url)
            
            # Must be same domain
            if expected_domain not in parsed.netloc:
                return False
            
            # Must not be mailto/tel
            if parsed.scheme in ["mailto", "tel"]:
                return False
            
            url_lower = url.lower()
            
            # Must contain profile keywords
            keywords = [
                "/lawyer",
                "/attorney",
                "/people/",
                "/professional",
                "/bio/",
                "/profile/",
                "/team/",
                "/our-people/",
            ]
            if not any(kw in url_lower for kw in keywords):
                return False
            
            # Filter out obvious junk
            junk_keywords = [
                "terms",
                "privacy",
                "advertising",
                "disclaimer",
                "cookie",
                "sitemap",
                "/search",
                "login",
                "subscribe",
                "career",
                "alumni",
            ]
            if any(junk in url_lower for junk in junk_keywords):
                return False
            
            # Must have reasonable path depth (at least something after the directory)
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) < 2:
                return False
            
            return True
        except Exception:
            return False

    def _enrich_profile_urls(self, urls: list[str]) -> list[dict]:
        """Fetch metadata for all profile URLs"""
        attorneys = []
        
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._fetch_profile_metadata, url): url for url in urls}
            for future in as_completed(futures):
                try:
                    att = future.result()
                    if att and att.get("name"):
                        attorneys.append(att)
                except Exception:
                    pass
        
        return attorneys

    def _save_debug_artifacts(self, page, base_url: str, metrics: DiscoveryMetrics) -> None:
        """Save screenshot and HTML for debugging"""
        try:
            safe_name = re.sub(r'[^\w\s-]', '', urlparse(base_url).netloc).strip().replace('.', '_')
            
            screenshot_path = self.debug_dir / f"{safe_name}_screenshot.png"
            html_path = self.debug_dir / f"{safe_name}_page.html"
            
            page.screenshot(path=str(screenshot_path))
            html = page.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            
            self.log(f"  Debug artifacts saved: {screenshot_path}, {html_path}")
        except Exception as e:
            self.log(f"  Could not save debug artifacts: {e}")

    def _extract_profile_urls_from_sitemap(self, base_url: str) -> list[str]:
        """Extract attorney profile URLs from sitemap (with robots.txt check)"""
        domain = urlparse(base_url).netloc
        scheme = urlparse(base_url).scheme

        # Check robots.txt for sitemap directives
        sitemap_urls = []
        try:
            robots_url = f"{scheme}://{domain}/robots.txt"
            self._rate_limit(domain)
            resp = self.session.get(robots_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                for line in resp.text.split('\n'):
                    if line.lower().startswith('sitemap:'):
                        sitemap_url = line.split(':', 1)[1].strip()
                        sitemap_urls.append(sitemap_url)
        except Exception:
            pass

        # Add default locations
        if not sitemap_urls:
            sitemap_urls = [
                f"{scheme}://{domain}/sitemap.xml",
                f"{scheme}://{domain}/sitemap_index.xml",
            ]

        all_profile_urls = set()
        for sitemap_url in sitemap_urls:
            try:
                self._rate_limit(domain)
                resp = self.session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    continue

                content = resp.content
                if sitemap_url.endswith(".gz"):
                    content = gzip.decompress(content)

                root = ET.fromstring(content)
                self._parse_sitemap_recursive(root, all_profile_urls, base_url)

                if all_profile_urls:
                    break
            except Exception:
                continue

        return list(all_profile_urls)

    def _parse_sitemap_recursive(
        self, element: ET.Element, urls: set, base_url: str
    ) -> None:
        """Recursively parse sitemap XML"""
        domain = urlparse(base_url).netloc
        
        for child in element:
            tag = child.tag.lower()
            # Handle namespaces
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            
            if "loc" in tag and child.text:
                loc = child.text.strip()
                if loc.endswith(".xml") or loc.endswith(".xml.gz"):
                    try:
                        self._rate_limit(domain)
                        resp = self.session.get(loc, timeout=REQUEST_TIMEOUT)
                        if resp.status_code == 200:
                            content = resp.content
                            if loc.endswith(".gz"):
                                content = gzip.decompress(content)
                            sub_root = ET.fromstring(content)
                            self._parse_sitemap_recursive(sub_root, urls, base_url)
                    except Exception:
                        pass
                else:
                    if self._is_attorney_profile_url(loc):
                        urls.add(loc)
            
            # Recurse into children
            self._parse_sitemap_recursive(child, urls, base_url)

    def _is_attorney_profile_url(self, url: str) -> bool:
        """Check if URL is attorney profile"""
        url_lower = url.lower()
        keywords = ["/lawyer", "/attorney", "/people/", "/professional", "/bio/", "/profile/"]
        if not any(kw in url_lower for kw in keywords):
            return False
        
        # Filter junk
        junk_keywords = [
            "terms",
            "privacy",
            "advertising",
            "disclaimer",
            "cookie",
            "sitemap",
        ]
        if any(junk in url_lower for junk in junk_keywords):
            return False
        
        return True

    def _fetch_profile_metadata(self, url: str) -> dict | None:
        """Fetch metadata from profile page"""
        try:
            domain = urlparse(url).netloc
            self._rate_limit(domain)
            
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return None

            html = resp.text
            name = self._extract_name_from_html(html, url)
            title = self._extract_title_from_html(html)
            practice = self._extract_practice_from_html(html)
            office = self._extract_office_from_html(html)

            if not name:
                return None

            return {
                "name": name,
                "title": title,
                "practice": practice,
                "office": office,
                "url": url,
            }
        except Exception:
            return None

    def _extract_name_from_html(self, html: str, url: str) -> str:
        """Extract name from HTML"""
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if h1_match:
            name = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
            if name and len(name) < 100 and self._looks_like_person_name(name):
                return name

        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
        )
        if title_match:
            title_text = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
            parts = re.split(r"[|\-–—]", title_text)
            if parts:
                name = parts[0].strip()
                if name and len(name) < 100 and self._looks_like_person_name(name):
                    return name

        path = urlparse(url).path
        segments = [s for s in path.split("/") if s]
        if segments:
            last = segments[-1].replace("-", " ").replace("_", " ").title()
            if self._looks_like_person_name(last):
                return last

        return ""

    def _extract_title_from_html(self, html: str) -> str:
        """Extract title from HTML"""
        patterns = [
            r'<[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</[^>]+>',
            r'<[^>]*class="[^"]*position[^"]*"[^>]*>(.*?)</[^>]+>',
            r'<span[^>]*>\s*(Partner|Associate|Counsel|Of Counsel)\s*</span>',
        ]
        for pat in patterns:
            match = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if match:
                text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                if text and len(text) < 100:
                    return text
        return ""

    def _extract_practice_from_html(self, html: str) -> str:
        """Extract practice area from HTML"""
        practice_section = re.search(
            r"<[^>]*>Practice[s]?\s*(?:Area[s]?)?</[^>]*>(.*?)</(?:div|section|ul)",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if practice_section:
            content = practice_section.group(1)
            links = re.findall(r"<a[^>]*>(.*?)</a>", content, re.IGNORECASE)
            practices = [re.sub(r"<[^>]+>", "", link).strip() for link in links]
            practices = [p for p in practices if p and len(p) < 100]
            if practices:
                return ", ".join(practices[:5])
        return ""

    def _extract_office_from_html(self, html: str) -> str:
        """Extract office from HTML"""
        patterns = [
            r'<[^>]*class="[^"]*office[^"]*"[^>]*>(.*?)</[^>]+>',
            r'<[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</[^>]+>',
        ]
        for pat in patterns:
            match = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if match:
                text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                if text and len(text) < 200:
                    return text
        return ""

    def _extract_attorneys_from_json(self, data: Any, base_url: str) -> list[dict]:
        """Extract attorney records from JSON"""
        records = []

        def visit(obj):
            if isinstance(obj, dict):
                has_name = any(
                    k in obj
                    for k in ["name", "fullName", "displayName", "firstName", "lastName"]
                )
                if has_name:
                    records.append(obj)
                for v in obj.values():
                    visit(v)
            elif isinstance(obj, list):
                for item in obj:
                    visit(item)

        visit(data)

        attorneys = []
        for rec in records:
            att = self._normalize_json_record(rec, base_url)
            if att and att.get("name") and self._looks_like_person_name(att["name"]):
                attorneys.append(att)

        return attorneys

    def _normalize_json_record(self, rec: dict, base_url: str) -> dict | None:
        """Normalize JSON record"""

        def pick(*keys):
            for k in keys:
                if k in rec and rec[k]:
                    return rec[k]
            return None

        def to_text(val):
            if val is None:
                return ""
            if isinstance(val, str):
                return val.strip()
            if isinstance(val, list):
                parts = [to_text(v) for v in val if v]
                return ", ".join(parts)
            return str(val).strip()

        first = pick("firstName", "first_name", "first")
        last = pick("lastName", "last_name", "last")
        name = pick("name", "fullName", "displayName") or " ".join(
            filter(None, [to_text(first), to_text(last)])
        )

        title = pick("title", "position", "role", "jobTitle")
        practice = pick("practice", "practiceAreas", "practices", "services")
        office = pick("office", "location", "officeName")
        url = pick("url", "profileUrl", "link", "path")

        url_text = to_text(url)
        if url_text and not url_text.startswith("http"):
            url_text = urljoin(base_url, url_text)

        name_text = to_text(name)
        if not name_text or len(name_text) < 4:
            return None

        return {
            "name": name_text,
            "title": to_text(title),
            "practice": to_text(practice),
            "office": to_text(office),
            "url": url_text,
        }

    def _looks_like_person_name(self, text: str) -> bool:
        """Check if text looks like person name"""
        if not text or len(text) < 4:
            return False
        if any(ch in text for ch in ["_", "#", "{"]):
            return False
        parts = [p for p in text.split() if p and p[0].isupper()]
        return len(parts) >= 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="High-coverage attorney scraper with stabilization-based discovery"
    )
    parser.add_argument("excel_path", nargs="?", help="Excel file path")
    parser.add_argument(
        "--limit", type=int, default=0, help="Max attorneys per firm (0=all)"
    )
    parser.add_argument(
        "--max-firms", type=int, default=0, help="Max firms to process (0=all)"
    )
    parser.add_argument(
        "--workers", type=int, default=MAX_WORKERS, help="Parallel workers"
    )
    parser.add_argument(
        "--sheet",
        default=DEFAULT_SHEET_NAME,
        help=f"Output sheet (default: {DEFAULT_SHEET_NAME})",
    )
    parser.add_argument(
        "--debug-firm", default="", help="Process only firms matching this name"
    )
    parser.add_argument(
        "--debug-domain", default="", help="Process only domains matching this string"
    )
    parser.add_argument(
        "--headful", type=bool, default=False, help="Run browser in headful mode"
    )
    parser.add_argument(
        "--stabilization",
        type=int,
        default=DEFAULT_STABILIZATION,
        help=f"Stabilization threshold (default: {DEFAULT_STABILIZATION})",
    )
    parser.add_argument(
        "--max-scroll-seconds",
        type=int,
        default=DEFAULT_MAX_SCROLL_SECONDS,
        help=f"Max seconds per directory mode (default: {DEFAULT_MAX_SCROLL_SECONDS})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    excel_path = args.excel_path
    if not excel_path:
        excel_path = input("Excel file path: ").strip().strip('"')
    if not excel_path:
        raise SystemExit(2)

    finder = AttorneyFinder(
        limit=args.limit,
        sheet_name=args.sheet,
        max_firms=args.max_firms,
        workers=args.workers,
        debug_firm=args.debug_firm,
        debug_domain=args.debug_domain,
        headful=args.headful,
        stabilization=args.stabilization,
        max_scroll_seconds=args.max_scroll_seconds,
    )
    start = time.time()
    total = finder.run(excel_path)
    elapsed = time.time() - start
    finder.log(f"\nTotal runtime: {elapsed:.1f}s")
    return 0 if total >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
