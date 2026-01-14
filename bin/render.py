#!/usr/bin/env python3
"""
Render all local plan JSON files and organisation pages as HTML using GOV.UK Frontend.
"""

import json
import sys
import csv
import argparse
import shutil
from pathlib import Path
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader, select_autoescape


def load_json(json_path):
    """Load JSON file"""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_organisations(csv_path="var/cache/organisation.csv"):
    """Load organisation data from CSV and create lookup dict"""
    organisations = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                org_code = row.get("organisation", "")
                org_name = row.get("name", "")
                if org_code and org_name:
                    organisations[org_code] = {"name": org_name, "reference": org_code}
    except FileNotFoundError:
        print(f"Warning: Organisation CSV not found at {csv_path}", file=sys.stderr)
    return organisations


def load_document_urls(source_dir="source"):
    """Load document URLs from source JSON files and create lookup by endpoint (authority id)"""
    document_urls = {}
    try:
        source_path = Path(source_dir)
        for json_file in source_path.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            # Extract document URLs for each document by endpoint
                            if item.get("documents"):
                                for doc in item["documents"]:
                                    if doc.get("endpoint") and doc.get("document-url"):
                                        endpoint = doc["endpoint"]
                                        document_urls[endpoint] = doc["document-url"]
            except Exception as e:
                # Silently skip files with errors
                pass
    except Exception as e:
        print(f"Warning: Error loading document URLs: {e}", file=sys.stderr)
    return document_urls


def validate_local_plan(data, json_path):
    """Validate that a JSON file contains valid local plan data.

    Returns a tuple (is_valid, error_message).
    A valid local plan must have:
    - name: plan name
    - organisation-name: organisation name
    - period-start-date and period-end-date: plan period
    - housing-numbers: non-empty array with housing data
    - organisation or organisations: at least one organisation reference
    """
    errors = []

    # Check required string fields
    if not data.get("name"):
        errors.append("Missing 'name'")

    if not data.get("organisation-name"):
        errors.append("Missing 'organisation-name'")

    # Check period dates
    if data.get("period-start-date") is None:
        errors.append("Missing 'period-start-date'")

    if data.get("period-end-date") is None:
        errors.append("Missing 'period-end-date'")

    # Check housing numbers (at least one entry)
    housing_numbers = data.get("housing-numbers", [])
    if not isinstance(housing_numbers, list) or len(housing_numbers) == 0:
        errors.append("Missing or empty 'housing-numbers' array")

    # Check for at least one organisation reference
    has_organisation = bool(data.get("organisation"))
    has_organisations = bool(data.get("organisations"))
    if not has_organisation and not has_organisations:
        errors.append("Missing both 'organisation' and 'organisations'")

    if errors:
        return False, "; ".join(errors)

    return True, None


def format_number(value):
    """Format number with thousand separators"""
    if isinstance(value, (int, float)) and value != "":
        return f"{value:,}"
    return value if value != "" else "Not specified"


def collect_organisation_plans(local_plan_dir):
    """Collect which plans each organisation is part of"""
    org_plans = defaultdict(list)

    json_files = sorted(Path(local_plan_dir).glob("*.json"))

    for json_path in json_files:
        try:
            data = load_json(json_path)

            # Get organisations from this plan
            orgs = data.get("organisations", [])
            if not orgs and data.get("organisation"):
                # Single authority plan
                orgs = [data["organisation"]]

            for org_code in orgs:
                org_plans[org_code].append(
                    {
                        "name": data.get("name", json_path.stem),
                        "filename": json_path.stem,
                        "organisation-name": data.get("organisation-name", ""),
                        "period-start-date": data.get("period-start-date", ""),
                        "period-end-date": data.get("period-end-date", ""),
                    }
                )
        except Exception as e:
            print(f"  Warning: Error processing {json_path.name}: {e}", file=sys.stderr)
            continue

    return org_plans


