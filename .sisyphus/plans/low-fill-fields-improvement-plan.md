# Low Fill-Rate Improvement for Department, Practice Areas, and Offices

## TL;DR
> **Summary**: Improve recall for `department`, `practice_areas`, and `offices` by fixing destructive list merges, removing partial-profile short-circuiting, broadening safe heading/validator coverage, and adding fixture-backed regression cases for currently under-tested low-fill failure modes.
> **Deliverables**:
> - additional measurement slices and failing fixtures for low-fill bottlenecks
> - merge-policy fixes in shared list-field combiners
> - validator and section-parser improvements for the three target fields only
> - additive enrichment-path fixes for partially-populated profiles and SPA recovery cases
> - final before/after baseline evidence proving fill-rate lift without contamination regression
> **Effort**: Large
> **Parallel**: YES - 3 waves
> **Critical Path**: Task 1 → Task 2 → Task 4 → Task 6 → Task 8 → Task 10

## Context
### Original Request
Create an improvement plan because `department`, `practice_area`, and `office` still have low fill-rates even though the previous validation and comparison gates passed.

### Interview Summary
- Current user-run validation commands all pass.
- Current measured values show the remaining concern is recall/fill-rate, not broken correctness gates.
- The follow-up scope is limited to `department`, `practice_areas`, and `offices`.
- The next plan should optimize these low-fill fields without reopening unrelated fields or weakening contamination protections.

### Metis Review (gaps addressed)
- Highest-impact likely cause is destructive list merging in shared merge layers.
- Secondary causes are partial-profile short-circuit guards, conservative validators for short tokens/office formats, and narrow section synonym coverage.
- Existing tests under-cover large contaminated practice dumps, department blob cleanup, surname+state office artifacts, and SPA small-content or Playwright-recoverable cases.
- Plan must keep contamination-rate growth capped and avoid widening scope into other fields or a general SPA refactor.

## Work Objectives
### Core Objective
Raise fill-rate for `department`, `practice_areas`, and `offices` across the existing sampled dataset by addressing shared recall bottlenecks and adding fixture-backed coverage for current blind spots, while keeping contamination-rate increases to no more than +2 percentage points per target field.

### Deliverables
- frozen pre-fix baseline artifact for the three target fields
- new failing fixtures/cache samples and regression tests for uncovered low-fill failure modes
- list-merge fixes in shared merge layers affecting both extraction paths
- validator and parser updates scoped to `department`, `practice_areas`, and `offices`
- additive enrichment fixes for partially-populated profiles and narrowly-scoped SPA recovery conditions
- final before/after evidence under `outputs/` and `.sisyphus/evidence/`

### Definition of Done (verifiable conditions with commands)
- `python3.12 tests/test_validators.py`
- `python3.12 tests/test_parser_sections.py`
- `python3.12 tests/test_enrichment_integration.py`
- `python3.12 tests/test_field_merger.py`
- `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_before.json`
- `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_after.json`
- `python3.12 measure_baseline.py --compare outputs/low_fill_before.json outputs/low_fill_after.json --min-improvement 0.03`
- `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields department,practice_areas,offices --report outputs/low_fill_cross_path_diff.json --max-gap 0.10`

