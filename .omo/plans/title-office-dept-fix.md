# Title / Offices / Departments 추출 품질 개선

## TL;DR

> **Quick Summary**: `attorneys_2026-03-24T20-29-18.xlsx` 기준으로 진단된 Title 오염·빈값, Offices 49% 빈값, Departments 90% 빈값을 수정한다. 핵심은 ① `_extract_title_proximity()` 스코프 제한 → ② `validate_title()` 펌명 필터 추가 → ③ 각 firm별 CSS/구조 셀렉터 보강 → ④ Offices 보강 → ⑤ Department 헤딩 매핑 확장 순서로 진행한다.
>
> **Deliverables**:
> - `enrichment.py` — proximity 스코프 제한, title/office 셀렉터 추가, Weil 오염 수정, Sullivan & Cromwell `bio-loc` 파싱, Saul Ewing 웹컴포넌트 파싱
> - `validators.py` — `validate_title(firm_name)` 파라미터 추가, 펌명 필터
> - `parser_sections.py` — department 헤딩 동의어 확장
> - `.sisyphus/evidence/baseline-quality.txt` — 수정 전 baseline 수치
>
> **Estimated Effort**: Medium (총 ~2–3시간)  
> **Parallel Execution**: NO — 각 Task가 이전 Task의 코드에 의존  
> **Critical Path**: T1 → T2 → T3 → T4 → T5 → T6 → T7 → F1–F3

---

## Context

### Original Request
`outputs/attorneys_2026-03-24T20-29-18.xlsx` (1264행, ~70개 펌) 기준으로 title이 부족하고 오염되어 있으며, offices/departments도 추출 안 되는 케이스가 많다. 원인 진단 후 수정.

### Interview Summary
**진단된 문제**:
- **Title** — 11% 빈값 + ~200건 오염 (펌 이름, 마케팅 문구, 뉴스 헤드라인)
- **Offices** — 49% 빈값 (CSS 셀렉터 누락, Weil 전체 사무소 오염, `_US_MAJOR_LAW_CITIES` 미포함 도시)
- **Departments** — 90% 빈값 (Kirkland 전용 셀렉터만 존재, 일반 fallback 없음)

**Root cause 핵심**:
- `_extract_title_proximity()` — 페이지 전체에서 `soup.find(string=regex)`로 title 키워드를 찾아 parent 전체 텍스트 반환 → "Partner with us today" 같은 마케팅 문구 통과
- `validate_title()` — 길이(2–120)+email/phone만 체크, 펌 이름·마케팅 문구 통과
- 각 firm CSS 셀렉터 목록에 Cahill, Susman Godfrey, Sullivan & Cromwell 등 미포함

### Research Findings (Metis 포함)
- Cahill: `<p class="position">Associate</p>` (h1 바로 아래, Stage 0 셀렉터 `position` 없음)
- Susman Godfrey: `<section class="page-header">` 안에서 h1 sibling `p` 텍스트
- Sullivan & Cromwell: `<div class="bio-hero-panel">` → `<p class="BioHeroPanel_subtitle__*">` (CSS Modules 해시 → `class*=` 매칭 필요)
- Troutman Pepper: `<div class="general">` → h1 sibling `<p>` (Stage 0 셀렉터 없음)
- Saul Ewing: `<se-profile-hero main-title="Partner" primary-office-location="Harrisburg">` 웹컴포넌트 속성
- Weil: 403 직접 차단, offices는 `class="locations"` nav 요소 또는 office-href 스캐너에서 전체 사무소 목록 유입
- `Harrisburg`만 `_US_MAJOR_LAW_CITIES`에서 누락 (Fort Worth 등은 이미 포함)
- `_KNOWN_ATTORNEY_TITLES` frozenset이 `validators.py`에 정의되어 있으나 `validate_title()`에서 미사용

### Metis Review
**식별된 gaps (반영)**:
- `BioHeroPanel_subtitle__BGhKi` 하드코딩 금지 → `class*="BioHeroPanel_subtitle"` 사용
- Bootstrap 유틸 클래스(`mb-0`, `position`) 셀렉터 추가 금지
- `validate_title()` 시그니처 변경 전 모든 호출부 확인 필요
- Weil 오염 경로: office-href 스캐너(lines 705–742)가 Weil nav 링크를 파싱할 가능성 → 반드시 추적 후 수정
- Sullivan CSS Modules 해시는 배포마다 변경 → structural 접근 필요
- department "heading" 동의어: "Practice Group", "Industry Group" 등 추가 필요

---

## Work Objectives

### Core Objective
진단된 Title/Offices/Departments 추출 버그를 수정하여 현재 출력 품질을 개선한다. 기존에 올바르게 작동하는 펌들(Kirkland, Skadden, Paul Hastings 등)의 fill rate 저하 없이 새 펌들의 빈값·오염을 줄인다.

### Concrete Deliverables
- `enrichment.py` 수정 (proximity 스코프, title/office 셀렉터, Weil 수정, S&C, Saul Ewing)
- `validators.py` 수정 (`validate_title` firm_name 파라미터, 펌명 필터)
- `parser_sections.py` 수정 (department 헤딩 동의어)
- `.sisyphus/evidence/baseline-quality.txt` (수정 전 baseline)
- `.sisyphus/evidence/` 내 각 task별 QA 결과

### Definition of Done
- [ ] Cahill title 오염 0% (`position` class 또는 h1-sibling 파싱)
- [ ] Troutman Pepper, Susman Godfrey title 오염 0%
- [ ] Sullivan & Cromwell title 빈값 개선 (현재 19/20 빈값 → 0–2/20)
- [ ] Weil offices: 개인 1개 도시 추출 (전체 사무소 목록 제거)
- [ ] Sullivan & Cromwell, Saul Ewing offices fill rate ≥80%
- [ ] 마케팅 문구("partner with us", "knobbe martens" 등) title 0건
- [ ] Kirkland/Skadden/Paul Hastings 기존 fill rate 유지 (title ≥95%, offices ≥80%)
- [ ] `validate_title()` 변경 후 기존 valid title 신규 거부 0건

