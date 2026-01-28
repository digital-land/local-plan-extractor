# Processing GitHub Issues for Local Plan Corrections

This document describes how to use `process_github_issues.py` to automatically process "needs-editing" GitHub issues and apply corrections to local plan JSON files.

## Overview

The script:
1. Fetches open issues labeled "needs-editing" from GitHub
2. Parses the "What needs to be changed?" section
3. Extracts structured data corrections (adoption dates, plan titles, housing numbers)
4. Updates the corresponding JSON files in `local-plan/`
5. Tracks processing state to avoid duplicate processing

## Usage

### Basic Usage

Process all unprocessed issues:
```bash
python bin/process_github_issues.py
```

### Common Options

Process a limited number of issues:
```bash
python bin/process_github_issues.py --limit 5
```

Test what would be changed without applying changes (dry-run mode):
```bash
python bin/process_github_issues.py --dry-run
```

Process a specific issue by number:
```bash
python bin/process_github_issues.py --issue-number 357
```

Regenerate CSV datasets after processing (requires `generate-csvs.py`):
```bash
python bin/process_github_issues.py --regenerate-csvs
```

### Combining Options

```bash
# Dry-run on first 5 issues to preview changes
python bin/process_github_issues.py --dry-run --limit 5

# Process specific issue and regenerate CSVs
python bin/process_github_issues.py --issue-number 357 --regenerate-csvs
```

### GitHub Integration (Optional)

Add comments and close issues automatically after processing:

```bash
# Add comment to issues listing changes applied
python bin/process_github_issues.py --limit 5 --comment

# Close issues after processing
python bin/process_github_issues.py --limit 5 --close

# Both: comment AND close
python bin/process_github_issues.py --limit 5 --comment --close
```

**Authentication**:
- Both features work without authentication (60 requests/hour GitHub rate limit)
- For higher rate limits (5,000 requests/hour), set `GITHUB_TOKEN`:
  ```bash
  export GITHUB_TOKEN=your_github_personal_access_token
  python bin/process_github_issues.py --comment --close
  ```

## How It Works

### Issue Parsing

The script looks for structured information in the issue body:

1. **File Reference**: Extracts from the "File:" field in the issue
   - Format: `6af8f47e5d7c2dc7ea79437297b5031c1ddbc7467a171bd79a93b253894d7b5e.json`

2. **Changes Section**: Reads the "## What needs to be changed?" section and parses:
   - **Adoption dates**: Recognizes formats like:
     - `plan adopted 29/07/2020`
     - `Adoption date: 11 December 2017`
     - `Adoption date: 16 January 2013`
   - **Plan titles**: Extracts from `Local Plan title: <new title>`
   - **Plan periods**: Parses from `Plan period: 2013 - 2033` or similar formats
   - **Housing requirements**: Finds values in `Housing requirement: <number> homes/dwellings`

### Field Updates: Dual File Synchronization

The script updates **two types of files** to keep data synchronized:

#### Local Plan Files (`local-plan/*.json`)
All changes are applied to the local plan file:

| Change Type | JSON Field | Applied To |
|------------|-----------|-----------|
| Adoption date | `adoption-date` | local-plan/ and source/ |
| Plan title | `name` | local-plan/ and source/ |
| Plan period start | `period-start-date` | local-plan/ and source/ |
| Plan period end | `period-end-date` | local-plan/ and source/ |
| Housing requirement (total) | `housing-numbers[0].required-housing` | local-plan/ only |
| Housing requirement (annual) | `housing-numbers[0].annual-required-housing` | local-plan/ only |

#### Source Files (`source/{organisation}.json`)
When adoption-date, name, or period-date changes are made, the script **automatically updates the corresponding source file**:

1. Looks up the organisation code from the local-plan JSON
2. Finds the source file: `source/{organisation}.json`
3. Locates the plan object that contains the endpoint (hash)
4. Updates the same fields in the source file to keep them synchronized

**Example**: When updating issue #357 about Mid Devon:
- Updates `local-plan/6af8f47e5...json` with new adoption date
- Also updates `source/local-authority:MDE.json` with same change
- Both files now have identical adoption date

### Processing Outcomes

Each issue results in one of the following statuses:

- **✓ Updated**: Changes were parsed and applied to JSON
- **⊘ Skipped**: Issue references another issue or already processed
- **⚠ Flagged**: Issue requires manual review (e.g., deletion request)
- **✗ Failed**: Could not parse changes or file not found

### State Management

The script maintains a state file at `collection/issue_processing_state.json` that tracks:
- Successfully processed issues with changes applied
- Skipped issues with reason
- Failed issues with error messages

