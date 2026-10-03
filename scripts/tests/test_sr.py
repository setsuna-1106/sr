"""Regression tests use isolated repositories; no real research assets are changed."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import sr
import sr_library as library


def entry(key="demo2026", title="A {nested} title", doi="10.1234/demo"):
    return (f"@article{{{key},\n  title = {{{title}}},\n  author = {{A. Author}},\n"
            f"  journal = {{Journal}},\n  year = {{2026}},\n  doi = {{{doi}}},\n}}\n")


class BibliographyTests(unittest.TestCase):
    def test_nested_quoted_numeric_values_and_comments(self):
        parsed = library.parse_bib('% comment\n' + entry() + '\n@book(other, title="A {quoted} title", year=2025)')
        self.assertEqual(parsed["demo2026"]["fields"]["title"], "A {nested} title")
        self.assertEqual(parsed["other"]["fields"]["year"], "2025")
        self.assertEqual(parsed["demo2026"]["raw"] + "\n", entry())

    def test_malformed_duplicate_and_macro_entries_are_not_silently_accepted(self):
        for text in (entry() + entry(), entry().rstrip()[:-1], '@article{a,title=macro}',
                     '@article{a,title={x} # {y}}', '@article{a,title={x},title={y}}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                library.parse_bib(text)


class SrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(sr, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("library/papers", "library/notes", "library/inbox", "projects", "outputs/drafts", "outputs/releases", "sync"):
            (self.root / name).mkdir(parents=True)
        shutil.copytree(SCRIPTS.parent / "projects/_template", self.root / "projects/_template")
        for name in ("PROFILE.md", "lessons.md", "sync/manifest.md"):
            (self.root / name).write_text("# Test\n", encoding="utf-8")
        self.bib = self.root / "library/references.bib"
        self.bib.write_text(entry(), encoding="utf-8")
        self.pdf = self.root / "library/papers/demo2026.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\nfixture\n")
        self.index = self.root / "library/index.md"
        self.index.write_text("# 文献分类索引\n\n## 主题\n\n"
            "| bibkey | 论文 | 文献角色 | 卡片状态 | 关键词 | 资产 |\n"
            "|---|---|---|---|---|---|\n"
            "| `demo2026` | A title | 人工分类 | 未建卡 | 人工标签 | [PDF](papers/demo2026.pdf) |\n", encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sr.main(list(argv)) or 0
        return code, out.getvalue(), err.getvalue()

    def import_args(self, path, *extra):
        return ("add-file", str(path), "--as", "second2026", "--doi", "10.1234/second",
                "--title", "Verified title", "--author", "B. Author", "--journal", "Journal", "--year", "2026", *extra)

    def test_exact_key_lookup_and_path_validation(self):
        before = self.bib.read_bytes()
        for key in ("demo", "Author", "../escape", ""):
            with self.subTest(key=key):
                self.assertEqual(1, self.run_cli("note", key)[0])
        self.assertEqual(before, self.bib.read_bytes())
        self.assertEqual([], list((self.root / "library/notes").iterdir()))
        self.assertEqual(1, self.run_cli("status", "../escape", "粗读")[0])

    def test_note_and_status_sync_all_index_rows_preserving_personal_content(self):
        self.index.write_text(self.index.read_text() + "\n## 另一个主题\n" + self.index.read_text().splitlines()[-1] + "\n")
        self.assertEqual(0, self.run_cli("note", "demo2026")[0])
        path = self.root / "library/notes/demo2026.md"
        text = path.read_text().replace("## 我的话（本人填写）\n", "## 我的话（本人填写）\n\n用户的解读，不可修改。\n")
        path.write_text(text)
        self.assertEqual(0, self.run_cli("status", "demo2026", "粗读")[0])
        self.assertEqual(text.replace("- 状态：未读", "- 状态：粗读"), path.read_text())
        self.assertEqual(2, self.index.read_text().count("| 粗读 |"))
        self.assertEqual(2, self.index.read_text().count("[精读卡](notes/demo2026.md)"))
        self.assertIn("人工分类", self.index.read_text())

    def test_invalid_or_duplicate_status_does_not_write(self):
        path = self.root / "library/notes/demo2026.md"
        for text in ("# Missing status\n", "- 状态：未读\n- 状态：粗读\n", "- 状态：猜测\n"):
            path.write_text(text)
            self.assertEqual(1, self.run_cli("status", "demo2026", "粗读")[0])
            self.assertEqual(text, path.read_text())

    def test_inventory_is_idempotent_and_rejects_same_size_corruption(self):
        self.assertEqual(0, self.run_cli("inventory")[0])
        inventory = self.root / "sync/assets.json"
        before = inventory.read_bytes()
        self.assertEqual(0, self.run_cli("inventory")[0])
        self.assertEqual(before, inventory.read_bytes())
        self.pdf.write_bytes(self.pdf.read_bytes().replace(b"fixture", b"changed"))
        self.assertEqual(1, self.run_cli("doctor", "--verify")[0])
        self.assertEqual(1, self.run_cli("inventory")[0])
        self.assertEqual(before, inventory.read_bytes())

    def test_import_archives_and_records_hash_and_unclassified_index(self):
        source = self.root / "library/inbox/new.PDF"
        source.write_bytes(b"%PDF-1.7\nnew paper\n")
        raw = source.read_bytes()
        self.assertEqual(0, self.run_cli(*self.import_args(source))[0])
        target = self.root / "library/papers/second2026.pdf"
        self.assertFalse(source.exists())
        self.assertEqual(raw, target.read_bytes())
        self.assertIn("second2026", library.bibliography(self.root))
        data = library.inventory_data(self.root)
        self.assertEqual(library.file_record(self.root, target), data["papers"]["second2026"])
        self.assertIn("## 待分类", self.index.read_text())
        self.assertIn("| 待确认 | 未建卡 | 待分类 |", self.index.read_text())

    def test_inventory_preserves_historical_markdown_checksums(self):
        (self.root / "sync/manifest.md").write_text(
            "| PDF | `library/papers/demo2026.pdf` | SHA-256 `" + "0" * 64 + "` |\n")
        self.assertEqual(1, self.run_cli("inventory")[0])
        self.assertFalse((self.root / "sync/assets.json").exists())
        code, out, _ = self.run_cli("doctor", "--verify", "--json")
        self.assertEqual(1, code)
        self.assertIn("legacy_checksum_mismatch", {item["code"] for item in json.loads(out)["issues"]})

    def test_import_rejects_duplicate_doi_invalid_pdf_and_bad_metadata_before_move(self):
        source = self.root / "library/inbox/new.pdf"
        source.write_bytes(b"%PDF-1.7\nnew\n")
        before = self.bib.read_bytes()
        for extra in (("--doi", "https://DOI.org/10.1234/DEMO"), ("--year", "yesterday"),
                      ("--title", ""), ("--title", "broken } braces"), ("--as", "../escape")):
            with self.subTest(extra=extra):
                self.assertEqual(1, self.run_cli(*self.import_args(source, *extra))[0])
                self.assertTrue(source.exists())
                self.assertEqual(before, self.bib.read_bytes())
        source.write_bytes(b"<html>not a PDF</html>")
        self.assertEqual(1, self.run_cli(*self.import_args(source))[0])
        self.assertTrue(source.exists())

    def test_import_write_failure_rolls_back_all_files_and_keeps_source(self):
        source = self.root / "library/inbox/new.pdf"
        source.write_bytes(b"%PDF-1.7\nnew\n")
        old_bib, old_index = self.bib.read_bytes(), self.index.read_bytes()
        real_replace = library.os.replace
        def fail_index(src, target):
            if target == self.index:
                raise OSError("simulated disk error")
            return real_replace(src, target)
        with patch.object(library.os, "replace", side_effect=fail_index):
            self.assertEqual(1, self.run_cli(*self.import_args(source))[0])
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "library/papers/second2026.pdf").exists())
        self.assertFalse((self.root / "sync/assets.json").exists())
        self.assertEqual(old_bib, self.bib.read_bytes())
        self.assertEqual(old_index, self.index.read_bytes())
        self.assertEqual([], list(self.index.parent.glob(".index.md.*")))

    def test_status_write_failure_restores_note(self):
        self.run_cli("note", "demo2026")
        note = self.root / "library/notes/demo2026.md"
        before = note.read_bytes()
        real_replace = library.os.replace
        def fail_index(src, target):
            if target == self.index:
                raise OSError("index unavailable")
            return real_replace(src, target)
        with patch.object(library.os, "replace", side_effect=fail_index):
            self.assertEqual(1, self.run_cli("status", "demo2026", "粗读")[0])
        self.assertEqual(before, note.read_bytes())

    def test_import_does_not_move_an_already_archived_paper(self):
        self.assertEqual(1, self.run_cli(*self.import_args(self.pdf))[0])
        self.assertTrue(self.pdf.exists())

    def test_doctor_reports_missing_pdf_orphans_index_drift_and_inbox(self):
        self.run_cli("note", "demo2026")
        self.pdf.unlink()
        (self.root / "library/notes/orphan.md").write_text("- 状态：未读\n")
        self.index.write_text(self.index.read_text().replace("| 未读 |", "| 粗读 |"))
        (self.root / "library/inbox/pending.pdf").write_bytes(b"%PDF-1.7\n")
        code, out, _ = self.run_cli("doctor", "--json")
        self.assertEqual(1, code)
        codes = {item["code"] for item in json.loads(out)["issues"]}
        self.assertTrue({"missing_pdf", "orphan_asset", "index_drift", "inbox"} <= codes)

    def test_doctor_warning_and_strict_exit_codes(self):
        self.assertEqual(0, self.run_cli("doctor")[0])
        self.assertEqual(1, self.run_cli("doctor", "--strict")[0])
        self.run_cli("note", "demo2026")
        self.run_cli("inventory")
        code, out, _ = self.run_cli("doctor", "--strict", "--verify", "--json")
        self.assertEqual(0, code)
        self.assertEqual([], json.loads(out)["issues"])

    def test_malformed_inventory_does_not_crash_or_overwrite_baseline(self):
        path = self.root / "sync/assets.json"
        path.write_text('{"schema_version":1,"papers":{"demo2026":null}}')
        before = path.read_bytes()
        self.assertEqual(1, self.run_cli("doctor", "--json")[0])
        self.assertEqual(1, self.run_cli("inventory")[0])
        self.assertEqual(before, path.read_bytes())

    def test_index_adds_new_entries_once_and_preserves_classification(self):
        self.bib.write_text(entry() + entry("second2026", "Title | with pipe", "10.1234/second"))
        self.assertEqual(1, self.run_cli("index", "--check")[0])
        self.assertNotIn("second2026", self.index.read_text())
        self.assertEqual(0, self.run_cli("index")[0])
        first = self.index.read_bytes()
        self.assertEqual(0, self.run_cli("index", "--check")[0])
        self.assertEqual(0, self.run_cli("index")[0])
        self.assertEqual(first, self.index.read_bytes())
        self.assertIn("人工分类", self.index.read_text())

    def test_index_removes_link_to_deleted_note(self):
        self.run_cli("note", "demo2026")
        (self.root / "library/notes/demo2026.md").unlink()
        self.assertEqual(0, self.run_cli("index")[0])
        self.assertNotIn("[精读卡]", self.index.read_text())
        self.assertIn("| 未建卡 |", self.index.read_text())

    def test_import_leaves_source_symlink_and_its_target_untouched(self):
        original = self.root / "original.pdf"
        original.write_bytes(b"%PDF-1.7\noriginal\n")
        source = self.root / "library/inbox/linked.pdf"
        source.symlink_to(original)
        self.assertEqual(1, self.run_cli(*self.import_args(source))[0])
        self.assertTrue(source.is_symlink())
        self.assertTrue(original.exists())

    def test_import_keeps_source_if_it_changes_during_commit(self):
        source = self.root / "library/inbox/new.pdf"
        source.write_bytes(b"%PDF-1.7\noriginal\n")
        write_files = library.write_files
        def change_source(updates):
            write_files(updates)
            source.write_bytes(b"%PDF-1.7\nchanged source content\n")
        with patch.object(library, "write_files", side_effect=change_source):
            code, _, err = self.run_cli(*self.import_args(source))
        self.assertEqual(0, code)
        self.assertIn("源文件发生变化", err)
        self.assertTrue(source.exists())
        self.assertEqual(b"%PDF-1.7\noriginal\n", (self.root / "library/papers/second2026.pdf").read_bytes())

    def test_read_commands_do_not_create_files(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, out, _ = self.run_cli("list", "--status", "未建卡", "--json")
        self.assertEqual(0, code)
        self.assertEqual("demo2026", json.loads(out)[0]["bibkey"])
        self.assertEqual(0, self.run_cli("overview", "--json")[0])
        self.assertEqual(0, self.run_cli("index", "--check")[0])
        self.assertEqual(entry().rstrip(), self.run_cli("bib", "demo2026")[1].rstrip())
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_writer_lock_rejects_second_writer_but_allows_reads(self):
        with library.writer_lock(self.root):
            self.assertEqual(1, self.run_cli("note", "demo2026")[0])
            self.assertEqual(0, self.run_cli("list")[0])

    def test_new_preserves_template_and_overview_reads_checkpoint(self):
        self.assertEqual(0, self.run_cli("new", "example", "--desc", "用户提供目标")[0])
        project = self.root / "projects/example"
        self.assertIn("## 闪卡", (project / "README.md").read_text())
        self.assertIn("sr card add example", (project / "README.md").read_text())
        (project / "state.md").write_text("# Project state\n\n## Status\n\n- 阶段：实验\n- 最后更新：2026-09-26\n\n## 下一步第一件事\n\n> 运行指定实验。\n\n## 未销账\n")
        row = json.loads(self.run_cli("overview", "--json")[1])["projects"][0]
        self.assertEqual("运行指定实验。", row["next_action"])
        self.assertEqual("实验", row["stage"])
        self.assertEqual(1, self.run_cli("new", "../escape")[0])

    def test_find_skips_binary_files_venvs_and_generated_large_text(self):
        (self.root / "good.md").write_text("unique-search-token")
        (self.root / ".venv").mkdir()
        (self.root / ".venv/hidden.py").write_text("unique-search-token")
        (self.root / "large.txt").write_text("unique-search-token" + "x" * (2 * 1024 * 1024))
        self.pdf.write_bytes(b"unique-search-token")
        self.assertEqual("good.md\n", self.run_cli("find", "unique-search-token")[1])

    def test_card_lookup_uses_exact_id_not_prefix(self):
        directory = self.root / "cards"
        directory.mkdir()
        base_id = "20260926-120000"
        (directory / f"{base_id}-2-first.md").write_text(f"id: {base_id}-2\n")
        target = directory / f"{base_id}-second.md"
        target.write_text(f"id: {base_id}\n")
        self.assertEqual(target, sr.card_file(directory, base_id))


if __name__ == "__main__":
    unittest.main()
