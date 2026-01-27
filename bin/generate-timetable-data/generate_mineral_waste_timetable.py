#!/usr/bin/env python3
"""
Fuzzy merge two LPA datasets based on LPA name matching.

Merges:
- df_other_plans (Local Council column)
- df_all_plans (LPA column)

Using fuzzy string matching to handle naming differences.
"""

import pandas as pd
import os
from difflib import SequenceMatcher
import warnings
import re
from datetime import datetime


def normalize_lpa_name(name):
    """Normalize LPA name by removing common suffixes and standardizing."""
    name = str(name).lower().strip()

    # Remove common council type suffixes
    suffixes = [
        'county council', 'district council', 'borough council', 'city council',
        'metropolitan borough council', 'unitary authority', 'council',
        'mbc', 'dbc', 'bc', 'cc', 'ua'
    ]

    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()

    # Normalize special characters and abbreviations
    replacements = {
        ' & ': ' and ',
        '&': 'and',
        '/': ' ',
        ',': ' ',
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    # Remove extra spaces
    name = ' '.join(name.split())

    return name


def fuzzy_match_score(str1, str2):
    """Calculate fuzzy match score between two strings (0-100)."""
    # Handle NaN values
    if pd.isna(str1) or pd.isna(str2):
        return 0

    norm1 = normalize_lpa_name(str1)
    norm2 = normalize_lpa_name(str2)

    # Exact match on normalized names
    if norm1 == norm2:
        return 100

    # Check if one is contained in the other (with some leeway)
    if norm1 in norm2 or norm2 in norm1:
        return 90

    # Use sequence matching on normalized names
    ratio = SequenceMatcher(None, norm1, norm2).ratio()
    return int(ratio * 100)

# File paths
DATA_DIR = '/Users/sianteesdale/Documents/GitHub/local-plan-extractor/data/timetable_data'

# Load datasets
print("Loading datasets...")
df_other_plans = pd.read_excel(
    os.path.join(DATA_DIR, '01 - LPA Other Plan Progress - 01 January 2026.xlsx'),
    engine='openpyxl',
    skiprows=2
)

df_all_plans = pd.read_excel(
    os.path.join(DATA_DIR, 'All Submitted Plans.xlsx'),
    engine='openpyxl'
)
df_all_plans = df_all_plans.loc[df_all_plans['DPD Type'] == 'M&W']

print(f"df_other_plans: {len(df_other_plans)} rows")
print(f"df_all_plans (M&W only): {len(df_all_plans)} rows")

# Clean up names and remove NaN values
df_other_plans = df_other_plans.dropna(subset=['Local Council'])
df_all_plans = df_all_plans.dropna(subset=['LPA'])

df_other_plans['Local Council'] = df_other_plans['Local Council'].str.strip()
df_all_plans['LPA'] = df_all_plans['LPA'].str.strip()

# Get unique LPAs from both datasets
other_lpas = df_other_plans['Local Council'].unique()
all_lpas = df_all_plans['LPA'].unique()

print(f"\nUnique LPAs in df_other_plans: {len(other_lpas)}")
print(f"Unique LPAs in df_all_plans: {len(all_lpas)}")

# Create mapping from all_lpas to other_lpas using fuzzy matching
print("\n" + "=" * 80)
print("FUZZY MATCHING LPA NAMES")
print("=" * 80)

# Dictionary to store matches: all_lpas -> other_lpas
lpa_mapping = {}
match_quality = {}
unmatched = []

for all_lpa in all_lpas:
    # Find best match in other_lpas using fuzzy matching
    best_match = None
    best_score = 0

    for other_lpa in other_lpas:
        # Use fuzzy match score
        score = fuzzy_match_score(all_lpa, other_lpa)

        if score > best_score:
            best_score = score
            best_match = other_lpa

    lpa_mapping[all_lpa] = best_match
    match_quality[all_lpa] = best_score

    # Show matches with score < 85 (potential issues)
    if best_score < 85:
        unmatched.append({
            'all_lpas': all_lpa,
            'other_lpas': best_match,
            'score': best_score
        })
        print(f"⚠️  Low confidence match (score: {best_score})")
        print(f"    df_all_plans: '{all_lpa}'")
        print(f"    → df_other_plans: '{best_match}'")
        print()

print(f"\n{len([s for s in match_quality.values() if s >= 85])} high-confidence matches (score >= 85)")
print(f"{len([s for s in match_quality.values() if s < 85])} low-confidence matches (score < 85)")

# Add mapping column to df_all_plans
df_all_plans['Local Council'] = df_all_plans['LPA'].map(lpa_mapping)

# Add plan name fuzzy matching for Step 1
print("\nAdding plan name matching for Step 1...")

# For each row in df_all_plans, find best plan name match in df_other_plans with same Local Council
# Prefer recent/non-adopted plans over old adopted plans
step1_plan_matches = []

for idx, row in df_all_plans.iterrows():
    matched_lpa = row['Local Council']
    all_title = row['Title']

    if pd.isna(matched_lpa):
        # No LPA match, keep original
        step1_plan_matches.append({
            'other_plan_name': None,
            'plan_name_score_step1': 0,
        })
        continue

    # Find all plans from matched LPA in df_other_plans
    lpa_plans_df = df_other_plans[df_other_plans['Local Council'] == matched_lpa].copy()

    if len(lpa_plans_df) == 0:
        step1_plan_matches.append({
            'other_plan_name': None,
            'plan_name_score_step1': 0,
        })
        continue

    # Rank plans: prefer non-adopted, then most recent
    # Non-adopted plans (Adopted is NaN) rank higher than adopted plans
    lpa_plans_df['is_adopted'] = lpa_plans_df['Adopted'].notna()
    lpa_plans_df['date_rank'] = pd.to_datetime(lpa_plans_df['Published'], errors='coerce').fillna(pd.Timestamp('1900-01-01'))

    # Sort to prefer non-adopted (False before True) and recent (descending)
    lpa_plans_df = lpa_plans_df.sort_values(
        ['is_adopted', 'date_rank'],
        ascending=[True, False]
    )

    # Find best matching plan name among the sorted list
    best_plan_name = None
    best_plan_score = 0
    best_idx_in_lpa_df = 0

    for lpa_idx, (_, lpa_row) in enumerate(lpa_plans_df.iterrows()):
        plan_name = lpa_row['Plan Name']
        score = fuzzy_match_score(all_title, plan_name)

        # Higher score wins, but tie-breaking uses the sort order
        if score > best_plan_score:
            best_plan_score = score
            best_plan_name = plan_name
            best_idx_in_lpa_df = lpa_idx

    step1_plan_matches.append({
        'other_plan_name': best_plan_name,
        'plan_name_score_step1': best_plan_score,
    })

step1_plan_match_df = pd.DataFrame(step1_plan_matches)
df_all_plans = pd.concat([
    df_all_plans.reset_index(drop=True),
    step1_plan_match_df
], axis=1)

# Perform the merge with plan name
print("\n" + "=" * 80)
print("MERGING DATASETS (with plan name matching)")
print("=" * 80)

merged = df_other_plans.merge(
    df_all_plans,
    left_on=['Local Council', 'Plan Name'],
    right_on=['Local Council', 'other_plan_name'],
    how='outer',
    indicator=True
)

print(f"\nMerge results:")
print(f"  Both datasets: {len(merged[merged['_merge'] == 'both'])} rows")
print(f"  Only in df_other_plans: {len(merged[merged['_merge'] == 'left_only'])} rows")
print(f"  Only in df_all_plans: {len(merged[merged['_merge'] == 'right_only'])} rows")


# Export results if requested
output_file = '/Users/sianteesdale/Documents/GitHub/local-plan-extractor/merged_lpa_plans.csv'
merged_with_scores = merged.copy()
merged_with_scores['match_score'] = merged_with_scores['Local Council'].map(
    lambda x: next((match_quality[k] for k in all_lpas if lpa_mapping[k] == x), None)
)

print(f"\n{'=' * 80}")
print(f"Skipping export of intermediate merged data (keeping only final cleaned CSV)")
print(f"{'=' * 80}")

# ============================================================================
# STEP 2: FILTER FOR WASTE/MINERAL/M&W AND MERGE WITH ALL SUBMITTED PLANS
# ============================================================================

print(f"\n\n{'=' * 80}")
print("STEP 2: FILTER AND MERGE WITH ALL SUBMITTED PLANS")
print(f"{'=' * 80}\n")

# Filter merged data for waste/mineral/M&W plans
print("Filtering merged data for waste/mineral/M&W plans...")
merged_filtered = merged_with_scores[
    merged_with_scores['Plan Name'].str.lower().str.contains('waste|mineral|m&w', na=False, regex=True)
].copy()

print(f"Filtered merged plans: {len(merged_filtered)} rows (from {len(merged_with_scores)})")

# Remove old adopted plans to avoid duplicate matches
# Keep only the most recent version per (Local Council, Plan Name) combination
print("Removing old adopted plans (keeping most recent per LPA/Plan Name)...")
pre_dedup_count = len(merged_filtered)

merged_filtered = merged_filtered.sort_values(['Local Council', 'Plan Name', 'Published'], ascending=[True, True, False])
merged_filtered = merged_filtered.drop_duplicates(subset=['Local Council', 'Plan Name'], keep='first')

post_dedup_count = len(merged_filtered)
if pre_dedup_count > post_dedup_count:
    print(f"  Removed {pre_dedup_count - post_dedup_count} old/duplicate plans")
else:
    print(f"  No duplicates removed")

# Skip export of filtered merged data (intermediate file)

# Load All Submitted Plans (all types, not just M&W)
print("\nLoading All Submitted Plans.xlsx (all types)...")
df_all_submitted = pd.read_excel(os.path.join(DATA_DIR, 'All Submitted Plans.xlsx'),
                                engine='openpyxl')

print(f"All Submitted Plans: {len(df_all_submitted)} rows")

# Filter All Submitted for waste/mineral/M&W
print("Filtering All Submitted Plans for waste/mineral/M&W...")
df_all_submitted_filtered = df_all_submitted[
    df_all_submitted['Title'].str.lower().str.contains('waste|mineral|m&w', na=False, regex=True)
].copy()

print(f"Filtered All Submitted Plans: {len(df_all_submitted_filtered)} rows")

# Create LPA + Plan Name mapping for second merge
print("\nCreating LPA + Plan Name mapping for All Submitted Plans...")

# First, map each submitted LPA to merged LPA
all_submitted_lpas_list = df_all_submitted_filtered['LPA'].unique()
merged_lpas_list = merged_filtered['Local Council'].unique()

lpa_mapping_2 = {}
match_quality_2 = {}
unmatched_lpas_step2 = []

for submitted_lpa in all_submitted_lpas_list:
    best_match = None
    best_score = 0

    for merged_lpa in merged_lpas_list:
        score = fuzzy_match_score(submitted_lpa, merged_lpa)

        if score > best_score:
            best_score = score
            best_match = merged_lpa

    # Only accept match if score is reasonably high (>= 70%) to avoid spurious matches
    # This prevents "Newham, Barking..." from matching to "Barnsley" (39% score)
    if best_score >= 70:
        lpa_mapping_2[submitted_lpa] = best_match
        match_quality_2[submitted_lpa] = best_score
    else:
        # Low confidence match - leave as NaN to avoid cross-matching errors
        lpa_mapping_2[submitted_lpa] = None
        match_quality_2[submitted_lpa] = best_score
        unmatched_lpas_step2.append({
            'LPA': submitted_lpa,
            'Best Match': best_match,
            'Score': best_score
        })

if unmatched_lpas_step2:
    print(f"\nWarning: {len(unmatched_lpas_step2)} All Submitted LPAs have low confidence matches (< 70%):")
    for item in unmatched_lpas_step2[:5]:
        print(f"  '{item['LPA']}' → '{item['Best Match']}' (score: {item['Score']})")

high_conf_2 = len([s for s in match_quality_2.values() if s >= 90])
med_conf_2 = len([s for s in match_quality_2.values() if 80 <= s < 90])
low_conf_2_count = len([s for s in match_quality_2.values() if s < 80])

print(f"\nLPA Matching Summary (All Submitted → Merged):")
print(f"  {high_conf_2} high confidence (≥90)")
print(f"  {med_conf_2} medium confidence (80-89)")
print(f"  {low_conf_2_count} low confidence (<80)")

# Add mapped LPA to All Submitted
df_all_submitted_filtered['Matched_Local_Council'] = df_all_submitted_filtered['LPA'].map(lpa_mapping_2)

# Now perform plan name matching for rows that share the same LPA
print("\nPerforming plan name fuzzy matching within LPA groups...")

# Group merged_filtered by Local Council to get all plans per LPA
with warnings.catch_warnings():
    warnings.filterwarnings('ignore', category=FutureWarning)
    merged_by_lpa = merged_filtered.groupby('Local Council').apply(
        lambda x: x[['Plan Name', 'Local Council']].drop_duplicates()
    ).reset_index(drop=True)

# For each row in df_all_submitted_filtered, find best plan name match in merged_filtered
plan_matches = []

for idx, row in df_all_submitted_filtered.iterrows():
    matched_lpa = row['Matched_Local_Council']
    submitted_title = row['Title']

    if pd.isna(matched_lpa):
        # No LPA match, keep original
        plan_matches.append({
            'merged_plan_name': None,
            'plan_name_score': 0,
            'all_idx': idx
        })
        continue

    # Find all plans from matched LPA in merged_filtered
    lpa_plans = merged_filtered[merged_filtered['Local Council'] == matched_lpa]['Plan Name'].unique()

    if len(lpa_plans) == 0:
        plan_matches.append({
            'merged_plan_name': None,
            'plan_name_score': 0,
            'all_idx': idx
        })
        continue

    # Find best matching plan name
    # Require minimum 80% match to avoid spurious plan matches
    best_plan_name = None
    best_plan_score = 0
    min_plan_score_threshold = 80

    for plan_name in lpa_plans:
        score = fuzzy_match_score(submitted_title, plan_name)
        if score > best_plan_score:
            best_plan_score = score
            best_plan_name = plan_name

    # Only accept if score meets threshold
    if best_plan_score < min_plan_score_threshold:
        best_plan_name = None
        best_plan_score = 0

    plan_matches.append({
        'merged_plan_name': best_plan_name,
        'plan_name_score': best_plan_score,
        'all_idx': idx
    })

plan_match_df = pd.DataFrame(plan_matches)
df_all_submitted_filtered = pd.concat([
    df_all_submitted_filtered.reset_index(drop=True),
    plan_match_df[['merged_plan_name', 'plan_name_score']]
], axis=1)

# Perform final merge on both LPA and plan name
print("\nMerging datasets on LPA + Plan Name...")
final_merged = merged_filtered.merge(
    df_all_submitted_filtered,
    left_on=['Local Council', 'Plan Name'],
    right_on=['Matched_Local_Council', 'merged_plan_name'],
    how='outer',
    indicator='_merge_final',
    suffixes=('_other', '_submitted')
)

both_final = len(final_merged[final_merged['_merge_final'] == 'both'])
left_only_final = len(final_merged[final_merged['_merge_final'] == 'left_only'])
right_only_final = len(final_merged[final_merged['_merge_final'] == 'right_only'])

print(f"\nFinal merge results:")
print(f"  Both datasets: {both_final} rows")
print(f"  Only in merged plans: {left_only_final} rows")
print(f"  Only in All Submitted: {right_only_final} rows")
print(f"  Total rows: {len(final_merged)}")

# Clean and export final result
print("\nCleaning final dataset...")

# Keep columns from df_other_plans (all 8)
cols_other = ['Local Council', 'Plan Name', 'Published', 'Submitted', 'Found Sound', 'Adopted', '_merge_final', 'plan_name_score']

# Keep columns from df_all_plans (9 specific ones with _submitted suffix)
cols_submitted = [
    'LPA_submitted', 'Title_submitted',
    'Adoption Date_submitted', 'Actual Submission Date_Reg 22_submitted',
    'Actual Hearing Start Date_submitted', 'Hearings Close Date_submitted',
    'Actual Publication Date_Reg 19_submitted', 'Withdrawn Date_submitted',
    'LPA Lookup_submitted'
]

# All columns to keep
cols_to_keep = cols_other + cols_submitted

# Select only the columns we want
final_merged_cleaned = final_merged[cols_to_keep].copy()

# Consolidate duplicate columns: prefer former column, use latter if former is empty
print("Consolidating duplicate date columns...")

# 1. Consolidate Adopted and Adoption Date_submitted -> Adoption Date
final_merged_cleaned['Adoption Date'] = pd.to_datetime(final_merged_cleaned['Adopted'], errors='coerce').fillna(pd.to_datetime(final_merged_cleaned['Adoption Date_submitted'], errors='coerce'))

# 2. Consolidate Published and Actual Publication Date_Reg 19_submitted -> Publication Date
final_merged_cleaned['Publication Date'] = pd.to_datetime(final_merged_cleaned['Published'], errors='coerce').fillna(pd.to_datetime(final_merged_cleaned['Actual Publication Date_Reg 19_submitted'], errors='coerce'))

# 3. Consolidate Submitted and Actual Submission Date_Reg 22_submitted -> Submission Date
final_merged_cleaned['Submission Date'] = pd.to_datetime(final_merged_cleaned['Submitted'], errors='coerce').fillna(pd.to_datetime(final_merged_cleaned['Actual Submission Date_Reg 22_submitted'], errors='coerce'))

# Drop original columns and keep consolidated ones
cols_to_drop = ['Published', 'Submitted', 'Adopted', 'Adoption Date_submitted',
                'Actual Publication Date_Reg 19_submitted', 'Actual Submission Date_Reg 22_submitted']
final_merged_cleaned = final_merged_cleaned.drop(columns=cols_to_drop)

# Reorder columns: put consolidated dates after Plan Name
final_column_order = [
    'Local Council', 'Plan Name', 'Publication Date', 'Submission Date',
    'Found Sound', 'Adoption Date', '_merge_final', 'plan_name_score',
    'LPA_submitted', 'Title_submitted',
    'Actual Hearing Start Date_submitted', 'Hearings Close Date_submitted',
    'Withdrawn Date_submitted', 'LPA Lookup_submitted'
]
final_merged_cleaned = final_merged_cleaned[final_column_order]

# Rename columns to standardized naming scheme
column_rename_map = {
    'Publication Date': 'reg-19-publication-local-plan-published',
    'Submission Date': 'submit-plan-for-examination',
    'Found Sound': 'planning-inspectorate-found-sound',
    'Adoption Date': 'plan-adopted',
    'Actual Hearing Start Date_submitted': 'planning-inspectorate-examination-start',
    'Hearings Close Date_submitted': 'planning-inspectorate-examination-end',
    'Withdrawn Date_submitted': 'plan-withdrawn'
}
final_merged_cleaned = final_merged_cleaned.rename(columns=column_rename_map)

# Format date columns to YYYY-MM-DD format
print("Formatting date columns to YYYY-MM-DD...")
date_columns = [
    'planning-inspectorate-found-sound',
    'reg-19-publication-local-plan-published',
    'submit-plan-for-examination',
    'plan-adopted',
    'planning-inspectorate-examination-start',
    'planning-inspectorate-examination-end',
    'plan-withdrawn'
]

for col in date_columns:
    # Convert to datetime first, then format as YYYY-MM-DD
    final_merged_cleaned[col] = pd.to_datetime(final_merged_cleaned[col], errors='coerce').dt.strftime('%Y-%m-%d')

# Remove unnecessary columns
print("Removing merge tracking columns...")
final_merged_cleaned = final_merged_cleaned.drop(columns=['_merge_final', 'plan_name_score'])

# Replace 'LPA not known to MCLHG' with NaN in LPA Lookup_submitted and rename to organisations
print("Cleaning LPA Lookup column...")
final_merged_cleaned['LPA Lookup_submitted'] = final_merged_cleaned['LPA Lookup_submitted'].replace(
    'LPA not known to MCLHG', pd.NA
)
final_merged_cleaned = final_merged_cleaned.rename(columns={'LPA Lookup_submitted': 'organisations'})

# Consolidate plan title columns: prioritize Title_submitted, fall back to Plan Name
print("Consolidating plan title columns...")
final_merged_cleaned['name'] = final_merged_cleaned['Title_submitted'].fillna(final_merged_cleaned['Plan Name'])
final_merged_cleaned = final_merged_cleaned.drop(columns=['Plan Name'])

# Consolidate geography columns: prioritize LPA_submitted, fall back to Local Council
print("Consolidating geography columns...")
final_merged_cleaned['planning-authorities'] = final_merged_cleaned['LPA_submitted'].fillna(final_merged_cleaned['Local Council'])
final_merged_cleaned = final_merged_cleaned.drop(columns=['Local Council', 'LPA_submitted', 'Title_submitted'])

# Standardise planning authority names (expand abbreviations and fix variations)
print("Standardising planning authority names...")

def standardise_authority_name(name):
    """Standardise authority names by expanding abbreviations and fixing variations."""
    if pd.isna(name):
        return name

    name = str(name).strip()

    # Fix known typos
    name = name.replace('Coucil', 'Council')

    # Expand abbreviations - be comprehensive to catch all patterns
    # Replace CC in various contexts (word boundary, before space/slash/paren/end)
    name = re.sub(r'\bCC\b(?=\s|/|$|\))', 'County Council', name)
    # Replace MBC in various contexts
    name = re.sub(r'\bMBC\b(?=\s|$|\))', 'Metropolitan Borough Council', name)
    # Replace DC in various contexts
    name = re.sub(r'\bDC\b(?=\s|$|\))', 'District Council', name)

    # Handle ampersands
    name = re.sub(r'&', 'and', name)

    # Standardise parentheses formatting
    name = re.sub(r'\s*\(\s*', ' (', name)  # Normalize space before (
    name = re.sub(r'\s*\)\s*', ')', name)   # Normalize space after )

    # Clean up multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()

    # Handle 'with' vs 'With' - standardise to lowercase 'with'
    name = re.sub(r'\bWith\b', 'with', name)

    return name

final_merged_cleaned['planning-authorities'] = final_merged_cleaned['planning-authorities'].apply(standardise_authority_name)

# Melt date columns into long format
print("Melting date columns into long format...")
id_vars = ['planning-authorities', 'name', 'organisations']

melted_df = final_merged_cleaned.melt(
    id_vars=id_vars,
    value_vars=date_columns,
    var_name='local-plan-event',
    value_name='start-date'
)

# Filter out rows where start-date is NaN to reduce size
melted_df = melted_df[melted_df['start-date'].notna()].copy()

# Sort by planning-authorities, name, and event for better readability
melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Consolidate duplicate plan records
print("Consolidating duplicate plan records...")

# Define consolidation rules for plans with multiple authority name variations
# This handles cases where the same plan is recorded under different authority naming conventions
consolidation_rules = [
    {
        'name_pattern': 'Joint Minerals Local Plan',
        'authority_patterns': ['West Sussex'],  # Match any authority containing 'West Sussex'
        'preferred_authority': 'West Sussex/South Downs National Park'
    },
    {
        'name_pattern': 'Joint Minerals Plan',
        'authority_patterns': ['East Riding', 'Hull'],  # Match authorities containing either pattern
        'preferred_authority': 'Hull City Council and East Riding of Yorkshire Council'
    },
    {
        'name_pattern': 'Joint Plan',
        'authority_patterns': ['York'],  # Match authorities containing 'York'
        'preferred_authority': 'North Yorkshire County Council, City of York Council and North York Moors National Park'
    },
    {
        'name_pattern': 'Joint Waste Plan',
        'authority_patterns': ['Nottinghamshire', 'Nottingham'],  # Match authorities containing either pattern
        'preferred_authority': 'Nottinghamshire County Council and Nottingham City Council'
    },
    {
        'name_pattern': 'Joint Minerals.*Waste Plan',  # Match both "and" and "&" variations
        'authority_patterns': ['Cambridgeshire', 'Peterborough'],  # Match authorities containing either pattern
        'preferred_authority': 'Cambridgeshire County Council and Peterborough City Council'
    },
    {
        'name_pattern': 'Waste.*Minerals',  # Match all Waste and Minerals plans (Local Plan, Plan, Sites, etc.)
        'authority_patterns': ['East Sussex', 'Brighton'],  # Match authorities containing these patterns
        'preferred_authority': 'East Sussex County Council, Brighton and Hove City Council and South Downs National Park Authority'
    },
    {
        'name_pattern': '.*Minerals.*Sites',  # Match minerals/mineral sites plans
        'authority_patterns': ['Poole', 'Bournemouth', 'Dorset'],  # Match authorities containing any of these patterns
        'preferred_authority': 'Dorset County Council, Bournemouth Borough Council and Borough of Poole'
    },
    {
        'name_pattern': '.*Waste.*',  # Match waste plans (but not those with minerals - handled above)
        'authority_patterns': ['Poole', 'Bournemouth', 'Dorset'],  # Match authorities containing any of these patterns
        'preferred_authority': 'Dorset County Council, Bournemouth Borough Council and Borough of Poole'
    },
    {
        'name_pattern': 'M&W Local Plan|Minerals & Waste Local Plan',  # Match both M&W and Minerals & Waste variations
        'authority_patterns': ['Buckinghamshire'],  # Match authorities containing 'Buckinghamshire'
        'preferred_authority': 'Buckinghamshire County Council'
    }
]

for rule in consolidation_rules:
    # Find all matching records (name matches AND authority contains pattern)
    mask = melted_df['name'].str.contains(rule['name_pattern'], case=False, na=False)

    for auth_pattern in rule['authority_patterns']:
        mask = mask & melted_df['planning-authorities'].str.contains(auth_pattern, case=False, na=False)

    if mask.sum() > 0:
        # Get the matching and non-matching records
        matching_records = melted_df[mask].copy()
        non_matching = melted_df[~mask].copy()

        # Consolidate: change authority to preferred
        matching_records['planning-authorities'] = rule['preferred_authority']

        # Remove exact duplicates (same plan/event/date combination)
        matching_records = matching_records.drop_duplicates(
            subset=['planning-authorities', 'name', 'local-plan-event', 'start-date']
        )

        # Combine with non-matching records
        melted_df = pd.concat([non_matching, matching_records], ignore_index=True)

        # Re-sort
        melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

        print(f"  Consolidated '{rule['name_pattern']}' under '{rule['preferred_authority']}'")

# Post-processing: standardize York records
york_authority = 'North Yorkshire County Council, City of York Council and North York Moors National Park'
york_mask = melted_df['planning-authorities'] == york_authority
if york_mask.sum() > 0:
    # Standardize plan name to 'Mineral and Waste Joint Plan'
    melted_df.loc[york_mask, 'name'] = 'Mineral and Waste Joint Plan'

    # For adoption events, use 2022-02-16
    adoption_mask = york_mask & (melted_df['local-plan-event'] == 'plan-adopted')
    melted_df.loc[adoption_mask, 'start-date'] = '2022-02-16'

    # Remove any duplicate rows after standardization
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: standardize East Sussex records
east_sussex_authority = 'East Sussex County Council, Brighton and Hove City Council and South Downs National Park Authority'
east_sussex_mask = melted_df['planning-authorities'] == east_sussex_authority
if east_sussex_mask.sum() > 0:
    # Standardize plan name to 'Waste and Minerals Local Plan - Revised Policies'
    melted_df.loc[east_sussex_mask, 'name'] = 'Waste and Minerals Local Plan - Revised Policies'

    # Remove any duplicate rows after standardization
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: standardize Buckinghamshire records
buckinghamshire_authority = 'Buckinghamshire County Council'
buckinghamshire_mask = melted_df['planning-authorities'] == buckinghamshire_authority
if buckinghamshire_mask.sum() > 0:
    # Standardize plan name to 'M&W Local Plan' (consolidate M&W and Minerals & Waste variations)
    mw_plan_mask = buckinghamshire_mask & (
        melted_df['name'].str.contains('M&W Local Plan', case=False, na=False) |
        melted_df['name'].str.contains('Minerals & Waste Local Plan', case=False, na=False)
    )
    if mw_plan_mask.sum() > 0:
        melted_df.loc[mw_plan_mask, 'name'] = 'M&W Local Plan'

        # Remove any duplicate rows after standardization
        melted_df = melted_df.drop_duplicates(
            subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
            keep='first'
        )
        print(f"  Consolidated Buckinghamshire records: M&W Local Plan variations consolidated")
        melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: standardize Cambridgeshire records
cambridgeshire_authority = 'Cambridgeshire County Council and Peterborough City Council'
cambridgeshire_mask = melted_df['planning-authorities'] == cambridgeshire_authority
if cambridgeshire_mask.sum() > 0:
    # Standardize plan name to 'Joint Minerals and Waste Plan' (consolidate & and "and" variations)
    jmwp_mask = cambridgeshire_mask & (
        melted_df['name'].str.contains('Joint Minerals.*Waste Plan', case=False, na=False)
    )
    if jmwp_mask.sum() > 0:
        melted_df.loc[jmwp_mask, 'name'] = 'Joint Minerals and Waste Plan'

        # Remove any duplicate rows after standardization
        melted_df = melted_df.drop_duplicates(
            subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
            keep='first'
        )
        print(f"  Consolidated Cambridgeshire records: Joint Minerals and Waste Plan variations consolidated")
        melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: standardize Essex waste plan records
essex_authority = 'Essex County Council and Southend-on-Sea City Council'
essex_waste_mask = melted_df['planning-authorities'].str.contains('Essex', case=False, na=False) & melted_df['name'].str.contains('Waste.*Plan', case=False, na=False)
if essex_waste_mask.sum() > 0:
    # Consolidate to preferred authority and name
    melted_df.loc[essex_waste_mask, 'planning-authorities'] = essex_authority
    melted_df.loc[essex_waste_mask, 'name'] = 'Waste Local Plan'

    # Remove any duplicate rows after standardization
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Essex records: Waste Plan variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)


