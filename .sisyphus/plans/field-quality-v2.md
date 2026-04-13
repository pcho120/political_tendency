# Field Quality V2 — Title/Office/Department 개선 + 128 실패 Firm 해결 + Access Denied 조기 탐지

## TL;DR

> **Quick Summary**: find_attorney.py 파이프라인의 필드 추출 품질을 대폭 개선. Office 31%→70%+, Title 85%→95%+, Department 82%→90%+. 128개 실패 firm을 위한 discovery fallback 강화. BOT_PROTECTED 26개 firm 조기 탐지/skip으로 시간 절약. Practice areas 네비게이션 오염 수정. 200개 전체 firm 재실행.
>
> **Deliverables**:
> - `validators.py`: office/title/practice_areas validation 개선
> - `find_attorney.py`: BOT_PROTECTED 조기 skip + per-firm 403 abort + discovery fallback 강화
> - `field_enricher.py`: office/title HTML 추출 패턴 확장
> - `config/practice_department_map.json`: 매핑 확장 (24→35+)
> - 200개 firm 전체 재실행 결과 (JSONL + Excel)
>
> **Estimated Effort**: Large (코드 수정 ~6-8시간 + 파이프라인 런타임 ~20-30시간)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: T1 (baseline) → T2-T6 (validators, parallel) → T7-T9 (pipeline flow) → T10 (full run) → F1-F4

---

## Context

### Original Request
T8 전체 실행 (39시간) 완료 후 유저가 필드 품질 분석. "title, office, department을 중점적으로 보안해서 모든 정보 가져올 수 있게 수정해줘. access denied 조기 탐지로 시간 단축."
최종 요청: "모든 200개 다 해줘. 법적으로 안되는것들 빼고"

### Interview Summary
**Key Discussions**:
- T8 결과: 72/200 firms 성공 (36%), 62,007 records, 39시간
- Office 31.3% (worst), Title 84.7%, Department 82.3%
- 128 실패 firm: 26 BOT_PROTECTED + 61 xml_sitemap 0결과 + 11 dom_exhaustion 0결과 + 30 기타 compliance blocked
- User: 200개 전체 대상, 법적 제약만 제외, practice_areas 오염도 수정

**Research Findings**:
1. **Pipeline 확인**: `find_attorney.py`가 T8 실행 파이프라인 (NOT run_pipeline.py)
2. **Discovery stubs**: `directory_listing`, `alphabet_enumeration`, `filter_enumeration`이 stub (dom_exhaustion으로 위임)
3. **Office bottleneck**: `_is_us_location()`이 `_US_MAJOR_LAW_CITIES` (120개) 외 도시 거부. "Bethesda, MD" 같은 유효 도시도 drop
4. **Title validation**: firm name contamination에 과도하게 민감
5. **Practice areas**: `_JUNK_PHRASES` (21개)에 nav 항목 없음 ("Home", "Search" 통과)
6. **Access denied**: `site_structures.json` BOT_PROTECTED를 find_attorney.py에서 로드하지 않음. 403을 프로필 단위로만 처리, firm 단위 중단 없음

### Metis Review
**Identified Gaps** (addressed):
- **Pipeline 혼동**: 두 개 파이프라인 존재 → find_attorney.py로 확정
- **_JUNK_PHRASES substring 위험**: "coverage" 같은 단어가 "Insurance Coverage" 차단 가능 → exact match 또는 nav-only 단어만 추가
- **Office validation 과도 완화 위험**: City, ST 포맷 허용 시 "Click Here, NY" 같은 garbage 허용 가능 → 최소 도시명 길이 + UI 단어 필터
- **Regression 위험**: validator 변경이 기존 72개 firm 품질 저하 가능 → baseline 캡처 후 비교 필수
- **61 sitemap-failed 근본 원인**: fallback이 이미 존재하는데도 실패 → 원인 진단 필요
- **practice_department_map 확장**: 24→35+ 매핑으로 department 간접 개선

---

## Work Objectives

### Core Objective
find_attorney.py 파이프라인의 title/office/department 추출 품질을 대폭 개선하고, 128개 실패 firm의 discovery를 강화하고, BOT_PROTECTED firm을 조기 탐지하여 200개 전체 firm에서 최대한 완전한 데이터를 추출한다.

### Concrete Deliverables
- `validators.py`: validate_offices(), validate_title(), validate_practice_areas() 개선
- `field_enricher.py`: office/title HTML heuristics 패턴 확장
- `find_attorney.py`: BOT_PROTECTED pre-skip, per-firm 403 abort, discovery stub 구현
- `config/practice_department_map.json`: 매핑 확장
- 200개 firm 전체 재실행 결과

### Definition of Done
- [ ] Title fill rate ≥ 90% (현재 84.7%)
- [ ] Office fill rate ≥ 60% (현재 31.3%)
- [ ] Department fill rate ≥ 85% (현재 82.3%)
- [ ] ≥ 140개 firm에서 attorney 추출 성공 (현재 72개)
- [ ] BOT_PROTECTED firm 0초 내 skip (현재 수천 초 낭비)
- [ ] Practice areas에 nav 항목 0건 (현재 "Home", "Search" 등 오염)
- [ ] 기존 72개 firm에서 regression 없음 (필드 카운트 ≥ 95% 유지)

### Must Have
- Title/office/department fill rate 개선
- BOT_PROTECTED firm 조기 skip + failure report 기록
- Practice areas nav 오염 필터링
- 200개 전체 firm 재실행
- Baseline 캡처 + regression 테스트

### Must NOT Have (Guardrails)
- ❌ Firm-specific CSS selectors 또는 URL 패턴 하드코딩
- ❌ robots.txt 위반 또는 Cloudflare 우회
- ❌ 외부 디렉토리 추가 (Martindale, Avvo, LinkedIn 등)
- ❌ Industries 필드 변경
- ❌ AttorneyProfile dataclass 필드 정의 변경
- ❌ NLP/ML 기반 필드 추론
- ❌ `_JUNK_PHRASES`에 substring 매칭으로 모호한 단어 추가 (exact match만)
- ❌ `_is_us_location()` US 필터 완전 제거 (완화만, 제거 아님)
- ❌ education, bar_admissions, name 추출 변경
- ❌ run_pipeline.py / discovery.py / enrichment.py 수정 (find_attorney.py 파이프라인만)

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (standalone test scripts)
- **Automated tests**: Tests-after (각 task 완료 후 검증 스크립트 실행)
- **Framework**: standalone Python scripts + pipeline CLI

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Validator changes**: Python script로 test cases 실행 (import validators, call functions, assert)
- **Pipeline changes**: `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{firm}" --limit N` 실행
- **Full run**: JSONL 파싱하여 fill rate 계산

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — baseline + diagnosis):
├── Task 1: Baseline 캡처 [quick] ~10분
└── Task 2: 61개 sitemap-failed firm 근본 원인 진단 [deep] ~20분

Wave 2 (After Wave 1 — validators + enrichment 개선, MAX PARALLEL):
├── Task 3: Practice areas nav 오염 필터링 [unspecified-high] ~15분
├── Task 4: Office validation 완화 + 도시 확장 [unspecified-high] ~20분
├── Task 5: Title validation 완화 [quick] ~10분
├── Task 6: Office/Title HTML 추출 패턴 확장 [deep] ~25분
└── Task 7: practice_department_map.json 확장 [quick] ~10분

Wave 3 (After Wave 2 — pipeline flow 변경):
├── Task 8: BOT_PROTECTED 조기 skip + failure report [deep] ~25분
├── Task 9: Per-firm 403 abort 로직 [unspecified-high] ~15분
└── Task 10: Discovery stub 구현 (T2 진단 결과 반영) [deep] ~30분

