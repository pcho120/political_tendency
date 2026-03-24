# Output Quality Fix — Attorney Extraction Pipeline

## TL;DR

> **Quick Summary**: `run_pipeline.py` 아웃풋의 4가지 데이터 품질 문제를 수정한다. Quinn Emanuel SPA 재분류, Education 필드 footer 노이즈 제거, 이름 없는 프로필 출력 제외, Offices 공백 원인 진단 후 수정.
>
> **Deliverables**:
> - `site_structures.json`: Quinn Emanuel 구조 타입 수정
> - `parser_sections.py`: footer 컨테이너 감지로 Education 노이즈 제거
> - `run_pipeline.py`: 이름 없는 프로필 필터
> - `validators.py` 또는 enrichment 단계: Offices 공백 원인에 따라 조건부 수정
>
> **Estimated Effort**: Medium
> **Parallel Execution**: NO — 순서 의존성 있음 (Issue 2 → 4 → 1 → 3)
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4(conditional)

---

## Context

### Original Request
테스트 실행 결과 (`attorneys_2026-03-24T17-48-34.xlsx`, 1591명) 에서 발견된 품질 문제 수정.

### Interview Summary
**Key Discussions**:
- Offices 78% 공백, Departments 90% 공백, Attorney Name 15% 누락
- Quinn Emanuel 20명 전원 이름 없음 + 전사 오피스 목록이 개인 오피스로 잘못 입력
- Education 필드에 footer/nav/copyright 텍스트 노이즈 포함
- Extraction Status: 94% PARTIAL, 4% SUCCESS, 2% FAILED

**Research Findings**:
- `run_pipeline.py:309` — 이름 없는 프로필도 그냥 append됨 (필터 없음)
- `site_structures.json:674` — Quinn Emanuel이 `HTML_DIRECTORY_FLAT` (confidence 0.7)로 분류됨 → JS 렌더링 필요한데 Playwright 안 씀
- `parser_sections.py` — `_collect_content_after`가 `find_all_next()`로 전체 DOM 순회 → footer 컨테이너 감지 없음
- `validators.py:258-330` — `validate_offices`는 빈 리스트를 받으면 `NOT_FOUND` 반환 → 추출 단계가 문제일 가능성 높음

### Metis Review
**Identified Gaps** (addressed):
- Issue 3 (Offices) 수정 방향이 틀렸을 가능성 → 진단 먼저, 코드 수정은 조건부
- 이름 필터 삽입 위치가 잘못되면 통계 왜곡 → pre-write 위치에만 삽입
- footer 감지를 텍스트 패턴으로 하면 취약 → HTML 구조 기반 감지로 변경
- Issue 2(Quinn Emanuel) 먼저 수정해야 Issue 1(이름 필터) 측정이 정확해짐
- Playwright 미설치 환경에서는 Issue 2 fix가 효과 없음 → 환경 확인 포함

---

## Work Objectives

### Core Objective
4가지 데이터 품질 문제를 순서대로 수정하여 아웃풋의 정확도를 높인다.

### Concrete Deliverables
- `site_structures.json` — Quinn Emanuel `structure_type` 수정
- `parser_sections.py` — `_collect_content_after` footer 감지 추가
- `run_pipeline.py` — 이름 없는 프로필 pre-write 필터
- `validators.py` or enrichment — Offices 진단 결과에 따라 조건부 수정

### Definition of Done
- [ ] `python3.12 run_pipeline.py --firms "quinn" --max-profiles 5` → ≥3명 이름 있음
- [ ] Gibson Dunn Education 필드에 `©` 또는 `Follow us on Twitter` 없음
- [ ] 아웃풋 Excel에 `Attorney Name` 빈 행 0개
- [ ] Offices 공백 원인 진단 완료 + 진단 결과에 맞는 수정 적용

### Must Have
- Issue 2 → Issue 4 → Issue 1 → Issue 3(조건부) 순서 준수
- 이름 필터는 `filter_us_attorneys` 이후 pre-write 위치에만 삽입
- footer 감지는 HTML 구조 기반 (`<footer>` 태그 + class/id 패턴) — 텍스트 매칭 금지
- Issue 3 수정 전 반드시 진단 실행