# Post-processing: consolidate North London Waste records
print("Standardizing authority names...")

# Find all North London Waste records (individual boroughs, North London Boroughs, and North London Waste)
# that relate to the North London Waste Plan(s)
north_london_name_mask = (
    melted_df['name'].str.contains('North London', case=False, na=False) |
    melted_df['name'].str.contains('Joint North London', case=False, na=False) |
    (melted_df['planning-authorities'] == 'North London Waste')  # Catch older Joint Waste plan
)

north_london_auth_mask = (
    melted_df['planning-authorities'].str.contains('Barnet', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Camden', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Enfield', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Hackney', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Haringey', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Islington', case=False, na=False) |
    melted_df['planning-authorities'].str.contains('Waltham Forest', case=False, na=False) |
    (melted_df['planning-authorities'].str.contains('North London', case=False, na=False) &
     ~melted_df['planning-authorities'].str.contains('East London|South London', case=False, na=False))
)

north_london_mask = north_london_name_mask & north_london_auth_mask

if north_london_mask.sum() > 0:
    # Get the records to consolidate and those not to consolidate
    north_london_records = melted_df[north_london_mask].copy()
    non_north_london = melted_df[~north_london_mask].copy()

    # For both newer and older North London Waste plans, consolidate to full 7-borough authority name
    newer_plan_mask = north_london_records['name'].str.contains('North London Waste Plan|Joint North London Waste Plan', case=False, na=False)
    newer_plan_records = north_london_records[newer_plan_mask].copy()
    older_plan_records = north_london_records[~newer_plan_mask].copy()

    # Consolidate newer plan to full 7-borough authority name and standardize plan name
    if len(newer_plan_records) > 0:
        newer_plan_records['planning-authorities'] = 'London Borough of Barnet, London Borough of Camden, London Borough of Enfield, London Borough of Hackney, London Borough of Haringey, London Borough of Islington and London Borough of Waltham Forest'
        # Standardize plan name to 'North London Waste Plan'
        newer_plan_records['name'] = 'North London Waste Plan'

        # Remove exact duplicates
        newer_plan_records = newer_plan_records.drop_duplicates(
            subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
            keep='first'
        )

    # Also consolidate older plan to full 7-borough authority name
    if len(older_plan_records) > 0:
        older_plan_records['planning-authorities'] = 'London Borough of Barnet, London Borough of Camden, London Borough of Enfield, London Borough of Hackney, London Borough of Haringey, London Borough of Islington and London Borough of Waltham Forest'

        # Remove exact duplicates
        older_plan_records = older_plan_records.drop_duplicates(
            subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
            keep='first'
        )

    # Combine all records back together
    melted_df = pd.concat([non_north_london, newer_plan_records, older_plan_records], ignore_index=True)
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)