def render_local_plan(json_path, output_dir, env, organisations_lookup, document_url_lookup=None):
    """Render a local plan JSON file to HTML.

    Returns (output_path, data) on success, or (None, None) if validation fails.
    """

    # Load the JSON data
    json_path = Path(json_path)
    data = load_json(json_path)

    # Validate the local plan data
    is_valid, error_msg = validate_local_plan(data, json_path)
    if not is_valid:
        return None, None

    # Look up document-url from source data if available
    if document_url_lookup and "authority" in data:
        authority_id = data["authority"]
        if authority_id in document_url_lookup:
            data["document-url"] = document_url_lookup[authority_id]

    # Check if PDF file exists and its size
    MAX_PDF_SIZE = 100 * 1024 * 1024  # 100 MB limit
    if data.get("pdf_file"):
        pdf_path = Path(data["pdf_file"])
        if not pdf_path.exists():
            data["pdf_file_missing"] = True
            # If PDF is missing but we have a source URL, provide helpful message
            if data.get("document-url"):
                data["error"] = "PDF file is too large to display in browser. Please view the original document using the source link above."
        else:
            data["pdf_file_missing"] = False
            file_size = pdf_path.stat().st_size
            if file_size > MAX_PDF_SIZE:
                size_mb = file_size / (1024 * 1024)
                data["error"] = f"PDF file is too large ({size_mb:.0f} MB) to display in browser. Please view the original document from the source link above."
    else:
        data["pdf_file_missing"] = False

    # Load template
    template = env.get_template("local-plan.html")

    # Generate output filename from JSON filename - put in local-plan subdirectory
    output_filename = json_path.stem + ".html"
    output_path = Path(output_dir) / "local-plan" / output_filename

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render template
    html_content = template.render(
        plan=data,
        json_filename=json_path.name,
        organisations=organisations_lookup,
        home_path="../index.html",
    )

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path, data


def render_organisation_page(
    org_code, org_data, plans, output_dir, env, organisations_lookup
):
    """Render an organisation page showing all plans they're part of"""

    # Load template
    template = env.get_template("organisation.html")

    # Create organisation subdirectory for this org (organisation/org_code/)
    org_subdir = Path(output_dir) / "organisation" / org_code
    org_subdir.mkdir(parents=True, exist_ok=True)

    # Create index.html in the org subdirectory
    output_path = org_subdir / "index.html"

    # Render template
    html_content = template.render(
        organisation=org_data,
        plans=plans,
        organisations=organisations_lookup,
        home_path="../../index.html",
    )

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


def render_index(plans, output_dir, env):
    """Render the index page with list of all local plans"""

    # Load template
    template = env.get_template("index.html")

    # Ensure all plans have organisation-name populated (for joint-authority plans)
    for plan in plans:
        if not plan.get("organisation-name"):
            # Look for joint-planning-authority entry in housing-numbers
            for entry in plan.get("housing-numbers", []):
                if entry.get("organisation", "").startswith("joint-planning-authority:"):
                    plan["organisation-name"] = entry.get("organisation-name", "Unknown")
                    break

    # Sort plans by name
    sorted_by_name = sorted(plans, key=lambda p: p.get("name", ""))

    # Sort plans by organisation name
    sorted_by_org = sorted(plans, key=lambda p: p.get("organisation-name", ""))

    # Render template
    html_content = template.render(
        plans=plans,
        plans_by_name=sorted_by_name,
        plans_by_org=sorted_by_org,
        home_path="index.html"
    )

    # Write output
    output_path = Path(output_dir) / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


