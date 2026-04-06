# Attorney Extraction Parser Fix — All Fields, All Firms

## TL;DR

> **Quick Summary**: Fix field extraction bugs in the attorney scraping pipeline so that `office`, `department`, `practice_areas`, and other fields are correctly populated across all 176 parseable AmLaw200 firms. Core issues: section boundary content bleed in `_collect_content_after`, bio text leaking into practice_areas, department never extracted, and Latham (SPA_OTHER) h2/h3 sub-section hierarchy not recognized.
>
> **Deliverables**:
> - `parser_sections.py` — fixed section boundary traversal + improved department synonyms
> - `validators.py` — bio-sentence detection added to `validate_practice_areas`
> - `enrichment.py` — department extraction from CSS + title split; Latham h3 sub-section recognition; `"profile"` synonym protection
> - `tests/test_parser_sections.py` — new regression test cases (TDD first)
> - `tests/test_enrichment_integration.py` — new integration fixture for Latham structure
>
> **Estimated Effort**: Medium (2–3 hours)
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: Task 1 (tests/RED) → Task 2 (section boundary) → Task 3 (bio leak) → Task 4 (department) → Task 5 (Latham) → Task 6 (integration sweep)

---

## Context

### Original Request
"어디까지 했어? 이제 다 된건가? 테스트 했을때 아직 office, department, location이 잘 안됫던거 같은데"
→ office, department, location 전체 펌에 걸쳐 올바르게 나오게 해달라. 모든 필드, 모든 펌.

### Interview Summary
**Key Discussions**:
- Kirkland (SITEMAP_XML): name/title/offices/practice_areas/bar_admissions/education 정상. department만 빈 배열.
- Latham (SPA_OTHER): full_name에 practice group name, title=None, offices=[], practice_areas에 Chambers 수상텍스트/날짜 → 전체 프로필 파싱 실패
- Jones Day (HTML_DIRECTORY_FLAT, 403): offices에 bar admission 텍스트 혼입
- Paul Weiss (SITEMAP_XML): practice_areas에 biography 문단 텍스트 혼입
- Department 접근: 가능한 모든 소스(CSS class, JSON-LD, title split, section heading)에서 추출. 없으면 []

**Research Findings**:
- Latham HTML 구조 실제 확인: `<h1>Name</h1>` → `<h2>Profile</h2>`, `<h2>Experience</h2>`, `<h2>Qualifications</h2>` → `<h3>Bar Qualification</h3>`, `<h3>Education</h3>`, `<h3>Practices</h3>`, `<h2>Recognition</h2>` — title/office는 JSON-LD 없음, CSS 블록에 위치
- Jones Day: 403 응답 → BOT_PROTECTED로 재분류 필요. offices 텍스트 bleed는 Kirkland 등 다른 SITEMAP_XML 펌에서도 발생
- `find_all_next()` 가 sections간 content bleed의 근본 원인
- `"profile"` synonym이 biography에 있어 Latham의 `<h2>Profile</h2>` 전체가 biography 버킷으로 들어가고 이후 content sweep에서 모든 데이터가 biography로 오분류됨

### Metis Review
**Identified Gaps (addressed)**:
- Latham JSON-LD 없음 확인 필요 → 확인됨(no JSON-LD), CSS 기반 추출 필요
- Jones Day 403 → BOT_PROTECTED 재분류 + offices bleed는 다른 firma에서의 문제로 계속 수정
- `"profile"` synonym collision → Option B: qualified synonym으로 변경
- department는 section heading으로는 대부분 없음 → CSS class generic pattern + title split 필요
- `find_all_next()` 근본 설계 한계 → sibling-first 탐색으로 수정
- bio text가 practice_areas로 유입 → sentence-pattern detection 추가

---

## Work Objectives

### Core Objective
`_collect_content_after` 섹션 경계 bleed를 수정하고, biography/practice_areas 분리를 강화하며, department 추출 경로를 다양화하고, Latham h3 sub-section 구조를 인식하게 하여 모든 구조 타입에서 올바른 필드 추출을 달성한다.

### Concrete Deliverables
- `parser_sections.py` — 수정된 `_collect_content_after` + `"profile"` qualified synonym
- `validators.py` — `validate_practice_areas`에 bio-sentence 필터 추가
- `enrichment.py` — generic CSS department extraction + title split department + Latham h3 sub-section recognition
- `tests/test_parser_sections.py` — 신규 경계 테스트 케이스 (TDD-RED)
- `tests/test_enrichment_integration.py` — Latham SPA_OTHER 픽스처 테스트

### Definition of Done
- [ ] `python tests/test_parser_sections.py` → 0 failures (모든 신규 + 기존 테스트 통과)
- [ ] `python tests/test_enrichment_integration.py` → 0 failures
- [ ] `python run_pipeline.py --firms "kirkland" --max-profiles 5` → offices 비어있지 않음, practice_areas에 bio 텍스트 없음
- [ ] `python run_pipeline.py --firms "paul weiss" --max-profiles 5` → practice_areas에 bio 문단 없음
- [ ] `python run_pipeline.py --firms "latham" --max-profiles 5` → full_name이 실제 사람 이름, practices 추출됨
- [ ] `python -c "from parser_sections import normalize_section_title; assert normalize_section_title('Litigation Group') == 'departments'"` → exit 0
- [ ] `python -c "from parser_sections import normalize_section_title; assert normalize_section_title('Working Group') != 'departments'"` → exit 0

