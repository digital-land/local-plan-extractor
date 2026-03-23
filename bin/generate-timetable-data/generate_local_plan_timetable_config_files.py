#!/usr/bin/env python3
"""
Generate config files for local plan timetable entities.

Creates lookup.csv and entity-organisation.csv files with:
- Updated references for old 40 priority LPAs (keeping existing entities)
- New entity assignments for 271 new LPAs (starting at 5103454)
"""

import pandas as pd
import os
import requests
from io import StringIO

# Get the script's directory and project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATASET_DIR = os.path.join(PROJECT_ROOT, 'dataset')
CONFIG_DIR = os.path.join(DATASET_DIR, 'config')

# Create config directory if it doesn't exist
os.makedirs(CONFIG_DIR, exist_ok=True)

# Old 40 priority LPA short slugs (for identifying old rows)
OLD_40_LPAS_SLUGS = [
    'amber-valley',
    'bristol-city',
    'cannock-chase',
    'chichester',
    'chorley',
    'dacorum',
    'dudley',
    'east-riding-of-yorkshire',
    'epsom-and-ewell',
    'erewash',
    'great-yarmouth',
    'horsham',
    'hyndburn',
    'isle-of-wight',
    'kings-lynn-and-west-norfolk',
    'malvern-hills',
    'newcastle-under-lyme',
    'north-norfolk',
    'nuneaton-and-bedworth',
    'pendle',
    'rutland',
    'sandwell',
    'south-oxfordshire',
    'south-staffordshire',
    'south-tyneside',
    'spelthorne',
    'st-albans',
    'stroud',
    'surrey-heath',
    'teignbridge',
    'tunbridge-wells',
    'vale-of-white-horse',
    'west-berkshire',
    'wiltshire',
    'winchester',
    'wirral',
    'wokingham',
    'wolverhampton',
    'worcester',
    'wychavon'
]

def slugify(text):
    """Convert text to slug format."""
    if pd.isna(text) or not text:
        return ''
    slug = str(text).lower().strip()
    slug = slug.replace('&', 'and')
    slug = slug.replace('–', '-')
    slug = slug.replace('—', '-')
    slug = slug.replace('/', '-')
    slug = slug.replace(' ', '-')
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    slug = slug.strip('-')
    return slug

def fetch_lookup_csv():
    """Fetch lookup.csv from GitHub."""
    url = "https://raw.githubusercontent.com/digital-land/config/refs/heads/main/pipeline/local-plan/lookup.csv"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))

def fetch_provision_csv():
    """Fetch provision.csv from datasette."""
    url = "https://datasette.planning.data.gov.uk/digital-land/provision.csv?_sort=organisation&role__exact=local-planning-authority&dataset__exact=local-plan&_labels=on&_size=max"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))

def load_timetable_csv():
    """Load the generated local-plan-timetable.csv."""
    timetable_path = os.path.join(DATASET_DIR, 'local-plan-timetable.csv')
    return pd.read_csv(timetable_path)

def build_slug_to_full_name_map(provision_df, lookup_df):
    """Build mapping from short slug to full organisation name.

    Extracts slugs from the actual lookup.csv data and matches to provision data.
    """
    mapping = {}

    # Get unique short slugs from actual lookup.csv references in entity range 5101702-5102701
    old_40_rows = lookup_df[lookup_df['entity'].between(5101702, 5102701)].copy()

    # Extract short slug from each reference by finding common suffix patterns
    extracted_slugs = set()

    # Known event suffixes to help identify slug boundaries
    events = [
        'publish-notice-intention-commence',
        'scoping-consultation-start', 'scoping-consultation-end',
        'consultation-start', 'consultation-end',
        'gateway-1-self-assessment',
        'gateway-2-advice-sought', 'gateway-2-advice-published',
        'gateway-3-advice-sought', 'gateway-3-advice-published',
        'gateway-3-further-advice-sought', 'gateway-3-repeat-advice-published',
        'examination-submitted', 'examination-pause-start', 'examination-pause-end',
        'examination-recommendations-published',
        'plan-content-evidence-consultation-start', 'plan-content-evidence-consultation-end',
        'proposed-plan-consultation-start', 'proposed-plan-consultation-end',
        'main-modification-consultation-start', 'main-modification-consultation-end',
        'adopted', 'plan-adopted',
        'annual-monitoring-report-published',
        'plan-evaluation-report-published',
        'additional-consultation-start', 'additional-consultation-end'
    ]

    for ref in old_40_rows['reference']:
        ref_str = str(ref)
        # Try to find which event this reference ends with
        for event in sorted(events, key=len, reverse=True):  # Try longer events first
            if ref_str.endswith(event):
                short_slug = ref_str[:-len(event)-1]  # -1 for the dash before event
                if short_slug:
                    extracted_slugs.add(short_slug)
                break

    print(f"  - Extracted {len(extracted_slugs)} unique short slugs from lookup.csv")

    # For each extracted slug, find matching provision entry
    for old_slug in sorted(extracted_slugs):
        best_match = None
        best_score = 0

        for _, row in provision_df.iterrows():
            full_slug = slugify(row['organisation_label'])
            full_label = str(row['organisation_label']).lower()

            # Try multiple matching strategies in order of preference:
            score = 0

            # 1. Exact prefix match (e.g., "amber-valley-" in "amber-valley-borough-council-")
            if full_slug.startswith(old_slug):
                score = len(old_slug) * 1000  # High weight for prefix

            # 2. Substring match in the slug (e.g., "wolverhampton" in "city-of-wolverhampton-council")
            elif old_slug in full_slug:
                score = len(old_slug) * 500  # Medium weight

            # 3. Substring match in the full label (case-insensitive, e.g., "King's Lynn" contains "lynn")
            elif any(part in full_label for part in old_slug.split('-')):
                # Count how many parts of the old slug appear in the full label
                score = sum(1 for part in old_slug.split('-') if part in full_label) * 100

            if score > best_score:
                best_score = score
                best_match = {
                    'full_name': row['organisation_label'],
                    'full_slug': full_slug,
                    'organisation': row['organisation']
                }

        if best_match:
            mapping[old_slug] = best_match
        else:
            print(f"  WARNING: Could not find matching provision entry for slug '{old_slug}'")

    return mapping

