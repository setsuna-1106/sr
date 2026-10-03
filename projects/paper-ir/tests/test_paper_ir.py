import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "paper_ir.py"
spec = importlib.util.spec_from_file_location("paper_ir", MODULE_PATH)
paper_ir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paper_ir)


class PaperIRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "paper.md"
        self.source.write_text("# Test\n\nThe method uses a reference sample.\n", encoding="utf-8")
        self.artifact = self.root / "paper.json"
        paper_ir.convert(SimpleNamespace(source=self.source, output=self.artifact,
                                         paper_id="test2026", title="Test"))

    def test_supported_item_and_source_trace(self):
        paper_ir.add(SimpleNamespace(artifact=self.artifact, kind="method", status="supported",
                                     text="Uses a reference sample", block="b0002",
                                     quote="uses a reference sample"))
        data = paper_ir.load_json(self.artifact)
        self.assertEqual([], paper_ir.validate_artifact(data, self.artifact))
        self.assertEqual("i0001", data["items"][0]["id"])

    def test_source_change_invalidates_artifact(self):
        self.source.write_text("# Test\n\nChanged text.\n", encoding="utf-8")
        self.assertTrue(any("SHA-256 mismatch" in error for error in
                            paper_ir.validate_artifact(paper_ir.load_json(self.artifact), self.artifact)))

    def test_false_quote_is_rejected_without_write(self):
        with self.assertRaisesRegex(ValueError, "quote not found"):
            paper_ir.add(SimpleNamespace(artifact=self.artifact, kind="method", status="supported",
                                         text="Fabricated", block="b0002", quote="not in paper"))
        self.assertEqual([], paper_ir.load_json(self.artifact)["items"])

    def test_tampered_block_is_rejected_even_with_updated_hash(self):
        data = json.loads(self.artifact.read_text(encoding="utf-8"))
        data["blocks"][1]["text"] = "A fake method."
        data["blocks"][1]["sha256"] = paper_ir.digest(b"A fake method.")
        self.assertTrue(any("fresh extraction" in error for error in
                            paper_ir.validate_artifact(data, self.artifact)))


if __name__ == "__main__":
    unittest.main()
