#!/usr/bin/env python3
"""
Extract adoption and withdrawn dates from local plan PDF documents and add to source JSONs.

This script:
1. Adds 'adoption-date' and 'withdrawn-date' fields to all source JSON files (if missing)
2. Extracts dates from:
   - PDF documents (adoption statements and inspector's reports)
   - Parses natural language dates like "was adopted on the 18th of July 2018"
3. Updates source JSON files with extracted dates

Usage:
    python bin/extract-adoption-dates.py                    # Process all source files
    python bin/extract-adoption-dates.py --lpa ARU,BAB     # Process specific LPAs
    python bin/extract-adoption-dates.py --verbose          # Show detailed extraction attempts
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


class DateExtractor:
    def __init__(self, source_dir: str = "source", verbose: bool = False):
        """Initialize the date extractor."""
        self.source_dir = Path(source_dir)
        self.verbose = verbose

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

    def _extract_text_from_pdf(self, url: str, endpoint: Optional[str] = None) -> Optional[str]:
        """Extract adoption/withdrawal date from a PDF document via Claude API.

        First checks for local PDF at collection/document/{endpoint}.pdf
        If not found, downloads from URL.
        Extracts first 20 pages and sends to Claude API.
        This keeps within the 100-page limit and focuses on adoption dates which
        typically appear in opening pages.
        """
        try:
            import os
            import io
            import json as json_module
            import urllib.request
            import urllib.error
            import base64
            from PyPDF2 import PdfReader, PdfWriter

            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                if self.verbose:
                    logger.debug(f"  ANTHROPIC_API_KEY not set, skipping PDF extraction")
                return None

            if self.verbose:
                logger.debug(f"  Extracting dates from PDF via Claude API: {url}")

            # Try to find local PDF first
            pdf_data = None
            local_pdf_path = None

            if endpoint:
                local_pdf_path = Path('collection/document') / f"{endpoint}.pdf"
                if local_pdf_path.exists():
                    if self.verbose:
                        logger.debug(f"  Using local PDF: {local_pdf_path}")
                    try:
                        with open(local_pdf_path, 'rb') as f:
                            pdf_data = f.read()
                    except Exception as e:
                        if self.verbose:
                            logger.debug(f"  Failed to read local PDF: {e}")

            # Fall back to downloading from URL if no local PDF
            if not pdf_data:
                try:
                    if self.verbose:
                        logger.debug(f"  Downloading PDF from: {url}")
                    with urllib.request.urlopen(url, timeout=30) as response:
                        pdf_data = response.read()
                except urllib.error.URLError as e:
                    if self.verbose:
                        logger.debug(f"  Failed to download PDF: {e}")
                    return None

            # Extract first 20 pages using PyPDF2
            try:
                pdf_reader = PdfReader(io.BytesIO(pdf_data))
                total_pages = len(pdf_reader.pages)
                pages_to_extract = min(20, total_pages)

                if self.verbose:
                    logger.debug(f"  PDF has {total_pages} pages, extracting first {pages_to_extract}")

                # Create a new PDF with first 20 pages
                pdf_writer = PdfWriter()
                for page_num in range(pages_to_extract):
                    pdf_writer.add_page(pdf_reader.pages[page_num])

                # Convert to bytes and encode as base64
                output = io.BytesIO()
                pdf_writer.write(output)
                output.seek(0)
                pdf_base64 = base64.standard_b64encode(output.read()).decode('utf-8')

            except Exception as e:
                if self.verbose:
                    logger.debug(f"  Failed to process PDF pages: {e}")
                return None

            # Call Claude API with base64-encoded PDF
            headers = {
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            }

            payload = {
                "model": "claude-opus-4-1-20250805",
                "max_tokens": 256,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": "Extract ONLY the adoption date and/or withdrawal date from this document. Look for phrases like 'adopted on', 'was adopted', 'adoption date', 'On [date] ... adopted', 'withdrawn on', etc. Return just the date part (e.g., '18 July 2018', '4 March 2025') or the relevant sentence with the date highlighted. If multiple dates, return each on a new line. If no dates found, return 'NO_DATES'."
                            }
                        ]
                    }
                ]
            }

            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=json_module.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json_module.loads(response.read().decode('utf-8'))
                extracted_text = result.get('content', [{}])[0].get('text', '')

                if self.verbose:
                    logger.debug(f"    Claude returned: {repr(extracted_text[:200])}")

                if extracted_text and extracted_text != 'NO_DATES':
                    return extracted_text
                return None

        except urllib.error.URLError as e:
            if self.verbose:
                logger.debug(f"  Failed to fetch from API: {e}")
            return None
        except Exception as e:
            if self.verbose:
                logger.debug(f"  Error extracting from PDF: {e}")
            return None

    def _extract_dates_from_text(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract adoption and withdrawn dates from natural language text."""
        adoption_date = None
        withdrawn_date = None

        if not text:
            return adoption_date, withdrawn_date

        text_lower = text.lower()

        # First, check if Claude returned just a date (e.g., "4 March 2025" or "14th December 2017")
        # Try to parse dates that appear in simple formats like "day month year" with optional ordinal suffix
        simple_date_pattern = r'^[\s\*]*(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})[\s\*]*$'
        match = re.search(simple_date_pattern, text_lower)
        if match and not adoption_date:
            day, month_str, year = match.groups()
            adoption_date = self._parse_natural_date(day, month_str, year)
            if adoption_date and self.verbose:
                logger.debug(f"  Found adoption date from simple format: {adoption_date}")
            return adoption_date, withdrawn_date

        # Patterns for natural language adoption dates
        adoption_patterns = [
            # "on 14th December 2017 Adur District Council adopted..." - date comes before "adopted" (with ordinal suffix)
            r'on\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})\s+.*?adopted',
            # "was adopted on the 18th of July 2018"
            r'adopted\s+on\s+the\s+(\d{1,2})(?:st|nd|rd|th)\s+of\s+(\w+)\s+(\d{4})',
            # "adopted on 18 July 2018"
            r'adopted\s+on\s+(\d{1,2})\s+(\w+)\s+(\d{4})',
            # "adopted 18 July 2018"
            r'adopted\s+(\d{1,2})\s+(\w+)\s+(\d{4})',
            # "adoption date: 18 July 2018"
            r'adoption\s+date[:\s]+(\d{1,2})\s+(\w+)\s+(\d{4})',
            # "adoption: 18th July 2018"
            r'adoption[:\s]+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(\w+)\s+(\d{4})',
            # "adopted February 2019" - month and year only
            r'adopted\s+(\w+)\s+(\d{4})',
            # "adoption date: February 2019" - month and year only
            r'adoption\s+date[:\s]+(\w+)\s+(\d{4})',
            # Numeric formats
            r'adopted\s+on\s+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'adoption\s+date[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
        ]

        for pattern in adoption_patterns:
            match = re.search(pattern, text_lower)
            if match:
                # Handle natural language patterns (day month year)
                if len(match.groups()) == 3:
                    day, month_str, year = match.groups()
                    adoption_date = self._parse_natural_date(day, month_str, year)
                # Handle month and year only patterns
                elif len(match.groups()) == 2:
                    month_str, year = match.groups()
                    adoption_date = self._parse_natural_date_month_year(month_str, year)
                # Handle numeric patterns
                elif len(match.groups()) == 1:
                    adoption_date = self._normalize_date(match.group(1))

                if adoption_date:
                    if self.verbose:
                        logger.debug(f"  Found adoption date: {adoption_date}")
                    break

        # Patterns for natural language withdrawal dates
        withdrawal_patterns = [
            # "on 14th December 2017 ... withdrawn" - date comes before "withdrawn" (with ordinal suffix)
            r'on\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})\s+.*?withdrawn',
            r'withdrawn\s+on\s+the\s+(\d{1,2})(?:st|nd|rd|th)\s+of\s+(\w+)\s+(\d{4})',
            r'withdrawn\s+on\s+(\d{1,2})\s+(\w+)\s+(\d{4})',
            r'withdrawn\s+(\d{1,2})\s+(\w+)\s+(\d{4})',
            r'withdrawal\s+date[:\s]+(\d{1,2})\s+(\w+)\s+(\d{4})',
            # "withdrawn February 2019" - month and year only
            r'withdrawn\s+(\w+)\s+(\d{4})',
            # "withdrawal date: February 2019" - month and year only
            r'withdrawal\s+date[:\s]+(\w+)\s+(\d{4})',
            r'withdrawn\s+on\s+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
            r'withdrawal\s+date[:\s]+(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})',
        ]

        for pattern in withdrawal_patterns:
            match = re.search(pattern, text_lower)
            if match:
                if len(match.groups()) == 3:
                    day, month_str, year = match.groups()
                    withdrawn_date = self._parse_natural_date(day, month_str, year)
                # Handle month and year only patterns
                elif len(match.groups()) == 2:
                    month_str, year = match.groups()
                    withdrawn_date = self._parse_natural_date_month_year(month_str, year)
                elif len(match.groups()) == 1:
                    withdrawn_date = self._normalize_date(match.group(1))

                if withdrawn_date:
                    if self.verbose:
                        logger.debug(f"  Found withdrawal date: {withdrawn_date}")
                    break

        return adoption_date, withdrawn_date

    def _parse_natural_date(self, day: str, month_str: str, year: str) -> Optional[str]:
        """Parse natural language date like 'the 18th of July 2018'."""
        months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }

        month_num = months.get(month_str.lower())
        if month_num:
            try:
                dt = datetime(int(year), month_num, int(day))
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                return None
        return None

    def _parse_natural_date_month_year(self, month_str: str, year: str) -> Optional[str]:
        """Parse natural language date with only month and year like 'February 2019', returns YYYY-MM format."""
        months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }

        month_num = months.get(month_str.lower())
        if month_num:
            try:
                return f"{int(year)}-{month_num:02d}"
            except ValueError:
                return None
        return None

    def extract_dates_from_plan(self, plan: Dict) -> Tuple[Optional[str], Optional[str]]:
        """Extract adoption and withdrawn dates from a plan entry by reading PDF documents."""
        adoption_date = None
        withdrawn_date = None
        plan_name = plan.get('name', 'Unknown')
        org_name = plan.get('organisation-name', 'Unknown')

        if self.verbose:
            logger.debug(f"Processing: {org_name} - {plan_name}")

        documents = plan.get('documents', [])

        # Priority order for document types to extract dates from
        # Adoption statements are checked first as they're specifically designed
        # to document adoption dates (e.g., "On 4 March 2025 the council adopted...")
        document_priority = [
            ('adoption-statement', 'adoption statement'),
            ('local-plan-adopted', 'adopted local plan'),
            ('local-plan', 'local plan'),
            ('inspectors-report', "inspector's report"),
            ('core-strategy', 'core strategy'),
            ('development-plan-document', 'development plan document'),
        ]

        for doc_type_filter, doc_type_name in document_priority:
            for doc in documents:
                doc_type = doc.get('document-type', '').lower()
                if doc_type_filter in doc_type:
                    doc_url = doc.get('document-url')
                    if doc_url:
                        if self.verbose:
                            logger.debug(f"  Checking {doc_type_name}: {doc.get('name', 'Unknown')}")

                        # Extract text from PDF (pass endpoint for local file lookup)
                        endpoint = doc.get('endpoint')
                        pdf_text = self._extract_text_from_pdf(doc_url, endpoint=endpoint)
                        if pdf_text:
                            adoption_date, withdrawn_date = self._extract_dates_from_text(pdf_text)
                            if adoption_date or withdrawn_date:
                                return adoption_date, withdrawn_date

        return adoption_date, withdrawn_date

    def _reorder_plan_dict(self, plan: Dict) -> Dict:
        """Reorder plan dictionary to place adoption/withdrawn dates between period-end-date and documents."""
        reordered = {}

        # Add keys in order, inserting adoption/withdrawn dates at the right position
        for key, value in plan.items():
            if key == 'documents':
                # Insert adoption-date and withdrawn-date before documents
                if 'adoption-date' in plan:
                    reordered['adoption-date'] = plan['adoption-date']
                if 'withdrawn-date' in plan:
                    reordered['withdrawn-date'] = plan['withdrawn-date']

            if key not in ['adoption-date', 'withdrawn-date']:
                reordered[key] = value

        # If there's no documents key, add the dates at the end
        if 'documents' not in plan:
            if 'adoption-date' in plan:
                reordered['adoption-date'] = plan['adoption-date']
            if 'withdrawn-date' in plan:
                reordered['withdrawn-date'] = plan['withdrawn-date']

        return reordered

    def process_all_source_files(self, lpa_codes: Optional[list] = None, start_from: Optional[str] = None):
        """Process all source JSON files and add/update adoption and withdrawn dates.

        Args:
            lpa_codes: Comma-separated list of LPA codes to process
            start_from: LPA code to start processing from (resume functionality)
        """
        if not self.source_dir.exists():
            logger.error(f"Source directory not found: {self.source_dir}")
            return

        json_files = sorted(self.source_dir.glob("local-authority:*.json"))

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

        # Handle start_from (resume functionality)
        start_index = 0
        if start_from:
            start_from_upper = start_from.upper()
            for idx, file in enumerate(json_files):
                if start_from_upper in file.stem:
                    start_index = idx
                    logger.info(f"Resuming from {file.name}")
                    break
            else:
                logger.warning(f"LPA code '{start_from}' not found in file list. Starting from beginning.")
                start_index = 0

        updated_count = 0
        total_updates = 0
        total_files = len(json_files)

        for file_index, json_file in enumerate(json_files[start_index:], start_index + 1):
            try:
                logger.info(f"[{file_index}/{total_files}] Processing {json_file.name}")

                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Handle both array and single object formats
                plans = data if isinstance(data, list) else [data]

                file_modified = False
                for plan in plans:
                    # Add fields if missing
                    if 'adoption-date' not in plan:
                        plan['adoption-date'] = None
                    if 'withdrawn-date' not in plan:
                        plan['withdrawn-date'] = None

                    # Try to extract dates
                    adoption_date, withdrawn_date = self.extract_dates_from_plan(plan)

                    # Update if extracted dates are better than current values
                    if adoption_date and not plan['adoption-date']:
                        plan['adoption-date'] = adoption_date
                        file_modified = True
                        total_updates += 1
                        logger.info(f"  Added adoption-date: {adoption_date}")

                    if withdrawn_date and not plan['withdrawn-date']:
                        plan['withdrawn-date'] = withdrawn_date
                        file_modified = True
                        total_updates += 1
                        logger.info(f"  Added withdrawn-date: {withdrawn_date}")

                    # Reorder the plan dict to place adoption/withdrawn dates in the right position
                    plans[plans.index(plan)] = self._reorder_plan_dict(plan)

                # Rewrite data with reordered plans
                if isinstance(data, list):
                    data = [self._reorder_plan_dict(plan) for plan in plans]
                else:
                    data = self._reorder_plan_dict(plans[0])

                if file_modified:
                    # Write updated file
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    logger.info(f"✓ Updated {json_file.name}")
                    updated_count += 1
                else:
                    # Still write to ensure fields exist even if null
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)

            except Exception as e:
                logger.error(f"Failed to process {json_file.name}: {e}")

        logger.info(f"\nSummary: {updated_count} files updated, {total_updates} dates extracted")


