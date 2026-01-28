#!/usr/bin/env python3

import csv
import sys
from pathlib import Path
import re

csv.field_size_limit(sys.maxsize)


def load_lpa_geometries(lpa_csv_path):
    """Load geometries by LPA reference code."""
    geometries = {}
    with open(lpa_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("reference", "").strip()
            geometry = row.get("geometry", "").strip()
            if reference and geometry:
                geometries[reference] = geometry
    return geometries


def load_county_geometries(county_csv_path):
    """Load geometries from county CSV with CTYUA13CD column."""
    geometries = {}
    with open(county_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("CTYUA13CD", "").strip()
            geometry = row.get("WKT", "").strip()
            if reference and geometry:
                geometries[reference] = geometry
    return geometries


def load_unitary_geometries(unitary_csv_path):
    """Load geometries from county-and-unitary-authority CSV with CTYUA24CD column."""
    geometries = {}
    with open(unitary_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("CTYUA24CD", "").strip()
            geometry = row.get("WKT", "").strip()
            if reference and geometry:
                geometries[reference] = geometry
    return geometries


def extract_polygons_from_wkt(wkt):
    """Extract individual polygon coordinates from POLYGON or MULTIPOLYGON WKT."""
    if not wkt:
        return []

    wkt = wkt.strip()

    # Handle POLYGON format
    if wkt.startswith("POLYGON"):
        match = re.match(r"POLYGON\s*\(\s*(.+)\s*\)\s*$", wkt)
        if match:
            content = match.group(1)
            # Wrap the polygon in parentheses for consistency with MULTIPOLYGON format
            return [f"({content})"]
        return []

    # Handle MULTIPOLYGON format
    if wkt.startswith("MULTIPOLYGON"):
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

    return []


def combine_geometries(geometries):
    """Combine multiple POLYGON or MULTIPOLYGON geometries into one."""
    all_polygons = []

    for geom in geometries:
        if not geom:
            continue
        polygons = extract_polygons_from_wkt(geom)
        all_polygons.extend(polygons)

    if not all_polygons:
        return ""

    # Combine into single MULTIPOLYGON
    return f"MULTIPOLYGON ({', '.join(all_polygons)})"


def process_plan_type(plans_csv, all_geometries, plan_type, geography_column, output_csv):
    """Process a single plan type (mineral or waste) and generate boundary CSV."""
    print(f"Processing {plan_type} plans...", file=sys.stderr)
    boundaries = []

    with open(plans_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row["reference"]
            name = row["name"]
            geography_codes = row.get(geography_column, "").strip()

            if not geography_codes or geography_codes == "NONE":
                print(f"Warning: No geography codes for {reference}", file=sys.stderr)
                continue

            # Parse geography codes
            codes = [code.strip() for code in geography_codes.split("-")]

            # Get geometries for each code
            geometries = []
            for code in codes:
                if code in all_geometries:
                    geometries.append(all_geometries[code])
                else:
                    print(f"Warning: No geometry found for code {code} in {reference}", file=sys.stderr)

            # Combine geometries
            combined_geometry = combine_geometries(geometries)

            boundaries.append({
                "reference": reference,
                "name": name,
                "organisations": row.get("organisations", ""),
                geography_column: geography_codes,
                "geometry": combined_geometry,
            })

    # Write output
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="") as f:
        fieldnames = [
            "reference",
            "name",
            "organisations",
            geography_column,
            "geometry",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for boundary in sorted(boundaries, key=lambda x: x["reference"]):
            writer.writerow(boundary)

    print(f"✓ Generated {len(boundaries)} {plan_type} plan boundaries in {output_csv}", file=sys.stderr)
    return len(boundaries)


def main():
    # File paths
    mineral_plans_csv = Path("dataset/mineral-plans.csv")
    waste_plans_csv = Path("dataset/waste-plans.csv")
    lpa_csv = Path("var/cache/local-planning-authority.csv")
    county_csv = Path("var/cache/ctyua_2024_bfe_v4.csv")
    unitary_csv = Path("var/cache/county-and-unitary-authority.csv")
    mineral_output_csv = Path("dataset/mineral-plan-boundary.csv")
    waste_output_csv = Path("dataset/waste-plan-boundary.csv")

    # Load geometries from all sources (once)
    print("Loading geometries...", file=sys.stderr)
    print("  Loading LPA geometries...", file=sys.stderr)
    lpa_geometries = load_lpa_geometries(lpa_csv)

    print("  Loading county geometries...", file=sys.stderr)
    county_geometries = load_county_geometries(county_csv)

    print("  Loading unitary authority geometries...", file=sys.stderr)
    unitary_geometries = load_unitary_geometries(unitary_csv)

    # Merge geometries (LPA takes precedence, then county, then unitary)
    all_geometries = {**unitary_geometries, **county_geometries, **lpa_geometries}
    print(f"  Total geometries loaded: {len(all_geometries)}", file=sys.stderr)

    # Process both plan types
    print("\nProcessing plan boundaries...", file=sys.stderr)
    mineral_count = process_plan_type(
        mineral_plans_csv,
        all_geometries,
        "mineral",
        "mineral-planning-authority",
        mineral_output_csv
    )

    waste_count = process_plan_type(
        waste_plans_csv,
        all_geometries,
        "waste",
        "waste-planning-authority",
        waste_output_csv
    )

    print(f"\nGenerated {mineral_count} mineral and {waste_count} waste plan boundaries", file=sys.stderr)


if __name__ == "__main__":
    main()
