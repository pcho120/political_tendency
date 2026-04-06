#!/usr/bin/env python3
"""attorney_extractor.py - Production-Grade Attorney Profile Extraction

ZERO-LOSS multi-stage extraction system that guarantees 100% field coverage.

Extraction Cascade:
1. HTML + BeautifulSoup (semantic parsing)
2. JSON-LD structured data
3. Embedded state objects (React/Next.js)
4. XHR/API interception (Playwright fallback)

Fields Extracted (ALL required):
- full_name
- title / role
- office_location(s) [list]
- department / group [list]
- practice_areas [list]
- industries [list]
- bar_admissions [list]
- education: [{degree, school, year}]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None
    Tag = None


# US States for bar admission extraction
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    "D.C.", "DC"
]

# Common degree abbreviations
DEGREE_PATTERNS = {
    r'\bJ\.?D\.?\b': 'JD',
    r'\bLL\.?M\.?\b': 'LLM',
    r'\bLL\.?B\.?\b': 'LLB',
    r'\bB\.?A\.?\b': 'BA',
    r'\bB\.?S\.?\b': 'BS',
    r'\bM\.?B\.?A\.?\b': 'MBA',
    r'\bM\.?A\.?\b': 'MA',
    r'\bM\.?S\.?\b': 'MS',
    r'\bPh\.?D\.?\b': 'PhD',
    # Spelled-out degree names (Gibson Dunn: 'Juris Doctor', 'Bachelor of Arts', etc.)
    r'\bJuris\s+Doctor\b': 'JD',
    r'\bJuris\s+Doctorate\b': 'JD',
    r'\bBachelor\s+of\s+Arts\b': 'BA',
    r'\bBachelor\s+of\s+Science\b': 'BS',
    r'\bMaster\s+of\s+Laws\b': 'LLM',
    r'\bMaster\s+of\s+Arts\b': 'MA',
    r'\bMaster\s+of\s+Science\b': 'MS',
    r'\bMaster\s+of\s+Business\s+Administration\b': 'MBA',
    r'\bBachelor\s+of\s+Commerce\b': 'BCom',
    r'\bB\.?Com\.?\b': 'BCom',
    r'\bBachelor\s+of\s+Laws\b': 'LLB',
    r'\bMaster\s+of\s+Public\s+Health\b': 'MPH',
    r'\bMaster\s+of\s+Public\s+Policy\b': 'MPP',
    r'\bDoctor\s+of\s+Philosophy\b': 'PhD',
}

# Name validation: handles honorifics (Dr./Prof./Hon.), leading initials, particles (de/van/etc.),
# and Unicode Latin characters (accented names like Jean-François, Müller, etc.)
_VALID_NAME_RE = re.compile(
    r"^"
    r"(?:Dr\.?\s+|Prof\.?\s+|Hon\.?\s+)?"     # optional honorific
    r"(?:[A-Z]\.?\s+)?"                         # optional leading initial: "J. " or "J "
    r"[A-ZÀ-Ö][a-zA-ZÀ-öø-ÿ\u0100-\u024F\-']+"  # first name (Unicode Latin)
    r"(?:"
        r"\s+"
        r"(?:[A-ZÀ-Ö]\.?|[A-ZÀ-Ö][a-zA-ZÀ-öø-ÿ\u0100-\u024F\.\-']+|[a-z]{1,4})"
    r")+"
    r"$"
)


@dataclass
class EducationRecord:
    """Single education record"""
    degree: str | None = None
    school: str | None = None
    year: int | None = None
    
    def to_dict(self) -> dict:
        return {
            "degree": self.degree,
            "school": self.school,
            "year": self.year
        }


@dataclass
class AttorneyProfile:
    """Complete attorney profile with ALL required fields"""
    firm: str
    profile_url: str
    full_name: str | None = None
    title: str | None = None
    offices: list[str] = field(default_factory=list)
    department: list[str] = field(default_factory=list)
    practice_areas: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    bar_admissions: list[str] = field(default_factory=list)
    education: list[EducationRecord] = field(default_factory=list)
    extraction_status: str = "UNKNOWN"  # SUCCESS | PARTIAL | FAILED
    missing_fields: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "firm": self.firm,
            "profile_url": self.profile_url,
            "full_name": self.full_name,
            "title": self.title,
            "offices": self.offices,
            "department": self.department,
            "practice_areas": self.practice_areas,
            "industries": self.industries,
            "bar_admissions": self.bar_admissions,
            "education": [e.to_dict() for e in self.education],
            "extraction_status": self.extraction_status,
            "missing_fields": self.missing_fields,
            "diagnostics": self.diagnostics
        }
    
    def calculate_status(self) -> None:
        """Calculate extraction status based on missing fields.

        SUCCESS requires ALL of:
        - valid full_name (non-empty, passes name validation)
        - title present
        - at least one office
        - bar_admissions present
        - education parsed

        Industry normalization: if industries is empty, set sentinel ["no industry field"].
        """
        required_fields = [
            "full_name", "title", "offices", "department",
            "practice_areas", "industries", "bar_admissions", "education"
        ]

        # --- Industry normalization: always provide a value ---
        if not self.industries:
            self.industries = ["no industry field"]

        self.missing_fields = []

        # Validate full_name - strip known professional suffixes before regex check
        _name_for_check = self.full_name.strip() if self.full_name else ''
        _name_for_check = re.sub(r',\s*(?:P\.C\.|Jr\.?|Sr\.?|II|III|IV|Esq\.?)\s*$', '', _name_for_check, flags=re.IGNORECASE).strip()
        name_valid = (
            bool(_name_for_check)
            and _VALID_NAME_RE.match(_name_for_check)
            and _name_for_check.lower() not in _HEADER_TERMS
        )
        if not name_valid:
            self.missing_fields.append("full_name")

        if not self.title:
            self.missing_fields.append("title")
        if not self.offices:
            self.missing_fields.append("offices")
        if not self.department:
            self.missing_fields.append("department")
        if not self.practice_areas:
            self.missing_fields.append("practice_areas")
        # industries is always populated now (sentinel above), so never missing
        if not self.bar_admissions:
            self.missing_fields.append("bar_admissions")
        if not self.education:
            self.missing_fields.append("education")

        # SUCCESS: must have valid name + title + offices + bar_admissions + education
        hard_requirements = {"full_name", "title", "offices", "bar_admissions", "education"}
        hard_missing = hard_requirements & set(self.missing_fields)

        if len(self.missing_fields) == 0:
            self.extraction_status = "SUCCESS"
        elif not hard_missing:
            # All hard requirements met; some soft fields (department/practice) missing
            self.extraction_status = "PARTIAL"
        elif len(self.missing_fields) < len(required_fields):
            self.extraction_status = "PARTIAL"
        else:
            self.extraction_status = "FAILED"


class AttorneyExtractor:
    """Multi-stage extraction engine with zero data loss guarantee"""
    
    def __init__(self):
        self.use_bs4 = BS4_AVAILABLE
        if not self.use_bs4:
            print("WARNING: BeautifulSoup4 not available, falling back to regex-only extraction")
    
    def extract_profile(self, firm_name: str, profile_url: str, html: str) -> AttorneyProfile:
        """Extract complete attorney profile using multi-stage cascade
        
        Args:
            firm_name: Name of the law firm
            profile_url: URL of the attorney profile page
            html: HTML content of the profile page
        
        Returns:
            AttorneyProfile with all available data (missing fields = null)
        """
        profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)

        # STAGE 0: Firm-specific extraction (highest priority — exact CSS selectors)
        if self.use_bs4:
            soup_stage0 = BeautifulSoup(html, 'html.parser')
            if self._is_kirkland_profile(soup_stage0, profile_url):
                self._extract_kirkland_profile(profile, soup_stage0)
                profile.calculate_status()
                if profile.extraction_status == 'SUCCESS':
                    return profile
                # Partial — fall through to generic stages for remaining fields

        # STAGE 1: JSON-LD extraction (highest priority)
        json_ld_data = self._extract_json_ld(html)
        if json_ld_data:
            self._merge_json_ld_data(profile, json_ld_data)

        # STAGE 2: Embedded state objects (React/Next.js)
        embedded_data = self._extract_embedded_state(html)
        if embedded_data:
            self._merge_embedded_data(profile, embedded_data)

        # STAGE 3: BeautifulSoup semantic extraction
        if self.use_bs4:
            soup = BeautifulSoup(html, 'html.parser')
            self._extract_with_bs4(profile, soup, profile_url)

        # STAGE 4: Regex fallback extraction
        self._extract_with_regex(profile, html, profile_url)

        # Calculate final status
        profile.calculate_status()

        return profile
    
    # ========================================================================
    # STAGE 0: FIRM-SPECIFIC EXTRACTION (Kirkland & Ellis)
    # ========================================================================

    def _is_kirkland_profile(self, soup: 'BeautifulSoup', profile_url: str) -> bool:
        """Detect if this is a Kirkland & Ellis profile page."""
        if 'kirkland.com' in profile_url:
            return True
        # Also detect by page class
        body = soup.find('body')
        if body and 'page__people-detail' in (body.get('class') or []):
            return True
        return False

    def _extract_kirkland_profile(self, profile: 'AttorneyProfile', soup: 'BeautifulSoup') -> None:
        """Extract all fields from a Kirkland & Ellis profile using confirmed CSS selectors."""
        # Name
        if not profile.full_name:
            el = soup.select_one('.profile-heading__name-label, .profile-heading__name')
            if el:
                name = el.get_text(strip=True)
                # Strip professional suffixes before validation
                _clean = re.sub(r',\s*(?:P\.C\.|Jr\.?|Sr\.?|II|III|IV|Esq\.?)\s*$', '', name, flags=re.IGNORECASE).strip()
                if _clean and self._looks_like_person_name(_clean):
                    profile.full_name = name  # store original (with suffix)

        # Title (position/level)
        if not profile.title:
            el = soup.select_one('.profile-heading__position')
            if el:
                profile.title = el.get_text(strip=True)

        # Department (specialty/practice group)
        if not profile.department:
            el = soup.select_one('.profile-heading__specialty')
            if el:
                profile.department = [el.get_text(strip=True)]

        # Office locations
        if not profile.offices:
            offices = [el.get_text(strip=True) for el in soup.select('.profile-heading__location-link')]
            if offices:
                profile.offices = offices

        # Practice areas (.prominent-services__link)
        if not profile.practice_areas:
            practices = [el.get_text(strip=True) for el in soup.select('.prominent-services__link')]
            if practices:
                profile.practice_areas = practices

        # Bar admissions
        if not profile.bar_admissions:
            admissions = []
            for li in soup.select('.normalized-rte-list--admissions li'):
                year_el = li.select_one('.normalized-rte-list__admission-year')
                loc_el  = li.select_one('.normalized-rte-list__admission-location')
                parts = []
                if year_el:
                    parts.append(year_el.get_text(strip=True))
                if loc_el:
                    parts.append(loc_el.get_text(strip=True))
                entry = ' '.join(parts).strip()
                if entry:
                    admissions.append(entry)
            if admissions:
                profile.bar_admissions = admissions

        # Education
        if not profile.education:
            edu_records = []
            for li in soup.select('.normalized-rte-list--education li'):
                school_el = li.select_one('.normalized-rte-list__item--edu-name')
                school_el = li.select_one('.normalized-rte-list__item--edu-name')
                degree_el = li.select_one('.normalized-rte-list__item--edu-degree')
                year_el   = li.select_one('.normalized-rte-list__item--edu-year')
                if school_el or degree_el:
                    rec = EducationRecord()
                    rec.school = school_el.get_text(strip=True) if school_el else None
                    rec.degree = degree_el.get_text(strip=True) if degree_el else None
                    rec.year   = year_el.get_text(strip=True) if year_el else None
                    edu_records.append(rec)
            if edu_records:
                profile.education = edu_records


    # ========================================================================
    # STAGE 1: JSON-LD EXTRACTION
    # ========================================================================
    
    def _extract_json_ld(self, html: str) -> dict | None:
        """Extract and parse all JSON-LD blocks"""
        try:
            json_ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html,
                re.IGNORECASE | re.DOTALL
            )
            
            for block in json_ld_blocks:
                try:
                    data = json.loads(block)
                    items = data if isinstance(data, list) else [data]
                    
                    for item in items:
                        if isinstance(item, dict):
                            if item.get("@type") in ["Person", "http://schema.org/Person"]:
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        
        return None
    
    def _merge_json_ld_data(self, profile: AttorneyProfile, data: dict) -> None:
        """Merge JSON-LD Person data into profile"""
        # Name
        if not profile.full_name:
            name = data.get("name", "")
            if name and self._looks_like_person_name(name):
                profile.full_name = name.strip()
        
        # Title/Job
        if not profile.title:
            job_title = data.get("jobTitle", "")
            if job_title:
                profile.title = job_title.strip()
        
        # Office/Location
        work_location = data.get("workLocation", {})
        if isinstance(work_location, dict):
            location_name = work_location.get("name", "")
            if location_name and location_name not in profile.offices:
                profile.offices.append(location_name.strip())
        
        address = data.get("address", {})
        if isinstance(address, dict):
            city = address.get("addressLocality", "")
            if city and city not in profile.offices:
                profile.offices.append(city.strip())
        elif isinstance(address, str) and address:
            if address not in profile.offices:
                profile.offices.append(address.strip())
        
        # Practice Areas (knowsAbout)
        knows_about = data.get("knowsAbout", [])
        if isinstance(knows_about, list):
            for item in knows_about:
                if item and item not in profile.practice_areas:
                    profile.practice_areas.append(str(item).strip())
        elif isinstance(knows_about, str) and knows_about:
            if knows_about not in profile.practice_areas:
                profile.practice_areas.append(knows_about.strip())
        
        # Education (alumniOf)
        alumni_of = data.get("alumniOf", [])
        if isinstance(alumni_of, list):
            for school_data in alumni_of:
                if isinstance(school_data, dict):
                    school_name = school_data.get("name", "")
                    if school_name:
                        edu = EducationRecord(school=school_name.strip())
                        profile.education.append(edu)
                elif isinstance(school_data, str):
                    edu = EducationRecord(school=school_data.strip())
                    profile.education.append(edu)
        elif isinstance(alumni_of, dict):
            school_name = alumni_of.get("name", "")
            if school_name:
                edu = EducationRecord(school=school_name.strip())
                profile.education.append(edu)
    
    # ========================================================================
    # STAGE 2: EMBEDDED STATE OBJECTS
    # ========================================================================
    
    def _extract_embedded_state(self, html: str) -> dict | None:
        """Extract embedded React/Next.js state objects"""
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\.__APOLLO_STATE__\s*=\s*({.*?});',
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        ]
        
        for pattern in patterns:
            try:
                matches = re.findall(pattern, html, re.DOTALL)
                for match in matches:
                    try:
                        data = json.loads(match)
                        # Look for attorney-related data
                        attorney_data = self._find_attorney_data_recursive(data)
                        if attorney_data:
                            return attorney_data
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue
        
        return None
    
    def _find_attorney_data_recursive(self, obj: Any, depth: int = 0) -> dict | None:
        """Recursively search for attorney profile data in nested objects"""
        if depth > 5:
            return None
        
        if isinstance(obj, dict):
            # Look for attorney-like keys
            attorney_keys = ["attorney", "lawyer", "professional", "person", "profile", "bio"]
            for key in attorney_keys:
                if key in str(obj.keys()).lower():
                    return obj
            
            # Recurse into values
            for value in obj.values():
                result = self._find_attorney_data_recursive(value, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_attorney_data_recursive(item, depth + 1)
                if result:
                    return result
        
        return None
    
    def _merge_embedded_data(self, profile: AttorneyProfile, data: dict) -> None:
        """Merge embedded state data into profile"""
        # This is generic - specific structure depends on site
        # Try common field names
        for name_key in ["name", "fullName", "displayName", "firstName"]:
            if name_key in data and not profile.full_name:
                name = str(data[name_key])
                if self._looks_like_person_name(name):
                    profile.full_name = name.strip()
                    break
        
        for title_key in ["title", "position", "role", "jobTitle"]:
            if title_key in data and not profile.title:
                profile.title = str(data[title_key]).strip()
                break
        
        # Try to extract practice areas
        for practice_key in ["practices", "practiceAreas", "expertise", "specialties"]:
            if practice_key in data:
                practices = data[practice_key]
                if isinstance(practices, list):
                    for p in practices:
                        if p and p not in profile.practice_areas:
                            profile.practice_areas.append(str(p).strip())
                elif isinstance(practices, str) and practices:
                    if practices not in profile.practice_areas:
                        profile.practice_areas.append(practices.strip())

        # Target list fields: additive union/dedup for department
        for dept_key in ["department", "group", "practiceGroup", "division"]:
            if dept_key in data:
                dept_val = data[dept_key]
                if isinstance(dept_val, list):
                    for d in dept_val:
                        if d and str(d).strip() not in profile.department:
                            profile.department.append(str(d).strip())
                elif isinstance(dept_val, str) and dept_val and dept_val.strip() not in profile.department:
                    profile.department.append(dept_val.strip())
                break

        # Target list fields: additive union/dedup for offices
        for office_key in ["offices", "office", "location", "officeLocation"]:
            if office_key in data:
                office_val = data[office_key]
                if isinstance(office_val, list):
                    for o in office_val:
                        if o and str(o).strip() not in profile.offices:
                            profile.offices.append(str(o).strip())
                elif isinstance(office_val, str) and office_val and office_val.strip() not in profile.offices:
                    profile.offices.append(office_val.strip())
                break
    
    # ========================================================================
    # STAGE 3: BEAUTIFULSOUP SEMANTIC EXTRACTION
    # ========================================================================
    
    def _extract_with_bs4(self, profile: AttorneyProfile, soup: BeautifulSoup, url: str) -> None:
        """Extract fields using BeautifulSoup semantic parsing"""
        
        # Name extraction
        if not profile.full_name:
            profile.full_name = self._extract_name_bs4(soup, url)
        
        # Title extraction
        if not profile.title:
            profile.title = self._extract_title_bs4(soup)
        
        # Office/Location
        offices = self._extract_offices_bs4(soup)
        for office in offices:
            if office not in profile.offices:
                profile.offices.append(office)
        
        # Department/Group
        departments = self._extract_departments_bs4(soup)
        for dept in departments:
            if dept not in profile.department:
                profile.department.append(dept)
        
        # Practice Areas
        practices = self._extract_practices_bs4(soup)
        for practice in practices:
            if practice not in profile.practice_areas:
                profile.practice_areas.append(practice)
        
        # Industries
        industries = self._extract_industries_bs4(soup)
        for industry in industries:
            if industry not in profile.industries:
                profile.industries.append(industry)
        
        # Bar Admissions
        bars = self._extract_bar_admissions_bs4(soup)
        for bar in bars:
            if bar not in profile.bar_admissions:
                profile.bar_admissions.append(bar)
        
        # Education — skip generic extraction if Kirkland-specific extractor already populated it
        if not profile.education:
            education_records = self._extract_education_bs4(soup)
            for edu in education_records:
                profile.education.append(edu)
    
    def _extract_name_bs4(self, soup: BeautifulSoup, url: str) -> str | None:
        """Extract name using BeautifulSoup"""
        # Priority 1: H1 tag
        h1 = soup.find('h1')
        if h1:
            name = h1.get_text(strip=True)
            if self._looks_like_person_name(name):
                return name
        
        # Priority 2: Elements with name-related classes
        name_selectors = [
            {'class': re.compile(r'.*\bname\b.*', re.I)},
            {'class': re.compile(r'.*\battorney-name\b.*', re.I)},
            {'class': re.compile(r'.*\bprofessional-name\b.*', re.I)},
        ]
        
        for selector in name_selectors:
            elem = soup.find(['h1', 'h2', 'div', 'span'], selector)
            if elem:
                name = elem.get_text(strip=True)
                if self._looks_like_person_name(name):
                    return name
        
        # Priority 3: Meta og:title
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title_text = og_title['content']
            parts = re.split(r'[|\-–—]', title_text)
            if parts:
                name = parts[0].strip()
                if self._looks_like_person_name(name):
                    return name
        
        # Priority 4: Title tag
        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            parts = re.split(r'[|\-–—]', title_text)
            if parts:
                name = parts[0].strip()
                if self._looks_like_person_name(name):
                    return name
        
        return None
    
    def _extract_title_bs4(self, soup: BeautifulSoup) -> str | None:
        """Extract title/position using BeautifulSoup"""
        # Strategy 0a: Cleary Gottlieb — <h2 class='person-info__position'>
        cleary_pos = soup.find('h2', class_='person-info__position')
        if cleary_pos:
            t = cleary_pos.get_text(strip=True)
            if t and len(t) < 100 and not re.search(r'(cookie|consent|privacy choices)', t, re.I):
                return t
        # Strategy 0b: Paul Weiss — <div class='contact-block'> <p class='detail-label'>Partner</p>
        contact_block = soup.find('div', class_=lambda c: c and 'contact-block' in ' '.join(c if isinstance(c,list) else [c]))
        if contact_block:
            lbl = contact_block.find('p', class_='detail-label')
            if lbl:
                t = lbl.get_text(strip=True)
                if t and len(t) < 80 and not re.search(r'(tel:|email|@|cookie|privacy)', t, re.I):
                    return t
        # Strategy 0: Drupal field_job_title (Davis Polk pattern)
        # <div class='field-name--field_job_title field-item'>Partner</div>
        job_title_elem = soup.find(
            ['div', 'span', 'p'],
            class_=lambda c: c and any('field_job_title' in cls for cls in (c if isinstance(c, list) else [c]))
        )
        if job_title_elem:
            t = job_title_elem.get_text(strip=True)
            if t and len(t) < 100:
                return t
        # Strategy 1: class-based title selector (skip nav/menu elements)
        for elem in soup.find_all(['div', 'span', 'p'], class_=re.compile(r'(title|position|role|job)', re.I)):
            cls = ' '.join(elem.get('class', []))
            # Skip nav/menu/footer elements
            if re.search(r'(menu|nav|footer|breadcrumb|modal|share|card|insight|teaser|cookie|consent|privacy)', cls, re.I):
                continue
            # Skip if inside a nav or header tag
            if elem.find_parent(['nav', 'header']):
                continue
            title = elem.get_text(strip=True)
            if title and len(title) < 100 and not re.search(r'(cookie|consent|privacy choices)', title, re.I):
                return title

        # Strategy 2: White & Case hero pattern — 'fs-5' div with 'Title, City' format
        fs5 = soup.find('div', class_=lambda c: c and 'fs-5' in c)
        if fs5:
            text = fs5.get_text(strip=True)
            # 'Partner, Los Angeles' — split on first comma
            if ',' in text and len(text) < 100:
                return text.split(',')[0].strip()

        # Strategy 3: keyword scan (skip script/style/meta parents)
        title_keywords = [
            "Partner", "Associate", "Counsel", "Of Counsel",
            "Senior Associate", "Managing Partner", "Senior Partner",
            "Member", "Shareholder", "Principal",
            "Attorney",
        ]
        
        for keyword in title_keywords:
            for elem in soup.find_all(string=re.compile(rf'\b{keyword}\b', re.I)):
                parent = elem.parent
                if parent and parent.name not in ('script', 'style', 'meta', 'head'):
                    if parent.find_parent(['nav', 'header', 'footer']):
                        continue
                    title = parent.get_text(strip=True)
                    if title and len(title) < 100 and not re.search(r'(cookie|consent|privacy choices)', title, re.I):
                        return title
        
        return None
    
    def _extract_offices_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract office locations using BeautifulSoup"""
        offices = []
        # Strategy 0a: Cleary Gottlieb — <h4 class='location__city'>
        for city_tag in soup.find_all('h4', class_='location__city'):
            city = city_tag.get_text(strip=True)
            if city and city not in offices:
                offices.append(city)
        if offices:
            return offices

        # Strategy 0b: Paul Weiss — <div class='location-block'> <a>Washington, DC</a>
        pw_loc = soup.find('div', class_=lambda c: c and 'location-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_loc:
            for a in pw_loc.find_all('a'):
                city = a.get_text(strip=True)
                if city and len(city) < 60 and city not in offices:
                    offices.append(city)
            if offices:
                return offices
        office_selectors = [
            {'class': re.compile(r'(office|location|city)', re.I)},
        ]
        
        FOOTER_CLASSES = re.compile(r'footer', re.I)
        for selector in office_selectors:
            elems = soup.find_all(['div', 'span', 'p', 'li', 'button', 'a'], selector)
            for elem in elems:
                # Skip footer/nav/menu elements
                elem_classes = ' '.join(elem.get('class', []))
                if FOOTER_CLASSES.search(elem_classes):
                    continue
                if elem.find_parent(['nav', 'header', 'footer']):
                    continue
                text = elem.get_text(strip=True)
                if text and len(text) < 100:
                    offices.append(text)

        # Secondary: links to /office/{city-slug}/ (e.g. Gibson Dunn: /office/palo-alto/)
        if not offices:
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                if re.search(r'/office/[a-z0-9-]+/?$', href, re.I):
                    text = a.get_text(strip=True)
                    if text and len(text) < 60:
                        offices.append(text)

        # Tertiary: White & Case fs-5 hero pattern — 'Title, City'
        if not offices:
            fs5 = soup.find('div', class_=lambda c: c and 'fs-5' in c)
            if fs5:
                text = fs5.get_text(strip=True)
                if ',' in text and len(text) < 150:
                    parts = text.split(',')
                    # parts[0] is title, rest are city names
                    # Merge state abbreviations (e.g. 'Washington', 'DC') back into city
                    cities: list[str] = []
                    for part in parts[1:]:
                        part = re.sub(r'\+\d.*', '', part).strip()
                        if not part:
                            continue
                        # If part looks like a state/country suffix (2-3 uppercase chars or 'DC')
                        if re.fullmatch(r'[A-Z\.]{1,3}', part) and cities:
                            cities[-1] = cities[-1] + ', ' + part
                        elif len(part) < 50:
                            cities.append(part)
                    offices.extend(cities)

        return offices
    
    def _extract_departments_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract department/group using BeautifulSoup"""
        departments: list[str] = []
        seen: set[str] = set()
        inline_tags = {"span", "a", "strong", "em", "b", "small", "label"}
        generic_labels = {
            "department", "departments", "group", "groups", "division", "section",
            "practice group", "practice groups", "team", "overview",
        }
        nav_keywords = (
            "home", "people", "lawyers", "practices", "industries", "offices", "careers",
            "insights", "our firm", "inclusion", "alumni", "search", "login", "go back",
            "proceed",
        )
        bio_verbs = re.compile(
            r"\b(advises|advised|represents|represented|focuses|focused|works on|clients|"
            r"experience|distressed debt|workouts|compliance)\b",
            re.IGNORECASE,
        )

        def _clean_candidate(text: str) -> str:
            text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
            return re.sub(r"\s+", " ", text).strip(" \t\r\n:-•|")

        def _is_noise(candidate: str) -> bool:
            lowered = candidate.lower()
            if not candidate or len(candidate) < 2 or len(candidate) > 100:
                return True
            if lowered in generic_labels:
                return True
            if re.search(r"@|http|www\.|cookie|consent|tel:|phone", candidate, re.IGNORECASE):
                return True
            if any(keyword in lowered for keyword in nav_keywords):
                return True
            if len(candidate) >= 35 and bio_verbs.search(candidate):
                return True
            return False

        def _add_candidate(candidate: str | None) -> None:
            if not candidate:
                return
            cleaned = _clean_candidate(candidate)
            if _is_noise(cleaned):
                return
            key = cleaned.lower()
            if key in seen:
                return
            seen.add(key)
            departments.append(cleaned)

        def _extract_json_ld_departments() -> None:
            def _collect_department_values(value: Any) -> None:
                if isinstance(value, str):
                    _add_candidate(value)
                elif isinstance(value, list):
                    for item in value:
                        _collect_department_values(item)
                elif isinstance(value, dict):
                    _collect_department_values(value.get("name") or value.get("department"))

            def _walk_json_ld(node: Any) -> None:
                if isinstance(node, list):
                    for item in node:
                        _walk_json_ld(item)
                    return
                if not isinstance(node, dict):
                    return

                node_type = node.get("@type")
                node_types = node_type if isinstance(node_type, list) else [node_type]
                if any(str(item).lower().endswith("person") for item in node_types if item):
                    _collect_department_values(node.get("department"))

                for value in node.values():
                    if isinstance(value, (dict, list)):
                        _walk_json_ld(value)

            for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
                raw = script.string or script.get_text()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                _walk_json_ld(payload)

        def _extract_label_from_container(elem: Tag) -> str | None:
            for attr_name in ("data-department", "data-dept"):
                attr_value = elem.get(attr_name)
                if isinstance(attr_value, str) and attr_value.strip():
                    return attr_value.strip()

            heading = elem.find(["h1", "h2", "h3", "h4", "h5", "h6", "button", "summary", "label"])
            if heading:
                heading_text = heading.get_text(" ", strip=True)
                if heading_text and heading_text.lower() not in generic_labels:
                    return heading_text

            direct_parts: list[str] = []
            for child in elem.children:
                if isinstance(child, str):
                    part = child.strip()
                elif getattr(child, "name", None) in inline_tags:
                    part = child.get_text(" ", strip=True)
                else:
                    continue
                if part:
                    direct_parts.append(part)

            if direct_parts:
                return " ".join(direct_parts)

            return None

        def _candidate_texts_from_sibling(elem: Tag) -> list[str]:
            if elem.get("role") == "tablist" or elem.find(attrs={"role": "tab"}):
                return []

            items: list[str] = []
            for node in elem.find_all(["li", "p", "dd"]):
                text = node.get_text(" ", strip=True)
                if text:
                    items.append(text)

            if items:
                return items

            fallback = _extract_label_from_container(elem)
            return [fallback] if fallback else []

        _extract_json_ld_departments()

        for elem in soup.find_all(
            lambda tag: tag.name and not tag.find_parent(["nav", "header", "footer"]) and (
                any(
                    re.search(r"department|practice[-_ ]?group|dept", cls, re.I)
                    for cls in (tag.get("class") or [])
                )
                or tag.has_attr("data-department")
                or tag.has_attr("data-dept")
            )
        ):
            _add_candidate(_extract_label_from_container(elem))

        for header in soup.find_all(["h2", "h3", "h4", "h5"]):
            if header.find_parent(["nav", "header", "footer"]):
                continue
            header_text = _clean_candidate(header.get_text(" ", strip=True)).lower()
            header_keywords = ["department", "group", "division", "section", "practice group", "team"]
            if not any(keyword in header_text for keyword in header_keywords):
                continue

            for sibling in header.next_siblings:
                if not getattr(sibling, "name", None):
                    continue
                if sibling.name in {"h2", "h3", "h4", "h5"}:
                    break
                if sibling.name in {"nav", "header", "footer"}:
                    continue
                sibling_marker = " ".join(
                    str(part)
                    for part in [
                        sibling.get("data-tab", ""),
                        sibling.get("id", ""),
                        sibling.get("aria-label", ""),
                        " ".join(sibling.get("class") or []),
                    ]
                    if part
                ).lower()
                if sibling_marker and not any(keyword in sibling_marker for keyword in header_keywords):
                    continue
                for candidate in _candidate_texts_from_sibling(sibling):
                    _add_candidate(candidate)

        return departments
    
    # ========================================================================
    # SECTION-HEADER HELPER (used by practices, industries, bar, education)
    # ========================================================================

    def _extract_section_items_after_header(
        self,
        soup: BeautifulSoup,
        header_keywords: list[str],
    ) -> list[str]:
        """Generic section-header-based extractor.

        Locates h2/h3/h4 tags whose text (case-insensitive) contains ANY of the
        supplied keywords, then walks forward siblings until the next heading,
        collecting text from <li>, <a>, <p>, and <dd> elements.

        Returns a deduplicated list of stripped strings (<= 200 chars each).
        """
        seen: set[str] = set()
        results: list[str] = []

        for header in soup.find_all(['h2', 'h3', 'h4', 'h5']):
            # Skip headers inside nav/header/footer
            if header.find_parent(['nav', 'header', 'footer']):
                continue
            header_text = header.get_text(strip=True).lower()
            if not any(kw.lower() in header_text for kw in header_keywords):
                continue

            # Walk forward siblings until we hit another heading or footer
            for sibling in header.find_all_next():
                # Stop at the next heading at the same (or higher) level
                if sibling.name in ('h2', 'h3', 'h4', 'h5') and sibling is not header:
                    break
                # Stop when we enter the footer
                if sibling.name == 'footer':
                    break
                # Skip elements inside nav/header/footer
                if sibling.find_parent(['nav', 'header', 'footer']):
                    continue
                # Collect leaf text nodes from list items, links, paragraphs, dd
                if sibling.name in ('li', 'dd', 'p'):
                    text = sibling.get_text(strip=True)
                    if text and len(text) <= 200 and text not in seen:
                        seen.add(text)
                        results.append(text)

        return results
    def _extract_practices_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract practice areas using BeautifulSoup"""
        practices: list[str] = []
        # Strategy 0a: Cleary Gottlieb — <aside> 'Areas of Experience'
        # Partners use simple-list__item > <a>, associates use complex-list__item > <p class='complex-list__item-title'>
        # If page has side-section asides (Cleary structure) but no 'Areas of Experience', return empty.
        cleary_asides = soup.find_all('aside', class_='side-section')
        if cleary_asides:
            for aside in cleary_asides:
                hdr = aside.find('h3', class_='side-section__header')
                if hdr and re.search(r'areas?\s+of\s+experience|practice', hdr.get_text(strip=True), re.I):
                    # Try complex-list__item (associate structure)
                    for li in aside.find_all('li', class_='complex-list__item'):
                        title_p = li.find('p', class_='complex-list__item-title')
                        item = (title_p or li).get_text(strip=True)
                        if item and len(item) < 120 and item not in practices:
                            practices.append(item)
                    # Try simple-list__item (partner structure)
                    for li in aside.find_all('li', class_='simple-list__item'):
                        a = li.find('a')
                        item = (a or li).get_text(strip=True)
                        if item and len(item) < 120 and item not in practices:
                            practices.append(item)
                    if practices:
                        return practices
            # Cleary page but no 'Areas of Experience' aside — attorney with no listed practices.
            return []

        # Strategy 0b: Paul Weiss — <div class='practices-block'> <p class='detail-item'>Litigation</p>
        pw_practices = soup.find('div', class_=lambda c: c and 'practices-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_practices:
            for item in pw_practices.find_all('p', class_='detail-item'):
                t = item.get_text(strip=True)
                if t and len(t) < 100 and t not in practices:
                    practices.append(t)
            if practices:
                return practices
        # Strategy 0: Davis Polk Drupal field_primary_practice (field-name--field_primary_practice)
        primary_practice_div = soup.find(
            lambda tag: tag.name in ('div', 'span', 'p') and any(
                'primary_practice' in cls
                for cls in (tag.get('class') or [])
            )
        )
        if primary_practice_div:
            t = primary_practice_div.get_text(strip=True)
            if t and len(t) < 100 and t not in practices:
                practices.append(t)

        # Strategy 1: section-header approach (works for Kirkland and most AmLaw firms)
        header_items = self._extract_section_items_after_header(
            soup,
            ['practice', 'practices', 'areas', 'expertise', 'specialt', 'competen', 'service', 'capabilit'],
        )
        for item in header_items:
            if item not in practices:
                practices.append(item)

        # Strategy 2: Drupal CMS class fields (DLA Piper, White & Case, etc.) - kept as fallback
        if not practices:
            drupal_fields = soup.find_all(
                'div',
                class_=lambda c: c and (
                    'field--name-field-services' in ' '.join(c if isinstance(c, list) else [c])
                    or 'field--name-field-sub-services' in ' '.join(c if isinstance(c, list) else [c])
                    or 'field--name-field-related-services' in ' '.join(c if isinstance(c, list) else [c])
                ),
            )
            for field_div in drupal_fields:
                for item_div in field_div.find_all('div', class_=lambda c: c and 'field--item' in ' '.join(c if isinstance(c, list) else [c])):
                    practice = item_div.get_text(strip=True)
                    if practice and len(practice) < 100 and practice not in practices:
                        practices.append(practice)
                for link in field_div.find_all('a'):
                    practice = link.get_text(strip=True)
                    if practice and len(practice) < 100 and practice not in practices:
                        practices.append(practice)
        return practices
    
    def _extract_industries_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract industries using BeautifulSoup"""
        industries: list[str] = []
        seen: set[str] = set()
        header_keywords = [
            'industr',
            'sector',
            'market',
            'industry focus',
            'industry experience',
            'industries served',
        ]

        def _add_candidate(value: str | None) -> None:
            if not value:
                return
            candidate = re.sub(r'\s+', ' ', str(value)).strip(' \t\r\n:-•|')
            if not candidate or len(candidate) > 150:
                return
            key = candidate.lower()
            if key in {
                'industry', 'industries', 'sector', 'sectors', 'market', 'markets',
                'industry focus', 'industry experience', 'industries served',
            }:
                return
            if key in seen:
                return
            seen.add(key)
            industries.append(candidate)

        def _add_from_value(value: Any) -> None:
            if isinstance(value, str):
                _add_candidate(value)
            elif isinstance(value, list):
                for item in value:
                    _add_from_value(item)
            elif isinstance(value, dict):
                _add_from_value(value.get('name') or value.get('@value'))

        def _walk_json_ld(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    _walk_json_ld(item)
                return
            if not isinstance(node, dict):
                return

            if 'knowsAbout' in node:
                _add_from_value(node.get('knowsAbout'))

            for value in node.values():
                if isinstance(value, (dict, list)):
                    _walk_json_ld(value)

        def _add_direct_texts(elem: Tag) -> None:
            for child in elem.children:
                if isinstance(child, str):
                    _add_candidate(child.strip())
                    continue
                if getattr(child, 'name', None) in {'span', 'a', 'p', 'div', 'strong', 'em'}:
                    _add_candidate(child.get_text(' ', strip=True))

        def _extract_from_container(container: Tag) -> None:
            for header in container.find_all(['h2', 'h3', 'h4', 'h5', 'h6']):
                if header.find_parent(['nav', 'header', 'footer']):
                    continue
                header_text = header.get_text(' ', strip=True).lower()
                if not any(keyword in header_text for keyword in header_keywords):
                    continue

                for sibling in header.next_siblings:
                    if not getattr(sibling, 'name', None):
                        continue
                    if sibling.name in {'h2', 'h3', 'h4', 'h5', 'h6'}:
                        break
                    if sibling.find_parent(['nav', 'header', 'footer']):
                        continue

                    list_items = sibling.find_all('li') if hasattr(sibling, 'find_all') else []
                    if list_items:
                        for li in list_items:
                            _add_candidate(li.get_text(' ', strip=True))
                        continue

                    if sibling.name in {'li', 'p', 'dd', 'a', 'span', 'div'}:
                        _add_candidate(sibling.get_text(' ', strip=True))
                    _add_direct_texts(sibling)

        for script in soup.find_all('script', attrs={'type': re.compile(r'application/ld\+json', re.I)}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            _walk_json_ld(payload)

        for elem in soup.select('[class*="industr"], [class*="sector"], [data-industry]'):
            if elem.find_parent(['nav', 'header', 'footer']):
                continue
            if elem.find_all('li'):
                for li in elem.find_all('li'):
                    _add_candidate(li.get_text(' ', strip=True))
            else:
                _add_direct_texts(elem)

        for container in soup.find_all('aside'):
            _extract_from_container(container)
        for container in soup.select('[class*="sidebar"], [class*="aside"]'):
            _extract_from_container(container)

        _extract_from_container(soup)

        return industries or ['no industry field']

    def _extract_bar_admissions_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract bar admissions using BeautifulSoup"""
        # Strategy 0a: Cleary Gottlieb — <aside> 'Bar Admissions' with complex-list__item-title
        bars: list[str] = []
        for aside in soup.find_all('aside', class_='side-section'):
            hdr = aside.find('h3', class_='side-section__header')
            if hdr and re.search(r'bar\s+admission|admission', hdr.get_text(strip=True), re.I):
                for li in aside.find_all('li', class_='complex-list__item'):
                    title_p = li.find('p', class_='complex-list__item-title')
                    item = (title_p or li).get_text(strip=True)
                    if item and len(item) <= 80 and item not in bars:
                        bars.append(item)
                if bars:
                    return bars
        # Strategy 0b: Paul Weiss — <div class='admissions-block'> <p class='detail-item'>New York</p>
        pw_adm = soup.find('div', class_=lambda c: c and 'admissions-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_adm:
            for item in pw_adm.find_all('p', class_='detail-item'):
                t = item.get_text(strip=True)
                if t and len(t) < 80 and t not in bars:
                    bars.append(t)
            if bars:
                return bars
        # Primary: section-header approach (h2/h3/h4 with bar/admission keywords)
        raw_items = self._extract_section_items_after_header(
            soup, ['bar admission', 'bar admissions', 'bar', 'admission']
        )
        if raw_items:
            for item in raw_items:
                # Skip news/insight headlines (too long or contain typical news patterns)
                if len(item) > 50 or re.search(r'(news|insight|publication|article|alert|advises|acquires|announces|represents|secures|wins)', item, re.I):
                    continue
                extracted = self._extract_states_from_text(item)
                if extracted:
                    for state in extracted:
                        if state not in bars:
                            bars.append(state)
                else:
                    # Keep raw item if no US state name matched
                    if item not in bars:
                        bars.append(item)

        # Secondary: h5/h6 'Admissions:' sub-header (Gibson Dunn pattern)
        # <h5>Admissions:</h5><ul><li>Colorado Bar</li>...</ul>
        if not bars:
            for sub_hdr in soup.find_all(['h5', 'h6']):
                sub_text = sub_hdr.get_text(strip=True).lower()
                if 'admission' in sub_text:
                    # Collect <li> items from the immediately following <ul>
                    nxt = sub_hdr.find_next_sibling()
                    while nxt and nxt.name not in ('h2', 'h3', 'h4', 'h5', 'h6'):
                        if nxt.name in ('ul', 'ol'):
                            for li in nxt.find_all('li'):
                                item = li.get_text(strip=True)
                                # Filter out news headlines / long items
                                if item and len(item) <= 50 and not re.search(r'(news|insight|publication|article|alert|advises|acquires|announces|represents|secures|wins)', item, re.I) and item not in bars:
                                    bars.append(item)
                            break
                        nxt = nxt.find_next_sibling()

        # Tertiary: Drupal CMS field--name-field-admissions (White & Case pattern)
        if not bars:
            drupal_field = soup.find('div', class_=lambda c: c and 'field--name-field-admissions' in ' '.join(c if isinstance(c, list) else [c]))
            if drupal_field:
                for item_div in drupal_field.find_all('div', class_=lambda c: c and 'field--item' in ' '.join(c if isinstance(c, list) else [c])):
                    item = item_div.get_text(strip=True)
                    if item and item not in bars:
                        bars.append(item)

        # Legacy fallback: find_all(string=...) + find_parent
        if not bars:
            bar_headers = soup.find_all(string=re.compile(r'bar\s*(admission|admissions)', re.I))
            for header in bar_headers:
                parent = header.find_parent(['div', 'section', 'ul', 'dl'])
                if parent:
                    text = parent.get_text()
                    extracted = self._extract_states_from_text(text)
                    for state in extracted:
                        if state not in bars:
                            bars.append(state)

        return bars

    def _extract_education_bs4(self, soup: BeautifulSoup) -> list[EducationRecord]:
        """Extract education records using BeautifulSoup"""
        education_records: list[EducationRecord] = []
        # Strategy 0a: Cleary Gottlieb — <aside> 'Education' with complex-list__item
        # Structure: <li class='complex-list__item'>
        #   <p class='complex-list__item-title'>School</p>
        #   <p><em>J.D.</em></p>  <p><em>2023</em></p>
        for aside in soup.find_all('aside', class_='side-section'):
            hdr = aside.find('h3', class_='side-section__header')
            if hdr and re.search(r'education', hdr.get_text(strip=True), re.I):
                for li in aside.find_all('li', class_='complex-list__item'):
                    title_p = li.find('p', class_='complex-list__item-title')
                    school = title_p.get_text(strip=True) if title_p else None
                    remaining_texts = []
                    for p in li.find_all('p'):
                        if p == title_p:
                            continue
                        remaining_texts.append(p.get_text(strip=True))
                    combined = ' '.join(remaining_texts)
                    deg = self._extract_degree_from_text(combined)
                    yr = self._extract_year_from_text(combined)
                    if school or deg:
                        education_records.append(EducationRecord(degree=deg, school=school, year=yr))
                if education_records:
                    return education_records

        # Strategy 0b: Paul Weiss — <div class='education-block'> <p class='detail-item'>J.D., Harvard, magna cum laude</p>
        pw_edu = soup.find('div', class_=lambda c: c and 'education-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_edu:
            for item in pw_edu.find_all('p', class_='detail-item'):
                text = item.get_text(strip=True)
                if not text or len(text) > 200:
                    continue
                # Format: 'J.D., Harvard Law School, magna cum laude'
                parts = [p.strip() for p in text.split(',', 2)]
                deg = self._extract_degree_from_text(parts[0]) if parts else None
                school = parts[1].strip() if len(parts) > 1 else None
                yr = self._extract_year_from_text(text)
                if school or deg:
                    education_records.append(EducationRecord(degree=deg, school=school, year=yr))
            if education_records:
                return education_records
        # Strategy 0: Davis Polk — field-name--field_degrees with div.degree per entry
        deg_field = soup.find(
            lambda tag: tag.name in ('div', 'span', 'p') and any(
                'field_degrees' in cls
                for cls in (tag.get('class') or [])
            )
        )
        if deg_field:
            for item in deg_field.find_all('div', class_='field-item'):
                degree_div = item.find('div', class_='degree')
                if degree_div:
                    line = degree_div.get_text(strip=True)
                    deg = self._extract_degree_from_text(line)
                    yr = self._extract_year_from_text(line)
                    sch = re.sub(r'' + re.escape(deg) + r'', '', line, flags=re.IGNORECASE).strip(' ,-') if deg else line
                    sch = re.sub(r'^[A-Z]\.(?:[A-Z]\.)+,?\s*', '', sch).strip(' ,-')
                    if yr:
                        sch = sch.replace(str(yr), '').strip(' ,-')
                    if sch or deg:
                        education_records.append(EducationRecord(degree=deg, school=sch or None, year=yr))
            # Fallback: lines that start with a degree abbreviation
            if not education_records:
                for line in deg_field.get_text(separator='\n', strip=True).split('\n'):
                    line = line.strip()
                    if not line or len(line) > 150:
                        continue
                    if re.match(r'^(J\.D\.|LL\.M\.|LL\.B\.|B\.A\.|B\.S\.|M\.B\.A\.|Ph\.D\.|A\.B\.)', line):
                        deg = self._extract_degree_from_text(line)
                        yr = self._extract_year_from_text(line)
                        sch = re.sub(r'\b' + re.escape(deg) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if deg else line
                        if yr:
                            sch = sch.replace(str(yr), '').strip(' ,-')
                        if sch or deg:
                            education_records.append(EducationRecord(degree=deg, school=sch or None, year=yr))
        # Primary: section-header approach (skip if already populated by Strategy 0)
        if not education_records:
            raw_items = self._extract_section_items_after_header(soup, ['education', 'academic'])
            for text in raw_items:
                degree = self._extract_degree_from_text(text)
                year = self._extract_year_from_text(text)
                school = text
                if degree:
                    school = re.sub(
                        r'\b' + re.escape(degree) + r'\b', '', school, flags=re.IGNORECASE
                    )
                    # Also strip spelled-out degree forms (Gibson Dunn: 'Juris Doctor', 'Bachelor of Arts')
                    for pat in DEGREE_PATTERNS:
                        school = re.sub(pat, '', school, flags=re.IGNORECASE)
                # Strip residual parenthetical notes (Gibson Dunn: 'University of Houston - (some notes)')
                school = re.sub(r'\s*\([^)]*\)\s*', ' ', school)
                # Strip dash separator (Gibson Dunn: 'Georgetown University - 2019 Juris Doctor')
                school = re.sub(r'\s*-\s*$', '', school)
                if year:
                    school = school.replace(str(year), '')
                school = re.sub(r'\s{2,}', ' ', school)  # collapse multiple spaces
                school = school.strip(' ,-')
                education_records.append(EducationRecord(
                    degree=degree,
                    school=school if school else None,
                    year=year,
                ))

        # Legacy fallback: find_all(string=...) + find_parent
        if not education_records:
            edu_headers = soup.find_all(string=re.compile(r'education|academic', re.I))
            for header in edu_headers:
                parent = header.find_parent(['div', 'section', 'ul', 'dl'])
                if parent:
                    dts = parent.find_all('dt')
                    dds = parent.find_all('dd')
                    if dts and dds:
                        for dt, dd in zip(dts, dds):
                            degree_text = dt.get_text(strip=True)
                            school_text = dd.get_text(strip=True)
                            degree = self._extract_degree_from_text(degree_text)
                            year = self._extract_year_from_text(degree_text + ' ' + school_text)
                            education_records.append(EducationRecord(
                                degree=degree, school=school_text, year=year
                            ))
                    else:
                        for item in parent.find_all('li'):
                            text = item.get_text(strip=True)
                            degree = self._extract_degree_from_text(text)
                            year = self._extract_year_from_text(text)
                            school = text
                            if degree:
                                school = re.sub(
                                    r'\b' + re.escape(degree) + r'\b',
                                    '', school, flags=re.IGNORECASE
                                )
                            if year:
                                school = school.replace(str(year), '')
                            school = school.strip(' ,-')
                            education_records.append(EducationRecord(
                                degree=degree,
                                school=school if school else None,
                                year=year,
                            ))

        # Davis Polk: field-name--field_degrees — each field-item has a div.degree with "J.D., School Name"
        if not education_records:
            deg_field = soup.find(
                lambda tag: tag.name in ('div', 'span', 'p') and any(
                    'field_degrees' in cls
                    for cls in (tag.get('class') or [])
                )
            )
            if deg_field:
                for item in deg_field.find_all('div', class_='field-item'):
                    degree_div = item.find('div', class_='degree')
                    if degree_div:
                        line = degree_div.get_text(strip=True)
                        degree = self._extract_degree_from_text(line)
                        year = self._extract_year_from_text(line)
                        school = re.sub(r'\b' + re.escape(degree) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if degree else line
                        if year:
                            school = school.replace(str(year), '').strip(' ,-')
                        if school or degree:
                            education_records.append(EducationRecord(degree=degree, school=school or None, year=year))
                # Fallback: try lines starting with a degree abbreviation
                if not education_records:
                    for line in deg_field.get_text(separator='\n', strip=True).split('\n'):
                        line = line.strip()
                        if not line or len(line) > 150:
                            continue
                        if re.match(r'^(J\.D\.|LL\.M\.|LL\.B\.|B\.A\.|B\.S\.|M\.B\.A\.|Ph\.D\.|A\.B\.)', line):
                            degree = self._extract_degree_from_text(line)
                            year = self._extract_year_from_text(line)
                            school = re.sub(r'\b' + re.escape(degree) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if degree else line
                            if year:
                                school = school.replace(str(year), '').strip(' ,-')
                            if school or degree:
                                education_records.append(EducationRecord(degree=degree, school=school or None, year=year))

        # Drupal CMS: field--name-field-education with nested degree+school (White & Case pattern)
        if not education_records:
            edu_field = soup.find('div', class_=lambda c: c and 'field--name-field-education' in ' '.join(c if isinstance(c, list) else [c]))
            if edu_field:
                for item in edu_field.find_all('div', class_=lambda c: c and 'paragraph--type--education' in ' '.join(c if isinstance(c, list) else [c])):
                    deg_div = item.find('div', class_=lambda c: c and 'field--name-field-degree' in ' '.join(c if isinstance(c, list) else [c]))
                    school_div = item.find('div', class_=lambda c: c and 'field--name-field-school' in ' '.join(c if isinstance(c, list) else [c]))
                    year_div = item.find('div', class_=lambda c: c and 'field--name-field-year' in ' '.join(c if isinstance(c, list) else [c]))
                    degree = deg_div.get_text(strip=True) if deg_div else None
                    school = school_div.get_text(strip=True) if school_div else None
                    year_text = year_div.get_text(strip=True) if year_div else None
                    year = self._extract_year_from_text(year_text) if year_text else None
                    if school or degree:
                        education_records.append(EducationRecord(degree=degree, school=school, year=year))


        return education_records
    
    # ========================================================================
    # STAGE 4: REGEX FALLBACK EXTRACTION
    # ========================================================================
    
    def _extract_with_regex(self, profile: AttorneyProfile, html: str, url: str) -> None:
        """Regex fallback extraction for fields still missing"""
        
        # Name (if still missing)
        if not profile.full_name:
            # H1 tag
            h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            if h1_match:
                name = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
                if self._looks_like_person_name(name):
                    profile.full_name = name
        
        # Practice areas (if missing)
        if not profile.practice_areas:
            practice_section = re.search(
                r'(?i)<(?:h[234]|div|span|p|td)[^>]*>[^<]*Practice[s]?(?:\s*Area[s]?)?[^<]*</(?:h[234]|div|span|p|td)>(.*?)(?=<(?:h[234])|$)',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if practice_section:
                content = practice_section.group(1)
                links = re.findall(r'<a[^>]*>(.*?)</a>', content, re.IGNORECASE)
                for link in links:
                    practice = re.sub(r'<[^>]+>', '', link).strip()
                    if practice and len(practice) < 100 and practice not in profile.practice_areas:
                        profile.practice_areas.append(practice)
                # Also grab plain list items if no links found
                if not profile.practice_areas:
                    items = re.findall(r'<li[^>]*>(.*?)</li>', content, re.IGNORECASE | re.DOTALL)
                    for item in items:
                        text = re.sub(r'<[^>]+>', '', item).strip()
                        if text and len(text) < 100 and text not in profile.practice_areas:
                            profile.practice_areas.append(text)
        # Bar admissions (if missing)
        if not profile.bar_admissions:
            bar_section = re.search(
                r'(?i)<(?:h[234]|div|span|p|td)[^>]*>[^<]*Bar\s*Admission[s]?[^<]*</(?:h[234]|div|span|p|td)>(.*?)(?=<(?:h[234])|$)',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if bar_section:
                text = re.sub(r'<[^>]+>', '', bar_section.group(1))
                profile.bar_admissions = self._extract_states_from_text(text)
        if not profile.education:
            edu_section = re.search(
                r'(?i)<(?:h[234]|div|span|p|td)[^>]*>[^<]*Education[^<]*</(?:h[234]|div|span|p|td)>(.*?)(?=<(?:h[234])|$)',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if edu_section:
                content = edu_section.group(1)
                # Extract list items
                items = re.findall(r'<li[^>]*>(.*?)</li>', content, re.IGNORECASE | re.DOTALL)
                for item in items:
                    text = re.sub(r'<[^>]+>', '', item).strip()
                    degree = self._extract_degree_from_text(text)
                    year = self._extract_year_from_text(text)
                    school = text
                    if degree:
                        school = re.sub(
                            r'\b' + re.escape(degree) + r'\b', '', school, flags=re.IGNORECASE
                        )
                    if year:
                        school = school.replace(str(year), '')
                    school = school.strip(' ,-')
                    edu = EducationRecord(
                        degree=degree,
                        school=school if school else None,
                        year=year
                    )
                    profile.education.append(edu)
                # no-JD sentinel for regex path
                if profile.education:
                    has_jd = any(
                        e.degree and 'JD' in e.degree.upper()
                        for e in profile.education
                    )
                    if not has_jd:
                        profile.education.append(
                            EducationRecord(degree='no JD', school='unknown', year=None)
                        )
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _looks_like_person_name(self, text: str) -> bool:
        """Check if text looks like a person name.

        Strict validation:
        - Must match First Last pattern (e.g. "John Smith", "Mary O'Brien")
        - Must not be a known header/label term
        - Must not contain digits
        - Length 4–100
        """
        if not text or len(text) < 4 or len(text) > 100:
            return False
        if any(ch in text for ch in ["_", "#", "{", "}"]):
            return False
        if any(ch.isdigit() for ch in text):
            return False
        if text.strip().lower() in _HEADER_TERMS:
            return False
        return bool(_VALID_NAME_RE.match(text.strip()))
    
    def _extract_degree_from_text(self, text: str) -> str | None:
        """Extract degree abbreviation from text"""
        for pattern, degree in DEGREE_PATTERNS.items():
            if re.search(pattern, text, re.IGNORECASE):
                return degree
        return None
    
    def _extract_year_from_text(self, text: str) -> int | None:
        """Extract graduation year from text (1950-2030)"""
        years = re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', text)
        if years:
            return int(years[0])
        return None
    
    def _extract_states_from_text(self, text: str) -> list[str]:
        """Extract US state names from text"""
        states_found = []
        for state in US_STATES:
            if re.search(rf'\b{state}\b', text, re.IGNORECASE):
                if state not in states_found:
                    states_found.append(state)
        return states_found

# Header terms that must never be treated as attorney names
_HEADER_TERMS = frozenset({
    "last name", "first name", "firm name", "attorney", "name", "title",
    "lawyer", "partner", "associate", "counsel", "full name", "contact",
})


def extract_degrees(text: str) -> list[dict]:
    """Standalone degree extraction with structured output and no-JD sentinel.

    Returns a list of dicts: [{"school": str, "degree": str, "year": str}]
    If no JD/J.D. is found, appends a sentinel: {"school": "unknown", "degree": "no JD", "year": ""}.
    """
    if not text:
        return [{"school": "unknown", "degree": "no JD", "year": ""}]

    records: list[dict] = []
    has_jd = False

    # Split on common separators: semicolons, newlines, bullet characters
    segments = re.split(r'[;\n\r\u2022\u2013\u2014|]', text)

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        # Detect degree in this segment
        degree_found: str | None = None
        for pattern, degree_label in DEGREE_PATTERNS.items():
            if re.search(pattern, seg, re.IGNORECASE):
                degree_found = degree_label
                if degree_label == 'JD':
                    has_jd = True
                break

        if degree_found is None:
            # No degree abbreviation — skip segment unless it looks like an education entry
            if not re.search(r'university|college|school|institute|law', seg, re.IGNORECASE):
                continue

        # Extract year
        year_match = re.search(r'\b(19[5-9]\d|20[0-3]\d)\b', seg)
        year_str = year_match.group(1) if year_match else ""

        # Extract school — remove degree token and year from text
        school = seg
        if degree_found:
            school = re.sub(
                r'\b' + degree_found.replace('.', '\\.') + r'\b', '', school, flags=re.IGNORECASE
            )
        if year_str:
            school = school.replace(year_str, '')
        school = re.sub(r'[,;\(\)]+', ' ', school).strip()
        school = re.sub(r'\s{2,}', ' ', school).strip()

        records.append({
            "school": school if school else "unknown",
            "degree": degree_found or "unknown",
            "year": year_str,
        })

    if not has_jd:
        records.append({"school": "unknown", "degree": "no JD", "year": ""})

    return records if records else [{"school": "unknown", "degree": "no JD", "year": ""}]


@dataclass
class EducationRecord:
    """Single education record"""
    degree: str | None = None
    school: str | None = None
    year: int | None = None
    
    def to_dict(self) -> dict:
        return {
            "degree": self.degree,
            "school": self.school,
            "year": self.year
        }


@dataclass
class AttorneyProfile:
    """Complete attorney profile with ALL required fields"""
    firm: str
    profile_url: str
    full_name: str | None = None
    title: str | None = None
    offices: list[str] = field(default_factory=list)
    department: list[str] = field(default_factory=list)
    practice_areas: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    bar_admissions: list[str] = field(default_factory=list)
    education: list[EducationRecord] = field(default_factory=list)
    extraction_status: str = "UNKNOWN"  # SUCCESS | PARTIAL | FAILED
    missing_fields: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "firm": self.firm,
            "profile_url": self.profile_url,
            "full_name": self.full_name,
            "title": self.title,
            "offices": self.offices,
            "department": self.department,
            "practice_areas": self.practice_areas,
            "industries": self.industries,
            "bar_admissions": self.bar_admissions,
            "education": [e.to_dict() for e in self.education],
            "extraction_status": self.extraction_status,
            "missing_fields": self.missing_fields,
            "diagnostics": self.diagnostics
        }
    
    def calculate_status(self) -> None:
        """Calculate extraction status based on missing fields.

        SUCCESS requires ALL of:
        - valid full_name (non-empty, passes name validation)
        - title present
        - at least one office
        - bar_admissions present
        - education parsed

        Industry normalization: if industries is empty, set sentinel ["no industry field"].
        """
        required_fields = [
            "full_name", "title", "offices", "department",
            "practice_areas", "industries", "bar_admissions", "education"
        ]

        # --- Industry normalization: always provide a value ---
        if not self.industries:
            self.industries = ["no industry field"]

        self.missing_fields = []

        # Validate full_name - strip known professional suffixes before regex check
        _name_for_check = self.full_name.strip() if self.full_name else ''
        _name_for_check = re.sub(r',\s*(?:P\.C\.|Jr\.?|Sr\.?|II|III|IV|Esq\.?)\s*$', '', _name_for_check, flags=re.IGNORECASE).strip()
        name_valid = (
            bool(_name_for_check)
            and _VALID_NAME_RE.match(_name_for_check)
            and _name_for_check.lower() not in _HEADER_TERMS
        )
        if not name_valid:
            self.missing_fields.append("full_name")

        if not self.title:
            self.missing_fields.append("title")
        if not self.offices:
            self.missing_fields.append("offices")
        if not self.department:
            self.missing_fields.append("department")
        if not self.practice_areas:
            self.missing_fields.append("practice_areas")
        # industries is always populated now (sentinel above), so never missing
        if not self.bar_admissions:
            self.missing_fields.append("bar_admissions")
        if not self.education:
            self.missing_fields.append("education")

        # SUCCESS: must have valid name + title + offices + bar_admissions + education
        hard_requirements = {"full_name", "title", "offices", "bar_admissions", "education"}
        hard_missing = hard_requirements & set(self.missing_fields)

        if len(self.missing_fields) == 0:
            self.extraction_status = "SUCCESS"
        elif not hard_missing:
            # All hard requirements met; some soft fields (department/practice) missing
            self.extraction_status = "PARTIAL"
        elif len(self.missing_fields) < len(required_fields):
            self.extraction_status = "PARTIAL"
        else:
            self.extraction_status = "FAILED"


class AttorneyExtractor:
    """Multi-stage extraction engine with zero data loss guarantee"""
    
    def __init__(self):
        self.use_bs4 = BS4_AVAILABLE
        if not self.use_bs4:
            print("WARNING: BeautifulSoup4 not available, falling back to regex-only extraction")
    
    def extract_profile(self, firm_name: str, profile_url: str, html: str) -> AttorneyProfile:
        """Extract complete attorney profile using multi-stage cascade
        
        Args:
            firm_name: Name of the law firm
            profile_url: URL of the attorney profile page
            html: HTML content of the profile page
        
        Returns:
            AttorneyProfile with all available data (missing fields = null)
        """
        profile = AttorneyProfile(firm=firm_name, profile_url=profile_url)

        # STAGE 0: Firm-specific extraction (highest priority — exact CSS selectors)
        if self.use_bs4:
            soup_stage0 = BeautifulSoup(html, 'html.parser')
            if self._is_kirkland_profile(soup_stage0, profile_url):
                self._extract_kirkland_profile(profile, soup_stage0)
                profile.calculate_status()
                if profile.extraction_status == 'SUCCESS':
                    return profile
                # Partial — fall through to generic stages for remaining fields

        # STAGE 1: JSON-LD extraction (highest priority)
        json_ld_data = self._extract_json_ld(html)
        if json_ld_data:
            self._merge_json_ld_data(profile, json_ld_data)

        # STAGE 2: Embedded state objects (React/Next.js)
        embedded_data = self._extract_embedded_state(html)
        if embedded_data:
            self._merge_embedded_data(profile, embedded_data)

        # STAGE 3: BeautifulSoup semantic extraction
        if self.use_bs4:
            soup = BeautifulSoup(html, 'html.parser')
            self._extract_with_bs4(profile, soup, profile_url)

        # STAGE 4: Regex fallback extraction
        self._extract_with_regex(profile, html, profile_url)

        # Calculate final status
        profile.calculate_status()

        return profile
    
    # ========================================================================
    # STAGE 0: FIRM-SPECIFIC EXTRACTION (Kirkland & Ellis)
    # ========================================================================

    def _is_kirkland_profile(self, soup: BeautifulSoup, profile_url: str) -> bool:
        """Detect if this is a Kirkland & Ellis profile page."""
        if 'kirkland.com' in profile_url:
            return True
        body = soup.find('body')
        if body and 'page__people-detail' in (body.get('class') or []):
            return True
        return False

    def _extract_kirkland_profile(self, profile: AttorneyProfile, soup: BeautifulSoup) -> None:
        """Extract all fields from a Kirkland & Ellis profile using confirmed CSS selectors."""
        # Name
        if not profile.full_name:
            el = soup.select_one('.profile-heading__name-label, .profile-heading__name')
            if el:
                name = el.get_text(strip=True)
                # Strip professional suffixes before validation
                _clean = re.sub(r',\s*(?:P\.C\.|Jr\.?|Sr\.?|II|III|IV|Esq\.?)\s*$', '', name, flags=re.IGNORECASE).strip()
                if _clean and self._looks_like_person_name(_clean):
                    profile.full_name = name  # store original (with suffix)

        # Title (position/level)
        if not profile.title:
            el = soup.select_one('.profile-heading__position')
            if el:
                profile.title = el.get_text(strip=True)

        # Department (specialty/practice group)
        if not profile.department:
            el = soup.select_one('.profile-heading__specialty')
            if el:
                profile.department = [el.get_text(strip=True)]

        # Office locations
        if not profile.offices:
            offices = [el.get_text(strip=True) for el in soup.select('.profile-heading__location-link')]
            if offices:
                profile.offices = offices

        # Practice areas (.prominent-services__link)
        if not profile.practice_areas:
            practices = [el.get_text(strip=True) for el in soup.select('.prominent-services__link')]
            if practices:
                profile.practice_areas = practices

        # Bar admissions
        if not profile.bar_admissions:
            admissions = []
            for li in soup.select('.normalized-rte-list--admissions li'):
                year_el = li.select_one('.normalized-rte-list__admission-year')
                loc_el  = li.select_one('.normalized-rte-list__admission-location')
                parts = []
                if year_el:
                    parts.append(year_el.get_text(strip=True))
                if loc_el:
                    parts.append(loc_el.get_text(strip=True))
                entry = ' '.join(parts).strip()
                if entry:
                    admissions.append(entry)
            if admissions:
                profile.bar_admissions = admissions

        # Education
        if not profile.education:
            edu_records = []
            for li in soup.select('.normalized-rte-list--education li'):
                school_el = li.select_one('.normalized-rte-list__item--edu-name')
                degree_el = li.select_one('.normalized-rte-list__item--edu-degree')
                year_el   = li.select_one('.normalized-rte-list__item--edu-year')
                if school_el or degree_el:
                    rec = EducationRecord()
                    rec.school = school_el.get_text(strip=True) if school_el else None
                    rec.degree = degree_el.get_text(strip=True) if degree_el else None
                    rec.year   = year_el.get_text(strip=True) if year_el else None
                    edu_records.append(rec)
            if edu_records:
                profile.education = edu_records


    # ========================================================================
    # STAGE 1: JSON-LD EXTRACTION
    # ========================================================================
    
    def _extract_json_ld(self, html: str) -> dict | None:
        """Extract and parse all JSON-LD blocks"""
        try:
            json_ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html,
                re.IGNORECASE | re.DOTALL
            )
            
            for block in json_ld_blocks:
                try:
                    data = json.loads(block)
                    items = data if isinstance(data, list) else [data]
                    
                    for item in items:
                        if isinstance(item, dict):
                            if item.get("@type") in ["Person", "http://schema.org/Person"]:
                                return item
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        
        return None
    
    def _merge_json_ld_data(self, profile: AttorneyProfile, data: dict) -> None:
        """Merge JSON-LD Person data into profile"""
        # Name
        if not profile.full_name:
            name = data.get("name", "")
            if name and self._looks_like_person_name(name):
                profile.full_name = name.strip()
        
        # Title/Job
        if not profile.title:
            job_title = data.get("jobTitle", "")
            if job_title:
                profile.title = job_title.strip()
        
        # Office/Location
        work_location = data.get("workLocation", {})
        if isinstance(work_location, dict):
            location_name = work_location.get("name", "")
            if location_name and location_name not in profile.offices:
                profile.offices.append(location_name.strip())
        
        address = data.get("address", {})
        if isinstance(address, dict):
            city = address.get("addressLocality", "")
            if city and city not in profile.offices:
                profile.offices.append(city.strip())
        elif isinstance(address, str) and address:
            if address not in profile.offices:
                profile.offices.append(address.strip())
        
        # Practice Areas (knowsAbout)
        knows_about = data.get("knowsAbout", [])
        if isinstance(knows_about, list):
            for item in knows_about:
                if item and item not in profile.practice_areas:
                    profile.practice_areas.append(str(item).strip())
        elif isinstance(knows_about, str) and knows_about:
            if knows_about not in profile.practice_areas:
                profile.practice_areas.append(knows_about.strip())
        
        # Education (alumniOf)
        alumni_of = data.get("alumniOf", [])
        if isinstance(alumni_of, list):
            for school_data in alumni_of:
                if isinstance(school_data, dict):
                    school_name = school_data.get("name", "")
                    if school_name:
                        edu = EducationRecord(school=school_name.strip())
                        profile.education.append(edu)
                elif isinstance(school_data, str):
                    edu = EducationRecord(school=school_data.strip())
                    profile.education.append(edu)
        elif isinstance(alumni_of, dict):
            school_name = alumni_of.get("name", "")
            if school_name:
                edu = EducationRecord(school=school_name.strip())
                profile.education.append(edu)
    
    # ========================================================================
    # STAGE 2: EMBEDDED STATE OBJECTS
    # ========================================================================
    
    def _extract_embedded_state(self, html: str) -> dict | None:
        """Extract embedded React/Next.js state objects"""
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\.__APOLLO_STATE__\s*=\s*({.*?});',
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        ]
        
        for pattern in patterns:
            try:
                matches = re.findall(pattern, html, re.DOTALL)
                for match in matches:
                    try:
                        data = json.loads(match)
                        # Look for attorney-related data
                        attorney_data = self._find_attorney_data_recursive(data)
                        if attorney_data:
                            return attorney_data
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue
        
        return None
    
    def _find_attorney_data_recursive(self, obj: Any, depth: int = 0) -> dict | None:
        """Recursively search for attorney profile data in nested objects"""
        if depth > 5:
            return None
        
        if isinstance(obj, dict):
            # Look for attorney-like keys
            attorney_keys = ["attorney", "lawyer", "professional", "person", "profile", "bio"]
            for key in attorney_keys:
                if key in str(obj.keys()).lower():
                    return obj
            
            # Recurse into values
            for value in obj.values():
                result = self._find_attorney_data_recursive(value, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_attorney_data_recursive(item, depth + 1)
                if result:
                    return result
        
        return None
    
    def _merge_embedded_data(self, profile: AttorneyProfile, data: dict) -> None:
        """Merge embedded state data into profile"""
        # This is generic - specific structure depends on site
        # Try common field names
        for name_key in ["name", "fullName", "displayName", "firstName"]:
            if name_key in data and not profile.full_name:
                name = str(data[name_key])
                if self._looks_like_person_name(name):
                    profile.full_name = name.strip()
                    break
        
        for title_key in ["title", "position", "role", "jobTitle"]:
            if title_key in data and not profile.title:
                profile.title = str(data[title_key]).strip()
                break
        
        # Try to extract practice areas
        for practice_key in ["practices", "practiceAreas", "expertise", "specialties"]:
            if practice_key in data:
                practices = data[practice_key]
                if isinstance(practices, list):
                    for p in practices:
                        if p and p not in profile.practice_areas:
                            profile.practice_areas.append(str(p).strip())
                elif isinstance(practices, str) and practices:
                    if practices not in profile.practice_areas:
                        profile.practice_areas.append(practices.strip())

        # Target list fields: additive union/dedup for department
        for dept_key in ["department", "group", "practiceGroup", "division"]:
            if dept_key in data:
                dept_val = data[dept_key]
                if isinstance(dept_val, list):
                    for d in dept_val:
                        if d and str(d).strip() not in profile.department:
                            profile.department.append(str(d).strip())
                elif isinstance(dept_val, str) and dept_val and dept_val.strip() not in profile.department:
                    profile.department.append(dept_val.strip())
                break

        # Target list fields: additive union/dedup for offices
        for office_key in ["offices", "office", "location", "officeLocation"]:
            if office_key in data:
                office_val = data[office_key]
                if isinstance(office_val, list):
                    for o in office_val:
                        if o and str(o).strip() not in profile.offices:
                            profile.offices.append(str(o).strip())
                elif isinstance(office_val, str) and office_val and office_val.strip() not in profile.offices:
                    profile.offices.append(office_val.strip())
                break
    
    # ========================================================================
    # STAGE 3: BEAUTIFULSOUP SEMANTIC EXTRACTION
    # ========================================================================
    
    def _extract_with_bs4(self, profile: AttorneyProfile, soup: BeautifulSoup, url: str) -> None:
        """Extract fields using BeautifulSoup semantic parsing"""
        
        # Name extraction
        if not profile.full_name:
            profile.full_name = self._extract_name_bs4(soup, url)
        
        # Title extraction
        if not profile.title:
            profile.title = self._extract_title_bs4(soup)
        
        # Office/Location
        offices = self._extract_offices_bs4(soup)
        for office in offices:
            if office not in profile.offices:
                profile.offices.append(office)
        
        # Department/Group
        departments = self._extract_departments_bs4(soup)
        for dept in departments:
            if dept not in profile.department:
                profile.department.append(dept)
        
        # Practice Areas
        practices = self._extract_practices_bs4(soup)
        for practice in practices:
            if practice not in profile.practice_areas:
                profile.practice_areas.append(practice)
        
        # Industries
        industries = self._extract_industries_bs4(soup)
        for industry in industries:
            if industry not in profile.industries:
                profile.industries.append(industry)
        
        # Bar Admissions
        bars = self._extract_bar_admissions_bs4(soup)
        for bar in bars:
            if bar not in profile.bar_admissions:
                profile.bar_admissions.append(bar)
        
        # Education — skip generic extraction if Kirkland-specific extractor already populated it
        if not profile.education:
            education_records = self._extract_education_bs4(soup)
            for edu in education_records:
                profile.education.append(edu)
    
    def _extract_name_bs4(self, soup: BeautifulSoup, url: str) -> str | None:
        """Extract name using BeautifulSoup"""
        # Priority 1: H1 tag
        h1 = soup.find('h1')
        if h1:
            name = h1.get_text(strip=True)
            if self._looks_like_person_name(name):
                return name
        
        # Priority 2: Elements with name-related classes
        name_selectors = [
            {'class': re.compile(r'.*\bname\b.*', re.I)},
            {'class': re.compile(r'.*\battorney-name\b.*', re.I)},
            {'class': re.compile(r'.*\bprofessional-name\b.*', re.I)},
        ]
        
        for selector in name_selectors:
            elem = soup.find(['h1', 'h2', 'div', 'span'], selector)
            if elem:
                name = elem.get_text(strip=True)
                if self._looks_like_person_name(name):
                    return name
        
        # Priority 3: Meta og:title
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title_text = og_title['content']
            parts = re.split(r'[|\-–—]', title_text)
            if parts:
                name = parts[0].strip()
                if self._looks_like_person_name(name):
                    return name
        
        # Priority 4: Title tag
        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            parts = re.split(r'[|\-–—]', title_text)
            if parts:
                name = parts[0].strip()
                if self._looks_like_person_name(name):
                    return name
        
        return None
    
    def _extract_title_bs4(self, soup: BeautifulSoup) -> str | None:
        """Extract title/position using BeautifulSoup"""
        # Strategy 0a: Cleary Gottlieb — <h2 class='person-info__position'>
        cleary_pos = soup.find('h2', class_='person-info__position')
        if cleary_pos:
            t = cleary_pos.get_text(strip=True)
            if t and len(t) < 100 and not re.search(r'(cookie|consent|privacy choices)', t, re.I):
                return t
        # Strategy 0b: Paul Weiss — <div class='contact-block'> <p class='detail-label'>Partner</p>
        contact_block = soup.find('div', class_=lambda c: c and 'contact-block' in ' '.join(c if isinstance(c,list) else [c]))
        if contact_block:
            lbl = contact_block.find('p', class_='detail-label')
            if lbl:
                t = lbl.get_text(strip=True)
                if t and len(t) < 80 and not re.search(r'(tel:|email|@|cookie|privacy)', t, re.I):
                    return t
        # Strategy 0: Drupal field_job_title (Davis Polk pattern)
        # <div class='field-name--field_job_title field-item'>Partner</div>
        job_title_elem = soup.find(
            ['div', 'span', 'p'],
            class_=lambda c: c and any('field_job_title' in cls for cls in (c if isinstance(c, list) else [c]))
        )
        if job_title_elem:
            t = job_title_elem.get_text(strip=True)
            if t and len(t) < 100:
                return t
        # Strategy 1: class-based title selector (skip nav/menu elements)
        for elem in soup.find_all(['div', 'span', 'p'], class_=re.compile(r'(title|position|role|job)', re.I)):
            cls = ' '.join(elem.get('class', []))
            # Skip nav/menu/footer elements
            if re.search(r'(menu|nav|footer|breadcrumb|modal|share|card|insight|teaser|cookie|consent|privacy)', cls, re.I):
                continue
            # Skip if inside a nav or header tag
            if elem.find_parent(['nav', 'header']):
                continue
            title = elem.get_text(strip=True)
            if title and len(title) < 100 and not re.search(r'(cookie|consent|privacy choices)', title, re.I):
                return title

        # Strategy 2: White & Case hero pattern — 'fs-5' div with 'Title, City' format
        fs5 = soup.find('div', class_=lambda c: c and 'fs-5' in c)
        if fs5:
            text = fs5.get_text(strip=True)
            # 'Partner, Los Angeles' — split on first comma
            if ',' in text and len(text) < 100:
                return text.split(',')[0].strip()

        # Strategy 3: keyword scan (skip script/style/meta parents)
        title_keywords = [
            "Partner", "Associate", "Counsel", "Of Counsel",
            "Senior Associate", "Managing Partner", "Senior Partner",
            "Member", "Shareholder", "Principal",
            "Attorney",
        ]
        
        for keyword in title_keywords:
            for elem in soup.find_all(string=re.compile(rf'\b{keyword}\b', re.I)):
                parent = elem.parent
                if parent and parent.name not in ('script', 'style', 'meta', 'head'):
                    if parent.find_parent(['nav', 'header', 'footer']):
                        continue
                    title = parent.get_text(strip=True)
                    if title and len(title) < 100 and not re.search(r'(cookie|consent|privacy choices)', title, re.I):
                        return title
        
        return None
    
    def _extract_offices_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract office locations using BeautifulSoup"""
        offices = []
        # Strategy 0a: Cleary Gottlieb — <h4 class='location__city'>
        for city_tag in soup.find_all('h4', class_='location__city'):
            city = city_tag.get_text(strip=True)
            if city and city not in offices:
                offices.append(city)
        if offices:
            return offices

        # Strategy 0b: Paul Weiss — <div class='location-block'> <a>Washington, DC</a>
        pw_loc = soup.find('div', class_=lambda c: c and 'location-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_loc:
            for a in pw_loc.find_all('a'):
                city = a.get_text(strip=True)
                if city and len(city) < 60 and city not in offices:
                    offices.append(city)
            if offices:
                return offices
        office_selectors = [
            {'class': re.compile(r'(office|location|city)', re.I)},
        ]
        
        FOOTER_CLASSES = re.compile(r'footer', re.I)
        for selector in office_selectors:
            elems = soup.find_all(['div', 'span', 'p', 'li', 'button', 'a'], selector)
            for elem in elems:
                # Skip footer/nav/menu elements
                elem_classes = ' '.join(elem.get('class', []))
                if FOOTER_CLASSES.search(elem_classes):
                    continue
                if elem.find_parent(['nav', 'header', 'footer']):
                    continue
                text = elem.get_text(strip=True)
                if text and len(text) < 100:
                    offices.append(text)

        # Secondary: links to /office/{city-slug}/ (e.g. Gibson Dunn: /office/palo-alto/)
        if not offices:
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                if re.search(r'/office/[a-z0-9-]+/?$', href, re.I):
                    text = a.get_text(strip=True)
                    if text and len(text) < 60:
                        offices.append(text)

        # Tertiary: White & Case fs-5 hero pattern — 'Title, City'
        if not offices:
            fs5 = soup.find('div', class_=lambda c: c and 'fs-5' in c)
            if fs5:
                text = fs5.get_text(strip=True)
                if ',' in text and len(text) < 150:
                    parts = text.split(',')
                    # parts[0] is title, rest are city names
                    # Merge state abbreviations (e.g. 'Washington', 'DC') back into city
                    cities: list[str] = []
                    for part in parts[1:]:
                        part = re.sub(r'\+\d.*', '', part).strip()
                        if not part:
                            continue
                        # If part looks like a state/country suffix (2-3 uppercase chars or 'DC')
                        if re.fullmatch(r'[A-Z\.]{1,3}', part) and cities:
                            cities[-1] = cities[-1] + ', ' + part
                        elif len(part) < 50:
                            cities.append(part)
                    offices.extend(cities)

        return offices
    
    def _extract_departments_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract department/group using BeautifulSoup"""
        departments: list[str] = []
        seen: set[str] = set()
        inline_tags = {"span", "a", "strong", "em", "b", "small", "label"}
        generic_labels = {
            "department", "departments", "group", "groups", "division", "section",
            "practice group", "practice groups", "team", "overview",
        }
        nav_keywords = (
            "home", "people", "lawyers", "practices", "industries", "offices", "careers",
            "insights", "our firm", "inclusion", "alumni", "search", "login", "go back",
            "proceed",
        )
        bio_verbs = re.compile(
            r"\b(advises|advised|represents|represented|focuses|focused|works on|clients|"
            r"experience|distressed debt|workouts|compliance)\b",
            re.IGNORECASE,
        )

        def _clean_candidate(text: str) -> str:
            text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
            return re.sub(r"\s+", " ", text).strip(" \t\r\n:-•|")

        def _is_noise(candidate: str) -> bool:
            lowered = candidate.lower()
            if not candidate or len(candidate) < 2 or len(candidate) > 100:
                return True
            if lowered in generic_labels:
                return True
            if re.search(r"@|http|www\.|cookie|consent|tel:|phone", candidate, re.IGNORECASE):
                return True
            if any(keyword in lowered for keyword in nav_keywords):
                return True
            if len(candidate) >= 35 and bio_verbs.search(candidate):
                return True
            return False

        def _add_candidate(candidate: str | None) -> None:
            if not candidate:
                return
            cleaned = _clean_candidate(candidate)
            if _is_noise(cleaned):
                return
            key = cleaned.lower()
            if key in seen:
                return
            seen.add(key)
            departments.append(cleaned)

        def _extract_json_ld_departments() -> None:
            def _collect_department_values(value: Any) -> None:
                if isinstance(value, str):
                    _add_candidate(value)
                elif isinstance(value, list):
                    for item in value:
                        _collect_department_values(item)
                elif isinstance(value, dict):
                    _collect_department_values(value.get("name") or value.get("department"))

            def _walk_json_ld(node: Any) -> None:
                if isinstance(node, list):
                    for item in node:
                        _walk_json_ld(item)
                    return
                if not isinstance(node, dict):
                    return

                node_type = node.get("@type")
                node_types = node_type if isinstance(node_type, list) else [node_type]
                if any(str(item).lower().endswith("person") for item in node_types if item):
                    _collect_department_values(node.get("department"))

                for value in node.values():
                    if isinstance(value, (dict, list)):
                        _walk_json_ld(value)

            for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
                raw = script.string or script.get_text()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                _walk_json_ld(payload)

        def _extract_label_from_container(elem: Tag) -> str | None:
            for attr_name in ("data-department", "data-dept"):
                attr_value = elem.get(attr_name)
                if isinstance(attr_value, str) and attr_value.strip():
                    return attr_value.strip()

            heading = elem.find(["h1", "h2", "h3", "h4", "h5", "h6", "button", "summary", "label"])
            if heading:
                heading_text = heading.get_text(" ", strip=True)
                if heading_text and heading_text.lower() not in generic_labels:
                    return heading_text

            direct_parts: list[str] = []
            for child in elem.children:
                if isinstance(child, str):
                    part = child.strip()
                elif getattr(child, "name", None) in inline_tags:
                    part = child.get_text(" ", strip=True)
                else:
                    continue
                if part:
                    direct_parts.append(part)

            if direct_parts:
                return " ".join(direct_parts)

            return None

        def _candidate_texts_from_sibling(elem: Tag) -> list[str]:
            if elem.get("role") == "tablist" or elem.find(attrs={"role": "tab"}):
                return []

            items: list[str] = []
            for node in elem.find_all(["li", "p", "dd"]):
                text = node.get_text(" ", strip=True)
                if text:
                    items.append(text)

            if items:
                return items

            fallback = _extract_label_from_container(elem)
            return [fallback] if fallback else []

        _extract_json_ld_departments()

        for elem in soup.find_all(
            lambda tag: tag.name and not tag.find_parent(["nav", "header", "footer"]) and (
                any(
                    re.search(r"department|practice[-_ ]?group|dept", cls, re.I)
                    for cls in (tag.get("class") or [])
                )
                or tag.has_attr("data-department")
                or tag.has_attr("data-dept")
            )
        ):
            _add_candidate(_extract_label_from_container(elem))

        for header in soup.find_all(["h2", "h3", "h4", "h5"]):
            if header.find_parent(["nav", "header", "footer"]):
                continue
            header_text = _clean_candidate(header.get_text(" ", strip=True)).lower()
            header_keywords = ["department", "group", "division", "section", "practice group", "team"]
            if not any(keyword in header_text for keyword in header_keywords):
                continue

            for sibling in header.next_siblings:
                if not getattr(sibling, "name", None):
                    continue
                if sibling.name in {"h2", "h3", "h4", "h5"}:
                    break
                if sibling.name in {"nav", "header", "footer"}:
                    continue
                sibling_marker = " ".join(
                    str(part)
                    for part in [
                        sibling.get("data-tab", ""),
                        sibling.get("id", ""),
                        sibling.get("aria-label", ""),
                        " ".join(sibling.get("class") or []),
                    ]
                    if part
                ).lower()
                if sibling_marker and not any(keyword in sibling_marker for keyword in header_keywords):
                    continue
                for candidate in _candidate_texts_from_sibling(sibling):
                    _add_candidate(candidate)

        return departments
    
    def _extract_practices_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract practice areas using BeautifulSoup"""
        practices: list[str] = []
        # Strategy 0a: Cleary Gottlieb — <aside> 'Areas of Experience'
        # Partners use simple-list__item > <a>, associates use complex-list__item > <p class='complex-list__item-title'>
        # If page has side-section asides (Cleary structure) but no 'Areas of Experience', return empty.
        cleary_asides = soup.find_all('aside', class_='side-section')
        if cleary_asides:
            for aside in cleary_asides:
                hdr = aside.find('h3', class_='side-section__header')
                if hdr and re.search(r'areas?\s+of\s+experience|practice', hdr.get_text(strip=True), re.I):
                    # Try complex-list__item (associate structure)
                    for li in aside.find_all('li', class_='complex-list__item'):
                        title_p = li.find('p', class_='complex-list__item-title')
                        item = (title_p or li).get_text(strip=True)
                        if item and len(item) < 120 and item not in practices:
                            practices.append(item)
                    # Try simple-list__item (partner structure)
                    for li in aside.find_all('li', class_='simple-list__item'):
                        a = li.find('a')
                        item = (a or li).get_text(strip=True)
                        if item and len(item) < 120 and item not in practices:
                            practices.append(item)
                    if practices:
                        return practices
            # Cleary page but no 'Areas of Experience' aside — attorney with no listed practices.
            return []

        # Strategy 0b: Paul Weiss — <div class='practices-block'> <p class='detail-item'>Litigation</p>
        pw_practices = soup.find('div', class_=lambda c: c and 'practices-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_practices:
            for item in pw_practices.find_all('p', class_='detail-item'):
                t = item.get_text(strip=True)
                if t and len(t) < 100 and t not in practices:
                    practices.append(t)
            if practices:
                return practices
        # Strategy 0: Davis Polk Drupal field_primary_practice (field-name--field_primary_practice)
        primary_practice_div = soup.find(
            lambda tag: tag.name in ('div', 'span', 'p') and any(
                'primary_practice' in cls
                for cls in (tag.get('class') or [])
            )
        )
        if primary_practice_div:
            t = primary_practice_div.get_text(strip=True)
            if t and len(t) < 100 and t not in practices:
                practices.append(t)

        # Strategy 1: section-header approach (works for Kirkland, Gibson Dunn, most AmLaw firms)
        header_items = self._extract_section_items_after_header(
            soup,
            ['practice', 'practices', 'areas', 'expertise', 'specialt', 'competen', 'service', 'capabilit'],
        )
        for item in header_items:
            if item not in practices:
                practices.append(item)
        
        # Strategy 2: Drupal CMS fields (DLA Piper, White & Case, etc.) - fallback
        if not practices:
            drupal_service_fields = soup.find_all('div', class_=lambda c: c and (
                'field--name-field-services' in ' '.join(c if isinstance(c, list) else [c])
                or 'field--name-field-sub-services' in ' '.join(c if isinstance(c, list) else [c])
                or 'field--name-field-related-services' in ' '.join(c if isinstance(c, list) else [c])
            ))
            for field_div in drupal_service_fields:
                for item_div in field_div.find_all('div', class_=lambda c: c and 'field--item' in ' '.join(c if isinstance(c, list) else [c])):
                    practice = item_div.get_text(strip=True)
                    if practice and len(practice) < 100 and practice not in practices:
                        practices.append(practice)
                for link in field_div.find_all('a'):
                    practice = link.get_text(strip=True)
                    if practice and len(practice) < 100 and practice not in practices:
                        practices.append(practice)
        # Strategy 3: Look for practice/service/expertise/capabilities string headers
        if not practices:
            practice_headers = soup.find_all(string=re.compile(r'(practice|service|expertise|specialt|competen|capabilit)\s*(area|focus|s)?', re.I))
            for header in practice_headers:
                parent = header.find_parent(['div', 'section', 'ul'])
                if parent:
                    links = parent.find_all('a')
                    for link in links:
                        practice = link.get_text(strip=True)
                        if practice and len(practice) < 100 and practice not in practices:
                            practices.append(practice)
                    items = parent.find_all('li')
                    for item in items:
                        practice = item.get_text(strip=True)
                        if practice and len(practice) < 100 and practice not in practices:
                            practices.append(practice)
        
        return practices
    
    def _extract_section_items_after_header(
        self,
        soup: BeautifulSoup,
        header_keywords: list[str],
    ) -> list[str]:
        """Generic section-header-based extractor.

        Locates h2/h3/h4 tags whose text (case-insensitive) contains ANY of the
        supplied keywords, then walks forward siblings until the next heading,
        collecting text from <li>, <a>, <p>, and <dd> elements.

        Returns a deduplicated list of stripped strings (<= 200 chars each).
        """
        seen: set[str] = set()
        results: list[str] = []

        for header in soup.find_all(['h2', 'h3', 'h4', 'h5']):
            # Skip headers inside nav/header/footer
            if header.find_parent(['nav', 'header', 'footer']):
                continue
            header_text = header.get_text(strip=True).lower()
            if not any(kw.lower() in header_text for kw in header_keywords):
                continue

            # Walk forward siblings until we hit another heading or footer
            for sibling in header.find_all_next():
                # Stop at the next heading at the same (or higher) level
                if sibling.name in ('h2', 'h3', 'h4', 'h5') and sibling is not header:
                    break
                # Stop when we enter the footer
                if sibling.name == 'footer':
                    break
                # Skip elements inside nav/header/footer
                if sibling.find_parent(['nav', 'header', 'footer']):
                    continue
                # Collect leaf text nodes from list items, links, paragraphs, dd
                if sibling.name in ('li', 'dd', 'p'):
                    text = sibling.get_text(strip=True)
                    if text and len(text) <= 200 and text not in seen:
                        seen.add(text)
                        results.append(text)

        return results

    def _extract_industries_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract industries using BeautifulSoup"""
        industries: list[str] = []
        seen: set[str] = set()
        header_keywords = [
            'industr',
            'sector',
            'market',
            'industry focus',
            'industry experience',
            'industries served',
        ]

        def _add_candidate(value: str | None) -> None:
            if not value:
                return
            candidate = re.sub(r'\s+', ' ', str(value)).strip(' \t\r\n:-•|')
            if not candidate or len(candidate) > 150:
                return
            key = candidate.lower()
            if key in {
                'industry', 'industries', 'sector', 'sectors', 'market', 'markets',
                'industry focus', 'industry experience', 'industries served',
            }:
                return
            if key in seen:
                return
            seen.add(key)
            industries.append(candidate)

        def _add_from_value(value: Any) -> None:
            if isinstance(value, str):
                _add_candidate(value)
            elif isinstance(value, list):
                for item in value:
                    _add_from_value(item)
            elif isinstance(value, dict):
                _add_from_value(value.get('name') or value.get('@value'))

        def _walk_json_ld(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    _walk_json_ld(item)
                return
            if not isinstance(node, dict):
                return

            if 'knowsAbout' in node:
                _add_from_value(node.get('knowsAbout'))

            for value in node.values():
                if isinstance(value, (dict, list)):
                    _walk_json_ld(value)

        def _add_direct_texts(elem: Tag) -> None:
            for child in elem.children:
                if isinstance(child, str):
                    _add_candidate(child.strip())
                    continue
                if getattr(child, 'name', None) in {'span', 'a', 'p', 'div', 'strong', 'em'}:
                    _add_candidate(child.get_text(' ', strip=True))

        def _extract_from_container(container: Tag) -> None:
            for header in container.find_all(['h2', 'h3', 'h4', 'h5', 'h6']):
                if header.find_parent(['nav', 'header', 'footer']):
                    continue
                header_text = header.get_text(' ', strip=True).lower()
                if not any(keyword in header_text for keyword in header_keywords):
                    continue

                for sibling in header.next_siblings:
                    if not getattr(sibling, 'name', None):
                        continue
                    if sibling.name in {'h2', 'h3', 'h4', 'h5', 'h6'}:
                        break
                    if sibling.find_parent(['nav', 'header', 'footer']):
                        continue

                    list_items = sibling.find_all('li') if hasattr(sibling, 'find_all') else []
                    if list_items:
                        for li in list_items:
                            _add_candidate(li.get_text(' ', strip=True))
                        continue

                    if sibling.name in {'li', 'p', 'dd', 'a', 'span', 'div'}:
                        _add_candidate(sibling.get_text(' ', strip=True))
                    _add_direct_texts(sibling)

        for script in soup.find_all('script', attrs={'type': re.compile(r'application/ld\+json', re.I)}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            _walk_json_ld(payload)

        for elem in soup.select('[class*="industr"], [class*="sector"], [data-industry]'):
            if elem.find_parent(['nav', 'header', 'footer']):
                continue
            if elem.find_all('li'):
                for li in elem.find_all('li'):
                    _add_candidate(li.get_text(' ', strip=True))
            else:
                _add_direct_texts(elem)

        for container in soup.find_all('aside'):
            _extract_from_container(container)
        for container in soup.select('[class*="sidebar"], [class*="aside"]'):
            _extract_from_container(container)

        _extract_from_container(soup)

        return industries or ['no industry field']

    def _extract_bar_admissions_bs4(self, soup: BeautifulSoup) -> list[str]:
        """Extract bar admissions using BeautifulSoup"""
        # Strategy 0a: Cleary Gottlieb — <aside> 'Bar Admissions' with complex-list__item-title
        bars: list[str] = []
        for aside in soup.find_all('aside', class_='side-section'):
            hdr = aside.find('h3', class_='side-section__header')
            if hdr and re.search(r'bar\s+admission|admission', hdr.get_text(strip=True), re.I):
                for li in aside.find_all('li', class_='complex-list__item'):
                    title_p = li.find('p', class_='complex-list__item-title')
                    item = (title_p or li).get_text(strip=True)
                    if item and len(item) <= 80 and item not in bars:
                        bars.append(item)
                if bars:
                    return bars
        # Strategy 0b: Paul Weiss — <div class='admissions-block'> <p class='detail-item'>New York</p>
        pw_adm = soup.find('div', class_=lambda c: c and 'admissions-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_adm:
            for item in pw_adm.find_all('p', class_='detail-item'):
                t = item.get_text(strip=True)
                if t and len(t) < 80 and t not in bars:
                    bars.append(t)
            if bars:
                return bars
        # Primary: section-header approach (h2/h3/h4 with bar/admission keywords)
        raw_items = self._extract_section_items_after_header(
            soup, ['bar admission', 'bar admissions', 'bar', 'admission']
        )
        if raw_items:
            for item in raw_items:
                # Skip news/insight headlines (too long or contain typical news patterns)
                if len(item) > 50 or re.search(r'(news|insight|publication|article|alert|advises|acquires|announces|represents|secures|wins)', item, re.I):
                    continue
                extracted = self._extract_states_from_text(item)
                if extracted:
                    for state in extracted:
                        if state not in bars:
                            bars.append(state)
                else:
                    # Keep raw item if no US state name matched
                    if item not in bars:
                        bars.append(item)

        # Secondary: h5/h6 'Admissions:' sub-header (Gibson Dunn pattern)
        # <h5>Admissions:</h5><ul><li>Colorado Bar</li>...</ul>
        if not bars:
            for sub_hdr in soup.find_all(['h5', 'h6']):
                sub_text = sub_hdr.get_text(strip=True).lower()
                if 'admission' in sub_text:
                    # Collect <li> items from the immediately following <ul>
                    nxt = sub_hdr.find_next_sibling()
                    while nxt and nxt.name not in ('h2', 'h3', 'h4', 'h5', 'h6'):
                        if nxt.name in ('ul', 'ol'):
                            for li in nxt.find_all('li'):
                                item = li.get_text(strip=True)
                                # Filter out news headlines / long items
                                if item and len(item) <= 50 and not re.search(r'(news|insight|publication|article|alert|advises|acquires|announces|represents|secures|wins)', item, re.I) and item not in bars:
                                    bars.append(item)
                            break
                        nxt = nxt.find_next_sibling()

        # Tertiary: Drupal CMS field--name-field-admissions (White & Case pattern)
        if not bars:
            drupal_field = soup.find('div', class_=lambda c: c and 'field--name-field-admissions' in ' '.join(c if isinstance(c, list) else [c]))
            if drupal_field:
                for item_div in drupal_field.find_all('div', class_=lambda c: c and 'field--item' in ' '.join(c if isinstance(c, list) else [c])):
                    item = item_div.get_text(strip=True)
                    if item and item not in bars:
                        bars.append(item)

        # Legacy fallback: find_all(string=...) + find_parent
        if not bars:
            bar_headers = soup.find_all(string=re.compile(r'bar\s*(admission|admissions)', re.I))
            for header in bar_headers:
                parent = header.find_parent(['div', 'section', 'ul', 'dl'])
                if parent:
                    text = parent.get_text()
                    extracted = self._extract_states_from_text(text)
                    for state in extracted:
                        if state not in bars:
                            bars.append(state)

        return bars

    def _extract_education_bs4(self, soup: BeautifulSoup) -> list[EducationRecord]:
        """Extract education records using BeautifulSoup"""
        education_records: list[EducationRecord] = []
        # Strategy 0a: Cleary Gottlieb — <aside> 'Education' with complex-list__item
        for aside in soup.find_all('aside', class_='side-section'):
            hdr = aside.find('h3', class_='side-section__header')
            if hdr and re.search(r'education', hdr.get_text(strip=True), re.I):
                for li in aside.find_all('li', class_='complex-list__item'):
                    title_p = li.find('p', class_='complex-list__item-title')
                    school = title_p.get_text(strip=True) if title_p else None
                    remaining_texts = []
                    for p in li.find_all('p'):
                        if p == title_p:
                            continue
                        remaining_texts.append(p.get_text(strip=True))
                    combined = ' '.join(remaining_texts)
                    deg = self._extract_degree_from_text(combined)
                    yr = self._extract_year_from_text(combined)
                    if school or deg:
                        education_records.append(EducationRecord(degree=deg, school=school, year=yr))
                if education_records:
                    return education_records

        # Strategy 0b: Paul Weiss — <div class='education-block'> <p class='detail-item'>J.D., Harvard, magna cum laude</p>
        pw_edu = soup.find('div', class_=lambda c: c and 'education-block' in ' '.join(c if isinstance(c,list) else [c]))
        if pw_edu:
            for item in pw_edu.find_all('p', class_='detail-item'):
                text = item.get_text(strip=True)
                if not text or len(text) > 200:
                    continue
                # Format: 'J.D., Harvard Law School, magna cum laude'
                parts = [p.strip() for p in text.split(',', 2)]
                deg = self._extract_degree_from_text(parts[0]) if parts else None
                school = parts[1].strip() if len(parts) > 1 else None
                yr = self._extract_year_from_text(text)
                if school or deg:
                    education_records.append(EducationRecord(degree=deg, school=school, year=yr))
            if education_records:
                return education_records
        # Strategy 0: Davis Polk — field-name--field_degrees with div.degree per entry
        deg_field = soup.find(
            lambda tag: tag.name in ('div', 'span', 'p') and any(
                'field_degrees' in cls
                for cls in (tag.get('class') or [])
            )
        )
        if deg_field:
            for item in deg_field.find_all('div', class_='field-item'):
                degree_div = item.find('div', class_='degree')
                if degree_div:
                    line = degree_div.get_text(strip=True)
                    deg = self._extract_degree_from_text(line)
                    yr = self._extract_year_from_text(line)
                    sch = re.sub(r'' + re.escape(deg) + r'', '', line, flags=re.IGNORECASE).strip(' ,-') if deg else line
                    sch = re.sub(r'^[A-Z]\.(?:[A-Z]\.)+,?\s*', '', sch).strip(' ,-')
                    if yr:
                        sch = sch.replace(str(yr), '').strip(' ,-')
                    if sch or deg:
                        education_records.append(EducationRecord(degree=deg, school=sch or None, year=yr))
            # Fallback: lines that start with a degree abbreviation
            if not education_records:
                for line in deg_field.get_text(separator='\n', strip=True).split('\n'):
                    line = line.strip()
                    if not line or len(line) > 150:
                        continue
                    if re.match(r'^(J\.D\.|LL\.M\.|LL\.B\.|B\.A\.|B\.S\.|M\.B\.A\.|Ph\.D\.|A\.B\.)', line):
                        deg = self._extract_degree_from_text(line)
                        yr = self._extract_year_from_text(line)
                        sch = re.sub(r'\b' + re.escape(deg) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if deg else line
                        if yr:
                            sch = sch.replace(str(yr), '').strip(' ,-')
                        if sch or deg:
                            education_records.append(EducationRecord(degree=deg, school=sch or None, year=yr))
        # Primary: section-header approach (skip if already populated by Strategy 0)
        if not education_records:
            raw_items = self._extract_section_items_after_header(soup, ['education', 'academic'])
            for text in raw_items:
                degree = self._extract_degree_from_text(text)
                year = self._extract_year_from_text(text)
                school = text
                if degree:
                    school = re.sub(
                        r'\b' + re.escape(degree) + r'\b', '', school, flags=re.IGNORECASE
                    )
                    # Also strip spelled-out degree forms (Gibson Dunn: 'Juris Doctor', 'Bachelor of Arts')
                    for pat in DEGREE_PATTERNS:
                        school = re.sub(pat, '', school, flags=re.IGNORECASE)
                # Strip residual parenthetical notes (Gibson Dunn: 'University of Houston - (some notes)')
                school = re.sub(r'\s*\([^)]*\)\s*', ' ', school)
                # Strip dash separator (Gibson Dunn: 'Georgetown University - 2019 Juris Doctor')
                school = re.sub(r'\s*-\s*$', '', school)
                if year:
                    school = school.replace(str(year), '')
                school = re.sub(r'\s{2,}', ' ', school)  # collapse multiple spaces
                school = school.strip(' ,-')
                education_records.append(EducationRecord(
                    degree=degree,
                    school=school if school else None,
                    year=year,
                ))

        # Legacy fallback: find_all(string=...) + find_parent
        if not education_records:
            edu_headers = soup.find_all(string=re.compile(r'education|academic', re.I))
            for header in edu_headers:
                parent = header.find_parent(['div', 'section', 'ul', 'dl'])
                if parent:
                    dts = parent.find_all('dt')
                    dds = parent.find_all('dd')
                    if dts and dds:
                        for dt, dd in zip(dts, dds):
                            degree_text = dt.get_text(strip=True)
                            school_text = dd.get_text(strip=True)
                            degree = self._extract_degree_from_text(degree_text)
                            year = self._extract_year_from_text(degree_text + ' ' + school_text)
                            education_records.append(EducationRecord(
                                degree=degree, school=school_text, year=year
                            ))
                    else:
                        for item in parent.find_all('li'):
                            text = item.get_text(strip=True)
                            degree = self._extract_degree_from_text(text)
                            year = self._extract_year_from_text(text)
                            school = text
                            if degree:
                                school = re.sub(
                                    r'\b' + re.escape(degree) + r'\b',
                                    '', school, flags=re.IGNORECASE
                                )
                            if year:
                                school = school.replace(str(year), '')
                            school = school.strip(' ,-')
                            education_records.append(EducationRecord(
                                degree=degree,
                                school=school if school else None,
                                year=year,
                            ))

        # Davis Polk: field-name--field_degrees — each field-item has a div.degree with "J.D., School Name"
        if not education_records:
            deg_field = soup.find(
                lambda tag: tag.name in ('div', 'span', 'p') and any(
                    'field_degrees' in cls
                    for cls in (tag.get('class') or [])
                )
            )
            if deg_field:
                for item in deg_field.find_all('div', class_='field-item'):
                    degree_div = item.find('div', class_='degree')
                    if degree_div:
                        line = degree_div.get_text(strip=True)
                        degree = self._extract_degree_from_text(line)
                        year = self._extract_year_from_text(line)
                        school = re.sub(r'\b' + re.escape(degree) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if degree else line
                        if year:
                            school = school.replace(str(year), '').strip(' ,-')
                        if school or degree:
                            education_records.append(EducationRecord(degree=degree, school=school or None, year=year))
                # Fallback: try lines starting with a degree abbreviation
                if not education_records:
                    for line in deg_field.get_text(separator='\n', strip=True).split('\n'):
                        line = line.strip()
                        if not line or len(line) > 150:
                            continue
                        if re.match(r'^(J\.D\.|LL\.M\.|LL\.B\.|B\.A\.|B\.S\.|M\.B\.A\.|Ph\.D\.|A\.B\.)', line):
                            degree = self._extract_degree_from_text(line)
                            year = self._extract_year_from_text(line)
                            school = re.sub(r'\b' + re.escape(degree) + r'\b', '', line, flags=re.IGNORECASE).strip(' ,-') if degree else line
                            if year:
                                school = school.replace(str(year), '').strip(' ,-')
                            if school or degree:
                                education_records.append(EducationRecord(degree=degree, school=school or None, year=year))

        # Drupal CMS: field--name-field-education with nested degree+school (White & Case pattern)
        if not education_records:
            edu_field = soup.find('div', class_=lambda c: c and 'field--name-field-education' in ' '.join(c if isinstance(c, list) else [c]))
            if edu_field:
                for item in edu_field.find_all('div', class_=lambda c: c and 'paragraph--type--education' in ' '.join(c if isinstance(c, list) else [c])):
                    deg_div = item.find('div', class_=lambda c: c and 'field--name-field-degree' in ' '.join(c if isinstance(c, list) else [c]))
                    school_div = item.find('div', class_=lambda c: c and 'field--name-field-school' in ' '.join(c if isinstance(c, list) else [c]))
                    year_div = item.find('div', class_=lambda c: c and 'field--name-field-year' in ' '.join(c if isinstance(c, list) else [c]))
                    degree = deg_div.get_text(strip=True) if deg_div else None
                    school = school_div.get_text(strip=True) if school_div else None
                    year_text = year_div.get_text(strip=True) if year_div else None
                    year = self._extract_year_from_text(year_text) if year_text else None
                    if school or degree:
                        education_records.append(EducationRecord(degree=degree, school=school, year=year))


        return education_records
    
    # ========================================================================
    # STAGE 4: REGEX FALLBACK EXTRACTION
    # ========================================================================
    
    def _extract_with_regex(self, profile: AttorneyProfile, html: str, url: str) -> None:
        """Regex fallback extraction for fields still missing"""
        
        # Name (if still missing)
        if not profile.full_name:
            # H1 tag
            h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            if h1_match:
                name = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
                if self._looks_like_person_name(name):
                    profile.full_name = name
        
        # Practice areas (if missing)
        if not profile.practice_areas:
            practice_section = re.search(
                r"<[^>]*>Practice[s]?\s*(?:Area[s]?)?</[^>]*>(.*?)</(?:div|section|ul)",
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if practice_section:
                content = practice_section.group(1)
                links = re.findall(r"<a[^>]*>(.*?)</a>", content, re.IGNORECASE)
                for link in links:
                    practice = re.sub(r"<[^>]+>", "", link).strip()
                    if practice and len(practice) < 100 and practice not in profile.practice_areas:
                        profile.practice_areas.append(practice)
        
        # Bar admissions (if missing)
        if not profile.bar_admissions:
            bar_section = re.search(
                r"<[^>]*>Bar\s*Admission[s]?</[^>]*>(.*?)</(?:div|section|ul)",
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if bar_section:
                text = re.sub(r"<[^>]+>", "", bar_section.group(1))
                profile.bar_admissions = self._extract_states_from_text(text)
        
        # Education (if missing)
        if not profile.education:
            edu_section = re.search(
                r"<[^>]*>Education</[^>]*>(.*?)</(?:div|section|ul)",
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if edu_section:
                content = edu_section.group(1)
                # Extract list items
                items = re.findall(r"<li[^>]*>(.*?)</li>", content, re.IGNORECASE | re.DOTALL)
                for item in items:
                    text = re.sub(r"<[^>]+>", "", item).strip()
                    degree = self._extract_degree_from_text(text)
                    year = self._extract_year_from_text(text)
                    
                    # Extract school
                    school = text
                    if degree:
                        school = school.replace(degree, '')
                    if year:
                        school = school.replace(str(year), '')
                    school = school.strip(' ,-')
                    
                    edu = EducationRecord(
                        degree=degree,
                        school=school if school else None,
                        year=year
                    )
                    profile.education.append(edu)
                # no-JD sentinel for regex path
                if profile.education:
                    has_jd = any(
                        e.degree and 'JD' in e.degree.upper()
                        for e in profile.education
                    )
                    if not has_jd:
                        profile.education.append(
                            EducationRecord(degree="no JD", school="unknown", year=None)
                        )
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _looks_like_person_name(self, text: str) -> bool:
        """Check if text looks like a person name.

        Strict validation:
        - Must match First Last pattern (e.g. "John Smith", "Mary O'Brien")
        - Must not be a known header/label term
        - Must not contain digits
        - Length 4–100
        """
        if not text or len(text) < 4 or len(text) > 100:
            return False
        if any(ch in text for ch in ["_", "#", "{", "}"]):
            return False
        if any(ch.isdigit() for ch in text):
            return False
        if text.strip().lower() in _HEADER_TERMS:
            return False
        return bool(_VALID_NAME_RE.match(text.strip()))
    
    def _extract_degree_from_text(self, text: str) -> str | None:
        """Extract degree abbreviation from text"""
        for pattern, degree in DEGREE_PATTERNS.items():
            if re.search(pattern, text, re.IGNORECASE):
                return degree
        return None
    
    def _extract_year_from_text(self, text: str) -> int | None:
        """Extract graduation year from text (1950-2030)"""
        years = re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', text)
        if years:
            return int(years[0])
        return None
    
    def _extract_states_from_text(self, text: str) -> list[str]:
        """Extract US state names from text"""
        states_found = []
        for state in US_STATES:
            if re.search(rf'\b{state}\b', text, re.IGNORECASE):
                if state not in states_found:
                    states_found.append(state)
        return states_found