def generate_lookup_csv(lookup_df, timetable_df, slug_mapping):
    """Generate the updated lookup.csv with old + new rows."""

    print("Generating lookup CSV...")

    # Step 1: Extract rows for old 40 LPAs from lookup.csv
    old_40_mask = lookup_df['entity'].between(5101702, 5102701)
    old_40_rows = lookup_df[old_40_mask].copy()

    print(f"  - Found {len(old_40_rows)} old priority LPA rows in lookup.csv")

    # Step 2: Update references for old 40 LPAs
    unmatched_refs = []

    def update_old_reference(row):
        ref = row['reference']
        # Find which extracted slug this reference starts with
        for old_slug in slug_mapping.keys():
            if ref.startswith(old_slug + '-'):
                # Extract the event part (everything after the slug + dash)
                event = ref[len(old_slug) + 1:]
                full_slug = slug_mapping[old_slug]['full_slug']
                return f"{full_slug}-new-local-plan-{event}"
        # If we can't find a mapping, track it and return the original
        unmatched_refs.append(ref)
        return ref

    old_40_rows['reference'] = old_40_rows.apply(update_old_reference, axis=1)

    if unmatched_refs:
        print(f"  WARNING: {len(unmatched_refs)} references could not be matched to slug mappings:")
        for ref in sorted(set(unmatched_refs))[:10]:  # Show first 10 unique
            print(f"    - {ref}")
        if len(set(unmatched_refs)) > 10:
            print(f"    ... and {len(set(unmatched_refs)) - 10} more")

    # Step 3: Generate new rows for 271 new LPAs
    # Get all new-local-plan rows from timetable that are NOT old priority LPAs
    new_lpa_rows = timetable_df[timetable_df['reference'].str.contains('new-local-plan')].copy()

    # Filter out old 40 LPAs using their FULL slugs from slug_mapping
    for old_slug, mapping in slug_mapping.items():
        full_slug = mapping['full_slug']
        new_lpa_rows = new_lpa_rows[~new_lpa_rows['reference'].str.startswith(full_slug + '-')]

    print(f"  - Processing {len(new_lpa_rows)} new LPA rows for entity assignment")

    # Assign consecutive entity numbers starting at 5103454
    next_entity = 5103454
    new_rows_list = []

    for idx, row in new_lpa_rows.iterrows():
        new_row = {
            'prefix': 'development-plan-timetable',
            'resource': '',
            'endpoint': '',
            'entry-number': '',
            'organisation': 'government-organisation:D1342',
            'reference': row['reference'],
            'entity': next_entity,
            'entry-date': '',
            'start-date': '',
            'end-date': ''
        }
        new_rows_list.append(new_row)
        next_entity += 1

    df_new_rows = pd.DataFrame(new_rows_list)

    # Combine old + new rows
    result_df = pd.concat([old_40_rows, df_new_rows], ignore_index=True)

    # Sort by entity for clean output
    result_df = result_df.sort_values('entity').reset_index(drop=True)

    print(f"  - Generated {len(result_df)} total rows (old: {len(old_40_rows)}, new: {len(df_new_rows)})")

    return result_df

