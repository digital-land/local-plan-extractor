#!/usr/bin/env python3
"""
Generate config files for local plan entities.

Creates lookup.csv and entity-organisation.csv files for the 311 new
placeholder local plan rows, with entity numbers continuing from the
existing maximum in the digital-land config repository.
"""

import pandas as pd
import os
import requests
from io import StringIO

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATASET_DIR = os.path.join(PROJECT_ROOT, 'dataset')
CONFIG_DIR = os.path.join(DATASET_DIR, 'config')

os.makedirs(CONFIG_DIR, exist_ok=True)

ENTITY_START = 4220656  # Continues from existing max of 4220655


def slugify(text):
    """Convert text to slug format."""
    if pd.isna(text) or not text:
        return ''
    slug = str(text).lower().strip()
    slug = slug.replace('&', 'and')
    slug = slug.replace('\u2013', '-')
    slug = slug.replace('\u2014', '-')
    slug = slug.replace('/', '-')
    slug = slug.replace(' ', '-')
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    slug = slug.strip('-')
    return slug


def fetch_github_lookup():
    """Fetch existing local-plan lookup.csv from GitHub."""
    url = "https://raw.githubusercontent.com/digital-land/config/refs/heads/main/pipeline/local-plan/lookup.csv"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))


def fetch_provision_csv():
    """Fetch provision.csv from Datasette."""
    url = (
        "https://datasette.planning.data.gov.uk/digital-land/provision.csv"
        "?_sort=organisation&role__exact=local-planning-authority"
        "&dataset__exact=local-plan&_labels=on&_size=max"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))


def load_local_plan_csv():
    """Load the generated local-plan.csv."""
    path = os.path.join(DATASET_DIR, 'local-plan.csv')
    return pd.read_csv(path)


def build_slug_to_curie(provision_df):
    """Build mapping from slugified organisation label to organisation CURIE."""
    mapping = {}
    for _, row in provision_df.iterrows():
        slug = slugify(row['organisation_label'])
        if slug:
            mapping[slug] = row['organisation']
    return mapping


def generate_lookup_csv(local_plan_df, github_lookup_df, slug_to_curie):
    """Generate lookup.csv for the 311 new local-plan references."""

    print("Generating lookup CSV...")

    # Get references already in GitHub lookup (deduplication guard)
    existing_refs = set(github_lookup_df['reference'].astype(str))

    # Filter to new placeholder rows not already in GitHub
    new_rows = local_plan_df[
        local_plan_df['reference'].str.contains('new-local-plan', na=False)
    ].copy()

    new_rows = new_rows[~new_rows['reference'].isin(existing_refs)]
    print(f"  - {len(new_rows)} new references to assign entities to")

    # Assign consecutive entities
    rows_list = []
    next_entity = ENTITY_START

    for _, row in new_rows.iterrows():
        rows_list.append({
            'prefix': 'local-plan',
            'resource': '',
            'endpoint': '',
            'entry-number': '',
            'organisation': 'government-organisation:D1342',
            'reference': row['reference'],
            'entity': next_entity,
            'entry-date': '',
            'start-date': '',
            'end-date': ''
        })
        next_entity += 1

    result_df = pd.DataFrame(rows_list)
    print(f"  - Entity range: {ENTITY_START} to {next_entity - 1}")
    return result_df


def generate_entity_organisation_csv(lookup_df, provision_df, slug_to_curie):
    """Generate entity-organisation.csv for the 311 new local-plan entities.

    Each LPA has exactly 1 entity so entity-minimum = entity-maximum.
    """

    print("Generating entity-organisation CSV...")

    rows_list = []
    unmatched = []

    for _, row in lookup_df.iterrows():
        reference = row['reference']
        entity = row['entity']

        # Derive LPA slug: everything before '-new-local-plan'
        lpa_slug = reference.replace('-new-local-plan', '')

        # Look up organisation CURIE by slug
        organisation = slug_to_curie.get(lpa_slug, '')

        if not organisation:
            # Fallback: try partial match (e.g. national parks drop 'authority' suffix)
            for slug, curie in slug_to_curie.items():
                if lpa_slug in slug or slug in lpa_slug:
                    organisation = curie
                    break

        if not organisation:
            unmatched.append(lpa_slug)

        rows_list.append({
            'dataset': 'local-plan',
            'entity-minimum': entity,
            'entity-maximum': entity,
            'organisation': organisation
        })

    if unmatched:
        print(f"  WARNING: Could not find CURIE for {len(unmatched)} organisations:")
        for slug in sorted(unmatched):
            print(f"    - {slug}")

    result_df = pd.DataFrame(rows_list)
    print(f"  - Generated {len(result_df)} entity-organisation rows")
    return result_df


def main():
    print("=" * 70)
    print("LOCAL PLAN CONFIG FILE GENERATOR")
    print("=" * 70)

    try:
        print("\nLoading source data...")
        github_lookup_df = fetch_github_lookup()
        local_plan_max = github_lookup_df[github_lookup_df['prefix'] == 'local-plan']['entity'].max()
        print(f"  - Fetched GitHub lookup.csv ({len(github_lookup_df)} rows, local-plan max entity: {local_plan_max})")

        provision_df = fetch_provision_csv()
        print(f"  - Fetched provision.csv ({len(provision_df)} rows)")

        local_plan_df = load_local_plan_csv()
        print(f"  - Loaded local-plan.csv ({len(local_plan_df)} rows)")

        print("\nBuilding slug → CURIE mapping...")
        slug_to_curie = build_slug_to_curie(provision_df)
        print(f"  - Mapped {len(slug_to_curie)} organisations")

        print()
        lookup_output = generate_lookup_csv(local_plan_df, github_lookup_df, slug_to_curie)

        print()
        entity_org_output = generate_entity_organisation_csv(lookup_output, provision_df, slug_to_curie)

        print("\nWriting output files...")
        lookup_path = os.path.join(CONFIG_DIR, 'local-plan-lookup.csv')
        lookup_output.to_csv(lookup_path, index=False)
        print(f"  ✓ Wrote {lookup_path}")

        entity_org_path = os.path.join(CONFIG_DIR, 'local-plan-entity-organisation.csv')
        entity_org_output.to_csv(entity_org_path, index=False)
        print(f"  ✓ Wrote {entity_org_path}")

        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)
        print(f"lookup.csv rows: {len(lookup_output)}")
        print(f"entity-organisation.csv rows: {len(entity_org_output)}")

        duplicate_entities = lookup_output[lookup_output.duplicated(subset=['entity'], keep=False)]
        if len(duplicate_entities) > 0:
            print(f"WARNING: Found {len(duplicate_entities)} duplicate entity numbers!")
        else:
            print("✓ No duplicate entity numbers")

        missing_orgs = entity_org_output[entity_org_output['organisation'] == '']
        if len(missing_orgs) > 0:
            print(f"WARNING: {len(missing_orgs)} rows have no organisation CURIE")
        else:
            print("✓ All rows have organisation CURIEs")

        print(f"\nSample rows:")
        sample = lookup_output.head(3)[['prefix', 'reference', 'entity']].to_string(index=False)
        print(sample)

        print(f"\nSample entity-organisation:")
        sample_eo = entity_org_output.head(3).to_string(index=False)
        print(sample_eo)

        print("\n" + "=" * 70)
        print("COMPLETE")
        print("=" * 70)

    except Exception as e:
        print(f"ERROR: {e}")
        raise


if __name__ == '__main__':
    main()
