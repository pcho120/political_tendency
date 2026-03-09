#!/usr/bin/env python3
"""multi_mode_extractor.py - Multi-Mode Profile Extraction (REQUIREMENT B)

MODE 1: requests HTML (fast, most sites)
MODE 2: Playwright rendered DOM (JS-heavy sites)
MODE 3: API interception (JSON data sources)

Escalation flow:
1. Try Mode 1 (requests)
2. If PARTIAL or FAILED status → escalate to Mode 2
3. If still missing key fields → escalate to Mode 3
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from attorney_extractor import AttorneyExtractor, AttorneyProfile, EducationRecord
from profile_quality_gate import ReasonCode, FieldValidator, save_debug_artifacts


@dataclass
class ExtractionAttempt:
    """Single extraction attempt with mode tracking"""
    mode: str  # "MODE1_REQUESTS" | "MODE2_PLAYWRIGHT" | "MODE3_API"
    profile: AttorneyProfile
    html_size: int = 0
    json_payloads: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    
    def __post_init__(self):
        if self.json_payloads is None:
            self.json_payloads = []


class MultiModeExtractor:
    """Multi-mode extraction with automatic escalation"""
    
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = 10,
        debug_dir: Path | None = None,
        enable_playwright: bool = True
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.debug_dir = debug_dir or Path("debug_reports")
        self.enable_playwright = enable_playwright
        
        # Single extractor instance
        self.extractor = AttorneyExtractor()
        
        # Configure session
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def extract_profile(
        self,
        firm_name: str,
        profile_url: str,
        rate_limit_fn: Callable[[str], None] | None = None,
        force_playwright: bool = False
    ) -> AttorneyProfile:
        """Main entry point - tries all 3 modes with automatic escalation
        
        Args:
            firm_name: Firm name
            profile_url: Profile URL to extract
            rate_limit_fn: Optional rate limiting function
            
        Returns:
            AttorneyProfile with best possible extraction
        """
        attempts: list[ExtractionAttempt] = []
        
        # Mode 1: requests HTML (FAST)
        attempt1: ExtractionAttempt | None = None
        if not force_playwright:
            attempt1 = self._try_mode1_requests(firm_name, profile_url, rate_limit_fn)
            attempts.append(attempt1)
            
            # Check if we need to escalate
            if attempt1.profile.extraction_status == "SUCCESS":
                return attempt1.profile
        
        # Mode 2: Playwright rendered DOM (if enabled and extraction incomplete)
        if self.enable_playwright:
            if force_playwright or (attempt1 is not None and attempt1.profile.extraction_status in ["PARTIAL", "FAILED"]):
                attempt2 = self._try_mode2_playwright(firm_name, profile_url, rate_limit_fn)
                attempts.append(attempt2)
                
                # Merge results (Mode 2 takes precedence for non-null fields)
                if force_playwright:
                    merged = attempt2.profile
                else:
                    if attempt1 is None:
                        return attempt2.profile
                    if attempt1 is None:
                        return attempt2.profile
                    merged = self._merge_profiles(attempt1.profile, attempt2.profile, precedence="mode2")
                
                if merged.extraction_status == "SUCCESS":
                    return merged
                
                # Mode 3: API interception (if still missing key fields)
                if self._has_missing_key_fields(merged):
                    attempt3 = self._try_mode3_api_interception(firm_name, profile_url, rate_limit_fn)
                    attempts.append(attempt3)
                    
                    # Final merge (Mode 3 API data takes precedence)
                    final = self._merge_profiles(merged, attempt3.profile, precedence="mode3")
                    
                    # Save debug artifacts if still PARTIAL/FAILED
                    if final.extraction_status != "SUCCESS":
                        self._save_debug_artifacts(
                            profile_url,
                            firm_name,
                            attempt2.profile,  # Best HTML
                            attempt3.json_payloads,
                            final
                        )
                    
                    return final
                
                return merged
        
        # No escalation available or not enabled
        if attempts:
            return attempts[0].profile
        return AttorneyProfile(firm=firm_name, profile_url=profile_url)
    
    def extract_profile_with_page(
        self,
        firm_name: str,
        profile_url: str,
        page: Any,
        rate_limit_fn: Callable[[str], None] | None = None,
    ) -> AttorneyProfile:
        """Extract a profile reusing an existing Playwright page (no browser restart).

        The caller owns the page lifecycle — this method navigates the page,
        reads the HTML, and returns the parsed profile.  It does NOT close
        the page so the caller can reuse it for the next URL.
        """
        start = time.time()
        domain = urlparse(profile_url).netloc
        if rate_limit_fn:
            rate_limit_fn(domain)

        captured_json: list[dict] = []

        def _handle_json(response: Any) -> None:
            try:
                if response.ok and "json" in response.headers.get("content-type", "").lower():
                    try:
                        captured_json.append({"url": response.url, "data": response.json()})
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", _handle_json)

        try:
            try:
                page.goto(profile_url, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass  # may still have content

            try:
                page.wait_for_selector(
                    ".profile-heading, h1, [class*='name'], main, article",
                    timeout=15000,
                )
            except Exception:
                page.wait_for_timeout(3000)

            # Click accordions for hidden sections (education, bar admissions, etc.)
            accordion_labels = ["education", "admissions", "bar", "qualifications",
                                 "credentials", "professional background"]
            for label in accordion_labels:
                try:
                    for sel in [f"button:has-text('{label}')",
                                f"a:has-text('{label}')",
                                f"[role='tab']:has-text('{label}')"]:
                        try:
                            els = page.locator(sel).all()
                            for el in els:
                                if el.is_visible():
                                    el.click()
                                    page.wait_for_timeout(400)
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass

            html = page.content()
        except Exception as e:
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["shared_page_exception"] = str(type(e).__name__)
            # Remove the response listener before returning
            try:
                page.remove_listener("response", _handle_json)
            except Exception:
                pass
            return profile

        # Remove listener before HTML parse (keeps page clean for next URL)
        try:
            page.remove_listener("response", _handle_json)
        except Exception:
            pass

        # Try JSON API data first
        if captured_json:
            _jp = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            _jp = self._extract_from_json_payloads(_jp, captured_json)
            _jp.calculate_status()
            if _jp.extraction_status == "SUCCESS":
                _jp = self._validate_and_add_reasons(_jp, mode="MODE2_JSON")
                return _jp

        # Bot-protection check
        bot_indicators = ["cloudflare-challenge", "__cf_chl_", "cf-challenge-running",
                          "attention required", "checking your browser", "captcha"]
        html_lower = html.lower()
        if any(ind in html_lower for ind in bot_indicators):
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["bot_protection"] = True
            profile.missing_fields = self._all_field_names()
            return profile

        # Parse HTML
        profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
        profile = self.extractor.extract_profile(firm_name, profile_url, html)
        profile.diagnostics["html_size"] = len(html)
        profile.diagnostics["duration_ms"] = int((time.time() - start) * 1000)
        profile.diagnostics["data_source"] = "firm_website"
        profile = self._validate_and_add_reasons(profile, mode="MODE2_SHARED_PAGE")
        return profile

    def _try_mode1_requests(
        self,
        firm_name: str,
        profile_url: str,
        rate_limit_fn: Callable[[str], None] | None
    ) -> ExtractionAttempt:
        """Mode 1: Standard requests.get() + HTML parsing"""
        start = time.time()
        
        domain = urlparse(profile_url).netloc
        if rate_limit_fn:
            rate_limit_fn(domain)
        
        try:
            resp = self.session.get(profile_url, timeout=self.timeout)
            
            if resp.status_code != 200:
                # HTTP error
                profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                profile.extraction_status = "FAILED"
                profile.diagnostics[f"http_{resp.status_code}"] = True
                profile.missing_fields = self._all_field_names()
                
                # Add reason codes
                for field in profile.missing_fields:
                    if resp.status_code == 403:
                        profile.diagnostics[f"{field}_reason"] = ReasonCode.BLOCKED_403
                    elif resp.status_code == 429:
                        profile.diagnostics[f"{field}_reason"] = ReasonCode.BLOCKED_429
                    else:
                        profile.diagnostics[f"{field}_reason"] = ReasonCode.BLOCKED_OTHER
                
                return ExtractionAttempt(
                    mode="MODE1_REQUESTS",
                    profile=profile,
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            html = resp.text
            html_size = len(html)
            
            # CRITICAL: Check for bot protection pages (Cloudflare, CAPTCHA, etc.)
            # These pages return 200 but contain challenge/protection content
            bot_protection_indicators = [
                "attention required",
                "checking your browser",
                "captcha",
                "cloudflare-challenge",
                "__cf_chl_",
                "cf-challenge-running",
                "bot protection",
                "challenge-platform",
                "cf-browser-verification",
                "access denied",
                "why have i been blocked"
            ]
            
            html_lower = html.lower()
            if any(indicator in html_lower for indicator in bot_protection_indicators):
                # Bot protection detected - treat as blocked
                profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                profile.extraction_status = "FAILED"
                profile.diagnostics["blocked_403"] = True  # Treat as 403 even if 200
                profile.diagnostics["bot_protection"] = True
                profile.missing_fields = self._all_field_names()
                
                for field in profile.missing_fields:
                    profile.diagnostics[f"{field}_reason"] = ReasonCode.BLOCKED_403
                
                return ExtractionAttempt(
                    mode="MODE1_REQUESTS",
                    profile=profile,
                    html_size=html_size,
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            # Check for blocking/redirect
            if html_size < 2000:
                profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                profile.extraction_status = "FAILED"
                profile.diagnostics["small_content"] = True
                profile.missing_fields = self._all_field_names()
                
                for field in profile.missing_fields:
                    profile.diagnostics[f"{field}_reason"] = ReasonCode.SMALL_CONTENT
                
                return ExtractionAttempt(
                    mode="MODE1_REQUESTS",
                    profile=profile,
                    html_size=html_size,
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            # Extract using attorney_extractor
            profile = self.extractor.extract_profile(firm_name, profile_url, html)
            
            # Apply field validation and add reason codes
            profile = self._validate_and_add_reasons(profile, mode="MODE1")
            
            return ExtractionAttempt(
                mode="MODE1_REQUESTS",
                profile=profile,
                html_size=html_size,
                duration_ms=int((time.time() - start) * 1000)
            )
            
        except Exception as e:
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["exception"] = str(type(e).__name__)
            profile.missing_fields = self._all_field_names()
            
            for field in profile.missing_fields:
                profile.diagnostics[f"{field}_reason"] = ReasonCode.EXCEPTION
            
            return ExtractionAttempt(
                mode="MODE1_REQUESTS",
                profile=profile,
                duration_ms=int((time.time() - start) * 1000)
            )
    
    def _try_mode2_playwright(
        self,
        firm_name: str,
        profile_url: str,
        rate_limit_fn: Callable[[str], None] | None
    ) -> ExtractionAttempt:
        """Mode 2: Playwright with JS rendering + networkidle wait"""
        start = time.time()
        
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            # Playwright not available - return empty result
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["playwright_unavailable"] = True
            return ExtractionAttempt(mode="MODE2_PLAYWRIGHT", profile=profile)
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                
                # Fix 4: JSON intercept — capture API responses before DOM parsing
                captured_json_mode2: list[dict] = []

                def _handle_json_response_mode2(response):
                    try:
                        if response.ok and "json" in response.headers.get("content-type", "").lower():
                            try:
                                data = response.json()
                                captured_json_mode2.append({"url": response.url, "data": data})
                            except Exception:
                                pass
                    except Exception:
                        pass

                page.on("response", _handle_json_response_mode2)
                
                domain = urlparse(profile_url).netloc
                if rate_limit_fn:
                    rate_limit_fn(domain)

                # Navigate: use domcontentloaded for JS-SPA sites (networkidle can timeout)
                try:
                    page.goto(profile_url, timeout=30000, wait_until="domcontentloaded")
                except Exception:
                    # Already timed out but page may still have loaded content
                    pass

                # Wait for profile content to appear (Kirkland: .profile-heading; generic: h1)
                try:
                    page.wait_for_selector(
                        ".profile-heading, h1, [class*='name'], main, article",
                        timeout=15000
                    )
                except Exception:
                    page.wait_for_timeout(4000)  # last-resort wait
                
                # Auto-detect and click accordions/tabs for hidden fields
                # (Education, Bar Admissions, etc. often hidden behind accordions)
                accordion_labels = [
                    "education", "admissions", "bar", "qualifications",
                    "credentials", "professional background", "academic background"
                ]
                
                for label in accordion_labels:
                    try:
                        # Try multiple selector patterns for accordion buttons/tabs
                        selectors = [
                            f"button:has-text('{label}')",
                            f"a:has-text('{label}')",
                            f"[role='tab']:has-text('{label}')",
                            f".accordion:has-text('{label}')",
                            f"[data-accordion]:has-text('{label}')",
                            f"[aria-controls]:has-text('{label}')"
                        ]
                        
                        for selector in selectors:
                            try:
                                elements = page.locator(selector).all()
                                for element in elements:
                                    if element.is_visible():
                                        element.click()
                                        page.wait_for_timeout(500)  # Wait for accordion animation
                                        break
                            except:
                                continue  # Try next selector
                    except:
                        pass  # Silent fail - accordion might not exist
                
                # Get rendered HTML AFTER accordion interactions
                html = page.content()
                html_size = len(html)
                
                
                # Fix 4: If JSON API data was captured, try extracting from it first
                if captured_json_mode2:
                    _json_profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                    _json_profile = self._extract_from_json_payloads(_json_profile, captured_json_mode2)
                    _json_profile.calculate_status()
                    if _json_profile.extraction_status == "SUCCESS":
                        page.close()
                        context.close()
                        browser.close()
                        _json_profile = self._validate_and_add_reasons(_json_profile, mode="MODE2_JSON")
                        return ExtractionAttempt(
                            mode="MODE2_PLAYWRIGHT_JSON",
                            profile=_json_profile,
                            html_size=html_size,
                            json_payloads=captured_json_mode2,
                            duration_ms=int((time.time() - start) * 1000)
                        )
                page.close()
                context.close()
                browser.close()
                
                # Detect bot protection or blocked content
                bot_protection_indicators = [
                    "attention required",
                    "checking your browser",
                    "captcha",
                    "cloudflare-challenge",
                    "__cf_chl_",
                    "cf-challenge-running",
                    "bot protection",
                    "challenge-platform",
                    "cf-browser-verification",
                    "access denied",
                    "why have i been blocked"
                ]
                html_lower = html.lower()
                if any(indicator in html_lower for indicator in bot_protection_indicators):
                    profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                    profile.extraction_status = "FAILED"
                    profile.diagnostics["blocked_403"] = True
                    profile.diagnostics["bot_protection"] = True
                    profile.missing_fields = self._all_field_names()
                    for field in profile.missing_fields:
                        profile.diagnostics[f"{field}_reason"] = ReasonCode.BLOCKED_403
                    return ExtractionAttempt(
                        mode="MODE2_PLAYWRIGHT",
                        profile=profile,
                        html_size=html_size,
                        duration_ms=int((time.time() - start) * 1000)
                    )
                if html_size < 2000:
                    profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                    profile.extraction_status = "FAILED"
                    profile.diagnostics["small_content"] = True
                    profile.missing_fields = self._all_field_names()
                    for field in profile.missing_fields:
                        profile.diagnostics[f"{field}_reason"] = ReasonCode.SMALL_CONTENT
                    return ExtractionAttempt(
                        mode="MODE2_PLAYWRIGHT",
                        profile=profile,
                        html_size=html_size,
                        duration_ms=int((time.time() - start) * 1000)
                    )

                # Extract using attorney_extractor on rendered HTML
                profile = self.extractor.extract_profile(firm_name, profile_url, html)
                
                # Apply validation
                profile = self._validate_and_add_reasons(profile, mode="MODE2")
                
                return ExtractionAttempt(
                    mode="MODE2_PLAYWRIGHT",
                    profile=profile,
                    html_size=html_size,
                    duration_ms=int((time.time() - start) * 1000)
                )
                
        except Exception as e:
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["mode2_exception"] = str(type(e).__name__)
            return ExtractionAttempt(
                mode="MODE2_PLAYWRIGHT",
                profile=profile,
                duration_ms=int((time.time() - start) * 1000)
            )
    
    def _try_mode3_api_interception(
        self,
        firm_name: str,
        profile_url: str,
        rate_limit_fn: Callable[[str], None] | None
    ) -> ExtractionAttempt:
        """Mode 3: Playwright with API/XHR interception"""
        start = time.time()
        
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["playwright_unavailable"] = True
            return ExtractionAttempt(mode="MODE3_API", profile=profile)
        
        captured_json = []
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                # Intercept API responses
                def handle_response(response):
                    try:
                        url_lower = response.url.lower()
                        
                        # Look for API/JSON responses
                        if response.ok and "json" in response.headers.get("content-type", "").lower():
                            try:
                                data = response.json()
                                captured_json.append({
                                    "url": response.url,
                                    "data": data,
                                    "size": len(str(data))
                                })
                            except:
                                pass
                    except:
                        pass
                
                page.on("response", handle_response)
                
                domain = urlparse(profile_url).netloc
                if rate_limit_fn:
                    rate_limit_fn(domain)
                
                # Navigate
                page.goto(profile_url, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(2000)  # Extra wait for late API calls
                
                page.close()
                context.close()
                browser.close()
                
                # Extract fields from captured JSON
                profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
                profile = self._extract_from_json_payloads(profile, captured_json)
                
                # Apply validation
                profile = self._validate_and_add_reasons(profile, mode="MODE3")
                
                return ExtractionAttempt(
                    mode="MODE3_API",
                    profile=profile,
                    json_payloads=captured_json,
                    duration_ms=int((time.time() - start) * 1000)
                )
                
        except Exception as e:
            profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)
            profile.extraction_status = "FAILED"
            profile.diagnostics["mode3_exception"] = str(type(e).__name__)
            return ExtractionAttempt(
                mode="MODE3_API",
                profile=profile,
                json_payloads=captured_json,
                duration_ms=int((time.time() - start) * 1000)
            )
    
    def _extract_from_json_payloads(
        self,
        profile: AttorneyProfile,
        payloads: list[dict]
    ) -> AttorneyProfile:
        """Extract attorney fields from captured JSON payloads"""
        for payload in payloads:
            data = payload.get("data", {})
            
            # Try to find attorney data in JSON
            attorney_data = self._find_attorney_in_json(data)
            
            if attorney_data:
                # Extract name
                if not profile.full_name:
                    name = attorney_data.get("name") or attorney_data.get("fullName") or attorney_data.get("displayName")
                    if name:
                        profile.full_name = str(name)
                
                # Extract title
                if not profile.title:
                    title = attorney_data.get("title") or attorney_data.get("position") or attorney_data.get("jobTitle")
                    if title:
                        profile.title = str(title)
                
                # Extract offices
                office = attorney_data.get("office") or attorney_data.get("location") or attorney_data.get("officeLocation")
                if office and not profile.offices:
                    if isinstance(office, list):
                        profile.offices = [str(o) for o in office]
                    else:
                        profile.offices = [str(office)]
                
                # Extract practice areas
                practices = attorney_data.get("practiceAreas") or attorney_data.get("practices") or attorney_data.get("expertise")
                if practices and not profile.practice_areas:
                    if isinstance(practices, list):
                        profile.practice_areas = [str(p) for p in practices]
                    elif isinstance(practices, str):
                        profile.practice_areas = [practices]
                
                # Extract industries
                industries = attorney_data.get("industries") or attorney_data.get("sectors")
                if industries and not profile.industries:
                    if isinstance(industries, list):
                        profile.industries = [str(i) for i in industries]
                    elif isinstance(industries, str):
                        profile.industries = [industries]
                
                # Extract bar admissions
                bars = attorney_data.get("barAdmissions") or attorney_data.get("admissions") or attorney_data.get("licenses")
                if bars and not profile.bar_admissions:
                    if isinstance(bars, list):
                        profile.bar_admissions = [str(b) for b in bars]
                    elif isinstance(bars, str):
                        profile.bar_admissions = [bars]
                
                # Extract education
                education = attorney_data.get("education") or attorney_data.get("schools")
                if education and not profile.education:
                    if isinstance(education, list):
                        for edu in education:
                            if isinstance(edu, dict):
                                year_val = edu.get("year")
                                year_num = year_val if isinstance(year_val, int) else None
                                if year_num is None and year_val is not None:
                                    year_text = str(year_val)
                                    if year_text.isdigit():
                                        year_num = int(year_text)
                                record = EducationRecord(
                                    degree=str(edu.get("degree")) if edu.get("degree") else None,
                                    school=str(edu.get("school")) if edu.get("school") else None,
                                    year=year_num
                                )
                                profile.education.append(record)
        
        return profile
    
    def _find_attorney_in_json(self, data: Any, depth: int = 0, max_depth: int = 5) -> dict | None:
        """Recursively find attorney data in JSON"""
        if depth > max_depth:
            return None
        
        if isinstance(data, dict):
            # Check if this dict looks like attorney data
            keys = set(data.keys())
            attorney_indicators = {"name", "fullName", "title", "position", "practiceAreas", "barAdmissions", "education"}
            
            if len(keys & attorney_indicators) >= 2:
                return data
            
            # Recurse
            for value in data.values():
                result = self._find_attorney_in_json(value, depth + 1, max_depth)
                if result:
                    return result
        
        elif isinstance(data, list):
            for item in data:
                result = self._find_attorney_in_json(item, depth + 1, max_depth)
                if result:
                    return result
        
        return None
    
    def _filter_us_offices_only(self, offices: list[str]) -> list[str]:
        """Filter offices to keep only US locations
        
        Accepts offices in formats:
        - "City, ST" where ST is a valid US state code
        - "Washington, DC" or "Washington DC"
        
        Returns empty list if no US offices found (caller will mark as us_office_not_found)
        """
        US_STATE_CODES = {
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
            'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
            'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
            'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
            'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
        }
        # Known US cities (without state code) used by law firms
        US_CITIES = {
            'new york', 'chicago', 'los angeles', 'houston', 'dallas', 'san francisco',
            'washington', 'boston', 'miami', 'seattle', 'austin', 'denver', 'atlanta',
            'philadelphia', 'silicon valley', 'palo alto', 'menlo park', 'century city',
            'san diego', 'minneapolis', 'charlotte', 'detroit', 'pittsburgh', 'baltimore',
            'nashville', 'phoenix', 'salt lake city', 'portland', 'richmond', 'raleigh',
            'wilmington', 'hartford', 'new orleans', 'cincinnati', 'indianapolis',
            'kansas city', 'st. louis', 'columbus', 'cleveland', 'rochester', 'buffalo',
            'sacramento', 'san jose', 'las vegas', 'orlando', 'tampa', 'jacksonville',
            'fort worth', 'oklahoma city', 'tucson', 'albuquerque', 'memphis', 'louisville',
        }

        us_offices: list[str] = []
        for office in offices:
            office_clean = office.strip()

            # Pattern: "City, ST" where ST is US state code
            if ', ' in office_clean:
                parts = office_clean.split(', ')
                if len(parts) >= 2:
                    # Normalize state code: remove dots (D.C. -> DC)
                    state_code = parts[-1].strip().upper().replace('.', '')
                    if state_code in US_STATE_CODES:
                        us_offices.append(office_clean)
            # Pattern: bare "Washington DC" / "Washington D.C." / "Washington, D.C." variants
            elif office_clean.lower().replace('.', '').replace(',', '').strip() == 'washington dc':
                us_offices.append('Washington, DC')
            # Pattern: bare US city name (e.g., "Chicago", "New York")
            elif office_clean.lower() in US_CITIES:
                us_offices.append(office_clean)
        return us_offices
    
    def _validate_and_add_reasons(self, profile: AttorneyProfile, mode: str) -> AttorneyProfile:
        """Apply field validation and add reason codes for missing fields"""
        
        # Validate name
        if profile.full_name:
            validated, reason = FieldValidator.validate_name(profile.full_name)
            if validated:
                profile.full_name = validated
            else:
                profile.full_name = None
                profile.diagnostics["full_name_reason"] = reason
        else:
            if mode == "MODE1":
                profile.diagnostics["full_name_reason"] = ReasonCode.NOT_FOUND_RAW_HTML
            elif mode == "MODE2":
                profile.diagnostics["full_name_reason"] = ReasonCode.NOT_FOUND_AFTER_JS
            elif mode == "MODE3":
                profile.diagnostics["full_name_reason"] = ReasonCode.API_NOT_DETECTED
        
        # Validate title
        if profile.title:
            validated, reason = FieldValidator.validate_title(profile.title)
            if validated:
                profile.title = validated
            else:
                profile.title = None
                profile.diagnostics["title_reason"] = reason
        else:
            if mode == "MODE1":
                profile.diagnostics["title_reason"] = ReasonCode.NOT_FOUND_RAW_HTML
            elif mode == "MODE2":
                profile.diagnostics["title_reason"] = ReasonCode.NOT_FOUND_AFTER_JS
            elif mode == "MODE3":
                profile.diagnostics["title_reason"] = ReasonCode.API_NOT_DETECTED
        
        # Filter US offices only (BEFORE validation)
        if profile.offices:
            profile.offices = self._filter_us_offices_only(profile.offices)
            if not profile.offices:
                # Had offices but all non-US
                profile.diagnostics["us_office_not_found"] = True
                if "offices" not in profile.missing_fields:
                    profile.missing_fields.append("offices")
        
        # Validate offices
        validated, reason = FieldValidator.validate_offices(profile.offices)
        if validated:
            profile.offices = validated
        elif reason:
            profile.diagnostics["offices_reason"] = reason
        
        # Validate departments
        validated, reason = FieldValidator.validate_department(profile.department)
        if validated:
            profile.department = validated
        elif reason:
            profile.diagnostics["department_reason"] = reason
        
        # Validate practice areas
        validated, reason = FieldValidator.validate_practice_areas(profile.practice_areas)
        if validated:
            profile.practice_areas = validated
        elif reason:
            profile.diagnostics["practice_areas_reason"] = reason
        
        # Validate bar admissions
        validated, reason = FieldValidator.validate_bar_admissions(profile.bar_admissions)
        if validated:
            profile.bar_admissions = validated
        elif reason:
            profile.diagnostics["bar_admissions_reason"] = reason
        
        # Validate education
        validated, reason = FieldValidator.validate_education([e.to_dict() for e in profile.education])
        if validated:
            profile.education = [
                EducationRecord(
                    degree=e.get("degree"),
                    school=e.get("school"),
                    year=e.get("year")
                )
                for e in validated
            ]
        elif reason:
            profile.diagnostics["education_reason"] = reason
        
        # Recalculate status
        profile.calculate_status()
        
        return profile
    
    def _merge_profiles(
        self,
        profile1: AttorneyProfile,
        profile2: AttorneyProfile,
        precedence: str = "mode2"
    ) -> AttorneyProfile:
        """Merge two profiles, with precedence for non-null fields"""
        merged = AttorneyProfile(firm=profile1.firm, profile_url=profile1.profile_url)
        
        # Determine which takes precedence
        primary = profile2 if precedence in ["mode2", "mode3"] else profile1
        fallback = profile1 if precedence in ["mode2", "mode3"] else profile2
        
        # Merge fields (primary takes precedence for non-null)
        merged.full_name = primary.full_name or fallback.full_name
        merged.title = primary.title or fallback.title
        merged.offices = primary.offices if primary.offices else fallback.offices
        merged.department = primary.department if primary.department else fallback.department
        merged.practice_areas = primary.practice_areas if primary.practice_areas else fallback.practice_areas
        merged.industries = primary.industries if primary.industries else fallback.industries
        merged.bar_admissions = primary.bar_admissions if primary.bar_admissions else fallback.bar_admissions
        merged.education = primary.education if primary.education else fallback.education
        
        # Merge diagnostics
        merged.diagnostics = {**fallback.diagnostics, **primary.diagnostics}
        
        merged.calculate_status()
        
        return merged
    
    def _has_missing_key_fields(self, profile: AttorneyProfile) -> bool:
        """Check if profile is missing key fields (title, practice, bar, education)"""
        key_fields_present = [
            bool(profile.title),
            bool(profile.practice_areas),
            bool(profile.bar_admissions),
            bool(profile.education)
        ]
        
        return sum(key_fields_present) < 2  # Missing 3+ key fields
    
    def _all_field_names(self) -> list[str]:
        """Get list of all required field names"""
        return [
            "full_name",
            "title",
            "offices",
            "department",
            "practice_areas",
            "industries",
            "bar_admissions",
            "education"
        ]
    
    def _save_debug_artifacts(
        self,
        profile_url: str,
        firm_name: str,
        best_profile: AttorneyProfile,
        json_payloads: list[dict],
        final_profile: AttorneyProfile
    ) -> None:
        """Save debug artifacts wrapper"""
        save_debug_artifacts(
            profile_url=profile_url,
            firm_name=firm_name,
            html_content=None,  # Not saved in this version
            json_payloads=json_payloads,
            extraction_result={
                "profile_url": profile_url,
                "firm": firm_name,
                "extraction_status": final_profile.extraction_status,
                "missing_fields": final_profile.missing_fields,
                "diagnostics": final_profile.diagnostics,
                "fields": {
                    "full_name": final_profile.full_name,
                    "title": final_profile.title,
                    "offices": final_profile.offices,
                    "department": final_profile.department,
                    "practice_areas": final_profile.practice_areas,
                    "industries": final_profile.industries,
                    "bar_admissions": final_profile.bar_admissions,
                    "education": [e.to_dict() for e in final_profile.education]
                }
            },
            debug_dir=self.debug_dir
        )
