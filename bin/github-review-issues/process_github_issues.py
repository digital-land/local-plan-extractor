#!/usr/bin/env python3
"""
Process 'needs-editing' GitHub issues and apply corrections to local plan JSON files.

This script:
1. Fetches open issues labeled 'needs-editing' from GitHub
2. Parses the "What needs to be changed?" section
3. Extracts structured corrections (adoption dates, titles, housing numbers)
4. Updates the corresponding JSON files in both local-plan/ and source/
5. Optionally comments on and closes issues
6. Tracks processing state to avoid duplicate processing

Usage:
    python bin/github-review-issues/process_github_issues.py                              # Process all unprocessed issues
    python bin/github-review-issues/process_github_issues.py --limit 5                    # Process first 5 issues
    python bin/github-review-issues/process_github_issues.py --dry-run                    # Show changes without applying
    python bin/github-review-issues/process_github_issues.py --issue-number 357           # Process specific issue
    python bin/github-review-issues/process_github_issues.py --regenerate-csvs            # Run generate-csvs.py after
    python bin/github-review-issues/process_github_issues.py --comment                    # Add comment to issues
    python bin/github-review-issues/process_github_issues.py --close                      # Close issues
    python bin/github-review-issues/process_github_issues.py --comment --close --limit 5  # Comment + close first 5

Environment:
    GITHUB_TOKEN: GitHub personal access token for authenticated API requests (optional)
"""

import json
import urllib.request
import urllib.error
import argparse
import sys
import re
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

