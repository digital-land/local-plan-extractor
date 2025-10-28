#!/usr/bin/env python3
"""
Replace " and " with ", " in organisation-name fields for joint plans.
"""

import json
from pathlib import Path

def fix_organisation_name(json_path):
    """Replace ' and ' with ', ' in organisation-name field."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Check if organisation-name contains " and "
    org_name = data.get('organisation-name', '')
    if ' and ' not in org_name:
        return False, "No ' and ' found"

    # Replace " and " with ", "
    new_org_name = org_name.replace(' and ', ', ')
    data['organisation-name'] = new_org_name

    # Write back to file
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')

    return True, f"Changed from:\n  {org_name}\nto:\n  {new_org_name}"

def main():
    local_plan_dir = Path('local-plan')

    print("Scanning for joint plans with ' and ' in organisation-name...\n")

    updated = 0
    skipped = 0

    for json_path in sorted(local_plan_dir.glob('*.json')):
        modified, message = fix_organisation_name(json_path)

        if modified:
            print(f"✓ {json_path.name}")
            print(f"  {message}\n")
            updated += 1
        else:
            skipped += 1

    print(f"Summary:")
    print(f"  Updated: {updated}")
    print(f"  Skipped: {skipped}")

if __name__ == '__main__':
    main()
