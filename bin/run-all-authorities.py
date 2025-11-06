#!/usr/bin/env python3
"""
Driver script to run find-local-plan.py for all local planning authorities.

Usage:
    python bin/run-all-authorities.py
    python bin/run-all-authorities.py --no-save-pdfs
    python bin/run-all-authorities.py --authorities BIR,MAN,LDS
    python bin/run-all-authorities.py --start-from MAN
    python bin/run-all-authorities.py --limit 10

This script:
1. Reads all local planning authorities from docs/var/cache/organisation.csv
2. For each authority, runs bin/find-local-plan.py
3. Saves the output to source/{organisation-code}.json
4. Optionally downloads all PDFs found
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def load_local_planning_authorities(lpa_lookup_csv, organisation_csv):
    """
    Load all local planning authority codes by filtering organisation.csv
    using local-planning-authority-lookup.csv.

    Args:
        lpa_lookup_csv: Path to local-planning-authority-lookup.csv
        organisation_csv: Path to organisation.csv

    Returns list of tuples: (organisation_code, organisation_name)
    """
    # Step 1: Load all organisation codes from the LPA lookup CSV
    lpa_org_codes = set()
    with open(lpa_lookup_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            org_code = row.get('organisation', '').strip()
            if org_code:
                lpa_org_codes.add(org_code)

    print(f"Found {len(lpa_org_codes)} local planning authorities in {lpa_lookup_csv}")

    # Step 2: Load organisation.csv and filter by the LPA codes
    authorities = []
    with open(organisation_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            org_code = row.get('organisation', '').strip()
            org_name = row.get('name', '').strip()

            # Only include if this organisation is in the LPA lookup
            if org_code in lpa_org_codes:
                authorities.append((org_code, org_name))

    print(f"Filtered to {len(authorities)} authorities from {organisation_csv}")

    return authorities


def run_find_local_plan(org_code, org_name, save_pdfs=True, debug=False):
    """
    Run find-local-plan.py for a single authority.

    Returns (success: bool, output: dict/list, error: str)
    """
    cmd = ['python3', 'bin/find-local-plan.py', org_code]

    if not save_pdfs:
        cmd.append('--no-save-pdfs')

    if debug:
        cmd.append('--debug')

    print(f"\n{'='*80}")
    print(f"Processing: {org_name} ({org_code})")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*80}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout per authority
        )

        if result.returncode == 0:
            try:
                output = json.loads(result.stdout)
                return True, output, None
            except json.JSONDecodeError as e:
                return False, None, f"Invalid JSON output: {e}\n{result.stdout[:500]}"
        else:
            return False, None, f"Command failed with exit code {result.returncode}\nStderr: {result.stderr}"

    except subprocess.TimeoutExpired:
        return False, None, "Command timed out after 5 minutes"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"


def save_output(org_code, output, output_dir):
    """Save the JSON output to source/{org_code}.json"""
    output_file = output_dir / f"{org_code}.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✓ Saved output to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Run find-local-plan.py for all local planning authorities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all authorities and save PDFs (default)
  python bin/run-all-authorities.py

  # Process all authorities without saving PDFs
  python bin/run-all-authorities.py --no-save-pdfs

  # Process only specific authorities
  python bin/run-all-authorities.py --authorities BIR,MAN,LDS

  # Process authorities starting from a specific code
  python bin/run-all-authorities.py --start-from MAN

  # Process only the first 10 authorities
  python bin/run-all-authorities.py --limit 10

  # Dry run to see what would be processed
  python bin/run-all-authorities.py --dry-run
"""
    )

    parser.add_argument(
        '--lpa-lookup-csv',
        default='docs/var/cache/local-planning-authority-lookup.csv',
        help='Path to LPA lookup CSV file (default: docs/var/cache/local-planning-authority-lookup.csv)'
    )

    parser.add_argument(
        '--organisation-csv',
        default='docs/var/cache/organisation.csv',
        help='Path to organisation CSV file (default: docs/var/cache/organisation.csv)'
    )

    parser.add_argument(
        '--output-dir',
        default='source',
        help='Directory to save output JSON files (default: source)'
    )

    parser.add_argument(
        '--save-pdfs',
        dest='save_pdfs',
        action='store_true',
        default=True,
        help='Save PDFs for each authority (default: True)'
    )

    parser.add_argument(
        '--no-save-pdfs',
        dest='save_pdfs',
        action='store_false',
        help='Do not save PDFs for each authority'
    )

    parser.add_argument(
        '--authorities',
        help='Comma-separated list of specific authority codes to process (e.g., BIR,MAN,LDS)'
    )

    parser.add_argument(
        '--start-from',
        help='Start processing from this authority code (useful for resuming)'
    )

    parser.add_argument(
        '--limit',
        type=int,
        help='Maximum number of authorities to process'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be processed without actually running'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Pass --debug flag to find-local-plan.py'
    )

    args = parser.parse_args()

    # Check that both CSV files exist
    lpa_lookup_path = Path(args.lpa_lookup_csv)
    organisation_path = Path(args.organisation_csv)

    if not lpa_lookup_path.exists():
        print(f"Error: LPA lookup CSV not found at {lpa_lookup_path}", file=sys.stderr)
        print("Run 'make' to download required data files", file=sys.stderr)
        sys.exit(1)

    if not organisation_path.exists():
        print(f"Error: Organisation CSV not found at {organisation_path}", file=sys.stderr)
        print("Run 'make' to download required data files", file=sys.stderr)
        sys.exit(1)

    # Load all authorities
    print(f"Loading local planning authorities from {lpa_lookup_path} and {organisation_path}...")
    all_authorities = load_local_planning_authorities(args.lpa_lookup_csv, args.organisation_csv)
    print(f"Found {len(all_authorities)} local planning authorities")

    # Filter authorities based on arguments
    authorities_to_process = all_authorities

    if args.authorities:
        requested_codes = set(code.strip() for code in args.authorities.split(','))
        authorities_to_process = [
            (code, name) for code, name in all_authorities
            if code in requested_codes or code.split(':')[-1] in requested_codes
        ]
        print(f"Filtered to {len(authorities_to_process)} requested authorities")

    if args.start_from:
        # Find the index of start_from authority
        found = False
        for i, (code, name) in enumerate(authorities_to_process):
            if code == args.start_from or code.split(':')[-1] == args.start_from:
                authorities_to_process = authorities_to_process[i:]
                found = True
                print(f"Starting from {name} ({code})")
                break
        if not found:
            print(f"Warning: Start authority '{args.start_from}' not found", file=sys.stderr)

    if args.limit:
        authorities_to_process = authorities_to_process[:args.limit]
        print(f"Limited to first {len(authorities_to_process)} authorities")

    if args.dry_run:
        print("\nDry run - authorities that would be processed:")
        for i, (code, name) in enumerate(authorities_to_process, 1):
            print(f"{i:3d}. {name:50s} ({code})")
        print(f"\nTotal: {len(authorities_to_process)} authorities")
        print(f"Save PDFs: {args.save_pdfs}")
        sys.exit(0)

    # Process each authority
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {
        'success': [],
        'failed': [],
        'errors': []
    }

    for i, (org_code, org_name) in enumerate(authorities_to_process, 1):
        print(f"\n[{i}/{len(authorities_to_process)}]", end=' ')

        success, output, error = run_find_local_plan(
            org_code,
            org_name,
            save_pdfs=args.save_pdfs,
            debug=args.debug
        )

        if success:
            save_output(org_code, output, output_dir)
            results['success'].append((org_code, org_name))

            # Print summary of what was found
            if isinstance(output, list) and len(output) > 0:
                if 'error' in output[0]:
                    print(f"⚠ Warning: {output[0].get('error')}")
                    results['errors'].append((org_code, org_name, output[0].get('error')))
                else:
                    num_plans = len(output)
                    num_docs = sum(len(plan.get('documents', [])) for plan in output)
                    print(f"✓ Found {num_plans} plan(s) with {num_docs} document(s)")
            else:
                print("✓ No plans found")
        else:
            print(f"✗ Failed: {error}")
            results['failed'].append((org_code, org_name, error))

    # Print final summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total processed: {len(authorities_to_process)}")
    print(f"Successful: {len(results['success'])}")
    print(f"Failed: {len(results['failed'])}")
    print(f"With errors: {len(results['errors'])}")

    if results['failed']:
        print("\nFailed authorities:")
        for code, name, error in results['failed']:
            print(f"  - {name} ({code}): {error[:100]}")

    if results['errors']:
        print("\nAuthorities with errors:")
        for code, name, error in results['errors']:
            print(f"  - {name} ({code}): {error[:100]}")

    # Exit with appropriate code
    sys.exit(0 if len(results['failed']) == 0 else 1)


if __name__ == '__main__':
    main()
