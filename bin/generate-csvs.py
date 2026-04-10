#!/usr/bin/env python3
"""
Generate local plan CSVs from local plan source data with extracted enrichment.

This script creates two CSV files:
1. local-plan.csv - All local plans (draft and adopted) with enrichment from extracted data,
                    including required-housing totals from local-plan/ JSON files
2. local-plan-document.csv - Individual plan documents from source

Local plan data is sourced from source/ JSON files (both draft and adopted plans).
Enrichment data (local-planning-authorities E0x codes, housing data) is merged from
extracted local-plan/ JSON files where available.

Usage:
    python bin/generate-csvs.py                              # Generate CSVs for all local plans
    python bin/generate-csvs.py --lpa PEN,BOT,SHO           # Generate CSVs for specific LPAs
    python bin/generate-csvs.py --output-dir ./dataset/        # Specify output directory
    python bin/generate-csvs.py --lpa BRO --output-dir ./   # Combine options
"""

import json
import csv
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import logging
import urllib.request
import urllib.error
import io

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
        'babergh-and-mid-suffolk-joint-local-plan-part-1':'the-babergh-and-mid-suffolk-joint-local-plan'
    }

    # Exceptions to the "skip joint-planning-authority" rule
    # These are endpoints where joint-planning-authority housing data should be included
    JOINT_HOUSING_DATA_EXCEPTIONS = {
        '3cba9221447d0329e6ab8ff1c37305fd4bdfca6cd5efa908693d0c559de126f5',  # West Dorset-Weymouth (WDO-WEY)
        '5b1d60774e8bd97a7f40221468bd0f4c5c9446faca7ef38dccb19c514850811a'  # Greater Norwich Local Plan
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

        # Track which endpoint each housing data came from
        # Key: organisation -> {'endpoint': endpoint_hash}
        self.housing_data_source = {}

        # Map from endpoint to plan reference (for looking up correct plan reference from housing data endpoint)
        # Key: endpoint_hash -> plan_reference
        self.endpoint_to_plan_ref = {}

        # Track plan references we've already processed (to avoid duplicates)
        self.processed_plan_references = set()

        # Document counter tracking for reference generation
        self.authority_plan_counters = {}  # (authority_slug, year) -> counter
        self.current_plan_reference = None

        # Track (organisation, plan_reference) pairs already processed for housing data (to avoid duplicates)
        self.organisations_with_housing = set()  # (org, plan_ref) tuple set

        # Store main plan document references for housing data linking
        self.plan_main_document_ref = {}  # plan_ref -> main_doc_ref

        # Map to track joint plans and their organisations
        # Key: organisation code -> List of authorities
        self.joint_plan_organisations = {}

        # Map from joint plan names (slugified) to their authorities
        # Key: plan_name_slug -> list of authorities
        self.joint_plan_names = {}

        # Map to track source metadata (document-url, documentation-url, local-plan-process) by organisation
        # Key: organisation -> {document-url, documentation-url, local-plan-process}
        self.source_metadata = {}

        # Map to store enrichment data from extracted local-plan/ files
        # Indexed by PDF endpoint hash for matching with source documents
        self.enrichment_by_endpoint = {}  # endpoint hash -> enriched data
        self.enrichment_by_org = {}  # organisation -> enriched data

        # Map to store geography code mappings from boundary data
        # Key: organisation code (e.g., 'local-authority:DUR') -> geography code (e.g., 'E60000001')
        self.boundary_mapping = {}

        # Static mapping for national parks and development corporations (Q-codes to E-codes)
        self.static_geography_mapping = {
            'national-park-authority:Q4972284': 'E60000326',  # Broads Authority
            'national-park-authority:Q5225646': 'E60000318',  # Dartmoor
            'national-park-authority:Q72617784': 'E60000319',  # Exmoor
            'national-park-authority:Q27159704': 'E60000320',  # Lake District
            'national-park-authority:Q72617158': 'E60000321',  # New Forest
            'national-park-authority:Q72617669': 'E60000322',  # North York Moors
            'national-park-authority:Q72617890': 'E60000323',  # Northumberland
            'national-park-authority:Q72617988': 'E60000324',  # Peak District
            'national-park-authority:Q20198711': 'E60000325',  # South Downs
            'national-park-authority:Q27178932': 'E60000327',  # Yorkshire Dales
            'development-corporation:Q72463795': 'E60000328',  # Ebbsfleet Development Corporation
            'development-corporation:Q6670544': 'E60000329',   # London Legacy Development Corporation
            'development-corporation:Q20648596': 'E60000330',  # Old Oak and Park Royal Development Corporation
        }

        # Load joint plan mappings from the joint-local-plans.json file
        self._load_joint_plan_mappings()

        # Load source metadata for enrichment
        self._load_source_metadata()

        # Load enrichment data from extracted local-plan directory
        self._load_enrichment_data_from_local_plan_dir(local_plan_dir)

        # Automatically load housing data from local-plan directory
        self._load_housing_from_local_plan_dir(local_plan_dir)

        # Load geography code mappings from boundary data
        self._load_boundary_mapping()

    def _authority_to_slug(self, authority_name: str) -> str:
        """Convert authority name to slug format (lowercase, spaces to dashes, sanitize special chars)."""
        if not authority_name:
            return ''
        # Convert to lowercase and strip
        slug = authority_name.lower().strip()
        # Replace common special characters with hyphens or spaces
        slug = slug.replace('&', 'and')
        slug = slug.replace('–', '-')  # en-dash
        slug = slug.replace('—', '-')  # em-dash
        slug = slug.replace('/', '-')
        # Replace spaces with dashes
        slug = slug.replace(' ', '-')
        # Remove any remaining non-alphanumeric characters except hyphens
        slug = ''.join(c for c in slug if c.isalnum() or c == '-')
        # Remove multiple consecutive dashes
        while '--' in slug:
            slug = slug.replace('--', '-')
        # Remove leading/trailing dashes
        slug = slug.strip('-')
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
                endpoint = json_file.stem  # Filename without .json extension

                if org and housing_numbers:
                    # Store housing data keyed by top-level organisation
                    if org not in self.housing_data:
                        self.housing_data[org] = {
                            'housing-numbers': housing_numbers,
                            'organisation-name': data.get('organisation-name', ''),
                        }
                        # Track the endpoint this housing data came from
                        self.housing_data_source[org] = {'endpoint': endpoint}
                        loaded_count += 1
                        logger.debug(f"Loaded housing data for {org} from endpoint {endpoint}")

                    # Also store housing data keyed by individual organisations within housing-numbers
                    # This handles joint plans where the top-level org is different from individual orgs
                    for housing_entry in housing_numbers:
                        entry_org = housing_entry.get('organisation', '')
                        if entry_org and entry_org != org and entry_org not in self.housing_data:
                            self.housing_data[entry_org] = {
                                'housing-numbers': [housing_entry],
                                'organisation-name': housing_entry.get('organisation-name', ''),
                            }
                            # Track the endpoint this housing data came from
                            self.housing_data_source[entry_org] = {'endpoint': endpoint}
                            loaded_count += 1
                            logger.debug(f"Loaded housing data for {entry_org} (from joint plan endpoint {endpoint})")

                    # For joint-planning-authority and local-planning-group entries, also create entries under individual local-authority codes
                    # This allows the housing data to be picked up when processing individual source files
                    if org.startswith(('joint-planning-authority:', 'local-planning-group:')):
                        # Extract authority codes (e.g., 'WDO-WEY' from 'joint-planning-authority:WDO-WEY' or 'local-planning-group:WDO-WEY')
                        codes_str = org.split(':')[1]  # Get 'WDO-WEY'
                        authority_codes = codes_str.split('-')

                        for auth_code in authority_codes:
                            local_auth_org = f'local-authority:{auth_code}'
                            if local_auth_org not in self.housing_data:
                                # Create entry pointing to the joint housing data
                                self.housing_data[local_auth_org] = {
                                    'housing-numbers': housing_numbers,
                                    'organisation-name': data.get('organisation-name', ''),
                                }
                                # Track the endpoint this housing data came from
                                self.housing_data_source[local_auth_org] = {'endpoint': endpoint}
                                loaded_count += 1
                                logger.debug(f"Loaded housing data for {local_auth_org} (from {org}, endpoint {endpoint})")

            except Exception as e:
                logger.debug(f"Failed to load housing data from {json_file.name}: {e}")

        if loaded_count > 0:
            logger.info(f"Loaded housing data for {loaded_count} organisations")

    def _load_source_metadata(self):
        """Load metadata from source JSON files to enrich extracted data.

        Extracts document-url, documentation-url, and local-plan-process from source files
        and maps them by organisation for later lookup.

        Falls back to using first document's URLs if plan-level URLs are not available.
        """
        if not self.source_dir.exists():
            logger.debug(f"Source directory not found: {self.source_dir}")
            return

        try:
            json_files = list(self.source_dir.glob("*.json"))
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                        # Handle both single dict and list formats
                        plans = data if isinstance(data, list) else [data]

                        for plan in plans:
                            org = plan.get('organisation', '')
                            if not org:
                                continue

                            # Skip entries with errors
                            if plan.get('error'):
                                continue

                            # Start with plan-level metadata
                            doc_url = plan.get('document-url', '')
                            doc_url_site = plan.get('documentation-url', '')

                            # Fall back to first document's URLs if plan-level are empty
                            if not doc_url or not doc_url_site:
                                documents = plan.get('documents', [])
                                if documents and isinstance(documents, list) and len(documents) > 0:
                                    first_doc = documents[0]
                                    if not doc_url:
                                        doc_url = first_doc.get('document-url', '')
                                    if not doc_url_site:
                                        doc_url_site = first_doc.get('documentation-url', '')

                            # Store metadata for this organisation (prefer entries with valid data)
                            # Skip if we already have better data (unless this entry has more)
                            if org not in self.source_metadata or (doc_url and not self.source_metadata[org].get('document-url')):
                                self.source_metadata[org] = {
                                    'document-url': doc_url,
                                    'documentation-url': doc_url_site,
                                    'local-plan-process': plan.get('local-plan-process', plan.get('status', '')),
                                }
                except Exception as e:
                    logger.debug(f"Error loading metadata from {json_file.name}: {e}")
                    continue

            if self.source_metadata:
                logger.debug(f"Loaded metadata for {len(self.source_metadata)} organisations from source")
        except Exception as e:
            logger.debug(f"Error loading source metadata: {e}")

    def _load_boundary_mapping(self, boundary_csv: str = "dataset/local-plan-boundary.csv"):
        """Load geography code mapping from local-plan-boundary.csv.

        Creates a mapping of organisation codes to geography codes for looking up missing
        local-planning-authorities codes.
        """
        boundary_path = Path(boundary_csv)
        if not boundary_path.exists():
            logger.debug(f"Boundary CSV not found: {boundary_csv}")
            return

        try:
            with open(boundary_path, 'r', encoding='utf-8') as f:
                # Only read the first 5 columns (reference, name, organisation, organisations, local-planning-authorities)
                # to avoid loading the huge geometry column
                for line in f:
                    if line.startswith('reference'):
                        # Skip header
                        continue

                    # Parse just the first 5 comma-separated values
                    parts = line.split(',', 5)  # Split on first 5 commas
                    if len(parts) >= 4:
                        reference = parts[0].strip()
                        organisation = parts[2].strip()

                        if organisation and reference:
                            self.boundary_mapping[organisation] = reference
                            logger.debug(f"Mapped {organisation} -> {reference}")

            if self.boundary_mapping:
                logger.info(f"Loaded {len(self.boundary_mapping)} geography code mappings from boundary data")
        except Exception as e:
            logger.warning(f"Error loading boundary mapping: {e}")

    def _lookup_geographies(self, organisations: str) -> str:
        """Look up geography codes for a semicolon-separated list of organisations.

        Args:
            organisations: E.g., 'local-authority:DUR;local-authority:TYN'

        Returns:
            Hyphen-separated geography codes, or empty string if not found
        """
        if not organisations:
            return ''

        org_list = organisations.split(';')
        geographies = []

        for org in org_list:
            org = org.strip()
            # First try boundary mapping (local authorities)
            if org in self.boundary_mapping:
                geographies.append(self.boundary_mapping[org])
            # Then try static mapping (national parks, development corporations)
            elif org in self.static_geography_mapping:
                geographies.append(self.static_geography_mapping[org])

        return '-'.join(geographies) if geographies else ''

    def _load_enrichment_data_from_local_plan_dir(self, local_plan_dir: str = "local-plan"):
        """Load enrichment data from extracted local-plan JSON files.

        Creates indexes for quick lookup by PDF endpoint and organisation.
        Enrichment data includes local-planning-authorities (E0x geography codes).
        """
        local_plan_path = Path(local_plan_dir)
        if not local_plan_path.exists():
            logger.debug(f"Local plan directory not found: {local_plan_dir}")
            return

        try:
            json_files = list(local_plan_path.glob("*.json"))
            loaded_count = 0

            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Extract PDF endpoint from pdf_file field
                    pdf_file = data.get('pdf_file', '')
                    if pdf_file:
                        # Extract hash from "collection/document/HASH.pdf"
                        endpoint = pdf_file.split('/')[-1].replace('.pdf', '')
                        self.enrichment_by_endpoint[endpoint] = data
                        loaded_count += 1
                        logger.debug(f"Indexed enrichment data by endpoint: {endpoint}")

                    # Also index by organisation for fallback matching
                    org = data.get('organisation')
                    if org:
                        self.enrichment_by_org[org] = data

                except Exception as e:
                    logger.debug(f"Failed to load enrichment data from {json_file.name}: {e}")

            if loaded_count > 0:
                logger.info(f"Loaded enrichment data from {loaded_count} local plan files")

        except Exception as e:
            logger.debug(f"Error loading enrichment data: {e}")

    def _get_enrichment_data(self, plan_data: Dict) -> Optional[Dict]:
        """Get enrichment data for a source plan by trying multiple matching strategies.

        First tries to match by PDF endpoint from documents, then falls back to organisation.
        """
        # Try to find enrichment by checking document endpoints
        documents = plan_data.get('documents', [])
        if isinstance(documents, list):
            for doc in documents:
                endpoint = doc.get('endpoint', '')
                if endpoint and endpoint in self.enrichment_by_endpoint:
                    logger.debug(f"Found enrichment by document endpoint: {endpoint}")
                    return self.enrichment_by_endpoint[endpoint]

        # Fallback: try to match by organisation
        org = plan_data.get('organisation', '')
        if org and org in self.enrichment_by_org:
            logger.debug(f"Found enrichment by organisation: {org}")
            return self.enrichment_by_org[org]

        return None

    def _get_required_housing(self, plan_data: Dict) -> str:
        """Extract required-housing total by matching document endpoints only.

        Looks up enrichment data by document endpoint (no organisation fallback).
        For joint plans, uses the aggregate entry if present; otherwise sums
        individual authority entries. Returns "" if no endpoint match found.
        """
        # Try to find enrichment by checking document endpoints (no fallback to org)
        documents = plan_data.get('documents', [])
        enrichment_data = None
        if isinstance(documents, list):
            for doc in documents:
                endpoint = doc.get('endpoint', '')
                if endpoint and endpoint in self.enrichment_by_endpoint:
                    enrichment_data = self.enrichment_by_endpoint[endpoint]
                    break

        if not enrichment_data:
            return ""

        housing_numbers = enrichment_data.get('housing-numbers', [])
        if not housing_numbers or not isinstance(housing_numbers, list):
            return ""

        top_org = enrichment_data.get('organisation', '')
        is_joint = top_org.startswith(('joint-planning-authority:', 'local-planning-group:'))

        if is_joint:
            # Use aggregate entry if present
            for entry in housing_numbers:
                entry_org = entry.get('organisation', '')
                if entry_org.startswith(('joint-planning-authority:', 'local-planning-group:')):
                    val = entry.get('required-housing', '')
                    return str(val) if val != '' and val is not None else ''
            # No aggregate — sum all individual entries
            total = 0
            found_any = False
            for entry in housing_numbers:
                val = entry.get('required-housing', '')
                if val == '' or val is None:
                    continue
                try:
                    total += int(val)
                    found_any = True
                except (TypeError, ValueError):
                    return ''  # Non-numeric range value; cannot sum
            return str(total) if found_any else ''
        else:
            # Single-authority: find matching entry
            for entry in housing_numbers:
                if entry.get('organisation', '') == top_org:
                    val = entry.get('required-housing', '')
                    return str(val) if val != '' and val is not None else ''
            return ''

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
            processed_plans = set()  # Track unique joint plans to avoid duplicates

            for org_code, plan_info in joint_plans.items():
                authorities = plan_info.get('joint-plan-authorities', [])
                if authorities and isinstance(authorities, list) and len(authorities) > 1:
                    # Map this organisation to its joint plan authorities
                    self.joint_plan_organisations[org_code] = authorities

                    # Also map the joint plan name to its authorities (only once per unique plan)
                    plan_name = plan_info.get('joint-plan-name', '')
                    if plan_name:
                        plan_slug = self._authority_to_slug(plan_name)
                        if plan_slug not in processed_plans:
                            self.joint_plan_names[plan_slug] = authorities
                            processed_plans.add(plan_slug)
                            logger.debug(f"Mapped joint plan name '{plan_name}' ({plan_slug}) to {len(authorities)} authorities")

                    logger.debug(f"Loaded joint plan for {org_code}: {len(authorities)} authorities")

            if self.joint_plan_organisations:
                logger.info(f"Loaded {len(self.joint_plan_organisations)} joint plan mappings and {len(self.joint_plan_names)} joint plan names")
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

    def load_local_plans_from_extracted_data(self, local_plan_dir: str = "local-plan", lpa_codes: Optional[List[str]] = None):
        """Load local plans directly from extracted local-plan JSON files.

        This provides the definitive local plan data with enriched fields including
        local-planning-authorities (E0x codes) and housing data.

        Args:
            local_plan_dir: Directory containing extracted local plan JSON files
            lpa_codes: Optional list of LPA codes to filter by (e.g., ['PEN', 'BOT', 'SHO'])
        """
        local_plan_path = Path(local_plan_dir)
        if not local_plan_path.exists():
            logger.error(f"Local plan directory not found: {local_plan_dir}")
            return

        json_files = list(local_plan_path.glob("*.json"))
        logger.info(f"Found {len(json_files)} extracted local plan files in {local_plan_dir}")

        # Filter by LPA codes if provided
        if lpa_codes:
            lpa_codes_set = set(lpa_codes)
            original_count = len(json_files)
            json_files = [
                f for f in json_files
                if any(code in f.read_text() for code in lpa_codes_set)
            ]
            logger.info(f"Filtered to {len(json_files)} files for LPAs: {', '.join(lpa_codes)}")

        loaded = 0
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._process_local_plan(data)
                    loaded += 1
            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")
                continue

        logger.info(f"Loaded {loaded} local plans from extracted data")

    def load_documents_from_source(self, lpa_codes: Optional[List[str]] = None):
        """Load only documents from source JSON files (for local-plan-document.csv).

        This method extracts documents from source files without duplicating local plans,
        since plans are already loaded from local-plan/ extracted data.
        """
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
            logger.info(f"Filtered to {len(json_files)} source files for LPAs: {', '.join(lpa_codes)}")

        doc_count = 0
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Data is an array of local plans
                    if isinstance(data, list):
                        for local_plan in data:
                            docs_added = self._extract_documents_only(local_plan)
                            doc_count += docs_added
                    elif isinstance(data, dict):
                        docs_added = self._extract_documents_only(data)
                        doc_count += docs_added

            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")
                continue

        logger.info(f"Loaded {doc_count} documents from source files")

    def _extract_documents_only(self, plan_data: Dict) -> int:
        """Extract documents from a plan without creating a local plan entry.

        Returns the count of documents added.
        """
        documents = plan_data.get('documents', [])
        if not isinstance(documents, list):
            return 0

        # We need to determine the plan reference to link documents to
        # Use the organisation to look up the plan reference
        org = plan_data.get('organisation', '')
        if not org:
            return 0

        # Try to find a matching plan we've already added
        plan_reference = None
        for plan in self.local_plans:
            # Check if this plan matches by organisation or name
            if org in plan.get('organisations', ''):
                plan_reference = plan['reference']
                break

        if not plan_reference:
            # If we can't find a matching plan, skip documents
            return 0

        # Extract year for document numbering
        year = plan_data.get('period-start-date', '') or plan_data.get('period-end-date', '')
        if isinstance(year, (int, float)):
            year = str(int(year))
        elif isinstance(year, str) and year.endswith('.0'):
            year = year[:-2]

        # Determine authority slug for counter
        org_name = plan_data.get('organisation-name', org)
        authority_slug = self._authority_to_slug(org_name)
        plan_key = (authority_slug, year)

        if plan_key not in self.authority_plan_counters:
            self.authority_plan_counters[plan_key] = 0

        # Process documents
        doc_count = 0
        for doc in documents:
            try:
                self.authority_plan_counters[plan_key] += 1
                doc_num = self.authority_plan_counters[plan_key]
                doc_reference = f"{plan_reference}-{doc_num}"

                doc_entry = {
                    'reference': doc_reference,
                    'name': doc.get('name', ''),
                    'description': doc.get('description', ''),
                    'local-plan': plan_reference,
                    'document-types': doc.get('document-type', ''),
                    'documentation-url': doc.get('documentation-url', ''),
                    'document-url': doc.get('document-url', ''),
                    'entry-date': datetime.now().strftime('%Y-%m-%d'),
                    'start-date': self._format_date(plan_data.get('adoption-date')) if plan_data.get('adoption-date') else '',
                    'end-date': self._format_date(plan_data.get('withdrawn-date')) if plan_data.get('withdrawn-date') else '',
                    'notes': doc.get('notes', ''),
                }

                self.local_plan_documents.append(doc_entry)
                doc_count += 1
            except Exception as e:
                logger.debug(f"Error processing document: {e}")

        return doc_count

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
                            self._process_source_local_plan(local_plan)
                    elif isinstance(data, dict):
                        self._process_source_local_plan(data)

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
        """Process a local plan entry from extracted local-plan JSON.

        Extracted data already has enriched fields like local-planning-authorities.
        Merges metadata from source files (document-url, documentation-url, local-plan-process).
        """
        # Skip excluded plan types
        if self._should_skip_plan(plan_data):
            return

        try:
            org = plan_data.get('organisation', '')
            organisation_name = plan_data.get('organisation-name', org)
            authority_slug = self._authority_to_slug(organisation_name)
            year = plan_data.get('period-start-date', '')
            if not year:
                year = plan_data.get('period-end-date', '')

            # Normalize year to string
            if isinstance(year, (int, float)):
                try:
                    year = str(int(year))
                except Exception:
                    year = str(year)
            elif isinstance(year, str) and year.endswith('.0'):
                year = year[:-2]

            # Determine if this is a joint plan
            organisations = plan_data.get('organisations', [])

            # If no organisations array in source, check if this plan matches a known joint plan by name
            if not organisations:
                plan_name = plan_data.get('name', '')
                if plan_name:
                    plan_slug = self._authority_to_slug(plan_name)
                    # Check if this plan name matches any known joint plan
                    if plan_slug in self.joint_plan_names:
                        organisations = self.joint_plan_names[plan_slug]
                        logger.debug(f"Auto-populated organisations from joint plan name: '{plan_name}' -> {organisations}")

            is_joint = organisations and isinstance(organisations, list) and len(organisations) > 1

            # Generate reference
            if is_joint:
                plan_name = plan_data.get('name', '')
                slug = self._authority_to_slug(plan_name)
                reference = slug if slug else ''
            else:
                authority_slug = self._authority_to_slug(organisation_name)
                if year:
                    reference = f"{authority_slug}-local-plan-{year}"
                else:
                    reference = f"{authority_slug}-local-plan"

            # Apply reference overrides
            if reference in self.REFERENCE_OVERRIDES:
                original_reference = reference
                reference = self.REFERENCE_OVERRIDES[reference]
                logger.debug(f"Applied reference override: {original_reference} → {reference}")

            # Build organisations string
            if organisations and isinstance(organisations, list):
                local_planning_authorities = ';'.join(organisations)
            else:
                local_planning_authorities = org if org else ''

            # Extract local-planning-authorities (E0x geography codes)
            lpa_codes = plan_data.get('local-planning-authorities', [])
            if isinstance(lpa_codes, list):
                lpa_codes_str = '-'.join(lpa_codes)
            else:
                lpa_codes_str = ''

            # Get metadata from source files if available
            # For joint plans, try to find metadata from any of the individual organisations
            source_meta = self.source_metadata.get(org, {})
            if not source_meta and is_joint:
                # For joint plans, look up by individual organisations
                for individual_org in organisations:
                    if individual_org in self.source_metadata:
                        source_meta = self.source_metadata[individual_org]
                        break

            local_plan_entry = {
                'reference': reference,
                'name': plan_data.get('name', ''),
                'dataset': 'local-plan',
                'period-start-date': self._format_date(plan_data.get('period-start-date', '')),
                'period-end-date': self._format_date(plan_data.get('period-end-date', '')),
                'organisations': local_planning_authorities,
                'local-planning-authorities': lpa_codes_str,
                'mineral-planning-authorities': plan_data.get('mineral-planning-authorities', ''),
                'waste-planning-authorities': plan_data.get('waste-planning-authorities', ''),
                'local-plan-process': source_meta.get('local-plan-process', ''),
                'documentation-url': source_meta.get('documentation-url', ''),
                'document-url': source_meta.get('document-url', ''),
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'start-date': plan_data.get('adoption-date'),
                'end-date': plan_data.get('withdrawn-date'),
                'notes': plan_data.get('notes', ''),
            }

            self.local_plans.append(local_plan_entry)

            # Add housing data if available
            if org in self.housing_data:
                # Use the reference we just created
                self._add_housing_data(reference, local_planning_authorities, org)

        except Exception as e:
            logger.error(f"Failed to process local plan from extracted data: {e}")

    def _process_source_local_plan(self, plan_data: Dict):
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

            # Only auto-populate organisations from joint plan mapping if the plan NAME matches a known joint plan
            # Do NOT auto-populate just because the organisation is part of a joint plan, as single-authority
            # plans might be from an authority that's part of a joint plan
            if not organisations:
                plan_name = plan_data.get('name', '')
                if plan_name:
                    plan_slug = self._authority_to_slug(plan_name)
                    # Check if this plan name matches any known joint plan
                    if plan_slug in self.joint_plan_names:
                        organisations = self.joint_plan_names[plan_slug]
                        logger.debug(f"Source plan '{plan_name}' (org={org}) auto-populated organisations from joint plan: {organisations}")

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

            # Track if this is a duplicate plan reference (handles joint plans in multiple source files)
            is_duplicate_plan = reference in self.processed_plan_references

            if not is_duplicate_plan:
                # Mark this reference as processed (only for first occurrence)
                self.processed_plan_references.add(reference)

            # Build local-planning-authorities string
            # For joint plans, use the organisations array; otherwise use single organisation

            # NOTE: organisations variable was already set above with joint plan lookup
            # Don't overwrite it here. Use the organisations that were already determined.

            # For extracted data (not source), check if this is a known joint plan from housing data
            # We only do this for extracted data, not source data, to avoid incorrectly marking
            # single-authority plans as joint plans
            # (This check will be done later in _process_extracted_local_plan if needed)

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

            # Extract local-planning-authorities (E0x geography codes)
            # Try to get from enrichment data first, fall back to source data
            lpa_codes = plan_data.get('local-planning-authorities', [])

            # Try to get enrichment data (includes geography codes)
            enrichment_data = self._get_enrichment_data(plan_data)
            if enrichment_data:
                enriched_lpa_codes = enrichment_data.get('local-planning-authorities', [])
                if enriched_lpa_codes:
                    lpa_codes = enriched_lpa_codes
                    logger.debug(f"Using enriched local-planning-authorities from extracted data")

            if isinstance(lpa_codes, list):
                lpa_codes_str = '-'.join(lpa_codes)
            else:
                lpa_codes_str = ''

            local_plan_entry = {
                'reference': reference,
                'name': plan_data.get('name', ''),
                'dataset': 'local-plan',
                'period-start-date': self._format_date(plan_data.get('period-start-date', '')),
                'period-end-date': self._format_date(plan_data.get('period-end-date', '')),
                'organisations': local_planning_authorities,
                'local-planning-authorities': lpa_codes_str,
                'mineral-planning-authorities': plan_data.get('mineral-planning-authorities', ''),
                'waste-planning-authorities': plan_data.get('waste-planning-authorities', ''),
                'local-plan-process': plan_data.get('local-plan-process', plan_data.get('status', '')),
                'required-housing': self._get_required_housing(plan_data),
                'documentation-url': plan_data.get('documentation-url', ''),
                'document-url': plan_data.get('document-url', ''),
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'start-date': plan_data.get('adoption-date'),
                'end-date': plan_data.get('withdrawn-date'),
                'notes': plan_data.get('notes', ''),
            }

            # Only add local plan entry and process documents if this is not a duplicate reference
            # (Joint plans appear in multiple source files but should only be added once to local_plans)
            if not is_duplicate_plan:
                self.local_plans.append(local_plan_entry)

                # Process documents and map endpoints to plan reference
                documents = plan_data.get('documents', [])
                if isinstance(documents, list):
                    for doc in documents:
                        # Map endpoint to plan reference for housing data lookup
                        endpoint = doc.get('endpoint', '')
                        if endpoint:
                            # Store endpoint -> plan reference mapping for housing data source tracking
                            if endpoint not in self.endpoint_to_plan_ref:
                                self.endpoint_to_plan_ref[endpoint] = reference
                                logger.debug(f"Mapped endpoint {endpoint} to plan reference {reference}")

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
            else:
                logger.debug(f"Skipping duplicate local plan entry for reference: {reference}")

            # Add housing data if available (do this even for duplicate plans so all organisations get housing data)
            if org in self.housing_data:
                self._add_housing_data(reference, local_planning_authorities, org)
            else:
                logger.debug(f"No housing data for org={org}, reference={reference}")

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
        """Add housing data entry for an organisation (only once per organisation-plan combo to avoid duplicates).

        For joint plans, uses the consolidated plan_reference for all individual authority entries.
        """
        try:
            # Skip if we've already added housing data for this organisation-plan combination
            org_plan_key = (org, plan_reference)
            if org_plan_key in self.organisations_with_housing:
                logger.debug(f"Skipping housing data for {org} under {plan_reference} (already processed)")
                return

            housing = self.housing_data.get(org, {})
            if not housing:
                return

            housing_numbers = housing.get('housing-numbers', '[]')

            # Parse housing numbers if it's a JSON string
            if isinstance(housing_numbers, str):
                try:
                    housing_numbers = json.loads(housing_numbers)
                except:
                    housing_numbers = []

            # For joint plans, determine if we should use a consolidated plan reference
            # First, check if we have a direct endpoint -> plan_reference mapping
            effective_plan_reference = plan_reference
            endpoint_hash = ''

            # Check if the housing data source has an endpoint, and if that endpoint maps to a plan
            if org in self.housing_data_source:
                endpoint_hash = self.housing_data_source[org].get('endpoint', '')
                if endpoint_hash and endpoint_hash in self.endpoint_to_plan_ref:
                    # Use the plan reference from the endpoint mapping
                    effective_plan_reference = self.endpoint_to_plan_ref[endpoint_hash]
                    logger.debug(f"Using plan reference from endpoint mapping for {org}: {effective_plan_reference}")

            # If not from endpoint, check if this organisation is part of a joint plan
            if effective_plan_reference == plan_reference and org in self.joint_plan_organisations:
                # This organisation is part of a joint plan
                # Find the local plan that matches all the authorities in this joint plan
                joint_authorities = self.joint_plan_organisations[org]
                authorities_set = set(joint_authorities)

                # Look through local plans to find one with exactly these authorities
                # Prioritize adopted plans over draft plans
                matching_plans = []
                for existing_plan in self.local_plans:
                    plan_orgs = set(o.strip() for o in existing_plan.get('organisations', '').split(';'))
                    if plan_orgs == authorities_set:
                        matching_plans.append(existing_plan)

                if matching_plans:
                    # Sort by adoption priority:
                    # 1. Plans with adoption date (adopted), sorted by date descending (most recent first)
                    # 2. Then plans without adoption date (draft), in original order
                    def sort_key(plan):
                        start_date = plan.get('start-date', '')
                        if start_date:
                            # Return (0, negative year) to sort adopted plans first, then by year descending
                            year_str = start_date[:4]
                            year = -int(year_str) if year_str.isdigit() else 0
                            return (0, year)
                        else:
                            # Return (1, 0) to sort draft plans after adopted plans
                            return (1, 0)

                    matching_plans.sort(key=sort_key)
                    selected_plan = matching_plans[0]

                    effective_plan_reference = selected_plan.get('reference', '')
                    logger.debug(f"Using consolidated plan {effective_plan_reference} for {org} (selected from {len(matching_plans)} matching plans)")

            # Create entry for each authority in housing-numbers array
            if isinstance(housing_numbers, list):
                # Check if this is a joint plan by looking it up in local_plans
                is_joint_from_lpa = ';' in lpa if lpa else False
                is_joint_from_plan = False

                # Also check the actual local plan to see if it's marked as joint
                for existing_plan in self.local_plans:
                    if existing_plan.get('reference', '') == effective_plan_reference:
                        plan_orgs = existing_plan.get('organisations', '')
                        if plan_orgs and ';' in plan_orgs:
                            is_joint_from_plan = True
                        break

                is_joint_housing = is_joint_from_lpa or is_joint_from_plan

                for num_entry in housing_numbers:
                    # Extract organisation from housing-numbers entry
                    lpa_code = num_entry.get('organisation', '')

                    # Skip aggregate joint-planning-authority entries (we only want individual local authorities)
                    # UNLESS this endpoint is in the exceptions list
                    if lpa_code.startswith(('joint-planning-authority:', 'local-planning-group:')):
                        if endpoint_hash not in self.JOINT_HOUSING_DATA_EXCEPTIONS:
                            logger.debug(f"Skipping aggregate entry: {lpa_code}")
                            continue
                        else:
                            logger.debug(f"Including exception aggregate entry: {lpa_code} (endpoint: {endpoint_hash})")

                    # Use the correct document reference from the main document mapping
                    # If not found or None, fallback to first document reference
                    housing_reference = self.plan_main_document_ref.get(
                        effective_plan_reference,
                        f"{effective_plan_reference}-1"
                    ) or f"{effective_plan_reference}-1"

                    # For joint plans with individual authority entries, append the authority code
                    # to make the reference unique
                    if is_joint_housing and lpa_code.startswith('local-authority:'):
                        # Extract just the code part (e.g., 'bab' from 'local-authority:BAB')
                        authority_code = lpa_code.split(':')[1].lower()
                        housing_reference = f"{housing_reference}-{authority_code}"

                    housing_entry = {
                        'reference': housing_reference,
                        'local-plan': effective_plan_reference,
                        'local-planning-authority': lpa_code,
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

                # Mark this organisation-plan combination as processed
                org_plan_key = (org, effective_plan_reference)
                self.organisations_with_housing.add(org_plan_key)

        except Exception as e:
            logger.error(f"Failed to add housing data for {org}: {e}")

    def _consolidate_joint_plan_housing_data(self, local_plans: List[Dict]):
        """Update housing data to use consolidated plan references for joint plans.

        For joint plans, all housing data entries from the individual authorities
        should reference the consolidated plan instead of their individual plans.
        """
        # Build a map of organisation codes to their consolidated plan reference
        org_to_plan = {}

        for plan in local_plans:
            organisations = plan.get('organisations', '')
            if not organisations:
                continue

            # If this plan has multiple organisations, it's a joint plan
            org_list = [o.strip() for o in organisations.split(';')]
            if len(org_list) > 1:
                # Map each organisation to the consolidated plan reference
                plan_ref = plan.get('reference', '')
                for org in org_list:
                    if org:
                        org_to_plan[org] = plan_ref
                        logger.debug(f"Mapped {org} -> {plan_ref} (joint plan)")

        # Update housing data entries for organisations in joint plans
        updated_count = 0
        for housing in self.local_plan_housing:
            lpa_code = housing.get('local-planning-authority', '')
            if lpa_code in org_to_plan:
                old_plan = housing.get('local-plan', '')
                new_plan = org_to_plan[lpa_code]
                if old_plan != new_plan:
                    housing['local-plan'] = new_plan
                    updated_count += 1
                    logger.debug(f"Updated housing data: {lpa_code} {old_plan} -> {new_plan}")

        if updated_count > 0:
            logger.info(f"Updated {updated_count} housing data entries to use consolidated joint plan references")

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

    def add_provisioned_lpa_placeholders(self):
        """Add a blank placeholder row to local_plans for each provisioned LPA.

        Fetches all LPAs provisioned for the local-plan dataset from the
        Datasette API. For each LPA, adds one placeholder row with reference
        '{lpa-slug}-new-local-plan' unless that reference already exists.

        Uses self.boundary_mapping (populated from local-plan-boundary.csv)
        to resolve the organisation CURIE to its E6 geography code. Falls back
        to looking up by authority name if CURIE lookup fails.
        """
        try:
            rows = fetch_provisioned_lpas()
        except Exception as e:
            logger.warning(f"Could not fetch provisioned LPAs; skipping placeholders: {e}")
            return

        added = 0
        skipped_duplicate = 0
        skipped_no_e6 = 0

        for row in rows:
            organisation = row.get('organisation', '').strip()
            organisation_label = row.get('organisation_label', '').strip()

            if not organisation:
                continue

            # Derive reference from the human-readable label (same slug logic as all other refs)
            lpa_slug = self._authority_to_slug(organisation_label)
            reference = f"{lpa_slug}-new-local-plan"

            # Deduplication: skip if this reference was already produced from source JSON files
            if reference in self.processed_plan_references:
                logger.debug(f"Skipping placeholder for {organisation_label!r}: reference {reference!r} already exists")
                skipped_duplicate += 1
                continue

            # Look up E6 geography code via the already-loaded boundary mapping (by CURIE)
            e6_code = self.boundary_mapping.get(organisation, '')

            # Fallback: search by authority name if CURIE lookup fails
            # (national parks and dev corps use wikidata-based CURIEs not in boundary_mapping)
            if not e6_code:
                e6_code = self._lookup_e6_code_by_name(organisation_label)

            if not e6_code:
                logger.debug(f"No E6 code found for {organisation_label!r}; placeholder will have blank local-planning-authorities")
                skipped_no_e6 += 1

            placeholder = {
                'reference': reference,
                'name': 'Emerging new local plan',
                'dataset': 'local-plan',
                'period-start-date': '',
                'period-end-date': '',
                'organisations': '',
                'local-plan-boundary': '',
                'local-planning-authorities': e6_code,
                'mineral-planning-authorities': '',
                'waste-planning-authorities': '',
                'local-plan-process': '',
                'required-housing': '',
                'documentation-url': '',
                'document-url': '',
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'start-date': '',
                'end-date': '',
                'notes': 'Placeholder to help the authority provide their data',
            }

            self.local_plans.append(placeholder)
            self.processed_plan_references.add(reference)
            added += 1

        logger.info(
            f"Added {added} placeholder rows "
            f"({skipped_duplicate} skipped as duplicates, "
            f"{skipped_no_e6} without E6 code)"
        )

    def _lookup_e6_code_by_name(self, authority_name: str) -> str:
        """Look up E6 code by authority name in local-plan-boundary.csv.

        Used as fallback for authorities with non-standard CURIEs (e.g., national parks,
        development corporations) that don't match the primary boundary_mapping.
        Handles variations like 'Broads Authority' vs 'The Broads Authority' and
        'Dartmoor National Park Authority' vs 'Dartmoor National Park'.
        """
        boundary_path = Path("dataset/local-plan-boundary.csv")
        if not boundary_path.exists():
            return ''

        # Normalize name for comparison
        normalized_input = authority_name.lower().strip()
        # Remove 'the ' prefix and 'authority' suffix for more flexible matching
        normalized_input = normalized_input.lstrip('the ').rstrip(' authority').strip()

        try:
            with open(boundary_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('reference'):
                        continue

                    # Parse CSV line handling quoted fields (e.g., "Bournemouth, Christchurch and Poole Council")
                    # Extract reference (before first comma) and name (between first and second comma)
                    if ',' not in line:
                        continue

                    parts = line.split(',', 1)
                    reference = parts[0].strip()

                    # Extract name field, handling quotes
                    rest = parts[1]
                    if rest.startswith('"'):
                        # Quoted field: find matching closing quote
                        end_quote = rest.find('"', 1)
                        if end_quote > 0:
                            name = rest[1:end_quote].strip()
                        else:
                            continue
                    else:
                        # Unquoted field: take up to next comma
                        name_end = rest.find(',')
                        if name_end > 0:
                            name = rest[:name_end].strip()
                        else:
                            name = rest.strip()

                    # Normalize boundary name the same way
                    normalized_boundary = name.lower().strip()
                    normalized_boundary = normalized_boundary.lstrip('the ').rstrip(' authority').strip()

                    # Exact match or fuzzy match by removing common words
                    if normalized_boundary == normalized_input:
                        return reference
        except Exception as e:
            logger.debug(f"Error looking up E6 code by name: {e}")

        return ''

    def write_csvs(self):
        """Write all three CSVs to output directory."""
        # Sort by reference before writing
        local_plans_sorted = sorted(self.local_plans, key=lambda x: x.get('reference') or '')

        # Populate missing geography codes from boundary mapping
        updated_count = 0
        for plan in local_plans_sorted:
            if not plan.get('local-planning-authorities', '').strip():
                # Missing geography code
                organisations = plan.get('organisations', '')
                geographies = self._lookup_geographies(organisations)

                if geographies:
                    plan['local-planning-authorities'] = geographies
                    updated_count += 1
                    logger.debug(f"Populated geographies for {plan.get('reference')}: {geographies}")

        if updated_count > 0:
            logger.info(f"Populated {updated_count} missing geography codes from boundary data")

        # Create local-plan-boundary column (dash-separated) and update local-planning-authorities (semicolon-separated)
        for plan in local_plans_sorted:
            lpa_codes = plan.get('local-planning-authorities', '')
            # local-plan-boundary keeps dashes, local-planning-authorities gets semicolons
            plan['local-plan-boundary'] = lpa_codes  # Keep dashes
            plan['local-planning-authorities'] = lpa_codes.replace('-', ';') if lpa_codes else ''  # Convert to semicolons

        self._write_csv('local-plan.csv', local_plans_sorted, [
            'reference', 'name', 'dataset', 'period-start-date', 'period-end-date',
            'organisations', 'local-plan-boundary', 'local-planning-authorities', 'mineral-planning-authorities',
            'waste-planning-authorities', 'local-plan-process', 'required-housing',
            'documentation-url', 'document-url', 'entry-date', 'start-date', 'end-date', 'notes'
        ])

        local_plan_documents_sorted = sorted(self.local_plan_documents, key=lambda x: x.get('reference') or '')

        self._write_csv('local-plan-document.csv', local_plan_documents_sorted, [
            'reference', 'name', 'description', 'local-plan', 'document-types',
            'documentation-url', 'document-url', 'entry-date', 'start-date', 'end-date', 'notes'
        ])

    def _deduplicate_housing_data(self, housing_data: List[Dict]) -> List[Dict]:
        """Remove duplicate housing data, preferring entries with authority codes appended.

        For joint plans, we want to keep entries with authority codes (e.g. plan-1-bab)
        rather than entries without codes (e.g. plan-1), as the codes ensure uniqueness.
        """
        # Group entries by (plan, authority) combination
        grouped = {}

        for entry in housing_data:
            plan = entry.get('local-plan', '')
            authority = entry.get('local-planning-authority', '')
            key = (plan, authority)

            if key not in grouped:
                grouped[key] = entry
            else:
                # If we already have an entry for this (plan, authority), prefer the one with authority code
                old_ref = grouped[key].get('reference', '')
                new_ref = entry.get('reference', '')

                # Check if one has authority code and the other doesn't
                authority_code = authority.split(':')[-1].lower() if ':' in authority else ''
                has_code_old = authority_code and old_ref.endswith(f"-{authority_code}")
                has_code_new = authority_code and new_ref.endswith(f"-{authority_code}")

                if has_code_new and not has_code_old:
                    # Prefer entry with code
                    grouped[key] = entry
                    logger.debug(f"Preferring reference with code for {plan}/{authority}: {new_ref} over {old_ref}")

        return list(grouped.values())

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


def fetch_provisioned_lpas() -> List[Dict]:
    """Fetch provisioned LPAs from the Datasette API.

    Returns a list of dicts, each with keys:
        'organisation'       - CURIE, e.g. 'local-authority:DUR'
        'organisation_label' - human label, e.g. 'Durham County Council'

    Raises an exception if the fetch fails.
    """
    url = (
        "https://datasette.planning.data.gov.uk/digital-land/provision.csv"
        "?_sort=organisation&role__exact=local-planning-authority"
        "&dataset__exact=local-plan&_labels=on&_size=max"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "local-plan-extractor/generate-csvs"})
    with urllib.request.urlopen(req, timeout=30) as response:
        content = response.read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def main():
    parser = argparse.ArgumentParser(
        description='Generate local plan CSVs from source data with enrichment from extracted local plans',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate all CSVs for all local plans
  python bin/generate-csvs.py

  # Generate CSVs for specific LPAs only
  python bin/generate-csvs.py --lpa PEN,BOT,SHO

  # Specify custom output directory
  python bin/generate-csvs.py --output-dir ./dataset/

  # Combine options
  python bin/generate-csvs.py --lpa BRO,NOW,SNO --output-dir ./output/
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

    parser.add_argument(
        '--no-placeholders',
        action='store_true',
        help='Skip adding blank placeholder rows for provisioned LPAs (useful for offline/CI use)',
        default=False
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

    # Load local plans from source/ (primary source - includes both draft and adopted plans)
    # Enrichment data from local-plan/ is automatically merged during processing
    generator.load_source_json_files(lpa_codes=lpa_codes)

    # Add blank placeholder rows for all provisioned LPAs (unless skipped)
    if not args.no_placeholders:
        generator.add_provisioned_lpa_placeholders()

    # Write CSVs
    generator.write_csvs()

    logger.info("✓ CSV generation complete")


if __name__ == '__main__':
    main()
