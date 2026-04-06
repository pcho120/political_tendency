# Full Field Extraction — AmLaw200 전체 변호사 8개 필드 완전 추출

## TL;DR

> **Quick Summary**: find_attorney.py 기반으로 200개 AmLaw 펌의 모든 변호사에 대해 8개 필드(이름, 직책, office, department, practice areas, industries, bar admissions, education)를 완전 추출. BOT_PROTECTED 펌은 Martindale-Hubbell로 대체 수집. department/industries 추출 로직을 패턴 매칭 + 매핑 테이블로 대폭 강화.
> 
> **Deliverables**:
> - pytest 인프라 + 추출 로직 단위 테스트
> - department/industries 추출 강화 (sentinel 비율 60%→30% 이하 목표)
> - practice_areas → department 매핑 테이블
> - Martindale robots.txt 컴플라이언스 수정
> - 누락 10개 펌 probe 및 site_structures.json 업데이트
> - 200개 펌 전체 추출 결과 Excel + JSONL
> 
> **Estimated Effort**: Large (에이전트 실행 ~4-6시간 + 파이프라인 런타임 ~10-15시간)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 3 → Task 5 → Task 7 → Task 9 → Task 11 → Task 13 → F1-F4

---

## Context

### Original Request
200개 AmLaw 펌의 모든 변호사에 대해 8개 필드를 합법적으로 완전 추출하는 단일 플랜 요청.

### Interview Summary
**Key Discussions**:
- **베이스 스크래퍼**: find_attorney.py 선택 (높은 커버리지, 외부 디렉토리 폴백 내장)
- **BOT_PROTECTED 펌**: Martindale-Hubbell 공개 디렉토리로 대체 (합법적)
- **약한 필드**: department/industries 추출 로직 대폭 강화 (패턴 매칭 + 매핑 테이블)
- **기존 데이터**: 무시, 처음부터 새로 전체 실행
- **테스트**: pytest 세팅 + TDD 방식 단위 테스트

**Research Findings**:
- site_structures.json에 190개 펌만 존재 (rank 3-12 누락 — DLA Piper, Baker McKenzie 등 대형)
- BOT_PROTECTED 실제 23개 펌 (초기 추정 10-15보다 많음)
- SITEMAP_XML 102개, HTML_DIRECTORY_FLAT 35개, HTML_ALPHA_PAGINATED 11개 등
- Martindale 추출기 이미 구현됨 (~500줄) — 하지만 /search/ 경로가 robots.txt 위반
- tests/ 디렉토리에 4개 테스트 파일 + HTML fixture 이미 존재 (pytest 미설치)

### Metis Review
**Identified Gaps** (addressed):
- rank 3-12 펌 누락 → probe 후 포함 결정
- Martindale robots.txt 위반 → /organization/ 또는 sitemap 기반으로 수정
- department/industries 품질 기준 모호 → 패턴 매칭 + 매핑 테이블 결정
- 기존 테스트가 standalone 스크립트 → pytest 변환 포함
- Martindale가 이미 구현됨 → 신규 개발이 아닌 개선/수정으로 스코프 조정

---

## Work Objectives

### Core Objective
find_attorney.py 파이프라인의 필드 추출 능력을 강화하여 200개 AmLaw 펌의 모든 변호사에 대해 8개 필드를 최대한 완전하게 추출한다.

### Concrete Deliverables
- `pyproject.toml` + `tests/conftest.py`: pytest 인프라
- 추출 로직 단위 테스트 (validators, parser_sections, department, industries, martindale)
- `enrichment.py`, `parser_sections.py`, `field_enricher.py`: department/industries 추출 강화
- `config/practice_department_map.json`: practice_areas → department 매핑 테이블
- `external_directory_extractor.py`: Martindale robots.txt 컴플라이언스 수정
- `site_structures.json`: 누락 10개 펌 추가 (총 200개)
- `outputs/attorneys_<timestamp>.xlsx` + `.jsonl`: 전체 추출 결과

### Definition of Done
- [ ] `pytest tests/ -v` → 0 failures
- [ ] `python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 5` → 8개 필드 모두 비어있지 않음
- [ ] department sentinel 비율 < 40% (baseline 대비 감소)
- [ ] industries sentinel 비율 < 50% (baseline 대비 감소)
- [ ] 200개 펌 전체 추출 완료, 출력 파일 존재
- [ ] Martindale 코드에 `/search/` 경로 없음

### Must Have
- 8개 필드 전체 추출 (name, title, office, department, practice_areas, industries, bar_admissions, education)
- robots.txt 준수 (Martindale 포함)
- BOT_PROTECTED 펌 → Martindale 폴백
- 200개 펌 전체 커버리지 (190 기존 + 10 probe)
- pytest 기반 단위 테스트
- Excel + JSONL 출력

### Must NOT Have (Guardrails)
- ❌ Cloudflare/bot-protection 우회 시도
- ❌ robots.txt Disallow 경로 접근
- ❌ 펌별 하드코딩 로직 추가 (AGENTS.md 절대 규칙)
- ❌ NLP/ML 기반 필드 추론
- ❌ 새 외부 디렉토리 추가 (Avvo, Yelp, Super Lawyers 등)
- ❌ AttorneyProfile 데이터클래스 필드 정의 변경
- ❌ 단위 테스트에서 실제 웹사이트 접근
- ❌ 기존 190개 펌 전체 re-probe
- ❌ 출력 포맷 변경 (Excel + JSONL 유지)
- ❌ Playwright 에스컬레이션 로직 신규 추가 (기존 것만 사용)

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: PARTIAL (tests/ 디렉토리 + fixture 존재, pytest 미설치)
- **Automated tests**: TDD (RED-GREEN-REFACTOR)
- **Framework**: pytest + pytest-mock + responses
- **TDD**: 각 추출 강화 작업에서 failing test 먼저 → implementation → pass

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **추출 로직**: Bash (python3.12 REPL) — import, call, compare output
- **파이프라인 실행**: Bash (python3.12 find_attorney.py) — run, check output files
- **Martindale 수정**: Bash (grep) — 코드에 /search/ 없음 확인
- **테스트**: Bash (pytest) — 실행, 결과 확인

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — foundation):
├── Task 1: pytest 인프라 세팅 [quick] (~5분)
├── Task 2: 기존 standalone 테스트 → pytest 변환 [quick] (~10분)
└── Task 3: Baseline 측정 (3개 펌 추출, sentinel 비율 기록) [unspecified-high] (~10분)

Wave 2 (After Wave 1 — TDD RED phase + Martindale + probe):
├── Task 4: department 추출 테스트 작성 (RED) [quick] (~10분)
├── Task 5: industries 추출 테스트 작성 (RED) [quick] (~10분)
├── Task 6: Martindale 컴플라이언스 수정 + 테스트 [deep] (~20분)
├── Task 7: practice→department 매핑 테이블 생성 [unspecified-high] (~15분)
└── Task 8: 누락 10개 펌 probe + site_structures.json 업데이트 [unspecified-high] (~15분)

