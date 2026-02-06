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
import random
import requests
import xml.etree.ElementTree as ET
import gzip
from urllib.parse import urljoin, urlparse
from openpyxl import load_workbook
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Some sites use WAF/bot protection; these additional headers help requests look more like a browser.
DEFAULT_EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

# Max threads for parallel scraping
MAX_WORKERS = 10 

# Common directory paths
DIRECTORY_PATHS = [
    "/people", "/attorneys", "/lawyers", "/professionals", "/our-people", 
    "/team", "/staff", "/directory", "/people-directory"
]

# Sitemap paths to check (many law firms use specialized sitemap URLs)
SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap/lawyers",
    "/sitemap/lawyers.xml", 
    "/sitemap/attorneys",
    "/sitemap/attorneys.xml",
    "/sitemap/people",
    "/sitemap/people.xml",
    "/sitemap/professionals",
    "/sitemap/professionals.xml",
    "/sitemap/team",
    "/sitemap/team.xml",
    "/sitemap_index.xml",
    "/sitemaps/sitemap.xml",
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
        self._thread_local = threading.local()

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
            # Fix: Close file handle issues by removing images
            if not self.current_file:
                self.log("ERROR: No file selected.")
                return

            wb = load_workbook(self.current_file)
            ws = wb.active

            if ws is None:
                self.log("ERROR: Failed to load active worksheet.")
                self.run_btn.config(state="normal")
                return
            
            # Remove images to prevent "I/O operation on closed file" error
            try:
                imgs = getattr(ws, "_images", None)
                if imgs is not None:
                    setattr(ws, "_images", [])
            except Exception:
                pass
            
            # openpyxl Worksheet supports 1-based row indexing
            first_row = list(ws.iter_rows(min_row=1, max_row=1))
            if not first_row or not first_row[0]:
                self.log("ERROR: Worksheet appears to be empty.")
                self.run_btn.config(state="normal")
                return

            headers = [cell.value for cell in first_row[0]]
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

            if "Failed Profiles" in wb.sheetnames:
                del wb["Failed Profiles"]
            failed_ws = wb.create_sheet("Failed Profiles")
            failed_ws.append(["Firm", "Profile URL"])

            total_processed = 0
            total_failed = 0

            for row in ws.iter_rows(min_row=2, values_only=False):
                if self.stop_event.is_set(): break
                
                firm_cell = row[firm_col_idx-1]
                url_cell = row[url_col_idx-1]
                firm_name = firm_cell.value
                base_url = url_cell.value
                
                if not base_url or not firm_name:
                    continue

                self.log(f"\nProcessing: {firm_name}...")

                # New session per firm (connection pooling + retries)
                session = self.build_session()

                # reduce concurrency on known WAF-heavy sites
                firm_domain = urlparse(str(base_url)).netloc.lower()
                workers = 3 if "kirkland.com" in firm_domain else MAX_WORKERS
                
                # --- PHASE 1: DISCOVERY ---
                all_urls = set()
                
                # A. Sitemap Strategy
                self.log("  [1/2] Checking Sitemap...")
                sitemap_urls = self.get_profile_urls_from_sitemap(base_url, session=session)
                if sitemap_urls:
                    self.log(f"    Found {len(sitemap_urls)} from sitemap.")
                    all_urls.update(sitemap_urls)
                
                # B. Crawl Strategy
                self.log("  [2/2] Crawling Directory...")
                crawl_urls = self.crawl_directory(base_url, session=session)
                if crawl_urls:
                    new_found = len(crawl_urls)
                    self.log(f"    Found {new_found} from crawling.")
                    all_urls.update(crawl_urls)
                
                self.log(f"  Total Unique Profiles: {len(all_urls)}")
                
                # --- PHASE 2: EXTRACTION ---
                self.log(f"  Extracting Data (Parallel x{workers})...")
                
                count = 0
                failed = 0
                failed_urls = []
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    # Do NOT share a Session across threads; scraper uses per-thread sessions.
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
                            else:
                                failed += 1
                                failed_urls.append(future_to_url[future])
                        except Exception:
                            failed += 1
                            failed_urls.append(future_to_url[future])
                 
                total_failed += failed
                self.log(f"  Finished {firm_name}: {count} attorneys added. Failed: {failed}")

                for u in failed_urls:
                    failed_ws.append([firm_name, u])

                wb.save(self.current_file)

            self.log(f"\nDONE! Total attorneys found: {total_processed}. Failed profiles: {total_failed}")
            messagebox.showinfo("Success", f"Extraction Complete!\nTotal attorneys: {total_processed}\nFailed profiles: {total_failed}")

        except Exception as e:
            self.log(f"CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.run_btn.config(state="normal")

    def build_session(self):
        """Create a requests.Session with retry/backoff."""
        s = requests.Session()
        s.headers.update(HEADERS)
        s.headers.update(DEFAULT_EXTRA_HEADERS)

        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=4,
            backoff_factor=0.8,
            status_forcelist=[403, 408, 425, 429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"],
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        return s

    def get_thread_session(self):
        s = getattr(self._thread_local, "session", None)
        if s is None:
            s = self.build_session()
            setattr(self._thread_local, "session", s)
        return s

    def is_blocked_page(self, html):
        if not html:
            return False
        t = html.lower()
        # Common WAF / block messages
        return (
            "you have been blocked" in t
            or "access denied" in t
            or "incapsula" in t
            or "request unsuccessful" in t
            or "pardon our interruption" in t
        )

    # --- SITEMAP LOGIC (Enhanced to check multiple paths) ---
    def get_profile_urls_from_sitemap(self, base_url, session=None):
        """Check multiple sitemap locations and collect all attorney URLs"""
        all_urls = set()
        try:
            domain = urlparse(base_url).netloc
            scheme = urlparse(base_url).scheme
            base = f"{scheme}://{domain}"
            sess = session or requests
            
            # Check all possible sitemap paths
            for path in SITEMAP_PATHS:
                target = f"{base}{path}"
                try:
                    resp = sess.head(target, timeout=10, allow_redirects=True)
                    if resp.status_code == 200:
                        self.log(f"    Sitemap found: {path}")
                        urls = self.parse_sitemap(target, session=sess)
                        if urls:
                            all_urls.update(urls)
                except Exception:
                    continue
            
            return list(all_urls)
        except Exception:
            return []

    def parse_sitemap(self, sitemap_url, depth=0, session=None):
        """Parse sitemap XML to extract attorney profile URLs"""
        if depth > 2: return []
        urls = set()
        try:
            sess = session or requests
            resp = sess.get(sitemap_url, timeout=30)
            if resp.status_code != 200: return []
            
            content = resp.content
            if sitemap_url.endswith(".gz"): content = gzip.decompress(content)
            
            root = ET.fromstring(content)
            is_index = "sitemapindex" in root.tag.lower()
            
            keywords = ["lawyer", "attorney", "people", "team", "bio", "profile", "professional"]
            
            for child in root:
                loc = None
                for sub in child:
                    if "loc" in sub.tag.lower():
                        loc = sub.text.strip() if sub.text else None
                        break
                if not loc: continue
                
                if is_index:
                    # Sitemap index - recursively parse child sitemaps
                    if any(kw in loc.lower() for kw in keywords):
                        urls.update(self.parse_sitemap(loc, depth+1, session=sess))
                else:
                    # Direct sitemap with URLs
                    path = urlparse(loc).path
                    path_parts = path.strip('/').split('/')
                    
                    # Heuristic: attorney profile URLs typically have 2+ path segments
                    # e.g. /lawyers/a/smith-john or /people/john-smith
                    if len(path_parts) >= 2:
                        # If sitemap URL contains lawyer/attorney keywords, take ALL URLs from it
                        if any(kw in sitemap_url.lower() for kw in ["lawyer", "attorney", "people", "team", "professional"]):
                            urls.add(loc)
                        # Otherwise, filter URLs that match keywords
                        elif any(kw in loc.lower() for kw in keywords):
                            urls.add(loc)
        except Exception as e:
            pass
        return list(urls)

    # --- CRAWLING LOGIC (New) ---
    def crawl_directory(self, base_url, session=None):
        """Finds the directory page and crawls all pages."""
        urls = set()
        sess = session or requests
        directory_url = self.find_directory_url(base_url, session=sess)
        
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
                resp = sess.get(current_url, timeout=20)
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

    def find_directory_url(self, base_url, session=None):
        """Guesses the /people or /attorneys URL"""
        sess = session or requests
        for path in DIRECTORY_PATHS:
            url = urljoin(base_url, path)
            try:
                resp = sess.head(url, timeout=10, allow_redirects=True)
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
    def scrape_profile(self, url, session=None):
        # (Same robust scraper as before)
        try:
            # per-thread session to avoid sharing connections across threads
            sess = self.get_thread_session()

            # small jitter to reduce WAF triggers
            time.sleep(random.uniform(0.05, 0.25))

            resp = sess.get(url, timeout=25)
            if resp.status_code != 200: return None
            if self.is_blocked_page(resp.text):
                return None
            soup = BeautifulSoup(resp.text, 'html.parser')
            text = soup.get_text()

            # Firm-specific fast path (Kirkland)
            try:
                if "kirkland.com" in urlparse(url).netloc:
                    name_el = soup.select_one('.profile-heading__name-label')
                    title_el = soup.select_one('.profile-heading__position')
                    practice_el = soup.select_one('.profile-heading__specialty')
                    office_el = soup.select_one('.profile-heading__location-link')
                    if name_el:
                        name = name_el.get_text(strip=True)
                        title = title_el.get_text(strip=True) if title_el else "Unknown"
                        practice = practice_el.get_text(strip=True) if practice_el else "Unknown"
                        office = office_el.get_text(strip=True) if office_el else "Unknown"
                        return {"name": name, "title": title, "practice": practice, "office": office, "url": url}
            except Exception:
                pass
            
            # Name
            name = "Unknown"
            h1 = soup.find('h1')
            if h1: name = h1.get_text(strip=True)
            
            # Title
            title = "Unknown"
            common_titles = ["Partner", "Associate", "Counsel", "Of Counsel", "Shareholder", "Principal"]
            # Look for specific classes first
            for tag in soup.find_all(['h2', 'h3', 'div', 'span', 'p']):
                classes = tag.get('class')
                if classes is None:
                    cls = ""
                elif isinstance(classes, str):
                    cls = classes
                else:
                    cls = " ".join([str(c) for c in classes])
                cls = cls.lower()
                if 'title' in cls or 'position' in cls:
                    t = tag.get_text(strip=True)
                    if t and t != name and len(t) < 50:
                        title = t; break
            
            if title == "Unknown":
                for t in common_titles:
                    if t in text[:3000]: title = t; break

            # Practice
            practice = "Unknown"
            for ph in soup.find_all(['h2', 'h3', 'strong']):
                if not re.search(r'Practice|Service', ph.get_text(" ", strip=True), re.I):
                    continue
                container = ph.find_parent('div')
                if container:
                    links = container.find_all('a')
                    if links: 
                        practice = ", ".join([l.get_text(strip=True) for l in links[:3]])
                        break

            # Office
            office = "Unknown"
            for parent in soup.find_all(['p', 'div', 'span', 'li', 'strong']):
                txt = parent.get_text(" ", strip=True)
                if not txt:
                    continue
                if re.search(r'\b(Office|Location)\b', txt, re.I):
                    office = re.sub(r'(?i)\b(Office|Location)\b', '', txt).strip()[:50]
                    if office:
                        break

            return {"name": name, "title": title, "practice": practice, "office": office, "url": url}
        except: return None

if __name__ == "__main__":
    root = tk.Tk()
    app = FindAttorneyApp(root)
    root.mainloop()
