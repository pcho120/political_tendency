#!/usr/bin/env python3
"""field_merger.py - Field-Level Attorney Profile Merger

Merges AttorneyProfile objects from multiple sources using a
precedence-based strategy. Each field tracks which source URL
contributed it.

Precedence (higher = more trusted):
    profile_core=100, mixed=90, attorney_list=80,
    education=70, bar_admission=70, practice=60, external_directory=30

Rules:
  - Scalar fields (full_name, title): higher precedence wins; ties keep existing
  - List fields (offices, department, practice_areas, industries,
    bar_admissions, education): higher precedence OVERWRITES; same
    precedence UNION-DEDUP (merge without duplicates)
  - Every field that receives a value tracks source_origin_per_field[field] = source_url
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from attorney_extractor import AttorneyProfile, EducationRecord

# ---------------------------------------------------------------------------
# Precedence registry (mirrors FIELD_SOURCE_PRECEDENCE in find_attorney.py)
# ---------------------------------------------------------------------------
FIELD_SOURCE_PRECEDENCE: dict[str, int] = {
    "profile_core": 100,
    "mixed": 90,
    "attorney_list": 80,
    "education": 70,
    "bar_admission": 70,
    "practice": 60,
    "external_directory": 30,
}

# Fields and their merge type
SCALAR_FIELDS: tuple[str, ...] = ("full_name", "title")
LIST_FIELDS: tuple[str, ...] = (
    "offices",
    "department",
    "practice_areas",
    "industries",
    "bar_admissions",
    "education",
)
ALL_PROFILE_FIELDS: tuple[str, ...] = SCALAR_FIELDS + LIST_FIELDS


# ---------------------------------------------------------------------------
# Extended profile dataclass
# ---------------------------------------------------------------------------
@dataclass
class MergedAttorneyProfile(AttorneyProfile):
    """AttorneyProfile extended with per-field source tracking and merge log."""

    # field_name -> source_url that supplied the current value
    source_origin_per_field: dict[str, str] = field(default_factory=dict)

    # Human-readable log of every merge decision
    merge_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["source_origin_per_field"] = self.source_origin_per_field
        d["merge_log"] = self.merge_log
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _precedence(source_type: str) -> int:
    """Return numeric precedence for a source type (default 50 if unknown)."""
    return FIELD_SOURCE_PRECEDENCE.get(source_type, 50)


def _dedup_list(lst: list) -> list:
    """Return list with duplicates removed (order-preserving).

    Handles both plain values and EducationRecord objects.
    """
    seen: set[str] = set()
    result = []
    for item in lst:
        key = str(item.to_dict()) if isinstance(item, EducationRecord) else str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _is_empty(value: Any) -> bool:
    """True if the value is None or an empty list/string."""
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, str):
        return value.strip() == ""
    return False


# ---------------------------------------------------------------------------
# Core merger class
# ---------------------------------------------------------------------------

class FieldMerger:
    """Merges AttorneyProfile objects from multiple sources.

    Usage
    -----
    merger = FieldMerger()

    # Single incremental merge (mutates base in-place):
    merger.merge(base, supplement, source_url="https://...", source_type="education")

    # Collapse a list of (profile, url, type) into one best profile:
    merged = merger.merge_all([(p1, url1, "profile_core"), (p2, url2, "education")])
    """

    def merge(
        self,
        base: AttorneyProfile,
        supplement: AttorneyProfile,
        source_url: str,
        source_type: str,
    ) -> None:
        """Merge fields from *supplement* into *base* using precedence rules.

        Mutates *base* in-place.
        If *base* is a ``MergedAttorneyProfile``, updates tracking metadata.
        If *base* is a plain ``AttorneyProfile``, silently skips tracking.
        """
        src_prec = _precedence(source_type)
        is_merged = isinstance(base, MergedAttorneyProfile)

        # Per-field precedence cache (stored on the object to survive incremental merges)
        if not hasattr(base, "_field_precedence_cache"):
            object.__setattr__(base, "_field_precedence_cache", {})
        prec_cache: dict[str, int] = base._field_precedence_cache  # type: ignore[attr-defined]

        for f_name in ALL_PROFILE_FIELDS:
            new_val = getattr(supplement, f_name, None)
            if _is_empty(new_val):
                continue  # Nothing to contribute

            cur_val = getattr(base, f_name, None)
            cur_prec = prec_cache.get(f_name, 0)

            if f_name in SCALAR_FIELDS:
                # --- Scalar merge ---
                if _is_empty(cur_val):
                    # Empty — take new value
                    setattr(base, f_name, new_val)
                    prec_cache[f_name] = src_prec
                    if is_merged:
                        base.source_origin_per_field[f_name] = source_url  # type: ignore[union-attr]
                        base.merge_log.append(  # type: ignore[union-attr]
                            f"SET   {f_name}={new_val!r} from {source_type}({source_url})"
                        )
                elif src_prec > cur_prec:
                    # Higher precedence — overwrite
                    old_val = cur_val
                    setattr(base, f_name, new_val)
                    prec_cache[f_name] = src_prec
                    if is_merged:
                        base.source_origin_per_field[f_name] = source_url  # type: ignore[union-attr]
                        base.merge_log.append(  # type: ignore[union-attr]
                            f"OVR   {f_name}: {old_val!r} -> {new_val!r}  "
                            f"({cur_prec} -> {src_prec}) from {source_type}({source_url})"
                        )
                # Same or lower precedence for scalar — keep existing, no-op

            else:
                # --- List merge ---
                cur_list: list = cur_val if isinstance(cur_val, list) else []

                if _is_empty(cur_list):
                    # Empty — take new list
                    new_list = list(new_val)
                    setattr(base, f_name, new_list)
                    prec_cache[f_name] = src_prec
                    if is_merged:
                        base.source_origin_per_field[f_name] = source_url  # type: ignore[union-attr]
                        base.merge_log.append(  # type: ignore[union-attr]
                            f"SET   {f_name}=[{len(new_list)} items] from {source_type}({source_url})"
                        )
                elif src_prec > cur_prec:
                    # Higher precedence — overwrite entirely
                    new_list = list(new_val)
                    setattr(base, f_name, new_list)
                    prec_cache[f_name] = src_prec
                    if is_merged:
                        base.source_origin_per_field[f_name] = source_url  # type: ignore[union-attr]
                        base.merge_log.append(  # type: ignore[union-attr]
                            f"OVR   {f_name}: [{len(cur_list)} items] -> [{len(new_list)} items] "
                            f"({cur_prec} -> {src_prec}) from {source_type}({source_url})"
                        )
                elif src_prec == cur_prec:
                    # Same precedence — union-dedup
                    merged_list = _dedup_list(cur_list + list(new_val))
                    added = len(merged_list) - len(cur_list)
                    if added > 0:
                        setattr(base, f_name, merged_list)
                        if is_merged:
                            # source_origin stays with whichever came first at this precedence
                            if f_name not in base.source_origin_per_field:  # type: ignore[union-attr]
                                base.source_origin_per_field[f_name] = source_url  # type: ignore[union-attr]
                            base.merge_log.append(  # type: ignore[union-attr]
                                f"UNION {f_name}: +{added} items from {source_type}({source_url})"
                            )
                # Lower precedence list — keep existing, no-op

        # Recalculate extraction status after merge
        if hasattr(base, "calculate_status"):
            base.calculate_status()

    def merge_all(
        self,
        profiles_with_sources: list[tuple[AttorneyProfile, str, str]],
    ) -> MergedAttorneyProfile:
        """Collapse a list of (profile, source_url, source_type) into one best profile.

        Profiles are sorted by descending precedence before merging so that the
        highest-precedence source always forms the authoritative base for scalar
        fields when multiple sources exist at the same precedence.

        Returns a ``MergedAttorneyProfile``.
        """
        if not profiles_with_sources:
            raise ValueError("profiles_with_sources must not be empty")

        # Sort by descending precedence so the most trusted source leads
        sorted_profiles = sorted(
            profiles_with_sources,
            key=lambda t: _precedence(t[2]),
            reverse=True,
        )

        lead_profile, lead_url, lead_type = sorted_profiles[0]

        # Build MergedAttorneyProfile from the leading profile
        base = MergedAttorneyProfile(
            firm=lead_profile.firm,
            profile_url=lead_profile.profile_url,
            full_name=lead_profile.full_name,
            title=lead_profile.title,
            offices=list(lead_profile.offices),
            department=list(lead_profile.department),
            practice_areas=list(lead_profile.practice_areas),
            industries=list(lead_profile.industries),
            bar_admissions=list(lead_profile.bar_admissions),
            education=list(lead_profile.education),
            extraction_status=lead_profile.extraction_status,
            missing_fields=list(lead_profile.missing_fields),
            diagnostics=copy.deepcopy(lead_profile.diagnostics),
        )
        base.merge_log.append(
            f"INIT  base from {lead_type}({lead_url})"
        )

        # Seed precedence cache from the leading profile
        lead_prec = _precedence(lead_type)
        base._field_precedence_cache = {}  # type: ignore[attr-defined]
        for f_name in ALL_PROFILE_FIELDS:
            val = getattr(base, f_name, None)
            if not _is_empty(val):
                base._field_precedence_cache[f_name] = lead_prec  # type: ignore[attr-defined]
                base.source_origin_per_field[f_name] = lead_url

        # Merge remaining profiles in precedence order
        for supplement, src_url, src_type in sorted_profiles[1:]:
            self.merge(base, supplement, source_url=src_url, source_type=src_type)

        return base


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

def _demo():
    """Quick smoke test for field_merger.py."""
    from attorney_extractor import EducationRecord

    p1 = AttorneyProfile(
        firm="Test Firm",
        profile_url="https://testfirm.com/people/alice",
        full_name="Alice Smith",
        title="Partner",
        offices=["New York"],
        practice_areas=["Litigation"],
        bar_admissions=["New York"],
    )

    p2 = AttorneyProfile(
        firm="Test Firm",
        profile_url="https://testfirm.com/people/alice/education",
        education=[EducationRecord(degree="JD", school="Harvard Law", year=2005)],
        bar_admissions=["New York", "California"],
        industries=["Finance"],
    )

    p3 = AttorneyProfile(
        firm="Test Firm",
        profile_url="https://martindale.com/alice-smith",
        full_name="A. Smith",  # Lower-quality name from external dir
        offices=["New York", "London"],
        practice_areas=["Litigation", "Corporate"],
    )

    merger = FieldMerger()
    merged = merger.merge_all([
        (p1, p1.profile_url, "profile_core"),
        (p2, p2.profile_url, "education"),
        (p3, p3.profile_url, "external_directory"),
    ])

    import json
    print(json.dumps(merged.to_dict(), indent=2, default=str))
    print("\nMerge log:")
    for line in merged.merge_log:
        print(" ", line)


if __name__ == "__main__":
    _demo()
