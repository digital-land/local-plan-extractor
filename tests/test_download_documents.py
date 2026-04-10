#!/usr/bin/env python3
"""
Unit tests for download-documents.py.

Tests the DocumentDownloader class in isolation — no real HTTP requests
are made. All file I/O uses temporary directories.

Coverage:
  - find_main_document: filtering logic for the default (main plan only) mode
  - find_all_documents: gathering all docs for --all mode
  - save_document: correct suffix detection and file writing
  - create_log_entry: log file structure and SHA1 hash
  - create_resource_entry: deduplication by SHA1
  - _download_single: orchestration, state tracking, early exit
  - process_authority: main vs --all mode, bad JSON handling
  - run: --limit, --authority, --resume, --retry-failed, --retry-no-document
  - state management: load from file, create fresh, save
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load download-documents.py (hyphen in name prevents normal import)
_spec = importlib.util.spec_from_file_location(
    "download_documents",
    os.path.join(os.path.dirname(__file__), '..', 'bin', 'download-documents.py')
)
_mod = importlib.util.module_from_spec(_spec)
# Patch cloudscraper before the module executes so no real session is created
with patch('cloudscraper.create_scraper', return_value=MagicMock()):
    _spec.loader.exec_module(_mod)

DocumentDownloader = _mod.DocumentDownloader

# Minimal fake PDF content (magic bytes so detect_file_suffix returns 'pdf')
FAKE_PDF = b"%PDF-1.4 fake content"
FAKE_DOCX = b"PK\x03\x04" + b"word/" + b"\x00" * 50


def make_downloader(tmp_dir, state=None):
    """Create a DocumentDownloader pointed at a temp directory, with cloudscraper mocked."""
    source_dir = Path(tmp_dir) / "source"
    collection_dir = Path(tmp_dir) / "collection"
    source_dir.mkdir()

    with patch('cloudscraper.create_scraper', return_value=MagicMock()):
        dl = DocumentDownloader(
            source_dir=str(source_dir),
            collection_dir=str(collection_dir),
        )

    if state:
        dl.state.update(state)

    return dl, source_dir


def write_source_json(source_dir, authority, plans):
    """Write a source JSON file for an authority."""
    path = source_dir / f"{authority}.json"
    path.write_text(json.dumps(plans))
    return path


def adopted_lp_plan(doc_url="https://example.com/plan.pdf", endpoint="abc123", doc_type="local-plan-adopted"):
    """Return a minimal adopted local plan entry."""
    return {
        "organisation": "local-authority:TST",
        "status": "adopted",
        "reference": "LP-TST-2020",
        "documents": [
            {
                "document-url": doc_url,
                "document-type": doc_type,
                "endpoint": endpoint,
            }
        ]
    }


# ---------------------------------------------------------------------------
# find_main_document
# ---------------------------------------------------------------------------

class TestFindMainDocument(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_adopted_plan_with_lp_reference(self):
        plan = adopted_lp_plan()
        result = self.dl.find_main_document([plan])
        self.assertIsNotNone(result)
        plan_entry, doc_entry = result
        self.assertEqual(doc_entry["document-url"], "https://example.com/plan.pdf")

    def test_ignores_non_adopted_status(self):
        plan = adopted_lp_plan()
        plan["status"] = "draft"
        self.assertIsNone(self.dl.find_main_document([plan]))

    def test_ignores_reference_not_starting_with_lp(self):
        plan = adopted_lp_plan()
        plan["reference"] = "WP-TST-2020"
        self.assertIsNone(self.dl.find_main_document([plan]))

    def test_ignores_wrong_document_type(self):
        plan = adopted_lp_plan(doc_type="evidence-base")
        self.assertIsNone(self.dl.find_main_document([plan]))

    def test_accepts_local_plan_document_type(self):
        plan = adopted_lp_plan(doc_type="local-plan")
        result = self.dl.find_main_document([plan])
        self.assertIsNotNone(result)

    def test_ignores_doc_with_empty_url(self):
        plan = adopted_lp_plan(doc_url="")
        self.assertIsNone(self.dl.find_main_document([plan]))

    def test_returns_none_for_empty_list(self):
        self.assertIsNone(self.dl.find_main_document([]))

    def test_returns_first_match_when_multiple_plans(self):
        plan1 = adopted_lp_plan(doc_url="https://example.com/first.pdf", endpoint="ep1")
        plan2 = adopted_lp_plan(doc_url="https://example.com/second.pdf", endpoint="ep2")
        _, doc = self.dl.find_main_document([plan1, plan2])
        self.assertEqual(doc["endpoint"], "ep1")

    def test_skips_non_matching_plan_finds_second(self):
        bad = adopted_lp_plan()
        bad["status"] = "draft"
        good = adopted_lp_plan(doc_url="https://example.com/good.pdf", endpoint="good_ep")
        _, doc = self.dl.find_main_document([bad, good])
        self.assertEqual(doc["endpoint"], "good_ep")


# ---------------------------------------------------------------------------
# find_all_documents
# ---------------------------------------------------------------------------

class TestFindAllDocuments(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_all_docs_with_url_and_endpoint(self):
        plans = [
            {"documents": [
                {"document-url": "https://a.com/1.pdf", "endpoint": "ep1"},
                {"document-url": "https://a.com/2.pdf", "endpoint": "ep2"},
            ]},
        ]
        results = self.dl.find_all_documents(plans)
        self.assertEqual(len(results), 2)

    def test_skips_doc_without_url(self):
        plans = [{"documents": [{"endpoint": "ep1"}]}]
        self.assertEqual(self.dl.find_all_documents(plans), [])

    def test_skips_doc_without_endpoint(self):
        plans = [{"documents": [{"document-url": "https://a.com/1.pdf"}]}]
        self.assertEqual(self.dl.find_all_documents(plans), [])

    def test_collects_across_multiple_plans(self):
        plans = [
            {"documents": [{"document-url": "https://a.com/1.pdf", "endpoint": "ep1"}]},
            {"documents": [{"document-url": "https://a.com/2.pdf", "endpoint": "ep2"}]},
        ]
        self.assertEqual(len(self.dl.find_all_documents(plans)), 2)

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(self.dl.find_all_documents([]), [])

    def test_returns_empty_when_no_documents_key(self):
        self.assertEqual(self.dl.find_all_documents([{"status": "adopted"}]), [])


# ---------------------------------------------------------------------------
# save_document
# ---------------------------------------------------------------------------

class TestSaveDocument(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saves_pdf_with_pdf_suffix(self):
        ok, err = self.dl.save_document(FAKE_PDF, "endpoint123")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertTrue((self.dl.document_dir / "endpoint123.pdf").exists())

    def test_saves_docx_with_docx_suffix(self):
        ok, err = self.dl.save_document(FAKE_DOCX, "endpoint456", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertTrue(ok)
        self.assertTrue((self.dl.document_dir / "endpoint456.docx").exists())

    def test_saved_content_matches_input(self):
        self.dl.save_document(FAKE_PDF, "ep_content")
        content = (self.dl.document_dir / "ep_content.pdf").read_bytes()
        self.assertEqual(content, FAKE_PDF)

    def test_returns_error_when_directory_missing(self):
        # Point document_dir at a non-existent nested path to force failure
        self.dl.document_dir = Path(self.tmp.name) / "no" / "such" / "dir"
        ok, err = self.dl.save_document(FAKE_PDF, "ep")
        self.assertFalse(ok)
        self.assertIn("Failed to save", err)


# ---------------------------------------------------------------------------
# create_log_entry
# ---------------------------------------------------------------------------

class TestCreateLogEntry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_log_file(self):
        ok, err = self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertTrue((self.dl.log_dir / "ep1.json").exists())

    def test_log_contains_correct_url(self):
        self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF)
        log = json.loads((self.dl.log_dir / "ep1.json").read_text())
        self.assertEqual(log["endpoint-url"], "https://example.com/plan.pdf")

    def test_log_sha1_matches_content(self):
        self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF)
        log = json.loads((self.dl.log_dir / "ep1.json").read_text())
        expected = hashlib.sha1(FAKE_PDF).hexdigest()
        self.assertEqual(log["resource"], expected)

    def test_log_bytes_matches_content_length(self):
        self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF)
        log = json.loads((self.dl.log_dir / "ep1.json").read_text())
        self.assertEqual(log["bytes"], str(len(FAKE_PDF)))

    def test_log_uses_provided_content_type(self):
        self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF, content_type="application/pdf")
        log = json.loads((self.dl.log_dir / "ep1.json").read_text())
        self.assertEqual(log["content-type"], "application/pdf")

    def test_log_defaults_content_type_to_pdf(self):
        self.dl.create_log_entry("https://example.com/plan.pdf", "ep1", FAKE_PDF)
        log = json.loads((self.dl.log_dir / "ep1.json").read_text())
        self.assertEqual(log["content-type"], "application/pdf")


# ---------------------------------------------------------------------------
# create_resource_entry
# ---------------------------------------------------------------------------

class TestCreateResourceEntry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_resource_file_named_by_sha1(self):
        ok, sha1 = self.dl.create_resource_entry(FAKE_PDF)
        self.assertTrue(ok)
        expected_hash = hashlib.sha1(FAKE_PDF).hexdigest()
        self.assertEqual(sha1, expected_hash)
        self.assertTrue((self.dl.resource_dir / sha1).exists())

    def test_does_not_overwrite_existing_resource(self):
        self.dl.create_resource_entry(FAKE_PDF)
        sha1 = hashlib.sha1(FAKE_PDF).hexdigest()
        resource_path = self.dl.resource_dir / sha1
        mtime_before = resource_path.stat().st_mtime

        self.dl.create_resource_entry(FAKE_PDF)
        self.assertEqual(resource_path.stat().st_mtime, mtime_before)

    def test_different_content_creates_different_resource(self):
        self.dl.create_resource_entry(FAKE_PDF)
        self.dl.create_resource_entry(b"different content")
        self.assertEqual(len(list(self.dl.resource_dir.iterdir())), 2)


# ---------------------------------------------------------------------------
# _download_single
# ---------------------------------------------------------------------------

class TestDownloadSingle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, _ = make_downloader(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_already_downloaded_when_in_state(self):
        self.dl.state["downloaded"].append("ep_existing")
        result = self.dl._download_single("https://example.com/plan.pdf", "ep_existing")
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "Already downloaded")

    def test_successful_download_adds_endpoint_to_state(self):
        self.dl.download_document = MagicMock(return_value=(True, FAKE_PDF, "application/pdf", None))
        result = self.dl._download_single("https://example.com/plan.pdf", "new_ep")
        self.assertTrue(result["success"])
        self.assertIn("new_ep", self.dl.state["downloaded"])

    def test_failed_download_returns_error_and_does_not_update_state(self):
        self.dl.download_document = MagicMock(return_value=(False, None, None, "HTTP error 404"))
        result = self.dl._download_single("https://example.com/plan.pdf", "fail_ep")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "HTTP error 404")
        self.assertNotIn("fail_ep", self.dl.state["downloaded"])

    def test_successful_download_creates_files(self):
        self.dl.download_document = MagicMock(return_value=(True, FAKE_PDF, "application/pdf", None))
        self.dl._download_single("https://example.com/plan.pdf", "file_ep")
        self.assertTrue((self.dl.document_dir / "file_ep.pdf").exists())
        self.assertTrue((self.dl.log_dir / "file_ep.json").exists())


# ---------------------------------------------------------------------------
# process_authority
# ---------------------------------------------------------------------------

class TestProcessAuthority(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, self.source_dir = make_downloader(self.tmp.name)
        self.dl.download_document = MagicMock(return_value=(True, FAKE_PDF, "application/pdf", None))

    def tearDown(self):
        self.tmp.cleanup()

    def test_main_mode_returns_no_document_when_none_found(self):
        path = write_source_json(self.source_dir, "local-authority:TST", [
            {"status": "draft", "reference": "LP-TST-2020", "documents": []}
        ])
        result = self.dl.process_authority("local-authority:TST", path, download_all=False)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "No adopted local plan document found")

    def test_main_mode_downloads_main_document(self):
        plan = adopted_lp_plan(endpoint="main_ep")
        path = write_source_json(self.source_dir, "local-authority:TST", [plan])
        result = self.dl.process_authority("local-authority:TST", path, download_all=False)
        self.assertTrue(result["success"])
        self.assertIn("main_ep", self.dl.state["downloaded"])

    def test_all_mode_downloads_all_documents(self):
        plans = [{
            "documents": [
                {"document-url": "https://a.com/1.pdf", "endpoint": "ep1"},
                {"document-url": "https://a.com/2.pdf", "endpoint": "ep2"},
            ]
        }]
        path = write_source_json(self.source_dir, "local-authority:TST", plans)
        result = self.dl.process_authority("local-authority:TST", path, download_all=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["downloaded_count"], 2)

    def test_all_mode_returns_no_documents_when_none_found(self):
        path = write_source_json(self.source_dir, "local-authority:TST", [{"documents": []}])
        result = self.dl.process_authority("local-authority:TST", path, download_all=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "No documents found")

    def test_invalid_json_returns_error(self):
        path = self.source_dir / "local-authority:BAD.json"
        path.write_text("{ not valid json")
        result = self.dl.process_authority("local-authority:BAD", path)
        self.assertFalse(result["success"])
        self.assertIn("Failed to load JSON", result["reason"])

    def test_missing_file_returns_error(self):
        path = self.source_dir / "local-authority:MISSING.json"
        result = self.dl.process_authority("local-authority:MISSING", path)
        self.assertFalse(result["success"])
        self.assertIn("Failed to load JSON", result["reason"])


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

class TestStateManagement(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_fresh_state_when_no_file(self):
        dl, _ = make_downloader(self.tmp.name)
        self.assertIn("processed", dl.state)
        self.assertIn("downloaded", dl.state)
        self.assertIn("failed", dl.state)
        self.assertIn("no_document", dl.state)

    def test_loads_existing_state_from_file(self):
        collection_dir = Path(self.tmp.name) / "collection2"
        collection_dir.mkdir()
        state_file = collection_dir / "download_state.json"
        existing_state = {
            "started": "2024-01-01T00:00:00+00:00",
            "processed": ["local-authority:TST"],
            "failed": [],
            "no_document": [],
            "downloaded": ["ep_already"]
        }
        state_file.write_text(json.dumps(existing_state))

        source_dir = Path(self.tmp.name) / "source2"
        source_dir.mkdir()
        with patch('cloudscraper.create_scraper', return_value=MagicMock()):
            dl = DocumentDownloader(source_dir=str(source_dir), collection_dir=str(collection_dir))

        self.assertIn("local-authority:TST", dl.state["processed"])
        self.assertIn("ep_already", dl.state["downloaded"])

    def test_saves_state_to_file(self):
        dl, _ = make_downloader(self.tmp.name)
        dl.state["processed"].append("local-authority:NEW")
        dl._save_state()
        saved = json.loads(dl.state_file.read_text())
        self.assertIn("local-authority:NEW", saved["processed"])


# ---------------------------------------------------------------------------
# run — filtering logic
# ---------------------------------------------------------------------------

class TestRunFiltering(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl, self.source_dir = make_downloader(self.tmp.name)
        # Patch process_authority to avoid real downloads
        self.dl.process_authority = MagicMock(return_value={
            "success": True, "reason": None, "endpoint": "ep1",
            "url": "https://example.com/plan.pdf",
            "downloaded_count": 1, "failed_count": 0,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def _write_authorities(self, codes):
        for code in codes:
            write_source_json(self.source_dir, code, [adopted_lp_plan()])

    def test_limit_restricts_number_processed(self):
        self._write_authorities(["local-authority:A", "local-authority:B", "local-authority:C"])
        self.dl.run(limit=2)
        self.assertEqual(self.dl.process_authority.call_count, 2)

    def test_authority_filter_restricts_to_matching_file(self):
        self._write_authorities(["local-authority:AAA", "local-authority:BBB"])
        self.dl.run(authority_filter="local-authority:AAA")
        self.assertEqual(self.dl.process_authority.call_count, 1)
        call_args = self.dl.process_authority.call_args
        self.assertIn("AAA", str(call_args))

    def test_resume_skips_already_processed(self):
        self._write_authorities(["local-authority:A", "local-authority:B"])
        self.dl.state["processed"].append("local-authority:A")
        self.dl.run(resume=True)
        self.assertEqual(self.dl.process_authority.call_count, 1)

    def test_retry_failed_only_processes_failed_authorities(self):
        self._write_authorities(["local-authority:A", "local-authority:B"])
        self.dl.state["failed"] = [{"org_code": "local-authority:A", "reason": "HTTP error 404", "url": None}]
        self.dl.run(retry_failed=True)
        self.assertEqual(self.dl.process_authority.call_count, 1)

    def test_retry_no_document_only_processes_no_document_authorities(self):
        self._write_authorities(["local-authority:A", "local-authority:B"])
        self.dl.state["no_document"] = ["local-authority:B"]
        self.dl.run(retry_no_document=True)
        self.assertEqual(self.dl.process_authority.call_count, 1)

    def test_no_source_files_exits_cleanly(self):
        # source dir is empty — should not raise
        self.dl.run()
        self.dl.process_authority.assert_not_called()


if __name__ == '__main__':
    unittest.main()
