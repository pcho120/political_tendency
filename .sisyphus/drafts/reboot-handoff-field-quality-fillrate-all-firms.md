# Reboot Handoff: Field Quality / Fill-Rate Across All Firms

## Current Goal
- Create a decision-complete repo-wide work plan to improve **accuracy + fill-rate** for:
  - title
  - offices
  - department
  - practice areas
  - industries

## User Decisions Already Made
- No firm prioritization: **all firms are in scope**.
- Plan scope: **both extraction paths** must be covered.
  - Main path: `run_pipeline.py` / `enrichment.py`
  - Alternate path: `find_attorney.py` architecture
- Verification strategy: **tests + sampled runs**.
- Quality policy: **Balanced**.

## Research Already Completed

### Main architectural findings
- `run_pipeline.py -> enrichment.ProfileEnricher.enrich()` is the main current pipeline path.
- Main extraction cascade already exists in `enrichment.py`:
  - CSS-class/site-specific extraction
  - JSON-LD
  - embedded state
  - Playwright-captured JSON
  - heading-based section parsing
  - proximity fallback
- `find_attorney.py` is a separate architecture and cannot be treated as identical to the main pipeline.

### Cross-field leverage points
- `parser_sections.py` is a major cross-firm leverage point because section normalization couples:
  - title
  - offices
  - department
  - practice areas
  - industries
- `validators.py` is another major leverage point because it controls per-field rejection reasons and sentinel behavior.
- `field_enricher.py` and `field_merger.py` already contain provenance/confidence primitives that should be used in the plan.

### Measurement / verification findings
- Non-empty rate is **not sufficient**.
- Plan metrics must distinguish:
  - correct
  - contaminated / wrong
  - truly missing
- Existing outputs and diagnostics are sufficient to support stronger measurement:
  - `*_reason`
  - `section_keys_found`
  - `enrichment_log`
  - `field_sources`
  - JSONL outputs with `missing_fields` and `extraction_status`

## Planning Risks Already Identified
- Cross-field coupling: improving one field can regress another.
- Structure-type variability: acceptance criteria must be structure-aware.
- Overfitting risk: cannot optimize only against a few firms.
- Sentinel distortion: values like `no industry field` must not be counted as successful extraction.
- Dual-architecture scope creep: `run_pipeline.py` and `find_attorney.py` need separate but coordinated acceptance criteria.

## Working Draft
- Existing draft: `.sisyphus/drafts/field-quality-fillrate-all-firms.md`

## Exact Files Most Relevant For Plan
- `run_pipeline.py`
- `enrichment.py`
- `parser_sections.py`
- `validators.py`
- `attorney_extractor.py`
- `field_enricher.py`
- `field_merger.py`
- `find_attorney.py`
- `profile_quality_gate.py`
- `source_validator.py`
- `site_structures.json`

## Last Known State Before Reboot
- Research/background work is complete.
- User preference questions are answered.
- Draft is updated with confirmed scope and decisions.
- Next step was: **run Metis gap review, then generate final plan**.
- A Metis call was attempted but interrupted/aborted before completion.

## Immediate Next Step After Reboot
1. Re-run **Metis** review for this planning session.
2. Generate final plan to:
   - `.sisyphus/plans/field-quality-fillrate-all-firms.md`
3. Self-review for:
   - acceptance criteria completeness
   - cross-path coverage (`run_pipeline.py` + `find_attorney.py`)
   - benchmark / structure-aware verification
   - anti-overfitting guardrails

## What The Final Plan Must Include
- One single plan, not split by phases/documents.
- Baseline measurement tasks.
- Shared parsing/normalization tasks.
- Validator/provenance/confidence tasks.
- Separate path-specific tasks for:
  - main pipeline
  - alternate pipeline
- Cross-path no-regression verification.
- Final verification wave with 4 reviewers.
