#!/usr/bin/env python3
"""
Helper script to retrospectively add adoption-date and withdrawn-date to source JSON files.

This script:
1. Scans source/ JSON files
2. Attempts to extract adoption/withdrawn dates from local-plan/ extracted data
3. Allows manual entry for dates not found automatically
4. Updates source JSON files with the extracted dates

Usage:
    python bin/add-adoption-dates.py                    # Interactive mode, process all LPAs
    python bin/add-adoption-dates.py --lpa ARU,BAB     # Process specific LPAs
    python bin/add-adoption-dates.py --auto-only        # Only update from extracted data (no manual entry)
"""

import json
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class AdoptionDateHelper:
    def __init__(self, source_dir: str = "source", local_plan_dir: str = "local-plan"):
        """Initialize the adoption date helper."""
        self.source_dir = Path(source_dir)
        self.local_plan_dir = Path(local_plan_dir)
        self.extracted_data = {}
        self._load_extracted_data()

    def _load_extracted_data(self):
        """Load all extracted local plan data from local-plan/ directory."""
        if not self.local_plan_dir.exists():
            logger.debug(f"Local plan directory not found: {self.local_plan_dir}")
            return

        json_files = list(self.local_plan_dir.glob("*.json"))
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Store by endpoint/filename for later lookup
                    self.extracted_data[json_file.stem] = data
            except Exception as e:
                logger.debug(f"Failed to load {json_file.name}: {e}")

        if self.extracted_data:
            logger.info(f"Loaded {len(self.extracted_data)} extracted local plan files")

    def _extract_dates_from_text(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract adoption and withdrawn dates from text."""
        adoption_date = None
        withdrawn_date = None

        if not text:
            return adoption_date, withdrawn_date

        # Look for adoption date patterns
        # Common patterns: "adopted on [date]", "adoption date: [date]", "adopted: [date]"
        adoption_patterns = [
            r'adopted\s+on\s+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'adoption\s+date[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'adopted[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'date\s+of\s+adoption[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
        ]

        for pattern in adoption_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                adoption_date = self._normalize_date(match.group(1))
                if adoption_date:
                    break

        # Look for withdrawal date patterns
        withdrawal_patterns = [
            r'withdrawn\s+on\s+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'withdrawal\s+date[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'withdrawn[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'date\s+of\s+withdrawal[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
        ]

        for pattern in withdrawal_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                withdrawn_date = self._normalize_date(match.group(1))
                if withdrawn_date:
                    break

        return adoption_date, withdrawn_date

    def _normalize_date(self, date_str: str) -> Optional[str]:
        """Convert various date formats to ISO format YYYY-MM-DD."""
        if not date_str:
            return None

        # Try different date formats
        formats = [
            '%d/%m/%Y',
            '%d-%m-%Y',
            '%d.%m.%Y',
            '%d %B %Y',
            '%d %b %Y',
            '%B %d, %Y',
            '%b %d, %Y',
            '%Y-%m-%d',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        return None

    def _get_dates_from_extracted_data(self, endpoint: str) -> Tuple[Optional[str], Optional[str]]:
        """Try to extract dates from already-extracted local plan data."""
        if endpoint not in self.extracted_data:
            return None, None

        data = self.extracted_data[endpoint]

        # Look for dates in the extracted data structure
        adoption_date = data.get('adoption-date')
        withdrawn_date = data.get('withdrawn-date')

        # Also check in summary or other fields
        if not adoption_date and 'summary' in data:
            adoption_date, withdrawn_date = self._extract_dates_from_text(data.get('summary', ''))

        return adoption_date, withdrawn_date

    def process_source_files(self, lpa_codes: Optional[list] = None, auto_only: bool = False):
        """Process source JSON files and add adoption/withdrawn dates."""
        if not self.source_dir.exists():
            logger.error(f"Source directory not found: {self.source_dir}")
            return

        json_files = sorted(self.source_dir.glob("*.json"))

        if lpa_codes:
            # Filter to requested LPAs
            lpa_codes_set = set(lpa_codes)
            json_files = [
                f for f in json_files
                if any(code in f.stem for code in lpa_codes_set)
            ]
            logger.info(f"Processing {len(json_files)} files for LPAs: {', '.join(lpa_codes)}")
        else:
            logger.info(f"Processing {len(json_files)} source files")

        updated_count = 0
        skipped_count = 0

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Handle both array and single object formats
                plans = data if isinstance(data, list) else [data]

                file_updated = False
                for plan in plans:
                    if self._process_plan(plan, auto_only):
                        file_updated = True

                if file_updated:
                    # Write updated file
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    logger.info(f"✓ Updated {json_file.name}")
                    updated_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                logger.error(f"Failed to process {json_file.name}: {e}")
                skipped_count += 1

        logger.info(f"\nSummary: {updated_count} files updated, {skipped_count} skipped")

    def _process_plan(self, plan: Dict, auto_only: bool = False) -> bool:
        """Process a single plan entry and add adoption/withdrawn dates if missing."""
        # Skip if already has adoption date
        if 'adoption-date' in plan:
            logger.debug(f"Plan {plan.get('name', 'Unknown')} already has adoption-date")
            return False

        org_name = plan.get('organisation-name', 'Unknown')
        plan_name = plan.get('name', 'Unknown')

        # Try to find dates from extracted data
        adoption_date = None
        withdrawn_date = None

        # Check documents for endpoints to extracted data
        documents = plan.get('documents', [])
        for doc in documents:
            endpoint = doc.get('endpoint')
            if endpoint:
                adoption_date, withdrawn_date = self._get_dates_from_extracted_data(endpoint)
                if adoption_date or withdrawn_date:
                    break

        # If not found in extracted data and not auto_only, prompt user
        if not adoption_date and not auto_only:
            adoption_date = self._prompt_for_date(
                f"{org_name} - {plan_name}",
                "adoption"
            )

        if not withdrawn_date and not auto_only and plan.get('status') == 'withdrawn':
            withdrawn_date = self._prompt_for_date(
                f"{org_name} - {plan_name}",
                "withdrawn"
            )

        # Add dates to plan if we found them
        if adoption_date:
            plan['adoption-date'] = adoption_date
            return True

        if withdrawn_date:
            plan['withdrawn-date'] = withdrawn_date
            return True

        return False

    def _prompt_for_date(self, plan_label: str, date_type: str) -> Optional[str]:
        """Prompt user to enter a date."""
        print(f"\n{plan_label}")
        while True:
            user_input = input(f"  Enter {date_type} date (YYYY-MM-DD or skip): ").strip()

            if user_input.lower() in ['skip', 's', 'n', 'no', '']:
                return None

            # Try to normalize the input
            normalized = self._normalize_date(user_input)
            if normalized:
                return normalized
            else:
                print("  Invalid date format. Please use YYYY-MM-DD or common formats like DD/MM/YYYY")


def main():
    parser = argparse.ArgumentParser(
        description='Add adoption and withdrawn dates to source JSON files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode - process all LPAs
  python bin/add-adoption-dates.py

  # Process specific LPAs with manual prompts
  python bin/add-adoption-dates.py --lpa ARU,BAB,BDG

  # Auto-populate only from extracted data (no manual prompts)
  python bin/add-adoption-dates.py --auto-only

  # Combine options
  python bin/add-adoption-dates.py --lpa ARU,BAB --auto-only
        """
    )

    parser.add_argument(
        '--lpa',
        help='Comma-separated list of LPA codes to process (e.g., "ARU,BAB"). If not specified, processes all.',
        default=None
    )

    parser.add_argument(
        '--source-dir',
        help='Path to source directory (default: source/)',
        default='source'
    )

    parser.add_argument(
        '--local-plan-dir',
        help='Path to local-plan directory (default: local-plan/)',
        default='local-plan'
    )

    parser.add_argument(
        '--auto-only',
        action='store_true',
        help='Only update from extracted data, no manual prompts'
    )

    args = parser.parse_args()

    # Parse LPA codes if provided
    lpa_codes = None
    if args.lpa:
        lpa_codes = [code.strip().upper() for code in args.lpa.split(',')]

    # Create helper and process files
    helper = AdoptionDateHelper(
        source_dir=args.source_dir,
        local_plan_dir=args.local_plan_dir
    )

    helper.process_source_files(lpa_codes=lpa_codes, auto_only=args.auto_only)
    logger.info("✓ Date addition complete")


if __name__ == '__main__':
    main()
