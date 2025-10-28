import anthropic
import base64
import json
import csv
from pathlib import Path
from typing import Dict, Optional, List
import os
import PyPDF2
import io
import time
import argparse
import sys

from organisation_matcher import OrganisationMatcher

class LocalPlanHousingExtractor:
    def __init__(self, api_key: str, organisation_csv: str = "var/cache/organisation.csv"):
        """Initialize with Anthropic API key and load organisation mapping"""
        self.client = anthropic.Anthropic(api_key=api_key)
        self.max_pages = 32  # Conservative limit to stay under token budget
        self.rate_limit_delay = 2  # Seconds to wait between requests
        self.max_retries = 5  # Maximum number of retry attempts
        self.org_matcher = OrganisationMatcher(organisation_csv)

    def score_page_relevance(self, text: str) -> int:
        """Score a page based on relevance to housing numbers"""
        text_lower = text.lower()
        score = 0
        
        # High value keywords (likely to contain actual numbers)
        high_value = [
            'housing requirement', 'housing target', 'housing provision',
            'housing trajectory', 'housing supply', 'housing table',
            'allocated sites', 'site allocations', 'housing delivery',
            'windfall allowance', 'windfall provision',
            'objectively assessed need', 'housing figures',
            'dwellings per annum', 'homes per annum',
            'five year land supply', 'housing land supply',
            'commitments', 'planning permission', 'under construction',
            'completions', 'pipeline', 'committed sites',
            'broad locations', 'strategic allocation', 'strategic sites',
            'strategic development', 'areas of search', 'broad areas',
            'spatial distribution', 'distribution of development',
            'authority breakdown', 'district breakdown', 'local authority'
        ]

        # Medium value keywords
        medium_value = [
            'spatial strategy', 'housing policy', 'housing distribution',
            'housing allocation', 'development strategy', 'by district',
            'by authority', 'per district', 'per authority'
        ]
        
        # Count high value matches
        for keyword in high_value:
            if keyword in text_lower:
                score += 10
        
        # Count medium value matches
        for keyword in medium_value:
            if keyword in text_lower:
                score += 3
        
        # Bonus for numbers that look like housing figures
        if any(word in text_lower for word in ['dwellings', 'homes', 'units']):
            # Look for substantial numbers
            import re
            numbers = re.findall(r'\b\d{3,6}\b', text)
            if len(numbers) > 2:
                score += 5
        
        # Bonus for tables (often contain housing data)
        if text.count('\n') > 20 and any(char.isdigit() for char in text):
            score += 5
        
        return score
    
    def find_relevant_pages(self, pdf_path: str, top_n: int = None) -> List[int]:
        """Identify the most relevant pages for housing numbers"""
        
        if top_n is None:
            top_n = self.max_pages
        
        page_scores = []
        
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                total_pages = len(reader.pages)
                
                print(f"  Scanning {total_pages} pages for relevant content...", file=sys.stderr)
                
                for page_num in range(total_pages):
                    try:
                        page = reader.pages[page_num]
                        text = page.extract_text()
                        
                        score = self.score_page_relevance(text)
                        
                        if score > 0:
                            page_scores.append((page_num, score))
                            
                    except Exception as e:
                        continue
                
                # Sort by score and take top N pages
                page_scores.sort(key=lambda x: x[1], reverse=True)
                relevant_pages = [page_num for page_num, score in page_scores[:top_n]]
                relevant_pages.sort()  # Keep pages in order
                
                print(f"  Selected top {len(relevant_pages)} pages (scores: {[s for _, s in page_scores[:top_n]]})", file=sys.stderr)
                
        except Exception as e:
            print(f"  Error scanning PDF: {e}", file=sys.stderr)
            return []
        
        return relevant_pages
    
    def extract_pages_to_pdf(self, pdf_path: str, page_numbers: List[int]) -> bytes:
        """Extract specific pages from PDF and return as bytes"""
        
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                writer = PyPDF2.PdfWriter()
                
                for page_num in page_numbers:
                    if page_num < len(reader.pages):
                        writer.add_page(reader.pages[page_num])
                
                # Write to bytes
                output = io.BytesIO()
                writer.write(output)
                output.seek(0)
                
                pdf_bytes = output.read()
                print(f"  Extracted PDF size: {len(pdf_bytes):,} bytes", file=sys.stderr)
                
                return pdf_bytes
                
        except Exception as e:
            print(f"  Error extracting pages: {e}", file=sys.stderr)
            return None
    
    def extract_housing_data(self, pdf_path: str, authority_name: str = None, max_pages: int = None) -> Dict:
        """Extract housing numbers from a local plan PDF using Claude"""
        
        print(f"\nProcessing: {pdf_path}", file=sys.stderr)
        
        if max_pages is None:
            max_pages = self.max_pages
        
        # Step 1: Find most relevant pages
        relevant_pages = self.find_relevant_pages(pdf_path, top_n=max_pages)
        
        if not relevant_pages:
            return {
                "authority": authority_name or Path(pdf_path).stem,
                "pdf_file": str(pdf_path),
                "error": "No relevant pages found with housing keywords"
            }
        
        # Step 2: Extract only relevant pages
        pdf_bytes = self.extract_pages_to_pdf(pdf_path, relevant_pages)
        
        if not pdf_bytes:
            return {
                "authority": authority_name or Path(pdf_path).stem,
                "pdf_file": str(pdf_path),
                "error": "Failed to extract relevant pages"
            }
        
        # Check if still too large, reduce if needed
        if len(pdf_bytes) > 10_000_000:  # ~10MB limit as safety
            print(f"  Warning: Extracted PDF still large, reducing to top {max_pages//2} pages", file=sys.stderr)
            return self.extract_housing_data(pdf_path, authority_name, max_pages=max_pages//2)
        
        # Encode the extracted pages
        pdf_data = base64.standard_b64encode(pdf_bytes).decode('utf-8')
        
        # Create the prompt for Claude
        prompt = """Please analyze this local plan document and extract the following housing information:

1. **Plan Name**: The full title of the local plan document (e.g., "Birmingham Development Plan 2031", "Core Strategy 2020-2035")
2. **Organisation Name**: The name of the local authority or organisation that produced the plan (e.g., "Birmingham City Council", "Bassetlaw District Council"). For joint plans covering multiple authorities, list all authorities separated by commas (e.g., "Babergh District Council, Mid Suffolk District Council")
3. **Housing Numbers**: Extract housing numbers and put them in the housing-numbers array:
   - For SINGLE AUTHORITY plans: Create one entry in the array with the authority's housing numbers
   - For JOINT plans: Create one entry for each member authority with their individual housing numbers

   For each authority, extract:
   - **Required Housing**: The total number of homes required for the plan period (overall housing target/requirement)
   - **Allocated Housing**: The number of homes expected from specifically allocated sites in the plan
   - **Windfall Housing**: The number of homes expected from windfall development (small unallocated sites)
   - **Committed Housing**: The number of homes already granted planning permission or under construction (sometimes called "commitments" or "pipeline")
   - **Broad Locations Housing**: The number of homes expected from broad locations/strategic development areas (areas identified for growth but not yet with detailed allocations)
   - **Annual Required Housing**: The annual housing requirement (homes per year)
   - **Pages**: Page numbers where the information was found
   - **Notes**: Any relevant notes about the housing numbers

Provide your response in this exact JSON format:
{
    "name": "Full title of the plan document",
    "organisation-name": "Name of the local authority/organisation",
    "period-start-date": <year or "">,
    "period-end-date": <year or "">,
    "housing-numbers": [
        {
            "organisation-name": "Name of the authority",
            "required-housing": <number or "">,
            "allocated-housing": <number or "">,
            "windfall-housing": <number or "">,
            "committed-housing": <number or "">,
            "broad-locations-housing": <number or "">,
            "annual-required-housing": <number or "">,
            "pages": "Page numbers where found",
            "notes": "Notes specific to this authority"
        }
    ],
    "confidence": "high/medium/low"
}

Key points:
- Extract the plan name from the cover page or title
- Extract the organisation name from the cover page, title page, or document header (usually the local authority name)
- For joint plans, list all authorities in organisation-name field separated by commas
- IMPORTANT: ALWAYS populate the housing-numbers array with at least one entry
- For single authority plans: Create ONE entry in housing-numbers array with that authority's housing numbers
- For joint plans: Create one entry for EACH member authority in the housing-numbers array
- For JOINT PLANS: Search thoroughly for per-authority housing requirements/targets. These are often in:
  * Policy tables showing spatial distribution of housing across authorities
  * Appendices with authority-by-authority breakdowns
  * Housing trajectory tables with district/authority columns
  * "Spatial distribution" or "Distribution of development" sections
  * Look for both annual and total plan period figures for each authority
- If requirement is per annum (e.g., "400 homes per annum"), multiply by plan period for total
- Look in housing trajectory tables, housing land supply tables, and policy summaries
- Committed housing may be listed as "completions + commitments", "permissions", or "pipeline"
- Broad locations may be called "strategic allocations", "strategic sites", "broad locations for growth", or "areas of search"
- IMPORTANT - ALLOCATED HOUSING: Search thoroughly for allocated housing numbers. Look for:
  * Housing land supply tables with columns for "Allocations", "Allocated sites", "Plan allocations"
  * Site allocation policies (e.g., "Policy JP-Allocation 1", "Site H1", "Strategic Allocation SA1")
  * Appendices listing all allocated sites with dwelling capacities
  * Housing trajectory tables breaking down supply by source (allocations vs commitments vs windfall)
  * For JOINT PLANS specifically: Look for per-authority breakdowns of "Places for Everyone allocations", "Strategic allocations", "Plan allocations" by district
  * If a table shows housing supply broken down by category, "allocations" is typically separate from "commitments" and "windfall"
  * If site allocations are described as employment-only, industrial, commercial, or non-residential, then allocated-housing is 0
  * If you find that an authority has strategic allocations but they are all employment sites with no residential component, use 0 not ""
- IMPORTANT - ANNUAL REQUIRED HOUSING: When extracting annual-required-housing:
  * Calculate the expected annual figure: required-housing ÷ plan period years
  * If you find an annual requirement in the document, verify it matches the calculation (within rounding)
  * If the extracted annual figure doesn't match the calculation, double-check both numbers
  * If no annual figure is found in the document, leave it as "" (it will be calculated automatically)
- IMPORTANT: For housing number fields, distinguish between:
  * Use 0 (zero) if you are confident the value is actually zero (e.g., no residential allocations, employment-only sites, or document explicitly states "no allocations")
  * Use "" (empty string) only if the number cannot be found or is unclear in the document
- Keep notes concise but mention if figures overlap (e.g., if commitments are included in allocated sites) and explain the basis for 0 values
"""

        try:
            print(f"  Sending {len(relevant_pages)} pages to Claude API...", file=sys.stderr)
            
            # Retry logic for rate limits
            for attempt in range(1, self.max_retries + 1):
                try:
                    # Call Claude API with PDF
                    message = self.client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=4096,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "document",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "application/pdf",
                                            "data": pdf_data
                                        }
                                    },
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ]
                            }
                        ]
                    )
                    
                    # If successful, break out of retry loop
                    break
                    
                except anthropic.RateLimitError as e:
                    if attempt < self.max_retries:
                        wait_time = 60 * attempt  # Exponential backoff: 60s, 120s, 180s, etc.
                        print(f"  Rate limit hit. Waiting {wait_time}s before retry {attempt}/{self.max_retries}...", file=sys.stderr)
                        time.sleep(wait_time)
                    else:
                        raise  # Re-raise if we've exhausted retries
                
                except anthropic.BadRequestError as e:
                    error_msg = str(e)
                    if 'prompt is too long' in error_msg and max_pages > 8:
                        print(f"  Prompt too long, reducing to {max_pages//2} pages and retrying...", file=sys.stderr)
                        return self.extract_housing_data(pdf_path, authority_name, max_pages=max_pages//2)
                    else:
                        raise  # Re-raise other bad request errors
            
            # Extract the response
            response_text = message.content[0].text
            
            # Try to parse JSON from the response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                housing_data = json.loads(json_str)
            else:
                housing_data = {
                    "raw_response": response_text,
                    "error": "Could not parse JSON response"
                }
            
            # Add metadata
            housing_data['authority'] = authority_name or Path(pdf_path).stem
            housing_data['pdf_file'] = str(pdf_path)
            housing_data['pages_analysed'] = len(relevant_pages)

            # Match organisation codes
            if 'organisation-name' in housing_data:
                housing_data['organisation'] = self.org_matcher.match(housing_data['organisation-name'])

            # Match organisation codes in housing-numbers array
            if 'housing-numbers' in housing_data and isinstance(housing_data['housing-numbers'], list):
                for org_entry in housing_data['housing-numbers']:
                    if 'organisation-name' in org_entry:
                        org_entry['organisation'] = self.org_matcher.match(org_entry['organisation-name'])

                # Determine if this is a joint plan (multiple entries in housing-numbers)
                if len(housing_data['housing-numbers']) > 1:
                    # Collect all organisation codes from breakdown
                    org_codes = []
                    for org_entry in housing_data['housing-numbers']:
                        org_code = org_entry.get('organisation', '')
                        if org_code:
                            org_codes.append(org_code)

                    # Create organisations array
                    if org_codes:
                        housing_data['organisations'] = org_codes

                        # Construct joint planning authority reference
                        # Extract reference parts (after colon) from CURIE values
                        ref_parts = []
                        for org_code in org_codes:
                            if ':' in org_code:
                                ref_part = org_code.split(':', 1)[1]
                                ref_parts.append(ref_part)

                        # Sort alphabetically and create joint reference
                        if ref_parts:
                            ref_parts.sort()
                            joint_ref = 'joint-planning-authority:' + '-'.join(ref_parts)
                            housing_data['organisation'] = joint_ref

                            # Calculate sums from member authorities for joint entry
                            housing_fields = [
                                'required-housing',
                                'allocated-housing',
                                'windfall-housing',
                                'committed-housing',
                                'broad-locations-housing',
                                'annual-required-housing'
                            ]

                            sums = {}
                            for field in housing_fields:
                                total = 0
                                for entry in housing_data['housing-numbers']:
                                    value = entry.get(field, '')
                                    # Treat empty strings as 0, include actual 0 values
                                    if isinstance(value, (int, float)):
                                        total += value
                                    elif value == '':
                                        total += 0
                                sums[field] = total

                            # Add joint authority entry to housing-numbers with calculated totals
                            joint_entry = {
                                'organisation-name': housing_data.get('organisation-name', ''),
                                'organisation': joint_ref,
                                'required-housing': sums['required-housing'],
                                'allocated-housing': sums['allocated-housing'],
                                'windfall-housing': sums['windfall-housing'],
                                'committed-housing': sums['committed-housing'],
                                'broad-locations-housing': sums['broad-locations-housing'],
                                'annual-required-housing': sums['annual-required-housing'],
                                'pages': housing_data.get('pages', ''),
                                'notes': housing_data.get('notes', '')
                            }
                            housing_data['housing-numbers'].append(joint_entry)
                elif len(housing_data['housing-numbers']) == 1:
                    # For single authority plans, ensure organisation code is set at top level
                    single_entry = housing_data['housing-numbers'][0]
                    if 'organisation' in single_entry and single_entry['organisation']:
                        housing_data['organisation'] = single_entry['organisation']

            # Construct local-plan-boundary field
            if 'organisations' in housing_data:
                # For joint plans, concatenate local-planning-authority values
                lpa_codes = []
                for org_entry in housing_data.get('housing-numbers', []):
                    org_name = org_entry.get('organisation-name', '')
                    if org_name:
                        lpa_code = self.org_matcher.get_local_planning_authority(org_name)
                        if lpa_code and lpa_code not in lpa_codes:
                            lpa_codes.append(lpa_code)

                if lpa_codes:
                    housing_data['local-plan-boundary'] = '-'.join(lpa_codes)
                    housing_data['local-planning-authorities'] = lpa_codes
            elif 'organisation-name' in housing_data:
                # For single authority plans, use the single local-planning-authority
                lpa_code = self.org_matcher.get_local_planning_authority(housing_data['organisation-name'])
                if lpa_code:
                    housing_data['local-plan-boundary'] = lpa_code
                    housing_data['local-planning-authorities'] = [lpa_code]

            # Calculate missing annual-required-housing values
            start_date = housing_data.get('period-start-date', '')
            end_date = housing_data.get('period-end-date', '')

            if isinstance(start_date, int) and isinstance(end_date, int) and end_date > start_date:
                plan_years = end_date - start_date

                if 'housing-numbers' in housing_data:
                    for entry in housing_data['housing-numbers']:
                        required = entry.get('required-housing', '')
                        annual = entry.get('annual-required-housing', '')

                        # Calculate if required is present but annual is missing
                        if isinstance(required, (int, float)) and required > 0:
                            if annual == '' or annual is None:
                                calculated_annual = round(required / plan_years)
                                entry['annual-required-housing'] = calculated_annual
                                print(f"    Calculated annual-required-housing: {calculated_annual} ({required}/{plan_years} years)", file=sys.stderr)

            print(f"  ✓ Extraction complete", file=sys.stderr)
            
            # Add delay to respect rate limits
            time.sleep(self.rate_limit_delay)
            
            return housing_data
            
        except anthropic.RateLimitError as e:
            return {
                "authority": authority_name or Path(pdf_path).stem,
                "pdf_file": str(pdf_path),
                "error": f"Rate limit exceeded after {self.max_retries} retries: {str(e)}"
            }
        except Exception as e:
            return {
                "authority": authority_name or Path(pdf_path).stem,
                "pdf_file": str(pdf_path),
                "error": str(e)
            }
    
    def extract_from_multiple_pdfs(self, pdf_directory: str, output_csv: str = "housing_data.csv", delay_between_files: int = 3):
        """Process multiple PDFs in a directory"""
        
        pdf_dir = Path(pdf_directory)
        pdf_files = list(pdf_dir.glob("*.pdf"))
        
        print(f"Found {len(pdf_files)} PDF files to process")
        print(f"Rate limit settings: {self.rate_limit_delay}s between API calls, {delay_between_files}s between files\n")
        
        all_results = []
        
        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(pdf_files)}] {pdf_path.name}")
            print('='*60)
            
            result = self.extract_housing_data(str(pdf_path))
            all_results.append(result)
            
            # Print summary
            if 'error' not in result:
                print(f"\n  Results:")
                print(f"    Plan name: {result.get('name', 'N/A')}")
                org_name = result.get('organisation-name', 'N/A')
                org_code = result.get('organisation', '')
                if org_code:
                    print(f"    Organisation: {org_name} ({org_code})")
                else:
                    print(f"    Organisation: {org_name}")
                print(f"    Plan period: {result.get('period-start-date', '?')} - {result.get('period-end-date', '?')}")
                print(f"    Confidence: {result.get('confidence', 'N/A')}")

                # Display housing numbers from housing-numbers array
                if result.get('housing-numbers') and len(result['housing-numbers']) > 0:
                    if len(result['housing-numbers']) == 1:
                        # Single authority plan - display summary
                        housing = result['housing-numbers'][0]
                        print(f"    Required housing: {housing.get('required-housing', 'N/A')}")
                        print(f"    Allocated housing: {housing.get('allocated-housing', 'N/A')}")
                        print(f"    Windfall housing: {housing.get('windfall-housing', 'N/A')}")
                        print(f"    Committed housing: {housing.get('committed-housing', 'N/A')}")
                        print(f"    Broad locations: {housing.get('broad-locations-housing', 'N/A')}")
                        if housing.get('pages'):
                            print(f"    Pages: {housing['pages']}")
                        if housing.get('notes'):
                            print(f"    Notes: {housing['notes'][:100]}...")
                    else:
                        # Joint plan - display breakdown
                        print(f"\n  Organisation Breakdown (Joint Plan with {len(result['housing-numbers'])} authorities):")
                        for org in result['housing-numbers']:
                            org_name = org.get('organisation-name', 'N/A')
                            org_code = org.get('organisation', '')
                            if org_code:
                                print(f"    • {org_name} ({org_code}):")
                            else:
                                print(f"    • {org_name}:")
                            print(f"      Required: {org.get('required-housing', 'N/A')}")
                            print(f"      Allocated: {org.get('allocated-housing', 'N/A')}")
                            print(f"      Windfall: {org.get('windfall-housing', 'N/A')}")
                            print(f"      Committed: {org.get('committed-housing', 'N/A')}")
                            print(f"      Broad locations: {org.get('broad-locations-housing', 'N/A')}")
                            if org.get('pages'):
                                print(f"      Pages: {org['pages']}")
                            if org.get('notes'):
                                print(f"      Notes: {org['notes'][:80]}...")
            else:
                print(f"\n  ✗ Error: {result['error']}")
            
            # Extra delay between files
            if i < len(pdf_files):
                print(f"\n  Waiting {delay_between_files}s before next file...")
                time.sleep(delay_between_files)
        
        # Save to CSV
        self._save_to_csv(all_results, output_csv)
        
        return all_results
    
    def _save_to_csv(self, results: list, output_file: str):
        """Save results to CSV file"""
        
        if not results:
            print("No results to save", file=sys.stderr)
            return
        
        fieldnames = [
            'authority',
            'name',
            'organisation-name',
            'organisation',
            'organisations',
            'local-plan-boundary',
            'local-planning-authorities',
            'pdf_file',
            'period-start-date',
            'period-end-date',
            'housing-numbers',
            'pages_analysed',
            'confidence',
            'error'
        ]

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasingle=True)
            writer.writeheader()

            for result in results:
                row = {}
                for field in fieldnames:
                    value = result.get(field, '')
                    # Serialize arrays as JSON for CSV storage
                    if field in ('housing-numbers', 'organisations', 'local-planning-authorities') and isinstance(value, list):
                        row[field] = json.dumps(value) if value else ''
                    else:
                        row[field] = value
                writer.writerow(row)
        
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"✓ Saved results to {output_file}", file=sys.stderr)
        print('='*60, file=sys.stderr)


