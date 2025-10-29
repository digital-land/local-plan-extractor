#!/usr/bin/env python3
"""
Find local plan documentation URLs for a given organisation using Claude.

Usage:
    python bin/find-local-plan.py local-authority:DAC
    python bin/find-local-plan.py local-authority:MAN

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
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional, List


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
        self, api_key: str, organisation_csv: str = "var/cache/organisation.csv"
    ):
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
        self, org_name: str, official_website: Optional[str] = None
    ) -> List[Dict[str, str]]:
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

        # Common paths for local plan pages
        paths = [
            "/planning",  # Try planning section first
            "/lgnl/planning_and_building_control/planning_policy_guidance/Local_plan/local_plan.aspx",  # Tower Hamlets adopted plan (Contensis CMS)
            "/lgnl/planning_and_building_control/planning_policy_guidance/Emerging-Draft-Local-Plan.aspx",  # Tower Hamlets emerging plan
            "/lgnl/planning_and_building_control/planning_policy_guidance/emerging-draft-local-plan.aspx",  # Case variation
            "/lgnl/planning_and_building_control/planning_policy_guidance/new-local-plan.aspx",  # Other Contensis CMS variations
            "/newlocalplan",  # Fenland and similar councils
            "/developmentplan",  # Fenland and similar councils
            "/emerging-local-plan",  # Common emerging plan patterns
            "/emerging-plan",
            "/draft-local-plan",
            "/draft-plan",
            "/new-local-plan",
            "/local-plan",
            "/localplan",
            "/planning/local-plan",
            "/planning/emerging-local-plan",
            "/planning/draft-local-plan",
            "/planning/new-local-plan",
            "/planning/planning-policy/local-plan",
            "/planning/planning-policy/emerging-local-plan",
            "/planning-policy/local-plan",
            "/planning-policy/emerging-local-plan",
            "/planning-policy",
            "/lgnl/planning_and_building_control/planning_policy_guidance/local_plan",  # Variant without .aspx
            "/home/planning-development/planning-strategic-planning",
            "/home/planning-development/planning-strategic-planning/new-local-plan",
            "/planning/strategic-planning/local-plan",
            "/planning/strategic-planning/emerging-local-plan",
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
                urls.append(
                    {
                        "title": f"{org_name} Local Plan",
                        "url": f"https://{domain}{path}",
                        "snippet": f"Constructed URL for {org_name}",
                    }
                )

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
            response = requests.get(
                url, headers=headers, timeout=15, allow_redirects=True
            )
            response.raise_for_status()

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
        likely_urls = self.construct_likely_urls(org_name, official_website)

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
                    response = requests.get(
                        result["url"],
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        },
                        timeout=15,
                        allow_redirects=True,
                    )
                    if response.status_code == 200:
                        links = self.extract_local_plan_links(
                            result["url"], response.text
                        )
                        print(
                            f"  Found {len(links)} local plan links on this page",
                            file=sys.stderr,
                        )
                        discovered_links.update(links)

                        docs = self.extract_document_links(result["url"], response.text)
                        print(
                            f"  Found {len(docs)} document links on this page",
                            file=sys.stderr,
                        )
                        all_pdf_links.extend(docs)
                except Exception as e:
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
                    response = requests.get(
                        link,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        },
                        timeout=15,
                        allow_redirects=True,
                    )
                    if response.status_code == 200:
                        docs = self.extract_document_links(link, response.text)
                        print(f"  Found {len(docs)} document links", file=sys.stderr)
                        all_pdf_links.extend(docs)
                except Exception as e:
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
            doc_summary = "\n\nDOCUMENTS FOUND (PDF and Word):\n"
            for doc in all_pdf_links[:50]:  # Include up to 50 documents
                doc_summary += f"- {doc['url']}\n  Link text: {doc['text']}\n  Classified as: {doc['document-type']}\n  Found on page: {doc['source-url']}\n"
            content_summary += doc_summary

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
        "documentation-url": "the best URL for this specific local plan document (main page)",
        "document-url": "the direct URL to the PDF document for this plan (e.g., core strategy PDF, local plan PDF)",
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

DOCUMENTATION-URL (the main page for the plan):
- The documentation-url should be the MOST SPECIFIC URL for that particular document:
  * Prefer pages specifically about that local plan (e.g., "/planning/local-plan-2018-2033")
  * Avoid generic planning section URLs unless that's all that's available

DOCUMENT-URL (the actual PDF document) - CRITICAL REQUIREMENT:
- This is the MOST IMPORTANT field - you MUST try VERY HARD to find the main plan document PDF
- The document-url should be the direct URL to the PDF document itself (not the page about it)
- THOROUGHLY search the "DOCUMENTS FOUND" section above for the main plan document
- For each local plan, you MUST find its main PDF document if it exists at all

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
   * Asset storage: /assets/[folder]/[filename].pdf
   * Document repositories: /documents/[ID] or /docs/[name]

7. VALIDATION:
   * The URL MUST point to an actual downloadable file (PDF, Word doc)
   * The URL should NOT be a webpage/HTML page about the plan
   * If in doubt between multiple candidates, choose the one with the most specific name match

8. ONLY use "" (empty string) if:
   * You have searched thoroughly through ALL documents in "DOCUMENTS FOUND"
   * No document reasonably matches the plan name, period, or status
   * The page only describes the plan but has no downloadable documents

EXAMPLES OF CORRECT MATCHING:
- Plan: "City Plan 2040" (regulation-19)
  → Document URL: "https://www.cityoflondon.gov.uk/assets/Services-Environment/City-Plan-2040.pdf"
  (Matches plan name exactly and is a direct PDF link)

- Plan: "Local Plan 2018-2033" (adopted, 2020)
  → Document URL: "https://www.example.gov.uk/downloads/file/1813/local-plan-2018-2033"
  (Matches plan name and period, classified as local-plan)

- Plan: "Core Strategy 2006-2031" (adopted, 2013)
  → Document URL: "https://www.example.gov.uk/planning/core-strategy-2006-2031.pdf"
  (Matches plan type and period)

REMEMBER: The document-url field is MANDATORY - finding the right PDF is your top priority!

DOCUMENTS ARRAY:
- The documents array should contain ALL related documents for this local plan
- Include documents from the "DOCUMENTS FOUND" section that relate to this specific plan
- For each document, provide:

DOCUMENT-URL: Use the normalized URL from "DOCUMENTS FOUND" section

DOCUMENTATION-URL: Use the source page URL from "Found on page" field in "DOCUMENTS FOUND" section
  * This is the webpage that contains the link to the document
  * Extract from the "Found on page" field for each document
  * This helps users understand where the document was discovered and provides context

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

                # Add endpoint field to each document (SHA256 hash of document URL)
                for plan in result:
                    if "documents" in plan and isinstance(plan["documents"], list):
                        for doc in plan["documents"]:
                            if "document-url" in doc:
                                url = doc["document-url"]
                                endpoint = hashlib.sha256(
                                    url.encode("utf-8")
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
                        if "documents" in plan and isinstance(plan["documents"], list):
                            for doc in plan["documents"]:
                                if "document-url" in doc:
                                    url = doc["document-url"]
                                    endpoint = hashlib.sha256(
                                        url.encode("utf-8")
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

        likely_urls = finder.construct_likely_urls(org_name, official_website)
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

        # Download all documents
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
