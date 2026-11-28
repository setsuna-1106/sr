#!/usr/bin/env python3
"""Local research workbench service. JSON Lines on stdin/stdout; no model calls."""
import base64
from copy import deepcopy
from datetime import date
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sys
import traceback
import uuid

import sr_board
import sr_library as library
from sr_workbench_citations import CitationNetwork
from sr_workbench_trash import delete_items, restore_items
from sr_workbench_store import OperationStore, WorkbenchError, digest, json_bytes, raw_at, stamp

PROTOCOL_VERSION = 1
META = Path("workbench/metadata.json")
MAX_TEXT = 4 * 1024 * 1024
PROJECT_STATUSES = {"active", "paused", "completed"}
MILESTONE_STATUSES = {"todo", "doing", "done"}
RECORD_KINDS = {"note", "meeting", "experiment", "simulation", "fit", "analysis", "reading"}


def identifier(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


def stable_id(prefix, path):
    return prefix + "-" + uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex[:12]


def single_line(value, label="文本", empty=False):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x1f\x7f]", value):
        raise WorkbenchError("invalid", f"{label}应为不超过 2048 字符的单行文本")
    if not empty and not value.strip():
        raise WorkbenchError("invalid", f"{label}不能为空")
    return value.strip()


def check_date(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise WorkbenchError("invalid", "日期须为 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise WorkbenchError("invalid", "日期无效") from exc
    return value


def decode_text(raw):
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WorkbenchError("unsupported", "当前编辑器只支持 UTF-8 文本") from exc


def encode_text(content, original=b""):
    if not isinstance(content, str) or "\x00" in content or len(content.encode("utf-8")) > MAX_TEXT:
        raise WorkbenchError("invalid", "文本无效或超过 4 MB 编辑限制")
    old = decode_text(original)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if normalized == old.replace("\r\n", "\n").replace("\r", "\n"):
        return original
    newline = "\r\n" if "\r\n" in old else "\n"
    # Preserve the editor's logical ending, including multiple trailing blank lines.
    return (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + normalized.replace("\n", newline).encode("utf-8")


def replace_heading(text, heading, body):
    pattern = rf"(^## {re.escape(heading)}[^\n]*\n)(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, re.M | re.S)
    replacement = "\n" + body.strip() + "\n\n"
    if match:
        return text[:match.start(2)] + replacement + text[match.end(2):]
    return text.rstrip() + f"\n\n## {heading}\n" + replacement


def replace_field(text, field, value):
    pattern = rf"^- {re.escape(field)}：[^\n]*$"
    if re.search(pattern, text, re.M):
        return re.sub(pattern, lambda _: f"- {field}：{value}", text, count=1, flags=re.M)
    match = re.search(r"^## Status\s*\n", text, re.M)
    if match:
        return text[:match.end()] + f"\n- {field}：{value}\n" + text[match.end():]
    return text.rstrip() + f"\n\n- {field}：{value}\n"


NOTES_START = "<!-- sr-workbench:notes -->"
NOTES_END = "<!-- /sr-workbench:notes -->"


def task_notes(board, key):
    start, end = board.sections[key]
    section = "\n".join(board.lines[start:end])
    match = re.search(re.escape(NOTES_START) + r"\n(.*?)\n" + re.escape(NOTES_END), section, re.S)
    if match:
        return "\n".join(line[2:] if line.startswith("> ") else line for line in match[1].split("\n"))
    return "\n".join(board.cards[key]["notes"])


def write_task_notes(board, key, content):
    if not isinstance(content, str) or len(content) > 30000 or "\x00" in content:
        raise WorkbenchError("invalid", "任务说明无效或超过 30000 字符")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    block = NOTES_START + "\n" + "\n".join("> " + line for line in content.split("\n")) + "\n" + NOTES_END
    lines = board.lines[:]
    start, end = board.sections[key]
    section = "\n".join(lines[start:end])
    pattern = re.escape(NOTES_START) + r"\n.*?\n" + re.escape(NOTES_END)
    if re.search(pattern, section, re.S):
        section = re.sub(pattern, lambda _: block, section, count=1, flags=re.S)
    else:
        section = section.rstrip() + "\n\n" + block + "\n"
    lines[start:end] = section.split("\n")
    return sr_board.Board(board.root, encode_text(board.newline.join(lines) + (board.newline if board.trailing_newline else ""), board.raw))


class Workbench:
    def __init__(self):
        self.root = None
        self.citations = CitationNetwork()

    def require_open(self):
        if self.root is None:
            raise WorkbenchError("workspace_required", "请先选择科研工作区")

    def metadata(self):
        self.require_open()
        path = self.root / META
        if path.is_symlink() or (self.root / "workbench").is_symlink():
            raise WorkbenchError("forbidden", "工作台元数据不能位于软链接")
        raw = raw_at(path)
        value = json.loads(raw) if raw is not None else {"schema_version": 1}
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkbenchError("invalid", "工作台元数据版本无效")
        for key in ("projects", "tasks", "milestones", "assets", "records", "trash"):
            value.setdefault(key, {})
            if not isinstance(value[key], dict) or any(not isinstance(item, dict) for item in value[key].values()):
                raise WorkbenchError("invalid", f"元数据 {key} 应为对象映射")
        return value

    def store(self):
        return OperationStore(self.root, self.authorize_write)

    def roots(self, meta=None):
        meta = meta or self.metadata()
        result = [{"path": str(self.root), "kind": "directory", "title": "科研工作区"}]
        for item in meta["assets"].values():
            if item.get("path"):
                path = self.stored_path(item["path"])
                if path.is_relative_to(self.root / "workbench/trash"):
                    continue
                result.append({"path": str(path), "kind": item.get("kind", "file"), "title": item.get("title", path.name)})
        return result

    def stored_path(self, value):
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.root / path

    def store_path(self, path):
        path = Path(path).resolve()
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def resolve(self, value, exists=True, internal=False):
        self.require_open()
        if not isinstance(value, str) or "\x00" in value:
            raise WorkbenchError("invalid", "文件路径无效")
        path = self.stored_path(value or ".").resolve()
        permitted = any(path == Path(item["path"]).resolve() or
                        (item["kind"] == "directory" and path.is_relative_to(Path(item["path"]).resolve()))
                        for item in self.roots())
        if not permitted:
            raise WorkbenchError("forbidden", "路径不在工作区或已关联目录中")
        if path.is_relative_to(self.root):
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts) or (relative.parts and relative.parts[0] == "workbench" and not internal):
                raise WorkbenchError("forbidden", "内部管理文件不通过普通文件接口访问")
        if exists and not path.exists():
            raise WorkbenchError("not_found", "文件暂不可访问", {"path": str(path)})
        return path

    def authorize_write(self, path):
        path = Path(path)
        resolved = path.resolve()
        if resolved == (self.root / META).resolve():
            if path.is_symlink() or (self.root / "workbench").is_symlink():
                raise WorkbenchError("forbidden", "元数据路径不能为软链接")
            return
        self.resolve(str(path), exists=False)
        if resolved.suffix.lower() != ".md":
            raise WorkbenchError("forbidden", "工作台只写入 Markdown 记录和专用元数据")

    def board(self):
        path = self.root / sr_board.BOARD
        if path.exists():
            return sr_board.Board(self.root)
        raw = "# 科研任务看板\n\n| 待开工 | 进行中 | 等待 | 已完成 |\n|---|---|---|---|\n\n## 卡片详情\n".encode()
        return sr_board.Board(self.root, raw)

    def projects(self, meta):
        result = []
        managed_paths = {item.get("path"): (key, item) for key, item in meta["projects"].items()}
        for path in sorted((self.root / "projects").glob("*")):
            if not path.is_dir() or path.is_symlink() or path.name.startswith(("_", ".")):
                continue
            state_path = path / "state.md"
            if not state_path.is_file() or state_path.is_symlink():
                continue
            state = decode_text(state_path.read_bytes())
            relative = path.relative_to(self.root).as_posix()
            key, item = managed_paths.get(relative, (stable_id("project", relative), {}))
            def field(label):
                match = re.search(rf"^- {label}：[ \t]*(.*)$", state, re.M)
                return match[1].strip() if match else ""
            section = re.search(r"^## 下一步第一件事\s*\n(.*?)(?=^## |\Z)", state, re.M | re.S)
            next_action = "\n".join(line.lstrip("> ").strip() for line in section[1].splitlines()).strip() if section else ""
            stage = field("阶段")
            default_status = "paused" if "暂停" in stage else "active"
            readme = path / "README.md"
            readme_text = decode_text(readme.read_bytes()) if readme.is_file() else ""
            title = re.search(r"^# (.+)$", readme_text, re.M)
            goal_section = re.search(r"^## 目标\s*\n(.*?)(?=^## |\Z)", readme_text, re.M | re.S)
            name = item.get("name") or (title[1] if title and title[1] != "Project" else path.name)
            result.append({**item, "id": key, "path": relative, "name": name, "stage": stage,
                           "status": item.get("status", default_status), "archived": bool(item.get("archived", False)),
                           "next_action": next_action, "goal": goal_section[1].strip() if goal_section else "", "updated": field("最后更新"),
                           "state_path": str(state_path), "journal_path": str(path / "journal.md")})
        return result

    def snapshot(self):
        meta = self.metadata()
        projects = self.projects(meta)
        board = self.board()
        by_path = {row["path"] + "/state.md": row["id"] for row in projects}
        tasks = []
        for key, card in board.cards.items():
            extra = meta["tasks"].get(key, {})
            inferred = {by_path.get(link["href"].removeprefix("/file/")) for link in card["links"]}
            inferred.discard(None)
            project_id = extra.get("project_id") if "project_id" in extra else next(iter(inferred)) if len(inferred) == 1 else None
            task = {**{k: v for k, v in card.items() if k != "cell"}, **extra, "id": card["id"],
                    "title": card["title"], "status": card["status"], "project_id": project_id,
                    "milestone_id": extra.get("milestone_id"), "pinned": bool(extra.get("pinned", False)),
                    "due_date": card.get("due_date", ""), "completion": card.get("completion", ""),
                    "notes": task_notes(board, key),
                    "detail_notes": card["notes"],
                    "source_path": str(self.root / sr_board.BOARD)}
            tasks.append(task)
        papers = []
        if (self.root / "library/references.bib").exists():
            mapping_path = self.root / "sync/zotero.json"
            try:
                mapping = json.loads(mapping_path.read_text()).get("papers", {}) if mapping_path.exists() else {}
            except (OSError, ValueError):
                mapping = {}
            bibliography = library.bibliography(self.root)
            for row in library.paper_rows(self.root):
                pdf = self.root / "library/papers" / (row["bibkey"] + ".pdf")
                note = self.root / "library/notes" / (row["bibkey"] + ".md")
                keys = mapping.get(row["bibkey"], {}).get("pdf_keys", [])
                fields = bibliography[row["bibkey"]]["fields"]
                project_ids = list(dict.fromkeys(project_id for item in meta["assets"].values()
                                                 if self.stored_path(item["path"]).resolve() == pdf.resolve()
                                                 for project_id in item.get("project_ids", [])))
                papers.append({**row, "id": row["bibkey"], "author": fields.get("author", ""),
                               "year": fields.get("year", ""), "doi": fields.get("doi", ""),
                               "journal": fields.get("journal", ""), "project_ids": project_ids,
                               "pdf_path": str(pdf), "note_path": str(note) if note.exists() else None,
                               "zotero_url": "zotero://open-pdf/library/items/" + keys[0] if len(keys) == 1 else None})
        assets = [{**item, "id": key, "path": str(self.stored_path(item["path"])),
                   "available": not self.stored_path(item["path"]).is_relative_to(self.root / "workbench/trash")
                                and self.stored_path(item["path"]).exists()} for key, item in meta["assets"].items()]
        records = [{**item, "id": key, "path": str(self.stored_path(item["path"])),
                    "available": not self.stored_path(item["path"]).is_relative_to(self.root / "workbench/trash")
                                 and self.stored_path(item["path"]).is_file()} for key, item in meta["records"].items()]
        known = {row["path"] for row in records}
        for folder, kind in (("records", "note"), ("outputs/reading/zotero/sessions", "reading"), ("outputs/meetings", "meeting")):
            directory = self.root / folder
            if directory.is_dir() and not directory.is_symlink():
                for path in sorted(directory.rglob("*.md")):
                    if path.is_symlink() or not path.resolve().is_relative_to(self.root) or str(path) in known:
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    records.append({"id": stable_id("record", relative), "path": str(path), "title": path.stem,
                                    "kind": kind, "project_ids": [], "task_ids": [], "available": True, "discovered": True})
        records_by_id = {row["id"]: row for row in records}
        for task in tasks:
            for record_id in task.get("record_ids", []):
                record = records_by_id.get(record_id)
                if not record:
                    continue
                try:
                    path = self.resolve(record["path"])
                    if path.is_file():
                        task["links"].append({"label": "来源记录：" + record["title"], "path": str(path), "record_id": record_id})
                except (OSError, WorkbenchError):
                    # Keep the relationship even when unavailable, but never return
                    # an out-of-scope path as an actionable source link.
                    pass
        version = hashlib.sha256()
        version_paths = [self.root / META, self.root / sr_board.BOARD, self.root / "library/references.bib"]
        for project in projects:
            version_paths.extend([Path(project["state_path"]), self.root / project["path"] / "README.md"])
        for path in sorted(version_paths):
            version.update(str(path.relative_to(self.root)).encode())
            version.update(raw_at(path) or b"")
        return {"root": str(self.root), "revision": version.hexdigest(), "metadata_revision": digest(raw_at(self.root / META)),
                "board_revision": board.revision if (self.root / sr_board.BOARD).exists() else None,
                "trash": [{"id": key, "kind": item["kind"], "title": item["title"], "deleted_at": item["deleted_at"],
                           "task_ids": item["task_ids"], "milestone_count": len(item["milestone_ids"])}
                          for key, item in sorted(meta["trash"].items(), key=lambda pair: pair[1]["deleted_at"], reverse=True)],
                "projects": projects, "tasks": tasks, "milestones": [{**value, "id": key} for key, value in meta["milestones"].items()],
                "assets": assets, "records": records, "papers": papers, "recovery": self.store().pending()}

    def validate_markdown(self, path, content):
        relative = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else ""
        text = decode_text(content)
        if relative == sr_board.BOARD.as_posix():
            sr_board.Board(self.root, content)
        elif relative.startswith("library/notes/"):
            library.note_status(text)
        elif re.fullmatch(r"projects/[^/]+/state.md", relative):
            for heading in ("Status", "下一步第一件事"):
                if len(re.findall(rf"^## {heading}\s*$", text, re.M)) != 1:
                    raise WorkbenchError("invalid", f"项目断点需保留唯一的「{heading}」章节")
            for field in ("阶段", "最后更新"):
                if len(re.findall(rf"^- {field}：.*$", text, re.M)) != 1:
                    raise WorkbenchError("invalid", f"项目断点需保留唯一的「{field}」字段")

    def file_read(self, params):
        path = self.resolve(params.get("path", ""))
        if not path.is_file():
            raise WorkbenchError("invalid", "请选择文件")
        if path.stat().st_size > MAX_TEXT:
            raise WorkbenchError("too_large", "文件超过 4 MB 文本预览限制")
        raw = path.read_bytes()
        content = decode_text(raw)
        return {"path": str(path), "content": content, "revision": digest(raw), "editable": path.suffix.lower() == ".md",
                "encoding": "utf-8-bom" if raw.startswith(b"\xef\xbb\xbf") else "utf-8",
                "newline": "crlf" if b"\r\n" in raw else "lf"}

    def file_list(self, params):
        path = self.resolve(params.get("path", ""))
        if not path.is_dir():
            raise WorkbenchError("invalid", "请选择目录")
        hidden = {"node_modules", "__pycache__", "target", "venv", "workbench"}
        entries = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            if not params.get("show_hidden") and (child.name.startswith(".") or child.name in hidden):
                continue
            try:
                target = self.resolve(str(child))
                entries.append({"name": child.name, "path": str(child), "kind": "directory" if target.is_dir() else "file",
                                "size": target.stat().st_size if target.is_file() else None, "available": True, "symlink": child.is_symlink()})
            except (OSError, WorkbenchError):
                entries.append({"name": child.name, "path": str(child), "kind": "symlink" if child.is_symlink() else "file",
                                "available": False, "blocked": True})
            if len(entries) >= 2000:
                break
        return {"path": str(path), "entries": entries, "roots": self.roots(), "limited": len(entries) == 2000}

    def find_project(self, snapshot, value, optional=False):
        if value in (None, "") and optional:
            return None
        row = next((row for row in snapshot["projects"] if row["id"] == value or row["path"] == value), None)
        if row is None:
            raise WorkbenchError("not_found", "项目不存在")
        return row

    def check_relations(self, data, snapshot, meta):
        for field, available in (("project_ids", {row["id"] for row in snapshot["projects"]}),
                                 ("task_ids", {row["id"] for row in snapshot["tasks"]}),
                                 ("milestone_ids", set(meta["milestones"])), ("asset_ids", set(meta["assets"])),
                                 ("record_ids", {row["id"] for row in snapshot["records"]})):
            if field not in data:
                continue
            value = data[field]
            if not isinstance(value, list) or any(not isinstance(item, str) or item not in available for item in value):
                raise WorkbenchError("invalid", f"{field} 含不存在的关联")
            data[field] = list(dict.fromkeys(value))

    def edit_task(self, method, params, meta, snapshot, updates):
        create = method == "task.create"
        board = self.board()
        changes = dict(params) if create else deepcopy(params.get("changes", {}))
        for name in ("operation_id", "revision", "id"):
            changes.pop(name, None)
        allowed = {"title", "status", "project_id", "milestone_id", "pinned", "order", "due_date", "followup", "blocker", "evidence", "completion", "notes", "asset_ids", "record_ids", "archived", "source_record_id"}
        if set(changes) - allowed:
            raise WorkbenchError("invalid", "未知任务字段", {"fields": sorted(set(changes) - allowed)})
        if create:
            task_id = params.get("id") or identifier("T")
            if not re.fullmatch(sr_board.ID, task_id) or task_id.lower() in board.cards:
                raise WorkbenchError("invalid", "任务 ID 无效或已存在")
            current = {"id": task_id, "project_id": None, "milestone_id": None}
        else:
            current = next((row for row in snapshot["tasks"] if row["id"].lower() == str(params.get("id", "")).lower()), None)
            if not current:
                raise WorkbenchError("not_found", "任务不存在")
            task_id = current["id"]
        source_record = None
        if changes.get("source_record_id"):
            source_record = next((row for row in snapshot["records"] if row["id"] == changes["source_record_id"]), None)
            if not source_record:
                raise WorkbenchError("not_found", "来源记录不存在")
            source_path = self.resolve(source_record["path"])
            if not source_path.is_file():
                raise WorkbenchError("not_found", "来源记录暂不可访问")
        project_id = changes.get("project_id", current.get("project_id")) or None
        if create and not project_id and source_record and len(source_record.get("project_ids", [])) == 1:
            project_id = source_record["project_ids"][0]
        project = self.find_project(snapshot, project_id, optional=True)
        project_id = project["id"] if project else None
        milestone_id = changes.get("milestone_id", current.get("milestone_id")) or None
        if milestone_id:
            milestone = meta["milestones"].get(milestone_id)
            if not milestone or milestone.get("project_id") != project_id:
                raise WorkbenchError("invalid", "任务里程碑必须属于同一项目；更换项目时请同时选择或清除里程碑")
        if "pinned" in changes and not isinstance(changes["pinned"], bool):
            raise WorkbenchError("invalid", "置顶应为布尔值")
        if "order" in changes and not isinstance(changes["order"], (float, int)):
            raise WorkbenchError("invalid", "顺序应为数字")
        extra = meta["tasks"].setdefault(task_id.lower(), {})
        extra.update(project_id=project_id, milestone_id=milestone_id)
        for name in ("pinned", "order", "asset_ids", "record_ids", "archived"):
            if name in changes:
                extra[name] = changes[name]
        if source_record:
            extra["record_ids"] = list(dict.fromkeys(extra.get("record_ids", []) + [source_record["id"]]))
        self.check_relations(extra, snapshot, meta)
        title = single_line(changes.get("title", "" if create else current["title"]), "任务标题")
        if "】" in title:
            raise WorkbenchError("invalid", "任务标题不能包含看板分隔符 】")
        if create:
            label = single_line(project["name"] if project else "待整理").replace("】", "")
            cell = f"[{task_id}](#{task_id.lower()}) · 【{label}】{title.replace('|', chr(92) + '|')}"
            lines = board.lines[:]
            lines.insert(board.end, "| " + " | ".join([cell, "—", "—", "—"]) + " |")
            text = board.newline.join(lines).rstrip() + f"\n\n### {task_id}\n\n"
            if project:
                text += f"- [项目断点]({project['path'].removeprefix('projects/')}/state.md)\n"
            board = sr_board.Board(self.root, encode_text(text, board.raw))
        elif title != current["title"]:
            old = board.cards[task_id.lower()]["cell"]
            match = sr_board.CARD.fullmatch(old)
            new = f"[{match[1]}](#{match[2]}) · 【{match[3]}】{title.replace('|', chr(92) + '|')}"
            line_index = board.start + board.card_rows[task_id.lower()]
            lines = board.lines[:]
            lines[line_index] = lines[line_index].replace(old, new, 1)
            board = sr_board.Board(self.root, encode_text(board.newline.join(lines) + (board.newline if board.trailing_newline else ""), board.raw))
        if "notes" in changes:
            board = write_task_notes(board, task_id.lower(), changes["notes"])
        board_changes = {key: value for key, value in changes.items() if key in set(sr_board.FIELDS) | {"status"}}
        for key in ("followup", "due_date"):
            if key in board_changes:
                board_changes[key] = check_date(board_changes[key])
        text = board.update(task_id, board_changes) if board_changes else board.raw.decode("utf-8")
        updates[self.root / sr_board.BOARD] = text.encode("utf-8")
        return {"id": task_id}

    def edit_project(self, method, params, meta, snapshot, updates):
        if method == "project.create":
            name = single_line(params.get("name", ""), "项目名称")
            project_id = identifier("project")
            slug = params.get("slug") or re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:48] or project_id
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
                raise WorkbenchError("invalid", "项目目录名无效")
            relative = "projects/" + slug
            if (self.root / relative).exists():
                raise WorkbenchError("conflict", "同名项目目录已存在")
            goal = params.get("goal", "")
            if not isinstance(goal, str) or len(goal) > 20000:
                raise WorkbenchError("invalid", "项目目标过长")
            meta["projects"][project_id] = {"path": relative, "name": name, "status": "active", "archived": False}
            template = self.root / "projects/_template"
            if not template.is_dir() or template.is_symlink():
                raise WorkbenchError("invalid", "工作区缺少 projects/_template；请先恢复项目模板")
            for required in ("README.md", "state.md", "journal.md"):
                if not (template / required).is_file():
                    raise WorkbenchError("invalid", "项目模板缺少 " + required)
            for source in template.rglob("*"):
                if any(part.startswith(".") for part in source.relative_to(template).parts):
                    continue
                if source.is_symlink():
                    raise WorkbenchError("forbidden", "项目模板含软链接，未创建项目")
                if source.is_file():
                    if source.suffix.lower() != ".md" or source.stat().st_size > MAX_TEXT:
                        raise WorkbenchError("unsupported", "项目模板含不支持的文件，请先核对：" + source.name)
                    raw = source.read_bytes()
                    content = decode_text(raw)
                    if source == template / "README.md":
                        content = content.replace("# Project", "# " + name, 1).replace("project-name", slug)
                        if goal:
                            content = replace_heading(content, "目标", goal)
                    elif source == template / "state.md":
                        content = replace_field(content, "最后更新", date.today().isoformat())
                        if params.get("stage"):
                            content = replace_field(content, "阶段", single_line(params["stage"], "研究阶段"))
                        if params.get("next_action"):
                            if not isinstance(params["next_action"], str) or len(params["next_action"]) > 20000:
                                raise WorkbenchError("invalid", "下一步文本无效")
                            content = replace_heading(content, "下一步第一件事", params["next_action"])
                    updates[self.root / relative / source.relative_to(template)] = encode_text(content, raw)
            return {"id": project_id, "path": relative}
        current = self.find_project(snapshot, params.get("id"))
        changes = deepcopy(params.get("changes", {}))
        if set(changes) - {"name", "status", "archived", "stage", "next_action", "goal"}:
            raise WorkbenchError("invalid", "未知项目字段")
        item = meta["projects"].setdefault(current["id"], {"path": current["path"], "status": current["status"], "archived": current["archived"]})
        for key in ("name", "status", "archived"):
            if key in changes:
                item[key] = changes[key]
        if "name" in changes:
            item["name"] = single_line(item["name"], "项目名称")
        if item["status"] not in PROJECT_STATUSES or not isinstance(item["archived"], bool):
            raise WorkbenchError("invalid", "项目管理状态无效")
        if "stage" in changes or "next_action" in changes:
            path = Path(current["state_path"])
            original = path.read_bytes()
            text = decode_text(original).replace("\r\n", "\n")
            if "stage" in changes:
                text = replace_field(text, "阶段", single_line(changes["stage"], "研究阶段", empty=True))
            if "next_action" in changes:
                if not isinstance(changes["next_action"], str) or len(changes["next_action"]) > 20000:
                    raise WorkbenchError("invalid", "下一步文本无效")
                text = replace_heading(text, "下一步第一件事", changes["next_action"])
            text = replace_field(text, "最后更新", date.today().isoformat())
            updates[path] = encode_text(text, original)
        if "goal" in changes:
            path = self.root / current["path"] / "README.md"
            original = raw_at(path) or b""
            updates[path] = encode_text(replace_heading(decode_text(original), "目标", changes["goal"]), original)
        return {"id": current["id"]}

    def edit_milestone(self, method, params, meta, snapshot):
        create = method == "milestone.create"
        key = identifier("milestone") if create else params.get("id")
        if not create and key not in meta["milestones"]:
            raise WorkbenchError("not_found", "里程碑不存在")
        changes = {k: v for k, v in params.items() if k not in {"operation_id", "revision"}} if create else params.get("changes", {})
        if set(changes) - {"title", "project_id", "status", "completion", "due_date", "evidence", "archived"}:
            raise WorkbenchError("invalid", "未知里程碑字段")
        item = meta["milestones"].setdefault(key, {"status": "todo", "created_at": stamp()})
        previous_project = item.get("project_id")
        item.update(changes)
        item["title"] = single_line(item.get("title", ""), "里程碑标题")
        item["project_id"] = self.find_project(snapshot, item.get("project_id"))["id"]
        if previous_project and previous_project != item["project_id"] and any(row.get("milestone_id") == key for row in snapshot["tasks"]):
            raise WorkbenchError("invalid", "里程碑仍有关联任务，不能直接换到其他项目")
        if item["status"] not in MILESTONE_STATUSES:
            raise WorkbenchError("invalid", "里程碑状态无效")
        item["due_date"] = check_date(item.get("due_date"))
        return {"id": key}

    def edit_asset(self, method, params, meta, snapshot):
        create = method == "asset.link"
        key = identifier("asset") if create else params.get("id")
        if not create and key not in meta["assets"]:
            raise WorkbenchError("not_found", "资产引用不存在")
        changes = {k: v for k, v in params.items() if k not in {"operation_id", "revision"}} if create else deepcopy(params.get("changes", {}))
        if set(changes) - {"path", "title", "kind", "featured", "purpose", "tags", "project_id", "project_ids", "task_ids", "milestone_ids", "archived"}:
            raise WorkbenchError("invalid", "未知资产字段")
        if "project_id" in changes:
            project_id = changes.pop("project_id")
            changes["project_ids"] = [project_id] if project_id else []
        if "path" in changes:
            selected = self.stored_path(changes["path"]).resolve()
            if not selected.exists():
                raise WorkbenchError("not_found", "所选资产暂不可访问")
            actual_kind = "directory" if selected.is_dir() else "file"
            if changes.get("kind", actual_kind) != actual_kind:
                raise WorkbenchError("invalid", "资产类型与文件不符")
            changes.update(path=self.store_path(selected), kind=actual_kind)
            if create:
                existing = next((other_key for other_key, item in meta["assets"].items() if self.stored_path(item["path"]).resolve() == selected), None)
                if existing:
                    key = existing
        item = meta["assets"].setdefault(key, {"created_at": stamp(), "featured": True, "project_ids": [], "task_ids": [], "milestone_ids": []})
        if create:
            # Linking the same original from another project adds a relationship;
            # explicit asset.update remains the operation for replacing selections.
            for relation in ("project_ids", "task_ids", "milestone_ids"):
                if relation in changes and isinstance(changes[relation], list):
                    changes[relation] = list(dict.fromkeys(item.get(relation, []) + changes[relation]))
        if item.get("path") and "path" in changes and item["path"] != changes["path"]:
            item.setdefault("previous_locations", []).append({"path": item["path"], "changed_at": stamp()})
        item.update(changes)
        if not item.get("path"):
            raise WorkbenchError("invalid", "需要资产路径")
        item["title"] = single_line(item.get("title") or Path(item["path"]).name, "资产名称")
        if not isinstance(item.get("featured"), bool):
            raise WorkbenchError("invalid", "重点标记应为布尔值")
        self.check_relations(item, snapshot, meta)
        return {"id": key, "path": str(self.stored_path(item["path"]))}

    def edit_record(self, method, params, meta, snapshot, updates):
        create = method == "record.create"
        key = identifier("record") if create else params.get("id")
        changes = {k: v for k, v in params.items() if k not in {"operation_id", "revision"}} if create else deepcopy(params.get("changes", {}))
        allowed = {"title", "kind", "date", "project_id", "project_ids", "task_id", "task_ids", "asset_ids", "status", "content", "append_journal", "summary"}
        if set(changes) - allowed:
            raise WorkbenchError("invalid", "未知记录字段")
        for singular, plural in (("project_id", "project_ids"), ("task_id", "task_ids")):
            if singular in changes:
                value = changes.pop(singular)
                changes[plural] = [value] if value else []
        if not create and key not in meta["records"]:
            discovered = next((item for item in snapshot["records"] if item["id"] == key), None)
            if not discovered:
                raise WorkbenchError("not_found", "记录不存在")
            meta["records"][key] = {k: v for k, v in discovered.items() if k not in {"id", "available", "discovered"}}
            meta["records"][key]["path"] = self.store_path(Path(discovered["path"]))
        item = meta["records"].setdefault(key, {"created_at": stamp(), "project_ids": [], "task_ids": [], "asset_ids": [], "kind": "note"})
        item.update({k: v for k, v in changes.items() if k not in {"content", "append_journal", "summary"}})
        item["title"] = single_line(item.get("title", ""), "记录标题")
        if item["kind"] not in RECORD_KINDS:
            raise WorkbenchError("invalid", "记录类型无效")
        if "date" in item:
            item["date"] = check_date(item["date"])
        self.check_relations(item, snapshot, meta)
        if not item["project_ids"] and item["task_ids"]:
            item["project_ids"] = list(dict.fromkeys(row["project_id"] for row in snapshot["tasks"] if row["id"] in item["task_ids"] and row["project_id"]))
        if create:
            item["path"] = f"records/{item['kind']}/{key}.md"
            content = changes.get("content", "")
            updates[self.root / item["path"]] = encode_text(f"# {item['title']}\n\n{content}\n")
        elif "content" in changes:
            raise WorkbenchError("invalid", "正文请通过 files.save 并提供文件版本保存")
        if changes.get("append_journal"):
            summary = single_line(changes.get("summary", ""), "日志摘要")
            for project_id in item["project_ids"]:
                project = self.find_project(snapshot, project_id)
                journal = Path(project["journal_path"])
                raw = raw_at(journal) or b""
                relative_record = self.store_path(self.stored_path(item["path"]))
                import os
                link = Path(os.path.relpath(self.stored_path(relative_record), journal.parent)).as_posix()
                addition = f"\n\n## {item.get('date') or date.today().isoformat()}\n\n- {summary}\n- 来源：[{item['title']}](<{link}>)\n"
                updates[journal] = encode_text(decode_text(raw).rstrip() + addition, raw)
        return {"id": key, "path": str(self.stored_path(item["path"]))}

    def edit_reading_status(self, params, snapshot, updates):
        key, status = params.get("bibkey"), params.get("status")
        paper = next((row for row in snapshot["papers"] if row["bibkey"] == key), None)
        if paper is None:
            raise WorkbenchError("not_found", "未找到已登记的文献")

        note = self.root / "library/notes" / (key + ".md")
        index = self.root / "library/index.md"
        for path in (note, index):
            if path.resolve() != path:
                raise WorkbenchError("forbidden", "精读卡和文献索引的路径不能包含软链接")
        raw_note, raw_index = raw_at(note), raw_at(index)
        text = decode_text(raw_note) if raw_note is not None else library.new_note_text(key, paper["doi"])
        current_status = library.note_status(text) if raw_note is not None else "未建卡"
        if current_status != params.get("expected_status"):
            raise WorkbenchError("conflict", "阅读状态已变更，请核对最新状态后重试")

        updated = library.update_note_status(text, status)
        bom = b"\xef\xbb\xbf" if raw_note is not None and raw_note.startswith(b"\xef\xbb\xbf") else b""
        updates[note] = bom + updated.encode("utf-8")
        updates[index] = library.index_text(self.root, overrides={key: updated})
        return {"bibkey": key, "status": status}, {note: digest(raw_note), index: digest(raw_index)}

    def mutate(self, method, params):
        self.require_open()
        operation_id = params.get("operation_id")
        store = self.store()
        store.path(operation_id)
        with library.writer_lock(self.root):
            previous = store.existing(operation_id, method, params)
            if previous:
                return {"snapshot": self.snapshot(), "operation_id": operation_id, "result": previous["result"], "replayed": True}
            if store.pending():
                raise WorkbenchError("recovery_required", "工作区有待恢复操作，请先处理")
            snapshot = self.snapshot()
            if method != "files.save" and params.get("revision") != snapshot["revision"]:
                raise WorkbenchError("conflict", "工作区已变更，请刷新后重试", {"current_revision": snapshot["revision"]})
            meta = deepcopy(self.metadata())
            updates = {}
            move, expected = None, None
            if method in {"task.delete", "project.delete", "trash.restore"}:
                expected = {self.root / META: snapshot["metadata_revision"], self.root / sr_board.BOARD: snapshot["board_revision"]}
                if method == "trash.restore":
                    result, move = restore_items(self, params, meta, snapshot, updates)
                else:
                    result, move = delete_items(self, method, params, meta, snapshot, updates)
            elif method.startswith("task."):
                result = self.edit_task(method, params, meta, snapshot, updates)
            elif method.startswith("project."):
                result = self.edit_project(method, params, meta, snapshot, updates)
            elif method.startswith("milestone."):
                result = self.edit_milestone(method, params, meta, snapshot)
            elif method.startswith("asset."):
                result = self.edit_asset(method, params, meta, snapshot)
            elif method.startswith("record."):
                result = self.edit_record(method, params, meta, snapshot, updates)
            elif method == "paper.set_status":
                result, expected = self.edit_reading_status(params, snapshot, updates)
            elif method == "files.save":
                path = self.resolve(params.get("path", ""))
                self.authorize_write(path)
                raw = path.read_bytes()
                if params.get("expected_revision") != digest(raw):
                    raise WorkbenchError("conflict", "磁盘文件已修改，当前草稿未写入", {"current_revision": digest(raw), "content": decode_text(raw), "path": str(path)})
                updated = encode_text(params.get("content"), raw)
                updates[path] = updated
                result = {"path": str(path), "revision": digest(updated)}
                # Editing a reading card must keep the existing derived index in sync.
                if path.parent == self.root / "library/notes":
                    updates[self.root / "library/index.md"] = library.index_text(self.root, overrides={path.stem: decode_text(updated)})
            else:
                raise WorkbenchError("method_not_found", "未知写入方法")
            for path, content in updates.items():
                raw = content.encode("utf-8") if isinstance(content, str) else content
                self.authorize_write(path)
                self.validate_markdown(path, raw)
            if meta != self.metadata():
                # Persist project IDs once an explicit management action needs metadata.
                for project in snapshot["projects"]:
                    if method == "project.delete" and project["id"] == result["id"]:
                        continue
                    meta["projects"].setdefault(project["id"], {"path": project["path"], "status": project["status"], "archived": project["archived"]})
                updates[self.root / META] = json_bytes(meta)
            store.commit(operation_id, method, params, updates, result, move=move, expected=expected)
            return {"snapshot": self.snapshot(), "operation_id": operation_id, "result": result}

    def search(self, params):
        query = single_line(params.get("query", ""), "搜索词", empty=True).casefold()
        if not query:
            return {"results": []}
        snapshot = self.snapshot()
        results = []
        for collection, kind in (("projects", "project"), ("tasks", "task"), ("milestones", "milestone"), ("assets", "asset"), ("records", "record"), ("papers", "paper")):
            for row in snapshot[collection]:
                title = row.get("title") or row.get("name") or row["id"]
                text = " ".join(str(row.get(key, "")) for key in ("id", "name", "title", "purpose", "tags", "next_action", "notes"))
                path = row.get("path") or row.get("note_path") or row.get("state_path") or row.get("source_path")
                if kind in {"record", "paper"} and path:
                    try:
                        file = self.resolve(path)
                        if file.is_file() and file.stat().st_size <= 512 * 1024:
                            text += "\n" + decode_text(file.read_bytes())
                    except (OSError, WorkbenchError):
                        pass
                position = text.casefold().find(query)
                if position >= 0:
                    results.append({"id": row["id"], "type": kind, "title": title, "path": path,
                                    "project_id": row.get("project_id"), "excerpt": text[max(0, position - 45):position + 140]})
                if len(results) >= 100:
                    return {"results": results, "limited": True}
        return {"results": results}

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise WorkbenchError("invalid", "params 必须是对象")
        if method in {"protocol.handshake", "ping"}:
            return {"protocol_version": PROTOCOL_VERSION, "service": "sr-workbench", "workspace": str(self.root) if self.root else None}
        if method == "workspace.open":
            path = Path(params.get("path", "")).expanduser().resolve()
            if not path.is_dir() or not (path / "projects").is_dir() or not (path / "library").is_dir():
                raise WorkbenchError("invalid", "请选择含 projects 和 library 的科研工作区")
            previous, self.root = self.root, path
            try:
                snapshot = self.snapshot()
            except BaseException:
                self.root = previous
                raise
            if previous and previous != path:
                self.citations.cancel(previous)
            return snapshot
        self.require_open()
        if method == "workspace.snapshot":
            return self.snapshot()
        if method == "paper.citations":
            return self.citations.view(self.root)
        if method == "paper.citations_refresh":
            return self.citations.start(self.root)
        if method == "paper.citations_cancel":
            self.citations.cancel(self.root)
            return self.citations.view(self.root)
        if method == "files.list":
            return self.file_list(params)
        if method == "files.read":
            return self.file_read(params)
        if method in {"files.resolve", "paths.resolve"}:
            path = self.resolve(params.get("path", ""))
            return {"path": str(path), "kind": "directory" if path.is_dir() else "file"}
        if method == "files.preview":
            path = self.resolve(params.get("path", ""))
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"} or path.stat().st_size > 5 * 1024 * 1024:
                raise WorkbenchError("unsupported", "预览仅支持 5 MB 内的 PNG/JPEG/WebP/GIF；其他文件请在原应用打开")
            return {"mime": mime, "data_url": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()}
        if method == "search":
            return self.search(params)
        if method == "operation.get":
            value = self.store().get(params.get("operation_id"))
            if value is None:
                return {"found": False}
            return {"found": True, **{key: value.get(key) for key in ("id", "method", "status", "created_at", "completed_at", "result", "conflicts")}}
        if method == "workspace.recover":
            with library.writer_lock(self.root):
                store = self.store()
                ids = [params["operation_id"]] if params.get("operation_id") else [row["id"] for row in store.pending()]
                results = []
                for key in ids:
                    try:
                        operation = store.recover(key, params.get("action", "resume"))
                        results.append({"id": key, "status": operation["status"]})
                    except WorkbenchError as exc:
                        results.append({"id": key, "status": "conflict", "error": str(exc), "details": exc.details})
                return {"snapshot": self.snapshot(), "results": results}
        if method in {"task.delete", "project.delete", "trash.restore",
                      "task.create", "task.update", "project.create", "project.update", "milestone.create", "milestone.update",
                      "asset.link", "asset.update", "record.create", "record.update", "paper.set_status", "files.save"}:
            return self.mutate(method, params)
        raise WorkbenchError("method_not_found", "未知方法：" + str(method))


def serve(input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    workbench = Workbench()
    for line in input_stream:
        request_id = None
        try:
            if len(line) > 12 * 1024 * 1024:
                raise WorkbenchError("too_large", "请求超过大小限制")
            request = json.loads(line)
            if not isinstance(request, dict) or "id" not in request or not isinstance(request.get("method"), str):
                raise WorkbenchError("invalid", "请求需包含 id、method 和 params")
            request_id = request["id"]
            result = workbench.dispatch(request["method"], request.get("params", {}))
            response = {"id": request_id, "result": result}
        except WorkbenchError as exc:
            error = {"code": exc.code, "message": str(exc)}
            if exc.details is not None:
                error["details"] = exc.details
            response = {"id": request_id, "error": error}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            response = {"id": request_id, "error": {"code": "invalid" if isinstance(exc, (ValueError, KeyError, TypeError)) else "io_error", "message": str(exc)}}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {"id": request_id, "error": {"code": "internal", "message": "操作失败；草稿与已有文件保留，请查看日志"}}
        output_stream.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
        output_stream.flush()


if __name__ == "__main__":
    serve()