### Must Have
- `_extract_title_proximity()` 스코프를 hero/header 영역으로 제한
- `validate_title(firm_name)` 파라미터 추가 및 펌명 substring 필터
- Cahill `p.position`, Troutman h1-sibling, Susman `page-header` sibling CSS 추가 (URL 가드 포함)
- Sullivan `BioHeroPanel_subtitle` partial-class 매칭
- Saul Ewing `se-profile-hero` 웹컴포넌트 속성 파싱
- Weil offices 오염 경로 추적 및 수정
- `Harrisburg` → `_US_MAJOR_LAW_CITIES` 추가
- department 헤딩 동의어("Practice Group", "Group", "Industry Group") 추가

### Must NOT Have (Guardrails)
- `"position"`, `"mb-0"` 등 Bootstrap/generic 유틸 클래스를 CSS 셀렉터 목록에 추가 금지
- `BioHeroPanel_subtitle__BGhKi` 정확한 해시 문자열 사용 금지 (배포 시 깨짐)
- `validate_title()` 변경이 기존 valid title을 새로 거부해서는 안 됨
- Weil 수정 후 Cooley `class="locations"` 파싱 회귀 금지
- h1-sibling 패턴을 nav/footer 영역에서 실행 금지 (main/article 내부로 스코프 제한)
- department 로직이 practice_areas 데이터를 중복 수집해서는 안 됨
- Reed Smith 수정 시도 금지 (Astro.js SPA + Playwright 여부 미검증 → BOT_PROTECTED 태깅 고려)
- 70개 전체 펌의 완전 재스크래핑 금지 (이 태스크는 로직 수정이 목표)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO (pytest 미설치, standalone 스크립트 방식)
- **Automated tests**: NO (별도 test suite 없음)
- **Framework**: N/A — 기존 방식 `python3.12 run_pipeline.py --firms X --max-profiles N` 사용

### QA Policy
모든 QA는 Agent가 bash 명령으로 직접 실행. JSONL 출력 파싱으로 수치 검증.

---

## Execution Strategy

### Parallel Execution: SEQUENTIAL

각 Task가 이전 Task의 코드 변경에 의존하므로 순차 실행.

```
T1 (baseline + 원인 추적)        ~15분
  ↓
T2 (_extract_title_proximity 수정)  ~20분
  ↓
T3 (validate_title 펌명 필터)       ~15분
  ↓
T4 (Title CSS 셀렉터 보강)          ~20분
  ↓
T5 (Offices 보강 — Weil, S&C, Saul Ewing)  ~30분
  ↓
T6 (_US_MAJOR_LAW_CITIES 보완)      ~5분
  ↓
T7 (Department 헤딩 동의어 확장)    ~20분
  ↓
F1 (Kirkland 회귀 테스트)           ~10분
F2 (전체 수치 검증)                 ~10분
F3 (오염 패턴 잔존 확인)            ~10분
```

**Total**: ~2.5–3시간

---

## TODOs

---

- [x] 1. Baseline 수치 저장 & 오염 경로 추적

  **예상 소요시간**: ~15분

  **What to do**:
  - `.sisyphus/evidence/` 디렉토리 확인 (이미 존재)
  - 현재 JSONL에서 title/offices/departments 빈값 수치를 `.sisyphus/evidence/baseline-quality.txt`에 저장:
    ```bash
    python3.12 -c "
    import json
    from collections import Counter
    data = [json.loads(l) for l in open('outputs/attorneys_2026-03-24T20-29-18.jsonl')]
    print('=== BASELINE ===')
    print('Total rows:', len(data))
    print('title empty:', sum(1 for r in data if not r.get('title')), '/', len(data))
    print('offices empty:', sum(1 for r in data if not r.get('offices')), '/', len(data))
    print('dept empty:', sum(1 for r in data if not r.get('department')), '/', len(data))
    print()
    print('=== title_reason distribution (empty rows) ===')
    reasons = Counter(r.get('diagnostics',{}).get('title_reason','ok') for r in data if not r.get('title'))
    for k,v in reasons.most_common(): print(f'  {k}: {v}')
    print()
    print('=== offices_reason distribution (empty rows) ===')
    oreasons = Counter(r.get('diagnostics',{}).get('offices_reason','ok') for r in data if not r.get('offices'))
    for k,v in oreasons.most_common(): print(f'  {k}: {v}')
    print()
    print('=== 오염된 title 샘플 (firm name / marketing phrase) ===')
    VALID_KW = ['partner','associate','counsel','member','shareholder','attorney','director','senior','junior','of counsel']
    bad = [(r.get('firm',''),r.get('title',''),r.get('profile_url','')) for r in data
           if r.get('title') and not any(k in r['title'].lower() for k in VALID_KW)]
    for firm,t,url in bad[:20]: print(f'  {firm:30s} | {t[:50]:50s} | {url[:60]}')
    " | tee .sisyphus/evidence/baseline-quality.txt
    ```
  - Weil offices 오염 경로 추적: Weil 샘플 5건의 `diagnostics` 확인
    ```bash
    python3.12 -c "
    import json
    data = [json.loads(l) for l in open('outputs/attorneys_2026-03-24T20-29-18.jsonl')]
    weil = [r for r in data if 'weil' in r.get('firm','').lower()][:5]
    for r in weil:
        print(r.get('full_name'), '| offices:', r.get('offices'))
        print('  diagnostics:', {k:v for k,v in r.get('diagnostics',{}).items() if 'office' in k.lower() or 'stage' in k.lower() or 'section' in k.lower()})
    "
    ```
  - `enrichment.py`의 `_extract_title_proximity()` 함수 전체 (lines 1280–1340 근처) 코드 읽기
  - `enrichment.py`의 office-href 스캐너 (lines 705–742) 코드 읽기하여 Weil nav 링크가 잡히는지 확인

  **Must NOT do**:
  - 아직 코드 수정 금지 (진단·baseline만)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: bash 명령 실행 + 파일 읽기만, 코드 수정 없음
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (시작점)
  - **Blocks**: T2, T3, T4, T5
  - **Blocked By**: None (즉시 시작 가능)

  **References**:
  - `enrichment.py:1280–1340` — `_extract_title_proximity()` 함수
  - `enrichment.py:705–742` — office-href scanner
  - `enrichment.py:654–660` — `class="locations"` guard (Weil)
  - `outputs/attorneys_2026-03-24T20-29-18.jsonl` — 진단 데이터

  **Acceptance Criteria**:
  - [ ] `.sisyphus/evidence/baseline-quality.txt` 파일 생성 확인 (`cat` 명령으로 내용 출력)
  - [ ] title_reason 분포 출력 확인 (어느 stage/reason이 empty title을 만드는지)
  - [ ] Weil offices 오염 경로 특정 (어느 코드 라인에서 오는지 comment로 기록)

  **QA Scenarios**:
  ```
  Scenario: baseline-quality.txt 생성
    Tool: Bash
    Steps:
      1. cat .sisyphus/evidence/baseline-quality.txt
    Expected Result: "title empty: N / 1264" 형태 출력, 파일 존재
    Evidence: .sisyphus/evidence/task-1-baseline.txt (tee 결과)
  ```

  **Commit**: YES
  - Message: `chore: save extraction baseline metrics`
  - Files: `.sisyphus/evidence/baseline-quality.txt`
  - Pre-commit: N/A

