#!/usr/bin/env python3
"""
Script to add local-planning-authorities field to all JSON files in local-plan directory.

For single-authority plans:
  - Derives local-planning-authorities from the local-plan-boundary field by splitting on hyphens.

For joint-authority plans (those with organisations array):
  - Looks up organisations in local-planning-group-boundary.csv
  - Populates both local-plan-boundary and local-planning-authorities fields
"""

import json
import csv
import sys
from pathlib import Path
from collections import OrderedDict


def load_boundary_data(boundary_csv_path):
    """Load the local planning group boundary CSV and create a lookup table.

    Only reads the first 5 columns to avoid large geometry field issues.
    """
    boundary_lookup = {}

    try:
        with open(boundary_csv_path, 'r', encoding='utf-8') as f:
            # Read line by line and parse only the first few columns
            lines = f.readlines()
            if not lines:
                return boundary_lookup

            # Parse header
            header = lines[0].strip().split(',')
            ref_idx = header.index('reference') if 'reference' in header else 0
            org_idx = header.index('organisations') if 'organisations' in header else 3
            lpa_idx = header.index('local-planning-authorities') if 'local-planning-authorities' in header else 4

            # Parse data rows
            for line in lines[1:]:
                # Split only on the first few commas to avoid geometry field
                parts = line.strip().split(',', max(ref_idx, org_idx, lpa_idx) + 1)

                if len(parts) > lpa_idx:
                    reference = parts[ref_idx].strip('"').strip()
                    organisations = parts[org_idx].strip('"').strip()
                    lpa_string = parts[lpa_idx].strip('"').strip()

                    if organisations:
                        # Normalize the organisations to a sorted tuple for consistent matching
                        org_codes = [code.strip() for code in organisations.split(';')]
                        org_key = tuple(sorted(org_codes))

                        # Parse local-planning-authorities
                        lpa_codes = [code.strip() for code in lpa_string.split(';')] if lpa_string else []

                        boundary_lookup[org_key] = {
                            'boundary': reference,
                            'authorities': lpa_codes
                        }
    except FileNotFoundError:
        print(f"  ⚠ Warning: Boundary CSV not found - joint plans will be skipped", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠ Warning: Error reading boundary CSV: {e}", file=sys.stderr)

    return boundary_lookup


def process_json_file(file_path, boundary_lookup):
    """Add local-planning-authorities field to a JSON file"""

    print(f"Processing: {file_path.name}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f, object_pairs_hook=OrderedDict)

    # Check if this is a joint-authority plan (has organisations array)
    organisations = data.get("organisations")
    if organisations and isinstance(organisations, list):
        # Joint-authority plan - try to populate from boundary CSV
        has_geography = "local-plan-boundary" in data and "local-planning-authorities" in data
        has_org_field = "organisation" in data

        # Skip if it already has everything
        if has_geography and has_org_field:
            print(f"  ℹ Already has organisation field and geography codes")
            return False

        # Try to find matching boundary data
        org_key = tuple(sorted(organisations))
        if org_key in boundary_lookup:
            boundary_info = boundary_lookup[org_key]

            # Create joint-planning-authority field if not present
            if not has_org_field:
                # Extract just the authority codes from the full identifiers
                # e.g., "local-authority:BRO" -> "BRO"
                codes = []
                for org in organisations:
                    # Take the part after the last colon
                    code = org.split(":")[-1]
                    codes.append(code)
                joint_auth_code = "-".join(codes)
                data["organisation"] = f"joint-planning-authority:{joint_auth_code}"

            # Create new ordered dict with fields inserted after adoption-date or organisations
            new_data = OrderedDict()
            inserted = False

            for key, value in data.items():
                new_data[key] = value
                # Insert geography codes after adoption-date or organisations
                if not inserted and key in ("adoption-date", "organisations"):
                    if key == "adoption-date" or (key == "organisations" and "adoption-date" not in data):
                        if not has_geography:
                            new_data["local-plan-boundary"] = boundary_info['boundary']
                            new_data["local-planning-authorities"] = boundary_info['authorities']
                        inserted = True

            # Write back to file if we made changes
            changed = not has_org_field or not has_geography
            if changed:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(new_data, f, indent=2, ensure_ascii=False)
                    f.write("\n")  # Add trailing newline

                if not has_org_field and not has_geography:
                    print(f"  ✓ Added organisation field and geography codes from boundary lookup")
                elif not has_org_field:
                    print(f"  ✓ Added organisation field")
                else:
                    print(f"  ✓ Added geography codes")
            return changed
        else:
            print(f"  ⚠ Skipping - no matching entry in boundary CSV for organisations")
            return False

    # Single-authority plan - extract from local-plan-boundary
    if "local-plan-boundary" not in data:
        print(f"  ⚠ Skipping - no local-plan-boundary field")
        return False

    # Check if already has local-planning-authorities
    if "local-planning-authorities" in data:
        print(f"  ℹ Already has local-planning-authorities field")
        return False

    boundary = data["local-plan-boundary"]

    if not boundary:
        print(f"  ⚠ Skipping - local-plan-boundary is empty")
        return False

    # Split the boundary by hyphens to get individual codes
    lpa_codes = boundary.split("-")

    # Create new ordered dict with the field inserted after local-plan-boundary
    new_data = OrderedDict()
    for key, value in data.items():
        new_data[key] = value
        if key == "local-plan-boundary":
            new_data["local-planning-authorities"] = lpa_codes

    # Write back to file with pretty formatting
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
        f.write("\n")  # Add trailing newline

    print(f"  ✓ Added local-planning-authorities: {len(lpa_codes)} authorities")
    return True


def main():
    # Set up paths
    repo_root = Path(__file__).parent.parent
    local_plan_dir = repo_root / "local-plan"
    boundary_csv = repo_root / "dataset" / "local-planning-group-boundary.csv"

    if not local_plan_dir.exists():
        print(f"Error: Directory not found: {local_plan_dir}")
        sys.exit(1)

    # Load boundary data for joint-authority plans
    print("Loading boundary data for joint-authority plans...")
    boundary_lookup = load_boundary_data(boundary_csv)
    print(f"  Found {len(boundary_lookup)} geography mappings\n")

    json_files = sorted(local_plan_dir.glob("*.json"))

    if not json_files:
        print(f"No JSON files found in {local_plan_dir}")
        sys.exit(0)

    print(f"Found {len(json_files)} JSON files\n")

    updated_count = 0
    skipped_count = 0

    for json_file in json_files:
        try:
            if process_json_file(json_file, boundary_lookup):
                updated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  ✗ Error processing {json_file.name}: {e}")
            skipped_count += 1
        print()

    print("=" * 60)
    print(f"Summary:")
    print(f"  Updated: {updated_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Total:   {len(json_files)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