This prevents reprocessing the same issue multiple times.

## Date Format Support

The date parser automatically handles these formats and converts them to ISO format (YYYY-MM-DD):

- `DD/MM/YYYY` → `29/07/2020` → `2020-07-29`
- `DD Month YYYY` → `11 December 2017` → `2017-12-11`
- `DD Month, YYYY` → `11 December, 2017` → `2017-12-11`
- `DD.MM.YYYY` → `14.04.2016` → `2016-04-14`
- `Month DD, YYYY` → `December 11, 2017` → `2017-12-11`
- `YYYY-MM-DD` → `2016-04-14` → `2016-04-14` (already correct)

Abbreviated month names are also supported: `Jan`, `Feb`, `Mar`, etc.

## Example Output

```
============================================================
Processing GitHub Issues for Local Plan Corrections
============================================================

Found 10 unprocessed 'needs-editing' issues

Issue #357: [Needs editing] Mid Devon Local Plan Review 2013-2033
  File: 6af8f47e5d7c2dc7ea79437297b5031c1ddbc7467a171bd79a93b253894d7b5e.json
  Changes detected:
    - adoption-date: 2017-03-31 → 2020-07-29
  Status: ✓ Updated

Issue #349: [Needs editing] Local Plan 2006-2027
  File: aa19ba3f02904b4584c091d5520a62ca126e2688dbdd668aa9dab336e4d825df.json
  Changes detected:
    - adoption-date: None → 2013-12-11
    - name: Local Plan 2006-2027 → South Gloucestershire Local Plan: Core Strategy 2006-2027
  Status: ✓ Updated

Issue #346: [Needs editing] South Ribble Local Plan
  Status: ⚠ Flagged (requires manual deletion review)

============================================================
Summary:
  Processed: 2
  Skipped: 0
  Failed: 0
============================================================
```

## Workflow Recommendations

### 1. Review in Dry-Run Mode First

Always test changes before applying:
```bash
python bin/process_github_issues.py --dry-run --limit 10
```

Review the output to ensure:
- Changes are being detected correctly
- Dates are parsed in the expected format
- Only intended files are being updated

### 2. Process Issues in Batches

Process a small batch at a time to catch any issues:
```bash
python bin/process_github_issues.py --limit 20
```

Check the state file to see results:
```bash
cat collection/issue_processing_state.json
```

### 3. Regenerate Datasets

After processing issues, regenerate the CSV datasets:
```bash
python bin/generate-csvs.py
```

Or use the automated option:
```bash
python bin/process_github_issues.py --limit 20 --regenerate-csvs
```

### 4. Manual Review for Edge Cases

Some issues require manual review. The script flags these and skips them:

- **Deletion requests**: Issues mentioning "should be deleted" or "not an adopted"
- **References to other issues**: Issues referencing previous discussions or linked issues
- **Unparseable changes**: Issues with unclear change descriptions

Review these manually in the GitHub issue tracker and consider:
- Verifying the source documentation
- Checking if other plans need consolidation
- Ensuring data accuracy

## Troubleshooting

### No issues found
- Check that you're online and GitHub API is accessible
- Verify the repository URL is correct (uses `digital-land/local-plan-extractor`)
- Check GitHub API rate limits (60 req/hour for anonymous, 5000 for authenticated)

### Date not parsing
- The date parser recognizes most common formats
- If a date isn't recognized, it will be flagged as a failed issue
- Check the format and ensure it matches the supported formats above

### JSON file not found
- Verify the file reference in the GitHub issue matches the actual filename
- Check that the hash filename exists in `local-plan/` directory

### File permission errors
- Ensure you have write permissions to the `local-plan/` directory
- Check disk space availability

## Architecture

- **Main script**: `bin/process_github_issues.py` (~450 lines)
- **Date parser utility**: `bin/utils/date_parser.py` (~100 lines)
- **State tracking**: `collection/issue_processing_state.json`
- **Dependencies**: Standard library only (json, urllib, re, datetime, pathlib, argparse)

## Future Enhancements

Potential improvements for future versions:

1. **GitHub API Authentication**: Support `GITHUB_TOKEN` for higher rate limits
2. **Issue Comments**: Add option to comment on issues with applied changes
3. **Auto-close Issues**: Option to automatically close issues after processing
4. **Validation Reports**: More detailed validation output for failed issues
5. **Change Rollback**: Option to revert processed issues if needed
6. **Batch Processing Config**: Support configuration files for recurring batch jobs
