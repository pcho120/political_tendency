# Fix: find_attorney.py Pipeline Freeze Issues

## Context

`find_attorney.py` pipeline freezes the system after 2–3 hours when run with `--workers 2`.
Static analysis identified 8 issues across 4 files. All fixes are surgical — no refactors, no new dependencies.

**Runtime**: `python3.12` only. No pytest — tests are standalone scripts.
**Constraint**: Never bypass Cloudflare/bot-protection. Preserve all existing behavior.

---

## Fix 1 — [HIGH] Replace catastrophic regex with json.JSONDecoder.raw_decode

**Files**: `attorney_extractor.py` lines 443–444 and 2016–2017

**Problem**: `r'window\.__INITIAL_STATE__\s*=\s*({.*?});'` and `r'window\.__APOLLO_STATE__\s*=\s*({.*?});'` use `{.*?}` with `re.DOTALL`. On 3–10MB Next.js JSON blobs, the engine tries every `}` as a potential match end → O(N²) CPU. This is the #1 freeze cause.

**Fix**: Replace both regex patterns with `json.JSONDecoder().raw_decode()`:

```python
def _extract_window_var(html: str, var_name: str) -> dict | None:
    """Extract window.VAR_NAME = {...} without regex backtracking."""
    marker = f'window.{var_name}'
    idx = html.find(marker)
    if idx == -1:
        return None
    eq = html.find('=', idx + len(marker))
    if eq == -1:
        return None
    start = html.find('{', eq + 1)
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
```

Replace the two pattern entries:
- `r'window\.__INITIAL_STATE__\s*=\s*({.*?});'` → call `_extract_window_var(html, '__INITIAL_STATE__')`
- `r'window\.__APOLLO_STATE__\s*=\s*({.*?});'` → call `_extract_window_var(html, '__APOLLO_STATE__')`

The `__NEXT_DATA__` pattern (`<script id="__NEXT_DATA__">`) is fine as-is — `</script>` is a unique terminator with minimal backtracking risk.

**Both occurrences must be fixed** (lines ~443–450 and ~2016–2021). They appear to be duplicate methods; fix both.

**Acceptance**: Grep confirms zero occurrences of `__INITIAL_STATE__.*{.*?}` and `__APOLLO_STATE__.*{.*?}` in the file.

---

## Fix 2 — [HIGH] Thread join timeout + browser.close() hang

**File**: `find_attorney.py` lines 5071–5091

**Problem A**: The join loop gives thread[0] the full 1800s budget. By the time it finishes, `remaining` may be 0 for thread[1], so `t.join(timeout=0)` returns immediately and falsely triggers `timed_out = True` + premature browser.close() on thread[0]'s still-running browser.

**Problem B**: `browser.close()` has no timeout. If Chromium is hung on network I/O, this call blocks forever — freezing the main thread with no exit.

**Fix A** — divide join budget equally across threads:
```python
deadline = time.monotonic() + MAX_ENRICHMENT_TIME_PER_FIRM
for t in threads:
    remaining = max(5.0, deadline - time.monotonic())
    t.join(timeout=remaining)  # each thread gets remaining budget, not divided
    if t.is_alive():
        timed_out = True
        stop_event.set()
        break
```

**Fix B** — wrap each `browser.close()` in a daemon thread with 10s timeout:
```python
def _safe_browser_close(b: Any) -> None:
    ct = threading.Thread(target=b.close, daemon=True)
    ct.start()
    ct.join(timeout=10)

# Replace bare browser.close() calls in the timed_out block:
for browser in active_browsers:
    _safe_browser_close(browser)
```

Add the `_safe_browser_close` helper as a module-level function near the top of `find_attorney.py` (after imports).

**Acceptance**: The `timed_out` block no longer calls `browser.close()` directly. All browser close calls go through `_safe_browser_close`.

---

## Fix 3 — [HIGH] Sleep inside lock in RateLimitManager.wait()

**File**: `rate_limit_manager.py` lines 118–129

**Problem**: `time.sleep(wait_time)` is called inside `with self._lock:` AND while holding the semaphore. Other threads trying to read `_last_request_time` for the same domain are blocked for the entire sleep duration.

**Fix**: Move sleep outside the lock:
```python
def wait(self) -> None:
    if self.blocked:
        raise RateLimitBlockedError(self.domain, self.block_reason)
    assert self._semaphore is not None
    self._semaphore.acquire()
    try:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_time
            wait_time = max(0.0, self.min_delay - elapsed)
            self.total_wait_seconds += wait_time
            self.total_requests += 1
        # Sleep OUTSIDE the lock — other threads can proceed
        if wait_time > 0:
            time.sleep(wait_time)
        with self._lock:
            self._last_request_time = time.monotonic()
    finally:
        self._semaphore.release()
```

**Acceptance**: `time.sleep` in `wait()` is no longer inside `with self._lock:`.

---

## Fix 4 — [MEDIUM] Cap HTML input size before regex extraction

**Files**: `attorney_extractor.py`, `field_enricher.py`, `find_attorney.py`

**Problem**: Regex patterns like `<li[^>]*>(.*?)</li>` and `<address[^>]*>(.*?)</address>` are run against full uncapped HTML. Attorney profile pages are typically 50–200KB; some SPA pages ship 1–3MB of embedded JSON. Capping prevents worst-case scans.

