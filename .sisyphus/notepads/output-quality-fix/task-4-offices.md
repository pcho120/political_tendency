# Task 4: Offices Field Fix — Summary (2026-03-24)

## Diagnosis
- Dataset: `outputs/attorneys_2026-03-24T17-48-34.jsonl` (1591 profiles)
- Empty offices: 1244 (78%)
- Root cause distribution:
  - NOT_FOUND: 1048 (84%) — primary cause
  - VALIDATION_REJECTED: 167 (13%) — secondary cause
  - no_diagnostic: 29 (2%)

## Fix Applied (commit c2e5652)

### enrichment.py
1. Added 4 CSS class selectors: `vcard-office`, `location-name`, `office-link`, `office`
2. Added MoFo `profile-hero__details--title-location` span parser
3. Added generic office-href fallback using `/offices?/|/locations?/` regex pattern
   - Covers: Gibson Dunn, Dechert, Troutman, Cravath, Sidley, Mayer Brown, Ogletree, Littler, Husch Blackwell
   - Filter: token-level class matching (not substring) to avoid "navy" matching "nav"

### validators.py
- Expanded `_US_MAJOR_LAW_CITIES` with ~25 missing cities:
  Stamford, White Plains, Princeton, Bridgeport, New Haven, Morristown, Parsippany,
  Short Hills, Florham Park, Roseland, Edison, Fort Lauderdale, West Palm Beach,
  Boca Raton, Tallahassee, Lexington, Greenville, Chattanooga, Virginia Beach,
  Tysons, Tysons Corner, McLean, Reston, Madison, Fort Worth, Baton Rouge, Boise,
  Colorado Springs, Orange County

## Result
- Test: Gibson Dunn + Skadden 20 profiles
- Fill rate: 85% (17/20) vs 22% baseline
- 3 failures = non-US offices (Abu Dhabi, etc.) — correctly rejected by US-only validator
