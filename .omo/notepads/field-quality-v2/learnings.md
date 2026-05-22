# Field Quality V2 — Learnings

## Inherited Wisdom (from pipeline-remediation)

- **Two pipelines**: `find_attorney.py` uses `field_enricher.py` + `MultiModeExtractor`; `run_pipeline.py` uses `enrichment.py`'s `ProfileEnricher`. Features added to one don't appear in the other.
- **Department inference**: Added to `find_attorney.py` at line ~4590 (after field_enricher.enrich() in `_enrich_profile_urls()`). It imports `infer_department_from_practices` from `enrichment.py`.
- **Target pipeline**: `find_attorney.py` is the T8 production pipeline — ALL changes go here.
- **Python env**: Use `python` (3.14.0 on Windows). Always `encoding='utf-8'`.
- **Test pattern**: Standalone scripts (no pytest). Run directly with `python test_script.py`.

## Inherited Wisdom (from field-quality-fillrate-all-firms)

- **JSONL record shape**: `firm`, `profile_url`, `full_name`, `title`, `offices` (list), `department` (list or scalar), `practice_areas` (list), `industries` (list), `bar_admissions` (list), `education` (list), `extraction_status`, `missing_fields`, `diagnostics`.
- **industries sentinel**: `["no industry field"]` — don't touch industries.
- **validate_offices()**: already accepts international offices, returns `international_office` tag.
- **validate_title()**: does alias normalization (Of counsel → Of Counsel). Is NOT a whitelist — uncommon titles pass.
- **_JUNK_PHRASES**: if substring match is used, must convert to exact before adding new nav words (risk: "coverage" blocks "Insurance Coverage").

## Session Notes

## [2026-04-10 17:15] Task: T1-baseline
- Captured baseline fill rates from existing `attorneys.jsonl` and generated per-firm samples from isolated `find_attorney.py --output-dir` runs.
- Output files written under `.sisyphus/evidence/task-1-baseline/`; four test firms captured at 10 records each.
- Jones Day was not run; site-structure confirmation for BOT_PROTECTED could not be found in the current `site_structures.json`, so the summary currently marks it skipped with a note.
- Key baseline rates: title 0.912253, offices 0.741003, department 0.590469, practice_areas 0.599378.

## [2026-04-10 17:33] Task: T2-diagnosis
- Confirmed the main reproducible discovery bug is `select_strategies()` hard-returning `xml_sitemap` whenever XML is detected, even for firms classified as `HTML_ALPHA_PAGINATED` / `HTML_DIRECTORY_FLAT`.
- `directory_listing` and `alphabet_enumeration` are still stubs that only delegate to `dom_exhaustion`; T10 needs real implementations, not more XML tuning alone.
- Morgan Lewis is the clearest representative failure: sitemap produced 0 URLs, no on-site directory fallback ran, and external directories only added noisy rejected records.
- Several firms in the historical 0-attorney bucket are stale on current HEAD (Skadden, Goodwin Procter, Faegre Drinker all succeeded in reruns), so T10 should target still-reproducible discovery failures first.
- Some "xml_sitemap failures" are actually post-discovery issues (e.g. Steptoe had hundreds of discovered URLs in the summary but 0 extracted attorneys), so remediation should separate discovery failures from enrichment failures.

## [2026-04-10] Task: T3-practice-areas-nav-pollution
- **Critical bug found**: `_JUNK_PHRASES` used substring matching (`any(junk in practice.lower() for junk in _JUNK_PHRASES)`) — this meant adding "search" would block "Insurance Coverage Search", "people" would block "People Analytics", etc.
- **Fix**: Changed to exact match (`practice.lower().strip() in _JUNK_PHRASES`). Since `_JUNK_PHRASES` is a frozenset, `in` lookup is O(1) and more efficient than the old `any()` loop.
- **Added 20 nav/UI items** to `_JUNK_PHRASES`: home, search, menu, back to menu, main menu, close, login, sign in, sign up, subscribe, submit, contact us, about us, careers, news, events, site map, back, next, previous, print, share, email this page, offices, people, professionals.
- **Additional filters added**: pure-numeric rejection (`isdigit()`), stricter too-short filter (`<= 2` chars instead of `< 2`), `www.` URL detection.
- **"people" is safe as exact match**: "People Analytics" lowercased is "people analytics" which does NOT match "people" exactly. Verified in test.
- **Evidence**: `.sisyphus/evidence/task-3-nav-filter-test.txt` (PASS), `.sisyphus/evidence/task-3-no-false-positive.txt` (PASS).

