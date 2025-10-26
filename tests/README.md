# Tests

This directory contains tests for the local-plan-extractor project.

## Test Files

### test_organisation_matcher.py
Unit tests for the `organisation_matcher` module.

Tests include:
- Loading organisations from CSV
- Exact name matching
- Common variation matching (e.g., "Bolton" → "Bolton Council" → "Bolton Metropolitan Borough Council")
- Case-insensitive matching
- Conservative matching behavior (no uncertain matches)
- `match()` and `match_all()` methods

**Run:** `python3 tests/test_organisation_matcher.py -v`

### test_integration.py
Integration tests to verify that `local-plan-extractor.py` correctly integrates with the `organisation_matcher` module.

Tests include:
- Module exists and has valid syntax
- Correct imports and usage
- Old code removed (no duplication)
- Class and method definitions present

**Run:** `python3 tests/test_integration.py -v`

## Running All Tests

Run all tests from the project root:

```bash
# Run all tests
python3 -m unittest discover tests -v

# Run specific test file
python3 tests/test_organisation_matcher.py
python3 tests/test_integration.py

# Run with verbose output
python3 tests/test_organisation_matcher.py -v
```

## Test Results

All tests should pass:
- ✅ 15 unit tests for organisation_matcher
- ✅ 11 integration tests

Total: **26 tests**

## Requirements

Tests require:
- Python 3.6+
- `var/cache/organisation.csv` file to be present
- Access to `bin/organisation_matcher.py` and `bin/local-plan-extractor.py`

No additional test dependencies required (uses standard library `unittest`).