Wave 3 (After Wave 2 — TDD GREEN phase):
├── Task 9: department 추출 로직 강화 (GREEN) [deep] (~20분)
├── Task 10: industries 추출 로직 강화 (GREEN) [deep] (~20분)
└── Task 11: practice→department 매핑 적용 [unspecified-high] (~15분)

Wave 4 (After Wave 3 — integration + full run):
├── Task 12: 3개 펌 재추출 (post-change 비교) [unspecified-high] (~10분)
└── Task 13: 200개 펌 전체 추출 실행 [deep] (~10-15시간 런타임)

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: T1 → T3 → T4 → T9 → T12 → T13 → F1-F4 → user okay
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 5 (Wave 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | - | 2, 4, 5, 6, 7 | 1 |
| 2 | 1 | 4, 5 | 1 |
| 3 | - | 12 | 1 |
| 4 | 1, 2 | 9 | 2 |
| 5 | 1, 2 | 10 | 2 |
| 6 | 1 | 13 | 2 |
| 7 | - | 11 | 2 |
| 8 | - | 13 | 2 |
| 9 | 4 | 12 | 3 |
| 10 | 5 | 12 | 3 |
| 11 | 7, 9 | 12 | 3 |
| 12 | 3, 9, 10, 11 | 13 | 4 |
| 13 | 6, 8, 12 | F1-F4 | 4 |

### Agent Dispatch Summary

- **Wave 1**: **3** — T1 → `quick`, T2 → `quick`, T3 → `unspecified-high`
- **Wave 2**: **5** — T4 → `quick`, T5 → `quick`, T6 → `deep`, T7 → `unspecified-high`, T8 → `unspecified-high`
- **Wave 3**: **3** — T9 → `deep`, T10 → `deep`, T11 → `unspecified-high`
- **Wave 4**: **2** — T12 → `unspecified-high`, T13 → `deep`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. pytest 인프라 세팅

  **What to do**:
  - `pyproject.toml`에 pytest 설정 추가 (testpaths, python_files, markers)
  - `/home/pcho/.local/bin/pip install pytest pytest-mock responses` 실행
  - `tests/conftest.py` 생성: `html_fixture(name)` 헬퍼 (tests/fixtures/html/에서 HTML 로드), `jsonl_fixture(name)` 헬퍼
  - `tests/__init__.py` 생성 (빈 파일)
  - `pytest tests/ --collect-only`로 테스트 수집 확인

  **Must NOT do**:
  - 기존 테스트 파일 내용 수정하지 않음 (변환은 Task 2)
  - 추출 로직 수정하지 않음

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단일 설정 작업, pyproject.toml + conftest.py 2개 파일
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - None applicable

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 2, 4, 5, 6, 7
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `tests/` — 기존 테스트 디렉토리 구조. 4개 파일 + fixtures/html/ 확인
  - `tests/fixtures/html/` — 기존 HTML fixture 파일들 (department_concat_blob.html 등)

  **API/Type References**:
  - 없음

  **Test References**:
  - `tests/test_validators.py` — 기존 standalone 테스트 패턴 확인용

  **External References**:
  - pytest 공식 문서: conftest.py, fixture scope

  **WHY Each Reference Matters**:
  - `tests/` 구조를 먼저 읽어야 conftest.py가 기존 fixture들과 호환되게 설계 가능
  - 기존 standalone 테스트를 보면 어떤 fixture 헬퍼가 필요한지 파악 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: pytest 설치 및 설정 확인
    Tool: Bash
    Preconditions: Python 3.12 환경
    Steps:
      1. /home/pcho/.local/bin/pip install pytest pytest-mock responses
      2. pytest tests/ --collect-only
      3. python3.12 -c "import pytest; print(pytest.__version__)"
    Expected Result: pip 설치 성공, pytest가 tests/ 디렉토리를 인식, 버전 출력
    Failure Indicators: ModuleNotFoundError, "no tests ran", collection error
    Evidence: .sisyphus/evidence/task-1-pytest-setup.txt

  Scenario: conftest.py fixture 헬퍼 동작
    Tool: Bash
    Preconditions: conftest.py 작성 완료
    Steps:
      1. python3.12 -c "from tests.conftest import *; print('import ok')"
      2. tests/fixtures/html/ 디렉토리에 파일이 1개 이상 존재하는지 확인
    Expected Result: import 성공, fixture 디렉토리 확인
    Failure Indicators: ImportError, FileNotFoundError
    Evidence: .sisyphus/evidence/task-1-conftest-verify.txt
  ```

  **Commit**: YES (group: 1)
  - Message: `build(test): set up pytest infrastructure with conftest and fixtures`
  - Files: `pyproject.toml`, `tests/conftest.py`, `tests/__init__.py`
  - Pre-commit: `pytest tests/ --collect-only`

- [x] 2. 기존 standalone 테스트 → pytest 변환

  **What to do**:
  - `tests/` 디렉토리의 기존 4개 테스트 파일을 읽고 pytest 형식으로 변환
  - 커스텀 assertion → `assert` 문으로 변환
  - `if __name__ == "__main__"` 블록 제거 또는 유지 (호환성)
  - 각 테스트 함수에 `test_` prefix 확인
  - `pytest tests/ -v` 실행하여 전체 통과 확인

  **Must NOT do**:
  - 테스트 로직 자체를 변경하지 않음 (assertion 구문만 변환)
  - 추출 로직 수정하지 않음
  - 실제 웹사이트 접근하는 테스트가 있으면 skip 마킹

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 기존 파일의 구문 변환, 로직 변경 없음
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 1과 동일 Wave지만 Task 1 완료 후)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `tests/test_validators.py` — 변환 대상 1: validator 테스트
  - `tests/test_parser_sections.py` — 변환 대상 2: 파서 테스트
  - `tests/test_enrichment_integration.py` — 변환 대상 3: enrichment 테스트
  - `tests/test_field_merger.py` — 변환 대상 4: field merger 테스트

  **WHY Each Reference Matters**:
  - 각 파일의 현재 assertion 패턴을 파악해야 올바르게 pytest assert로 변환 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: 변환된 테스트 전체 통과
    Tool: Bash
    Preconditions: Task 1 완료 (pytest 설치됨)
    Steps:
      1. pytest tests/ -v --tb=short
      2. 결과에서 "passed" 개수 확인
      3. "failed" 또는 "error" 가 0인지 확인
    Expected Result: 모든 테스트 passed, 0 failed, 0 errors
    Failure Indicators: FAILED, ERROR, collection error
    Evidence: .sisyphus/evidence/task-2-pytest-conversion.txt

  Scenario: 기존 테스트 로직 보존 확인
    Tool: Bash
    Preconditions: 변환 완료
    Steps:
      1. pytest tests/ -v | grep "PASSED" | wc -l
      2. 기존 테스트 개수와 비교 (변환 전 수동 카운트)
    Expected Result: 테스트 개수 동일 (로직 보존)
    Failure Indicators: 테스트 개수 감소
    Evidence: .sisyphus/evidence/task-2-test-count.txt
  ```

  **Commit**: YES (group: 2)
  - Message: `refactor(test): convert standalone tests to pytest format`
  - Files: `tests/test_*.py`
  - Pre-commit: `pytest tests/ -v`

- [x] 3. Baseline 측정 — 3개 펌 추출 및 sentinel 비율 기록

  **What to do**:
  - 3개 대표 펌 선정: 1 SITEMAP_XML (예: Kirkland), 1 HTML_DIRECTORY_FLAT (예: Davis Polk), 1 BOT_PROTECTED (예: Jones Day)
  - 각 펌에 대해 `python3.12 find_attorney.py --debug-firm "{firm}" --max-profiles 10` 실행
  - 결과 JSONL 파일 분석: 8개 필드 각각의 sentinel/빈값 비율 계산
  - baseline 결과를 `.sisyphus/evidence/task-3-baseline.json`에 저장
  - 형식: `{"firm": "Kirkland", "department_sentinel_rate": 0.6, "industries_sentinel_rate": 0.7, ...}`

  **Must NOT do**:
  - 추출 로직 수정하지 않음 (순수 측정만)
  - BOT_PROTECTED 펌에 대해 직접 사이트 접근 시도하지 않음

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 파이프라인 실행 + 데이터 분석 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 12
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `find_attorney.py` — `--debug-firm` 및 `--max-profiles` 인수 사용법
  - `outputs/` — 출력 파일 위치 및 형식 (JSONL)
  - `attorney_extractor.py:99-145` — AttorneyProfile 필드 정의 (sentinel 값 확인)

  **WHY Each Reference Matters**:
  - find_attorney.py의 CLI 인수를 정확히 알아야 올바르게 실행 가능
  - AttorneyProfile의 sentinel 값 ("no industry field", "no JD" 등)을 알아야 비율 계산 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: 3개 펌 baseline 추출 성공
    Tool: Bash
    Preconditions: find_attorney.py 실행 가능
    Steps:
      1. python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 10
      2. python3.12 find_attorney.py --debug-firm "Davis Polk" --max-profiles 10
      3. python3.12 find_attorney.py --debug-firm "Jones Day" --max-profiles 10
      4. 각 결과 JSONL에서 department, industries 필드의 sentinel 비율 계산
    Expected Result: 3개 파일 생성, 각 필드별 sentinel 비율 기록
    Failure Indicators: 파이프라인 crash, 출력 파일 없음, 0 attorneys
    Evidence: .sisyphus/evidence/task-3-baseline.json

  Scenario: Baseline 데이터 무결성 확인
    Tool: Bash
    Preconditions: baseline 파일 생성됨
    Steps:
      1. python3.12 -c "import json; d=json.load(open('.sisyphus/evidence/task-3-baseline.json')); print(d)"
      2. 각 펌의 attorney count > 0 확인
    Expected Result: valid JSON, 각 펌 최소 1명 이상 attorney
    Failure Indicators: JSON parse error, attorney count = 0
    Evidence: .sisyphus/evidence/task-3-baseline-verify.txt
  ```

  **Commit**: NO (측정 데이터는 evidence에만 저장)

- [x] 4. Department 추출 테스트 작성 (TDD RED phase)

  **What to do**:
  - `tests/fixtures/html/` 에 최소 3개 department HTML fixture 생성:
    - `department_json_ld.html`: JSON-LD `"department"` 키가 있는 프로필
    - `department_css_class.html`: `[class*=department]`, `[data-department]` 패턴
    - `department_accordion.html`: accordion/tab 안에 department 정보가 있는 구조
  - `tests/test_department_extraction.py` 생성:
    - `test_extract_department_from_json_ld()` — JSON-LD에서 department 추출
    - `test_extract_department_from_css_class()` — CSS 클래스 패턴에서 추출
    - `test_extract_department_from_heading()` — heading 기반 섹션에서 추출
    - `test_department_contamination_filter()` — nav/UI 텍스트 오염 필터링 (기존 `department_concat_blob.html` fixture 활용)
    - `test_department_empty_returns_sentinel()` — department 없는 페이지 → `[]` 반환
  - 각 테스트는 enrichment/extraction 함수를 직접 호출하여 검증
  - `pytest tests/test_department_extraction.py` → FAIL 확인 (아직 로직 미구현)

  **Must NOT do**:
  - 추출 로직 자체를 수정하지 않음 (테스트만 작성)
  - 실제 웹사이트 접근하지 않음 (fixture만 사용)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 테스트 파일 + fixture HTML 작성. 추출 로직 변경 없음
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 8)
  - **Blocks**: Task 9
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `tests/fixtures/html/department_concat_blob.html` — 기존 department 오염 fixture (이미 존재)
  - `attorney_extractor.py:775-801` — `_extract_departments_bs4()` 현재 구현 (무엇이 부족한지 파악)
  - `tests/test_validators.py` — 기존 테스트 패턴 참고 (pytest 변환 후)

  **API/Type References**:
  - `attorney_extractor.py:99-145` — AttorneyProfile.department 필드 타입: `list[str]`

  **External References**:
  - 실제 법률 사이트의 department 구조 패턴 (JSON-LD schema.org/Person, css class 패턴)

  **WHY Each Reference Matters**:
  - `_extract_departments_bs4()` 를 읽어야 현재 구현의 한계를 테스트로 표현 가능
  - 기존 department_concat_blob.html fixture를 보면 오염 필터링 테스트 케이스 설계 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Department 테스트가 RED 상태 확인
    Tool: Bash
    Preconditions: Task 1, 2 완료
    Steps:
      1. pytest tests/test_department_extraction.py -v
      2. 결과에서 FAILED 개수 확인
    Expected Result: 최소 3개 이상 FAILED (RED phase — 아직 로직 미강화)
    Failure Indicators: 모든 테스트가 PASSED (로직이 이미 충분하다면 테스트가 너무 약함)
    Evidence: .sisyphus/evidence/task-4-department-red.txt

  Scenario: Fixture HTML 파일 유효성
    Tool: Bash
    Preconditions: fixture 파일 작성 완료
    Steps:
      1. python3.12 -c "from bs4 import BeautifulSoup; soup=BeautifulSoup(open('tests/fixtures/html/department_json_ld.html').read(), 'lxml'); print(len(soup.find_all('script', type='application/ld+json')))"
    Expected Result: 1 이상 (JSON-LD 태그 존재)
    Failure Indicators: 0 (fixture에 JSON-LD 없음), parse error
    Evidence: .sisyphus/evidence/task-4-fixture-verify.txt
  ```

  **Commit**: YES (group: 3)
  - Message: `test(extract): add failing tests for department extraction (TDD red)`
  - Files: `tests/test_department_extraction.py`, `tests/fixtures/html/department_*.html`
  - Pre-commit: `pytest tests/test_department_extraction.py` → expected FAIL

- [x] 5. Industries 추출 테스트 작성 (TDD RED phase)

  **What to do**:
  - `tests/fixtures/html/` 에 최소 3개 industries HTML fixture 생성:
    - `industries_heading_section.html`: "Industries" 또는 "Sectors" heading 아래 리스트
    - `industries_sidebar.html`: sidebar/aside에 industry 정보
    - `industries_json_ld.html`: JSON-LD `"knowsAbout"` 키에 industry 정보
  - `tests/test_industries_extraction.py` 생성:
    - `test_extract_industries_from_heading()` — heading 기반 섹션에서 추출
    - `test_extract_industries_from_json_ld()` — JSON-LD knowsAbout에서 추출
    - `test_extract_industries_from_sidebar()` — sidebar/aside 구조에서 추출
    - `test_industries_vs_practice_areas()` — industries와 practice_areas 구분
    - `test_industries_empty_returns_sentinel()` — industries 없음 → `["no industry field"]`
  - `pytest tests/test_industries_extraction.py` → FAIL 확인

  **Must NOT do**:
  - 추출 로직 수정하지 않음 (테스트만)
  - 실제 웹사이트 접근하지 않음

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 테스트 + fixture 작성만
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6, 7, 8)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `attorney_extractor.py:929-933` — `_extract_industries_bs4()` 현재 구현 (매우 짧음, heading만)
  - `parser_sections.py` — SECTION_SYNONYMS에서 'industries' 관련 synonym 확인
  - `validators.py` — industries sentinel 값: `["no industry field"]`

  **WHY Each Reference Matters**:
  - `_extract_industries_bs4()` 가 5줄 미만으로 매우 약함 → 테스트가 현재 실패할 새 전략을 커버해야 함
  - SECTION_SYNONYMS를 보면 어떤 heading 키워드가 이미 지원되는지 파악 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Industries 테스트가 RED 상태 확인
    Tool: Bash
    Preconditions: Task 1, 2 완료
    Steps:
      1. pytest tests/test_industries_extraction.py -v
      2. FAILED 개수 확인
    Expected Result: 최소 3개 이상 FAILED
    Failure Indicators: 모든 PASSED (테스트가 너무 약함)
    Evidence: .sisyphus/evidence/task-5-industries-red.txt
  ```

  **Commit**: YES (group: 4)
  - Message: `test(extract): add failing tests for industries extraction (TDD red)`
  - Files: `tests/test_industries_extraction.py`, `tests/fixtures/html/industries_*.html`
  - Pre-commit: `pytest tests/test_industries_extraction.py` → expected FAIL

- [x] 6. Martindale robots.txt 컴플라이언스 수정 + 테스트

  **What to do**:
  - `external_directory_extractor.py`에서 `/search/` URL 사용 부분 찾기
  - `/search/` → `/organization/{firm-slug}/` 또는 Martindale sitemap (`sitemap_profiles.xml`) 기반으로 변경
  - `_extract_from_martindale()` 의 URL 구성 로직 수정
  - `_extract_martindale_html()` 에서 `/search/` 대신 compliant 경로 사용
  - 모든 Martindale 관련 URL이 robots.txt Disallow에 해당하지 않는지 확인
  - rate limit 유지: Martindale 요청 간 최소 3초 간격
  - `tests/test_martindale_extractor.py` 작성:
    - `test_martindale_url_no_search_path()` — /search/ 경로 미사용 확인
    - `test_martindale_profile_mapping()` — _martindale_item_to_profile이 8개 필드 매핑
    - `test_martindale_rate_limit()` — 요청 간 3초 이상 간격 확인
    - `test_martindale_firm_name_filtering()` — 잘못된 firm 결과 필터링 확인
  - HTTP 모킹 (`responses` 라이브러리) 사용하여 라이브 호출 없이 테스트

  **Must NOT do**:
  - Martindale에 실제 HTTP 요청 보내지 않음 (테스트는 mock)
  - 새 외부 디렉토리 추가하지 않음
  - Justia/CA Bar/TX Bar 등 기존 다른 디렉토리 수정하지 않음

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: robots.txt 컴플라이언스 분석 + URL 로직 수정 + 테스트 작성 복합 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 7, 8)
  - **Blocks**: Task 13
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `external_directory_extractor.py` — `_extract_from_martindale()`, `_extract_martindale_html()`, `_martindale_html_cards()`, `_martindale_item_to_profile()` 전체
  - `compliance_engine.py` — robots.txt 체크 로직, path allowance 검사 방식

  **API/Type References**:
  - `attorney_extractor.py:99-145` — AttorneyProfile 필드 (Martindale → AttorneyProfile 매핑에 필요)
  - Martindale robots.txt: `https://www.martindale.com/robots.txt` — Disallow 경로 확인

  **External References**:
  - Martindale 공개 URL 구조: `/organization/{slug}/`, `/attorney/{slug}/`
  - Martindale sitemap: `sitemap_profiles.xml`

  **WHY Each Reference Matters**:
  - `external_directory_extractor.py` 전체를 읽어야 /search/ 사용 위치를 정확히 파악하고 대체 가능
  - `compliance_engine.py`를 보면 robots.txt 체크 패턴을 동일하게 적용 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: /search/ 경로 완전 제거 확인
    Tool: Bash (grep)
    Preconditions: 코드 수정 완료
    Steps:
      1. grep -rn "/search/" external_directory_extractor.py
      2. grep -rn "martindale.com/search" external_directory_extractor.py
    Expected Result: 0 matches (완전 제거됨)
    Failure Indicators: 1개 이상 match
    Evidence: .sisyphus/evidence/task-6-no-search-path.txt

  Scenario: Martindale 테스트 전체 통과
    Tool: Bash
    Preconditions: 테스트 작성 + 코드 수정 완료
    Steps:
      1. pytest tests/test_martindale_extractor.py -v
    Expected Result: 모든 테스트 PASSED
    Failure Indicators: FAILED, ERROR
    Evidence: .sisyphus/evidence/task-6-martindale-tests.txt

  Scenario: Martindale URL이 compliant한지 확인
    Tool: Bash
    Preconditions: 코드 수정 완료
    Steps:
      1. grep -n "martindale.com" external_directory_extractor.py
      2. 각 URL이 /organization/ 또는 /attorney/ 또는 sitemap 경로인지 확인
    Expected Result: 모든 URL이 robots.txt Disallow에 해당하지 않음
    Failure Indicators: /search/, /api/search 등 disallowed 경로 발견
    Evidence: .sisyphus/evidence/task-6-url-compliance.txt
  ```

  **Commit**: YES (group: 5)
  - Message: `fix(compliance): fix Martindale robots.txt violation, use compliant paths`
  - Files: `external_directory_extractor.py`, `tests/test_martindale_extractor.py`
  - Pre-commit: `pytest tests/test_martindale_extractor.py`

- [x] 7. Practice Areas → Department 매핑 테이블 생성

  **What to do**:
  - `config/practice_department_map.json` 생성
  - 주요 법률 practice areas를 department로 매핑하는 테이블 구축
  - 매핑 구조: `{"practice_area_pattern": "department_name"}`
  - 예시:
    - `"litigation"` → `"Litigation"`
    - `"corporate"`, `"m&a"`, `"mergers"` → `"Corporate"`
    - `"tax"` → `"Tax"`
    - `"real estate"` → `"Real Estate"`
    - `"intellectual property"`, `"patent"`, `"trademark"` → `"Intellectual Property"`
    - `"labor"`, `"employment"` → `"Labor & Employment"`
    - `"bankruptcy"`, `"restructuring"` → `"Restructuring"`
    - `"antitrust"`, `"competition"` → `"Antitrust"`
    - `"environmental"` → `"Environmental"`
    - `"finance"`, `"banking"` → `"Finance"`
  - regex pattern 기반 매칭 지원 (case-insensitive)
  - 최소 20개 이상의 practice area → department 매핑
  - 매핑 우선순위 정의: 여러 practice area가 있을 때 첫 번째 매칭 사용
  - `tests/test_practice_department_map.py` 작성:
    - `test_map_loads_valid_json()` — 파일 로드 성공
    - `test_map_coverage()` — 최소 20개 매핑 존재
    - `test_litigation_mapping()` — "Securities Litigation" → "Litigation"
    - `test_corporate_mapping()` — "Mergers & Acquisitions" → "Corporate"

  **Must NOT do**:
  - NLP/ML 기반 추론하지 않음 (순수 패턴 매핑)
  - enrichment 로직 자체를 수정하지 않음 (적용은 Task 11)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 법률 도메인 지식 필요 + JSON 설계 + 테스트
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6, 8)
  - **Blocks**: Task 11
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `config/known_patterns.json` — 기존 config JSON 파일 형식 참고
  - `attorney_extractor.py:850-927` — `_extract_practices_bs4()` — 어떤 practice area 값들이 추출되는지 파악

  **External References**:
  - 대형 로펌 department 분류 표준: Litigation, Corporate, Tax, IP, Real Estate, Labor, Restructuring 등

  **WHY Each Reference Matters**:
  - 실제 추출되는 practice_area 값을 보면 매핑 테이블이 커버해야 할 범위 파악 가능
  - 기존 config/ JSON 파일 형식을 따르면 코드베이스 일관성 유지

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: 매핑 테이블 유효성 검증
    Tool: Bash
    Preconditions: config/practice_department_map.json 생성됨
    Steps:
      1. python3.12 -c "import json; d=json.load(open('config/practice_department_map.json')); print(f'Mappings: {len(d)}')"
      2. 20개 이상인지 확인
    Expected Result: Mappings: 20 이상
    Failure Indicators: JSON parse error, < 20 mappings
    Evidence: .sisyphus/evidence/task-7-map-verify.txt

  Scenario: 매핑 테스트 통과
    Tool: Bash
    Preconditions: 테스트 작성 완료
    Steps:
      1. pytest tests/test_practice_department_map.py -v
    Expected Result: 모든 테스트 PASSED
    Failure Indicators: FAILED
    Evidence: .sisyphus/evidence/task-7-map-tests.txt
  ```

  **Commit**: YES (group: 6)
  - Message: `feat(extract): add practice→department mapping table`
  - Files: `config/practice_department_map.json`, `tests/test_practice_department_map.py`
  - Pre-commit: `pytest tests/test_practice_department_map.py`

