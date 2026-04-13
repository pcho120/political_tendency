# Field Quality V2 재실행 — 다른 컴퓨터 핸드오프 플랜

## TL;DR

> **Quick Summary**: field-quality-v2 플랜의 코드 변경(T1-T10)은 완료됨. validators.py가 find_attorney.py에 wired-in된 후 200개 firm 전체 파이프라인 재실행 + 결과 검증 + Final Verification Wave 수행.
>
> **Deliverables**:
> - 200개 firm 전체 재실행 결과 (validators 적용된 상태)
> - Fill rate 목표 달성 확인 (Title ≥90%, Office ≥60%, Dept ≥85%)
> - Nav pollution 0건 확인
> - Final Verification Wave (F1-F4)
>
> **Estimated Effort**: 파이프라인 실행 ~40-50시간 + 검증 ~2시간
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: commit → rerun → verify → F1-F4 → user okay

---

## Context

### 현재 상황 (2026-04-13 기준)

**완료된 작업:**
- T1-T10 코드 변경 전부 완료 (validators.py, find_attorney.py, field_enricher.py, multi_mode_extractor.py, config/practice_department_map.json)
- 200개 firm 파이프라인 1차 실행 완료 (58,200 records, 42시간 소요)
- validators를 find_attorney.py에 wiring 완료 (validate_practice_areas, validate_offices, validate_title)

**문제:**
- 파이프라인이 validators wiring 전에 실행됐으므로, 결과에 nav pollution이 포함되고 fill rate가 목표 미달
- 재실행 필요

**현재 Fill Rate vs 목표:**

| 지표 | 이전 (pre_v2) | 1차 실행 | validators 후처리 시뮬 | 목표 |
|------|-------------|----------|---------------------|------|
| Title | 80.9% | 85.1% | 81.3% (감소는 정당 — 뉴스 헤드라인 제거) | ≥90% |
| Office | 29.9% | 32.5% | 32.5% | ≥60% |
| Dept | 78.6% | 81.0% | 81.0% | ≥85% |
| Nav pollution | - | 54,934 | 2,600 ("about", "contact" 누락) | 0 |
| Firms | 71 | 76 (67개≥10건) | - | ≥140 |

### 중요 발견사항

1. **Title 하락은 정상**: validate_title()이 뉴스 헤드라인을 올바르게 거부함 ("DLA Piper adds leading Partner..." → 이건 title이 아닌 뉴스 제목). 실제 title 품질은 오히려 개선.

2. **Office 32.5%는 근본적 한계**: HTML에서 office가 추출되지 않는 firm이 대다수. validators 완화로는 해결 안 됨. 추출 로직(field_enricher.py)이 firm별 HTML 구조를 못 파싱하는 것이 원인.

3. **_JUNK_PHRASES에 "about"과 "contact" 누락**: validators.py에 이 두 단어를 추가하면 nav pollution 2,600건 → 0건.

4. **Firms 76개 (67개 ≥10건)**: 이전 72개와 비슷. 나머지 firm은 BOT_PROTECTED 또는 discovery 실패. 목표 140개는 현실적으로 불가능.

---

## Git 상태

### 커밋되지 않은 변경사항

**코드 파일 (커밋 필요):**
```
 M  config/practice_department_map.json  (+55 lines — 24→35 매핑 확장)
 M  field_enricher.py                   (+416 lines — HTML 추출 패턴 확장)
 M  find_attorney.py                    (+2030 lines — BOT skip, 403 abort, discovery stubs, validators wiring)
 M  multi_mode_extractor.py             (+5 lines — Retry-After 헤더 기록)
 M  validators.py                       (+116 lines — nav filter, office 완화, title 완화)
```

**데이터 파일 (커밋 선택):**
```
 M  attorneys.jsonl                      (1차 실행 결과 — 재실행 후 교체 예정)
 M  coverage_metrics.json
 M  firm_level_summary.csv
 M  firm_observations.jsonl
 M  debug_reports/*.json                 (다수)
```

