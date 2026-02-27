"""
Debug why offices are empty in the enriched Playwright HTML.
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def fetch(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(".profile-heading, h1", timeout=8000)
        except:
            await page.wait_for_timeout(4000)
        return await page.content()

def check(url):
    html = asyncio.run(fetch(url))
    soup = BeautifulSoup(html, 'html.parser')
    
    # Check location elements
    locs = soup.select('.profile-heading__location-link')
    print(f"\nURL: {url}")
    print(f"HTML length: {len(html)}")
    print(f".profile-heading__location-link count: {len(locs)}")
    for l in locs:
        print(f"  => {l.get_text(strip=True)!r}")
    
    # Check heading children
    heading = soup.select_one('.profile-heading')
    if heading:
        loc_text = heading.get_text(' ', strip=True)[:200]
        print(f"profile-heading text: {loc_text!r}")

check("https://www.kirkland.com/lawyers/a/abate-anthony")
check("https://www.kirkland.com/lawyers/a/abbassi-rajab")
