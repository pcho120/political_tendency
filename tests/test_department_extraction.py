#!/usr/bin/env python3
"""Department extraction red-phase tests."""

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


def _extract_departments(html_name: str) -> list[str]:
    soup = BeautifulSoup(html_fixture(html_name), "html.parser")
    extractor = AttorneyExtractor()
    return extractor._extract_departments_bs4(soup)


def test_extract_department_from_json_ld() -> None:
    departments = _extract_departments("department_json_ld.html")
    assert "Litigation" in departments


def test_extract_department_from_css_class() -> None:
    departments = _extract_departments("department_css_class.html")
    assert departments == ["Corporate Finance"]


def test_extract_department_from_heading() -> None:
    departments = _extract_departments("department_accordion.html")
    assert departments == ["Restructuring"]


def test_department_contamination_filter() -> None:
    departments = _extract_departments("department_concat_blob.html")
    assert departments == ["Environmental & Sustainability"]
    assert all("LawyersPractices" not in dept for dept in departments)


def test_department_empty_returns_sentinel() -> None:
    empty_html = "<!DOCTYPE html><html><head><title>Empty</title></head><body></body></html>"
    soup = BeautifulSoup(empty_html, "html.parser")
    extractor = AttorneyExtractor()
    departments = extractor._extract_departments_bs4(soup)
    assert departments == []
