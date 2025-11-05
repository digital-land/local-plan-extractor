# Successor Authorities

## Overview

Some local planning authorities have been abolished or merged into successor authorities. When this happens, their local plans are typically hosted on the successor authority's website rather than their original (now defunct) website.

The `find-local-plan.py` script now automatically handles these cases by checking for successor authorities and prioritizing the successor's domain when searching for local plans.

## How It Works

1. **Successor Mapping**: The file `var/successor-authorities.json` contains mappings of defunct authorities to their successors
2. **Domain Prioritization**: When searching for an abolished authority, the script will:
   - First try the successor authority's domain
   - Then fall back to the original authority's domain (if it still exists)
   - Then try common domain patterns as usual

3. **Logging**: The script will log when it detects a successor authority:
   ```
   Authority merged/abolished - using successor domain: www.buckinghamshire.gov.uk
     Aylesbury Vale District Council → Buckinghamshire Council
   ```

## Example: Aylesbury Vale

Aylesbury Vale District Council was abolished on 2020-03-31 and merged into Buckinghamshire Council.

**Before this feature:**
- Searched: `https://www.aylesburyvaledc.gov.uk/...` (defunct)
- Result: No pages found

**After this feature:**
- Searches: `https://www.buckinghamshire.gov.uk/...` (successor) first
- Then falls back to: `https://www.aylesburyvaledc.gov.uk/...` if needed
- Result: Finds local plans on Buckinghamshire Council's website

## Currently Supported Successor Authorities

### Buckinghamshire Council (2020-04-01)
Merged from:
- Aylesbury Vale District Council (AYL)
- Chiltern District Council (CHI)
- South Bucks District Council (SBU)
- Wycombe District Council (WYC)

### Cumberland Council (2023-04-01)
Merged from:
- Allerdale Borough Council (ALL)
- Copeland Borough Council (COP)
- Carlisle City Council (CAR)

### Westmorland and Furness Council (2023-04-01)
Merged from:
- Barrow-in-Furness Borough Council (BAR)
- Eden District Council (EDN)
- South Lakeland District Council (SLA)

## Adding New Successor Authorities

To add a new successor authority mapping, edit `var/successor-authorities.json`:

```json
{
  "successors": {
    "local-authority:XXX": {
      "name": "Old Authority Name",
      "end-date": "2020-03-31",
      "successor": "local-authority:YYY",
      "successor-name": "New Authority Name",
      "successor-website": "https://www.newauthority.gov.uk",
      "notes": "Description of the merger"
    }
  }
}
```

## Technical Details

- The successor mapping is loaded when `LocalPlanFinder` is initialized
- The `construct_likely_urls()` method checks for successors and adds the successor domain first
- The feature is backward compatible - it doesn't affect authorities without successors
- Original domains are still checked as fallbacks in case some content remains on old sites
