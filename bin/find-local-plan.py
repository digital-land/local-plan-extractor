#!/usr/bin/env python3
"""
Find local plan documentation URLs for a given organisation using Claude.

Usage:
    python bin/find-local-plan.py local-authority:DAC
    python bin/find-local-plan.py local-authority:MAN

IMPORTANT - Manually Maintained Authorities:
    The following authorities have joint local plans hosted on websites protected by bot
    detection (Sucuri Cloudproxy) and CANNOT be automatically scraped:
    - local-authority:MAV (Malvern Hills District Council)
    - local-authority:WOC (Worcester City Council)
    - local-authority:WYC (Wychavon District Council)

    These authorities should be maintained manually in source/{code}.json files.
    Use the --exclude flag in run-all-authorities.py to prevent overwriting:
    python bin/run-all-authorities.py --exclude MAV,WOC,WYC

Outputs JSON array with one element per local plan document. Each element contains:
    - organisation: the organisation code
    - organisation-name: the name of the organisation
    - documentation-url: URL to the main page for this specific local plan
    - document-url: Direct URL to the PDF document (core strategy, local plan, etc.)
    - name: name of the plan (e.g., "Core Strategy", "Site Allocations DPD")
    - status: plan status (draft, regulation-18, regulation-19, submitted, examination, adopted, withdrawn)
    - year: year the plan was adopted (or year of latest milestone if not adopted)
    - period-start-date: start date of the plan period (if available)
    - period-end-date: end date of the plan period (if available)
    - documents: array of related documents (PDFs/Word docs) with:
        * document-url: normalized URL to the document
        * document-type: classified type (local-plan, core-strategy, etc.)
        * name: readable descriptive name for the document
        * reference: short unique reference code (e.g., LP-2018-2033, SA-2020)
        * document-status: status of the document (draft, consultation, examination, adopted, withdrawn)
        * endpoint: SHA256 hash of the document-url

Note: A Local Planning Authority may have multiple local plan documents at different stages.
"""

import anthropic
import argparse
import csv
import hashlib
import json
import os
import sys
import time
import mimetypes
import urllib.request
import urllib.error
import requests
import cloudscraper
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional, List
import warnings
from urllib3.exceptions import InsecureRequestWarning

# Disable SSL warnings when verify=False is used
warnings.simplefilter('ignore', InsecureRequestWarning)


def calculate_sha1(content):
    """Calculate SHA1 hash of content"""
    return hashlib.sha1(content).hexdigest()


def detect_file_suffix(content, content_type, url):
    """Detect file suffix from content, content-type header, or URL."""
    # Try to get extension from content-type
    if content_type:
        mime_type = content_type.split(";")[0].strip()
        mime_to_ext = {
            "application/pdf": "pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/msword": "doc",
            "application/vnd.ms-excel": "xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "text/html": "html",
            "text/plain": "txt",
            "application/zip": "zip",
            "image/jpeg": "jpg",
            "image/png": "png",
        }
        if mime_type in mime_to_ext:
            return mime_to_ext[mime_type]
        ext = mimetypes.guess_extension(mime_type)
        if ext:
            return ext.lstrip(".")

    # Try to detect from magic bytes
    if content:
        if content.startswith(b"%PDF"):
            return "pdf"
        elif content.startswith(b"PK\x03\x04"):
            if b"word/" in content[:2000]:
                return "docx"
            elif b"xl/" in content[:2000]:
                return "xlsx"
            else:
                return "zip"
        elif content.startswith(b"\xd0\xcf\x11\xe0"):
            return "doc"
        elif content.startswith(b"<!DOCTYPE") or content.startswith(b"<html"):
            return "html"

    # Try to get extension from URL
    if url:
        url_path = url.split("?")[0]
        if "." in url_path:
            ext = url_path.rsplit(".", 1)[-1].lower()
            if ext in [
                "pdf",
                "doc",
                "docx",
                "xls",
                "xlsx",
                "html",
                "txt",
                "zip",
                "jpg",
                "png",
            ]:
                return ext

    return "bin"


def create_endpoint_hardlink(endpoint, resource_hash, content, content_type, url):
    """Create a hard link in collection/document/ to the resource file."""
    document_dir = Path("collection/document")
    document_dir.mkdir(parents=True, exist_ok=True)

    suffix = detect_file_suffix(content, content_type, url)
    hardlink_path = document_dir / f"{endpoint}.{suffix}"
    resource_path = Path("collection/resource") / resource_hash

    if hardlink_path.exists():
        hardlink_path.unlink()

    os.link(resource_path, hardlink_path)
    print(
        f"  → Created hardlink: document/{endpoint}.{suffix} => resource/{resource_hash}",
        file=sys.stderr,
    )


def download_document(url, endpoint):
    """Download document from URL and save to collection with proper logging."""
    if not url or url == "":
        print(f"Skipping empty URL", file=sys.stderr)
        return None

    if not url.startswith("http://") and not url.startswith("https://"):
        print(f"Skipping non-HTTP URL: {url}", file=sys.stderr)
        return None

    Path("collection/resource").mkdir(parents=True, exist_ok=True)
    Path("collection/log").mkdir(parents=True, exist_ok=True)

    log_path = Path(f"collection/log/{endpoint}.json")
    if log_path.exists():
        print(f"Already downloaded: {url}", file=sys.stderr)
        with open(log_path, "r") as f:
            log_entry = json.load(f)

        resource_hash = log_entry.get("resource")
        if resource_hash:
            resource_path = Path(f"collection/resource/{resource_hash}")
            if resource_path.exists():
                with open(resource_path, "rb") as f:
                    content = f.read()
                create_endpoint_hardlink(
                    endpoint,
                    resource_hash,
                    content,
                    log_entry.get("content-type", ""),
                    url,
                )
        return log_entry

    print(f"Downloading: {url}", file=sys.stderr)
    start_time = time.time()

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            },
        )

        with urllib.request.urlopen(req, timeout=60) as response:
            content = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            content_length = len(content)

        elapsed = time.time() - start_time
        resource_hash = calculate_sha1(content)

        resource_path = Path(f"collection/resource/{resource_hash}")
        with open(resource_path, "wb") as f:
            f.write(content)

        log_entry = {
            "resource": resource_hash,
            "endpoint-url": url,
            "entry-date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": str(status),
            "elapsed": f"{elapsed:.3f}",
            "content-type": content_type,
            "bytes": str(content_length),
        }

        with open(log_path, "w") as f:
            json.dump(log_entry, f, indent=2)

        create_endpoint_hardlink(endpoint, resource_hash, content, content_type, url)
        print(
            f"  ✓ Downloaded {content_length} bytes -> {resource_hash}", file=sys.stderr
        )
        return log_entry

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"  ✗ Error downloading {url}: {str(e)}", file=sys.stderr)

        log_entry = {
            "resource": "",
            "endpoint-url": url,
            "entry-date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "error",
            "elapsed": f"{elapsed:.3f}",
            "content-type": "",
            "bytes": "0",
        }

        with open(log_path, "w") as f:
            json.dump(log_entry, f, indent=2)

        return log_entry


