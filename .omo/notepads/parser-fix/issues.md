## [2026-04-04] Known Issues

### Issue 1: `_collect_content_after` section boundary bleed
- File: parser_sections.py lines 220-286
- Root cause: `find_all_next()` does depth-first DOM traversal, collecting content from adjacent sections before their stop headings
- Impact: offices gets bar_admissions text, practice_areas gets bio text

### Issue 2: "profile" synonym in biography
- File: parser_sections.py lines 146-153 (biography synonyms)
- Root cause: plain "profile" string maps any <h2>Profile</h2> to biography bucket
- Impact: Latham profiles → all content classified as biography → all fields empty

### Issue 3: practice_areas bio-sentence leak
- File: validators.py lines 438-473
- Root cause: only length/junk-phrase filtering, no bio-verb pattern detection
- Impact: Paul Weiss, other firms get full bio sentences in practice_areas

### Issue 4: department always empty
- File: enrichment.py
- Root cause: no generic CSS class or title-split extraction for department
- Impact: ALL 176 firms have department: []

### Issue 5: Latham h3 sub-sections
- File: enrichment.py + parser_sections.py
- Root cause: <h3>Practices</h3> under <h2>Qualifications</h2> not properly mapped
- Impact: Latham practice_areas empty or wrong

### Issue 6: Jones Day BOT_PROTECTED
- Returns 403, should be classified as BOT_PROTECTED in site_structures.json

### Issue 7: `_collect_content_after` crashes on `NavigableString` siblings
- File: parser_sections.py lines 269-270
- Root cause: sibling iteration assumes every node has `.get()`; text nodes break the traversal
- Impact: parser regression harness and enrichment integration now fail before section assertions run

## [2026-04-05] Task 6 -- New Issues Found

### Issue 8: Discovery returning non-attorney URLs for HTML_DIRECTORY_FLAT
- Firms: Goodwin Procter, King & Spalding
- Symptoms: full_name = practice group names, nav items, job posting titles
- Root cause: discovery.py does not validate that discovered URLs point to individual attorney profiles
- Fix location: discovery.py URL validation (out of scope for parser-fix plan)

### Issue 9: Fried Frank wrong domain in firm_finder
- firm_finder matched Fried Frank to fried.com (finance blog) not friedfrank.com
- Fix location: firm_finder.py domain scoring (out of scope for parser-fix plan)

### Issue 10: Latham discovery finds /offices/ URLs not /people/ URLs
- discovery.py returns office page URLs instead of attorney profile URLs for lw.com
- Fix location: discovery.py URL pattern filtering (out of scope for parser-fix plan)
