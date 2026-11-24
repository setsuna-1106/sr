"""Regression coverage for the Markdown board's file format."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sr_board import BOARD, WEEK, Board, document_html

EXAMPLE = """# 科研任务看板

| 待开工 | 进行中 | 等待 | 已完成 |
|---|---|---|---|
| [DEMO-01](#demo-01) · 【示例项目】核对传感器资料 | — | — | — |
| [DEMO-02](#demo-02) · 【示例项目】拟合曲线 | — | — | — |

## 卡片详情

### DEMO-01

- 核对任务说明。
- [项目断点](demo-project/state.md)

### DEMO-02

- 拟合一轮。
"""


class BoardTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / BOARD).parent.mkdir()
        (self.root / BOARD).write_text(EXAMPLE, encoding="utf-8")
        (self.root / WEEK).write_text(
            "## 首周 · 2026-11-16 至 2026-11-22\n"
            "| 主交付 · 核对资料 | [DEMO-01](KANBAN.md#demo-01) | 本周 |\n",
            encoding="utf-8",
        )

    def test_move_and_edit_preserve_markdown_links(self):
        board = Board(self.root)
        updated = board.update("DEMO-01", {"status": "waiting", "followup": "2026-11-19",
                                          "evidence": "../outputs/图 1.png", "blocker": "等待资料"})
        self.assertIn("| — | — | [DEMO-01](#demo-01) · 【示例项目】核对传感器资料 | — |", updated)
        self.assertIn("- 证据：[查看证据](<../outputs/图 1.png>)", updated)
        refreshed = Board(self.root, updated.encode())
        card = refreshed.cards["demo-01"]
        self.assertEqual(card["followup"], "2026-11-19")
        self.assertEqual(card["evidence"], "../outputs/图 1.png")
        self.assertEqual(card["links"], board.cards["demo-01"]["links"])
        self.assertEqual(refreshed.cards["demo-02"], board.cards["demo-02"])
        self.assertEqual(refreshed.snapshot()["week"]["priorities"], {"demo-01": "主交付"})
        self.assertIn('href="/#demo-01"', document_html(self.root, WEEK))
        self.assertEqual((self.root / BOARD).read_text(encoding="utf-8"), EXAMPLE)

    def test_invalid_or_cleared_fields_leave_other_details_intact(self):
        board = Board(self.root)
        with self.assertRaisesRegex(ValueError, "跟进日期"):
            board.update("demo-01", {"status": "waiting", "followup": "2026-11-31"})
        with self.assertRaisesRegex(ValueError, "仓库内路径"):
            board.update("demo-01", {"evidence": "../../outside.txt"})
        saved = Board(self.root, board.update("demo-01", {"evidence": "https://example.org/fit"}).encode())
        self.assertEqual(saved.update("demo-01", {"evidence": ""}), EXAMPLE)
