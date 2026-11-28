#!/usr/bin/env python3
"""Exercise the real JSON-lines worker against disposable, synthetic assets.

Run without arguments for the source worker; --worker tests a frozen executable.
--prepare-only creates a clearly labelled workspace for desktop interaction QA.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

BOARD = """# 验收任务看板（合成数据）

| 待开工 | 进行中 | 等待 | 已完成 |
|---|---|---|---|
| [TEST-01](#test-01) · 【验证项目】核对实验记录入口 | — | — | — |
| [IDEA-01](#idea-01) · 【候选】尚未正式立项的想法 | — | — | — |

## 卡片详情

### TEST-01

- [项目断点](verification/state.md)
- 自定义字段：旧工具与新界面都必须保留。

这里是原有自由正文，不得因改状态消失。

### IDEA-01

- 候选事项不能因为进入工作台就自动立项。
"""


def prepare(path: Path) -> Path:
    for name in ("library/papers", "library/notes", "library/inbox", "sync",
                 "projects/verification", "outputs/drafts", "outputs/releases"):
        (path / name).mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "projects/_template", path / "projects/_template")
    for name in ("PROFILE.md", "lessons.md", "sync/manifest.md"):
        (path / name).write_text("# 界面与协议验收工作区\n\n仅含合成测试资料。\n")
    (path / "library/references.bib").write_text(
        "@article{qa_existing, title={Synthetic paper with a reading note}, author={QA Author}, year={2026}}\n"
        "@article{qa_new, title={Synthetic paper without a reading note}, year={2026}}\n")
    (path / "library/notes/qa_existing.md").write_text("# qa_existing\n\n- 状态：未读\n\n保留测试正文。\n")
    (path / "library/index.md").write_text("# 测试文献索引\n")
    (path / "projects/KANBAN.md").write_text(BOARD)
    (path / "projects/WEEKLY_PLAN.md").write_text("# 验收排期\n")
    (path / "projects/verification/README.md").write_text("# 验证项目\n\n这是软件验收数据。\n")
    (path / "projects/verification/state.md").write_text(
        "# Project state\n\n## Status\n\n- 阶段：界面与协议验证\n"
        "- 最后更新：2026-11-13\n\n## 下一步第一件事\n\n> 检查工作台的原文件编辑。\n\n## 未销账\n\n- [ ] 检查保存\n")
    (path / "projects/verification/journal.md").write_text("# Project journal\n")
    # A transparent 1px PNG is enough to exercise relative images and base64 framing.
    (path / "outputs/结果图.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII="))
    (path / "projects/verification/测试笔记.md").write_bytes(
        b"\xef\xbb\xbf" + ("---\r\ncustom: preserved\r\n---\r\n# 中文记录\r\n\r\n"
        "未修改的内容。\r\n\r\n$$ E = mc^2 $$\r\n\r\n![结果](../../outputs/结果图.png)\r\n").encode())
    return path


class Client:
    def __init__(self, command: list[str]):
        self.logs = tempfile.TemporaryFile(mode="w+b")
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.logs, cwd="/", env={**os.environ, "PATH": "/usr/bin:/bin"})
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self.serial = 0

    def call(self, method: str, params: dict | None = None, error: str | None = None):
        self.serial += 1
        packet = {"id": self.serial, "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(packet, ensure_ascii=False) + "\n").encode())
        self.proc.stdin.flush()
        if not self.selector.select(20):
            raise AssertionError(f"worker timeout at {method}")
        line = self.proc.stdout.readline()
        if not line:
            self.logs.seek(0)
            raise AssertionError(f"worker exited at {method}: {self.logs.read().decode(errors='replace')}")
        result = json.loads(line)
        assert result.get("id") == self.serial, result
        if error:
            assert result.get("error", {}).get("code") == error, result
            return result["error"]
        assert "error" not in result, result
        return result["result"]

    def stop(self):
        self.proc.stdin.close()
        try:
            assert self.proc.wait(timeout=5) == 0, "worker must exit cleanly on stdin EOF"
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self.selector.close()
            self.proc.stdout.close()
            self.logs.close()


def hashes(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def exercise(command: list[str], root: Path):
    from sr_board import BOARD as BOARD_PATH, Board, BoardHandler
    client = Client(command)
    try:
        assert client.call("protocol.handshake")["protocol_version"] == 1
        before = hashes(root)
        snap = client.call("workspace.open", {"path": str(root)})
        client.call("workspace.snapshot")
        assert client.call("paper.citations") == {"edges": [], "coverage": {"qa_existing": "no_doi", "qa_new": "no_doi"},
                                                   "progress": None}
        assert before == hashes(root), "read-only opening changed source files"
        assert len(snap["projects"]) == 1 and len(snap["tasks"]) == 2, snap
        project_id = snap["projects"][0]["id"]
        first = next(t for t in snap["tasks"] if t["id"].upper() == "TEST-01")
        assert first["project_id"] == project_id, first
        candidate = next(t for t in snap["tasks"] if t["id"].upper() == "IDEA-01")
        assert candidate["project_id"] is None, candidate
        op = str(uuid.uuid4())
        update = {"id": first["id"], "changes": {"status": "doing", "pinned": True},
                  "revision": snap["revision"], "operation_id": op}
        client.call("task.update", update)
        # A lost response may cause an identical retry with the original revision.
        client.call("task.update", update)
        assert "原有自由正文" in (root / BOARD_PATH).read_text()
        assert "自定义字段" in (root / BOARD_PATH).read_text()
        assert Board(root).cards["test-01"]["status"] == "doing"
        print("PASS read-only import, inferred project/candidate separation, persisted idempotent task update")

        papers = {row["bibkey"]: row for row in snap["papers"]}
        assert papers["qa_existing"]["status"] == "未读" and papers["qa_new"]["status"] == "未建卡"
        status_edit = {"bibkey": "qa_existing", "expected_status": "未读", "status": "精读中",
                       "revision": client.call("workspace.snapshot")["revision"], "operation_id": str(uuid.uuid4())}
        changed = client.call("paper.set_status", status_edit)["snapshot"]
        assert changed["papers"][0]["status"] == "精读中"
        assert (root / "library/notes/qa_existing.md").read_text().endswith("保留测试正文。\n")
        assert "| 精读中 |" in (root / "library/index.md").read_text()
        print("PASS persisted reading status and synchronized index through worker protocol")

        server = ThreadingHTTPServer(("127.0.0.1", 0), BoardHandler)
        server.root = root
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = {"id": "TEST-01", "revision": Board(root).revision,
                       "changes": {"status": "waiting", "blocker": "等待测试材料"}}
            req = Request(f"http://127.0.0.1:{server.server_port}/api/board",
                          data=json.dumps(payload).encode(), method="PATCH",
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as response:
                assert response.status == 200
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        snap = client.call("workspace.snapshot")
        task = next(t for t in snap["tasks"] if t["id"].upper() == "TEST-01")
        assert task["status"] == "waiting" and task["pinned"], task
        client.call("task.update", {"id": task["id"], "changes": {"status": "done"},
             "revision": snap["revision"], "operation_id": str(uuid.uuid4())})
        assert "自定义字段" in (root / BOARD_PATH).read_text()
        print("PASS desktop → legacy HTTP board → desktop roundtrip")

        rel = "projects/verification/测试笔记.md"
        doc = client.call("files.read", {"path": rel})
        preview = client.call("files.preview", {"path": "outputs/结果图.png"})
        assert preview["data_url"].startswith("data:image/png;base64,")
        client.call("files.save", {"path": rel, "content": doc["content"].replace("未修改的内容。", "保存后的中文内容。"),
            "expected_revision": doc["revision"], "operation_id": str(uuid.uuid4())})
        raw = (root / rel).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
        assert b"custom: preserved" in raw
        doc = client.call("files.read", {"path": rel})
        external = raw + "\r\n外部编辑器添加。\r\n".encode()
        (root / rel).write_bytes(external)
        client.call("files.save", {"path": rel, "content": doc["content"] + "应用草稿", "expected_revision": doc["revision"],
                    "operation_id": str(uuid.uuid4())}, error="conflict")
        assert (root / rel).read_bytes() == external
        board = client.call("files.read", {"path": "projects/KANBAN.md"})
        old = (root / BOARD_PATH).read_bytes()
        client.call("files.save", {"path": "projects/KANBAN.md", "content": "# 损坏表格\n", "expected_revision": board["revision"],
                    "operation_id": str(uuid.uuid4())}, error="invalid")
        assert (root / BOARD_PATH).read_bytes() == old
        client.call("files.read", {"path": "/etc/passwd"}, error="forbidden")
        print("PASS Markdown BOM/CRLF, external conflict, invalid structured write, path boundary")
    finally:
        client.stop()
    reopened = Client(command)
    try:
        snap = reopened.call("workspace.open", {"path": str(root)})
        assert snap["papers"][0]["status"] == "精读中"
        task = next(t for t in snap["tasks"] if t["id"].upper() == "TEST-01")
        assert task["status"] == "done" and task["pinned"]
        saved_board = (root / BOARD_PATH).read_bytes()
        remove = {"id": project_id, "revision": snap["revision"], "operation_id": str(uuid.uuid4())}
        deleted = reopened.call("project.delete", remove)
        assert reopened.call("project.delete", remove)["replayed"]
        assert [t["id"] for t in deleted["snapshot"]["tasks"]] == ["IDEA-01"]
        assert not (root / "projects/verification").exists()
        restored = reopened.call("trash.restore", {"id": remove["operation_id"], "revision": deleted["snapshot"]["revision"],
                                                   "operation_id": str(uuid.uuid4())})["snapshot"]
        assert not restored["trash"] and restored["projects"][0]["id"] == project_id
        assert (root / BOARD_PATH).read_bytes() == saved_board and (root / rel).read_bytes() == external
        print("PASS project trash, original files and card metadata restored by packaged worker")
    finally:
        reopened.stop()
    print("PASS restart persistence and clean EOF exit from arbitrary cwd/PATH")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="sr-workbench-qa-"))
    try:
        prepare(root)
        if args.prepare_only:
            print(root)
            return
        command = [str(args.worker.resolve())] if args.worker else [sys.executable, str(ROOT / "scripts/sr_workbench.py")]
        exercise(command, root)
        print("All worker integration checks passed on synthetic assets.")
    finally:
        if not args.prepare_only:
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
