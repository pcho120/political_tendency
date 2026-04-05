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
#
# Each entry is either:
#   - A plain string: matches if the string appears anywhere in the normalized heading
#   - A tuple (substring, require_any): matches only if substring is present AND
#     at least one word from require_any appears in the normalized heading.
#     This prevents generic headings (e.g. "Working Group", "Section 1: Contact")
#     from matching risky short synonyms.
# ---------------------------------------------------------------------------

# Legal/professional context words that qualify the risky bare synonyms
_SERVICE_QUALIFIERS = frozenset({
    "practice", "legal", "advisory", "professional", "attorney", "law",
})
# Qualifiers that distinguish "Practice Areas" / "Practice Services" (→ practice_areas)
# from "Practice Group" / "Practice Section" (→ departments)
_PRACTICE_AREA_QUALIFIERS = frozenset({
    "area", "areas", "service", "services", "expertise", "focus",
    "specialty", "specialties", "specialization", "specializations",
    "competency", "competencies", "capability", "capabilities",
})
_GROUP_QUALIFIERS = frozenset({
    "practice", "industry", "litigation", "corporate", "tax", "advisory",
    "regulatory", "transactional", "antitrust", "employment", "real estate",
    "banking", "finance", "securities", "intellectual", "environmental",
})
_SECTION_QUALIFIERS = frozenset({
    "practice", "tax", "litigation", "corporate", "bar", "regulatory",
    "transactional", "employment", "antitrust", "banking", "finance",
    "securities", "intellectual", "environmental", "real estate",
})

# Type alias for synonym entries
_SynonymEntry = "str | tuple[str, frozenset[str]]"

SECTION_SYNONYMS: dict[str, list[_SynonymEntry]] = {
    "practice_areas": [
        "practice area",
        "practice areas",
        "practices",
        # bare "practice" only fires when accompanied by area-context qualifier
        # e.g. "Practice Areas" ✓, "Practice Services" ✓, but NOT "Practice Group" ✗ (→ departments)
        ("practice", _PRACTICE_AREA_QUALIFIERS),
        "capabilities",
        "expertise",
        "focus area",
        "focus areas",
        "specialt",           # covers specialties / specializations
        "competen",           # covers competencies
        # "service" only fires when accompanied by a professional-context qualifier
        # e.g. "Practice Services" ✓  but NOT "Client Services Team" ✗
        ("service", _SERVICE_QUALIFIERS),
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
        # bare "group" replaced by qualified form to avoid "Working Group" false positives
        ("group", _GROUP_QUALIFIERS),
        "practice group",
        "practice groups",
        "industry group",
        "industry groups",
        "division",
        # bare "section" replaced by qualified form to avoid "Section 1: Contact" false positives
        ("section", _SECTION_QUALIFIERS),
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
    """Walk forward siblings (and parent siblings) after *anchor* until the
    next heading at the same or higher level, collecting text blocks from
    content tags.

    Uses sibling traversal instead of ``find_all_next()`` to prevent
    depth-first DOM walking that bleeds content across section boundaries.

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

    def _walk_after(node: Tag, level: int) -> bool:
        """Walk siblings of *node* starting after node itself.

        Returns True if a stop heading was found (caller should stop too).
        """
        for sibling in node.next_siblings:
            if isinstance(sibling, NavigableString) or not isinstance(sibling, Tag):
                continue
            # Footer check
            sib_classes = set(sibling.get("class") or [])
            sib_id = (sibling.get("id") or "").lower()
            if sibling.name in _FOOTER_CONTAINER_NAMES:
                return True
            if sib_classes & _FOOTER_CONTAINER_CLASSES:
                return True
            if any(fc in sib_id for fc in _FOOTER_CONTAINER_CLASSES):
                return True
            # Stop at heading of same or higher level
            if _is_heading(sibling):
                sib_level = _tag_heading_level(sibling)
                if sib_level <= level:
                    return True  # stop
                # Sub-heading: skip it, keep going to collect its siblings
                continue
            # Collect from content nodes
            if sibling.name in _CONTENT_TAGS:
                text = _clean_text(sibling.get_text())
                if text and len(text) <= _MAX_BLOCK_LEN and text not in seen:
                    seen.add(text)
                    blocks.append(text)
            elif sibling.name in {"ul", "ol", "dl", "div", "section", "article",
                                   "table", "tbody", "tr", "aside"}:
                if _walk_children(sibling, level):
                    return True
        return False

    def _walk_children(container: Tag, level: int) -> bool:
        """Walk children of a container, respecting stop_level for headings.

        Returns True if a stop heading was found (caller should stop).
        """
        for child in container.children:
            if isinstance(child, NavigableString) or not isinstance(child, Tag):
                continue
            # Stop at heading of same or higher level
            if _is_heading(child):
                child_level = _tag_heading_level(child)
                if child_level <= level:
                    return True  # stop
                # Sub-heading: skip heading text, keep going
                continue
            # Collect from content nodes
            if child.name in _CONTENT_TAGS:
                text = _clean_text(child.get_text())
                if text and len(text) <= _MAX_BLOCK_LEN and text not in seen:
                    seen.add(text)
                    blocks.append(text)
            elif child.name in {"ul", "ol", "dl", "div", "section", "article",
                                 "table", "tbody", "tr", "aside"}:
                if _walk_children(child, level):
                    return True
        return False

    # Walk siblings of anchor at anchor's own level
    stopped = _walk_after(anchor, stop_level)

    # If anchor's parent is a container and we didn't hit a stop heading,
    # also walk the parent's siblings to handle content wrapped in container divs
    if not stopped:
        parent = anchor.parent
        if parent and getattr(parent, "name", None) in {
            "div", "section", "article", "main", "aside",
        }:
            _walk_after(parent, stop_level)

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

    Synonym entries are either plain strings (substring match) or
    (substring, require_any) tuples where require_any is a frozenset of
    qualifier words — at least one must appear in the heading for a match.

    Args:
        raw: Raw heading text (e.g. "Practice Areas", "Bar Admissions & Courts")

    Returns:
        Canonical key (e.g. "practice_areas", "bar_admissions") or a
        snake_case version of raw if no synonym matched.
    """
    normalized = _strip_punctuation_for_matching(raw)
    words = set(normalized.split())

    for canonical_key, synonyms in SECTION_SYNONYMS.items():
        for syn in synonyms:
            if isinstance(syn, tuple):
                substring, require_any = syn
                if substring in normalized and (words & require_any):
                    return canonical_key
            else:
                # Plain string: substring match — heading may contain extra words
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
