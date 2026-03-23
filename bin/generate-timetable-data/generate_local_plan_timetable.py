#!/usr/bin/env python3
"""
Generate local plan timetable CSV from VLS, PINs and prototype data.

This script processes VLS (Very Large Spreadsheet), PINs (Planning Inspectorate) and prototype 
data to create a comprehensive local plan timetable with standardised event dates.
"""

import pandas as pd
import json
import os
from datetime import datetime

# Get the script's directory and project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# Define data paths relative to project root
DATA_DIR = os.path.join(PROJECT_ROOT, 'data', 'timetable_data')
VAR_DIR = os.path.join(PROJECT_ROOT, 'var')
DATASET_DIR = os.path.join(PROJECT_ROOT, 'dataset')

# ============================================================================
# CONSTANTS
# ============================================================================

PRIORITY_LPAS = [
    'Amber Valley',
    'Bristol City',
    'Cannock Chase',
    'Chichester',
    'Chorley',
    'Dacorum',
    'Dudley',
    'East Riding of Yorkshire',
    'Epsom and Ewell',
    'Erewash',
    'Great Yarmouth',
    'Horsham',
    'Hyndburn',
    'Isle of Wight',
    "King's Lynn and West Norfolk",
    'Malvern Hills',
    'Newcastle-under-Lyme',
    'North Norfolk',
    'Nuneaton and Bedworth',
    'Pendle',
    'Rutland',
    'Sandwell',
    'South Oxfordshire',
    'South Staffordshire',
    'South Tyneside',
    'Spelthorne',
    'St Albans',
    'Stroud',
    'Surrey Heath',
    'Teignbridge',
    'Tunbridge Wells',
    'Vale of White Horse',
    'West Berkshire',
    'Wiltshire',
    'Winchester',
    'Wirral',
    'Wokingham',
    'Wolverhampton',
    'Worcester',
    'Wychavon'
]

CONSULTATION_START_COLS = [
    'Actual Start date of Second Regulation 18 Consultation',
    'Scheduled date of second Regulation 18 consultation (MMM-YYYY)',
    'Actual Start date of Third Regulation 18 Consultation',
    'Scheduled date of third Regulation 18 consultation (MMM-YYYY)',
    'Actual Start date of Fourth Regulation 18 Consultation',
    'Scheduled date of fourth Regulation 18 consultation (MMM-YYYY)'
]


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def authority_to_slug(authority_name: str) -> str:
    """Convert authority name to slug format."""
    if pd.isna(authority_name) or not authority_name:
        return ''
    slug = authority_name.lower().strip()
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


def to_slug_case(text: str) -> str:
    """Convert text to slug-case (e.g., 'Amber Valley' -> 'amber-valley')"""
    return text.lower().replace(' ', '-').replace("'", '')


def get_first_consultation_date(row):
    """
    Get first Regulation 18 consultation date with fallback logic.

    Uses actual First consultation date if available; falls back to scheduled
    First date if actual is missing and there are dates for 2nd/3rd/4th consultations.
    """
    actual_first = row['Actual Start date of First Regulation 18 Consultation']
    scheduled_first = row['Scheduled date of first Regulation 18 consultation (MMM-YYYY)']

    # Treat '_' as NaN
    if actual_first == '_':
        actual_first = pd.NaT
    if scheduled_first == '_':
        scheduled_first = pd.NaT

    # If actual First date exists, use it
    if pd.notna(actual_first):
        return actual_first

    # If actual First doesn't exist but scheduled First does
    if pd.notna(scheduled_first):
        # If any later date exists, use scheduled First
        if any(pd.notna(row[col]) for col in CONSULTATION_START_COLS):
            return scheduled_first

    # No dates available
    return pd.NaT


def load_reference_overrides():
    """
    Load manual reference overrides from CSV file.

    Returns a dictionary mapping mismatched_reference -> correct_reference
    Override file: data/local-plan-reference-overrides.csv
    """
    overrides = {}
    override_file = os.path.join(PROJECT_ROOT, 'data', 'local-plan-reference-overrides.csv')

    if not os.path.exists(override_file):
        return overrides

    try:
        df_overrides = pd.read_csv(override_file)
        for idx, row in df_overrides.iterrows():
            mismatched = row['mismatched_reference']
            correct = row['correct_reference']
            if pd.notna(mismatched) and pd.notna(correct):
                overrides[str(mismatched).strip()] = str(correct).strip()
    except Exception as e:
        print(f"    Warning: Could not load overrides file: {e}")

    return overrides