---

- [x] 2. `_extract_title_proximity()` — hero/header 스코프 제한

  **예상 소요시간**: ~20분

  **What to do**:
  - `enrichment.py`의 `_extract_title_proximity()` 함수를 읽어 현재 로직 파악
  - 현재 `soup.find(string=re.compile(...))` 호출이 페이지 전체를 검색함 → hero/header/main 영역으로 제한
  - 수정 전략:
    1. 먼저 hero/header 영역 DOM 요소를 찾는다:
       ```python
       _HERO_SELECTORS = [
           "bio-hero-panel", "page-header", "hero", "bio-header",
           "attorney-header", "profile-hero", "profile-header",
           "profile-heading", "attorney-bio-header",
       ]
       hero = None
       for sel in _HERO_SELECTORS:
           hero = soup.find(class_=sel) or soup.find(id=sel)
           if hero:
               break
       # main 태그 내 첫 300자 영역도 허용 (section 헤더 없는 경우)
       if not hero:
           hero = soup.find("main") or soup.find("article")
       search_scope = hero if hero else soup  # fallback: 전체 (기존 동작 유지)
       ```
    2. `soup.find(string=...)` → `search_scope.find(string=...)` 으로 교체
    3. 최후 fallback으로 `search_scope = soup` (기존 동작 보존)을 유지해서 회귀 최소화

  - **중요**: 수정 후 즉시 아래 QA 실행:
    ```bash
    python3.12 run_pipeline.py --firms "cahill" --max-profiles 5 --verbose 2>&1 | tee .sisyphus/evidence/task-2-cahill-after.txt
    python3.12 run_pipeline.py --firms "troutman" --max-profiles 5 --verbose 2>&1 | tee .sisyphus/evidence/task-2-troutman-after.txt
    python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5 --verbose 2>&1 | tee .sisyphus/evidence/task-2-kirkland-regression.txt
    ```

  **Must NOT do**:
  - hero 발견 실패 시 `search_scope = soup` fallback 제거 금지 (회귀 위험)
  - `_extract_title_proximity()` 이외 다른 extraction 함수 수정 금지 (이 Task 범위 밖)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: enrichment.py 전체 파악 후 정밀한 스코프 제한 로직 작성 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (T1 완료 후)
  - **Blocks**: T3, T4
  - **Blocked By**: T1

  **References**:
  - `enrichment.py:1280–1340` (추정) — `_extract_title_proximity()` 전체
  - `enrichment.py:395–396` — Stage 5 호출부 `_proximity_fallback(profile, html)`
  - 패턴 예시: Susman `section.page-header`, S&C `div.bio-hero-panel`, Cahill `div.bio-contact`

  **Acceptance Criteria**:
  - [ ] `python3.12 run_pipeline.py --firms "cahill" --max-profiles 5` → title에 "Cahill Gordon" 0건
  - [ ] `python3.12 run_pipeline.py --firms "troutman" --max-profiles 5` → title에 "Troutman Pepper Locke" 0건
  - [ ] `python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5` → title 5/5 채워짐 (회귀 없음)

  **QA Scenarios**:
  ```
  Scenario: Cahill title 오염 제거
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "cahill" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; cahill=[r for r in data if 'cahill' in r.get('firm','').lower()]; [print(r.get('full_name'),'|',r.get('title')) for r in cahill]"
    Expected Result: 각 Cahill 행의 title이 'Associate' 또는 'Partner' 등 정상 직함
    Failure Indicators: title에 'Cahill Gordon', 'Partner with us' 등이 나타남
    Evidence: .sisyphus/evidence/task-2-cahill-after.txt

  Scenario: Kirkland 회귀 없음
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; k=[r for r in data if 'kirkland' in r.get('firm','').lower()]; empty=[r for r in k if not r.get('title')]; print('kirkland title empty:', len(empty), '/', len(k))"
    Expected Result: kirkland title empty: 0 / 5
    Failure Indicators: empty가 1 이상
    Evidence: .sisyphus/evidence/task-2-kirkland-regression.txt
  ```

  **Commit**: YES
  - Message: `fix(enrichment): constrain _extract_title_proximity to hero/header DOM scope`
  - Files: `enrichment.py`
  - Pre-commit: `python3.12 -c "import enrichment; print('import ok')"`