class LocalPlanFinder:
    def __init__(
        self, api_key: str, organisation_csv: str = "var/cache/organisation.csv",
        joint_local_plans_json: str = "var/joint-local-plans.json"
    ):
        """Initialize with Anthropic API key and organisation CSV path"""
        self.client = anthropic.Anthropic(api_key=api_key)
        self.organisations = self._load_organisations(organisation_csv)
        self.joint_plans = self._load_joint_local_plans(joint_local_plans_json)

    def _load_organisations(self, csv_path: str) -> Dict[str, Dict[str, str]]:
        """Load organisation codes, names, and websites from CSV file.

        Returns:
            Dictionary mapping organisation codes to dict with 'name' and 'website'
        """
        organisations = {}
        try:
            if not os.path.exists(csv_path):
                print(
                    f"Warning: Organisation CSV not found at {csv_path}",
                    file=sys.stderr,
                )
                return organisations

            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)  # Read header

                # Find column indices
                name_idx = header.index("name") if "name" in header else 14
                org_idx = (
                    header.index("organisation") if "organisation" in header else 19
                )
                website_idx = header.index("website") if "website" in header else 27

                for row in reader:
                    if len(row) > max(name_idx, org_idx, website_idx):
                        name = row[name_idx].strip()
                        org_code = row[org_idx].strip()
                        website = (
                            row[website_idx].strip() if len(row) > website_idx else ""
                        )
                        if name and org_code:
                            organisations[org_code] = {"name": name, "website": website}
        except Exception as e:
            print(
                f"Warning: Could not load organisations from {csv_path}: {e}",
                file=sys.stderr,
            )

        return organisations

    def _load_joint_local_plans(self, json_path: str) -> Dict[str, Dict[str, str]]:
        """Load joint local plan mappings from JSON file.

        Returns:
            Dictionary mapping organisation codes to joint plan info
        """
        joint_plans = {}
        try:
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    joint_plans = data.get("joint-plans", {})
                    print(
                        f"Loaded {len(joint_plans)} joint local plan mappings",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"Note: Joint local plans file not found at {json_path}",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"Warning: Could not load joint local plans from {json_path}: {e}",
                file=sys.stderr,
            )

        return joint_plans

    def get_organisation_name(self, org_code: str) -> Optional[str]:
        """Get organisation name from code.

        Args:
            org_code: Organisation code (e.g., "local-authority:DAC")

        Returns:
            Organisation name if found, None otherwise
        """
        org_data = self.organisations.get(org_code)
        return org_data["name"] if org_data else None

    def get_organisation_website(self, org_code: str) -> Optional[str]:
        """Get organisation website from code.

        Args:
            org_code: Organisation code (e.g., "local-authority:DAC")

        Returns:
            Organisation website URL if found, None otherwise
        """
        org_data = self.organisations.get(org_code)
        return org_data["website"] if org_data and org_data["website"] else None

    def construct_likely_urls(
        self, org_name: str, official_website: Optional[str] = None, org_code: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Construct likely URLs for a local authority's planning pages.

        Args:
            org_name: Organisation name
            official_website: Official website URL from organisation.csv (if available)
            org_code: Organisation code (e.g., "local-authority:DAC") - used to check for joint local plans

        Returns:
            List of dicts with 'title', 'url', 'snippet'
        """
        domains = []
        configured_urls = []  # Track explicitly configured URLs (like from joint-local-plans.json)

        # Check if this authority has a joint local plan with other councils
        if org_code and org_code in self.joint_plans:
            plan_info = self.joint_plans[org_code]
            plan_website = plan_info.get("joint-plan-website")
            if plan_website:
                from urllib.parse import urlparse
                parsed = urlparse(plan_website)
                plan_domain = parsed.netloc
                if plan_domain:
                    domains.append(plan_domain)
                    print(
                        f"Authority is part of joint local plan - using joint plan domain: {plan_domain}",
                        file=sys.stderr,
                    )
                    print(
                        f"  Joint plan: {plan_info.get('joint-plan-name')}",
                        file=sys.stderr,
                    )
                    # Add the full configured URL as the first URL to try
                    configured_urls.append({
                        "title": f"Joint Local Plan: {plan_info.get('joint-plan-name')}",
                        "url": plan_website
                    })

        # If we have the official website, use it (but after joint plan domain)
        if official_website:
            # Extract domain from URL (e.g., "https://www.dacorum.gov.uk" -> "www.dacorum.gov.uk")
            from urllib.parse import urlparse

            parsed = urlparse(official_website)
            official_domain = parsed.netloc
            if official_domain and official_domain not in domains:
                domains.append(official_domain)
                print(
                    f"Using official website domain: {official_domain}", file=sys.stderr
                )

        # Also try guessing domains as fallback
        base_name = org_name.lower()
        for suffix in [
            " borough council",
            " city council",
            " district council",
            " county council",
            " council",
            " metropolitan borough",
        ]:
            base_name = base_name.replace(suffix, "")
        base_name = base_name.strip().replace(" ", "")

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

        # Council-specific path mappings
        # Maps organization codes to their specific URL paths
        council_specific_paths = {
            "local-authority:BAE": [  # Bassetlaw
                "/planning-and-building-control/planning-policy/bassetlaw-local-plan-2020-2038/bassetlaw-local-plan-2020-2038/",
                "/planning-and-building-control/planning-policy",
            ],
            "local-authority:BKM": [  # Buckinghamshire
                "/planning-and-building-control/planning-policy/local-planning-guidance/local-development-plans",
                "/planning-and-building-control/planning-policy/local-planning-guidance",
            ],
            "local-authority:BIR": [  # Birmingham
                "/info/20008/planning_and_development",
                "/info/20054/local_plan_documents",
                "/downloads/file/5433/adopted_birmingham_development_plan_2031",
                "/downloads/20054/local_plan_documents",
            ],
            "local-authority:TWH": [  # Tower Hamlets
                "/lgnl/planning_and_building_control/planning_policy_guidance/Local_plan/local_plan.aspx",
                "/lgnl/planning_and_building_control/planning_policy_guidance/Emerging-Draft-Local-Plan.aspx",
                "/lgnl/planning_and_building_control/planning_policy_guidance/emerging-draft-local-plan.aspx",
                "/lgnl/planning_and_building_control/planning_policy_guidance/new-local-plan.aspx",
            ],
            "local-authority:ADU": [  # Adur
                "/adur-ldf",
                "/adur-local-plan",
            ],
            "local-authority:FEN": [  # Fenland
                "/newlocalplan",
                "/developmentplan",
            ],
            "local-authority:BNE": [  # Barnet
                "/planning-and-building-control/planning-policies-and-local-plans",
                "/planning-and-building-control/planning-policies-and-local-plan/barnet-local-plan-2021-2036",
            ],
            "local-authority:BDG": [  # Barking and Dagenham
                "/planning-building-control-and-local-land-charges/planning-guidance-and-policies/local-plan",
            ],
            "local-authority:BNH": [  # Brighton and Hove
                "/planning/planning-policy/city-plan-part-one",
                "/planning/planning-policy/city-plan-part-two",
            ],
            "local-authority:BOS": [  # Bolsover
                "/services/p/planning-policy/planning-policy-documents/development-plan",
            ],
            "local-authority:BRM": [  # Bromsgrove
                "/council/policy/planning-policies-and-other-information/adopted-local-development-plan/the-bromsgrove-district-plan-2011-2030/adopted-bromsgrove-district-plan-2011-2030/",
            ],
            "local-authority:BUN": [  # Burnley
                "/planning/planning-policies/burnleys-local-plan/",
            ],
            "local-authority:CRW": [  # Crawley
                "/planning/planning-policy/local-plan/about-local-plan",
            ],
            "local-authority:DAC": [  # Dacorum
                "/planning-development/planning-strategic-planning/new-single-local-plan",
            ],
            "local-authority:DAL": [  # Darlington
                "/planning-and-building-regs/planning/planning-and-environmental-policy/",
            ],
            "local-authority:DEB": [  # Derbyshire Dales
                "/planning/planning-policy-and-local-plan/local-plan/local-plan-information-and-adoption",
            ],
            "local-authority:DUR": [  # County Durham
                "/cdp",
            ],
            "local-authority:EAL": [  # Ealing
                "/info/201164/local_plan",
            ],
            "local-authority:EAT": [  # Eastleigh
                "/planning-and-building/planning-policy-and-implementation/local-plan",
            ],
            "local-authority:ECA": [  # East Cambridgeshire
                "/planning-and-building-control/planning-policy-and-guidance/adopted-local-plan/local-plan",
            ],
            "local-authority:ELI": [  # East Lindsey
                "/localplan2018",
            ],
            "local-authority:ENF": [  # Enfield
                "/services/planning/adopted-plans",
                "/services/planning/new-enfield-local-plan",
            ],
            "local-authority:ERY": [  # East Riding of Yorkshire
                "/planning-permission-and-building-control/planning-policy-and-the-local-plan/east-riding-local-plan-update/",
            ],
            "local-authority:GAT": [  # Gateshead
                "/article/3001/Local-Plan",
                "/article/3251/Core-Strategy-and-Urban-Core-Plan-for-Gateshead-and-Newcastle-2010-2030",
            ],
            "local-authority:GED": [  # Gedling
                "/resident/planningandbuildingcontrol/planningpolicy/adoptedlocalplanandpolicydocuments/",
            ],
            "local-authority:GLO": [  # Gloucester
                "/planning-development/planning-policy/adopted-development-plan/",
            ],
            "local-authority:GRY": [  # Great Yarmouth
                "/article/2489/Current-Local-Plan",
            ],
            "local-authority:HAL": [  # Halton
                "/Pages/planning/policyguidance/planningplans.aspx",
            ],
            "local-authority:HOR": [  # Horsham
                "/planning/local-plan/read-the-current-local-plan",
            ],
            "local-authority:KHL": [  # Kingston upon Hull
                "/downloads/download/15/local-plan",
            ],
            "local-authority:KWL": [  # Knowsley
                "/planning-and-development/planning-policy/adopted-documents/",
            ],
            "local-authority:LUT": [  # Luton
                "/Page/Show/Environment/Planning/Regional%20and%20local%20planning/Pages/default.aspx",
            ],
            "local-authority:MAL": [  # Maldon
                "/homepage/7031/emerging_local_plan",
            ],
            "local-authority:MDB": [  # Middlesbrough
                "/planning-and-development/planning-policy/publication-local-plan/",
            ],
            "local-authority:MIK": [  # Milton Keynes
                "/planning-and-building/developingmk/planmk",
            ],
            "local-authority:NEC": [  # Newcastle-under-Lyme
                "/planning-policy/current-development-plan",
            ],
            "local-authority:NTY": [  # North Tyneside
                "/residents/planning/planning-policy/local-plan",
            ],
            "local-authority:OLD": [  # Oldham
                "/info/201229/current_local_planning_policy/2934/the_adopted_local_plan_in_oldham",
            ],
            "local-authority:PEN": [  # Pendle
                "/info/20072/planning_policies/273/local_plan",
            ],
            "local-authority:POR": [  # Portsmouth
                "/services/development-and-planning/planning-policy/portsmouth-local-plan/",
            ],
            "local-authority:REI": [  # Reigate and Banstead
                "/info/20271/local_plan",
                "/info/20271/local_plan/1101/development_plan",
            ],
            "local-authority:RUS": [  # Rushcliffe
                "/planning-growth/planning-policy/local-plan/",
            ],
            "local-authority:SLG": [  # Slough
                "/planning-policy/local-development-plan-slough",
            ],
            "local-authority:SND": [  # Sunderland
                "/article/15978/Core-Strategy-and-Development-Plan",
            ],
            "local-authority:STY": [  # South Tyneside
                "/article/3663/Local-Plan",
            ],
            "local-authority:TAN": [  # Tandridge
                "/Planning-and-building/Planning-strategies-and-policies/Adopted-development-plan",
            ],
            "local-authority:UTT": [  # Uttlesford
                "/article/4878/Planning-Policy-and-the-Local-Plan",
            ],
            "local-authority:WGN": [  # Wigan
                "/Council/Strategies-Plans-and-Policies/Planning/Local-plan/CoreStrategy.aspx",
            ],
            "national-park-authority:Q27178932": [  # Yorkshire Dales
                "/park-authority/living-and-working/planning-policy/local-planning-policy-pre-boundary-extension/",
            ],
            "national-park-authority:Q4972284": [  # Broads Authority
                "/planning/planning-policies/local-plan-for-the-broads",
            ],
        }

        # Build paths list: council-specific first, then generic
        paths = []

        # Add council-specific paths if they exist for this organization
        if org_code and org_code in council_specific_paths:
            paths.extend(council_specific_paths[org_code])

        # Add generic paths that work for most councils
        generic_paths = [
            "/planning",  # Common planning section
            "/local-plan",
            "/adopted-local-plan",  # Arun and similar councils with adopted plans
            "/localplan",
            "/planning/local-plan",
            "/planning/planning-policy",
            "/planning/planningpolicies",
            "/planning/planning-policies",
            "/planning/planning-policy/local-plan",
            "/planning/planning-policy/existing-local-plans",
            "/planning/policy/local-plan",
            "/planning-policy/local-plan",
            "/planning-policy",
            "/planning-policy-core-strategy",
            "/planningpolicy",
            "/planning-and-regeneration/local-plans",
            "/planning/strategic-planning/local-plan",
            "/planning/strategic-planning",
            "/services/planning/planning-policy",
            "/planning-and-building-control",
            "/planning-and-building-control/planning-policy",
            "/planning-applications/planning-policy",
            "/planning-services/planning-policy",
            "/emerging-local-plan",  # Emerging/draft plan patterns
            "/emerging-plan",
            "/draft-local-plan",
            "/draft-plan",
            "/new-local-plan",
            "/planning/emerging-local-plan",
            "/planning/draft-local-plan",
            "/planning/new-local-plan",
            "/planning/planning-policy/emerging-local-plan",
            "/planning-policy/emerging-local-plan",
            "/lgnl/planning_and_building_control/planning_policy_guidance/local_plan",  # Contensis CMS variant
            "/home/planning-development/planning-strategic-planning",
            "/home/planning-development/planning-strategic-planning/new-local-plan",
            "/planning/strategic-planning/emerging-local-plan",
            "/media",  # Common media storage directory
            "/media/documents",  # Common document storage paths
            "/media/downloads",
            "/media/files",
            "/media/uploads",
            "/documents",  # Generic document repositories
            "/downloads",
            "/files",
            "/uploads",
            "",  # Root path
        ]

        # Append generic paths to council-specific ones
        paths.extend(generic_paths)

        urls = []
        for domain in domains:
            for path in paths:
                urls.append(
                    {
                        "title": f"{org_name} Local Plan",
                        "url": f"https://{domain}{path}",
                        "snippet": f"Constructed URL for {org_name}",
                    }
                )

        # Prepend configured URLs (like from joint-local-plans.json) before generated URLs
        return configured_urls + urls

    def fetch_page_for_link_extraction(self, url: str) -> Optional[str]:
        """Fetch page content for link extraction, with Cloudflare bypass support.

        Args:
            url: URL to fetch

        Returns:
            HTML content if successful, None otherwise
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # Try standard requests first
        try:
            response = requests.get(
                url, headers=headers, timeout=15, allow_redirects=True, verify=False
            )
            if response.status_code == 200:
                return response.text
            elif response.status_code == 403:
                # Try cloudscraper for Cloudflare protection
                try:
                    scraper = cloudscraper.create_scraper()
                    response = scraper.get(url, headers=headers, timeout=15, allow_redirects=True)
                    if response.status_code == 200:
                        return response.text
                except Exception:
                    pass
        except Exception:
            pass

        return None

    def extract_local_plan_links(self, url: str, html_content: str) -> List[str]:
        """Extract links that likely point to local plan pages.

        Args:
            url: The base URL of the page
            html_content: The HTML content to parse

        Returns:
            List of URLs that likely point to local plan pages
        """
        from urllib.parse import urljoin

        soup = BeautifulSoup(html_content, "html.parser")

        local_plan_links = []

        # Find all links
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True).lower()

            # Look for links that mention local plan (including emerging/draft/new plans)
            local_plan_keywords = [
                "local plan",
                "local-plan",
                "localplan",
                "emerging local plan",
                "emerging plan",
                "emerging-local-plan",
                "draft local plan",
                "draft plan",
                "draft-local-plan",
                "new local plan",
                "new plan",
                "new-local-plan",
                "core strategy",
                "site allocation",
                "development plan",
                "planning policy",
                "regulation 18",
                "regulation 19",
                "consultation",
                "adopted local plan",
            ]

            # Check if link text or href contains local plan keywords
            is_local_plan_link = any(
                keyword in link_text for keyword in local_plan_keywords
            ) or any(keyword in href.lower() for keyword in local_plan_keywords)

            if is_local_plan_link:
                # Convert relative URLs to absolute
                full_url = urljoin(url, href)

                # Avoid duplicates and non-HTTP links
                if full_url.startswith("http") and full_url not in local_plan_links:
                    local_plan_links.append(full_url)

        return local_plan_links

    def classify_document_type(self, url: str, link_text: str) -> str:
        """Classify a document into a local-plan-document-type.

        Args:
            url: The document URL
            link_text: The link text for the document

        Returns:
            Document type classification
        """
        combined = (url + " " + link_text).lower()

        # Define classification patterns (order matters - most specific first)
        classifications = {
            # Examination and inspection documents
            "inspectors-report": [
                "inspector report",
                "inspector's report",
                "examination report",
                "inspectors final report",
                "inspector's final report",
            ],
            "examination-hearing-statement": [
                "examination hearing",
                "hearing statement",
                "matter statement",
                "examination document",
            ],
            "statement-of-common-ground": [
                "statement of common ground",
                "socg",
                "common ground statement",
            ],
            "main-modifications": [
                "main modifications",
                "proposed main modifications",
                "schedule of main modifications",
            ],
            # Consultation and adoption
            "adoption-statement": [
                "adoption statement",
                "adoption consultation statement",
                "statement of adoption",
            ],
            "consultation-statement": [
                "consultation statement",
                "statement of consultation",
                "regulation 22 statement",
            ],
            "representation-statement": [
                "representation statement",
                "summary of representations",
                "consultation responses",
            ],
            # Environmental and sustainability assessments
            "sustainability-appraisal": [
                "sustainability appraisal",
                "sa report",
                "sa addendum",
                "sa screening",
                "sa scoping",
                "sa non-technical summary",
            ],
            "strategic-environmental-assessment": [
                "strategic environmental assessment",
                "sea",
                "environmental report",
            ],
            "habitats-regulations-assessment": [
                "habitats regulations assessment",
                "hra",
                "appropriate assessment",
                "habitat assessment",
            ],
            "equalities-impact-assessment": [
                "equalities impact assessment",
                "eia",
                "equality impact",
                "equalities assessment",
            ],
            "health-impact-assessment": [
                "health impact assessment",
                "hia",
                "health assessment",
            ],
            # Housing and demographic evidence
            "strategic-housing-market-assessment": [
                "shma",
                "strategic housing market assessment",
                "housing market assessment",
                "housing needs assessment",
            ],
            "strategic-housing-land-availability": [
                "shlaa",
                "strategic housing land availability",
                "housing land availability",
                "helaa",
            ],
            "housing-delivery-test": [
                "housing delivery test",
                "hdt",
                "housing delivery action plan",
            ],
            "gypsy-and-traveller-assessment": [
                "gypsy and traveller",
                "gtaa",
                "traveller accommodation assessment",
            ],
            # Infrastructure and delivery
            "infrastructure-delivery-plan": [
                "infrastructure delivery plan",
                "idp",
                "infrastructure delivery",
            ],
            "transport-assessment": [
                "transport assessment",
                "transport study",
                "transport strategy",
                "local transport plan",
            ],
            "strategic-flood-risk-assessment": [
                "sfra",
                "strategic flood risk assessment",
                "flood risk assessment",
                "level 1 sfra",
                "level 2 sfra",
            ],
            "water-cycle-study": ["water cycle study", "wcs", "water resources"],
            # Economic and viability
            "viability-assessment": [
                "viability assessment",
                "viability study",
                "whole plan viability",
                "cil viability",
                "viability appraisal",
            ],
            "financial-viability-study": ["financial viability", "economic viability"],
            "employment-land-review": [
                "employment land review",
                "elr",
                "employment land study",
                "employment evidence",
            ],
            "retail-and-town-centre-study": [
                "retail study",
                "town centre study",
                "retail assessment",
                "town centres",
            ],
            "economic-development-strategy": [
                "economic development",
                "economic strategy",
                "economic assessment",
            ],
            # Character and design
            "landscape-character-assessment": [
                "landscape character assessment",
                "lca",
                "landscape assessment",
            ],
            "conservation-area-appraisal": [
                "conservation area appraisal",
                "conservation area assessment",
            ],
            "urban-design-framework": [
                "urban design framework",
                "design code",
                "design guide",
            ],
            "green-and-blue-infrastructure": [
                "green infrastructure",
                "blue infrastructure",
                "gi strategy",
                "open space strategy",
            ],
            # Development management
            "local-development-scheme": [
                "local development scheme",
                "lds",
                "local plan timetable",
            ],
            "statement-of-community-involvement": [
                "statement of community involvement",
                "sci",
                "community involvement",
            ],
            "authority-monitoring-report": [
                "authority monitoring report",
                "amr",
                "monitoring report",
                "annual monitoring",
            ],
            "policies-map": [
                "policies map",
                "policy map",
                "proposals map",
                "key diagram",
            ],
            # Plan types
            "area-action-plan": ["area action plan", "aap"],
            "neighbourhood-plan": [
                "neighbourhood plan",
                "neighbourhood development plan",
                "ndp",
            ],
            "supplementary-planning-document": [
                "supplementary planning document",
                "spd",
                "supplementary guidance",
            ],
            "site-allocations": [
                "site allocations",
                "site allocation",
                "allocations dpd",
                "allocations development plan",
                "site assessment",
            ],
            "development-management-policies": [
                "development management policies",
                "development management dpd",
                "dm policies",
            ],
            "core-strategy": ["core strategy", "cs dpd", "strategic policies"],
            "minerals-and-waste-plan": [
                "minerals and waste",
                "minerals local plan",
                "waste local plan",
                "minerals plan",
            ],
            "joint-strategic-plan": [
                "joint strategic plan",
                "joint plan",
                "strategic plan",
            ],
            # Main plan documents
            "local-plan-regulation-19": [
                "regulation 19",
                "publication version",
                "pre-submission",
                "publication draft",
            ],
            "local-plan-regulation-18": [
                "regulation 18",
                "preferred options",
                "draft plan",
                "consultation draft",
            ],
            "local-plan-submission": [
                "submission version",
                "submission draft",
                "submitted plan",
            ],
            "local-plan-adopted": ["adopted local plan", "adopted plan", "final plan"],
            "local-plan-review": [
                "local plan review",
                "review of local plan",
                "partial review",
            ],
            "local-plan": [
                "local plan",
                "development plan document",
                "dpd",
                "city plan",
                "borough plan",
                "district plan",
            ],
        }

        for doc_type, keywords in classifications.items():
            if any(keyword in combined for keyword in keywords):
                return doc_type

        # Default to local-plan if we can't classify
        return "local-plan"

    def extract_document_links(
        self, url: str, html_content: str
    ) -> List[Dict[str, str]]:
        """Extract PDF and Word document download links from a page with their link text.

        Args:
            url: The base URL of the page
            html_content: The HTML content to parse

        Returns:
            List of dicts with 'url', 'text', 'document-type', and 'source-url' for each document link
        """
        from urllib.parse import urljoin, urlparse

        soup = BeautifulSoup(html_content, "html.parser")

        doc_links = []

        # Find all links
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True)

            # Check if it's a document link (PDF, Word, or download patterns)
            is_document = (
                href.lower().endswith(".pdf")
                or href.lower().endswith(".doc")
                or href.lower().endswith(".docx")
                or ".pdf" in href.lower()
                or ".doc" in href.lower()
                or "/downloads/" in href.lower()
                or "/download/" in href.lower()
                or "download.cfm" in href.lower()  # ColdFusion download handler
                or "download.aspx" in href.lower()  # ASP.NET download handler
                or "download.php" in href.lower()  # PHP download handler
                or "getfile.aspx" in href.lower()  # ASP.NET file handler
                or "getdocument.aspx" in href.lower()  # ASP.NET document handler
                or "viewfile.aspx" in href.lower()  # ASP.NET view/download
                or "/file/" in href.lower()
                or "/document/" in href.lower()
                or "/docs/" in href.lower()
                or "pdf" in link_text.lower()
                or "download" in link_text.lower()
            )

            # Also check if link text suggests it's a plan-related document
            doc_keywords = [
                "local plan",
                "core strategy",
                "adopted",
                "submission",
                "regulation",
                "dpd",
                "spd",
                "sustainability",
                "appraisal",
                "assessment",
                "viability",
                "evidence",
                "inspector",
                "examination",
                "shma",
                "sfra",
                "policies map",
                "site allocation",
                "area action plan",
                "development plan",
            ]
            has_doc_keyword = any(kw in link_text.lower() for kw in doc_keywords)

            if is_document or has_doc_keyword:
                # Convert relative URLs to absolute
                full_url = urljoin(url, href)

                # Normalize URL (remove fragments, clean up)
                parsed = urlparse(full_url)
                normalized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.query:
                    normalized_url += f"?{parsed.query}"

                # Avoid duplicates and non-HTTP links
                if normalized_url.startswith("http") and not any(
                    d["url"] == normalized_url for d in doc_links
                ):
                    # Filter out URLs that are clearly listing/index pages, not actual documents
                    # These patterns indicate a page ABOUT documents, not a document itself
                    listing_page_indicators = [
                        "/info/",  # Birmingham info pages
                        "/downloads/" if "/downloads/file/" not in normalized_url else None,  # Generic downloads pages (but keep /downloads/file/)
                        "-documents",  # Pages listing documents
                        "_documents",
                        "/documents/" if not (normalized_url.endswith(".pdf") or "/documents/d/" in normalized_url) else None,  # Documents folders (unless direct PDF or /documents/d/ pattern)
                        "/planning-policy-plan-making",  # Policy overview pages
                        "/supplementary-planning-documents-spds" if not normalized_url.endswith(('.pdf', '.doc', '.docx')) else None,  # SPD listing pages
                    ]

                    # Remove None values
                    listing_page_indicators = [i for i in listing_page_indicators if i is not None]

                    # Skip if this looks like a listing page
                    is_listing_page = any(indicator in normalized_url for indicator in listing_page_indicators)

                    # But allow if it's clearly a file
                    is_clearly_file = (
                        normalized_url.endswith(('.pdf', '.doc', '.docx'))
                        or '/downloads/file/' in normalized_url  # Birmingham's file download pattern
                        or '/documents/d/' in normalized_url  # Mid Suffolk's document pattern
                        or 'download.cfm?' in normalized_url  # Arun's download handler
                        or 'download.aspx?' in normalized_url
                        or 'getfile.aspx?' in normalized_url
                    )

                    # Skip listing pages unless they're clearly files
                    if is_listing_page and not is_clearly_file:
                        continue

                    # Classify the document
                    doc_type = self.classify_document_type(normalized_url, link_text)

                    doc_links.append(
                        {
                            "url": normalized_url,
                            "text": link_text,
                            "document-type": doc_type,
                            "source-url": url,  # The page where this document was found
                        }
                    )

        return doc_links

    def fetch_page_content(self, url: str, max_length: int = 50000) -> tuple[str, bool]:
        """Fetch and extract text content from a URL.

        Uses cloudscraper to bypass Cloudflare protection if needed.

        Args:
            url: URL to fetch
            max_length: Maximum content length

        Returns:
            Tuple of (text content, success boolean)
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            # Try standard requests first
            try:
                response = requests.get(
                    url, headers=headers, timeout=15, allow_redirects=True, verify=False
                )
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                # If we get a 403 (Forbidden), it might be Cloudflare protection - try cloudscraper
                if e.response.status_code == 403:
                    print(f"  403 Forbidden - attempting to bypass with cloudscraper", file=sys.stderr)
                    try:
                        scraper = cloudscraper.create_scraper()
                        response = scraper.get(
                            url, headers=headers, timeout=15, allow_redirects=True
                        )
                        response.raise_for_status()
                        print(f"  Successfully bypassed protection with cloudscraper", file=sys.stderr)
                    except Exception as cs_error:
                        print(f"  Cloudscraper also failed: {type(cs_error).__name__}: {cs_error}", file=sys.stderr)
                        return "", False
                else:
                    raise

            # Only process HTML content
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type:
                print(f"  Skipping non-HTML content: {content_type}", file=sys.stderr)
                return "", False

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove script and style elements only (keep nav/footer/header as they may contain useful links)
            for script in soup(["script", "style"]):
                script.decompose()

            # Get text
            text = soup.get_text(separator="\n", strip=True)

            # Clean up excessive whitespace
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines)

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
            return [
                {
                    "organisation": org_code,
                    "organisation-name": "",
                    "error": f"Organisation code '{org_code}' not found in organisation.csv",
                }
            ]

        print(
            f"Searching for local plans for: {org_name} ({org_code})", file=sys.stderr
        )

        # Get official website if available
        official_website = self.get_organisation_website(org_code)

        # Construct likely URLs based on official website and common patterns
        print(f"Constructing likely URLs for {org_name}...", file=sys.stderr)
        likely_urls = self.construct_likely_urls(org_name, official_website, org_code)

        # Fetch content from likely URLs
        pages_content = []
        discovered_links = set()
        all_pdf_links = []

        for i, result in enumerate(likely_urls, 1):
            print(
                f"Trying URL {i}/{len(likely_urls)}: {result['url']}", file=sys.stderr
            )
            content, success = self.fetch_page_content(result["url"])
            if success and content:
                print(
                    f"  ✓ Success! Found content ({len(content)} chars)",
                    file=sys.stderr,
                )
                pages_content.append(
                    {"url": result["url"], "title": result["title"], "content": content}
                )

                # Extract local plan links and PDF links from this page
                try:
                    html_content = self.fetch_page_for_link_extraction(result["url"])
                    if html_content:
                        links = self.extract_local_plan_links(
                            result["url"], html_content
                        )
                        print(
                            f"  Found {len(links)} local plan links on this page",
                            file=sys.stderr,
                        )
                        discovered_links.update(links)

                        docs = self.extract_document_links(result["url"], html_content)
                        print(
                            f"  Found {len(docs)} document links on this page",
                            file=sys.stderr,
                        )
                        all_pdf_links.extend(docs)
                except Exception:
                    pass

                # Stop after finding 3 successful pages (to leave room for discovered links)
                if len(pages_content) >= 3:
                    break

        # Now fetch content from discovered local plan links
        print(
            f"\nFetching {len(discovered_links)} discovered local plan pages...",
            file=sys.stderr,
        )
        for link in list(discovered_links)[:10]:  # Limit to 10 additional pages
            # Skip if we already have this URL
            if any(p["url"] == link for p in pages_content):
                continue

            print(f"Fetching discovered link: {link}", file=sys.stderr)
            content, success = self.fetch_page_content(link)
            if success and content:
                print(f"  ✓ Success! ({len(content)} chars)", file=sys.stderr)
                pages_content.append(
                    {
                        "url": link,
                        "title": "Discovered local plan page",
                        "content": content,
                    }
                )

                # Also extract PDFs from discovered pages
                try:
                    html_content = self.fetch_page_for_link_extraction(link)
                    if html_content:
                        docs = self.extract_document_links(link, html_content)
                        print(f"  Found {len(docs)} document links", file=sys.stderr)
                        all_pdf_links.extend(docs)
                except Exception:
                    pass

                # Stop after we have enough pages total
                if len(pages_content) >= 8:
                    break

        if not pages_content:
            return [
                {
                    "organisation": org_code,
                    "organisation-name": org_name,
                    "error": "Could not fetch any page content from likely URLs",
                }
            ]

        print(f"\nTotal pages fetched: {len(pages_content)}", file=sys.stderr)
        print(f"Total document links found: {len(all_pdf_links)}", file=sys.stderr)

        # Prioritize actual file downloads over webpage links
        def document_priority(doc):
            """Return priority score (lower is better) for sorting documents."""
            url = doc['url'].lower()
            # Highest priority: Direct file downloads
            if 'download.cfm?' in url or 'download.aspx?' in url or 'getfile.aspx?' in url:
                return 0
            if url.endswith('.pdf') or '.pdf?' in url:
                return 1
            if url.endswith(('.doc', '.docx')) or '.doc?' in url:
                return 2
            if '/downloads/file/' in url or '/documents/d/' in url:
                return 3
            # Lower priority: Generic download patterns
            if '/download/' in url or '/downloads/' in url:
                return 4
            # Lowest priority: Everything else
            return 10

        # Sort documents by priority
        all_pdf_links.sort(key=document_priority)

        # Use Claude to analyze the content and extract information
        print(f"Analyzing content with Claude...", file=sys.stderr)

        content_summary = "\n\n---\n\n".join(
            [
                f"URL: {p['url']}\nTitle: {p['title']}\n\nContent:\n{p['content'][:10000]}"
                for p in pages_content
            ]
        )

        # Add document links summary
        if all_pdf_links:
            doc_summary = "\n\n" + "="*80 + "\n"
            doc_summary += "DOCUMENTS FOUND (Downloadable Files - PDFs, Word Docs, Download Handlers)\n"
            doc_summary += "⚠️ USE ONLY THESE URLs FOR document-url FIELDS ⚠️\n"
            doc_summary += "="*80 + "\n"
            for doc in all_pdf_links[:50]:  # Include up to 50 documents
                doc_summary += f"\n- URL: {doc['url']}\n"
                doc_summary += f"  Link text: {doc['text']}\n"
                doc_summary += f"  Classified as: {doc['document-type']}\n"
                doc_summary += f"  Found on page: {doc['source-url']}\n"
            doc_summary += "\n" + "="*80 + "\n"
            content_summary += doc_summary

            # Log first few documents for debugging
            # print(f"\nFirst 5 documents being sent to Claude:", file=sys.stderr)
            # for i, doc in enumerate(all_pdf_links[:5], 1):
            #     print(f"  {i}. {doc['text'][:60]}", file=sys.stderr)
            #     print(f"     URL: {doc['url']}", file=sys.stderr)
            #     print(f"     Type: {doc['document-type']}", file=sys.stderr)

        prompt = f"""I have searched for local plans for {org_name} and found these pages:

{content_summary}

⚠️⚠️⚠️ CRITICAL INSTRUCTION ⚠️⚠️⚠️
The page content above may contain URLs to webpages about local plans.
DO NOT USE THESE URLS - they are webpages, not downloadable files.
ONLY use URLs from the "DOCUMENTS FOUND" section which contains actual downloadable files.
Any URL you see in the page content that is NOT in "DOCUMENTS FOUND" is INVALID for document-url fields.

Based on this content, please identify ALL local plans (both adopted and emerging/draft) and provide information about each one. Local Planning Authorities often have multiple local plan documents such as:
- Core Strategy
- Site Allocations DPD
- Development Management Policies
- Area Action Plans
- Minerals and Waste Local Plans
- Joint Plans with other authorities
- New/Emerging Local Plans at various stages

⚠️ FINAL REMINDER BEFORE YOU START:
- For "document-url" fields: ONLY use URLs from "DOCUMENTS FOUND" section above
- URLs like /supplementary-planning-documents-spds, /supporting-documents, /helaa are WEBPAGES not files
- If you see a URL in the page content, CHECK if it's in "DOCUMENTS FOUND" before using it
- If it's NOT in "DOCUMENTS FOUND", do NOT use it for document-url

Return a JSON array where each element represents one local plan document:

[
    {{
        "organisation": "{org_code}",
        "organisation-name": "{org_name}",
        "reference": "unique identifier for this plan - format: LP-[ORG-REF]-[YEAR] (e.g., LP-DAC-2013, LP-BRX-2020)",
        "documentation-url": "the best URL for this specific local plan document (main page)",
        "document-url": "MUST be from DOCUMENTS FOUND section - a downloadable file URL (download.cfm, .pdf, etc.)",
        "name": "the full official name of this plan document (e.g., 'Dacorum Core Strategy 2006-2031', 'Dacorum Site Allocations DPD')",
        "status": "one of: draft, regulation-18, regulation-19, submitted, examination, adopted, withdrawn",
        "year": the year this plan was adopted (or year of latest milestone if not adopted, as an integer, e.g., 2013),
        "period-start-date": the start year of the plan period (as an integer, e.g., 2006) or "" if not available,
        "period-end-date": the end year of the plan period (as an integer, e.g., 2031) or "" if not available,
        "documents": [
            {{
                "document-url": "normalized URL to the document",
                "documentation-url": "the URL of the webpage that links to this document (from 'Found on page' field)",
                "document-type": "one of the classified types (see below)",
                "name": "readable name for the document",
                "reference": "short unique reference for this document (e.g., LP-2018-2033, SA-2020, IR-2020)",
                "document-status": "one of: draft, consultation, examination, adopted, withdrawn"
            }}
        ]
    }}
]

