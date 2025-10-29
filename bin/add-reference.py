#!/usr/bin/env python3
"""
Add reference field to local plans in existing source JSON files.

For each local plan, adds a "reference" field after the "organisation-name" field.
The reference format is: LP-[ORG-REF]-[YEAR]
Where:
- ORG-REF is the reference part of the organisation CURIE (e.g., "DAC" from "local-authority:DAC")
- YEAR is the year value from the plan
"""

import json
from pathlib import Path
from collections import OrderedDict


def extract_org_ref(organisation):
    """Extract the reference part from an organisation CURIE.

    Args:
        organisation: Organisation CURIE (e.g., "local-authority:DAC")

    Returns:
        Reference part (e.g., "DAC")
    """
    if ':' in organisation:
        return organisation.split(':')[1]
    return organisation


def create_reference(organisation, year):
    """Create a reference for a local plan.

    Args:
        organisation: Organisation CURIE (e.g., "local-authority:DAC")
        year: Year value

    Returns:
        Reference string (e.g., "LP-DAC-2013")
    """
    org_ref = extract_org_ref(organisation)
    return f"LP-{org_ref}-{year}"


def add_reference_to_plans(data):
    """Add reference field to each local plan in the data array."""
    # Track references to ensure uniqueness
    seen_references = {}

    for plan in data:
        organisation = plan.get('organisation', '')
        year = plan.get('year', '')

        # Skip if no year
        if not year:
            print(f"Warning: No year found for {organisation}, skipping reference")
            continue

        # Create base reference
        base_reference = create_reference(organisation, year)
        reference = base_reference

        # Handle duplicates by adding suffix
        if reference in seen_references:
            suffix = 2
            while f"{base_reference}-{suffix}" in seen_references:
                suffix += 1
            reference = f"{base_reference}-{suffix}"

        seen_references[reference] = True

        # Create a new ordered dict to maintain field order
        new_plan = OrderedDict()
        for key, value in plan.items():
            new_plan[key] = value
            # Insert reference after organisation-name
            if key == 'organisation-name' and 'reference' not in plan:
                new_plan['reference'] = reference

        # Update the plan with the new ordered dict
        plan.clear()
        plan.update(new_plan)

    return data


def main():
    source_dir = Path("source")

    # Find all local-authority JSON files
    json_files = sorted(source_dir.glob("local-authority:*.json"))

    print(f"Found {len(json_files)} source files to update")

    for json_file in json_files:
        print(f"\nProcessing {json_file.name}...", end=" ")

        try:
            # Read the file
            with open(json_file, 'r') as f:
                data = json.load(f)

            # Add reference fields
            updated_data = add_reference_to_plans(data)

            # Write back to file
            with open(json_file, 'w') as f:
                json.dump(updated_data, f, indent=2)

            print("✓ Updated")

        except Exception as e:
            print(f"✗ Error: {e}")

    print("\n✓ All files processed")


if __name__ == "__main__":
    main()
