# Stop-After: Graceful Pipeline Shutdown Feature

## TL;DR

> **Quick Summary**: `run_pipeline.py`에 두 가지 안전 중단 메커니즘을 추가한다. (1) `--stop-after DURATION` CLI 옵션으로 시간 기반 자동 중단, (2) `STOP` 파일 감지로 언제든 즉시 중단 요청 가능. 둘 다 현재 펌 완료 후 안전하게 데이터 저장 후 종료.
>
> **Deliverables**:
> - `run_pipeline.py`: `--stop-after` 인자 추가, stop 플래그 로직, STOP 파일 감지, SIGINT 처리, partial output 저장
>
> **Estimated Effort**: Quick
> **Parallel Execution**: NO — 단일 파일 수정
> **Critical Path**: Task 1 완료

---

## Context

### Original Request
노트북 하드웨어 불안정으로 파이프라인 실행 중 언제든 안전하게 중단할 수 있어야 함. OpenCode ESC가 항상 작동하지 않아서 시간 기반 자동 중단 기능이 필요.

### Interview Summary
**Key Discussions**:
- `--stop-after 30m`, `--stop-after 2h` 형식으로 시간 지정
- `STOP` 파일 트리거도 함께 구현 (터미널에서 `touch STOP` 한 줄로 제어)
- `/start-work`으로 플랜 실행 시 명령어에 `--stop-after` 포함 가능

**Research Findings**:
- `time.monotonic()` 이미 line 567에서 사용 중 → elapsed 계산에 재사용 가능
- `all_profiles` 리스트가 루프 전(line 568) 정의되고 루프 안에서 누적됨 → 중간 저장 가능
- `_write_excel` / `_write_jsonl` 함수 이미 존재 → early stop 시 그대로 호출 가능
- Sequential loop: line 599 / Parallel: line 582 `ThreadPoolExecutor`
- 현재 signal 처리 없음

### Metis Review
**Identified Gaps** (addressed):
- `STOP` 파일이 실행 전부터 존재하면 즉시 종료됨 → 시작 시 감지하여 경고 후 삭제
- `--stop-after 0m` 등 잘못된 입력 → argparse 오류로 처리
- `--discover-only` 모드에서 `--stop-after`: profiles 없으므로 discovery summary만 저장
- 빈 profiles로 early stop 시 파일 미생성 + 경고 메시지 출력
- 리눅스 suspend 시 `time.monotonic()` 미진행 → Wall clock(`time.time()`) 사용으로 변경
- Parallel mode: in-flight futures는 완료 대기, pending futures는 제출 안 함
- Partial output 파일명에 `_partial` suffix 자동 추가

---

## Work Objectives

### Core Objective
`run_pipeline.py` 단일 파일에 graceful stop 메커니즘을 추가하여 시간 기반 및 파일 트리거 방식 모두 지원.

### Concrete Deliverables
- `run_pipeline.py` 수정본 (stop 기능 포함)

### Definition of Done
- [ ] `python3.12 run_pipeline.py --stop-after 30s --firms "skadden" --max-profiles 3` → 30초 후 안전 종료, partial output 저장
- [ ] 실행 중 `touch STOP` → 현재 펌 완료 후 종료, STOP 파일 삭제됨
- [ ] `--stop-after 2days` → argparse 오류 출력 후 실행 안 됨
- [ ] `STOP` 파일이 실행 전 존재 → 경고 출력 후 삭제하고 정상 실행

### Must Have
- `--stop-after` 지원 형식: `30m`, `2h`, `1h30m`, `90s` (초 단위도)
- STOP 파일 트리거 (프로젝트 루트 `STOP` 파일)
- Partial output에 `_partial` suffix 자동 추가
- SIGINT (Ctrl+C) 도 동일한 graceful 경로로 처리
- Early stop 요약 출력: "Stopped after 3/148 firms, N profiles saved"
- 0 profiles 시 파일 미생성 + 경고