Wave 4 (After Wave 3 — 전체 실행):
└── Task 11: 200개 firm 전체 재실행 + 결과 검증 [unspecified-high] ~20-30시간

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay
```

**Critical Path**: T1 → T2 → T10 (discovery) → T11 (full run) → F1-F4
**Parallel Speedup**: ~60% faster than sequential
**Max Concurrent**: 5 (Wave 2)

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T3-T7 (baseline needed for regression) |
| T2 | — | T10 (diagnosis needed for discovery fix) |
| T3 | T1 | T7 (clean practice areas → better dept), T11 |
| T4 | T1 | T11 |
| T5 | T1 | T11 |
| T6 | T1 | T11 |
| T7 | T1, T3 | T11 |
| T8 | T1 | T11 |
| T9 | T1 | T11 |
| T10 | T2 | T11 |
| T11 | T3-T10 | F1-F4 |
| F1-F4 | T11 | (user okay) |

### Agent Dispatch Summary

- **Wave 1**: **2** — T1 → `quick`, T2 → `deep`
- **Wave 2**: **5** — T3 → `unspecified-high`, T4 → `unspecified-high`, T5 → `quick`, T6 → `deep`, T7 → `quick`
- **Wave 3**: **3** — T8 → `deep`, T9 → `unspecified-high`, T10 → `deep`
- **Wave 4**: **1** — T11 → `unspecified-high`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Baseline 캡처 — 개선 전 현재 상태 기록 예상 소요시간: 10분

  **What to do**:
  - 5개 diverse firm으로 baseline 캡처: Kirkland (SITEMAP_XML 성공), Latham (SITEMAP_XML 성공), Simpson Thacher (office/title 문제), Jones Day (BOT_PROTECTED), Fenwick (SPA/title 실패)
  - 각 firm에 대해 `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{firm}" --limit 10` 실행
  - 결과를 `.sisyphus/evidence/task-1-baseline/` 디렉토리에 저장: 각 firm의 field fill rate (title, office, department, practice_areas)
  - 기존 `outputs/attorneys.jsonl` (62,007 records)의 전체 field fill rate도 기록
  - Baseline 결과를 `.sisyphus/evidence/task-1-baseline-summary.json` 에 JSON으로 저장

  **Must NOT do**:
  - 코드 변경 없음 (순수 측정만)
  - 기존 outputs 파일 덮어쓰기 금지 (별도 디렉토리에 저장)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단순 CLI 실행 + 결과 기록, 코드 변경 없음
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `playwright`: 브라우저 불필요, CLI 실행만

  **Parallelization**:
  - **Can Run In Parallel**: YES (T2와 함께)
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Tasks 3, 4, 5, 6, 7 (baseline이 있어야 regression 비교 가능)
  - **Blocked By**: None (즉시 시작)

  **References**:
  **Pattern References**:
  - `find_attorney.py:4900-4989` — CLI arg 파싱 (`_parse_args()` + `main()`), `--debug-firm` 과 `--limit` 사용법
  - `.sisyphus/plans/pipeline-remediation.md:249` — 이전 플랜의 firm 테스트 명령어 패턴

  **API/Type References**:
  - `outputs/attorneys.jsonl` — 현재 62,007 records, 각 라인이 JSON object (fields: full_name, title, offices, department, practice_areas, industries, bar_admissions, education)

  **External References**:
  - `outputs/firm_level_summary.csv` — 200개 firm별 요약 (attorney_count, field fill rates)

  **WHY Each Reference Matters**:
  - CLI args: `--debug-firm`은 firm 이름의 부분 매치, `--limit`는 프로필 수 제한
  - attorneys.jsonl: baseline 전체 fill rate 계산의 source
  - firm_level_summary.csv: per-firm breakdown으로 문제 firm 식별

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Baseline 캡처 성공 확인
    Tool: Bash (python)
    Preconditions: find_attorney.py 실행 가능, outputs/attorneys.jsonl 존재
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 10
      2. Parse JSONL output, count non-empty title/office/dept fields
      3. Save per-firm results to .sisyphus/evidence/task-1-baseline/kirkland.json
      4. Repeat for Latham, Simpson Thacher, Fenwick
      5. For Jones Day (BOT_PROTECTED): just note current behavior (skip or timeout)
      6. Parse full outputs/attorneys.jsonl for overall fill rates
      7. Save summary to .sisyphus/evidence/task-1-baseline-summary.json
    Expected Result: baseline-summary.json contains fill rates for title, office, dept, practice_areas; per-firm JSON files for 4-5 test firms
    Failure Indicators: baseline-summary.json missing or empty; any firm test crashes
    Evidence: .sisyphus/evidence/task-1-baseline-summary.json

  Scenario: Baseline summary has expected structure
    Tool: Bash (python)
    Preconditions: task-1-baseline-summary.json exists
    Steps:
      1. python -c "import json; d=json.load(open('.sisyphus/evidence/task-1-baseline-summary.json')); assert 'overall' in d; assert 'per_firm' in d; assert len(d['per_firm']) >= 4; print('PASS')"
    Expected Result: "PASS" printed
    Failure Indicators: KeyError or AssertionError
    Evidence: .sisyphus/evidence/task-1-baseline-verify.txt
  ```

  **Commit**: NO (측정만, 코드 변경 없음)

---

- [x] 2. 61개 Sitemap-Failed Firm 근본 원인 진단 예상 소요시간: 20분

  **What to do**:
  - `outputs/firm_level_summary.csv`에서 `xml_sitemap` 시도했지만 0건인 61개 firm 목록 추출
  - 그 중 5개 대표 firm을 선택 (다양한 site_structures.json 타입)
  - 각 firm에 대해 `--discover-only --verbose` 로 실행하여 discovery 과정 추적
  - 진단 포인트:
    a. Sitemap URL을 정상적으로 fetch했는가? (HTTP 200?)
    b. Sitemap XML에 attorney/people/professional URL이 있는가?
    c. URL 필터링에서 걸러졌는가? (어떤 패턴에 매치?)
    d. Fallback이 시도되었는가? (dom_exhaustion 등)
    e. Fallback도 실패한 이유는?
  - `site_structures.json`에서 해당 firm들의 structure_type 확인
  - **진단 결과를 `.sisyphus/evidence/task-2-diagnosis.md`에 기록** — T10에서 이 결과를 바탕으로 discovery 수정

  **Must NOT do**:
  - 코드 변경 없음 (진단만)
  - 실패 firm에 과도한 요청 금지 (5개만 샘플링)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 다수 firm의 discovery 과정을 추적하며 근본 원인을 분석하는 심층 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (T1과 함께)
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 10 (진단 결과가 discovery 수정 방향 결정)
  - **Blocked By**: None (즉시 시작)

  **References**:
  **Pattern References**:
  - `find_attorney.py:2710` — `xml_sitemap` strategy (sitemap fetch + URL 추출)
  - `find_attorney.py:2928` — `dom_exhaustion` strategy (fallback)
  - `find_attorney.py:3024-3036` — stub strategies (directory_listing, alphabet_enumeration, filter_enumeration)
  - `find_attorney.py:1758` — `process_firm()` entry point (strategy selection)

  **API/Type References**:
  - `site_structures.json` — firm별 structure_type (SITEMAP_XML, HTML_DIRECTORY_FLAT 등)
  - `outputs/firm_level_summary.csv` — 200개 firm 실행 결과

  **WHY Each Reference Matters**:
  - xml_sitemap strategy: sitemap parsing 로직과 URL 필터링 조건 이해
  - dom_exhaustion: fallback이 왜 실패하는지 (directory path probing, link extraction)
  - stubs: 어떤 전략이 미구현인지 확인
  - site_structures.json: firm 타입별로 다른 discovery 전략 필요

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 진단 보고서 완성도 확인
    Tool: Bash (python)
    Preconditions: 5개 firm 진단 완료
    Steps:
      1. .sisyphus/evidence/task-2-diagnosis.md 파일 읽기
      2. 최소 5개 firm의 진단 결과 포함 확인
      3. 각 firm에 대해: sitemap fetch 결과, URL 필터링 결과, fallback 시도 여부, 실패 근본 원인 기록 확인
      4. "권장 수정 방향" 섹션 존재 확인
    Expected Result: 5개 firm 진단 + 권장 수정 방향이 포함된 체계적 보고서
    Failure Indicators: 진단 보고서 없음, 5개 미만 firm, 근본 원인 미기록
    Evidence: .sisyphus/evidence/task-2-diagnosis.md

  Scenario: 진단 firm이 다양한 structure_type 대표
    Tool: Bash (python)
    Preconditions: task-2-diagnosis.md exists
    Steps:
      1. 진단된 5개 firm의 structure_type 확인
      2. 최소 2개 이상의 서로 다른 structure_type 포함 확인 (SITEMAP_XML + HTML_DIRECTORY_FLAT 등)
    Expected Result: 다양한 타입의 실패 원인이 진단됨
    Failure Indicators: 모든 firm이 같은 structure_type
    Evidence: .sisyphus/evidence/task-2-diagnosis.md
  ```

  **Commit**: NO (진단만, 코드 변경 없음)