### Must Have
- 기존 Kirkland 추출 회귀 없음 (golden reference)
- `tests/test_parser_sections.py` BOUNDARY_CASE: h3 content under h2 계속 수집되어야 함
- `Working Group` → departments 매핑 안 되어야 함 (기존 adversarial test 유지)
- 모든 수정은 generic pattern (firm-specific if/elif 금지)

### Must NOT Have (Guardrails)
- 특정 펌 이름 hard-code (예: `if "lw.com" in url`) — 기존 weil.com 예외 외 추가 금지
- `find_attorney.py` 수정 — 범위 외
- `discovery.py` / `run_pipeline.py` 수정 — 범위 외
- department를 practice_areas에서 추론하는 로직 (예: M&A → Corporate) — feature 아닌 bug fix
- `_MAX_BLOCK_LEN = 400` 변경
- `PIPELINE_NO_PLAYWRIGHT=1` 우회
- Playwright fallback path 신규 추가
- 5-stage extraction cascade 순서 변경

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES — `tests/test_parser_sections.py`, `tests/test_enrichment_integration.py`
- **Automated tests**: TDD — RED first, then GREEN
- **Framework**: standalone script (`python tests/test_X.py`)
- **TDD approach**: 각 태스크 = RED (failing test 추가) → GREEN (최소 구현) → REFACTOR

### QA Policy
모든 태스크는 agent-executed QA scenarios 포함 (아래 각 TODO 참조).
Evidence는 `.sisyphus/evidence/task-{N}-{slug}.txt`에 저장.

- **Python module/parser**: Bash (`python -c` or standalone test script)
- **Pipeline end-to-end**: Bash (`python run_pipeline.py --firms X --max-profiles 3`)
- **Acceptance assertions**: Bash (`python -c "assert ..."`)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (동시 시작 가능):
├── Task 1: 신규 TDD 회귀 테스트 케이스 작성 [RED] (quick)
└── Task 2: find_all_next() section boundary bleed 수정 (unspecified-high)

Wave 2 (Task 1+2 완료 후):
├── Task 3: validate_practice_areas bio-sentence 필터 (quick)
├── Task 4: department 추출 — CSS generic + title split (unspecified-high)
└── Task 5: Latham h3 sub-section + "profile" synonym fix (unspecified-high)

Wave FINAL (전체 완료 후):
└── Task 6: 전체 통합 테스트 sweep — 구조 타입별 5 firm 샘플 (deep)
```

**Dependency Matrix**:
- Task 1: 없음 → Task 2, 3, 4, 5 (모두 블록)
- Task 2: Task 1 → Task 6
- Task 3: Task 1 → Task 6
- Task 4: Task 1 → Task 6
- Task 5: Task 1 → Task 6
- Task 6: Tasks 2, 3, 4, 5 모두

**Critical Path**: Task 1 → (Task 2 || Task 3 || Task 4 || Task 5) → Task 6

---

## TODOs

---

- [x] 1. 신규 TDD 회귀 테스트 케이스 작성 (RED phase)

  **What to do**:
  - `tests/test_parser_sections.py`에 다음 신규 테스트 케이스 추가:
    1. **`BOUNDARY_OFFICES_BAR`**: `<h2>Office</h2><p>New York</p><h2>Bar Admissions</h2><p>New York Bar</p>` → offices section에 "New York Bar" 포함 안 됨
    2. **`BOUNDARY_PRACTICE_BIO`**: `<h2>Practice Areas</h2><li>M&A</li><h2>Biography</h2><p>Jane advises clients on complex matters.</p>` → practice_areas에 bio 문장 포함 안 됨
    3. **`LATHAM_H3_UNDER_H2`**: `<h2>Qualifications</h2><h3>Education</h3><p>Harvard Law School</p><h3>Practices</h3><li>Antitrust</li>` → parse_sections 결과에서 "Antitrust"는 `practice_areas`에, "Harvard Law School"는 `education`에 있어야 함
    4. **`DEPT_LITIGATION_GROUP`**: `normalize_section_title("Litigation Group")` → `"departments"`
    5. **`DEPT_WORKING_GROUP_NEGATIVE`**: `normalize_section_title("Working Group")` → NOT `"departments"` (기존 테스트 재확인)
    6. **`DEPT_PRACTICE_GROUP`**: `normalize_section_title("Practice Group")` → `"departments"`
  - `tests/test_enrichment_integration.py`에 추가:
    7. **`LATHAM_SPA_OTHER_FIXTURE`**: Latham 스타일 HTML fixture로 `ProfileEnricher.enrich()` 호출 → `practice_areas` 비어있지 않고, `full_name`이 실제 이름, `offices` 비어있지 않음

  **Must NOT do**:
  - 테스트 통과를 위한 production 코드 수정 (이 태스크는 RED phase만)
  - Playwright 관련 테스트 추가

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 테스트 케이스 작성만, 기존 패턴 복사하여 추가
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1, Task 2와 동시)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 2, 3, 4, 5, 6
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `tests/test_parser_sections.py` 전체 — 기존 `NormalizeCase`, `ParseCase` 패턴 사용. `@dataclass(frozen=True)` 구조 유지
  - `tests/test_enrichment_integration.py` 전체 — `ProfileEnricher` 픽스처 패턴

  **API/Type References**:
  - `parser_sections.parse_sections(html: str) -> dict[str, list[str]]`
  - `parser_sections.normalize_section_title(raw: str) -> str`
  - `enrichment.ProfileEnricher.enrich(url, html, firm) -> AttorneyProfile`

  **WHY Each Reference Matters**:
  - 기존 테스트 패턴 (`NormalizeCase`, `ParseCase`)을 정확히 따라야 기존 test runner가 새 케이스도 자동으로 실행함
  - `test_enrichment_integration.py`의 픽스처 HTML 작성 시, Latham 실제 구조(`<h2>Profile</h2>`, `<h2>Qualifications</h2>` → `<h3>Practices</h3>`) 정확히 재현해야 함

  **Acceptance Criteria**:

  - [ ] 6개 신규 `NormalizeCase`/`ParseCase` 항목이 `test_parser_sections.py`에 추가됨
  - [ ] `python tests/test_parser_sections.py` 실행 시 신규 테스트들은 **FAIL** (RED 확인)
  - [ ] 1개 신규 픽스처 테스트가 `test_enrichment_integration.py`에 추가됨

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: RED phase 확인 — 새 boundary 테스트가 실패함
    Tool: Bash
    Preconditions: test 파일 수정 완료, production 코드는 미수정
    Steps:
      1. python tests/test_parser_sections.py 2>&1 | Select-Object -Last 20
      2. BOUNDARY_OFFICES_BAR, BOUNDARY_PRACTICE_BIO, LATHAM_H3_UNDER_H2 케이스가 FAIL로 나타나야 함
    Expected Result: 최소 3개 신규 케이스 FAIL (RED 증명)
    Evidence: .sisyphus/evidence/task-1-red-confirm.txt

  Scenario: 기존 테스트 회귀 없음
    Tool: Bash
    Steps:
      1. python tests/test_parser_sections.py 2>&1 | Select-Object -Last 10
      2. 기존 BOUNDARY_CASE (h3 under h2) 등 기존 케이스는 여전히 PASS
    Expected Result: 기존 케이스는 모두 PASS
    Evidence: .sisyphus/evidence/task-1-existing-pass.txt
  ```

  **Commit**: YES (그룹 1)
  - Message: `test: add RED regression tests for section boundary, bio leak, department, Latham structure`
  - Files: `tests/test_parser_sections.py`, `tests/test_enrichment_integration.py`
  - Pre-commit: `python tests/test_parser_sections.py` (일부 FAIL 예상 — RED phase)

