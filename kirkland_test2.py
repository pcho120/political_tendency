"""
Diagnostic step 2:
1. Get the first few valid profile URLs from the listing
2. Fetch one real profile and dump its structure
"""
import asyncio
from playwright.async_api import async_playwright

LISTING_URL = "https://www.kirkland.com/lawyers?letter=A"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # --- LISTING PAGE ---
        print(f"[LISTING] Navigating to {LISTING_URL}")
        await page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=30000)

        # Wait for person-like elements
        try:
            await page.wait_for_selector("[class*='person']", timeout=12000)
            print("[LISTING] [class*='person'] appeared")
        except Exception:
            print("[LISTING] timeout waiting for [class*='person']")

        # Collect all attorney links
        links = await page.eval_on_selector_all(
            "a[href*='/lawyers/']",
            "els => els.map(e => e.href)"
        )
        # Filter to actual profile links (not just /lawyers)
        profile_links = [l for l in links if l.count('/') > 4]
        print(f"[LISTING] Profile links found: {len(profile_links)}")
        for l in profile_links[:10]:
            print(f"  {l}")

        # Dump HTML around person cards
        person_html = await page.eval_on_selector_all(
            "[class*='person']",
            "els => els.slice(0, 3).map(e => e.outerHTML.slice(0, 500))"
        )
        print("\n[LISTING] First 3 person elements:")
        for i, h in enumerate(person_html):
            print(f"  [{i}] {h}\n")

        # Pick first valid profile
        if profile_links:
            profile_url = profile_links[0]
            print(f"\n[PROFILE] Fetching: {profile_url}")
            profile_page = await browser.new_page()
            await profile_page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            await profile_page.wait_for_timeout(6000)

            # Check h1
            try:
                h1 = await profile_page.inner_text("h1")
                print(f"[PROFILE] h1: {h1}")
            except:
                print("[PROFILE] No h1")

            # Dump body classes
            body_class = await profile_page.get_attribute("body", "class")
            print(f"[PROFILE] body class: {body_class}")

            # Find elements with class containing bio/attorney/person/profile/header
            for pattern in ["bio", "attorney", "profile", "professional", "lawyer"]:
                els = await profile_page.query_selector_all(f"[class*='{pattern}']")
                if els:
                    for el in els[:3]:
                        cls = await el.get_attribute("class")
                        txt = (await el.inner_text())[:200].strip()
                        print(f"[PROFILE] [{pattern}] class='{cls}' text='{txt[:100]}'")

            # Save HTML
            html = await profile_page.content()
            with open("kirkland_profile_debug2.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n[PROFILE] HTML saved to kirkland_profile_debug2.html (len={len(html)})")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
