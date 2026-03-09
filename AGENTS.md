# AGENTS.md — Coding Agent Guide

## Project Overview

AmLaw200 attorney data extraction pipeline. Scrapes publicly accessible law firm
websites to extract: name, title, office, department, practice areas, industries,
bar admissions, and education. Outputs to Excel + JSONL.

**Legal constraint (absolute):** Never bypass Cloudflare or bot-protection. Never
crawl `robots.txt` Disallow paths. Use only public HTML + sitemap data. Tag
blocked firms `BOT_PROTECTED` or `AUTH_REQUIRED` and skip them.

---

## Runtime Environment

- **Python:** `python3.12` — the `python` alias does not exist; always use `python3.12`
- **pip:** `/home/pcho/.local/bin/pip` (not on PATH; use full path)
- **No pytest installed.** Tests are standalone scripts run directly.
- **Key packages:** `requests`, `beautifulsoup4`, `lxml`, `openpyxl`
- **Optional:** `playwright` (for SPA fallback only; `PIPELINE_NO_PLAYWRIGHT=1` disables it)

---

## Build / Run Commands

```bash
# Full pipeline (all 200 firms)
python3.12 run_pipeline.py

# Single firm (discovery + enrichment)
python3.12 run_pipeline.py --firms "kirkland"

# Limit profiles per firm (fast test)
python3.12 run_pipeline.py --firms "kirkland" --max-profiles 5

# Discovery only
python3.12 run_pipeline.py --firms "latham" --discover-only

# Enrichment only (resume from JSONL)
python3.12 run_pipeline.py --skip-discovery --resume outputs/attorneys_2026-02-23.jsonl

# Verbose debug output
python3.12 run_pipeline.py --firms "gibson dunn" --verbose

# find_attorney.py (alternative scraper with Playwright fallback)
python3.12 find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx"
python3.12 find_attorney.py --debug-firm "Kirkland & Ellis" --headful true

# Site structure probe (classify all 200 firms)
python3.12 probe_structures.py
python3.12 probe_structures.py --max-firms 20 --workers 10 --resume
```

## Test Commands

There is no pytest suite. Tests are standalone scripts:

```bash
# Run a single firm extraction end-to-end
python3.12 run_pipeline.py --firms "kirkland" --max-profiles 3 --verbose

# Test sitemap extraction for Latham
python3.12 test_extraction.py

# Test firm URL discovery
python3.12 test_firm_finder.py

# Quick ad-hoc tests (edit test.py then run)
python3.12 test.py

# Validate a Kirkland profile
python3.12 kirkland_validate.py
python3.12 kirkland_enrich_test.py
```

To run a single extraction for any specific firm, pass `--firms "<partial name>"` to
`run_pipeline.py` with `--max-profiles N` for speed.

---

## Architecture

```
run_pipeline.py          Main pipeline runner
  ├── discovery.py       URL discovery (A-Z alphabet / JSON API / HTML)
  ├── enrichment.py      Profile enrichment (HTML → AttorneyProfile)
  │     ├── parser_sections.py   Heading-based section parser (no class selectors)
  │     └── validators.py        Per-field validation + sentinel values
  ├── attorney_extractor.py      AttorneyProfile dataclass + extraction helpers
  └── debug_logger.py    Per-firm debug log writer

find_attorney.py         Alternative high-coverage scraper with Playwright fallback
  ├── observation_logger.py
  ├── compliance_engine.py
  ├── rate_limit_manager.py
  ├── coverage_loop.py
  ├── field_enricher.py
  └── external_directory_extractor.py

probe_structures.py      One-time site structure classifier → site_structures.json
site_structures.json     Firm structure types: SITEMAP_XML | HTML_DIRECTORY_FLAT |
                         HTML_ALPHA_PAGINATED | JSON_API_ALPHA | SPA_OTHER |
                         SPA_NEXTJS | BOT_PROTECTED | AUTH_REQUIRED | UNKNOWN
config/                  blocklist.json, known_patterns.json, us_states.json
cache/                   firm_domain_cache.json, firm_aliases.json
outputs/                 attorneys_<timestamp>.xlsx + .jsonl
debug_reports/<firm>/    Per-firm debug logs
```

---

## Code Style Guidelines

### General