---

- [x] 3. Practice Areas 네비게이션 오염 필터링 예상 소요시간: 15분

  **What to do**:
  - `validators.py`의 `_JUNK_PHRASES` frozenset에 nav-specific 항목 추가:
    - 명확한 nav 단어: `"home"`, `"search"`, `"menu"`, `"back to menu"`, `"main menu"`, `"close"`, `"login"`, `"sign in"`, `"sign up"`, `"subscribe"`, `"submit"`, `"contact us"`, `"about us"`, `"careers"`, `"news"`, `"events"`, `"site map"`, `"back"`, `"next"`, `"previous"`, `"print"`, `"share"`, `"email"`, `"offices"`, `"people"`, `"professionals"`
  - **CRITICAL**: `_JUNK_PHRASES` 매칭이 현재 substring인지 exact match인지 확인
    - 만약 substring (`junk in practice.lower()`): **반드시 exact match로 변경** (`practice.lower().strip() in _JUNK_PHRASES`)
    - 이유: "coverage"를 추가하면 "Insurance Coverage"도 차단됨
  - nav 항목 추가 시 모호하지 않은 단어만 (legitimate practice area 이름과 겹치지 않는 것만)
  - 추가 필터: practice area가 순수 숫자, 1-2글자, 또는 URL 형태면 제거

  **Must NOT do**:
  - "coverage", "law", "services" 같은 모호한 단어 추가 금지 (practice area 이름에 포함될 수 있음)
  - `validate_practice_areas()` 전체 로직 변경 금지 (필터만 추가)
  - Industries validation 변경 금지

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: _JUNK_PHRASES 매칭 방식 분석 + 안전한 변경이 필요한 중간 복잡도 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6, 7)
  - **Blocks**: Task 7 (clean practice areas → better department inference), Task 11
  - **Blocked By**: Task 1 (baseline 필요)

  **References**:
  **Pattern References**:
  - `validators.py:156-161` — `_JUNK_PHRASES` frozenset (현재 21개 항목)
  - `validators.py:519` — `validate_practice_areas()` 함수 (필터링 로직)
  - `validators.py:156` — 매칭 방식 확인 필요: `junk in practice.lower()` (substring) vs `practice.lower().strip() in _JUNK_PHRASES` (exact)

  **Test References**:
  - `.sisyphus/evidence/task-1-baseline/` — Simpson Thacher baseline (nav 오염 확인용)

  **WHY Each Reference Matters**:
  - _JUNK_PHRASES: 현재 항목 + 매칭 방식 이해 → 안전한 추가 전략 결정
  - validate_practice_areas: 필터가 어디서 적용되는지, 다른 필터와 상호작용
  - Simpson Thacher baseline: nav 오염의 실제 예시 ("Home", "Back To Menu", "Search")

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Nav 항목이 practice areas에서 필터됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_practice_areas; result, reason = validate_practice_areas(['Corporate Law', 'Home', 'Search', 'Back To Menu', 'Litigation', 'Menu', 'Close']); print(f'Result: {result}'); assert 'Home' not in result; assert 'Search' not in result; assert 'Back To Menu' not in result; assert 'Menu' not in result; assert 'Close' not in result; assert 'Corporate Law' in result; assert 'Litigation' in result; print('PASS')"
    Expected Result: "PASS" — nav 항목 제거, 유효 practice areas 보존
    Failure Indicators: AssertionError (nav 항목이 남아있거나 유효 항목이 제거됨)
    Evidence: .sisyphus/evidence/task-3-nav-filter-test.txt

  Scenario: 정당한 practice areas가 잘못 필터되지 않음
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_practice_areas; result, reason = validate_practice_areas(['Insurance Coverage', 'Energy', 'Environmental Law', 'Healthcare', 'People Analytics']); print(f'Result: {result}'); assert 'Insurance Coverage' in result; assert 'Energy' in result; assert 'Environmental Law' in result; assert 'Healthcare' in result; print('PASS — no false positives')"
    Expected Result: "PASS" — 모든 legitimate practice areas 보존
    Failure Indicators: 유효 practice area가 필터됨
    Evidence: .sisyphus/evidence/task-3-no-false-positive.txt

  Scenario: Simpson Thacher 스타일 오염 데이터 정리 확인
    Tool: Bash (python find_attorney.py)
    Preconditions: validators.py 수정 완료, find_attorney.py 실행 가능
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Simpson Thacher" --limit 5
      2. Parse output JSONL for practice_areas field
      3. Assert no entries match nav words: home, search, menu, back, contact, about, close
    Expected Result: practice_areas에 nav 항목 0건
    Failure Indicators: nav 항목이 여전히 practice_areas에 존재
    Evidence: .sisyphus/evidence/task-3-simpson-thacher.txt
  ```

  **Commit**: YES
  - Message: `feat(validators): add nav item filtering to practice areas validation`
  - Files: `validators.py`
  - Pre-commit: `python -c "from validators import validate_practice_areas; ..."`

---

- [x] 4. Office Validation 완화 + US 도시 목록 확장 예상 소요시간: 20분

  **What to do**:
  - `validators.py`의 `validate_offices()` / `_is_us_location()` 분석 및 수정:
    a. **City, ST 포맷 허용**: `_US_MAJOR_LAW_CITIES` (120개)에 없더라도 `City, STATE_ABBR` 포맷이면 허용 (STATE_ABBR은 50개 주 + DC + PR 약어)
    b. **최소 도시명 길이**: 3자 이상 (1-2글자 garbage 방지)
    c. **UI 단어 필터**: 도시명이 "Click", "View", "Read", "More", "Here", "Back", "Next" 등 UI 단어면 거부
    d. **`_US_MAJOR_LAW_CITIES` 확장**: 현재 120개 → ~200개로 확장 (주요 법률 시장 도시 추가: Bethesda, Dayton, Scranton, Wilmington, Irvine, Pasadena, Boca Raton, Tysons, McLean, Stamford 등)
  - 변경 후 regression 확인: 기존에 정상 추출된 office가 여전히 통과하는지

  **Must NOT do**:
  - `_is_us_location()` US 필터 완전 제거 금지 (완화만)
  - 국제 office (London, Tokyo 등) 허용 금지
  - Firm-specific office 패턴 하드코딩 금지

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: validation 로직 분석 + 도시 목록 확장 + regression 확인이 필요한 중간 복잡도
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 5, 6, 7)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline 필요)

  **References**:
  **Pattern References**:
  - `validators.py:371-455` — `validate_offices()` 전체 함수
  - `validators.py:426-427` — `_US_MAJOR_LAW_CITIES` 체크 (핵심 bottleneck)
  - `validators.py` — `_is_us_location()` 함수 (US 주 이름/코드 매칭)
  - `validators.py` — `_US_MAJOR_LAW_CITIES` frozenset (현재 ~120개)
  - `validators.py` — `_US_STATE_ABBR` 또는 유사 상수 (50개 주 약어)

  **Test References**:
  - `.sisyphus/evidence/task-1-baseline/` — office fill rate baseline

  **WHY Each Reference Matters**:
  - validate_offices: 전체 필터링 체인 이해 (어디서 거부되는지)
  - _US_MAJOR_LAW_CITIES: 현재 포함된 도시 목록 → 누락된 법률 시장 도시 파악
  - _is_us_location: US 주 매칭 로직 → City, ST 포맷 허용 범위 확장

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 비주요 US 도시가 허용됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_offices; result, reason = validate_offices(['Bethesda, MD', 'Dayton, OH', 'Scranton, PA', 'McLean, VA', 'Tysons, VA']); print(f'Result: {result}'); assert len(result) == 5; print('PASS — all valid US cities accepted')"
    Expected Result: 5개 도시 모두 허용
    Failure Indicators: 유효 도시가 거부됨
    Evidence: .sisyphus/evidence/task-4-minor-cities.txt

  Scenario: 기존 주요 도시가 여전히 허용됨 (regression)
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_offices; result, reason = validate_offices(['New York, NY', 'Washington, DC', 'Chicago, IL', 'Los Angeles, CA', 'San Francisco, CA', 'Houston, TX']); print(f'Result: {result}'); assert len(result) == 6; print('PASS — all major cities still accepted')"
    Expected Result: 6개 주요 도시 모두 통과
    Failure Indicators: 기존 통과하던 도시가 거부됨
    Evidence: .sisyphus/evidence/task-4-regression.txt

  Scenario: Garbage 텍스트가 거부됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_offices; result, reason = validate_offices(['Click Here, NY', 'AB', 'View More, CA', '', 'London, UK', 'Tokyo, Japan']); print(f'Result: {result}'); assert 'Click Here, NY' not in result; assert 'View More, CA' not in result; assert 'London, UK' not in result; assert 'Tokyo, Japan' not in result; print('PASS — garbage and international rejected')"
    Expected Result: garbage 및 국제 도시 모두 거부
    Failure Indicators: garbage 텍스트가 허용됨
    Evidence: .sisyphus/evidence/task-4-garbage-reject.txt

  Scenario: 실제 firm에서 office fill rate 개선
    Tool: Bash (python find_attorney.py)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 10
      2. Parse output, count non-empty offices
      3. Compare with baseline from T1
    Expected Result: office fill rate가 baseline 대비 하락하지 않음 (≥ baseline)
    Failure Indicators: office fill rate가 baseline보다 5% 이상 하락
    Evidence: .sisyphus/evidence/task-4-kirkland-test.txt
  ```

  **Commit**: YES
  - Message: `feat(validators): relax office validation to accept City+StateCode format and expand US city list`
  - Files: `validators.py`
  - Pre-commit: `python -c "from validators import validate_offices; ..."`