---

- [x] 2. `_collect_content_after` section boundary bleed 수정

  **What to do**:
  - `parser_sections.py` `_collect_content_after()` (lines 220-286) 수정:
    - `find_all_next()` 는 전체 DOM depth-first 탐색으로 인접 section의 content를 경계 heading 전에 수집함
    - **수정 방향**: heading anchor의 **직접 parent container**를 기준으로, parent의 next siblings를 순회하는 방식으로 전환. 각 sibling이 heading으로 시작하면 해당 sibling 내부는 수집하지 않음
    - Parent container 전략:
      1. `anchor.parent` 가 `div/section/article` 등 container인 경우, `anchor.parent.next_siblings`를 순회
      2. 각 sibling node에 대해: sibling 자체가 heading이거나, sibling 내부 **첫 번째 descendant**가 heading이면 stop
      3. Stop 조건 미충족 시 기존 `_harvest()` 로직으로 content 수집
    - **중요**: h3-under-h2 패턴(기존 BOUNDARY_CASE)은 유지. 즉 같은 parent container 내의 sub-heading h3는 stop하지 않음
    - `stop_level` 파라미터 로직 유지 — h3가 h2 section 아래 있을 때 h2 stop_level=2 이므로 h3(level 3)는 stop 안 됨
  - 또한 `"profile"` synonym을 biography에서 qualified 형태로 변경:
    - 현재: `"profile"` (plain string)
    - 변경: `("profile", frozenset({"bio", "overview", "summary", "about"}))` — profile이 단독으로 쓰이면 매핑하지 않음
    - 이유: Latham의 `<h2>Profile</h2>`가 biography section으로 잘못 분류되어 이후 모든 content가 오분류됨

  **Must NOT do**:
  - `_MAX_BLOCK_LEN = 400` 변경
  - `_HEADING_TAGS`, `_CONTENT_TAGS` set 변경 (다른 태스크 영역)
  - firm-specific if/elif 추가

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: DOM traversal 로직 변경은 complex side-effect 가능성 높음. 정확한 before/after 분석 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 1과 동시, Wave 1)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 6
  - **Blocked By**: Task 1 (테스트 케이스 참조용)

  **References**:

  **Pattern References**:
  - `parser_sections.py:220-286` — `_collect_content_after` 전체
  - `parser_sections.py:257-286` — 현재 `find_all_next()` 탐색 로직
  - `parser_sections.py:239-255` — `_harvest()` inner function (유지)
  - `tests/test_parser_sections.py` BOUNDARY_CASE — h3 under h2 content 수집 테스트. 이 테스트가 수정 후에도 PASS 해야 함

  **API/Type References**:
  - `parser_sections.py:146-153` — biography synonyms (profile 수정 위치)
  - `parser_sections.py:61-154` — SECTION_SYNONYMS dict 전체 구조

  **External References**:
  - BeautifulSoup docs: `.next_siblings` vs `.find_all_next()` 차이

  **WHY Each Reference Matters**:
  - `_harvest()` 내부 로직은 변경 불필요 — container 내부 content 수집 자체는 올바름. 문제는 어떤 container를 순회하느냐
  - BOUNDARY_CASE 테스트: h3-under-h2 수집 유지 여부 검증의 핵심. 이 테스트가 PASS 해야만 수정이 안전함

  **Acceptance Criteria**:

  - [ ] `python tests/test_parser_sections.py` → BOUNDARY_OFFICES_BAR 테스트 PASS
  - [ ] `python tests/test_parser_sections.py` → BOUNDARY_PRACTICE_BIO 테스트 PASS
  - [ ] `python tests/test_parser_sections.py` → 기존 BOUNDARY_CASE (h3 under h2) 여전히 PASS
  - [ ] `python -c "from parser_sections import normalize_section_title; r = normalize_section_title('Profile'); assert r != 'biography', f'profile alone should not map to biography, got {r}'"` → exit 0

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: offices←→bar_admissions boundary 수정 확인
    Tool: Bash
    Preconditions: parser_sections.py 수정 완료
    Steps:
      1. python tests/test_parser_sections.py 2>&1 | Select-Object -Last 20
      2. BOUNDARY_OFFICES_BAR 케이스 PASS 확인
    Expected Result: BOUNDARY_OFFICES_BAR: PASS
    Failure Indicators: "New York Bar" still in offices section
    Evidence: .sisyphus/evidence/task-2-boundary-pass.txt

  Scenario: h3-under-h2 regression 없음
    Tool: Bash
    Steps:
      1. python tests/test_parser_sections.py 2>&1 | Select-Object -Last 30
      2. 기존 BOUNDARY_CASE: PASS 확인
    Expected Result: 기존 케이스 모두 PASS (regression zero)
    Evidence: .sisyphus/evidence/task-2-regression-check.txt

  Scenario: profile synonym 수정 확인
    Tool: Bash
    Steps:
      1. python -c "from parser_sections import normalize_section_title; r = normalize_section_title('Profile'); print('Result:', r); assert r != 'biography'"
    Expected Result: Result: (None 또는 다른 키, 절대 'biography' 아님)
    Evidence: .sisyphus/evidence/task-2-profile-synonym.txt
  ```

  **Commit**: YES (그룹 2)
  - Message: `fix(parser_sections): fix section boundary bleed in _collect_content_after and qualify "profile" synonym`
  - Files: `parser_sections.py`
  - Pre-commit: `python tests/test_parser_sections.py`

---

- [x] 3. `validate_practice_areas` bio-sentence 필터 추가

  **What to do**:
  - `validators.py`의 `validate_practice_areas()` (lines 438-473 근처) 수정:
    - 현재: 150자 초과 항목 및 `_JUNK_PHRASES` 매칭 항목만 필터링
    - 추가: biography sentence 패턴 감지 후 필터링
    - Biography 감지 조건 (다음 중 하나 이상 해당하면 필터):
      1. 50자 초과 AND 3개 이상 공백 AND 다음 bio-verb 패턴 포함: `\b(represents|advises|advised|focuses|focused|has represented|has advised|her practice|his practice|her work|his work|she has|he has|her clients|his clients|specializes in|concentrates in)\b` (case-insensitive)
      2. "Award", "Ranked", "Named", "Recognized", "recognized by", "Chambers", "Legal 500" 로 시작하거나 포함하며 50자 초과
      3. 날짜 패턴 + 50자 초과: `\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b` 또는 연도 단독(`\b20\d{2}\b`) 포함하며 나머지 텍스트가 영어 문장
    - **보존 조건**: 다음은 필터하지 않음:
      - 50자 미만 항목 (짧은 practice 이름: "M&A", "Tax", "Corporate Litigation")
      - 단순 슬래시 구분 리스트 (예: "Antitrust / Competition")
      - 괄호 없는 순수 명사구

  **Must NOT do**:
  - `_JUNK_PHRASES` 기존 항목 제거
  - 50자 미만 항목에 영향 주는 필터
  - `validate_bar_admissions`, `validate_education` 수정 (이 태스크 범위 외)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단일 함수에 정규식 필터 추가. 로직이 명확하고 side-effect 제한적
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 4, 5와 동시, Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `validators.py:438-473` — `validate_practice_areas()` 전체 (수정 대상)
  - `validators.py` 내 `_JUNK_PHRASES` 정의 (추가 패턴 참조)

  **Test References**:
  - `tests/test_parser_sections.py` BOUNDARY_PRACTICE_BIO — bio 문장이 필터되는지 검증

  **Acceptance Criteria**:

  - [ ] `python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['M&A', 'She advises clients on complex litigation matters.']); assert 'M&A' in r; assert not any('advises' in s for s in r), f'bio text should be filtered: {r}'"` → exit 0
  - [ ] `python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['Antitrust', 'Corporate', 'Named as a Chambers Top Ranked attorney for 2024 M&A work']); assert len(r) == 2, f'awards text should be filtered: {r}'"` → exit 0
  - [ ] `python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['M&A', 'Tax', 'Antitrust / Competition']); assert len(r) == 3"` → exit 0 (짧은 항목 보존)

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: bio 문장 필터 — 이전 Paul Weiss 문제 재현
    Tool: Bash
    Steps:
      1. python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['M&A', 'She advises clients on complex litigation matters.']); print(r); assert 'M&A' in r and len(r)==1"
    Expected Result: ['M&A'] — bio 문장 제거됨
    Evidence: .sisyphus/evidence/task-3-bio-filter.txt

  Scenario: 정상 practice area 보존
    Tool: Bash
    Steps:
      1. python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['M&A', 'Tax', 'Restructuring', 'Capital Markets']); assert len(r) == 4, f'Got: {r}'"
    Expected Result: 4개 모두 보존
    Evidence: .sisyphus/evidence/task-3-preserve.txt
  ```

  **Commit**: YES (그룹 3)
  - Message: `fix(validators): add bio-sentence detection to validate_practice_areas`
  - Files: `validators.py`
  - Pre-commit: `python tests/test_parser_sections.py`

---

- [x] 4. Department 추출 — CSS generic pattern + title split

  **What to do**:
  - `enrichment.py`의 `_extract_from_css_classes()` (또는 해당하는 CSS 추출 메서드) 수정:
    - **Generic CSS department pattern 추가**:
      ```python
      # Generic: class에 'department', 'practice-group', 'group' 포함하는 모든 요소
      for el in soup.find_all(True, class_=re.compile(r'department|practice[\-_]?group|dept', re.I)):
          text = _clean_text(el.get_text())
          if text and 2 < len(text) < 80:
              dept_candidates.append(text)
      ```
    - **Title split heuristic 추가** (기존 title 추출 후 실행):
      - title 텍스트가 "TITLE, DEPARTMENT" 형태인 경우 (쉼표 구분)
      - TITLE_WORD가 `_KNOWN_ATTORNEY_TITLES` frozenset에 속하면, 나머지 텍스트를 department 후보로
      - 예: "Partner, Corporate Department" → title="Partner", dept="Corporate Department"
      - `_KNOWN_ATTORNEY_TITLES` = `{"partner", "associate", "counsel", "senior counsel", "of counsel", "principal", "director", "shareholder", "member", "special counsel"}`
    - **JSON-LD department 경로 확인**: `_merge_json_ld()`에서 `jobTitle` 분리 외에 `department` 키도 이미 처리 중인지 확인. 없으면 추가
    - 추출된 dept 후보는 기존 `validate_department()` 호출로 검증 후 저장

  **Must NOT do**:
  - firm-specific CSS selector 추가 (예: `class_="lw-department"`)
  - practice_areas에서 department 추론 (예: "M&A" → "Corporate")
  - `discovery.py` 수정

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: `enrichment.py`의 5-stage cascade 이해 필요. CSS generic pattern이 의도치 않은 노이즈를 끌어올 수 있어 신중한 구현 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 3, 5와 동시, Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `enrichment.py` — `_extract_from_css_classes()` 메서드 (generic CSS extraction 기존 패턴)
  - `enrichment.py` — `_merge_json_ld()` 메서드 (JSON-LD department key 처리 확인)
  - `enrichment.py:611-616` — 기존 "Title, Department" CSS split 로직 (firm-specific 패턴 참조)
  - `validators.py` — `validate_department()` 함수 (추출된 dept 후보 검증에 사용)

  **API/Type References**:
  - `attorney_extractor.AttorneyProfile.department: list[str]` — 저장 타입

  **WHY Each Reference Matters**:
  - `_extract_from_css_classes` 기존 패턴: firm-specific selector가 어떻게 작성됐는지 보고 generic 버전으로 확장
  - `_merge_json_ld`: department JSON-LD key가 이미 처리 중인지 확인 — 중복 추가 방지
  - `validate_department()`: 최종 필터로 노이즈 차단. 호출 필수

  **Acceptance Criteria**:

  - [ ] `python run_pipeline.py --firms "kirkland" --max-profiles 5` → 최소 일부 profile에서 `department` 비어있지 않음 (Kirkland는 CSS class에 dept 정보 있음)
  - [ ] `python -c "from enrichment import ProfileEnricher; ..."` — title="Partner, Corporate" 입력 시 department=["Corporate"] 추출

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Kirkland에서 department 추출 확인
    Tool: Bash
    Steps:
      1. python run_pipeline.py --firms "kirkland" --max-profiles 5 2>&1 | Select-Object -Last 20
      2. 최신 outputs/*.jsonl 파일 읽어서 department 확인
      3. python -c "import json,glob,sys; sys.stdout.reconfigure(encoding='utf-8'); f=sorted(glob.glob('outputs/*.jsonl'))[-1]; data=[json.loads(l) for l in open(f,encoding='utf-8')]; print('Departments:', [p['department'] for p in data])"
    Expected Result: 최소 1개 이상의 profile에서 department 비어있지 않음
    Failure Indicators: 모든 profile에서 department: []
    Evidence: .sisyphus/evidence/task-4-dept-kirkland.txt

  Scenario: title split 동작 확인
    Tool: Bash
    Steps:
      1. python -c "
from validators import validate_department
result, _ = validate_department(['Corporate Department'])
print('Result:', result)
assert result and len(result) > 0
"
    Expected Result: department 값 추출됨
    Evidence: .sisyphus/evidence/task-4-title-split.txt
  ```

  **Commit**: YES (그룹 4)
  - Message: `fix(enrichment): add generic CSS department extraction and title-split department heuristic`
  - Files: `enrichment.py`
  - Pre-commit: `python tests/test_enrichment_integration.py`

