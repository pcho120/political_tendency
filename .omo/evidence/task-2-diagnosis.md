# T2: Sitemap-Failed Firm Diagnosis Report

## Summary
- Total tested: 5 firms from the prior `firm_level_summary.csv` zero-attorney set.
- Structure types covered: `SITEMAP_XML` (2), `HTML_DIRECTORY_FLAT` (1), `HTML_ALPHA_PAGINATED` (1), `SPA_OTHER` (1).
- Code-path findings:
  - `select_strategies()` hard-returns `xml_sitemap` whenever `XML_SITEMAP` is detected (`find_attorney.py:1230-1235`), so directory fallback is skipped entirely.
  - `xml_sitemap` discovery depends on `_extract_profile_urls_from_sitemap()` plus `_is_profile_like_url()`/`_is_attorney_profile_url()` path matching.
  - `directory_listing` and `alphabet_enumeration` are still stubs that only delegate to `dom_exhaustion` (`find_attorney.py:3024-3036`).
- Main root-cause patterns:
  1. **XML short-circuit with no fallback**: if sitemap yields 0 profile URLs, the run does not switch to real directory/alphabet traversal.
  2. **Historical zero-counts that are not reproducible on current HEAD**: several firms now succeed via `xml_sitemap`, so their earlier 0-counts are stale/historical rather than current discovery defects.
  3. **Stubbed non-XML recovery**: even if directory/alphabet strategies were selected, they still collapse to generic `dom_exhaustion` rather than structure-specific enumeration.

## Discovery Code Notes

### What URL patterns does `xml_sitemap` look for?
- `_is_profile_like_url()` accepts same-domain URLs containing one of:
  - `/lawyer`
  - `/attorney`
  - `/people/`
  - `/professional`
  - `/bio/`
  - `/profile/`
  - `/team/`
  - `/our-people/`
- It also accepts regex-style professional paths like `/professionals/[a-z]/[slug]`.
- `_is_attorney_profile_url()` separately looks for full path components such as `lawyers`, `attorney`, `attorneys`, `people`, `professionals`, `bio`, `profile`, `our-team`, `team`, `person`, while rejecting directory roots and junk paths.

### Fallback chain when `xml_sitemap` finds 0 URLs
- In practice there is **no discovery fallback** if `XML_SITEMAP` is present.
- `select_strategies()` returns only `xml_sitemap` and exits early.
- After 0 URLs, the run goes straight to enrichment with an empty list, then possibly to **external directory fallback**, not to directory/alphabet discovery.

### What do `directory_listing` and `alphabet_enumeration` do today?
- Both are stubs.
- `directory_listing` -> logs and delegates to `dom_exhaustion`.
- `alphabet_enumeration` -> logs and delegates to `dom_exhaustion`.
- Neither implements firm-structure-aware directory crawling yet.

## Per-Firm Analysis

### Firm: Skadden
- Structure Type: `SITEMAP_XML`
- Prior baseline status: `outputs/firm_level_summary.csv` shows `Discovered URLs=0`, `Extracted Attorneys=0`, `Sources Tried=xml_sitemap`.
- Sitemap fetch: HTTP 200 implied; current rerun parsed the sitemap and extracted URLs successfully.
- Sitemap URL count: `500 profile URLs found` in current rerun.
- URL filter result: 500 URLs matched sitemap/profile heuristics; sample URLs were `/professionals/{letter}/{slug}` and source validation passed.
- Fallback triggered: **NO**. `select_strategies()` chose only `xml_sitemap`.
- Fallback result: N/A.
- Specific failure reason: **historical zero-count is not reproducible on current HEAD**. Current code discovers 500 URLs and returns 5 attorneys under `--limit 5`. This looks like a stale T8 result or a now-fixed historical defect, not an active sitemap-navigation failure.

### Firm: Goodwin Procter
- Structure Type: `HTML_DIRECTORY_FLAT`
- Prior baseline status: `0 discovered / 0 extracted`, `Sources Tried=xml_sitemap`.
- Sitemap fetch: HTTP 200 implied; current rerun fetched and validated sitemap data.
- Sitemap URL count: `500 profile URLs found`.
- URL filter result: 500 matched profile patterns; sample `/en/people/{letter}/{slug}` pages validated successfully.
- Fallback triggered: **NO**. XML success prevented any directory fallback.
- Fallback result: N/A.
- Specific failure reason: **historical zero-count is not reproducible now**. Current HEAD succeeds through `xml_sitemap` even though `site_structures.json` classifies the firm as `HTML_DIRECTORY_FLAT`. The earlier 0-result appears to have been a historical run artifact rather than a current discovery block.