EXAMPLE OUTPUT:
{{
    "organisation": "local-authority:BRX",
    "organisation-name": "Broxbourne Borough Council",
    "reference": "LP-BRX-2020",
    "documentation-url": "https://www.broxbourne.gov.uk/planning/local-plan-2018-2033/1",
    "document-url": "https://www.broxbourne.gov.uk/downloads/file/1813/local-plan-2018-2033",
    "name": "Broxbourne Local Plan 2018-2033",
    "status": "adopted",
    "year": 2020,
    "period-start-date": 2018,
    "period-end-date": 2033,
    "documents": [
        {{
            "document-url": "https://www.broxbourne.gov.uk/downloads/file/1813/local-plan-2018-2033",
            "documentation-url": "https://www.broxbourne.gov.uk/planning/local-plan-2018-2033/1",
            "document-type": "local-plan",
            "name": "Local Plan 2018-2033",
            "reference": "LP-2018-2033",
            "document-status": "adopted"
        }},
        {{
            "document-url": "https://www.broxbourne.gov.uk/downloads/file/925/sustainability-appraisal-post-adoption-statement-may-2020",
            "documentation-url": "https://www.broxbourne.gov.uk/planning/local-plan-2018-2033/1",
            "document-type": "sustainability-appraisal",
            "name": "Sustainability Appraisal of the Local Plan 2018-2033",
            "reference": "SA-2020",
            "document-status": "adopted"
        }},
        {{
            "document-url": "https://www.broxbourne.gov.uk/downloads/file/924/broxbourne-lp-report-final",
            "documentation-url": "https://www.broxbourne.gov.uk/planning/local-plan-2018-2033/1",
            "document-type": "inspectors-report",
            "name": "Broxbourne LP Report Final",
            "reference": "IR-2020",
            "document-status": "adopted"
        }}
    ]
}}