### Must NOT Have (Guardrails)
- 펌 중간에 중단 없음 — firm 경계에서만 stop 체크
- `find_attorney.py` 수정 금지 — `run_pipeline.py`만
- Resume-from-partial 기능 추가 금지 (별도 태스크)
- Per-firm timeout 추가 금지 (별도 태스크)
- STOP 파일 내용 파싱 금지 (존재 여부만 체크)
- SIGTERM/SIGHUP 처리 추가 금지 (SIGINT만)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO (pytest 없음)
- **Automated tests**: None
- **Framework**: 직접 실행으로 검증

### QA Policy
각 시나리오는 Bash(terminal) 기반으로 직접 실행 및 검증.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave ONLY (Single task — sequential):
└── Task 1: run_pipeline.py에 stop 기능 추가 [quick]

Wave FINAL:
└── Task F1: QA 검증 [unspecified-low]
```

---

## TODOs

- [x] 1. `run_pipeline.py`: stop-after + STOP 파일 + SIGINT graceful shutdown 추가

  **What to do**:

  ### A. Duration 파서 추가 (파일 상단, imports 아래)
  ```python
  import signal
  import threading

  _STOP_FILE = Path("STOP")

  def _parse_duration(s: str) -> int:
      """Parse '30m', '2h', '1h30m', '90s' → seconds. Raises ValueError on invalid."""
      import re
      s = s.strip().lower()
      m = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', s)
      if not m or not any(m.groups()):
          raise ValueError(f"Invalid duration '{s}'. Use formats like: 30m, 2h, 1h30m, 90s")
      h = int(m.group(1) or 0)
      mn = int(m.group(2) or 0)
      sc = int(m.group(3) or 0)
      total = h * 3600 + mn * 60 + sc
      if total <= 0:
          raise ValueError(f"Duration must be positive, got '{s}'")
      return total
  ```

  ### B. `build_arg_parser()`에 인자 추가
  `--structure-type` 인자 다음에 추가:
  ```python
  p.add_argument(
      "--stop-after",
      metavar="DURATION",
      default=None,
      help="Gracefully stop after DURATION (e.g. 30m, 2h, 1h30m). "
           "Finishes current firm, saves partial output.",
  )
  ```

  ### C. `main()` 함수 수정

  #### C1. `args = parser.parse_args()` 직후에 추가:
  ```python
  # Parse --stop-after
  stop_after_secs: int | None = None
  if args.stop_after:
      try:
          stop_after_secs = _parse_duration(args.stop_after)
      except ValueError as e:
          parser.error(str(e))

  # Check for pre-existing STOP file
  if _STOP_FILE.exists():
      log.warning("STOP file found at startup — deleting it and continuing.")
      _STOP_FILE.unlink()

  # Shared stop event
  _stop_event = threading.Event()

  # SIGINT → graceful stop
  def _handle_sigint(signum, frame):
      log.warning("Ctrl+C received — will stop after current firm completes.")
      _stop_event.set()
  signal.signal(signal.SIGINT, _handle_sigint)
  ```

  #### C2. `t_run_start = time.monotonic()` → `time.time()` 로 변경:
  ```python
  t_run_start = time.time()  # wall clock — survives laptop suspend
  ```

  #### C3. Sequential loop (line 599) 를 아래로 교체:
  ```python
  for idx, firm in enumerate(firms, start=1):
      # --- Stop check (firm boundary) ---
      if _stop_event.is_set():
          log.info(f"Stop flag set — skipping remaining {len(firms) - idx + 1} firms.")
          break
      if _STOP_FILE.exists():
          log.info("STOP file detected — finishing this run after current firm.")
          _stop_event.set()
          _STOP_FILE.unlink()
      if stop_after_secs is not None:
          elapsed_now = time.time() - t_run_start
          if elapsed_now >= stop_after_secs:
              log.info(f"--stop-after limit reached ({elapsed_now:.0f}s) — stopping.")
              _stop_event.set()
              break
      # --- End stop check ---
      log.info(f"── Firm {idx}/{len(firms)}: {firm.name} ──")
      result = run_firm(firm, **firm_kwargs)
      results.append(result)
      all_profiles.extend(result.profiles)
  ```

  #### C4. Parallel mode (line 582) 를 아래로 교체:
  ```python
  if args.workers > 1:
      log.info(f"Running {len(firms)} firms with {args.workers} parallel workers")
      with ThreadPoolExecutor(max_workers=args.workers) as pool:
          futures = {}
          for firm in firms:
              if _stop_event.is_set():
                  break  # Don't submit new futures
              futures[pool.submit(run_firm, firm, **firm_kwargs)] = firm
          for future in as_completed(futures):
              firm = futures[future]
              try:
                  result = future.result()
              except Exception as exc:
                  log.error(f"[{firm.name}] Unhandled exception: {exc}", exc_info=True)
                  result = FirmResult(firm=firm, errors=[str(exc)])
              results.append(result)
              all_profiles.extend(result.profiles)
              # Check stop after each future completes
              if _stop_event.is_set():
                  break
              if _STOP_FILE.exists():
                  log.info("STOP file detected during parallel run.")
                  _stop_event.set()
                  _STOP_FILE.unlink()
              if stop_after_secs is not None and (time.time() - t_run_start) >= stop_after_secs:
                  log.info("--stop-after limit reached during parallel run — stopping.")
                  _stop_event.set()
  ```

  #### C5. Output 저장 부분 (line 608 근처) 수정:
  ```python
  elapsed = time.time() - t_run_start
  stopped_early = _stop_event.is_set()

  if not args.discover_only and all_profiles:
      # Add _partial suffix if stopped early
      if stopped_early and not args.output:
          base_name_final = base_name + "_partial"
          out_xlsx = OUTPUT_DIR / f"{base_name_final}.xlsx"
          out_jsonl = OUTPUT_DIR / f"{base_name_final}.jsonl"
      _write_excel(all_profiles, out_xlsx)
      _write_jsonl(all_profiles, out_jsonl)
      if stopped_early:
          log.warning(
              f"⚠ Stopped early — {len(all_profiles)} profiles saved to "
              f"{out_jsonl.name} ({len(results)}/{len(firms)} firms completed)"
          )
  elif not args.discover_only and not all_profiles and stopped_early:
      log.warning("⚠ Stopped early — no profiles collected, no output files written.")
  elif args.discover_only:
      # discover-only 모드는 기존 로직 그대로 (summary JSON 저장)
      ...  # 기존 코드 유지
  ```

  **Must NOT do**:
  - `discovery.py`, `enrichment.py`, `find_attorney.py` 수정 금지
  - 펌 내부 루프에 stop 체크 추가 금지
  - SIGTERM/SIGHUP 핸들러 추가 금지
  - Resume 기능 추가 금지

  **Recommended Agent Profile**:
  > Single file modification with clear instructions.
  - **Category**: `quick`
    - Reason: 단일 파일, 명확한 구현 지침 제공됨
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave ONLY
  - **Blocks**: F1 (QA)
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `run_pipeline.py:567-608` — 현재 timing / output 처리 패턴
  - `run_pipeline.py:582-603` — sequential/parallel 루프 구조
  - `run_pipeline.py:423-503` — argparse 인자 추가 패턴

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: --stop-after triggers correctly (short timeout test)
    Tool: Bash
    Preconditions: 패키지 설치 완료, attorneys.jsonl 존재
    Steps:
      1. python3.12 run_pipeline.py --firms "skadden" --max-profiles 3 --stop-after 5s 2>&1
      2. 출력에서 "stop-after" 또는 "Stopped early" 메시지 확인
      3. outputs/ 디렉토리에서 _partial 파일 존재 여부 확인: ls outputs/*partial* 2>/dev/null
    Expected Result:
      - 프로세스가 정상 종료 (exit 0)
      - 출력에 stop 관련 메시지 포함
      - 5초 내에 종료되었거나 첫 펌 완료 후 종료
    Failure Indicators: Traceback, exit 1, 프로세스가 5분 이상 실행됨
    Evidence: .sisyphus/evidence/task-1-stop-after.txt

  Scenario: STOP file trigger
    Tool: Bash
    Preconditions: run_pipeline.py 수정 완료
    Steps:
      1. python3.12 run_pipeline.py --firms "skadden" --max-profiles 50 & PID=$!
      2. sleep 3 && touch STOP
      3. wait $PID; echo "Exit: $?"
      4. ls STOP 2>/dev/null || echo "STOP file deleted (correct)"
      5. ls outputs/*partial* 2>/dev/null || echo "No partial output (ok if 0 profiles)"
    Expected Result:
      - STOP 파일이 삭제됨
      - 프로세스가 graceful 종료
    Failure Indicators: STOP 파일이 남아있음, Traceback
    Evidence: .sisyphus/evidence/task-1-stop-file.txt

  Scenario: Invalid --stop-after format
    Tool: Bash
    Steps:
      1. python3.12 run_pipeline.py --stop-after 2days 2>&1; echo "Exit: $?"
    Expected Result:
      - "error: " 메시지 출력
      - Exit code 2 (argparse error)
      - 파이프라인 실행 안 됨
    Evidence: .sisyphus/evidence/task-1-invalid-format.txt

  Scenario: STOP file pre-exists at startup
    Tool: Bash
    Steps:
      1. touch STOP
      2. python3.12 run_pipeline.py --firms "skadden" --max-profiles 1 2>&1 | head -5
      3. ls STOP 2>/dev/null || echo "STOP deleted (correct)"
    Expected Result:
      - "STOP file found at startup" 경고 출력
      - STOP 파일 삭제됨
      - 파이프라인은 정상 실행
    Evidence: .sisyphus/evidence/task-1-preexist-stop.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-1-stop-after.txt` — stop-after 실행 출력
  - [ ] `.sisyphus/evidence/task-1-stop-file.txt` — STOP 파일 트리거 출력
  - [ ] `.sisyphus/evidence/task-1-invalid-format.txt` — 잘못된 형식 오류 출력
  - [ ] `.sisyphus/evidence/task-1-preexist-stop.txt` — STOP 파일 선재 시 출력

  **Commit**: YES
  - Message: `feat(pipeline): add --stop-after and STOP file graceful shutdown`
  - Files: `run_pipeline.py`
  - Pre-commit: `python3.12 -c "import run_pipeline" 2>&1`

---

## Final Verification Wave

- [x] F1. **QA 검증** — `unspecified-low`
  위 4개 QA 시나리오를 순서대로 실행. 각 evidence 파일 저장. 모든 시나리오 통과 시 APPROVE.
  Output: `Scenarios [4/4 pass] | VERDICT: APPROVE/REJECT`

---

## Commit Strategy

- **Task 1**: `feat(pipeline): add --stop-after and STOP file graceful shutdown` — `run_pipeline.py`

---

## Success Criteria

### Verification Commands
```bash
python3.12 run_pipeline.py --stop-after 2days 2>&1  # Expected: argparse error
touch STOP && python3.12 run_pipeline.py --firms "skadden" --max-profiles 1 2>&1 | grep -i stop
# Expected: "STOP file found at startup" 경고
```

### Final Checklist
- [ ] `--stop-after` 파싱 오류 처리
- [ ] STOP 파일 startup 감지 및 삭제
- [ ] Sequential 루프에서 firm 경계 stop 체크
- [ ] Parallel 모드에서 future 완료 후 stop 체크
- [ ] Partial output `_partial` suffix
- [ ] SIGINT graceful 처리
- [ ] 0 profiles 시 파일 미생성
