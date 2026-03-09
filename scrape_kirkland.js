const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ 
    headless: true,
    channel: undefined  // use bundled chromium, not system chrome
  });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  
  const urls = [
    'https://www.kirkland.com/lawyers/a/abbott-jason',
    'https://www.kirkland.com/lawyers/b/baker-kelvin'
  ];

  for (const url of urls) {
    console.log(`\n${'='.repeat(80)}`);
    console.log(`FETCHING: ${url}`);
    console.log('='.repeat(80));
    
    const page = await context.newPage();
    
    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(3000);
      
      // Get the full HTML
      const html = await page.content();
      
      // Save full HTML
      const slug = url.split('/').pop();
      require('fs').writeFileSync(`kirkland_${slug}.html`, html);
      console.log(`Saved full HTML to kirkland_${slug}.html`);
      
      // Take screenshot
      await page.screenshot({ path: `kirkland_${slug}.png`, fullPage: true });
      console.log(`Saved screenshot to kirkland_${slug}.png`);
      
      // Extract key profile sections via evaluate
      const profileData = await page.evaluate(() => {
        const getInfo = (selector) => {
          const el = document.querySelector(selector);
          return el ? { 
            tag: el.tagName, 
            classes: el.className, 
            text: el.textContent.trim().substring(0, 200),
            outerHTML: el.outerHTML.substring(0, 500)
          } : null;
        };
        
        // Try various selectors for attorney name
        const nameSelectors = ['h1', '.attorney-name', '.bio-name', '.lawyer-name', '[class*="name"]', '[class*="Name"]'];
        const titleSelectors = ['.attorney-title', '.bio-title', '.lawyer-title', '[class*="title"]', '[class*="Title"]', '[class*="position"]'];
        
        const results = {};
        
        // Get all h1, h2, h3 elements
        results.headings = [];
        document.querySelectorAll('h1, h2, h3').forEach(h => {
          results.headings.push({
            tag: h.tagName,
            classes: h.className,
            id: h.id,
            text: h.textContent.trim().substring(0, 200),
            outerHTML: h.outerHTML.substring(0, 500)
          });
        });
        
        // Get all elements with 'attorney', 'lawyer', 'bio', 'profile' in class
        results.profileElements = [];
        document.querySelectorAll('[class*="attorney"], [class*="lawyer"], [class*="bio"], [class*="profile"], [class*="Attorney"], [class*="Lawyer"], [class*="Bio"], [class*="Profile"]').forEach(el => {
          results.profileElements.push({
            tag: el.tagName,
            classes: el.className,
            id: el.id,
            text: el.textContent.trim().substring(0, 100),
            outerHTML: el.outerHTML.substring(0, 300)
          });
        });

        // Look for specific content areas
        results.contentSections = [];
        document.querySelectorAll('section, [class*="section"], [class*="Section"], [class*="detail"], [class*="Detail"]').forEach(el => {
          results.contentSections.push({
            tag: el.tagName,
            classes: el.className,
            id: el.id,
            childrenCount: el.children.length,
            text: el.textContent.trim().substring(0, 150)
          });
        });

        // Look for practice areas, education, admissions keywords
        results.keywordElements = [];
        const allElements = document.querySelectorAll('*');
        allElements.forEach(el => {
          const text = el.textContent.trim();
          if (el.children.length < 3 && 
              (text === 'Education' || text === 'Admissions' || text === 'Practice Areas' || 
               text === 'Bar Admissions' || text === 'Industries' || text === 'Services' ||
               text === 'Experience' || text === 'Expertise')) {
            results.keywordElements.push({
              tag: el.tagName,
              classes: el.className,
              text: text,
              parentTag: el.parentElement?.tagName,
              parentClasses: el.parentElement?.className,
              outerHTML: el.outerHTML.substring(0, 300),
              nextSiblingHTML: el.nextElementSibling?.outerHTML?.substring(0, 500) || 'none'
            });
          }
        });
        
        // Get the page title
        results.pageTitle = document.title;
        
        return results;
      });
      
      console.log('\n--- PAGE TITLE ---');
      console.log(profileData.pageTitle);
      
      console.log('\n--- HEADINGS ---');
      profileData.headings.forEach(h => console.log(JSON.stringify(h, null, 2)));
      
      console.log('\n--- PROFILE ELEMENTS (class contains attorney/lawyer/bio/profile) ---');
      profileData.profileElements.slice(0, 20).forEach(e => console.log(JSON.stringify(e, null, 2)));
      
      console.log('\n--- CONTENT SECTIONS ---');
      profileData.contentSections.slice(0, 15).forEach(s => console.log(JSON.stringify(s, null, 2)));
      
      console.log('\n--- KEYWORD ELEMENTS (Education, Admissions, etc.) ---');
      profileData.keywordElements.forEach(k => console.log(JSON.stringify(k, null, 2)));
      
    } catch (err) {
      console.error(`Error: ${err.message}`);
    }
    
    await page.close();
  }
  
  await browser.close();
})();
