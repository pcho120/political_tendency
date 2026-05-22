# Task 1 Issues: Contamination Sources Identified

## Issue #1: Section Parser Returns Full Section Content (CRITICAL)
- **File:** enrichment.py:1172-1175 (STAGE 4)
- **Problem:** `find_section(section_map, "departments")` returns all text under a "departments" heading,
  including nested lists, press releases, UI elements
- **Evidence:** Department field contains "Download vCard", "Read More", "€1.5 Billion Refinancing"
- **Impact:** 90.35% of records have department field populated, mostly with garbage
- **Dependency:** parser_sections.py parse_sections() logic needs review

## Issue #2: CSS Selector get_text() Grabs Too Much
- **File:** enrichment.py:788-800 (STAGE 0)
- **Problem:** `soup.find_all(class_=selector_class)` followed by `el.get_text(strip=True)`
  captures ALL descendant text, not just direct children
- **Pattern affected:** "listing-services__heading", "practice-group__heading"
- **Impact:** Secondary contamination source if section parser filter fails
- **Fix needed:** Use direct child text only or check HTML structure

## Issue #3: Validation Filter Insufficient
- **File:** validators.py:337-380 (validate_department)
- **Current filters:** email (@), URL (http/www), phone, cookies, char length
- **Missing filters:** 
  - "Download vCard", "Read More", "Expand Biography" (UI elements)
  - Date patterns (e.g., "March 12, 2026", "January 30, 2026")
  - "€" and "$" with Billions/Millions (press release patterns)
  - Bullet points and list markers
- **Status:** Validation passes contaminated items because they don't match known patterns

## Issue #4: No Firm Name in Output JSONL
- **File:** outputs/attorneys_2026-03-24T20-29-18.jsonl
- **Problem:** firm_name field is blank/missing for all 1264 records
- **Impact:** Cannot identify which firm these attorneys belong to
- **Cause:** Unknown (may be enrichment stage issue or extraction override)

## Recommended Priority for Task 2-4
1. Fix section parser return value (enrichment.py:1172-1175)
2. Fix CSS selector extraction (enrichment.py:788-800)
3. Enhance validator filters (validators.py:337-380)
4. Investigate firm_name field loss

## Task 1 Completed
All contamination sources traced. File+line references ready for fix implementation.
## CORRECTION: Task 1 Re-run Issues & Findings

### Issue #1: Title Empty Reasons Distribution (DOCUMENTED)

**File:** enrichment.py (multiple stages)

**Distribution (145 empty titles):**
- not_found: 133 (92%)
- contaminated: 8 (5%)
- too_short: 2 (1%)
- too_long: 2 (1%)

**Conclusion:** Most empty titles are due to missing extraction, not validation rejection.
Only 8 contaminated titles are being filtered out.

### Issue #2: Offices Empty Reasons Distribution (DOCUMENTED)

**File:** enrichment.py (validation stage)

**Distribution (629 empty offices):**
- not_found: 438 (70%)
- validation_rejected: 191 (30%)

**Conclusion:** 438 records have no offices extracted (CSS selectors/patterns missing).
191 records extracted offices but validation rejected (non-US, junk, too_long).

### Issue #3: Weil OFFICES CONTAMINATION - CRITICAL (CONFIRMED)

**File:** enrichment.py:705-742 (office-href scanner) AND/OR enrichment.py:1173 (section parser)

**Problem:** Weil attorneys receive full firm office list (10 cities) instead of individual location

**Evidence:**
- Record: Kumbi Abere | offices: ['Austin', 'Boston', 'Dallas', 'Houston', 'Los Angeles', 'Miami', 'New York', 'San Francisco', 'Silicon Valley', 'Washington, D.C.']
- Expected: Single office location (e.g., ['New York'] based on h1 'partner_london' section key)
- Pattern: All 5 Weil samples show identical 10-city list

**Likely Root Cause:**

1. **Office-href scanner (enrichment.py:705-742)**
   - Regex `/offices?/` pattern (line 706) matches Weil `/offices/` nav links
   - Noise filter checks parent tag names (line 725: nav/header/footer)
   - **Gap:** Weil may have `/offices/` links in main profile div (not tagged as nav)
   - Line 741-742: appends text without additional scope validation
   - **Fix Strategy:** Add URL guard for 'weil.com' to skip office-href scanner, OR tighten noise filter to include profile-main class checks

2. **Section parser (enrichment.py:1173)**
   - `find_section(section_map, "offices")` retrieves all text under "Offices" heading
   - Returns full section content (described in parser_sections.py)
   - **Hypothesis:** Weil page has `<h2>Offices</h2>` followed by full office directory
   - **Fix Strategy:** Limit section parser to first child element text only, not full subtree

### Issue #4: Title PROXIMITY Extraction - FULL PAGE SEARCH (RISK)

**File:** enrichment.py:1288-1328

