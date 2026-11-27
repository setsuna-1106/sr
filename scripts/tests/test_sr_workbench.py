"""Workbench guarantees exercised on isolated workspaces and external fixtures."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sr_workbench import Workbench, WorkbenchError, encode_text, serve
from sr_workbench_store import OperationStore, digest, legacy_board_update
from sr_board import Board
from sr_library import index_text


BOARD = """# 科研任务看板

| 待开工 | 进行中 | 等待 | 已完成 |
|---|---|---|---|
| [A-01](#a-01) · 【组内】核对资料 | — | — | — |
| [C-01](#c-01) · 【候选】尚未立项 | — | — | — |

## 卡片详情

### A-01

- 原有自由正文必须保留。
- 自定义字段：alpha
- [项目断点](alpha/state.md)

### C-01

- 候选评估尚未立项。
"""
STATE = """# Project state

## Status

- 阶段：设计
- 最后更新：2026-11-13

## 下一步第一件事

> 核对已有资料。

## 当前判断

保留本人判断。
"""


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "workspace with spaces"
        for name in ("projects/alpha", "projects/beta", "library/papers", "library/notes", "sync", "records"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for project in ("alpha", "beta"):
            (self.root / "projects" / project / "state.md").write_text(STATE.replace("阶段：设计", "阶段：暂停") if project == "beta" else STATE)
            (self.root / "projects" / project / "README.md").write_text(f"# {project}\n\n## 目标\n\n原始目标。\n")
            (self.root / "projects" / project / "journal.md").write_text("# 日志\n\n保留旧日志。\n")
        shutil.copytree(Path(__file__).resolve().parents[2] / "projects/_template", self.root / "projects/_template")
        (self.root / "projects/KANBAN.md").write_text(BOARD)
        (self.root / "projects/WEEKLY_PLAN.md").write_text("# 周计划\n\n## 本周 · 2026-11-13 至 2026-11-19\n")
        (self.root / "library/references.bib").write_text("")
        (self.root / "library/index.md").write_text("# 文献索引\n")
        self.worker = Workbench()
        self.worker.dispatch("workspace.open", {"path": str(self.root)})

    def write(self, method, **params):
        params.setdefault("revision", self.worker.snapshot()["revision"])
        params.setdefault("operation_id", uuid.uuid4().hex)
        return self.worker.dispatch(method, params)

    def project_id(self, name="alpha"):
        return next(row["id"] for row in self.worker.snapshot()["projects"] if row["name"] == name)

    def raw_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_open_is_byte_preserving_and_does_not_create_metadata(self):
        before = self.raw_files()
        result = self.worker.dispatch("workspace.open", {"path": str(self.root)})
        self.worker.dispatch("workspace.snapshot", {})
        self.assertEqual(before, self.raw_files())
        self.assertFalse((self.root / "workbench").exists())
        self.assertIsNone(next(row for row in result["tasks"] if row["id"] == "C-01")["project_id"])
        self.assertEqual(next(row for row in result["projects"] if row["name"] == "beta")["status"], "paused")

    def test_task_retry_and_legacy_roundtrip_preserve_notes_and_relations(self):
        project_id = self.project_id()
        notes = "一个说明\n\n来源：[纪要](../records/meeting.md)\n# 这不是卡片标题\n引用原话。"
        created = self.write("task.create", title="中文新任务", project_id=project_id, notes=notes, due_date="2026-11-20")
        task_id = created["result"]["id"]
        params = {"id": task_id, "changes": {"status": "doing", "pinned": True}, "operation_id": "retry-test", "revision": created["snapshot"]["revision"]}
        result = self.worker.dispatch("task.update", params)
        operation_path = self.root / "workbench/operations/retry-test.json"
        original = operation_path.read_bytes()
        retried = self.worker.dispatch("task.update", params)
        self.assertTrue(retried["replayed"])
        self.assertEqual(original, operation_path.read_bytes())
        legacy_board_update(self.root, Board(self.root).revision, task_id, {"status": "waiting", "blocker": "等待数据"})
        row = next(row for row in self.worker.snapshot()["tasks"] if row["id"] == task_id)
        self.assertEqual(row["notes"], notes)
        self.assertEqual(row["project_id"], project_id)
        self.assertEqual(row["due_date"], "2026-11-20")
        self.assertEqual(row["status"], "waiting")
        self.assertTrue(row["pinned"])
        self.assertIn("自定义字段：alpha", (self.root / "projects/KANBAN.md").read_text())
        with self.assertRaisesRegex(WorkbenchError, "不同的请求"):
            self.worker.dispatch("task.update", {**params, "changes": {"status": "done"}})
        self.assertEqual(result["operation_id"], "retry-test")

    def test_done_does_not_change_research_state_or_journal(self):
        paths = [self.root / "projects/alpha" / name for name in ("state.md", "journal.md")]
        before = [path.read_bytes() for path in paths]
        self.write("task.update", id="A-01", changes={"status": "done"})
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_reading_status_updates_card_and_index_and_rejects_stale_status(self):
        bib = self.root / "library/references.bib"
        original_bib = b"@article{sample, title={Sample title}, year={2026}}\n"
        bib.write_bytes(original_bib)
        note = self.root / "library/notes/sample.md"
        original_note = "\ufeff# sample\r\n\r\n- 状态： 未读  \r\n\r\n## 我的话（本人填写）\r\n保留本人原话。\n".encode()
        note.write_bytes(original_note)
        index = self.root / "library/index.md"
        index.write_text(index_text(self.root))
        params = {"bibkey": "sample", "expected_status": "未读", "status": "精读中", "operation_id": "reading-update"}
        result = self.write("paper.set_status", **params)
        self.assertEqual(result["snapshot"]["papers"][0]["status"], "精读中")
        self.assertEqual(note.read_bytes(), original_note.replace("未读".encode(), "精读中".encode()))
        self.assertEqual(bib.read_bytes(), original_bib)
        self.assertIn("| 精读中 |", index.read_text())
        self.assertEqual(index.read_text(), index_text(self.root))
        self.assertTrue(self.write("paper.set_status", **params)["replayed"])

        before = self.raw_files()
        with self.assertRaises(WorkbenchError) as caught:
            self.write("paper.set_status", bibkey="sample", expected_status="未读", status="粗读")
        self.assertEqual(caught.exception.code, "conflict")
        with self.assertRaisesRegex(ValueError, "有效的阅读状态"):
            self.write("paper.set_status", bibkey="sample", expected_status="精读中", status="未建卡")
        self.assertEqual(self.raw_files(), before)

    def test_setting_status_for_paper_without_note_can_recover_partial_save(self):
        (self.root / "library/references.bib").write_text(
            "@article{sample, title={Sample title}, year={2026}, doi={10.1234/sample}}\n")
        note = self.root / "library/notes/sample.md"
        index = self.root / "library/index.md"
        index.write_text(index_text(self.root))
        original_index = index.read_bytes()
        from sr_workbench_store import durable_write

        def fail_index(path, raw):
            if path == index:
                raise OSError("simulated interrupted index write")
            durable_write(path, raw)

        with patch("sr_workbench_store.durable_write", side_effect=fail_index):
            with self.assertRaisesRegex(OSError, "interrupted index write"):
                self.write("paper.set_status", bibkey="sample", expected_status="未建卡", status="粗读",
                           operation_id="reading-interrupted")
        self.assertIn("- 状态：粗读\n", note.read_text())
        self.assertEqual(index.read_bytes(), original_index)

        reopened = Workbench()
        snapshot = reopened.dispatch("workspace.open", {"path": str(self.root)})
        self.assertEqual([item["id"] for item in snapshot["recovery"]], ["reading-interrupted"])
        result = reopened.dispatch("workspace.recover", {"operation_id": "reading-interrupted"})
        self.assertEqual(result["results"][0]["status"], "committed")
        self.assertEqual(index.read_text(), index_text(self.root))
        self.assertIn("[精读卡](notes/sample.md)", index.read_text())
        self.assertIn("https://doi.org/10.1234/sample", note.read_text())
        self.assertIn("## 我的话（本人填写）\n\n## 我的推导（本人填写）\n\n## 卡点\n", note.read_text())

    def test_delete_single_card_preserves_shared_row_and_roundtrips_exact_bytes(self):
        path = self.root / "projects/KANBAN.md"
        raw = ("\ufeff" + BOARD.replace(" | — | — | — |\n| [C-01]", " |    [C-01]", 1)
               .replace("尚未立项 | — | — | — |", "尚未立项    | — | — |")
               + "\n## 手写附录\n保留此段。\n").replace("\n", "\r\n").encode()
        path.write_bytes(raw)
        params = dict(ids=["a-01"], operation_id="delete-single", revision=self.worker.snapshot()["revision"])
        result = self.write("task.delete", **params)
        self.assertTrue(self.write("task.delete", **params)["replayed"])
        self.assertEqual([t["id"] for t in result["snapshot"]["tasks"]], ["C-01"])
        self.assertIn("    [C-01](#c-01) · 【候选】尚未立项    ".encode(), path.read_bytes())
        self.assertNotIn(b"### A-01", path.read_bytes())
        self.assertTrue(path.read_bytes().endswith("## 手写附录\r\n保留此段。\r\n".encode()))
        self.write("trash.restore", id=result["result"]["trash_id"])
        self.assertEqual(raw, path.read_bytes())
        self.assertFalse(self.worker.snapshot()["trash"])

    def test_batch_delete_is_all_or_nothing_and_restore_merges_concurrent_content(self):
        path = self.root / "projects/KANBAN.md"
        revision = self.worker.snapshot()["revision"]
        self.write("task.update", id="A-01", changes={"status": "waiting", "pinned": True})
        before = path.read_bytes()
        with self.assertRaises(WorkbenchError):
            self.write("task.delete", ids=["A-01", "C-01"], revision=revision)
        with self.assertRaises(WorkbenchError):
            self.write("task.delete", ids=["A-01", "missing"])
        self.assertEqual(path.read_bytes(), before)
        result = self.write("task.delete", ids=["A-01", "C-01"])
        self.assertFalse(result["snapshot"]["tasks"])
        self.write("task.create", id="NEW", title="尚未立项")
        self.write("task.update", id="NEW", changes={"status": "doing"})
        path.write_bytes(path.read_bytes() + "\n## 外部笔记\n保留正文。\n".encode())
        snap = self.write("trash.restore", id=result["result"]["trash_id"])["snapshot"]
        self.assertEqual({t["id"]: t["status"] for t in snap["tasks"]}, {"A-01": "waiting", "C-01": "ready", "NEW": "doing"})
        self.assertIn("自定义字段：alpha", path.read_text())
        self.assertIn("## 外部笔记\n保留正文。", path.read_text())
        self.assertTrue(next(t for t in snap["tasks"] if t["id"] == "A-01")["pinned"])

    def test_delete_project_cascades_by_id_and_restores_assets_and_links(self):
        alpha, beta = self.project_id(), self.project_id("beta")
        folder = self.root / "projects/alpha"
        external = self.base / "shared.bin"
        external.write_bytes(b"external")
        (folder / "folder link").symlink_to(self.base, target_is_directory=True)
        (folder / "samples.bin").write_bytes(b"\x00\x01")
        marked = self.write("asset.link", path=str(folder / "samples.bin"), project_id=alpha)["result"]["id"]
        shared = self.write("asset.link", path=str(external), project_ids=[alpha, beta], task_ids=["A-01", "C-01"])["result"]["id"]
        record = self.write("record.create", title="共享纪要", project_ids=[alpha, beta], task_ids=["C-01", "A-01"], content="保留原件")["result"]
        milestone = self.write("milestone.create", project_id=alpha, title="第一阶段")["result"]["id"]
        self.write("task.update", id="A-01", changes={"milestone_id": milestone})
        self.write("project.update", id=alpha, changes={"name": "项目甲", "archived": True, "status": "paused"})
        result = self.write("project.delete", id=alpha)
        before = result["snapshot"]
        trash_id = result["result"]["trash_id"]
        self.assertFalse(folder.exists())
        self.assertEqual([p["id"] for p in before["projects"]], [beta])
        self.assertEqual([t["id"] for t in before["tasks"]], ["C-01"])
        self.assertFalse(before["milestones"])
        asset = next(a for a in before["assets"] if a["id"] == shared)
        self.assertEqual((asset["project_ids"], asset["task_ids"]), ([beta], ["C-01"]))
        self.assertEqual(before["records"][0]["task_ids"], ["C-01"])
        self.assertFalse(next(a for a in before["assets"] if a["id"] == marked)["available"])
        self.assertEqual((self.root / "workbench/trash" / trash_id / "samples.bin").read_bytes(), b"\x00\x01")
        self.assertEqual(external.read_bytes(), b"external")
        self.assertIn("保留原件", Path(record["path"]).read_text())
        self.write("asset.update", id=shared, changes={"purpose": "删除之后新写的用途"})
        snap = self.write("trash.restore", id=trash_id)["snapshot"]
        self.assertEqual((folder / "samples.bin").read_bytes(), b"\x00\x01")
        self.assertTrue((folder / "folder link").is_symlink())
        project = next(p for p in snap["projects"] if p["id"] == alpha)
        self.assertEqual((project["name"], project["archived"], project["status"]), ("项目甲", True, "paused"))
        self.assertEqual(next(t for t in snap["tasks"] if t["id"] == "A-01")["milestone_id"], milestone)
        asset = next(a for a in snap["assets"] if a["id"] == shared)
        self.assertEqual((asset["project_ids"], asset["task_ids"], asset["purpose"]), ([alpha, beta], ["A-01", "C-01"], "删除之后新写的用途"))
        self.assertEqual(snap["records"][0]["task_ids"], ["C-01", "A-01"])
        self.assertTrue(next(a for a in snap["assets"] if a["id"] == marked)["available"])

    def test_restore_dependencies_and_collisions_do_not_overwrite_new_folder_or_card(self):
        alpha = self.project_id()
        task = self.write("task.delete", ids=["A-01"])["result"]["trash_id"]
        project = self.write("project.delete", id=alpha)["result"]["trash_id"]
        with self.assertRaisesRegex(WorkbenchError, "先恢复"):
            self.write("trash.restore", id=task)
        folder = self.root / "projects/alpha"
        folder.mkdir()
        with self.assertRaisesRegex(WorkbenchError, "已存在"):
            self.write("trash.restore", id=project)
        self.assertEqual(list(folder.iterdir()), [])
        folder.rename(self.base / "new-folder")
        self.write("trash.restore", id=project)
        self.write("task.create", id="a-01", title="后来建立的卡")
        with self.assertRaisesRegex(WorkbenchError, "卡号"):
            self.write("trash.restore", id=task)
        snap = self.worker.snapshot()
        self.assertEqual(next(t["title"] for t in snap["tasks"] if t["id"] == "a-01"), "后来建立的卡")
        self.assertEqual([item["id"] for item in snap["trash"]], [task])

    def test_directory_move_and_partial_delete_resume_across_restart(self):
        import sr_workbench_store as store
        save = store.durable_write
        def interrupt(path, content):
            if path == self.root / "workbench/metadata.json":
                raise OSError("simulated power loss")
            save(path, content)
        params = dict(id=self.project_id(), operation_id="delete-dir", revision=self.worker.snapshot()["revision"])
        with patch.object(store, "durable_write", interrupt), self.assertRaises(OSError):
            self.write("project.delete", **params)
        self.worker = Workbench()
        state = self.worker.dispatch("workspace.open", {"path": str(self.root)})
        self.assertEqual([r["id"] for r in state["recovery"]], ["delete-dir"])
        keep = self.worker.dispatch("workspace.recover", {"operation_id": "delete-dir", "action": "keep_current"})
        self.assertEqual(keep["results"][0]["status"], "conflict")
        recovered = self.worker.dispatch("workspace.recover", {})
        self.assertEqual(recovered["results"][0]["status"], "committed")
        self.assertTrue(self.write("project.delete", **params)["replayed"])
        self.write("trash.restore", id="delete-dir")
        self.assertEqual(self.project_id(), params["id"])
        self.assertEqual((self.root / "projects/KANBAN.md").read_text(), BOARD)

    def test_trash_symlink_never_moves_project_outside_workspace(self):
        directory = self.root / "workbench"
        directory.mkdir()
        (directory / "trash").symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(WorkbenchError, "软链接"):
            self.write("project.delete", id=self.project_id(), operation_id="escape")
        self.assertTrue((self.root / "projects/alpha/state.md").is_file())
        self.assertFalse((self.base / "escape").exists())

    def test_committed_project_trash_can_be_restored_from_a_workspace_copy(self):
        alpha = self.project_id()
        trash_id = self.write("project.delete", id=alpha)["result"]["trash_id"]
        copied = self.base / "copied"
        shutil.copytree(self.root, copied)
        self.worker.dispatch("workspace.open", {"path": str(copied)})
        self.write("trash.restore", id=trash_id)
        self.assertEqual(self.project_id(), alpha)
        self.assertEqual((copied / "projects/alpha/state.md").read_text(), STATE)
        self.assertFalse((self.root / "projects/alpha").exists())

    def test_snapshot_revision_conflict_does_not_save(self):
        revision = self.worker.snapshot()["revision"]
        board = self.root / "projects/KANBAN.md"
        board.write_text(board.read_text() + "\n外部修改。\n")
        before = board.read_bytes()
        with self.assertRaises(WorkbenchError) as caught:
            self.write("task.update", id="A-01", changes={"status": "doing"}, revision=revision)
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(before, board.read_bytes())

    def test_project_from_template_and_goal_stage_next_are_kept(self):
        created = self.write("project.create", name="新的研究", goal="核对基线。", stage="界定问题", next_action="打开任务说明。")
        project = next(row for row in created["snapshot"]["projects"] if row["id"] == created["result"]["id"])
        self.assertEqual(project["goal"], "核对基线。")
        self.assertEqual(project["stage"], "界定问题")
        self.assertEqual(project["next_action"], "打开任务说明。")
        target = self.root / project["path"]
        self.assertTrue((target / "agents/README.md").is_file())
        self.assertTrue((target / "cards/README.md").is_file())
        self.assertIn("最大不确定性", (target / "state.md").read_text())
        self.write("project.update", id=project["id"], changes={"status": "paused", "archived": True})
        restored = self.write("project.update", id=project["id"], changes={"archived": False})
        self.assertEqual(next(row for row in restored["snapshot"]["projects"] if row["id"] == project["id"])["status"], "paused")

    def test_milestone_and_task_must_have_same_project(self):
        milestone = self.write("milestone.create", title="首次基线", project_id=self.project_id())["result"]["id"]
        with self.assertRaisesRegex(WorkbenchError, "同一项目"):
            self.write("task.create", title="错误归属", project_id=self.project_id("beta"), milestone_id=milestone)
        self.write("task.update", id="A-01", changes={"milestone_id": milestone})
        with self.assertRaisesRegex(WorkbenchError, "仍有关联任务"):
            self.write("milestone.update", id=milestone, changes={"project_id": self.project_id("beta")})

    def test_markdown_bom_crlf_blank_end_and_conflict(self):
        path = self.root / "records/中文.md"
        original = b"\xef\xbb\xbf" + "# 标题\r\n\r\n原内容。\r\n\r\n\r\n".encode()
        path.write_bytes(original)
        doc = self.worker.dispatch("files.read", {"path": str(path)})
        self.write("files.save", path=str(path), content=doc["content"].replace("原内容", "新内容").replace("\r\n", "\n"), expected_revision=doc["revision"])
        self.assertEqual(path.read_bytes(), original.replace("原内容".encode(), "新内容".encode()))
        before = path.read_bytes()
        with self.assertRaises(WorkbenchError) as caught:
            self.write("files.save", path=str(path), content="旧草稿", expected_revision=doc["revision"])
        self.assertEqual(caught.exception.code, "conflict")
        self.assertIn("新内容", caught.exception.details["content"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(encode_text("a\n\n", b"a\r\n\r\n"), b"a\r\n\r\n")

    def test_structural_and_protected_files_are_not_overwritten(self):
        path = self.root / "projects/KANBAN.md"
        doc = self.worker.dispatch("files.read", {"path": str(path)})
        with self.assertRaises(ValueError):
            self.write("files.save", path=str(path), content="# broken", expected_revision=doc["revision"])
        self.assertEqual(path.read_text(), BOARD)
        bib = self.root / "library/references.bib"
        with self.assertRaises(WorkbenchError):
            self.write("files.save", path=str(bib), content="arbitrary", expected_revision=digest(bib.read_bytes()))
        state = self.root / "projects/alpha/state.md"
        with self.assertRaises(WorkbenchError):
            self.write("files.save", path=str(state), content="# broken", expected_revision=digest(state.read_bytes()))

    def test_external_asset_scope_relocation_and_symlink_escape(self):
        external = self.base / "external"
        external.mkdir()
        (external / "数据.md").write_text("外部内容。")
        with self.assertRaises(WorkbenchError):
            self.worker.dispatch("files.read", {"path": str(external / "数据.md")})
        asset_id = self.write("asset.link", path=str(external), project_id=self.project_id(), kind="directory")["result"]["id"]
        self.assertIn("外部内容", self.worker.dispatch("files.read", {"path": str(external / "数据.md")})["content"])
        outside = self.base / "private.md"
        outside.write_text("不在范围")
        (external / "escape.md").symlink_to(outside)
        with self.assertRaises(WorkbenchError):
            self.worker.dispatch("files.read", {"path": str(external / "escape.md")})
        moved = self.base / "renamed"
        external.rename(moved)
        self.assertFalse(self.worker.snapshot()["assets"][0]["available"])
        self.write("asset.update", id=asset_id, changes={"path": str(moved), "featured": False})
        self.assertTrue((moved / "数据.md").exists())
        self.assertEqual(self.worker.snapshot()["assets"][0]["id"], asset_id)

    def test_record_body_single_source_journal_reference_and_search(self):
        result = self.write("record.create", title="组会记录", kind="meeting", task_id="A-01", content="师兄提供了参数范围。",
                            append_journal=True, summary="整理参数来源。")
        record = next(row for row in result["snapshot"]["records"] if row["id"] == result["result"]["id"])
        self.assertEqual(record["project_ids"], [self.project_id()])
        self.assertIn("师兄提供了参数范围", Path(record["path"]).read_text())
        journal = (self.root / "projects/alpha/journal.md").read_text()
        self.assertIn("整理参数来源", journal)
        self.assertNotIn("师兄提供了参数范围", journal)
        self.assertTrue(self.worker.dispatch("search", {"query": "参数范围"})["results"])
        updated = self.write("record.update", id=record["id"], changes={"project_ids": [], "task_ids": [], "asset_ids": []})
        self.assertEqual(next(row for row in updated["snapshot"]["records"] if row["id"] == record["id"])["task_ids"], [])

    def test_repeated_paper_link_merges_projects_and_keeps_central_metadata(self):
        bib = self.root / "library/references.bib"
        bib.write_text("@article{fixture2026,title={已核对标题},author={A. Author and B. Author},year={2026},doi={10.1234/fixture},journal={Journal}}\n")
        pdf = self.root / "library/papers/fixture2026.pdf"
        pdf.write_bytes(b"%PDF-1.7\nfixture")
        (self.root / "library/notes/fixture2026.md").write_text("# Fixture\n\n- 状态：未读\n")
        before = bib.read_bytes()
        first = self.write("asset.link", path=str(pdf), project_id=self.project_id(), kind="file")
        second = self.write("asset.link", path=str(pdf), project_id=self.project_id("beta"), kind="file")
        self.assertEqual(first["result"]["id"], second["result"]["id"])
        self.assertEqual(len(second["snapshot"]["assets"]), 1)
        paper = second["snapshot"]["papers"][0]
        self.assertEqual(set(paper["project_ids"]), {self.project_id(), self.project_id("beta")})
        self.assertEqual(paper["author"], "A. Author and B. Author")
        self.assertEqual(paper["year"], "2026")
        self.assertEqual(paper["doi"], "10.1234/fixture")
        self.assertEqual(before, bib.read_bytes())
        replaced = self.write("asset.update", id=first["result"]["id"], changes={"project_ids": [self.project_id("beta")]})
        self.assertEqual(replaced["snapshot"]["papers"][0]["project_ids"], [self.project_id("beta")])

    def test_meeting_task_has_structural_clickable_source_and_retains_quote(self):
        meeting = self.write("record.create", title="有空格的组会 来源", kind="meeting", project_id=self.project_id(), content="建议核对泵浦参数。")
        record_id = meeting["result"]["id"]
        path = Path(meeting["result"]["path"])
        notes = "来源文字：师兄说先核对泵浦参数。\n\n我的下一步尚待确定。"
        task = self.write("task.create", title="核对泵浦参数", source_record_id=record_id, project_id=None, notes=notes)
        row = next(item for item in task["snapshot"]["tasks"] if item["id"] == task["result"]["id"])
        self.assertEqual(row["project_id"], self.project_id())
        self.assertEqual(row["record_ids"], [record_id])
        self.assertEqual(row["notes"], notes)
        source = next(link for link in row["links"] if link.get("record_id") == record_id)
        self.assertEqual(source["path"], str(path))
        self.assertEqual(self.worker.dispatch("files.resolve", {"path": source["path"]})["path"], str(path))
        with self.assertRaises(WorkbenchError):
            self.write("task.create", title="无效来源", source_record_id="missing-record")
        # A later external symlink replacement must not become an actionable link.
        outside = self.base / "outside-secret.md"
        outside.write_text("outside")
        path.unlink()
        path.symlink_to(outside)
        fresh = next(item for item in self.worker.snapshot()["tasks"] if item["id"] == row["id"])
        self.assertEqual(fresh["record_ids"], [record_id])
        self.assertFalse(any(link.get("record_id") == record_id for link in fresh["links"]))

    def test_interrupted_commit_recovers_after_workspace_move(self):
        first, second = self.root / "records/one.md", self.root / "records/two.md"
        first.write_text("before one")
        second.write_text("before two")
        store = OperationStore(self.root)
        def stop(index, operation):
            if index == 0:
                raise RuntimeError("simulated process exit")
        with self.assertRaises(RuntimeError):
            store.commit("interrupted", "test", {}, {first: b"after one", second: b"after two"}, after_replace=stop)
        self.assertEqual(first.read_text(), "after one")
        self.assertEqual(second.read_text(), "before two")
        moved = self.base / "moved-workspace"
        self.root.rename(moved)
        self.worker.dispatch("workspace.open", {"path": str(moved)})
        result = self.worker.dispatch("workspace.recover", {})
        self.assertEqual(result["results"][0]["status"], "committed")
        self.assertEqual((moved / "records/two.md").read_text(), "after two")
        self.assertFalse(self.root.exists())
        self.assertEqual(self.worker.dispatch("operation.get", {"operation_id": "interrupted"})["status"], "committed")

    def test_recovery_never_overwrites_later_external_changes(self):
        first, second = self.root / "records/one.md", self.root / "records/two.md"
        first.write_text("before one")
        second.write_text("before two")
        store = OperationStore(self.root)
        def stop(*args):
            raise RuntimeError("simulated process exit")
        with self.assertRaises(RuntimeError):
            store.commit("conflicted", "test", {}, {first: b"after one", second: b"after two"}, after_replace=stop)
        second.write_text("later external edit")
        with self.assertRaises(WorkbenchError) as caught:
            self.write("task.update", id="A-01", changes={"status": "done"})
        self.assertEqual(caught.exception.code, "recovery_required")
        result = self.worker.dispatch("workspace.recover", {"operation_id": "conflicted"})
        self.assertEqual(result["results"][0]["status"], "conflict")
        self.assertEqual(second.read_text(), "later external edit")
        self.worker.dispatch("workspace.recover", {"operation_id": "conflicted", "action": "keep_current"})
        self.assertEqual(self.worker.snapshot()["recovery"], [])
        self.assertTrue((self.root / "workbench/operations/conflicted.json").is_file())

    def test_json_lines_protocol_survives_bad_request_and_eof(self):
        source = io.StringIO("not-json\n" + json.dumps({"id": 1, "method": "protocol.handshake", "params": {}}) + "\n" +
                             json.dumps({"id": 2, "method": "workspace.open", "params": {"path": str(self.root)}}) + "\n")
        target = io.StringIO()
        serve(source, target)
        rows = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertIn("error", rows[0])
        self.assertEqual(rows[1]["result"]["protocol_version"], 1)
        self.assertEqual(rows[2]["result"]["root"], str(self.root))


if __name__ == "__main__":
    unittest.main()