# Example usage functions

def extract_single_pdf(pdf_path: str, api_key: str):
    """Extract housing data from a single PDF"""
    extractor = LocalPlanHousingExtractor(api_key)
    result = extractor.extract_housing_data(pdf_path)
    
    print("\n" + "="*60)
    print("HOUSING DATA EXTRACTION RESULTS")
    print("="*60)
    print(json.dumps(result, indent=2))
    
    return result


def extract_batch_pdfs(directory: str, api_key: str, delay_between_files: int = 3):
    """Extract housing data from all PDFs in a directory"""
    extractor = LocalPlanHousingExtractor(api_key)
    results = extractor.extract_from_multiple_pdfs(directory, "housing_data.csv", delay_between_files)
    
    # Print summary statistics
    print("\n" + "="*60)
    print("SUMMARY STATISTICS")
    print("="*60)
    
    successful = [r for r in results if 'error' not in r]
    failed = [r for r in results if 'error' in r]
    
    print(f"Successfully processed: {len(successful)}/{len(results)}")
    print(f"Failed: {len(failed)}/{len(results)}")
    
    if successful:
        total_reqs = [r['required-housing'] for r in successful 
                     if r.get('required-housing')]
        if total_reqs:
            print(f"\nTotal housing requirements found: {len(total_reqs)}")
            print(f"Average requirement: {sum(total_reqs)/len(total_reqs):,.0f} homes")
            print(f"Total homes across all plans: {sum(total_reqs):,.0f}")
    
    return results


