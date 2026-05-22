# Pipeline Remediation — Stuck 방지 + Resume + 200펌 전체 실행

## TL;DR

> **Quick Summary**: T13 실행에서 Mayer Brown SPA 사이트(1960 URL)에서 11시간 stuck된 문제를 해결. Playwright enrichment에 firm-level timeout 추가, SIGINT 핸들러로 graceful shutdown, resume 기능으로 중단 후 재시작, JSONL 중복 정리 유틸리티를 구현한 뒤 200개 펌 전체를 안정적으로 실행.
> 
> **Deliverables**:
> - Enrichment phase firm-level timeout (MAX_ENRICHMENT_TIME_PER_FIRM = 1800초)
> - SIGINT signal handler + graceful shutdown
> - JSONL dedup 유틸리티 (`dedup_jsonl.py`)
> - `--resume` CLI argument + skip-already-processed logic
> - Output crash safety (Excel rebuild from JSONL)
> - department inference 검증/수정
> - 200개 펌 전체 실행 + 결과 검증
> 
> **Estimated Effort**: Medium (에이전트 코드 수정 ~3-4시간 + 파이프라인 런타임 ~8-16시간)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 7 (clean outputs) → Task 8 (full run) → F1-F4

---

## Context

### Original Request
T13 전체 실행이 Mayer Brown에서 11시간 stuck. 유저가 분석 + 보완 플랜 요청.

### Interview Summary
**Key Discussions**:
- T1-T12 전부 완료 (61/61 tests pass), T13 부분 실행 (13/200 펌)
- Mayer Brown: SPA 사이트, sitemap에서 1960 URL 발견 → PLAYWRIGHT_ONLY 모드로 8 thread enrichment → 무한 대기
- `attorneys.jsonl`: 44,725줄이지만 중복 제거 시 18,769 unique (multiple append run)
- 유저가 즉시 보완 플랜 수립 선택

**Research Findings**:

1. **Playwright stuck 원인**: `MAX_FIRM_TIME=45s`는 discovery에만 적용. Enrichment phase에는 firm-level timeout 없음. `_run_batch_shared_browser`가 8 thread로 URL 분배 → per-profile/per-firm timeout 없이 무한 루프. SIGINT 핸들러도 없어서 Ctrl+C 불가.

2. **PLAYWRIGHT_ONLY 트리거**: bot protection 시그널 (line 1470-1472), SPA 패턴 감지 (line 2384), >50% profile blocked (line 4113-4116).

3. **Resume 기능 없음**: `attorneys.jsonl`은 `'a'` (append) 모드로 열림 (line 567-568). `firm_level_summary.csv`와 `coverage_metrics.json`은 전체 실행 끝에 overwrite. `--resume` CLI arg 없음.

4. **Per-firm 필드 품질** (13펌 deduplicated):
   - department: 10/13 펌에서 0% (Kirkland 99.7% 유일)
   - industries: 대부분 sentinel ("no industry field") — Gibson Dunn, Kirkland, Paul Hastings 등 0%
   - offices: 5개 펌에서 0% (King&Spalding, Quinn Emanuel, Simpson Thacher, Sullivan&Cromwell, White&Case)
   - bar_admissions: Quinn Emanuel 0%, Simpson Thacher 0%, King&Spalding 2.6%
   - White & Case: 1명만 추출됨 (심각한 문제)

### Metis Review
**Identified Gaps** (addressed):
- Enrichment timeout에서 orphaned Playwright 브라우저 프로세스 정리 필요
- JSONL 마지막 줄 corruption 대비 (hard kill 시 truncated JSON)
- department 0%는 `infer_department_from_practices()`가 실제로 fire되지 않는 문제일 수 있음 → 검증 필요
- industries sentinel은 웹사이트에 해당 필드가 없는 것이 정상일 수 있음 → 추출 로직 변경 X, 있는 데이터만 수집
- offices 0%인 펌들은 discovery에서 URL은 찾았지만 enrichment에서 office 파싱 실패 → 사이트 구조 차이
- 부분 완료된 firm의 resume 처리 (일부 attorney만 JSONL에 기록된 상태)
- network transient failure 시 8 worker thread 동시 실패 가능성
- disk space 소진 가능성 (200펌 × ~2000 attorneys)

---

## Work Objectives

### Core Objective
find_attorney.py 파이프라인의 안정성을 강화하여 200개 AmLaw 펌 전체를 hang 없이, 중단 후 재시작 가능하게, 중복 없이 처리한다.

### Concrete Deliverables
- `find_attorney.py`: enrichment timeout + SIGINT handler + resume logic
- `dedup_jsonl.py`: JSONL 중복 제거 유틸리티 (신규)
- `outputs/attorneys.jsonl`: 200개 펌 전체 추출 결과 (deduplicated)
- `outputs/*_attorneys.xlsx`: Excel 출력
- `outputs/firm_level_summary.csv`: 전체 펌 요약
- `outputs/coverage_metrics.json`: 전체 커버리지 메트릭

### Definition of Done
- [ ] `--debug-firm "Mayer Brown" --limit 50` → 10분 이내 완료 (stuck 없음)
- [ ] Ctrl+C → 30초 이내 clean exit, orphaned chromium 없음
- [ ] `--resume` → 이미 처리된 펌 skip, 중복 없음
- [ ] 200개 펌 전체 실행 완료 (human intervention 없이)
- [ ] JSONL에 duplicate profile_url 없음
- [ ] 기존 61/61 테스트 유지 (regression 없음)

### Must Have
- Enrichment phase firm-level timeout (default 30분)
- SIGINT/Ctrl+C graceful shutdown
- `--resume` CLI argument
- JSONL 중복 제거
- 200개 펌 전체 실행
- 부분 결과 보존 (timeout/interrupt 시)

