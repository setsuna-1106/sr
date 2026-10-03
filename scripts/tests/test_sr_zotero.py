"""Bridge tests use fixture libraries and a fake local API, never live Zotero."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sr
import sr_library as library
import sr_zotero as bridge


def paper(key="PAPER001", doi="10.1234/new", title="A new paper"):
    return {"key": key, "data": {"itemType": "journalArticle", "title": title,
        "DOI": doi, "date": "2026-09-29", "publicationTitle": "Test Journal",
        "creators": [{"creatorType": "author", "firstName": "A", "lastName": "Author"}]}}


def pdf(key="PDF00001", parent="PAPER001"):
    return {"key": key, "data": {"itemType": "attachment", "parentItem": parent,
                                "contentType": "application/pdf"}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "sr"
        for directory in ("library/papers", "library/notes", "library/inbox", "sync"):
            (self.root / directory).mkdir(parents=True)
        self.bib = self.root / "library/references.bib"
        self.bib.write_text("@article{old2026,\n title={Old},\n author={Author},\n journal={Journal},\n year={2026},\n doi={10.1234/old},\n}\n")
        self.old_pdf = self.root / "library/papers/old2026.pdf"
        self.old_pdf.write_bytes(b"%PDF-1.7\nfixture old\n")
        library.write_files({self.root / "sync/assets.json": library.json_text({
            "schema_version": 1, "papers": {"old2026": library.file_record(self.root, self.old_pdf)}})})
        self.index = self.root / "library/index.md"
        self.index.write_text(library.index_text(self.root))
        self.source = Path(self.temp.name) / "zotero source.pdf"
        self.source.write_bytes(b"%PDF-1.7\nfixture new\n")
        self.items = [paper(), pdf()]
        patcher = patch.object(sr, "ROOT", self.root)
        patcher.start(); self.addCleanup(patcher.stop)
        self.client_patch = patch.object(bridge, "Client")
        self.client = self.client_patch.start().return_value
        self.client.items.side_effect = lambda: self.items
        self.client.pdf_path.return_value = self.source
        self.addCleanup(self.client_patch.stop)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sr.main(["zotero", *args]) or 0
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file() and path.name != ".sr.lock"}

    def test_status_and_search_are_read_only(self):
        before = self.snapshot()
        code, out, _ = self.cli("status", "--json")
        self.assertEqual(0, code)
        self.assertEqual(0, json.loads(out)["matched"])
        code, out, _ = self.cli("list", "new paper", "--json")
        self.assertEqual(0, code)
        self.assertEqual(["PDF00001"], json.loads(out)[0]["pdf_keys"])
        self.assertEqual(before, self.snapshot())

    def test_export_preserves_central_assets_and_resolves_space_paths(self):
        before = self.snapshot()
        output = Path(self.temp.name) / "export file.bib"
        self.assertEqual(0, self.cli("export", "--output", str(output))[0])
        fields = library.parse_bib(output.read_text())["old2026"]["fields"]
        self.assertEqual(str(self.old_pdf.resolve()), fields["file"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(1, self.cli("export", "--output", str(output))[0])

    def test_export_rejects_corrupted_pdf(self):
        self.old_pdf.write_bytes(b"%PDF-1.7\ncorrupt\n")
        output = Path(self.temp.name) / "export.bib"
        self.assertEqual(1, self.cli("export", "--output", str(output))[0])
        self.assertFalse(output.exists())

    def test_missing_only_export_skips_already_matched_items(self):
        self.items = [paper(doi="https://doi.org/10.1234/OLD"), pdf()]
        output = Path(self.temp.name) / "missing.bib"
        self.assertEqual(0, self.cli("export", "--missing-only", "--output", str(output))[0])
        self.assertFalse(output.exists())

    def test_import_preview_does_not_write_or_move_source(self):
        before, source = self.snapshot(), self.source.read_bytes()
        self.assertEqual(0, self.cli("import", "PAPER001", "--as", "new2026")[0])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(source, self.source.read_bytes())

    def test_import_applies_registration_note_inventory_and_mapping(self):
        source = self.source.read_bytes()
        self.assertEqual(0, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.assertEqual(source, self.source.read_bytes())
        self.assertEqual(source, (self.root / "library/papers/new2026.pdf").read_bytes())
        self.assertEqual("未读", library.note_status((self.root / "library/notes/new2026.md").read_text()))
        self.assertIn("new2026", library.inventory_data(self.root)["papers"])
        self.assertEqual("PAPER001", json.loads((self.root / bridge.MAPPING).read_text())["papers"]["new2026"]["item_key"])
        before = self.snapshot()
        self.assertEqual(0, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.assertEqual(before, self.snapshot())

    def test_import_failure_rolls_back_registration(self):
        before = self.snapshot()
        with patch.object(sr, "note", side_effect=OSError("injected failure")):
            self.assertEqual(1, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.assertEqual(before, self.snapshot())
        self.assertTrue(self.source.exists())

    def test_broken_target_symlink_is_preserved(self):
        link = self.root / "library/notes/new2026.md"
        link.symlink_to(self.root / "nonexistent.md")
        before = self.snapshot()
        self.assertEqual(1, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.assertTrue(link.is_symlink())
        self.assertEqual(before, self.snapshot())

    def test_missing_metadata_ambiguous_pdf_and_unsafe_key_are_rejected(self):
        before = self.snapshot()
        self.items[0]["data"]["DOI"] = ""
        self.assertEqual(1, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.items = [paper(), pdf(), pdf(key="PDF00002")]
        self.assertEqual(1, self.cli("import", "PAPER001", "--as", "new2026", "--apply")[0])
        self.assertEqual(1, self.cli("import", "PAPER001", "--as", "../escape", "--apply")[0])
        self.assertEqual(before, self.snapshot())

    def test_doi_conflict_never_overwrites_existing_mapping(self):
        mapping = self.root / bridge.MAPPING
        mapping.write_text('{"previous":true}')
        self.items = [paper(doi="10.1234/old"), paper(key="PAPER002", doi="10.1234/old")]
        before = self.snapshot()
        self.assertEqual(1, self.cli("sync")[0])
        self.assertEqual(before, self.snapshot())

    def test_open_url_uses_live_matching_and_one_based_page(self):
        self.items = [paper(doi="10.1234/old"), pdf()]
        code, out, _ = self.cli("open", "old2026", "--page", "7", "--print-url")
        self.assertEqual(0, code)
        self.assertEqual("zotero://open-pdf/library/items/PDF00001?page=7\n", out)
        self.assertEqual(1, self.cli("open", "old2026", "--page", "0", "--print-url")[0])

    def test_remote_file_url_is_not_downloaded(self):
        class Reader:
            def read(self, *args, **kwargs):
                return "https://example.org/paper.pdf"
        with self.assertRaises(ValueError):
            _real_pdf_path(Reader(), "PDF00001")


_real_pdf_path = bridge.Client.pdf_path if hasattr(bridge.Client, "pdf_path") else None

if __name__ == "__main__":
    unittest.main()