---

- [x] 5. Title Validation 완화 예상 소요시간: 10분

  **What to do**:
  - `validators.py`의 `validate_title()` 분석 및 수정:
    a. **Firm name contamination 완화**: 현재 title에 firm name이 포함되면 reject → firm name이 title의 50% 이상을 차지할 때만 reject (예: "Partner at Crowell & Moring" → 허용, "Crowell & Moring" 단독 → 거부)
    b. **길이 제한 완화**: 현재 >120자 reject → >200자로 완화 (일부 firm은 긴 title 사용: "Partner, Corporate Department, Co-Chair of M&A Practice Group")
    c. **camelCase 거부 조건 검토**: JavaScript 변수명 같은 것만 거부, 정상 title에 대문자가 포함된 경우는 허용
  - `title_reason="validation_rejected"` 케이스 수정 확인 (Crowell & Moring)

  **Must NOT do**:
  - Title validation 완전 제거 금지 (완화만)
  - Email/phone contamination 필터 제거 금지 (이건 정당한 거부)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단일 함수의 threshold 조정, 간단한 변경
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 6, 7)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline 필요)

  **References**:
  **Pattern References**:
  - `validators.py:286` — `validate_title()` 함수
  - `validators.py` — firm name contamination 체크 로직 (title에 firm name 포함 여부)
  - `validators.py` — length validation (>120 chars rejection)
  - `validators.py` — camelCase detection 로직

  **Test References**:
  - `.sisyphus/evidence/task-1-baseline/` — Crowell & Moring baseline (title validation_rejected)

  **WHY Each Reference Matters**:
  - validate_title: 전체 validation 체인 이해
  - firm name contamination: Crowell & Moring의 실패 원인 직접 해결
  - length/camelCase: 과도한 rejection 식별

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Firm name이 포함된 title이 허용됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_title; title, reason = validate_title('Partner at Crowell & Moring LLP', firm_name='Crowell & Moring'); print(f'Title: {title}, Reason: {reason}'); assert title != '' and reason is None; print('PASS')"
    Expected Result: title이 빈 문자열이 아니고 reason이 None
    Failure Indicators: title이 빈 문자열이거나 reason이 "validation_rejected"
    Evidence: .sisyphus/evidence/task-5-firm-name-title.txt

  Scenario: 순수 firm name만 있는 title은 여전히 거부됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_title; title, reason = validate_title('Crowell & Moring LLP', firm_name='Crowell & Moring'); print(f'Title: {title}, Reason: {reason}'); assert title == '' or reason is not None; print('PASS — pure firm name rejected')"
    Expected Result: 순수 firm name만 있는 title이 거부됨
    Failure Indicators: firm name 단독이 title로 허용됨
    Evidence: .sisyphus/evidence/task-5-pure-firm-reject.txt

  Scenario: 긴 복합 title이 허용됨
    Tool: Bash (python)
    Preconditions: validators.py 수정 완료
    Steps:
      1. python -c "from validators import validate_title; title, reason = validate_title('Partner, Corporate Department, Co-Chair of Mergers and Acquisitions Practice Group and Financial Advisory'); print(f'Title: {title}, Reason: {reason}'); assert title != '' and reason is None; print('PASS — long title accepted')"
    Expected Result: 130+ 글자 title이 허용됨
    Failure Indicators: 길이 초과로 거부됨
    Evidence: .sisyphus/evidence/task-5-long-title.txt
  ```

  **Commit**: YES
  - Message: `feat(validators): relax title validation for firm name contamination and length`
  - Files: `validators.py`
  - Pre-commit: `python -c "from validators import validate_title; ..."`

---

- [x] 6. Office/Title HTML 추출 패턴 확장 예상 소요시간: 25분

  **What to do**:
  - `field_enricher.py`의 `_apply_html_heuristics()` 또는 관련 함수에서 office/title 추출 패턴 확장:

  **Office 추출 패턴 추가**:
  - `<address>` 태그 내 텍스트 → office로 추출
  - `class` 또는 `id`에 `contact`, `address`, `vcard`, `adr` 포함하는 요소
  - `itemprop="address"` 또는 `itemprop="workLocation"` 속성
  - Schema.org `PostalAddress` 내 `addressLocality` + `addressRegion`
  - `<meta property="og:locality">` 또는 유사 메타 태그
  - Heading "Office" 또는 "Location" 아래 텍스트

  **Title 추출 패턴 추가**:
  - `itemprop="jobTitle"` 속성 (microdata)
  - `class`에 `role`, `position`, `rank`, `level` 포함하는 요소
  - `<meta property="og:title">` 에서 title 부분 추출 (보통 "Name - Title - Firm" 포맷)
  - JSON-LD `Person` schema의 `jobTitle` (이미 있을 수 있으나 확인)

  - **CRITICAL**: 새 패턴은 generic 해야 함 (firm-specific selector 금지)
  - 기존 추출 로직의 우선순위를 유지 (JSON-LD > microdata > embedded JSON > HTML heuristics)

  **Must NOT do**:
  - Firm-specific CSS selector 추가 금지 (예: `.bio-card-title` → 이미 있으면 유지, 새로 추가 금지)
  - Enrichment order 변경 금지 (JSON-LD가 항상 최우선)
  - Industries 추출 패턴 변경 금지

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: HTML 파싱 패턴 확장은 기존 코드와 상호작용이 복잡하고 여러 함수를 수정해야 함
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 5, 7)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline 필요)

  **References**:
  **Pattern References**:
  - `field_enricher.py:123` — `enrich()` 메서드 (enrichment pipeline order: JSON-LD → microdata → embedded JSON → HTML heuristics)
  - `field_enricher.py:194` — title enrichment (JSON-LD jobTitle/title)
  - `field_enricher.py:201` — office enrichment (JSON-LD address/workLocation)
  - `field_enricher.py` — `_apply_html_heuristics()` (마지막 수단 HTML 파싱)
  - `find_attorney.py:5040` — `_extract_title_from_html()` (초기 추출)
  - `find_attorney.py:5134` — `_extract_office_from_html()` (초기 추출)

  **API/Type References**:
  - Schema.org `Person` type: `jobTitle`, `worksFor`, `workLocation`, `address`
  - Schema.org `PostalAddress` type: `addressLocality`, `addressRegion`

  **WHY Each Reference Matters**:
  - enrich(): 새 패턴이 기존 우선순위 체인에 올바르게 삽입되어야 함
  - _apply_html_heuristics: HTML 기반 추출의 마지막 단계, 여기에 패턴 추가
  - _extract_title/office_from_html: find_attorney.py의 초기 추출, 여기도 패턴 추가 가능

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Address 태그에서 office 추출
    Tool: Bash (python)
    Preconditions: field_enricher.py 수정 완료
    Steps:
      1. HTML 샘플 생성: "<html><body><address>123 Main St, New York, NY 10001</address></body></html>"
      2. FieldEnricher.enrich() 호출하여 offices 추출
      3. Assert offices에 "New York" 또는 "New York, NY" 포함
    Expected Result: address 태그에서 office 추출 성공
    Failure Indicators: offices가 빈 리스트
    Evidence: .sisyphus/evidence/task-6-address-tag.txt

  Scenario: 실제 firm에서 개선된 office 추출
    Tool: Bash (python find_attorney.py)
    Preconditions: field_enricher.py + validators.py 수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Simpson Thacher" --limit 10
      2. Parse output, count non-empty offices
      3. Compare with T1 baseline
    Expected Result: office fill rate가 baseline 대비 개선 (Simpson Thacher는 기존 90% missing)
    Failure Indicators: office fill rate가 baseline 대비 하락
    Evidence: .sisyphus/evidence/task-6-simpson-office.txt

  Scenario: 기존 JSON-LD 추출이 여전히 우선됨 (regression)
    Tool: Bash (python)
    Preconditions: field_enricher.py 수정 완료
    Steps:
      1. JSON-LD가 있는 HTML 샘플로 enrich() 호출
      2. Assert JSON-LD 값이 HTML heuristics 값보다 우선 사용됨
    Expected Result: JSON-LD 값이 결과에 반영됨
    Failure Indicators: HTML heuristics가 JSON-LD를 덮어씀
    Evidence: .sisyphus/evidence/task-6-jsonld-priority.txt
  ```

  **Commit**: YES
  - Message: `feat(enrichment): expand office and title HTML extraction patterns`
  - Files: `field_enricher.py`, `find_attorney.py` (if initial extraction also updated)
  - Pre-commit: `python -c "from field_enricher import FieldEnricher; ..."`