def load_adoption_dates_from_local_plans(df_lp):
    """
    Load adoption dates from local-plan.csv.

    Returns a dictionary keyed by (organisations, adoption_date_str) -> reference
    Uses the 'organisations' column (which contains CURIEs like 'local-authority:AYL')
    rather than 'local-planning-authorities' (which contains ONS codes).

    For multi-LPA organisations (e.g., "local-authority:NOW;local-authority:BRO;local-authority:SNO"),
    creates entries for both the full string and each individual component LPA.
    """
    adoption_dates = {}

    # Get records with start-date (which represents adoption dates in local-plan.csv)
    # Use 'organisations' column which has the CURIE format matching df_proto_merged
    lp_with_dates = df_lp[(df_lp['start-date'].notna()) & (df_lp['organisations'].notna())].copy()

    for idx, row in lp_with_dates.iterrows():
        org = row['organisations']
        adoption_date_str = str(row['start-date']).split(' ')[0]  # Extract date part only
        reference = row['reference']

        if org and adoption_date_str and reference:
            # Create entry for full organisation string
            key = (org, adoption_date_str)
            adoption_dates[key] = reference

            # For multi-LPA organisations, also create entries for each component
            if ';' in str(org):
                org_parts = str(org).split(';')
                for org_part in org_parts:
                    org_part = org_part.strip()
                    component_key = (org_part, adoption_date_str)
                    # Only create if not already present (full string takes precedence)
                    if component_key not in adoption_dates:
                        adoption_dates[component_key] = reference

    return adoption_dates


