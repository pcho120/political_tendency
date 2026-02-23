"""
Source Validator - Per-Source Quality Gate

PRINCIPLE: Validate source BEFORE accepting all URLs.

Sample 3 profiles from each candidate source.
Check field thresholds:
- name: required (3/3)
- title: ≥1/3
- office: ≥1/3 (US only)
- practice_areas: ≥1/3

If validation fails, reject source and try next layer.
This prevents accepting 4206 URLs that will all fail enrichment.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Configure logging
logger = logging.getLogger(__name__)

# US states for office filtering
US_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
}

US_STATE_NAMES = {
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'new hampshire', 'new jersey', 'new mexico', 'new york',
    'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
    'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
    'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
    'west virginia', 'wisconsin', 'wyoming', 'district of columbia'
}


@dataclass
class ProfileSample:
    """Single profile sample from source"""
    url: str
    name: Optional[str] = None
    title: Optional[str] = None
    office: Optional[str] = None  # US office only
    practice_areas: list[str] = field(default_factory=list)
    has_us_office: bool = False
    extraction_success: bool = False
    failure_reason: Optional[str] = None
    from_playwright: bool = False


@dataclass
class SourceValidationResult:
    """Result of source validation"""
    source_url: str
    source_type: str  # attorney_list, sitemap, directory, etc.
    is_valid: bool
    sampled_profiles: list[ProfileSample]
    field_validation: dict  # field -> pass count
    validation_notes: list[str]
    failure_reason: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            'source_url': self.source_url,
            'source_type': self.source_type,
            'is_valid': self.is_valid,
            'samples_count': len(self.sampled_profiles),
            'field_validation': self.field_validation,
            'validation_notes': self.validation_notes,
            'failure_reason': self.failure_reason
        }


class SourceValidator:
    """
    Validates sources by sampling profiles and checking field thresholds.
    
    CRITICAL: This prevents accepting sources that will yield poor profiles.
    Samples 3 profiles per source before accepting all URLs.
    """
    
    def __init__(self, session: requests.Session, timeout: int = 7):
        self.session = session
        self.timeout = timeout
    
    def validate_source(self, 
                       source_url: str,
                       source_type: str,
                       candidate_urls: list[str],
                       sample_size: int = 3) -> SourceValidationResult:
        """
        Validate source by sampling profiles.
        
        Args:
            source_url: The source URL (sitemap, directory, etc.)
            source_type: Type of source (attorney_list, sitemap, directory)
            candidate_urls: List of profile URLs from this source
            sample_size: Number of profiles to sample (default 3)
        
        Returns:
            SourceValidationResult with is_valid flag
        """
        logger.info(f"Validating source: {source_url} ({source_type})")
        logger.info(f"  Candidate URLs: {len(candidate_urls)}")
        
        result = SourceValidationResult(
            source_url=source_url,
            source_type=source_type,
            is_valid=False,
            sampled_profiles=[],
            field_validation={},
            validation_notes=[]
        )
        
        # Edge case: no candidate URLs
        if not candidate_urls:
            result.failure_reason = "no_candidate_urls"
            result.validation_notes.append("Source has no candidate URLs")
            return result
        
        # Sample URLs (random selection)
        sample_count = min(sample_size, len(candidate_urls))
        sampled_urls = random.sample(candidate_urls, sample_count)
        
        logger.info(f"  Sampling {sample_count} profile URLs...")
        
        # Extract from each sample
        for url in sampled_urls:
            sample = self._sample_profile(url)
            result.sampled_profiles.append(sample)
        
        # Calculate field validation counts
        field_counts = {
            'name': sum(1 for s in result.sampled_profiles if s.name),
            'title': sum(1 for s in result.sampled_profiles if s.title),
            'office': sum(1 for s in result.sampled_profiles if s.has_us_office),
            'practice_areas': sum(1 for s in result.sampled_profiles if s.practice_areas)
        }
        
        result.field_validation = field_counts
        
        # Validation thresholds
        name_required = sample_count  # All samples must have name
        field_threshold = max(1, sample_count // 3)  # At least 1/3
        
        passed_name = field_counts['name'] >= name_required
        passed_title = field_counts['title'] >= field_threshold
        passed_office = field_counts['office'] >= field_threshold
        passed_practice = field_counts['practice_areas'] >= field_threshold
        allow_missing_title = any(s.from_playwright and s.name for s in result.sampled_profiles)
        
        logger.info(f"  Field validation:")
        logger.info(f"    name: {field_counts['name']}/{sample_count} (required: {name_required})")
        logger.info(f"    title: {field_counts['title']}/{sample_count} (required: {field_threshold})")
        logger.info(f"    office (US): {field_counts['office']}/{sample_count} (required: {field_threshold})")
        logger.info(f"    practice_areas: {field_counts['practice_areas']}/{sample_count} (required: {field_threshold})")
        
        # Determine if source is valid
        if not passed_name:
            result.failure_reason = "name_threshold_not_met"
            result.validation_notes.append(f"Name field missing in {sample_count - field_counts['name']}/{sample_count} samples")
        elif not passed_title and not allow_missing_title:
            result.failure_reason = "title_threshold_not_met"
            result.validation_notes.append(f"Title field below threshold: {field_counts['title']}/{sample_count}")
        elif not passed_office and not passed_practice and not allow_missing_title:
            # At least ONE of office or practice areas should pass
            result.failure_reason = "no_usable_metadata"
            result.validation_notes.append("Neither office nor practice areas meet threshold")
        else:
            result.is_valid = True
            if not passed_title and allow_missing_title:
                result.validation_notes.append("title_missing_in_sample")
                logger.info("  Title missing in samples, but Playwright extracted names; allowing validation to pass")
            result.validation_notes.append(f"Source validated: {field_counts['name']} names, {field_counts['title']} titles, {field_counts['office']} US offices")
        
        logger.info(f"  Validation result: {'PASS' if result.is_valid else 'FAIL'}")
        if not result.is_valid:
            logger.info(f"  Failure reason: {result.failure_reason}")
        
        return result
    
    def _sample_profile(self, url: str) -> ProfileSample:
        """Sample a single profile URL and extract basic fields"""
        sample = ProfileSample(url=url)
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            
            # Log raw HTML for debugging (first 500 chars)
            html_preview = response.text[:500]
            logger.info(f"Sample HTML from {url}:")
            logger.info(f"  {html_preview}...")
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Detect bot protection BEFORE extraction
            bot_protection_detected = self._detect_bot_protection(response, soup)
            if bot_protection_detected:
                logger.warning(f"  Bot protection detected on {url}: {bot_protection_detected}")
                sample.failure_reason = f"bot_protection_{bot_protection_detected}"
                
                # Try lightweight Playwright render for sampling only
                playwright_sample = self._try_playwright_sample(url)
                if playwright_sample:
                    logger.info(f"  Playwright fallback succeeded for {url}")
                    return playwright_sample
                else:
                    logger.warning(f"  Playwright fallback failed for {url}")
                    return sample
            
            if response.status_code != 200:
                sample.failure_reason = f"http_{response.status_code}"
                return sample
            
            # If HTML does not contain a <h1> with a name, attempt lightweight Playwright render
            h1 = soup.find('h1')
            if not h1 or not h1.get_text(strip=True):
                playwright_sample = self._try_playwright_sample(url)
                if playwright_sample:
                    logger.info(f"  Playwright fallback succeeded for {url}")
                    return playwright_sample
                else:
                    logger.warning(f"  Playwright fallback failed for {url}")

            # Quick extraction (lightweight, no heavy parsing)
            sample.name = self._extract_name(soup)
            sample.title = self._extract_title(soup)
            sample.office = self._extract_office(soup)
            sample.practice_areas = self._extract_practice_areas(soup)
            
            # Log extraction results
            logger.info(f"  Extracted: name={bool(sample.name)}, title={bool(sample.title)}, office={bool(sample.office)}")
            
            # Check if has US office
            sample.has_us_office = self._has_us_office(sample.office)
            
            # Mark success if we got at least name
            sample.extraction_success = bool(sample.name)
            
        except Exception as e:
            sample.failure_reason = str(e)
            logger.debug(f"Sample extraction failed for {url}: {e}")
        
        return sample
    
    def _extract_name(self, soup: BeautifulSoup) -> Optional[str]:
        """Quick name extraction"""
        # Try common patterns
        selectors = [
            ('h1', {}),
            ('h2', {}),
            ('div', {'class': 'name'}),
            ('span', {'class': 'attorney-name'}),
            ('meta', {'property': 'og:title'}),
        ]
        
        for tag, attrs in selectors:
            elem = soup.find(tag, attrs)
            if elem:
                text = elem.get('content') if tag == 'meta' else elem.get_text(strip=True)
                if text and len(text) > 3 and len(text) < 100:
                    return text
        
        return None
    
    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Quick title extraction"""
        # Common title patterns
        keywords = ['partner', 'associate', 'counsel', 'attorney', 'lawyer', 'of counsel']
        
        # Check common locations
        for elem in soup.find_all(['p', 'div', 'span'], limit=20):
            text = elem.get_text(strip=True).lower()
            if any(kw in text for kw in keywords) and len(text) < 100:
                return elem.get_text(strip=True)
        
        return None
    
    def _extract_office(self, soup: BeautifulSoup) -> Optional[str]:
        """Quick office extraction"""
        # Look for location/office keywords
        keywords = ['office', 'location', 'address']
        
        for elem in soup.find_all(['div', 'span', 'p'], limit=30):
            if any(kw in str(elem.get('class', '')).lower() for kw in keywords):
                text = elem.get_text(strip=True)
                if text and len(text) > 2 and len(text) < 200:
                    return text
        
        return None
    
    def _extract_practice_areas(self, soup: BeautifulSoup) -> list[str]:
        """Quick practice areas extraction"""
        practice_areas = []
        keywords = ['practice', 'area', 'focus', 'expertise']
        
        for elem in soup.find_all(['div', 'ul', 'section'], limit=20):
            if any(kw in str(elem.get('class', '')).lower() for kw in keywords):
                # Extract list items or text
                items = elem.find_all('li')
                if items:
                    practice_areas.extend([li.get_text(strip=True) for li in items if li.get_text(strip=True)])
                else:
                    text = elem.get_text(strip=True)
                    if text:
                        practice_areas.append(text)
        
        return practice_areas[:5]  # Limit to 5
    
    def _has_us_office(self, office_text: Optional[str]) -> bool:
        """Check if office text contains US state"""
        if not office_text:
            return False
        
        office_lower = office_text.lower()
        
        # Check state abbreviations
        words = office_text.upper().split()
        if any(word in US_STATES for word in words):
            return True
        
        # Check state names
        if any(state in office_lower for state in US_STATE_NAMES):
            return True
        
        return False
    
    def _detect_bot_protection(self, response, soup: BeautifulSoup) -> Optional[str]:
        """Detect bot protection mechanisms
        
        Returns:
            None if no bot protection detected
            String description of bot protection type if detected
        """
        # Check HTTP 403
        if response.status_code == 403:
            return "http_403"
        
        # Check Cloudflare
        if 'cf-ray' in response.headers or 'cloudflare' in response.text.lower():
            return "cloudflare"
        
        # Check reCAPTCHA
        if 'recaptcha' in response.text.lower() or 'grecaptcha' in response.text.lower():
            return "recaptcha"
        
        # Check for challenge pages
        title = soup.find('title')
        if title:
            title_text = title.get_text().lower()
            if any(keyword in title_text for keyword in ['attention required', 'just a moment', 'checking your browser']):
                return "challenge_page"
        
        # Check for empty/minimal HTML (often indicates JS-required page)
        text_content = soup.get_text(strip=True)
        if len(text_content) < 100:
            return "minimal_content"
        
        return None
    
    def _try_playwright_sample(self, url: str) -> Optional[ProfileSample]:
        """Attempt lightweight Playwright render for sampling only
        
        Returns ProfileSample if successful, None if Playwright unavailable or fails
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.debug("Playwright not available for fallback sampling")
            return None
        
        sample = ProfileSample(url=url)
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # Quick page load with timeout
                page.goto(url, timeout=15000, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)  # Brief wait for JS
                
                # Get HTML after JS render
                html = page.content()
                browser.close()
                
                # Parse and extract
                soup = BeautifulSoup(html, 'html.parser')
                sample.name = self._extract_name(soup)
                sample.title = self._extract_title_playwright(soup) or self._extract_title(soup)
                sample.office = self._extract_office(soup)
                sample.practice_areas = self._extract_practice_areas(soup)
                sample.has_us_office = self._has_us_office(sample.office)
                sample.extraction_success = bool(sample.name)
                sample.from_playwright = True
                
                return sample if sample.extraction_success else None
                
        except Exception as e:
            logger.debug(f"Playwright sampling failed for {url}: {e}")
            return None

    def _extract_title_playwright(self, soup: BeautifulSoup) -> Optional[str]:
        """Expanded title extraction for Playwright-rendered HTML"""
        role_keywords = ['partner', 'associate', 'counsel', 'attorney', 'lawyer', 'of counsel']

        # h2/h3 with role keywords
        for heading in soup.find_all(['h2', 'h3'], limit=10):
            text = heading.get_text(strip=True)
            if text and any(kw in text.lower() for kw in role_keywords) and len(text) < 120:
                return text

        # Elements with class names indicating title/position/role
        for elem in soup.find_all(['div', 'span', 'p'], limit=30):
            class_attr = elem.get('class') if hasattr(elem, 'get') else None
            if isinstance(class_attr, list):
                class_text = " ".join([str(c) for c in class_attr]).lower()
            else:
                class_text = str(class_attr or "").lower()
            if any(key in class_text for key in ['title', 'position', 'role']):
                text = elem.get_text(strip=True)
                if text and any(kw in text.lower() for kw in role_keywords) and len(text) < 120:
                    return text

        # JSON-LD jobTitle
        for script in soup.find_all('script', {'type': 'application/ld+json'}, limit=5):
            try:
                data = json.loads(script.get_text(strip=True))
                if isinstance(data, dict):
                    if 'jobTitle' in data and isinstance(data['jobTitle'], str):
                        return data['jobTitle']
                    if data.get('@type') == 'Person' and isinstance(data.get('jobTitle'), str):
                        return data['jobTitle']
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and isinstance(item.get('jobTitle'), str):
                            return item['jobTitle']
            except Exception:
                continue

        # meta og:title parsing
        og_title = soup.find('meta', {'property': 'og:title'})
        if og_title:
            content = og_title.get('content')
            content_text = content if isinstance(content, str) else ""
            if any(kw in content_text.lower() for kw in role_keywords):
                return content_text

        return None


# Convenience function
def validate_source(source_url: str,
                   source_type: str, 
                   candidate_urls: list[str],
                   session: requests.Session,
                   sample_size: int = 3,
                   timeout: int = 7) -> SourceValidationResult:
    """
    Convenience function to validate a source.
    
    Returns SourceValidationResult with is_valid flag.
    """
    validator = SourceValidator(session, timeout)
    return validator.validate_source(source_url, source_type, candidate_urls, sample_size)