- All files start with `#!/usr/bin/env python3` and a module-level docstring.
- `from __future__ import annotations` at the top of every file (enables PEP 563
  postponed evaluation for forward references).
- Line length: ~100 chars (no hard formatter enforced; match surrounding code).
- No trailing whitespace. Unix line endings.

### Imports

Standard order (no blank line between groups in this codebase, but maintain logical
grouping):
1. `from __future__ import annotations`
2. Standard library
3. Third-party (`requests`, `bs4`, `openpyxl`, `playwright`)
4. Local modules

Wrap optional imports in try/except and set a `*_AVAILABLE` bool:
```python
try:
    from bs4 import BeautifulSoup, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None   # type: ignore[assignment]
```

### Type Annotations

- Use `from __future__ import annotations` so forward refs work everywhere.
- Use `TYPE_CHECKING` guard for heavy imports used only in annotations:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from bs4 import BeautifulSoup
  ```
- Prefer `list[str]`, `dict[str, Any]`, `str | None` (Python 3.10+ union syntax).
- `Optional[X]` is acceptable but `X | None` preferred in new code.
- Suppress unavoidable pyright/mypy noise with inline `# type: ignore[...]` or
  `# pyright: ignore[...]` comments — keep them narrow and specific.

### Dataclasses

All data transfer objects use `@dataclass`:
```python
from dataclasses import dataclass, field

@dataclass
class AttorneyProfile:
    full_name: str = ""
    offices: list[str] = field(default_factory=list)
```

### Naming Conventions

| Item | Convention | Example |
|---|---|---|
| Modules | `snake_case` | `parser_sections.py` |
| Classes | `PascalCase` | `ProfileEnricher` |
| Functions / methods | `snake_case` | `parse_sections()` |
| Constants | `UPPER_SNAKE_CASE` | `REQUEST_TIMEOUT = 5` |
| Private helpers | `_leading_underscore` | `_is_profile_url()` |
| Boolean flags | `is_` / `has_` prefix | `BS4_AVAILABLE`, `has_pagination` |

### Error Handling

- Wrap every network call in `try/except` — never let a single firm crash the loop.
- Catch specific exceptions first (`requests.Timeout`, `requests.ConnectionError`),
  then `Exception` as a fallback. Log, don't raise, when processing a firm.
- Use the `DebugLogger` / `logger = logging.getLogger(__name__)` pattern; do not
  use bare `print()` in library modules (only in CLI/test scripts).
- Return empty/sentinel values on failure rather than `None` where possible:
  - `bar_admissions` empty → `[]` (not `None`)
  - `industries` empty → `["no industry field"]` (sentinel)
  - `education` empty → `[EducationRecord(degree="no JD", school="unknown")]`

### HTTP Requests

- Always pass a browser-like `User-Agent` header.
- Respect `robots.txt`; check `compliance_engine.py` before crawling a new firm.
- Honor `Crawl-delay`. Default delay between requests: `RATE_LIMIT_DELAY = 0.5s`.
- Timeouts: `REQUEST_TIMEOUT=5s`, `PROFILE_FETCH_TIMEOUT=10s`,
  `PLAYWRIGHT_PAGE_TIMEOUT=20000ms`.
- `BOT_PROTECTED` / `AUTH_REQUIRED` firms → record, skip, never retry with evasion.

### Regex

- Use raw strings: `r'\bJ\.?D\.?\b'`
- Flag invalid escape sequences (the codebase has two `SyntaxWarning` in
  `attorney_extractor.py:1268` and `:2561` — fix with raw strings when editing).
- Compile patterns at module level when reused.

### No Firm-Specific Code

**Do not hard-code logic for individual firms.** All firm-specific behaviour must be
driven by `site_structures.json` structure types. Add a new extractor class for a
structure type; let the pipeline select it automatically.

---

## Key Data Files

| File | Purpose |
|---|---|
| `site_structures.json` | 200-firm structure classification results |
| `url_corrections.json` | Corrected URLs for 53 firms with wrong originals |
| `cache/firm_domain_cache.json` | Firm → canonical URL mapping |
| `config/blocklist.json` | Domains that are never scraped |
| `AmLaw200_2025 Rank_gross revenue_with_websites.xlsx` | Source firm list |
