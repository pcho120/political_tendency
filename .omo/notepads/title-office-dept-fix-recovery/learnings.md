# Learnings — title-office-dept-fix-recovery

- Fresh runs for knobbe/arentfox/weil each returned 5 sampled rows and 0 exact title contaminations under the current equality check.
- The validator handoff is confirmed at `enrichment.py:415` via `validate_title(profile.title, firm_name=profile.firm or "")`; `profile.firm` is present at call time in the current code path.
- Diagnostics evidence now shows `title_reason` present and flagged `contaminated` for all 5 sampled rows in each firm output.

- Task 2 pinned the current title validator baseline: all contaminated cases already reject, so the regression script intentionally returns non-zero with a REPRO MISMATCH notice for downstream fix work.

- Task 3 resolution: the production contamination bug was already fixed prior to Task 3. The only change needed was to `test_title_regression.py` exit semantics — removed the forced `return 1` on the REPRO_MISMATCH path so the harness exits 0 when all 6 cases (3 contaminated + 3 valid) match expectations. No changes to `validators.py` or `enrichment.py` were required.
- Kirkland 10-profile sample post-Task-3: total_rows=10, contaminated_titles=0, newly_rejected_count=0. Green.

- Task 4 (Weil title source diagnosis): Fresh run 2026-04-01, 5 profiles sampled. contaminated_titles=0. All 5 rows returned title=None (Weil's site is JS-rendered; requests-mode yields PARTIAL with missing=['title'] for all profiles). Contamination string "Weil, Gotshal & Manges LLP" never assigned → no active contamination path. No patch to enrichment.py required. Diagnosis: not active in current sample. Evidence: task-4-weil-diagnosis.txt, task-4-weil-clean.txt.

- Task 5 reruns completed for knobbe/arentfox/weil/kirkland/paul hastings; fresh evidence stayed clean and final-report-v2 now ends in PASS.

- Recovery-session doc fix: final-report-v2 now uses PASS/FAIL sections, task-1-repro includes profile_url samples for knobbe/arentfox/weil, and the evidence set now records the python3.12-unavailable → python3 fallback explicitly.

- Runtime handoff proof added: task-1-repro now shows latest JSONL provenance, per-firm non-empty counts, sample tuples, and validate_title(...) call results.

- Repro criterion is now explicit: task 1 did not reproduce historical contamination in the current runtime sample, so the session stayed evidence-only.
