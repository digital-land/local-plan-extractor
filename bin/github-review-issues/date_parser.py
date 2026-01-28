#!/usr/bin/env python3
"""
Robust date parsing for multiple formats found in GitHub issue change descriptions.

Supports:
- DD/MM/YYYY → YYYY-MM-DD
- DD Month YYYY → YYYY-MM-DD (e.g., "11 December 2017")
- DD.MM.YYYY → YYYY-MM-DD
- Month DD, YYYY → YYYY-MM-DD (e.g., "December 11, 2017")
"""

import re
from datetime import datetime
from typing import Optional

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def parse_date(date_string: str) -> Optional[str]:
    """
    Parse various date formats and return ISO format YYYY-MM-DD.

    Returns:
        Date in YYYY-MM-DD format, or None if parsing fails.
    """
    if not date_string or not isinstance(date_string, str):
        return None

    date_string = date_string.strip()

    # Try DD/MM/YYYY
    match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_string)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    # Try DD.MM.YYYY
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', date_string)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    # Try DD Month YYYY or DD Month, YYYY (e.g., "11 December 2017" or "11 December, 2017")
    match = re.match(r'^(\d{1,2})\s+([a-zA-Z]+)\s*,?\s*(\d{4})$', date_string)
    if match:
        day, month_str, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        month = MONTH_MAP.get(month_str)
        if month and 1 <= day <= 31:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    # Try Month DD, YYYY or Month DD YYYY (e.g., "December 11, 2017")
    match = re.match(r'^([a-zA-Z]+)\s+(\d{1,2}),?\s*(\d{4})$', date_string)
    if match:
        month_str, day, year = match.group(1).lower(), int(match.group(2)), int(match.group(3))
        month = MONTH_MAP.get(month_str)
        if month and 1 <= day <= 31:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    # Try YYYY-MM-DD (already in correct format)
    match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', date_string)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                pass

    return None


def extract_dates_from_text(text: str) -> list[str]:
    """
    Find all date-like patterns in text and return parsed dates.

    Returns:
        List of dates in YYYY-MM-DD format found in the text.
    """
    if not text:
        return []

    dates = []

    # Find patterns like "adopted 29/07/2020" or "date 11 December 2017"
    patterns = [
        r'(\d{1,2}/\d{1,2}/\d{4})',           # DD/MM/YYYY
        r'(\d{1,2}\.\d{1,2}\.\d{4})',         # DD.MM.YYYY
        r'(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})', # DD Month YYYY
        r'([a-zA-Z]+)\s+(\d{1,2})[,]?\s+(\d{4})', # Month DD YYYY
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            date_str = match.group(0)
            parsed = parse_date(date_str)
            if parsed and parsed not in dates:
                dates.append(parsed)

    return dates
