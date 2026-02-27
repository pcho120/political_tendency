"""
Quick diagnostic script to:
1. Fetch the Kirkland lawyers listing page (letter=A) and dump card selectors
2. Fetch a single Kirkland profile page and dump key field selectors
"""
import asyncio
from playwright.async_api import async_playwright

LISTING_URL = "https://www.kirkland.com/lawyers?letter=A"
PROFILE_URL  = "https://www.kirkland.com/lawyers/b/blake-ben"


async def fetch_listing():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print(f"[LISTING] Navigating to {LISTING_URL}")
        await page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=30000)
        # Wait up to 10s for any card-like element to appear
        selectors_to_try = [
            ".people-card",
            ".professional-card",
            "[class*='card']",
            "[class*='person']",
            "[class*='attorney']",
            "[class*='people']",
            "a[href*='/lawyers/']",
        ]
        for sel in selectors_to_try:
            try:
                await page.wait_for_selector(sel, timeout=10000)
                count = await page.eval_on_selector_all(sel, "els => els.length")
                print(f"[LISTING] Selector '{sel}' found — count={count}")
            except Exception:
                print(f"[LISTING] Selector '{sel}' NOT found within 10s")

        # Dump first 3000 chars of body HTML
        body = await page.inner_html("body")
        print("\n[LISTING] Body HTML (first 5000 chars):")
        print(body[:5000])
        await browser.close()


async def fetch_profile():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print(f"\n[PROFILE] Navigating to {PROFILE_URL}")
        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(6000)  # let Vue render

        # Try common name/title selectors
        field_selectors = {
            "name":     ["h1", ".bio-header__name", ".attorney-name", "[class*='name']", ".page-header__title"],
            "title":    [".bio-header__title", ".attorney-title", "[class*='title']", ".page-header__subtitle"],
            "office":   [".bio-header__office", "[class*='office']", "[class*='location']"],
            "practice": ["[class*='practice']", "[class*='expertise']"],
            "bar":      ["[class*='bar']", "[class*='admission']"],
            "education":["[class*='education']", "[class*='degree']"],
        }

        for field, sels in field_selectors.items():
            for sel in sels:
                try:
                    els = await page.query_selector_all(sel)
                    if els:
                        texts = []
                        for el in els[:3]:
                            t = (await el.inner_text()).strip()
                            if t:
                                texts.append(t[:100])
                        if texts:
                            print(f"[PROFILE] {field} | '{sel}' => {texts}")
                            break
                except Exception as e:
                    pass

        # Dump full page HTML
        html = await page.content()
        print(f"\n[PROFILE] Full HTML length: {len(html)}")
        print("\n[PROFILE] HTML sample (first 8000 chars):")
        print(html[:8000])

        # Save full HTML for inspection
        with open("kirkland_profile_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\n[PROFILE] Full HTML saved to kirkland_profile_debug.html")

        await browser.close()


async def main():
    await fetch_listing()
    await fetch_profile()


if __name__ == "__main__":
    asyncio.run(main())
