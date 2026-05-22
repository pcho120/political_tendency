# Title / Offices / Departments 추출 품질 개선 — Recovery Plan

## TL;DR
> **Summary**: 기존 `title-office-dept-fix` 실행은 최종 검증까지 갔지만 F3 contamination 실패로 종료됐다. 복구 범위는 좁게 유지한다: 현재 코드 기준으로 title 오염을 재현하고, 실제 누수 경로를 확인한 뒤, `validators.py` 및 필요 시 `enrichment.py`만 최소 수정하여 최종 검증을 재통과시킨다.
> **Deliverables**:
> - `validators.py` — firm-name contamination 회귀 방지 수정
> - `enrichment.py` — Weil title 오염 경로가 validator 밖이면 해당 경로만 수정
> - `test_title_regression.py` 또는 동등한 standalone test script — failing → passing title contamination regression coverage
> - `.sisyphus/evidence/` — 재현, no-regression, final verification evidence
> **Effort**: Short
> **Parallel**: NO
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → F1–F4

## Context
### Original Request
기존 작업이 어디까지 됐는지 확인한 뒤, 남은 이슈만 정리한 복구 플랜 생성.

### Interview Summary
- 활성 실행 상태는 `.sisyphus/boulder.json` 기준 `title-office-dept-fix.md` 에 묶여 있다.
- `.sisyphus/evidence/final-report.txt` 기준 최종 상태는 `OVERALL: NEEDS FIXES` 이다.
- F1 Regression은 PASS, F2 Improvements는 PARTIAL, F3 Contamination은 FAIL 이다.
- 미해결 오염 대상은 Weil, Knobbe Martens, ArentFox Schiff 이다.
- 기존 리포트는 `validate_title()` firm-name filter 비활성화를 root cause 로 적었지만, 현재 코드(`validators.py:231-293`)에는 이미 firm-name contamination 로직이 존재한다.

### Metis Review (gaps addressed)
- 핵심 가드레일: **"필터를 켠다"고 가정하지 말고 먼저 현재 코드로 재현**한다.
- `profile.firm` 이 validation 시점에 비어 있을 가능성, 또는 contamination 이 validation 이후 stage 에서 재주입될 가능성을 우선 확인한다.
- Weil 은 og:title 경로로 추정되지만 실제 source stage 가 불명확하므로 별도 진단 task 를 강제한다.
- Cahill offices 는 final report 상 minor note 이지만 이번 recovery 스코프에서는 제외한다.

## Work Objectives
### Core Objective
`title-office-dept-fix` 의 남은 title contamination 만 제거하고, 기존 PASS regression 을 깨지 않은 채 최종 검증(F1-F4)을 재통과시킨다.

### Deliverables
- contamination 재현 evidence
- title contamination regression test coverage
- validator/enrichment 최소 수정본
- fresh no-regression evidence
- final verification rerun evidence

### Definition of Done (verifiable conditions with commands)
- [x] `python3.12 run_pipeline.py --firms "knobbe" --max-profiles 5 --verbose` 결과에서 title 이 `Knobbe Martens` 인 행 0개
- [x] `python3.12 run_pipeline.py --firms "arentfox" --max-profiles 5 --verbose` 결과에서 title 이 `ArentFox Schiff` 인 행 0개
- [x] `python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose` 결과에서 title 이 `Weil, Gotshal & Manges LLP` 인 행 0개
- [x] `python3.12 run_pipeline.py --firms "kirkland" --max-profiles 10` 결과에서 valid title 신규 rejection 0개
- [x] `.sisyphus/evidence/final-report-v2.txt` 에 F1/F2/F3/F4 모두 PASS 기록

### Must Have
- 재현 → 테스트 작성 → 수정 → Weil 경로 진단/수정 → 최종 검증 순서 고정
- `validators.py` 변경 전 현재 실패 사례를 evidence 와 test case 로 고정
- Weil contamination source 가 validator 밖이면 `enrichment.py` 의 해당 assignment path 만 수정
- `profile.firm` 값이 validation 시점에 전달되는지 evidence 로 확인
- `newly rejected: 0` 검증을 fresh run 기준으로 재생성

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- `parser_sections.py`, `discovery.py`, `run_pipeline.py` 구조 변경 금지
- Cahill offices, departments fill-rate, 신규 firm selector 추가 금지
- contamination 재현 없이 "추정 수정" 금지
- Weil 진단 없이 og:title 추정만으로 수정 금지
- final verification 전에 임의로 완료 처리 금지