**Untracked:**
```
 ??  .sisyphus/evidence/*               (검증 증거 파일들)
 ??  .sisyphus/notepads/*               (작업 노트)
 ??  .sisyphus/plans/field-quality-v2.md (원본 플랜)
 ??  attorneys_pre_v2_backup.jsonl       (백업)
 ??  AmLaw200*_attorneys.xlsx            (출력 Excel)
 ??  AmLaw200*_source_failure_report.xlsx
```

### Remote
- `origin`: `https://github.com/pcho120/political_tendency.git`
- Branch: `main` (only branch)

---

## 실행 환경

### 현재 컴퓨터 (Windows)
- OS: Windows, PowerShell
- Python: 3.14.0 (`python` 명령)
- 인코딩: `$env:PYTHONIOENCODING="utf-8"` 필수 (cp949 에러 방지)

### 다른 컴퓨터 환경 세팅
1. Python 3.12+ 설치 확인
2. `pip install requests beautifulsoup4 lxml openpyxl` (playwright는 optional)
3. repo clone: `git clone https://github.com/pcho120/political_tendency.git`
4. 입력 파일 확인: `AmLaw200_2025 Rank_gross revenue_with_websites.xlsx` (repo root에 있어야 함)
5. `site_structures.json` 존재 확인 (BOT_PROTECTED 분류에 필요)

---

## Work Objectives

### Core Objective
validators가 wired-in된 상태에서 200개 firm 파이프라인을 재실행하고, fill rate 목표 달성 여부를 검증하고, 코드 품질 리뷰(F1-F4)를 완료한다.

### Definition of Done
- [ ] 코드 변경사항 커밋 + push 완료
- [ ] `_JUNK_PHRASES`에 "about", "contact" 추가 완료
- [ ] 200개 firm 재실행 완료 (validators 적용 상태)
- [ ] Nav pollution 0건
- [ ] Fill rate 결과 기록 (.sisyphus/evidence/task-11-results-summary.json)
- [ ] Final Verification Wave (F1-F4) 통과
- [ ] 사용자 explicit okay

### Must NOT Have
- ❌ Firm-specific CSS selectors 하드코딩
- ❌ robots.txt 위반 / Cloudflare 우회
- ❌ run_pipeline.py / discovery.py / enrichment.py 수정
- ❌ industries 필드 변경
- ❌ 실행 중 코드 변경

---

## Verification Strategy

### QA Policy
- Frontend/UI: 해당 없음 (CLI pipeline)
- CLI: `python find_attorney.py` 실행 + JSONL 파싱
- Python: validators unit tests

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (즉시 — 커밋 + 소수정 + 재실행):
├── Task 1: 코드 변경사항 커밋 + push [quick] ~5분
├── Task 2: _JUNK_PHRASES에 "about", "contact" 추가 [quick] ~3분
└── Task 3: 200개 firm 전체 재실행 [unspecified-high] ~40-50시간

Wave 2 (Task 3 완료 후 — 검증 + Final Verification):
├── Task 4: 재실행 결과 검증 [quick] ~15분
├── Task 5: F1-F4 Final Verification Wave [parallel] ~30분
└── Task 6: 결과 보고 + 사용자 okay 대기 [quick] ~5분
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T3 (push 후 다른 컴에서 pull) |
| T2 | — | T3 |
| T3 | T1, T2 | T4, T5, T6 |
| T4 | T3 | T6 |
| T5 | T3 | T6 |
| T6 | T4, T5 | (user okay) |

---

## TODOs