### Must NOT Have (Guardrails)
- ❌ discovery 로직 변경
- ❌ extraction heuristic / field parsing 패턴 변경
- ❌ 새 CSS selector나 추출 패턴 추가
- ❌ firm-specific 하드코딩 (kirkland_scroll 등 기존 것은 유지, 새로 추가 X)
- ❌ firm processing 병렬화 (순차 유지)
- ❌ retry 로직 추가 (기존 Stage 2 Playwright fallback만 사용)
- ❌ NLP/ML 기반 필드 추론
- ❌ 새 외부 디렉토리 추가
- ❌ Cloudflare/bot-protection 우회
- ❌ AttorneyProfile 데이터클래스 변경

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest, 61/61 PASS)
- **Automated tests**: YES (Tests-after — 기존 테스트 유지 + 신규 기능 테스트 추가)
- **Framework**: pytest (pyproject.toml 설정 완료)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **CLI/Pipeline**: Use Bash — Run command, validate output, check exit code
- **File integrity**: Use Python one-liners — Parse JSONL, count records, verify dedup
- **Process cleanup**: Use `tasklist`/`taskkill` — Verify no orphaned chromium

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — core stability fixes, MAX PARALLEL):
├── Task 1: Enrichment firm-level timeout [deep] — 예상: 20분
├── Task 2: SIGINT signal handler + graceful shutdown [deep] — 예상: 15분
└── Task 3: JSONL dedup utility (dedup_jsonl.py) [quick] — 예상: 10분

Wave 2 (After Wave 1 — resume + crash safety):
├── Task 4: Resume capability (--resume CLI arg) [deep] — 예상: 20분
│   (depends: Task 1, 2 — needs timeout+signal to be in place first)
└── Task 5: Output crash safety (Excel rebuild from JSONL) [unspecified-high] — 예상: 15분
    (depends: Task 1 — needs timeout to ensure partial saves work)

Wave 3 (After Wave 2 — verification + department check):
└── Task 6: Department inference verification [unspecified-high] — 예상: 15분
    (depends: Task 4 — needs resume to test incrementally)

Wave 4 (After Wave 3 — clean + full run):
├── Task 7: Clean old outputs + dedup existing data [quick] — 예상: 5분
│   (depends: Task 3 — needs dedup utility)
└── Task 8: Full 200-firm pipeline run [deep] — 예상: 8-16시간 (무인 실행)
    (depends: Task 1-7 all complete)

Wave FINAL (After Task 8):
├── F1: Plan compliance audit (oracle) — 예상: 10분
├── F2: Code quality review (unspecified-high) — 예상: 10분
├── F3: Real manual QA (unspecified-high) — 예상: 15분
└── F4: Scope fidelity check (deep) — 예상: 10분
→ Present results → Get explicit user okay
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | — | 2, 4, 5, 8 | 1 |
| 2 | — | 4, 8 | 1 |
| 3 | — | 7 | 1 |
| 4 | 1, 2 | 6, 8 | 2 |
| 5 | 1 | 8 | 2 |
| 6 | 4 | 8 | 3 |
| 7 | 3 | 8 | 4 |
| 8 | 1-7 | F1-F4 | 4 |
| F1-F4 | 8 | — | FINAL |

### Agent Dispatch Summary

