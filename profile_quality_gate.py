#!/usr/bin/env python3
"""profile_quality_gate.py - URL Quality Gate & Multi-Mode Extraction

REQUIREMENT A: URL QUALITY GATE
- Sample N URLs from candidates
- Compute profile-likelihood scores
- Infer dominant URL pattern automatically
- Build site-specific regex (NO hardcoding)
- Filter full set with inferred pattern

REQUIREMENT B: MULTI-MODE PROFILE FETCH
- Mode 1: requests HTML (fast)
- Mode 2: Playwright rendered DOM (JS-heavy sites)
- Mode 3: API interception (JSON data sources)

REQUIREMENT C: FIELD VALIDATION & CLEANUP
- Validators per field type
- Reason codes for missing fields
- Contact info contamination removal

REQUIREMENT D: DEBUG ARTIFACTS
- HTML snapshots on failure
- Intercepted JSON payloads
- Field evidence reports
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urljoin

import requests

# Field-specific reason codes
class ReasonCode:
    NOT_FOUND_RAW_HTML = "not_found_in_raw_html"
    NOT_FOUND_AFTER_JS = "not_found_after_js"
    API_NOT_DETECTED = "api_not_detected"
    BLOCKED_403 = "blocked_403"
    BLOCKED_429 = "blocked_429"
    BLOCKED_OTHER = "blocked_other"
    PARSE_FAILED = "parse_failed"
    VALIDATION_REJECTED = "validation_rejected"
    SMALL_CONTENT = "small_content_likely_blocked"
    EXCEPTION = "exception"


@dataclass
class ProfileLikelihoodScore:
    """Profile likelihood scoring for URL quality gate"""
    url: str
    score: float = 0.0
    has_person_name: bool = False
    has_bar_keywords: bool = False
    has_education_keywords: bool = False
    has_practice_keywords: bool = False
    is_list_page: bool = False
    is_search_page: bool = False
    html_size: int = 0
    
    def compute_final_score(self) -> float:
        """Compute final likelihood score (0-100)"""
        score = 0.0
        
        # Positive signals
        if self.has_person_name:
            score += 40.0
        if self.has_bar_keywords:
            score += 20.0
        if self.has_education_keywords:
            score += 15.0
        if self.has_practice_keywords:
            score += 15.0
        if self.html_size > 5000:  # Substantial content
            score += 10.0
        
        # Negative signals
        if self.is_list_page:
            score -= 50.0
        if self.is_search_page:
            score -= 50.0
        if self.html_size < 2000:  # Too small
            score -= 30.0
        
        self.score = max(0.0, min(100.0, score))
        return self.score


@dataclass
class URLPatternInference:
    """Inferred URL pattern from profile samples"""
    pattern_regex: str = ""
    common_path_prefix: str = ""
    slug_pattern: str = ""
    confidence: float = 0.0
    sample_urls: list[str] = field(default_factory=list)
    rejected_patterns: list[str] = field(default_factory=list)


@dataclass
class QualityGateResult:
    """Result of URL quality gate filtering"""
    total_candidates: int = 0
    sampled_count: int = 0
    positive_samples: int = 0
    filtered_profile_urls: set[str] = field(default_factory=set)
    rejected_urls: set[str] = field(default_factory=set)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    inferred_pattern: URLPatternInference = field(default_factory=URLPatternInference)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "total_candidates": self.total_candidates,
            "sampled_count": self.sampled_count,
            "positive_samples": self.positive_samples,
            "filtered_count": len(self.filtered_profile_urls),
            "rejected_count": len(self.rejected_urls),
            "rejection_reasons": self.rejection_reasons,
            "inferred_pattern": {
                "regex": self.inferred_pattern.pattern_regex,
                "path_prefix": self.inferred_pattern.common_path_prefix,
                "slug_pattern": self.inferred_pattern.slug_pattern,
                "confidence": self.inferred_pattern.confidence,
                "sample_urls": self.inferred_pattern.sample_urls[:5]
            },
            "diagnostics": self.diagnostics
        }


class URLQualityGate:
    """Intelligent URL filtering with auto-pattern detection"""
    
    def __init__(self, session: requests.Session | None = None, timeout: int = 5):
        self.session = session or requests.Session()
        self.timeout = timeout
        
        # Configure session with faster timeout
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def filter_candidates(
        self, 
        candidate_urls: set[str], 
        base_url: str,
        sample_size: int = 200,
        min_confidence: float = 60.0
    ) -> QualityGateResult:
        """Main URL quality gate - filter candidates with auto-pattern detection
        
        Args:
            candidate_urls: Raw candidate URLs from discovery
            base_url: Base domain URL
            sample_size: Number of URLs to sample for inference
            min_confidence: Minimum confidence threshold for profile likelihood
            
        Returns:
            QualityGateResult with filtered URLs and diagnostics
        """
        result = QualityGateResult()
        result.total_candidates = len(candidate_urls)
        
        if not candidate_urls:
            return result
        
        # Step 1: Sample URLs (random or evenly spaced)
        sample_urls = self._sample_urls(candidate_urls, sample_size)
        result.sampled_count = len(sample_urls)
        
        # Step 2: Score each sample
        scored_samples = []
        for url in sample_urls:
            score_obj = self._score_url_profile_likelihood(url)
            scored_samples.append(score_obj)
        
        # Step 3: Identify positive samples (high likelihood)
        positive_samples = [s for s in scored_samples if s.score >= min_confidence]
        result.positive_samples = len(positive_samples)
        
        if len(positive_samples) < 5:
            # Too few positive samples - fall back to basic filtering
            result.diagnostics["warning"] = f"Only {len(positive_samples)} positive samples, using basic filtering"
            result.filtered_profile_urls = candidate_urls
            return result
        
        # Step 4: Infer URL pattern from positive samples
        pattern = self._infer_url_pattern([s.url for s in positive_samples])
        result.inferred_pattern = pattern
        
        if pattern.confidence < 0.5:
            # Low confidence pattern - fall back to basic filtering
            result.diagnostics["warning"] = f"Low pattern confidence ({pattern.confidence:.2f}), using basic filtering"
            result.filtered_profile_urls = candidate_urls
            return result
        
        # Step 5: Filter full candidate set using inferred pattern
        for url in candidate_urls:
            if self._matches_pattern(url, pattern):
                result.filtered_profile_urls.add(url)
            else:
                result.rejected_urls.add(url)
                # Classify rejection reason
                reason = self._classify_rejection_reason(url, pattern)
                result.rejection_reasons[reason] = result.rejection_reasons.get(reason, 0) + 1
        
        result.diagnostics["pattern_applied"] = True
        result.diagnostics["avg_positive_score"] = sum(s.score for s in positive_samples) / len(positive_samples)
        
        return result
    
    def _sample_urls(self, urls: set[str], sample_size: int) -> list[str]:
        """Sample N URLs evenly or randomly"""
        url_list = sorted(list(urls))  # Sort for reproducibility
        
        if len(url_list) <= sample_size:
            return url_list
        
        # Even spacing sampling
        step = len(url_list) / sample_size
        sampled = [url_list[int(i * step)] for i in range(sample_size)]
        return sampled
    
    def _score_url_profile_likelihood(self, url: str) -> ProfileLikelihoodScore:
        """Score a single URL's profile likelihood
        
        Fetches URL and checks for:
        - Person name (H1, JSON-LD)
        - Bar admission keywords
        - Education keywords
        - Practice area keywords
        - List page indicators
        
        Retries once on timeout/connection error
        """
        score = ProfileLikelihoodScore(url=url)
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                
                if resp.status_code != 200:
                    if attempt < max_retries - 1:
                        continue  # Retry on non-200
                    return score
                
                html = resp.text.lower()
                score.html_size = len(html)
                
                # Check for person name signals
                if re.search(r'<h1[^>]*>([^<]+)</h1>', html):
                    h1_text = re.search(r'<h1[^>]*>([^<]+)</h1>', html).group(1)
                    if self._looks_like_person_name(h1_text):
                        score.has_person_name = True
                
                # Check JSON-LD Person schema
                if '"@type"' in html and '"person"' in html:
                    score.has_person_name = True
                
                # Check for bar admission keywords
                bar_keywords = ['bar admission', 'admitted to', 'licensed', 'bar association']
                if any(kw in html for kw in bar_keywords):
                    score.has_bar_keywords = True
                
                # Check for education keywords
                edu_keywords = ['education', 'j.d.', 'jd', 'll.m', 'law school', 'university']
                if any(kw in html for kw in edu_keywords):
                    score.has_education_keywords = True
                
                # Check for practice keywords (expanded for multilingual support)
                practice_keywords = ['practice area', 'expertise', 'specialization', 'focus', 'service', 'competenc']
                if any(kw in html for kw in practice_keywords):
                    score.has_practice_keywords = True
                
                # Check for list page indicators (NEGATIVE)
                list_indicators = ['showing', 'results', 'page 1', 'next page', 'load more']
                if any(ind in html for ind in list_indicators):
                    score.is_list_page = True
                
                # Check for search page indicators (NEGATIVE)
                if 'search' in url.lower() or '?q=' in url.lower():
                    score.is_search_page = True
                
                score.compute_final_score()
                break  # Success - exit retry loop
                
            except (requests.Timeout, requests.ConnectionError):
                if attempt < max_retries - 1:
                    continue  # Retry
                score.score = 0.0
            except Exception:
                score.score = 0.0
                break
        
        return score
    
    def _looks_like_person_name(self, text: str) -> bool:
        """Check if text looks like a person name"""
        text = text.strip()
        if len(text) < 4 or len(text) > 100:
            return False
        
        # Must have at least 2 capitalized words
        words = text.split()
        capitalized = [w for w in words if w and w[0].isupper()]
        return len(capitalized) >= 2
    
    def _infer_url_pattern(self, positive_urls: list[str]) -> URLPatternInference:
        """Infer URL pattern from positive samples
        
        Finds:
        - Common path prefix
        - Slug structure pattern
        - Builds regex
        """
        pattern = URLPatternInference()
        pattern.sample_urls = positive_urls[:10]
        
        if not positive_urls:
            return pattern
        
        # Parse all URLs
        parsed_urls = [urlparse(url) for url in positive_urls]
        
        # Find common path prefix
        paths = [p.path for p in parsed_urls]
        common_prefix = self._find_common_path_prefix(paths)
        pattern.common_path_prefix = common_prefix
        
        # Analyze slug patterns after common prefix
        slugs = []
        for path in paths:
            if path.startswith(common_prefix):
                slug = path[len(common_prefix):].strip('/')
                if slug:
                    slugs.append(slug)
        
        # Infer slug pattern (e.g., "firstname-lastname" or single slug)
        pattern.slug_pattern = self._infer_slug_pattern(slugs)
        
        # Build regex
        if common_prefix and pattern.slug_pattern:
            # Escape regex special chars in prefix
            escaped_prefix = re.escape(common_prefix)
            pattern.pattern_regex = f"{escaped_prefix}{pattern.slug_pattern}"
            pattern.confidence = 0.9
        elif common_prefix:
            escaped_prefix = re.escape(common_prefix)
            pattern.pattern_regex = f"{escaped_prefix}[^/]+/?$"
            pattern.confidence = 0.7
        else:
            pattern.confidence = 0.3
        
        return pattern
    
    def _find_common_path_prefix(self, paths: list[str]) -> str:
        """Find longest common path prefix"""
        if not paths:
            return ""
        
        # Split paths into segments
        path_segments = [p.strip('/').split('/') for p in paths]
        
        # Find common prefix segments
        if not path_segments:
            return ""
        
        common = []
        for i in range(len(path_segments[0])):
            segment = path_segments[0][i]
            if all(i < len(p) and p[i] == segment for p in path_segments):
                common.append(segment)
            else:
                break
        
        return '/' + '/'.join(common) if common else ""
    
    def _infer_slug_pattern(self, slugs: list[str]) -> str:
        """Infer slug structure pattern
        
        Examples:
        - "john-smith" -> /[a-z]+-[a-z]+
        - "jsmith" -> /[a-z]+
        - "john-smith-123" -> /[a-z]+-[a-z]+-\\d+
        """
        if not slugs:
            return "/[^/]+/?$"
        
        # Analyze slug structures
        has_hyphen = sum(1 for s in slugs if '-' in s) / len(slugs) > 0.5
        has_digit = sum(1 for s in slugs if any(c.isdigit() for c in s)) / len(slugs) > 0.3
        
        if has_hyphen and has_digit:
            return r"/[a-z0-9-]+/?$"
        elif has_hyphen:
            return r"/[a-z-]+/?$"
        elif has_digit:
            return r"/[a-z0-9]+/?$"
        else:
            return r"/[a-z]+/?$"
    
    def _matches_pattern(self, url: str, pattern: URLPatternInference) -> bool:
        """Check if URL matches inferred pattern"""
        if not pattern.pattern_regex:
            return True  # No pattern = accept all
        
        parsed = urlparse(url)
        path = parsed.path
        
        try:
            return bool(re.search(pattern.pattern_regex, path))
        except re.error:
            return True  # Regex error = accept
    
    def _classify_rejection_reason(self, url: str, pattern: URLPatternInference) -> str:
        """Classify why a URL was rejected"""
        parsed = urlparse(url)
        path = parsed.path
        
        if not path.startswith(pattern.common_path_prefix):
            return "wrong_path_prefix"
        
        if '/search' in path or '?q=' in url:
            return "search_page"
        
        if path.count('/') < 2:
            return "too_shallow"
        
        if path.count('/') > 6:
            return "too_deep"
        
        return "pattern_mismatch"


# Field Validators (REQUIREMENT C)

class FieldValidator:
    """Field-specific validation and cleanup"""
    
    @staticmethod
    def validate_name(name: str | None) -> tuple[str | None, str | None]:
        """Validate full_name field
        
        Returns: (cleaned_name or None, reason_code or None)
        """
        if not name:
            return None, ReasonCode.NOT_FOUND_RAW_HTML
        
        name = name.strip()
        
        # Must be reasonable length
        if len(name) < 2 or len(name) > 150:
            return None, ReasonCode.VALIDATION_REJECTED
        
        # Must look like person name (allow non-ASCII like Karabeleš)
        if not re.search(r'[A-Za-zÀ-ÿ]', name):
            return None, ReasonCode.VALIDATION_REJECTED
        
        # Must have at least 2 parts or initials
        parts = name.split()
        if len(parts) < 2 and not re.search(r'[A-Z]\.', name):
            return None, ReasonCode.VALIDATION_REJECTED
        
        return name, None
    
    @staticmethod
    def validate_title(title: str | None) -> tuple[str | None, str | None]:
        """Validate title field
        
        Must be <= 120 chars and not contain email/phone
        """
        if not title:
            return None, ReasonCode.NOT_FOUND_RAW_HTML
        
        title = title.strip()
        
        # Check for contamination
        if re.search(r'@|email|phone|\d{3}[.-]\d{3}', title.lower()):
            return None, ReasonCode.VALIDATION_REJECTED
        
        if len(title) > 120:
            return None, ReasonCode.VALIDATION_REJECTED
        
        return title, None
    
    @staticmethod
    def validate_offices(offices: list[str]) -> tuple[list[str], str | None]:
        """Validate and normalize office locations
        
        Normalize to ["City"] or ["City, ST"] format
        Remove junk tokens like "(Work)", "Location", "Lokation"
        """
        if not offices:
            return [], ReasonCode.NOT_FOUND_RAW_HTML
        
        cleaned = []
        junk_patterns = [r'\(work\)', r'\(office\)', r'^\s*location\s*$', r'^\s*lokation\s*$', r'^\s*$']
        
        for office in offices:
            office = office.strip()
            
            # Skip generic labels
            if office.lower() in ['location', 'lokation', 'office', 'offices', 'work']:
                continue
            
            # Remove junk patterns
            for pattern in junk_patterns:
                office = re.sub(pattern, '', office, flags=re.IGNORECASE).strip()
            
            # Clean up patterns like "LokationKøbenhavn" → "København"
            office = re.sub(r'^location', '', office, flags=re.IGNORECASE)
            office = re.sub(r'^lokation', '', office, flags=re.IGNORECASE)
            
            # Clean up comma-separated junk like "(Work,Praha)" → "Praha"
            # Remove leading/trailing commas and parentheses
            office = office.strip('(),').strip()
            
            # Split on common delimiters and take the last meaningful part
            # E.g., "Work,Praha" → ["Work", "Praha"] → "Praha"
            if ',' in office:
                parts = [p.strip() for p in office.split(',')]
                # Take the last part that's not junk
                for part in reversed(parts):
                    if part.lower() not in ['work', 'office', 'location']:
                        office = part
                        break
            
            if office and len(office) > 2:
                cleaned.append(office)
        
        if not cleaned:
            return [], ReasonCode.VALIDATION_REJECTED
        
        return list(set(cleaned)), None  # Deduplicate
    
    @staticmethod
    def validate_department(departments: list[str]) -> tuple[list[str], str | None]:
        """Validate department/group field
        
        Must NOT contain email/phone/URLs/cookie notices/language selectors
        Must be short label list
        """
        if not departments:
            return [], ReasonCode.NOT_FOUND_RAW_HTML
        
        cleaned = []
        for dept in departments:
            dept = dept.strip()
            
            # Check for contamination patterns
            contamination_patterns = [
                r'@',  # Email
                r'http|www\.',  # URLs
                r'tel:|phone',  # Phone labels
                r'\d{3}[.-]\d{3}',  # Phone numbers (US format)
                r'call\s*\+?\d',  # "Call +420..." pattern
                r'phone\s*:?\s*\+?\d',  # "Phone: +1..." pattern
                r'cookie|consent',  # Cookie notices
                r'always\s*active',  # Cookie toggles
                r'english\w{4,}',  # Language selectors like "EnglishNorsk"
                r'^\w+\s*\|\s*\w+$',  # "English | Dansk" format
            ]
            
            if any(re.search(pattern, dept, re.I) for pattern in contamination_patterns):
                continue  # Skip contaminated
            
            # Must be reasonable length
            if len(dept) < 3 or len(dept) > 150:
                continue
            
            # Skip if looks like a person name (contamination from bio)
            if re.match(r'^about\s+\w+\s+\w+', dept, re.I):
                continue
            
            cleaned.append(dept)
        
        if not cleaned:
            return [], ReasonCode.VALIDATION_REJECTED
        
        return list(set(cleaned)), None
    
    @staticmethod
    def validate_practice_areas(practices: list[str]) -> tuple[list[str], str | None]:
        """Validate practice areas list
        
        Remove navigation/CTA words, cookie notices, and UI junk
        """
        if not practices:
            return [], ReasonCode.NOT_FOUND_RAW_HTML
        
        cleaned = []
        
        # Junk words/phrases to filter
        junk_patterns = [
            'view all', 'read more', 'learn more', 'see more', 'click here',
            'cookie', 'consent', 'privacy policy', 'checkbox', 'here.', 
            'always active', 'settings', 'manage', 'accept', 'allow'
        ]
        
        for practice in practices:
            practice = practice.strip()
            
            # Skip junk
            if any(junk in practice.lower() for junk in junk_patterns):
                continue
            
            # Skip URL/link-like text
            if 'http' in practice.lower() or '@' in practice:
                continue
            
            # Must be reasonable length
            if len(practice) < 3 or len(practice) > 100:
                continue
            
            cleaned.append(practice)
        
        if not cleaned:
            return [], ReasonCode.VALIDATION_REJECTED
        
        return list(set(cleaned)), None
    
    @staticmethod
    def validate_bar_admissions(bars: list[str]) -> tuple[list[str], str | None]:
        """Validate bar admissions list
        
        Parse state names/abbreviations
        """
        if not bars:
            return [], ReasonCode.NOT_FOUND_RAW_HTML
        
        cleaned = list(set(bars))  # Deduplicate
        
        if not cleaned:
            return [], ReasonCode.VALIDATION_REJECTED
        
        return cleaned, None
    
    @staticmethod
    def validate_education(education: list[dict]) -> tuple[list[dict], str | None]:
        """Validate education records
        
        Allow missing year but not missing school when education section exists
        """
        if not education:
            return [], ReasonCode.NOT_FOUND_RAW_HTML
        
        valid = []
        for edu in education:
            # Must have school if education record exists
            if not edu.get('school'):
                continue
            
            # Degree is optional but preferred
            # Year is optional
            
            valid.append(edu)
        
        if not valid:
            return [], ReasonCode.VALIDATION_REJECTED
        
        return valid, None


def save_debug_artifacts(
    profile_url: str,
    firm_name: str,
    html_content: str | None,
    json_payloads: list[dict],
    extraction_result: dict,
    debug_dir: Path
) -> None:
    """Save debug artifacts on extraction failure (REQUIREMENT D)
    
    Saves:
    - Rendered HTML snapshot
    - Intercepted JSON payloads (first 5 largest)
    - Field evidence report
    """
    safe_firm = re.sub(r'[^\w\s-]', '', firm_name).strip().replace(' ', '_')
    safe_url = re.sub(r'[^\w]', '_', urlparse(profile_url).path)[:50]
    
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    # Save HTML snapshot
    if html_content:
        html_path = debug_dir / f"{safe_firm}_{safe_url}_snapshot.html"
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    
    # Save JSON payloads (first 5 largest)
    if json_payloads:
        sorted_payloads = sorted(json_payloads, key=lambda x: len(str(x)), reverse=True)[:5]
        json_path = debug_dir / f"{safe_firm}_{safe_url}_api_payloads.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_payloads, f, indent=2)
    
    # Save field evidence report
    evidence_path = debug_dir / f"{safe_firm}_{safe_url}_field_evidence.json"
    with open(evidence_path, 'w', encoding='utf-8') as f:
        json.dump(extraction_result, f, indent=2)