### Must NOT Have (Guardrails)
- `result.profiles.append(profile)` 위치(line 309)에 이름 필터 삽입 금지
- `site_structures.json`에서 Quinn Emanuel 외 다른 회사 수정 금지
- `_collect_content_after` 구조 리팩토링 금지 — 추가(additive) 변경만
- `validate_offices` 수정 전 진단 없이 코드 변경 금지
- Practice Areas, Bar Admissions 등 다른 필드 footer 정리 금지 (이번 스코프 아님)
- 빈 title/department로 인한 추가 필터 금지 — `full_name` 필터만
- footer 클래스 감지에 `contact` 패턴 포함 금지 (합법적인 Contact Information 섹션 손상 위험)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO (pytest 없음)
- **Automated tests**: 각 이슈별 standalone 검증 스크립트 사용
- **Framework**: `python3.12 script.py` 직접 실행

### QA Policy
- 모든 검증은 Bash + `python3.12`로 직접 실행
- Evidence는 `.sisyphus/evidence/` 에 저장

---

## Execution Strategy

### Parallel Execution Waves

```
Wave ONLY (순서 의존성으로 Sequential):
├── Task 1: Quinn Emanuel SPA 재분류 (site_structures.json) [quick]
├── Task 2: footer 감지 추가 (parser_sections.py) [unspecified-high]
├── Task 3: 이름 없는 프로필 필터 (run_pipeline.py) [quick]
└── Task 4: Offices 진단 + 조건부 수정 [unspecified-high]

Wave FINAL:
└── Task F1: 전체 QA 검증
```

### Dependency Matrix
- **Task 1**: 없음 — 즉시 시작 가능
- **Task 2**: Task 1 이후 권장 (Quinn Emanuel 재분류 후 parser 효과 동시 검증 가능)
- **Task 3**: Task 1, Task 2 이후 — Quinn Emanuel 이름이 회복된 후 필터 측정해야 정확
- **Task 4**: Task 3 이후 — 진단 먼저, 코드는 조건부

---

## TODOs

- [x] 1. `site_structures.json`: Quinn Emanuel 구조 타입을 SPA_OTHER로 변경

  **What to do**:
  - `site_structures.json`에서 Quinn Emanuel 엔트리 찾기 (lines 648-676 근처)
  - `"structure_type": "HTML_DIRECTORY_FLAT"` → `"structure_type": "SPA_OTHER"` 로 변경
  - 그 외 모든 필드 (confidence, notes, directory_path_found 등) 절대 수정 금지
  - Playwright 설치 확인: `python3.12 -c "from playwright.sync_api import sync_playwright; print('OK')"`

  **Must NOT do**:
  - Quinn Emanuel 외 다른 회사 엔트리 수정 금지
  - `confidence`, `notes`, `directory_path_found` 등 다른 필드 수정 금지

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: JSON 파일 한 줄 수정

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave ONLY (첫 번째)
  - **Blocks**: Task 2, 3, 4
  - **Blocked By**: None

  **References**:
  - `site_structures.json:648-676` — Quinn Emanuel 엔트리
  - `run_pipeline.py:172-196` — `load_firms()` 에서 structure_type 사용 방식
  - `discovery.py` — `lookup_structure()` 함수로 structure_type 기반 분기

  **Acceptance Criteria**:

  **QA Scenarios**:

  ```
  Scenario: Playwright 가용성 확인
    Tool: Bash
    Steps:
      1. python3.12 -c "from playwright.sync_api import sync_playwright; print('OK')" 2>&1
    Expected Result: "OK" 출력 (없으면 "ModuleNotFoundError" — 이 경우 Quinn Emanuel fix는 SPA_OTHER로 분류만 하고 Playwright 없이는 효과 제한적임을 기록)
    Evidence: .sisyphus/evidence/task-1-playwright-check.txt

  Scenario: Quinn Emanuel 재분류 후 재실행
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "quinn" --max-profiles 5 --verbose 2>&1
      2. 최신 outputs/*.xlsx 에서 quinn 행 확인:
         python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         ws = openpyxl.load_workbook(f, read_only=True).active
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         quinn = [r for r in rows if r[0] and 'quinn' in r[0].lower()]
         print(f'Quinn rows: {len(quinn)}')
         for r in quinn[:3]: print(f'  name={r[1]}, office={r[3]}')
         "
    Expected Result: quinn 행 존재 + 이름 있는 행 ≥1 (Playwright 있으면 ≥3)
    Failure Indicators: 모든 quinn 행 name=None
    Evidence: .sisyphus/evidence/task-1-quinn-rerun.txt
  ```

  **Commit**: YES
  - Message: `fix: reclassify Quinn Emanuel as SPA_OTHER in site_structures.json`
  - Files: `site_structures.json`

