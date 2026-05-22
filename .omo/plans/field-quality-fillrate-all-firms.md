# Field Quality and Fill-Rate Across All Firms

## TL;DR
> **Summary**: Improve accuracy and fill-rate for title, offices, department, practice areas, and industries across all firms by adding fixture-backed measurement, tightening shared parsing/validation, closing known extraction gaps in both pipelines, and enforcing cross-path non-regression.
> **Deliverables**:
> - baseline measurement and comparison scripts with cached/fixture-backed inputs
> - parser and validator regression harnesses for the five target fields
> - main-path extraction fixes in `enrichment.py`
> - alternate-path merge/provenance alignment in `find_attorney.py`
> - cross-path diff report and final before/after quality evidence
> **Effort**: XL
> **Parallel**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2 → Task 6 → Task 8 → Task 9 → Task 10

## Context
### Original Request
Create a decision-complete repo-wide work plan to improve field quality and fill-rate for title, offices, department, practice areas, and industries across all firms.

### Interview Summary
- All firms are in scope; there is no firm prioritization.
- Both extraction paths are in scope:
  - main path: `run_pipeline.py` / `enrichment.py`
  - alternate path: `find_attorney.py` architecture
- Verification must use tests plus sampled runs.
- Quality policy is balanced: improve fill-rate without tolerating uncontrolled contamination.

### Metis Review (gaps addressed)
- Baseline measurement is currently missing and must be created before any extraction changes.
- Measurement must distinguish correct vs contaminated vs truly missing, not just non-empty rate.
- BOT_PROTECTED and AUTH_REQUIRED firms must be excluded from improvement targets and reported separately.
- `find_attorney.py` has dual merge behavior that must be resolved before provenance/confidence alignment.
- `parser_sections.py` synonym expansion needs adversarial false-positive tests.
- `enrichment.py` Stage-5 fallback currently has known field coverage gaps and must be extended without adding new firm-specific branches.

## Work Objectives
### Core Objective
Raise accuracy and fill-rate for the five target fields across both extraction architectures using structure-aware, cache/fixture-backed verification that prevents contamination regressions and overfitting.

### Deliverables
- `measure_baseline.py` with before/after compare mode and machine-readable exit codes
- cached sample manifest and synthetic HTML fixture corpus covering major structure types
- standalone regression scripts for parser, validators, and enrichment integration
- main-path extraction improvements for parser, validators, JSON-LD/embedded-state merge, and proximity fallback
- alternate-path merge/provenance alignment and path comparison script
- final baseline comparison artifacts and cross-path diff artifacts under `outputs/` plus task evidence under `.sisyphus/evidence/`

### Definition of Done (verifiable conditions with commands)
- `python3.12 tests/test_parser_sections.py`
- `python3.12 tests/test_validators.py`
- `python3.12 tests/test_enrichment_integration.py`
- `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_before.json`
- `python3.12 measure_baseline.py --compare outputs/baseline_before.json outputs/baseline_after.json --min-improvement 0.05`
- `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff.json`

