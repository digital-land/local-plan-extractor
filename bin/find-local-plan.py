#!/usr/bin/env python3
"""
Find local plan documentation URLs for a given organisation using Claude.

Usage:
    python bin/find-local-plan.py local-authority:DAC
    python bin/find-local-plan.py local-authority:MAN

Outputs JSON array with one element per local plan document. Each element contains:
    - organisation: the organisation code
    - organisation-name: the name of the organisation
    - documentation-url: URL to the specific local plan document
    - name: name of the plan (e.g., "Core Strategy", "Site Allocations DPD")
    - status: plan status (draft, regulation-18, regulation-19, submitted, examination, adopted, withdrawn)
    - year: year the plan was adopted (or year of latest milestone if not adopted)
    - period-start-date: start date of the plan period (if available)
    - period-end-date: end date of the plan period (if available)

Note: A Local Planning Authority may have multiple local plan documents at different stages.
"""

import anthropic
import argparse
import csv
import json
import os
import sys
import requests
from bs4 import BeautifulSoup
from typing import Dict, Optional, List


class LocalPlanFinder:
    def __init__(self, api_key: str, organisation_csv: str = "var/cache/organisation.csv"):
        """Initialize with Anthropic API key and organisation CSV path"""
        self.client = anthropic.Anthropic(api_key=api_key)
        self.organisations = self._load_organisations(organisation_csv)

    def _load_organisations(self, csv_path: str) -> Dict[str, Dict[str, str]]:
        """Load organisation codes, names, and websites from CSV file.

        Returns:
            Dictionary mapping organisation codes to dict with 'name' and 'website'
        """
        organisations = {}
        try:
            if not os.path.exists(csv_path):
                print(f"Warning: Organisation CSV not found at {csv_path}", file=sys.stderr)
                return organisations

            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)  # Read header

                # Find column indices
                name_idx = header.index('name') if 'name' in header else 14
                org_idx = header.index('organisation') if 'organisation' in header else 19
                website_idx = header.index('website') if 'website' in header else 27

                for row in reader:
                    if len(row) > max(name_idx, org_idx, website_idx):
                        name = row[name_idx].strip()
                        org_code = row[org_idx].strip()
                        website = row[website_idx].strip() if len(row) > website_idx else ""
                        if name and org_code:
                            organisations[org_code] = {
                                'name': name,
                                'website': website
                            }
        except Exception as e:
            print(f"Warning: Could not load organisations from {csv_path}: {e}", file=sys.stderr)

        return organisations

    def get_organisation_name(self, org_code: str) -> Optional[str]:
        """Get organisation name from code.

        Args:
            org_code: Organisation code (e.g., "local-authority:DAC")

        Returns:
            Organisation name if found, None otherwise
        """
        org_data = self.organisations.get(org_code)
        return org_data['name'] if org_data else None

    def get_organisation_website(self, org_code: str) -> Optional[str]:
        """Get organisation website from code.

        Args:
            org_code: Organisation code (e.g., "local-authority:DAC")

        Returns:
            Organisation website URL if found, None otherwise
        """
        org_data = self.organisations.get(org_code)
        return org_data['website'] if org_data and org_data['website'] else None

    def construct_likely_urls(self, org_name: str, official_website: Optional[str] = None) -> List[Dict[str, str]]:
        """Construct likely URLs for a local authority's planning pages.

        Args:
            org_name: Organisation name
            official_website: Official website URL from organisation.csv (if available)

        Returns:
            List of dicts with 'title', 'url', 'snippet'
        """
        domains = []

        # If we have the official website, use it first
        if official_website:
            # Extract domain from URL (e.g., "https://www.dacorum.gov.uk" -> "www.dacorum.gov.uk")
            from urllib.parse import urlparse
            parsed = urlparse(official_website)
            official_domain = parsed.netloc
            if official_domain:
                domains.append(official_domain)
                print(f"Using official website domain: {official_domain}", file=sys.stderr)

        # Also try guessing domains as fallback
        base_name = org_name.lower()
        for suffix in [' borough council', ' city council', ' district council',
                       ' county council', ' council', ' metropolitan borough']:
            base_name = base_name.replace(suffix, '')
        base_name = base_name.strip().replace(' ', '')

        # Add common URL patterns as fallback
        fallback_domains = [
            f"www.{base_name}.gov.uk",
            f"{base_name}.gov.uk",
            f"www.{base_name}council.gov.uk",
            f"{base_name}council.gov.uk",
        ]

        # Add fallback domains that aren't already in the list
        for domain in fallback_domains:
            if domain not in domains:
                domains.append(domain)

        # Common paths for local plan pages
        paths = [
            "/planning",  # Try planning section first
            "/newlocalplan",  # Fenland and similar councils
            "/developmentplan",  # Fenland and similar councils
            "/local-plan",
            "/localplan",
            "/planning/local-plan",
            "/planning/planning-policy/local-plan",
            "/planning-policy/local-plan",
            "/planning-policy",
            "/home/planning-development/planning-strategic-planning",
            "/home/planning-development/planning-strategic-planning/new-local-plan",
            "/planning/strategic-planning/local-plan",
            "/planning/strategic-planning",
            "/services/planning/planning-policy",
            "/planning-and-building-control/planning-policy",
            "/planning-applications/planning-policy",
            "/planning-services/planning-policy",
            "",  # Root path
        ]

        urls = []
        for domain in domains:
            for path in paths:
                urls.append({
                    'title': f"{org_name} Local Plan",
                    'url': f"https://{domain}{path}",
                    'snippet': f"Constructed URL for {org_name}"
                })

        return urls

    def extract_local_plan_links(self, url: str, html_content: str) -> List[str]:
        """Extract links that likely point to local plan pages.

        Args:
            url: The base URL of the page
            html_content: The HTML content to parse

        Returns:
            List of URLs that likely point to local plan pages
        """
        from urllib.parse import urljoin
        soup = BeautifulSoup(html_content, 'html.parser')

        local_plan_links = []

        # Find all links
        for link in soup.find_all('a', href=True):
            href = link['href']
            link_text = link.get_text(strip=True).lower()

            # Look for links that mention local plan
            local_plan_keywords = [
                'local plan', 'local-plan', 'localplan',
                'core strategy', 'site allocation',
                'development plan', 'planning policy'
            ]

            # Check if link text or href contains local plan keywords
            is_local_plan_link = any(keyword in link_text for keyword in local_plan_keywords) or \
                                 any(keyword in href.lower() for keyword in local_plan_keywords)

            if is_local_plan_link:
                # Convert relative URLs to absolute
                full_url = urljoin(url, href)

                # Avoid duplicates and non-HTTP links
                if full_url.startswith('http') and full_url not in local_plan_links:
                    local_plan_links.append(full_url)

        return local_plan_links

    def fetch_page_content(self, url: str, max_length: int = 50000) -> tuple[str, bool]:
        """Fetch and extract text content from a URL.

        Args:
            url: URL to fetch
            max_length: Maximum content length

        Returns:
            Tuple of (text content, success boolean)
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()

            # Only process HTML content
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' not in content_type:
                print(f"  Skipping non-HTML content: {content_type}", file=sys.stderr)
                return "", False

            soup = BeautifulSoup(response.text, 'html.parser')

            # Remove script and style elements only (keep nav/footer/header as they may contain useful links)
            for script in soup(['script', 'style']):
                script.decompose()

            # Get text
            text = soup.get_text(separator='\n', strip=True)

            # Clean up excessive whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            text = '\n'.join(lines)

            # Limit length
            if len(text) > max_length:
                text = text[:max_length]

            if len(text) > 200:  # Minimum viable content
                return text, True
            else:
                return "", False

        except requests.exceptions.Timeout:
            print(f"  Timeout fetching {url}", file=sys.stderr)
            return "", False
        except requests.exceptions.ConnectionError:
            print(f"  Connection error for {url}", file=sys.stderr)
            return "", False
        except requests.exceptions.HTTPError as e:
            print(f"  HTTP error {e.response.status_code} for {url}", file=sys.stderr)
            return "", False
        except Exception as e:
            print(f"  Error fetching {url}: {type(e).__name__}: {e}", file=sys.stderr)
            return "", False

    def find_local_plan(self, org_code: str) -> List[Dict]:
        """Find local plan documentation URLs for an organisation using web search and Claude.

        Args:
            org_code: Organisation code (e.g., "local-authority:DAC")

        Returns:
            List of dictionaries, each with organisation, organisation-name, documentation-url,
            name, year, period-start-date, and period-end-date
        """
        org_name = self.get_organisation_name(org_code)

        if not org_name:
            return [{
                "organisation": org_code,
                "organisation-name": "",
                "error": f"Organisation code '{org_code}' not found in organisation.csv"
            }]

        print(f"Searching for local plans for: {org_name} ({org_code})", file=sys.stderr)

        # Get official website if available
        official_website = self.get_organisation_website(org_code)

        # Construct likely URLs based on official website and common patterns
        print(f"Constructing likely URLs for {org_name}...", file=sys.stderr)
        likely_urls = self.construct_likely_urls(org_name, official_website)

        # Fetch content from likely URLs
        pages_content = []
        discovered_links = set()

        for i, result in enumerate(likely_urls, 1):
            print(f"Trying URL {i}/{len(likely_urls)}: {result['url']}", file=sys.stderr)
            content, success = self.fetch_page_content(result['url'])
            if success and content:
                print(f"  ✓ Success! Found content ({len(content)} chars)", file=sys.stderr)
                pages_content.append({
                    'url': result['url'],
                    'title': result['title'],
                    'content': content
                })

                # Extract local plan links from this page
                try:
                    response = requests.get(result['url'], headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }, timeout=15, allow_redirects=True)
                    if response.status_code == 200:
                        links = self.extract_local_plan_links(result['url'], response.text)
                        print(f"  Found {len(links)} local plan links on this page", file=sys.stderr)
                        discovered_links.update(links)
                except Exception as e:
                    pass

                # Stop after finding 2 successful pages (to leave room for discovered links)
                if len(pages_content) >= 2:
                    break

        # Now fetch content from discovered local plan links
        print(f"\nFetching {len(discovered_links)} discovered local plan pages...", file=sys.stderr)
        for link in list(discovered_links)[:5]:  # Limit to 5 additional pages
            # Skip if we already have this URL
            if any(p['url'] == link for p in pages_content):
                continue

            print(f"Fetching discovered link: {link}", file=sys.stderr)
            content, success = self.fetch_page_content(link)
            if success and content:
                print(f"  ✓ Success! ({len(content)} chars)", file=sys.stderr)
                pages_content.append({
                    'url': link,
                    'title': 'Discovered local plan page',
                    'content': content
                })

                # Stop after we have enough pages total
                if len(pages_content) >= 5:
                    break

        if not pages_content:
            return [{
                "organisation": org_code,
                "organisation-name": org_name,
                "error": "Could not fetch any page content from likely URLs"
            }]

        print(f"\nTotal pages fetched: {len(pages_content)}", file=sys.stderr)

        # Use Claude to analyze the content and extract information
        print(f"Analyzing content with Claude...", file=sys.stderr)

        content_summary = "\n\n---\n\n".join([
            f"URL: {p['url']}\nTitle: {p['title']}\n\nContent:\n{p['content'][:10000]}"
            for p in pages_content
        ])

        prompt = f"""I have searched for local plans for {org_name} and found these pages:

{content_summary}

Based on this content, please identify ALL local plans (both adopted and emerging/draft) and provide information about each one. Local Planning Authorities often have multiple local plan documents such as:
- Core Strategy
- Site Allocations DPD
- Development Management Policies
- Area Action Plans
- Minerals and Waste Local Plans
- Joint Plans with other authorities
- New/Emerging Local Plans at various stages

Return a JSON array where each element represents one local plan document:

[
    {{
        "organisation": "{org_code}",
        "organisation-name": "{org_name}",
        "documentation-url": "the best URL for this specific local plan document (main page or PDF)",
        "name": "the full official name of this plan document (e.g., 'Dacorum Core Strategy 2006-2031', 'Dacorum Site Allocations DPD')",
        "status": "one of: draft, regulation-18, regulation-19, submitted, examination, adopted, withdrawn",
        "year": the year this plan was adopted (or year of latest milestone if not adopted, as an integer, e.g., 2013),
        "period-start-date": the start year of the plan period (as an integer, e.g., 2006) or "" if not available,
        "period-end-date": the end year of the plan period (as an integer, e.g., 2031) or "" if not available
    }}
]

STATUS FIELD GUIDE:
- "draft" = Early draft or Issues and Options stage
- "regulation-18" = Regulation 18 consultation (early engagement/preferred options)
- "regulation-19" = Regulation 19 consultation (pre-submission/publication stage)
- "submitted" = Submitted to the Planning Inspectorate for examination
- "examination" = Currently undergoing examination by Planning Inspector
- "adopted" = Formally adopted by the council
- "withdrawn" = Plan has been withdrawn