---

- [x] 5. Latham SPA_OTHER — h3 sub-section 인식 + profile synonym 수정

  **What to do**:
  - **Task 2에서 이미 `"profile"` synonym 수정이 이뤄졌다면 이 태스크에서는 Latham 특화 부분만 처리**
  - `enrichment.py` — SPA_OTHER 타입에서의 title/office 추출 개선:
    - Latham 페이지는 JSON-LD 없음. title/office는 `<h1>` 바로 다음에 오는 `<p>` 또는 `<span>` 텍스트에 위치할 가능성 높음
    - **Generic hero-section extraction 추가**:
      ```python
      # After h1 name extraction: look for title/office in the first 3 siblings/children after h1
      h1 = soup.find("h1")
      if h1:
          for candidate in h1.find_next_siblings(limit=5):
              text = _clean_text(candidate.get_text())
              if text in _KNOWN_ATTORNEY_TITLES_SET:  # "Partner", "Associate" etc.
                  # title found
              elif _is_us_city_or_state(text):  # validate with known city list
                  # office found
      ```
    - **`<h3>` sub-section under `<h2>Qualifications</h2>` 인식**:
      - Latham 구조: `<h2>Qualifications</h2>` 아래에 `<h3>Bar Qualification</h3>`, `<h3>Education</h3>`, `<h3>Practices</h3>`
      - 현재 `parse_sections()`는 heading_nodes loop에서 h3를 별도로 처리하므로 `<h3>Practices</h3>` → `practice_areas` 매핑이 이뤄져야 함
      - 문제: Task 2에서 `_collect_content_after` 수정 후 h3 content가 h2 Qualifications 버킷에도 중복으로 들어가지 않는지 검증
      - 검증 포인트: `parse_sections()` 결과에서 `practice_areas` 키에 "Antitrust"가 있고, `education` 키에 "Harvard Law"가 있으며, `bar_admissions` 키에 관련 텍스트가 있어야 함
    - `_is_us_city_or_state()` 헬퍼가 없다면 기존 `US_STATES` 리스트 + 주요 US 도시 리스트로 구현:
      ```python
      _US_CITIES = frozenset({
          "New York", "Los Angeles", "Chicago", "Houston", "Washington", "San Francisco",
          "Boston", "Dallas", "Miami", "Atlanta", "Seattle", "Denver", "Philadelphia",
          "Phoenix", "San Diego", "Minneapolis", "Portland", "Austin", "Las Vegas",
          "Charlotte", "Detroit", "Newark", "Pittsburgh", "Richmond", "Salt Lake City",
          "San Jose", "Tampa", "Baltimore", "Cincinnati", "Cleveland", "Columbus",
          "Hartford", "Honolulu", "Kansas City", "Memphis", "Milwaukee", "Nashville",
          "New Orleans", "Oakland", "Oklahoma City", "Orlando", "Sacramento",
          "San Antonio", "Silicon Valley", "St. Louis", "Wilmington",
      })
      ```

  **Must NOT do**:
  - Latham-specific `if "lw.com" in url` 코드 추가
  - Playwright escalation 추가
  - `SPA_OTHER` 타입 전용 별도 extraction path 신규 구현 (generic hero-section extraction으로 모든 타입 커버)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: enrichment.py 5-stage cascade 깊은 이해 필요, hero-section extraction의 false positive 방지 중요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 3, 4와 동시, Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6
  - **Blocked By**: Task 1 (테스트), Task 2 (profile synonym + boundary fix, 상호 의존)

  **References**:

  **Pattern References**:
  - `enrichment.py` — `_extract_hero_fields()` 메서드 (기존 h1 근처 추출 패턴)
  - `enrichment.py` — `_extract_from_css_classes()` (기존 패턴, generic 확장 참조)
  - `enrichment.py` — `_extract_from_structured_data()` (JSON-LD 경로, Latham에서는 없음)
  - `attorney_extractor.py` — `US_STATES`, `_KNOWN_ATTORNEY_TITLES` (있다면) — 헬퍼 재사용
  - `tests/test_enrichment_integration.py` LATHAM_SPA_OTHER_FIXTURE — 이 테스트가 PASS해야 함

  **WHY Each Reference Matters**:
  - `_extract_hero_fields()`: 이미 h1 근처 추출 로직이 있을 수 있음. 중복 추가 방지 및 확장 포인트 파악
  - `US_STATES`: 이미 정의된 리스트, `_US_CITIES` 추가 시 같은 위치에 두어 일관성 유지
  - LATHAM_SPA_OTHER_FIXTURE: Task 1에서 작성한 테스트. 이 테스트가 PASS하면 Task 5 완료

  **Acceptance Criteria**:

  - [ ] `python tests/test_enrichment_integration.py` → LATHAM_SPA_OTHER_FIXTURE PASS
  - [ ] `python run_pipeline.py --firms "latham" --max-profiles 3` → `full_name`이 "Israel Practice", "Stephanie Adams\nPartner" 등 비정상 값 아닌 실제 사람 이름
  - [ ] `python run_pipeline.py --firms "latham" --max-profiles 3` → `practice_areas` 비어있지 않으며 Chambers 수상 텍스트/날짜 없음

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Latham 프로필 파싱 정상화 확인
    Tool: Bash
    Steps:
      1. python run_pipeline.py --firms "latham" --max-profiles 3 2>&1 | Select-Object -Last 20
      2. python -c "import json,glob,sys; sys.stdout.reconfigure(encoding='utf-8'); f=sorted(glob.glob('outputs/*.jsonl'))[-1]; [print('Name:', p['full_name'], '| Title:', p['title'], '| PA:', p['practice_areas'][:2]) for p in [json.loads(l) for l in open(f, encoding='utf-8')] ]"
    Expected Result: full_name = "Firstname Lastname" 형태, title != None, practice_areas = ["Antitrust", ...] 형태 (Chambers 텍스트 없음)
    Failure Indicators: full_name contains "Practice", title = None
    Evidence: .sisyphus/evidence/task-5-latham-result.txt

  Scenario: 기존 SITEMAP_XML firms regression 없음
    Tool: Bash
    Steps:
      1. python run_pipeline.py --firms "kirkland" --max-profiles 5 2>&1 | Select-Object -Last 15
    Expected Result: offices, title, practice_areas, bar_admissions 모두 비어있지 않음
    Evidence: .sisyphus/evidence/task-5-kirkland-regression.txt
  ```

  **Commit**: YES (그룹 5)
  - Message: `fix(enrichment): add generic hero-section title/office extraction and h3 sub-section recognition for SPA_OTHER`
  - Files: `enrichment.py`
  - Pre-commit: `python tests/test_enrichment_integration.py`

---

- [x] 6. 전체 통합 테스트 sweep — 구조 타입별 대표 firm 샘플

  **What to do**:
  - Wave 2 (Tasks 2-5) 완료 후, 전체 pipeline을 구조 타입별로 샘플 테스트
  - 테스트 대상 (각 3 profiles):
    - SITEMAP_XML: `kirkland`, `paul weiss`, `greenberg traurig`
    - HTML_DIRECTORY_FLAT: (Jones Day 403이면 skip → `goodwin procter`, `king spalding`)
    - SPA_OTHER: `latham`, `fried frank`
    - HTML_ALPHA_PAGINATED: `hunton andrews`, `stinson`
  - 각 firm의 결과에서 다음 체크:
    1. `full_name` — 실제 사람 이름 형태 (성+이름, "Israel Practice" 같은 것 없음)
    2. `title` — "Partner"/"Associate"/"Counsel" 등, None 아님
    3. `offices` — US 도시 또는 주, bar admission 텍스트 혼입 없음
    4. `practice_areas` — 짧은 명사구, bio/awards 텍스트 없음
    5. `department` — 빈 배열이어도 허용 (데이터 없으면 OK, 오염 데이터면 NG)
    6. `bar_admissions` — US state 이름
    7. `education` — degree/school/year 형태
  - 발견된 추가 버그는 이 태스크 내에서 즉시 수정 또는 새 TODO 등록
  - `site_structures.json`에서 Jones Day가 403 반환 시 `BOT_PROTECTED`로 재분류

  **Must NOT do**:
  - 20개 이상 firm 전체 실행 (이 태스크는 샘플 검증)
  - `discovery.py` 수정
  - 성능 최적화

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 여러 firm 테스트 + 결과 분석 + 잔여 버그 트리아지 필요. 자율적 판단 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (모든 Wave 2 태스크 완료 후)
  - **Parallel Group**: Sequential (Wave FINAL)
  - **Blocks**: 없음 (최종 태스크)
  - **Blocked By**: Tasks 2, 3, 4, 5 모두

  **References**:

  **Pattern References**:
  - `site_structures.json` — Jones Day 항목 위치 (`structure_type` 필드)
  - AGENTS.md — 테스트 커맨드 패턴

  **Acceptance Criteria**:

  - [ ] SITEMAP_XML 3 firms × 3 profiles = 9 profiles: 모두 `full_name` 유효, `title` 유효
  - [ ] SPA_OTHER (latham, fried frank): `full_name` 유효, `practice_areas` bio 텍스트 없음
  - [ ] `python tests/test_parser_sections.py` → 0 failures (모든 신규 + 기존)
  - [ ] `python tests/test_enrichment_integration.py` → 0 failures

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: SITEMAP_XML 대표 firm 결과 검증
    Tool: Bash
    Steps:
      1. python run_pipeline.py --firms "greenberg traurig" --max-profiles 3 2>&1 | Select-Object -Last 10
      2. python -c "import json,glob,sys; sys.stdout.reconfigure(encoding='utf-8'); f=sorted(glob.glob('outputs/*.jsonl'))[-1]; [print(json.dumps({k: p[k] for k in ['full_name','title','offices','department','practice_areas']}, ensure_ascii=False)[:200]) for p in [json.loads(l) for l in open(f,encoding='utf-8')]]"
    Expected Result: 각 profile의 full_name, title, offices 모두 정상 값
    Evidence: .sisyphus/evidence/task-6-sitemap-sample.txt

  Scenario: SPA_OTHER Latham + Fried Frank 결과 검증
    Tool: Bash
    Steps:
      1. python run_pipeline.py --firms "fried frank" --max-profiles 3 2>&1 | Select-Object -Last 10
      2. 최신 JSONL 파일에서 full_name, title, practice_areas 확인
    Expected Result: full_name 유효한 사람 이름, practice_areas에 bio/awards 텍스트 없음
    Evidence: .sisyphus/evidence/task-6-spa-other-sample.txt

  Scenario: 전체 테스트 스위트 최종 확인
    Tool: Bash
    Steps:
      1. python tests/test_parser_sections.py 2>&1 | Select-Object -Last 10
      2. python tests/test_enrichment_integration.py 2>&1 | Select-Object -Last 10
    Expected Result: 두 파일 모두 0 failures
    Evidence: .sisyphus/evidence/task-6-final-tests.txt
  ```

  **Commit**: YES (그룹 6)
  - Message: `fix: integration sweep — reclassify Jones Day as BOT_PROTECTED, address remaining field extraction issues`
  - Files: `site_structures.json` (Jones Day 재분류 시), 기타 잔여 수정 파일
  - Pre-commit: `python tests/test_parser_sections.py`

