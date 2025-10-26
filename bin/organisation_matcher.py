"""
Organisation name matching utility.

Loads organisation data from CSV and provides matching functionality
to map organisation names to their codes.
"""

import csv
import os
import sys
from typing import Dict


class OrganisationMatcher:
    """Match organisation names to their official codes from CSV."""

    def __init__(self, csv_path: str = "var/cache/organisation.csv"):
        """Initialize with path to organisation CSV file.

        Args:
            csv_path: Path to organisation.csv file
        """
        self.organisations = self._load_organisations(csv_path)

    def _load_organisations(self, csv_path: str) -> Dict[str, str]:
        """Load organisation names and codes from CSV file.

        Args:
            csv_path: Path to CSV file

        Returns:
            Dictionary mapping lowercase organisation names to codes
        """
        organisations = {}
        try:
            if not os.path.exists(csv_path):
                print(f"Warning: Organisation CSV not found at {csv_path}", file=sys.stderr)
                return organisations

            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)  # Skip header

                # Find column indices
                name_idx = header.index('name') if 'name' in header else 14
                org_idx = header.index('organisation') if 'organisation' in header else 19

                for row in reader:
                    if len(row) > max(name_idx, org_idx):
                        name = row[name_idx].strip()
                        org_code = row[org_idx].strip()
                        if name and org_code:
                            organisations[name.lower()] = org_code
        except Exception as e:
            print(f"Warning: Could not load organisations from {csv_path}: {e}", file=sys.stderr)

        return organisations

    def match(self, organisation_name: str) -> str:
        """Match an organisation name to its code from the CSV.

        Uses exact matching and common variations only - no fuzzy matching.
        Only returns a code when there is a confident match.

        Args:
            organisation_name: The organisation name to match

        Returns:
            Organisation code if a confident match is found, empty string otherwise

        Examples:
            >>> matcher = OrganisationMatcher()
            >>> matcher.match("Bolton Council")
            'local-authority:BOL'
            >>> matcher.match("Manchester City Council")
            'local-authority:MAN'
            >>> matcher.match("Unknown Council")
            ''
        """
        if not organisation_name or not self.organisations:
            return ""

        search_name = organisation_name.strip().lower()

        # Try exact match first
        if search_name in self.organisations:
            return self.organisations[search_name]

        # Try common variations
        variations = [
            search_name,
            f"{search_name} council",
            f"{search_name} metropolitan borough council",
            f"{search_name} district council",
            f"{search_name} borough council",
            f"{search_name} city council",
            f"{search_name} county council",
        ]

        # Also try removing "council" if it's already in the name
        if "council" in search_name:
            base_name = search_name.replace(" council", "").strip()
            variations.extend([
                base_name,
                f"{base_name} metropolitan borough council",
                f"{base_name} district council",
                f"{base_name} borough council",
                f"{base_name} city council",
                f"{base_name} county council",
            ])

        for variation in variations:
            if variation in self.organisations:
                return self.organisations[variation]

        # No confident match found
        return ""

    def match_all(self, names: list) -> Dict[str, str]:
        """Match multiple organisation names at once.

        Args:
            names: List of organisation names to match

        Returns:
            Dictionary mapping original names to their organisation codes
        """
        return {name: self.match(name) for name in names}