---

- [x] 7. practice_department_map.json 확장 예상 소요시간: 10분

  **What to do**:
  - `config/practice_department_map.json` 매핑 확장 (현재 24개 → 35+):
  - 추가할 매핑 (explore agent 분석 기반):
    - "Appellate" → "Litigation"
    - "Product Liability" → "Litigation"
    - "Mass Tort" → "Litigation"
    - "Financial Services Regulation" → "Regulatory"
    - "Communications" → "Regulatory"
    - "Telecom" → "Regulatory"
    - "Aviation" → "Regulatory"
    - "Transportation" → "Regulatory"
    - "Construction" → "Real Estate"
    - "Cannabis" → "Regulatory"
    - "Trade Secrets" → "IP"
  - 기존 매핑과 충돌 없는지 확인
  - `infer_department_from_practices()` 함수가 확장된 매핑을 정상 로드하는지 확인

  **Must NOT do**:
  - 기존 24개 매핑 변경 금지 (추가만)
  - `infer_department_from_practices()` 함수 로직 변경 금지 (매핑 데이터만 확장)
  - 모호한 매핑 추가 금지 (예: "Government" → 여러 department 가능)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: JSON 파일에 항목 추가하는 단순 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4, 5, 6)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline), Task 3 (clean practice areas가 있어야 department inference 정확)

  **References**:
  **Pattern References**:
  - `config/practice_department_map.json` — 현재 24개 매핑 (practice area → department)
  - `enrichment.py` — `infer_department_from_practices()` 함수 (매핑 로드 + 적용)
  - `find_attorney.py:4590-4598` — department inference 호출 지점 (T6에서 추가)

  **WHY Each Reference Matters**:
  - practice_department_map.json: 현재 매핑 확인, 충돌 방지
  - infer_department_from_practices: 매핑이 어떻게 적용되는지 (exact match? substring? case-insensitive?)
  - find_attorney.py:4590: 호출 흐름 확인 (practice_areas → department inference)

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 새 매핑이 정상 로드됨
    Tool: Bash (python)
    Preconditions: practice_department_map.json 확장 완료
    Steps:
      1. python -c "import json; m=json.load(open('config/practice_department_map.json')); print(f'Total mappings: {len(m[\"mappings\"])}'); assert len(m['mappings']) >= 35; print('PASS')"
    Expected Result: 35+ 매핑, PASS 출력
    Failure Indicators: 35 미만 또는 JSON 파싱 에러
    Evidence: .sisyphus/evidence/task-7-mapping-count.txt

  Scenario: 새 매핑으로 department 추론 성공
    Tool: Bash (python)
    Preconditions: practice_department_map.json 확장 + enrichment.py 정상
    Steps:
      1. python -c "from enrichment import infer_department_from_practices; dept = infer_department_from_practices(['Appellate Litigation', 'Supreme Court']); print(f'Department: {dept}'); assert dept != ''; print('PASS')"
      2. python -c "from enrichment import infer_department_from_practices; dept = infer_department_from_practices(['Product Liability', 'Mass Tort']); print(f'Department: {dept}'); assert dept != ''; print('PASS')"
    Expected Result: 새 practice areas에서 department 추론 성공
    Failure Indicators: department이 빈 문자열
    Evidence: .sisyphus/evidence/task-7-inference-test.txt
  ```

  **Commit**: YES
  - Message: `feat(config): expand practice-to-department mappings from 24 to 35+`
  - Files: `config/practice_department_map.json`
  - Pre-commit: `python -c "from enrichment import infer_department_from_practices; ..."`

---

- [x] 8. BOT_PROTECTED 조기 Skip + Failure Report 확장 예상 소요시간: 25분

  **What to do**:
  - `find_attorney.py`의 firm 처리 루프 시작부에 BOT_PROTECTED 조기 skip 로직 추가:
    a. `site_structures.json` 로드 (파이프라인 초기화 시 1회)
    b. 각 firm 처리 시작 전: firm의 domain을 site_structures.json에서 검색
    c. `structure_type == "BOT_PROTECTED"` 또는 `is_bot_protected == true` → 즉시 skip
    d. Skip된 firm을 source failure report에 기록 (reason: "BOT_PROTECTED_PRECLASSIFIED")
  - **Runtime 감지 추가**: site_structures.json에 없는 firm에 대해서도 compliance_engine homepage probe 결과 활용
    a. Firm 처리 시작 시 compliance_engine이 이미 homepage을 probe하는지 확인
    b. BLOCKED_BY_BOT 결과면 → skip + failure report 기록
  - **Source Failure Report 확장**:
    a. 현재 failure report 생성 코드 위치 확인 (현재 2 entries만)
    b. BOT_PROTECTED skip된 firm도 report에 포함
    c. Report에 컬럼 추가: Firm, Reason (BOT_PROTECTED/AUTH_REQUIRED/ACCESS_DENIED), Domain, Structure Type, Detection Method (preclassified/runtime)

  **Must NOT do**:
  - Cloudflare/bot protection 우회 시도 금지
  - BOT_PROTECTED firm에 ANY HTTP 요청 금지 (pre-classified인 경우)
  - site_structures.json 수정 금지 (읽기 전용)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: pipeline flow 변경, 여러 코드 경로에 걸쳐 있는 복잡한 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (T9, T10과 다른 코드 영역)
  - **Parallel Group**: Wave 3 (with Tasks 9, 10)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline)

  **References**:
  **Pattern References**:
  - `find_attorney.py:1758` — `process_firm()` entry point (skip 로직 삽입 위치)
  - `find_attorney.py:1310` — firm loop (`try:` 블록 시작, 여기 전에 skip 체크)
  - `find_attorney.py:5295-5304` — HTTP status 처리 (현재 per-profile 403 처리)
  - `compliance_engine.py:264-280` — homepage probe (BOT_WALL_PATTERNS, AUTH_WALL_PATTERNS)
  - `compliance_engine.py:75` — `BOT_WALL_PATTERNS` (regex patterns for "access denied" etc.)

  **API/Type References**:
  - `site_structures.json` — `structure_type: "BOT_PROTECTED"`, `is_bot_protected: true` fields
  - Source failure report Excel — 현재 2 entries (Troutman, Winstead)

  **WHY Each Reference Matters**:
  - process_firm: skip 로직을 삽입할 정확한 위치
  - firm loop: skip 후 다음 firm으로 continue
  - compliance_engine: 이미 존재하는 bot detection 로직 재사용
  - site_structures.json: pre-classified BOT_PROTECTED 정보

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: BOT_PROTECTED firm이 즉시 skip됨
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py 수정 완료, site_structures.json에 BOT_PROTECTED firm 존재
    Steps:
      1. $start = Get-Date
      2. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Jones Day" --limit 5 2>&1 | Select-String -Pattern "SKIP|BOT_PROTECTED|skip"
      3. $elapsed = (Get-Date) - $start
      4. Assert $elapsed.TotalSeconds < 10
      5. Assert output contains "BOT_PROTECTED" or "skip" message
    Expected Result: Jones Day가 10초 이내 skip됨, BOT_PROTECTED 관련 로그 출력
    Failure Indicators: 10초 이상 걸림 (HTTP 요청 시도), skip 메시지 없음
    Evidence: .sisyphus/evidence/task-8-bot-skip.txt

  Scenario: Failure report에 BOT_PROTECTED firm 기록됨
    Tool: Bash (python)
    Preconditions: BOT_PROTECTED firm skip 후
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Jones Day" --limit 5
      2. Failure report 파일 확인 (Excel 또는 JSON)
      3. Assert "Jones Day" entry with reason "BOT_PROTECTED"
    Expected Result: failure report에 Jones Day 기록됨
    Failure Indicators: failure report에 Jones Day 없음 또는 reason 누락
    Evidence: .sisyphus/evidence/task-8-failure-report.txt

  Scenario: 정상 firm은 skip되지 않음
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py 수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 3
      2. Assert output shows discovery/enrichment proceeding normally
      3. Assert ≥1 attorney record extracted
    Expected Result: Kirkland이 정상 처리됨
    Failure Indicators: Kirkland이 skip됨 또는 0건 추출
    Evidence: .sisyphus/evidence/task-8-normal-firm.txt
  ```

  **Commit**: YES
  - Message: `feat(pipeline): add BOT_PROTECTED early skip and extend failure report`
  - Files: `find_attorney.py`
  - Pre-commit: `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 3`

