#!/usr/bin/env python3

import csv
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)


def load_organisation_mappings(org_csv_path):
    """Load mappings from LPA code to organisation CURIE and name."""
    org_mapping = {}
    with open(org_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lpa = row.get("local-planning-authority", "").strip()
            organisation = row.get("organisation", "").strip()
            name = row.get("name", "").strip()
            if lpa and organisation:
                org_mapping[lpa] = {
                    "organisation": organisation,
                    "name": name
                }
    return org_mapping


def main():
    # File paths
    org_csv = Path("var/cache/organisation.csv")
    lpa_csv = Path("var/cache/local-planning-authority.csv")
    group_boundary_csv = Path("dataset/local-planning-group-boundary.csv")
    lpa_boundary_csv = Path("dataset/local-plan-boundary.csv")
    combined_boundary_csv = Path("dataset/local-plan-boundary.csv")

    # Load organisation mappings
    print("Loading organisation mappings...", file=sys.stderr)
    org_mapping = load_organisation_mappings(org_csv)

    # Process individual LPAs
    print("Processing individual local planning authorities...", file=sys.stderr)
    lpa_boundaries = []

    with open(lpa_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("reference", "").strip()
            lpa_name = row.get("name", "").strip()
            geometry = row.get("geometry", "").strip()

            if not reference or not geometry:
                continue

            # Look up organisation and name from mapping
            mapping = org_mapping.get(reference)
            if mapping:
                organisation = mapping["organisation"]
                name = mapping["name"] or lpa_name
            else:
                # For special authorities like National Parks, create organisation from name
                organisation = f"local-planning-authority:{reference}"
                name = lpa_name
                print(f"Info: Created organisation for {reference} ({name})", file=sys.stderr)

            # Remove ' LPA' suffix from name
            name = name.replace(" LPA", "")

            lpa_boundaries.append({
                "reference": reference,
                "name": name,
                "organisation": organisation,
                "organisations": organisation,
                "local-planning-authorities": reference,
                "geometry": geometry,
            })

    # Write LPA boundaries
    lpa_boundary_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(lpa_boundary_csv, "w", newline="") as f:
        fieldnames = [
            "reference",
            "name",
            "organisation",
            "organisations",
            "local-planning-authorities",
            "geometry",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for boundary in sorted(lpa_boundaries, key=lambda x: x["reference"]):
            writer.writerow(boundary)

    print(f"Generated {len(lpa_boundaries)} local planning authority boundaries in {lpa_boundary_csv}", file=sys.stderr)

    # Combine group and LPA boundaries (LPAs first, then groups)
    print("Combining group and LPA boundaries...", file=sys.stderr)
    all_boundaries = []

    # Add LPA boundaries first
    all_boundaries.extend(lpa_boundaries)

    # Then add group boundaries at the bottom
    with open(group_boundary_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_boundaries.append(row)

    print(f"Loaded {len(lpa_boundaries)} LPA boundaries and 24 group boundaries", file=sys.stderr)

    # Write combined boundaries
    combined_boundary_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(combined_boundary_csv, "w", newline="") as f:
        fieldnames = [
            "reference",
            "name",
            "organisation",
            "organisations",
            "local-planning-authorities",
            "geometry",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for boundary in all_boundaries:
            writer.writerow(boundary)

    print(f"Generated {len(all_boundaries)} combined boundaries in {combined_boundary_csv}", file=sys.stderr)

if __name__ == "__main__":
    main()