**Fix**: At the entry point of each main extraction method that receives raw HTML, add:
```python
html = html[:500_000]  # cap at 500KB — sufficient for any attorney profile
```

Specific locations:
- `attorney_extractor.py`: top of `extract_profile()` method (or wherever `html` first arrives for regex processing)
- `field_enricher.py`: top of `enrich_profile()` / `_html_extract_offices()` / `_html_extract_practice_areas()`
- `find_attorney.py`: top of `_extract_profile_from_html()` or equivalent

Do NOT cap HTML before BeautifulSoup parsing — BeautifulSoup handles large inputs fine and needs the full DOM. Cap only before the regex-heavy fallback paths.

**Acceptance**: All regex fallback paths in the three files operate on `html[:500_000]` or smaller.

---

## Fix 5 — [MEDIUM] Stream unbounded accumulators to disk

**File**: `find_attorney.py`

**Problem**: `self.all_coverage_metrics` (appended at lines 822, 2123, 2357, 2505, 4077) and `self.source_failures` (appended at lines 810, 2424, 2438, 3029) grow unbounded in RAM across 200 firms. Each `CoverageMetric` holds URL sets and strategy data.

**Fix**: Use `collections.deque` with a maxlen cap so old entries are evicted automatically:
```python
# In __init__:
import collections
self.all_coverage_metrics: collections.deque = collections.deque(maxlen=500)
self.source_failures: collections.deque = collections.deque(maxlen=1000)
```
This is a safe drop-in replacement since all existing access is append + iteration. The existing save/write logic already iterates over these — `deque` supports iteration identically to `list`.

**Acceptance**: Both fields initialized as `deque(maxlen=...)` in `__init__`. No `list` initialization for these two fields remains.

---

## Fix 6 — [MEDIUM] Fix O(N) str(obj.keys()) in recursive JSON traversal

**File**: `attorney_extractor.py` line 474 (and ~2045 — duplicate method)

**Problem**: Inside `_find_attorney_data_recursive`, every dict node does `if key in str(obj.keys()).lower()` — converting all keys to a string on every recursion. For a 500-key Next.js object traversed to depth 5, this runs thousands of string allocations.

**Fix**:
```python
def _find_attorney_data_recursive(self, obj: Any, depth: int = 0) -> dict | None:
    if depth > 5:
        return None
    attorney_keys = {"attorney", "lawyer", "professional", "person", "profile", "bio"}
    if isinstance(obj, dict):
        obj_keys_lower = {k.lower() for k in obj.keys()}
        if obj_keys_lower & attorney_keys:  # set intersection — O(min(m,n))
            return obj
        for value in obj.values():
            result = self._find_attorney_data_recursive(value, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = self._find_attorney_data_recursive(item, depth + 1)
            if result:
                return result
    return None
```

Fix **both** occurrences (~line 465 and ~line 2036).

**Acceptance**: Zero occurrences of `str(obj.keys())` remain in `_find_attorney_data_recursive`.

---

## Fix 7 — [LOW] Add missing [:500] slice on href findall

**File**: `find_attorney.py` lines 2586, 2608

**Problem**: These two lines call `re.findall(r'href=["\'](.*?)["\']', response.text)` without any slice, unlike lines 1266/1317 which already have `[:300]`.

**Fix**: Add `[:500]` to both:
```python
links = re.findall(r'href=["\'](.*?)["\']', response.text)[:500]
```

**Acceptance**: All `re.findall(r'href=...', ...)` calls in find_attorney.py have a trailing `[:N]` slice.

---

## Fix 8 — [LOW] Remove redundant domain_last_request rate limiter

**File**: `find_attorney.py`

**Problem**: `self.domain_last_request: dict[str, float] = {}` (line 688) and `_rate_limit()` method (lines 1014–1020) duplicate what `self.rate_limit_manager` already does. The dict grows forever and the dual-limiting adds unnecessary latency.

**Fix**: 
1. Remove `self.domain_last_request` from `__init__`
2. Remove the `_rate_limit()` method
3. Replace all `self._rate_limit(domain)` call sites with `self.rate_limit_manager.wait(domain)` (or remove if the caller already calls rate_limit_manager directly)

First grep for all `_rate_limit(` call sites to confirm there are no other usages before removing.

**Acceptance**: No `domain_last_request` or `_rate_limit` references remain in `find_attorney.py`.

---

## Verification Steps (after all fixes)

1. Run `python3.12 -c "import find_attorney"` — no import errors
2. Run `python3.12 -c "import attorney_extractor"` — no SyntaxWarnings
3. Run `python3.12 -W error::SyntaxWarning attorney_extractor.py` — zero warnings
4. Run a smoke test: `python3.12 find_attorney.py "AmLaw200_2025 Rank_gross revenue_with_websites.xlsx" --output-dir outputs --workers 2 --max-profiles 3 --firms "kirkland"` — completes without hanging
5. Confirm `all_coverage_metrics` and `source_failures` are `deque` instances at runtime (add a one-line assert in the test or inspect manually)

---

## Non-Goals (do not touch)

- Do not refactor discovery strategies or Playwright strategy logic
- Do not change `MAX_ENRICHMENT_TIME_PER_FIRM` value
- Do not add new third-party dependencies
- Do not modify `robots.txt` compliance logic
- Do not change output format (Excel/JSONL schema)
