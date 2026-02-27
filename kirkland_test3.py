"""
Test how many attorneys are on the listing page per letter and how to get them all.
Also test scrolling to get more results.
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Navigate to letter A
        url = "https://www.kirkland.com/lawyers?letter=A"
        print(f"Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(".person-result", timeout=15000)

        count1 = await page.eval_on_selector_all(".person-result", "els => els.length")
        print(f"Initial count after load: {count1}")

        # Scroll down a few times
        for i in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            count = await page.eval_on_selector_all(".person-result", "els => els.length")
            print(f"After scroll {i+1}: {count}")

        # Get all links
        links = await page.eval_on_selector_all(
            ".person-result__name a",
            "els => els.map(e => e.href)"
        )
        print(f"\nTotal profile links: {len(links)}")
        for l in links[:5]:
            print(f"  {l}")

        # Also check if there's a 'load more' button or pagination
        load_more = await page.query_selector_all("[class*='load-more'], [class*='pagination'], [class*='show-more']")
        print(f"\nLoad-more/pagination elements: {len(load_more)}")
        for el in load_more:
            cls = await el.get_attribute("class")
            txt = (await el.inner_text())[:100]
            print(f"  class={cls} text={txt}")

        # Check total count hint in the page
        for pattern in ["total", "count", "results", "showing"]:
            els = await page.query_selector_all(f"[class*='{pattern}']")
            for el in els:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) < 200:
                    print(f"  [{pattern}] {txt}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