- [ ] 1. 코드 변경사항 커밋 + Push 예상 소요시간: 5분

  **What to do**:
  - 코드 파일만 stage (데이터 파일 제외):
    ```bash
    git add validators.py find_attorney.py field_enricher.py multi_mode_extractor.py config/practice_department_map.json
    git add .sisyphus/plans/field-quality-v2.md .sisyphus/plans/field-quality-v2-rerun.md
    ```
  - 커밋:
    ```bash
    git commit -m "feat: wire validators into find_attorney.py pipeline + field quality improvements (T1-T10)

    - validators.py: nav item filtering (exact match), office validation relaxation (City+StateCode), title validation relaxation (firm name 50% threshold, 200 char limit)
    - field_enricher.py: HTML extraction patterns (address tag, itemprop, Schema.org, og:locality)
    - find_attorney.py: BOT_PROTECTED pre-skip, per-firm 403 abort, directory_listing/alphabet_enumeration discovery, validate_practice_areas/offices/title wiring
    - multi_mode_extractor.py: Retry-After header recording
    - config/practice_department_map.json: 24 to 35 mappings"
    ```
  - Push: `git push origin main`

  **Must NOT do**:
  - attorneys.jsonl, debug_reports/ 등 데이터 파일 커밋 금지 (대용량)
  - .sisyphus/evidence/ 커밋 금지 (local 증거 파일)

  **Recommended Agent Profile**: `quick`

  **Acceptance Criteria**:
  ```
  Scenario: Push 성공 확인
    Tool: Bash (git)
    Steps:
      1. git push origin main
      2. Assert exit code 0
    Expected: push 성공
  ```

  **Commit**: YES (이 task 자체가 commit task)

---

- [ ] 2. _JUNK_PHRASES에 "about", "contact" 추가 예상 소요시간: 3분

  **What to do**:
  - `validators.py`의 `_JUNK_PHRASES` frozenset에 `"about"`, `"contact"` 추가
  - 이미 "about us", "contact us"는 포함되어 있으나, 단독 "about", "contact"가 빠져있음
  - 추가 후 테스트:
    ```python
    from validators import validate_practice_areas
    result, _ = validate_practice_areas(["Corporate Law", "About", "Contact", "Litigation"])
    assert result == ["Corporate Law", "Litigation"]
    ```
  - "About" 단독은 절대 practice area가 아님 (안전한 추가)
  - "Contact" 단독도 절대 practice area가 아님 (안전한 추가)

  **Must NOT do**:
  - "about"의 substring 영향 확인: exact match 방식이므로 "About Us" 별도 존재, "About" 추가해도 "About This Practice" 같은 건 없음 → 안전
  - validate_practice_areas 로직 변경 금지

  **References**:
  - `validators.py` — `_JUNK_PHRASES` frozenset (현재 44개 항목)
  - 현재 잔여 nav pollution: "about" 2,119건, "contact" 481건

  **Recommended Agent Profile**: `quick`

  **Acceptance Criteria**:
  ```
  Scenario: "about"과 "contact" 필터됨
    Tool: Bash (python)
    Steps:
      1. python -c "from validators import validate_practice_areas; r,_=validate_practice_areas(['Corporate','About','Contact','Litigation']); assert 'About' not in r; assert 'Contact' not in r; print('PASS')"
    Expected: PASS
  ```

  **Commit**: YES
  - Message: `fix(validators): add 'about' and 'contact' to _JUNK_PHRASES nav filter`
  - Files: `validators.py`

---

- [ ] 3. 200개 Firm 전체 재실행 예상 소요시간: 40-50시간

  **What to do**:
  - 다른 컴퓨터에서:
    1. `git pull origin main`
    2. `pip install requests beautifulsoup4 lxml openpyxl` (필요시)
    3. 기존 outputs 백업:
       ```bash
       cp outputs/attorneys.jsonl outputs/attorneys_pre_rerun.jsonl
       ```
    4. 파이프라인 실행:
       ```bash
       # Linux/Mac:
       PYTHONIOENCODING=utf-8 nohup python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs > outputs/run_rerun_log.txt 2>&1 &
       echo $! > outputs/pipeline_pid.txt
       
       # 또는 Windows PowerShell:
       $env:PYTHONIOENCODING="utf-8"
       Start-Process -FilePath "python" -ArgumentList 'find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs' -RedirectStandardOutput "outputs\run_rerun_stdout.txt" -RedirectStandardError "outputs\run_rerun_stderr.txt" -NoNewWindow -PassThru
       ```
    5. 진행 모니터링:
       ```bash
       # Linux/Mac:
       tail -f outputs/run_rerun_log.txt | grep "Processing:\|Total:"
       wc -l outputs/attorneys.jsonl
       
       # Windows:
       Get-Content outputs\run_rerun_stdout.txt -Tail 10 -Wait
       (Get-Item outputs\attorneys.jsonl).Length
       ```

  **Must NOT do**:
  - 실행 중 코드 변경 금지
  - 기존 outputs 덮어쓰기 전 백업 필수
  - SIGKILL 사용 금지 (Ctrl+C → graceful shutdown)

  **Recommended Agent Profile**: `unspecified-high` (장시간 모니터링)

  **Acceptance Criteria**:
  ```
  Scenario: 파이프라인 완료 확인
    Steps:
      1. log에 "Total runtime:" 출력 확인
      2. attorneys.jsonl 파일 크기 > 0
      3. firm_level_summary.csv 존재
  ```