## Verification Strategy
> ZERO HUMAN INTERVENTION — all verification is agent-executed.
- Test decision: tests-after + standalone script or repo-native direct Python script (`python3.12 ...`); pytest 전제 금지
- QA policy: Every task includes direct command-based scenarios and saved evidence
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.txt`

## Execution Strategy
### Parallel Execution Waves
> This recovery is intentionally sequential because each step narrows the root cause for the next.

Wave 1: reproduce + pin failure cases
Wave 2: add regression tests / scripts for contaminated vs valid titles
Wave 3: fix validator logic or firm-name handoff path
Wave 4: diagnose and fix Weil-specific title source if still failing
Wave 5: targeted reruns + final verification

### Dependency Matrix (full, all tasks)
- Task 1: no dependencies
- Task 2: blocked by Task 1
- Task 3: blocked by Task 2
- Task 4: blocked by Task 3
- Task 5: blocked by Task 4
- F1-F4: blocked by Task 5

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 1 task → `quick`
- Wave 2 → 1 task → `quick`
- Wave 3 → 1 task → `unspecified-high`
- Wave 4 → 1 task → `unspecified-high`
- Wave 5 → 1 task → `quick`
- Final → 4 tasks → `oracle`, `unspecified-high`, `unspecified-high`, `deep`

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. 현재 contamination 상태 재현 및 실제 validator 입력 확인

  **예상 소요시간**: 15분

  **What to do**: 
  - `.sisyphus/evidence/final-report.txt` 의 failing firms 3개(Weil, Knobbe, ArentFox)만 fresh run 으로 재현한다.
  - 각 run 후 최신 JSONL 에서 firm/title/profile_url 을 추출해 contamination count 를 저장한다.
  - 추가로 `profile.diagnostics["title_reason"]` 존재 여부를 함께 기록한다.
  - `validators.py:231-293` 와 `enrichment.py:402-421` 기준으로 현재 validator 가 실제 호출되는 구조를 evidence 에 요약한다.
  - **중요**: 이 단계에서는 코드 수정 금지. 오직 재현과 사실 확정만 수행.

  **Must NOT do**:
  - validator/enrichment 코드 수정 금지
  - Kirkland 등 regression firm 까지 확장 실행 금지

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: direct reproduction + evidence capture only
  - Skills: []
  - Omitted: [`playwright`] — Reason: browser automation 불필요; pipeline command 로 재현 가능

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: [2, 3, 4, 5, F1, F2, F3, F4] | Blocked By: []

  **References**:
  - `validators.py:231-293` — current `validate_title()` contamination logic
  - `enrichment.py:402-421` — title validation call site
  - `.sisyphus/evidence/final-report.txt:24-32` — failing contamination targets
  - `.sisyphus/boulder.json:12-39` — prior execution sessions

  **Acceptance Criteria** (agent-executable only):
  - [ ] `.sisyphus/evidence/task-1-repro.txt` 에 3개 firm contamination count 저장
  - [ ] evidence 에 current validator call path 요약 포함
  - [ ] 각 firm 에 대해 title contamination 이 현재도 재현되는지 binary result 확보

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: contamination 재현
    Tool: Bash
    Steps: 1. `python3.12 run_pipeline.py --firms "knobbe" --max-profiles 5 --verbose`
           2. `python3.12 run_pipeline.py --firms "arentfox" --max-profiles 5 --verbose`
           3. `python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose`
           4. 최신 `outputs/*.jsonl` 을 읽어 contaminated title 수 집계 후 `.sisyphus/evidence/task-1-repro.txt` 로 저장
    Expected: 각 firm 의 contaminated title count 가 숫자로 기록되고, contamination 존재 여부가 명확히 나온다
    Evidence: .sisyphus/evidence/task-1-repro.txt

  Scenario: validator 입력 경로 확인
    Tool: Bash
    Steps: 1. `python3.12 -c "from pathlib import Path; import itertools; p=Path('enrichment.py'); lines=p.read_text().splitlines(); print(lines[414]); print(lines[415]); print(lines[416])"`
    Expected: `validate_title(profile.title, firm_name=profile.firm or "")` 호출이 출력된다
    Evidence: .sisyphus/evidence/task-1-validator-call.txt
  ```

  **Commit**: NO | Message: `n/a` | Files: `.sisyphus/evidence/task-1-repro.txt`, `.sisyphus/evidence/task-1-validator-call.txt`

- [x] 2. contamination regression script 작성 및 failing baseline 고정

  **예상 소요시간**: 20분

  **What to do**:
  - 새 standalone test script (`test_title_regression.py`) 를 추가한다.
  - repo 제약상 pytest 전제 금지. `python3.12 test_title_regression.py` 로 직접 실행 가능한 형태로 작성한다.
  - 두 케이스 테이블을 포함한다:
    - `CONTAMINATED_CASES`: Weil, Knobbe Martens, ArentFox Schiff exact/variant titles
    - `VALID_CASES`: Partner, Senior Associate, Managing Partner 등 기존 정상 title
  - 스크립트는 각 case 에 대해 `validate_title(raw, firm_name=...)` 결과를 검사하고, 실패 시 non-zero exit code 를 반환해야 한다.
  - **중요**: Task 1 재현 결과와 다르면 contaminated cases 테이블을 Task 1 evidence 에 맞춰 조정한다.

  **Must NOT do**:
  - pytest import 추가 금지
  - production logic 변경 금지

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: isolated test script authoring
  - Skills: []
  - Omitted: [`refactor`] — Reason: 구조 개편이 아닌 bounded regression harness 작성

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: [3, 4, 5, F1, F2, F3, F4] | Blocked By: [1]

  **References**:
  - `validators.py:231-293` — function under test
  - `.sisyphus/evidence/task-1-repro.txt` — confirmed contaminated titles
  - `.sisyphus/evidence/final-report.txt:40-53` — expected unresolved patterns
  - `test_extraction.py:1-78` — repo-native standalone script pattern

  **Acceptance Criteria** (agent-executable only):
  - [ ] `python3.12 test_title_regression.py` 실행 가능
  - [ ] contaminated cases 중 최소 1개가 current code 에서 FAIL 하거나, 모두 PASS 라면 script output 에 "repro mismatch" 경고를 남겨 Task 3 입력으로 사용
  - [ ] valid cases 가 명시적으로 포함됨

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: regression script 실행
    Tool: Bash
    Steps: 1. `python3.12 test_title_regression.py > .sisyphus/evidence/task-2-title-regression.txt 2>&1; test $? -eq 0 || true`
    Expected: contaminated / valid case 결과가 모두 출력된다
    Evidence: .sisyphus/evidence/task-2-title-regression.txt

  Scenario: direct validator smoke check
    Tool: Bash
    Steps: 1. `python3.12 -c "from validators import validate_title; cases=[('Knobbe Martens','Knobbe Martens Olson & Bear LLP'),('Partner','Knobbe Martens Olson & Bear LLP')]; [print(c, '->', validate_title(*c)) for c in cases]"`
    Expected: contaminated 케이스와 valid 케이스 결과 차이가 명시된다
    Evidence: .sisyphus/evidence/task-2-smoke.txt
  ```

  **Commit**: YES | Message: `test: add standalone regression coverage for title contamination` | Files: `test_title_regression.py`, `.sisyphus/evidence/task-2-title-regression.txt`, `.sisyphus/evidence/task-2-smoke.txt`

- [x] 3. validator 누수 원인 최소 수정

  **예상 소요시간**: 25분

  **What to do**:
  - Task 1/2 결과를 바탕으로 실제 누수 원인을 분기한다.
  - 분기 A: `validate_title()` 가 contaminated title 을 통과시키면 `validators.py` 에서 normalization 을 강화한다.
    - 공백 split 전 punctuation 제거/정규화
    - merged firm token (`ArentFox`) 및 suffix(`LLP`) 를 고려하되 과잉 차단 금지
  - 분기 B: `firm_name` 이 validation 시점에 비어 있거나 short-name 으로 들어오면, `enrichment.py` call path 또는 upstream firm handoff 를 최소 수정한다.
  - 수정 후 `python3.12 test_title_regression.py` 를 다시 실행해 contaminated cases PASS(=correctly rejected), valid cases PASS 를 확보한다.
  - 신규 거부 방지용 quick check 를 위해 `kirkland` 10-profile fresh run 을 수행하고 title 들을 `validate_title(title, firm_name=firm)` 에 재통과시켜 신규 rejection 0개를 기록한다.

  **Must NOT do**:
  - Weil-specific stage diagnosis를 여기서 추정 처리 금지
  - departments/offices 로직 수정 금지

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: logic correction with regression risk
  - Skills: []
  - Omitted: [`quick`] — Reason: title contamination heuristics 는 false positive 위험이 있어 신중한 수정 필요

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: [4, 5, F1, F2, F3, F4] | Blocked By: [2]

  **References**:
  - `validators.py:265-292` — existing firm-name contamination heuristics
  - `enrichment.py:415-421` — validation handoff path
  - `.sisyphus/evidence/task-1-repro.txt` — actual failing patterns
  - `test_title_regression.py` — expected behavior contract

  **Acceptance Criteria** (agent-executable only):
  - [ ] `python3.12 test_title_regression.py` exit code 0
  - [ ] `.sisyphus/evidence/task-3-newly-rejected.txt` 에 Kirkland 신규 rejection 0 기록
  - [ ] contamination cases 3개 모두 corrected rejection 으로 기록

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: regression script green
    Tool: Bash
    Steps: 1. `python3.12 test_title_regression.py > .sisyphus/evidence/task-3-regression-green.txt 2>&1`
    Expected: script exits 0 and reports all contaminated cases rejected, valid cases accepted
    Evidence: .sisyphus/evidence/task-3-regression-green.txt

  Scenario: newly rejected = 0
    Tool: Bash
    Steps: 1. `python3.12 run_pipeline.py --firms "kirkland" --max-profiles 10 > /tmp/task3-kirkland.log 2>&1`
           2. 최신 JSONL 을 읽어 기존 title 들을 `validate_title()` 로 재평가하여 rejection count 저장
    Expected: newly rejected count = 0
    Evidence: .sisyphus/evidence/task-3-newly-rejected.txt
  ```

  **Commit**: YES | Message: `fix(validators): close firm-name title contamination gaps` | Files: `validators.py`, `enrichment.py`(if handoff fix required), `test_title_regression.py`, `.sisyphus/evidence/task-3-regression-green.txt`, `.sisyphus/evidence/task-3-newly-rejected.txt`

- [x] 4. Weil 전용 title source 추적 및 필요 시 단일 경로 수정

  **예상 소요시간**: 25분

  **What to do**:
  - Task 3 이후에도 Weil contamination 이 남아 있으면 source stage 를 특정한다.
  - `enrichment.py` 내 title assignment points (`profile.title = ...`) 를 기준으로, Weil profile 에서 어떤 stage 가 최종 contaminated title 을 넣는지 직접 추적한다.
  - 진단용 임시 logging 또는 diagnostics key 추가는 허용되지만 최종 커밋 전에 제거하거나 production-safe diagnostics 로 정리한다.
  - contamination source 가 Stage 4 `section_map['title']` 이면 해당 candidate guard 를 Weil URL 범위에서 보강한다.
  - contamination source 가 Stage 5 `_extract_title_proximity(html)` 이면 proximity fallback 에 Weil-safe guard 를 추가한다.
  - contamination source 가 다른 stage 라면 그 assignment path 하나만 차단한다.

  **Must NOT do**:
  - Weil 이외 firm-specific selector 추가 금지
  - source stage 미확정 상태에서 blind patch 금지

  **Recommended Agent Profile**:
  - Category: `unspecified-high` — Reason: source tracing across multiple extraction stages
  - Skills: []
  - Omitted: [`playwright`] — Reason: browser layer가 아니라 extractor stage tracing 문제

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: [5, F1, F2, F3, F4] | Blocked By: [3]

  **References**:
  - `enrichment.py:600-690` — CSS-class title extraction paths
  - `enrichment.py:1238-1256` — section_map title path
  - `enrichment.py:1324-1328` — proximity fallback title path
  - `.sisyphus/evidence/final-report.txt:49-53` — Weil title diagnosis note

  **Acceptance Criteria** (agent-executable only):
  - [ ] `.sisyphus/evidence/task-4-weil-diagnosis.txt` 에 source stage 명시
  - [ ] `python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose` 결과 contamination 0
  - [ ] Knobbe/ArentFox regressions 재발 없음

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: Weil source stage 진단
    Tool: Bash
    Steps: 1. Weil 3-profile run with temporary diagnostics enabled
           2. title assignment stage / final title 값을 `.sisyphus/evidence/task-4-weil-diagnosis.txt` 로 저장
    Expected: contamination 이 유입되는 정확한 stage 가 1개 이상 특정된다
    Evidence: .sisyphus/evidence/task-4-weil-diagnosis.txt

  Scenario: Weil contamination 제거 확인
    Tool: Bash
    Steps: 1. `python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose`
           2. 최신 JSONL 에서 `title == 'Weil, Gotshal & Manges LLP'` count 집계
    Expected: count = 0
    Evidence: .sisyphus/evidence/task-4-weil-clean.txt
  ```

  **Commit**: YES | Message: `fix(enrichment): stop Weil-specific title contamination at source` | Files: `enrichment.py`, `.sisyphus/evidence/task-4-weil-diagnosis.txt`, `.sisyphus/evidence/task-4-weil-clean.txt`

- [x] 5. targeted rerun 및 final verification evidence 재생성

  **예상 소요시간**: 20분

  **What to do**:
  - contamination failure firms 3개와 regression reference firms 2개(Kirkland, Paul Hastings)를 재실행한다.
  - 결과를 합쳐 `.sisyphus/evidence/final-report-v2.txt` 를 작성한다.
  - final report 형식은 기존 `.sisyphus/evidence/final-report.txt` 와 동일한 구조를 유지하되, 이번 recovery 결과만 반영한다.
  - report 에 반드시 다음을 포함한다:
    - F1 Regression
    - F2 Improvements
    - F3 Contamination
    - F4 Scope fidelity (이번 recovery 가 scope 밖 이슈를 건드리지 않았는지)

  **Must NOT do**:
  - 전체 70개 firm rerun 금지
  - report 를 evidence 없이 서술형으로만 작성 금지

  **Recommended Agent Profile**:
  - Category: `quick` — Reason: bounded verification run aggregation
  - Skills: []
  - Omitted: [`oracle`] — Reason: this task is evidence generation; oracle is reserved for final verification wave

  **Parallelization**: Can Parallel: NO | Wave 5 | Blocks: [F1, F2, F3, F4] | Blocked By: [4]

  **References**:
  - `.sisyphus/evidence/final-report.txt` — report template and verdict style
  - `.sisyphus/evidence/task-3-newly-rejected.txt` — no-regression input
  - `.sisyphus/evidence/task-4-weil-clean.txt` — Weil cleanup result

  **Acceptance Criteria** (agent-executable only):
  - [ ] `.sisyphus/evidence/final-report-v2.txt` 생성
  - [ ] report 내 F1/F2/F3/F4 verdict 가 모두 binary 로 명시
  - [ ] contamination target 3개 firm 결과가 report 에 포함

  **QA Scenarios** (MANDATORY — task incomplete without these):
  ```
  Scenario: final report v2 생성
    Tool: Bash
    Steps: 1. targeted firms rerun
           2. aggregation script 로 verdict summary 생성
    Expected: `.sisyphus/evidence/final-report-v2.txt` 존재 and verdict sections complete
    Evidence: .sisyphus/evidence/final-report-v2.txt

  Scenario: contamination summary binary check
    Tool: Bash
    Steps: 1. `python3.12 -c "from pathlib import Path; t=Path('.sisyphus/evidence/final-report-v2.txt').read_text(); assert 'F3 Contamination' in t and 'FAIL' not in t.split('OVERALL')[0]; print('PASS')"`
    Expected: PASS
    Evidence: .sisyphus/evidence/task-5-final-check.txt
  ```

  **Commit**: YES | Message: `chore: record recovery verification evidence for title contamination fixes` | Files: `.sisyphus/evidence/final-report-v2.txt`, `.sisyphus/evidence/task-5-final-check.txt`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Commit 1: `test: add standalone regression coverage for title contamination`
- Commit 2: `fix(validators): close firm-name title contamination gaps`
- Commit 3: `fix(enrichment): stop Weil-specific title contamination at source` (only if Task 4 requires code change)
- Commit 4: `chore: record recovery verification evidence for title contamination fixes`

## Success Criteria
- All previously failing title contamination samples are removed
- Kirkland / Paul Hastings regression references remain clean
- No newly rejected valid titles on fresh sample run
- Final report v2 records all verification gates as PASS
