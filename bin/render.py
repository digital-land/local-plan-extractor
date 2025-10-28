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
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_organisations(csv_path='var/cache/organisation.csv'):
    """Load organisation data from CSV and create lookup dict"""
    organisations = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                org_code = row.get('organisation', '')
                org_name = row.get('name', '')
                if org_code and org_name:
                    organisations[org_code] = {
                        'name': org_name,
                        'reference': org_code
                    }
    except FileNotFoundError:
        print(f"Warning: Organisation CSV not found at {csv_path}", file=sys.stderr)
    return organisations


def format_number(value):
    """Format number with thousand separators"""
    if isinstance(value, (int, float)) and value != '':
        return f"{value:,}"
    return value if value != '' else 'Not specified'


def collect_organisation_plans(local_plan_dir):
    """Collect which plans each organisation is part of"""
    org_plans = defaultdict(list)

    json_files = sorted(Path(local_plan_dir).glob('*.json'))

    for json_path in json_files:
        try:
            data = load_json(json_path)

            # Get organisations from this plan
            orgs = data.get('organisations', [])
            if not orgs and data.get('organisation'):
                # Single authority plan
                orgs = [data['organisation']]

            for org_code in orgs:
                org_plans[org_code].append({
                    'name': data.get('name', json_path.stem),
                    'filename': json_path.stem,
                    'organisation-name': data.get('organisation-name', ''),
                    'period-start-date': data.get('period-start-date', ''),
                    'period-end-date': data.get('period-end-date', '')
                })
        except Exception as e:
            print(f"  Warning: Error processing {json_path.name}: {e}", file=sys.stderr)
            continue

    return org_plans


def render_local_plan(json_path, output_dir, env, organisations_lookup):
    """Render a local plan JSON file to HTML"""

    # Load the JSON data
    json_path = Path(json_path)
    data = load_json(json_path)

    # Load template
    template = env.get_template('local-plan.html')

    # Generate output filename from JSON filename - put in local-plan subdirectory
    output_filename = json_path.stem + '.html'
    output_path = Path(output_dir) / 'local-plan' / output_filename

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render template
    html_content = template.render(
        plan=data,
        json_filename=json_path.name,
        organisations=organisations_lookup,
        home_path='../index.html'
    )

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path, data


def render_organisation_page(org_code, org_data, plans, output_dir, env, organisations_lookup):
    """Render an organisation page showing all plans they're part of"""

    # Load template
    template = env.get_template('organisation.html')

    # Create organisation subdirectory for this org (organisation/org_code/)
    org_subdir = Path(output_dir) / 'organisation' / org_code
    org_subdir.mkdir(parents=True, exist_ok=True)

    # Create index.html in the org subdirectory
    output_path = org_subdir / 'index.html'

    # Render template
    html_content = template.render(
        organisation=org_data,
        plans=plans,
        organisations=organisations_lookup,
        home_path='../../index.html'
    )

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path


def render_index(plans, output_dir, env):
    """Render the index page with list of all local plans"""

    # Load template
    template = env.get_template('index.html')

    # Sort plans by name
    sorted_plans = sorted(plans, key=lambda p: p.get('name', ''))

    # Render template
    html_content = template.render(
        plans=sorted_plans,
        home_path='index.html'
    )

    # Write output
    output_path = Path(output_dir) / 'index.html'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description='Render all local plan JSON files and organisation pages as HTML',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render all plans and organisation pages
  python bin/render.py

  # Specify custom directories
  python bin/render.py --local-plans data/plans/ --output public/
        """
    )

    parser.add_argument(
        '--local-plans',
        default='local-plan',
        help='Directory containing local plan JSON files (default: local-plan/)'
    )

    parser.add_argument(
        '--output', '-o',
        default='docs',
        help='Output directory for HTML files (default: docs/)'
    )

    parser.add_argument(
        '--templates', '-t',
        default='templates',
        help='Templates directory (default: templates/)'
    )

    args = parser.parse_args()

    # Check directories exist
    local_plan_dir = Path(args.local_plans)
    if not local_plan_dir.exists():
        print(f"Error: Local plans directory not found: {args.local_plans}", file=sys.stderr)
        sys.exit(1)

    templates_dir = Path(args.templates)
    if not templates_dir.exists():
        print(f"Error: Templates directory not found: {args.templates}", file=sys.stderr)
        sys.exit(1)

    # Load organisations lookup
    print("Loading organisations...")
    organisations_lookup = load_organisations()
    org_names = {code: data['name'] for code, data in organisations_lookup.items()}

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(['html', 'xml'])
    )
    env.filters['format_number'] = format_number

    # Find all JSON files
    json_files = sorted(local_plan_dir.glob('*.json'))
    print(f"\nFound {len(json_files)} local plan JSON files")

    # Render all local plan pages
    print("\nRendering local plan pages...")
    rendered_plans = 0
    plans_data = []
    for json_path in json_files:
        try:
            output_path, data = render_local_plan(json_path, args.output, env, org_names)
            print(f"  ✓ {json_path.stem}")
            rendered_plans += 1
            # Add filename for linking
            data['filename'] = json_path.stem
            plans_data.append(data)
        except Exception as e:
            print(f"  ✗ {json_path.stem}: {e}", file=sys.stderr)

    print(f"\n✓ Rendered {rendered_plans} local plan pages")

    # Render index page
    print("\nRendering index page...")
    try:
        index_path = render_index(plans_data, args.output, env)
        print(f"  ✓ Created index.html")
    except Exception as e:
        print(f"  ✗ Error creating index: {e}", file=sys.stderr)

    # Collect organisation information
    print("\nCollecting organisation information...")
    org_plans = collect_organisation_plans(local_plan_dir)
    print(f"  Found {len(org_plans)} organisations")

    # Render organisation pages
    print("\nRendering organisation pages...")
    rendered_orgs = 0
    for org_code, plans in sorted(org_plans.items()):
        try:
            org_data = organisations_lookup.get(org_code, {
                'name': org_code,
                'reference': org_code
            })
            output_path = render_organisation_page(
                org_code, org_data, plans, args.output, env, org_names
            )
            print(f"  ✓ {org_code}")
            rendered_orgs += 1
        except Exception as e:
            print(f"  ✗ {org_code}: {e}", file=sys.stderr)

    print(f"\n✓ Rendered {rendered_orgs} organisation pages")

    # Create .nojekyll file
    nojekyll_path = Path(args.output) / '.nojekyll'
    nojekyll_path.touch()
    print(f"\n✓ Created {nojekyll_path}")

    # Copy var/cache directory for GeoJSON data
    print("\nCopying data files...")
    var_cache_src = Path('var/cache')
    var_cache_dest = Path(args.output) / 'var' / 'cache'

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

    print("\n" + "="*60)
    print("Summary:")
    print(f"  Index page: 1")
    print(f"  Local plan pages: {rendered_plans}")
    print(f"  Organisation pages: {rendered_orgs}")
    print(f"  Output directory: {args.output}")
    print("="*60)


if __name__ == '__main__':
    main()
