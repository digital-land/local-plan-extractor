#!/usr/bin/env python3
"""
Download main local plan documents from all local planning authority JSON files.

This script:
1. Scans all source/*.json files (one per local planning authority)
2. Identifies the main document for each authority based on:
   - status: "adopted"
   - document-type: "local-plan-adopted" or "local-plan"
   - has document-url
   - reference starts with "LP"
3. Downloads documents to collection/document/{endpoint}.pdf
4. Creates log entries in collection/log/{endpoint}.json
5. Creates resource entries in collection/resource/
6. Tracks progress in a JSON state file for resumability

Usage:
    python bin/download-main-documents.py
    python bin/download-main-documents.py --limit 10
    python bin/download-main-documents.py --authority local-authority:BUC
    python bin/download-main-documents.py --resume

To retry only failed authorities:
    python bin/download-main-documents.py --retry-failed
    python bin/download-main-documents.py --retry-failed --limit 10

To retry only authorities with no main document:
    python bin/download-main-documents.py --retry-no-document
    python bin/download-main-documents.py --retry-no-document --limit 20
    
State file: collection/download_state.json
  - Tracks which authorities have been processed
  - Tracks which documents were successfully downloaded
  - Tracks authorities with no eligible documents
  - Persists progress so the script can be resumed
"""

import argparse
import hashlib
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import cloudscraper
from urllib3.exceptions import InsecureRequestWarning

# Disable SSL warnings
warnings.simplefilter('ignore', InsecureRequestWarning)