---

- [x] 3. `validate_title()` — 펌 이름 필터 추가

  **예상 소요시간**: ~15분

  **What to do**:
  - `validators.py`의 `validate_title()` 시그니처 변경:
    ```python
    def validate_title(raw: str | None, firm_name: str = "") -> tuple[str | None, str | None]:
    ```
  - 함수 내부에 firm_name 체크 추가 (length 체크 이후):
    ```python
    # Reject if title IS the firm name (or a close substring match)
    if firm_name:
        fn_lower = firm_name.lower().strip()
        t_lower = title.lower()
        # Full match or major token overlap (≥3 tokens match)
        if fn_lower in t_lower or t_lower in fn_lower:
            return None, ValidationReason.CONTAMINATED
        # Token overlap check: ≥3 words in common → likely firm name
        fn_tokens = set(fn_lower.split())
        t_tokens = set(t_lower.split())
        common = fn_tokens & t_tokens
        if len(common) >= 2 and len(common) >= len(t_tokens) * 0.5:
            return None, ValidationReason.CONTAMINATED
    ```
  - `validate_title()` 호출부 전체 확인 (`lsp_find_references` 또는 grep) → 모든 호출부에 `firm_name=profile.firm` 전달
    - 주요 호출: `enrichment.py:415` → `validate_title(profile.title, firm_name=profile.firm or "")`
  - `_KNOWN_ATTORNEY_TITLES` frozenset이 있으나 현재 미사용 → **이번 Task에서는 건드리지 말 것** (allowlist 전환은 범위 밖, 오히려 드롭 위험)
  - 변경 후 no-regression 검증:
    ```bash
    python3.12 -c "
    import json
    from validators import validate_title
    data = [json.loads(l) for l in open('outputs/attorneys_2026-03-24T20-29-18.jsonl')]
    valid_now = [r for r in data if r.get('title')]
    newly_rejected = [r for r in valid_now if validate_title(r['title'], r.get('firm',''))[0] is None]
    print('Newly rejected by updated validate_title:', len(newly_rejected))
    for r in newly_rejected[:10]:
        print(' ', r.get('firm'), '|', r.get('title'))
    " | tee .sisyphus/evidence/task-3-no-regression.txt
    ```
  - 결과에서 `Newly rejected: 0` 확인 필수. 만약 0이 아니면 토큰 겹침 임계값 조정 후 재검증.

  **Must NOT do**:
  - `validate_title()`을 allowlist 방식으로 전환 금지 ("Senior Litigation Partner" 같은 정상 titles 드롭 위험)
  - 정확한 해시 클래스 문자열 사용 금지
  - 기존 valid title을 1건이라도 새로 거부하면 릴리스 금지

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 시그니처 변경 + 필터 로직 추가, 호출부 업데이트 — 범위 명확
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (T2 완료 후)
  - **Blocks**: T4
  - **Blocked By**: T2

  **References**:
  - `validators.py:231–260` — `validate_title()` 현재 구현
  - `validators.py:162–200` (추정) — `_KNOWN_ATTORNEY_TITLES` frozenset 정의
  - `enrichment.py:414–421` — `validate_title()` 호출부
  - `enrichment.py:169–190` — `profile.firm` 설정 위치

  **Acceptance Criteria**:
  - [ ] `.sisyphus/evidence/task-3-no-regression.txt` 내 `Newly rejected: 0` 확인
  - [ ] `python3.12 run_pipeline.py --firms "knobbe martens" --max-profiles 5` → title에 "Knobbe Martens" 0건
  - [ ] `python3.12 run_pipeline.py --firms "troutman" --max-profiles 5` → title에 "Troutman Pepper Locke" 0건

  **QA Scenarios**:
  ```
  Scenario: 펌 이름 title 제거 — Knobbe Martens
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "knobbe martens" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; rows=[r for r in data if 'knobbe' in r.get('firm','').lower()]; [print(r.get('full_name'),'|', r.get('title')) for r in rows]"
    Expected Result: title이 None 또는 'Partner'/'Associate' 등 정상 직함 (펌 이름 없음)
    Failure Indicators: title에 'Knobbe Martens' 텍스트 존재
    Evidence: .sisyphus/evidence/task-3-knobbe.txt

  Scenario: 기존 valid title 신규 거부 없음
    Tool: Bash
    Steps:
      1. python3.12 -c "...no-regression 스크립트..."
    Expected Result: Newly rejected: 0
    Failure Indicators: Newly rejected > 0 이면 임계값 재조정
    Evidence: .sisyphus/evidence/task-3-no-regression.txt
  ```

  **Commit**: YES
  - Message: `fix(validators): add firm_name filter to validate_title to reject firm-name contamination`
  - Files: `validators.py`, `enrichment.py`
  - Pre-commit: `python3.12 -c "from validators import validate_title; print(validate_title('Partner', 'Knobbe Martens')); print(validate_title('Associate', 'Cahill Gordon'))"`

---