STATUS FIELD GUIDE:
- "draft" = Early draft or Issues and Options stage
- "regulation-18" = Regulation 18 consultation (early engagement/preferred options)
- "regulation-19" = Regulation 19 consultation (pre-submission/publication stage)
- "submitted" = Submitted to the Planning Inspectorate for examination
- "examination" = Currently undergoing examination by Planning Inspector
- "adopted" = Formally adopted by the council
- "withdrawn" = Plan has been withdrawn

DOCUMENT TYPES (comprehensive list - use the most specific type):
Plan Documents:
- local-plan: Main local plan document (use specific variants below if applicable)
- local-plan-adopted: Adopted local plan
- local-plan-regulation-19: Regulation 19 publication version
- local-plan-regulation-18: Regulation 18 draft/preferred options
- local-plan-submission: Submission version to Planning Inspectorate
- local-plan-review: Local plan review documents
- core-strategy: Core strategy document
- site-allocations: Site allocations DPD
- development-management-policies: Development management policies
- minerals-and-waste-plan: Minerals and waste local plans
- joint-strategic-plan: Joint strategic plans
- area-action-plan: Area action plans (AAPs)
- neighbourhood-plan: Neighbourhood plans
- supplementary-planning-document: SPDs

Examination Documents:
- inspectors-report: Planning Inspector's examination report
- examination-hearing-statement: Examination hearing statements
- statement-of-common-ground: Statements of common ground
- main-modifications: Schedule of main modifications

