#!/usr/bin/env python3
"""
Find Attorney (Hybrid: Sitemap + Crawl)
Combines Sitemap speed with Directory Crawling for max coverage.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import time
import re
import os
import sys
import threading
import requests
import xml.etree.ElementTree as ET
import gzip
from urllib.parse import urljoin, urlparse
from openpyxl import load_workbook
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Max threads for parallel scraping
MAX_WORKERS = 10 

# Common directory paths
DIRECTORY_PATHS = [
    "/people", "/attorneys", "/lawyers", "/professionals", "/our-people", 
    "/team", "/staff", "/directory", "/people-directory"
]

class FindAttorneyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Attorney Finder (Max Coverage)")
        self.root.geometry("800x650")
        
        main_frame = ttk.Frame(root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Find Attorneys (Hybrid Mode)", font=("Arial", 16, "bold")).pack(pady=(0, 20))
        
        # File Selection
        file_frame = ttk.LabelFrame(main_frame, text="Input", padding="10")
        file_frame.pack(fill=tk.X, pady=5)
        self.file_path_var = tk.StringVar(value="No file selected")
        ttk.Label(file_frame, textvariable=self.file_path_var).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="Browse...", command=self.browse_file).pack(side=tk.RIGHT)
        
        # Settings
        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        settings_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(settings_frame, text="Max pages to crawl (0=unlimited):").pack(side=tk.LEFT)
        self.max_pages_var = tk.IntVar(value=50) # Safety limit
        ttk.Spinbox(settings_frame, from_=0, to=1000, textvariable=self.max_pages_var, width=8).pack(side=tk.LEFT, padx=5)

        ttk.Checkbutton(settings_frame, text="Use Sitemap (Fast)", variable=tk.BooleanVar(value=True)).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(settings_frame, text="Use Crawling (Thorough)", variable=tk.BooleanVar(value=True)).pack(side=tk.LEFT, padx=10)
        
        # Progress
        self.progress_text = tk.Text(main_frame, height=15, state="disabled")
        self.progress_text.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        self.run_btn = ttk.Button(btn_frame, text="Start Extraction", command=self.start_thread, state="disabled")
        self.run_btn.pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Exit", command=root.quit).pack(side=tk.RIGHT)

        self.current_file = None
        self.stop_event = threading.Event()

    def browse_file(self):
        path = filedialog.askopenfilename(filetypes=[("Excel Files", "*.xlsx")])
        if path:
            self.current_file = path
            self.file_path_var.set(os.path.basename(path))
            self.run_btn.config(state="normal")
            self.log(f"Loaded: {path}")

    def log(self, msg):
        self.progress_text.config(state="normal")
        self.progress_text.insert(tk.END, msg + "\n")
        self.progress_text.see(tk.END)
        self.progress_text.config(state="disabled")
        self.root.update_idletasks()

    def start_thread(self):
        self.run_btn.config(state="disabled")
        self.stop_event.clear()
        threading.Thread(target=self.process_file, daemon=True).start()

    def process_file(self):
        try:
            wb = load_workbook(self.current_file)
            ws = wb.active
            
            headers = [cell.value for cell in ws[1]]
            try:
                url_col_idx = headers.index("Official Website") + 1
                firm_col_idx = headers.index("Firm") + 1
            except ValueError:
                self.log("ERROR: File must have 'Firm' and 'Official Website' columns.")
                self.run_btn.config(state="normal")
                return

            if "Attorneys" in wb.sheetnames:
                del wb["Attorneys"]
            out_ws = wb.create_sheet("Attorneys")
            out_ws.append(["Firm", "Attorney Name", "Title", "Practice Area", "Office", "Profile URL"])

            total_processed = 0

            for row in ws.iter_rows(min_row=2, values_only=False):
                if self.stop_event.is_set(): break
                
                firm_name = row[firm_col_idx-1].value
                base_url = row[url_col_idx-1].value
                
                if not base_url or not firm_name:
                    continue

                self.log(f"\nProcessing: {firm_name}...")
                
                # --- PHASE 1: DISCOVERY ---
                all_urls = set()
                
                # A. Sitemap Strategy
                self.log("  [1/2] Checking Sitemap...")
                sitemap_urls = self.get_profile_urls_from_sitemap(base_url)
                if sitemap_urls:
                    self.log(f"    Found {len(sitemap_urls)} from sitemap.")
                    all_urls.update(sitemap_urls)
                
                # B. Crawl Strategy
                self.log("  [2/2] Crawling Directory...")
                crawl_urls = self.crawl_directory(base_url)
                if crawl_urls:
                    new_found = len(crawl_urls)
                    self.log(f"    Found {new_found} from crawling.")
                    all_urls.update(crawl_urls)
                
                self.log(f"  Total Unique Profiles: {len(all_urls)}")
                
                # --- PHASE 2: EXTRACTION ---
                self.log(f"  Extracting Data (Parallel x{MAX_WORKERS})...")
                
                count = 0
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_url = {executor.submit(self.scrape_profile, url): url for url in all_urls}
                    
                    for future in as_completed(future_to_url):
                        if self.stop_event.is_set(): break
                        try:
                            data = future.result()
                            if data:
                                out_ws.append([
                                    firm_name,
                                    data['name'],
                                    data['title'],
                                    data['practice'],
                                    data['office'],
                                    data['url']
                                ])
                                count += 1
                                total_processed += 1
                                if count % 20 == 0:
                                    self.log(f"    Extracted {count}/{len(all_urls)}...")
                        except Exception:
                            pass
                
                self.log(f"  Finished {firm_name}: {count} attorneys added.")
                wb.save(self.current_file)

            self.log(f"\nDONE! Total attorneys found: {total_processed}")
            messagebox.showinfo("Success", f"Extraction Complete!\nTotal attorneys: {total_processed}")

        except Exception as e:
            self.log(f"CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.run_btn.config(state="normal")

    # --- SITEMAP LOGIC (Reused) ---
    def get_profile_urls_from_sitemap(self, base_url):
        # (Same sitemap logic as before - simplified for brevity)
        try:
            domain = urlparse(base_url).netloc
            scheme = urlparse(base_url).scheme
            target = f"{scheme}://{domain}/sitemap.xml" # Simplified check
            
            try:
                resp = requests.head(target, headers=HEADERS, timeout=5)
                if resp.status_code != 200: return []
            except: return []

            return self.parse_sitemap(target)
        except: return []

    def parse_sitemap(self, sitemap_url, depth=0):
        if depth > 2: return []
        urls = set()
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=10)
            if resp.status_code != 200: return []
            
            content = resp.content
            if sitemap_url.endswith(".gz"): content = gzip.decompress(content)
            
            root = ET.fromstring(content)
            is_index = "sitemapindex" in root.tag.lower()
            
            keywords = ["lawyer", "attorney", "people", "team", "bio", "profile"]
            
            for child in root:
                loc = None
                for sub in child:
                    if "loc" in sub.tag.lower():
                        loc = sub.text.strip()
                        break
                if not loc: continue
                
                if is_index:
                    if any(kw in loc.lower() for kw in keywords):
                        urls.update(self.parse_sitemap(loc, depth+1))
                else:
                    if any(kw in loc.lower() for kw in keywords) and len(urlparse(loc).path.split('/')) > 2:
                        urls.add(loc)
        except: pass
        return list(urls)

    # --- CRAWLING LOGIC (New) ---
    def crawl_directory(self, base_url):
        """Finds the directory page and crawls all pages."""
        urls = set()
        directory_url = self.find_directory_url(base_url)
        
        if not directory_url: 
            return []
            
        self.log(f"    Directory found: {directory_url}")
        
        # Crawl pages
        current_url = directory_url
        pages_crawled = 0
        max_pages = self.max_pages_var.get()
        
        while current_url and (max_pages == 0 or pages_crawled < max_pages):
            try:
                self.log(f"      Scanning page {pages_crawled+1}...")
                resp = requests.get(current_url, headers=HEADERS, timeout=10)
                if resp.status_code != 200: break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # 1. Extract Profiles from this page
                page_urls = self.extract_profile_links(soup, base_url)
                if not page_urls: break # Stop if empty page
                
                new_count = len(page_urls - urls)
                urls.update(page_urls)
                
                # 2. Find Next Page
                next_link = self.find_next_page(soup, base_url)
                if not next_link or next_link == current_url: 
                    break
                
                current_url = next_link
                pages_crawled += 1
                time.sleep(1) # Respectful delay
                
            except Exception as e:
                self.log(f"      Crawl error: {e}")
                break
                
        return list(urls)

    def find_directory_url(self, base_url):
        """Guesses the /people or /attorneys URL"""
        for path in DIRECTORY_PATHS:
            url = urljoin(base_url, path)
            try:
                resp = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
                if resp.status_code == 200:
                    return url
            except: pass
        return None

    def extract_profile_links(self, soup, base_url):
        """Finds links that look like profiles on a directory page"""
        urls = set()
        keywords = ["lawyer", "attorney", "people", "team", "bio", "profile"]
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Heuristic: Profile links often contain the keywords OR are nested in 'result-item' divs
            # For now, let's grab any link that looks like a sub-page of the directory
            full_url = urljoin(base_url, href)
            
            # Filter bad links
            if any(x in href.lower() for x in ['pdf', 'vcard', 'mailto', 'linkedin']): continue
            if full_url == base_url: continue
            
            # Profile URLs usually have 2+ segments (e.g. /people/john-doe)
            path = urlparse(full_url).path
            if len(path.strip('/').split('/')) >= 2:
                 urls.add(full_url)
                 
        return urls

    def find_next_page(self, soup, base_url):
        """Finds the 'Next' button link"""
        # Look for text "Next" or ">"
        next_btn = soup.find('a', string=re.compile(r'Next|>', re.I))
        if next_btn and next_btn.get('href'):
            return urljoin(base_url, next_btn['href'])
            
        # Look for class "next"
        next_btn = soup.find('a', class_=re.compile(r'next', re.I))
        if next_btn and next_btn.get('href'):
            return urljoin(base_url, next_btn['href'])
            
        return None

    # --- PROFILE SCRAPER (Reused) ---
    def scrape_profile(self, url):
        # (Same robust scraper as before)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200: return None
            soup = BeautifulSoup(resp.text, 'html.parser')
            text = soup.get_text()
            
            # Name
            name = "Unknown"
            h1 = soup.find('h1')
            if h1: name = h1.get_text(strip=True)
            
            # Title
            title = "Unknown"
            common_titles = ["Partner", "Associate", "Counsel", "Of Counsel", "Shareholder", "Principal"]
            # Look for specific classes first
            for tag in soup.find_all(['h2', 'h3', 'div', 'span', 'p']):
                cls = " ".join(tag.get('class', [])).lower()
                if 'title' in cls or 'position' in cls:
                    t = tag.get_text(strip=True)
                    if t and t != name and len(t) < 50:
                        title = t; break
            
            if title == "Unknown":
                for t in common_titles:
                    if t in text[:3000]: title = t; break

            # Practice
            practice = "Unknown"
            p_headers = soup.find_all(['h2','h3','strong'], string=re.compile(r'Practice|Service', re.I))
            for ph in p_headers:
                container = ph.find_parent('div')
                if container:
                    links = container.find_all('a')
                    if links: 
                        practice = ", ".join([l.get_text(strip=True) for l in links[:3]])
                        break

            # Office
            office = "Unknown"
            o_headers = soup.find_all(string=re.compile(r'Office|Location', re.I))
            for oh in o_headers:
                parent = oh.find_parent()
                if parent:
                    # Look for nearby text
                    txt = parent.get_text(strip=True)
                    # Simple clean
                    office = txt.replace('Office', '').replace('Location', '').strip()[:50]
                    break

            return {"name": name, "title": title, "practice": practice, "office": office, "url": url}
        except: return None

if __name__ == "__main__":
    root = tk.Tk()
    app = FindAttorneyApp(root)
    root.mainloop()