def main():
    parser = argparse.ArgumentParser(
        description='Extract adoption and withdrawn dates from local plan documents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all source files and extract dates
  python bin/extract-adoption-dates.py

  # Process specific LPAs
  python bin/extract-adoption-dates.py --lpa ARU,BAB,BDG

  # Resume processing from a specific LPA (resume from BRD onwards)
  python bin/extract-adoption-dates.py --start-from BRD

  # Show detailed extraction attempts
  python bin/extract-adoption-dates.py --verbose

  # Combine options: resume from BRD with verbose output
  python bin/extract-adoption-dates.py --start-from BRD --verbose
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
        '--verbose',
        action='store_true',
        help='Show detailed extraction attempts'
    )

    parser.add_argument(
        '--start-from',
        help='LPA code to resume processing from (e.g., "BRD"). Processes from this LPA onwards.',
        default=None
    )

    args = parser.parse_args()

    # Enable debug logging if verbose mode requested
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Parse LPA codes if provided
    lpa_codes = None
    if args.lpa:
        lpa_codes = [code.strip().upper() for code in args.lpa.split(',')]

    # Create extractor and process files
    extractor = DateExtractor(
        source_dir=args.source_dir,
        verbose=args.verbose
    )

    extractor.process_all_source_files(lpa_codes=lpa_codes, start_from=args.start_from)
    logger.info("✓ Date extraction complete")


if __name__ == '__main__':
    main()