Consultation Documents:
- adoption-statement: Adoption statements
- consultation-statement: Consultation statements
- representation-statement: Summary of representations

Environmental Assessments:
- sustainability-appraisal: Sustainability appraisal
- strategic-environmental-assessment: Strategic environmental assessment
- habitats-regulations-assessment: Habitats regulations assessment
- equalities-impact-assessment: Equalities impact assessment
- health-impact-assessment: Health impact assessment

Housing Evidence:
- strategic-housing-market-assessment: SHMA/housing needs assessment
- strategic-housing-land-availability: SHLAA/HELAA
- housing-delivery-test: Housing delivery test action plans
- gypsy-and-traveller-assessment: Gypsy and traveller accommodation

Infrastructure Evidence:
- infrastructure-delivery-plan: Infrastructure delivery plans
- transport-assessment: Transport assessments and strategies
- strategic-flood-risk-assessment: Strategic flood risk assessment
- water-cycle-study: Water cycle studies

Economic Evidence:
- viability-assessment: Viability assessments
- financial-viability-study: Financial viability studies
- employment-land-review: Employment land reviews
- retail-and-town-centre-study: Retail and town centre studies
- economic-development-strategy: Economic strategies

Character and Design:
- landscape-character-assessment: Landscape character assessments
- conservation-area-appraisal: Conservation area appraisals
- urban-design-framework: Urban design frameworks
- green-and-blue-infrastructure: Green/blue infrastructure strategies

Development Management:
- local-development-scheme: Local development scheme (timetable)
- statement-of-community-involvement: Statement of community involvement
- authority-monitoring-report: Annual monitoring reports
- policies-map: Policies map/proposals map

IMPORTANT:
- Return an array with one element for EACH separate local plan document
- Include BOTH current plans AND superseded/previous plans (e.g., a plan for 2018-2033 AND an older plan for 2001-2011)
- Include BOTH adopted plans AND emerging/draft plans at various stages
- Look for status indicators like "adopted", "Regulation 18", "Regulation 19", "consultation", "examination", "submitted", "withdrawn"

CRITICAL DISTINCTION - UNDERSTAND THESE TWO DIFFERENT FIELDS:

DOCUMENTATION-URL (the webpage about the plan):
- This is the HTML webpage that DESCRIBES or LINKS TO the local plan
- Example: "https://www.arun.gov.uk/adopted-local-plan" (a webpage with information)
- Example: "https://www.example.gov.uk/planning/local-plan-2018-2033" (a webpage)
- This is where you FIND the download link, not the download itself

DOCUMENT-URL (the actual downloadable file) - CRITICAL REQUIREMENT:
⚠️ MANDATORY RULE: You MUST ONLY use URLs from the "DOCUMENTS FOUND" section
- DO NOT use webpage URLs from the page content
- DO NOT invent or construct URLs
- ONLY copy URLs directly from "DOCUMENTS FOUND" list
- This MUST be a downloadable file URL (PDF, download.cfm, etc.)
- Example from "DOCUMENTS FOUND": "https://www.arun.gov.uk/download.cfm?doc=docm93jijm4n12844.pdf&ver=12984"
- If no suitable file exists in "DOCUMENTS FOUND", use "" (empty string)
- THOROUGHLY search the "DOCUMENTS FOUND" section above for the main plan document

HOW TO FIND THE RIGHT DOCUMENT-URL:
1. FIRST: Look for documents whose link text exactly matches the plan name
   * Example: If the plan is "City Plan 2040", look for a document with link text "City Plan 2040" or "City Plan 2040 PDF"
   * Example: If the plan is "Local Plan 2018-2033", look for link text "Local Plan 2018-2033" or "Adopted Local Plan 2018-2033"

2. SECOND: Look for documents with matching time periods
   * If the plan covers 2018-2033, prioritize documents with "2018-2033" or "2018" or "2033" in the URL or link text
   * Match the plan period dates to document names

3. THIRD: Look for documents matching the plan status
   * For adopted plans: Look for "adopted", "final", "adoption" in the document name
   * For Regulation 19 plans: Look for "regulation 19", "publication", "pre-submission" in the document name
   * For Regulation 18 plans: Look for "regulation 18", "preferred options", "consultation draft" in the document name

4. FOURTH: Look for generic plan names if specific matches fail
   * "Local Plan", "Core Strategy", "City Plan", "Borough Plan", "District Plan"
   * Look in URLs for patterns like "/assets/", "/downloads/", "/file/", "/documents/" followed by plan names

5. FIFTH: Check document types carefully
   * Prioritize documents classified as "local-plan", "local-plan-adopted", "local-plan-regulation-19", "local-plan-regulation-18"
   * Then try "core-strategy", "site-allocations", "development-management-policies"
   * AVOID evidence documents unless no main plan is found (sustainability appraisal, viability assessment, etc.)

6. COMMON URL PATTERNS TO RECOGNIZE:
   * Direct PDF links: ending in .pdf
   * Download endpoints: /downloads/file/[ID]/[name]
   * Download handlers: download.cfm?doc=..., download.aspx?id=..., download.php?file=...
   * Asset storage: /assets/[folder]/[filename].pdf
   * Document repositories: /documents/[ID] or /docs/[name]

7. HOW TO TELL IF A URL IS A DOWNLOADABLE FILE:
   * ✅ VALID (these ARE downloadable files):
     - Ends with: .pdf, .doc, .docx
     - Contains: download.cfm, download.aspx, download.php, getfile.aspx
     - Contains: /downloads/, /file/, /assets/
     - Example: https://www.arun.gov.uk/download.cfm?doc=docm93jijm4n12844.pdf&ver=12984 ✅
     - Example: https://www.example.gov.uk/assets/local-plan.pdf ✅

   * ❌ INVALID (these are webpages, NOT files):
     - No file extension and no download handler
     - Example: https://www.example.gov.uk/local-plan ❌
     - Example: https://www.example.gov.uk/adopted-local-plan ❌
     - Example: https://www.example.gov.uk/planning-policy ❌

8. ONLY use "" (empty string) if:
   * You have searched thoroughly through ALL documents in "DOCUMENTS FOUND"
   * No document reasonably matches the plan name, period, or status
   * The page only describes the plan but has no downloadable documents