- [x] 8. 누락 10개 펌 probe + site_structures.json 업데이트

  **What to do**:
  - `AmLaw200_2025 Rank_gross revenue_with_websites.xlsx`에서 rank 3-12 펌 식별
  - `site_structures.json`에 없는 펌들의 리스트 확인
  - 각 누락 펌에 대해 `probe_structures.py` 로직을 수동 실행하거나 직접 probe:
    - robots.txt 확인
    - sitemap 존재 여부
    - /people, /attorneys, /lawyers 등 directory path probe
    - SPA 신호 감지
    - bot-wall 감지
  - 결과를 `site_structures.json`에 추가 (기존 형식 동일)
  - `python3.12 -c "import json; d=json.load(open('site_structures.json')); print(len(d))"` → 200

  **Must NOT do**:
  - 기존 190개 펌 re-probe하지 않음
  - bot-protection 감지되면 BOT_PROTECTED로 태깅하고 건너뜀
  - robots.txt Disallow 경로 접근하지 않음

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 웹 프로빙 + JSON 업데이트 + 컴플라이언스 체크
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5, 6, 7)
  - **Blocks**: Task 13
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `site_structures.json` — 기존 항목 형식 (structure_type, confidence, notes, probe_data 등)
  - `probe_structures.py` — 프로빙 로직 전체 (sitemap 감지, directory path probe, SPA 감지)
  - `cache/firm_domain_cache.json` — firm → URL 매핑 (누락 펌의 URL 확인)
  - `url_corrections.json` — 교정된 URL 확인

  **API/Type References**:
  - `AmLaw200_2025 Rank_gross revenue_with_websites.xlsx` — rank 3-12 펌 이름 + URL

  **WHY Each Reference Matters**:
  - site_structures.json의 기존 항목 형식을 정확히 따라야 파이프라인이 새 항목을 인식
  - probe_structures.py를 참고해야 동일한 분류 기준 적용 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: site_structures.json 200개 펌 확인
    Tool: Bash
    Preconditions: probe 완료 + JSON 업데이트
    Steps:
      1. python3.12 -c "import json; d=json.load(open('site_structures.json')); print(f'Total firms: {len(d)}')"
      2. 200 이상인지 확인
    Expected Result: Total firms: 200
    Failure Indicators: < 200, JSON parse error
    Evidence: .sisyphus/evidence/task-8-probe-count.txt

  Scenario: 새 항목 형식 유효성
    Tool: Bash
    Preconditions: JSON 업데이트됨
    Steps:
      1. python3.12 -c "import json; d=json.load(open('site_structures.json')); new_firms=[k for k,v in d.items() if v.get('confidence',0)<1.0]; print(f'New: {len(new_firms)}'); [print(f'  {k}: {v[\"structure_type\"]}') for k,v in d.items() if k in new_firms[:5]]"
    Expected Result: 새 항목들이 올바른 structure_type을 가짐
    Failure Indicators: KeyError, 형식 불일치
    Evidence: .sisyphus/evidence/task-8-probe-format.txt
  ```

  **Commit**: YES (group: 10)
  - Message: `data(probe): add missing firms (ranks 3-12) to site_structures.json`
  - Files: `site_structures.json`
  - Pre-commit: `python3.12 -c "import json; print(len(json.load(open('site_structures.json'))))"` → 200

- [x] 9. Department 추출 로직 강화 (TDD GREEN phase)

  **What to do**:
  - `enrichment.py` / `attorney_extractor.py` / `field_enricher.py`에서 department 추출 강화:
    1. **JSON-LD 추출**: `<script type="application/ld+json">`에서 `"department"` 키 파싱
    2. **CSS 클래스 패턴**: `[class*=department]`, `[data-department]`, `.department`, `.practice-group` 등 선택자 추가
    3. **heading synonym 확장**: parser_sections.py의 SECTION_SYNONYMS에 "department", "group", "practice group", "team" 등 추가
    4. **오염 필터링**: nav/menu/footer에서 온 department 텍스트 필터링 강화
  - `validators.py`에서 department 검증 로직 보강:
    - 너무 긴 값 필터 (>100자)
    - URL/HTML 태그 잔여물 필터
    - 중복 제거
  - `pytest tests/test_department_extraction.py` → 전체 PASS 확인

  **Must NOT do**:
  - 펌별 하드코딩 CSS 선택자 추가하지 않음 (generic 패턴만)
  - NLP 기반 추론하지 않음
  - practice_areas → department 매핑은 여기서 안 함 (Task 11에서)
  - AttorneyProfile 필드 정의 변경하지 않음

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 다중 파일 수정 (enrichment + extractor + parser + validators) + TDD GREEN
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 10, 11)
  - **Blocks**: Tasks 11, 12
  - **Blocked By**: Task 4

  **References**:

  **Pattern References**:
  - `attorney_extractor.py:775-801` — `_extract_departments_bs4()` 현재 구현 (강화 대상)
  - `attorney_extractor.py:850-927` — `_extract_practices_bs4()` 참고 (practice_areas 추출 방식을 department에 적용)
  - `enrichment.py:165-200` — enrichment cascade 순서 (JSON-LD → embedded state → heading → fallback)
  - `parser_sections.py` — SECTION_SYNONYMS dict (heading synonym 추가 위치)
  - `validators.py` — `validate_departments()` 함수 (있다면)
  - `tests/fixtures/html/department_concat_blob.html` — 기존 오염 fixture

  **Test References**:
  - `tests/test_department_extraction.py` — Task 4에서 작성한 failing 테스트 (이것을 PASS 시켜야 함)

  **WHY Each Reference Matters**:
  - `_extract_departments_bs4()` 를 직접 읽어야 어디를 강화할지 판단 가능
  - `_extract_practices_bs4()` 의 패턴을 참고하면 department에도 유사 전략 적용 가능
  - enrichment cascade 순서를 이해해야 JSON-LD 추출이 heading보다 먼저 실행되도록 배치 가능

  **Acceptance Criteria**:

  **If TDD (tests enabled):**
  - [ ] `pytest tests/test_department_extraction.py -v` → 전체 PASS (GREEN phase)
  - [ ] 기존 테스트 regression 없음: `pytest tests/ -v` → 0 failures

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Department 테스트 GREEN 전환
    Tool: Bash
    Preconditions: Task 4 테스트가 RED 상태
    Steps:
      1. pytest tests/test_department_extraction.py -v
      2. 모든 테스트가 PASSED인지 확인
    Expected Result: 5개 이상 테스트 전체 PASSED
    Failure Indicators: FAILED 존재
    Evidence: .sisyphus/evidence/task-9-department-green.txt

  Scenario: 기존 테스트 regression 없음
    Tool: Bash
    Preconditions: department 로직 수정 완료
    Steps:
      1. pytest tests/ -v --tb=short
    Expected Result: 0 failures (기존 테스트 포함 전체 통과)
    Failure Indicators: 기존 테스트 FAILED
    Evidence: .sisyphus/evidence/task-9-no-regression.txt
  ```

  **Commit**: YES (group: 7)
  - Message: `feat(extract): enhance department extraction logic (TDD green)`
  - Files: `enrichment.py`, `parser_sections.py`, `field_enricher.py`, `validators.py`, `attorney_extractor.py`
  - Pre-commit: `pytest tests/test_department_extraction.py`

