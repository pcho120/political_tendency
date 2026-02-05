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
