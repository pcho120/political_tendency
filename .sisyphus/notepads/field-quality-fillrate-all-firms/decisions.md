# Decisions

## Task 1 — Decisions (2026-04-02)

### D1: Manifest is the authoritative source for `is_blocked`
The manifest `is_blocked` flag (not just `diagnostics.blocked`) is the authoritative classifier for blocked firms. This prevents a firm's internal failed-fetch diagnostics from being confused with a firm-level bot-protection block. Blocked firms' profiles are always counted as `blocked_excluded` regardless of individual profile diagnostics.

### D2: Length thresholds for list-field contamination
Reasonable max lengths: offices=10, department=5, practice_areas=20, industries=15. These are conservative upper bounds based on known real data. A list exceeding these is almost certainly a navigation/section dump. This is a sufficient proxy without requiring a full taxonomy lookup.

### D3: Title scoring uses a positive-token set as a signal, not a whitelist
`_TITLE_POSITIVE_TOKENS` is used to positively identify valid titles. Unknown but short/clean titles (≤ 80 chars, no date pattern) are accepted as `correct` to avoid false rejections of uncommon attorney titles. This follows the plan's "do not enforce a tiny canonical whitelist" rule.

### D4: Fixture-backed cache files for non-real-data firms
17 of the 22 manifest firms have no data in `attorneys.jsonl`. Synthetic fixture JSONL files were created for those firms in `tests/fixtures/cache/`. Synthetic records represent typical real-world profiles for each structure type (correct, missing, and partial variants included) to ensure the denominator and rates are meaningful.

### D5: Both `--compare before after` (flat args) and `compare` sub-command supported
The plan specifies the flat-args form `measure_baseline.py --compare A B --min-improvement X`. argparse sub-commands would break this syntax. The CLI pre-processes `--compare` in argv before passing to argparse to handle both the plan's interface and a future sub-command form.

### D6: No live-fetch in --use-cache mode
`--use-cache` is required for default verification. Live fetching is a stub that emits a WARNING and produces no records. This enforces the plan's "no live-network requirement for default verification" rule.

## Task 7 — Decisions (2026-04-02)

### D7: International offices are accepted with a policy signal
`validate_offices()` now keeps cleaned non-US office values instead of dropping
them as missing. The return reason `international_office` marks the policy
path, separating international acceptance from contamination and empty-input
outcomes.

### D8: Title normalization is alias-based, not whitelist-based
The title validator now maps a small tested alias set (`Of counsel`,
`Sr. Associate`) to canonical casing while still allowing unseen clean titles to
pass through unchanged.
