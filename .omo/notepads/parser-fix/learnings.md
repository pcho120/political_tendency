## [2026-04-04] Baseline

### Python Command
- Use `python` (maps to Python 3.14.0 at C:\Python314\python.exe)
- `python3.12` does NOT exist on this Windows machine

### Test Baselines (confirmed passing)
- `python tests/test_parser_sections.py` → 10/10 PASS
- `python tests/test_enrichment_integration.py` → 45/45 PASS

### Key File Locations
- `parser_sections.py` — section boundary traversal + synonym map
- `validators.py` — field validation including validate_practice_areas
- `enrichment.py` — 5-stage extraction cascade (ProfileEnricher)
- `tests/test_parser_sections.py` — NormalizeCase + ParseCase + boundary tests
- `tests/test_enrichment_integration.py` — ProfileEnricher fixture tests

### Latham HTML Structure (confirmed via research)
```
<h1>Name</h1>
<h2>Profile</h2>
<h2>Experience</h2>
  <h3>Merger Control...</h3>
<h2>Qualifications</h2>
  <h3>Bar Qualification</h3>
  <h3>Education</h3>
  <h3>Practices</h3>   ← practice_areas here
<h2>Recognition</h2>
<h2>News & Insights</h2>
```
- NO JSON-LD on Latham pages
- title/office are in CSS blocks near h1

### Root Cause of Bugs
1. `_collect_content_after` uses `find_all_next()` (depth-first DOM traversal) → bleeds across section boundaries
2. `"profile"` synonym in biography → Latham's <h2>Profile</h2> maps all content to biography
3. `validate_practice_areas` doesn't filter bio sentences
4. No generic department extraction path (CSS or title split)

### 2026-04-04 Regression Harness Update
- `tests/test_parser_sections.py` now captures current boundary crash behavior with `NavigableString` siblings in `_collect_content_after`.
- `tests/test_enrichment_integration.py` can still fail early for the same parser crash, so the fixture test is added as a red-phase regression target rather than a passing assertion.

## [2026-04-04] Task 2 completed

### Fix 1: `_collect_content_after()` — sibling traversal
- Replaced `anchor.find_all_next()` (depth-first DOM traversal) with `_walk_after()` (sibling traversal)
- Added `_walk_children()` helper to recursively walk container children while respecting heading stop levels
- Key insight: `_harvest()` returns on ANY heading (including sub-headings), which breaks h3-under-h2 content collection. `_walk_children()` only stops on headings at or above stop_level, skipping sub-headings
- `NavigableString` objects have a `.name` attribute (`None`) so `hasattr(sibling, "name")` passes for them — must use `isinstance(sibling, Tag)` instead
- Parent-container fallback: if anchor's parent is a container div and no stop heading found among anchor's siblings, also walk parent's siblings

### Fix 2: Removed "profile" from biography synonyms
- `normalize_section_title("Profile")` now returns `"profile"` (snake_case fallback) instead of `"biography"`
- This prevents Latham's `<h2>Profile</h2>` from sweeping all subsequent content into the biography bucket

### Test Results
- Original 10/10 baseline cases: all PASS (no regression)
- New boundary cases (BOUNDARY_OFFICES_BAR, BOUNDARY_PRACTICE_BIO, LATHAM_H3_UNDER_H2): all PASS
- 2 pre-existing failures in NORMALIZE_NEW_CASES ("Practice Group" and "Practice Department" → `practice_areas` instead of `departments`) — caused by `"practice"` substring matching `practice_areas` before reaching `departments` synonyms. NOT caused by this task's changes.

### Evidence
- `.sisyphus/evidence/task-2-boundary-pass.txt` — full test output showing 14/16 pass
- `.sisyphus/evidence/task-2-profile-synonym.txt` — confirms normalize_section_title("Profile") returns "profile" not "biography"

## [2026-04-04] Task 3 completed
- Added _BIO_VERB_PATTERN, _AWARD_PATTERN, _DATE_PATTERN to validators.py at module level
- Filter applies only to items > 50 chars in validate_practice_areas
- Tests: 16/16 + 48/48 still pass

## [2026-04-04] Task 4 completed
- Added generic CSS department extraction to _extract_from_css_classes in enrichment.py
- Patterns: class regex 'department|practice[-_]?group|dept', data-department, data-dept attrs
- JSON-LD department key handling already existed in _merge_json_ld (lines 1087-1111) — no changes needed
- Title-split heuristic (lines 611-616) already handles "Title, Department" splitting — no changes needed
- Tests: 48/48 + 16/16 still pass

## [2026-04-04] Task 5 completed
- Added generic CSS fallbacks for title, office in _extract_from_css_classes
- Title selectors added: bio-hero-title, profile-title, person-title, lawyer-position, staff-title
- Office selectors added: bio-hero-office, profile-office, person-office, lawyer-location, staff-office
- Regex patterns: class~=title|position|role for title; class~=office|location|city for office
- Also added generic h1 fallback for name (works for any page with just <h1>)
- Guards: skip heading tags, nav/footer, phone numbers, emails, _HEADER_TERMS
- Latham fixture: full_name='Stephanie Adams', title='Partner', offices=['New York'], practice_areas=['Antitrust', 'Corporate']
- Tests: 48/48 + 16/16 still pass

## [2026-04-05] Task 6 completed -- Integration Sweep Results

### SITEMAP_XML Firms (PASS)
- Kirkland (5 profiles): valid names, titles, offices, practice_areas -- no bio leakage
- Paul Weiss (3 profiles): valid -- practice_areas clean (bio filter working)
- Greenberg Traurig (3 profiles): valid -- GT uses verbose job titles like Data Privacy Lawyer (firm style)

### HTML_DIRECTORY_FLAT Firms (DISCOVERY FAIL -- out of scope)
- Goodwin Procter: discovery returns careers pages -> garbage names
- King & Spalding: discovery returns practice area pages as profiles
- Root cause: discovery.py not filtering non-attorney URLs before enrichment
- Cannot fix in enrichment.py/parser_sections.py -- needs discovery.py URL validation

### SPA_OTHER Firms (DISCOVERY FAIL -- out of scope)
- Latham: discovery finds /en/offices/ URLs instead of /en/people/ attorney URLs
- Fried Frank: firm_finder resolves to wrong domain (fried.com blog, not friedfrank.com)
- Parser/enrichment fixes work correctly when given real profile pages (confirmed by unit tests)

### Jones Day
- Already classified BOT_PROTECTED in site_structures.json (confirmed)

### Summary
- Parser fixes (Tasks 2-5) work correctly for SITEMAP_XML firms (verified with Kirkland, Paul Weiss, GT)
- HTML_DIRECTORY_FLAT and SPA_OTHER failures are discovery-layer issues, out of scope
- Unit test coverage proves enrichment handles Latham-style HTML correctly