def render_review_page(plan_filenames, output_dir, env):
    """Render the review page for reviewing local plans one at a time"""

    # Load template
    template = env.get_template("review.html")

    # Render template
    html_content = template.render(
        plan_filenames=plan_filenames,
        home_path="index.html"
    )

    # Write output
    output_path = Path(output_dir) / "review.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Render all local plan JSON files and organisation pages as HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render all plans and organisation pages
  python bin/render.py

  # Specify custom directories
  python bin/render.py --local-plans data/plans/ --output public/
        """,
    )

    parser.add_argument(
        "--local-plans",
        default="local-plan",
        help="Directory containing local plan JSON files (default: local-plan/)",
    )

    parser.add_argument(
        "--output",
        "-o",
        default="docs",
        help="Output directory for HTML files (default: docs/)",
    )

    parser.add_argument(
        "--templates",
        "-t",
        default="templates",
        help="Templates directory (default: templates/)",
    )

    args = parser.parse_args()

    # Check directories exist
    local_plan_dir = Path(args.local_plans)
    if not local_plan_dir.exists():
        print(
            f"Error: Local plans directory not found: {args.local_plans}",
            file=sys.stderr,
        )
        sys.exit(1)

    templates_dir = Path(args.templates)
    if not templates_dir.exists():
        print(
            f"Error: Templates directory not found: {args.templates}", file=sys.stderr
        )
        sys.exit(1)

    # Load organisations lookup
    print("Loading organisations...")
    organisations_lookup = load_organisations()
    org_names = {code: data["name"] for code, data in organisations_lookup.items()}

    # Load document URLs lookup
    print("Loading document URLs from source...")
    document_urls = load_document_urls()
    print(f"  Found {len(document_urls)} document URLs")

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["format_number"] = format_number

    # Find all JSON files
    json_files = sorted(local_plan_dir.glob("*.json"))
    print(f"\nFound {len(json_files)} local plan JSON files")

    # Render all local plan pages
    print("\nRendering local plan pages...")
    rendered_plans = 0
    skipped_plans = 0
    plans_data = []
    for json_path in json_files:
        try:
            output_path, data = render_local_plan(
                json_path, args.output, env, org_names, document_urls
            )
            if output_path is None:
                # Validation failed
                print(f"  ⊘ {json_path.stem} (validation failed)", file=sys.stderr)
                skipped_plans += 1
            else:
                print(f"  ✓ {json_path.stem}")
                rendered_plans += 1
                # Add filename for linking
                data["filename"] = json_path.stem
                # Include housing-numbers for organisation lookup in index template
                plans_data.append(data)
        except Exception as e:
            print(f"  ✗ {json_path.stem}: {e}", file=sys.stderr)

    print(f"\n✓ Rendered {rendered_plans} local plan pages")
    if skipped_plans > 0:
        print(f"⊘ Skipped {skipped_plans} invalid local plans")

    # Render index page
    print("\nRendering index page...")
    try:
        index_path = render_index(plans_data, args.output, env)
        print(f"  ✓ Created index.html")
    except Exception as e:
        print(f"  ✗ Error creating index: {e}", file=sys.stderr)

    # Render review page
    print("\nRendering review page...")
    try:
        plan_filenames = [json_path.stem for json_path in json_files if json_path.stem in [p["filename"] for p in plans_data]]
        review_path = render_review_page(plan_filenames, args.output, env)
        print(f"  ✓ Created review.html")
    except Exception as e:
        print(f"  ✗ Error creating review page: {e}", file=sys.stderr)

    # Collect organisation information
    print("\nCollecting organisation information...")
    org_plans = collect_organisation_plans(local_plan_dir)
    print(f"  Found {len(org_plans)} organisations")

    # Render organisation pages
    print("\nRendering organisation pages...")
    rendered_orgs = 0
    for org_code, plans in sorted(org_plans.items()):
        try:
            org_data = organisations_lookup.get(
                org_code, {"name": org_code, "reference": org_code}
            )
            output_path = render_organisation_page(
                org_code, org_data, plans, args.output, env, org_names
            )
            print(f"  ✓ {org_code}")
            rendered_orgs += 1
        except Exception as e:
            print(f"  ✗ {org_code}: {e}", file=sys.stderr)

    print(f"\n✓ Rendered {rendered_orgs} organisation pages")

    # Create .nojekyll file
    nojekyll_path = Path(args.output) / ".nojekyll"
    nojekyll_path.touch()
    print(f"\n✓ Created {nojekyll_path}")

    # Copy var/cache directory for GeoJSON data
    print("\nCopying data files...")
    var_cache_src = Path("var/cache")
    var_cache_dest = Path(args.output) / "var" / "cache"

    if var_cache_src.exists():
        # Remove destination if it exists
        if var_cache_dest.exists():
            shutil.rmtree(var_cache_dest.parent)

        # Copy the directory
        var_cache_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(var_cache_src, var_cache_dest)
        print(f"  ✓ Copied var/cache to {var_cache_dest}")
    else:
        print(f"  ⚠ Warning: var/cache directory not found", file=sys.stderr)

    # Copy collection/document directory for PDFs
    collection_src = Path("collection/document")
    collection_dest = Path(args.output) / "collection" / "document"

    if collection_src.exists():
        # Remove destination if it exists
        if collection_dest.exists():
            shutil.rmtree(collection_dest.parent)

        # Copy the directory
        collection_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(collection_src, collection_dest)
        print(f"  ✓ Copied collection/document to {collection_dest}")
    else:
        print(f"  ⚠ Warning: collection/document directory not found", file=sys.stderr)

    # Copy local-plan JSON files for review page (enriched with document URLs)
    local_plan_src = Path(args.local_plans)
    local_plan_data_dest = Path(args.output) / "local-plan-data"

    if local_plan_src.exists():
        # Remove destination if it exists
        if local_plan_data_dest.exists():
            shutil.rmtree(local_plan_data_dest)

        # Create directory and copy JSON files
        local_plan_data_dest.mkdir(parents=True, exist_ok=True)
        json_count = 0
        for json_file in local_plan_src.glob("*.json"):
            # Load the JSON data
            data = load_json(json_file)

            # Add document-url from source data if available
            if document_urls and "authority" in data:
                authority_id = data["authority"]
                if authority_id in document_urls:
                    data["document-url"] = document_urls[authority_id]

            # Write enriched JSON to output directory
            output_json_path = local_plan_data_dest / json_file.name
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            json_count += 1
        print(f"  ✓ Copied {json_count} JSON files to {local_plan_data_dest}")
    else:
        print(f"  ⚠ Warning: local-plan directory not found", file=sys.stderr)

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Index page: 1")
    print(f"  Local plan pages: {rendered_plans}")
    print(f"  Organisation pages: {rendered_orgs}")
    print(f"  Output directory: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
