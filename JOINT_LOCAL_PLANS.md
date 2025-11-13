# Joint Local Plans

## Overview

Some local planning authorities have created joint local plans with neighboring councils. When this happens, their local plans are hosted on a shared website rather than on individual council websites.

The `find-local-plan.py` script now automatically handles these cases by checking for joint local plans and prioritising the joint plan website domain when searching for local plans.

## How It Works

1. **Joint Plan Mapping**: The file `var/joint-local-plans.json` contains mappings of authorities to their joint local plan websites
2. **Domain Prioritisation**: When searching for an authority that is part of a joint plan, the script will:

   - First try the joint plan website domain
   - Then try the official authority's domain (if different)
   - Then try common domain patterns as usual

3. **Logging**: The script will log when it detects a joint local plan:
   ```
   Authority is part of joint local plan - using joint plan domain: www.swdevelopmentplan.org
     Joint plan: South Worcestershire Development Plan
   ```

## Currently Supported Joint Local Plans

### South East Lincolnshire Local Plan

Website: https://southeastlincslocalplan.org/

Authorities:

- Boston Borough Council (BOT)
- South Holland District Council (SHO)

### Greater Norwich Local Plan

Website: https://www.gnlp.org.uk/

Authorities:

- Norwich City Council (NOW)
- Broadland District Council (BRO)
- South Norfolk Council (SNO)

### Lewes and Eastbourne Local Plan

Website: https://www.lewes-eastbourne.gov.uk/

Authorities:

- Lewes District Council (LEE)
- Eastbourne Borough Council (EAS)

### Central Lincolnshire Local Plan

Website: https://www.n-kesteven.gov.uk/central-lincolnshire/

Authorities:

- City of Lincoln Council (LIC)
- North Kesteven District Council (NKE)
- West Lindsey District Council (WLI)

### Babergh and Mid Suffolk Local Plan

Website: https://www.midsuffolk.gov.uk/joint-local-plan/

Authorities:

- Babergh District Council (BAB)
- Mid Suffolk District Council (MSU)

### North Devon and Torridge Local Plan

Website: https://consult.torridge.gov.uk/kse/folder/91954

Authorities:

- North Devon Council (NDE)
- Torridge District Council (TOR)

### Plymouth and South West Devon Local Plan

Website: https://www.plymouth.gov.uk/adopted-plymouth-and-south-west-devon-joint-local-plan

Authorities:

- Plymouth City Council (PLY)
- South Hams District Council (SHA)
- West Devon District Council (WDE)

### South Worcestershire Development Plan (NOT included in automated scraping)

Website: https://www.swdevelopmentplan.org/

Authorities:

- Malvern Hills District Council (MAV)
- Worcester City Council (WOC)
- Wychavon District Council (WYC)

**NOTE:** This website is protected by Sucuri Cloudproxy bot detection, which blocks automated scraping. These three authorities are NOT included in the `joint-local-plans.json` file and must be maintained manually. Their local plan documents are available at:
- https://www.swdevelopmentplan.org/component/fileman/?view=file&routed=1&name=The-Adopted-SWDP-February-2016.pdf&folder=Documents/South%20Worcestershire%20Development%20Plan/SWDP%202016&container=fileman-files

To prevent these authorities from being overwritten by the automated scraping when running `run-all-authorities.py`, use the `--exclude` flag:
```bash
python bin/run-all-authorities.py --exclude MAV,WOC,WYC
```

### Newcastle and Gateshead Local Plan

Website: https://newcastlegatesheadplan.org/
NOTE: This is the website for the local plan that is under consultation. There are no documents to scrape, therefore this is not included in the joint-local-plans.json

Authorities:

- Newcastle City Council (NEW)
- Gateshead Council (GAT)

## Adding New Joint Local Plans

To add a new joint local plan mapping, edit `var/joint-local-plans.json`:

```json
{
  "local-authority:XXX": {
    "name": "Authority Name",
    "joint-plan-name": "Joint Plan Name",
    "joint-plan-authorities": ["local-authority:XXX", "local-authority:YYY"],
    "joint-plan-website": "https://www.jointplan.gov.uk/",
    "notes": "Additional notes about the joint plan"
  }
}
```

Each authority that is part of a joint plan should have its own entry in the `joint-plans` object.

## Technical Details

- The joint local plans mapping is loaded when `LocalPlanFinder` is initialised
- The `construct_likely_urls()` method checks for joint plans and adds the joint plan domain first
- The feature is backward compatible - it doesn't affect authorities without joint plans
- Individual authority domains are still checked as fallbacks in case some content remains on their own sites
