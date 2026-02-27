"""
Quick enrichment test: enrich 3 real Kirkland profile URLs via MultiModeExtractor
(simulating what the pipeline does with force_playwright=True)
"""
from multi_mode_extractor import MultiModeExtractor

URLS = [
    "https://www.kirkland.com/lawyers/a/abate-anthony",
    "https://www.kirkland.com/lawyers/a/abate-james",
    "https://www.kirkland.com/lawyers/a/abbassi-rajab",
]

extractor = MultiModeExtractor(enable_playwright=True)

for url in URLS:
    print(f"\nExtracting: {url}")
    profile = extractor.extract_profile("Kirkland & Ellis", url, force_playwright=True)
    print(f"  Status:      {profile.extraction_status}")
    print(f"  Name:        {profile.full_name!r}")
    print(f"  Title:       {profile.title!r}")
    print(f"  Dept:        {profile.department!r}")
    print(f"  Offices:     {profile.offices}")
    print(f"  Practices:   {profile.practice_areas}")
    print(f"  Bar:         {profile.bar_admissions}")
    print(f"  Education:   {[(e.school, e.degree, e.year) for e in (profile.education or [])]}")
    print(f"  Missing:     {profile.missing_fields}")
