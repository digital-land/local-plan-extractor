#!/usr/bin/env python3

import csv
import sys
from pathlib import Path
from collections import defaultdict
import re

# Increase CSV field size limit for large geometry fields
csv.field_size_limit(sys.maxsize)


def load_organisation_mappings(org_csv_path):
    """Load mappings from organisation CURIE to LPA and LAD codes."""
    lpa_mapping = {}
    lad_mapping = {}
    with open(org_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            org = row.get("organisation", "").strip()
            lpa = row.get("local-planning-authority", "").strip()
            lad = row.get("local-authority-district", "").strip()
            if org:
                if lpa:
                    lpa_mapping[org] = lpa
                if lad:
                    lad_mapping[org] = lad
    return lpa_mapping, lad_mapping


def load_geometries_from_csv(csv_path):
    """Load geometries by reference code from a CSV file."""
    geometries = {}
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ref = row.get("reference", "").strip()
            geometry = row.get("geometry", "").strip()
            if ref and geometry:
                geometries[ref] = geometry
    return geometries


def extract_polygons_from_multipolygon(wkt):
    """Extract individual polygon coordinates from MULTIPOLYGON WKT."""
    if not wkt or not wkt.startswith("MULTIPOLYGON"):
        return []

    # Extract the content between outer parentheses
    # MULTIPOLYGON (((coords)), ((coords)))
    match = re.match(r"MULTIPOLYGON\s*\(\s*(.+)\s*\)\s*$", wkt)
    if not match:
        return []

    content = match.group(1)
    polygons = []

    # Parse nested parentheses to extract individual polygons
    depth = 0
    current_polygon = []
    i = 0

    while i < len(content):
        char = content[i]
        if char == "(":
            depth += 1
            current_polygon.append(char)
        elif char == ")":
            depth -= 1
            current_polygon.append(char)
            # When we close a top-level polygon (depth becomes 0)
            if depth == 0 and current_polygon:
                polygon_str = "".join(current_polygon).strip()
                if polygon_str:
                    polygons.append(polygon_str)
                current_polygon = []
        elif depth > 0:
            current_polygon.append(char)
        i += 1

    return polygons


def combine_geometries(geometries):
    """Combine multiple MULTIPOLYGON geometries into one."""
    all_polygons = []

    for geom in geometries:
        if not geom:
            continue
        polygons = extract_polygons_from_multipolygon(geom)
        all_polygons.extend(polygons)

    if not all_polygons:
        return ""

    # Combine into single MULTIPOLYGON
    return f"MULTIPOLYGON ({', '.join(all_polygons)})"


def main():
    # File paths
    group_csv = Path("dataset/local-planning-group.csv")
    org_csv = Path("var/cache/organisation.csv")
    lpa_csv = Path("var/cache/local-planning-authority.csv")
    lad_csv = Path("var/cache/local-authority-district.csv")
    output_csv = Path("dataset/local-planning-group-boundary.csv")

    # Load mappings
    print("Loading organisation mappings...", file=sys.stderr)
    org_to_lpa, org_to_lad = load_organisation_mappings(org_csv)

    print("Loading LPA geometries...", file=sys.stderr)
    lpa_geometries = load_geometries_from_csv(lpa_csv)

    print("Loading LAD geometries...", file=sys.stderr)
    lad_geometries = load_geometries_from_csv(lad_csv)

    # Process groups
    print("Processing local planning groups...", file=sys.stderr)
    boundaries = []

    with open(group_csv, "r") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, 1):
            if row_num > 24:
                break
            group_reference = row["reference"]
            group_name = row["name"]
            organisations = row["organisations"]

            # Parse organisations
            org_list = [org.strip() for org in organisations.split(";")]

            # Get LPA codes for each organisation
            lpa_codes = []
            geometries = []

            for org in org_list:
                # Try LPA code first
                if org in org_to_lpa:
                    lpa_code = org_to_lpa[org]
                    lpa_codes.append(lpa_code)

                    # Get geometry for this LPA code
                    if lpa_code in lpa_geometries:
                        geometries.append(lpa_geometries[lpa_code])
                    else:
                        print(
                            f"Warning: No geometry found for {org} (LPA: {lpa_code})",
                            file=sys.stderr,
                        )
                # Fallback to LAD code for dissolved councils
                elif org in org_to_lad:
                    lad_code = org_to_lad[org]
                    lpa_codes.append(lad_code)

                    # Get geometry from LAD
                    if lad_code in lad_geometries:
                        geometries.append(lad_geometries[lad_code])
                        print(
                            f"Info: Using LAD boundary for {org} (LAD: {lad_code})",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"Warning: No LAD geometry found for {org} (LAD: {lad_code})",
                            file=sys.stderr,
                        )
                else:
                    print(
                        f"Warning: No LPA or LAD code found for organisation {org}",
                        file=sys.stderr,
                    )

            # Sort LPA codes for reference
            sorted_lpa_codes = sorted(lpa_codes)
            lpa_reference = "-".join([code for code in sorted_lpa_codes])

            # Combine geometries
            combined_geometry = combine_geometries(geometries)

            boundaries.append(
                {
                    "organisation": f"local-planning-group:{group_reference}",
                    "reference": lpa_reference,
                    "name": group_name,
                    "organisations": organisations,
                    "local-planning-authorities": ";".join(sorted_lpa_codes),
                    "geometry": combined_geometry,
                }
            )

    # Write output
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="") as f:
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

        for boundary in sorted(boundaries, key=lambda x: x["reference"]):
            writer.writerow(boundary)

    print(
        f"Generated {len(boundaries)} local planning group boundaries in {output_csv}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