- [x] 4. Title CSS 셀렉터 보강 — Cahill, Troutman, Susman, Sullivan & Cromwell

  **예상 소요시간**: ~20분

  **What to do**:
  - `enrichment.py`의 Stage 0 title 셀렉터 목록 (`for selector_class in [...]`, lines 590–611)에 다음 추가:
    - `"position"` — **금지** (Bootstrap generic). 대신 firm-URL guard 방식 사용

  - **Cahill** (`cahill.com`): h1 sibling `<p class="position">` 파싱
    ```python
    # Cahill Gordon: <div class="bio-contact"><h1>Name</h1><p class="position">Associate</p>...
    if not profile.title and 'cahill.com' in (url or ''):
        bio = soup.find(class_="bio-contact")
        if bio:
            pos = bio.find("p", class_="position")
            if pos:
                t = pos.get_text(strip=True)
                if t and len(t) < 100:
                    profile.title = t
    ```

  - **Troutman Pepper** (`troutman.com`): h1 sibling `<p>` in `<div class="general">`
    ```python
    # Troutman: <div class="general"><h1>Name</h1><p>Associate</p>
    if not profile.title and 'troutman.com' in (url or ''):
        general = soup.find(class_="general")
        if general:
            h1 = general.find("h1")
            if h1:
                nxt = h1.find_next_sibling("p")
                if nxt:
                    t = nxt.get_text(strip=True)
                    if t and len(t) < 100:
                        profile.title = t
    ```

  - **Susman Godfrey** (`susmangodfrey.com`): `section.page-header` 내 h1 sibling
    ```python
    # Susman: <section class="page-header"><h1>Name</h1>sibling "Associate"<br>"New York"...
    if not profile.title and 'susmangodfrey.com' in (url or ''):
        ph = soup.find("section", class_="page-header")
        if ph:
            h1 = ph.find("h1")
            if h1:
                # Next significant sibling text node or element
                for sib in h1.next_siblings:
                    t = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()
                    if t and len(t) < 100 and '@' not in t and not t[0].isdigit():
                        profile.title = t
                        break
    ```

  - **Sullivan & Cromwell** (`sullcrom.com`): `div.bio-hero-panel` 내 `class*="BioHeroPanel_subtitle"` p 태그
    ```python
    # S&C: <div class="bio-hero-panel">...<p class="BioHeroPanel_subtitle__HASH">Associate</p>
    if not profile.title and 'sullcrom.com' in (url or ''):
        hero = soup.find(class_="bio-hero-panel")
        if hero:
            # CSS Modules hash changes on deploy — use partial class match
            sub = hero.find(lambda tag: tag.name == "p" and
                            any("BioHeroPanel_subtitle" in c for c in (tag.get("class") or [])))
            if sub:
                t = sub.get_text(strip=True)
                if t and len(t) < 100:
                    profile.title = t
    ```

  - 이 4개 블록을 `enrichment.py`의 `_extract_from_css_classes()` 내 title 섹션 끝 부분(기존 셀렉터 for loop 이후)에 삽입. 각 블록은 `if not profile.title and '[domain]' in (url or ''):` 으로 시작.
  - `url` 파라미터가 해당 함수에 전달되는지 확인. 없으면 `_extract_from_css_classes(profile, soup, url=url)` 형태로 파라미터 추가.

  **Must NOT do**:
  - `"position"` 클래스를 전역 셀렉터 목록에 추가 금지
  - URL guard 없이 h1-sibling 패턴 추가 금지 (다른 사이트에서 오발화)
  - Sullivan `BioHeroPanel_subtitle__BGhKi` 정확한 해시 문자열 사용 금지

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 4개 firm 각각의 HTML 구조를 이해하고 URL-scoped 로직 작성 필요
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (T3 완료 후)
  - **Blocks**: T5
  - **Blocked By**: T3

  **References**:
  - `enrichment.py:588–620` — Stage 0 title 셀렉터 for loop
  - `enrichment.py:476–510` — `_extract_from_css_classes()` 함수 시그니처
  - Cahill HTML: `<div class="bio-contact"><h1>Name</h1><p class="position">Associate</p>`
  - Troutman HTML: `<div class="general"><h1>Name</h1><p>Associate</p>`
  - Susman HTML: `<section class="page-header">...<h1>Dan Duhaime</h1>...sibling "Associate"...sibling "New York"`
  - Sullivan HTML: `<div class="bio-hero-panel">...<p class="BioHeroPanel_subtitle__*">Associate</p>`

  **Acceptance Criteria**:
  - [ ] `python3.12 run_pipeline.py --firms "cahill" --max-profiles 5` → 5/5 title 채워짐
  - [ ] `python3.12 run_pipeline.py --firms "troutman" --max-profiles 5` → title 오염 0건, 정상 직함 확인
  - [ ] `python3.12 run_pipeline.py --firms "susman" --max-profiles 5` → title 오염 0건
  - [ ] `python3.12 run_pipeline.py --firms "sullivan" --max-profiles 5` → title 5/5 (현재 19/20 빈값)

  **QA Scenarios**:
  ```
  Scenario: Sullivan & Cromwell title 복구
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "sullivan" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; sc=[r for r in data if 'sullcrom' in r.get('profile_url','') or 'sullivan' in r.get('firm','').lower()]; empty=[r for r in sc if not r.get('title')]; print('S&C title empty:', len(empty), '/', len(sc)); [print(r.get('full_name'),'|',r.get('title')) for r in sc[:5]]"
    Expected Result: S&C title empty: 0 / 5 (또는 최대 1)
    Failure Indicators: empty가 3 이상
    Evidence: .sisyphus/evidence/task-4-sullivan-title.txt

  Scenario: Cahill title 정상 추출
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "cahill" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; rows=[r for r in data if 'cahill' in r.get('firm','').lower()]; [print(r.get('full_name'),'|',r.get('title')) for r in rows]"
    Expected Result: 각 행 title = 'Associate' 또는 'Partner' 등 정상 직함 (5/5)
    Failure Indicators: title이 None 또는 'Cahill Gordon'
    Evidence: .sisyphus/evidence/task-4-cahill-title.txt
  ```

  **Commit**: YES
  - Message: `fix(enrichment): add firm-specific title selectors for Cahill, Troutman, Susman, Sullivan`
  - Files: `enrichment.py`
  - Pre-commit: `python3.12 -c "import enrichment; print('import ok')"`