---

## Final Verification Wave (MANDATORY)

> 모든 구현 태스크 완료 후 실행. 결과를 사용자에게 제시하고 명시적 승인 대기.

- [x] F1. **Plan Compliance Audit** — `oracle`
  각 Must Have 항목 구현 여부 확인. Must NOT Have (firm-specific hardcode 등) 검색. evidence 파일 존재 확인.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | VERDICT: APPROVE/REJECT`
  Result: APPROVE (false alarm REJECT — M&A fix already in place via _KNOWN_UPPERCASE_PRACTICES)

- [x] F2. **Code Quality Review** — `unspecified-high`
  `python -c "import py_compile; py_compile.compile('parser_sections.py'); py_compile.compile('validators.py'); py_compile.compile('enrichment.py')"` 문법 오류 확인. `as any`, 빈 except, 주석처리된 코드, firm-specific if/elif 검색.
  Output: `Syntax [PASS/FAIL] | Firm-specific code [CLEAN/N issues] | VERDICT`
  Result: APPROVE — 16/16 + 48/48 PASS, syntax clean, no firm-specific hardcodes

- [x] F3. **Real QA Execution** — `unspecified-high`
  깨끗한 상태에서 Kirkland, Paul Weiss, Latham 각 5 profiles 실행. JSONL 직접 읽어 필드 검증.
  Output: `Firms [N/N pass] | VERDICT`
  Result: APPROVE — Kirkland 4/4, Paul Weiss 5/5 PASS; also added _KNOWN_UPPERCASE_PRACTICES fix

- [x] F4. **Scope Fidelity Check** — `deep`
  `find_attorney.py`, `discovery.py`, `run_pipeline.py` 수정 없음 확인. 신규 firm-specific 코드 없음 확인.
  Output: `Out-of-scope files [CLEAN/issues] | VERDICT`
  Result: APPROVE (false alarm REJECT — git diff HEAD -- find_attorney.py was empty)

---

## Commit Strategy

1. `test: add RED regression tests for section boundary, bio leak, department, Latham structure`
2. `fix(parser_sections): fix section boundary bleed in _collect_content_after and qualify "profile" synonym`
3. `fix(validators): add bio-sentence detection to validate_practice_areas`
4. `fix(enrichment): add generic CSS department extraction and title-split department heuristic`
5. `fix(enrichment): add generic hero-section title/office extraction and h3 sub-section recognition for SPA_OTHER`
6. `fix: integration sweep — reclassify Jones Day BOT_PROTECTED, address remaining issues`

---

## Success Criteria

### Verification Commands
```bash
# 테스트 스위트
python tests/test_parser_sections.py  # Expected: 0 failures
python tests/test_enrichment_integration.py  # Expected: 0 failures

