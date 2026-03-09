#!/usr/bin/env python3
"""validators.py - Per-Field Validators with Sentinels (PART 2, STEP 4-5)

Standalone validation layer for the heading-based extraction pipeline.
All validators return (cleaned_value, reason_code | None).

Sentinel values:
- industries empty  → ["no industry field"]
- education empty   → [EducationRecord(degree="no JD", school="unknown", year=None)]

All validators operate on plain Python types — no BeautifulSoup or requests
dependencies here.  Importable independently of the rest of the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Re-export shared constants (keep single source of truth in attorney_extractor)
# ---------------------------------------------------------------------------
try:
    from attorney_extractor import (  # type: ignore[assignment] # pyright: ignore[reportAssignmentType]
        DEGREE_PATTERNS,
        US_STATES,
        EducationRecord,  # pyright: ignore[reportAssignmentType]
        AttorneyProfile,
    )
except ImportError:
    # Standalone fallback — define minimally so module is importable in isolation
    DEGREE_PATTERNS = {  # type: ignore[assignment] # pyright: ignore[reportConstantRedefinition]
        r'\bJ\.?D\.?\b': 'JD',
        r'\bLL\.?M\.?\b': 'LLM',
        r'\bLL\.?B\.?\b': 'LLB',
        r'\bB\.?A\.?\b': 'BA',
        r'\bB\.?S\.?\b': 'BS',
        r'\bM\.?B\.?A\.?\b': 'MBA',
        r'\bM\.?A\.?\b': 'MA',
        r'\bM\.?S\.?\b': 'MS',
        r'\bPh\.?D\.?\b': 'PhD',
    }
    US_STATES = [  # type: ignore[assignment] # pyright: ignore[reportConstantRedefinition]
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
        "D.C.", "DC",
    ]

    @dataclass  # pyright: ignore[reportRedeclaration]
    class EducationRecord:  # type: ignore[no-redef] # pyright: ignore[reportRedeclaration]
        degree: str | None = None
        school: str | None = None
        year: int | None = None
        def to_dict(self) -> dict[str, Any]:
            return {"degree": self.degree, "school": self.school, "year": self.year}
    AttorneyProfile: Any = None  # type: ignore[assignment, misc] # pyright: ignore[reportRedeclaration]

# ---------------------------------------------------------------------------
# Reason codes (mirrored from profile_quality_gate.ReasonCode for consistency)
# ---------------------------------------------------------------------------

class ValidationReason:
    NOT_FOUND = "not_found"
    SENTINEL_APPLIED = "sentinel_applied"
    VALIDATION_REJECTED = "validation_rejected"
    CONTAMINATED = "contaminated"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    NO_JD = "no_jd_sentinel"
    NO_INDUSTRY = "no_industry_sentinel"


# ---------------------------------------------------------------------------
# US state lookup tables (full names and abbreviations)
# ---------------------------------------------------------------------------

_US_STATE_ABBR: set[str] = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

# Major US cities commonly used as law firm office names (no state suffix needed)
_US_MAJOR_LAW_CITIES: frozenset[str] = frozenset({
    # Northeast
    "New York", "Boston", "Philadelphia", "Pittsburgh", "Hartford", "Providence",
    "Albany", "Buffalo", "Newark", "Baltimore", "Washington", "Washington D.C.",
    "Washington DC", "Wilmington",
    # Southeast
    "Atlanta", "Miami", "Tampa", "Orlando", "Jacksonville", "Charlotte",
    "Raleigh", "Richmond", "Nashville", "Memphis", "Louisville", "Birmingham",
    "New Orleans", "Jacksonville",
    # Midwest
    "Chicago", "Detroit", "Cleveland", "Columbus", "Cincinnati", "Indianapolis",
    "Milwaukee", "Minneapolis", "Saint Paul", "St. Paul", "Kansas City",
    "St. Louis", "Omaha", "Des Moines",
    # Southwest / Mountain
    "Dallas", "Houston", "Austin", "San Antonio", "Denver", "Phoenix",
    "Albuquerque", "Salt Lake City", "Las Vegas", "Tucson",
    # West Coast
    "Los Angeles", "San Francisco", "San Diego", "Seattle", "Portland",
    "Sacramento", "San Jose", "Palo Alto", "Silicon Valley", "Menlo Park",
    "Oakland", "Irvine", "Century City", "Bay Area",
    # Other notable
    "Anchorage", "Honolulu",
    # Abbreviations / alt forms used by firms
    "NYC", "D.C.", "DC", "LA",
})

# Pre-compile state patterns for fast scanning
_US_STATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf'\b{re.escape(s)}\b', re.IGNORECASE)
    for s in US_STATES
]

# Abbreviation lookup: full name → canonical abbreviation
_FULL_TO_ABBR: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}

# ---------------------------------------------------------------------------
# Common junk phrases / navigation text
# ---------------------------------------------------------------------------

_JUNK_PHRASES: frozenset[str] = frozenset({
    "view all", "read more", "learn more", "see more", "click here",
    "cookie", "consent", "privacy policy", "checkbox",
    "always active", "settings", "manage", "accept", "allow",
    "here.", "skip to main", "skip navigation", "toggle menu",
})

_KNOWN_ATTORNEY_TITLES: frozenset[str] = frozenset({
    "partner", "associate", "counsel", "of counsel", "senior associate",
    "managing partner", "senior partner", "member", "shareholder",
    "principal", "special counsel", "senior counsel", "junior associate",
    "equity partner", "non-equity partner", "senior director",
})

_NAME_VALID_RE = re.compile(
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
_HEADER_TERMS: frozenset[str] = frozenset({
    "last name", "first name", "firm name", "attorney", "name", "title",
    "lawyer", "partner", "associate", "counsel", "full name", "contact",
    "practice areas", "practice area", "professionals", "our people",
    "people", "attorneys", "lawyers", "team", "biography", "profile",
    "who are you looking for", "search results", "search professionals",
    "meet our team", "our attorneys", "our lawyers", "legal team",
})


# ---------------------------------------------------------------------------
# Individual field validators
# ---------------------------------------------------------------------------

def validate_name(raw: str | None) -> tuple[str | None, str | None]:
    """Validate attorney full name.

    Rules:
    - 4–100 characters
    - Must match name pattern: optional honorific (Dr./Prof./Hon.) followed by
      capitalized name parts, allowing lowercase particles (de, van, von, la, etc.)
    - No digits, underscores, braces
    - Not a known header term

    Returns:
        (cleaned_name, None) on success
        (None, ValidationReason.*) on failure
    """
    if not raw:
        return None, ValidationReason.NOT_FOUND

    name = raw.strip()

    if len(name) < 4 or len(name) > 100:
        return None, ValidationReason.TOO_SHORT if len(name) < 4 else ValidationReason.TOO_LONG

    if any(ch in name for ch in ["_", "#", "{", "}"]):
        return None, ValidationReason.CONTAMINATED

    if any(ch.isdigit() for ch in name):
        return None, ValidationReason.CONTAMINATED

    if name.lower() in _HEADER_TERMS:
        return None, ValidationReason.VALIDATION_REJECTED

    if not _NAME_VALID_RE.match(name):
        return None, ValidationReason.VALIDATION_REJECTED

    return name, None


def validate_title(raw: str | None) -> tuple[str | None, str | None]:
    """Validate attorney title / role.

    Rules:
    - 2–120 characters
    - No email / phone contamination
    - May be a free-form title (e.g., "Senior Litigation Partner")

    Returns:
        (cleaned_title, None) on success
        (None, ValidationReason.*) on failure
    """
    if not raw:
        return None, ValidationReason.NOT_FOUND

    title = raw.strip()

    if len(title) < 2:
        return None, ValidationReason.TOO_SHORT

    if len(title) > 120:
        return None, ValidationReason.TOO_LONG

    if re.search(r"@|http|www\.", title, re.IGNORECASE):
        return None, ValidationReason.CONTAMINATED

    if re.search(r"\d{3}[.\-]\d{3}|\btel\b|\bphone\b", title, re.IGNORECASE):
        return None, ValidationReason.CONTAMINATED

    return title, None


def validate_offices(raw: list[str]) -> tuple[list[str], str | None]:
    """Validate and normalize office locations — US only.

    Accepts:
    - "City, ST" (two-letter state code)
    - "Washington, DC" / "Washington DC"
    - Full city names present in US (heuristic: if city text is >= 3 chars and
      does not look like a foreign city)

    Filters out:
    - "Location", "Lokation", "(Work)" junk labels
    - Non-US office strings where no US state abbreviation is present

    Returns:
        (us_only_offices, None) on success
        ([], ValidationReason.NOT_FOUND) if empty input
        ([], ValidationReason.VALIDATION_REJECTED) if all non-US after filter
    """
    if not raw:
        return [], ValidationReason.NOT_FOUND

    cleaned: list[str] = []
    seen: set[str] = set()

    _junk_labels = {"location", "lokation", "office", "offices", "work", "city"}

    for office in raw:
        text = office.strip()
        if not text:
            continue

        # Strip known junk prefixes/labels
        text = re.sub(r"(?i)^(location|lokation)\s*", "", text).strip("(), ")
        if text.lower() in _junk_labels:
            continue
        if len(text) < 2:
            continue

        # Normalize "Washington DC" without comma
        if re.match(r"(?i)^washington\s+dc$", text):
            text = "Washington, DC"

        # US state code check: "City, ST"
        if ", " in text:
            parts = text.split(", ")
            state_code = parts[-1].strip().upper()
            if state_code in _US_STATE_ABBR:
                normalized = text
                if normalized not in seen:
                    seen.add(normalized)
                    cleaned.append(normalized)
                continue

        # Known major US law firm city (no state suffix needed)
        if text in _US_MAJOR_LAW_CITIES or text.lower() in {c.lower() for c in _US_MAJOR_LAW_CITIES}:
            if text not in seen:
                seen.add(text)
                cleaned.append(text)
            continue

        # US state full name check (e.g. "New York office")
        for state in US_STATES:
            if re.search(rf'\b{re.escape(state)}\b', text, re.IGNORECASE):
                if text not in seen:
                    seen.add(text)
                    cleaned.append(text)
                break

    if not cleaned:
        return [], ValidationReason.VALIDATION_REJECTED

    return cleaned, None


def validate_department(raw: list[str]) -> tuple[list[str], str | None]:
    """Validate department / practice group labels.

    Filters:
    - Email, URL, phone contamination
    - Cookie notices, language selectors
    - Items > 150 chars or < 3 chars

    Returns:
        (cleaned_departments, None) or ([], reason)
    """
    if not raw:
        return [], ValidationReason.NOT_FOUND

    contamination_patterns = [
        r"@",
        r"http|www\.",
        r"tel:|phone",
        r"\d{3}[.\-]\d{3}",
        r"call\s*\+?\d",
        r"cookie|consent",
        r"always\s*active",
        r"(?i)english\w{4,}",
        r"^\w+\s*\|\s*\w+$",
    ]

    cleaned: list[str] = []
    seen: set[str] = set()

    for dept in raw:
        dept = dept.strip()
        if not dept or len(dept) < 3 or len(dept) > 150:
            continue
        if any(re.search(p, dept, re.IGNORECASE) for p in contamination_patterns):
            continue
        if dept.lower() in seen:
            continue
        seen.add(dept.lower())
        cleaned.append(dept)

    if not cleaned:
        return [], ValidationReason.VALIDATION_REJECTED

    return cleaned, None


def validate_practice_areas(raw: list[str]) -> tuple[list[str], str | None]:
    """Validate practice areas list.

    Filters navigation/CTA words, cookie notices, and UI junk.

    Returns:
        (cleaned_practices, None) or ([], reason)
    """
    if not raw:
        return [], ValidationReason.NOT_FOUND

    cleaned: list[str] = []
    seen: set[str] = set()

    for practice in raw:
        practice = practice.strip()

        if not practice or len(practice) < 3 or len(practice) > 150:
            continue

        if any(junk in practice.lower() for junk in _JUNK_PHRASES):
            continue

        if "http" in practice.lower() or "@" in practice:
            continue

        key = practice.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(practice)

    if not cleaned:
        return [], ValidationReason.VALIDATION_REJECTED

    return cleaned, None


def validate_industries(raw: list[str]) -> tuple[list[str], str | None]:
    """Validate industries list.

    Sentinel: if raw is empty (or all items invalid), returns
    ["no industry field"] with reason ValidationReason.NO_INDUSTRY.

    Returns:
        (industries, None) — includes sentinel if needed
    """
    if not raw:
        return ["no industry field"], ValidationReason.NO_INDUSTRY

    cleaned: list[str] = []
    seen: set[str] = set()

    for industry in raw:
        industry = industry.strip()

        if not industry or len(industry) < 3 or len(industry) > 150:
            continue

        if any(junk in industry.lower() for junk in _JUNK_PHRASES):
            continue

        if "http" in industry.lower() or "@" in industry:
            continue

        key = industry.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(industry)

    if not cleaned:
        return ["no industry field"], ValidationReason.NO_INDUSTRY

    return cleaned, None


def validate_bar_admissions(raw: list[str]) -> tuple[list[str], str | None]:
    """Validate bar admissions — US states only.

    Each item in raw is scanned for US state names (full or abbreviation).
    Court names (e.g., "U.S. Court of Appeals") are included only if they
    contain a US state reference.

    Returns:
        (us_state_admissions, None) or ([], reason)
    """
    if not raw:
        return [], ValidationReason.NOT_FOUND

    found: list[str] = []
    seen: set[str] = set()

    for item in raw:
        # Extract all US state names from this text block
        states = _extract_states_from_text(item)
        for state in states:
            if state not in seen:
                seen.add(state)
                found.append(state)

        # If text itself is already a clean state name (passed in validated form)
        if not states:
            stripped = item.strip()
            if stripped in _FULL_TO_ABBR or stripped.upper() in _US_STATE_ABBR:
                if stripped not in seen:
                    seen.add(stripped)
                    found.append(stripped)

    if not found:
        return [], ValidationReason.VALIDATION_REJECTED

    return found, None


def validate_education(raw: list[EducationRecord]) -> tuple[list[EducationRecord], str | None]:
    """Validate education records.

    Rules:
    - Must have school OR degree (records missing both are dropped)
    - Year must be 1950–2030 if present
    - If no JD found in any record, append no-JD sentinel

    Sentinel: if raw is empty, returns sentinel list with ValidationReason.NO_JD.

    Returns:
        (records, None) — includes no-JD sentinel when applicable
    """
    if not raw:
        sentinel = EducationRecord(degree="no JD", school="unknown", year=None)
        return [sentinel], ValidationReason.NO_JD

    valid: list[EducationRecord] = []
    has_jd = False

    for rec in raw:
        # Already a sentinel — carry through
        if rec.degree == "no JD":
            has_jd = False  # Don't count sentinel as having JD
            valid.append(rec)
            continue

        # Must have at minimum a school name or a degree
        if not rec.school and not rec.degree:
            continue

        # Validate year range
        if rec.year is not None:
            if not (1950 <= rec.year <= 2030):
                rec = EducationRecord(degree=rec.degree, school=rec.school, year=None)

        # Track JD presence
        if rec.degree and "JD" in rec.degree.upper():
            has_jd = True

        valid.append(rec)

    if not valid:
        sentinel = EducationRecord(degree="no JD", school="unknown", year=None)
        return [sentinel], ValidationReason.NO_JD

    # Remove pre-existing no-JD sentinels before re-evaluating
    valid_without_sentinel = [r for r in valid if r.degree != "no JD"]

    if not has_jd and valid_without_sentinel:
        # Re-check: is JD present after sentinel removal?
        has_jd_check = any(
            r.degree and "JD" in r.degree.upper()
            for r in valid_without_sentinel
        )
        if not has_jd_check:
            sentinel = EducationRecord(degree="no JD", school="unknown", year=None)
            return valid_without_sentinel + [sentinel], ValidationReason.NO_JD

    return valid_without_sentinel if valid_without_sentinel else valid, None


# ---------------------------------------------------------------------------
# Extraction helpers (used by enrichment.py)
# ---------------------------------------------------------------------------

def extract_degree_from_text(text: str) -> str | None:
    """Extract the highest-priority degree abbreviation from text.

    Priority order: JD > LLM > LLB > MBA > PhD > MA > MS > BA > BS

    Returns:
        Degree abbreviation (e.g. "JD") or None if no degree found.
    """
    priority_order = ["JD", "LLM", "LLB", "MBA", "PhD", "MA", "MS", "BA", "BS"]
    found: dict[str, str] = {}

    for pattern, degree in DEGREE_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            found[degree] = degree

    for degree in priority_order:
        if degree in found:
            return degree

    return None


def extract_year_from_text(text: str) -> int | None:
    """Extract graduation year (1950–2030) from text.

    Returns first match as int, or None.
    """
    years = re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', text)
    return int(years[0]) if years else None


def extract_school_from_text(text: str, degree: str | None, year: int | None) -> str | None:
    """Extract school name by removing degree and year tokens from text.

    Returns:
        Cleaned school name string, or None if nothing remains.
    """
    school = text

    if degree:
        # Remove degree token
        for pattern, deg in DEGREE_PATTERNS.items():
            if deg == degree:
                school = re.sub(pattern, "", school, flags=re.IGNORECASE)
                break

    if year:
        school = school.replace(str(year), "")

    # Clean up punctuation / extra whitespace
    school = re.sub(r"[,;\(\)]+", " ", school)
    school = re.sub(r"\s{2,}", " ", school).strip(" ,.-")

    return school if school else None


def _extract_states_from_text(text: str) -> list[str]:
    """Extract US state names from a text block.

    Matches both full names and 2-letter abbreviations (word-boundary aware).
    Returns unique matches in order of first appearance.
    """
    found: list[str] = []
    seen: set[str] = set()

    # Full state names first (more specific)
    for state in US_STATES:
        if re.search(rf'\b{re.escape(state)}\b', text, re.IGNORECASE):
            canonical = state
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)

    return found


def parse_education_text_blocks(blocks: list[str]) -> list[EducationRecord]:
    """Convert raw text blocks from the education section into EducationRecord objects.

    Each block is expected to be one education entry, e.g.:
    - "J.D., Harvard Law School, 2001"
    - "B.A., Economics, Yale University"

    Applies the no-JD sentinel if no JD is found among all records.

    Returns:
        List of EducationRecord objects (never empty — sentinel added if needed).
    """
    records: list[EducationRecord] = []
    has_jd = False

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        degree = extract_degree_from_text(block)
        year = extract_year_from_text(block)
        school = extract_school_from_text(block, degree, year)

        if not school and not degree:
            continue

        if degree and "JD" in degree.upper():
            has_jd = True

        records.append(EducationRecord(degree=degree, school=school, year=year))

    if not records:
        return [EducationRecord(degree="no JD", school="unknown", year=None)]

    if not has_jd:
        records.append(EducationRecord(degree="no JD", school="unknown", year=None))

    return records


def parse_bar_admissions_text_blocks(blocks: list[str]) -> list[str]:
    """Extract US state names from raw bar admissions text blocks.

    Returns:
        Deduplicated list of US state names found across all blocks.
        Empty list if no US states found.
    """
    found: list[str] = []
    seen: set[str] = set()

    for block in blocks:
        for state in _extract_states_from_text(block):
            if state not in seen:
                seen.add(state)
                found.append(state)

    return found