# Rename Redbridge authority
redbridge_mask = melted_df['planning-authorities'] == 'Redbridge, London Borough of'
if redbridge_mask.sum() > 0:
    melted_df.loc[redbridge_mask, 'planning-authorities'] = 'London Borough of Redbridge'

# Rename East London authorities
east_london_mask = melted_df['planning-authorities'] == 'Newham, Barking and Dagenham, Havering and Redbridge'
if east_london_mask.sum() > 0:
    melted_df.loc[east_london_mask, 'planning-authorities'] = 'London Borough of Barking and Dagenham, London Borough of Havering, London Borough of Newham and London Borough of Redbridge'

# Add type column based on plan name
def get_plan_type(plan_name):
    """Determine plan type: M (mineral), W (waste), or M;W (joint)"""
    if pd.isna(plan_name):
        return None

    plan_name_lower = str(plan_name).lower()
    has_mineral = 'mineral' in plan_name_lower
    has_waste = 'waste' in plan_name_lower
    has_mw = 'm&w' in plan_name_lower or 'm & w' in plan_name_lower

    if has_mw or (has_mineral and has_waste):
        return 'M;W'
    elif has_mineral:
        return 'M'
    elif has_waste:
        return 'W'
    else:
        return None

melted_df['type'] = melted_df['name'].apply(get_plan_type)