def generate_entity_organisation_csv(lookup_output_df, slug_mapping, provision_df):
    """Generate entity-organisation.csv mapping entities to organisation.

    Uses the slug mapping and provision data to assign correct organisation CURIEs.
    """

    print("Generating entity-organisation CSV...")

    # Group by LPA (extract from reference)
    # Sort by entity to get min/max ranges
    lookup_output_df_sorted = lookup_output_df.sort_values('entity').reset_index(drop=True)

    # Build entity ranges per LPA full slug
    lpa_ranges = {}

    for _, row in lookup_output_df_sorted.iterrows():
        ref = row['reference']
        entity = row['entity']

        # Extract LPA full slug from reference (everything before '-new-local-plan-')
        if '-new-local-plan-' in ref:
            lpa_full_slug = ref.split('-new-local-plan-')[0]
        else:
            # Fallback for any other formats
            continue

        if lpa_full_slug not in lpa_ranges:
            lpa_ranges[lpa_full_slug] = {'min': entity, 'max': entity}
        else:
            lpa_ranges[lpa_full_slug]['min'] = min(lpa_ranges[lpa_full_slug]['min'], entity)
            lpa_ranges[lpa_full_slug]['max'] = max(lpa_ranges[lpa_full_slug]['max'], entity)

    # Create rows for entity-organisation.csv with proper organisation CURIEs
    rows_list = []
    for lpa_full_slug in sorted(lpa_ranges.keys()):
        # Find the organisation CURIE for this LPA
        organisation = 'government-organisation:D1342'  # Default fallback

        # Check if this is an old priority LPA (in slug_mapping)
        for old_slug, mapping in slug_mapping.items():
            if mapping['full_slug'] == lpa_full_slug:
                organisation = mapping['organisation']
                break

        # If not found in slug_mapping, search provision_df by slugified label
        if organisation == 'government-organisation:D1342':
            for _, prov_row in provision_df.iterrows():
                if slugify(prov_row['organisation_label']) == lpa_full_slug:
                    organisation = prov_row['organisation']
                    break

        rows_list.append({
            'dataset': 'development-plan-timetable',
            'entity-minimum': lpa_ranges[lpa_full_slug]['min'],
            'entity-maximum': lpa_ranges[lpa_full_slug]['max'],
            'organisation': organisation
        })

    result_df = pd.DataFrame(rows_list)
    print(f"  - Generated {len(result_df)} entity-organisation rows (one per LPA)")

    return result_df

def main():
    print("=" * 70)
    print("LOCAL PLAN TIMETABLE CONFIG FILE GENERATOR")
    print("=" * 70)

    try:
        # Load source data
        print("\nLoading source data...")
        lookup_df = fetch_lookup_csv()
        print(f"  - Fetched lookup.csv ({len(lookup_df)} rows)")

        provision_df = fetch_provision_csv()
        print(f"  - Fetched provision.csv ({len(provision_df)} rows)")

        timetable_df = load_timetable_csv()
        print(f"  - Loaded local-plan-timetable.csv ({len(timetable_df)} rows)")

        # Build slug mapping
        print("\nBuilding slug mapping...")
        slug_mapping = build_slug_to_full_name_map(provision_df, lookup_df)
        print(f"  - Mapped {len(slug_mapping)} old priority LPA slugs")

        # Generate output files
        print()
        lookup_output = generate_lookup_csv(lookup_df, timetable_df, slug_mapping)

        print()
        entity_org_output = generate_entity_organisation_csv(lookup_output, slug_mapping, provision_df)

        # Write output files
        print("\nWriting output files...")
        lookup_path = os.path.join(CONFIG_DIR, 'local-plan-timetable-lookup.csv')
        lookup_output.to_csv(lookup_path, index=False)
        print(f"  ✓ Wrote {lookup_path}")

        entity_org_path = os.path.join(CONFIG_DIR, 'local-plan-timetable-entity-organisation.csv')
        entity_org_output.to_csv(entity_org_path, index=False)
        print(f"  ✓ Wrote {entity_org_path}")

        # Verification
        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)
        print(f"lookup.csv rows: {len(lookup_output)}")
        print(f"entity-organisation.csv rows: {len(entity_org_output)}")

        # Check for duplicates
        duplicate_entities = lookup_output[lookup_output.duplicated(subset=['entity'], keep=False)]
        if len(duplicate_entities) > 0:
            print(f"WARNING: Found {len(duplicate_entities)} duplicate entity numbers!")
        else:
            print("✓ No duplicate entity numbers")

        # Check entity ranges
        min_entity = lookup_output['entity'].min()
        max_entity = lookup_output['entity'].max()
        print(f"Entity range: {min_entity} to {max_entity}")

        # Sample old reference update
        old_samples = lookup_output[lookup_output['entity'].between(5101702, 5101705)]
        if len(old_samples) > 0:
            print(f"\nSample old LPA updated reference:")
            print(f"  {old_samples.iloc[0]['reference']}")

        # Sample new reference
        new_samples = lookup_output[lookup_output['entity'] >= 5103454].head(1)
        if len(new_samples) > 0:
            print(f"\nSample new LPA reference:")
            print(f"  {new_samples.iloc[0]['reference']}")

        print("\n" + "=" * 70)
        print("COMPLETE")
        print("=" * 70)

    except Exception as e:
        print(f"ERROR: {e}")
        raise

if __name__ == '__main__':
    main()
