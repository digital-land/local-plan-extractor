#!/usr/bin/env python3
"""
Replace " and " with ", " in organisation-name fields for joint plans.
"""

import json
from pathlib import Path


def fix_organisation_name(json_path):
    """Replace ' and ' with ', ' in organisation-name fields."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    modified = False
    changes = []

    # Check top-level organisation-name
    org_name = data.get("organisation-name", "")
    if " and " in org_name:
        new_org_name = org_name.replace(" and ", ", ")
        data["organisation-name"] = new_org_name
        changes.append(f"Top-level organisation-name")
        modified = True

    # Check housing-numbers array entries
    if "housing-numbers" in data and isinstance(data["housing-numbers"], list):
        for i, entry in enumerate(data["housing-numbers"]):
            entry_org_name = entry.get("organisation-name", "")
            if " and " in entry_org_name:
                new_entry_org_name = entry_org_name.replace(" and ", ", ")
                entry["organisation-name"] = new_entry_org_name
                # Check if this is a joint planning authority entry
                if entry.get("organisation", "").startswith(
                    "joint-planning-authority:"
                ):
                    changes.append(f"Joint authority entry in housing-numbers")
                else:
                    changes.append(f"Entry {i} in housing-numbers")
                modified = True

    if not modified:
        return False, "No ' and ' found"

    # Write back to file
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return True, f"Updated: {', '.join(changes)}"


def main():
    local_plan_dir = Path("local-plan")

    print("Scanning for joint plans with ' and ' in organisation-name...\n")

    updated = 0
    skipped = 0

    for json_path in sorted(local_plan_dir.glob("*.json")):
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


if __name__ == "__main__":
    main()
