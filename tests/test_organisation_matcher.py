#!/usr/bin/env python3
"""
Unit tests for organisation_matcher module.

Tests the OrganisationMatcher class functionality including:
- Loading organisations from CSV
- Exact name matching
- Common variation matching
- Conservative matching (no uncertain matches)
"""

import sys
import os
import unittest

# Add bin to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))

from organisation_matcher import OrganisationMatcher


class TestOrganisationMatcher(unittest.TestCase):
    """Test cases for OrganisationMatcher class."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures - load matcher once for all tests."""
        # Change to project root
        os.chdir(os.path.join(os.path.dirname(__file__), '..'))
        cls.matcher = OrganisationMatcher("var/cache/organisation.csv")

    def test_matcher_loads_organisations(self):
        """Test that organisations are loaded from CSV."""
        self.assertIsNotNone(self.matcher.organisations)
        self.assertGreater(len(self.matcher.organisations), 0)

    def test_exact_match_bolton(self):
        """Test exact matching for Bolton Council."""
        result = self.matcher.match("Bolton Council")
        self.assertEqual(result, "local-authority:BOL")

    def test_exact_match_bury(self):
        """Test exact matching for Bury Council."""
        result = self.matcher.match("Bury Council")
        self.assertEqual(result, "local-authority:BUR")

    def test_exact_match_manchester(self):
        """Test exact matching for Manchester City Council."""
        result = self.matcher.match("Manchester City Council")
        self.assertEqual(result, "local-authority:MAN")

    def test_exact_match_oldham(self):
        """Test exact matching for Oldham Council."""
        result = self.matcher.match("Oldham Council")
        self.assertEqual(result, "local-authority:OLD")

    def test_exact_match_trafford(self):
        """Test exact matching for Trafford Council."""
        result = self.matcher.match("Trafford Council")
        self.assertEqual(result, "local-authority:TRF")

    def test_exact_match_bassetlaw(self):
        """Test exact matching for Bassetlaw District Council."""
        result = self.matcher.match("Bassetlaw District Council")
        self.assertEqual(result, "local-authority:BAE")

    def test_variation_match_bolton_metropolitan_borough(self):
        """Test variation matching with full title."""
        result = self.matcher.match("Bolton Metropolitan Borough Council")
        self.assertEqual(result, "local-authority:BOL")

    def test_no_match_returns_empty_string(self):
        """Test that unknown authorities return empty string."""
        result = self.matcher.match("Unknown Authority")
        self.assertEqual(result, "")

    def test_empty_name_returns_empty_string(self):
        """Test that empty name returns empty string."""
        result = self.matcher.match("")
        self.assertEqual(result, "")

    def test_none_name_returns_empty_string(self):
        """Test that None name returns empty string."""
        result = self.matcher.match(None)
        self.assertEqual(result, "")

    def test_match_all_multiple_names(self):
        """Test matching multiple names at once."""
        names = ["Bolton Council", "Manchester City Council", "Unknown Authority"]
        results = self.matcher.match_all(names)

        self.assertEqual(len(results), 3)
        self.assertEqual(results["Bolton Council"], "local-authority:BOL")
        self.assertEqual(results["Manchester City Council"], "local-authority:MAN")
        self.assertEqual(results["Unknown Authority"], "")

    def test_match_all_empty_list(self):
        """Test match_all with empty list."""
        results = self.matcher.match_all([])
        self.assertEqual(results, {})

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        result1 = self.matcher.match("Bolton Council")
        result2 = self.matcher.match("bolton council")
        result3 = self.matcher.match("BOLTON COUNCIL")

        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)

    def test_whitespace_trimming(self):
        """Test that whitespace is trimmed from input."""
        result1 = self.matcher.match("Bolton Council")
        result2 = self.matcher.match("  Bolton Council  ")

        self.assertEqual(result1, result2)


if __name__ == '__main__':
    unittest.main()
