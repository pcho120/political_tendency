
## [2026-04-10] Task: T6-office-title-html
- `find_attorney.py` still reports many pre-existing Pyright diagnostics unrelated to T6; changed-file clean verification was satisfied on `field_enricher.py` and the dedicated QA script.

## [2026-04-10 18:18] Task: T8-bot-protected-preskip
- Windows console/logging still emits CP949 UnicodeEncodeError for existing em-dash log messages unless PYTHONIOENCODING=utf-8 is set during CLI QA. Evidence scripts should force UTF-8 capture on Windows.

## [2026-04-10] Task: T10-directory-fallbacks
- Morgan Lewis robots.txt disallows `/api/*`, so the new request-based HTML directory implementation intentionally does not rely on the otherwise-functional `/api/custom/biolisting/execute` endpoint.
- Morgan Lewis homepage/directory can serve normal HTML with browser-like Accept headers, but direct profile enrichment still returns runtime 403 bot-protection for sampled profile pages in this environment.
- Existing logging emits Unicode dash characters that trigger `cp949` console `UnicodeEncodeError` noise on Windows PowerShell; redirecting to UTF-8 files avoids losing QA evidence, but the logging issue remains pre-existing technical debt.

## [2026-04-12] Task: T11-find-attorney-validator-wiring
- `find_attorney.py` had a validator integration gap: `validate_practice_areas()`, `validate_offices()`, and `validate_title()` already existed but were never called in the post-enrichment attorney loop before JSONL persistence.
- `validate_offices()` signature on current HEAD is `validate_offices(raw: list[str])` (no `firm_name` parameter), despite older task notes claiming a `firm_name` arg. The wiring must call the actual signature to avoid runtime `TypeError`.
- PowerShell `*>` redirection produced UTF-16 QA captures for `task-11-kirkland-retest.txt` and `task-11-jones-day-retest.txt`; downstream verification had to decode bytes with replacement-safe handling instead of plain UTF-8 text reads.

## [2026-04-13] Task: T12-junk-phrases-hotfix
- `python3.12` is not available in this Linux environment (`command not found`), so the requested verification commands could not run as written.
- Working tree also contains pre-existing unrelated modifications in `.sisyphus/boulder.json` and `.sisyphus/plans/field-quality-v2-rerun.md`; they were left untouched.
