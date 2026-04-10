#!/usr/bin/env python3
"""
Import manually scraped adopted local plans from Excel into the database.

This script:
1. Reads adopted plans from data/manually_scraped_adopted_plans.xlsx
2. Downloads the PDF from the document-url
3. Calculates SHA256 hash (endpoint)
4. Extracts housing numbers from the PDF
5. Updates/creates the source/ JSON file
6. Creates the local-plan/ JSON file
7. Generates the HTML page

Usage:
  # Test with one LPA code
  python bin/import-manually-scraped-plans.py --test CMD

  # Import all with full URLs
  python bin/import-manually-scraped-plans.py --all

  # Import specific LPA codes
  python bin/import-manually-scraped-plans.py --lpas CMD,COT,CRW
"""

import argparse
import json
import logging
import hashlib
import sys
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from urllib.parse import urlparse
import urllib.request
import subprocess

import pandas as pd

# Import the housing extractor - load as module
import importlib.util
spec = importlib.util.spec_from_file_location("local_plan_extractor", Path(__file__).parent / "local-plan-extractor.py")
local_plan_extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_plan_extractor)
LocalPlanExtractor = local_plan_extractor.LocalPlanHousingExtractor

# Import organisation matcher for geographic metadata
from organisation_matcher import OrganisationMatcher

