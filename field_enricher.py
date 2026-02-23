#!/usr/bin/env python3
"""field_enricher.py - Per-Field Source Provenance + JSON-LD Enrichment

Enriches AttorneyProfile objects with:
1. JSON-LD structured data (via extruct or regex fallback)
2. Embedded JSON state objects (React/Next.js __NEXT_DATA__, window.__APP_STATE__ etc.)
3. Microdata (schema.org Person via extruct)
4. Per-field source provenance: tracks which source contributed each field
5. Confidence scoring per source type

Architecture:
  - FieldEnricher.enrich(profile, html, profile_url, source_type) → None
    Fills in missing fields from html, records provenance.
  - FieldProvenanceRecord tracks source per field.
  - Enrichment is non-destructive: existing non-empty values are never overwritten.
    Exception: list fields (offices, practice_areas, etc.) are MERGED (no duplicates added).

Legal constraints honoured:
  - Only enriches from HTML already fetched by the caller (no additional HTTP)
  - No authentication, no CAPTCHA, no robots.txt violations
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from attorney_extractor import AttorneyProfile, EducationRecord

try:
    import extruct
    EXTRUCT_AVAILABLE = True
except ImportError:
    EXTRUCT_AVAILABLE = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source confidence levels (higher = more authoritative)
# ---------------------------------------------------------------------------

SOURCE_CONFIDENCE: dict[str, float] = {
    "official_profile_html":        1.0,
    "official_json_ld":             0.95,
    "official_microdata":           0.90,
    "official_embedded_json":       0.85,
    "official_directory_listing":   0.75,
    "official_pdf_brochure":        0.70,
    "external_json_ld":             0.65,
    "external_directory":           0.55,
    "state_bar_registry":           0.70,
    "unknown":                      0.30,
}


# ---------------------------------------------------------------------------
# Provenance record
# ---------------------------------------------------------------------------

@dataclass
class FieldProvenanceRecord:
    """Provenance for one field of one attorney profile."""
    field_name: str
    source_type: str     # key from SOURCE_CONFIDENCE
    source_url: str
    confidence: float
    extracted_value: Any  # the actual value stored


@dataclass
class EnrichmentLog:
    """Accumulated enrichment log for one attorney profile."""
    profile_url: str
    records: list[FieldProvenanceRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, field_name: str, source_type: str, source_url: str, value: Any) -> None:
        confidence = SOURCE_CONFIDENCE.get(source_type, 0.3)
        self.records.append(FieldProvenanceRecord(
            field_name=field_name,
            source_type=source_type,
            source_url=source_url,
            confidence=confidence,
            extracted_value=value,
        ))

    def to_dict(self) -> dict:
        return {
            "profile_url": self.profile_url,
            "field_sources": {
                r.field_name: {"source_type": r.source_type, "source_url": r.source_url,
                                "confidence": r.confidence}
                for r in self.records
            },
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Main enricher
# ---------------------------------------------------------------------------

class FieldEnricher:
    """
    Enriches an AttorneyProfile from HTML of the profile page.

    Usage:
        enricher = FieldEnricher()
        log = enricher.enrich(profile, html, profile_url="https://...", source_type="official_profile_html")
        # log.to_dict() stored in profile.diagnostics['enrichment_log']
    """

    def enrich(
        self,
        profile: AttorneyProfile,
        html: str,
        *,
        profile_url: str,
        source_type: str = "official_profile_html",
    ) -> EnrichmentLog:
        """
        Enrich profile from HTML. Non-destructive — existing values are kept.
        List fields are merged (unique entries added).

        Returns EnrichmentLog with per-field provenance.
        """
        elog = EnrichmentLog(profile_url=profile_url)

        # 1. JSON-LD (highest confidence structured data)
        json_ld = _extract_json_ld(html, profile_url)
        if json_ld:
            ld_source = f"official_json_ld" if "official" in source_type else "external_json_ld"
            self._apply_json_ld(profile, json_ld, source_url=profile_url,
                                source_type=ld_source, elog=elog)

        # 2. Microdata (schema.org via extruct)
        if EXTRUCT_AVAILABLE:
            microdata_person = _extract_microdata_person(html, profile_url)
            if microdata_person:
                self._apply_microdata(profile, microdata_person, source_url=profile_url,
                                      source_type=source_type, elog=elog)

        # 3. Embedded JSON state (React/Next.js)
        embedded_data = _extract_embedded_json(html)
        if embedded_data:
            self._apply_embedded_json(profile, embedded_data, source_url=profile_url,
                                      source_type=source_type, elog=elog)

        # 4. HTML heuristic extraction (last resort for remaining missing fields)
        if profile._has_missing_fields():
            self._apply_html_heuristics(profile, html, source_url=profile_url,
                                        source_type=source_type, elog=elog)

        # Store enrichment log in diagnostics
        profile.diagnostics['enrichment_log'] = elog.to_dict()
        profile.diagnostics['field_sources'] = elog.to_dict().get('field_sources', {})

        # Recalculate missing fields after enrichment
        profile.calculate_status()

        return elog

    # ------------------------------------------------------------------
    # JSON-LD applicator
    # ------------------------------------------------------------------

    def _apply_json_ld(
        self,
        profile: AttorneyProfile,
        ld: dict,
        source_url: str,
        source_type: str,
        elog: EnrichmentLog,
    ) -> None:
        """Apply JSON-LD Person data to profile, tracking provenance."""

        # full_name
        if not profile.full_name:
            name = ld.get('name', '') or ''
            if name:
                profile.full_name = name.strip()
                elog.add('full_name', source_type, source_url, name)

        # title
        if not profile.title:
            title = ld.get('jobTitle', '') or ld.get('title', '') or ''
            if title:
                profile.title = title.strip()
                elog.add('title', source_type, source_url, title)

        # offices (from address / workLocation)
        added_offices = self._extract_offices_from_ld(ld)
        new_offices = [o for o in added_offices if o not in profile.offices]
        if new_offices:
            profile.offices.extend(new_offices)
            elog.add('offices', source_type, source_url, new_offices)

        # practice_areas (from knowsAbout, hasCredential, makesOffer)
        added_pa = self._extract_practice_areas_from_ld(ld)
        new_pa = [p for p in added_pa if p not in profile.practice_areas]
        if new_pa:
            profile.practice_areas.extend(new_pa)
            elog.add('practice_areas', source_type, source_url, new_pa)

        # education (from alumniOf)
        added_edu = self._extract_education_from_ld(ld)
        for edu in added_edu:
            if not any(e.school == edu.school for e in profile.education):
                profile.education.append(edu)
                elog.add('education', source_type, source_url,
                         {'degree': edu.degree, 'school': edu.school, 'year': edu.year})

        # department (from department)
        if not profile.department:
            dept = ld.get('department', '') or ''
            if isinstance(dept, dict):
                dept = dept.get('name', '') or ''
            if dept:
                profile.department = [dept.strip()]
                elog.add('department', source_type, source_url, dept)

        # bar_admissions (from memberOf)
        member_of = ld.get('memberOf', [])
        if isinstance(member_of, (str, dict)):
            member_of = [member_of]
        for m in member_of:
            if isinstance(m, str) and _is_bar_admission(m):
                if m not in profile.bar_admissions:
                    profile.bar_admissions.append(m)
                    elog.add('bar_admissions', source_type, source_url, m)
            elif isinstance(m, dict):
                name = m.get('name', '') or ''
                if name and _is_bar_admission(name) and name not in profile.bar_admissions:
                    profile.bar_admissions.append(name)
                    elog.add('bar_admissions', source_type, source_url, name)

    def _extract_offices_from_ld(self, ld: dict) -> list[str]:
        """Extract US office strings from JSON-LD address/workLocation."""
        offices = []
        for key in ('address', 'workLocation', 'location'):
            addr = ld.get(key)
            if not addr:
                continue
            if isinstance(addr, dict):
                office = _ld_addr_to_string(addr)
                if office and _is_us_location(office):
                    offices.append(office)
            elif isinstance(addr, list):
                for a in addr:
                    if isinstance(a, dict):
                        office = _ld_addr_to_string(a)
                        if office and _is_us_location(office):
                            offices.append(office)
        return offices

    def _extract_practice_areas_from_ld(self, ld: dict) -> list[str]:
        """Extract practice areas from JSON-LD."""
        areas = []
        for key in ('knowsAbout', 'hasCredential', 'makesOffer', 'areaServed'):
            items = ld.get(key, [])
            if isinstance(items, str):
                items = [items]
            for item in items:
                if isinstance(item, str) and 3 < len(item) < 120:
                    areas.append(item)
                elif isinstance(item, dict):
                    name = item.get('name', '') or ''
                    if name and 3 < len(name) < 120:
                        areas.append(name)
        return areas

    def _extract_education_from_ld(self, ld: dict) -> list[EducationRecord]:
        """Extract education records from JSON-LD alumniOf."""
        edu_records = []
        alumni = ld.get('alumniOf', [])
        if isinstance(alumni, (str, dict)):
            alumni = [alumni]
        for item in alumni:
            if isinstance(item, str) and len(item) > 3:
                edu_records.append(EducationRecord(school=item))
            elif isinstance(item, dict):
                school = item.get('name', '') or ''
                degree = item.get('description', '') or item.get('credential', '') or ''
                year = _extract_year(item.get('endDate', '') or item.get('year', ''))
                if school:
                    edu_records.append(EducationRecord(
                        degree=degree.strip() or None,
                        school=school.strip(),
                        year=year,
                    ))
        return edu_records

    # ------------------------------------------------------------------
    # Microdata applicator
    # ------------------------------------------------------------------

    def _apply_microdata(
        self,
        profile: AttorneyProfile,
        microdata: dict,
        source_url: str,
        source_type: str,
        elog: EnrichmentLog,
    ) -> None:
        """Apply schema.org microdata Person to profile."""
        props = microdata.get('properties', {})

        if not profile.full_name:
            name = _first_string(props.get('name'))
            if name:
                profile.full_name = name
                elog.add('full_name', source_type, source_url, name)

        if not profile.title:
            title = _first_string(props.get('jobTitle'))
            if title:
                profile.title = title
                elog.add('title', source_type, source_url, title)

        # Address
        for addr_item in (props.get('address') or []):
            if isinstance(addr_item, dict):
                addr_props = addr_item.get('properties', {})
                city = _first_string(addr_props.get('addressLocality')) or ''
                state = _first_string(addr_props.get('addressRegion')) or ''
                office = f"{city}, {state}".strip(', ')
                if office and _is_us_location(office) and office not in profile.offices:
                    profile.offices.append(office)
                    elog.add('offices', source_type, source_url, office)

    # ------------------------------------------------------------------
    # Embedded JSON state applicator
    # ------------------------------------------------------------------

    def _apply_embedded_json(
        self,
        profile: AttorneyProfile,
        data: dict,
        source_url: str,
        source_type: str,
        elog: EnrichmentLog,
    ) -> None:
        """
        Apply embedded JSON state to profile.
        Recursively searches for attorney-like objects in the JSON tree.
        """
        person = _find_person_in_json(data, profile.full_name)
        if not person:
            return

        if not profile.full_name:
            name = person.get('name') or person.get('fullName') or person.get('full_name', '')
            if name:
                profile.full_name = str(name).strip()
                elog.add('full_name', source_type, source_url, name)

        if not profile.title:
            title = (person.get('title') or person.get('jobTitle') or
                     person.get('position') or person.get('role', ''))
            if title:
                profile.title = str(title).strip()
                elog.add('title', source_type, source_url, title)

        # Offices
        for office_key in ('offices', 'office', 'location', 'city'):
            offices_raw = person.get(office_key, [])
            if isinstance(offices_raw, str):
                offices_raw = [offices_raw]
            for o in offices_raw:
                if isinstance(o, str) and _is_us_location(o) and o not in profile.offices:
                    profile.offices.append(o)
                    elog.add('offices', source_type, source_url, o)
                elif isinstance(o, dict):
                    city = o.get('city', '') or o.get('name', '') or ''
                    state = o.get('state', '') or o.get('stateCode', '') or ''
                    office = f"{city}, {state}".strip(', ')
                    if office and _is_us_location(office) and office not in profile.offices:
                        profile.offices.append(office)
                        elog.add('offices', source_type, source_url, office)

        # Practice areas
        for pa_key in ('practiceAreas', 'practice_areas', 'areas', 'services'):
            pa_raw = person.get(pa_key, [])
            if isinstance(pa_raw, str):
                pa_raw = [pa_raw]
            for pa in pa_raw:
                if isinstance(pa, str) and 3 < len(pa) < 120 and pa not in profile.practice_areas:
                    profile.practice_areas.append(pa)
                    elog.add('practice_areas', source_type, source_url, pa)
                elif isinstance(pa, dict):
                    name = pa.get('name', '') or ''
                    if name and name not in profile.practice_areas:
                        profile.practice_areas.append(name)
                        elog.add('practice_areas', source_type, source_url, name)

        # Industries
        for ind_key in ('industries', 'industry', 'sectors'):
            ind_raw = person.get(ind_key, [])
            if isinstance(ind_raw, str):
                ind_raw = [ind_raw]
            for ind in ind_raw:
                if isinstance(ind, str) and 3 < len(ind) < 120 and ind not in profile.industries:
                    profile.industries.append(ind)
                    elog.add('industries', source_type, source_url, ind)

        # Department
        if not profile.department:
            dept = person.get('department') or person.get('group') or person.get('section', '')
            if isinstance(dept, list) and dept:
                profile.department = [str(d) for d in dept if d]
                elog.add('department', source_type, source_url, profile.department)
            elif isinstance(dept, str) and dept:
                profile.department = [dept.strip()]
                elog.add('department', source_type, source_url, dept)

        # Bar admissions
        for bar_key in ('barAdmissions', 'bar_admissions', 'admissions', 'licensedIn'):
            bar_raw = person.get(bar_key, [])
            if isinstance(bar_raw, str):
                bar_raw = [bar_raw]
            for bar in bar_raw:
                if isinstance(bar, str) and _is_bar_admission(bar) and bar not in profile.bar_admissions:
                    profile.bar_admissions.append(bar)
                    elog.add('bar_admissions', source_type, source_url, bar)

        # Education
        edu_raw = person.get('education') or person.get('alumniOf') or []
        if isinstance(edu_raw, (str, dict)):
            edu_raw = [edu_raw]
        for edu in edu_raw:
            rec = _parse_edu_item(edu)
            if rec and not any(e.school == rec.school for e in profile.education):
                profile.education.append(rec)
                elog.add('education', source_type, source_url,
                         {'degree': rec.degree, 'school': rec.school, 'year': rec.year})

    # ------------------------------------------------------------------
    # HTML heuristics (last resort)
    # ------------------------------------------------------------------

    def _apply_html_heuristics(
        self,
        profile: AttorneyProfile,
        html: str,
        source_url: str,
        source_type: str,
        elog: EnrichmentLog,
    ) -> None:
        """
        Heuristic HTML extraction for fields still missing after structured data passes.
        Uses regex-based extraction — less reliable but better than nothing.
        """
        missing = profile._missing_field_names()

        if 'full_name' in missing:
            name = _html_extract_name(html)
            if name:
                profile.full_name = name
                elog.add('full_name', source_type + '_html', source_url, name)

        if 'title' in missing:
            title = _html_extract_title(html)
            if title:
                profile.title = title
                elog.add('title', source_type + '_html', source_url, title)

        if 'bar_admissions' in missing or not profile.bar_admissions:
            bars = _html_extract_bar_admissions(html)
            new_bars = [b for b in bars if b not in profile.bar_admissions]
            if new_bars:
                profile.bar_admissions.extend(new_bars)
                elog.add('bar_admissions', source_type + '_html', source_url, new_bars)

        if 'education' in missing or not profile.education:
            edu_records = _html_extract_education(html)
            for rec in edu_records:
                if not any(e.school == rec.school for e in profile.education):
                    profile.education.append(rec)
                    elog.add('education', source_type + '_html', source_url,
                             {'degree': rec.degree, 'school': rec.school, 'year': rec.year})

        if 'practice_areas' in missing or not profile.practice_areas:
            pas = _html_extract_practice_areas(html)
            new_pas = [p for p in pas if p not in profile.practice_areas]
            if new_pas:
                profile.practice_areas.extend(new_pas)
                elog.add('practice_areas', source_type + '_html', source_url, new_pas)


# ---------------------------------------------------------------------------
# AttorneyProfile helper methods (monkey-patched onto the dataclass)
# ---------------------------------------------------------------------------

def _profile_has_missing_fields(self: AttorneyProfile) -> bool:
    """Return True if any required field is missing."""
    return bool(self._missing_field_names())


def _profile_missing_field_names(self: AttorneyProfile) -> list[str]:
    """Return list of required field names that are empty."""
    missing = []
    if not self.full_name:
        missing.append('full_name')
    if not self.title:
        missing.append('title')
    if not self.offices:
        missing.append('offices')
    if not self.department:
        missing.append('department')
    if not self.practice_areas:
        missing.append('practice_areas')
    if not self.industries:
        missing.append('industries')
    if not self.bar_admissions:
        missing.append('bar_admissions')
    if not self.education:
        missing.append('education')
    return missing


# Monkey-patch if not already present
if not hasattr(AttorneyProfile, '_has_missing_fields'):
    AttorneyProfile._has_missing_fields = _profile_has_missing_fields
if not hasattr(AttorneyProfile, '_missing_field_names'):
    AttorneyProfile._missing_field_names = _profile_missing_field_names


# ---------------------------------------------------------------------------
# JSON-LD extraction
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str, base_url: str) -> dict | None:
    """Extract the most relevant Person-type JSON-LD from HTML."""
    if EXTRUCT_AVAILABLE:
        try:
            data = extruct.extract(
                html,
                base_url=base_url,
                syntaxes=['json-ld'],
                uniform=True,
            )
            items = data.get('json-ld', [])
            # Priority: Person > Attorney > Lawyer > first item
            for item in items:
                t = item.get('@type', '')
                types = t if isinstance(t, list) else [t]
                if any(x in ('Person', 'Attorney', 'Lawyer') for x in types):
                    return item
            return items[0] if items else None
        except Exception:
            pass

    # Regex fallback
    return _extract_json_ld_regex(html)


def _extract_json_ld_regex(html: str) -> dict | None:
    """Regex-based JSON-LD fallback."""
    pat = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.I,
    )
    for m in pat.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get('@type') in ('Person', 'Attorney', 'Lawyer'):
                        return item
                return data[0] if data else None
            elif isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _extract_microdata_person(html: str, base_url: str) -> dict | None:
    """Extract schema.org Person from microdata using extruct."""
    if not EXTRUCT_AVAILABLE:
        return None
    try:
        data = extruct.extract(html, base_url=base_url, syntaxes=['microdata'], uniform=True)
        items = data.get('microdata', [])
        for item in items:
            type_val = item.get('type', '')
            if 'Person' in str(type_val) or 'Attorney' in str(type_val):
                return item
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Embedded JSON state extraction
# ---------------------------------------------------------------------------

_EMBEDDED_JSON_PATTERNS: list[re.Pattern] = [
    # Next.js
    re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL | re.I),
    # Nuxt.js
    re.compile(r'window\.__NUXT__\s*=\s*(\{.*?\});', re.DOTALL),
    # Generic window state
    re.compile(r'window\.__(?:APP|INITIAL|REDUX|STATE)_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
    re.compile(r'window\.__(?:PRELOADED|SERVER)_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
    # Apollo / GraphQL
    re.compile(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
]


def _extract_embedded_json(html: str) -> dict | None:
    """Extract embedded JSON state from React/Next.js/Nuxt pages."""
    for pat in _EMBEDDED_JSON_PATTERNS:
        m = pat.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# JSON tree search for person-like object
# ---------------------------------------------------------------------------

def _find_person_in_json(data: Any, known_name: str | None = None, depth: int = 0) -> dict | None:
    """
    Recursively search a JSON structure for an object that looks like a person.
    If known_name is provided, tries to match it for higher confidence.
    """
    if depth > 8:
        return None

    if isinstance(data, dict):
        # Direct person indicators
        person_keys = {'fullName', 'full_name', 'firstName', 'lastName', 'displayName',
                       'jobTitle', 'practiceAreas', 'barAdmissions', 'alumniOf',
                       'practice_areas', 'bar_admissions'}
        if person_keys.intersection(data.keys()):
            # Validate: if known_name provided, check for name match
            if known_name:
                for nk in ('name', 'fullName', 'full_name', 'displayName'):
                    n = data.get(nk, '') or ''
                    if isinstance(n, str) and known_name.lower() in n.lower():
                        return data
            else:
                return data

        # Recurse
        for v in data.values():
            result = _find_person_in_json(v, known_name, depth + 1)
            if result:
                return result

    elif isinstance(data, list):
        for item in data[:20]:  # cap list traversal
            result = _find_person_in_json(item, known_name, depth + 1)
            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# HTML heuristic extractors
# ---------------------------------------------------------------------------

_NAME_PATTERNS = [
    re.compile(r'<h1[^>]*class=["\'][^"\']*(?:attorney|lawyer|person|name|title)[^"\']*["\'][^>]*>(.*?)</h1>', re.DOTALL | re.I),
    re.compile(r'<h1[^>]*>([\w\s\-\.\,\']+(?:Jr\.|Sr\.|III|II|IV)?)</h1>', re.I),
    re.compile(r'"name"\s*:\s*"([^"]{5,80})"', re.I),
]

_TITLE_PATTERNS = [
    re.compile(r'<[^>]+class=["\'][^"\']*(?:position|job.?title|role|attorney.?type)[^"\']*["\'][^>]*>(.*?)</\w+>', re.DOTALL | re.I),
    re.compile(r'"(?:jobTitle|title|position)"\s*:\s*"([^"]{3,80})"', re.I),
]

_PRACTICE_AREA_HEADER = re.compile(r'practice\s+areas?', re.I)
_BAR_ADMISSION_HEADER = re.compile(r'bar\s+admissions?|admissions\s+&?\s*qualifications?', re.I)
_EDUCATION_HEADER = re.compile(r'\beducation\b', re.I)

_DEGREE_PATTERN = re.compile(
    r'\b(J\.?D\.?|LL\.?M\.?|LL\.?B\.?|B\.?A\.?|B\.?S\.?|M\.?B\.?A\.?|M\.?A\.?|M\.?S\.?|Ph\.?D\.?)\b'
)
_YEAR_PATTERN = re.compile(r'\b(19\d{2}|20\d{2})\b')

_US_STATE_NAMES = {
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

_US_STATE_CODES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC',
}

_NON_US_FAST_REJECT = {
    'london', 'england', 'united kingdom', 'hong kong', 'singapore',
    'tokyo', 'japan', 'beijing', 'shanghai', 'paris', 'france', 'germany',
    'berlin', 'munich', 'frankfurt', 'dubai', 'abu dhabi', 'sydney',
    'australia', 'toronto', 'canada', 'brussels', 'amsterdam', 'madrid',
    'milan', 'rome', 'moscow', 'korea', 'seoul',
}


def _html_extract_name(html: str) -> str | None:
    """Extract attorney name from HTML using heuristic patterns."""
    for pat in _NAME_PATTERNS:
        m = pat.search(html)
        if m:
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if 3 < len(text) < 80:
                return text
    return None


def _html_extract_title(html: str) -> str | None:
    """Extract attorney title from HTML."""
    for pat in _TITLE_PATTERNS:
        m = pat.search(html)
        if m:
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if 3 < len(text) < 100:
                return text
    return None


def _html_extract_practice_areas(html: str) -> list[str]:
    """Extract practice areas from HTML section following the header."""
    areas = []
    m = _PRACTICE_AREA_HEADER.search(html)
    if not m:
        return areas
    section = html[m.start():m.start() + 3000]
    # Extract list items
    for li_m in re.finditer(r'<li[^>]*>(.*?)</li>', section, re.DOTALL):
        text = re.sub(r'<[^>]+>', '', li_m.group(1)).strip()
        if 3 < len(text) < 100:
            areas.append(text)
    # Also extract anchor text
    if not areas:
        for a_m in re.finditer(r'<a[^>]*>(.*?)</a>', section, re.DOTALL):
            text = re.sub(r'<[^>]+>', '', a_m.group(1)).strip()
            if 3 < len(text) < 100 and text not in areas:
                areas.append(text)
    return areas[:20]


def _html_extract_bar_admissions(html: str) -> list[str]:
    """Extract bar admissions from HTML."""
    bars = []
    m = _BAR_ADMISSION_HEADER.search(html)
    if not m:
        return bars
    section = html[m.start():m.start() + 2000]
    for li_m in re.finditer(r'<li[^>]*>(.*?)</li>', section, re.DOTALL):
        text = re.sub(r'<[^>]+>', '', li_m.group(1)).strip()
        if _is_bar_admission(text):
            bars.append(text)
    return bars[:20]


def _html_extract_education(html: str) -> list[EducationRecord]:
    """Extract education records from HTML."""
    records = []
    m = _EDUCATION_HEADER.search(html)
    if not m:
        return records
    section = html[m.start():m.start() + 3000]
    for li_m in re.finditer(r'<li[^>]*>(.*?)</li>', section, re.DOTALL):
        text = re.sub(r'<[^>]+>', '', li_m.group(1)).strip()
        rec = _parse_edu_text(text)
        if rec:
            records.append(rec)
    return records[:10]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _ld_addr_to_string(addr: dict) -> str:
    """Convert a JSON-LD address dict to 'City, ST' string."""
    city = addr.get('addressLocality', '') or addr.get('city', '') or ''
    state = addr.get('addressRegion', '') or addr.get('state', '') or ''
    parts = [p.strip() for p in [city, state] if p.strip()]
    return ', '.join(parts)


def _is_us_location(text: str) -> bool:
    """Return True if text refers to a US location."""
    if not text:
        return False
    lower = text.strip().lower()
    for indicator in _NON_US_FAST_REJECT:
        if indicator in lower:
            return False
    m = re.search(r',\s*([A-Za-z]{2})(?:\s+\d{5})?$', text.strip())
    if m:
        code = m.group(1).upper()
        if code in _US_STATE_CODES:
            return True
        if code in {'UK', 'AU', 'DE', 'FR', 'JP', 'CN', 'SG', 'AE', 'QA', 'HK', 'CA'}:
            return False
    for state in _US_STATE_NAMES:
        if state in lower:
            return True
    return False


def _is_bar_admission(text: str) -> bool:
    """Return True if text looks like a US bar admission."""
    lower = text.lower()
    for state in _US_STATE_NAMES:
        if state in lower:
            return True
    m = re.search(r'\b([A-Z]{2})\b', text)
    if m and m.group(1) in _US_STATE_CODES:
        return True
    return False


def _extract_year(val: Any) -> int | None:
    """Extract a 4-digit year from a string or int."""
    if isinstance(val, int) and 1950 <= val <= 2030:
        return val
    if isinstance(val, str):
        m = re.search(r'\b(19\d{2}|20\d{2})\b', val)
        if m:
            return int(m.group(0))
    return None


def _first_string(val: Any) -> str | None:
    """Return first string from a value that might be a string or list."""
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, list):
        for item in val:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _parse_edu_item(item: Any) -> EducationRecord | None:
    """Parse an education item from JSON."""
    if isinstance(item, str):
        return _parse_edu_text(item)
    if isinstance(item, dict):
        school = item.get('name', '') or item.get('school', '') or ''
        degree = item.get('degree', '') or item.get('description', '') or ''
        year = _extract_year(item.get('endDate') or item.get('year'))
        if school:
            return EducationRecord(degree=degree.strip() or None, school=school.strip(), year=year)
    return None


def _parse_edu_text(text: str) -> EducationRecord | None:
    """Parse education text like 'J.D., Harvard Law School (2010)'."""
    if not text or len(text.strip()) < 5:
        return None
    text = text.strip()
    degree_m = _DEGREE_PATTERN.search(text)
    year_m = _YEAR_PATTERN.search(text)
    degree = degree_m.group(0) if degree_m else None
    year = int(year_m.group(0)) if year_m else None

    school_text = text
    if degree_m:
        school_text = school_text.replace(degree_m.group(0), '')
    if year_m:
        school_text = school_text.replace(year_m.group(0), '')
    school_text = re.sub(r'[,\(\)\[\]]', ' ', school_text)
    school_text = re.sub(r'\s+', ' ', school_text).strip(' -–')

    if school_text and len(school_text) >= 5:
        return EducationRecord(degree=degree, school=school_text, year=year)
    return None