EXAMPLES OF CORRECT MATCHING:
- Plan: "Arun Local Plan" (adopted, 2018)
  → Documentation URL: "https://www.arun.gov.uk/adopted-local-plan" (the webpage)
  → Document URL: "https://www.arun.gov.uk/download.cfm?doc=docm93jijm4n12844.pdf&ver=12984" (the actual PDF)
  (Notice: documentation-url is the HTML page, document-url is the downloadable file)

- Plan: "City Plan 2040" (regulation-19)
  → Documentation URL: "https://www.cityoflondon.gov.uk/planning/city-plan"
  → Document URL: "https://www.cityoflondon.gov.uk/assets/Services-Environment/City-Plan-2040.pdf"
  (Matches plan name exactly and is a direct PDF link)

- Plan: "Local Plan 2018-2033" (adopted, 2020)
  → Documentation URL: "https://www.example.gov.uk/planning/local-plan"
  → Document URL: "https://www.example.gov.uk/downloads/file/1813/local-plan-2018-2033"
  (Matches plan name and period, classified as local-plan)

- Plan: "Core Strategy 2006-2031" (adopted, 2013)
  → Documentation URL: "https://www.example.gov.uk/planning/core-strategy"
  → Document URL: "https://www.example.gov.uk/planning/core-strategy-2006-2031.pdf"
  (Matches plan type and period)

CRITICAL INSTRUCTIONS FOR DOCUMENT-URL:
1. FIRST PRIORITY: Look for the actual PDF/document download URL in the "DOCUMENTS FOUND" section
   - These will have URLs ending in .pdf, .doc, .docx
   - OR URLs containing: download.cfm, download.aspx, download.php, getfile.aspx, /downloads/, /file/
2. The document-url MUST be a downloadable file URL - NEVER a webpage URL
3. ONLY use "" (empty string) if you absolutely cannot find ANY downloadable file in "DOCUMENTS FOUND"

REMEMBER: Finding the right downloadable file URL is your top priority - they ARE in the "DOCUMENTS FOUND" section!

DOCUMENTS ARRAY - CRITICAL RULES:
- The documents array should contain ALL related documents for this local plan
- Include documents from the "DOCUMENTS FOUND" section that relate to this specific plan

⚠️ MANDATORY RULE FOR DOCUMENT-URL FIELD:
- You MUST ONLY use URLs that appear in the "DOCUMENTS FOUND" section above
- DO NOT invent or construct document-url values from the page content
- DO NOT use webpage URLs you find in the page content
- ONLY copy-paste URLs from the "DOCUMENTS FOUND" list
- If no suitable document exists in "DOCUMENTS FOUND", use "" (empty string)

For each document in the array, provide:

DOCUMENT-URL:
  * MUST be copied directly from a URL in the "DOCUMENTS FOUND" section (do not modify it)
  * This is the downloadable file URL (PDF, download.cfm, etc.)
  * Example from "DOCUMENTS FOUND": "https://www.arun.gov.uk/download.cfm?doc=docm93jijm4n12844.pdf&ver=12984"

DOCUMENTATION-URL:
  * Copy from the "Found on page" field in "DOCUMENTS FOUND" section
  * This is the HTML webpage that contains the link
  * Example: "https://www.arun.gov.uk/adopted-local-plan"

DOCUMENT-TYPE: Use the pre-classified type from "DOCUMENTS FOUND" section (already done)

NAME: Use the actual document title as closely as possible
  * Extract directly from the link text in the "DOCUMENTS FOUND" section
  * Keep the original phrasing and wording from the link text
  * Preserve the document's own title/name rather than creating a new one
  * Clean up minor formatting issues (extra spaces, line breaks)
  * Examples showing link text → name:
    - Link text: "Local Plan 2018-2033" → name: "Local Plan 2018-2033"
    - Link text: "Sustainability Appraisal of the Local Plan 2018-2033" → name: "Sustainability Appraisal of the Local Plan 2018-2033"
    - Link text: "Broxbourne LP Report Final" → name: "Broxbourne LP Report Final"
    - Link text: "previous local plan" → name: "Full Adopted Local Plan 2001-2011" (extract from URL if link text is vague)
    - Link text: "Core Strategy 2006-2031 (PDF 2.3MB)" → name: "Core Strategy 2006-2031"
  * Only modify if the link text is too vague (e.g., "download", "click here")
  * Keep abbreviations if they appear in the original title (e.g., "LP", "DPD", "SA")
  * Remove file size indicators (e.g., "PDF 2.3MB") but keep everything else

REFERENCE: Create a short, unique reference code for the document
  * Format: [TYPE-PREFIX]-[YEAR or IDENTIFIER]
  * Type prefixes based on document-type:
    - LP = local-plan
    - CS = core-strategy
    - SA-DPD = site-allocations
    - AS = adoption-statement
    - AAP = area-action-plan
    - FVS = financial-viability-study
    - IR = inspectors-report
    - PM = policies-map
    - SFRA = strategic-flood-risk-assessment
    - SHMA = strategic-housing-market-assessment
    - SPD = supplementary-planning-documents
    - LDS = local-development-scheme
    - SA = sustainability-appraisal
    - LPR = local-plan-review
    - VA = viability-assessment
  * Examples:
    - "LP-2018-2033" for Local Plan 2018-2033
    - "SA-2020" for Sustainability Appraisal 2020
    - "IR-2020" for Inspector's Report 2020
    - "CS-2006-2031" for Core Strategy 2006-2031
    - "SHMA-2015" for Strategic Housing Market Assessment 2015
    - "SPD-EMPLOYMENT-2021" for Employment SPD 2021
  * Ensure uniqueness within the organisation by adding identifiers if needed
  * Keep it short (under 30 characters) but descriptive enough to identify the document

DOCUMENT-STATUS: Determine the status of this specific document
  * Look for status indicators in the document name/link text:
    - "draft" = Draft documents, early versions, issues & options
    - "consultation" = Documents out for consultation, preferred options, regulation 18/19 consultation
    - "examination" = Examination documents, submitted versions undergoing examination
    - "adopted" = Formally adopted documents, final versions
    - "withdrawn" = Withdrawn or superseded documents
  * Status keywords to look for:
    - "draft", "early draft", "issues and options" → draft
    - "consultation", "regulation 18", "regulation 19", "preferred options" → consultation
    - "submission", "examination", "submitted version" → examination
    - "adopted", "final", "post-adoption" → adopted
    - "withdrawn", "superseded", "replaced" → withdrawn
  * Default to "adopted" for older documents if no status indicator is present
  * The document status may differ from the plan status (e.g., an adopted plan may have draft evidence documents)

MATCHING DOCUMENTS TO PLANS:
- For each plan, include:
  * The main local plan document (if different from document-url)
  * Supporting documents: sustainability appraisal, inspector's report, adoption statement
  * Evidence base: SHMA, SFRA, viability assessments
  * Related documents: site allocations, SPDs, policies map
- Match documents to plans by:
  * Looking for plan names/dates in the document link text
  * Matching time periods (e.g., documents from 2018-2020 likely relate to a 2018-2033 plan)
  * Document types that logically belong together
- If a document clearly relates to the plan, include it even if not explicitly named