# Import date parser for normalizing adoption dates
sys.path.insert(0, str(Path(__file__).parent / 'github-review-issues'))
from date_parser import parse_date

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ManualPlanImporter:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        if verbose:
            logger.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)
        self.source_dir = Path('source')
        self.local_plan_dir = Path('local-plan')
        self.collection_dir = Path('collection/document')
        self.collection_dir.mkdir(parents=True, exist_ok=True)

        # Initialize extractor with API key from environment
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self.extractor = LocalPlanExtractor(api_key)

        # Initialize organisation matcher for geographic metadata
        org_csv = Path(__file__).parent.parent / 'var' / 'cache' / 'organisation.csv'
        self.org_matcher = OrganisationMatcher(str(org_csv))

    def load_local_document(self, endpoint: str) -> Optional[Tuple[str, bytes]]:
        """Load a document from local collection by endpoint hash."""
        try:
            pdf_path = self.collection_dir / f"{endpoint}.pdf"
            if pdf_path.exists():
                content = pdf_path.read_bytes()
                if self.verbose:
                    logger.info(f"  Using local file: {pdf_path.name}")
                return f"{endpoint}.pdf", content
            else:
                logger.error(f"  Local file not found: {pdf_path}")
                return None
        except Exception as e:
            logger.error(f"  Failed to load local document {endpoint}: {e}")
            return None

    def download_document(self, url: str) -> Optional[Tuple[str, bytes]]:
        """Download a document from URL and return (filename, content)."""
        try:
            if self.verbose:
                logger.info(f"  Downloading: {url[:80]}...")

            # Download with timeout
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read()

            # Extract filename from URL
            parsed = urlparse(url)
            filename = parsed.path.split('/')[-1]
            if not filename:
                filename = 'document.pdf'

            return filename, content
        except Exception as e:
            logger.error(f"  Failed to download {url}: {e}")
            return None

    def calculate_endpoint(self, content: bytes) -> str:
        """Calculate SHA256 hash of content."""
        return hashlib.sha256(content).hexdigest()

    def extract_housing_numbers(self, pdf_path: Path, org_code: str, plan_name: str) -> Optional[Dict]:
        """Extract housing numbers from PDF using Claude."""
        try:
            # Use the existing LocalPlanExtractor
            housing_data = self.extractor.extract_housing_data(str(pdf_path), authority_name=org_code)

            if housing_data and 'error' not in housing_data:
                if self.verbose:
                    logger.info(f"  Extracted housing: {housing_data.get('required-housing', 'N/A')}")
                return housing_data
            else:
                if self.verbose:
                    logger.info(f"  No housing data extracted")
                return None
        except Exception as e:
            logger.error(f"  Error extracting housing numbers: {e}")
            return None

    def create_source_plan_entry(self, row: pd.Series, endpoint: str) -> Dict:
        """Create a plan entry for the source/ JSON file."""
        org_code = row['organisation']

        # Extract adoption and withdrawn dates from Excel if present
        # Parse dates to YYYY-MM-DD format
        adoption_date = None
        withdrawn_date = None

        if 'adoption-date' in row and pd.notna(row['adoption-date']):
            raw_date = str(row['adoption-date']).strip()
            adoption_date = parse_date(raw_date)
            if not adoption_date and self.verbose:
                logger.warning(f"  Could not parse adoption-date: {raw_date}")

        if 'withdrawn-date' in row and pd.notna(row['withdrawn-date']):
            raw_date = str(row['withdrawn-date']).strip()
            withdrawn_date = parse_date(raw_date)
            if not withdrawn_date and self.verbose:
                logger.warning(f"  Could not parse withdrawn-date: {raw_date}")

        return {
            "organisation": org_code,
            "organisation-name": row['organisation-label'],
            "reference": f"LP-{org_code.split(':')[1]}-{int(row['period-start-date'])}",
            "documentation-url": row['documentation-url'],
            "document-url": row['document-url'],
            "name": self._generate_plan_name(row),
            "status": "adopted",
            "year": int(row['period-start-date']),
            "period-start-date": int(row['period-start-date']),
            "period-end-date": int(row['period-end-date']),
            "adoption-date": adoption_date,
            "withdrawn-date": withdrawn_date,
            "documents": [
                {
                    "document-url": row['document-url'],
                    "documentation-url": row['documentation-url'],
                    "document-type": "local-plan",
                    "name": self._generate_document_name(row),
                    "reference": f"LP-{org_code.split(':')[1]}-{int(row['period-start-date'])}",
                    "document-status": "adopted",
                    "endpoint": endpoint
                }
            ]
        }

    def _generate_plan_name(self, row: pd.Series) -> str:
        """Generate a plan name from the row data."""
        start = int(row['period-start-date'])
        end = int(row['period-end-date'])
        return f"Local Plan {start}-{end}"

    def _generate_document_name(self, row: pd.Series) -> str:
        """Generate a document name from the row data."""
        start = int(row['period-start-date'])
        end = int(row['period-end-date'])
        return f"Adopted Local Plan {start}-{end}"

    def update_source_json(self, row: pd.Series, plan_entry: Dict) -> bool:
        """Update or create the source JSON file for the organisation."""
        org_code = row['organisation']
        source_file = self.source_dir / f"{org_code}.json"

        try:
            # Load existing data or create new
            if source_file.exists():
                with open(source_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    data = [data]
            else:
                data = []

            # Check if this plan already exists by period dates
            existing_entry = None
            for idx, p in enumerate(data):
                if (p.get('period-start-date') == plan_entry['period-start-date'] and
                    p.get('period-end-date') == plan_entry['period-end-date']):
                    existing_entry = (idx, p)
                    break

            if existing_entry:
                idx, existing = existing_entry
                # Update missing or empty fields in the existing entry
                updated = False

                # Update missing document-url
                if not existing.get('document-url'):
                    existing['document-url'] = plan_entry['document-url']
                    updated = True
                    if self.verbose:
                        logger.info(f"  Updated document-url")

                # Update missing period-start-date
                if not existing.get('period-start-date'):
                    existing['period-start-date'] = plan_entry['period-start-date']
                    updated = True
                    if self.verbose:
                        logger.info(f"  Updated period-start-date")

                # Update missing period-end-date
                if not existing.get('period-end-date'):
                    existing['period-end-date'] = plan_entry['period-end-date']
                    updated = True
                    if self.verbose:
                        logger.info(f"  Updated period-end-date")

                # Update documents array if existing is empty
                if not existing.get('documents'):
                    existing['documents'] = plan_entry['documents']
                    updated = True
                    if self.verbose:
                        logger.info(f"  Updated documents array")
                elif isinstance(existing['documents'], list) and len(existing['documents']) > 0:
                    # Check if we need to add or update the main local-plan document
                    new_endpoint = plan_entry['documents'][0]['endpoint']
                    new_doc_url = plan_entry['documents'][0]['document-url']

                    # Find if there's already a local-plan type document
                    local_plan_doc = None
                    for idx, doc in enumerate(existing['documents']):
                        if doc.get('document-type') == 'local-plan':
                            local_plan_doc = (idx, doc)
                            break

                    if local_plan_doc:
                        # Update existing local-plan document
                        idx, doc = local_plan_doc
                        if doc.get('endpoint') != new_endpoint:
                            doc['endpoint'] = new_endpoint
                            doc['document-url'] = new_doc_url
                            updated = True
                            if self.verbose:
                                logger.info(f"  Updated endpoint and document-url in local-plan document")
                    else:
                        # Add a new local-plan document entry
                        new_doc_entry = {
                            "document-url": new_doc_url,
                            "documentation-url": existing.get('documentation-url'),
                            "document-type": "local-plan",
                            "name": existing.get('name'),
                            "reference": existing.get('reference'),
                            "document-status": "adopted",
                            "endpoint": new_endpoint
                        }
                        existing['documents'].insert(0, new_doc_entry)
                        updated = True
                        if self.verbose:
                            logger.info(f"  Added new local-plan document entry")

                if updated:
                    with open(source_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    if self.verbose:
                        logger.info(f"  Updated {source_file.name} with missing fields")
                    return True
                else:
                    if self.verbose:
                        logger.info(f"  Plan already exists with all fields in {source_file.name}")
                    return False
            else:
                # New plan - add it
                data.append(plan_entry)
                with open(source_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                if self.verbose:
                    logger.info(f"  Added new plan to {source_file.name}")
                return True
        except Exception as e:
            logger.error(f"  Error updating source JSON: {e}")
            return False

    def create_local_plan_json(self, row: pd.Series, endpoint: str, housing_data: Optional[Dict]) -> bool:
        """Create the local-plan JSON file with geographic metadata."""
        local_plan_file = self.local_plan_dir / f"{endpoint}.json"

        try:
            housing_numbers = []
            if housing_data:
                # Ensure housing data is not nested - flatten if needed
                housing_entry = housing_data
                if 'housing-numbers' in housing_data and isinstance(housing_data['housing-numbers'], list):
                    # Extract from nested structure
                    if len(housing_data['housing-numbers']) > 0:
                        housing_entry = housing_data['housing-numbers'][0]
                housing_numbers = [housing_entry]

            org_name = row['organisation-label']

            # Get geographic metadata from organisation matcher
            lpa_code = self.org_matcher.get_local_planning_authority(org_name)
            matched_org_code = self.org_matcher.match(org_name)

            # Extract adoption date from Excel if present and parse to YYYY-MM-DD format
            adoption_date = None
            if 'adoption-date' in row and pd.notna(row['adoption-date']):
                raw_date = str(row['adoption-date']).strip()
                adoption_date = parse_date(raw_date)
                if not adoption_date and self.verbose:
                    logger.warning(f"  Could not parse adoption-date: {raw_date}")

            data = {
                "name": self._generate_plan_name(row),
                "organisation-name": org_name,
                "period-start-date": int(row['period-start-date']),
                "period-end-date": int(row['period-end-date']),
                "housing-numbers": housing_numbers,
                "confidence": "medium" if housing_data else "low",
                "authority": endpoint,
                "pdf_file": f"collection/document/{endpoint}.pdf",
                "pages_analysed": 0,
                "organisation": matched_org_code or "",
                "adoption-date": adoption_date
            }

            # Add geographic metadata if available
            if lpa_code:
                data["local-plan-boundary"] = lpa_code
                data["local-planning-authorities"] = [lpa_code]

            with open(local_plan_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            if self.verbose:
                logger.info(f"  Created {local_plan_file.name}")
                if lpa_code:
                    logger.info(f"  Added geographic metadata: {lpa_code}")
            return True
        except Exception as e:
            logger.error(f"  Error creating local-plan JSON: {e}")
            return False

    def generate_html(self) -> bool:
        """Generate HTML pages for all plans using make."""
        try:
            # Run make to render all pages
            result = subprocess.run(
                ['make'],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path(__file__).parent.parent
            )

            if result.returncode == 0:
                if self.verbose:
                    logger.info(f"  Generated HTML pages")
                return True
            else:
                logger.warning(f"  HTML generation had issues")
                if self.verbose:
                    logger.warning(f"  {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"  Error generating HTML: {e}")
            return False

    def import_plan(self, row: pd.Series, endpoint: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str], bool]:
        """Import a single plan. If endpoint is provided, use local file instead of downloading.

        Returns:
            Tuple of (success: bool, local_plan_file: str, source_file: str, was_skipped: bool)
        """
        org_label = row['organisation-label']
        logger.info(f"\nImporting: {org_label}")

        # Load document (use local if endpoint provided, otherwise download)
        if endpoint:
            doc_result = self.load_local_document(endpoint)
        else:
            doc_result = self.download_document(row['document-url'])

        if not doc_result:
            logger.error(f"Failed to import {org_label}")
            return False, None, None, False

        filename, content = doc_result

        # If endpoint not provided, calculate it from the downloaded content
        if not endpoint:
            endpoint = self.calculate_endpoint(content)

        # Check if already imported (skip if local-plan JSON already exists)
        local_plan_file = f"local-plan/{endpoint}.json"
        local_plan_path = self.local_plan_dir / f"{endpoint}.json"
        if local_plan_path.exists():
            org_code = row['organisation']
            source_file = f"source/{Path(org_code).name if ':' in org_code else org_code}.json"
            logger.info(f"  ⊘ Already imported, skipping")
            return True, local_plan_file, source_file, True

        # Save PDF to collection (check if it already exists first)
        pdf_path = self.collection_dir / f"{endpoint}.pdf"
        if pdf_path.exists():
            if self.verbose:
                logger.info(f"  PDF already exists: {pdf_path.name}")
        else:
            pdf_path.write_bytes(content)
            if self.verbose:
                logger.info(f"  Saved PDF: {pdf_path.name}")

        # Extract housing numbers
        housing_data = self.extract_housing_numbers(pdf_path, row['organisation'], self._generate_plan_name(row))

        # Create source JSON entry
        plan_entry = self.create_source_plan_entry(row, endpoint)

        # Update source JSON
        if not self.update_source_json(row, plan_entry):
            logger.warning(f"  Plan already exists in source")

        # Create local-plan JSON
        if not self.create_local_plan_json(row, endpoint, housing_data):
            logger.error(f"  Failed to create local-plan JSON")
            return False, None, None, False

        logger.info(f"✓ Successfully imported {org_label}")

        # Return success with file paths
        org_code = row['organisation']
        source_file = f"source/{Path(org_code).name if ':' in org_code else org_code}.json"

        return True, local_plan_file, source_file, False

    def import_from_excel(self, lpa_codes: Optional[List[str]] = None, test_mode: bool = False, endpoints: Optional[Dict[str, str]] = None, excel_file: str = 'data/manually_scraped_adopted_plans.xlsx'):
        """Import plans from the Excel file. endpoints is a dict mapping org codes to endpoint hashes."""
        xlsx_file = Path(excel_file)

        if not xlsx_file.exists():
            logger.error(f"Excel file not found: {xlsx_file}")
            return

        # Read Excel
        df = pd.read_excel(xlsx_file, sheet_name='manual-search')

        # Filter to adopted plans with full URLs
        adopted = df[df['type'].str.lower() == 'adopted'].copy()
        if self.verbose:
            logger.debug(f"Adopted plans: {len(adopted)}")

        has_urls = adopted[adopted['document-url'].astype(str).str.startswith('http')].copy()
        if self.verbose:
            logger.debug(f"With full URLs: {len(has_urls)}")

        # Further filter by LPA codes if specified
        if lpa_codes:
            # Expand LPA codes to include full form (local-authority:CODE)
            expanded_codes = []
            for code in lpa_codes:
                if ':' not in code:
                    # Add both local-authority and development-corporation forms
                    expanded_codes.append(f"local-authority:{code}")
                    expanded_codes.append(f"development-corporation:{code}")
                    expanded_codes.append(f"national-park-authority:{code}")
                else:
                    expanded_codes.append(code)

            if self.verbose:
                logger.debug(f"Filtering by LPA codes: {expanded_codes}")
            has_urls = has_urls[has_urls['organisation'].isin(expanded_codes)].copy()
            if self.verbose:
                logger.debug(f"After LPA filter: {len(has_urls)}")

        # In test mode, only import first plan
        if test_mode:
            has_urls = has_urls.head(1)
            if self.verbose:
                logger.debug(f"Test mode - first plan only: {len(has_urls)}")

        total = len(has_urls)
        logger.info(f"Found {total} plans to import")

        if total == 0:
            logger.warning("No plans matched the criteria")
            return

        success_count = 0
        skipped_count = 0
        github_comments = []  # Store GitHub comment templates

        for idx, (i, row) in enumerate(has_urls.iterrows(), 1):
            logger.info(f"\n[{idx}/{total}] Processing...")
            try:
                # Check if we have a pre-calculated endpoint for this LPA
                endpoint = None
                if endpoints:
                    org_code = row['organisation'].split(':')[-1]
                    endpoint = endpoints.get(org_code)

                success, local_plan_file, source_file, was_skipped = self.import_plan(row, endpoint=endpoint)
                if success:
                    if was_skipped:
                        skipped_count += 1
                        logger.info(f"  Already imported: {local_plan_file}")
                    else:
                        success_count += 1

                        # Collect GitHub comment template if issue number is available
                        if 'github_issue' in row and pd.notna(row['github_issue']):
                            issue_num = int(row['github_issue'])
                            comment = f"""Added to codebase.
- Local plan file: {local_plan_file}
- Source file: {source_file}"""
                            github_comments.append((issue_num, comment))

            except Exception as e:
                logger.error(f"Error importing plan: {e}")

        logger.info(f"\n✓ Import complete: {success_count}/{total} plans imported, {skipped_count} skipped (already imported)")

        # Generate HTML for all pages if any plans were imported
        if success_count > 0:
            logger.info("\nGenerating HTML pages...")
            self.generate_html()

        # Print GitHub comments for manual closure
        if github_comments:
            logger.info("\n" + "=" * 80)
            logger.info("GITHUB ISSUE CLOSURE COMMENTS")
            logger.info("=" * 80)
            for issue_num, comment in github_comments:
                logger.info(f"\nIssue #{issue_num}:")
                logger.info("-" * 40)
                logger.info(comment)
                logger.info("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description='Import manually scraped adopted local plans',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with first plan with full URL
  python bin/import-manually-scraped-plans.py --test

  # Test with specific LPA
  python bin/import-manually-scraped-plans.py --test --lpas CMD

  # Import all plans with full URLs
  python bin/import-manually-scraped-plans.py --all

  # Import specific LPAs
  python bin/import-manually-scraped-plans.py --lpas CMD,COT,CRW
"""
    )

    parser.add_argument('--test', action='store_true', help='Test mode: import first plan only')
    parser.add_argument('--all', action='store_true', help='Import all plans (default if no --test or --lpas)')
    parser.add_argument('--lpas', help='Comma-separated list of LPA codes to import')
    parser.add_argument('--endpoints', help='Comma-separated list of CODE:HASH pairs for pre-downloaded files (e.g., GRY:1404d98e9bd6c04599fd3779baf4b7771ec515a2dd9f6560233e99b72699a374)')
    parser.add_argument('--excel-file', default='data/manually_scraped_adopted_plans.xlsx', help='Path to Excel file to import (default: data/manually_scraped_adopted_plans.xlsx)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Validate that at least one mode is specified
    if not args.test and not args.all and not args.lpas:
        parser.error('Specify --test, --all, or --lpas')

    importer = ManualPlanImporter(verbose=args.verbose)

    lpa_codes = None
    if args.lpas:
        lpa_codes = [code.strip() for code in args.lpas.split(',')]

    endpoints = None
    if args.endpoints:
        endpoints = {}
        for pair in args.endpoints.split(','):
            code, hash_val = pair.strip().split(':')
            endpoints[code.strip()] = hash_val.strip()

    test_mode = args.test

    importer.import_from_excel(lpa_codes=lpa_codes, test_mode=test_mode, endpoints=endpoints, excel_file=args.excel_file)


if __name__ == '__main__':
    main()
