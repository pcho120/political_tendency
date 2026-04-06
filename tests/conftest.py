"""Pytest fixtures for fixture-backed test modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
HTML_FIXTURES_DIR = FIXTURES_DIR / "html"
CACHE_FIXTURES_DIR = FIXTURES_DIR / "cache"


def html_fixture(name: str) -> str:
    """Load an HTML fixture from tests/fixtures/html/."""
    return (HTML_FIXTURES_DIR / name).read_text(encoding="utf-8")


def jsonl_fixture(name: str) -> list[dict[str, Any]]:
    """Load a JSONL fixture from tests/fixtures/cache/."""
    rows: list[dict[str, Any]] = []
    for line in (CACHE_FIXTURES_DIR / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
