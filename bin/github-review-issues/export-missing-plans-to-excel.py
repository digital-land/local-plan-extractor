#!/usr/bin/env python3
"""
Export missing-plan GitHub issues to Excel for manual review and import.

This script:
1. Fetches all issues with 'missing-plan' label from GitHub
2. Parses the structured body text (Name, Organisation(s), dates, URLs)
3. Maps organisation names to codes using organisation.csv
4. Handles joint plans (multiple organisations)
5. Exports to data/missing_plans_import_2026.xlsx

The Excel file is then manually reviewed and corrected before import.
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional
import urllib.request
import urllib.error

import pandas as pd

# Add parent directory to path to import modules from bin/
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import organisation matcher for mapping org names to codes
from organisation_matcher import OrganisationMatcher

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com/repos/digital-land/local-plan-extractor"


class MissingPlansExporter:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        if verbose:
            logger.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)

        # Initialize organisation matcher
        org_csv = Path(__file__).parent.parent / 'var' / 'cache' / 'organisation.csv'
        self.org_matcher = OrganisationMatcher(str(org_csv))

        # GitHub API token for better rate limits
        self.github_token = os.environ.get('GITHUB_TOKEN', '')

    def _make_github_request(self, url: str) -> Optional[List[Dict]]:
        """Make a request to GitHub API with authentication if available."""
        headers = {}
        if self.github_token:
            headers['Authorization'] = f'Bearer {self.github_token}' if self.github_token.startswith('github_') else f'token {self.github_token}'

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.HTTPError as e:
            logger.error(f"HTTP Error {e.code}: {e.reason}")
            return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

    def _fetch_all_missing_plan_issues(self) -> List[Dict]:
        """Fetch all issues with missing-plan label."""
        issues = []
        page = 1

        while True:
            url = f"{GITHUB_API_BASE}/issues?state=open&labels=missing-plan&per_page=100&page={page}"
            logger.info(f"Fetching page {page}...")

            page_issues = self._make_github_request(url)
            if not page_issues:
                logger.error("Failed to fetch issues from GitHub")
                return issues

            if not page_issues:  # Empty page means we're done
                break

            issues.extend(page_issues)
            page += 1

        logger.info(f"Fetched {len(issues)} missing-plan issues")
        return issues

    def _parse_issue_body(self, body: str, issue_number: int) -> List[Dict]:
        """Parse structured issue body to extract plan data.

        Returns a list of dictionaries (one per organisation for joint plans).
        """
        plans = []

        try:
            # Extract sections from body
            lines = body.split('\n')

            # Extract name
            name = ""
            organisations = []
            start_date = ""
            end_date = ""
            adopted_date = ""
            withdrawn_date = ""
            documentation_url = ""
            document_url = ""

            current_section = None
            org_lines = []

            for line in lines:
                line = line.strip()

                if line.startswith('**Name of local plan**'):
                    current_section = 'name'
                    continue
                elif line.startswith('**Organisation(s)**'):
                    current_section = 'organisations'
                    continue
                elif line.startswith('**Start date**'):
                    current_section = 'start_date'
                    continue
                elif line.startswith('**End date**'):
                    current_section = 'end_date'
                    continue
                elif line.startswith('**Adopted date**'):
                    current_section = 'adopted_date'
                    continue
                elif line.startswith('**Withdrawn date**'):
                    current_section = 'withdrawn_date'
                    continue
                elif line.startswith('**Documentation URL**'):
                    current_section = 'documentation_url'
                    continue
                elif line.startswith('**Document URL**'):
                    current_section = 'document_url'
                    continue
                elif line.startswith('**'):
                    # Another section header
                    current_section = None
                    continue

                # Skip empty lines and notes in parentheses
                if not line or line.startswith('('):
                    continue

                # Collect data based on current section
                if current_section == 'name' and not name:
                    name = line
                elif current_section == 'organisations':
                    if line and not line.startswith('[') and not line.startswith('#'):
                        org_lines.append(line)
                elif current_section == 'start_date' and not start_date:
                    start_date = line
                elif current_section == 'end_date' and not end_date:
                    end_date = line
                elif current_section == 'adopted_date' and not adopted_date:
                    adopted_date = line
                elif current_section == 'withdrawn_date' and not withdrawn_date:
                    withdrawn_date = line
                elif current_section == 'documentation_url' and not documentation_url:
                    if line.startswith('http'):
                        documentation_url = line
                elif current_section == 'document_url' and not document_url:
                    if line.startswith('http'):
                        document_url = line

            # Parse organisation lines, filtering out notes
            for org_line in org_lines:
                # Skip lines that are notes or explanations
                if org_line.startswith('[note:') or 'defined at' in org_line.lower():
                    continue
                # Clean up the line
                org = org_line.strip()
                if org:
                    organisations.append(org)

            # Create entry for each organisation (for joint plans)
            if not organisations:
                logger.warning(f"Issue #{issue_number}: No organisations found")
                return plans

            for org_name in organisations:
                org_code = self.org_matcher.match(org_name)

                if not org_code:
                    logger.warning(f"Issue #{issue_number}: Could not match organisation '{org_name}'")
                    org_code = f"local-authority:UNKNOWN"  # Placeholder

                plan_entry = {
                    'organisation': org_code,
                    'organisation-label': org_name.strip(),
                    'type': 'adopted',
                    'documentation-url': documentation_url,
                    'document-url': document_url,
                    'period-start-date': start_date,
                    'period-end-date': end_date,
                    'adoption-date': adopted_date,
                    'withdrawn-date': withdrawn_date,
                    'notes': f"GitHub issue #{issue_number}" +
                             (f" | Withdrawn: {withdrawn_date}" if withdrawn_date else ""),
                    'github_issue': issue_number,
                    'Done by? (AMP/ST)': ''
                }

                plans.append(plan_entry)

            return plans

        except Exception as e:
            logger.error(f"Error parsing issue #{issue_number}: {e}")
            return plans

    def export_to_excel(self, output_path: str = 'data/missing_plans_import_2026.xlsx'):
        """Fetch issues and export to Excel."""
        # Fetch all missing-plan issues
        issues = self._fetch_all_missing_plan_issues()
        if not issues:
            logger.error("No issues to export")
            return False

        # Parse all issues
        all_plans = []
        for issue in issues:
            issue_number = issue.get('number')
            body = issue.get('body', '')

            plans = self._parse_issue_body(body, issue_number)
            if plans:
                all_plans.extend(plans)
                logger.info(f"Issue #{issue_number}: Parsed {len(plans)} organisation(s)")

        if not all_plans:
            logger.error("No plans could be parsed from issues")
            return False

        # Create DataFrame
        df = pd.DataFrame(all_plans)

        # Ensure output directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write to Excel
        logger.info(f"Writing {len(all_plans)} entries to {output_path}")
        df.to_excel(output_path, sheet_name='manual-search', index=False)

        logger.info(f"Successfully exported to {output_path}")
        logger.info(f"Total entries: {len(all_plans)}")
        logger.info(f"Unique issues: {len(set(p['github_issue'] for p in all_plans))}")

        # Print summary of unmapped organisations
        unmapped = [p for p in all_plans if 'UNKNOWN' in p['organisation']]
        if unmapped:
            logger.warning(f"\n{len(unmapped)} entries with unmapped organisations:")
            for plan in unmapped:
                logger.warning(f"  - {plan['organisation-label']}")

        return True


def main():
    parser = argparse.ArgumentParser(
        description='Export missing-plan GitHub issues to Excel for import'
    )
    parser.add_argument(
        '--output',
        default='data/missing_plans_import_2026.xlsx',
        help='Output Excel file path (default: data/missing_plans_import_2026.xlsx)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    exporter = MissingPlansExporter(verbose=args.verbose)
    success = exporter.export_to_excel(args.output)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