### Must Have
- One measurement contract used by both paths.
- Separate reporting for blocked firms (`BOT_PROTECTED`, `AUTH_REQUIRED`).
- Structure-aware sampling using `site_structures.json`.
- TDD-style red/green order for parser and validator fixes using standalone `python3.12` scripts.
- No live-network requirement for default verification; cached/fixture-backed commands are the default.
- Cross-path comparison artifact with machine-readable exit status and a fixed 10 percentage-point tolerance.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No bot-protection evasion, Cloudflare bypassing, or requests to `robots.txt` disallowed paths.
- No new hard-coded firm-domain branches such as `if "firm.com" in url` in extraction code.
- No standalone cleanup/refactor project for the existing Stage-0 hard-coded CSS exceptions in `enrichment.py`; treat them as inherited debt, prevent expansion, and route any unavoidable net-new exception through config rather than code.
- No canonical taxonomy expansion project for all practice areas or industries; dedupe/cleanup only.
- No refactor of `field_enricher.py` internals beyond the minimal changes required to standardize call-site behavior.
- No acceptance criteria that depend on human/manual review.
- No success metric based only on non-empty fill-rate.

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: TDD (red-green with standalone `python3.12` scripts) plus cached sample runs
- QA policy: Every task includes one happy-path and one failure/edge-path scenario
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`
- Runtime pin: all commands use `python3.12`
- Default data source: cached HTML / synthetic fixtures; live site fetches are opt-in only
- Denominator policy: exclude `BOT_PROTECTED` and `AUTH_REQUIRED` from improvement targets, but emit separate counts for them in every report

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: foundation and measurement tasks (Tasks 1-5)
- measurement schema, manifest, fixture corpus, parser/validator harnesses, and cross-path diff harness

Wave 2: extraction and alignment tasks (Tasks 6-10)
- parser fixes, validator fixes, main-path gap closure, alternate-path alignment, final baseline and anti-overfitting gate

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
|---|---|---|
| 1 | none | 2, 5, 6, 7, 8, 9, 10 |
| 2 | 1 | 6, 7, 8, 10 |
| 3 | 1 | 6, 8 |
| 4 | 1 | 7, 8, 9 |
| 5 | 1 | 9, 10 |
| 6 | 2, 3 | 8, 10 |
| 7 | 2, 4 | 8, 9, 10 |
| 8 | 2, 3, 4, 6, 7 | 9, 10 |
| 9 | 4, 5, 7, 8 | 10 |
| 10 | 5, 6, 7, 8, 9 | Final Verification |

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 5 tasks → `deep` (1), `unspecified-high` (2, 5), `quick` (3, 4)
- Wave 2 → 5 tasks → `unspecified-high` (6, 8, 9, 10), `quick` (7)

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Build structure-aware measurement contract and sample manifest

  **예상 소요시간**: 2시간 30분

  **What to do**: Create the baseline measurement contract used by the entire effort. Add `measure_baseline.py` that reads cached/sample inputs, aggregates the five target fields into `correct`, `contaminated`, `missing`, and `blocked` buckets, stratifies by `site_structures.json`, and writes machine-readable JSON reports. Add a sample manifest file that pins which firms/profiles are used in verification so all later comparisons use the same denominator. The manifest must include 5 firms for each improvable structure type with at least 5 firms available (`SITEMAP_XML`, `HTML_DIRECTORY_FLAT`, `HTML_ALPHA_PAGINATED`, `SPA_OTHER`); if a structure type has fewer than 5 firms available, include all of them; also include up to 2 firms each for `BOT_PROTECTED` and `AUTH_REQUIRED` only as exclusion-control rows.
  **Must NOT do**: Do not fetch live sites by default. Do not score blocked firms as ordinary missing values. Do not define success as non-empty-only fill-rate.

  **Recommended Agent Profile**:
  - Category: `deep` — Reason: this task defines the shared verification contract that all later work depends on.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — browser automation is not needed for a cached measurement harness.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: [2, 3, 4, 5, 6, 7, 8, 9, 10] | Blocked By: []

  **References**:
  - Pattern: `site_structures.json` — structure-type denominator and blocked-firm classification source.
  - Pattern: `run_pipeline.py` — current output shape for main-path JSONL generation.
  - Pattern: `find_attorney.py` — alternate-path output shape and field naming expectations.
  - Pattern: `.sisyphus/drafts/reboot-handoff-field-quality-fillrate-all-firms.md:42-60` — confirmed measurement distinctions and blocked-firm risks.
  - Pattern: `.sisyphus/drafts/field-quality-fillrate-all-firms.md:10-22` — confirmed technical decisions and cross-field coupling.

  **Acceptance Criteria**:
  - [ ] `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_before.json` exits 0.
  - [ ] `outputs/baseline_before.json` includes top-level keys for `summary`, `by_structure_type`, `by_field`, and `blocked_firms`.
  - [ ] Each target field report contains counts for `correct`, `contaminated`, `missing`, and `blocked_excluded`.
  - [ ] The script exits non-zero if the manifest references missing cached inputs.

  **QA Scenarios**:
  ```
  Scenario: Baseline report generation
    Tool: Bash
    Steps: Run `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_before.json`.
    Expected: Exit code 0 and a JSON file containing structure-type and blocked-firm sections.
    Evidence: .sisyphus/evidence/task-1-measure-baseline.txt

  Scenario: Manifest entry missing cache artifact
    Tool: Bash
    Steps: Run the script against a temporary manifest that references a non-existent cached profile input.
    Expected: Non-zero exit code with an error naming the missing manifest entry; no success report is emitted.
    Evidence: .sisyphus/evidence/task-1-measure-baseline-error.txt
  ```

  **Commit**: YES | Message: `[stream-0] feat(measurement): add structure-aware baseline contract` | Files: [`measure_baseline.py`, `tests/fixtures/sample_manifest.json`]

- [x] 2. Create synthetic fixture corpus and parser/enrichment regression harness

  **예상 소요시간**: 2시간

  **What to do**: Add synthetic/anonymized HTML fixtures that represent at least `SITEMAP_XML`, `HTML_DIRECTORY_FLAT`, `HTML_ALPHA_PAGINATED`, and `SPA_OTHER` patterns. Add standalone regression scripts that load fixtures into `ProfileEnricher` and assert exact extraction behavior for the five target fields. Ensure fixtures include both intended headings and adversarial content that should not be extracted.
  **Must NOT do**: Do not copy live firm HTML verbatim. Do not create fixtures that encode a single firm's layout too literally. Do not rely on network fetches.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: this is test/fixture design with multiple representative patterns.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — fixtures are static.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [6, 7, 8, 10] | Blocked By: [1]

  **References**:
  - Pattern: `enrichment.py` — `ProfileEnricher.enrich()` entrypoint for fixture-backed integration tests.
  - Pattern: `parser_sections.py` — section heading and content collection behavior that fixtures must cover.
  - Pattern: `AGENTS.md` Architecture + Test Commands sections — standalone script convention, no pytest.
  - Pattern: `site_structures.json` — fixture classes to represent.

  **Acceptance Criteria**:
  - [ ] `tests/fixtures/html/` contains at least one synthetic fixture per required structure type.
  - [ ] `python3.12 tests/test_enrichment_integration.py` exits 0.
  - [ ] The regression harness includes at least one adversarial fixture where a navigation/service heading must not populate `practice_areas` or `department`.
  - [ ] The harness fails clearly when a fixture is missing or malformed.

  **QA Scenarios**:
  ```
  Scenario: Fixture-backed enrichment regression passes
    Tool: Bash
    Steps: Run `python3.12 tests/test_enrichment_integration.py`.
    Expected: Exit code 0 with assertions covering title, offices, department, practice_areas, and industries.
    Evidence: .sisyphus/evidence/task-2-enrichment-fixtures.txt

  Scenario: Adversarial fixture prevents false-positive extraction
    Tool: Bash
    Steps: Run the regression script section that feeds a fixture containing navigation text such as "Client Services" and non-profile overview text.
    Expected: `practice_areas` and `department` remain unpopulated or explicitly rejected for that fixture.
    Evidence: .sisyphus/evidence/task-2-enrichment-fixtures-error.txt
  ```

  **Commit**: YES | Message: `[stream-0] test(fixtures): add synthetic multi-structure enrichment fixtures` | Files: [`tests/fixtures/html/*`, `tests/test_enrichment_integration.py`]

- [x] 3. Add parser-specific false-positive regression suite

  **예상 소요시간**: 1시간 30분

  **What to do**: Add `tests/test_parser_sections.py` covering positive and adversarial cases for heading normalization and `_collect_content_after` stopping behavior. Include explicit checks for known high-risk synonyms such as service/group/section and newly added industry-oriented headings such as client sectors/focused industries only when backed by fixtures.
  **Must NOT do**: Do not expand synonyms without a paired adversarial test. Do not add more than five new synonyms per target field in this plan.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: narrow parser regression harness around already-identified behaviors.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — parser tests are pure HTML parsing.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [6, 8] | Blocked By: [1]

  **References**:
  - Pattern: `parser_sections.py` — `SECTION_SYNONYMS`, `_normalize_heading`, `_collect_content_after`.
  - Pattern: `.sisyphus/drafts/reboot-handoff-field-quality-fillrate-all-firms.md:32-39` — parser_sections is a cross-field leverage point.
  - Pattern: Metis review — specific false-positive risks around "service", "section", and "group".

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_parser_sections.py` exits 0.
  - [ ] Every new synonym introduced by later parser changes has one positive and one adversarial assertion in this script.
  - [ ] The script covers a nested-heading case proving `_collect_content_after` stops before unrelated sections.

  **QA Scenarios**:
  ```
  Scenario: Parser positive and adversarial cases all pass
    Tool: Bash
    Steps: Run `python3.12 tests/test_parser_sections.py`.
    Expected: Exit code 0 with explicit positive and false-positive coverage.
    Evidence: .sisyphus/evidence/task-3-parser-tests.txt

  Scenario: Nested heading bleed-through is rejected
    Tool: Bash
    Steps: Execute the test case that places a valid heading before an unrelated same-level heading.
    Expected: Extracted section content stops before the unrelated heading; no contamination from later blocks.
    Evidence: .sisyphus/evidence/task-3-parser-tests-error.txt
  ```

  **Commit**: YES | Message: `[stream-0] test(parser_sections): add false-positive regressions` | Files: [`tests/test_parser_sections.py`]

- [x] 4. Add validator regression suite for title and offices policy

  **예상 소요시간**: 1시간 30분

  **What to do**: Add `tests/test_validators.py` covering valid title acceptance, contamination rejection, empty/sentinel behavior, office normalization behavior, and the chosen international-office policy. Include explicit assertions for case normalization and near-synonym handling if implemented, plus blocked cases where firm names, emails, phones, or generic text must be rejected.
  **Must NOT do**: Do not silently change the offices policy without tests documenting the expected behavior. Do not conflate missing and contaminated results.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: isolated validator rules with deterministic inputs.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — not needed.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [7, 8, 9] | Blocked By: [1]

  **References**:
  - Pattern: `validators.py` — `validate_title`, `validate_offices`, target-field sentinel logic.
  - Pattern: `test_title_regression.py` — existing standalone test style to follow.
  - Pattern: Metis review — unused `_KNOWN_ATTORNEY_TITLES`, international office gap, contamination examples.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_validators.py` exits 0.
  - [ ] The validator suite covers at least: valid title, firm-name contamination, email contamination, empty input, and office handling for a non-US example according to the selected policy.
  - [ ] Failure output identifies the exact validator rule that regressed.

  **QA Scenarios**:
  ```
  Scenario: Validator regression suite passes
    Tool: Bash
    Steps: Run `python3.12 tests/test_validators.py`.
    Expected: Exit code 0 and explicit pass coverage for title/offices edge cases.
    Evidence: .sisyphus/evidence/task-4-validator-tests.txt

  Scenario: Known contamination is rejected
    Tool: Bash
    Steps: Execute the test case supplying a title candidate that contains a firm name and an email address.
    Expected: Validator returns the expected rejection/sentinel outcome instead of accepting the string.
    Evidence: .sisyphus/evidence/task-4-validator-tests-error.txt
  ```

  **Commit**: YES | Message: `[stream-0] test(validators): add title and offices regressions` | Files: [`tests/test_validators.py`]

- [x] 5. Create cross-path comparison harness and sampled-output manifest

  **예상 소요시간**: 2시간

  **What to do**: Add `compare_paths.py` that compares main-path and alternate-path JSONL outputs for the five target fields on the same sampled firm/profile manifest. Emit machine-readable per-field diffs, blocked-firm exclusions, and a non-regression exit code based on a fixed 10 percentage-point tolerance for each target field. Pin the sampled-output manifest so later waves compare the same firms across both paths.
  **Must NOT do**: Do not compare paths using different firm samples. Do not treat absent outputs for blocked firms as regressions.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: cross-path schema reconciliation and reporting logic.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — this is output comparison, not browser work.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: [9, 10] | Blocked By: [1]

  **References**:
  - Pattern: `find_attorney.py` — alternate-path output fields and merge behavior source.
  - Pattern: `run_pipeline.py` — main-path output naming and sampling flow.
  - Pattern: Metis review — cross-path comparison artifact requirement and within-threshold exit-code rule.

  **Acceptance Criteria**:
  - [ ] `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff.json --max-gap 0.10` exits 0 when each target field stays within the 10 percentage-point tolerance.
  - [ ] `outputs/cross_path_diff.json` includes `per_field`, `per_firm`, `threshold`, and `blocked_firms_excluded` sections.
  - [ ] The script exits non-zero when any target field exceeds the 10 percentage-point tolerance.

  **QA Scenarios**:
  ```
  Scenario: Cross-path diff report generation
    Tool: Bash
    Steps: Run `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff.json --max-gap 0.10` on matched sampled JSONL outputs from both paths.
    Expected: Exit code 0 and a report with per-field delta information.
    Evidence: .sisyphus/evidence/task-5-compare-paths.txt

  Scenario: Regression threshold breach is caught
    Tool: Bash
    Steps: Run the compare command with `--max-gap 0.10` on a deliberately mismatched fixture/sample pair where one path omits a target field repeatedly.
    Expected: Non-zero exit code and a report naming the regressed field.
    Evidence: .sisyphus/evidence/task-5-compare-paths-error.txt
  ```

  **Commit**: YES | Message: `[stream-0] feat(compare_paths): add cross-path diff harness` | Files: [`compare_paths.py`, `tests/fixtures/sample_manifest.json`]

- [x] 6. Tighten shared section parsing without synonym overreach

  **예상 소요시간**: 2시간

  **What to do**: Update `parser_sections.py` to reduce false positives from generic heading synonyms and to add only evidence-backed industry/practice-area heading aliases needed by the fixture corpus. Fix `_collect_content_after` so section collection stops cleanly at unrelated sections in nested DOM layouts. Keep changes minimal and directly traceable to failing parser/enrichment fixtures.
  **Must NOT do**: Do not add more than five new synonyms per field. Do not use raw word additions such as `service`, `group`, or `section` without contextual guards and paired adversarial tests. Do not alter unrelated biography/education parsing behavior outside what the new tests prove necessary.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: cross-field parser behavior affects multiple target fields and requires careful regression control.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — parser work is deterministic and fixture-backed.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [8, 10] | Blocked By: [2, 3]

  **References**:
  - Pattern: `parser_sections.py` — primary implementation target.
  - Test: `tests/test_parser_sections.py` — false-positive and nested-heading guardrails.
  - Test: `tests/test_enrichment_integration.py` — end-to-end field extraction expectations from fixtures.
  - Research: Metis review — false-positive risks around service/section/group and missing industry heading aliases.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_parser_sections.py` exits 0 after parser changes.
  - [ ] `python3.12 tests/test_enrichment_integration.py` exits 0 with no newly introduced contamination in adversarial fixtures.
  - [ ] Any synonym additions are documented in tests with one positive and one adversarial case each.
  - [ ] No target field’s contaminated count increases in `measure_baseline.py` sampled comparison by more than 2 percentage points after this task alone.

  **QA Scenarios**:
  ```
  Scenario: Shared parser fixes improve intended extraction
    Tool: Bash
    Steps: Run `python3.12 tests/test_parser_sections.py` and `python3.12 tests/test_enrichment_integration.py` after applying parser changes.
    Expected: Both scripts exit 0; intended section headings populate the correct target fields.
    Evidence: .sisyphus/evidence/task-6-parser-fixes.txt

  Scenario: Generic heading no longer contaminates field extraction
    Tool: Bash
    Steps: Run the adversarial parser/enrichment fixture cases containing headings like "Client Services" or generic "Section" wrappers.
    Expected: `practice_areas`, `department`, and `industries` remain unfilled or correctly rejected for those adversarial cases.
    Evidence: .sisyphus/evidence/task-6-parser-fixes-error.txt
  ```

  **Commit**: YES | Message: `[stream-1] fix(parser_sections): tighten section normalization and stop conditions` | Files: [`parser_sections.py`, `tests/test_parser_sections.py`, `tests/test_enrichment_integration.py`]

- [x] 7. Tighten validator behavior for title and offices without suppressing valid data

  **예상 소요시간**: 1시간 30분

  **What to do**: Update `validators.py` so title handling rejects known contamination consistently, normalizes casing/near-synonym variants when covered by tests, and makes the `_KNOWN_ATTORNEY_TITLES` policy explicit instead of leaving it unused. Use this default title policy: normalize casing and a small tested alias set (for example `Sr. Associate` → `Senior Associate`, `Of counsel` → `Of Counsel`) but do not reject previously unseen attorney titles if they are contamination-free. Update office validation to follow one documented rule for international office handling and normalization while preserving separate contamination vs missing diagnostics. Use this default office policy: accept cleaned non-US office strings as valid `offices` values, preserve the cleaned label when no US canonical mapping exists, and emit diagnostics indicating the value is international rather than silently dropping it.
  **Must NOT do**: Do not enforce a tiny canonical title whitelist that drops valid free-form attorney titles. Do not silently discard international offices without either capturing them or flagging them explicitly per the implemented policy.

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: narrowly scoped validator behavior with deterministic input/output checks.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — not required.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: [8, 9, 10] | Blocked By: [2, 4]

  **References**:
  - Pattern: `validators.py` — implementation target.
  - Test: `test_title_regression.py` — existing title regression style to preserve/extend.
  - Test: `tests/test_validators.py` — new acceptance harness.
  - Research: Metis review — unused `_KNOWN_ATTORNEY_TITLES`, international office gap, contamination examples.

  **Acceptance Criteria**:
  - [ ] `python3.12 test_title_regression.py` exits 0.
  - [ ] `python3.12 tests/test_validators.py` exits 0.
  - [ ] Title candidates containing firm-name/email/phone contamination are rejected consistently.
  - [ ] Office validation emits the documented result for a non-US office example and does not silently reclassify contamination as ordinary missing.

  **QA Scenarios**:
  ```
  Scenario: Validator tightening preserves valid attorney data
    Tool: Bash
    Steps: Run `python3.12 test_title_regression.py` and `python3.12 tests/test_validators.py`.
    Expected: Both scripts exit 0, including valid title/offices cases.
    Evidence: .sisyphus/evidence/task-7-validator-fixes.txt

  Scenario: Contaminated title and office inputs are handled explicitly
    Tool: Bash
    Steps: Execute the validator cases using a firm-name title string, an email-contaminated title string, and an international/non-US office sample.
    Expected: Contaminated titles are rejected; the office case follows the documented policy with a non-ambiguous result.
    Evidence: .sisyphus/evidence/task-7-validator-fixes-error.txt
  ```

  **Commit**: YES | Message: `[stream-2] fix(validators): tighten title and offices policy` | Files: [`validators.py`, `test_title_regression.py`, `tests/test_validators.py`]

- [x] 8. Close main-path extraction gaps across JSON-LD, embedded state, and proximity fallback

  **예상 소요시간**: 2시간 30분

  **What to do**: Update `enrichment.py` so the main pipeline can populate the five target fields through the existing cascade without relying on new firm-specific branches. Specifically close the known Stage-5 gaps for `department` and `industries`, ensure JSON-LD and embedded-state merge logic covers the target fields when those values are present, and preserve de-duplication/provenance behavior so earlier-stage good data is not overwritten by weaker later-stage candidates.
  **Must NOT do**: Do not add new `if "firm.com" in url` conditions. Do not bypass validators. Do not widen proximity heuristics beyond the explicit target fields and tested keywords required to satisfy fixtures and sampled baseline deltas.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: core extraction path work with multiple stage interactions.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — default verification is cached/fixture-backed, not browser-driven.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [9, 10] | Blocked By: [2, 3, 4, 6, 7]

  **References**:
  - Pattern: `enrichment.py` — `_merge_json_ld`, `_merge_embedded_state`, `_extract_from_section_map`, `_proximity_fallback`, `ProfileEnricher.enrich()`.
  - Pattern: `attorney_extractor.py` — field conventions and existing profile shape.
  - Test: `tests/test_enrichment_integration.py` — fixture-backed extraction expectations.
  - Test: `tests/test_parser_sections.py`, `tests/test_validators.py` — upstream guardrails.
  - Research: Metis review — Stage-5 gap for `industries`/`department` and hard-coded CSS anti-pattern.

  **Acceptance Criteria**:
  - [ ] `python3.12 tests/test_enrichment_integration.py` exits 0.
  - [ ] `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_main_path_after_stream3.json` exits 0.
  - [ ] In the sampled baseline report, at least one of `department` or `industries` shows measurable improvement versus `outputs/baseline_before.json` without any target field contaminated-rate increasing by more than 2 percentage points.
  - [ ] No new firm-specific branch appears in `enrichment.py`.

  **QA Scenarios**:
  ```
  Scenario: Main-path extraction improvements raise sampled fill-rate
    Tool: Bash
    Steps: Run `python3.12 tests/test_enrichment_integration.py` and then `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_main_path_after_stream3.json`.
    Expected: Tests pass and the resulting report shows improvement for at least one currently underfilled target field.
    Evidence: .sisyphus/evidence/task-8-main-path.txt

  Scenario: Over-broad fallback causes contamination and is caught
    Tool: Bash
    Steps: Run the adversarial fixtures plus a compare of `outputs/baseline_before.json` vs `outputs/baseline_main_path_after_stream3.json`.
    Expected: If contaminated-rate increases by more than 2 percentage points for any target field, the task is treated as failed and must be revised.
    Evidence: .sisyphus/evidence/task-8-main-path-error.txt
  ```

  **Commit**: YES | Message: `[stream-3] fix(enrichment): close main-path field coverage gaps` | Files: [`enrichment.py`, `tests/test_enrichment_integration.py`, `outputs/baseline_main_path_after_stream3.json`]

- [x] 9. Align alternate-path merge and provenance behavior with the main quality contract

  **예상 소요시간**: 2시간

  **What to do**: Audit `find_attorney.py`, `field_enricher.py`, and `field_merger.py` to choose one canonical merge path for the alternate architecture. Update the call flow so provenance/confidence attribution is applied consistently for all profiles, not only partial/failed ones, while keeping `field_enricher.py` internal refactoring to the minimum necessary. Ensure output fields and reason metadata are comparable with the main-path baseline/compare scripts.
  **Must NOT do**: Do not perform a broad redesign of `field_enricher.py`. Do not leave both merge strategies active without an explicit authority rule. Do not change output semantics in a way that breaks `compare_paths.py`.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: architectural alignment across several alternate-path modules.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — path alignment is not browser-dependent.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [10] | Blocked By: [4, 5, 7, 8]

  **References**:
  - Pattern: `find_attorney.py` — current alternate-path orchestration and `merge_attorney_fields` behavior.
  - Pattern: `field_enricher.py` — provenance/confidence extraction primitives.
  - Pattern: `field_merger.py` — alternate merge primitive that may need to become canonical.
  - Test: `tests/test_validators.py` — validator outputs expected by alternate-path comparison.
  - Tooling: `compare_paths.py` — output compatibility contract.
  - Research: Metis review — dual merge ambiguity and partial-only enrichment gap.

  **Acceptance Criteria**:
  - [ ] The alternate path has one documented canonical merge path; the obsolete path is removed or explicitly deprecated in code comments.
  - [ ] `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff_after_alignment.json --max-gap 0.10` exits 0.
  - [ ] Alternate-path outputs include enough field/provenance metadata to be aggregated by `measure_baseline.py` and compared by `compare_paths.py`.
  - [ ] Applying enrichment/provenance to complete profiles does not regress sampled alternate-path output quality.

  **QA Scenarios**:
  ```
  Scenario: Alternate-path alignment yields comparable outputs
    Tool: Bash
    Steps: Produce matched sample JSONL outputs from both paths, then run `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff_after_alignment.json --max-gap 0.10`.
    Expected: Exit code 0 and a report showing all target fields within threshold.
    Evidence: .sisyphus/evidence/task-9-alt-path.txt

  Scenario: Stale dual-merge behavior is detected
    Tool: Bash
    Steps: Run the compare harness and any targeted alternate-path regression case where the deprecated merge path would produce different field precedence.
    Expected: If both merge behaviors are still active or precedence is ambiguous, the report/test fails with a field-level mismatch.
    Evidence: .sisyphus/evidence/task-9-alt-path-error.txt
  ```

  **Commit**: YES | Message: `[stream-4] fix(find_attorney): align merge and provenance behavior` | Files: [`find_attorney.py`, `field_enricher.py`, `field_merger.py`, `outputs/cross_path_diff_after_alignment.json`]

- [x] 10. Run final before/after measurement gate and anti-overfitting verification

  **예상 소요시간**: 1시간 30분

  **What to do**: Re-run the full sampled baseline after Streams 6-9, generate the final before/after comparison, and verify structure-aware improvements without contamination regressions. Produce final evidence artifacts that separate blocked firms, show field deltas by structure type, and confirm the alternate path remains within threshold of the main path on the pinned sample set.
  **Must NOT do**: Do not declare success from a single favorable field. Do not hide a contamination increase by averaging it away across fields or structures. Do not use live-only results as the primary acceptance evidence.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: final quality gate consolidating all outputs and regression thresholds.
  - Skills: `[]` — no extra skill required.
  - Omitted: `['playwright']` — this gate is report-driven.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [Final Verification] | Blocked By: [5, 6, 7, 8, 9]

  **References**:
  - Tooling: `measure_baseline.py` — before/after comparison contract.
  - Tooling: `compare_paths.py` — cross-path non-regression contract.
  - Artifact: `outputs/baseline_before.json` — baseline reference point.
  - Artifact: `outputs/cross_path_diff_after_alignment.json` — alternate-path comparison input.
  - Research: Metis review — anti-overfitting threshold and blocked-firm reporting requirements.

  **Acceptance Criteria**:
  - [ ] `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_after.json` exits 0.
  - [ ] `python3.12 measure_baseline.py --compare outputs/baseline_before.json outputs/baseline_after.json --min-improvement 0.05` exits 0.
  - [ ] No target field in the sampled report shows contaminated-rate regression greater than 2 percentage points.
  - [ ] `python3.12 compare_paths.py --main-jsonl outputs/run_pipeline_sample.jsonl --alt-jsonl outputs/find_attorney_sample.jsonl --fields title,offices,department,practice_areas,industries --report outputs/cross_path_diff_final.json --max-gap 0.10` exits 0.

  **QA Scenarios**:
  ```
  Scenario: Final before/after gate passes
    Tool: Bash
    Steps: Run `python3.12 measure_baseline.py --manifest tests/fixtures/sample_manifest.json --use-cache --output outputs/baseline_after.json` and then `python3.12 measure_baseline.py --compare outputs/baseline_before.json outputs/baseline_after.json --min-improvement 0.05`.
    Expected: Both commands exit 0 and the compare report shows measurable net improvement without blocked-firm denominator pollution.
    Evidence: .sisyphus/evidence/task-10-final-gate.txt

  Scenario: Anti-overfitting gate catches contamination regression
    Tool: Bash
    Steps: Run the compare mode against a deliberately regressed output where one field's contaminated count is higher than baseline by more than 2 percentage points.
    Expected: Non-zero exit code and a report naming the offending field/structure type.
    Evidence: .sisyphus/evidence/task-10-final-gate-error.txt
  ```

  **Commit**: YES | Message: `[stream-6] chore(quality-gate): add final before-after verification artifacts` | Files: [`outputs/baseline_after.json`, `outputs/cross_path_diff_final.json`, `.sisyphus/evidence/task-10-final-gate.txt`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Preserve red/green history: each parser or validator behavior change starts with a failing standalone script, followed by the implementation commit that makes it pass.
- Commit format: `[stream-N] type(scope): description`
- Never combine baseline artifact generation with extraction logic changes.
- Keep blocked-firm reporting, cross-path compare, and anti-overfitting gate as separate commits for easy regression isolation.

## Success Criteria
- All five target fields have machine-readable before/after quality reports.
- No targeted field shows contaminated-rate regression greater than 2 percentage points in the sampled structure-aware baseline.
- `compare_paths.py` exits 0 with alternate-path field performance within the agreed threshold of the main path sample.
- `compare_paths.py` exits 0 with alternate-path field performance within 10 percentage points of the main path sample for every target field.
- Reports explicitly separate blocked firms from improvable firms.
- No new hard-coded firm-specific extraction branches are introduced.