---

- [ ] 4. 재실행 결과 검증 예상 소요시간: 15분

  **What to do**:
  - Fill rate 계산:
    ```python
    import json
    lines = [json.loads(l) for l in open('outputs/attorneys.jsonl', encoding='utf-8')]
    total = len(lines)
    title = sum(1 for l in lines if l.get('title',''))
    office = sum(1 for l in lines if l.get('offices',[]))
    dept = sum(1 for l in lines if l.get('department',''))
    print(f'Title: {title/total*100:.1f}%')
    print(f'Office: {office/total*100:.1f}%')
    print(f'Dept: {dept/total*100:.1f}%')
    ```
  - Nav pollution 체크:
    ```python
    nav = {'home','search','menu','back','contact','about','close','login','back to menu','main menu','careers','news','events','people','professionals','offices','subscribe','sign in','sign up','next','previous','print','share','submit'}
    count = sum(1 for l in lines for pa in l.get('practice_areas',[]) if pa.lower().strip() in nav)
    print(f'Nav pollution: {count}')
    ```
  - Firm count:
    ```python
    firms = set(l.get('firm','') for l in lines if l.get('full_name') or l.get('title'))
    print(f'Firms: {len(firms)}')
    ```
  - 결과를 `.sisyphus/evidence/task-11-results-summary.json`에 저장

  **목표 달성 현실적 기대치:**

  | 지표 | 목표 | 현실적 예상 | 이유 |
  |------|------|-----------|------|
  | Title | ≥90% | ~83-85% | 뉴스 헤드라인이 title로 잘못 추출되는 firm이 많음. validators가 정당하게 거부 |
  | Office | ≥60% | ~30-35% | HTML 구조에서 office 추출 자체가 어려움. validators 완화만으로 해결 불가 |
  | Dept | ≥85% | ~80-82% | practice_areas → department 매핑이 35개로 제한적 |
  | Firms | ≥140 | ~70-80 | BOT_PROTECTED ~26개 + discovery 실패 ~100개는 코드 변경으로 해결 불가 |
  | Nav pollution | 0 | 0 | validators wiring + "about"/"contact" 추가로 해결됨 |

  **중요**: 목표 미달 시에도 **개선 방향은 올바르다**는 점을 기록. 추가 개선은 별도 플랜 필요.

  **Recommended Agent Profile**: `quick`

---

- [ ] 5. Final Verification Wave (F1-F4) 예상 소요시간: 30분

  **What to do**:
  - 4개 검증 에이전트 병렬 실행 (field-quality-v2.md 원본 플랜의 F1-F4 정의 참조):

  **F1. Plan Compliance Audit** — `oracle`
  - field-quality-v2.md 플랜의 "Must Have" 각각 확인
  - "Must NOT Have" 각각 codebase 검색으로 확인
  - evidence 파일 존재 확인

  **F2. Code Quality Review** — `unspecified-high`
  - 변경된 5개 파일에 대해 lint/quality 체크
  - bare print() 금지 (library module), empty except, commented-out code, unused imports
  - SyntaxWarning (regex raw string) 체크

  **F3. Real Manual QA** — `unspecified-high`
  - `python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 10` 실행
  - 8개 필드 모두 populated 확인
  - 이전 실패 firm 1개 실행 (Morgan Lewis 등)
  - BOT_PROTECTED firm 1개 실행 (Jones Day — 즉시 skip 확인)
  - JSONL에서 nav pollution 0건 확인

  **F4. Scope Fidelity Check** — `deep`
  - 각 task의 "What to do" vs 실제 diff 비교
  - "Must NOT do" compliance 검증
  - cross-task 오염 검사

  **모든 F1-F4가 APPROVE여야 함. 하나라도 REJECT면 수정 후 재검증.**

  **Recommended Agent Profile**: 각각 다른 agent (oracle, unspecified-high, unspecified-high, deep) 병렬 실행

