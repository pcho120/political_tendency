Political Tendency Research Project

📌 Project Overview

Political Tendency is a research-oriented data analysis project designed to analyze publicly available information in order to infer political tendencies of attorneys working at large firms.

The project leverages official company websites and publicly accessible social media content to conduct non-deterministic, analytical research.
It is intended strictly for academic, exploratory, and research purposes.

This project does not aim to label, judge, or definitively determine any individual’s political beliefs.

🧩 Project Pipeline
1. Official Website Collection

Input:
An Excel file containing a list of large companies (e.g., law firms)

Process:
Automatically search and identify each company’s official website URL

Output:
An updated Excel file with official website URLs added

### Official Website Discovery (firm_finder.py)

This step uses deterministic, cached search to identify the official website domain for each firm.

**Environment variables:**
- `GOOGLE_CSE_API_KEY` (preferred)
- `GOOGLE_CSE_CX`
- `SERPAPI_KEY` (optional fallback)

**Usage:**
```bash
python firm_finder.py "input.xlsx"
python firm_finder.py "input.xlsx" --refresh
python firm_finder.py "input.xlsx" --max-firms 50 --start-index 0
```

**Output columns:**
- Firm
- normalized_firm
- official_website_url
- discovery_method (cache | alias_match | google_cse | serpapi | fallback_guess)
- confidence_score (0.0-1.0)
- evidence
- notes

**Scoring summary:**
- +0.50 if brand tokens appear in title/snippet
- +0.20 if top result for “official website” query
- +0.15 if domain appears multiple times
- +0.10 if domain contains firm token
- -0.50 for directory/news domains (Wikipedia, Chambers, etc.)
- -0.30 for unrelated subdomains

Domains are verified via HTTPS checks and homepage evidence (title/meta/nav). Failures are written to `discovery_failures.xlsx` with attempted queries and candidate domains.

2. Attorney List Extraction

Input:
Official company website URLs

Process:
Extract publicly available information about attorneys, including:

Name

Title / Practice Area

(When available) profile page URL

Output:
A structured dataset of attorneys associated with each company

3. Political Tendency Analysis (Research-Oriented)

Input:
Publicly available Twitter(X) posts from identified attorneys

Process:

Text collection and preprocessing

Keyword, topic, and language-pattern analysis

Probabilistic or trend-based inference of political tendencies

Output:
Research-level analytical insights into political tendencies
(expressed as trends, probabilities, or clusters—not definitive labels)

🔍 Key Characteristics

✅ Uses only publicly accessible data

✅ Designed for automated and scalable research workflows

✅ Focuses on inference and analysis, not classification or judgment

❌ Not intended for surveillance, profiling, or decision-making about individuals

⚠️ Ethical & Legal Considerations

This project is intended solely for research and academic use.

Social media data collection must comply with:

Platform Terms of Service (ToS)

Applicable local and international regulations

All findings should be interpreted as analytical tendencies, not factual assertions.

Users are responsible for ensuring ethical use of the results.

🛠️ Example Tech Stack

Python

Pandas / OpenPyXL

Web Scraping (Requests, BeautifulSoup, Selenium, etc.)

Natural Language Processing (NLP)

Excel-based Input / Output

📄 Disclaimer

This project does not determine or verify an individual’s political beliefs.
All outputs represent research-based analytical interpretations derived from publicly available data and should not be treated as factual or authoritative conclusions.