# Kirkland golden reference
python run_pipeline.py --firms "kirkland" --max-profiles 5
# Expected: offices, title, practice_areas, bar_admissions, education 모두 비어있지 않음

# Paul Weiss bio leak 수정 확인
python run_pipeline.py --firms "paul weiss" --max-profiles 5
# Expected: practice_areas에 "advises", "represents" 같은 bio 문장 없음

# Latham 수정 확인
python run_pipeline.py --firms "latham" --max-profiles 5
# Expected: full_name이 실제 사람 이름, practice_areas에 Chambers 텍스트 없음

# 단위 assertion
python -c "from parser_sections import normalize_section_title; assert normalize_section_title('Litigation Group') == 'departments'"
python -c "from parser_sections import normalize_section_title; assert normalize_section_title('Working Group') != 'departments'"
python -c "from validators import validate_practice_areas; r, _ = validate_practice_areas(['M&A', 'She advises clients on complex litigation matters.']); assert len(r) == 1 and r[0] == 'M&A'"
```

### Final Checklist
- [ ] `_collect_content_after`가 인접 section 경계를 올바르게 인식
- [ ] `"profile"` 단독 heading → biography 오매핑 없음
- [ ] practice_areas에서 bio 문장 필터링
- [ ] department가 CSS class / title split / JSON-LD에서 추출 시도됨
- [ ] Latham (SPA_OTHER) 프로필에서 full_name, title, practice_areas 정상 추출
- [ ] Kirkland golden reference 회귀 없음
- [ ] 기존 테스트 (BOUNDARY_CASE, Working Group 네거티브 등) 모두 PASS
- [ ] `find_attorney.py`, `discovery.py`, `run_pipeline.py` 수정 없음
- [ ] firm-specific if/elif 신규 추가 없음
