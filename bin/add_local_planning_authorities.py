#!/usr/bin/env python3
"""
Script to add local-planning-authorities field to all JSON files in local-plan directory.
This field is derived from the local-plan-boundary field by splitting on hyphens.
"""

import json
import sys
from pathlib import Path
from collections import OrderedDict


def process_json_file(file_path):
    """Add local-planning-authorities field to a JSON file"""

    print(f"Processing: {file_path.name}")

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f, object_pairs_hook=OrderedDict)

    # Check if local-plan-boundary exists
    if 'local-plan-boundary' not in data:
        print(f"  ⚠ Skipping - no local-plan-boundary field")
        return False

    # Check if already has local-planning-authorities
    if 'local-planning-authorities' in data:
        print(f"  ℹ Already has local-planning-authorities field")
        return False

    boundary = data['local-plan-boundary']

    if not boundary:
        print(f"  ⚠ Skipping - local-plan-boundary is empty")
        return False

    # Split the boundary by hyphens to get individual codes
    lpa_codes = boundary.split('-')

    # Create new ordered dict with the field inserted after local-plan-boundary
    new_data = OrderedDict()
    for key, value in data.items():
        new_data[key] = value
        if key == 'local-plan-boundary':
            new_data['local-planning-authorities'] = lpa_codes

    # Write back to file with pretty formatting
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
        f.write('\n')  # Add trailing newline

    print(f"  ✓ Added local-planning-authorities: {len(lpa_codes)} authorities")
    return True


def main():
    # Find all JSON files in local-plan directory
    local_plan_dir = Path(__file__).parent.parent / 'local-plan'

    if not local_plan_dir.exists():
        print(f"Error: Directory not found: {local_plan_dir}")
        sys.exit(1)

    json_files = sorted(local_plan_dir.glob('*.json'))

    if not json_files:
        print(f"No JSON files found in {local_plan_dir}")
        sys.exit(0)

    print(f"Found {len(json_files)} JSON files\n")

    updated_count = 0
    skipped_count = 0

    for json_file in json_files:
        try:
            if process_json_file(json_file):
                updated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  ✗ Error processing {json_file.name}: {e}")
            skipped_count += 1
        print()

    print("="*60)
    print(f"Summary:")
    print(f"  Updated: {updated_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Total:   {len(json_files)}")
    print("="*60)


if __name__ == '__main__':
    main()