class DocumentDownloader:
    def __init__(self, source_dir: str = "source", collection_dir: str = "collection"):
        self.source_dir = Path(source_dir)
        self.collection_dir = Path(collection_dir)
        self.document_dir = self.collection_dir / "document"
        self.log_dir = self.collection_dir / "log"
        self.resource_dir = self.collection_dir / "resource"
        self.endpoint_dir = self.collection_dir / "endpoint"

        # Create directories if they don't exist
        for d in [self.document_dir, self.log_dir, self.resource_dir, self.endpoint_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Create cloudscraper session for Cloudflare bypassing
        self.scraper = cloudscraper.create_scraper()

        # State file for tracking progress
        self.state_file = self.collection_dir / "download_state.json"
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file or create new state."""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            "started": datetime.now(timezone.utc).isoformat(),
            "processed": [],
            "failed": [],
            "no_document": [],
            "downloaded": []
        }

    def _save_state(self):
        """Save state to file."""
        self.state["updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def find_main_document(self, org_data: List[Dict]) -> Optional[Tuple[Dict, Dict]]:
        """
        Find the main document entry from organisation data.

        Returns:
            Tuple of (plan_entry, document_entry) or None if not found
        """
        for plan_entry in org_data:
            # Check if this plan is adopted
            if plan_entry.get("status") != "adopted":
                continue

            # Check if reference starts with "LP"
            reference = plan_entry.get("reference", "")
            if not reference.startswith("LP"):
                continue

            # Find matching document
            documents = plan_entry.get("documents", [])
            for doc in documents:
                doc_type = doc.get("document-type", "")
                if doc_type in ["local-plan-adopted", "local-plan", "core-strategy"]:
                    if doc.get("document-url"):
                        return (plan_entry, doc)

        return None

    def download_document(self, url: str, endpoint: str) -> Tuple[bool, Optional[bytes], Optional[str]]:
        """
        Download a document from URL using cloudscraper to bypass Cloudflare.

        Returns:
            Tuple of (success, content, error_message)
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            response = self.scraper.get(
                url,
                headers=headers,
                timeout=30
            )

            response.raise_for_status()

            # Verify it's a PDF
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                return False, None, f"Not a PDF: {content_type}"

            return True, response.content, None

        except Exception as e:
            # Handle various request errors
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                return False, None, "HTTP error 403"
            elif "404" in error_msg or "Not Found" in error_msg:
                return False, None, "HTTP error 404"
            elif "timeout" in error_msg.lower():
                return False, None, f"Timeout fetching {url}"
            elif "connection" in error_msg.lower():
                return False, None, f"Connection error: {e}"
            else:
                return False, None, f"Error: {type(e).__name__}: {e}"

    def save_document(self, content: bytes, endpoint: str) -> Tuple[bool, Optional[str]]:
        """
        Save document to collection/document/{endpoint}.pdf

        Returns:
            Tuple of (success, error_message)
        """
        try:
            pdf_file = self.document_dir / f"{endpoint}.pdf"
            with open(pdf_file, 'wb') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, f"Failed to save document: {e}"

    def create_log_entry(self, url: str, endpoint: str, content: bytes, status: int = 200) -> Tuple[bool, Optional[str]]:
        """
        Create log entry in collection/log/{endpoint}.json

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Calculate SHA1 of content for resource hash
            content_hash = hashlib.sha1(content).hexdigest()

            log_entry = {
                "resource": content_hash,
                "endpoint-url": url,
                "entry-date": datetime.now(timezone.utc).isoformat(),
                "status": str(status),
                "elapsed": "0.000",
                "content-type": "application/pdf",
                "bytes": str(len(content))
            }

            log_file = self.log_dir / f"{endpoint}.json"
            with open(log_file, 'w') as f:
                json.dump(log_entry, f, indent=2)

            return True, None
        except Exception as e:
            return False, f"Failed to create log: {e}"

    def create_resource_entry(self, content: bytes) -> Tuple[bool, Optional[str]]:
        """
        Create resource entry (binary PDF) in collection/resource/{sha1}

        Returns:
            Tuple of (success, resource_hash or error_message)
        """
        try:
            content_hash = hashlib.sha1(content).hexdigest()
            resource_file = self.resource_dir / content_hash

            # Only save if not already exists
            if not resource_file.exists():
                with open(resource_file, 'wb') as f:
                    f.write(content)

            return True, content_hash
        except Exception as e:
            return False, f"Failed to create resource: {e}"

    def process_authority(self, org_code: str, json_file: Path) -> Dict:
        """
        Process a single authority's JSON file.

        Returns:
            Dictionary with processing result
        """
        result = {
            "org_code": org_code,
            "success": False,
            "reason": None,
            "endpoint": None,
            "url": None
        }

        # Load JSON file
        try:
            with open(json_file, 'r') as f:
                org_data = json.load(f)
        except Exception as e:
            result["reason"] = f"Failed to load JSON: {e}"
            return result

        # Find main document
        document_info = self.find_main_document(org_data)
        if not document_info:
            result["reason"] = "No adopted local plan document found"
            return result

        plan_entry, doc_entry = document_info
        url = doc_entry.get("document-url")
        endpoint = doc_entry.get("endpoint")

        result["url"] = url
        result["endpoint"] = endpoint

        # Check if already processed
        if endpoint in self.state["downloaded"]:
            result["reason"] = "Already downloaded"
            result["success"] = True
            return result

        # Download document
        print(f"  Downloading {org_code}...", end=" ", file=sys.stderr, flush=True)
        success, content, error = self.download_document(url, endpoint)
        if not success:
            result["reason"] = error
            print(f"✗ {error}", file=sys.stderr)
            return result

        # Save document
        success, error = self.save_document(content, endpoint)
        if not success:
            result["reason"] = error
            print(f"✗ {error}", file=sys.stderr)
            return result

        # Create log entry
        success, error = self.create_log_entry(url, endpoint, content)
        if not success:
            result["reason"] = error
            print(f"✗ {error}", file=sys.stderr)
            return result

        # Create resource entry
        success, resource_hash = self.create_resource_entry(content)
        if not success:
            result["reason"] = resource_hash
            print(f"✗ {resource_hash}", file=sys.stderr)
            return result

        result["success"] = True
        print(f"✓ Downloaded ({len(content)} bytes)", file=sys.stderr)
        return result

    def run(self, limit: Optional[int] = None, authority_filter: Optional[str] = None, resume: bool = False,
            retry_failed: bool = False, retry_no_document: bool = False):
        """
        Process all or filtered authority JSON files.

        Args:
            limit: Maximum number of authorities to process
            authority_filter: Process only this specific authority
            resume: Resume from last saved state
            retry_failed: Retry only previously failed authorities
            retry_no_document: Retry only authorities with no main document
        """
        # Find all JSON files in source directory
        json_files = sorted(self.source_dir.glob("*.json"))

        if not json_files:
            print("No JSON files found in source/", file=sys.stderr)
            return

        print(f"Found {len(json_files)} authority files", file=sys.stderr)

        # Filter by retry mode
        if retry_failed:
            failed_codes = set(item["org_code"] for item in self.state.get("failed", []))
            json_files = [f for f in json_files if f.stem in failed_codes]
            print(f"Filtering to {len(json_files)} previously failed authorities", file=sys.stderr)

        if retry_no_document:
            no_doc_codes = set(self.state.get("no_document", []))
            json_files = [f for f in json_files if f.stem in no_doc_codes]
            print(f"Filtering to {len(json_files)} authorities with no main document", file=sys.stderr)

        # Filter if requested
        if authority_filter:
            json_files = [f for f in json_files if authority_filter in f.name]
            print(f"Filtered to {len(json_files)} file(s)", file=sys.stderr)

        # Limit if requested
        if limit:
            json_files = json_files[:limit]
            print(f"Limited to {limit} file(s)", file=sys.stderr)

        # Skip already processed if resuming
        if resume:
            processed = set(self.state.get("processed", []))
            json_files = [f for f in json_files if f.stem not in processed]
            print(f"Resuming: {len(json_files)} remaining", file=sys.stderr)

        # Process each file
        print(f"\nProcessing {len(json_files)} authorities...\n", file=sys.stderr)

        for i, json_file in enumerate(json_files, 1):
            org_code = json_file.stem
            print(f"[{i}/{len(json_files)}] {org_code}", file=sys.stderr, end=" ")

            result = self.process_authority(org_code, json_file)

            # Track result
            self.state["processed"].append(org_code)

            if result["success"]:
                if result.get("reason") != "Already downloaded":
                    self.state["downloaded"].append(result.get("endpoint"))
            elif result.get("reason") == "No adopted local plan document found":
                self.state["no_document"].append(org_code)
                print(f"⚠ No main document", file=sys.stderr)
            else:
                self.state["failed"].append({
                    "org_code": org_code,
                    "reason": result.get("reason"),
                    "url": result.get("url")
                })

            # Save state after each authority
            self._save_state()

        # Final summary
        print(f"\n{'='*80}", file=sys.stderr)
        print("SUMMARY", file=sys.stderr)
        print(f"{'='*80}", file=sys.stderr)
        print(f"Processed: {len(self.state['processed'])}", file=sys.stderr)
        print(f"Downloaded: {len(self.state['downloaded'])}", file=sys.stderr)
        print(f"No document: {len(self.state['no_document'])}", file=sys.stderr)
        print(f"Failed: {len(self.state['failed'])}", file=sys.stderr)

        if self.state["failed"]:
            print(f"\nFailed authorities (see {self.state_file} for details):", file=sys.stderr)
            for item in self.state["failed"][:10]:
                print(f"  - {item['org_code']}: {item['reason'][:80]}", file=sys.stderr)
            if len(self.state["failed"]) > 10:
                print(f"  ... and {len(self.state['failed']) - 10} more", file=sys.stderr)

        if self.state["no_document"]:
            print(f"\nAuthorities without main document:", file=sys.stderr)
            for org_code in self.state["no_document"][:10]:
                print(f"  - {org_code}", file=sys.stderr)
            if len(self.state["no_document"]) > 10:
                print(f"  ... and {len(self.state['no_document']) - 10} more", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Download main local plan documents from all authorities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all authorities
  python bin/download-main-documents.py

  # Process only first 10 authorities
  python bin/download-main-documents.py --limit 10

  # Process a specific authority
  python bin/download-main-documents.py --authority local-authority:BUC

  # Resume from last saved state (skip already processed)
  python bin/download-main-documents.py --resume

  # Retry only previously failed authorities
  python bin/download-main-documents.py --retry-failed

  # Retry only authorities with no main document
  python bin/download-main-documents.py --retry-no-document

  # Retry failed with limit
  python bin/download-main-documents.py --retry-failed --limit 20

State file: collection/download_state.json
  - Tracks which authorities have been processed
  - Tracks which documents were successfully downloaded
  - Tracks authorities with no eligible documents
  - Persists progress so the script can be resumed
"""
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of authorities to process"
    )

    parser.add_argument(
        "--authority",
        help="Process only this specific authority (e.g., local-authority:BUC)"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last saved state (skip already processed)"
    )

    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only previously failed authorities (from state file)"
    )

    parser.add_argument(
        "--retry-no-document",
        action="store_true",
        help="Retry only authorities with no main document (from state file)"
    )

    args = parser.parse_args()

    downloader = DocumentDownloader()
    downloader.run(
        limit=args.limit,
        authority_filter=args.authority,
        resume=args.resume,
        retry_failed=args.retry_failed,
        retry_no_document=args.retry_no_document
    )


if __name__ == "__main__":
    main()