# Rename individual authorities (silent processing)
east_london_authorities_mask = melted_df['planning-authorities'] == 'East London Authorities'
if east_london_authorities_mask.sum() > 0:
    melted_df.loc[east_london_authorities_mask, 'planning-authorities'] = 'East London Waste Authorities'

# Rename long East London name to standard short name
east_london_long_mask = melted_df['planning-authorities'] == 'London Borough of Barking and Dagenham, London Borough of Havering, London Borough of Newham and London Borough of Redbridge'
if east_london_long_mask.sum() > 0:
    melted_df.loc[east_london_long_mask, 'planning-authorities'] = 'East London Waste Authorities'

merseyside_mask = melted_df['planning-authorities'] == 'Merseyside, joint authorities'
if merseyside_mask.sum() > 0:
    melted_df.loc[merseyside_mask, 'planning-authorities'] = 'Merseyside and Halton'

cambs_mask = melted_df['planning-authorities'] == 'Cambridgeshire County Council and Peterborough'
if cambs_mask.sum() > 0:
    melted_df.loc[cambs_mask, 'planning-authorities'] = 'Cambridgeshire County Council and Peterborough City Council'

glos_mask = melted_df['planning-authorities'] == 'Gloucestershire'
if glos_mask.sum() > 0:
    melted_df.loc[glos_mask, 'planning-authorities'] = 'Gloucestershire County Council'

