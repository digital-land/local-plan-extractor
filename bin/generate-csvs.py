#!/usr/bin/env python3
"""
Generate local plan CSVs from source JSON files and extracted housing data.

This script creates three CSV files:
1. local-plan.csv - Main local plan documents
2. local-plan-document.csv - Individual plan documents
3. local-plan-housing.csv - Housing requirements data (automatically loaded from local-plan/)

Housing data is automatically loaded from extracted local plan JSON files in the local-plan/ directory.

Usage:
    python bin/generate-csvs.py                              # Process all LPAs with auto-loaded housing data
    python bin/generate-csvs.py --lpa PEN,BOT,SHO           # Process specific LPAs
    python bin/generate-csvs.py --output-dir ./data/        # Specify output directory
    python bin/generate-csvs.py --existing-datasets ./path/  # Reuse existing entity numbers
"""

import json
import csv
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class LocalPlanCSVGenerator:
    # Reference overrides: maps generated reference to reference currently used on the Provide platform
    # Edit this dictionary to override generated references
    REFERENCE_OVERRIDES = {
        # Example:
        'babergh-and-mid-suffolk-joint-local-plan-part-1-2018-2037':'the-babergh-and-mid-suffolk-joint-local-plan'
    }

    def __init__(self, source_dir: str = "source", output_dir: str = ".", local_plan_dir: str = "local-plan"):
        """Initialize the CSV generator."""
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.local_plans = []
        self.local_plan_documents = []
        self.local_plan_housing = []
        self.housing_data = {}

        # Document counter tracking for reference generation
        self.authority_plan_counters = {}  # (authority_slug, year) -> counter
        self.current_plan_reference = None

        # Track organisations already processed for housing data (to avoid duplicates)
        self.organisations_with_housing = set()

        # Store main plan document references for housing data linking
        self.plan_main_document_ref = {}  # plan_ref -> main_doc_ref

        # Map to track joint plans and their organisations
        # Key: organisation code -> List of authorities
        self.joint_plan_organisations = {}

        # Load joint plan mappings from the joint-local-plans.json file
        self._load_joint_plan_mappings()

        # Automatically load housing data from local-plan directory
        self._load_housing_from_local_plan_dir(local_plan_dir)

    def _authority_to_slug(self, authority_name: str) -> str:
        """Convert authority name to slug format (lowercase, spaces to dashes)."""
        if not authority_name:
            return ''
        # Convert to lowercase and replace spaces with dashes
        slug = authority_name.lower().strip()
        slug = slug.replace(' ', '-')
        # Remove multiple consecutive dashes
        while '--' in slug:
            slug = slug.replace('--', '-')
        return slug

    def _load_housing_from_local_plan_dir(self, local_plan_dir: str = "local-plan"):
        """Automatically load housing data from extracted local plan JSON files."""
        local_plan_path = Path(local_plan_dir)
        if not local_plan_path.exists():
            logger.debug(f"Local plan directory not found: {local_plan_dir}")
            return

        json_files = list(local_plan_path.glob("*.json"))
        if not json_files:
            logger.debug(f"No JSON files found in {local_plan_dir}")
            return

        logger.info(f"Loading housing data from {len(json_files)} local plan files")

        loaded_count = 0
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Extract organisation and housing data
                org = data.get('organisation', '')
                housing_numbers = data.get('housing-numbers', [])

                if org and housing_numbers:
                    # Store housing data keyed by top-level organisation
                    if org not in self.housing_data:
                        self.housing_data[org] = {
                            'housing-numbers': housing_numbers,
                            'organisation-name': data.get('organisation-name', ''),
                        }
                        loaded_count += 1
                        logger.debug(f"Loaded housing data for {org}")

                    # Also store housing data keyed by individual organisations within housing-numbers
                    # This handles joint plans where the top-level org is different from individual orgs
                    for housing_entry in housing_numbers:
                        entry_org = housing_entry.get('organisation', '')
                        if entry_org and entry_org != org and entry_org not in self.housing_data:
                            self.housing_data[entry_org] = {
                                'housing-numbers': [housing_entry],
                                'organisation-name': housing_entry.get('organisation-name', ''),
                            }
                            loaded_count += 1
                            logger.debug(f"Loaded housing data for {entry_org} (from joint plan)")

            except Exception as e:
                logger.debug(f"Failed to load housing data from {json_file.name}: {e}")

        if loaded_count > 0:
            logger.info(f"Loaded housing data for {loaded_count} organisations")

    def _load_joint_plan_mappings(self):
        """Load joint plan mappings from the joint-local-plans.json file.

        This maps each authority to the list of authorities in its joint plan (if any).
        """
        joint_plans_path = Path("var") / "joint-local-plans.json"
        if not joint_plans_path.exists():
            logger.debug(f"Joint local plans file not found: {joint_plans_path}")
            return

        try:
            with open(joint_plans_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            joint_plans = data.get('joint-plans', {})
            for org_code, plan_info in joint_plans.items():
                authorities = plan_info.get('joint-plan-authorities', [])
                if authorities and isinstance(authorities, list) and len(authorities) > 1:
                    # Map this organisation to its joint plan authorities
                    self.joint_plan_organisations[org_code] = authorities
                    logger.debug(f"Loaded joint plan for {org_code}: {len(authorities)} authorities")

            if self.joint_plan_organisations:
                logger.info(f"Loaded {len(self.joint_plan_organisations)} joint plan mappings")
        except Exception as e:
            logger.warning(f"Failed to load joint plan mappings: {e}")

    def load_housing_data(self, housing_csv: str):
        """Load housing data from CSV generated by local-plan-extractor.py"""
        housing_path = Path(housing_csv)
        if not housing_path.exists():
            logger.warning(f"Housing CSV not found: {housing_csv}")
            return

        try:
            with open(housing_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Use organisation as key
                    org = row.get('organisation', '')
                    if org:
                        self.housing_data[org] = row
            logger.info(f"Loaded housing data for {len(self.housing_data)} organisations")
        except Exception as e:
            logger.error(f"Failed to load housing data: {e}")

    def load_source_json_files(self, lpa_codes: Optional[List[str]] = None):
        """Load all source JSON files, optionally filtered by LPA codes."""
        if not self.source_dir.exists():
            logger.error(f"Source directory not found: {self.source_dir}")
            return

        json_files = list(self.source_dir.glob("*.json"))
        logger.info(f"Found {len(json_files)} JSON files in {self.source_dir}")

        if lpa_codes:
            # Filter to requested LPAs
            lpa_codes_set = set(lpa_codes)
            json_files = [
                f for f in json_files
                if any(code in f.stem for code in lpa_codes_set)
            ]
            logger.info(f"Filtered to {len(json_files)} files for LPAs: {', '.join(lpa_codes)}")

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Data is an array of local plans
                    if isinstance(data, list):
                        for local_plan in data:
                            self._process_local_plan(local_plan)
                    elif isinstance(data, dict):
                        self._process_local_plan(data)

            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")
                continue

        logger.info(f"Loaded {len(self.local_plans)} local plans and {len(self.local_plan_documents)} documents")

    def _should_skip_plan(self, plan_data: Dict) -> bool:
        """Check if a plan should be skipped based on exclusion rules."""
        name = plan_data.get('name', '').lower()

        # Skip waste plans
        if 'waste' in name:
            logger.info(f"Skipping waste plan: {plan_data.get('name', '')}")
            return True

        # Skip mineral plans
        if 'mineral' in name:
            logger.info(f"Skipping mineral plan: {plan_data.get('name', '')}")
            return True

        # Add more edge cases as needed
        return False

    def _process_local_plan(self, plan_data: Dict):
        """Process a single local plan entry from JSON."""
        # Skip excluded plan types
        if self._should_skip_plan(plan_data):
            return

        try:
            # Create local-plan entry
            org = plan_data.get('organisation', '')
            organisation_name = plan_data.get('organisation-name', org)

            # Normalize authority slug (always available for downstream calls)
            authority_slug = self._authority_to_slug(organisation_name)

            # Extract year from plan data (check 'period-start-date' first, fallback to 'period-end-date')
            year = plan_data.get('period-start-date', '')
            if not year:
                # Try to extract year from period-end-date
                period_end = plan_data.get('period-end-date', '')
                if period_end:
                    year = period_end

            # Normalize year to string without '.0' if numeric-like
            if isinstance(year, (int, float)):
                try:
                    year = str(int(year))
                except Exception:
                    year = str(year)
            elif isinstance(year, str) and year.endswith('.0'):
                year = year[:-2]

            # Determine whether this is a joint plan
            organisations = plan_data.get('organisations', [])
            if not organisations and org in self.joint_plan_organisations:
                organisations = self.joint_plan_organisations[org]

            is_joint = False
            if organisations and isinstance(organisations, list) and len(organisations) > 1:
                is_joint = True

            # Generate slug and reference
            # For joint plans: slug from plan name only
            # For non-joint: slug from authority name and format {slug}-local-plan-{year}
            if is_joint:
                # Use plan name as slug for joint plans
                plan_name = plan_data.get('name', '')
                slug = self._authority_to_slug(plan_name)
                reference = slug if slug else ''
            else:
                authority_slug = self._authority_to_slug(organisation_name)
                if year:
                    reference = f"{authority_slug}-local-plan-{year}"
                else:
                    reference = f"{authority_slug}-local-plan"

            # Apply reference overrides if defined
            if reference in self.REFERENCE_OVERRIDES:
                original_reference = reference
                reference = self.REFERENCE_OVERRIDES[reference]
                logger.info(f"Applied reference override: {original_reference} → {reference}")

            # Build local-planning-authorities string
            # For joint plans, use the organisations array; otherwise use single organisation

            # First check if source data has organisations array
            organisations = plan_data.get('organisations', [])

            # If not in source data, check if this is a known joint plan from housing data
            if not organisations and org in self.joint_plan_organisations:
                organisations = self.joint_plan_organisations[org]

            if organisations and isinstance(organisations, list):
                # Join multiple authorities with semicolons (no spaces)
                local_planning_authorities = ';'.join(organisations)
            else:
                # Single authority
                local_planning_authorities = org if org else ''

            # Store current plan reference for document numbering
            self.current_plan_reference = reference

            # Initialize document counter for this plan (use slug, which differs for joint plans)
            plan_key = (slug if is_joint else authority_slug, year)
            if plan_key not in self.authority_plan_counters:
                self.authority_plan_counters[plan_key] = 0

            # Initialize plan-to-document mapping
            self.plan_main_document_ref[reference] = None

            local_plan_entry = {
                'reference': reference,
                'name': plan_data.get('name', ''),
                'dataset': 'local-plan',
                'period-start-date': self._format_date(plan_data.get('period-start-date', '')),
                'period-end-date': self._format_date(plan_data.get('period-end-date', '')),
                'local-planning-authorities': local_planning_authorities,
                'mineral-planning-authorities': plan_data.get('mineral-planning-authorities', ''),
                'waste-planning-authorities': plan_data.get('waste-planning-authorities', ''),
                'local-plan-process': plan_data.get('local-plan-process', plan_data.get('status', '')),
                'documentation-url': plan_data.get('documentation-url', ''),
                'document-url': plan_data.get('document-url', ''),
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'start-date': plan_data.get('adoption-date'),
                'end-date': plan_data.get('withdrawn-date'),
                'notes': plan_data.get('notes', ''),
            }

            self.local_plans.append(local_plan_entry)

            # Process documents
            documents = plan_data.get('documents', [])
            if isinstance(documents, list):
                for doc in documents:
                    # Pass the plan-specific slug for joint plans so document counters align
                    doc_authority_slug = slug if is_joint else authority_slug
                    self._process_document(
                        reference,
                        doc_authority_slug,
                        year,
                        doc,
                        plan_data.get('adoption-date'),
                        plan_data.get('withdrawn-date')
                    )

            # Add housing data if available
            if org in self.housing_data:
                self._add_housing_data(reference, local_planning_authorities, org)

        except Exception as e:
            logger.error(f"Failed to process local plan: {e}")

    def _process_document(self, plan_reference: str, authority_slug: str, year: str, doc_data: Dict,
                         adoption_date: Optional[str] = None, withdrawn_date: Optional[str] = None):
        """Process a single document entry.

        Args:
            plan_reference: Reference of the parent plan
            authority_slug: Authority slug for counter keying
            year: Year of the plan
            doc_data: Document data dictionary
            adoption_date: Adoption date inherited from parent plan
            withdrawn_date: Withdrawn date inherited from parent plan
        """
        try:
            # Get the counter key for this plan
            plan_key = (authority_slug, year)

            # Increment counter for this plan and generate numbered reference
            self.authority_plan_counters[plan_key] += 1
            doc_num = self.authority_plan_counters[plan_key]
            doc_reference = f"{plan_reference}-{doc_num}"

            # Track the first (main) document for this plan
            if doc_num == 1:
                self.plan_main_document_ref[plan_reference] = doc_reference

            doc_entry = {
                'reference': doc_reference,
                'name': doc_data.get('name', ''),
                'description': doc_data.get('description', ''),
                'local-plan': plan_reference,
                'document-types': doc_data.get('document-type', ''),
                'documentation-url': doc_data.get('documentation-url', ''),
                'document-url': doc_data.get('document-url', ''),
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'start-date': self._format_date(adoption_date) if adoption_date else '',
                'end-date': self._format_date(withdrawn_date) if withdrawn_date else '',
                'notes': doc_data.get('notes', ''),
            }

            self.local_plan_documents.append(doc_entry)

            # Store the generated reference for housing data to reference
            doc_data['_generated_reference'] = doc_reference

        except Exception as e:
            logger.error(f"Failed to process document: {e}")

    def _add_housing_data(self, plan_reference: str, lpa: str, org: str):
        """Add housing data entry for an organisation (only once per organisation to avoid duplicates)."""
        try:
            # Skip if we've already added housing data for this organisation
            if org in self.organisations_with_housing:
                logger.debug(f"Skipping housing data for {org} (already processed)")
                return

            housing = self.housing_data.get(org, {})
            housing_numbers = housing.get('housing-numbers', '[]')

            # Parse housing numbers if it's a JSON string
            if isinstance(housing_numbers, str):
                try:
                    housing_numbers = json.loads(housing_numbers)
                except:
                    housing_numbers = []

            # Create entry for each authority in housing-numbers array
            if isinstance(housing_numbers, list):
                for num_entry in housing_numbers:
                    # Use the correct document reference from the main document mapping
                    # If not found, fallback to first document reference
                    housing_reference = self.plan_main_document_ref.get(
                        plan_reference,
                        f"{plan_reference}-1"
                    )

                    housing_entry = {
                        'reference': housing_reference,
                        'local-plan': plan_reference,
                        'local-planning-authority': num_entry.get('organisation-name', lpa),
                        'required-housing': num_entry.get('required-housing', ''),
                        'committed-housing': num_entry.get('committed-housing', ''),
                        'allocated-housing': num_entry.get('allocated-housing', ''),
                        'broad-locations-housing': num_entry.get('broad-locations-housing', ''),
                        'windfall-housing': num_entry.get('windfall-housing', ''),
                        'entry-date': datetime.now().strftime('%Y-%m-%d'),
                        'start-date': '',
                        'end-date': '',
                        'notes': num_entry.get('notes', ''),
                    }
                    self.local_plan_housing.append(housing_entry)

                # Mark this organisation as processed
                self.organisations_with_housing.add(org)

        except Exception as e:
            logger.error(f"Failed to add housing data for {org}: {e}")

    def _format_date(self, date_input) -> str:
        """Format date input to ISO format."""
        if not date_input or date_input == '':
            return ''

        # If it's already a string that looks like ISO format
        if isinstance(date_input, str):
            if date_input.startswith('20') and len(date_input) >= 4:
                # Might be a year or year-month or full date
                return date_input
            return ''

        # If it's an integer (year)
        if isinstance(date_input, int):
            return str(date_input)

        return ''

    def write_csvs(self):
        """Write all three CSVs to output directory."""
        self._write_csv('local-plan.csv', self.local_plans, [
            'reference', 'name', 'dataset', 'period-start-date', 'period-end-date',
            'local-planning-authorities', 'mineral-planning-authorities',
            'waste-planning-authorities', 'local-plan-process',
            'documentation-url', 'document-url', 'entry-date', 'start-date', 'end-date', 'notes'
        ])

        self._write_csv('local-plan-document.csv', self.local_plan_documents, [
            'reference', 'name', 'description', 'local-plan', 'document-types',
            'documentation-url', 'document-url', 'entry-date', 'start-date', 'end-date', 'notes'
        ])

        # Deduplicate housing data (remove exact duplicate rows)
        housing_deduped = self._deduplicate_housing_data(self.local_plan_housing)
        if len(housing_deduped) < len(self.local_plan_housing):
            logger.info(f"Deduplicated housing data: {len(self.local_plan_housing)} → {len(housing_deduped)} rows")

        self._write_csv('local-plan-housing.csv', housing_deduped, [
            'reference', 'local-plan', 'local-planning-authority',
            'required-housing', 'committed-housing', 'allocated-housing',
            'broad-locations-housing', 'windfall-housing',
            'entry-date', 'start-date', 'end-date', 'notes'
        ])

    def _deduplicate_housing_data(self, housing_data: List[Dict]) -> List[Dict]:
        """Remove exact duplicate rows from housing data (keeping first occurrence)."""
        seen = set()
        deduplicated = []

        for entry in housing_data:
            # Create a tuple of all fields except 'entity' (which is auto-generated)
            # This allows us to detect truly identical housing records
            key = (
                entry.get('reference', ''),
                entry.get('local-plan', ''),
                entry.get('local-planning-authority', ''),
                entry.get('required-housing', ''),
                entry.get('committed-housing', ''),
                entry.get('allocated-housing', ''),
                entry.get('broad-locations-housing', ''),
                entry.get('windfall-housing', ''),
                entry.get('notes', ''),
            )

            if key not in seen:
                seen.add(key)
                deduplicated.append(entry)

        return deduplicated

    def _write_csv(self, filename: str, data: List[Dict], fieldnames: List[str]):
        """Write a single CSV file."""
        output_path = self.output_dir / filename

        try:
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()

                for row in data:
                    # Ensure all fields exist
                    for field in fieldnames:
                        if field not in row:
                            row[field] = ''
                    writer.writerow(row)

            logger.info(f"✓ Written {len(data)} rows to {output_path}")

        except Exception as e:
            logger.error(f"Failed to write {filename}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Generate local plan CSVs from source JSON files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate all CSVs for all authorities (housing data auto-loaded from local-plan/)
  python bin/generate-csvs.py

  # Process only specific LPAs
  python bin/generate-csvs.py --lpa PEN,BOT,SHO

  # Specify output directory
  python bin/generate-csvs.py --output-dir ./data/

  # Combine options
  python bin/generate-csvs.py --lpa PEN,BOT --output-dir ./output/
        """
    )

    parser.add_argument(
        '--lpa',
        help='Comma-separated list of LPA codes to process (e.g., "PEN,BOT,SHO"). If not specified, processes all LPAs.',
        default=None
    )

    parser.add_argument(
        '--source-dir',
        help='Path to source directory containing JSON files (default: source/)',
        default='source'
    )

    parser.add_argument(
        '--output-dir',
        help='Output directory for CSV files (default: dataset/)',
        default='dataset'
    )

    parser.add_argument(
        '--housing',
        help='Path to housing_data.csv from local-plan-extractor.py (deprecated - housing data is now auto-loaded from local-plan/ directory)',
        default=None
    )

    args = parser.parse_args()

    # Parse LPA codes if provided
    lpa_codes = None
    if args.lpa:
        lpa_codes = [code.strip().upper() for code in args.lpa.split(',')]

    # Create generator
    generator = LocalPlanCSVGenerator(
        source_dir=args.source_dir,
        output_dir=args.output_dir
    )

    # Load housing data if provided
    if args.housing:
        generator.load_housing_data(args.housing)

    # Load source JSON files
    generator.load_source_json_files(lpa_codes)

    # Write CSVs
    generator.write_csvs()

    logger.info("✓ CSV generation complete")


if __name__ == '__main__':
    main()
