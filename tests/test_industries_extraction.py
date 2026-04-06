#!/usr/bin/env python3
"""Industries extraction red-phase tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attorney_extractor import AttorneyExtractor
from tests.conftest import html_fixture


def _extract_industries(html_name: str) -> list[str]:
    soup = BeautifulSoup(html_fixture(html_name), "html.parser")
    extractor = AttorneyExtractor()
    return extractor._extract_industries_bs4(soup)


def test_extract_industries_from_heading() -> None:
    industries = _extract_industries("industries_heading_section.html")
    assert industries == ["Financial Services", "Healthcare", "Real Estate", "Asset Management"]


def test_extract_industries_from_json_ld() -> None:
    industries = _extract_industries("industries_json_ld.html")
    assert "Banking" in industries
    assert "Capital Markets" in industries
    assert "Private Equity" in industries


def test_extract_industries_from_sidebar() -> None:
    industries = _extract_industries("industries_sidebar.html")
    assert industries == ["Energy", "Technology", "Insurance", "Infrastructure"]


def test_industries_vs_practice_areas() -> None:
    html = """<!DOCTYPE html>
    <html lang='en'>
      <body>
        <main>
          <section>
            <h2>Practice Areas</h2>
            <ul>
              <li>Corporate</li>
              <li>Litigation</li>
            </ul>
          </section>
          <section>
            <h2>Industries</h2>
            <ul>
              <li>Technology</li>
              <li>Healthcare</li>
              <li>Life Sciences</li>
            </ul>
          </section>
        </main>
      </body>
    </html>"""
    soup = BeautifulSoup(html, "html.parser")
    extractor = AttorneyExtractor()
    industries = extractor._extract_industries_bs4(soup)
    assert industries == ["Technology", "Healthcare", "Life Sciences"]
    assert "Corporate" not in industries
    assert "Litigation" not in industries


def test_industries_empty_returns_sentinel() -> None:
    soup = BeautifulSoup("<!DOCTYPE html><html><body></body></html>", "html.parser")
    extractor = AttorneyExtractor()
    industries = extractor._extract_industries_bs4(soup)
    assert industries == ["no industry field"]