def fuzzy_merge_local_plans(df_proto, df_lp_clean, year_tolerance=3):
    """
    Merge prototype data with local plans using multi-level matching strategy.

    Matching levels (in order):
    1. Exact merge on all three keys: period-start-date, period-end-date, local-planning-authorities
    2. Fuzzy merge: LPA + end date match, start dates within year_tolerance years
    3. Fallback merge: LPA + end date only (ignores start date, handles missing data)
    4. Joint plan merge: For multi-LPA records, match each component LPA against reference database

    Args:
        df_proto: Proto data with columns: period-start-date, period-end-date, local-planning-authorities
        df_lp_clean: LP reference data with same columns plus 'reference'
        year_tolerance: Maximum years between start dates for fuzzy match (default 3)

    Returns:
        Merged dataframe with reference added where matches found
    """
    # Level 1: Exact merge on all three keys
    exact_merge = df_proto.merge(
        df_lp_clean,
        on=['period-start-date', 'period-end-date', 'local-planning-authorities'],
        how='left'
    )

    # Find unmatched rows in proto
    unmatched_proto_idx = exact_merge[exact_merge['reference'].isna()].index

    if len(unmatched_proto_idx) == 0:
        return exact_merge

    # Level 2: Fuzzy matching on start date (within year_tolerance)
    fuzzy_matches_found = 0

    for idx in unmatched_proto_idx:
        if pd.notna(exact_merge.loc[idx, 'reference']):
            # Already matched in a previous iteration
            continue

        proto_start = exact_merge.loc[idx, 'period-start-date']
        proto_end = exact_merge.loc[idx, 'period-end-date']
        proto_lpa = exact_merge.loc[idx, 'local-planning-authorities']

        # Find candidates from lp_clean matching on LPA and end date
        candidates = df_lp_clean[
            (df_lp_clean['local-planning-authorities'] == proto_lpa) &
            (df_lp_clean['period-end-date'] == proto_end)
        ].copy()

        if len(candidates) > 0:
            # Filter by start date tolerance
            year_diff = (candidates['period-start-date'] - proto_start).abs()
            candidates['year_diff'] = year_diff

            fuzzy_candidates = candidates[year_diff <= year_tolerance]

            if len(fuzzy_candidates) > 0:
                # Use the match with closest year
                best_match_idx = fuzzy_candidates['year_diff'].idxmin()
                best_match = fuzzy_candidates.loc[best_match_idx]

                exact_merge.loc[idx, 'reference'] = best_match['reference']
                fuzzy_matches_found += 1

    if fuzzy_matches_found > 0:
        print(f"    Fuzzy matching found {fuzzy_matches_found} additional matches (year tolerance: ±{year_tolerance} years)")

    # Level 3: Fallback matching on end date only (for missing start dates)
    fallback_matches_found = 0
    still_unmatched_idx = exact_merge[exact_merge['reference'].isna()].index

    for idx in still_unmatched_idx:
        proto_end = exact_merge.loc[idx, 'period-end-date']
        proto_lpa = exact_merge.loc[idx, 'local-planning-authorities']

        # Find candidates matching on LPA and end date only
        candidates = df_lp_clean[
            (df_lp_clean['local-planning-authorities'] == proto_lpa) &
            (df_lp_clean['period-end-date'] == proto_end)
        ]

        if len(candidates) > 0:
            # Take the first match (could prioritize by start date if needed)
            best_match = candidates.iloc[0]
            exact_merge.loc[idx, 'reference'] = best_match['reference']
            fallback_matches_found += 1

    if fallback_matches_found > 0:
        print(f"    Fallback matching found {fallback_matches_found} additional matches (end date + LPA only)")

    # Level 4: Joint plan matching (for multi-LPA plans like "CHO;SRI;PRE")
    joint_plan_matches_found = 0
    still_unmatched_idx = exact_merge[exact_merge['reference'].isna()].index

    for idx in still_unmatched_idx:
        proto_end = exact_merge.loc[idx, 'period-end-date']
        proto_lpa_str = exact_merge.loc[idx, 'local-planning-authorities']

        # Check if this is a joint plan (contains semicolons)
        if ';' not in str(proto_lpa_str):
            continue

        # Split the LPA codes (handle both formats: "CHO;SRI;PRE" and "local-authority-eng:CHO;...")
        lpa_parts = str(proto_lpa_str).split(';')

        # Try to match each component LPA
        for lpa_part in lpa_parts:
            lpa_part = lpa_part.strip()
            # Normalize: convert "local-authority-eng:CHO" to "local-authority:CHO"
            lpa_part_normalized = lpa_part.replace('local-authority-eng:', 'local-authority:')

            # Find candidates matching on normalized LPA and end date
            candidates = df_lp_clean[
                (df_lp_clean['local-planning-authorities'] == lpa_part_normalized) &
                (df_lp_clean['period-end-date'] == proto_end)
            ]

            if len(candidates) > 0:
                # Use the first matching reference
                best_match = candidates.iloc[0]
                exact_merge.loc[idx, 'reference'] = best_match['reference']
                joint_plan_matches_found += 1
                break  # Use first component's reference

    if joint_plan_matches_found > 0:
        print(f"    Joint plan matching found {joint_plan_matches_found} additional matches (multi-LPA)")

    return exact_merge


# ============================================================================
# LOAD DATA
# ============================================================================

