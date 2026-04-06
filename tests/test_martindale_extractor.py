#!/usr/bin/env python3
"""Martindale compliance and extraction tests."""

from __future__ import annotations

import sys
from pathlib import Path

import responses

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import external_directory_extractor as martindale_module


def test_martindale_url_no_search_path() -> None:
    source = (ROOT / "external_directory_extractor.py").read_text(encoding="utf-8")
    assert "/search/" not in source


def test_martindale_profile_mapping() -> None:
    profile = martindale_module._martindale_item_to_profile(
        {
            "fullName": "Jane Doe",
            "title": "Partner",
            "profileUrl": "/attorney/jane-doe/",
            "city": "New York",
            "state": "NY",
            "department": ["Litigation"],
            "practiceAreas": ["Commercial Litigation"],
            "industries": ["Financial Services"],
            "barAdmissions": ["New York"],
            "education": [
                {
                    "degree": "J.D.",
                    "school": "Harvard Law School",
                    "year": 2010,
                }
            ],
            "firmName": "Example & Partners",
        },
        "Example & Partners",
    )

    assert profile is not None
    assert profile.full_name == "Jane Doe"
    assert profile.title == "Partner"
    assert profile.offices == ["New York, NY"]
    assert profile.department == ["Litigation"]
    assert profile.practice_areas == ["Commercial Litigation"]
    assert profile.industries == ["Financial Services"]
    assert profile.bar_admissions == ["New York"]
    assert len(profile.education) == 1
    assert profile.education[0].school == "Harvard Law School"


@responses.activate
def test_martindale_rate_limit(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(martindale_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    responses.add(
        responses.GET,
        "https://www.martindale.com/sitemap_browse.xml",
        body=(
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<urlset>"
            "<url><loc>https://www.martindale.com/organization/example-partners/</loc></url>"
            "</urlset>"
        ),
        status=200,
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        "https://www.martindale.com/organization/example-partners/",
        body=(
            "<html><body>"
            "<div class='attorney-card'>"
            "<div class='firm'>Example &amp; Partners</div>"
            "<h2 class='name'>Jane Doe</h2>"
            "<a href='/attorney/jane-doe/'></a>"
            "<div class='location'>New York, NY</div>"
            "<div class='title'>Partner</div>"
            "</div>"
            "</body></html>"
        ),
        status=200,
        content_type="text/html",
    )

    extractor = martindale_module.ExternalDirectoryExtractor()
    profiles, summary = extractor._extract_from_martindale("Example & Partners", max_results=5)

    assert summary.source == "martindale"
    assert len(profiles) == 1
    assert sleeps == [3.0, 3.0]


@responses.activate
def test_martindale_firm_name_filtering(monkeypatch) -> None:
    monkeypatch.setattr(martindale_module.time, "sleep", lambda _seconds: None)

    responses.add(
        responses.GET,
        "https://www.martindale.com/sitemap_browse.xml",
        body=(
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<urlset>"
            "<url><loc>https://www.martindale.com/organization/example-partners/</loc></url>"
            "</urlset>"
        ),
        status=200,
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        "https://www.martindale.com/organization/example-partners/",
        body=(
            "<html><body>"
            "<div class='attorney-card'>"
            "<div class='firm'>Example &amp; Partners</div>"
            "<h2 class='name'>Jane Doe</h2>"
            "<a href='/attorney/jane-doe/'></a>"
            "<div class='location'>New York, NY</div>"
            "<div class='title'>Partner</div>"
            "</div>"
            "<div class='attorney-card'>"
            "<div class='firm'>Wrong Firm LLP</div>"
            "<h2 class='name'>John Roe</h2>"
            "<a href='/attorney/john-roe/'></a>"
            "<div class='location'>Chicago, IL</div>"
            "<div class='title'>Associate</div>"
            "</div>"
            "</body></html>"
        ),
        status=200,
        content_type="text/html",
    )

    extractor = martindale_module.ExternalDirectoryExtractor()
    profiles, _summary = extractor._extract_from_martindale("Example & Partners", max_results=5)

    assert [profile.full_name for profile in profiles] == ["Jane Doe"]
    assert all(profile.firm == "Example & Partners" for profile in profiles)