---

- [x] 5. Offices 보강 — Weil 오염 수정 + Sullivan & Cromwell + Saul Ewing 웹컴포넌트

  **예상 소요시간**: ~30분

  **What to do**:

  **5-A: Weil 오염 수정**
  - T1에서 파악한 오염 경로 기반으로 수정. 가능한 경로:
    - (경로 1) `class="locations"` guard: 현재 `len(text) < 100` 확인 → Weil 텍스트가 100자 초과면 이미 가드됨. 만약 Weil이 Playwright로 도달하는 경우 다른 경로일 수 있음
    - (경로 2) office-href 스캐너 (lines 705–742): Weil `/office/city/` 링크들을 nav 안에서 탐지. 부모 container가 nav로 올바르게 분류되는지 확인. 안 된다면 Weil URL guard 추가:
      ```python
      # Weil: skip office-href scanner (returns all firm offices, not attorney-specific)
      if 'weil.com' in (url or ''):
          pass  # skip
      else:
          # ... 기존 office-href scanner ...
      ```
    - T1 진단 결과에 따라 실제 오염 경로를 수정
  - 수정 후 테스트:
    ```bash
    python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose
    ```
    → 각 attorney가 1–2개 도시만 가져야 함 (전체 사무소 목록 X)
  - **Cooley 회귀 테스트** (둘 다 `class="locations"` 사용):
    ```bash
    python3.12 run_pipeline.py --firms "cooley" --max-profiles 5 --verbose
    ```
    → Cooley offices 여전히 추출되어야 함

  **5-B: Sullivan & Cromwell offices** (`sullcrom.com`)
  ```python
  # S&C: <div class="bio-loc"><p class="sc-font-secondary fw-500 pe-2 mb-0">New York</p>...
  if not profile.offices and 'sullcrom.com' in (url or ''):
      bio_loc = soup.find(class_="bio-loc")
      if bio_loc:
          # First <p> inside bio-loc is the city
          p = bio_loc.find("p")
          if p:
              city = p.get_text(strip=True)
              if city and len(city) < 60 and city not in profile.offices:
                  profile.offices.append(city)
  ```

  **5-C: Saul Ewing 웹컴포넌트** (`saul.com`)
  ```python
  # Saul Ewing: <se-profile-hero main-title="Partner" primary-office-location="Harrisburg">
  if 'saul.com' in (url or ''):
      hero_el = soup.find("se-profile-hero")
      if hero_el:
          # Title
          if not profile.title:
              for attr in ("main-title", "title", "role"):
                  val = hero_el.get(attr, "").strip()
                  if val and len(val) < 100:
                      profile.title = val
                      break
          # Office
          if not profile.offices:
              for attr in ("primary-office-location", "office", "location"):
                  val = hero_el.get(attr, "").strip()
                  if val and len(val) < 60:
                      profile.offices.append(val)
                      break
  ```

  - Saul Ewing 블록은 title/office 둘 다 처리 (웹컴포넌트에서 한 번에)
  - 이 블록들을 `_extract_from_css_classes()` 내 offices 섹션 끝에 삽입

  **Must NOT do**:
  - Weil 수정 후 Cooley 회귀 금지
  - `class="bio-loc"` 를 전역 셀렉터 목록에 추가 금지 (S&C URL guard 필요)
  - Saul Ewing 웹컴포넌트 파서를 범용 웹컴포넌트 시스템으로 확장 금지 (saul.com에만 적용)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Weil 오염 경로 추적 + 3개 firm 각각 다른 패턴 구현
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (T4 완료 후)
  - **Blocks**: T6
  - **Blocked By**: T4

  **References**:
  - `enrichment.py:622–765` — 전체 offices 추출 섹션
  - `enrichment.py:654–660` — `class="locations"` guard
  - `enrichment.py:705–742` — office-href scanner
  - Sullivan HTML: `<div class="bio-loc"><p class="sc-font-secondary...">New York</p>`
  - Saul HTML: `<se-profile-hero main-title="Partner" primary-office-location="Harrisburg">`
  - `.sisyphus/evidence/baseline-quality.txt` — Weil 오염 경로 (T1에서 기록됨)

  **Acceptance Criteria**:
  - [x] Weil: 각 attorney offices = 1–2개 도시 (파이프 구분 전체 목록 X)
  - [x] Cooley: offices 여전히 추출됨 (회귀 없음)
  - [x] Sullivan & Cromwell: offices ≥4/5 채워짐 (현재 0/20)
  - [x] Saul Ewing: offices ≥4/5 채워짐 (현재 0/20)

  **QA Scenarios**:
  ```
  Scenario: Weil 개인 offices 추출 (전체 목록 제거)
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "weil" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; weil=[r for r in data if 'weil' in r.get('firm','').lower()]; [print(r.get('full_name'),'|',r.get('offices')) for r in weil[:5]]"
    Expected Result: offices = ['New York'] 또는 ['Dallas'] 등 단일 도시 (Austin|Boston|... 없음)
    Failure Indicators: offices에 3개 이상 도시 OR 파이프 구분 긴 문자열
    Evidence: .sisyphus/evidence/task-5-weil-offices.txt

  Scenario: Cooley offices 회귀 없음
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "cooley" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; rows=[r for r in data if 'cooley' in r.get('firm','').lower()]; empty=[r for r in rows if not r.get('offices')]; print('Cooley offices empty:', len(empty), '/', len(rows))"
    Expected Result: Cooley offices empty: 0 / 5 (또는 최대 1)
    Failure Indicators: empty ≥ 3
    Evidence: .sisyphus/evidence/task-5-cooley-regression.txt

  Scenario: Saul Ewing offices + title 웹컴포넌트 파싱
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "saul ewing" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; rows=[r for r in data if 'saul' in r.get('firm','').lower()]; [print(r.get('full_name'),'| title:', r.get('title'),'| offices:', r.get('offices')) for r in rows]"
    Expected Result: title = 'Partner'/'Associate' 등, offices = ['Harrisburg'] 등 정상 값
    Failure Indicators: title/offices 빈값
    Evidence: .sisyphus/evidence/task-5-saul-ewing.txt
  ```

  **Commit**: YES
  - Message: `fix(enrichment): fix Weil offices contamination, add S&C bio-loc and Saul Ewing web-component parsers`
  - Files: `enrichment.py`
  - Pre-commit: `python3.12 -c "import enrichment; print('import ok')"`