---

- [x] 2. `parser_sections.py`: `_collect_content_after`에 구조적 footer 감지 추가

  **What to do**:
  - `parser_sections.py`에서 `_collect_content_after` 함수 찾기
  - 함수 상단 (모듈 레벨)에 두 상수 추가:
    ```python
    _FOOTER_CONTAINER_NAMES = frozenset({"footer"})
    _FOOTER_CONTAINER_CLASSES = frozenset({
        "footer", "site-footer", "global-footer",
        "copyright-bar", "bottom-bar", "page-footer",
    })
    ```
  - `find_all_next()` 루프 안에서 각 sibling 처리 전에 footer 감지 조건 추가:
    - `sibling.name in _FOOTER_CONTAINER_NAMES` → break
    - `sibling`의 class 또는 id가 `_FOOTER_CONTAINER_CLASSES`의 어떤 값이라도 포함하면 → break
  - 기존 heading-level stop 로직(`sib_level <= stop_level`) 절대 수정 금지
  - 함수 구조 리팩토링 금지 — 추가 조건만 삽입

  **Must NOT do**:
  - copyright 텍스트, "Follow us on Twitter" 등 텍스트 매칭 방식 금지
  - `contact` 패턴을 footer class에 포함 금지 (합법적인 Contact Information 섹션 손상)
  - `sib_level <= stop_level` 로직 변경 금지
  - `_collect_content_after` 외 다른 함수 수정 금지
  - Practice Areas, Bar Admissions 등 다른 필드에 영향 없음을 확인할 것

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 공유 파서 수정 — 부작용 범위가 넓어 신중한 분석 필요

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave ONLY (두 번째)
  - **Blocks**: Task 3
  - **Blocked By**: Task 1 (Quinn Emanuel 재분류 후 동시 검증 가능)

  **References**:
  - `parser_sections.py:218-226` — `_collect_content_after` 함수 (find_all_next 루프)
  - `parser_sections.py:284-369` — `parse_sections` 전체 흐름
  - `enrichment.py:389-394` — `_extract_from_section_map` 호출부 (parser 사용 방식)
  - Gibson Dunn profile URL 예시: `https://www.gibsondunn.com/lawyer/kennedy-david/`

  **Acceptance Criteria**:

  **QA Scenarios**:

  ```
  Scenario: Gibson Dunn Education 노이즈 제거 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "gibson dunn" --max-profiles 5 2>&1
      2. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         ws = openpyxl.load_workbook(f, read_only=True).active
         headers = [c.value for c in next(ws.iter_rows(max_row=1))]
         edu_idx = headers.index('Education')
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         noisy = [r for r in rows if r[edu_idx] and ('©' in str(r[edu_idx]) or 'Follow us on Twitter' in str(r[edu_idx]))]
         clean = [r for r in rows if r[edu_idx] and 'JD' in str(r[edu_idx])]
         print(f'Noisy: {len(noisy)}, Clean JD rows: {len(clean)}')
         assert len(noisy) == 0, f'FAIL: {len(noisy)} noisy education rows'
         print('PASS')
         " 2>&1
    Expected Result: noisy=0, clean JD rows ≥1
    Failure Indicators: "©" 또는 "Follow us on Twitter" 포함된 education 행 존재
    Evidence: .sisyphus/evidence/task-2-education-noise.txt

  Scenario: 다른 필드 손상 없음 확인 (regression)
    Tool: Bash
    Steps:
      1. 동일 outputs 파일에서 practice_areas, bar_admissions 확인:
         python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         ws = openpyxl.load_workbook(f, read_only=True).active
         headers = [c.value for c in next(ws.iter_rows(max_row=1))]
         pa_idx = headers.index('Practice Areas')
         ba_idx = headers.index('Bar Admissions')
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         pa_noisy = [r for r in rows if r[pa_idx] and '©' in str(r[pa_idx])]
         ba_noisy = [r for r in rows if r[ba_idx] and '©' in str(r[ba_idx])]
         print(f'Practice Areas noisy: {len(pa_noisy)}, Bar Admissions noisy: {len(ba_noisy)}')
         pa_filled = [r for r in rows if r[pa_idx] and str(r[pa_idx]).strip()]
         print(f'Practice Areas filled: {len(pa_filled)}/{len(rows)} (should be similar to before fix)')
         print('PASS')
         " 2>&1
    Expected Result: practice_areas, bar_admissions에 © 없음, 채워진 행 수 유지
    Evidence: .sisyphus/evidence/task-2-regression.txt
  ```

  **Commit**: YES
  - Message: `fix: add structural footer detection to parser_sections._collect_content_after`
  - Files: `parser_sections.py`