### Must Have
- Keep scope limited to `department`, `practice_areas`, and `offices`.
- Preserve current output schema and field names.
- Use TDD order for every behavior change: failing test first, then fix, then measurement check.
- Keep current sample manifest as the baseline denominator, but add targeted fixtures/cache samples for uncovered failure modes.
- Enforce a hard contamination guard: no target field may regress by more than +2 percentage points.
- Preserve cross-path comparability after all changes.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No changes to `title`, `industries`, `education`, `bar_admissions`, `full_name`, or unrelated fields.
- No bot-protection evasion, Cloudflare bypassing, or requests to `robots.txt` disallowed paths.
- No broad refactor of `run_pipeline.py`, `probe_structures.py`, or general SPA architecture.
- No new hard-coded firm-domain branches such as `if "firm.com" in url` in extraction code.
- No global taxonomy expansion project for practice areas or departments; only add test-backed normalizations or synonym coverage required by current failure modes.
- No destructive overwrite behavior for target list fields after this plan completes.
- No acceptance criteria based only on non-empty fill-rate.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: TDD with standalone `python3.12` scripts plus baseline measurement checkpoints after each high-impact bottleneck fix wave
- QA policy: every task includes one happy-path and one failure/edge-path scenario
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`
- Runtime pin: all commands use `python3.12`
- Baseline policy: capture `outputs/low_fill_before.json` before implementation starts; compare against `outputs/low_fill_after.json` at the end
- Contamination policy: each target field must remain at or below `baseline + 0.02`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: baseline freeze and RED-phase fixture/test expansion (Tasks 1-3)
- capture numeric baseline, add uncovered failure-mode fixtures/caches, add failing validator/parser/merge/integration regressions

Wave 2: shared recall bottleneck fixes (Tasks 4-7)
- repair merge semantics, validator coverage, parser synonym mapping, and additive enrichment guards

Wave 3: path-specific low-fill improvements and final gate (Tasks 8-10)
- narrow SPA/multi-mode recovery, remeasure, and prove fill-rate improvement without contamination regression

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
|---|---|---|
| 1 | none | 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| 2 | 1 | 3, 5, 6, 7, 8, 10 |
| 3 | 1, 2 | 4, 5, 6, 7, 8 |
| 4 | 1, 3 | 6, 8, 10 |
| 5 | 1, 2, 3 | 6, 7, 8, 10 |
| 6 | 4, 5 | 7, 8, 10 |
| 7 | 3, 4, 5, 6 | 8, 9, 10 |
| 8 | 3, 4, 5, 6, 7 | 9, 10 |
| 9 | 7, 8 | 10 |
| 10 | 1, 2, 3, 4, 5, 6, 7, 8, 9 | Final Verification |

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 3 tasks → `deep` (1), `unspecified-high` (2), `quick` (3)
- Wave 2 → 4 tasks → `unspecified-high` (4, 6, 7), `quick` (5)
- Wave 3 → 3 tasks → `unspecified-high` (8, 10), `quick` (9)

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [ ] 1. Freeze low-fill baseline and per-field target thresholds

  **예상 소요시간**: 40분

  **What to do**: Before any new logic changes, capture a dedicated before-state baseline for `department`, `practice_areas`, and `offices` using the current pinned manifest. Save the artifact as `outputs/low_fill_before.json`. Document current fill-rate and contamination-rate values for these three fields so every later task can compare against the same denominator and hard contamination guard.
  **Must NOT do**: Do not reuse a mutable post-fix output file as the pre-fix baseline. Do not change measurement logic in this task.

  **Recommended Agent Profile**:
  - Category: `deep` — Reason: this task defines the numeric contract for all later work.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — no browser work is required.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: [2, 3, 4, 5, 6, 7, 8, 9, 10] | Blocked By: []

  **References**:
  - Tooling: `measure_baseline.py` — existing measurement contract.
  - Artifact: `outputs/baseline_after.json` — most recent known quality snapshot.
  - Manifest: `tests/fixtures/sample_manifest.json` — denominator source.
  - Research: current observed values from user verification (`department=44%`, `practice_areas=65%`, `offices=84%`).

  **Acceptance Criteria**:
  - [ ] `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_before.json` exits 0.
  - [ ] `outputs/low_fill_before.json` contains `by_field.department`, `by_field.practice_areas`, and `by_field.offices` with both fill-rate and contamination-rate values.
  - [ ] The baseline artifact is saved before any code changes in this plan.

  **QA Scenarios**:
  ```
  Scenario: Freeze the pre-fix low-fill baseline
    Tool: Bash
    Steps: Run `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_before.json`.
    Expected: Exit code 0 and a JSON artifact containing by-field values for department, practice_areas, and offices.
    Evidence: .sisyphus/evidence/task-1-low-fill-baseline.txt

  Scenario: Missing manifest input still fails fast
    Tool: Bash
    Steps: Run the same command against a temporary manifest referencing a non-existent cache file.
    Expected: Non-zero exit code naming the missing manifest row; no baseline artifact is treated as valid.
    Evidence: .sisyphus/evidence/task-1-low-fill-baseline-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-0] chore(baseline): freeze low-fill before snapshot` | Files: [`outputs/low_fill_before.json`, `.sisyphus/evidence/task-1-low-fill-baseline.txt`]

- [ ] 2. Add uncovered low-fill fixtures and cache samples

  **예상 소요시간**: 1시간 40분

  **What to do**: Add targeted synthetic fixtures/cache samples for the currently under-covered failure modes: (a) large contaminated practice-area dump, (b) department concatenation blob, (c) surname+state office artifact, and (d) SPA small-content or Playwright-recoverable case. Wire these artifacts into the existing test harnesses and, where appropriate, the sample manifest.
  **Must NOT do**: Do not replace the existing positive fixtures. Do not add real live-firm HTML verbatim. Do not create fixtures for unrelated fields.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: multi-fixture design with manifest and cache integration.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — fixtures should remain synthetic/cached.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [3, 5, 6, 7, 8, 10] | Blocked By: [1]

  **References**:
  - Fixture: `tests/fixtures/html/adversarial_nav_pollution.html` — existing adversarial style to follow.
  - Cache examples: `tests/fixtures/cache/paul_weiss.jsonl`, `tests/fixtures/cache/kirkland.jsonl`, `tests/fixtures/cache/latham.jsonl` — real failure-pattern references.
  - Manifest: `tests/fixtures/sample_manifest.json` — current denominator source.
  - Test harness: `tests/test_enrichment_integration.py` — existing integration style.

  **Acceptance Criteria**:
  - [ ] New fixture/cache artifacts exist for all four uncovered failure modes.
  - [ ] Existing harnesses can load the new artifacts without breaking prior tests.
  - [ ] At least one new manifest/cache slice is available for measurement of a previously under-covered low-fill case.

  **QA Scenarios**:
  ```
  Scenario: New low-fill fixtures load successfully
    Tool: Bash
    Steps: Run `python3.12 tests/test_enrichment_integration.py` after adding the new fixtures and any supporting cache rows.
    Expected: Existing tests still run, and new targeted fixture cases are discoverable by the harness.
    Evidence: .sisyphus/evidence/task-2-low-fill-fixtures.txt

  Scenario: Missing or malformed new fixture fails clearly
    Tool: Bash
    Steps: Execute the new targeted test case with one fixture path intentionally missing or malformed.
    Expected: Non-zero exit code with a direct error that names the problematic fixture.
    Evidence: .sisyphus/evidence/task-2-low-fill-fixtures-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-0] test(fixtures): add uncovered low-fill failure cases` | Files: [`tests/fixtures/html/*`, `tests/fixtures/cache/*`, `tests/fixtures/sample_manifest.json`, `tests/test_enrichment_integration.py`]

- [ ] 3. Add RED-phase regressions for merge, validator, parser, and partial-profile supplementation gaps

  **예상 소요시간**: 1시간 30분

  **What to do**: Add or extend standalone tests so the following failures are explicitly red before any fixes land: destructive list overwrite in merge layers, rejection of short but valid practice/department tokens (`IP`, `M&A`, `Tax`), surname+state office artifacts, missing synonym mapping for headings like `Areas of Focus` or `Practice Focus`, and skipped supplementation for partially-populated profiles.
  **Must NOT do**: Do not implement production fixes in this task. Do not mask failures with broad expected-value allowances.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: concentrated RED-phase regression harness work.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — no browser work is required.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [4, 5, 6, 7, 8] | Blocked By: [1, 2]

  **References**:
  - Test: `tests/test_validators.py` — current validator harness.
  - Test: `tests/test_parser_sections.py` — current synonym and boundary harness.
  - Test: `tests/test_enrichment_integration.py` — current fixture-backed integration harness.
  - Merge logic: `field_merger.py`, `multi_mode_extractor.py` — target merge behavior to capture.

  **Acceptance Criteria**:
  - [ ] `tests/test_field_merger.py` exists and fails against current destructive list overwrite behavior.
  - [ ] `tests/test_validators.py` contains failing cases for short valid tokens and surname+state office artifacts.
  - [ ] `tests/test_parser_sections.py` contains failing heading-normalization cases for under-covered synonym variants.
  - [ ] `tests/test_enrichment_integration.py` contains a failing case proving a partially-populated profile is not supplemented when it should be.

  **QA Scenarios**:
  ```
  Scenario: New RED-phase tests fail for the expected reasons
    Tool: Bash
    Steps: Run `python3.12 tests/test_field_merger.py`, `python3.12 tests/test_validators.py`, `python3.12 tests/test_parser_sections.py`, and `python3.12 tests/test_enrichment_integration.py` immediately after adding the new assertions.
    Expected: At least the newly added targeted cases fail while pre-existing baseline failures remain unchanged.
    Evidence: .sisyphus/evidence/task-3-red-regressions.txt

  Scenario: False-positive pass is rejected
    Tool: Bash
    Steps: Inspect the newly added tests by running them with intentionally broad expectations removed or with explicit failure messages enabled.
    Expected: The tests fail specifically on the targeted current behavior, not because of fixture loading noise.
    Evidence: .sisyphus/evidence/task-3-red-regressions-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-1] test(regressions): add RED low-fill bottleneck cases` | Files: [`tests/test_field_merger.py`, `tests/test_validators.py`, `tests/test_parser_sections.py`, `tests/test_enrichment_integration.py`]

- [ ] 4. Replace destructive list overwrite with union-dedup merge semantics for target fields

  **예상 소요시간**: 1시간 20분

  **What to do**: Update shared merge logic so higher-precedence sources no longer erase previously extracted list values for `department`, `practice_areas`, and `offices`. Keep scalar precedence behavior unchanged. Apply the same target-field-safe union/dedup rule in both shared merge layers that currently overwrite list fields.
  **Must NOT do**: Do not change scalar merge semantics. Do not alter precedence numbers. Do not broaden the change to unrelated list fields unless directly required by target-field contracts and proven safe by tests.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: shared merge semantics affect both extraction paths.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['git-master']` — no git-specific workflow needed.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [6, 8, 10] | Blocked By: [1, 3]

  **References**:
  - Implementation: `field_merger.py` — shared precedence-driven merge logic.
  - Implementation: `multi_mode_extractor.py` — multi-mode profile merge logic.
  - Test: `tests/test_field_merger.py` — RED-phase contract for this task.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_field_merger.py` exits 0.
  - [ ] Merging a higher-precedence target-field list no longer removes previously captured valid entries.
  - [ ] Target-field dedup remains stable and does not create duplicate values under same-source or multi-source merges.

  **QA Scenarios**:
  ```
  Scenario: List merge preserves additive recall
    Tool: Bash
    Steps: Run `python3.12 tests/test_field_merger.py` after applying the merge fix.
    Expected: The tests show union-dedup behavior for department, practice_areas, and offices instead of destructive overwrite.
    Evidence: .sisyphus/evidence/task-4-list-merge.txt

  Scenario: Scalar precedence remains unchanged
    Tool: Bash
    Steps: Run the merge regression cases that include scalar fields beside list fields.
    Expected: Scalar fields still follow existing precedence rules; only target-field list merging changes.
    Evidence: .sisyphus/evidence/task-4-list-merge-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-2] fix(merge): preserve target-field list recall` | Files: [`field_merger.py`, `multi_mode_extractor.py`, `tests/test_field_merger.py`]

- [ ] 5. Relax target-field validators only where current tests prove valid data is being discarded

  **예상 소요시간**: 1시간 20분

  **What to do**: Update validators for `department`, `practice_areas`, and `offices` so they accept currently rejected but valid low-fill examples from the RED suite: short valid tokens, comma-free office forms, and surname+state cleanup where normalization is unambiguous. Preserve existing contamination rejection for URLs, phone numbers, emails, nav junk, and over-long garbage strings.
  **Must NOT do**: Do not relax contamination regexes. Do not touch validators for unrelated fields. Do not silently convert clearly ambiguous office strings into guessed cities.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: tightly-scoped validator change with deterministic test coverage.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — not required.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [6, 7, 8, 10] | Blocked By: [1, 2, 3]

  **References**:
  - Implementation: `validators.py` — `validate_department`, `validate_practice_areas`, `validate_offices`.
  - Test: `tests/test_validators.py` — RED/green contract.
  - Failure-pattern caches: `tests/fixtures/cache/kirkland.jsonl`, `tests/fixtures/cache/paul_weiss.jsonl`.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_validators.py` exits 0.
  - [ ] Short valid tokens such as `IP`, `M&A`, or `Tax` are accepted when otherwise clean and test-covered.
  - [ ] Surname+state office artifacts are rejected or normalized to the city/state component only when unambiguous.
  - [ ] Existing contamination examples still reject cleanly.

  **QA Scenarios**:
  ```
  Scenario: Validators retain more valid low-fill values
    Tool: Bash
    Steps: Run `python3.12 tests/test_validators.py` after validator updates.
    Expected: Previously failing low-fill cases now pass while existing contamination cases remain green.
    Evidence: .sisyphus/evidence/task-5-validators.txt

  Scenario: Relaxed validators do not admit obvious junk
    Tool: Bash
    Steps: Execute the validator cases with nav junk, phone numbers, or email-containing strings for the target fields.
    Expected: Non-clean values still return rejection/sentinel outcomes instead of being accepted.
    Evidence: .sisyphus/evidence/task-5-validators-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-2] fix(validators): recover valid low-fill target values` | Files: [`validators.py`, `tests/test_validators.py`]

- [ ] 6. Expand safe heading normalization and target-field parser coverage

  **예상 소요시간**: 1시간 20분

  **What to do**: Extend section-heading normalization only for test-backed `department` and `practice_areas` variants that are currently missed, such as `Areas of Focus`, `Practice Focus`, or equivalent discovered in the RED suite. Keep adversarial guards for nav/service headings in place. If department blob strings need safe split/normalization, implement only the explicitly tested separator handling.
  **Must NOT do**: Do not add broad raw synonyms like `services` or `group` without guards. Do not alter unrelated section keys. Do not weaken adversarial nav protections.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: parser changes affect multiple extraction layers and contamination risk.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — parser work is deterministic.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [7, 8, 10] | Blocked By: [4, 5]

  **References**:
  - Implementation: `parser_sections.py` — `SECTION_SYNONYMS`, normalization, section search helpers.
  - Test: `tests/test_parser_sections.py` — required adversarial and positive coverage.
  - Test: `tests/test_enrichment_integration.py` — downstream safety check.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_parser_sections.py` exits 0.
  - [ ] Every new heading variant added in this task has both a positive assertion and an adversarial assertion.
  - [ ] No previously passing nav-pollution protections regress.

  **QA Scenarios**:
  ```
  Scenario: New heading variants map to target fields safely
    Tool: Bash
    Steps: Run `python3.12 tests/test_parser_sections.py` after parser changes.
    Expected: The new heading cases pass and all adversarial cases remain green.
    Evidence: .sisyphus/evidence/task-6-parser-low-fill.txt

  Scenario: Nav or service headings still do not leak into target fields
    Tool: Bash
    Steps: Run the adversarial parser cases and the integration nav-pollution fixture.
    Expected: Generic site-chrome headings remain unmapped or rejected.
    Evidence: .sisyphus/evidence/task-6-parser-low-fill-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-3] fix(parser_sections): recover missed target headings safely` | Files: [`parser_sections.py`, `tests/test_parser_sections.py`, `tests/test_enrichment_integration.py`]

- [ ] 7. Remove partial-profile short-circuiting for additive target-field enrichment

  **예상 소요시간**: 1시간 40분

  **What to do**: Update enrichment flows so `department`, `practice_areas`, and `offices` can still be supplemented when a profile is partially populated rather than fully empty. Keep scalar short-circuit rules intact, but change the target-field list logic to use additive union/dedup semantics instead of “skip if any value exists.” Apply this only where the RED suite proved missed supplementation.
  **Must NOT do**: Do not change extraction order. Do not remove scalar-field guards. Do not allow later weak sources to overwrite earlier strong target-field values.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: nuanced enrichment flow changes with contamination risk.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — browser work is not the focus of this task.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [8, 9, 10] | Blocked By: [3, 4, 5, 6]

  **References**:
  - Implementation: `enrichment.py` — section-map extraction and proximity fallback guards.
  - Implementation: `field_enricher.py` — partial-profile heuristic entry decisions.
  - Test: `tests/test_enrichment_integration.py` — partial-profile supplementation regressions.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_enrichment_integration.py` exits 0.
  - [ ] A profile with one existing target-field entry can still receive additional valid target-field values from later safe sources.
  - [ ] Later weak sources do not overwrite earlier strong values for the target fields.

  **QA Scenarios**:
  ```
  Scenario: Partially-populated profiles are supplemented additively
    Tool: Bash
    Steps: Run `python3.12 tests/test_enrichment_integration.py` after enrichment-flow changes.
    Expected: The new partial-profile case passes and earlier fixtures remain green.
    Evidence: .sisyphus/evidence/task-7-additive-enrichment.txt

  Scenario: Additive supplementation does not reintroduce contamination
    Tool: Bash
    Steps: Run the adversarial nav-pollution fixture and any new contaminated practice dump case after the enrichment change.
    Expected: Target fields do not absorb nav/footer junk despite now allowing supplementation.
    Evidence: .sisyphus/evidence/task-7-additive-enrichment-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-4] fix(enrichment): supplement target fields additively` | Files: [`enrichment.py`, `field_enricher.py`, `tests/test_enrichment_integration.py`]

- [ ] 8. Narrowly improve SPA and multi-mode recovery for low-fill target fields

  **예상 소요시간**: 1시간 40분

  **What to do**: Improve low-fill recovery only for the three target fields in SPA or multi-mode cases that currently escape supplementation. Prefer narrow changes: broaden target-field-specific escalation/heuristic checks or captured-data supplementation only where the RED fixtures prove current misses. Keep this scoped to target-field recall rather than a general SPA redesign.
  **Must NOT do**: Do not redesign the full Playwright strategy. Do not change unrelated mode escalation behavior. Do not broaden the task into title/industries recovery.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: path-specific recall recovery with moderate architectural sensitivity.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['dev-browser']` — browser automation is not required for cached/fixture-backed validation.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [9, 10] | Blocked By: [3, 4, 5, 6, 7]

  **References**:
  - Implementation: `multi_mode_extractor.py` — multi-mode merge and fallback flow.
  - Implementation: `enrichment.py` — target-field-specific hybrid/escalation checks.
  - Cache/fixture references: `tests/fixtures/cache/latham.jsonl`, new SPA low-fill fixtures added in Task 2.

  **Acceptance Criteria**:
  - [ ] The new SPA or multi-mode low-fill regression cases pass.
  - [ ] Existing non-SPA target-field tests remain green.
  - [ ] The change is narrowly scoped to target-field recovery and does not alter unrelated output contracts.

  **QA Scenarios**:
  ```
  Scenario: SPA or multi-mode low-fill target fields recover correctly
    Tool: Bash
    Steps: Run the targeted integration or cache-backed regression cases covering the new SPA/multi-mode fixture set.
    Expected: Department/practice_areas/offices are recovered where the new narrow fallback is intended to apply.
    Evidence: .sisyphus/evidence/task-8-spa-recovery.txt

  Scenario: Narrow SPA recovery does not spill into unrelated behavior
    Tool: Bash
    Steps: Run previously passing integration cases plus a non-target-field regression sample after the SPA/multi-mode adjustment.
    Expected: Only the intended low-fill target-field cases change.
    Evidence: .sisyphus/evidence/task-8-spa-recovery-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-5] fix(spa): recover target-field low-fill cases narrowly` | Files: [`multi_mode_extractor.py`, `enrichment.py`, `tests/test_enrichment_integration.py`]

- [ ] 9. Re-run focused baseline checkpoints after shared and path-specific fixes

  **예상 소요시간**: 50분

  **What to do**: After the merge/validator/parser/additive-enrichment/SPA fixes are complete, run focused measurement checkpoints to confirm numeric lift for `department`, `practice_areas`, and `offices`. Save the final artifact as `outputs/low_fill_after.json` and produce an intermediate checkpoint artifact if needed for debugging.
  **Must NOT do**: Do not overwrite `outputs/low_fill_before.json`. Do not declare success from a single improved field if the others regress in contamination.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: measurement rerun and evidence capture.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['git-master']` — no git-specific workflow required.

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: [10] | Blocked By: [7, 8]

  **References**:
  - Tooling: `measure_baseline.py`
  - Artifact: `outputs/low_fill_before.json`
  - Manifest: `tests/fixtures/sample_manifest.json`

  **Acceptance Criteria**:
  - [ ] `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_after.json` exits 0.
  - [ ] At least two of the three target fields improve fill-rate versus `outputs/low_fill_before.json`.
  - [ ] No target field contamination-rate increases by more than +2 percentage points.

  **QA Scenarios**:
  ```
  Scenario: Post-fix low-fill baseline is generated successfully
    Tool: Bash
    Steps: Run `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/low_fill_after.json`.
    Expected: Exit code 0 and a final post-fix low-fill baseline artifact.
    Evidence: .sisyphus/evidence/task-9-low-fill-after.txt

  Scenario: Measurement regression is caught numerically
    Tool: Bash
    Steps: Compare `outputs/low_fill_before.json` and `outputs/low_fill_after.json`, or run against an intentionally regressed artifact.
    Expected: A regression in contamination or no-improvement case is detectable and causes the task to be treated as failed.
    Evidence: .sisyphus/evidence/task-9-low-fill-after-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-6] chore(measurement): capture post-fix low-fill snapshot` | Files: [`outputs/low_fill_after.json`, `.sisyphus/evidence/task-9-low-fill-after.txt`]

- [ ] 10. Prove before/after low-fill improvement and cross-path non-regression

  **예상 소요시간**: 1시간

  **What to do**: Run the final comparison gate using the frozen before/after low-fill baselines and a focused cross-path diff on `department`, `practice_areas`, and `offices`. Produce final evidence proving improved recall, stable contamination, and continued within-threshold alignment between the main and alternate paths.
  **Must NOT do**: Do not average away a contamination regression. Do not treat blocked firms as ordinary misses. Do not skip the cross-path comparison.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: final evidence consolidation and binary pass/fail gate.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — final gate is report-driven.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [Final Verification] | Blocked By: [1, 2, 3, 4, 5, 6, 7, 8, 9]

  **References**:
  - Tooling: `measure_baseline.py`
  - Tooling: `compare_paths.py`
  - Artifact: `outputs/low_fill_before.json`
  - Artifact: `outputs/low_fill_after.json`
  - Existing comparison outputs: `outputs/cross_path_diff_final.json`

  **Acceptance Criteria**:
  - [ ] `python3.12 measure_baseline.py --compare outputs/low_fill_before.json outputs/low_fill_after.json --min-improvement 0.03` exits 0.
  - [ ] `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields department,practice_areas,offices --report outputs/low_fill_cross_path_diff.json --max-gap 0.10` exits 0.
  - [ ] Final evidence explicitly states before/after fill-rates and contamination deltas for all three target fields.

  **QA Scenarios**:
  ```
  Scenario: Final low-fill gate passes
    Tool: Bash
    Steps: Run `python3.12 measure_baseline.py --compare outputs/low_fill_before.json outputs/low_fill_after.json --min-improvement 0.03` and then `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields department,practice_areas,offices --report outputs/low_fill_cross_path_diff.json --max-gap 0.10`.
    Expected: Both commands exit 0 and the evidence reports improved fill-rate with no contamination breach.
    Evidence: .sisyphus/evidence/task-10-low-fill-gate.txt

  Scenario: Contamination or path-regression breach is caught
    Tool: Bash
    Steps: Run the same commands against an intentionally regressed post-fix artifact or mismatched cross-path sample.
    Expected: Non-zero exit code and output naming the offending field.
    Evidence: .sisyphus/evidence/task-10-low-fill-gate-error.txt
  ```

  **Commit**: YES | Message: `[low-fill-7] chore(quality-gate): prove low-fill improvement` | Files: [`outputs/low_fill_cross_path_diff.json`, `.sisyphus/evidence/task-10-low-fill-gate.txt`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [ ] F1. Plan Compliance Audit — oracle
- [ ] F2. Code Quality Review — unspecified-high
- [ ] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [ ] F4. Scope Fidelity Check — deep

## Commit Strategy
- Preserve RED → GREEN → checkpoint order; every bottleneck fix starts from a failing regression.
- Commit format: `[low-fill-N] type(scope): description`
- Keep fixture/test expansion separate from production code fixes.
- Keep merge semantics, validator tuning, parser tuning, and enrichment-flow changes in separate commits for easier bisecting.
- Keep final measurement artifacts separate from implementation commits.

## Success Criteria
- `department`, `practice_areas`, and `offices` all have explicit before/after measurement artifacts under the same denominator.
- At least two of the three target fields improve fill-rate measurably versus `outputs/low_fill_before.json`.
- No target field’s contamination-rate increases by more than 2 percentage points.
- The main and alternate paths remain within 10 percentage points for the three target fields.
- No unrelated fields or extraction systems are changed outside the defined scope.