---

- [x] 6. `_US_MAJOR_LAW_CITIES`에 누락 도시 추가

  **예상 소요시간**: ~5분

  **What to do**:
  - `validators.py`의 `_US_MAJOR_LAW_CITIES` frozenset에 다음 추가:
    - `"Harrisburg"` (Saul Ewing의 Pennsylvania 사무소)
  - Metis 확인: Fort Worth, Palo Alto, Century City 등은 이미 포함되어 있음 → 추가 불필요
  - 추가 후 간단 검증:
    ```bash
    python3.12 -c "from validators import validate_offices; print(validate_offices(['Harrisburg']))"
    ```
    → `(['Harrisburg'], None)` 이어야 함

  **Must NOT do**:
  - 목록을 광범위하게 확장 금지 (확인된 누락 도시만)
  - `validate_offices()` 로직 자체 변경 금지

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단순 1줄 추가
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (T5와 같은 파일 수정)
  - **Parallel Group**: Sequential (T5 완료 후)
  - **Blocks**: T7
  - **Blocked By**: T5

  **References**:
  - `validators.py:94–200` (추정) — `_US_MAJOR_LAW_CITIES` 정의

  **Acceptance Criteria**:
  - [ ] `python3.12 -c "from validators import validate_offices; print(validate_offices(['Harrisburg']))"` → `(['Harrisburg'], None)`

  **QA Scenarios**:
  ```
  Scenario: Harrisburg validate_offices 통과
    Tool: Bash
    Steps:
      1. python3.12 -c "from validators import validate_offices; result=validate_offices(['Harrisburg']); assert result[0]==['Harrisburg'], f'FAIL: {result}'; print('PASS:', result)"
    Expected Result: PASS: (['Harrisburg'], None)
    Evidence: .sisyphus/evidence/task-6-harrisburg.txt
  ```

  **Commit**: YES (T7과 합산)
  - Message: 다음 Task(T7)와 합산
  - Files: `validators.py`

---

- [x] 7. `parser_sections.py` — Department 헤딩 동의어 확장

  **예상 소요시간**: ~20분

  **What to do**:
  - `parser_sections.py`를 전체 읽어 현재 department/section heading 매핑 구조 파악
  - `"department"` 섹션에 매핑되는 헤딩 키워드 목록에 다음 동의어 추가:
    ```python
    "department": [
        "department",
        "practice group",      # 추가
        "practice groups",     # 추가
        "industry group",      # 추가
        "industry groups",     # 추가
        "group",               # 추가 (단독 헤딩으로 쓰이는 경우)
        # 기존 항목들 유지
    ]
    ```
  - **중요 가드**: `"practice areas"`, `"practices"`, `"expertise"` 는 이미 `practice_areas` 섹션에 매핑됨. department 매핑에 이 단어들 추가 금지 (중복 수집 방지)
  - 변경 후 테스트:
    ```bash
    python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5 --verbose 2>&1 | grep -i "department\|section_keys"
    python3.12 run_pipeline.py --firms "paul hastings" --max-profiles 5 --verbose 2>&1 | grep -i "department\|section_keys"
    ```
  - departments fill rate 개선 수치 확인 (target: 현재 ~10% → ≥20%)

  **Must NOT do**:
  - `"practice areas"`, `"practices"`, `"expertise"` 를 department 헤딩에 추가 금지
  - `"group"` 단독은 오발화 가능성 있음 → 테스트 후 문제 있으면 제거

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 헤딩 동의어 목록 추가, 구조 변경 없음
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (T6 완료 후)
  - **Blocks**: F1, F2, F3
  - **Blocked By**: T6

  **References**:
  - `parser_sections.py:85–120` (추정) — section heading 매핑 딕셔너리
  - `enrichment.py:787–800` — department 추출 section 활용부 (`find_section(section_map, "departments")`)

  **Acceptance Criteria**:
  - [ ] `python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5` → department 회귀 없음 (기존 작동 유지)
  - [ ] departments fill rate ≥ 20% (baseline ~10%에서 개선) — 전체 재스크래핑 없이 현재 JSONL 재파싱으로 확인 불가, 대신 샘플 펌 테스트로 검증

  **QA Scenarios**:
  ```
  Scenario: Kirkland department 회귀 없음
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5 --verbose
      2. python3.12 -c "import json; data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]; k=[r for r in data if 'kirkland' in r.get('firm','').lower()]; [print(r.get('full_name'),'| dept:', r.get('department')) for r in k]"
    Expected Result: Kirkland 5건 모두 department 채워짐 (기존 동작)
    Failure Indicators: department 빈값 증가
    Evidence: .sisyphus/evidence/task-7-kirkland-dept.txt
  ```

  **Commit**: YES (T6 validators.py 변경과 합산)
  - Message: `fix(validators, parser_sections): add Harrisburg city and expand department heading synonyms`
  - Files: `validators.py`, `parser_sections.py`
  - Pre-commit: `python3.12 -c "import validators; import parser_sections; print('import ok')"`

---

## Final Verification Wave

> T7까지 완료 후 3개 검증을 **순차 실행**. 모두 PASS여야 완료.