# Import date parser from same directory
from date_parser import parse_date

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class GitHubIssueProcessor:
    """Process GitHub issues and apply corrections to local plan data."""

    GITHUB_API_BASE = "https://api.github.com/repos/digital-land/local-plan-extractor"
    STATE_FILE = Path("collection/issue_processing_state.json")
    LOCAL_PLAN_DIR = Path("local-plan")
    SOURCE_DIR = Path("source")

    def __init__(self, dry_run: bool = False, regenerate_csvs: bool = False, comment_issues: bool = False, close_issues: bool = False):
        """Initialize the processor."""
        self.dry_run = dry_run
        self.regenerate_csvs = regenerate_csvs
        self.comment_issues = comment_issues
        self.close_issues = close_issues
        self.github_token = os.environ.get('GITHUB_TOKEN')
        self.state = self._load_state()
        self.processed_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.changes_log = []

    def _load_state(self) -> Dict[str, Any]:
        """Load processing state from file."""
        if self.STATE_FILE.exists():
            try:
                with open(self.STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state file: {e}")
                return {"processed_issues": {}, "skipped_issues": {}, "failed_issues": {}}
        return {"processed_issues": {}, "skipped_issues": {}, "failed_issues": {}}

    def _save_state(self) -> None:
        """Save processing state to file."""
        if not self.dry_run:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(self.STATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.state, f, indent=2, ensure_ascii=False)
                    f.write('\n')
            except Exception as e:
                logger.error(f"Failed to save state file: {e}")

    def _comment_on_issue(self, issue_number: int, changes: List[str]) -> Tuple[bool, Optional[str]]:
        """Add a comment to the GitHub issue. Returns (success, error_message)."""
        if not self.comment_issues:
            return True, None

        try:
            # Build comment message
            changes_text = "\n".join([f"- {change}" for change in changes])
            comment_body = f"""Changes applied by automated processor:

{changes_text}

This issue has been automatically processed and updated."""

            # Prepare GitHub API request
            url = f"{self.GITHUB_API_BASE}/issues/{issue_number}/comments"
            data = json.dumps({"body": comment_body}).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('User-Agent', 'local-plan-extractor')

            # Add authentication if token available
            if self.github_token:
                # Support both classic (token prefix) and fine-grained (Bearer prefix) tokens
                if self.github_token.startswith('github_pat_'):
                    # Fine-grained token
                    req.add_header('Authorization', f'Bearer {self.github_token}')
                else:
                    # Classic token
                    req.add_header('Authorization', f'token {self.github_token}')

            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status in [200, 201]:
                        return True, None
                    else:
                        return False, f"HTTP {response.status}"
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return False, "Unauthorized (missing/invalid GITHUB_TOKEN)"
                elif e.code == 403:
                    return False, "Forbidden (rate limited or insufficient permissions)"
                else:
                    return False, f"HTTP {e.code}: {e.reason}"
            except urllib.error.URLError as e:
                return False, f"Network error: {e.reason}"

        except Exception as e:
            return False, str(e)

    def _close_issue(self, issue_number: int) -> Tuple[bool, Optional[str]]:
        """Close a GitHub issue. Returns (success, error_message)."""
        if not self.close_issues:
            return True, None

        try:
            # Prepare GitHub API request
            url = f"{self.GITHUB_API_BASE}/issues/{issue_number}"
            data = json.dumps({"state": "closed"}).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='PATCH')
            req.add_header('Content-Type', 'application/json')
            req.add_header('User-Agent', 'local-plan-extractor')

            # Add authentication if token available
            if self.github_token:
                # Support both classic (token prefix) and fine-grained (Bearer prefix) tokens
                if self.github_token.startswith('github_pat_'):
                    # Fine-grained token
                    req.add_header('Authorization', f'Bearer {self.github_token}')
                else:
                    # Classic token
                    req.add_header('Authorization', f'token {self.github_token}')

            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status in [200, 201]:
                        return True, None
                    else:
                        return False, f"HTTP {response.status}"
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return False, "Unauthorized (missing/invalid GITHUB_TOKEN)"
                elif e.code == 403:
                    return False, "Forbidden (rate limited or insufficient permissions)"
                else:
                    return False, f"HTTP {e.code}: {e.reason}"
            except urllib.error.URLError as e:
                return False, f"Network error: {e.reason}"

        except Exception as e:
            return False, str(e)

    def _fetch_issues(self, issue_number: Optional[int] = None, limit: Optional[int] = None) -> List[Dict]:
        """Fetch issues from GitHub API."""
        issues = []
        page = 1

        try:
            while True:
                url = f"{self.GITHUB_API_BASE}/issues?state=open&labels=needs-editing&per_page=30&page={page}"
                logger.info(f"Fetching issues from GitHub (page {page})...")

                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'local-plan-extractor')

                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        page_issues = json.loads(response.read().decode('utf-8'))
                        if not page_issues:
                            break

                        for issue in page_issues:
                            # Filter by issue number if specified
                            if issue_number and issue['number'] != issue_number:
                                continue

                            # Skip already processed issues
                            if str(issue['number']) in self.state['processed_issues']:
                                continue

                            issues.append(issue)

                            if limit and len(issues) >= limit:
                                return issues

                        page += 1

                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        logger.error("Repository not found")
                    else:
                        logger.error(f"HTTP error {e.code}: {e.reason}")
                    break
                except urllib.error.URLError as e:
                    logger.error(f"Failed to fetch issues: {e.reason}")
                    break

        except Exception as e:
            logger.error(f"Unexpected error fetching issues: {e}")

        return issues

    def _extract_file_reference(self, issue_body: str) -> Optional[str]:
        """Extract JSON file reference from issue body."""
        # Look for "File: <hash>.json" pattern
        match = re.search(r'\*\*File\*\*:\s*([a-f0-9]{64})\.json', issue_body)
        if match:
            return f"{match.group(1)}.json"

        # Also try without ** markup
        match = re.search(r'File:\s*([a-f0-9]{64})\.json', issue_body)
        if match:
            return f"{match.group(1)}.json"

        return None

    def _extract_changes_section(self, issue_body: str) -> str:
        """Extract the 'What needs to be changed?' section."""
        if '## What needs to be changed?' not in issue_body:
            return ""

        parts = issue_body.split('## What needs to be changed?')
        if len(parts) < 2:
            return ""

        # Get text until next section or end
        text = parts[1].split('##')[0].strip()
        return text

    def _parse_changes(self, changes_text: str) -> Dict[str, Any]:
        """Parse changes from text and extract structured data."""
        changes = {}

        if not changes_text:
            return changes

        # Skip certain patterns that indicate manual review needed
        if re.search(r'(already been done|referenced in|see issue|#\d+)', changes_text, re.IGNORECASE):
            return {"_skip": "references other issue"}

        if re.search(r'(should be deleted|not an adopted|should be removed)', changes_text, re.IGNORECASE):
            return {"_flag": "requires manual deletion review"}

        # Extract adoption date
        # Patterns: "plan adopted DD/MM/YYYY" or "Adoption date: DD Month YYYY"
        date_match = re.search(
            r'(?:plan adopted|adoption date)[:\s]+([^\n]*)',
            changes_text,
            re.IGNORECASE
        )
        if date_match:
            date_text = date_match.group(1).strip()
            # Remove trailing brackets or notes
            date_text = re.sub(r'\s*\[.*?\].*$', '', date_text)
            parsed_date = parse_date(date_text.split('[')[0].strip())
            if parsed_date:
                changes['adoption-date'] = parsed_date

        # Extract plan period dates (start year and end year)
        # Pattern: "Plan period: 2013 - 2033" or "2020 to 2036"
        period_match = re.search(r'(?:plan period|plan period dates)[:\s]*(\d{4})\s*(?:-|to)\s*(\d{4})', changes_text, re.IGNORECASE)
        if period_match:
            start_year = int(period_match.group(1))
            end_year = int(period_match.group(2))
            changes['period-start-date'] = start_year
            changes['period-end-date'] = end_year

        # Extract plan title
        # Pattern: "Local Plan title: <title>"
        title_match = re.search(r'Local Plan title:\s*([^\n]+)', changes_text, re.IGNORECASE)
        if title_match:
            changes['name'] = title_match.group(1).strip()

        # Extract housing requirement
        # Patterns: "Housing requirement: 6,890 homes" or "Housing requirement: 315 dwellings (annual)"
        housing_match = re.search(
            r'Housing requirement[:\s]+([0-9,]+)\s*(?:homes|dwellings)',
            changes_text,
            re.IGNORECASE
        )
        if housing_match:
            housing_str = housing_match.group(1).replace(',', '')
            try:
                housing_num = int(housing_str)
                # Check if it's annual or total
                if 'annual' in changes_text.lower():
                    changes['annual-required-housing'] = housing_num
                else:
                    # Default to required-housing for total
                    changes['required-housing'] = housing_num
            except ValueError:
                pass

        return changes

    def _validate_json(self, data: Dict[str, Any], json_path: Path) -> Tuple[bool, Optional[str]]:
        """Validate that JSON has required fields."""
        errors = []

        if not data.get("name"):
            errors.append("Missing 'name'")

        if not data.get("organisation-name"):
            errors.append("Missing 'organisation-name'")

        if data.get("period-start-date") is None and data.get("period-start-date") != "":
            errors.append("Missing 'period-start-date'")

        if data.get("period-end-date") is None and data.get("period-end-date") != "":
            errors.append("Missing 'period-end-date'")

        housing_numbers = data.get("housing-numbers", [])
        if not isinstance(housing_numbers, list) or len(housing_numbers) == 0:
            errors.append("Missing or empty 'housing-numbers' array")

        if errors:
            return False, "; ".join(errors)

        return True, None

    def _update_source_file(self, local_plan_data: Dict[str, Any], endpoint: str, changes: Dict[str, Any]) -> Tuple[bool, List[str], Optional[str]]:
        """Update corresponding source/ file with changes. Returns (success, changes_applied, error_message)."""
        applied = []
        error = None

        try:
            # Get organisation code from local plan data
            organisation = local_plan_data.get('organisation')
            if not organisation:
                return False, [], "No organisation found in local-plan JSON"

            # Find source file
            source_file = self.SOURCE_DIR / f"{organisation}.json"
            if not source_file.exists():
                return False, [], f"Source file not found: {source_file}"

            # Read source file
            with open(source_file, 'r', encoding='utf-8') as f:
                source_data = json.load(f)

            if not isinstance(source_data, list):
                return False, [], f"Invalid source file format: expected array"

            # Find the plan that contains this endpoint
            plan_found = False
            matching_document = None
            for plan in source_data:
                documents = plan.get('documents', [])
                for doc in documents:
                    if doc.get('endpoint') == endpoint:
                        plan_found = True
                        matching_document = doc
                        original_plan = json.loads(json.dumps(plan))

                        # Apply changes to the plan
                        if 'adoption-date' in changes:
                            plan['adoption-date'] = changes['adoption-date']
                            applied.append(f"source adoption-date: {original_plan.get('adoption-date')} → {changes['adoption-date']}")

                        if 'name' in changes:
                            plan['name'] = changes['name']
                            applied.append(f"source name: {original_plan.get('name')} → {changes['name']}")

                            # Also update the matching document's name
                            if matching_document:
                                original_doc_name = matching_document.get('name')
                                matching_document['name'] = changes['name']
                                applied.append(f"source document name: {original_doc_name} → {changes['name']}")

                        if 'period-start-date' in changes:
                            plan['period-start-date'] = changes['period-start-date']
                            applied.append(f"source period-start-date: {original_plan.get('period-start-date')} → {changes['period-start-date']}")

                        if 'period-end-date' in changes:
                            plan['period-end-date'] = changes['period-end-date']
                            applied.append(f"source period-end-date: {original_plan.get('period-end-date')} → {changes['period-end-date']}")

                        break

                if plan_found:
                    break

            if not plan_found:
                return False, [], f"Endpoint {endpoint} not found in {source_file}"

            # Write back source file
            if not self.dry_run and applied:
                with open(source_file, 'w', encoding='utf-8') as f:
                    json.dump(source_data, f, indent=2, ensure_ascii=False)
                    f.write('\n')

            return True, applied, None

        except FileNotFoundError as e:
            return False, [], f"Source file not found: {e}"
        except json.JSONDecodeError as e:
            return False, [], f"Invalid JSON in source file: {e}"
        except Exception as e:
            return False, [], f"Error updating source file: {e}"

    def _update_json_file(self, json_path: Path, changes: Dict[str, Any]) -> Tuple[bool, List[str], Optional[str]]:
        """Update JSON file with changes. Returns (success, changes_applied, error_message)."""
        applied = []
        error = None

        try:
            # Read original
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            original_data = json.loads(json.dumps(data))

            # Apply changes
            if 'adoption-date' in changes:
                data['adoption-date'] = changes['adoption-date']
                applied.append(f"adoption-date: {original_data.get('adoption-date')} → {changes['adoption-date']}")

            if 'name' in changes:
                data['name'] = changes['name']
                applied.append(f"name: {original_data.get('name')} → {changes['name']}")

            if 'period-start-date' in changes:
                data['period-start-date'] = changes['period-start-date']
                applied.append(f"period-start-date: {original_data.get('period-start-date')} → {changes['period-start-date']}")

            if 'period-end-date' in changes:
                data['period-end-date'] = changes['period-end-date']
                applied.append(f"period-end-date: {original_data.get('period-end-date')} → {changes['period-end-date']}")

            if 'required-housing' in changes:
                if data.get('housing-numbers') and len(data['housing-numbers']) > 0:
                    # For single-entry housing numbers, update the first entry
                    # For multi-entry (joint plans), update all entries
                    for i, housing_entry in enumerate(data['housing-numbers']):
                        original_value = original_data['housing-numbers'][i].get('required-housing') if i < len(original_data['housing-numbers']) else ''
                        data['housing-numbers'][i]['required-housing'] = changes['required-housing']
                        if i == 0:
                            applied.append(f"required-housing: {original_value} → {changes['required-housing']}")

            if 'annual-required-housing' in changes:
                if data.get('housing-numbers') and len(data['housing-numbers']) > 0:
                    # For single-entry housing numbers, update the first entry
                    # For multi-entry (joint plans), update all entries
                    for i, housing_entry in enumerate(data['housing-numbers']):
                        original_value = original_data['housing-numbers'][i].get('annual-required-housing') if i < len(original_data['housing-numbers']) else ''
                        data['housing-numbers'][i]['annual-required-housing'] = changes['annual-required-housing']
                        if i == 0:
                            applied.append(f"annual-required-housing: {original_value} → {changes['annual-required-housing']}")

            # Validate
            is_valid, validation_error = self._validate_json(data, json_path)
            if not is_valid:
                return False, applied, f"Validation failed: {validation_error}"

            # Write back
            if not self.dry_run:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write('\n')

            return True, applied, None

        except FileNotFoundError:
            return False, [], f"JSON file not found: {json_path}"
        except json.JSONDecodeError as e:
            return False, [], f"Invalid JSON: {e}"
        except Exception as e:
            return False, [], f"Error updating file: {e}"

    def process_issues(self, issue_number: Optional[int] = None, limit: Optional[int] = None) -> None:
        """Process GitHub issues and apply corrections."""
        print("\n" + "=" * 60)
        print("Processing GitHub Issues for Local Plan Corrections")
        print("=" * 60)

        if self.dry_run:
            print("Mode: DRY RUN (no changes will be applied)\n")
        else:
            print()

        # Warn if comment/close requested without authentication
        if (self.comment_issues or self.close_issues) and not self.github_token:
            print("⚠ WARNING: --comment and/or --close requested but GITHUB_TOKEN not set")
            print("  GitHub API requests may be rate-limited (60 req/hour)\n")

        if self.comment_issues:
            print("✓ Issue comments: ENABLED")
        if self.close_issues:
            print("✓ Issue closing: ENABLED")
        if self.comment_issues or self.close_issues:
            if self.github_token:
                token_preview = self.github_token[:10] + "..." + self.github_token[-4:]
                print(f"  Token set: {token_preview}")
            else:
                print("  ⚠ No GITHUB_TOKEN found in environment")
            print()

        # Fetch issues
        issues = self._fetch_issues(issue_number=issue_number, limit=limit)
        print(f"Found {len(issues)} unprocessed 'needs-editing' issues\n")

        if not issues:
            print("No issues to process.")
            return

        # Process each issue
        for issue in issues:
            issue_num = issue['number']
            issue_title = issue['title']
            issue_body = issue.get('body', '')

            print(f"Issue #{issue_num}: {issue_title}")

            # Extract file reference and changes
            file_ref = self._extract_file_reference(issue_body)
            changes_text = self._extract_changes_section(issue_body)
            changes = self._parse_changes(changes_text)

            # Check for skip/flag conditions
            if '_skip' in changes:
                print(f"  Status: ⊘ Skipped ({changes['_skip']})")
                self.state['skipped_issues'][str(issue_num)] = changes['_skip']
                self.skipped_count += 1
                print()
                continue

            if '_flag' in changes:
                print(f"  Status: ⚠ Flagged ({changes['_flag']})")
                self.state['failed_issues'][str(issue_num)] = changes['_flag']
                self.failed_count += 1
                print()
                continue

            if not file_ref:
                print(f"  Status: ✗ Failed (could not extract file reference)")
                self.state['failed_issues'][str(issue_num)] = "No file reference found"
                self.failed_count += 1
                print()
                continue

            if not changes or all(k.startswith('_') for k in changes.keys()):
                print(f"  Status: ✗ Failed (could not parse changes)")
                self.state['failed_issues'][str(issue_num)] = "No parseable changes found"
                self.failed_count += 1
                print()
                continue

            # Update JSON file
            json_path = self.LOCAL_PLAN_DIR / file_ref
            success, applied_changes, error = self._update_json_file(json_path, changes)

            if success:
                # Extract endpoint (hash) from filename
                endpoint = file_ref.replace('.json', '')

                # Also update source file if needed
                source_changes = []
                source_error = None
                if any(k in changes for k in ['adoption-date', 'name', 'period-start-date', 'period-end-date']):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            local_plan_data = json.load(f)
                        source_success, source_changes, source_error = self._update_source_file(local_plan_data, endpoint, changes)
                        if not source_success:
                            # Log source update failure but don't fail the whole issue
                            logger.warning(f"Could not update source file: {source_error}")
                    except Exception as e:
                        logger.warning(f"Could not read local-plan file for source update: {e}")

                print(f"  File: {file_ref}")
                print(f"  Changes detected:")
                # Only show local-plan changes in output (source updates are implicit)
                for change in applied_changes:
                    print(f"    - {change}")
                print(f"  Status: ✓ Updated" + (" (dry-run)" if self.dry_run else ""))
                # Record both local-plan and source changes in state file for reference
                all_changes = applied_changes + source_changes
                self.state['processed_issues'][str(issue_num)] = {
                    "processed_at": datetime.now().isoformat(),
                    "status": "success",
                    "changes_applied": all_changes,
                    "file": file_ref
                }
                self.processed_count += 1
                self.changes_log.append(f"Issue #{issue_num}: {applied_changes}")

                # Comment on issue if requested
                if self.comment_issues and not self.dry_run:
                    comment_success, comment_error = self._comment_on_issue(issue_num, applied_changes)
                    if comment_success:
                        print(f"  GitHub: ✓ Comment added")
                    else:
                        logger.warning(f"Could not comment on issue #{issue_num}: {comment_error}")
                        print(f"  GitHub: ⚠ Comment failed ({comment_error})")

                # Close issue if requested
                if self.close_issues and not self.dry_run:
                    close_success, close_error = self._close_issue(issue_num)
                    if close_success:
                        print(f"  GitHub: ✓ Issue closed")
                    else:
                        logger.warning(f"Could not close issue #{issue_num}: {close_error}")
                        print(f"  GitHub: ⚠ Close failed ({close_error})")
            else:
                print(f"  File: {file_ref}")
                print(f"  Status: ✗ Failed ({error})")
                self.state['failed_issues'][str(issue_num)] = error
                self.failed_count += 1

            print()

        # Save state
        self._save_state()

        # Print summary
        print("=" * 60)
        print("Summary:")
        print(f"  Processed: {self.processed_count}")
        print(f"  Skipped: {self.skipped_count}")
        print(f"  Failed: {self.failed_count}")
        print("=" * 60)

        # Regenerate CSVs if requested
        if self.regenerate_csvs and self.processed_count > 0 and not self.dry_run:
            print("\nRegenerating CSV datasets...")
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, "bin/generate-csvs.py"],
                    cwd=Path.cwd(),
                    capture_output=True,
                    timeout=60
                )
                if result.returncode == 0:
                    print("✓ CSV generation completed")
                else:
                    logger.warning(f"CSV generation had issues: {result.stderr.decode('utf-8', errors='ignore')}")
            except Exception as e:
                logger.error(f"Failed to regenerate CSVs: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Process 'needs-editing' GitHub issues and apply corrections to local plan JSON files",
        epilog="Examples:\n"
               "  process_github_issues.py              # Process all unprocessed issues\n"
               "  process_github_issues.py --limit 5    # Process first 5 issues\n"
               "  process_github_issues.py --dry-run    # Preview changes without applying\n"
               "  process_github_issues.py --comment    # Add comment to issues (requires GITHUB_TOKEN)\n"
               "  process_github_issues.py --close      # Close issues after processing\n"
               "  process_github_issues.py --comment --close --limit 10 # Comment + close 10 issues",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of issues to process'
    )

    parser.add_argument(
        '--issue-number',
        type=int,
        help='Process specific issue by number'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without applying changes'
    )

    parser.add_argument(
        '--regenerate-csvs',
        action='store_true',
        help='Regenerate CSV datasets after processing'
    )

    parser.add_argument(
        '--comment',
        action='store_true',
        help='Add comment to issues after processing (requires GITHUB_TOKEN)'
    )

    parser.add_argument(
        '--close',
        action='store_true',
        help='Close issues after processing (requires GITHUB_TOKEN)'
    )

    args = parser.parse_args()

    processor = GitHubIssueProcessor(
        dry_run=args.dry_run,
        regenerate_csvs=args.regenerate_csvs,
        comment_issues=args.comment,
        close_issues=args.close
    )

    processor.process_issues(
        issue_number=args.issue_number,
        limit=args.limit
    )


if __name__ == '__main__':
    main()