notts_mask = melted_df['planning-authorities'] == 'Nottingham'
if notts_mask.sum() > 0:
    melted_df.loc[notts_mask, 'planning-authorities'] = 'Nottingham City Council'

staffs_mask = melted_df['planning-authorities'] == 'Staffordshire County Council and Stoke'
if staffs_mask.sum() > 0:
    melted_df.loc[staffs_mask, 'planning-authorities'] = 'Staffordshire County Council and Stoke-on-Trent City Council'

buck_mask = melted_df['planning-authorities'] == 'Buckinghamshire Council'
if buck_mask.sum() > 0:
    melted_df.loc[buck_mask, 'planning-authorities'] = 'Buckinghamshire County Council'

essex_mask = melted_df['planning-authorities'] == 'Essex and Southend-on-Sea'
if essex_mask.sum() > 0:
    melted_df.loc[essex_mask, 'planning-authorities'] = 'Essex County Council and Southend-on-Sea City Council'

bdr_mask = melted_df['planning-authorities'] == 'Barnsley, Doncaster and Rotherham Councils'
if bdr_mask.sum() > 0:
    melted_df.loc[bdr_mask, 'planning-authorities'] = 'Barnsley Borough Council, City of Doncaster Council and Rotherham Metropolitan Borough Council'

bed_mask = melted_df['planning-authorities'] == 'Bedford, Central Beds and Luton Councils'
if bed_mask.sum() > 0:
    melted_df.loc[bed_mask, 'planning-authorities'] = 'Bedford Borough Council, Luton Borough Council and Central Bedfordshire Council'

ckms_mask = melted_df['planning-authorities'] == 'Croydon, Kingston, Merton and Sutton'
if ckms_mask.sum() > 0:
    melted_df.loc[ckms_mask, 'planning-authorities'] = 'London Borough of Croydon, Royal Borough of Kingston upon Thames, London Borough of Merton, London Borough of Sutton'

hants_mask = melted_df['planning-authorities'] == 'Hampshire, New Forest, Portsmouth, South Downs and Southampton'
if hants_mask.sum() > 0:
    melted_df.loc[hants_mask, 'planning-authorities'] = 'Hampshire County Council, New Forest District Council, Southampton City Council, Portsmouth City Council and South Downs National Park Authority'

mk_mask = melted_df['planning-authorities'] == 'Milton Keynes'
if mk_mask.sum() > 0:
    melted_df.loc[mk_mask, 'planning-authorities'] = 'Milton Keynes City Council'

rutland_mask = melted_df['planning-authorities'] == 'Rutland Council'
if rutland_mask.sum() > 0:
    melted_df.loc[rutland_mask, 'planning-authorities'] = 'Rutland County Council'

wakefield_mask = melted_df['planning-authorities'] == 'Wakefield Council'
if wakefield_mask.sum() > 0:
    melted_df.loc[wakefield_mask, 'planning-authorities'] = 'Wakefield Metropolitan District Council'

wb_mask = melted_df['planning-authorities'] == 'West Berkshire'
if wb_mask.sum() > 0:
    melted_df.loc[wb_mask, 'planning-authorities'] = 'West Berkshire Council'

ws_mask = melted_df['planning-authorities'] == 'West Sussex'
if ws_mask.sum() > 0:
    melted_df.loc[ws_mask, 'planning-authorities'] = 'West Sussex County Council'

wssdn_mask = melted_df['planning-authorities'] == 'West Sussex/South Downs National Park'
if wssdn_mask.sum() > 0:
    melted_df.loc[wssdn_mask, 'planning-authorities'] = 'West Sussex County Council and South Downs National Park'

wiltshire_mask = melted_df['planning-authorities'] == 'Wiltshire County Council (and Swindon)'
if wiltshire_mask.sum() > 0:
    melted_df.loc[wiltshire_mask, 'planning-authorities'] = 'Wiltshire County Council and Swindon Borough Council'

durham_mask = melted_df['planning-authorities'] == 'County Durham'
if durham_mask.sum() > 0:
    melted_df.loc[durham_mask, 'planning-authorities'] = 'Durham County Council'

# Correct Nottingham City Council Minerals Local Plan to Nottinghamshire County Council
# (The Minerals Local Plan adopted 2021-03-25 belongs to Nottinghamshire, not Nottingham City Council)
nottingham_minerals_mask = (melted_df['planning-authorities'] == 'Nottingham City Council') & (
    melted_df['name'] == 'Minerals Local Plan'
)
if nottingham_minerals_mask.sum() > 0:
    melted_df.loc[nottingham_minerals_mask, 'planning-authorities'] = 'Nottinghamshire County Council'
    print(f"  Corrected Minerals Local Plan authority: Nottingham City Council → Nottinghamshire County Council")

# Correct Nottingham City Council Waste Core Strategy to be a joint plan
nottingham_waste_mask = (melted_df['planning-authorities'] == 'Nottingham City Council') & (
    melted_df['name'] == 'Waste Core Strategy'
)
if nottingham_waste_mask.sum() > 0:
    melted_df.loc[nottingham_waste_mask, 'planning-authorities'] = 'Nottinghamshire County Council and Nottingham City Council'
    print(f"  Corrected Waste Core Strategy authority: Nottingham City Council → Nottinghamshire County Council and Nottingham City Council")

# Consolidate Kent mineral plans - handle after all the other processing
# This will be done in post-processing section after all column creation

# Enrich authority names using var/cache/organisation.csv lookup
print("\nEnriching authority names with var/cache/organisation.csv lookup...")
org_file = 'var/cache/organisation.csv'
if os.path.exists(org_file):
    org_df = pd.read_csv(org_file)
    # Create a lookup set of all organisation names
    org_names = set(org_df['name'].unique())

    # Create a mapping function that enriches authority names
    def enrich_authority_name(auth_name):
        if pd.isna(auth_name):
            return auth_name

        auth_str = str(auth_name).strip()

        # Check if it exactly matches an organisation name
        if auth_str in org_names:
            return auth_str

        # For single-word names without a council designation, search for organisation matches
        if ' ' not in auth_str and not any(word in auth_str.lower() for word in ['council', 'authority', 'authorities']):
            # Special case: prefer County Council for Buckinghamshire
            if auth_str.lower() == 'buckinghamshire':
                return 'Buckinghamshire County Council'

            # Look for any organisation name that starts with this word
            for org_name in sorted(org_names):
                if org_name.lower().startswith(auth_str.lower() + ' '):
                    # Return the first match (sorted for consistency)
                    return org_name

        return auth_str

    # Apply the enrichment
    melted_df['planning-authorities'] = melted_df['planning-authorities'].apply(enrich_authority_name)
    print(f"  Applied organisation name enrichment to planning-authorities")