- [x] F1. **기존 top 펌 회귀 테스트** — `quick`

  Kirkland, Paul Hastings, Baker Botts 3개 펌 각 10프로파일 실행. title/offices fill rate baseline 대비 저하 없음 확인.

  ```bash
  python3.12 run_pipeline.py --firms "kirkland" --max-profiles 10 --verbose
  python3.12 run_pipeline.py --firms "paul hastings" --max-profiles 10 --verbose
  python3.12 run_pipeline.py --firms "baker botts" --max-profiles 10 --verbose
  python3.12 -c "
  import json
  data = [json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]
  for firm in ['kirkland', 'paul hastings', 'baker botts']:
      rows = [r for r in data if firm in r.get('firm','').lower()][-10:]
      title_ok = sum(1 for r in rows if r.get('title'))
      offices_ok = sum(1 for r in rows if r.get('offices'))
      print(f'{firm}: title {title_ok}/{len(rows)}, offices {offices_ok}/{len(rows)}')
  " | tee .sisyphus/evidence/final-regression.txt
  ```
  기준: Kirkland title ≥9/10, offices ≥9/10; Paul Hastings title ≥9/10; Baker Botts offices ≥7/10
  Output: `VERDICT: PASS / FAIL`

- [x] F2. **수정된 펌들 fill rate 개선 수치 확인** — `quick`

  Cahill, Troutman, Susman, Sullivan, Saul Ewing, Weil 각각 5프로파일 재실행 후 before/after 비교:

  ```bash
  for firm in "cahill" "troutman" "susman" "sullivan" "saul ewing" "weil"; do
    python3.12 run_pipeline.py --firms "$firm" --max-profiles 5 --verbose
  done
  python3.12 -c "
  import json
  data = [json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]
  firms = ['cahill','troutman','susman','sullivan','saul','weil']
  for f in firms:
      rows = [r for r in data if f in r.get('firm','').lower()][-5:]
      title_ok = sum(1 for r in rows if r.get('title'))
      offices_ok = sum(1 for r in rows if r.get('offices'))
      bad_title = [r.get('title') for r in rows if r.get('title') and 
                   any(f2 in r['title'].lower() for f2 in [f,'with us','today','follow','share'])]
      print(f'{f:20s}: title {title_ok}/{len(rows)}, offices {offices_ok}/{len(rows)}, bad_titles={bad_title}')
  " | tee .sisyphus/evidence/final-fixed-firms.txt
  ```
  기준: 각 firm title 오염 0건, offices ≥3/5
  Output: `VERDICT: PASS / FAIL`

- [x] F3. **오염 패턴 잔존 확인** — `quick`

  LATEST JSONL에서 여전히 남아있는 오염 title 수 집계:
  ```bash
  python3.12 -c "
  import json
  data = [json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]
  VALID_KW = ['partner','associate','counsel','member','shareholder','attorney','director','senior','junior','of counsel','paralegal','patent','agent']
  bad = [r for r in data if r.get('title') and not any(k in r['title'].lower() for k in VALID_KW)]
  print(f'오염 title 잔존: {len(bad)} / {len([r for r in data if r.get(\"title\")])}')
  from collections import Counter
  bc = Counter(r['title'] for r in bad)
  for t,c in bc.most_common(10): print(f'  [{c}] {t[:70]}')
  " | tee .sisyphus/evidence/final-contamination-check.txt
  ```
  기준: 오염 title ≤5건 (이전 ~200건 대비 대폭 감소, 완전 제거는 재스크래핑 필요)
  Output: `VERDICT: PASS / FAIL`

---

## Commit Strategy

| Commit | Message | Files | 예상시간 |
|---|---|---|---|
| T1 | `chore: save extraction baseline metrics` | `.sisyphus/evidence/baseline-quality.txt` | 즉시 |
| T2 | `fix(enrichment): constrain _extract_title_proximity to hero/header DOM scope` | `enrichment.py` | ~20분 |
| T3 | `fix(validators): add firm_name filter to validate_title` | `validators.py`, `enrichment.py` | ~15분 |
| T4 | `fix(enrichment): add firm-specific title selectors for Cahill, Troutman, Susman, Sullivan` | `enrichment.py` | ~20분 |
| T5 | `fix(enrichment): fix Weil offices contamination, add S&C and Saul Ewing office parsers` | `enrichment.py` | ~30분 |
| T6+T7 | `fix(validators, parser_sections): add Harrisburg city and expand department heading synonyms` | `validators.py`, `parser_sections.py` | ~25분 |

---

## Success Criteria

### Before (baseline)
- title empty: 145/1264 (11%)
- title 오염 (펌명/마케팅): ~200건
- offices empty: ~629/1264 (49%)
- departments empty: ~1142/1264 (90%)

### After (target — 샘플 펌 기준)
- 수정 대상 펌 title 오염: **0건** (Cahill, Troutman, Susman, Sullivan, Knobbe, ArentFox 등)
- Weil offices: **개인 1–2개 도시** (전체 목록 제거)
- Sullivan & Cromwell, Saul Ewing offices: **≥80% fill**
- 기존 잘 되던 펌 (Kirkland, Skadden, Paul Hastings) fill rate: **회귀 없음**
- departments: 동의어 확장으로 소폭 개선 (정확 수치는 전체 재스크래핑 후 측정)

### Verification Commands
```bash
# Baseline 확인
cat .sisyphus/evidence/baseline-quality.txt

# 수정 후 오염 잔존 확인
python3.12 -c "
import json
data=[json.loads(l) for l in open('outputs/attorneys_LATEST.jsonl')]
bad=[r for r in data if r.get('title') and not any(k in r['title'].lower() for k in ['partner','associate','counsel','member','shareholder','attorney','director','senior'])]
print('오염 title 잔존:', len(bad))
"

# 최종 회귀 테스트
python3.12 run_pipeline.py --firms "kirkland" --max-profiles 10
```