---

- [x] 9. Per-Firm 403 Abort 로직 예상 소요시간: 15분

  **What to do**:
  - `find_attorney.py`의 enrichment 단계에서 per-firm 403 abort 로직 추가:
    a. 각 firm의 프로필 enrichment 시 연속 403 카운터 유지
    b. **첫 N개 프로필 (N=5) 중 100%가 403** → firm 전체 abort
    c. 또는 **전체 프로필의 80%가 403** → firm abort
    d. Abort 시: firm을 failure report에 기록 (reason: "ACCESS_DENIED_RUNTIME"), 남은 프로필 skip
    e. White & Case 같은 케이스 방지: 2,814개를 일일이 시도하는 대신 첫 5개에서 abort
  - "Access Denied" 문자열이 HTML body에 포함된 경우도 403과 동일하게 카운트
    - `compliance_engine.py:75`의 `BOT_WALL_PATTERNS` 재사용

  **Must NOT do**:
  - 일시적 403 (rate limiting)과 영구 403 (bot protection) 혼동 금지
    - Rate limiting 403: Retry-After 헤더 있음, 간헐적 → abort하면 안 됨
    - Bot protection 403: 모든 요청에 일관적 → abort 대상
  - 단일 403에 abort 금지 (최소 5개 연속이어야)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: enrichment flow에 조건부 abort 삽입, 중간 복잡도
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (T8과 다른 코드 영역: T8은 discovery 전, T9는 enrichment 중)
  - **Parallel Group**: Wave 3 (with Tasks 8, 10)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (baseline)

  **References**:
  **Pattern References**:
  - `find_attorney.py:5269` — `_enrich_single_profile()` (per-profile enrichment)
  - `find_attorney.py:5295-5304` — HTTP status 처리 (403 감지 지점)
  - `find_attorney.py:4050-4080` — `_run_batch_shared_browser()` (batch enrichment with 8 threads)
  - `compliance_engine.py:75` — `BOT_WALL_PATTERNS` ("access denied" 등)

  **WHY Each Reference Matters**:
  - _enrich_single_profile: 403 카운터를 여기서 업데이트
  - HTTP status 처리: 이미 403 감지하는 코드가 있으므로 확장
  - _run_batch_shared_browser: multi-thread enrichment에서 abort signal 전파 필요
  - BOT_WALL_PATTERNS: "Access Denied" HTML 감지에 재사용

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 연속 403 firm이 조기 abort됨
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py 수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "White & Case" --limit 20
      2. Count total HTTP requests made (from verbose/debug output)
      3. Assert total requests ≤ 10 (should abort after ~5 consecutive 403s)
      4. Assert output contains "abort" or "ACCESS_DENIED" message
    Expected Result: White & Case가 10개 이내 요청 후 abort (이전 2,814개 대비 99.6% 감소)
    Failure Indicators: 20개 이상 요청 시도, abort 메시지 없음
    Evidence: .sisyphus/evidence/task-9-403-abort.txt

  Scenario: 정상 firm은 abort되지 않음
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py 수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Latham" --limit 10
      2. Assert output shows normal enrichment (no abort)
      3. Assert ≥5 attorney records extracted
    Expected Result: Latham이 정상 처리됨
    Failure Indicators: Latham이 abort됨 또는 0건 추출
    Evidence: .sisyphus/evidence/task-9-normal-firm.txt
  ```

  **Commit**: YES
  - Message: `feat(pipeline): add per-firm 403 abort to prevent wasting time on access-denied sites`
  - Files: `find_attorney.py`
  - Pre-commit: `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Latham" --limit 5`