---

- [x] 3. `run_pipeline.py`: 이름 없는 프로필을 pre-write 위치에서 필터

  **What to do**:
  - `run_pipeline.py`에서 `filter_us_attorneys` 호출 직후 (line ~696), `_write_excel` 호출 직전에 이름 필터 추가:
    ```python
    before_name_count = len(all_profiles)
    all_profiles = [p for p in all_profiles if p.full_name and p.full_name.strip()]
    dropped = before_name_count - len(all_profiles)
    if dropped:
        log.info(f"Name filter: {dropped} profiles dropped (empty full_name)")
    ```
  - 삽입 위치: `filter_us_attorneys` 블록 바로 다음, `if not args.discover_only and all_profiles:` 블록 바로 앞
  - `result.profiles.append(profile)` (line 309) 위치에는 절대 삽입 금지

  **Must NOT do**:
  - line 309 (`result.profiles.append`) 근처에 이름 필터 삽입 금지
  - `extraction_status`, `calculate_status`, `missing_fields` 로직 수정 금지
  - title 이 없는 프로필 필터 추가 금지 — full_name 필터만

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 4줄 추가, 삽입 위치만 정확하면 됨

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave ONLY (세 번째)
  - **Blocks**: Task 4, F1
  - **Blocked By**: Task 1, Task 2 (Quinn Emanuel 회복 후 측정해야 정확)

  **References**:
  - `run_pipeline.py:690-704` — `filter_us_attorneys` 이후 `_write_excel` 직전 구간
  - `run_pipeline.py:309-312` — 삽입 금지 위치 (확인용)
  - `attorney_extractor.py:113-126` — `AttorneyProfile.full_name` 필드 정의

  **Acceptance Criteria**:

  **QA Scenarios**:

  ```
  Scenario: 이름 없는 행 0개 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "skadden" "cravath" "white and case" --max-profiles 5 2>&1
      2. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         ws = openpyxl.load_workbook(f, read_only=True).active
         headers = [c.value for c in next(ws.iter_rows(max_row=1))]
         name_idx = headers.index('Attorney Name')
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         blank = [r for r in rows if not r[name_idx] or not str(r[name_idx]).strip()]
         print(f'Total rows: {len(rows)}, Blank name rows: {len(blank)}')
         assert len(blank) == 0, f'FAIL: {len(blank)} blank-name rows found'
         print('PASS')
         " 2>&1
    Expected Result: Blank name rows = 0
    Failure Indicators: blank 행 존재
    Evidence: .sisyphus/evidence/task-3-blank-names.txt

  Scenario: 파이프라인 로그에 Name filter 메시지 확인
    Tool: Bash
    Steps:
      1. 위 실행 로그에서 "Name filter" 메시지 확인:
         python3.12 run_pipeline.py --firms "skadden" --max-profiles 5 2>&1 | grep -i "name filter\|dropped"
    Expected Result: "Name filter: N profiles dropped" 또는 dropped=0 메시지 출력
    Evidence: .sisyphus/evidence/task-3-log-message.txt
  ```

  **Commit**: YES
  - Message: `fix: filter nameless profiles before Excel/JSONL output in run_pipeline`
  - Files: `run_pipeline.py`

---

