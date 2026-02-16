# Local Plan Extractor - Project Documentation

## Data Source & Legal Basis

**Ministry of Housing, Communities & Local Government is listed as an alternative source when we have collected the information from public sources.**

This project extracts local plan data from publicly available sources. All data collected is:

- **Publicly available**: Local plans are published by local planning authorities as public documents
- **Non-destructive**: We read and extract information without modifying source systems
- **Legitimate use**: The data is used to seed [planning.data.gov.uk](https://planning.data.gov.uk), a public project to improve access to planning information
- **Compliant with public sector information reuse**: All extracted data is published under the [Open Government Licence 3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/), consistent with Crown copyright reuse principles

## Data Sources Overview

This project combines data from multiple authoritative sources to create comprehensive planning datasets. Here's a summary of all data sources:

| Dataset | Source | Type |
| --- | --- | --- |
| **Local Plans** | Individual LPA websites | Webscrapped |
| **Local Plan Timetables** | VLS + PINs + Prototype | Enriched/Merged |
| **Mineral Plans** | Planning Inspectorate data sources | Official Database |
| **Waste Plans** | Planning Inspectorate data sources | Official Database |
| **LPA Boundaries** | planning.data.gov.uk | Official Geography |
| **County/Unitary Boundaries** | ONS Geoportal | Official Geography |

### Detailed Source Descriptions

**Local Plans** (Webscrapped)

- **Source**: Individual local planning authority websites, using authority website list from [planning.data.gov.uk organisation dataset](https://datasette.planning.data.gov.uk/digital-land/organisation)
- **Format**: PDF documents downloaded and processed
- **Processing**: Claude AI extraction of housing numbers, dates, and policy information

**Local Plan Timetables** (Enriched from Government Sources)

- **Primary Source**: VLS (Very Large Spreadsheet) - Internal MHCLG tracking of local plan progress
- **Secondary Source**: Planning Inspectorate (PINs) official records of submitted and examined plans
- **Tertiary Source**: [planning.data.gov.uk Prototype Platform](https://local-plans.prototype.planning.data.gov.uk/local-plans) - MHCLG consolidated data
- **Processing**: Fuzzy matching to consolidate multiple sources and link to webscrapped plans
- **Licence**: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)

**Mineral & Waste Plans** (Official Databases)

- **Source**: Planning Inspectorate (PINs) official submitted plans database
- **Datasets Used**:
  - "All Submitted Plans" - authoritative registry of all submitted plans, including mineral and waste, with examination status and adoption dates
  - "LPA Other Plan Progress" - published from [local-plan-monitoring-progress](https://www.gov.uk/government/publications/local-plan-monitoring-progress)
- **Status**: Both datasets contain publicly known information
- **Licence**: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)

**Boundary & Reference Data** (Official Sources)

- **LPA Boundaries**: [planning.data.gov.uk Local Planning Authority dataset](https://www.planning.data.gov.uk/dataset/local-planning-authority)
  - Licence: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- **County & Unitary Authority Boundaries**: [ONS Geoportal - Counties and Unitary Authorities](https://geoportal.statistics.gov.uk/datasets/a0c842713f10448eba4fe3be3be440dd_0)
  - Licence: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- **Reference Datasets**: Downloaded from planning.data.gov.uk during build:
  - `organisation.csv` - authority metadata
  - `local-planning-authority.csv` - LPA codes and names
  - `local-planning-authority.geojson` - boundary geometries
  - `local-plan-document-type.csv` - document classification
  - Licence: [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- **Purpose**: Geography code enrichment (E-codes) and authority reconciliation

### Data Quality & Provenance Notes

- **Publicly Known Information**: Where internal government datasets (VLS, "All Submitted Plans") are used, they contain publicly known information compiled from official sources and consultations

- **Authoritative Sources**: All data can be traced back to official government sources (individual LPA websites, ONS, PINs, MHCLG)
- **Alternative Validation**: Ministry of Housing, Communities & Local Government data feeds serve as validation checks
- **Licensing**: Data from planning.data.gov.uk, ONS, PINs, and MHCLG sources are all published under [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- **Version Control**: All source data and transformations are tracked in Git for transparency and auditability

## Project Overview

This project automatically collects and structures local plan information from UK local planning authorities. Local plans are strategic documents that set out how an area will develop over a 15-20 year period. They include:

- Plan names and adoption dates
- Housing requirements and allocations
- Development locations and priorities
- Planning policy information

The project:

1. **Discovers** local plan documents from authority websites and open data sources
2. **Downloads** PDF documents for processing
3. **Extracts** structured data (housing numbers, dates, policy information) using Claude AI
4. **Consolidates** data into CSV datasets with enrichment from multiple sources
5. **Handles complexity** around joint local plans, missing authority metadata, and data inconsistencies

## Architecture Overview

### Data Flow

```text
Public Authority Websites
         ↓
   [Find Local Plans]
         ↓
   [Download PDFs]
         ↓
    [Extract Data]
    (Claude AI)
         ↓
[Consolidate & Enrich]
         ↓
  CSV Output Files
```

### Key Components

#### 1. **Source Data** (`source/` directory)

Contains JSON files mapping each local planning authority to their local plans:

- Authority metadata (organisation code, name, URLs)
- Document URLs pointing to plan documents
- Initial plan metadata (dates, status, adoption information)

Format:

```json
{
  "organisation": "local-authority:XXX",
  "organisation-name": "Authority Name",
  "name": "Local Plan Name",
  "period-start-date": 2020,
  "period-end-date": 2035,
  "documents": [
    {
      "name": "Main Plan Document",
      "document-url": "https://...",
      "documentation-url": "https://..."
    }
  ]
}
```

**Data Source**: Manually curated from authority websites and [planning.data.gov.uk](https://planning.data.gov.uk) existing data

#### 2. **PDF Extraction** (`bin/local-plan-extractor.py`)

Processes downloaded PDFs and extracts structured data using Claude AI.

**Why Claude AI?**

- **Accuracy**: Language models can understand complex planning documents with varied formatting
- **Cost-effective**: Avoids expensive OCR/ML infrastructure for specialised domain data
- **Flexible extraction**: Can adapt to different document structures without retraining
- **Speed**: Processes documents faster than manual review while maintaining quality

**Process**:

1. Extracts up to 32 pages from each PDF (configurable)
2. Sends to Claude with specific extraction prompts for housing numbers, policy keywords, dates
3. Returns structured JSON with confidence levels and page references
4. Includes fallback logic if extraction fails

**Output**: Individual JSON files per document containing:

- Housing numbers (required, allocated, committed, windfall, broad locations)
- Periods covered by the plan
- Organisation identifiers
- Page references for verification

**Cost Considerations**: Uses Claude for accuracy and cost-efficiency. The `--max-pages` flag and rate limiting controls are included to manage API costs during batch processing.

#### 3. **CSV Generation** (`bin/generate-csvs.py`)

Consolidates extracted data into three CSV outputs:

##### a) `local-plan.csv` - Main local plan records

Contains one row per unique local plan (draft and adopted). Includes:

- Reference (auto-generated from authority name + year)
- Name and period dates
- Organisations involved (single or joint)
- Local planning authority geography codes (E-codes)
- Adoption dates and status
- Metadata URLs

**Why separate CSVs?** The three-table structure mirrors the [digital-land specification](https://digital-land.github.io/specification/specification/local-plan/), allowing data consumers to query at different levels:

- High-level plan summaries
- Detailed document information
- Specific housing requirements

##### b) `local-plan-document.csv` - Individual documents

One row per document within a plan. Links documents to their parent plan and includes:

- Document type (main plan, supporting document, technical appendix, etc.)
- URLs for access
- Adoption/withdrawal dates inherited from parent plan

##### c) `local-plan-housing.csv` - Housing data

One row per organisation per plan. Extracted housing numbers:

- Required housing numbers
- Allocated housing
- Committed housing (permissions granted, development underway)
- Windfall allocations
- Broad location allocations

#### 4. **Local Plan Timetable Data** (`bin/generate-timetable-data/generate_local_plan_timetable.py`)

**Important**: This data is **NOT webscrapped**. Instead, it is enriched from authoritative government sources.

The local plan timetable dataset contains milestone dates for the local plan process:

- Plan publication dates
- Regulation 18 consultation periods (first, second, third, fourth consultation stages)
- Submission to Planning Inspectorate (Regulation 19/22)
- Examination hearing dates
- Adoption dates

**Data Sources**:

1. **VLS (Very Large Spreadsheet)**: MHCLG internal tracking of local plan progress from priority authorities
2. **PINs Data (Planning Inspectorate)**: Official records of submitted and examined plans from the Planning Inspectorate
3. **Prototype Data**: Existing data from [planning.data.gov.uk prototype platform](https://local-plans.prototype.planning.data.gov.uk/local-plans)

**Process**:

- Merges data from VLS, PINs, and prototype platforms
- Matches plans by fuzzy LPA name matching (handles naming variations)
- Consolidates multiple data sources where available
- Links timetable entries to webscrapped local plan records

**Output Files**:

- `local-plan-timetable.csv` - Complete timetable records linked to webscrapped plans
- `mismatched-local-plan-timetable-rows.csv` - Timetable entries that couldn't be matched to webscrapped plans (for manual review)

**Coverage**: ~38 priority authorities with detailed timetable data from MHCLG tracking. Coverage is expanding as more authorities provide timetable data to PINs.

#### 5. **Mineral and Waste Plans** (NEW DATASETS)

**Important**: These datasets are **newly created** and not webscrapped. They are created by solely from Planning Inspectorate data.

Mineral and waste plans are separate strategic planning documents (distinct from local plans) that cover:

**Mineral Plans**:

- Where minerals can be extracted (coal, aggregates, metallic minerals, etc.)
- Restoration requirements for quarries and extraction sites
- Supply and demand forecasts

**Waste Plans**:

- Waste management facility locations (landfill, incinerators, recycling centres)
- Waste treatment and recovery capacity
- Integration with national waste policy

These have never been systematically collected before. This project creates the first comprehensive national dataset of mineral and waste plans.

**Data Sources**:

1. **All Submitted Plans (Excel)**: Official internal Planning Inspectorate database of all submitted mineral and waste plans with status, adoption dates, and examination outcomes
2. **LPA Other Plan Progress (Excel)**: Published Planning Inspectorate tracking of mineral and waste plan progress, timetables, and current status
3. **Boundary Data**: ONS-derived boundary files for geography code enrichment (E-codes)

**Process**:

1. Load mineral and waste plan records from PINs "All Submitted Plans" database
2. Cross-reference with PINs "LPA Other Plan Progress" data for additional status and timetable information
3. Consolidate status, dates, and timetable information
4. Enrich with geography codes from boundary data
5. Generate parallel CSV structure to local plans:
   - One main plan record per mineral/waste plan
   - Associated timetable entries
   - Boundary geometry for mapping

**Output Files**:

- `mineral-plan.csv` - All mineral plans with adoption status, periods, organisations
- `mineral-plan-timetable.csv` - Milestone dates for mineral plan process
- `mineral-plan-boundary.csv` - Boundary geometries for mineral planning authorities (GIS-ready)

- `waste-plan.csv` - All waste plans with adoption status, periods, organisations
- `waste-plan-timetable.csv` - Milestone dates for waste plan process
- `waste-plan-boundary.csv` - Boundary geometries for waste planning authorities (GIS-ready)

**Data Quality Notes**:

- Data sourced entirely from Planning Inspectorate (PINs) datasets
- "All Submitted Plans" is the authoritative database of all submitted mineral and waste plans
- "LPA Other Plan Progress" provides current tracking of plan progress and timetables
- Some historic plans may have incomplete data (pre-2010 plans less complete)
- Adoption dates verified against PINs examination records
- Geographic coverage: All English local planning authorities (including combined and unitary authorities)

## Key Decision Points & Rationale

### 1. Joint Local Plans Handling

**The Problem**: Some local planning authorities create joint local plans shared between multiple councils (e.g., Greater Norwich Local Plan covers Norwich, Broadland, and South Norfolk). These appear in source data as multiple entries but represent a single plan.

**Solution**:

- `var/joint-local-plans.json` maps each authority to its joint plan partners
- During generation, we identify joint plans and create a single consolidated record
- All partner authorities link to the same plan reference
- Housing data is merged and deduplicated appropriately

**Why this approach?**

- Avoids data duplication (one plan, multiple entries)
- Maintains relationships between authorities
- Allows queries like "all housing in the Greater Norwich area" to work correctly
- Follows the digital-land specification pattern for joint planning data

### 2. Reference Generation

**The Problem**: Local plans need stable, consistent references for linking across datasets. Authority websites use inconsistent naming schemes.

**Solution**:

- Auto-generate from authority name + year: `pendle-local-plan-2019`
- For joint plans: use plan name: `greater-norwich-local-plan`
- Allow manual overrides in `REFERENCE_OVERRIDES` dictionary for special cases

**Why auto-generate?**

- Ensures consistency across the dataset (no human naming variations)
- Makes references discoverable (authority name is embedded in the reference)
- Allows overrides for edge cases without touching the generation logic
- Enables automated reconciliation with existing planning.data.gov.uk references

### 3. Geography Code Enrichment

**The Problem**: Local plans link to organisations (e.g., `local-authority:PEN`), but users often need geography codes (e.g., `E07000121`) for GIS systems.

**Solution**:

- Load geography codes from `dataset/local-plan-boundary.csv` (derived from ONS boundary data)
- Static mappings for national parks and development corporations
- Fall back to boundary data if extracted data doesn't have codes
- Store missing codes in a separate tracking file for manual review

**Why separate enrichment?**

- Source data doesn't always include geography codes
- Boundary data comes from official ONS releases (more authoritative than scraping)
- Maintains clean separation between extracted data and enriched data
- Allows for incremental improvements (new boundary data = automatic enrichment)

### 4. Manual Lookups for Missing Plans

**The Problem**: Not all local plans are easily discovered through automated scraping:

- Some authorities have outdated or complex websites
- Old plans may have been removed or archived
- Joint plan websites may have inconsistent structures
- Some authorities host plans on Sharepoint or other unconventional platforms

**Solution**:

- Fallback to manual discovery for problematic authorities
- `source/` directory can be populated manually using Council data
- Document URLs added manually where automated discovery fails
- Version control tracks who added what and when
- GitHub issues used to flag missing plans for community contribution

**Bot Detection Handling**:

- South Worcestershire Development Plan is protected by Sucuri bot detection
- Documented in [JOINT_LOCAL_PLANS.md](var/JOINT_LOCAL_PLANS.md)
- Must be maintained manually using `--exclude` flag
- Uses `bin/run-all-authorities.py --exclude MAV,WOC,WYC`

### 5. Data Sourcing Priority

When data is missing or uncertain:

1. **Authoritative sources first**: ONS boundary data, official planning.data.gov.uk feeds
2. **Direct from source**: Authority websites and official documents
3. **Derived from documents**: Extracted via Claude AI from PDFs
4. **Community knowledge**: GitHub issues and manual contributions
5. **Fallbacks**: Older data, documentation-only references

The Ministry of Housing, Communities & Local Government (MHCLG) data feeds serve as validation checks and alternative sources when direct authority data is unavailable.

### 6. Deduplication Strategy

**Housing Data Deduplication**:

- For joint plans, multiple extractions may produce duplicate housing entries
- Prefer entries with authority code suffixes (e.g., `plan-1-now`, `plan-1-bro`) over generic entries
- Group by (plan, authority) combination and keep only the most specific entry

**Plan Deduplication**:

- Joint plans appear in multiple source files (one per authority)
- Only add to output once; process documents and housing data for all occurrences
- Track processed references to avoid duplicate plan entries

### 7. Rate Limiting & Cost Management

**The Problem**: Batch processing hundreds of PDFs against Claude API can be expensive and hit rate limits.

**Solution**:

- Configurable delays: `--api-delay` between API calls, `--file-delay` between files
- `--max-pages` limits pages processed per PDF (default 32)
- Progress logging shows processed vs. failed counts
- Error handling continues processing on failures rather than stopping entirely

**Rationale**:

- Allows operators to balance speed vs. cost
- Prevents rate limit errors from stopping entire batch
- Failed PDFs still captured in output with error message for manual review
- Resumable processing (can re-run without reprocessing successes)

## Data Quality & Validation

### Confidence Levels

Claude extraction includes confidence levels:

- `high`: Clear housing numbers with explicit page references
- `medium`: Inferred from context or secondary sources
- `low`: Extracted but significant uncertainty

Users should prioritise high-confidence entries.

### Validation Workflow

1. **Automated validation**: Check for missing mandatory fields, validate date formats
2. **Sample review**: Manual spot-check of extracted housing numbers against source PDFs
3. **Cross-reference**: Compare with MHCLG data feeds and planning.data.gov.uk existing data
4. **Community feedback**: planning.data.gov.uk Check & Provide service allows authorities to validate and correct data

### Known Limitations

- **OCR-dependent**: Scanned PDFs with poor quality may have low accuracy
- **Format variation**: Different authorities use different structures; some data may be missed
- **Date uncertainty**: Plan periods sometimes unclear; uses best available evidence
- **Non-English plans**: Welsh language documents handled but with lower confidence
- **Historic plans**: Old/archived plans may be incomplete or sourced from secondary sources

## Usage Examples

### Extract Housing from Single PDF

```bash
python bin/local-plan-extractor.py ./collection/document/plan.pdf > local-plan/plan.json
```

### Batch Extract All PDFs

```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/ \
  --api-delay 3 \
  --max-pages 32
```

### Generate CSVs for Specific Authorities

```bash
python bin/generate-csvs.py --lpa PEN,BOT,SHO --output-dir ./dataset/
```

### Exclude Protected Sites

```bash
python bin/run-all-authorities.py --exclude MAV,WOC,WYC
```

## File Structure

```text
local-plan-extractor/
├── source/                          # JSON mappings of authority → local plans
│   ├── local-authority:PEN.json
│   └── ... (one file per authority)
├── collection/document/             # Downloaded PDF files
├── local-plan/                      # Extracted data (JSON per PDF)
├── dataset/                         # Generated CSV outputs
│   ├── local-plan.csv
│   ├── local-plan-document.csv
│   └── local-plan-housing.csv
├── var/
│   ├── joint-local-plans.json       # Joint plan mappings
│   └── cache/                       # Downloaded reference data
├── bin/
│   ├── local-plan-extractor.py      # Claude-based extraction
│   ├── generate-csvs.py             # CSV generation & consolidation
│   ├── find-local-plan.py           # Discovery helper
│   ├── download-documents.py        # PDF downloader
│   └── run-all-authorities.py       # Batch processor
└── docs/                            # Generated static HTML pages
```

## Troubleshooting Common Issues

### Missing Local Plans

1. Check if authority is in `var/joint-local-plans.json` (requires joint plan website)
2. Verify source file exists: `source/local-authority:XXX.json`
3. Check if authority is in exclusion list (South Worcestershire, etc.)
4. File GitHub issue for community contribution

### Extraction Confidence Issues

- **Low confidence housing numbers**: Reduce `--max-pages` to focus on early pages
- **Missing housing data**: Try different page ranges with manual extraction
- **OCR failures**: Check PDF quality; consider manual entry for critical data

### Geography Code Missing

1. Verify `dataset/local-plan-boundary.csv` exists
2. Check authority code format: `local-authority:XXX`
3. File issue if authority has no boundary data from ONS

### Rate Limiting

```bash
# Increase delays to slow down API calls
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/ \
  --api-delay 5 \
  --file-delay 10
```

## Contributing Improvements

### Adding a New Joint Local Plan

Edit `var/joint-local-plans.json`:

```json
{
  "local-authority:XXX": {
    "name": "Authority Name",
    "joint-plan-name": "Joint Plan Name",
    "joint-plan-authorities": ["local-authority:XXX", "local-authority:YYY"],
    "joint-plan-website": "https://...",
    "notes": "Additional notes"
  }
}
```

### Manually Adding a Missing Plan

1. Create `source/local-authority:XXX.json` with plan metadata
2. Download PDF to `collection/document/`
3. Extract: `python bin/local-plan-extractor.py collection/document/plan.pdf > local-plan/plan.json`
4. Regenerate: `python bin/generate-csvs.py`
5. Commit changes with clear commit message

### Correcting Extracted Data

1. Edit `local-plan/XXX.json` directly
2. Run `python bin/generate-csvs.py` to regenerate CSVs
3. Validate changes against source PDF
4. Commit with explanation of correction

## Maintenance & Updates

### Regular Updates Needed

- **Quarterly**: Refresh with new adoption dates and timetable updates
- **Annually**: Check for new authorities, withdrawn plans, updates to joint plan structures
- **As needed**: Fix extraction errors, update reference mappings

### Running Full Rebuild

```bash
make clean
make
```

This will:

1. Download latest reference data (ONS, planning.data.gov.uk feeds)
2. Validate authority metadata
3. Extract from all PDFs
4. Generate final CSVs
5. Generate static HTML documentation

## References & Related Standards

- [Digital Land Local Plan Specification](https://digital-land.github.io/specification/specification/local-plan/)
- [Digital Land Local Plan Timetable Specification](https://digital-land.github.io/specification/specification/local-plan-timetable/)
- [Planning Data Check & Provide Service](https://provide.planning.data.gov.uk/)
- [Open Government Licence 3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- [ONS Geography Codes](https://www.ons.gov.uk/methodology/geography)

## Questions?

- Technical issues: Check existing documentation in `var/*.md`
- Data quality concerns: Use planning.data.gov.uk Check & Provide service
- Missing plans: File GitHub issue with authority details
- Contribution ideas: Start a discussion in GitHub
