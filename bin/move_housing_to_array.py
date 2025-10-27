#!/usr/bin/env python3
"""
Move top-level housing fields into housing-numbers array for single authority plans.
"""

import json
from pathlib import Path

# Housing fields to move
HOUSING_FIELDS = [
    'required-housing',
    'annual-required-housing',
    'allocated-housing',
    'committed-housing',
    'windfall-housing',
    'broad-locations-housing',
]

def move_housing_to_array(json_path):
    """Move top-level housing fields into housing-numbers array."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Check if there are top-level housing fields
    has_top_level = any(field in data for field in HOUSING_FIELDS)

    if not has_top_level:
        return False, "No top-level housing fields"

    # Check if housing-numbers is empty or missing
    if data.get('housing-numbers') and len(data['housing-numbers']) > 0:
        return False, "housing-numbers array already populated"

    # Create housing-numbers entry
    housing_entry = {}

    # Add organisation info if available
    if 'organisation-name' in data:
        housing_entry['organisation-name'] = data['organisation-name']
    if 'organisation' in data:
        housing_entry['organisation'] = data['organisation']

    # Move housing fields
    for field in HOUSING_FIELDS:
        if field in data:
            housing_entry[field] = data[field]

    # Move pages and notes if present
    if 'pages' in data:
        housing_entry['pages'] = data['pages']
    if 'notes' in data:
        housing_entry['notes'] = data['notes']

    # Create new data dict with correct field order
    new_data = {}

    # Keep fields in order, adding housing-numbers array
    for key, value in data.items():
        if key in HOUSING_FIELDS or key in ['pages', 'notes']:
            # Skip these - they'll go in the array
            continue
        elif key == 'housing-numbers':
            # Replace with new array
            new_data[key] = [housing_entry]
        else:
            new_data[key] = value

    # Write back to file
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
        f.write('\n')

    return True, "Moved housing fields to array"

def main():
    local_plan_dir = Path('local-plan')

    print("Scanning for plans with top-level housing fields...\n")

    updated = 0
    skipped = 0

    for json_path in sorted(local_plan_dir.glob('*.json')):
        modified, message = move_housing_to_array(json_path)

        if modified:
            print(f"✓ {json_path.name}: {message}")
            updated += 1
        else:
            print(f"  {json_path.name}: {message}")
            skipped += 1

    print(f"\nSummary:")
    print(f"  Updated: {updated}")
    print(f"  Skipped: {skipped}")

if __name__ == '__main__':
    main()
