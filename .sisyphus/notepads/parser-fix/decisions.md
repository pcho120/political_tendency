## [2026-04-04] Architecture Decisions

### Decision: Python command alias
- Use `python` not `python3.12` on this Windows environment

### Decision: Wave 1 - Tasks 1+2 in parallel
- Task 1: RED phase test cases (quick)
- Task 2: section boundary bleed fix (unspecified-high)
- These are independent: Task 1 only adds tests, Task 2 only modifies parser_sections.py

### Decision: Wave 2 - Tasks 3+4+5 in parallel (after Wave 1 done)
- Task 3: validators.py bio-sentence filter
- Task 4: enrichment.py department extraction
- Task 5: enrichment.py Latham h3 sub-section + hero-section extraction
- These are independent of each other

### Decision: "profile" synonym fix
- Change from plain "profile" string to qualified form with required co-words
- Option B from Metis: `("profile", frozenset({"bio","overview","summary","about"}))` — profile alone does NOT map to biography

### Decision: boundary bleed fix approach
- Switch from `find_all_next()` to parent container sibling traversal
- h3-under-h2 (BOUNDARY_CASE) must still work after fix