- **Wave 1**: **3 tasks** — T1 → `deep`, T2 → `deep`, T3 → `quick`
- **Wave 2**: **2 tasks** — T4 → `deep`, T5 → `unspecified-high`
- **Wave 3**: **1 task** — T6 → `unspecified-high`
- **Wave 4**: **2 tasks** — T7 → `quick`, T8 → `deep`
- **FINAL**: **4 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Enrichment Firm-Level Timeout — 예상: 20분

  **What to do**:
  - `find_attorney.py`에 `MAX_ENRICHMENT_TIME_PER_FIRM = 1800` 상수 추가 (30분, line 69 부근 기존 상수들 옆)
  - `_run_batch_shared_browser()` 메서드 (line ~4050-4080)에 thread cancellation flag 패턴 추가:
    - `threading.Event()` 기반 `_enrichment_stop_event` 생성
    - 각 worker thread가 URL 반복문 내에서 매 iteration마다 `_enrichment_stop_event.is_set()` 체크 → set이면 즉시 break
    - `t.join(timeout=MAX_ENRICHMENT_TIME_PER_FIRM)` 으로 thread join timeout 설정
    - timeout 후 `_enrichment_stop_event.set()` → 모든 worker에게 중지 신호
  - `process_firm()` 메서드에서 enrichment 호출 부분에 firm-level timeout 래핑:
    - enrichment 시작 시각 기록
    - 완료 또는 timeout 후, 부분 결과를 JSONL에 저장
    - timeout 로그: `logger.warning(f"Firm {firm} enrichment timed out after {MAX_ENRICHMENT_TIME_PER_FIRM}s, saving {n} partial results")`
  - Playwright 브라우저 정리: timeout 후 `browser.close()` 호출하여 orphaned chromium 방지
  - 기존 `PLAYWRIGHT_PAGE_TIMEOUT = 20000` (20초 per page) 유지 — 변경 X

  **Must NOT do**:
  - discovery 로직 변경
  - `MAX_FIRM_TIME` 값 변경 (discovery용 유지)
  - firm processing 순서나 병렬화 변경
  - 기존 per-page timeout 값 변경

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 복잡한 threading 패턴 + 기존 코드 구조 이해 필요
  - **Skills**: []
    - No special skills needed — pure Python threading work

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5, 8
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `find_attorney.py:69` — `MAX_FIRM_TIME = 45` (기존 timeout 상수 위치, 새 상수를 여기 근처에 추가)
  - `find_attorney.py:4050-4080` — `_run_batch_shared_browser()` (8 thread worker loop, URL 분배 로직)
  - `find_attorney.py:4036-4044` — per-URL exception handling 패턴 (이 패턴을 따라 timeout도 같은 방식으로 처리)
  - `find_attorney.py:1470-1472` — PLAYWRIGHT_ONLY 트리거 로직 (bot protection signal)
  - `find_attorney.py:4113-4116` — >50% blocked → Playwright escalation 로직

  **API/Type References**:
  - Python `threading.Event()` — `set()`, `is_set()`, `wait(timeout)` 메서드
  - Python `threading.Thread.join(timeout=N)` — timeout 후 thread가 살아있는지 `t.is_alive()` 체크

  **WHY Each Reference Matters**:
  - line 69: 새 상수 위치 결정 — 기존 상수들과 같은 블록에 배치
  - line 4050-4080: 이 함수가 실제 Playwright enrichment을 수행하는 곳 — 여기에 stop_event 체크 삽입
  - line 4036-4044: exception handling 패턴 — timeout도 이 패턴 따라 catch + log + continue
  - line 1470-1472, 4113-4116: PLAYWRIGHT_ONLY 진입 조건 이해 — 이 조건은 변경하지 않음

  **Acceptance Criteria**:
  - [ ] `MAX_ENRICHMENT_TIME_PER_FIRM` 상수가 `find_attorney.py`에 존재
  - [ ] `_enrichment_stop_event` 패턴이 `_run_batch_shared_browser()`에 구현
  - [ ] `pytest tests/ -v` → 0 failures (regression 없음)

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Timeout이 정상 작동하여 stuck 방지
    Tool: Bash
    Preconditions: find_attorney.py에 timeout 코드 추가 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 10
      2. 실행 시간 측정 (PowerShell: Measure-Command { ... })
      3. 완료 후 outputs/attorneys.jsonl에 Kirkland 데이터 존재 확인
    Expected Result: 2분 이내 완료, JSONL에 Kirkland 레코드 10개 이하 존재
    Failure Indicators: 5분 이상 소요, 또는 프로세스가 hang
    Evidence: .sisyphus/evidence/task-1-timeout-kirkland.txt

  Scenario: Timeout 후 orphaned chromium 프로세스 없음
    Tool: Bash
    Preconditions: Timeout 테스트 완료 직후
    Steps:
      1. tasklist | findstr /i "chromium" (Windows)
      2. 또는 Get-Process | Where-Object { $_.ProcessName -match "chromium" }
    Expected Result: chromium 프로세스 0개 (또는 테스트 전과 동일한 수)
    Failure Indicators: 테스트 후 새로운 chromium 프로세스 존재
    Evidence: .sisyphus/evidence/task-1-no-orphan-chromium.txt

  Scenario: Regression 테스트 통과
    Tool: Bash
    Preconditions: 코드 변경 완료
    Steps:
      1. pytest tests/ -v
    Expected Result: 61+ passed, 0 failed
    Failure Indicators: 1개 이상 failure
    Evidence: .sisyphus/evidence/task-1-regression-tests.txt
  ```

  **Commit**: YES
  - Message: `fix(enrichment): add firm-level timeout to prevent Playwright stuck`
  - Files: `find_attorney.py`
  - Pre-commit: `pytest tests/ -v`

---

- [x] 2. SIGINT Signal Handler + Graceful Shutdown — 예상: 15분

  **What to do**:
  - `find_attorney.py`의 `run()` 메서드 시작 부분에 SIGINT 핸들러 등록:
    ```python
    import signal
    self._shutdown_requested = False
    def _signal_handler(signum, frame):
        logger.warning("SIGINT received — finishing current firm and shutting down...")
        self._shutdown_requested = True
    signal.signal(signal.SIGINT, _signal_handler)
    ```
  - 메인 firm loop (line 1163: `for firm, url in firms:`)에서 매 iteration 시작 시 `self._shutdown_requested` 체크:
    ```python
    if self._shutdown_requested:
        logger.info(f"Shutdown requested. Processed {i}/{len(firms)} firms. Saving outputs...")
        break
    ```
  - loop break 후 기존 output 저장 로직 (Excel save, JSONL close, summary write, coverage write)이 정상 실행되도록 보장
  - enrichment 진행 중 SIGINT 수신 시: 현재 firm의 enrichment 완료 대기 (Task 1의 timeout에 의해 최대 30분) 또는 `_enrichment_stop_event.set()` 호출하여 즉시 중단 후 부분 결과 저장
  - Playwright 브라우저 cleanup: shutdown 시 모든 열린 browser를 close
  - Windows 호환성: `signal.SIGINT`는 Windows에서도 Ctrl+C로 작동. `signal.SIGTERM`은 Windows에서 지원 안 됨 → SIGINT만 처리

  **Must NOT do**:
  - `signal.SIGTERM` 핸들러 추가 (Windows 미지원)
  - firm processing 순서 변경
  - 기존 output 저장 로직 구조 변경 (순서만 유지)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: signal handling + threading 상호작용 + Playwright cleanup 복합 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Tasks 4, 8
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `find_attorney.py:1163` — 메인 firm loop (`for firm, url in firms:`)
  - `find_attorney.py:1253-1274` — output 저장 로직 (Excel save, JSONL close, summary CSV, coverage JSON)
  - `find_attorney.py:567-568` — JSONL file open in `__init__()` (shutdown 시 flush + close 필요)
  - `find_attorney.py:4050-4080` — `_run_batch_shared_browser()` (Task 1에서 추가한 stop_event와 연동)

  **External References**:
  - Python `signal` module — `signal.signal(signal.SIGINT, handler)` 패턴
  - Windows signal 제한: SIGINT만 Ctrl+C에 매핑, SIGTERM은 프로세스 kill에만 사용

  **WHY Each Reference Matters**:
  - line 1163: SIGINT 후 firm loop를 break하는 위치
  - line 1253-1274: break 후에도 이 코드가 실행되어야 함 (partial output 저장)
  - line 567-568: JSONL 파일을 flush+close해야 데이터 손실 방지
  - line 4050-4080: enrichment 중 SIGINT 시 stop_event.set()으로 thread들을 빠르게 종료

  **Acceptance Criteria**:
  - [ ] `signal.signal(signal.SIGINT, ...)` 핸들러가 `run()` 메서드에 등록됨
  - [ ] `self._shutdown_requested` 체크가 firm loop에 존재
  - [ ] shutdown 시 JSONL, Excel, summary, coverage 모두 저장됨
  - [ ] `pytest tests/ -v` → 0 failures

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Ctrl+C로 graceful shutdown
    Tool: Bash
    Preconditions: SIGINT 핸들러 구현 완료
    Steps:
      1. 별도 PowerShell에서: Start-Process python -ArgumentList "find_attorney.py", """AmLaw200_2025 Rank_gross revenue_with_websites.xlsx""", "--max-firms", "5" -PassThru 로 프로세스 시작
      2. 30초 대기 후 프로세스에 Ctrl+C 전송 (또는 Stop-Process)
      3. outputs/attorneys.jsonl 존재 확인 및 내용 검증
      4. tasklist | findstr /i "chromium" 으로 orphaned 프로세스 확인
    Expected Result: 프로세스가 30초 이내 종료, JSONL에 1-2개 펌 데이터 존재, chromium 0개
    Failure Indicators: 프로세스가 종료 안 됨, JSONL 빈 파일, chromium 잔존
    Evidence: .sisyphus/evidence/task-2-sigint-shutdown.txt

  Scenario: JSONL 무결성 (truncated line 없음)
    Tool: Bash
    Preconditions: Ctrl+C 테스트 후
    Steps:
      1. python -c "import json; [json.loads(l) for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip()]; print('OK')"
    Expected Result: "OK" 출력 (모든 줄이 유효한 JSON)
    Failure Indicators: json.JSONDecodeError 발생
    Evidence: .sisyphus/evidence/task-2-jsonl-integrity.txt
  ```

  **Commit**: YES
  - Message: `fix(pipeline): add SIGINT handler for graceful shutdown`
  - Files: `find_attorney.py`
  - Pre-commit: `pytest tests/ -v`