IMPORTANT:
- Return an array with one element for EACH separate local plan document
- Include BOTH current plans AND superseded/previous plans (e.g., a plan for 2018-2033 AND an older plan for 2001-2011)
- Include BOTH adopted plans AND emerging/draft plans at various stages
- Look for status indicators like "adopted", "Regulation 18", "Regulation 19", "consultation", "examination", "submitted", "withdrawn"
- The documentation-url should be the MOST SPECIFIC URL for that particular document:
  * Prefer pages specifically about that local plan (e.g., "/planning/local-plan-2018-2033")
  * Prefer direct PDF links to plan documents if available
  * Avoid generic planning section URLs unless that's all that's available
- Extract plan period dates from the plan name or content (e.g., "2006-2031" means period-start-date: 2006, period-end-date: 2031)
  * Look carefully for period dates - they may be in the plan title, headings, or document text
  * Common formats: "2018-2033", "2018 to 2033", "plan period 2018-2033"
- Use empty string "" for period dates if not clearly stated
- For the year field:
  * Look for adoption dates in formats like "adopted 23 June 2020", "adoption date: 2020-06-23", "adopted in 2020"
  * If adopted, use the adoption year (extract the year from the full date if needed)
  * If not adopted, use the year of the latest milestone (e.g., when consultation started, when submitted)
  * If no clear year is found, use ""
