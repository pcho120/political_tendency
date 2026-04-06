## [T1] pytest infrastructure
- pytest installed at: C:\Users\kunch\AppData\Roaming\Python\Python314\Scripts
- conftest.py fixtures: html_fixture(name), jsonl_fixture(name)
- pyproject.toml added with testpaths=["tests"]
- Gotcha: `pytest`/`python3.12` were not on PATH; used `python -m pytest` and `pip`.

## [T2] Test conversion
- Converted 4 test files, 40 total test functions created
- No live-network tests required skipping; enrichment tests ran successfully with local fixtures
- Parametrize patterns used for validator and parser case tables; standalone harness mains preserved for backward compatibility

## [T3] Baseline measurement
- Firms used: [Kirkland, SITEMAP_XML], [Davis Polk, HTML_DIRECTORY_FLAT], [Jones Day, BOT_PROTECTED]
- department_sentinel_rate: Kirkland=0.0, Davis Polk=0.9, Jones Day=N/A (0 attorneys)
- industries_sentinel_rate: Kirkland=1.0, Davis Polk=1.0 — industries sentinel "no industry field" is universal
- education_sentinel_rate: 0.0 for both extractable firms — education parsing works well
- title_empty_rate: 0.0 — titles always extracted
- office_empty_rate: Kirkland=0.3 (some international attorneys have no US office), Davis Polk=0.1
- practice_areas_empty_rate: 0.0 — practice areas always found
- bar_admissions_empty_rate: Kirkland=0.3, Davis Polk=0.0
- Jones Day: BOT_PROTECTED, all profile requests return 403, 0 attorneys extracted (expected)
- find_attorney.py CLI: uses `--limit` (not `--max-profiles`), requires Excel path positional arg
- find_attorney.py appends to `outputs/attorneys.jsonl` across runs (not per-firm files)
- JSONL field names match AttorneyProfile.to_dict(): firm, profile_url, full_name, title, offices, department, practice_areas, industries, bar_admissions, education (list of {degree,school,year}), extraction_status, missing_fields, diagnostics
- Key insight: industries is 100% sentinel ("no industry field") across all firms — this is the highest-priority field to improve
- Key insight: department extraction varies widely by firm (0% sentinel for Kirkland vs 90% for Davis Polk)

## [T4] Department RED tests
- Test function names: test_extract_department_from_json_ld, test_extract_department_from_css_class, test_extract_department_from_heading, test_department_contamination_filter, test_department_empty_returns_sentinel
- How many failed (RED): 5/5
- Current _extract_departments_bs4 approach: class regex on department/group/division/section plus heading-following-sibling text only; no JSON-LD, no accordion/tab handling, no sentinel fallback
- What fixture HTML structures were created: JSON-LD person schema with department, CSS class-based department block, accordion/tab panel department block

## [T5] Industries RED tests
- Test function names: test_extract_industries_from_heading, test_extract_industries_from_json_ld, test_extract_industries_from_sidebar, test_industries_vs_practice_areas, test_industries_empty_returns_sentinel
- How many failed (RED): 5/5
- Current _extract_industries_bs4 approach: delegates to _extract_section_items_after_header(soup, ['industr']) and returns raw section items only
- Fixture HTML structures created: heading-based industries list, sidebar/aside industries list, JSON-LD knowsAbout industries data

## [T6] Martindale compliance fix
- Replaced Martindale `/search/` and `/api/search/` usage with compliant sitemap-driven `/organization/` discovery plus direct `/attorney/` profile handling.
- Martindale robots.txt key rules: `Disallow: /search/`, `Disallow: /cdn-cgi/`, `Disallow: /assets/html/profiles/`; allowed sitemap paths include `sitemap_profiles.xml`, `sitemap_browse.xml`, and `sitemap_new_profiles.xml`.
- Test approach used: `responses` HTTP mocks for sitemap and organization pages, plus direct unit assertions on mapping/filter logic.
- Rate limit: 3.0 seconds implemented.
- Martindale URL structure issue: firm discovery cannot rely on search endpoints, so compliant fallback now matches firm slugs from allowed sitemap URLs and organization pages only.

## [T7] Practice-department mapping table
- Total mappings: 24
- JSON structure: {mappings: [{patterns: [...], department: "...", priority: N}]}
- Mapping logic: case-insensitive substring matching
- config/practice_department_map.json created

## [T8] Missing firm probing
- Started with: 190 entries in site_structures.json
- Firms found missing: DLA Piper (verein), Skadden, Gibson Dunn, Sidley, Ropes & Gray, Baker McKenzie (verein), White & Case, Morgan Lewis, Hogan Lovells, Simpson Thacher (ranks 3-12)
- Added firms:
  - DLA Piper (verein) → HTML_ALPHA_PAGINATED (directory at /people with pagination)
  - Skadden → SITEMAP_XML (sitemap has attorney URLs at /professionals/)
  - Gibson Dunn → HTML_ALPHA_PAGINATED (directory at /people with pagination)
  - Sidley → HTML_ALPHA_PAGINATED (directory at /people with alphabet nav)
  - Ropes & Gray → BOT_PROTECTED (Cloudflare 403, robots.txt accessible but sitemap blocked)
  - Baker McKenzie (verein) → SITEMAP_XML (multilingual sitemaps with attorney URLs at /en/people/)
  - White & Case → BOT_PROTECTED (403 on all pages including robots.txt, not Cloudflare)
  - Morgan Lewis → HTML_ALPHA_PAGINATED (directory at /people with alphabet nav)
  - Hogan Lovells → BOT_PROTECTED (Cloudflare 403, robots.txt accessible but sitemap blocked)
  - Simpson Thacher → HTML_ALPHA_PAGINATED (directory at /people with alphabet nav)