- [x] 3. JSONL Dedup Utility — 예상: 10분

  **What to do**:
  - 새 파일 `dedup_jsonl.py` 생성 (프로젝트 루트):
    - CLI: `python dedup_jsonl.py <input.jsonl> [--output <output.jsonl>]`
    - 기본 동작: input을 읽어 `profile_url` 기준 중복 제거, 같은 URL의 여러 레코드 중 가장 많은 비어있지 않은 필드를 가진 레코드 보존
    - output 미지정 시 `<input>_deduped.jsonl` 생성 (원본 보존)
    - 통계 출력: 입력 줄 수, 고유 URL 수, 제거된 중복 수, 펌별 카운트
  - malformed line 처리: JSON parse 실패 시 해당 줄 스킵 + 경고 (hard kill로 인한 truncated line 대비)
  - encoding: 항상 `utf-8`
  - profile_url이 없는 레코드: `firm + full_name`을 dedup key로 사용 (fallback)
  - 모듈 상단에 `#!/usr/bin/env python3` + docstring + `from __future__ import annotations`

  **Must NOT do**:
  - 기존 파일 수정
  - 원본 JSONL 덮어쓰기 (별도 출력)
  - 레코드 내용 변경 (dedup만, 필드 수정 X)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 단순한 단일 파일 스크립트, 복잡한 의존성 없음
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 7
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `find_attorney.py:1234-1237` — JSONL 레코드 형식 (`att.to_dict()` → JSON line)
  - `find_attorney.py:567-568` — JSONL file open 패턴 (encoding='utf-8')
  - `run_pipeline.py:380-407` — 기존 JSONL 로딩 패턴 (resume에서 사용)

  **WHY Each Reference Matters**:
  - line 1234-1237: JSONL 레코드 구조 이해 — 어떤 필드가 있는지, `to_dict()` 출력 형식
  - line 567-568: encoding 패턴 확인 — 반드시 utf-8
  - line 380-407: run_pipeline.py의 JSONL 로딩 패턴 참고 — 유사한 방식으로 구현

  **Acceptance Criteria**:
  - [ ] `dedup_jsonl.py` 파일 존재
  - [ ] 테스트 데이터에서 정확한 중복 제거 확인

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 중복 제거 정확성
    Tool: Bash
    Preconditions: dedup_jsonl.py 생성 완료
    Steps:
      1. python -c "
         import json
         # Create test data with duplicates
         records = [
           {'firm': 'TestFirm', 'full_name': 'John Doe', 'profile_url': 'http://test.com/john', 'title': 'Partner'},
           {'firm': 'TestFirm', 'full_name': 'John Doe', 'profile_url': 'http://test.com/john', 'title': ''},
           {'firm': 'TestFirm', 'full_name': 'Jane Smith', 'profile_url': 'http://test.com/jane', 'title': 'Associate'},
         ]
         with open('test_dedup.jsonl', 'w', encoding='utf-8') as f:
           for r in records:
             f.write(json.dumps(r) + '\n')
         "
      2. python dedup_jsonl.py test_dedup.jsonl --output test_dedup_out.jsonl
      3. python -c "lines = open('test_dedup_out.jsonl','r',encoding='utf-8').readlines(); print(f'Lines: {len(lines)}'); import json; d=json.loads(lines[0]); print(f'First title: {d.get(\"title\")}')"
    Expected Result: Lines: 2, First title: Partner (더 완전한 레코드 보존)
    Failure Indicators: Lines != 2, 또는 빈 title 레코드가 보존됨
    Evidence: .sisyphus/evidence/task-3-dedup-accuracy.txt

  Scenario: Malformed line 처리
    Tool: Bash
    Preconditions: dedup_jsonl.py 생성 완료
    Steps:
      1. python -c "
         with open('test_malformed.jsonl', 'w', encoding='utf-8') as f:
           f.write('{\"firm\": \"A\", \"full_name\": \"X\", \"profile_url\": \"http://a.com/x\"}\n')
           f.write('{truncated json\n')
           f.write('{\"firm\": \"B\", \"full_name\": \"Y\", \"profile_url\": \"http://b.com/y\"}\n')
         "
      2. python dedup_jsonl.py test_malformed.jsonl --output test_malformed_out.jsonl
      3. python -c "lines=open('test_malformed_out.jsonl','r',encoding='utf-8').readlines(); print(f'Lines: {len(lines)}')"
    Expected Result: Lines: 2 (malformed line 스킵, 나머지 보존), 경고 메시지 출력
    Failure Indicators: crash 또는 Lines != 2
    Evidence: .sisyphus/evidence/task-3-malformed-handling.txt
  ```

  **Commit**: YES
  - Message: `feat(tools): add JSONL dedup utility`
  - Files: `dedup_jsonl.py`
  - Pre-commit: 위 QA scenarios 통과

---

- [x] 4. Resume Capability (--resume CLI) — 예상: 20분

  **What to do**:
  - `find_attorney.py`의 `_parse_args()` (line 4900)에 `--resume` argument 추가:
    ```python
    parser.add_argument("--resume", action="store_true", help="Resume from existing JSONL — skip already-processed firms")
    ```
  - `AttorneyFinder.__init__()`에서 resume 모드 처리:
    - `self.resume_mode = args.resume` (또는 생성자 파라미터)
    - resume=True일 때: 기존 JSONL 파일 로드 → `profile_url` 또는 `firm` 기준으로 이미 처리된 firm set 추출
    - `self.processed_firms: set[str]` — 이미 JSONL에 존재하는 firm 이름 set
  - JSONL 파일 모드 선택:
    - resume=False: `'w'` 모드 (새로 시작, 기존 데이터 제거)
    - resume=True: `'a'` 모드 (기존 데이터에 추가)
  - 메인 firm loop에서 skip 로직:
    ```python
    if self.resume_mode and firm in self.processed_firms:
        logger.info(f"[RESUME] Skipping already-processed firm: {firm}")
        continue
    ```
  - 부분 완료된 firm 처리: firm 이름이 JSONL에 존재하지만 attorney 수가 expected보다 적은 경우 → 해당 firm은 "처리 완료"로 간주 (보수적 접근 — re-processing보다 안전)
  - summary CSV와 coverage JSON도 resume 시 기존 데이터 로드 + 신규 펌 데이터 append 후 write
  - Excel은 resume 시 기존 JSONL 전체를 읽어 rebuild (Task 5에서 구현)

  **Must NOT do**:
  - 기존 JSONL 데이터 수정/삭제 (append만)
  - 부분 완료된 firm의 기존 레코드 삭제 후 re-process
  - `--resume` 없이 실행 시 동작 변경 (기존 동작 유지, 단 JSONL을 'w' 모드로 변경)
  - firm processing 순서 변경

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 여러 출력 파일의 resume 로직 + JSONL 파싱 + CLI arg 추가 등 복합 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (with Task 5)
  - **Blocks**: Tasks 6, 8
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `find_attorney.py:4900-4989` — `_parse_args()` + `main()` (CLI arg 추가 위치)
  - `find_attorney.py:567-568` — JSONL file open in `__init__()` (모드 변경: 'a' → 조건부)
  - `find_attorney.py:1163` — firm loop (skip 로직 삽입 위치)
  - `find_attorney.py:1264-1272` — summary CSV + coverage JSON write (resume 시 기존 데이터 merge)
  - `run_pipeline.py:380-407` — 기존 JSONL 로딩 패턴 (resume 참고 구현)
  - `run_pipeline.py:483-485` — `--resume`, `--skip-discovery` arg 패턴

  **WHY Each Reference Matters**:
  - line 4900-4989: CLI arg 추가 + main() 함수에서 resume flag 전달 방법
  - line 567-568: JSONL 열기 방식 변경 (resume 여부에 따라 'w' 또는 'a')
  - line 1163: firm loop에 skip 조건 삽입할 정확한 위치
  - line 1264-1272: resume 시 기존 summary/coverage 데이터와 merge 필요
  - run_pipeline.py line 380-407, 483-485: 기존 resume 구현 참고 (같은 프로젝트의 다른 스크립트)

  **Acceptance Criteria**:
  - [ ] `--resume` CLI argument 존재
  - [ ] resume 시 이미 처리된 firm skip
  - [ ] resume 시 JSONL에 중복 없음
  - [ ] resume 없이 실행 시 JSONL이 'w' 모드로 열림

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Resume가 이미 처리된 firm을 skip
    Tool: Bash
    Preconditions: Task 1, 2 완료. 기존 attorneys.jsonl에 데이터 존재.
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 5
      2. python -c "import json; firms=set(); [firms.add(json.loads(l).get('firm')) for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip()]; print(f'Firms: {firms}')"
      3. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Kirkland" --limit 5 --resume
      4. 로그에서 "[RESUME] Skipping already-processed firm: Kirkland" 메시지 확인
      5. python -c "import json; c=0; [c:=c+1 for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip() and json.loads(l).get('firm')=='Kirkland']; print(f'Kirkland records: {c}')"
    Expected Result: Step 4에서 RESUME skip 메시지. Step 5에서 레코드 수가 Step 2와 동일 (중복 없음)
    Failure Indicators: resume 후 Kirkland 레코드 수 증가 (중복 발생), 또는 skip 메시지 없음
    Evidence: .sisyphus/evidence/task-4-resume-skip.txt

  Scenario: --resume 없이 실행 시 기존 데이터 초기화
    Tool: Bash
    Preconditions: attorneys.jsonl에 기존 데이터 존재
    Steps:
      1. python -c "print(sum(1 for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip()))"
      2. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Latham" --limit 3
      3. python -c "import json; firms=set(); [firms.add(json.loads(l).get('firm')) for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip()]; print(f'Firms: {firms}')"
    Expected Result: Step 3에서 firms={'Latham'} (기존 Kirkland 데이터 사라짐, 'w' 모드)
    Failure Indicators: 기존 firm 데이터가 남아있음 (여전히 'a' 모드)
    Evidence: .sisyphus/evidence/task-4-write-mode.txt
  ```

  **Commit**: YES
  - Message: `feat(pipeline): add --resume flag to skip already-processed firms`
  - Files: `find_attorney.py`
  - Pre-commit: `pytest tests/ -v`

