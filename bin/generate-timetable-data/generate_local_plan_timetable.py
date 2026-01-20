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
    df_lp_clean = df_lp_clean.dropna(subset=['period-start-date', 'period-end-date', 'organisations'])
    df_lp_clean = df_lp_clean.drop_duplicates()

    # Merge
    df_proto_merged = df_proto_consolidated.rename(columns={
        'period-start-year': 'period-start-date',
        'period-end-year': 'period-end-date'
    }).merge(
        df_lp_clean.rename(columns={'organisations': 'local-planning-authorities'}),
        on=['period-start-date', 'period-end-date', 'local-planning-authorities'],
        how='left'
    )

    # Generate local-plan identifiers (use reference if available, else generate from label)
    df_proto_merged['local-plan'] = df_proto_merged['reference'].fillna(
        df_proto_merged['organisation_label'].apply(authority_to_slug) + '-local-plan-' +
        df_proto_merged['period-start-date'].astype(str)
    )

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
                'reference': f"{to_slug_case(lpa_name)}-{event}",
                'local-plan': None,
                'local-plan-event': event,
                'start-date': None,
                'entry-date': datetime.now().strftime('%Y-%m-%d')
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

    # Export
    export_timetable(df_combined)

    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
