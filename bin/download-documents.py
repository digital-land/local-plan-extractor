#!/usr/bin/env python3
"""
Download local plan documents from all local planning authority JSON files.

By default, downloads only the main adopted local plan document per authority:
  - status: "adopted"
  - reference starts with "LP"
  - document-type: "local-plan-adopted" or "local-plan"

Use --all to download every document URL found across all plans.

Usage:
    python bin/download-documents.py
    python bin/download-documents.py --all
    python bin/download-documents.py --limit 10
    python bin/download-documents.py --authority local-authority:BUC
    python bin/download-documents.py --resume

To retry only failed authorities:
    python bin/download-documents.py --retry-failed
    python bin/download-documents.py --retry-failed --limit 10

To retry only authorities with no document:
    python bin/download-documents.py --retry-no-document

State file: collection/download_state.json
  - Tracks which authorities have been processed
  - Tracks which documents were successfully downloaded
  - Tracks authorities with no eligible documents
  - Persists progress so the script can be resumed
"""

import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cloudscraper
from urllib3.exceptions import InsecureRequestWarning

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import detect_file_suffix

warnings.simplefilter('ignore', InsecureRequestWarning)


class DocumentDownloader:
    def __init__(self, source_dir: str = "source", collection_dir: str = "collection"):
        self.source_dir = Path(source_dir)
        self.collection_dir = Path(collection_dir)
        self.document_dir = self.collection_dir / "document"
        self.log_dir = self.collection_dir / "log"
        self.resource_dir = self.collection_dir / "resource"
        self.endpoint_dir = self.collection_dir / "endpoint"

        for d in [self.document_dir, self.log_dir, self.resource_dir, self.endpoint_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.scraper = cloudscraper.create_scraper()

        self.state_file = self.collection_dir / "download_state.json"
        self.state = self._load_state()

    def _load_state(self) -> Dict:
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
        self.state["updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def find_main_document(self, org_data: List[Dict]) -> Optional[Tuple[Dict, Dict]]:
        """Find the single main adopted local plan document for an authority."""
        for plan_entry in org_data:
            if plan_entry.get("status") != "adopted":
                continue
            if not plan_entry.get("reference", "").startswith("LP"):
                continue
            for doc in plan_entry.get("documents", []):
                if doc.get("document-type") in ["local-plan-adopted", "local-plan"]:
                    if doc.get("document-url"):
                        return (plan_entry, doc)
        return None

    def find_all_documents(self, org_data: List[Dict]) -> List[Tuple[Dict, Dict]]:
        """Find every document URL across all plans for an authority."""
        docs = []
        for plan_entry in org_data:
            for doc in plan_entry.get("documents", []):
                if doc.get("document-url") and doc.get("endpoint"):
                    docs.append((plan_entry, doc))
        return docs

    def download_document(self, url: str, require_pdf: bool = True) -> Tuple[bool, Optional[bytes], Optional[str], Optional[str]]:
        """
        Download a document from URL using cloudscraper to bypass Cloudflare.

        Returns:
            Tuple of (success, content, content_type, error_message)
        """
        try:
            response = self.scraper.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=30
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()

            if require_pdf and "pdf" not in content_type and not url.lower().endswith(".pdf"):
                return False, None, None, f"Not a PDF: {content_type}"

            return True, response.content, content_type, None

        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                return False, None, None, "HTTP error 403"
            elif "404" in error_msg or "Not Found" in error_msg:
                return False, None, None, "HTTP error 404"
            elif "timeout" in error_msg.lower():
                return False, None, None, f"Timeout fetching {url}"
            elif "connection" in error_msg.lower():
                return False, None, None, f"Connection error: {e}"
            else:
                return False, None, None, f"Error: {type(e).__name__}: {e}"

    def save_document(self, content: bytes, endpoint: str, content_type: str = "", url: str = "") -> Tuple[bool, Optional[str]]:
        """Save document to collection/document/{endpoint}.{suffix}"""
        try:
            suffix = detect_file_suffix(content, content_type, url)
            file_path = self.document_dir / f"{endpoint}.{suffix}"
            with open(file_path, 'wb') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, f"Failed to save document: {e}"

    def create_log_entry(self, url: str, endpoint: str, content: bytes, content_type: str = "") -> Tuple[bool, Optional[str]]:
        """Create log entry in collection/log/{endpoint}.json"""
        try:
            content_hash = hashlib.sha1(content).hexdigest()
            log_entry = {
                "resource": content_hash,
                "endpoint-url": url,
                "entry-date": datetime.now(timezone.utc).isoformat(),
                "status": "200",
                "elapsed": "0.000",
                "content-type": content_type or "application/pdf",
                "bytes": str(len(content))
            }
            with open(self.log_dir / f"{endpoint}.json", 'w') as f:
                json.dump(log_entry, f, indent=2)
            return True, None
        except Exception as e:
            return False, f"Failed to create log: {e}"

    def create_resource_entry(self, content: bytes) -> Tuple[bool, Optional[str]]:
        """Create resource entry in collection/resource/{sha1}"""
        try:
            content_hash = hashlib.sha1(content).hexdigest()
            resource_file = self.resource_dir / content_hash
            if not resource_file.exists():
                with open(resource_file, 'wb') as f:
                    f.write(content)
            return True, content_hash
        except Exception as e:
            return False, f"Failed to create resource: {e}"

    def _download_single(self, url: str, endpoint: str, require_pdf: bool = True) -> Dict:
        """Download one document and write all collection artefacts. Returns a result dict."""
        result = {"success": False, "reason": None, "endpoint": endpoint, "url": url}

        if endpoint in self.state["downloaded"]:
            result["success"] = True
            result["reason"] = "Already downloaded"
            return result

        print(f"  Downloading {url[:80]}...", end=" ", file=sys.stderr, flush=True)
        success, content, content_type, error = self.download_document(url, require_pdf=require_pdf)
        if not success:
            result["reason"] = error
            print(f"✗ {error}", file=sys.stderr)
            return result

        for step, args in [
            (self.save_document, (content, endpoint, content_type, url)),
            (self.create_log_entry, (url, endpoint, content, content_type)),
            (self.create_resource_entry, (content,)),
        ]:
            ok, err = step(*args)
            if not ok:
                result["reason"] = err
                print(f"✗ {err}", file=sys.stderr)
                return result

        self.state["downloaded"].append(endpoint)
        result["success"] = True
        print(f"✓ {len(content)} bytes", file=sys.stderr)
        return result

    def process_authority(self, org_code: str, json_file: Path, download_all: bool = False) -> Dict:
        """
        Process a single authority's JSON file.

        Returns a result dict summarising what happened.
        """
        result = {
            "org_code": org_code,
            "success": False,
            "reason": None,
            "endpoint": None,
            "url": None,
            "downloaded_count": 0,
            "failed_count": 0,
        }

        try:
            with open(json_file, 'r') as f:
                org_data = json.load(f)
        except Exception as e:
            result["reason"] = f"Failed to load JSON: {e}"
            return result

        if download_all:
            documents = self.find_all_documents(org_data)
            if not documents:
                result["reason"] = "No documents found"
                return result

            for _, doc_entry in documents:
                url = doc_entry.get("document-url")
                endpoint = doc_entry.get("endpoint")
                doc_result = self._download_single(url, endpoint, require_pdf=False)
                if doc_result["success"] and doc_result["reason"] != "Already downloaded":
                    result["downloaded_count"] += 1
                elif not doc_result["success"]:
                    result["failed_count"] += 1

            result["success"] = result["downloaded_count"] > 0 or any(
                e in self.state["downloaded"]
                for _, d in documents
                if (e := d.get("endpoint"))
            )
            if not result["success"]:
                result["reason"] = f"{result['failed_count']} download(s) failed"
        else:
            document_info = self.find_main_document(org_data)
            if not document_info:
                result["reason"] = "No adopted local plan document found"
                return result

            _, doc_entry = document_info
            url = doc_entry.get("document-url")
            endpoint = doc_entry.get("endpoint")
            result["url"] = url
            result["endpoint"] = endpoint

            print(f"  {org_code}...", end=" ", file=sys.stderr, flush=True)
            doc_result = self._download_single(url, endpoint, require_pdf=True)
            result["success"] = doc_result["success"]
            result["reason"] = doc_result["reason"]
            if doc_result["success"] and doc_result["reason"] != "Already downloaded":
                result["downloaded_count"] = 1

        return result

    def run(
        self,
        download_all: bool = False,
        limit: Optional[int] = None,
        authority_filter: Optional[str] = None,
        resume: bool = False,
        retry_failed: bool = False,
        retry_no_document: bool = False,
    ):
        json_files = sorted(self.source_dir.glob("*.json"))
        if not json_files:
            print("No JSON files found in source/", file=sys.stderr)
            return

        print(f"Found {len(json_files)} authority files", file=sys.stderr)
        mode = "all documents" if download_all else "main adopted plan only"
        print(f"Mode: {mode}", file=sys.stderr)

        if retry_failed:
            failed_codes = set(item["org_code"] for item in self.state.get("failed", []))
            json_files = [f for f in json_files if f.stem in failed_codes]
            print(f"Filtering to {len(json_files)} previously failed authorities", file=sys.stderr)

        if retry_no_document:
            no_doc_codes = set(self.state.get("no_document", []))
            json_files = [f for f in json_files if f.stem in no_doc_codes]
            print(f"Filtering to {len(json_files)} authorities with no document", file=sys.stderr)

        if authority_filter:
            json_files = [f for f in json_files if authority_filter in f.name]
            print(f"Filtered to {len(json_files)} file(s)", file=sys.stderr)

        if limit:
            json_files = json_files[:limit]
            print(f"Limited to {limit} file(s)", file=sys.stderr)

        if resume:
            processed = set(self.state.get("processed", []))
            json_files = [f for f in json_files if f.stem not in processed]
            print(f"Resuming: {len(json_files)} remaining", file=sys.stderr)

        print(f"\nProcessing {len(json_files)} authorities...\n", file=sys.stderr)

        for i, json_file in enumerate(json_files, 1):
            org_code = json_file.stem
            print(f"[{i}/{len(json_files)}] {org_code}", file=sys.stderr)

            result = self.process_authority(org_code, json_file, download_all=download_all)
            self.state["processed"].append(org_code)

            if result["success"]:
                pass
            elif result.get("reason") in ("No adopted local plan document found", "No documents found"):
                self.state["no_document"].append(org_code)
                print(f"  ⚠ {result['reason']}", file=sys.stderr)
            else:
                self.state["failed"].append({
                    "org_code": org_code,
                    "reason": result.get("reason"),
                    "url": result.get("url")
                })

            self._save_state()

        print(f"\n{'='*80}", file=sys.stderr)
        print("SUMMARY", file=sys.stderr)
        print(f"{'='*80}", file=sys.stderr)
        print(f"Processed:    {len(self.state['processed'])}", file=sys.stderr)
        print(f"Downloaded:   {len(self.state['downloaded'])}", file=sys.stderr)
        print(f"No document:  {len(self.state['no_document'])}", file=sys.stderr)
        print(f"Failed:       {len(self.state['failed'])}", file=sys.stderr)

        if self.state["failed"]:
            print(f"\nFailed authorities:", file=sys.stderr)
            for item in self.state["failed"][:10]:
                print(f"  - {item['org_code']}: {item['reason'][:80]}", file=sys.stderr)
            if len(self.state["failed"]) > 10:
                print(f"  ... and {len(self.state['failed']) - 10} more", file=sys.stderr)

        if self.state["no_document"]:
            print(f"\nAuthorities without document:", file=sys.stderr)
            for org_code in self.state["no_document"][:10]:
                print(f"  - {org_code}", file=sys.stderr)
            if len(self.state["no_document"]) > 10:
                print(f"  ... and {len(self.state['no_document']) - 10} more", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Download local plan documents for all authorities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download main adopted plan per authority (default)
  python bin/download-documents.py

  # Download all documents across all plans
  python bin/download-documents.py --all

  # Process only first 10 authorities
  python bin/download-documents.py --limit 10

  # Process a specific authority
  python bin/download-documents.py --authority local-authority:BUC

  # Resume from last saved state
  python bin/download-documents.py --resume

  # Retry only previously failed authorities
  python bin/download-documents.py --retry-failed

  # Retry only authorities with no document found
  python bin/download-documents.py --retry-no-document
"""
    )

    parser.add_argument(
        "--all",
        action="store_true",
        dest="download_all",
        help="Download all document URLs, not just the main adopted plan"
    )
    parser.add_argument("--limit", type=int, help="Maximum number of authorities to process")
    parser.add_argument("--authority", help="Process only this specific authority (e.g., local-authority:BUC)")
    parser.add_argument("--resume", action="store_true", help="Resume from last saved state")
    parser.add_argument("--retry-failed", action="store_true", help="Retry only previously failed authorities")
    parser.add_argument("--retry-no-document", action="store_true", help="Retry only authorities with no document found")

    args = parser.parse_args()

    downloader = DocumentDownloader()
    downloader.run(
        download_all=args.download_all,
        limit=args.limit,
        authority_filter=args.authority,
        resume=args.resume,
        retry_failed=args.retry_failed,
        retry_no_document=args.retry_no_document,
    )


if __name__ == "__main__":
    main()