- [x] 5. Output Crash Safety (Excel Rebuild from JSONL) — 예상: 15분

  **What to do**:
  - 현재 문제: Excel은 메모리에 누적 후 맨 마지막에 한 번 저장 (line 1253-1257). crash 시 Excel 전부 손실.
  - 해결 방법 A (권장): `run()` 종료 직전 (정상/shutdown/exception 모두)에 JSONL을 읽어 Excel을 rebuild:
    - `_rebuild_excel_from_jsonl(jsonl_path, excel_path)` 메서드 추가
    - JSONL의 모든 레코드를 읽어 firm별로 그룹핑 → Excel 시트에 작성
    - 기존 in-memory 누적 방식은 유지 (정상 완료 시 사용)
    - crash/shutdown 시에만 rebuild 사용 (JSONL이 source of truth)
  - `try/finally` 블록으로 output 저장을 감싸서 exception 시에도 저장 보장:
    ```python
    try:
        for firm, url in firms:
            ...
    finally:
        self._save_all_outputs(excel_path)  # Excel, summary, coverage 모두 저장
    ```
  - summary CSV와 coverage JSON도 같은 패턴: 축적된 데이터를 finally 블록에서 저장

  **Must NOT do**:
  - 매 firm마다 Excel 저장 (성능 문제 — 200펌이면 200번 write)
  - JSONL 형식이나 내용 변경
  - 기존 output 저장 로직 삭제 (finally에 이동만)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: output pipeline 구조 변경 + error handling 패턴
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 4)
  - **Blocks**: Task 8
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `find_attorney.py:1253-1274` — 기존 output 저장 로직 (Excel save, JSONL close, summary, coverage)
  - `find_attorney.py:1089-1117` — Excel workbook 생성 + sheet setup
  - `find_attorney.py:1168-1230` — per-firm Excel row 추가 로직
  - `find_attorney.py:1234-1237` — JSONL 레코드 형식

  **WHY Each Reference Matters**:
  - line 1253-1274: 이 블록을 try/finally로 감싸야 함
  - line 1089-1117: Excel rebuild 시 같은 workbook/sheet 구조 재현 필요
  - line 1168-1230: rebuild에서 같은 row 형식 사용
  - line 1234-1237: JSONL → dict 변환 패턴

  **Acceptance Criteria**:
  - [ ] `try/finally` 블록으로 output 저장 보장
  - [ ] `_rebuild_excel_from_jsonl()` 메서드 존재
  - [ ] crash 시에도 Excel 파일 생성됨

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 정상 완료 시 Excel 생성 확인
    Tool: Bash
    Preconditions: Task 5 코드 변경 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Davis Polk" --limit 5
      2. python -c "import openpyxl; wb=openpyxl.load_workbook('outputs/AmLaw200_2025 Rank_gross revenue_with_websites_attorneys.xlsx'); ws=wb.active; print(f'Rows: {ws.max_row}')"
    Expected Result: Rows >= 2 (header + data)
    Failure Indicators: 파일 없음 또는 Rows == 1 (header만)
    Evidence: .sisyphus/evidence/task-5-excel-normal.txt

  Scenario: Shutdown 후에도 output 저장됨
    Tool: Bash
    Preconditions: Task 2 (SIGINT) + Task 5 완료
    Steps:
      1. 파이프라인 시작 (--max-firms 5)
      2. 30초 후 Ctrl+C
      3. outputs/attorneys.jsonl 존재 + 내용 확인
      4. outputs/firm_level_summary.csv 존재 확인
      5. outputs/coverage_metrics.json 존재 확인
    Expected Result: 3개 파일 모두 존재하고 유효한 내용 포함
    Failure Indicators: 파일 누락 또는 빈 파일
    Evidence: .sisyphus/evidence/task-5-crash-safety.txt
  ```

  **Commit**: YES
  - Message: `fix(output): add crash safety with try/finally and Excel rebuild from JSONL`
  - Files: `find_attorney.py`
  - Pre-commit: `pytest tests/ -v`

---

- [x] 6. Department Inference Verification — 예상: 15분

  **What to do**:
  - T11에서 구현한 `infer_department_from_practices()` (field_enricher.py)가 실제 파이프라인에서 fire되는지 검증:
    - find_attorney.py에서 enrichment 후 department가 비어있을 때 이 함수가 호출되는 경로 trace
    - 13개 펌 중 department 0%인 10개 펌에서 이 함수가 실제로 호출되었지만 매핑 결과가 없는지, 아니면 호출 자체가 안 되는지 확인
  - 문제 진단:
    - `config/practice_department_map.json`에 24개 매핑이 있음 — 이 매핑이 실제 practice_areas 값과 매치되는지 확인
    - find_attorney.py의 field_enricher 통합 지점 (line numbers from T11) 에서 호출 조건 확인
    - 로그 레벨을 높여서 (`--verbose`) department inference 호출 여부 확인
  - 수정이 필요한 경우:
    - find_attorney.py에서 `infer_department_from_practices()` 호출이 누락되어 있으면 추가
    - 호출은 되지만 매핑이 불일치하면 → `practice_department_map.json`에 상위 빈도 practice area 매핑 추가 (최대 10개 추가)
    - **단, 새 extraction 패턴이나 CSS selector 추가는 금지** — 매핑 테이블만 보강

  **Must NOT do**:
  - parser_sections.py나 enrichment.py의 extraction 로직 변경
  - 새 CSS selector 추가
  - NLP/ML 기반 추론
  - AttorneyProfile 구조 변경

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 여러 파일에 걸친 데이터 흐름 추적 + 조건부 수정
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (Task 4 이후)
  - **Parallel Group**: Wave 3 (단독)
  - **Blocks**: Task 8
  - **Blocked By**: Task 4

  **References**:

  **Pattern References**:
  - `field_enricher.py` — `infer_department_from_practices()` 함수 (T11에서 구현)
  - `enrichment.py` — `infer_department_from_practices()` 호출 지점 (T11에서 통합)
  - `config/practice_department_map.json` — 24개 practice→department 매핑
  - `tests/test_practice_department_map.py` — 7/7 PASS (매핑 테스트)
  - `tests/test_enrichment_integration.py` — 11/11 PASS (통합 테스트)

  **WHY Each Reference Matters**:
  - field_enricher.py: 실제 inference 함수 — 로직이 맞는지 확인
  - enrichment.py: find_attorney.py에서 이 모듈의 함수를 호출하는지 확인
  - practice_department_map.json: 매핑 범위 확인 — 어떤 practice area가 커버되는지
  - 테스트 파일들: regression 방지

  **Acceptance Criteria**:
  - [ ] `infer_department_from_practices()` 호출 경로 확인 완료
  - [ ] department fill rate 개선 (또는 개선 불가능한 이유 문서화)
  - [ ] `pytest tests/ -v` → 0 failures

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Department inference가 실제로 fire되는지 확인
    Tool: Bash
    Preconditions: Task 6 검증/수정 완료
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Latham" --limit 10 --verbose
      2. 로그에서 "infer_department" 또는 "department" 관련 메시지 검색
      3. python -c "
         import json
         dept_count = 0
         total = 0
         for l in open('outputs/attorneys.jsonl','r',encoding='utf-8'):
           if not l.strip(): continue
           d = json.loads(l)
           if d.get('firm') == 'Latham':
             total += 1
             if d.get('department'): dept_count += 1
         print(f'Latham: {dept_count}/{total} with department')
         "
    Expected Result: department가 practice_areas 매핑을 통해 일부라도 채워짐 (0%보다 개선)
    Failure Indicators: 여전히 0% department
    Evidence: .sisyphus/evidence/task-6-department-inference.txt

  Scenario: Regression 테스트
    Tool: Bash
    Preconditions: 코드 변경 완료 (있는 경우)
    Steps:
      1. pytest tests/ -v
    Expected Result: 61+ passed, 0 failed (기존 + 매핑 추가 시 신규 테스트도 pass)
    Failure Indicators: failure 발생
    Evidence: .sisyphus/evidence/task-6-regression.txt
  ```

  **Commit**: YES (수정 있는 경우만)
  - Message: `fix(enrichment): ensure department inference fires in pipeline`
  - Files: `find_attorney.py`, `config/practice_department_map.json` (필요시)
  - Pre-commit: `pytest tests/ -v`