- Final count: 200 entries
- Gotcha: Windows cp949 encoding breaks on unicode chars in page titles — need sys.stdout.reconfigure(encoding='utf-8')
- Gotcha: 3 firms (Ropes & Gray, White & Case, Hogan Lovells) return 403 on homepage but probe script treated timeout errors as "failed to fetch" rather than bot protection — needed manual re-probe
- site_structures.json is a JSON array (not dict), sorted by rank

## [T9] Department GREEN fixes
- `_extract_departments_bs4()` had to be fixed in the active later `AttorneyExtractor` definition; this file contains duplicate class/method blocks and the earlier copy is not the one exercised by tests.
- Department extraction now supports JSON-LD Person `department` values, class/data-attribute containers, and heading/accordion panels while avoiding full-container `.get_text()` contamination.
- Safe department candidates come from heading/inline label text only, with nav/UI junk, long blobs, and biography-style sentences filtered out.
- Department empty sentinel stays `[]`; that differs from industries, which still use `['no industry field']`.
- Full regression run after Task 9 showed department tests green and no breakage in previously passing suites; the remaining failures were the pre-existing industries red tests.

## [T10] Industries GREEN fixes
- `_extract_industries_bs4()` had to be fixed in the active later `AttorneyExtractor` definition; the earlier duplicate method body is not the one exercised by pytest.
- Industries extraction now unions JSON-LD `knowsAbout`, class/data-attribute containers (`[class*=industr]`, `[class*=sector]`, `[data-industry]`), sidebar/aside sections, and heading-based sections with industry/sector/market synonyms.
- Results are deduplicated in insertion order and fall back to the required sentinel `['no industry field']` when no industry candidates survive extraction.
- RED fixtures needed source-data fixes: added `Asset Management` to `industries_heading_section.html`, `Infrastructure` to `industries_sidebar.html`, and `Life Sciences` to the inline industries HTML in `test_industries_vs_practice_areas`.
- `parser_sections.SECTION_SYNONYMS['industries']` now also recognizes `industry focus`, `industry experience`, `industries served`, singular `sector`, and singular `market` without breaking existing parser regression tests.

## [T11] Practice→Department inference fallback
- Function: `infer_department_from_practices(practice_areas, department)` in `enrichment.py` (module-level, exported)
- Location in pipeline: called inside `ProfileEnricher._validate_fields()` AFTER `validate_department()` cleans/empties the list, but BEFORE `validate_practice_areas()` runs — so practice_areas are still raw at that point (doesn't matter since patterns are substring-based)
- Mapping table loaded lazily from `config/practice_department_map.json` and cached in module-level `_PRACTICE_DEPARTMENT_MAP` global
- Mappings sorted by priority (lower number = higher priority) at load time
- Returns first match's department with `" (inferred)"` suffix, e.g. `["Litigation (inferred)"]`
- Returns `[]` if department already populated, practice_areas empty, or no mapping matched
- `validators.py` `validate_department()` passes `"(inferred)"` suffixed values through without filtering — no changes needed there
- 3 new tests added to `tests/test_practice_department_map.py`: `test_department_inferred_from_practice_areas`, `test_direct_department_not_overridden`, `test_no_practice_area_no_inference`
- Total test count: 61 (58 existing + 3 new), all passing

## [T12] Post-change 3-firm re-extraction & baseline comparison
- Ran find_attorney.py --limit 10 --debug-firm for Kirkland, Davis Polk, Jones Day
- Python env: python 3.14.0 on Windows, encoding='utf-8' required for JSON reads (cp949 default fails on unicode chars)
- find_attorney.py does NOT write to outputs/attorneys.jsonl by default ? uses root attorneys.jsonl and debug_reports/{Firm}_attorneys.json
- Jones Day: all 10 profile URLs returned HTTP 403, Playwright fallback timed out at 300s (BOT_PROTECTED confirmed)
- Kirkland results: dept=0.0 (same as baseline), ind=1.0 (same) ? kirkland.com simply does not expose industries on profiles
- Davis Polk results: dept=1.0 (baseline was 0.9 ? regression), ind=0.1 (baseline was 1.0 ? HUGE improvement)
- The T11 practice��department fallback did NOT fire for Davis Polk ? needs investigation (maybe practice_areas format doesn't match mapping patterns)
- Davis Polk industries extraction has noise contamination: some attorneys have transaction descriptions (e.g. 'Owen and Minor note offering') mixed in with real industries
- Key win: T10's _extract_industries_bs4() reduced Davis Polk industries sentinel from 100% to 10%
- Key gap: Davis Polk department still 100% sentinel ? site genuinely lacks department field, and practice��dept mapping didn't trigger