else:
    print(f"  Warning: var/cache/organisation.csv not found at {org_file}")

# Post-processing: consolidate Bradford records
print("\nConsolidating Bradford records...")
bradford_mask = melted_df['planning-authorities'].isin(['Bradford District Council', 'Bradford Metropolitan Borough Council'])
bradford_records = melted_df[bradford_mask].copy()

if len(bradford_records) > 0:
    # Update planning-authorities to single name and plan name to unified version
    melted_df.loc[bradford_mask, 'planning-authorities'] = 'City of Bradford Metropolitan District Council'
    melted_df.loc[bradford_mask, 'name'] = 'Waste Management Development Plan Document'
    print(f"  Consolidated Bradford records: {bradford_records['planning-authorities'].nunique()} authorities → 1 (City of Bradford Metropolitan District Council)")
    print(f"  Updated plan names to 'Waste Management Development Plan Document'")
else:
    print(f"  No Bradford records found")

# Create planning-authorities-listed column (semi-colon separated)
print("\nCreating planning-authorities-listed column...")

# Special mappings for multi-authority groups that don't parse naturally
special_mappings = {
    'Merseyside and Halton': 'Sefton Metropolitan Borough Council;Wirral Metropolitan Borough Council;Liverpool City Council;St Helens Metropolitan Borough Council;Knowsley Metropolitan Borough Council;Halton Borough Council',
    'Central and Eastern Berkshire Authorities': 'Bracknell Forest Council;Reading Borough Council;Wokingham Borough Council;West Berkshire Council',
    'Greater Manchester Authorities': 'Bolton Metropolitan Borough Council;Bury Metropolitan Borough Council;Manchester City Council;Oldham Metropolitan Borough Council;Rochdale Metropolitan Borough Council;Salford City Council;Stockport Metropolitan Borough Council;Tameside Metropolitan Borough Council;Trafford Metropolitan Borough Council;Wigan Metropolitan Borough Council',
    'East London Waste Authorities': 'London Borough of Barking and Dagenham;London Borough of Havering;London Borough of Newham;London Borough of Redbridge',
    'West of England Partnership': 'Bath and North East Somerset Council;Bristol City Council;North Somerset Council;South Gloucestershire Council',
    'Tees Valley Authorities': 'Darlington Borough Council;Hartlepool Borough Council;Middlesbrough Council;Redcar and Cleveland Borough Council;Stockton-on-Tees Borough Council',
    'Hampshire, New Forest, Portsmouth, South Downs and Southampton': 'Hampshire County Council;New Forest District Council;Southampton City Council;Portsmouth City Council;South Downs National Park Authority',
    'Dorset County Council, Bournemouth Borough Council and Poole Borough Council': 'Dorset County Council;Bournemouth Borough Council;Borough of Poole Council',
    'East Sussex County Council, Brighton and Hove City Council and South Downs National Park Authority': 'East Sussex County Council;Brighton and Hove City Council;South Downs National Park Authority',
}

def parse_authorities_to_list(auth_name):
    """Parse planning-authorities name into semicolon-separated list."""
    if pd.isna(auth_name):
        return None

    auth_str = str(auth_name).strip()

    # Check if it matches a special mapping
    if auth_str in special_mappings:
        return special_mappings[auth_str]

    # Parse naturally by splitting on commas and final " and "
    # Split by comma first
    parts = [p.strip() for p in auth_str.split(',')]

    # Handle the last part which may contain " and "
    if len(parts) > 0:
        last_part = parts[-1]
        # Check if last part contains " and "
        if ' and ' in last_part:
            # Split the last part by " and "
            last_parts = [p.strip() for p in last_part.split(' and ')]
            # Replace the last part with the split parts
            parts = parts[:-1] + last_parts

    # Join with semicolons
    result = ';'.join(parts)
    return result

melted_df['planning-authorities-listed'] = melted_df['planning-authorities'].apply(parse_authorities_to_list)

# Create curie-organisations column
print("Creating curie-organisations column...")

# Load organisation lookup
org_file = 'var/cache/organisation.csv'
org_curies = {}
if os.path.exists(org_file):
    org_lookup_df = pd.read_csv(org_file)
    # Create mapping of organisation name to CURIE
    for _, row in org_lookup_df.iterrows():
        name = str(row['name']).strip()
        prefix = str(row['prefix']).strip()
        reference = str(row['reference']).strip()
        curie = f"{prefix}:{reference}"
        org_curies[name] = curie
    print(f"  Loaded {len(org_curies)} organisation name → CURIE mappings")
else:
    print(f"  Warning: var/cache/organisation.csv not found")

# Load fallback lookup from local-planning-authority-lookup.csv for missing organizations
lpa_lookup_curies = {}
lpa_file = 'var/cache/local-planning-authority-lookup.csv'
if os.path.exists(lpa_file):
    lpa_df = pd.read_csv(lpa_file)
    # Create mapping of authority name to CURIE from local-planning-authority-lookup.csv
    for _, row in lpa_df.iterrows():
        name = str(row['organisation_label']).strip()
        curie = str(row['organisation']).strip()
        lpa_lookup_curies[name] = curie
    print(f"  Loaded {len(lpa_lookup_curies)} LPA name → CURIE mappings (fallback)")
else:
    print(f"  Warning: var/cache/local-planning-authority-lookup.csv not found")

def get_curie_organisations(authorities_listed):
    """Map planning-authorities-listed to CURIE format."""
    if pd.isna(authorities_listed):
        return None

    auth_str = str(authorities_listed).strip()
    if not auth_str:
        return None

    # Split by semicolon
    councils = [c.strip() for c in auth_str.split(';')]

    # Look up each council
    curies = []
    for council in councils:
        # Try exact match first in organisations.csv
        if council in org_curies:
            curies.append(org_curies[council])
            continue

        # Try exact match in local-planning-authority-lookup.csv (fallback)
        if council in lpa_lookup_curies:
            curies.append(lpa_lookup_curies[council])
            continue

        # Try to find best matching organization name
        # Extract the main authority name (e.g., "Barnsley" from "Barnsley Borough Council")
        council_lower = council.lower()
        council_words = council_lower.split()

        # Remove common words to find the main name
        stop_words = {'city', 'of', 'the', 'and', 'borough', 'county', 'district', 'council', 'metropolitan', 'authority', 'authorities'}
        significant_words = [w for w in council_words if w not in stop_words]

        best_score = 0
        best_curie = None
        best_source = None

        # Search in both lookups
        all_lookups = {**org_curies, **lpa_lookup_curies}

        for org_name, curie in all_lookups.items():
            org_lower = org_name.lower()
            org_words = org_lower.split()

            # First, check if main significant words match (highest priority)
            main_match_count = sum(1 for word in significant_words if word in org_words)

            # Secondary score: total word overlap
            council_word_set = set(council_words)
            org_word_set = set(org_words)
            common_count = len(council_word_set & org_word_set)

            # Combined score: (main matches * 100) + common words
            score = (main_match_count * 100) + common_count

            if score > best_score:
                best_score = score
                best_curie = curie

        if best_curie:
            curies.append(best_curie)

    if curies:
        return ';'.join(curies)
    else:
        return None

melted_df['curie-organisations'] = melted_df['planning-authorities-listed'].apply(get_curie_organisations)

# Create geography-codes column
print("Creating geography-codes column...")

# Load local-plan-boundary.csv to create CURIE-to-reference mapping
boundary_file = 'dataset/local-plan-boundary.csv'
curie_to_reference = {}
if os.path.exists(boundary_file):
    boundary_df = pd.read_csv(boundary_file)
    # Create mapping of CURIE to reference
    for _, row in boundary_df.iterrows():
        organisation = str(row['organisation']).strip()
        reference = str(row['reference']).strip()
        curie_to_reference[organisation] = reference
    print(f"  Loaded {len(curie_to_reference)} CURIE → reference mappings")
else:
    print(f"  Warning: dataset/local-plan-boundary.csv not found")

# Load organisation.csv as backup lookup using local-authority-district
org_file = 'var/cache/organisation.csv'
curie_to_district = {}
if os.path.exists(org_file):
    org_lookup_df = pd.read_csv(org_file)
    # Create mapping of organisation (CURIE) to local-authority-district
    for _, row in org_lookup_df.iterrows():
        organisation = str(row['organisation']).strip()
        lad = str(row['local-authority-district']).strip()
        if organisation and lad and lad != 'nan':
            curie_to_district[organisation] = lad
    print(f"  Loaded {len(curie_to_district)} CURIE → local-authority-district mappings (fallback)")