- If you can only identify one local plan, return an array with one element
- Order the array with current/most recent plans first, then older/superseded plans

Provide ONLY the JSON array response, no other text."""

        try:
            message = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            # Extract the response text
            response_text = ""
            for block in message.content:
                if hasattr(block, 'text'):
                    response_text += block.text

            print(f"Received response from Claude", file=sys.stderr)

            # Try to parse JSON from the response (looking for array)
            json_start = response_text.find('[')
            json_end = response_text.rfind(']') + 1

            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                result = json.loads(json_str)

                # Validate it's an array
                if not isinstance(result, list):
                    result = [result]  # Wrap single object in array

                print(f"Found {len(result)} local plan(s)", file=sys.stderr)
                return result
            else:
                # Fallback: try to parse as single object and wrap in array
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1

                if json_start != -1 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    result = json.loads(json_str)
                    return [result]
                else:
                    return [{
                        "organisation": org_code,
                        "organisation-name": org_name,
                        "error": "Could not parse JSON response from Claude",
                        "raw_response": response_text[:500]
                    }]

        except Exception as e:
            return [{
                "organisation": org_code,
                "organisation-name": org_name,
                "error": str(e)
            }]


def main():
    parser = argparse.ArgumentParser(
        description='Find all local plan documentation URLs for an organisation using Claude',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Find all local plans for Dacorum Borough Council (returns JSON array)
  python bin/find-local-plan.py local-authority:DAC

  # Find all local plans for Manchester City Council
  python bin/find-local-plan.py local-authority:MAN

  # Debug mode - test URL fetching without calling Claude API
  python bin/find-local-plan.py local-authority:DAC --debug

Output:
  Returns a JSON array where each element represents one local plan document.
  An LPA may have multiple documents (Core Strategy, Site Allocations, etc.)
  at different stages (adopted, regulation-18, regulation-19, etc.).

Status values:
  - draft: Early draft or Issues and Options stage
  - regulation-18: Regulation 18 consultation (early engagement)
  - regulation-19: Regulation 19 consultation (pre-submission)
  - submitted: Submitted to Planning Inspectorate
  - examination: Undergoing examination
  - adopted: Formally adopted
  - withdrawn: Withdrawn from process
"""
    )

    parser.add_argument(
        'organisation',
        help='Organisation code (e.g., local-authority:DAC)'
    )

    parser.add_argument(
        '--organisation-csv',
        default='var/cache/organisation.csv',
        help='Path to organisation CSV file (default: var/cache/organisation.csv)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Debug mode - test URL fetching without calling Claude API'
    )

    args = parser.parse_args()

    # Get API key from environment variable
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key and not args.debug:
        print("Error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        print("Set it with: export ANTHROPIC_API_KEY='your-key-here'", file=sys.stderr)
        print("Or use --debug flag to test URL fetching only", file=sys.stderr)
        sys.exit(1)

    # Create finder (API key can be None in debug mode)
    finder = LocalPlanFinder(api_key or 'debug', args.organisation_csv)

    if args.debug:
        # Debug mode - just show what URLs we would try
        org_name = finder.get_organisation_name(args.organisation)
        if not org_name:
            print(f"Error: Organisation code '{args.organisation}' not found", file=sys.stderr)
            sys.exit(1)

        official_website = finder.get_organisation_website(args.organisation)

        print(f"Organisation: {org_name} ({args.organisation})", file=sys.stderr)
        if official_website:
            print(f"Official website: {official_website}", file=sys.stderr)
        print(f"\nTesting URL fetching...\n", file=sys.stderr)

        likely_urls = finder.construct_likely_urls(org_name, official_website)
        success_count = 0

        discovered_links = set()

        for i, result in enumerate(likely_urls[:10], 1):  # Test first 10 URLs
            print(f"{i}. {result['url']}", file=sys.stderr)
            content, success = finder.fetch_page_content(result['url'])
            if success:
                success_count += 1
                print(f"   ✓ Success! ({len(content)} chars)", file=sys.stderr)
                if success_count == 1:
                    # Show snippet of first successful fetch
                    print(f"\n   First 500 chars of content:", file=sys.stderr)
                    print(f"   {content[:500]}...\n", file=sys.stderr)

                # Try to extract local plan links
                try:
                    import requests
                    response = requests.get(result['url'], headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }, timeout=15, allow_redirects=True)
                    if response.status_code == 200:
                        links = finder.extract_local_plan_links(result['url'], response.text)
                        if links:
                            print(f"   Found {len(links)} local plan links:", file=sys.stderr)
                            for link in links[:5]:  # Show first 5
                                print(f"     - {link}", file=sys.stderr)
                            discovered_links.update(links)
                except Exception as e:
                    pass

        print(f"\nSuccessfully fetched {success_count} pages", file=sys.stderr)
        print(f"Discovered {len(discovered_links)} total local plan links", file=sys.stderr)
        sys.exit(0)

    # Normal mode - full search
    results = finder.find_local_plan(args.organisation)

    # Output JSON to stdout
    print(json.dumps(results, indent=2))

    # Summary to stderr
    if results and not any('error' in r for r in results):
        print(f"\n✓ Found {len(results)} local plan document(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
