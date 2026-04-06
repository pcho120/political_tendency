"""Tests for config/practice_department_map.json — practice-to-department mapping table."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "practice_department_map.json"


def _load_map() -> dict:
    """Load the practice-department mapping file."""
    with open(MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def _match_department(practice_area: str, mappings: list[dict]) -> str | None:
    """Return the department for a practice area using case-insensitive substring matching.

    Returns the department of the first matching mapping (lowest index wins for
    same-priority ties). Returns None if no mapping matches.
    """
    practice_lower = practice_area.lower()
    # Sort by priority (lower number = higher priority)
    sorted_mappings = sorted(mappings, key=lambda m: m.get("priority", 99))
    for mapping in sorted_mappings:
        for pattern in mapping["patterns"]:
            if pattern.lower() in practice_lower:
                return mapping["department"]
    return None


def test_map_loads_valid_json():
    """File loads without error and has the expected 'mappings' key."""
    data = _load_map()
    assert "mappings" in data, "Top-level key 'mappings' is missing"
    assert isinstance(data["mappings"], list), "'mappings' should be a list"
    # Each entry must have required keys
    for i, entry in enumerate(data["mappings"]):
        assert "patterns" in entry, f"Entry {i} missing 'patterns'"
        assert "department" in entry, f"Entry {i} missing 'department'"
        assert "priority" in entry, f"Entry {i} missing 'priority'"
        assert isinstance(entry["patterns"], list), f"Entry {i} 'patterns' should be a list"
        assert len(entry["patterns"]) > 0, f"Entry {i} has empty 'patterns'"


def test_map_coverage():
    """At least 20 mappings exist."""
    data = _load_map()
    count = len(data["mappings"])
    assert count >= 20, f"Expected at least 20 mappings, got {count}"


def test_litigation_mapping():
    """'Securities Litigation' should map to 'Litigation'."""
    data = _load_map()
    dept = _match_department("Securities Litigation", data["mappings"])
    assert dept == "Litigation", f"Expected 'Litigation', got '{dept}'"


def test_corporate_mapping():
    """'Mergers & Acquisitions' should map to 'Corporate'."""
    data = _load_map()
    dept = _match_department("Mergers & Acquisitions", data["mappings"])
    assert dept == "Corporate", f"Expected 'Corporate', got '{dept}'"


# ---------------------------------------------------------------------------
# Inference fallback tests (T11)
# ---------------------------------------------------------------------------

from enrichment import infer_department_from_practices


def test_department_inferred_from_practice_areas():
    """When department is empty but practice_areas has 'Securities Litigation', infer 'Litigation (inferred)'."""
    result = infer_department_from_practices(
        practice_areas=["Securities Litigation", "M&A"],
        department=[],
    )
    assert result == ["Litigation (inferred)"], f"Expected ['Litigation (inferred)'], got {result}"


def test_direct_department_not_overridden():
    """When department already has a value, mapping does NOT override it."""
    result = infer_department_from_practices(
        practice_areas=["Securities Litigation"],
        department=["Corporate"],
    )
    assert result == [], f"Expected [], got {result}"


def test_no_practice_area_no_inference():
    """When practice_areas is empty, department stays empty."""
    result = infer_department_from_practices(
        practice_areas=[],
        department=[],
    )
    assert result == [], f"Expected [], got {result}"
