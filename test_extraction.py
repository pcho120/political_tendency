#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test extraction logic"""

import sys
import io
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add extraction to path
sys.path.insert(0, str(Path(__file__).parent / "extraction"))

from find_attorney import AttorneyFinder

# Create finder with sources file
finder = AttorneyFinder(
    limit=5,
    sheet_name="Attorneys",
    max_firms=1,
    workers=4,
    debug_firm="Latham",
    sources_file="outputs/sources_test.xlsx"
)

# Test sitemap extraction directly
base_url = "https://www.lw.com"
sitemap_url = "https://www.lw.com/sitemap_index.xml"

print(f"Testing sitemap extraction...")
print(f"Sitemap URL: {sitemap_url}")
print(f"Base URL: {base_url}\n")

urls = finder._extract_profile_urls_from_sitemap_direct(sitemap_url, base_url)
print(f"Extracted {len(urls)} profile URLs")

if urls:
    print(f"\nFirst 10 URLs:")
    for url in urls[:10]:
        print(f"  {url}")
else:
    print("\n⚠ No URLs extracted - checking why...")
    
    # Manual test
    import requests
    import xml.etree.ElementTree as ET
    
    resp = requests.get(sitemap_url)
    print(f"Status: {resp.status_code}")
    
    root = ET.fromstring(resp.content)
    is_index = any(elem.tag.endswith('}sitemap') or elem.tag == 'sitemap' for elem in root.iter())
    print(f"Is sitemap index: {is_index}")
    
    if is_index:
        for elem in root.iter():
            if elem.tag.endswith('}loc') or elem.tag == 'loc':
                sub_url = elem.text
                if sub_url and 'people' in sub_url.lower():
                    print(f"Found sub-sitemap: {sub_url}")
                    
                    # Try extracting from it
                    sub_resp = requests.get(sub_url)
                    sub_root = ET.fromstring(sub_resp.content)
                    
                    count = 0
                    for sub_elem in sub_root.iter():
                        if sub_elem.tag.endswith('}loc') or sub_elem.tag == 'loc':
                            url = sub_elem.text
                            if url and finder._is_profile_url(url, base_url):
                                count += 1
                                if count <= 5:
                                    print(f"  ✓ Profile URL: {url}")
                    
                    print(f"  Total profile URLs in sub-sitemap: {count}")
