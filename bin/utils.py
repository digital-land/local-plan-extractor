#!/usr/bin/env python3
"""
Shared utility functions for local plan scripts.
"""

import hashlib
import mimetypes
import os
import sys
from pathlib import Path


def calculate_sha1(content: bytes) -> str:
    """Calculate SHA1 hash of content bytes."""
    return hashlib.sha1(content).hexdigest()


def calculate_sha256(text: str) -> str:
    """Calculate SHA256 hash of a URL or text string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_file_suffix(content: bytes, content_type: str, url: str) -> str:
    """
    Detect file suffix from content, content-type header, or URL.

    Args:
        content: File content bytes
        content_type: HTTP Content-Type header
        url: Source URL

    Returns:
        File suffix (e.g., 'pdf', 'docx', 'html')
    """
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
            if ext in ["pdf", "doc", "docx", "xls", "xlsx", "html", "txt", "zip", "jpg", "png"]:
                return ext

    return "bin"


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug (e.g. 'Amber Valley' -> 'amber-valley')."""
    if not text:
        return ''
    slug = str(text).lower().strip()
    if slug == 'nan':
        return ''
    slug = slug.replace('&', 'and')
    slug = slug.replace('\u2013', '-')  # en dash
    slug = slug.replace('\u2014', '-')  # em dash
    slug = slug.replace('/', '-')
    slug = slug.replace(' ', '-')
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    slug = slug.strip('-')
    return slug


def create_endpoint_hardlink(
    endpoint: str,
    resource_hash: str,
    content: bytes,
    content_type: str,
    url: str,
    target_dir: str = "collection/document",
) -> None:
    """
    Create a hard link from target_dir/{endpoint}.{suffix} to collection/resource/{resource_hash}.

    Args:
        endpoint: Endpoint hash (SHA256 of URL)
        resource_hash: Resource hash (SHA1 of content)
        content: File content bytes (for suffix detection)
        content_type: HTTP Content-Type header
        url: Source URL
        target_dir: Directory to create the hardlink in (default: collection/document)
    """
    dir_path = Path(target_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    suffix = detect_file_suffix(content, content_type, url)
    hardlink_path = dir_path / f"{endpoint}.{suffix}"
    resource_path = Path("collection/resource") / resource_hash

    if hardlink_path.exists():
        hardlink_path.unlink()

    os.link(resource_path, hardlink_path)
    print(
        f"  → Created hardlink: {target_dir}/{endpoint}.{suffix} => resource/{resource_hash}",
        file=sys.stderr,
    )
