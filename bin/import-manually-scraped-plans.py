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
import re
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
            "adoption-date": None,
            "withdrawn-date": None,
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

            # Check if this plan already exists
            exists = any(
                p.get('period-start-date') == plan_entry['period-start-date'] and
                p.get('period-end-date') == plan_entry['period-end-date']
                for p in data
            )

            if not exists:
                data.append(plan_entry)
                with open(source_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                if self.verbose:
                    logger.info(f"  Updated {source_file.name}")
                return True
            else:
                if self.verbose:
                    logger.info(f"  Plan already exists in {source_file.name}")
                return False
        except Exception as e:
            logger.error(f"  Error updating source JSON: {e}")
            return False

    def create_local_plan_json(self, row: pd.Series, endpoint: str, housing_data: Optional[Dict]) -> bool:
        """Create the local-plan JSON file."""
        local_plan_file = self.local_plan_dir / f"{endpoint}.json"

        try:
            housing_numbers = []
            if housing_data:
                housing_numbers = [housing_data]

            data = {
                "name": self._generate_plan_name(row),
                "organisation-name": row['organisation-label'],
                "period-start-date": int(row['period-start-date']),
                "period-end-date": int(row['period-end-date']),
                "housing-numbers": housing_numbers,
                "confidence": "medium" if housing_data else "low",
                "authority": endpoint,
                "pdf_file": f"collection/document/{endpoint}.pdf",
                "pages_analysed": 0,
                "organisation": "",
                "adoption-date": None
            }

            with open(local_plan_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            if self.verbose:
                logger.info(f"  Created {local_plan_file.name}")
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

    def import_plan(self, row: pd.Series) -> bool:
        """Import a single plan."""
        org_label = row['organisation-label']
        logger.info(f"\nImporting: {org_label}")

        # Download document
        doc_result = self.download_document(row['document-url'])
        if not doc_result:
            logger.error(f"Failed to import {org_label}")
            return False

        filename, content = doc_result
        endpoint = self.calculate_endpoint(content)

        # Save PDF to collection
        pdf_path = self.collection_dir / f"{endpoint}.pdf"
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
            return False

        logger.info(f"✓ Successfully imported {org_label}")
        return True

    def import_from_excel(self, lpa_codes: Optional[List[str]] = None, test_mode: bool = False):
        """Import plans from the Excel file."""
        xlsx_file = Path('data/manually_scraped_adopted_plans.xlsx')

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
        for idx, (i, row) in enumerate(has_urls.iterrows(), 1):
            logger.info(f"\n[{idx}/{total}] Processing...")
            try:
                if self.import_plan(row):
                    success_count += 1
            except Exception as e:
                logger.error(f"Error importing plan: {e}")

        logger.info(f"\n✓ Import complete: {success_count}/{total} plans imported successfully")

        # Generate HTML for all pages if any plans were imported
        if success_count > 0:
            logger.info("\nGenerating HTML pages...")
            self.generate_html()


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
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Validate that at least one mode is specified
    if not args.test and not args.all and not args.lpas:
        parser.error('Specify --test, --all, or --lpas')

    importer = ManualPlanImporter(verbose=args.verbose)

    lpa_codes = None
    if args.lpas:
        lpa_codes = [code.strip() for code in args.lpas.split(',')]

    test_mode = args.test

    importer.import_from_excel(lpa_codes=lpa_codes, test_mode=test_mode)


if __name__ == '__main__':
    main()