def load_data():
    """Load all required data files."""

    # Load VLS data
    print("Loading VLS data...")
    df_vls = pd.read_excel(os.path.join(DATA_DIR, 'VLS - LDS & Reg 18 copy.xlsx'),
                           engine='openpyxl', skiprows=1, skipfooter=10)

    # Load hearing dates
    df_all_plans = pd.read_excel(os.path.join(DATA_DIR, 'All Submitted Plans.xlsx'),
                                engine='openpyxl')

    # Merge hearing dates into VLS
    df_vls = pd.merge(
        df_vls,
        df_all_plans.rename(columns={'LDF No': 'LDF Number'})[
            ['LDF Number', 'Actual Hearing Start Date', 'Hearings Close Date']
        ],
        on='LDF Number',
        how='left'
    )

    # Load lookup data
    print("Loading lookup data...")
    df_lpa_lookup = pd.read_csv(os.path.join(VAR_DIR, 'cache', 'local-planning-authority-lookup.csv'))
    lpa_to_name_dic = df_lpa_lookup.rename(columns={'organisation_label': 'name'}).set_index('organisation')['name'].to_dict()

    df_boundary_lookup = pd.read_csv(os.path.join(DATASET_DIR, 'local-plan-boundary.csv'))
    boundary_to_name_dic = df_boundary_lookup.set_index('organisation')['name'].to_dict()

    df_org_lookup = pd.read_csv(os.path.join(VAR_DIR, 'cache', 'organisation.csv'))
    df_org_curie = df_org_lookup[['name', 'prefix', 'reference']].copy()
    df_org_curie['organisation'] = df_org_curie['prefix'] + ':' + df_org_curie['reference']
    org_to_name_dic = df_org_curie.set_index('organisation')['name'].to_dict()

    lookup_dic = lpa_to_name_dic | boundary_to_name_dic | org_to_name_dic

    # Load prototype data
    print("Loading prototype data...")
    df_proto = pd.read_csv(os.path.join(DATA_DIR, 'prototype_data.csv'))
    df_proto = df_proto[[
        'ons_code', 'planning_authority', 'plan_title', 'start_year', 'end_year',
        'published', 'submitted', 'found_sound', 'adopted', 'source_document'
    ]].copy()

    # Load existing local plans
    print("Loading existing local plans...")
    df_lp = pd.read_csv(os.path.join(DATASET_DIR, 'local-plan.csv'))

    return df_vls, df_proto, df_lp, lookup_dic


# ============================================================================
# PROCESS VLS DATA
# ============================================================================

def process_vls_data(df_vls):
    """Process VLS data and extract consultation dates."""

    print("Processing VLS data...")

    # Calculate first consultation date
    df_vls['reg-18-consultation-start'] = df_vls.apply(get_first_consultation_date, axis=1)

    # Select and rename columns
    df_vls_processed = df_vls[[
        'Plan Period Start (YYYY)', 'Plan Period End (YYYY)',
        'ONSCODE',
        'Local Planning Authority',
        'Title of Emerging Development Plan Document',
        'Date of LDS or website timetable (MMM-YYYY)',
        'Actual End date of First Regulation 18 Consultation',
        'Actual End date of Second Regulation 18 Consultation',
        'Actual End date of Third Regulation 18 Consultation',
        'Actual End date of Fourth Regulation 18 Consultation',
        'Emerging Plan Withdrawn / Work Stopped',
        'reg-18-consultation-start',
        'Actual Hearing Start Date',
        'Hearings Close Date'
    ]].copy()

    # Convert datetime columns
    datetime_cols = [
        'Date of LDS or website timetable (MMM-YYYY)',
        'Actual End date of First Regulation 18 Consultation',
        'Actual End date of Second Regulation 18 Consultation',
        'Actual End date of Third Regulation 18 Consultation',
        'Actual End date of Fourth Regulation 18 Consultation',
        'Emerging Plan Withdrawn / Work Stopped',
        'reg-18-consultation-start',
        'Actual Hearing Start Date',
        'Hearings Close Date'
    ]
    for col in datetime_cols:
        df_vls_processed[col] = pd.to_datetime(df_vls_processed[col], errors='coerce')

    # Get latest consultation end date
    df_vls_processed['reg-18-consultation-end'] = (
        df_vls_processed['Actual End date of Fourth Regulation 18 Consultation']
        .combine_first(df_vls_processed['Actual End date of Third Regulation 18 Consultation'])
        .combine_first(df_vls_processed['Actual End date of Second Regulation 18 Consultation'])
        .combine_first(df_vls_processed['Actual End date of First Regulation 18 Consultation'])
    )

    df_vls_processed = df_vls_processed.drop([
        'Actual End date of Fourth Regulation 18 Consultation',
        'Actual End date of Third Regulation 18 Consultation',
        'Actual End date of Second Regulation 18 Consultation',
        'Actual End date of First Regulation 18 Consultation'
    ], axis=1)

    # Convert period columns to nullable integer
    df_vls_processed['Plan Period End (YYYY)'] = df_vls_processed['Plan Period End (YYYY)'].astype('Int64')
    df_vls_processed['Plan Period Start (YYYY)'] = df_vls_processed['Plan Period Start (YYYY)'].astype('Int64')

    # Rename columns
    df_vls_processed.rename(columns={
        'Plan Period Start (YYYY)': 'period-start-year',
        'Plan Period End (YYYY)': 'period-end-year',
        'ONSCODE': 'local-authority-code',
        'Local Planning Authority': 'organisation',
        'Title of Emerging Development Plan Document': 'name',
        'Date of LDS or website timetable (MMM-YYYY)': 'timetable-published',
        'Emerging Plan Withdrawn / Work Stopped': 'plan-withdrawn',
        'Actual Hearing Start Date': 'planning-inspectorate-examination-start',
        'Hearings Close Date': 'planning-inspectorate-examination-end'
    }, inplace=True)

    return df_vls_processed


