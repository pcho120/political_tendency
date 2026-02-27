"""
Quick validation: fetch a real Kirkland profile via Playwright, run it through
attorney_extractor.AttorneyExtractor, and print all fields.
"""
import asyncio
from playwright.async_api import async_playwright
from attorney_extractor import AttorneyExtractor

PROFILE_URL = "https://www.kirkland.com/lawyers/a/abate-anthony"


async def fetch_html(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ))
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # Wait for profile heading
        try:
            await page.wait_for_selector(".profile-heading, h1", timeout=8000)
        except Exception:
            await page.wait_for_timeout(4000)
        html = await page.content()
        await browser.close()
        return html


def main():
    print(f"Fetching: {PROFILE_URL}")
    html = asyncio.run(fetch_html(PROFILE_URL))
    print(f"HTML size: {len(html):,} bytes")

    extractor = AttorneyExtractor()
    profile = extractor.extract_profile("Kirkland & Ellis", PROFILE_URL, html)

    print(f"\n=== Extraction Result ===")
    print(f"Status:         {profile.extraction_status}")
    print(f"Name:           {profile.full_name!r}")
    print(f"Title:          {profile.title!r}")
    print(f"Department:     {profile.department!r}")
    print(f"Offices:        {profile.offices}")
    print(f"Practice Areas: {profile.practice_areas}")
    print(f"Bar Admissions: {profile.bar_admissions}")
    print(f"Education:")
    for e in (profile.education or []):
        print(f"  - {e.school} | {e.degree} | {e.year}")
    print(f"Missing Fields: {profile.missing_fields}")


if __name__ == "__main__":
    main()
