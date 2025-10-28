#!/usr/bin/env python3

import json
import hashlib
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
import urllib.request
import urllib.error

def calculate_sha1(content):
    """Calculate SHA1 hash of content"""
    return hashlib.sha1(content).hexdigest()

def calculate_sha256(text):
    """Calculate SHA256 hash of text (for endpoint field)"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def download_document(url, endpoint):
    """
    Download document from URL and save to collection with proper logging.

    Args:
        url: The document URL to download
        endpoint: The endpoint hash (SHA256 of URL)

    Returns:
        dict with download metadata or None if failed
    """

    # Skip empty URLs
    if not url or url == "":
        print(f"Skipping empty URL")
        return None

    # Skip non-http URLs (like ArcGIS web maps)
    if not url.startswith('http://') and not url.startswith('https://'):
        print(f"Skipping non-HTTP URL: {url}")
        return None

    # Create collection directories if they don't exist
    Path("collection/resource").mkdir(parents=True, exist_ok=True)
    Path("collection/log").mkdir(parents=True, exist_ok=True)

    # Check if log already exists (already downloaded)
    log_path = Path(f"collection/log/{endpoint}.json")
    if log_path.exists():
        print(f"Already downloaded: {url}")
        with open(log_path, 'r') as f:
            return json.load(f)

    print(f"Downloading: {url}")

    start_time = time.time()

    try:
        # Create request with headers to mimic browser
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        )

        # Download the file
        with urllib.request.urlopen(req, timeout=60) as response:
            content = response.read()
            status = response.status
            content_type = response.headers.get('Content-Type', '')
            content_length = len(content)

        elapsed = time.time() - start_time

        # Calculate SHA1 hash for filename
        resource_hash = calculate_sha1(content)

        # Save to collection/resource/
        resource_path = Path(f"collection/resource/{resource_hash}")
        with open(resource_path, 'wb') as f:
            f.write(content)

        # Create log entry
        log_entry = {
            "resource": resource_hash,
            "endpoint-url": url,
            "entry-date": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "status": str(status),
            "elapsed": f"{elapsed:.3f}",
            "content-type": content_type,
            "bytes": str(content_length)
        }

        # Save log file
        with open(log_path, 'w') as f:
            json.dump(log_entry, f, indent=2)

        print(f"  ✓ Downloaded {content_length} bytes -> {resource_hash}")

        return log_entry

    except urllib.error.HTTPError as e:
        elapsed = time.time() - start_time
        print(f"  ✗ HTTP Error {e.code}: {url}")

        # Still create a log entry for failed downloads
        log_entry = {
            "resource": "",
            "endpoint-url": url,
            "entry-date": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "status": str(e.code),
            "elapsed": f"{elapsed:.3f}",
            "content-type": "",
            "bytes": "0"
        }

        with open(log_path, 'w') as f:
            json.dump(log_entry, f, indent=2)

        return log_entry

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"  ✗ Error: {str(e)}")

        # Create log entry for other errors
        log_entry = {
            "resource": "",
            "endpoint-url": url,
            "entry-date": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "status": "error",
            "elapsed": f"{elapsed:.3f}",
            "content-type": "",
            "bytes": "0"
        }

        with open(log_path, 'w') as f:
            json.dump(log_entry, f, indent=2)

        return log_entry

def process_source_files():
    """Process all source JSON files and download documents"""

    source_dir = Path("source")
    if not source_dir.exists():
        print("Error: source directory not found")
        return

    # Find all local-authority JSON files
    source_files = list(source_dir.glob("local-authority:*.json"))

    if not source_files:
        print("No source files found")
        return

    print(f"Found {len(source_files)} source files")

    total_documents = 0
    downloaded = 0
    skipped = 0
    failed = 0

    for source_file in sorted(source_files):
        print(f"\nProcessing {source_file.name}...")

        with open(source_file, 'r') as f:
            plans = json.load(f)

        # Process each plan
        for plan in plans:
            documents = plan.get('documents', [])

            for doc in documents:
                doc_url = doc.get('document-url', '')

                if not doc_url:
                    continue

                total_documents += 1

                # Calculate endpoint if not present
                endpoint = doc.get('endpoint')
                if not endpoint:
                    endpoint = calculate_sha256(doc_url)
                    doc['endpoint'] = endpoint

                # Download document
                result = download_document(doc_url, endpoint)

                if result:
                    if result.get('resource'):
                        downloaded += 1
                    elif result.get('status') == 'error' or int(result.get('status', '0')) >= 400:
                        failed += 1
                else:
                    skipped += 1

                # Small delay to be nice to servers
                time.sleep(0.5)

        # Save updated source file with endpoint fields
        with open(source_file, 'w') as f:
            json.dump(plans, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Total documents: {total_documents}")
    print(f"  Downloaded: {downloaded}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed: {failed}")
    print(f"{'='*60}")

if __name__ == "__main__":
    process_source_files()