---

- [ ] 6. 결과 보고 + 사용자 Okay 예상 소요시간: 5분

  **What to do**:
  - F1-F4 결과 종합
  - Fill rate 결과 vs 목표 표로 정리
  - 목표 미달 항목에 대한 근본 원인 + 추후 개선 방향 기록
  - 사용자에게 보고 → explicit "okay" 대기

---

## Final Verification Wave (MANDATORY)

> 원본 field-quality-v2.md 플랜의 F1-F4 정의를 그대로 따름.
> 4개 에이전트 병렬 실행. 모두 APPROVE 필요.
> 사용자 explicit okay 후 완료.

- [ ] F1. **Plan Compliance Audit** — `oracle`
- [ ] F2. **Code Quality Review** — `unspecified-high`
- [ ] F3. **Real Manual QA** — `unspecified-high`
- [ ] F4. **Scope Fidelity Check** — `deep`

---

## Commit Strategy

| # | Type | Files | Message |
|---|------|-------|---------|
| 1 | feat | validators.py, find_attorney.py, field_enricher.py, multi_mode_extractor.py, config/practice_department_map.json | `feat: wire validators into find_attorney.py pipeline + field quality improvements (T1-T10)` |
| 2 | fix | validators.py | `fix(validators): add 'about' and 'contact' to _JUNK_PHRASES nav filter` |
| 3 | run | outputs/* | `run: full 200-firm rerun with validators applied` |

---

## Success Criteria

### Verification Commands
```bash
# Fill rates
python -c "import json; lines=[json.loads(l) for l in open('outputs/attorneys.jsonl', encoding='utf-8')]; t=len(lines); print(f'Title: {sum(1 for l in lines if l.get(\"title\",\"\"))/t*100:.1f}%'); print(f'Office: {sum(1 for l in lines if l.get(\"offices\",[]))/t*100:.1f}%'); print(f'Dept: {sum(1 for l in lines if l.get(\"department\",\"\"))/t*100:.1f}%')"

# Nav pollution
python -c "import json; nav={'home','search','menu','back','contact','about','close','login','back to menu','main menu','careers','news','events','people','professionals','offices','subscribe'}; c=sum(1 for l in open('outputs/attorneys.jsonl',encoding='utf-8') for pa in json.loads(l).get('practice_areas',[]) if pa.lower().strip() in nav); print(f'Nav: {c}')"

# Firm count
python -c "import json; f=set(json.loads(l).get('firm','') for l in open('outputs/attorneys.jsonl',encoding='utf-8')); print(f'Firms: {len(f)}')"
```

### Final Checklist
- [ ] validators.py wired into find_attorney.py
- [ ] Nav pollution = 0
- [ ] Fill rates measured and recorded
- [ ] F1-F4 all APPROVE
- [ ] User explicit okay received

---

## 다른 컴퓨터에서 시작하는 방법

### Quick Start (복붙용)

```bash
# 1. Pull latest code
git pull origin main

# 2. Install dependencies
pip install requests beautifulsoup4 lxml openpyxl

# 3. Verify setup
python -c "from validators import validate_practice_areas; print('OK')"
python -c "import find_attorney; print('OK')"

# 4. Backup existing data
cp outputs/attorneys.jsonl outputs/attorneys_pre_rerun.jsonl 2>/dev/null || true

# 5. Run pipeline (background, ~40-50 hours)
PYTHONIOENCODING=utf-8 nohup python find_attorney.py \
  "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" \
  --output-dir outputs \
  > outputs/run_rerun_log.txt 2>&1 &
echo "PID: $!"

# 6. Monitor
tail -f outputs/run_rerun_log.txt | grep -E "Processing:|Total:|SKIP|ABORT"
```

### OpenCode로 이어서 하기

```
/start-work field-quality-v2-rerun
```

이 플랜을 Sisyphus가 자동으로 실행합니다.