### Firm: Morgan Lewis
- Structure Type: `HTML_ALPHA_PAGINATED`
- Prior baseline status: `0 discovered / 0 extracted`, `Sources Tried=xml_sitemap`.
- Sitemap fetch: robots-listed sitemap endpoint was reachable enough to parse, but yielded no usable profile URLs.
- Sitemap URL count: `0 profile URLs found`.
- URL filter result: 0 matched attorney-profile patterns.
- Fallback triggered: **NO discovery fallback**. The run stayed on `xml_sitemap` only; later it invoked external directory fallback after discovery had already failed.
- Fallback result: external directories found noisy records (`calbar` +50 raw) but merged 0 usable attorneys; no on-site directory/alphabet traversal was attempted.
- Root cause: **reproducible discovery design bug**. The firm is an `HTML_ALPHA_PAGINATED` directory site, but `select_strategies()` short-circuits to `xml_sitemap` because XML was detected at all. When the sitemap contains no attorney-profile URLs, the pipeline never tries real alphabetical/directory discovery.

### Firm: Faegre Drinker
- Structure Type: `SPA_OTHER`
- Prior baseline status: `0 discovered / 0 extracted`, `Sources Tried=xml_sitemap`.
- Sitemap fetch: HTTP 200 implied; current rerun extracted URLs successfully.
- Sitemap URL count: `500 profile URLs found`.
- URL filter result: 500 matched `/en/professionals/{letter}/{slug}` style profile URLs; validation passed.
- Fallback triggered: **NO**.
- Fallback result: N/A.
- Root cause: **historical zero-count not reproducible on current HEAD**. Despite the `SPA_OTHER` classification, the sitemap is currently usable and validated. This firm does not demonstrate the T10 discovery-stub gap; it demonstrates that part of the 61-firm zero set is stale.

### Firm: Steptoe
- Structure Type: `SITEMAP_XML`
- Prior baseline status: `Discovered URLs=505`, `Extracted Attorneys=0`, `Sources Tried=xml_sitemap`.
- Sitemap fetch: HTTP 200 implied; sitemap and validation both succeed.
- Sitemap URL count: current rerun logged `500 profile URLs found`.
- URL filter result: 500 matched `/en/lawyers/{slug}.html` profiles; source validation passed.
- Fallback triggered: discovery fallback **NO**; enrichment used Playwright fallback for sampled profiles.
- Fallback result: discovery remained successful; 5 attorneys were returned in the rerun.
- Root cause: **not a sitemap discovery failure**. The historical run had discovered hundreds of URLs already, so the 0-attorney outcome happened downstream of discovery. For T10 this firm is mainly evidence that some zero-attorney rows inside the "xml_sitemap failures" bucket are actually enrichment/output failures, not discovery failures.

## Root Cause Patterns

1. **XML short-circuit suppresses correct structure-specific fallback**  
   - Reproduced clearly on **Morgan Lewis**.  
   - The discovery engine recommended directory-oriented strategies, but `select_strategies()` still returned only `xml_sitemap` once XML was present.

2. **`directory_listing` / `alphabet_enumeration` are not implemented yet**  
   - Even if the pipeline reached them, both just call `dom_exhaustion`.  
   - This leaves `HTML_DIRECTORY_FLAT` and `HTML_ALPHA_PAGINATED` firms without a true recovery path when sitemap extraction is empty.

3. **The prior 61-firm bucket mixes true discovery failures with stale/historical zero rows**  
   - **Skadden, Goodwin Procter, Faegre Drinker** now succeed from the sitemap path.  
   - **Steptoe** proves some rows were not discovery failures at all because URLs were already discovered in the original summary.

4. **External directory fallback is not a substitute for on-site discovery**  
   - On **Morgan Lewis**, external directory data was noisy and rejected by hard gates.  
   - It does not fix missing on-site directory enumeration.

## Recommended Fixes / 권장 수정 방향

1. **Do not hard-return on `XML_SITEMAP` alone.**  
   Change `select_strategies()` / coverage execution so that `xml_sitemap` can be attempted first **without suppressing** `directory_listing` or `alphabet_enumeration` when XML yields 0 URLs.

2. **Implement real `directory_listing` for `HTML_DIRECTORY_FLAT`.**  
   It should:
   - probe the discovered directory path,
   - extract all static profile anchors from the directory page,
   - follow pagination when present,
   - stop depending on generic `dom_exhaustion` only.

3. **Implement real `alphabet_enumeration` for `HTML_ALPHA_PAGINATED`.**  
   It should:
   - detect A-Z navigation links,
   - visit each letter page,
   - collect profile URLs across letters and pages,
   - avoid relying on XML presence when sitemap coverage is zero.

4. **Treat `xml_sitemap=0 + directory hints present` as an explicit recovery condition.**  
   If `site_structures.json` / observation data shows `directory_path_found`, alphabet nav, or pagination, automatically enqueue the appropriate non-XML strategy.

5. **Split diagnosis buckets before future remediation work.**  
   For T10, prioritize firms that are still true discovery failures (like Morgan Lewis-style XML short-circuit cases) and exclude historical/stale rows already fixed on current HEAD.

6. **Keep external directory fallback as a last resort only.**  
   It should not replace missing first-party directory discovery because it introduces noisy, rejected records and does not solve coverage.
