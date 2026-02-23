"""
Phase 1: Observation Logger

PRINCIPLE: OBSERVE ONLY. NO JUDGMENTS.

This module records pure structural facts about law firm websites.
It does NOT decide if extraction will succeed or fail.
It does NOT label firms as HARD_CASE or REJECT.

All observations are append-only JSONL for learning over time.
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class FirmObservation:
    """
    Pure structural observation of a firm website.
    
    NO SUCCESS/FAILURE LABELS.
    NO HARD_CASE OR REJECT CLASSIFICATIONS.
    
    Only records what was observed, not what it means for extraction.
    """
    # Identity
    firm: str
    base_url: str
    timestamp: str
    observation_id: str
    
    # Robots.txt observations
    robots_txt_found: bool = False
    robots_txt_url: Optional[str] = None
    robots_txt_allows_crawl: bool = True
    robots_txt_has_sitemap: bool = False
    
    # XML Sitemap observations
    xml_sitemaps: list[str] = field(default_factory=list)
    sitemap_total_urls: int = 0
    sitemap_contains_people: bool = False
    sitemap_is_attorney_list: bool = False
    sitemap_url_patterns: list[str] = field(default_factory=list)
    
    # Directory structure observations
    directory_paths_tested: list[str] = field(default_factory=list)
    directory_base_empty: bool = False
    directory_requires_auth: bool = False
    directory_has_pagination: bool = False
    directory_has_filters: bool = False
    
    # Alphabet/letter navigation observations
    alphabet_navigation_detected: bool = False
    letter_param_format: Optional[str] = None
    letter_links_found: list[str] = field(default_factory=list)
    
    # JavaScript/SPA observations
    heavy_javascript_detected: bool = False
    react_vue_angular_detected: bool = False
    json_api_endpoints_found: list[str] = field(default_factory=list)
    graphql_endpoint_detected: bool = False
    
    # Bot protection observations
    bot_protection_detected: bool = False
    cloudflare_detected: bool = False
    recaptcha_detected: bool = False
    http_403_encountered: bool = False
    http_429_encountered: bool = False
    
    # HTML structure observations
    structured_data_present: bool = False
    schema_org_person_found: bool = False
    microdata_detected: bool = False
    jsonld_detected: bool = False
    
    # Search functionality observations
    search_form_detected: bool = False
    search_endpoint_url: Optional[str] = None
    search_requires_javascript: bool = False
    
    # Additional context
    http_response_codes: list[int] = field(default_factory=list)
    redirect_chain: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ObservationLogger:
    """
    Logs pure observations to append-only JSONL file.
    
    CRITICAL: This logger NEVER makes success/failure judgments.
    It only records what was observed during probing.
    """
    
    def __init__(self, log_file: str = "firm_observations.jsonl"):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
    def observe_firm(self, firm: str, base_url: str) -> FirmObservation:
        """
        Run full observation suite on a firm website.
        
        Returns FirmObservation with all structural facts discovered.
        Does NOT return success/failure status.
        """
        observation = FirmObservation(
            firm=firm,
            base_url=base_url,
            timestamp=datetime.utcnow().isoformat() + "Z",
            observation_id=f"{firm}_{datetime.utcnow().timestamp()}"
        )
        
        logger.info(f"Starting observation for {firm} at {base_url}")
        
        # Run observation probes (each adds to observation object)
        self._probe_robots_txt(observation)
        self._probe_xml_sitemaps(observation)
        self._probe_directories(observation)
        self._probe_alphabet_navigation(observation)
        self._probe_javascript(observation)
        self._probe_bot_protection(observation)
        self._probe_structured_data(observation)
        self._probe_search_functionality(observation)
        
        # Persist to JSONL
        self._append_to_log(observation)
        
        logger.info(f"Observation complete for {firm} - logged {len(observation.notes)} notes")
        return observation
    
    def _probe_robots_txt(self, obs: FirmObservation):
        """Probe robots.txt for crawl rules and sitemap declarations."""
        robots_url = urljoin(obs.base_url, "/robots.txt")
        obs.robots_txt_url = robots_url
        
        try:
            response = requests.get(robots_url, timeout=10)
            obs.http_response_codes.append(response.status_code)
            
            if response.status_code == 200:
                obs.robots_txt_found = True
                content = response.text.lower()
                
                # Check for sitemap declarations
                if "sitemap:" in content:
                    obs.robots_txt_has_sitemap = True
                    # Extract sitemap URLs
                    for line in response.text.split("\n"):
                        if line.lower().startswith("sitemap:"):
                            sitemap_url = line.split(":", 1)[1].strip()
                            obs.xml_sitemaps.append(sitemap_url)
                            obs.notes.append(f"Found sitemap in robots.txt: {sitemap_url}")
                
                # Check for disallow rules
                if "disallow: /" in content:
                    obs.robots_txt_allows_crawl = False
                    obs.notes.append("robots.txt disallows crawling")
                    
        except Exception as e:
            obs.notes.append(f"robots.txt probe error: {str(e)}")
            logger.debug(f"robots.txt probe failed for {obs.firm}: {e}")
    
    def _probe_xml_sitemaps(self, obs: FirmObservation):
        """Probe for XML sitemaps and analyze structure."""
        # Try common sitemap locations if not found in robots.txt
        if not obs.xml_sitemaps:
            common_paths = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/lawyers.xml"]
            for path in common_paths:
                sitemap_url = urljoin(obs.base_url, path)
                try:
                    response = requests.head(sitemap_url, timeout=5)
                    if response.status_code == 200:
                        obs.xml_sitemaps.append(sitemap_url)
                except:
                    pass
        
        # Analyze each sitemap
        for sitemap_url in obs.xml_sitemaps[:3]:  # Limit to first 3
            try:
                response = requests.get(sitemap_url, timeout=10)
                obs.http_response_codes.append(response.status_code)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, "xml")
                    
                    # Count URLs
                    urls = soup.find_all("url") or soup.find_all("loc")
                    obs.sitemap_total_urls += len(urls)
                    
                    # Check if URLs contain people/attorney/lawyer patterns
                    sample_urls = [loc.get_text() for loc in soup.find_all("loc")[:20]]
                    people_patterns = ["people", "attorney", "lawyer", "professional", "team"]
                    
                    for url in sample_urls:
                        url_lower = url.lower()
                        if any(pattern in url_lower for pattern in people_patterns):
                            obs.sitemap_contains_people = True
                            obs.sitemap_url_patterns.append(url)
                            if len(obs.sitemap_url_patterns) >= 5:
                                break
                    
                    # If majority of URLs are people-related, this might be attorney list
                    if obs.sitemap_contains_people and obs.sitemap_total_urls > 10:
                        people_count = sum(1 for u in sample_urls if any(p in u.lower() for p in people_patterns))
                        if people_count / len(sample_urls) > 0.7:
                            obs.sitemap_is_attorney_list = True
                            obs.notes.append(f"Sitemap appears to be attorney list ({people_count}/{len(sample_urls)} URLs)")
                    
            except Exception as e:
                obs.notes.append(f"Sitemap analysis error for {sitemap_url}: {str(e)}")
    
    def _probe_directories(self, obs: FirmObservation):
        """Probe common directory paths for attorney listings."""
        common_paths = [
            "/attorneys", "/lawyers", "/people", "/professionals", 
            "/team", "/our-people", "/our-team", "/attorney-directory"
        ]
        
        for path in common_paths:
            full_url = urljoin(obs.base_url, path)
            obs.directory_paths_tested.append(full_url)
            
            try:
                response = requests.get(full_url, timeout=10, allow_redirects=True)
                obs.http_response_codes.append(response.status_code)
                
                if response.status_code == 403:
                    obs.directory_requires_auth = True
                    obs.http_403_encountered = True
                    obs.notes.append(f"403 Forbidden on {path}")
                    
                elif response.status_code == 200:
                    soup = BeautifulSoup(response.content, "html.parser")
                    
                    # Check if page is empty/sparse
                    text_content = soup.get_text(strip=True)
                    if len(text_content) < 200:
                        obs.directory_base_empty = True
                        obs.notes.append(f"{path} appears empty (<200 chars)")
                    
                    # Check for pagination
                    pagination_indicators = ["page=", "pagination", "next", "previous", "page-"]
                    if any(ind in response.text.lower() for ind in pagination_indicators):
                        obs.directory_has_pagination = True
                    
                    # Check for filters
                    filter_indicators = ["filter", "practice", "location", "office", "sort"]
                    if any(ind in response.text.lower() for ind in filter_indicators):
                        obs.directory_has_filters = True
                        
            except Exception as e:
                obs.notes.append(f"Directory probe error for {path}: {str(e)}")
    
    def _probe_alphabet_navigation(self, obs: FirmObservation):
        """Probe for alphabet-based navigation (A-Z links)."""
        # Check common directory pages
        for path in obs.directory_paths_tested[:3]:
            try:
                response = requests.get(path, timeout=10)
                if response.status_code != 200:
                    continue
                    
                soup = BeautifulSoup(response.content, "html.parser")
                
                # Look for letter links (A, B, C, etc.)
                links = soup.find_all("a", href=True)
                letter_links = []
                
                for link in links:
                    href = link.get("href", "")
                    text = link.get_text(strip=True)
                    
                    # Check if link text is single letter
                    if len(text) == 1 and text.isalpha():
                        letter_links.append(href)
                    
                    # Check for letter parameter patterns
                    if "letter=" in href.lower() or "alpha=" in href.lower() or "/[a-z]/" in href.lower():
                        obs.alphabet_navigation_detected = True
                        if not obs.letter_param_format:
                            obs.letter_param_format = href
                        obs.letter_links_found.append(href)
                
                # If we found 5+ single-letter links, this is alphabet navigation
                if len(letter_links) >= 5:
                    obs.alphabet_navigation_detected = True
                    obs.letter_links_found = letter_links[:26]
                    obs.notes.append(f"Found {len(letter_links)} alphabet navigation links")
                    break
                    
            except Exception as e:
                obs.notes.append(f"Alphabet probe error: {str(e)}")
    
    def _probe_javascript(self, obs: FirmObservation):
        """Probe for heavy JavaScript/SPA characteristics."""
        try:
            response = requests.get(obs.base_url, timeout=10)
            obs.http_response_codes.append(response.status_code)
            
            if response.status_code == 200:
                content = response.text.lower()
                
                # Check for SPA frameworks
                if "react" in content or "reactdom" in content:
                    obs.react_vue_angular_detected = True
                    obs.notes.append("React detected")
                if "vue" in content or "vue.js" in content:
                    obs.react_vue_angular_detected = True
                    obs.notes.append("Vue detected")
                if "angular" in content or "ng-app" in content:
                    obs.react_vue_angular_detected = True
                    obs.notes.append("Angular detected")
                
                # Check for API endpoints in scripts
                if "api/" in content or "/api/" in content:
                    obs.heavy_javascript_detected = True
                    obs.notes.append("API endpoints mentioned in scripts")
                
                # Check for GraphQL
                if "graphql" in content:
                    obs.graphql_endpoint_detected = True
                    obs.notes.append("GraphQL detected")
                    
        except Exception as e:
            obs.notes.append(f"JavaScript probe error: {str(e)}")
    
    def _probe_bot_protection(self, obs: FirmObservation):
        """Probe for bot protection mechanisms."""
        try:
            response = requests.get(obs.base_url, timeout=10)
            obs.http_response_codes.append(response.status_code)
            
            if response.status_code == 403:
                obs.http_403_encountered = True
                obs.bot_protection_detected = True
                obs.notes.append("HTTP 403 on base URL - possible bot protection")
            
            if response.status_code == 429:
                obs.http_429_encountered = True
                obs.bot_protection_detected = True
                obs.notes.append("HTTP 429 Rate limit encountered")
            
            if response.status_code == 200:
                content = response.text.lower()
                
                # Check for Cloudflare
                if "cloudflare" in content or "cf-ray" in response.headers:
                    obs.cloudflare_detected = True
                    obs.notes.append("Cloudflare detected")
                
                # Check for reCAPTCHA
                if "recaptcha" in content or "grecaptcha" in content:
                    obs.recaptcha_detected = True
                    obs.bot_protection_detected = True
                    obs.notes.append("reCAPTCHA detected")
                    
        except Exception as e:
            obs.notes.append(f"Bot protection probe error: {str(e)}")
    
    def _probe_structured_data(self, obs: FirmObservation):
        """Probe for structured data (Schema.org, JSON-LD, microdata)."""
        try:
            response = requests.get(obs.base_url, timeout=10)
            if response.status_code != 200:
                return
                
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Check for JSON-LD
            jsonld_scripts = soup.find_all("script", type="application/ld+json")
            if jsonld_scripts:
                obs.jsonld_detected = True
                obs.structured_data_present = True
                
                # Check if any contain Person schema
                for script in jsonld_scripts[:5]:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            schema_type = data.get("@type", "")
                            if "person" in str(schema_type).lower():
                                obs.schema_org_person_found = True
                                obs.notes.append("Schema.org Person found in JSON-LD")
                    except:
                        pass
            
            # Check for microdata
            if soup.find(attrs={"itemtype": True}):
                obs.microdata_detected = True
                obs.structured_data_present = True
                obs.notes.append("Microdata detected")
                
        except Exception as e:
            obs.notes.append(f"Structured data probe error: {str(e)}")
    
    def _probe_search_functionality(self, obs: FirmObservation):
        """Probe for search forms and endpoints."""
        try:
            response = requests.get(obs.base_url, timeout=10)
            if response.status_code != 200:
                return
                
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Look for search forms
            search_forms = soup.find_all("form", attrs={"action": True})
            for form in search_forms:
                action = form.get("action", "")
                if "search" in action.lower():
                    obs.search_form_detected = True
                    obs.search_endpoint_url = urljoin(obs.base_url, action)
                    obs.notes.append(f"Search form found: {action}")
                    break
            
            # Check if search requires JavaScript
            search_inputs = soup.find_all("input", attrs={"type": "search"})
            if search_inputs and not obs.search_form_detected:
                obs.search_requires_javascript = True
                obs.notes.append("Search input found but no standard form (likely JS-based)")
                
        except Exception as e:
            obs.notes.append(f"Search probe error: {str(e)}")
    
    def _append_to_log(self, observation: FirmObservation):
        """Append observation to JSONL file."""
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                json.dump(asdict(observation), f, ensure_ascii=False)
                f.write("\n")
            logger.debug(f"Appended observation for {observation.firm} to {self.log_file}")
        except Exception as e:
            logger.error(f"Failed to append observation to log: {e}")
    
    def load_all_observations(self) -> list[FirmObservation]:
        """Load all observations from JSONL file."""
        observations = []
        
        if not self.log_file.exists():
            return observations
        
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        observations.append(FirmObservation(**data))
            logger.info(f"Loaded {len(observations)} observations from {self.log_file}")
        except Exception as e:
            logger.error(f"Failed to load observations: {e}")
        
        return observations
    
    def get_observations_for_firm(self, firm: str) -> list[FirmObservation]:
        """Get all historical observations for a specific firm."""
        all_obs = self.load_all_observations()
        return [obs for obs in all_obs if obs.firm == firm]


# Convenience function for single-call observation
def observe_firm(firm: str, base_url: str, log_file: str = "firm_observations.jsonl") -> FirmObservation:
    """
    Convenience function to observe a firm and log results.
    
    Returns FirmObservation with all structural facts.
    Does NOT return success/failure judgment.
    """
    logger = ObservationLogger(log_file)
    return logger.observe_firm(firm, base_url)