else:
    print(f"  Warning: var/cache/organisation.csv not found")

# Add special mappings for known cases
# Lake District National Park uses national-park-authority:Q27159704 in source data,
# but local-plan-boundary.csv records it as local-planning-authority:E60000320
curie_to_reference['national-park-authority:Q27159704'] = 'E60000320'
# South Downs National Park Authority (Wikidata ID)
curie_to_reference['national-park-authority:Q20198711'] = 'E60000325'
# North York Moors National Park Authority (Wikidata ID)
curie_to_reference['national-park-authority:Q72617669'] = 'E60000322'

def get_geography_codes(curie_organisations):
    """Map curie-organisations to geography reference codes."""
    if pd.isna(curie_organisations):
        return 'NONE'

    curie_str = str(curie_organisations).strip()
    if not curie_str:
        return 'NONE'

    # Split by semicolon
    curies = [c.strip() for c in curie_str.split(';')]

    # Look up each CURIE
    references = []
    for curie in curies:
        if curie in curie_to_reference:
            # First priority: exact match in boundary file
            references.append(curie_to_reference[curie])
        elif curie in curie_to_district:
            # Fallback: use local-authority-district from organisation.csv
            references.append(curie_to_district[curie])
        else:
            references.append('NONE')

    # Join with dashes
    result = '-'.join(references)
    return result

melted_df['geography-codes'] = melted_df['curie-organisations'].apply(get_geography_codes)

# Post-processing: Consolidate Kent mineral plans
kent_consolidate_mask = (melted_df['planning-authorities'] == 'Kent County Council') & (
    (melted_df['name'] == 'Minerals & Waste Early Partial Review') |
    (melted_df['name'] == 'Minerals Sites Plan')
)
if kent_consolidate_mask.sum() > 0:
    melted_df.loc[kent_consolidate_mask, 'name'] = 'Mineral Sites Plan'
    # Remove duplicates after consolidation
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Kent records: Mineral Sites Plan variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: Consolidate Lincolnshire mineral and waste site locations plans
lincolnshire_consolidate_mask = (melted_df['planning-authorities'] == 'Lincolnshire County Council') & (
    (melted_df['name'] == 'Minerals & Waste Local Plan: Site locations doc.') |
    (melted_df['name'] == 'Minerals and Waste Site Locations')
)
if lincolnshire_consolidate_mask.sum() > 0:
    melted_df.loc[lincolnshire_consolidate_mask, 'name'] = 'Minerals and Waste Site Locations'
    # Remove duplicates after consolidation
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Lincolnshire records: Minerals and Waste Site Locations variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: Rename Norfolk plan
norfolk_rename_mask = (melted_df['planning-authorities'] == 'Norfolk County Council') & (
    melted_df['name'] == 'Mineral and Waste Plan Review'
)
if norfolk_rename_mask.sum() > 0:
    melted_df.loc[norfolk_rename_mask, 'name'] = 'Norfolk Minerals and Waste Local Plan'
    print(f"  Renamed Norfolk plan: Mineral and Waste Plan Review → Norfolk Minerals and Waste Local Plan")

# Post-processing: Consolidate Nottinghamshire Waste Core Strategy duplicates
# We now have both 'Nottinghamshire County Council' and 'Nottinghamshire County Council and Nottingham City Council' versions
# Keep the joint authority version and remove duplicates
nottingham_waste_duplicate_mask = (melted_df['planning-authorities'] == 'Nottinghamshire County Council') & (
    melted_df['name'] == 'Waste Core Strategy'
)
if nottingham_waste_duplicate_mask.sum() > 0:
    melted_df = melted_df[~nottingham_waste_duplicate_mask].copy()
    print(f"  Consolidated Nottinghamshire Waste Core Strategy: Removed individual Nottinghamshire entry, keeping joint authority version")

# Post-processing: Consolidate Oxfordshire mineral and waste plans
oxfordshire_consolidate_mask = (melted_df['planning-authorities'] == 'Oxfordshire County Council') & (
    (melted_df['name'] == 'M&W Core Strategy') |
    (melted_df['name'] == 'Minerals & Waste Local Plan -part 1 Core Strategy')
)
if oxfordshire_consolidate_mask.sum() > 0:
    melted_df.loc[oxfordshire_consolidate_mask, 'name'] = 'Oxfordshire Minerals and Waste Local Plan - Part 1 Core Strategy'
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Oxfordshire records: Oxfordshire Minerals and Waste Local Plan - Part 1 Core Strategy variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: Consolidate Essex and Southend-on-Sea Waste plans
southend_consolidate_mask = (melted_df['planning-authorities'] == 'Southend-on-Sea Borough Council') & (
    melted_df['name'] == 'Waste Plan'
)
if southend_consolidate_mask.sum() > 0:
    melted_df.loc[southend_consolidate_mask, 'planning-authorities'] = 'Essex County Council and Southend-on-Sea City Council'
    melted_df.loc[southend_consolidate_mask, 'name'] = 'Waste Local Plan'
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Essex and Southend-on-Sea records: Waste Local Plan variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: Consolidate Surrey Waste plan variations (2008 adoption)
surrey_waste_consolidate_mask = (melted_df['planning-authorities'] == 'Surrey County Council') & (
    (melted_df['name'] == 'Waste CS') |
    (melted_df['name'] == 'Waste Dc Policies') |
    (melted_df['name'] == 'Waste Development')
)
if surrey_waste_consolidate_mask.sum() > 0:
    melted_df.loc[surrey_waste_consolidate_mask, 'name'] = 'Surrey Waste Plan'
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated Surrey records: Surrey Waste Plan variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Post-processing: Rename Wakefield plan
wakefield_rename_mask = (melted_df['planning-authorities'] == 'Wakefield Metropolitan District Council') & (
    melted_df['name'] == 'Waste'
)
if wakefield_rename_mask.sum() > 0:
    melted_df.loc[wakefield_rename_mask, 'name'] = 'Waste Development Plan Document'
    print(f"  Renamed Wakefield plan: Waste → Waste Development Plan Document")

# Post-processing: Rename Warwickshire plan
warwickshire_rename_mask = (melted_df['planning-authorities'] == 'Warwickshire County Council') & (
    melted_df['name'] == 'Warwickshire Minerals Plan'
)
if warwickshire_rename_mask.sum() > 0:
    melted_df.loc[warwickshire_rename_mask, 'name'] = 'Warwickshire Minerals Local Plan'
    print(f"  Renamed Warwickshire plan: Warwickshire Minerals Plan → Warwickshire Minerals Local Plan")

# Post-processing: Update West Sussex Waste Local Plan to include South Downs NPA
west_sussex_waste_mask = (melted_df['planning-authorities'] == 'West Sussex County Council') & (
    melted_df['name'] == 'Waste Local Plan'
)
if west_sussex_waste_mask.sum() > 0:
    melted_df.loc[west_sussex_waste_mask, 'planning-authorities'] = 'West Sussex County Council and South Downs National Park Authority'
    # Recalculate curie-organisations
    melted_df.loc[west_sussex_waste_mask, 'curie-organisations'] = 'local-authority:WSX;national-park-authority:Q20198711'
    # Recalculate geography-codes
    melted_df.loc[west_sussex_waste_mask, 'geography-codes'] = 'E10000032-E60000325'
    print(f"  Updated West Sussex Waste Local Plan: Added South Downs National Park Authority to geography")

# Post-processing: Rename West Berkshire plan
west_berkshire_rename_mask = (melted_df['planning-authorities'] == 'West Berkshire Council') & (
    melted_df['name'] == 'Minerals & Waste'
)
if west_berkshire_rename_mask.sum() > 0:
    melted_df.loc[west_berkshire_rename_mask, 'name'] = 'Minerals and Waste Local Plan'
    print(f"  Renamed West Berkshire plan: Minerals & Waste → Minerals and Waste Local Plan")

# Post-processing: Rename West London Waste Plan
west_london_rename_mask = (melted_df['planning-authorities'] == 'West London Waste Plan') & (
    melted_df['name'] == 'West London Waste Plan (Brent, Ealing, Harrow, Hillingdon,Hounslow, Old Oak & Park Royal Development Corporation, Richmond Upon Thames)'
)
if west_london_rename_mask.sum() > 0:
    melted_df.loc[west_london_rename_mask, 'name'] = 'West London Waste Plan'
    print(f"  Renamed West London plan: Shortened plan name")