- [x] 7. Clean Old Outputs + Dedup Existing Data — 예상: 5분

  **What to do**:
  - 기존 `outputs/attorneys.jsonl`을 dedup (Task 3의 `dedup_jsonl.py` 사용):
    ```bash
    python dedup_jsonl.py outputs/attorneys.jsonl --output outputs/attorneys_deduped.jsonl
    ```
  - 기존 중복 JSONL 파일들 정리 (outputs/ 디렉토리의 수십 개 timestamped 파일):
    - `outputs/attorneys_2026-04-05T*.jsonl` + `.xlsx` — 이전 테스트 런 결과, 삭제
    - `outputs/attorneys_backup_t12.jsonl` — T12 백업, 보존
    - `outputs/attorneys.jsonl` → dedup 후 원본은 `attorneys_pre_remediation.jsonl`로 rename
    - `outputs/attorneys_deduped.jsonl` → `attorneys.jsonl`로 rename
  - `debug_reports/` 디렉토리의 Kirkland 개별 프로필 JSON 정리:
    - `Kirkland__lawyers_*_field_evidence.json` + `*_api_payloads.json` (수백 개) → 삭제
    - 펌별 summary JSON (`Kirkland_attorneys.json` 등)은 보존
  - 정리 후 디스크 공간 확인

  **Must NOT do**:
  - `attorneys_backup_t12.jsonl` 삭제 (T12 백업 보존)
  - 펌별 summary/compliance/coverage/metrics/profile JSON 삭제
  - `debug_reports/` 내 폴더 삭제 (kirkland/, latham/ 등)
  - dedup 전 원본 JSONL 삭제 (rename으로 보존)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 파일 정리 + dedup 유틸리티 실행, 단순 작업
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 8과는 순차)
  - **Parallel Group**: Wave 4 (Task 8 직전)
  - **Blocks**: Task 8
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `dedup_jsonl.py` — Task 3에서 생성한 dedup 유틸리티
  - `outputs/` 디렉토리 — 67개 파일 (대부분 이전 테스트 런 결과)
  - `debug_reports/` 디렉토리 — 754개 항목 (대부분 Kirkland 개별 프로필)

  **WHY Each Reference Matters**:
  - dedup_jsonl.py: 이 유틸리티로 기존 JSONL 정리
  - outputs/: 정리 대상 파일 목록 확인
  - debug_reports/: Kirkland 개별 파일이 수백 개 — 디스크 공간 확보

  **Acceptance Criteria**:
  - [ ] `outputs/attorneys.jsonl`이 deduplicated 상태
  - [ ] timestamped 테스트 파일들 삭제됨
  - [ ] Kirkland 개별 프로필 파일들 삭제됨
  - [ ] `attorneys_pre_remediation.jsonl` 백업 존재

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Dedup 후 JSONL 무결성
    Tool: Bash
    Preconditions: dedup 완료
    Steps:
      1. python -c "
         import json
         urls = []
         for l in open('outputs/attorneys.jsonl','r',encoding='utf-8'):
           if not l.strip(): continue
           d = json.loads(l)
           urls.append(d.get('profile_url',''))
         print(f'Total: {len(urls)}, Unique: {len(set(urls))}')
         assert len(urls) == len(set(u for u in urls if u)), 'Duplicates found!'
         print('No duplicates - OK')
         "
    Expected Result: Total == Unique, "No duplicates - OK"
    Failure Indicators: assertion error
    Evidence: .sisyphus/evidence/task-7-dedup-verify.txt

  Scenario: 디스크 정리 확인
    Tool: Bash
    Preconditions: 정리 완료
    Steps:
      1. Get-ChildItem outputs/attorneys_2026-04-05T* | Measure-Object
      2. Get-ChildItem debug_reports/Kirkland__lawyers_*_field_evidence.json | Measure-Object
    Expected Result: Count: 0 (둘 다)
    Failure Indicators: Count > 0
    Evidence: .sisyphus/evidence/task-7-cleanup-verify.txt
  ```

  **Commit**: NO (파일 정리만, 코드 변경 없음)

---

- [ ] 8. Full 200-Firm Pipeline Run — 예상: 8-16시간 (무인 실행)

  **What to do**:
  - 모든 이전 Task (1-7) 완료 확인
  - 기존 outputs 초기화 (Task 7에서 정리 완료)
  - 전체 파이프라인 실행:
    ```bash
    python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs
    ```
  - 실행 중 모니터링 (별도 터미널):
    - `python -c "import json; c=0; [c:=c+1 for l in open('outputs/attorneys.jsonl','r',encoding='utf-8') if l.strip()]; print(f'Lines: {c}')"` — 진행 상황 확인
    - `tasklist | findstr /i "chromium"` — orphaned 프로세스 확인
  - 완료 후 검증:
    - JSONL 레코드 수 + unique firm 수
    - firm_level_summary.csv에 200개 행 존재
    - coverage_metrics.json에 200개 항목
    - Excel 파일 생성 확인
  - 실행이 stuck 시: Task 1의 timeout이 자동으로 해결. 30분 후 다음 firm으로 진행.
  - 실행 중단 필요 시: Ctrl+C (Task 2의 graceful shutdown). 이후 `--resume`로 재시작 (Task 4).

  **Must NOT do**:
  - 코드 변경 (이 Task는 실행만)
  - firm 순서 변경
  - 수동으로 개별 firm 실행
  - Ctrl+C 외의 방법으로 중단

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 장시간 실행 모니터링 + 문제 발생 시 resume 판단
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (Task 7 이후)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1-7 (모두)

  **References**:

  **Pattern References**:
  - `find_attorney.py:4900-4989` — CLI args + main() (실행 명령)
  - `site_structures.json` — 200개 펌 목록 + 구조 유형
  - `config/practice_department_map.json` — department 매핑 (24+α 항목)

  **WHY Each Reference Matters**:
  - CLI args: 정확한 실행 명령 구성
  - site_structures.json: 어떤 펌이 어떤 유형인지 (BOT_PROTECTED, SPA 등) — 예상 동작 파악
  - practice_department_map.json: department inference 범위 확인

  **Acceptance Criteria**:
  - [ ] 200개 펌 전체 처리 완료 (BOT_PROTECTED 포함 — 태그 후 skip 또는 Martindale 폴백)
  - [ ] `outputs/attorneys.jsonl`에 duplicate profile_url 없음
  - [ ] `outputs/firm_level_summary.csv`에 200개 행
  - [ ] `outputs/coverage_metrics.json`에 200개 항목
  - [ ] Excel 파일 존재 + 데이터 포함
  - [ ] 전체 실행 시간 < 24시간

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: 200개 펌 전체 실행 완료
    Tool: Bash
    Preconditions: Tasks 1-7 완료, outputs 초기화
    Steps:
      1. python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs
      2. 완료 대기 (8-16시간)
      3. python -c "
         import json
         firms = set()
         total = 0
         for l in open('outputs/attorneys.jsonl','r',encoding='utf-8'):
           if not l.strip(): continue
           d = json.loads(l)
           firms.add(d.get('firm','?'))
           total += 1
         print(f'Total attorneys: {total}')
         print(f'Unique firms: {len(firms)}')
         print(f'Firms: {sorted(firms)}')
         "
    Expected Result: Unique firms >= 150 (일부 BOT_PROTECTED 제외), Total attorneys >= 50,000
    Failure Indicators: Unique firms < 100, 또는 프로세스 hang
    Evidence: .sisyphus/evidence/task-8-full-run-result.txt

  Scenario: JSONL 중복 없음 확인
    Tool: Bash
    Preconditions: 전체 실행 완료
    Steps:
      1. python -c "
         import json
         urls = []
         for l in open('outputs/attorneys.jsonl','r',encoding='utf-8'):
           if not l.strip(): continue
           urls.append(json.loads(l).get('profile_url',''))
         total = len(urls)
         unique = len(set(u for u in urls if u))
         no_url = sum(1 for u in urls if not u)
         print(f'Total: {total}, Unique URLs: {unique}, No URL: {no_url}')
         "
    Expected Result: Total - No URL == Unique URLs (URL 있는 레코드 중 중복 없음)
    Failure Indicators: duplicates 존재
    Evidence: .sisyphus/evidence/task-8-no-duplicates.txt

  Scenario: Summary + Coverage 파일 완전성
    Tool: Bash
    Preconditions: 전체 실행 완료
    Steps:
      1. python -c "
         import csv, json
         with open('outputs/firm_level_summary.csv','r',encoding='utf-8') as f:
           rows = list(csv.reader(f))
         print(f'Summary rows: {len(rows)-1}')  # header 제외
         with open('outputs/coverage_metrics.json','r',encoding='utf-8') as f:
           metrics = json.load(f)
         print(f'Coverage entries: {len(metrics)}')
         "
    Expected Result: Summary rows >= 150, Coverage entries >= 150
    Failure Indicators: rows < 100 또는 파일 없음
    Evidence: .sisyphus/evidence/task-8-summary-coverage.txt
  ```

  **Commit**: YES
  - Message: `data(pipeline): complete 200-firm extraction run`
  - Files: `outputs/attorneys.jsonl`, `outputs/*_attorneys.xlsx`, `outputs/firm_level_summary.csv`, `outputs/coverage_metrics.json`
  - Pre-commit: 위 QA scenarios 통과

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest tests/ -v` + check all changed files for: `as any`/`@ts-ignore` equivalents, empty excepts, bare print() in library modules, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration: resume after timeout, signal during enrichment then resume. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Commit | Scope | Pre-commit Test |
|--------|-------|-----------------|
| C1 | Enrichment timeout (Task 1) | `--debug-firm "Kirkland" --limit 10` completes <2분 |
| C2 | Signal handling (Task 2) | Start + Ctrl+C → clean exit <30초 |
| C3 | JSONL dedup utility (Task 3) | `python dedup_jsonl.py` on test data → correct count |
| C4 | Resume capability (Task 4) | Process 2 firms → kill → `--resume` → skip 2 |
| C5 | Output crash safety (Task 5) | Kill mid-run → restart → Excel has completed firms |
| C6 | Department inference check (Task 6) | `pytest tests/ -v` → 0 failures |
| C7 | Full 200-firm run (Task 8) | All 200 firms complete, JSONL has 0 duplicate profile_url |

---

## Success Criteria

### Verification Commands
```bash
# Timeout fix verification
python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --debug-firm "Mayer Brown" --limit 50
# Expected: completes in <10 minutes, partial results in JSONL

# Resume verification
python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --resume
# Expected: skips already-processed firms, processes remaining

# Dedup verification
python -c "import json; lines=open('outputs/attorneys.jsonl','r',encoding='utf-8').readlines(); urls=[json.loads(l).get('profile_url') for l in lines if l.strip()]; print(f'Total: {len(urls)}, Unique: {len(set(urls))}')"
# Expected: Total == Unique

# Test regression
pytest tests/ -v
# Expected: 61+ passed, 0 failed

# Full run completion
python find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs
# Expected: all 200 firms processed, outputs written
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass (61+ including new)
- [ ] 200 firms processed
- [ ] No duplicate profile_url in JSONL
- [ ] No orphaned chromium processes
