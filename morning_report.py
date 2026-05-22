#!/usr/bin/env python3
"""Morning report: summarize phase3_full results for review."""
from __future__ import annotations
import json
import os
import sys
from collections import Counter, defaultdict

OUT_DIR = "phase3_full"
JSONL = os.path.join(OUT_DIR, "attorneys.jsonl")
COVERAGE = os.path.join(OUT_DIR, "coverage_metrics.json")
PHASE2_COV = "phase2_post_12h_full/coverage_metrics.json"


def load_corrections() -> set[str]:
    try:
        return set(json.load(open("url_corrections.json")).keys())
    except Exception:
        return set()


def load_phase2_zero_firms() -> set[str]:
    try:
        data = json.load(open(PHASE2_COV))
        return {m["firm"] for m in data if m.get("extracted_attorney_count", 0) == 0}
    except Exception:
        return set()


def main() -> None:
    if not os.path.exists(JSONL):
        print(f"NO OUTPUT YET: {JSONL} missing")
        sys.exit(1)

    by_firm: dict[str, list[dict]] = defaultdict(list)
    total = 0
    with open(JSONL) as f:
        for line in f:
            try:
                a = json.loads(line)
            except Exception:
                continue
            by_firm[a.get("firm", "?")].append(a)
            total += 1

    print(f"=== PHASE3 MORNING REPORT ===")
    print(f"Total attorneys: {total}")
    print(f"Firms with data: {len(by_firm)}")

    fields = ["full_name", "title", "offices", "department", "practice_areas",
              "industries", "bar_admissions", "education"]
    print(f"\n=== FIELD COVERAGE (of {total} records) ===")
    for fld in fields:
        n = 0
        for recs in by_firm.values():
            for a in recs:
                v = a.get(fld)
                if v and v not in (["no industry field"], ["unknown"], "no JD"):
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        if any(d.get("school") not in (None, "unknown") for d in v):
                            n += 1
                    elif isinstance(v, list):
                        n += 1
                    elif v:
                        n += 1
        pct = 100 * n / total if total else 0
        print(f"  {fld:18s} {n:6d}/{total} ({pct:5.1f}%)")

    corrections = load_corrections()
    p2_zero = load_phase2_zero_firms()
    print(f"\n=== URL CORRECTION WINS ===")
    won = 0
    for f in corrections:
        if f in by_firm and len(by_firm[f]) > 0:
            won += 1
            print(f"  ✅ {f}: {len(by_firm[f])} attorneys (was 0 in phase2)")
    print(f"\nCorrected firms recovered: {won}/{len(corrections)}")

    print(f"\n=== STILL ZERO ATTORNEYS (in phase3) ===")
    if os.path.exists(COVERAGE):
        try:
            cov = json.load(open(COVERAGE))
            zeros = [m["firm"] for m in cov if m.get("extracted_attorney_count", 0) == 0]
            print(f"  {len(zeros)} firms (phase2 had {len(p2_zero)})")
            improvement = len(p2_zero) - len(zeros)
            print(f"  → Net recovery: {improvement} firms")
            print(f"\n  First 30 still-failing:")
            for f in zeros[:30]:
                print(f"    {f}")
        except Exception as e:
            print(f"  (coverage_metrics.json parse error: {e})")

    print(f"\n=== TOP 20 FIRMS BY EXTRACTED COUNT ===")
    ranked = sorted(by_firm.items(), key=lambda kv: -len(kv[1]))[:20]
    for firm, recs in ranked:
        offices_pop = sum(1 for a in recs if a.get("offices"))
        print(f"  {len(recs):5d}  {firm:40s}  offices_filled={offices_pop}/{len(recs)}")


if __name__ == "__main__":
    main()