---

- [x] 10. Discovery Stub 구현 (T2 진단 결과 반영) 예상 소요시간: 30분

  **What to do**:
  - **T2 진단 결과를 먼저 읽고** (`.sisyphus/evidence/task-2-diagnosis.md`), 근본 원인에 맞는 수정 적용
  - `find_attorney.py`의 stub strategies를 실제 구현으로 교체:

  **`directory_listing` (line 3024) 구현**:
  - 현재: dom_exhaustion으로 단순 위임
  - 변경: HTML_DIRECTORY_FLAT firm을 위한 정적 HTML 파싱
    a. `/people`, `/attorneys`, `/professionals`, `/lawyers`, `/our-team`, `/team` 등 directory path probe
    b. 각 path에서 HTML 가져오기 (requests, NOT Playwright)
    c. BeautifulSoup으로 `<a>` 태그 추출 → attorney profile URL 패턴 매칭
    d. Profile URL 패턴: `/people/`, `/attorneys/`, `/professionals/`, `/lawyer/`, `/bio/`, `/profile/` 등
    e. Pagination 링크 감지 시 모든 page 순회

  **`alphabet_enumeration` (line 3031) 구현**:
  - 현재: dom_exhaustion으로 단순 위임
  - 변경: HTML_ALPHA_PAGINATED firm을 위한 A-Z 페이지 순회
    a. `/people?letter=A`, `/attorneys/a`, `/professionals?last_name=A` 등 패턴
    b. 26개 알파벳 (A-Z) 각각에 대해 directory page fetch
    c. 각 page에서 attorney profile URL 추출 (directory_listing과 동일한 link 추출 로직)
    d. Rate limiting 준수 (RATE_LIMIT_DELAY 적용)

  **xml_sitemap fallback 강화**:
  - xml_sitemap이 0건 반환 시: directory_listing → alphabet_enumeration 순서로 fallback
  - select_strategies()에서 fallback chain 설정

  **CRITICAL**: T2 진단에서 발견된 구체적 실패 원인을 우선 해결

  **Must NOT do**:
  - Firm-specific URL 패턴 하드코딩 금지 (generic path + link pattern만)
  - robots.txt Disallow path 접근 금지
  - Rate limiting 무시 금지 (alphabet_enumeration은 26페이지 순회하므로 특히 중요)
  - Playwright 사용 금지 (정적 HTML 파싱만 — SPA firm은 별도)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 가장 복잡한 task — 두 개 strategy 구현 + fallback chain + rate limiting 준수
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (T8, T9와 다른 코드 영역)
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: Task 11
  - **Blocked By**: Task 2 (진단 결과 필수)

  **References**:
  **Pattern References**:
  - `find_attorney.py:3024` — `directory_listing` stub (현재 dom_exhaustion 위임)
  - `find_attorney.py:3031` — `alphabet_enumeration` stub (현재 dom_exhaustion 위임)
  - `find_attorney.py:2928` — `dom_exhaustion` strategy (참고용: pagination/scroll 로직)
  - `find_attorney.py:2710` — `xml_sitemap` strategy (참고용: sitemap fetch + URL 필터)
  - `find_attorney.py:1758` — `process_firm()` + `select_strategies()` (strategy 선택 + fallback chain)
  - `.sisyphus/evidence/task-2-diagnosis.md` — 61개 sitemap-failed firm 근본 원인 진단 결과

  **API/Type References**:
  - `site_structures.json` — firm별 structure_type (어떤 strategy가 적합한지)
  - `rate_limit_manager.py` — rate limiting 관련 (per-domain delay)
  - `compliance_engine.py` — robots.txt 체크 (새 path probe 전 확인)

  **WHY Each Reference Matters**:
  - stubs: 교체할 정확한 코드 위치
  - dom_exhaustion: 기존 pagination 로직 참고 (중복 구현 방지)
  - xml_sitemap: URL 필터링 패턴 참고
  - process_firm/select_strategies: fallback chain 설정 위치
  - task-2-diagnosis.md: 구체적 실패 원인 → 정확한 수정 방향

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: directory_listing으로 HTML_DIRECTORY_FLAT firm에서 URL 발견
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py stub 구현 완료
    Steps:
      1. T2 진단에서 식별된 HTML_DIRECTORY_FLAT firm 선택
      2. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{firm}" --limit 10 --verbose
      3. Assert output shows "directory_listing" strategy used
      4. Assert ≥1 attorney URL discovered
    Expected Result: 이전에 0건이던 firm에서 attorney URL 발견
    Failure Indicators: 여전히 0건, directory_listing strategy 미사용
    Evidence: .sisyphus/evidence/task-10-directory-listing.txt

  Scenario: alphabet_enumeration으로 HTML_ALPHA_PAGINATED firm에서 URL 발견
    Tool: Bash (python find_attorney.py)
    Preconditions: find_attorney.py stub 구현 완료
    Steps:
      1. T2 진단에서 식별된 HTML_ALPHA_PAGINATED firm 선택
      2. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{firm}" --limit 10 --verbose
      3. Assert output shows "alphabet_enumeration" strategy used
      4. Assert ≥1 attorney URL discovered
    Expected Result: 이전에 0건이던 firm에서 attorney URL 발견
    Failure Indicators: 여전히 0건
    Evidence: .sisyphus/evidence/task-10-alphabet-enum.txt

  Scenario: xml_sitemap 실패 시 fallback chain 작동
    Tool: Bash (python find_attorney.py)
    Preconditions: fallback chain 설정 완료
    Steps:
      1. xml_sitemap이 0건인 firm 선택
      2. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{firm}" --limit 10 --verbose
      3. Assert output shows xml_sitemap tried → 0 → fallback to directory_listing or alphabet_enumeration
    Expected Result: xml_sitemap 실패 후 자동 fallback
    Failure Indicators: fallback 없이 0건으로 종료
    Evidence: .sisyphus/evidence/task-10-fallback-chain.txt

  Scenario: Rate limiting 준수 확인
    Tool: Bash (python)
    Preconditions: alphabet_enumeration 구현 완료
    Steps:
      1. Run alphabet_enumeration for 1 firm with --verbose
      2. Parse timestamps from debug output
      3. Assert inter-request delay ≥ 0.5s
    Expected Result: 모든 요청 간 ≥ 0.5s delay
    Failure Indicators: 연속 요청이 0.5s 미만 간격
    Evidence: .sisyphus/evidence/task-10-rate-limit.txt
  ```

  **Commit**: YES
  - Message: `feat(discovery): implement directory_listing and alphabet_enumeration strategies with fallback chain`
  - Files: `find_attorney.py`
  - Pre-commit: `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "{test_firm}" --limit 5`

---

- [ ] 11. 200개 Firm 전체 재실행 + 결과 검증 예상 소요시간: 20-30시간

  **What to do**:
  - T3-T10 모든 변경사항 적용 확인 후 200개 firm 전체 실행:
    ```bash
    python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs
    ```
  - 실행 전: 기존 outputs/ 백업 (`outputs/attorneys_pre_v2.jsonl` 등)
  - 실행 중: 진행 상황 모니터링 (firm count, error rate)
  - 실행 후 결과 검증:
    a. 전체 fill rate 계산 (title, office, department)
    b. Per-firm 비교: 72개 기존 firm의 fill rate regression 체크
    c. 새로 성공한 firm 수 확인 (목표: ≥ 140개)
    d. BOT_PROTECTED firm skip 확인 (failure report에 26개 기록)
    e. Practice areas nav 오염 0건 확인
    f. T1 baseline과 비교하여 개선 폭 기록
  - 결과를 `.sisyphus/evidence/task-11-results-summary.json`에 저장

  **Must NOT do**:
  - 기존 outputs 파일 덮어쓰기 전 백업 필수
  - 실행 중 코드 변경 금지
  - 실행이 stuck되면 SIGINT (Ctrl+C) → graceful shutdown → 로그 확인

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 장시간 파이프라인 실행 + 결과 분석
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (전체 재실행은 단독)
  - **Parallel Group**: Wave 4 (단독)
  - **Blocks**: F1-F4 (Final Verification)
  - **Blocked By**: Tasks 3, 4, 5, 6, 7, 8, 9, 10 (모든 개선 사항)

  **References**:
  **Pattern References**:
  - `find_attorney.py:4900-4989` — CLI args + main() (전체 실행 명령)
  - `.sisyphus/evidence/task-1-baseline-summary.json` — baseline 비교용

  **API/Type References**:
  - `outputs/attorneys.jsonl` — 출력 JSONL
  - `outputs/firm_level_summary.csv` — firm별 요약
  - `outputs/coverage_metrics.json` — 커버리지 메트릭

  **WHY Each Reference Matters**:
  - CLI args: 정확한 실행 명령
  - baseline: 개선 전후 비교

  **Acceptance Criteria**:
  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Fill rate 목표 달성
    Tool: Bash (python)
    Preconditions: 200개 firm 전체 실행 완료
    Steps:
      1. Parse outputs/attorneys.jsonl
      2. Calculate fill rates for title, office, department
      3. Assert title ≥ 90%, office ≥ 60%, department ≥ 85%
    Expected Result: 모든 fill rate 목표 달성
    Failure Indicators: 하나라도 목표 미달
    Evidence: .sisyphus/evidence/task-11-fill-rates.json

  Scenario: Firm 수 목표 달성
    Tool: Bash (python)
    Preconditions: 200개 firm 전체 실행 완료
    Steps:
      1. Parse outputs/attorneys.jsonl, count distinct firms with ≥1 attorney
      2. Assert ≥ 140 firms
    Expected Result: 140+ firms with data
    Failure Indicators: 140 미만
    Evidence: .sisyphus/evidence/task-11-firm-count.txt

  Scenario: BOT_PROTECTED firms skip 확인
    Tool: Bash (python)
    Preconditions: 200개 firm 전체 실행 완료
    Steps:
      1. Failure report 확인
      2. Assert BOT_PROTECTED firm 수 ≥ 20
      3. Assert 이들 firm에 대한 attorney records 0건
    Expected Result: BOT_PROTECTED firms가 failure report에 기록되고 0건 추출
    Failure Indicators: BOT_PROTECTED firm에서 "Access Denied" records 생성
    Evidence: .sisyphus/evidence/task-11-bot-skip.txt

  Scenario: No regression on existing 72 firms
    Tool: Bash (python)
    Preconditions: 200개 firm 전체 실행 완료 + T1 baseline 존재
    Steps:
      1. T1 baseline의 5개 firm fill rates 로드
      2. 새 실행의 동일 5개 firm fill rates 계산
      3. Assert 각 firm의 각 field fill rate가 baseline 대비 5% 이상 하락하지 않음
    Expected Result: regression 없음
    Failure Indicators: 어떤 firm의 어떤 field가 5% 이상 하락
    Evidence: .sisyphus/evidence/task-11-regression-check.json

  Scenario: Nav pollution 0건
    Tool: Bash (python)
    Preconditions: 200개 firm 전체 실행 완료
    Steps:
      1. Parse all practice_areas from attorneys.jsonl
      2. Check against nav word set: home, search, menu, back, contact, about, close, login, back to menu, main menu
      3. Assert count == 0
    Expected Result: practice_areas에 nav 항목 0건
    Failure Indicators: 1건 이상 nav 항목 발견
    Evidence: .sisyphus/evidence/task-11-nav-pollution.txt
  ```

  **Commit**: YES
  - Message: `run: execute full 200-firm pipeline with quality improvements`
  - Files: `outputs/*`
  - Pre-commit: (none — runtime output)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run linter checks on changed files. Review all changed files for: `as any`/`@ts-ignore` (N/A Python), empty catches, bare print() in library modules, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify no SyntaxWarning from regex (raw strings).
  Output: `Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Run `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 10`. Verify all 8 fields populated. Run for 1 previously-failed firm. Run for 1 BOT_PROTECTED firm (should skip instantly). Parse JSONL output for nav pollution in practice_areas. Compare fill rates vs T1 baseline.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify everything in spec was built. Check "Must NOT do" compliance: no firm-specific selectors, no industries changes, no external directories, no run_pipeline.py changes. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Commit | Type | Files | Pre-commit |
|--------|------|-------|------------|
| 1 | baseline | test scripts | python verify script |
| 2 | feat(validators) | validators.py | python test_practice_filter.py |
| 3 | feat(validators) | validators.py | python test_office_validation.py |
| 4 | feat(validators) | validators.py | python test_title_validation.py |
| 5 | feat(enrichment) | field_enricher.py | python test_enrichment.py |
| 6 | feat(config) | practice_department_map.json | python test_dept_mapping.py |
| 7 | feat(pipeline) | find_attorney.py | python test_bot_skip.py |
| 8 | feat(pipeline) | find_attorney.py | python test_403_abort.py |
| 9 | feat(discovery) | find_attorney.py | python test_discovery.py |
| 10 | run | outputs/* | (full pipeline run) |

---

## Success Criteria

### Verification Commands
```bash
# Title fill rate
python -c "import json; lines=[json.loads(l) for l in open('outputs/attorneys.jsonl')]; filled=sum(1 for l in lines if l.get('title','')); print(f'Title: {filled}/{len(lines)} = {filled/len(lines)*100:.1f}%')"
# Expected: ≥ 90%

