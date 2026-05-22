# Update AGENTS.md: Add Estimated Time Guidelines

## TL;DR

> **Quick Summary**: AGENTS.md에 "플랜 제시 시 예상 소요시간 포함" 규칙 섹션 추가.
>
> **Deliverables**:
> - `AGENTS.md`: Planning & Communication Guidelines 섹션 추가
>
> **Estimated Effort**: Quick (1분 미만)
> **Parallel Execution**: NO
> **Critical Path**: Task 1

---

## TODOs

- [x] 1. `AGENTS.md`: Planning & Communication Guidelines 섹션 추가

  **What to do**:
  `## Key Data Files` 바로 위에 아래 섹션을 삽입:

  ```markdown
  ## Planning & Communication Guidelines

  When Prometheus (or any planning agent) presents a plan or task list to the user,
  **always include estimated time** alongside each step. Format:

  ```
  단계 설명   예상 소요시간: X분 / X시간
  ```

  Estimation guidelines:
  - Single firm test (`--max-profiles 5`): ~2–5분
  - SITEMAP_XML batch (전체): ~4–8시간 (펌당 평균 ~3분)
  - HTML_DIRECTORY_FLAT batch: ~3–6시간
  - Code change (single file, clear spec): ~5–10분 (agent 실행 기준)
  - `--stop-after` 기능 구현: ~10분

  ---
  ```

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: None

  **References**:
  - `AGENTS.md:215` — `## Key Data Files` 섹션 바로 위에 삽입

  **Acceptance Criteria**:
  ```
  Scenario: 섹션이 올바르게 삽입됨
    Tool: Bash
    Steps:
      1. grep -n "Planning & Communication" AGENTS.md
    Expected Result: 라인 번호와 함께 해당 섹션 헤더 출력
    Evidence: .sisyphus/evidence/task-1-agents-md.txt
  ```

  **Commit**: YES
  - Message: `docs(agents): add estimated time guidelines for planning`
  - Files: `AGENTS.md`
