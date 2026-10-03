"""Read-only context and draft lookup regressions; no live Zotero or model use."""
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


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("library/papers", "library/notes", "sync", "projects/demo", "projects/paper-ir"):
            (self.root / name).mkdir(parents=True)
        (self.root / "library/references.bib").write_text(
            "@article{paper2026,title={Fixture},author={A},journal={J},year={2026},doi={10.1234/demo}}\n")
        self.pdf = self.root / "library/papers/paper2026.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\nfixture")
        self.note = self.root / "library/notes/paper2026.md"
        self.note.write_text("# paper2026\n\n- 状态：未读\n\n## 我的话\n本人原话。\n")
        (self.root / "sync/assets.json").write_text(library.json_text({"schema_version": 1,
            "papers": {"paper2026": library.file_record(self.root, self.pdf)}}))
        (self.root / "projects/demo/state.md").write_text("# State\n\n下一步待确认。\n")
        (self.root / "projects/paper-ir/paper_ir.py").write_text(
            "def extract(source):\n    return 'fixture', [{'text': 'page one', 'location': {'page': 1}}, {'text': 'page two', 'location': {'page': 2}}]\n")
        patcher = patch.object(sr, "ROOT", self.root)
        patcher.start(); self.addCleanup(patcher.stop)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sr.main(["read", *args]) or 0
        return code, out.getvalue(), err.getvalue()

    def test_context_is_read_only_and_distinguishes_note_from_source(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, text, _ = self.cli("context", "paper2026", "--project", "demo")
        self.assertEqual(0, code)
        data = json.loads(text)
        self.assertEqual("未读", data["reading_status"])
        self.assertIn("不等于", data["note_basis"])
        self.assertIn("待确认", data["project_state"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_selected_page_never_includes_other_page(self):
        code, text, _ = self.cli("context", "paper2026", "--page", "2")
        self.assertEqual(0, code)
        data = json.loads(text)
        self.assertEqual(["page two"], [b["text"] for b in data["page_blocks"]])
        self.assertIn("未自动判定语义支持", data["evidence_boundary"])

    def test_changed_source_and_unregistered_key_are_rejected(self):
        self.assertEqual(1, self.cli("context", "../escape")[0])
        self.assertEqual(1, self.cli("context", "paper")[0])
        self.pdf.write_bytes(b"%PDF-1.7\nmodified")
        self.assertEqual(1, self.cli("context", "paper2026")[0])

    def test_invalid_page_or_project_are_rejected(self):
        for args in (("--page", "0"), ("--page", "3"), ("--project", "../escape"), ("--project", "absent")):
            self.assertEqual(1, self.cli("context", "paper2026", *args)[0])

    def test_drafts_use_exact_bibkey_and_do_not_read_symlink_targets(self):
        folder = self.root / "outputs/reading/zotero/sessions"
        folder.mkdir(parents=True)
        right = folder / "right.md"; right.write_text("bibkey: paper2026\n")
        (folder / "wrong.md").write_text("bibkey: paper2026more\n")
        (folder / "link.md").symlink_to(right)
        code, text, _ = self.cli("records", "paper2026", "--json")
        self.assertEqual(0, code)
        self.assertEqual([str(right)], json.loads(text))


if __name__ == "__main__":
    unittest.main()