# ============================================================================
# PROCESS PROTOTYPE DATA
# ============================================================================

def process_prototype_data(df_proto, df_vls_processed, lookup_dic):
    """Process prototype data and merge with VLS."""

    print("Processing prototype data...")

    # Clean up planning authority codes
    df_proto['planning_authority'] = df_proto['planning_authority'].str.replace('-eng', '')
    df_proto = df_proto.sort_values('planning_authority').reset_index(drop=True)

    # Rename columns
    df_proto = df_proto.rename(columns={
        'ons_code': 'local-authority-code',
        'planning_authority': 'local-planning-authorities',
        'plan_title': 'name',
        'start_year': 'period-start-year',
        'end_year': 'period-end-year',
        'published': 'reg-19-publication-local-plan-published',
        'submitted': 'submit-plan-for-examination',
        'found_sound': 'planning-inspectorate-found-sound',
        'adopted': 'plan-adopted',
        'source_document': 'document-url'
    })

    # Merge with VLS data (explicit column selection)
    vls_merge_cols = [col for col in df_vls_processed.columns if col not in ['organisation', 'local-authority-code', 'name', 'period-start-year', 'period-end-year']]
    df_proto_with_vls = pd.merge(
        df_proto,
        df_vls_processed[['local-authority-code', 'name'] + vls_merge_cols],
        on=['local-authority-code', 'name'],
        how='left'
    )

    # Convert to long format
    df_proto_melted = pd.melt(
        df_proto_with_vls,
        id_vars=['local-authority-code', 'local-planning-authorities', 'name',
                 'period-start-year', 'period-end-year', 'document-url']
    )
    df_proto_melted = df_proto_melted.sort_values(['local-planning-authorities', 'name']).reset_index(drop=True)

    # Fix incorrect CURIEs
    mapping = {
        "development-corporation:1": "development-corporation:Q6670544",
        "development-corporation:2": "development-corporation:Q20648596",
    }
    df_proto_melted['local-planning-authorities'] = df_proto_melted['local-planning-authorities'].replace(mapping)

    # Map organisation labels (with fallback for missing ones)
    df_proto_melted['organisation_label'] = df_proto_melted['local-planning-authorities'].map(
        lookup_dic
    ).fillna('London Legacy Development Corporation')

    # Rename columns
    df_proto_melted = df_proto_melted.rename(columns={
        'variable': 'local-plan-event',
        'value': 'start-date'
    })

    # Filter for rows with dates
    df_proto_events = df_proto_melted.loc[df_proto_melted['start-date'].notna()].reset_index(drop=True)

    return df_proto_events


# ============================================================================
# MERGE WITH EXISTING LOCAL PLANS
# ============================================================================

