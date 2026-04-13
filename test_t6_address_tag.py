#!/usr/bin/env python3
"""Task 6 QA scenarios for office/title heuristic expansion."""

from __future__ import annotations

import json
from pathlib import Path

from field_enricher import FieldEnricher


def main() -> None:
    evidence_dir = Path('.sisyphus/evidence')
    evidence_dir.mkdir(parents=True, exist_ok=True)

    fe = FieldEnricher()

    html = '<html><body><div><address>New York, NY 10001</address></div></body></html>'
    profile = {
        'profile_url': 'https://example.com/attorney/1',
        'full_name': 'Test User',
        'title': '',
        'offices': [],
        'practice_areas': [],
        'department': '',
    }
    result = fe.enrich(html, profile, firm_name='Test Firm')
    scenario1 = f"Offices: {result.get('offices', [])}\n"
    assert any('New York' in office for office in result.get('offices', [])), (
        f"Expected New York in offices, got: {result.get('offices', [])}"
    )
    scenario1 += 'PASS\n'
    (evidence_dir / 'task-6-address-tag.txt').write_text(scenario1, encoding='utf-8')

    html2 = '<html><body><span itemprop="jobTitle">Senior Associate</span></body></html>'
    profile2 = {
        'profile_url': 'https://example.com/attorney/2',
        'full_name': 'Test User',
        'title': '',
        'offices': [],
        'practice_areas': [],
        'department': '',
    }
    result2 = fe.enrich(html2, profile2, firm_name='Test Firm')
    scenario2 = f"Title: {result2.get('title', '')}\n"
    assert result2.get('title', '') != '', f"Expected title, got empty"
    scenario2 += 'PASS\n'
    (evidence_dir / 'task-6-jobtitle-attr.txt').write_text(scenario2, encoding='utf-8')

    json_ld = json.dumps(
        {
            '@context': 'https://schema.org',
            '@type': 'Person',
            'jobTitle': 'Partner (JSON-LD)',
            'workLocation': {
                '@type': 'PostalAddress',
                'addressLocality': 'Chicago',
                'addressRegion': 'IL',
            },
        }
    )
    html3 = (
        '<html><head><script type="application/ld+json">'
        f'{json_ld}'
        '</script></head><body><span itemprop="jobTitle">Associate (microdata)</span>'
        '<address>Boston, MA</address></body></html>'
    )
    profile3 = {
        'profile_url': 'https://example.com/attorney/3',
        'full_name': 'Test User',
        'title': '',
        'offices': [],
        'practice_areas': [],
        'department': '',
    }
    result3 = fe.enrich(html3, profile3, firm_name='Test Firm')
    scenario3 = f"Title: {result3.get('title', '')}, Offices: {result3.get('offices', [])}\n"
    assert 'Partner' in result3.get('title', ''), (
        f"Expected JSON-LD title 'Partner', got: {result3.get('title', '')}"
    )
    scenario3 += 'PASS - JSON-LD priority maintained\n'
    (evidence_dir / 'task-6-jsonld-priority.txt').write_text(scenario3, encoding='utf-8')

    print(scenario1, end='')
    print(scenario2, end='')
    print(scenario3, end='')


if __name__ == '__main__':
    main()