# Post-processing: Consolidate West Sussex Joint Minerals Local Plan variations
west_sussex_minerals_consolidate_mask = (melted_df['planning-authorities'] == 'West Sussex County Council and South Downs National Park') & (
    (melted_df['name'] == 'Joint Minerals Local Plan') |
    (melted_df['name'] == 'Joint Minerals Local Plan (with South Downs NPA)')
)
if west_sussex_minerals_consolidate_mask.sum() > 0:
    melted_df.loc[west_sussex_minerals_consolidate_mask, 'name'] = 'Joint Minerals Local Plan'
    melted_df = melted_df.drop_duplicates(
        subset=['planning-authorities', 'name', 'local-plan-event', 'start-date'],
        keep='first'
    )
    print(f"  Consolidated West Sussex records: Joint Minerals Local Plan variations consolidated")
    melted_df = melted_df.sort_values(['planning-authorities', 'name', 'local-plan-event']).reset_index(drop=True)

# Merge with manual search data (URLs and dates)
manual_search_file = 'data/timetable_data/manual-search-for-urls-and-dates.csv'
if os.path.exists(manual_search_file):
    manual_search_df = pd.read_csv(manual_search_file)
    # Merge on planning-authorities and name columns
    melted_df = melted_df.merge(
        manual_search_df[['planning-authorities', 'name', 'start-year', 'end-year', 'documentation-url', 'document-url']],
        on=['planning-authorities', 'name'],
        how='left'
    )
    # Convert start-year and end-year to nullable integers to preserve NaN
    melted_df['start-year'] = melted_df['start-year'].astype('Int64')
    melted_df['end-year'] = melted_df['end-year'].astype('Int64')
    print(f"Merged manual search data: {len(manual_search_df)} records matched")

# Generate local-plan reference
def generate_local_plan_reference(row):
    """Generate a unique reference for each mineral/waste plan."""
    # Extract CURIE codes
    curie_orgs = str(row['curie-organisations']).strip()
    if pd.isna(row['curie-organisations']) or not curie_orgs:
        return None

    # Extract codes from CURIEs (e.g., 'local-authority:BDF' -> 'bdf')
    curies = [c.strip() for c in curie_orgs.split(';')]
    codes = []
    for curie in curies:
        if ':' in curie:
            code = curie.split(':')[1].lower()
            codes.append(code)

    if not codes:
        return None

    codes_str = '-'.join(codes)

    # Determine type text
    plan_type = str(row['type']).strip()
    if plan_type == 'M':
        type_str = 'mineral'
    elif plan_type == 'W':
        type_str = 'waste'
    elif plan_type == 'M;W':
        type_str = 'mineral-waste'
    else:
        type_str = 'plan'

    # Get year from end-year or adoption-date
    year = None
    if pd.notna(row['end-year']):
        try:
            year = int(row['end-year'])
        except:
            pass
    elif pd.notna(row.get('adoption-date')):
        # Try to extract year from adoption-date
        try:
            year = int(str(row['adoption-date'])[:4])
        except:
            pass

    # Build reference
    if year:
        reference = f"{codes_str}-{type_str}-plan-{year}"
    else:
        reference = f"{codes_str}-{type_str}-plan"

    return reference

# Add local-plan reference to melted_df
print("Generating local-plan references...")
# Build a dataframe with unique plan metadata for reference generation
unique_plans = melted_df.groupby(['curie-organisations', 'name']).agg({
    'end-year': 'first',
    'type': 'first',
    'start-date': lambda x: x[x.index[melted_df.loc[x.index, 'local-plan-event'] == 'plan-adopted'].tolist()].iloc[0] if any(melted_df.loc[x.index, 'local-plan-event'] == 'plan-adopted') else None
}).reset_index()
unique_plans = unique_plans.rename(columns={'start-date': 'adoption-date'})

# Generate references
unique_plans['local-plan'] = unique_plans.apply(generate_local_plan_reference, axis=1)

# Create a mapping for lookup
plan_ref_map = dict(zip(zip(unique_plans['curie-organisations'], unique_plans['name']), unique_plans['local-plan']))

# Apply the mapping to melted_df
melted_df['local-plan'] = melted_df.apply(
    lambda row: plan_ref_map.get((row['curie-organisations'], row['name'])),
    axis=1
)

# Add entry-date column (kept in memory for generating other CSVs)
entry_date = datetime.now().strftime('%Y-%m-%d')
melted_df['entry-date'] = entry_date

# Export unique planning-authorities and name combinations with entry-date
# (Kept in memory for reference; currently used to generate manual dataset)
# unique_combos = melted_df[['planning-authorities', 'name']].drop_duplicates().sort_values(['planning-authorities', 'name']).reset_index(drop=True)
# unique_combos['entry-date'] = entry_date
# unique_combos_file = 'dataset/unique-planning-authorities-plans.csv'
# unique_combos.to_csv(unique_combos_file, index=False)
# print(f"✓ Exported {len(unique_combos)} unique combinations to {unique_combos_file}")

# Create mineral-plans.csv and waste-plans.csv
print("\nCreating mineral-plans.csv and waste-plans.csv...")

# Extract all adoption dates once
all_adoption_dates = melted_df[melted_df['local-plan-event'] == 'plan-adopted'][['curie-organisations', 'name', 'start-date']].copy()
all_adoption_dates = all_adoption_dates.rename(columns={'start-date': 'adoption-date'})

# Function to create plans CSV
def create_plans_csv(df, plan_types, filename):
    """Create a plans CSV from the melted dataframe"""
    # Filter by type
    type_df = df[df['type'].isin(plan_types)].copy()

    # Get unique plans with their metadata
    plans = type_df.groupby(['curie-organisations', 'name']).agg({
        'geography-codes': 'first',
        'start-year': 'first',
        'end-year': 'first',
        'documentation-url': 'first',
        'document-url': 'first',
        'local-plan': 'first',
        'type': 'first'
    }).reset_index()

    # Merge adoption dates
    plans = plans.merge(
        all_adoption_dates,
        on=['curie-organisations', 'name'],
        how='left'
    )

    # Sort by curie-organisations
    plans = plans.sort_values('curie-organisations').reset_index(drop=True)

    # Select and reorder columns (local-plan first after curie-organisations)
    plans = plans[['local-plan', 'curie-organisations', 'geography-codes', 'name', 'start-year', 'end-year', 'adoption-date', 'documentation-url', 'document-url']]

    # Rename local-plan to reference for plans CSVs
    plans = plans.rename(columns={'local-plan': 'reference'})

    # Add entry-date
    plans['entry-date'] = entry_date

    # Export
    plans.to_csv(filename, index=False)
    return len(plans)

# Create mineral plans (type='M' or 'M;W')
mineral_count = create_plans_csv(melted_df, ['M', 'M;W'], 'dataset/mineral-plans.csv')
print(f"✓ Exported {mineral_count} mineral plans to dataset/mineral-plans.csv")

# Create waste plans (type='W' or 'M;W')
waste_count = create_plans_csv(melted_df, ['W', 'M;W'], 'dataset/waste-plans.csv')
print(f"✓ Exported {waste_count} waste plans to dataset/waste-plans.csv")

# Create mineral-plan-timetable.csv and waste-plan-timetable.csv
print("\nCreating mineral-plan-timetable.csv and waste-plan-timetable.csv...")

# Create mineral plan timetable (type='M' or 'M;W')
mineral_timetable = melted_df[melted_df['type'].isin(['M', 'M;W'])].copy()
mineral_timetable = mineral_timetable.sort_values(['curie-organisations', 'name', 'local-plan-event']).reset_index(drop=True)
mineral_timetable_file = 'dataset/mineral-plan-timetable.csv'
mineral_timetable.to_csv(mineral_timetable_file, index=False)
print(f"✓ Exported {len(mineral_timetable)} rows to {mineral_timetable_file}")

# Create waste plan timetable (type='W' or 'M;W')
waste_timetable = melted_df[melted_df['type'].isin(['W', 'M;W'])].copy()
waste_timetable = waste_timetable.sort_values(['curie-organisations', 'name', 'local-plan-event']).reset_index(drop=True)
waste_timetable_file = 'dataset/waste-plan-timetable.csv'
waste_timetable.to_csv(waste_timetable_file, index=False)
print(f"✓ Exported {len(waste_timetable)} rows to {waste_timetable_file}")