- [x] 10. Industries 추출 로직 강화 (TDD GREEN phase)

  **What to do**:
  - `enrichment.py` / `attorney_extractor.py` / `field_enricher.py`에서 industries 추출 강화:
    1. **JSON-LD 추출**: `"knowsAbout"` 키에서 industry 정보 파싱
    2. **CSS 클래스 패턴**: `[class*=industr]`, `[class*=sector]`, `[data-industry]` 선택자 추가
    3. **heading synonym 확장**: SECTION_SYNONYMS에 "industries", "industry focus", "sectors", "markets", "industry experience" 등 추가
    4. **sidebar/aside 구조**: `<aside>`, `.sidebar` 안의 industry 리스트 추출
  - `validators.py`에서 industries 검증 보강:
    - practice_areas와 구분 (동일 값 필터링)
    - sentinel 로직 유지: 진짜 없으면 `["no industry field"]`
  - `pytest tests/test_industries_extraction.py` → 전체 PASS 확인

  **Must NOT do**:
  - 펌별 하드코딩하지 않음
  - bio 텍스트에서 NLP로 industry 추론하지 않음
  - practice_areas 값을 industry로 복사하지 않음

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 다중 파일 수정 + TDD GREEN + industries는 가장 약한 필드
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 9, 11)
  - **Blocks**: Task 12
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `attorney_extractor.py:929-933` — `_extract_industries_bs4()` 현재 구현 (매우 짧음, 핵심 강화 대상)
  - `attorney_extractor.py:850-927` — `_extract_practices_bs4()` (유사 구조 참고)
  - `parser_sections.py` — SECTION_SYNONYMS dict ("industries" 관련 synonym 확인/추가)
  - `enrichment.py` — JSON-LD 추출 cascade에 industries 추가 위치

  **Test References**:
  - `tests/test_industries_extraction.py` — Task 5에서 작성한 failing 테스트

  **WHY Each Reference Matters**:
  - `_extract_industries_bs4()` 가 5줄 미만 → 거의 새로 작성 수준의 강화 필요
  - practice_areas 추출 패턴을 참고하면 유사한 CSS/heading 전략 적용 가능

  **Acceptance Criteria**:

  **If TDD (tests enabled):**
  - [ ] `pytest tests/test_industries_extraction.py -v` → 전체 PASS
  - [ ] 기존 테스트 regression 없음

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Industries 테스트 GREEN 전환
    Tool: Bash
    Preconditions: Task 5 테스트가 RED 상태
    Steps:
      1. pytest tests/test_industries_extraction.py -v
    Expected Result: 5개 이상 테스트 전체 PASSED
    Failure Indicators: FAILED 존재
    Evidence: .sisyphus/evidence/task-10-industries-green.txt

  Scenario: Industries vs Practice Areas 구분
    Tool: Bash
    Preconditions: 로직 수정 완료
    Steps:
      1. pytest tests/test_industries_extraction.py::test_industries_vs_practice_areas -v
    Expected Result: PASSED (industries와 practice_areas가 구분됨)
    Failure Indicators: FAILED
    Evidence: .sisyphus/evidence/task-10-industries-vs-practices.txt
  ```

  **Commit**: YES (group: 8)
  - Message: `feat(extract): enhance industries extraction logic (TDD green)`
  - Files: `enrichment.py`, `parser_sections.py`, `field_enricher.py`, `attorney_extractor.py`, `validators.py`
  - Pre-commit: `pytest tests/test_industries_extraction.py`

- [x] 11. Practice → Department 매핑 적용

  **What to do**:
  - `config/practice_department_map.json`을 enrichment pipeline에 통합
  - `field_enricher.py` 또는 `enrichment.py`에 매핑 로직 추가:
    1. 프로필 enrichment 완료 후, department가 비어있는 경우에만 매핑 적용
    2. practice_areas 리스트에서 매핑 테이블 매칭 → department 추론
    3. 매핑은 fallback으로만 작동 (direct extraction이 우선)
  - 매핑된 department에 `"(inferred)"` 마커 추가하여 직접 추출과 구분
  - `validators.py`에서 "(inferred)" 마커된 department도 유효하게 처리
  - 테스트:
    - `test_department_inferred_from_practice_areas()` — practice area "Securities Litigation" → department ["Litigation (inferred)"]
    - `test_direct_department_not_overridden()` — 직접 추출된 department가 있으면 매핑 안 함
    - `test_no_practice_area_no_inference()` — practice_areas도 비어있으면 department 빈 상태 유지

  **Must NOT do**:
  - 직접 추출된 department를 매핑으로 덮어쓰지 않음
  - NLP 기반 추론하지 않음

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 매핑 통합 + fallback 로직 + 테스트
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 9, 10)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 7, 9

  **References**:

  **Pattern References**:
  - `config/practice_department_map.json` — Task 7에서 생성한 매핑 테이블
  - `field_enricher.py` — 기존 field enrichment 로직 (매핑 삽입 위치)
  - `enrichment.py` — enrichment cascade 흐름 (매핑이 마지막 fallback으로 동작해야 함)

  **WHY Each Reference Matters**:
  - field_enricher.py의 기존 enrichment 흐름을 이해해야 매핑을 올바른 위치에 삽입 가능
  - 매핑 테이블 형식을 알아야 로딩 + 매칭 로직 구현 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: 매핑 fallback 동작 확인
    Tool: Bash
    Preconditions: 매핑 로직 통합 완료
    Steps:
      1. pytest tests/test_practice_department_map.py -v
      2. pytest tests/test_department_extraction.py -v
    Expected Result: 모든 테스트 PASSED
    Failure Indicators: FAILED
    Evidence: .sisyphus/evidence/task-11-mapping-applied.txt

  Scenario: 직접 추출 우선 확인
    Tool: Bash
    Preconditions: 매핑 로직 통합 완료
    Steps:
      1. pytest tests/test_practice_department_map.py::test_direct_department_not_overridden -v
    Expected Result: PASSED
    Failure Indicators: FAILED (직접 추출이 매핑으로 덮어씌워짐)
    Evidence: .sisyphus/evidence/task-11-direct-priority.txt
  ```

  **Commit**: YES (group: 9)
  - Message: `feat(extract): apply practice→department mapping in enrichment`
  - Files: `enrichment.py`, `field_enricher.py`, `tests/test_practice_department_map.py`
  - Pre-commit: `pytest tests/ -v`