if __name__ == "__main__":
    # Set up command line argument parser
    parser = argparse.ArgumentParser(
        description='Extract housing data from local plan PDFs using Claude AI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single PDF file
  python script.py plan.pdf
  
  # Process all PDFs in a directory
  python script.py ./local_plans/
  
  # Specify output file
  python script.py ./local_plans/ --output my_results.csv
  
  # Adjust rate limiting delays
  python script.py ./local_plans/ --api-delay 5 --file-delay 10
        """
    )
    
    parser.add_argument(
        'path',
        help='Path to a PDF file or directory containing PDF files'
    )
    
    parser.add_argument(
        '--output', '-o',
        default=None,
        help='Output CSV file path (if not specified, outputs JSON to stdout)'
    )
    
    parser.add_argument(
        '--api-delay',
        type=int,
        default=2,
        help='Seconds to wait between API calls (default: 2)'
    )
    
    parser.add_argument(
        '--file-delay',
        type=int,
        default=3,
        help='Seconds to wait between processing files (default: 3)'
    )
    
    parser.add_argument(
        '--max-pages',
        type=int,
        default=32,
        help='Maximum pages to send to Claude (default: 32)'
    )
    
    args = parser.parse_args()
    
    # Get API key from environment variable
    API_KEY = os.getenv('ANTHROPIC_API_KEY')
    
    if not API_KEY:
        print("Error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        print("Set it with: export ANTHROPIC_API_KEY='your-key-here'", file=sys.stderr)
        sys.exit(1)
    
    # Create extractor with custom settings
    extractor = LocalPlanHousingExtractor(API_KEY)
    extractor.rate_limit_delay = args.api_delay
    extractor.max_pages = args.max_pages
    
    # Check if path is a file or directory
    path = Path(args.path)
    
    if not path.exists():
        print(f"Error: Path '{args.path}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    if path.is_file():
        # Process single PDF
        if path.suffix.lower() != '.pdf':
            print(f"Error: '{args.path}' is not a PDF file", file=sys.stderr)
            sys.exit(1)
        
        print(f"Processing single PDF file: {path.name}\n", file=sys.stderr)
        result = extractor.extract_housing_data(str(path))
        
        # Output result
        if args.output:
            # Save to CSV
            extractor._save_to_csv([result], args.output)
        else:
            # Output JSON to stdout
            print(json.dumps(result, indent=2))
        
    elif path.is_dir():
        # Process directory of PDFs
        print(f"Processing directory: {path}\n", file=sys.stderr)
        results = extractor.extract_from_multiple_pdfs(
            str(path), 
            args.output,  # Will be None if not specified
            args.file_delay
        )
        
        # Output results
        if args.output:
            # CSV already saved by extract_from_multiple_pdfs
            # Print summary to stderr
            print("\n" + "="*60, file=sys.stderr)
            print("SUMMARY STATISTICS", file=sys.stderr)
            print("="*60, file=sys.stderr)
            
            successful = [r for r in results if 'error' not in r]
            failed = [r for r in results if 'error' in r]
            
            print(f"Successfully processed: {len(successful)}/{len(results)}", file=sys.stderr)
            print(f"Failed: {len(failed)}/{len(results)}", file=sys.stderr)
            
            if successful:
                total_reqs = [r['required-housing'] for r in successful 
                             if r.get('required-housing')]
                if total_reqs:
                    print(f"\nTotal housing requirements found: {len(total_reqs)}", file=sys.stderr)
                    print(f"Average requirement: {sum(total_reqs)/len(total_reqs):,.0f} homes", file=sys.stderr)
                    print(f"Total homes across all plans: {sum(total_reqs):,.0f}", file=sys.stderr)
        else:
            # Output JSON array to stdout
            print(json.dumps(results, indent=2))
    
    else:
        print(f"Error: '{args.path}' is neither a file nor a directory", file=sys.stderr)
        sys.exit(1)
