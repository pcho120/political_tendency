#!/usr/bin/env python3
"""
Firm Website Finder - Deterministic, cached, auditable discovery

Goal: near-100% official website discovery for law firms.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from openpyxl import load_workbook, Workbook


CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)
CACHE_FILE = CACHE_DIR / "firm_domain_cache.json"
ALIAS_FILE = CACHE_DIR / "firm_aliases.json"

OUTPUT_FAILURES = "discovery_failures.xlsx"

GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "").strip()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()

GOOGLE_RATE_LIMIT_SECONDS = 1.0
SITE_RATE_LIMIT_SECONDS = 0.5

DEFAULT_TIMEOUT = 5

BLOCKED_DOMAINS = {
    "wikipedia.org",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "law.com",
    "vault.com",
    "chambers.com",
    "bloomberg.com",
    "crunchbase.com",
    "justia.com",
    "martindale.com",
    "superlawyers.com",
    "lawyer.com",
    "lawyers.com",
    "amlaw.com",
    "law360.com",
    "law360",
}

NOISE_SUFFIXES = {
    "llp",
    "llc",
    "pllc",
    "pc",
    "p.c",
    "l.l.p",
    "l.l.c",
    "the",
    "law firm",
    "verein",
    "international",
    "global",
}


@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str
    display_link: str


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def normalize_firm_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\([^\)]*\)$", "", text).strip()
    text = re.sub(r"[\.,'\"]", "", text)
    text = re.sub(r"\s+", " ", text)
    for suffix in NOISE_SUFFIXES:
        text = re.sub(rf"\b{re.escape(suffix)}\b", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_aliases(normalized: str) -> list[str]:
    if not normalized:
        return []
    aliases = set()
    aliases.add(normalized)
    aliases.add(normalized.replace(" and ", " "))
    aliases.add(normalized.replace(" ", ""))

    tokens = normalized.split()
    if len(tokens) >= 2:
        aliases.add(f"{tokens[0]} {tokens[-1]}")
        aliases.add(tokens[0])
    if "bockius" in tokens:
        aliases.add(" ".join([t for t in tokens if t != "bockius"]))

    return list({a.strip() for a in aliases if a.strip()})[:10]


def _expected_domains(aliases: list[str]) -> set[str]:
    expected = set()
    for alias in aliases:
        token = alias.replace(" ", "")
        if len(token) < 4:
            continue
        expected.add(f"{token}.com")
        expected.add(f"www.{token}.com")
    return expected


def _rate_limit(state: dict, key: str, delay: float) -> None:
    last = state.get(key, 0.0)
    elapsed = time.time() - last
    if elapsed < delay:
        time.sleep(delay - elapsed)
    state[key] = time.time()


def google_cse_search(query: str, rate_state: dict) -> list[SearchResult]:
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_CX:
        return []
    _rate_limit(rate_state, "google", GOOGLE_RATE_LIMIT_SECONDS)
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_CX,
        "q": query,
    }
    resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        return []
    data = resp.json()
    items = data.get("items", [])[:5]
    results = []
    for item in items:
        results.append(
            SearchResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
                display_link=item.get("displayLink", ""),
            )
        )
    return results


def serpapi_search(query: str, rate_state: dict) -> list[SearchResult]:
    if not SERPAPI_KEY:
        return []
    _rate_limit(rate_state, "google", GOOGLE_RATE_LIMIT_SECONDS)
    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": 5,
    }
    resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        return []
    data = resp.json()
    items = data.get("organic_results", [])[:5]
    results = []
    for item in items:
        results.append(
            SearchResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
                display_link=item.get("displayed_link", ""),
            )
        )
    return results


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def _looks_like_directory(domain: str) -> bool:
    return any(blocked in domain for blocked in BLOCKED_DOMAINS)


def score_candidate(
    domain: str,
    results: list[SearchResult],
    query_type: str,
    tokens: list[str],
    expected_domains: set[str],
) -> tuple[float, str]:
    score = 0.0
    evidence = []

    if any(tok in domain for tok in tokens):
        score += 0.10
        evidence.append("domain contains firm token")

    if domain in expected_domains:
        score += 0.10
        evidence.append("exact domain match")

    if results:
        top = results[0]
        if query_type == "official" and domain == _extract_domain(top.link):
            score += 0.20
            evidence.append("top result for official query")

    appearances = sum(1 for r in results if _extract_domain(r.link) == domain)
    if appearances > 1:
        score += 0.15
        evidence.append("domain appears multiple times")

    joined_text = " ".join([r.title + " " + r.snippet for r in results]).lower()
    if any(tok in joined_text for tok in tokens):
        score += 0.50
        evidence.append("brand tokens in title/snippet")

    if _looks_like_directory(domain):
        score -= 0.50
        evidence.append("blocked directory/news site")

    if domain.count(".") > 2:
        score -= 0.30
        evidence.append("subdomain on another platform")

    if domain.startswith("http"):
        score -= 0.20
        evidence.append("domain looks like full url")

    return score, "; ".join(evidence)


def verify_domain(domain: str, tokens: list[str], rate_state: dict) -> tuple[bool, str, str]:
    if not domain:
        return False, "", "empty domain"

    if _looks_like_directory(domain):
        return False, "", "blocked directory/news domain"

    _rate_limit(rate_state, domain, SITE_RATE_LIMIT_SECONDS)
    url = f"https://{domain}"
    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return False, "", f"request failed: {e}"

    if resp.status_code not in (200, 301, 302):
        return False, "", f"bad status {resp.status_code}"

    final_domain = _extract_domain(resp.url)
    if _looks_like_directory(final_domain):
        return False, "", "redirected to directory/news site"

    html = resp.text.lower()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).lower() if title_match else ""

    meta_match = re.search(r"og:site_name\"\s+content=\"([^\"]+)\"", html)
    meta = meta_match.group(1).lower() if meta_match else ""

    nav_keywords = ["professionals", "people", "lawyers", "attorneys", "offices"]
    nav_hit = any(k in html for k in nav_keywords)

    if any(tok in title or tok in meta or tok in html for tok in tokens):
        return True, resp.url, "token evidence in homepage"

    if nav_hit:
        return True, resp.url, "nav keywords present on homepage"

    for path in ["/about", "/people", "/professionals"]:
        _rate_limit(rate_state, final_domain, SITE_RATE_LIMIT_SECONDS)
        try:
            sub = requests.get(f"https://{final_domain}{path}", timeout=DEFAULT_TIMEOUT)
            if sub.status_code == 200:
                if any(tok in sub.text.lower() for tok in tokens):
                    return True, sub.url, f"token evidence in {path}"
        except Exception:
            continue

    return False, resp.url, "no brand evidence"


def discover_official_domain(firm_name: str, refresh: bool, rate_state: dict, cache: dict, alias_cache: dict) -> dict:
    normalized = normalize_firm_name(firm_name)
    tokens = normalized.split()
    aliases = generate_aliases(normalized)
    expected_domains = _expected_domains(aliases)

    # cache hit
    cache_entry = cache.get(normalized)
    if cache_entry and not refresh:
        try:
            ts = datetime.fromisoformat(cache_entry.get("timestamp"))
            if datetime.utcnow() - ts <= timedelta(days=30):
                return {
                    "firm": firm_name,
                    "normalized_firm": normalized,
                    "official_website_url": cache_entry.get("chosen_url", ""),
                    "discovery_method": "cache",
                    "confidence_score": cache_entry.get("confidence", 0.0),
                    "evidence": cache_entry.get("evidence", "cache"),
                    "notes": "cache hit",
                }
        except Exception:
            pass

    # alias cache hit
    alias_hit = alias_cache.get(normalized)
    if alias_hit:
        alias_url = alias_hit
        if not alias_url.startswith("http"):
            alias_url = f"https://{alias_url}"
        return {
            "firm": firm_name,
            "normalized_firm": normalized,
            "official_website_url": alias_url,
            "discovery_method": "alias_match",
            "confidence_score": 0.9,
            "evidence": "alias cache",
            "notes": "alias cache hit",
        }

    candidates: dict[str, dict] = {}
    attempted_queries = []

    def add_candidates(results: list[SearchResult], query_type: str):
        for res in results:
            domain = _extract_domain(res.link)
            if not domain:
                continue
            score, evidence = score_candidate(domain, results, query_type, tokens, expected_domains)
            if domain not in candidates:
                candidates[domain] = {
                    "score": score,
                    "evidence": evidence,
                    "results": results,
                    "exact_match": domain in expected_domains,
                }
            else:
                candidates[domain]["score"] = max(candidates[domain]["score"], score)

    queries = [
        (f"{firm_name} official website", "official"),
        (f"{firm_name} law firm", "lawfirm"),
        (f"site:amlaw.com {firm_name}", "corroboration"),
        (f"{firm_name} site:com OR site:net OR site:org", "domain_focus"),
    ]

    if GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX:
        for q, qtype in queries:
            attempted_queries.append(q)
            results = google_cse_search(q, rate_state)
            add_candidates(results, qtype)
            if candidates and max(c["score"] for c in candidates.values()) >= 0.9:
                break
    elif SERPAPI_KEY:
        for q, qtype in queries:
            attempted_queries.append(q)
            results = serpapi_search(q, rate_state)
            add_candidates(results, qtype)
            if candidates and max(c["score"] for c in candidates.values()) >= 0.9:
                break
    else:
        attempted_queries.append("fallback_guess")

    # fallback guess
    if not candidates:
        for alias in aliases:
            token = alias.replace(" ", "")
            for guess in [f"{token}.com", f"www.{token}.com"]:
                candidates[guess] = {
                    "score": 0.2,
                    "evidence": "fallback guess",
                    "results": [],
                }

    # verification
    best = None
    for domain, data in sorted(
        candidates.items(),
        key=lambda x: (-x[1]["score"], x[0]),
    )[:10]:
        ok, chosen_url, verify_note = verify_domain(domain, tokens, rate_state)
        if ok:
            best = (domain, chosen_url, data)
            data["verify_note"] = verify_note
            break
        data["verify_note"] = verify_note

    if best:
        domain, chosen_url, data = best
        evidence = data.get("evidence", "")
        confidence = min(1.0, max(0.0, data.get("score", 0.0)))
        cache[normalized] = {
            "domain": domain,
            "chosen_url": chosen_url,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat(),
            "evidence": evidence + "; " + data.get("verify_note", ""),
        }
        alias_cache[normalized] = domain
        method = "google_cse" if GOOGLE_CSE_API_KEY else "serpapi" if SERPAPI_KEY else "fallback_guess"
        if data.get("exact_match"):
            method = "exact_match"

        return {
            "firm": firm_name,
            "normalized_firm": normalized,
            "official_website_url": chosen_url,
            "discovery_method": method,
            "confidence_score": confidence,
            "evidence": evidence,
            "notes": data.get("verify_note", ""),
        }

    # failure
    return {
        "firm": firm_name,
        "normalized_firm": normalized,
        "official_website_url": "",
        "discovery_method": "unresolved",
        "confidence_score": 0.0,
        "evidence": "",
        "notes": "no verified domain",
        "attempted_queries": attempted_queries,
        "candidates": list(candidates.keys())[:10],
    }


def process_excel(input_path: str, refresh: bool, max_firms: int, start_index: int) -> None:
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    wb = load_workbook(input_path)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    if "Firm" not in headers:
        raise ValueError("Input must have a 'Firm' column")

    firm_col = headers.index("Firm") + 1

    # output workbook
    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = "Firms"
    out_ws.append([
        "Firm",
        "normalized_firm",
        "official_website_url",
        "discovery_method",
        "confidence_score",
        "evidence",
        "notes",
    ])

    failures = Workbook()
    fail_ws = failures.active
    fail_ws.title = "Failures"
    fail_ws.append([
        "Firm",
        "normalized_firm",
        "attempted_queries",
        "candidates",
        "failure_reason",
        "timestamp",
    ])

    cache = _load_json(CACHE_FILE)
    alias_cache = _load_json(ALIAS_FILE)
    rate_state: dict[str, float] = {}

    firms = []
    for row in ws.iter_rows(min_row=2):
        firm = row[firm_col - 1].value
        if firm:
            firms.append(str(firm).strip())

    if start_index > 0:
        firms = firms[start_index:]
    if max_firms > 0:
        firms = firms[:max_firms]

    for firm in firms:
        result = discover_official_domain(firm, refresh, rate_state, cache, alias_cache)
        out_ws.append([
            result["firm"],
            result["normalized_firm"],
            result["official_website_url"],
            result["discovery_method"],
            result["confidence_score"],
            result["evidence"],
            result["notes"],
        ])

        if not result.get("official_website_url"):
            fail_ws.append([
                result["firm"],
                result["normalized_firm"],
                ", ".join(result.get("attempted_queries", [])),
                ", ".join(result.get("candidates", [])),
                result.get("notes", ""),
                datetime.utcnow().isoformat(),
            ])

    # save outputs
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(os.path.dirname(input_path), f"{base}_with_websites.xlsx")
    out_wb.save(output_path)
    failures.save(OUTPUT_FAILURES)

    _save_json(CACHE_FILE, cache)
    _save_json(ALIAS_FILE, alias_cache)

    print(f"Saved: {output_path}")
    print(f"Failures: {OUTPUT_FAILURES}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Firm website discovery with caching")
    parser.add_argument("excel_path", help="Input Excel file with Firm column")
    parser.add_argument("--refresh", action="store_true", help="Ignore cache")
    parser.add_argument("--max-firms", type=int, default=0, help="Max firms to process")
    parser.add_argument("--start-index", type=int, default=0, help="Start index")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    process_excel(args.excel_path, args.refresh, args.max_firms, args.start_index)


if __name__ == "__main__":
    main()