- [x] 4. Offices 공백 원인 진단 + 조건부 수정

  **What to do**:

  ### Step A: 진단 실행 (MANDATORY — 코드 수정 전 반드시 먼저)
  ```bash
  python3.12 -c "
  import json
  from collections import Counter
  data = [json.loads(l) for l in open('outputs/attorneys_2026-03-24T17-48-34.jsonl')]
  reasons = Counter(
      p.get('diagnostics', {}).get('offices_reason', 'no_diagnostic')
      for p in data if not p.get('offices')
  )
  print('Offices empty reason distribution:')
  for reason, count in reasons.most_common():
      print(f'  {reason}: {count}')
  " 2>&1 | tee .sisyphus/evidence/task-4-offices-diagnosis.txt
  ```

  ### Step B: 진단 결과에 따른 분기

  **만약 다수가 `NOT_FOUND` (추출 단계 실패)**:
  - `enrichment.py:622-705` CSS class 기반 오피스 추출 단계 보강 필요
  - 가장 많이 실패하는 회사 5개 선별 → 해당 회사 HTML에서 오피스 정보가 어떤 selector에 있는지 확인
  - 새 selector 패턴 추가 (기존 패턴 목록에 append)
  - `validators.py` 수정 금지

  **만약 다수가 `VALIDATION_REJECTED` (validator 거부)**:
  - `validators.py:258-330` `validate_offices` 함수 확인
  - 거부된 샘플 값 5개 추출 → 어떤 패턴이 왜 거부됐는지 확인
  - 해당 패턴에 맞게 validator 규칙 완화
  - 기존에 통과하던 패턴 regression 테스트 포함

  **Must NOT do**:
  - 진단 없이 코드 수정 금지
  - 진단 결과와 무관한 파일 수정 금지 (NOT_FOUND면 validators.py 수정 금지, VALIDATION_REJECTED면 CSS selector 수정 불필요)
  - 오피스 alias 매핑 추가 금지 (예: "Silicon Valley → Palo Alto") — 새 기능이므로 스코프 밖

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 진단 결과에 따라 수정 방향이 완전히 달라지는 조건부 태스크 — 판단력 필요

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave ONLY (마지막)
  - **Blocks**: F1
  - **Blocked By**: Task 3

  **References**:
  - `enrichment.py:622-705` — CSS class 기반 offices 추출 (NOT_FOUND 경우 수정 위치)
  - `validators.py:258-330` — `validate_offices` 함수 (VALIDATION_REJECTED 경우 수정 위치)
  - `enrichment.py:423-430` — validator 호출 및 결과 적용 위치
  - `outputs/attorneys_2026-03-24T17-48-34.jsonl` — 진단용 데이터 파일

  **Acceptance Criteria**:

  **QA Scenarios**:

  ```
  Scenario: 진단 파일 생성 확인
    Tool: Bash
    Steps:
      1. cat .sisyphus/evidence/task-4-offices-diagnosis.txt
    Expected Result: reason distribution 출력 포함 (NOT_FOUND/VALIDATION_REJECTED 분포)
    Evidence: .sisyphus/evidence/task-4-offices-diagnosis.txt

  Scenario: 수정 후 offices 공백률 개선 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "gibson dunn" "skadden" "latham" --max-profiles 10 2>&1
      2. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         ws = openpyxl.load_workbook(f, read_only=True).active
         headers = [c.value for c in next(ws.iter_rows(max_row=1))]
         off_idx = headers.index('Offices')
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         filled = [r for r in rows if r[off_idx] and str(r[off_idx]).strip()]
         print(f'Offices filled: {len(filled)}/{len(rows)} ({len(filled)*100//len(rows) if rows else 0}%)')
         print('Baseline was 22% filled (78% empty)')
         " 2>&1
    Expected Result: offices 채워진 비율이 기존 22%보다 개선됨 (목표: ≥30%)
    Failure Indicators: 개선 없음 (22% 이하)
    Evidence: .sisyphus/evidence/task-4-offices-improvement.txt
  ```

  **Commit**: YES (진단 결과에 따라 수정된 파일만)
  - Message: `fix: address offices empty root cause (NOT_FOUND|VALIDATION_REJECTED)`
  - Files: `enrichment.py` 또는 `validators.py` (진단 결과에 따라)

---

## Final Verification Wave