def merge_with_local_plans(df_proto_events, df_lp):
    """Merge prototype data with existing local plans."""

    print("Merging with existing local plans...")

    # Group by common attributes to handle joint plans
    group_cols = ['name', 'period-start-year', 'period-end-year', 'document-url',
                  'local-plan-event', 'start-date']

    df_proto_consolidated = df_proto_events.drop(['local-authority-code'], axis=1).groupby(
        group_cols, as_index=False
    ).agg({
        'local-planning-authorities': ';'.join,
        'organisation_label': 'first',
    }).reset_index(drop=True)

    # Prepare local plans lookup
    df_lp_clean = df_lp[['period-start-date', 'period-end-date', 'organisations', 'reference']].copy()
    df_lp_clean['period-start-date'] = df_lp_clean['period-start-date'].astype('Int64')
    df_lp_clean['period-end-date'] = df_lp_clean['period-end-date'].astype('Int64')
    # Only drop rows missing organisations; keep rows with either start or end date for fuzzy/fallback matching
    df_lp_clean = df_lp_clean.dropna(subset=['organisations', 'period-end-date'])
    df_lp_clean = df_lp_clean.drop_duplicates()

    # Rename and prepare for merge
    df_proto_consolidated_renamed = df_proto_consolidated.rename(columns={
        'period-start-year': 'period-start-date',
        'period-end-year': 'period-end-date'
    })

    df_lp_clean_renamed = df_lp_clean.rename(columns={'organisations': 'local-planning-authorities'})

    # Merge with fuzzy year matching (allows ±3 years on start date)
    df_proto_merged = fuzzy_merge_local_plans(df_proto_consolidated_renamed, df_lp_clean_renamed, year_tolerance=3)

    # Load adoption dates and manual overrides for later matching
    adoption_dates_lookup = load_adoption_dates_from_local_plans(df_lp)
    reference_overrides = load_reference_overrides()

    # Generate local-plan identifiers (use reference if available, else generate from label)
    # Count rows using reference vs generated from label
    rows_from_reference = df_proto_merged['reference'].notna().sum()
    rows_from_label = df_proto_merged['reference'].isna().sum()

    print(f"  - Local-plan identifiers from reference: {rows_from_reference} rows")
    print(f"  - Local-plan identifiers from label: {rows_from_label} rows")

    # Print local plans that had to be generated from labels
    if rows_from_label > 0:
        print(f"\n  Local plans with generated labels ({rows_from_label} total):")
        generated_rows = df_proto_merged[df_proto_merged['reference'].isna()].copy()

        # Group by plan to show unique plans that didn't match
        unique_plans = generated_rows.drop_duplicates(subset=['organisation_label', 'name', 'period-start-date', 'period-end-date'])
        print(f"    {len(unique_plans)} unique plans without reference:\n")

        for idx, row in unique_plans.head(30).iterrows():
            print(f"    - {row['organisation_label']} | {row['name']} | {int(row['period-start-date'])}-{int(row['period-end-date'])}")

        if len(unique_plans) > 30:
            print(f"    ... and {len(unique_plans) - 30} more")

    # For rows without a reference from fuzzy matching, try start-year then end-year then no-year
    # This handles cases where:
    # - start date exists in prototype data but not in source JSON (use end-year fallback)
    # - local-plan.csv has no year suffix for catch-all references (use no-year fallback)
    df_proto_merged['local-plan'] = None

    unmatched_mask = df_proto_merged['reference'].isna()
    unmatched_indices = df_proto_merged[unmatched_mask].index

    adoption_date_matches_found = 0
    adoption_date_matched_plans = {}  # Track (lpa, name, start, end) -> matched_reference

    for idx in unmatched_indices:
        org_slug = authority_to_slug(df_proto_merged.loc[idx, 'organisation_label'])
        start_year = df_proto_merged.loc[idx, 'period-start-date']
        end_year = df_proto_merged.loc[idx, 'period-end-date']
        lpa = df_proto_merged.loc[idx, 'local-planning-authorities']

        # Generate candidate references
        start_ref = f"{org_slug}-local-plan-{int(start_year)}" if pd.notna(start_year) else None
        end_ref = f"{org_slug}-local-plan-{int(end_year)}" if pd.notna(end_year) else None
        no_year_ref = f"{org_slug}-local-plan"

        # Try start year first
        if start_ref:
            start_exists = df_lp_clean_renamed[
                (df_lp_clean_renamed['local-planning-authorities'] == lpa) &
                (df_lp_clean_renamed['reference'] == start_ref)
            ]

            if len(start_exists) > 0:
                df_proto_merged.loc[idx, 'local-plan'] = start_ref
                continue

        # Try end year fallback
        if end_ref:
            end_exists = df_lp_clean_renamed[
                (df_lp_clean_renamed['local-planning-authorities'] == lpa) &
                (df_lp_clean_renamed['reference'] == end_ref)
            ]

            if len(end_exists) > 0:
                df_proto_merged.loc[idx, 'local-plan'] = end_ref
                continue

        # Try no-year reference as final fallback (for catch-all references without year)
        no_year_exists = df_lp_clean_renamed[
            (df_lp_clean_renamed['local-planning-authorities'] == lpa) &
            (df_lp_clean_renamed['reference'] == no_year_ref)
        ]

        if len(no_year_exists) > 0:
            df_proto_merged.loc[idx, 'local-plan'] = no_year_ref
            continue

        # Try adoption date matching for plan-adopted events
        local_plan_event = df_proto_merged.loc[idx, 'local-plan-event']
        start_date = df_proto_merged.loc[idx, 'start-date']

        if local_plan_event == 'plan-adopted' and pd.notna(start_date):
            # Format adoption date as YYYY-MM-DD string
            adoption_date_str = pd.to_datetime(start_date).strftime('%Y-%m-%d')

            # Normalize LPA code (remove -eng suffix if present)
            lpa_normalized = lpa.replace('local-authority-eng:', 'local-authority:')

            # Handle multi-LPA strings (e.g., "local-authority:BRO;local-authority:NOW;local-authority:SNO")
            matched_reference = None
            if ';' in str(lpa_normalized):
                # Split and try each component LPA
                lpa_parts = str(lpa_normalized).split(';')
                for lpa_part in lpa_parts:
                    lpa_part = lpa_part.strip()
                    adoption_key = (lpa_part, adoption_date_str)
                    if adoption_key in adoption_dates_lookup:
                        matched_reference = adoption_dates_lookup[adoption_key]
                        break  # Use first matching reference
            else:
                # Single LPA
                adoption_key = (lpa_normalized, adoption_date_str)
                if adoption_key in adoption_dates_lookup:
                    matched_reference = adoption_dates_lookup[adoption_key]

            # If we found a match, use it
            if matched_reference:
                df_proto_merged.loc[idx, 'local-plan'] = matched_reference
                adoption_date_matches_found += 1

                # Track this plan as adoption-date matched so we can propagate to other events
                plan_key = (lpa, df_proto_merged.loc[idx, 'name'], start_year, end_year)
                adoption_date_matched_plans[plan_key] = matched_reference
                continue

        # Default to start year if none exists in local-plan.csv
        if start_ref:
            df_proto_merged.loc[idx, 'local-plan'] = start_ref
        elif end_ref:
            df_proto_merged.loc[idx, 'local-plan'] = end_ref
        else:
            # Default to no-year reference
            df_proto_merged.loc[idx, 'local-plan'] = no_year_ref

    if adoption_date_matches_found > 0:
        print(f"    Adoption date matching found {adoption_date_matches_found} matched references")

        # Propagate adoption-date matched references to ALL events for the same plan
        # (both null and non-null local-plan values)
        propagated_count = 0

        for plan_key, matched_ref in adoption_date_matched_plans.items():
            lpa, name, start_year, end_year = plan_key

            # Find ALL rows for this plan
            plan_rows = df_proto_merged[
                (df_proto_merged['local-planning-authorities'] == lpa) &
                (df_proto_merged['name'] == name) &
                (df_proto_merged['period-start-date'] == start_year) &
                (df_proto_merged['period-end-date'] == end_year)
            ]

            # Override local-plan for all rows in this plan
            for idx in plan_rows.index:
                if df_proto_merged.loc[idx, 'local-plan'] != matched_ref:
                    df_proto_merged.loc[idx, 'local-plan'] = matched_ref
                    propagated_count += 1

        if propagated_count > 0:
            print(f"    Propagated adoption-date matches to {propagated_count} other events")

    # For rows that already have a reference from fuzzy matching, use it
    matched_mask = ~unmatched_mask
    df_proto_merged.loc[matched_mask, 'local-plan'] = df_proto_merged.loc[matched_mask, 'reference']

    # Apply manual reference overrides
    if len(reference_overrides) > 0:
        override_count = 0
        for idx in df_proto_merged.index:
            current_ref = df_proto_merged.loc[idx, 'local-plan']
            if pd.notna(current_ref) and current_ref in reference_overrides:
                new_ref = reference_overrides[current_ref]
                df_proto_merged.loc[idx, 'local-plan'] = new_ref
                override_count += 1

        if override_count > 0:
            print(f"    Applied {override_count} manual reference overrides")

    # Generate reference codes
    df_proto_merged['reference'] = df_proto_merged['local-plan'] + '-' + df_proto_merged['local-plan-event']

    return df_proto_merged