**Current Logic:**
- Line 1320: `soup.find(string=re.compile(...))` searches ENTIRE page
- Returns parent element text with 120-char limit
- **Risk:** Parent may contain contaminating text near title keyword

**Evidence of Contamination (sample):**
- Winston & Strawn titles: "Winston & Strawn" (firm name appearing in parent)
- Reason: firm name may be sibling to title text in HTML

**Fix Strategy (from plan Task 2):**
- Constrain search to hero/header DOM scope (lines 1320 → search within hero element only)
- Fallback to soup if no hero found (regression mitigation)

### Issue #5: Title VALIDATION - Missing Firm Name Filter

**File:** validators.py:231-260

**Current Filters:**
- Length: 2-120 chars
- Email/phone/URL check
- Missing: **firm name substring check**

**Evidence:**
- Winston & Strawn "Winston & Strawn" passes validation (firm name NOT filtered)
- Cahill Gordon titles may pass with firm name contamination

**Fix Strategy (from plan Task 3):**
- Add firm_name parameter to validate_title()
- Reject if title contains ≥2 overlapping tokens with firm_name

### Task 1 Corrected - COMPLETION SUMMARY

✅ Baseline metrics captured with reason distributions
✅ Weil contamination confirmed (full office list returned)
✅ Contamination paths identified: enrichment.py:705-742 (office-href) AND enrichment.py:1173 (section parser)
✅ Title proximity risk documented: full page search (enrichment.py:1288-1328)
✅ All evidence files generated

**Next Steps (Task 2-5):**
1. Task 2: Constrain title proximity to hero/header scope (enrichment.py:1288-1328)
2. Task 3: Add firm_name filter to validate_title (validators.py)
3. Task 4: Add firm-specific CSS selectors (Cahill, Troutman, Sullivan, Saul Ewing)
4. Task 5: Fix Weil offices contamination + S&C/Saul Ewing parsers

## Task 2 Issues: og:title as Primary Contamination Source

### Issue #5 (Updated): og:title Section Parser — Actual Cahill/Troutman Contamination Source

**File:** parser_sections.py:422-432 AND enrichment.py:1154-1158 (Stage 4)

**Problem:**
- `og:title` is split on `|`/`-`/`—` at parser_sections.py:424
- `parts[1]` (second segment) is assumed to be title
- For law firms: `"Ethan Saber | Cahill Gordon & Reindel LLP"` → parts[1] = `"Cahill Gordon & Reindel LLP"`
- This gets stored in `section_map["title"]` and used at enrichment.py:1158 as profile.title

**Impact:**
- All Cahill profiles: title = "Cahill Gordon & Reindel LLP"
- All Troutman profiles: title = "Troutman Pepper Locke"
- `_extract_title_proximity()` is never invoked for these (Stage 4 already sets title)

**Fix Strategy (Task 3):**
- `validate_title(firm_name=...)` parameter will reject titles matching firm name
- OR og:title parser can check if parts[1] matches known firm name keywords

### Issue #6: _extract_title_proximity() Was Not the Cahill/Troutman Cause

**File:** enrichment.py:1288-1380

**Finding:** Task 1 evidence incorrectly attributed Cahill/Troutman contamination to this function.
The function operates at Stage 5 (proximity fallback) and only runs when `profile.title` is still None.
Since og:title sets title at Stage 4, Stage 5 proximity never fires for contaminated profiles.

**Task 2 change is still correct:** The scope constraint prevents marketing copy like "partner with us"
from being returned IF the proximity search does run (e.g., when og:title provides no data or is absent).

### Task 2 Completed
enrichment.py:1288-1380 updated. Scoped DOM search added. Kirkland regression: PASS (0/5 empty).

## Task 2 Re-attempt: og:title Guard — Root Cause Fixed

### Fix Applied
- **File:** enrichment.py:1153–1175
- **Change:** `_TITLE_KW` frozenset guard added to og:title candidate acceptance
- **Result:** Firm names in og:title parts[1] are now rejected; proximity search runs instead
- **Confirmed:** Cahill 0/4 contaminated, Troutman 0/5 contaminated, Kirkland 5/5 non-empty

### Task 2 Completed (Accepted)
Both changes verified passing all three acceptance criteria.

## Task 3 Complete: No Issues

✅ All firm-name contaminations properly detected and rejected
✅ No regressions on legitimate titles (127 contaminations ≠ regressions)
✅ Callsites updated (enrichment.py only needed for main pipeline)
✅ Validation logic robust against false positives

## Scope Creep Prevention Note

**Issue:** In a single session, drifted from Task 3 (parameter signature) into Task 4 (firm-specific CSS selectors).

**Root Cause:** Baseline tests revealed low title capture on Susman/Cahill/Sullivan, triggering investigative work that extended beyond stated task scope.

**Resolution:** Reverted all Task 4 additions. Task 3 is parameter infrastructure only. Task 4 selectors deferred to proper planning.

**Lesson:** Strict scope adherence required when working with multi-task plans. Each task must be completed and verified independently.
