#!/usr/bin/env python3
"""
Render local plan JSON files as HTML pages using GOV.UK Frontend.
"""

import json
import sys
import csv
import argparse
from pathlib import Path
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
                    organisations[org_code] = org_name
    except FileNotFoundError:
        print(f"Warning: Organisation CSV not found at {csv_path}", file=sys.stderr)
    return organisations


def format_number(value):
    """Format number with thousand separators"""
    if isinstance(value, (int, float)) and value != '':
        return f"{value:,}"
    return value if value != '' else 'Not specified'


def render_local_plan(json_path, output_dir, templates_dir):
    """Render a local plan JSON file to HTML"""

    # Load the JSON data
    json_path = Path(json_path)
    data = load_json(json_path)

    # Load organisation lookup
    organisations = load_organisations()

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(['html', 'xml'])
    )

    # Add custom filters
    env.filters['format_number'] = format_number

    # Load template
    template = env.get_template('local-plan.html')

    # Generate output filename from JSON filename
    output_filename = json_path.stem + '.html'
    output_path = Path(output_dir) / output_filename

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render template
    html_content = template.render(
        plan=data,
        json_filename=json_path.name,
        organisations=organisations
    )

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✓ Rendered: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description='Render local plan JSON files as HTML pages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render a single JSON file
  python bin/render-json.py local-plan/city-of-york-adopted-local-plan-2025.json

  # Specify custom output directory
  python bin/render-json.py local-plan/city-of-york.json --output public/

  # Specify custom templates directory
  python bin/render-json.py local-plan/city-of-york.json --templates my-templates/
        """
    )

    parser.add_argument(
        'json_file',
        help='Path to local plan JSON file'
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

    # Check if JSON file exists
    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"Error: File not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    # Check if templates directory exists
    templates_dir = Path(args.templates)
    if not templates_dir.exists():
        print(f"Error: Templates directory not found: {args.templates}", file=sys.stderr)
        sys.exit(1)

    # Render the page
    try:
        output_path = render_local_plan(json_path, args.output, templates_dir)
        print(f"\n✓ Successfully rendered HTML to {output_path}")
    except Exception as e:
        print(f"Error rendering HTML: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