OTHER FIELDS:
- For the reference field:
  * Format: "LP-[ORG-REF]-[YEAR]"
  * Extract the organisation reference from the organisation CURIE (e.g., "DAC" from "local-authority:DAC", "BRX" from "local-authority:BRX")
  * Use the year field value (the adoption year or latest milestone year)
  * Examples: "LP-DAC-2013", "LP-BRX-2020", "LP-TWH-2020"
  * This must uniquely identify each local plan within the dataset
  * If there are multiple plans with the same organisation and year, add a suffix like "-2" (e.g., "LP-DAC-2013-2")
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
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract the response text
            response_text = ""
            for block in message.content:
                if hasattr(block, "text"):
                    response_text += block.text

            print(f"Received response from Claude", file=sys.stderr)

            # Try to parse JSON from the response (looking for array)
            json_start = response_text.find("[")
            json_end = response_text.rfind("]") + 1

            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                result = json.loads(json_str)

                # Validate it's an array
                if not isinstance(result, list):
                    result = [result]  # Wrap single object in array

                # Validate and add endpoint field to each document (SHA256 hash of document URL)
                for plan in result:
                    # Validate main document-url
                    if "document-url" in plan and plan["document-url"]:
                        doc_url = plan["document-url"]
                        is_likely_webpage = (
                            not doc_url.endswith(('.pdf', '.doc', '.docx'))
                            and 'download.cfm' not in doc_url.lower()
                            and 'download.aspx' not in doc_url.lower()
                            and 'download.php' not in doc_url.lower()
                            and 'getfile' not in doc_url.lower()
                            and '/downloads/file/' not in doc_url.lower()
                            and '/documents/d/' not in doc_url.lower()
                            and '.pdf' not in doc_url.lower()
                        )
                        if is_likely_webpage:
                            plan["document-url"] = ""  # Clear invalid URL

                    if "documents" in plan and isinstance(plan["documents"], list):
                        for doc in plan["documents"]:
                            if "document-url" in doc:
                                url = doc["document-url"]

                                # Validate document URL
                                is_likely_webpage = (
                                    not url.endswith(('.pdf', '.doc', '.docx'))
                                    and 'download.cfm' not in url.lower()
                                    and 'download.aspx' not in url.lower()
                                    and 'download.php' not in url.lower()
                                    and 'getfile' not in url.lower()
                                    and '/downloads/file/' not in url.lower()
                                    and '/documents/d/' not in url.lower()
                                    and '.pdf' not in url.lower()
                                )
                                if is_likely_webpage:
                                    doc["document-url"] = ""  # Clear invalid URL

                                endpoint = hashlib.sha256(
                                    doc["document-url"].encode("utf-8")
                                ).hexdigest()
                                doc["endpoint"] = endpoint

                print(f"Found {len(result)} local plan(s)", file=sys.stderr)
                return result
            else:
                # Fallback: try to parse as single object and wrap in array
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1

                if json_start != -1 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    result = json.loads(json_str)

                    # Add endpoint field to each document (SHA256 hash of document URL)
                    result_list = [result]
                    for plan in result_list:
                        # Validate main document-url
                        if "document-url" in plan and plan["document-url"]:
                            doc_url = plan["document-url"]
                            # Check if it looks like a webpage instead of a downloadable file
                            is_likely_webpage = (
                                not doc_url.endswith(('.pdf', '.doc', '.docx'))
                                and 'download.cfm' not in doc_url.lower()
                                and 'download.aspx' not in doc_url.lower()
                                and 'download.php' not in doc_url.lower()
                                and 'getfile' not in doc_url.lower()
                                and '/downloads/file/' not in doc_url.lower()
                                and '.pdf' not in doc_url.lower()
                            )
                            if is_likely_webpage:
                                plan["document-url"] = ""  # Clear invalid URL

                        if "documents" in plan and isinstance(plan["documents"], list):
                            for doc in plan["documents"]:
                                if "document-url" in doc:
                                    url = doc["document-url"]

                                    # Validate document URL
                                    is_likely_webpage = (
                                        not url.endswith(('.pdf', '.doc', '.docx'))
                                        and 'download.cfm' not in url.lower()
                                        and 'download.aspx' not in url.lower()
                                        and 'download.php' not in url.lower()
                                        and 'getfile' not in url.lower()
                                        and '/downloads/file/' not in url.lower()
                                        and '.pdf' not in url.lower()
                                    )
                                    if is_likely_webpage:
                                        doc["document-url"] = ""  # Clear invalid URL

                                    endpoint = hashlib.sha256(
                                        doc["document-url"].encode("utf-8")
                                    ).hexdigest()
                                    doc["endpoint"] = endpoint

                    return result_list
                else:
                    return [
                        {
                            "organisation": org_code,
                            "organisation-name": org_name,
                            "error": "Could not parse JSON response from Claude",
                            "raw_response": response_text[:500],
                        }
                    ]

        except Exception as e:
            return [
                {
                    "organisation": org_code,
                    "organisation-name": org_name,
                    "error": str(e),
                }
            ]


def main():
    parser = argparse.ArgumentParser(
        description="Find all local plan documentation URLs for an organisation using Claude",
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
""",
    )

    parser.add_argument(
        "organisation", help="Organisation code (e.g., local-authority:DAC)"
    )

    parser.add_argument(
        "--organisation-csv",
        default="var/cache/organisation.csv",
        help="Path to organisation CSV file (default: var/cache/organisation.csv)",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode - test URL fetching without calling Claude API",
    )

    parser.add_argument(
        "--save-pdfs",
        dest="save_pdfs",
        action="store_true",
        default=True,
        help="Download and save all PDFs found (default: True)",
    )

    parser.add_argument(
        "--no-save-pdfs",
        dest="save_pdfs",
        action="store_false",
        help="Do not download and save PDFs",
    )

    args = parser.parse_args()

    # Get API key from environment variable
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key and not args.debug:
        print("Error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        print("Set it with: export ANTHROPIC_API_KEY='your-key-here'", file=sys.stderr)
        print("Or use --debug flag to test URL fetching only", file=sys.stderr)
        sys.exit(1)

    # Create finder (API key can be None in debug mode)
    finder = LocalPlanFinder(api_key or "debug", args.organisation_csv)

    if args.debug:
        # Debug mode - just show what URLs we would try
        org_name = finder.get_organisation_name(args.organisation)
        if not org_name:
            print(
                f"Error: Organisation code '{args.organisation}' not found",
                file=sys.stderr,
            )
            sys.exit(1)

        official_website = finder.get_organisation_website(args.organisation)

        print(f"Organisation: {org_name} ({args.organisation})", file=sys.stderr)
        if official_website:
            print(f"Official website: {official_website}", file=sys.stderr)
        print(f"\nTesting URL fetching...\n", file=sys.stderr)

        likely_urls = finder.construct_likely_urls(org_name, official_website, args.organisation)
        success_count = 0

        discovered_links = set()
        all_pdfs = []

        for i, result in enumerate(likely_urls[:10], 1):  # Test first 10 URLs
            print(f"{i}. {result['url']}", file=sys.stderr)
            content, success = finder.fetch_page_content(result["url"])
            if success:
                success_count += 1
                print(f"   ✓ Success! ({len(content)} chars)", file=sys.stderr)
                if success_count == 1:
                    # Show snippet of first successful fetch
                    print(f"\n   First 500 chars of content:", file=sys.stderr)
                    print(f"   {content[:500]}...\n", file=sys.stderr)

                # Try to extract local plan links and PDFs
                try:
                    import requests

                    response = requests.get(
                        result["url"],
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        },
                        timeout=15,
                        allow_redirects=True,
                        verify=False,
                    )
                    if response.status_code == 200:
                        links = finder.extract_local_plan_links(
                            result["url"], response.text
                        )
                        if links:
                            print(
                                f"   Found {len(links)} local plan links:",
                                file=sys.stderr,
                            )
                            for link in links[:5]:  # Show first 5
                                print(f"     - {link}", file=sys.stderr)
                            discovered_links.update(links)

                        docs = finder.extract_document_links(
                            result["url"], response.text
                        )
                        if docs:
                            print(
                                f"   Found {len(docs)} document links:", file=sys.stderr
                            )
                            for doc in docs[:5]:  # Show first 5
                                print(f"     - {doc['url']}", file=sys.stderr)
                                print(
                                    f"       Text: {doc['text'][:60]}...",
                                    file=sys.stderr,
                                )
                                print(
                                    f"       Type: {doc['document-type']}",
                                    file=sys.stderr,
                                )
                                print(
                                    f"       Source: {doc['source-url']}",
                                    file=sys.stderr,
                                )
                            all_pdfs.extend(docs)
                except Exception as e:
                    pass

        print(f"\nSuccessfully fetched {success_count} pages", file=sys.stderr)
        print(
            f"Discovered {len(discovered_links)} total local plan links",
            file=sys.stderr,
        )
        print(f"Discovered {len(all_pdfs)} total document links", file=sys.stderr)

        # Show document type breakdown
        if all_pdfs:
            type_counts = {}
            for doc in all_pdfs:
                doc_type = doc["document-type"]
                type_counts[doc_type] = type_counts.get(doc_type, 0) + 1
            print(f"\nDocument types found:", file=sys.stderr)
            for doc_type, count in sorted(
                type_counts.items(), key=lambda x: x[1], reverse=True
            ):
                print(f"  {doc_type}: {count}", file=sys.stderr)

        sys.exit(0)

    # Normal mode - full search
    results = finder.find_local_plan(args.organisation)

    # Save results to source file
    if results and not any("error" in r for r in results):
        org_code = args.organisation
        source_file = f"source/{org_code}.json"

        print(f"\nSaving results to {source_file}", file=sys.stderr)
        Path("source").mkdir(parents=True, exist_ok=True)
        with open(source_file, "w") as f:
            json.dump(results, f, indent=2)

        # Download all documents (if requested)
        if args.save_pdfs:
            print(f"\nDownloading documents...", file=sys.stderr)
            downloaded = 0
            skipped = 0
            failed = 0

            for plan in results:
                if "documents" in plan and isinstance(plan["documents"], list):
                    for doc in plan["documents"]:
                        doc_url = doc.get("document-url", "")
                        endpoint = doc.get("endpoint", "")

                        if doc_url and endpoint:
                            result = download_document(doc_url, endpoint)
                            if result:
                                if result.get("resource"):
                                    downloaded += 1
                                else:
                                    failed += 1
                            else:
                                skipped += 1

                            time.sleep(0.5)  # Be nice to servers

            print(f"\n{'='*60}", file=sys.stderr)
            print(f"Download Summary:", file=sys.stderr)
            print(f"  Downloaded: {downloaded}", file=sys.stderr)
            print(f"  Skipped: {skipped}", file=sys.stderr)
            print(f"  Failed: {failed}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
        else:
            print(f"\nSkipping PDF downloads (--no-save-pdfs flag set)", file=sys.stderr)

    # Output JSON to stdout
    print(json.dumps(results, indent=2))

    # Summary to stderr
    if results and not any("error" in r for r in results):
        print(
            f"\n✓ Found and processed {len(results)} local plan document(s)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
