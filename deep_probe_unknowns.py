#!/usr/bin/env python3
"""deep_probe_unknowns.py
UNKNOWN으로 분류된 53개 펌을 더 깊이 조사.

각 사이트에 대해:
  - 실제 HTML 소스를 가져와서 수동 분석 가능한 신호 추출
  - 더 많은 디렉토리 경로 시도
  - 직접 /attorneys, /lawyers 등 시도
  - 알려진 패턴의 변형 탐지
  - JSON 구조 탐지 강화
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TIMEOUT = 12
RATE_DELAY = 0.5
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 더 넓은 디렉토리 경로 목록
EXTENDED_PATHS = [
    "/attorneys", "/lawyers", "/people", "/professionals",
    "/our-team", "/team", "/attorneys-advisors",
    "/en/lawyers", "/en/attorneys", "/en/people",
    "/practice-professionals", "/who-we-are", "/our-people",
    "/firm/attorneys", "/firm/people", "/attorneys/search",
    "/search/lawyers", "/find-a-lawyer", "/find-an-attorney",
    "/en/professionals", "/attorney-search", "/people-search",
    "/about/people", "/about/attorneys", "/about/our-team",
    "/us/attorneys", "/us/people", "/us/professionals",
]

# 알파벳 API 추가 탐색
EXTENDED_ALPHA_PROBES = [
    "/lawyers?letter=A",
    "/attorneys?letter=A",
    "/people?letter=A",
    "/professionals?letter=A",
    "/en/lawyers?letter=A",
    "/attorneys/search?letter=A",
    "/people/search?letter=A",
    "/attorneys?alpha=A",
    "/lawyers?alpha=A",
    "/people?alpha=A",
    "/attorneys?last_name=A",
    "/search/professionals?letter=A",
]

ATTORNEY_KEYWORDS = re.compile(
    r'/(lawyers?|attorneys?|people|professionals?|bio|profile|person|team)/',
    re.IGNORECASE,
)


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(session, url):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        return r
    except Exception:
        return None


def _probe_extended(firm: str, url: str) -> dict:
    session = _session()
    result = {
        "firm": firm,
        "url": url,
        "found_paths": [],
        "json_api_path": "",
        "json_api_keys": [],
        "html_links_sample": [],
        "has_alpha": False,
        "has_pagination": False,
        "spa_signals": [],
        "attorney_link_pattern": "",
        "page_count_signal": "",
        "recommended_type": "UNKNOWN",
        "notes": "",
    }

    # 1. 확장 디렉토리 경로 시도
    best_path = ""
    best_html = ""
    for path in EXTENDED_PATHS:
        test_url = url.rstrip("/") + path
        r = _get(session, test_url)
        if r is None:
            continue
        if r.status_code == 200 and len(r.text) > 2000:
            # 변호사 관련 링크가 있는지 확인
            links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
            atty_links = [l for l in links if ATTORNEY_KEYWORDS.search(l)]
            if atty_links or len(r.text) > 10000:
                result["found_paths"].append({
                    "path": path,
                    "status": r.status_code,
                    "size": len(r.text),
                    "atty_links": len(atty_links),
                    "sample_links": atty_links[:3],
                })
                if not best_path or len(atty_links) > 0:
                    best_path = path
                    best_html = r.text
        time.sleep(RATE_DELAY * 0.3)

    # 2. JSON API 탐색
    for probe_path in EXTENDED_ALPHA_PROBES:
        test_url = url.rstrip("/") + probe_path
        r = _get(session, test_url)
        if r is None:
            continue
        if r.status_code == 200:
            ct = r.headers.get("content-type", "").lower()
            body = r.text.strip()
            is_json = "json" in ct or body.startswith("{") or body.startswith("[")
            if is_json:
                try:
                    data = r.json()
                    keys = list(data.keys())[:8] if isinstance(data, dict) else (
                        list(data[0].keys())[:8] if data and isinstance(data[0], dict) else []
                    )
                    result["json_api_path"] = probe_path
                    result["json_api_keys"] = keys
                    result["recommended_type"] = "JSON_API_ALPHA"
                    result["notes"] = f"JSON at {probe_path}, keys={keys}"
                    break
                except Exception:
                    pass
        time.sleep(RATE_DELAY * 0.3)

    # 3. best_html 분석
    if best_html:
        soup = BeautifulSoup(best_html, "html.parser")

        # 알파벳 nav
        alpha_links = soup.find_all("a", href=re.compile(r'[?&](letter|alpha)=[A-Z]', re.IGNORECASE))
        if len(alpha_links) >= 5:
            result["has_alpha"] = True

        # 페이지네이션
        next_links = soup.find_all("a", href=re.compile(r'[?&]page=\d+', re.IGNORECASE))
        if next_links or soup.find(string=re.compile(r'\bnext\b', re.IGNORECASE)):
            result["has_pagination"] = True

        # 변호사 링크 수집
        all_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        atty_links = [l for l in all_links if ATTORNEY_KEYWORDS.search(l or "")]
        result["html_links_sample"] = atty_links[:5]

        # 페이지 내 attorney 수 신호
        count_match = re.search(r'(\d{2,4})\s+(?:attorneys?|lawyers?|professionals?)', best_html, re.IGNORECASE)
        if count_match:
            result["page_count_signal"] = count_match.group(0)

        # URL 패턴 도출
        if atty_links:
            from os.path import commonprefix
            prefix = commonprefix([urlparse(l).path for l in atty_links[:10]])
            result["attorney_link_pattern"] = prefix.rstrip("/") + "/*" if len(prefix) > 2 else ""

        # SPA 신호
        spa_sigs = ["__NEXT_DATA__", "__NUXT__", "data-reactroot", "ng-app", "window.__INITIAL_STATE__"]
        result["spa_signals"] = [s for s in spa_sigs if s.lower() in best_html.lower()]

        # 추천 유형 결정
        if not result["recommended_type"] or result["recommended_type"] == "UNKNOWN":
            if result["spa_signals"]:
                result["recommended_type"] = "SPA_NEXTJS" if "__NEXT_DATA__" in result["spa_signals"] else "SPA_OTHER"
                result["notes"] = f"SPA signals: {result['spa_signals']}"
            elif result["has_alpha"]:
                result["recommended_type"] = "HTML_ALPHA_PAGINATED"
                result["notes"] = "A-Z nav found at " + best_path
            elif atty_links:
                result["recommended_type"] = "HTML_DIRECTORY_FLAT"
                result["notes"] = f"{len(atty_links)} attorney links at {best_path}"
            elif best_path:
                result["recommended_type"] = "HTML_DIRECTORY_FLAT"
                result["notes"] = f"Directory found at {best_path} but few attorney links"

    elif not result["json_api_path"]:
        result["recommended_type"] = "UNKNOWN"
        result["notes"] = "No directory path found with content"

    return result


def main():
    # Load site_structures.json
    data = json.loads(Path("site_structures.json").read_text(encoding="utf-8"))
    unknowns = [item for item in data if item["structure_type"] == "UNKNOWN"]

    print(f"Deep probing {len(unknowns)} UNKNOWN firms...\n")

    deep_results = []
    for i, item in enumerate(unknowns, 1):
        firm = item["firm"]
        url = item["url"]
        print(f"  [{i:>2}/{len(unknowns)}] {firm[:40]:<40}", end=" ", flush=True)
        t0 = time.time()
        result = _probe_extended(firm, url)
        elapsed = time.time() - t0
        print(f"→ {result['recommended_type']:<24} ({elapsed:.1f}s)  {result['notes'][:60]}")
        deep_results.append(result)

    # Print summary
    from collections import Counter
    counts = Counter(r["recommended_type"] for r in deep_results)
    print("\n--- Deep Probe Summary ---")
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {t:<28} {c:>3}")

    # Update site_structures.json with deep probe results
    deep_map = {r["firm"]: r for r in deep_results}
    for item in data:
        if item["firm"] in deep_map:
            dr = deep_map[item["firm"]]
            if dr["recommended_type"] != "UNKNOWN":
                item["structure_type"] = dr["recommended_type"]
                item["notes"] = f"[deep_probe] {dr['notes']}"
                item["confidence"] = 0.75
                if dr["json_api_path"]:
                    item["json_api_path"] = dr["json_api_path"]
                    item["json_api_sample_keys"] = dr["json_api_keys"]
                if dr["found_paths"]:
                    item["directory_path_found"] = dr["found_paths"][0]["path"]
                if dr["has_alpha"]:
                    item["has_alphabet_nav"] = True
                if dr["has_pagination"]:
                    item["has_pagination"] = True

    Path("site_structures.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also save detailed deep probe results
    Path("deep_probe_results.json").write_text(
        json.dumps(deep_results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nUpdated site_structures.json  (deep probe results in deep_probe_results.json)")


if __name__ == "__main__":
    main()
