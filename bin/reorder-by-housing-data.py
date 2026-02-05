#!/usr/bin/env python3
"""
Reorder source JSON files so plans with housing data come first.
Processes the 58 authorities with 2 duplicate housing entries.
"""

import json
from pathlib import Path
from typing import List, Dict, Set, Tuple

def get_endpoints_with_housing() -> Set[str]:
    """Get all endpoints that have housing data in local-plan/ files."""
    local_plan_dir = Path('/Users/sianteesdale/Documents/GitHub/local-plan-extractor/local-plan')
    endpoints_with_housing = set()

    for json_file in local_plan_dir.glob('*.json'):
        with open(json_file, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, dict) and data.get('housing-numbers'):
                    endpoint = data.get('authority')
                    if endpoint:
                        endpoints_with_housing.add(endpoint)
            except json.JSONDecodeError:
                pass

    return endpoints_with_housing

def reorder_source_file(file_path: Path, endpoints_with_housing: Set[str]) -> Tuple[bool, str]:
    """
    Reorder source file to move plans with housing data to top.
    Strategy:
    1. Plans with endpoints that have housing data in local-plan/ folder (highest priority)
    2. Plans with latest adoption date (secondary sort)
    Returns (was_modified, message)
    """
    with open(file_path, 'r') as f:
        data = json.load(f)

    if not isinstance(data, list) or len(data) < 2:
        return False, f"{file_path.name}: Not a list or < 2 entries"

    # Get original order
    original_refs = [p.get('reference', '?') for p in data]

    # Classify plans: with housing data vs without
    with_housing = []
    without_housing = []

    for plan in data:
        has_housing = False
        for doc in plan.get('documents', []):
            endpoint = doc.get('endpoint')
            if endpoint in endpoints_with_housing:
                has_housing = True
                break

        if has_housing:
            with_housing.append(plan)
        else:
            without_housing.append(plan)

    # Sort each group by adoption date (most recent first)
    def adoption_sort_key(plan):
        adoption_date = plan.get('adoption-date', '')
        if adoption_date:
            # Return negative to sort descending (most recent first)
            return -float(adoption_date.replace('-', '')) if adoption_date else 0
        else:
            # Plans without adoption date sort to end of their group
            return float('inf')

    with_housing.sort(key=adoption_sort_key)
    without_housing.sort(key=adoption_sort_key)

    # Reorder: housing plans first (sorted by adoption date), then rest
    sorted_data = with_housing + without_housing

    # Get new order
    new_refs = [p.get('reference', '?') for p in sorted_data]

    # Check if anything changed
    if original_refs == new_refs:
        return False, f"{file_path.name}: Already optimal"

    # Write back
    with open(file_path, 'w') as f:
        json.dump(sorted_data, f, indent=2)

    return True, f"{file_path.name}: {original_refs} → {new_refs}"

AUTHORITIES_TO_FIX = [
    "local-authority:BRO",
    "local-authority:CRW",
    "local-authority:GRT",
    "local-authority:GRY",
    "local-authority:HAS",
    "local-authority:HER",
    "local-authority:HNS",
    "local-authority:HOR",
    "local-authority:HRY",
    "local-authority:HUN",
    "local-authority:IOW",
    "local-authority:IPS",
    "local-authority:KTT",
    "local-authority:LCE",
    "local-authority:LIF",
    "local-authority:MDB",
    "local-authority:NEC",
    "local-authority:NLN",
    "local-authority:NSM",
    "local-authority:PEN",
    "local-authority:RUG",
    "local-authority:RUT",
    "local-authority:SCA",
    "local-authority:SDE",
    "local-authority:SEV",
    "local-authority:SGC",
    "local-authority:SHF",
    "local-authority:SHR",
    "local-authority:SKP",
    "local-authority:SOS",
    "local-authority:SPE",
    "local-authority:STY",
    "local-authority:SUR",
    "local-authority:THA",
    "local-authority:TON",
    "local-authority:WAE",
    "local-authority:WND",
    "local-authority:WOI",
    "local-authority:WOK",
    "national-park-authority:Q72617988"
]

def main():
    source_dir = Path('/Users/sianteesdale/Documents/GitHub/local-plan-extractor/source')

    print("Scanning for endpoints with housing data...")
    endpoints_with_housing = get_endpoints_with_housing()
    print(f"Found {len(endpoints_with_housing)} endpoints with housing data\n")

    modified_count = 0
    not_modified_count = 0
    not_found_count = 0

    for authority_id in AUTHORITIES_TO_FIX:
        filename = f"{authority_id}.json"
        file_path = source_dir / filename

        if not file_path.exists():
            print(f"NOT FOUND: {filename}")
            not_found_count += 1
            continue

        was_modified, message = reorder_source_file(file_path, endpoints_with_housing)

        if was_modified:
            print(f"✓ {message}")
            modified_count += 1
        else:
            print(f"  {message}")
            not_modified_count += 1

    print(f"\n=== Summary ===")
    print(f"Modified: {modified_count}")
    print(f"Unchanged: {not_modified_count}")
    print(f"Not found: {not_found_count}")
    print(f"\nNext step: Run 'python bin/generate-csvs.py' to regenerate CSVs")

if __name__ == '__main__':
    main()