## [2026-04-10] Task: T4-office-validation-relaxation
- **Root cause of low office fill rate**: `validate_offices()` at the `City, ST` path (line ~435) required the city to be in `_US_MAJOR_LAW_CITIES` frozenset. Any city not in the ~120-entry list was silently dropped. Minor but valid US cities like Bethesda MD, Dayton OH, Scranton PA were rejected.
- **Fix 1 — City+StateCode acceptance**: Changed both the comma-free (`Boston MA`) and comma (`City, ST`) paths to accept ANY city name + valid US state abbreviation, subject to garbage filters.
- **Fix 2 — Garbage city filter**: Added `_GARBAGE_CITY_WORDS` frozenset (30+ UI/nav words: click, view, read, more, here, back, next, etc.) to prevent accepting `Click Here, NY` or `View More, CA` as valid offices. Also enforced `len(city) >= 3` to reject too-short city names.
- **Fix 3 — International rejection**: Changed the fallback else-clause to REJECT unknown/international entries instead of accepting them with `saw_international` flag. Previously `London, UK` and `Tokyo, Japan` were accepted with `international_office` reason tag.
- **Fix 4 — Expanded _US_MAJOR_LAW_CITIES**: From ~120 to ~150+ entries. Added: Rochester, Syracuse, Scranton, Allentown, Dover, Annapolis, Rockville, Bethesda, Greenwich, Warwick, Burlington, Montpelier, Fairfax, Arlington, Norfolk, Roanoke, Durham, Greensboro, Winston-Salem, Columbia, Charleston, Savannah, Augusta, Knoxville, Little Rock, Fort Smith, Jackson, Biloxi, Shreveport, Dayton, Toledo, Fort Wayne, South Bend, Green Bay, Cedar Rapids, Lincoln, Fargo, Bismarck, Sioux Falls, Rapid City, Scottsdale, Oklahoma City, Tulsa, Santa Fe, Provo, Ogden, Billings, Missoula, Great Falls, Cheyenne, Casper, Twin Falls, Pasadena, Long Beach, Salem, Eugene, Juneau, Hilo, San Juan.
- **Added PR (Puerto Rico)** to `_US_STATE_ABBR`.
- **Behavioral change note**: International offices (London, Tokyo, etc.) are now REJECTED, not accepted with a flag. This is per task requirements but represents a change in behavior. If pipeline later needs international offices, the fallback should be restored.
- **Evidence**: task-4-minor-cities.txt (PASS), task-4-regression.txt (PASS), task-4-garbage-reject.txt (PASS).

## [2026-04-10] Task: T5-title-validation-relaxation
- **Title length relaxed**: `validate_title()` now allows titles up to 200 chars instead of 120.
- **Firm-name contamination relaxed**: Reject only when firm-name token overlap exceeds 50% of the title tokens, which keeps titles like `Partner at Crowell & Moring LLP` while still rejecting pure firm-name titles like `Crowell & Moring LLP`.
- **Identifier filter softened**: Replaced the broad camelCase regex with a JavaScript-identifier-style check that only rejects single-token identifiers with internal case transitions, so normal capitalized titles are preserved.
- **Email/phone checks kept**: Contamination filters for email, URL, and phone patterns were preserved.
- **Evidence**: `.sisyphus/evidence/task-5-firm-name-title.txt` (PASS), `.sisyphus/evidence/task-5-pure-firm-reject.txt` (PASS), `.sisyphus/evidence/task-5-long-title.txt` (PASS).
- Expanded config/practice_department_map.json from 24 to 35 mappings by adding safe, non-conflicting department rules.
- infer_department_from_practices already accepts practice_areas + department and now maps 'Appellate Litigation' to 'Litigation (inferred)' via the updated config table.

## [2026-04-10] Task: T6-office-title-html
- `FieldEnricher.enrich()` now supports both the production `(AttorneyProfile, html)` call path and the legacy QA `(html, profile_dict)` fixture style by mapping dict payloads to/from `AttorneyProfile`.
- HTML office heuristics now add `<address>`, `itemprop="address"`, `itemprop="workLocation"`, Schema.org `PostalAddress`, and `og:locality` extraction while keeping JSON-LD first in the enrichment pipeline.
- HTML title heuristics now recognize `itemprop="jobTitle"` plus class names containing `role`, `position`, `rank`, and `level`; regression confirmed that JSON-LD title still wins over microdata/HTML fallbacks.

## [2026-04-10 18:18] Task: T8-bot-protected-preskip
- ind_attorney.py now loads site_structures.json once at startup and indexes rows by normalized domain (www. stripped) for pre-firm checks.
- Preclassified skips happen at the top of process_firm() before compliance probing, so BOT_PROTECTED firms make zero pipeline HTTP requests and still emit metrics/summary/failure-report entries.
- Guardrail: trust structure_type == BOT_PROTECTED first; some rows have noisy is_bot_protected=true on reachable firms (for example Kirkland is SITEMAP_XML), so the boolean alone should only trigger when status is 401/403 or structure type is missing/unknown.

- Correction: ind_attorney.py startup load note above refers to ind_attorney.py (the previous bullet was appended with console-escaped punctuation artifacts).

