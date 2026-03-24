#!/usr/bin/env python3
"""parser_sections.py - Heading-Based Section Parser (PART 2, STEP 1-2)

Builds a normalized section map from HTML using heading hierarchy, ARIA roles,
accordion buttons, and dt/dd pairs — without any class-based selectors.

Public API:
    parse_sections(html: str) -> dict[str, list[str]]
        Returns { normalized_section_key: [text_blocks] }

    normalize_section_title(raw: str) -> str
        Maps raw heading text to a canonical key using SECTION_SYNONYMS.

    find_section(section_map: dict, key: str) -> list[str]
        Retrieves content blocks for a canonical section key.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None
    Tag = None


# ---------------------------------------------------------------------------
# Synonym map: canonical key -> list of surface-form substrings (lowercase)
# ---------------------------------------------------------------------------

SECTION_SYNONYMS: dict[str, list[str]] = {
    "practice_areas": [
        "practice area",
        "practice areas",
        "practices",
        "practice",
        "capabilities",
        "expertise",
        "focus area",
        "focus areas",
        "specialt",           # covers specialties / specializations
        "competen",           # covers competencies
        "service",            # covers services
    ],
    "industries": [
        "industries",
        "industry",
        "sectors",
        "markets",
        "market sector",
    ],
    "departments": [
        "department",
        "departments",
        "group",
        "practice group",
        "division",
        "section",
    ],
    "bar_admissions": [
        "bar admission",
        "bar admissions",
        "admissions",
        "admitted",
        "bar",
        "jurisdiction",
        "licensed in",
        "court admission",
    ],
    "education": [
        "education",
        "academic",
        "degrees",
        "credentials",
        "background",
        "qualifications",
    ],
    "offices": [
        "office",
        "offices",
        "location",
        "locations",
    ],
    "title": [
        "title",
        "position",
        "role",
        "job title",
        "designation",
    ],
    "languages": [
        "language",
        "languages",
    ],
    "recognition": [
        "recognition",
        "awards",
        "honors",
        "honour",
        "ranking",
        "rankings",
    ],
    "publications": [
        "publication",
        "publications",
        "articles",
        "authored",
    ],
    "biography": [
        "biography",
        "overview",
        "about",
        "summary",
        "bio",
        "profile",
    ],
}

# ---------------------------------------------------------------------------
# Heading tag names and ARIA-role equivalents
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CONTENT_TAGS = {"li", "a", "dd", "p", "span", "td"}

# Maximum text length for a single content block (guard against grabbing whole-page text)
_MAX_BLOCK_LEN = 400

# Footer container detection — structural HTML element names and class tokens
_FOOTER_CONTAINER_NAMES = frozenset({"footer"})
_FOOTER_CONTAINER_CLASSES = frozenset({
    "footer", "site-footer", "global-footer",
    "copyright-bar", "bottom-bar", "page-footer",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


def _strip_punctuation_for_matching(text: str) -> str:
    """Lowercase, remove punctuation except hyphens, collapse spaces."""
    text = text.lower()
    text = re.sub(r"[^\w\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tag_heading_level(tag: Tag) -> int:
    """Return numeric heading level (1-6) for a tag, or 0 if not a heading."""
    name = getattr(tag, "name", None)
    if name in _HEADING_TAGS:
        return int(name[1])
    role = tag.get("role", "")
    if role == "heading":
        aria = tag.get("aria-level", "2")
        try:
            return int(aria)
        except (ValueError, TypeError):
            return 2
    return 0


def _is_heading(tag: Tag) -> bool:
    return _tag_heading_level(tag) > 0


def _is_accordion_trigger(tag: Tag) -> bool:
    """Detect accordion/tab triggers that act as section headings."""
    name = getattr(tag, "name", None)
    if name != "button" and name != "a":
        return False
    # Must have aria-controls or aria-expanded — structural accordion indicators
    has_aria = tag.has_attr("aria-controls") or tag.has_attr("aria-expanded")
    return has_aria


def _collect_content_after(
    soup: BeautifulSoup,
    anchor: Tag,
    stop_level: int,
) -> list[str]:
    """Walk forward siblings (and cousins) after *anchor* until the next heading
    at the same or higher level, collecting text blocks from content tags.

    Args:
        soup: Full page soup (unused directly; kept for signature consistency)
        anchor: The heading tag to start from
        stop_level: Stop walking when we encounter a heading <= this level

    Returns:
        Deduplicated list of text blocks
    """
    seen: set[str] = set()
    blocks: list[str] = []

    def _harvest(node: Tag) -> None:
        """Recursively harvest text from content-tag descendants."""
        for child in node.children:
            if isinstance(child, NavigableString):
                continue
            if not hasattr(child, "name"):
                continue
            if _is_heading(child):
                return  # Don't descend into nested headings
            if child.name in _CONTENT_TAGS:
                text = _clean_text(child.get_text())
                if text and len(text) <= _MAX_BLOCK_LEN and text not in seen:
                    seen.add(text)
                    blocks.append(text)
            else:
                # Recurse into containers (div, section, ul, ol, dl, etc.)
                _harvest(child)

    for sibling in anchor.find_all_next():
        # Stop if we enter a structural footer container
        sib_classes = set(sibling.get("class") or [])
        sib_id = (sibling.get("id") or "").lower()
        if sibling.name in _FOOTER_CONTAINER_NAMES:
            break
        if sib_classes & _FOOTER_CONTAINER_CLASSES:
            break
        if any(fc in sib_id for fc in _FOOTER_CONTAINER_CLASSES):
            break
        # Stop at a heading that is same or higher (lower number = higher level)
        if _is_heading(sibling):
            sib_level = _tag_heading_level(sibling)
            if sib_level <= stop_level:
                break
            # Higher-level heading encountered — still a sub-section, keep going
            # but DON'T treat it as stop; collect its content instead
            continue
        # Collect from content nodes
        if sibling.name in _CONTENT_TAGS:
            text = _clean_text(sibling.get_text())
            if text and len(text) <= _MAX_BLOCK_LEN and text not in seen:
                seen.add(text)
                blocks.append(text)
        # Also descend into containers that are siblings
        elif sibling.name in {"ul", "ol", "dl", "div", "section", "article",
                               "table", "tbody", "tr", "aside"}:
            _harvest(sibling)

    return blocks


def _collect_dt_dd_pairs(dl_tag: Tag) -> list[str]:
    """Extract text from <dt>/<dd> pairs inside a <dl> tag."""
    results: list[str] = []
    for child in dl_tag.children:
        if not hasattr(child, "name"):
            continue
        if child.name in {"dt", "dd"}:
            text = _clean_text(child.get_text())
            if text:
                results.append(text)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_section_title(raw: str) -> str:
    """Map a raw heading string to a canonical section key.

    Matching is done against SECTION_SYNONYMS using substring search on the
    stripped/lowercased heading text.  The first synonym that matches wins.
    If no synonym matches, returns the raw title normalized to snake_case.

    Args:
        raw: Raw heading text (e.g. "Practice Areas", "Bar Admissions & Courts")

    Returns:
        Canonical key (e.g. "practice_areas", "bar_admissions") or a
        snake_case version of raw if no synonym matched.
    """
    normalized = _strip_punctuation_for_matching(raw)

    for canonical_key, synonyms in SECTION_SYNONYMS.items():
        for syn in synonyms:
            # Substring match — heading may contain extra words
            if syn in normalized:
                return canonical_key

    # Fallback: snake_case of normalized text (keeps unknown sections retrievable)
    return re.sub(r"\s+", "_", normalized).strip("_") or "unknown"


def parse_sections(html: str) -> dict[str, list[str]]:
    """Build a normalized section map from attorney profile HTML.

    Detects headings via:
    - h1–h6 tags
    - Elements with role="heading"
    - Accordion trigger buttons (aria-controls / aria-expanded)
    - <dt> / <dd> pairs in definition lists

    Returns:
        {
            "practice_areas": ["Mergers & Acquisitions", "Private Equity", ...],
            "bar_admissions": ["New York", "California", ...],
            "education": ["J.D., Harvard Law School, 2001", ...],
            ...
        }

    Keys are canonical section names from SECTION_SYNONYMS (or snake_case
    fallbacks for unrecognized sections).  Values are deduplicated lists of
    text blocks collected from the content following each heading.
    """
    if not BS4_AVAILABLE:
        return _parse_sections_regex_fallback(html)

    soup = BeautifulSoup(html, "html.parser")
    section_map: dict[str, list[str]] = {}

    # -----------------------------------------------------------------------
    # PASS 1: Traverse all heading-like elements in document order
    # -----------------------------------------------------------------------
    heading_nodes: list[Tag] = []

    for tag in soup.find_all(True):
        if _is_heading(tag) or _is_accordion_trigger(tag):
            heading_nodes.append(tag)

    for heading in heading_nodes:
        raw_text = _clean_text(heading.get_text())
        if not raw_text or len(raw_text) > 120:
            continue  # Skip headings that are empty or suspiciously long

        canonical_key = normalize_section_title(raw_text)
        stop_level = _tag_heading_level(heading) or 2  # accordion triggers → treat as h2

        content_blocks = _collect_content_after(soup, heading, stop_level)

        if not content_blocks:
            continue

        if canonical_key not in section_map:
            section_map[canonical_key] = []

        for block in content_blocks:
            if block not in section_map[canonical_key]:
                section_map[canonical_key].append(block)

    # -----------------------------------------------------------------------
    # PASS 2: Scan standalone <dl> tags for dt/dd content
    # (Some firms use definition lists without a preceding heading)
    # -----------------------------------------------------------------------
    for dl_tag in soup.find_all("dl"):
        parent_heading = dl_tag.find_previous(lambda t: _is_heading(t))
        if parent_heading:
            raw_text = _clean_text(parent_heading.get_text())
            canonical_key = normalize_section_title(raw_text)
        else:
            canonical_key = "unknown"

        pairs = _collect_dt_dd_pairs(dl_tag)
        if not pairs:
            continue

        if canonical_key not in section_map:
            section_map[canonical_key] = []

        for block in pairs:
            if block not in section_map[canonical_key]:
                section_map[canonical_key].append(block)

    # -----------------------------------------------------------------------
    # PASS 3: Extract name / title from hero / header area
    # (h1 content is almost always the attorney name; h2 is often title)
    # -----------------------------------------------------------------------
    _extract_hero_fields(soup, section_map)

    return section_map


def find_section(section_map: dict[str, list[str]], key: str) -> list[str]:
    """Retrieve content blocks for a canonical section key.

    Args:
        section_map: Output of parse_sections()
        key: Canonical key, e.g. "practice_areas", "bar_admissions"

    Returns:
        List of text blocks, or [] if section not present.
    """
    return section_map.get(key, [])


# ---------------------------------------------------------------------------
# Hero / header extraction helper
# ---------------------------------------------------------------------------

def _extract_hero_fields(soup: BeautifulSoup, section_map: dict[str, list[str]]) -> None:
    """Extract name and title from the page hero / header area.

    Adds to section_map["name"] and section_map["title"] without
    overwriting existing entries from heading-based extraction.
    """
    # Name: first h1 on the page
    h1 = soup.find("h1")
    if h1:
        name_text = _clean_text(h1.get_text())
        if name_text and len(name_text) < 120:
            if "name" not in section_map:
                section_map["name"] = []
            if name_text not in section_map["name"]:
                section_map["name"].append(name_text)

    # Title: check <title> tag and og:title meta
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        parts = re.split(r"[|\-\u2013\u2014]", og_title["content"])
        # Usually "First Last | Title | Firm" — second part is the role
        if len(parts) >= 2:
            candidate = _clean_text(parts[1])
            if candidate:
                if "title" not in section_map:
                    section_map["title"] = []
                if candidate not in section_map["title"]:
                    section_map["title"].append(candidate)


# ---------------------------------------------------------------------------
# Regex-only fallback (when BS4 unavailable)
# ---------------------------------------------------------------------------

def _parse_sections_regex_fallback(html: str) -> dict[str, list[str]]:
    """Minimal regex fallback for environments without BeautifulSoup.

    Finds h2–h4 tags and collects list items after each heading until
    the next heading.  Less accurate than the BS4 path.
    """
    section_map: dict[str, list[str]] = {}

    # Find all headings with their positions
    heading_pattern = re.compile(
        r"<h([2-4])[^>]*>(.*?)</h\1>",
        re.IGNORECASE | re.DOTALL,
    )
    headings = list(heading_pattern.finditer(html))

    for idx, match in enumerate(headings):
        raw_heading = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        canonical_key = normalize_section_title(raw_heading)

        start = match.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(html)
        chunk = html[start:end]

        # Extract list items
        items = re.findall(r"<li[^>]*>(.*?)</li>", chunk, re.IGNORECASE | re.DOTALL)
        blocks: list[str] = []
        for item in items:
            text = _clean_text(re.sub(r"<[^>]+>", "", item))
            if text and len(text) <= _MAX_BLOCK_LEN:
                blocks.append(text)

        # Fallback: grab link text if no list items
        if not blocks:
            links = re.findall(r"<a[^>]*>(.*?)</a>", chunk, re.IGNORECASE | re.DOTALL)
            for link in links:
                text = _clean_text(re.sub(r"<[^>]+>", "", link))
                if text and len(text) <= _MAX_BLOCK_LEN:
                    blocks.append(text)

        if blocks:
            if canonical_key not in section_map:
                section_map[canonical_key] = []
            for b in blocks:
                if b not in section_map[canonical_key]:
                    section_map[canonical_key].append(b)

    return section_map