# Office fill rate
python -c "import json; lines=[json.loads(l) for l in open('outputs/attorneys.jsonl')]; filled=sum(1 for l in lines if l.get('offices',[])); print(f'Office: {filled}/{len(lines)} = {filled/len(lines)*100:.1f}%')"
# Expected: ≥ 60%

# Department fill rate
python -c "import json; lines=[json.loads(l) for l in open('outputs/attorneys.jsonl')]; filled=sum(1 for l in lines if l.get('department','')); print(f'Dept: {filled}/{len(lines)} = {filled/len(lines)*100:.1f}%')"
# Expected: ≥ 85%

# Firm count
python -c "import json; firms=set(json.loads(l).get('firm','') for l in open('outputs/attorneys.jsonl')); print(f'Firms: {len(firms)}')"
# Expected: ≥ 140

# Nav pollution check
python -c "import json; nav={'home','search','menu','back','contact','about','close','login','back to menu','main menu'}; count=0; [count:=count+1 for l in open('outputs/attorneys.jsonl') for pa in json.loads(l).get('practice_areas',[]) if pa.lower().strip() in nav]; print(f'Nav pollution: {count}')"
# Expected: 0
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] Title ≥ 90%, Office ≥ 60%, Department ≥ 85%
- [ ] ≥ 140 firms with data
- [ ] BOT_PROTECTED firms in failure report
- [ ] No nav items in practice_areas
- [ ] No regression in existing 72 firms