- [x] F1. **전체 QA 검증** — `unspecified-low`

  아래 4가지를 순서대로 실행. 모두 통과 시 APPROVE.

  ```
  Scenario 1: Quinn Emanuel 이름 복원 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "quinn" --max-profiles 5 2>&1
      2. outputs/ 에서 최신 xlsx 확인
      3. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         wb = openpyxl.load_workbook(f, read_only=True)
         ws = wb.active
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         quinn = [r for r in rows if r[0] and 'quinn' in r[0].lower()]
         named = [r for r in quinn if r[1] and str(r[1]).strip()]
         print(f'Quinn: {len(named)}/{len(quinn)} named')
         assert len(named) >= 3, f'FAIL: only {len(named)} named'
         print('PASS')
         "
    Expected Result: ≥3 Quinn Emanuel 변호사 이름 있음
    Evidence: .sisyphus/evidence/final-quinn-names.txt

  Scenario 2: Education 노이즈 없음 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "gibson dunn" --max-profiles 5 2>&1
      2. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         wb = openpyxl.load_workbook(f, read_only=True)
         ws = wb.active
         headers = [c.value for c in next(ws.iter_rows(max_row=1))]
         edu_idx = headers.index('Education')
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         noisy = [r for r in rows if r[edu_idx] and ('©' in str(r[edu_idx]) or 'Follow us on Twitter' in str(r[edu_idx]))]
         print(f'Noisy education rows: {len(noisy)}')
         assert len(noisy) == 0, f'FAIL: {len(noisy)} noisy rows'
         print('PASS')
         "
    Expected Result: Education 필드에 © 또는 footer 텍스트 없음
    Evidence: .sisyphus/evidence/final-education-noise.txt

  Scenario 3: 이름 없는 행 0개 확인
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "skadden" "cravath" --max-profiles 5 2>&1
      2. python3.12 -c "
         import openpyxl, glob, os
         f = max(glob.glob('outputs/*.xlsx'), key=os.path.getmtime)
         wb = openpyxl.load_workbook(f, read_only=True)
         ws = wb.active
         rows = list(ws.iter_rows(min_row=2, values_only=True))
         blank = [r for r in rows if not r[1] or not str(r[1]).strip()]
         print(f'Blank name rows: {len(blank)}')
         assert len(blank) == 0, f'FAIL: {len(blank)} blank-name rows'
         print('PASS')
         "
    Expected Result: Attorney Name 컬럼에 빈 행 없음
    Evidence: .sisyphus/evidence/final-blank-names.txt

  Scenario 4: Offices 진단 결과 문서화
    Tool: Bash
    Steps:
      1. Task 4에서 생성된 .sisyphus/evidence/task-4-offices-diagnosis.txt 존재 확인
      2. 내용에 'NOT_FOUND' 또는 'VALIDATION_REJECTED' 분포 포함 확인
    Expected Result: 진단 파일 존재 + 수정 방향 명시
    Evidence: .sisyphus/evidence/task-4-offices-diagnosis.txt (기존 파일 참조)
  ```

  Output: `Scenarios [4/4 pass] | VERDICT: APPROVE/REJECT`

---

## Commit Strategy

- **Task 1**: `fix: reclassify Quinn Emanuel as SPA_OTHER in site_structures.json`
- **Task 2**: `fix: add structural footer detection to parser_sections._collect_content_after`
- **Task 3**: `fix: filter nameless profiles before Excel/JSONL output in run_pipeline`
- **Task 4**: `fix: address offices empty root cause (NOT_FOUND|VALIDATION_REJECTED)`

---

## Success Criteria

### Verification Commands
```bash
python3.12 run_pipeline.py --firms "quinn" --max-profiles 5 2>&1 | grep -i "full_name\|named\|attorney"
python3.12 run_pipeline.py --firms "gibson dunn" --max-profiles 3 2>&1
```

### Final Checklist
- [ ] Quinn Emanuel ≥3명 이름 있음
- [ ] Education에 © / footer 텍스트 없음
- [ ] 아웃풋 Attorney Name 빈 행 없음
- [ ] Offices 진단 완료 + 원인에 맞는 수정 적용
- [ ] 수정된 파일: site_structures.json, parser_sections.py, run_pipeline.py, (조건부) validators.py 또는 enrichment.py
