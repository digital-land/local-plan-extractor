#!/usr/bin/env python3

import csv
import math
import sys
from pathlib import Path
import re

import shapely
from shapely.geometry import MultiPolygon, Polygon

csv.field_size_limit(sys.maxsize)

# Holes smaller than this left by merging joint boundaries are filled (0.01 km²)
SLIVER_HOLE_MAX_AREA_M2 = 10000

# Joint boundary parts are snapped together within this distance before merging (~0.5 m)
SNAP_TOLERANCE_DEGREES = 0.5 / 111000


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


def load_authority_names(lpa_csv_path, lad_csv_path, county_csv_path, unitary_csv_path):
    """Load authority names mapped by E-code from all geometry sources."""
    names = {}

    # Load from local-planning-authority (E60000xxx codes)
    with open(lpa_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("reference", "").strip()
            name = row.get("name", "").strip()
            if reference and name:
                # Remove " LPA" suffix if present
                name = name.replace(" LPA", "")
                names[reference] = name

    # Load from local-authority-district (E06000xxx codes)
    with open(lad_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("reference", "").strip()
            name = row.get("name", "").strip()
            if reference and name:
                names[reference] = name

    # Load from ctyua_2024_bfe_v4 (E10000xxx codes via CTYUA13CD)
    with open(county_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("CTYUA13CD", "").strip()
            name = row.get("CTYUA13NM", "").strip()
            if reference and name:
                names[reference] = name

    # Load from county-and-unitary-authority (E06000xxx codes via CTYUA24CD)
    with open(unitary_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row.get("CTYUA24CD", "").strip()
            name = row.get("CTYUA24NM", "").strip()
            if reference and name:
                names[reference] = name

    return names


def load_organisation_names(org_csv_path):
    """Load organisation names mapped by organisation CURIE."""
    names = {}
    with open(org_csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            organisation = row.get("organisation", "").strip()
            name = row.get("name", "").strip()
            if organisation and name:
                names[organisation] = name
    return names


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


def approx_area_m2(polygon):
    """Approximate area in square metres of a WGS84 polygon."""
    lat = math.radians(polygon.centroid.y)
    return polygon.area * 111320 * 110574 * math.cos(lat)


def combine_geometries(geometries):
    """Combine multiple POLYGON or MULTIPOLYGON geometries into one."""
    geometries = [geom for geom in geometries if geom]

    if not geometries:
        return ""

    if len(geometries) == 1:
        polygons = extract_polygons_from_wkt(geometries[0])
        return f"MULTIPOLYGON ({', '.join(polygons)})" if polygons else ""

    # Joint boundary - merge the parts so shared edges and overlaps are dissolved,
    # otherwise the MULTIPOLYGON is invalid (self-intersecting). Snap each part
    # onto the parts before it first, so near-matching source edges meet exactly
    # and don't leave thin notches on the outer edge
    parts = [shapely.make_valid(shapely.from_wkt(geom)) for geom in geometries]
    for i in range(1, len(parts)):
        snapped = shapely.snap(parts[i], shapely.unary_union(parts[:i]), SNAP_TOLERANCE_DEGREES)
        parts[i] = shapely.make_valid(snapped)
    merged = shapely.unary_union(parts)
    polygons = [g for g in shapely.get_parts(merged) if isinstance(g, Polygon)]
    if not polygons:
        return ""

    # Source boundaries from different datasets don't meet exactly, so the merge
    # leaves sliver holes along shared borders - fill them, but keep any hole
    # that was already in a constituent boundary and that no other constituent
    # fills (e.g. a county's hole for a city is filled by the city, an enclave isn't)
    source_holes = shapely.unary_union([
        hole for hole in (
            Polygon(ring) for part in parts for p in shapely.get_parts(part)
            if isinstance(p, Polygon) for ring in p.interiors
        )
        if merged.intersection(hole).area < hole.area / 2
    ])
    polygons = [
        Polygon(p.exterior, [
            ring for ring in p.interiors
            if approx_area_m2(Polygon(ring)) >= SLIVER_HOLE_MAX_AREA_M2
            or source_holes.contains(Polygon(ring).representative_point())
        ])
        for p in polygons
    ]

    return MultiPolygon(polygons).wkt


def process_plan_type(plans_csv, all_geometries, plan_type, geography_column, output_csv, authority_names, organisation_names):
    """Process a single plan type (mineral or waste) and generate boundary CSV."""
    print(f"Processing {plan_type} plans...", file=sys.stderr)
    boundaries = []

    with open(plans_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reference = row["reference"]
            plan_name = row["name"]
            geography_codes = row.get(geography_column, "").strip()

            if not geography_codes or geography_codes == "NONE":
                print(f"Warning: No geography codes for {reference}", file=sys.stderr)
                continue

            # Parse geography codes
            codes = [code.strip() for code in geography_codes.split(";")]

            # Get geometries for each code
            geometries = []
            for code in codes:
                if code in all_geometries:
                    geometries.append(all_geometries[code])
                else:
                    print(f"Warning: No geometry found for code {code} in {reference}", file=sys.stderr)

            # Combine geometries
            combined_geometry = combine_geometries(geometries)

            # Determine the name: look up the first organisation CURIE, then fall back to E-code
            organisations_str = row.get("organisations", "").strip()
            boundary_name = None

            if organisations_str:
                org = organisations_str.split(";")[0].strip()
                boundary_name = organisation_names.get(org)

            if not boundary_name:
                if len(codes) == 1:
                    boundary_name = authority_names.get(codes[0], geography_codes)
                else:
                    boundary_name = geography_codes

            boundaries.append({
                "reference": geography_codes,
                "name": boundary_name,
                "organisations": row.get("organisations", ""),
                geography_column: geography_codes,
                "geometry": combined_geometry,
            })

    # Deduplicate boundaries by reference (each unique geography code should appear only once)
    seen_references = set()
    unique_boundaries = []
    for boundary in boundaries:
        ref = boundary["reference"]
        if ref not in seen_references:
            seen_references.add(ref)
            unique_boundaries.append(boundary)
        else:
            print(f"Warning: Dropping duplicate reference {ref} for {boundary['name']}", file=sys.stderr)

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

        for boundary in sorted(unique_boundaries, key=lambda x: x["reference"]):
            writer.writerow(boundary)

    removed_count = len(boundaries) - len(unique_boundaries)
    if removed_count > 0:
        print(f"✓ Generated {len(unique_boundaries)} unique {plan_type} plan boundaries in {output_csv} ({removed_count} duplicate references removed)", file=sys.stderr)
    else:
        print(f"✓ Generated {len(unique_boundaries)} unique {plan_type} plan boundaries in {output_csv}", file=sys.stderr)
    return len(unique_boundaries)


def main():
    # File paths
    mineral_plans_csv = Path("dataset/mineral-plan.csv")
    waste_plans_csv = Path("dataset/waste-plan.csv")
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

    # Load authority names
    print("Loading authority names...", file=sys.stderr)
    lad_csv = Path("var/cache/local-authority-district.csv")
    org_csv = Path("var/cache/organisation.csv")
    authority_names = load_authority_names(lpa_csv, lad_csv, county_csv, unitary_csv)
    organisation_names = load_organisation_names(org_csv)
    print(f"  Loaded {len(authority_names)} authority names", file=sys.stderr)
    print(f"  Loaded {len(organisation_names)} organisation names", file=sys.stderr)

    # Process both plan types
    print("\nProcessing plan boundaries...", file=sys.stderr)
    mineral_count = process_plan_type(
        mineral_plans_csv,
        all_geometries,
        "mineral",
        "mineral-planning-authorities",
        mineral_output_csv,
        authority_names,
        organisation_names
    )

    waste_count = process_plan_type(
        waste_plans_csv,
        all_geometries,
        "waste",
        "waste-planning-authorities",
        waste_output_csv,
        authority_names,
        organisation_names
    )

    print(f"\nGenerated {mineral_count} mineral and {waste_count} waste plan boundaries", file=sys.stderr)


if __name__ == "__main__":
    main()