- [x] 12. Post-change 3개 펌 재추출 + Baseline 비교

  **What to do**:
  - Task 3과 동일한 3개 펌 재추출:
    - `python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 10`
    - `python3.12 find_attorney.py --debug-firm "Davis Polk" --max-profiles 10`
    - `python3.12 find_attorney.py --debug-firm "Jones Day" --max-profiles 10`
  - 결과 JSONL에서 8개 필드 sentinel/빈값 비율 재계산
  - Task 3 baseline 결과와 비교:
    - department sentinel rate: baseline vs post-change
    - industries sentinel rate: baseline vs post-change
    - 다른 6개 필드: regression 없는지 확인
  - 비교 결과를 `.sisyphus/evidence/task-12-comparison.json`에 저장
  - department sentinel rate < 40%, industries sentinel rate < 50% 목표 확인

  **Must NOT do**:
  - 추출 로직 추가 수정하지 않음 (측정만)
  - 결과가 목표 미달이어도 코드 수정은 여기서 안 함

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 파이프라인 실행 + 데이터 비교 분석
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (sequential with Task 13)
  - **Blocks**: Task 13
  - **Blocked By**: Tasks 3, 9, 10, 11

  **References**:

  **Pattern References**:
  - `.sisyphus/evidence/task-3-baseline.json` — baseline 측정 결과 (비교 대상)
  - `find_attorney.py` — CLI 인수

  **WHY Each Reference Matters**:
  - baseline 결과를 읽어야 비교 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Post-change 비교 결과 생성
    Tool: Bash
    Preconditions: Tasks 9, 10, 11 완료
    Steps:
      1. python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 10
      2. python3.12 find_attorney.py --debug-firm "Davis Polk" --max-profiles 10
      3. python3.12 find_attorney.py --debug-firm "Jones Day" --max-profiles 10
      4. baseline과 비교하여 sentinel 비율 변화 확인
    Expected Result: department/industries sentinel 비율 감소, 다른 필드 regression 없음
    Failure Indicators: sentinel 비율 증가, 다른 필드 regression
    Evidence: .sisyphus/evidence/task-12-comparison.json

  Scenario: 목표 달성 확인
    Tool: Bash
    Preconditions: 비교 완료
    Steps:
      1. python3.12 -c "import json; d=json.load(open('.sisyphus/evidence/task-12-comparison.json')); print(f'dept: {d[\"post\"][\"department_sentinel_rate\"]}, ind: {d[\"post\"][\"industries_sentinel_rate\"]}')"
    Expected Result: department < 0.4, industries < 0.5
    Failure Indicators: 목표 미달
    Evidence: .sisyphus/evidence/task-12-goal-check.txt
  ```

  **Commit**: NO (evidence만)

- [ ] 13. 200개 펌 전체 추출 실행

  **What to do**:
  - 전체 추출 실행 전 환경 확인:
    - `site_structures.json` → 200개 펌 확인
    - `pytest tests/ -v` → 0 failures 확인
    - 디스크 공간 확인 (최소 5GB 여유)
  - `python3.12 find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx"` 실행
  - 또는 find_attorney.py의 전체 실행 모드 사용
  - 실행 중 모니터링:
    - 중간 JSONL 파일 증분 확인 (incremental output)
    - crash 감시
    - rate limit / 429 에러 감시
  - 완료 후 결과 확인:
    - Excel + JSONL 파일 존재
    - attorney 수 ≥ 30,000
    - 200개 펌 모두 처리됨 (BOT_PROTECTED 포함 Martindale 폴백)
    - 필드별 전체 sentinel 비율 계산 + 저장
  - 런타임 기대: ~10-15시간

  **Must NOT do**:
  - 추출 로직 수정하지 않음 (실행만)
  - rate limit 우회하지 않음
  - BOT_PROTECTED 사이트 직접 접근하지 않음

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 장시간 실행 모니터링 + 결과 분석
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (after Task 12)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: Tasks 6, 8, 12

  **References**:

  **Pattern References**:
  - `find_attorney.py` — 전체 실행 모드 (파일명 인수)
  - `site_structures.json` — 200개 펌 확인
  - `outputs/` — 출력 위치

  **WHY Each Reference Matters**:
  - find_attorney.py의 전체 실행 모드를 정확히 알아야 올바르게 실행
  - 출력 파일 위치를 알아야 검증 가능

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: 전체 추출 완료 + 결과 파일 존재
    Tool: Bash
    Preconditions: 모든 사전 작업 완료
    Steps:
      1. ls outputs/attorneys_*.xlsx
      2. ls outputs/attorneys_*.jsonl
      3. python3.12 -c "count=0;\nwith open('outputs/최신파일.jsonl') as f:\n  for line in f: count+=1\nprint(f'Attorneys: {count}')"
    Expected Result: Excel + JSONL 파일 존재, attorney 수 ≥ 30,000
    Failure Indicators: 파일 없음, attorney 수 < 30,000
    Evidence: .sisyphus/evidence/task-13-full-run-result.txt

  Scenario: 200개 펌 모두 처리됨
    Tool: Bash
    Preconditions: 추출 완료
    Steps:
      1. JSONL에서 unique firm 수 카운트
      2. 200에 근접하는지 확인
    Expected Result: unique firm ≥ 190 (일부 AUTH_REQUIRED/UNKNOWN 제외 가능)
    Failure Indicators: < 150 firms
    Evidence: .sisyphus/evidence/task-13-firm-coverage.txt

  Scenario: BOT_PROTECTED 펌 Martindale 폴백 확인
    Tool: Bash
    Preconditions: 추출 완료
    Steps:
      1. JSONL에서 "Jones Day" 펌의 attorney 존재 확인
      2. extraction_status 확인
    Expected Result: Jones Day attorney ≥ 1, Martindale 소스
    Failure Indicators: Jones Day attorney = 0
    Evidence: .sisyphus/evidence/task-13-bot-protected-fallback.txt
  ```

  **Commit**: YES (group: 11)
  - Message: `feat(pipeline): full 200-firm extraction run`
  - Files: `outputs/attorneys_<timestamp>.xlsx`, `outputs/attorneys_<timestamp>.jsonl`
  - Pre-commit: 출력 파일 존재 확인

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run linter + `pytest tests/ -v`. Review all changed files for: `as any`/type ignores, empty catches, print() in library modules, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (data/result/item/temp). Verify `python3.12` used everywhere (not `python`).
  Output: `Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Run `python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 5` and verify all 8 fields populated. Run same for 1 BOT_PROTECTED firm to verify Martindale fallback. Check output Excel file opens and has correct columns. Check JSONL is valid JSON per line. Verify no /search/ URLs in any HTTP requests (check debug logs).
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance: no firm-specific code, no NLP, no new directories, no /search/ path. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| # | Message | Key Files | Pre-commit Check |
|---|---------|-----------|-----------------|
| 1 | `build(test): set up pytest infrastructure with conftest and fixtures` | pyproject.toml, tests/conftest.py | `pytest tests/ --collect-only` |
| 2 | `refactor(test): convert standalone tests to pytest format` | tests/test_*.py | `pytest tests/ -v` |
| 3 | `test(extract): add failing tests for department extraction (TDD red)` | tests/test_department_extraction.py, fixtures | `pytest tests/test_department_extraction.py` → FAIL |
| 4 | `test(extract): add failing tests for industries extraction (TDD red)` | tests/test_industries_extraction.py, fixtures | `pytest tests/test_industries_extraction.py` → FAIL |
| 5 | `fix(compliance): fix Martindale robots.txt violation, use compliant paths` | external_directory_extractor.py, tests/test_martindale.py | `pytest tests/test_martindale.py` |
| 6 | `feat(extract): add practice→department mapping table` | config/practice_department_map.json | `python3.12 -c "import json; ..."` |
| 7 | `feat(extract): enhance department extraction logic (TDD green)` | enrichment.py, parser_sections.py, field_enricher.py, validators.py | `pytest tests/test_department_extraction.py` → PASS |
| 8 | `feat(extract): enhance industries extraction logic (TDD green)` | enrichment.py, parser_sections.py, field_enricher.py | `pytest tests/test_industries_extraction.py` → PASS |
| 9 | `feat(extract): apply practice→department mapping in enrichment` | enrichment.py, field_enricher.py | `pytest tests/ -v` |
| 10 | `data(probe): add 10 missing firms (ranks 3-12) to site_structures.json` | site_structures.json | `python3.12 -c "..."` → 200 |
| 11 | `feat(pipeline): full 200-firm extraction run` | outputs/*.xlsx, outputs/*.jsonl | file exists + row count |

---

## Success Criteria

### Verification Commands
```bash
# pytest 전체 통과
pytest tests/ -v --tb=short  # Expected: 0 failures

# Martindale 컴플라이언스
grep -r "/search/" external_directory_extractor.py  # Expected: 0 matches

# site_structures.json 완전성
python3.12 -c "import json; d=json.load(open('site_structures.json')); print(len(d))"  # Expected: 200

# 단일 펌 테스트 (SITEMAP_XML)
python3.12 find_attorney.py --debug-firm "Kirkland" --max-profiles 5  # Expected: 8 fields populated

# 출력 파일 존재
ls outputs/attorneys_*.xlsx  # Expected: file exists
ls outputs/attorneys_*.jsonl  # Expected: file exists
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] department sentinel rate < 40%
- [ ] industries sentinel rate < 50%
- [ ] 200 firms processed
- [ ] No robots.txt violations