# ============================================================================
# GENERATE PRIORITY LPAS EVENTS
# ============================================================================

def generate_priority_lpas_events():
    """Generate new local plan event rows for priority LPAs."""

    print("Generating priority LPAs events...")

    # Load the events
    with open(os.path.join(VAR_DIR, 'new-local-plan-events.json')) as f:
        events_data = json.load(f)

    # Remove 'withdrawn' and 'revoked' events
    events_list = [e for e in events_data['events'] if e not in ['withdrawn', 'revoked']]

    # Create one row per LPA-event combination
    results = []

    for lpa_name in PRIORITY_LPAS:
        # For each event, create a row
        for event in events_list:
            results.append({
                'reference': f"{to_slug_case(lpa_name)}-new-local-plan-{event}",
                'name': "Emerging new local plan",
                'local-plan': None,
                'local-plan-event': event,
                'start-date': None,
                'entry-date': datetime.now().strftime('%Y-%m-%d'),
                'notes': "Placeholder to help the authority provide their data",
            })

    df_priority_events = pd.DataFrame(results)
    print(f"  - Priority LPAs events: {len(df_priority_events)} rows")

    return df_priority_events


# ============================================================================
# EXPORT
# ============================================================================

def export_timetable(df_final, output_path=None):
    """Export final timetable CSV."""
    if output_path is None:
        output_path = os.path.join(DATASET_DIR, 'local-plan-timetable.csv')

    print("Exporting timetable...")

    # Format start-date column to date only (remove time component)
    df_final = df_final.copy()
    df_final['start-date'] = df_final['start-date'].apply(
        lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and hasattr(x, 'strftime') else (x if isinstance(x, str) else '')
    )
    df_final = df_final.sort_values('local-plan')

    df_final.to_csv(output_path, index=False)
    print(f"✓ Exported {len(df_final)} rows to {output_path}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main execution function."""

    print("=" * 70)
    print("LOCAL PLAN TIMETABLE GENERATOR")
    print("=" * 70)

    # Load data
    df_vls, df_proto, df_lp, lookup_dic = load_data()

    # Process VLS
    df_vls_processed = process_vls_data(df_vls)

    # Process prototype
    df_proto_events = process_prototype_data(df_proto, df_vls_processed, lookup_dic)
    print(f"  - Prototype data: {len(df_proto_events)} rows")

    # Merge with local plans
    df_proto_merged = merge_with_local_plans(df_proto_events, df_lp)
    print(f"  - Merged data: {len(df_proto_merged)} rows")

    # Create timetable from merged data
    df_timetable_final = df_proto_merged[['reference', 'local-plan', 'local-plan-event', 'start-date']].copy()
    df_timetable_final['entry-date'] = datetime.now().strftime('%Y-%m-%d')

    # Generate priority LPAs events
    df_priority_events = generate_priority_lpas_events()

    # Concatenate timetable with priority LPAs events
    df_combined = pd.concat([df_timetable_final, df_priority_events], ignore_index=True)
    print(f"  - Combined data: {len(df_combined)} rows")

    # Remove specific references
    refs_to_remove = [
        'stratford-on-avon-district-council-local-plan-2021-reg-18-consultation-end',
        'stratford-on-avon-district-council-local-plan-2021-reg-18-consultation-start',
        'stratford-on-avon-district-council-local-plan-2021-timetable-published',
        'chiltern-district-council-local-plan-2016-submit-plan-for-examination',
        'chiltern-district-council-local-plan-2016-reg-19-publication-local-plan-published'
    ]
    df_combined = df_combined[~df_combined['reference'].isin(refs_to_remove)]
    print(f"  - After removing specific references: {len(df_combined)} rows")

    # Export
    export_timetable(df_combined)

    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
