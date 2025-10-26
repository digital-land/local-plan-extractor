#!/usr/bin/env python3
"""
Integration tests for local-plan-extractor with organisation_matcher module.

Tests that the LocalPlanHousingExtractor correctly integrates with
the OrganisationMatcher module.
"""

import sys
import os
import unittest
import ast

# Add bin to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))

from organisation_matcher import OrganisationMatcher


class TestLocalPlanExtractorIntegration(unittest.TestCase):
    """Integration tests for local-plan-extractor with organisation matcher."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        # Change to project root
        os.chdir(os.path.join(os.path.dirname(__file__), '..'))
        cls.extractor_path = 'bin/local-plan-extractor.py'

    def test_extractor_syntax_valid(self):
        """Test that local-plan-extractor.py has valid Python syntax."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        try:
            ast.parse(extractor_code)
        except SyntaxError as e:
            self.fail(f"Syntax error in local-plan-extractor.py: {e}")

    def test_extractor_imports_organisation_matcher(self):
        """Test that extractor imports OrganisationMatcher."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        self.assertIn(
            'from organisation_matcher import OrganisationMatcher',
            extractor_code,
            "OrganisationMatcher import not found"
        )

    def test_extractor_creates_org_matcher_instance(self):
        """Test that extractor creates org_matcher instance."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        self.assertIn(
            'self.org_matcher = OrganisationMatcher',
            extractor_code,
            "org_matcher instance creation not found"
        )

    def test_extractor_uses_org_matcher_match(self):
        """Test that extractor uses org_matcher.match() method."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        self.assertIn(
            'self.org_matcher.match(',
            extractor_code,
            "org_matcher.match() calls not found"
        )

    def test_extractor_removed_old_match_method(self):
        """Test that old match_organisation method was removed."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        # Check that old method definition is gone
        self.assertNotIn(
            'def match_organisation(self',
            extractor_code,
            "Old match_organisation method still exists"
        )

        # Check that old calls are gone
        self.assertNotIn(
            'self.match_organisation(',
            extractor_code,
            "Old self.match_organisation() calls still exist"
        )

    def test_extractor_removed_load_organisations_method(self):
        """Test that old _load_organisations method was removed."""
        with open(self.extractor_path, 'r') as f:
            extractor_code = f.read()

        self.assertNotIn(
            'def _load_organisations(self',
            extractor_code,
            "Old _load_organisations method still exists"
        )

    def test_organisation_matcher_module_exists(self):
        """Test that organisation_matcher.py module file exists."""
        matcher_path = 'bin/organisation_matcher.py'
        self.assertTrue(
            os.path.exists(matcher_path),
            f"organisation_matcher.py not found at {matcher_path}"
        )

    def test_organisation_matcher_syntax_valid(self):
        """Test that organisation_matcher.py has valid Python syntax."""
        matcher_path = 'bin/organisation_matcher.py'
        with open(matcher_path, 'r') as f:
            matcher_code = f.read()

        try:
            ast.parse(matcher_code)
        except SyntaxError as e:
            self.fail(f"Syntax error in organisation_matcher.py: {e}")

    def test_organisation_matcher_has_class(self):
        """Test that organisation_matcher.py defines OrganisationMatcher class."""
        matcher_path = 'bin/organisation_matcher.py'
        with open(matcher_path, 'r') as f:
            matcher_code = f.read()

        self.assertIn(
            'class OrganisationMatcher',
            matcher_code,
            "OrganisationMatcher class not found"
        )

    def test_organisation_matcher_has_match_method(self):
        """Test that OrganisationMatcher has match() method."""
        matcher_path = 'bin/organisation_matcher.py'
        with open(matcher_path, 'r') as f:
            matcher_code = f.read()

        self.assertIn(
            'def match(self',
            matcher_code,
            "match() method not found in OrganisationMatcher"
        )

    def test_organisation_matcher_has_match_all_method(self):
        """Test that OrganisationMatcher has match_all() method."""
        matcher_path = 'bin/organisation_matcher.py'
        with open(matcher_path, 'r') as f:
            matcher_code = f.read()

        self.assertIn(
            'def match_all(self',
            matcher_code,
            "match_all() method not found in OrganisationMatcher"
        )


if __name__ == '__main__':
    unittest.main()