## [2026-04-10] Task: T9-per-firm-403-abort
- **_BotAbortTracker class** added to ind_attorney.py (after line 193): thread-safe tracker using 	hreading.Lock with two abort conditions:
  1. **Early window**: first 5 profiles ALL return bot-protection 403 -> immediate abort
  2. **Global ratio**: >80% of ALL processed profiles are bot-403 (checked after early window)
- **Rate-limit vs bot-protection distinction**: _is_bot_403() checks for Retry-After header in diagnostics — if present, it's a rate-limit 403 and does NOT count toward abort. Only bot-wall 403s (no Retry-After, or explicit bot_protection flag) trigger abort counting.
- **Retry-After header recording** added to multi_mode_extractor.py line ~278: stores etry_after in profile diagnostics when HTTP response has Retry-After header.
- **Wired into both batch functions**: _run_batch() (ThreadPoolExecutor) and _run_batch_shared_browser() (threading.Thread) both record profiles and check bort_tracker.aborted to skip remaining URLs.
- **Abort state stored on self**: _last_enrichment_aborted, _last_enrichment_abort_reason, _last_enrichment_bot_403_count — set in _enrich_profile_urls(), checked in both process_firm() paths.
- **Failure report propagation**: When abort triggers, discovery_status is overridden to DISCOVERY_BLOCKED and ailure_reason to the ACCESS_DENIED_RUNTIME message. In multi-source path, inal_status is also set to STATUS_LEGALLY_INCOMPLETE so FirmSummaryRow.legally_incomplete_reason is populated.
- **White & Case cannot test abort tracker**: The firm is pre-classified as BOT_PROTECTED in site_structures.json and skipped before enrichment. The abort tracker is defense-in-depth for firms NOT pre-classified but still returning bot-403 at enrichment.
- **Small firm safety**: Firms with < 5 total URLs cannot trigger early window abort (condition requires self._processed >= _ABORT_EARLY_WINDOW).
- **Unit tests**: 6 tests covering early window, rate-limit exclusion, mixed results, global ratio, normal firm, and small firm — all PASS.

## [2026-04-13] Task: T12-junk-phrases-hotfix
- Added standalone `about` and `contact` to `_JUNK_PHRASES` so `validate_practice_areas()` drops nav-only noise without affecting valid practices like `Insurance Coverage` or `Environmental Law`.

## [2026-04-10] Task: T10-directory-fallbacks
- Implemented real `directory_listing` / `alphabet_enumeration` request-based crawlers in `find_attorney.py` with `compliance_engine.is_allowed()` gates and `rate_limit_manager.wait()` before each outbound request.
- Added XML fallback chaining inside `_strategy_xml_sitemap()`: when sitemap yields 0 URLs, it now tries `directory_listing` then `alphabet_enumeration` before giving up.
- Expanded profile URL heuristics to accept Morgan Lewis-style `/bios/<slug>` profile URLs; this unlocked `https://www.morganlewis.com/sitemaps/people` and restored sitemap discovery coverage.
- Morgan Lewis currently reaches 500 discovered profile URLs on current HEAD, but enrichment of sampled profiles still hits runtime bot-protection 403s; QA still passes because 5 partial profiles are preserved and surfaced rather than dropped.
- Kirkland regression check stayed green: sitemap discovery + Playwright-only enrichment still returned 5 attorneys under `--limit 5`.

## [2026-04-13] Task: shared-page-stall-fix (Kirkland/SPA rerun stall)
- **Root cause**: `extract_profile_with_page()` attached `page.on("response", _handle_json)` on a reused Playwright page and called `response.json()` in the handler. On SPA-heavy sites (Kirkland), this call can block indefinitely, causing the shared-page enrichment path to stall with 4 browser workers alive but no progress.
- **Secondary bug**: cleanup used `page.remove_listener("response", ...)` which does not exist in Playwright Python (correct API is `page.off(...)`). This caused listeners to accumulate across reused page navigations, compounding the stall.
- **Fix**: Removed the entire `page.on("response", ...)` / `response.json()` / `captured_json` block from `extract_profile_with_page()`. The shared-page path now does HTML parsing only. Mode 3 API interception is unaffected (uses its own dedicated browser instance).
- **Progress logging**: Added `logger.debug/info/warning` calls at profile start, bot-protection detection, navigation exception, and completion (with `status=` and `elapsed_ms`). This makes a long rerun visibly progressing in logs.
- **Verification**: `--debug-firm "Kirkland" --limit 3` completed 3 SUCCESS profiles in 7.1 s with `[shared_page] done: ... | status=SUCCESS | ~600ms` log lines visible per profile.
- **Playwright API note**: Use `page.off("event", handler)` to remove a listener in Playwright Python, NOT `page.remove_listener(...)`. The latter silently fails (AttributeError swallowed by bare except).
