"""Durable operations shared by the desktop worker and the legacy board.

The operation record is written before any target is replaced. Recovery checks
every target against both recorded versions before doing any further writes.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import sr_library as library


class WorkbenchError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def fingerprint(method, params):
    return digest(json_bytes({"method": method, "params": params}))


def raw_at(path):
    return path.read_bytes() if path.is_file() else None


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rename_directory_exclusive(source, target):
    """A concurrently created target (including an empty folder) must win."""
    if os.name == "nt":
        os.rename(source, target)
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        result = libc.renamex_np(os.fsencode(source), os.fsencode(target), 0x4)  # RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        raise WorkbenchError("unsupported", "当前系统不支持安全移动项目目录")
    if result:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(target))


def durable_write(path, content):
    """Flush file contents and the rename/directory entries, including new parents."""
    path = Path(path)
    parents = []
    parent = path.parent
    while not parent.exists():
        parents.append(parent)
        parent = parent.parent
    library.write_files({path: content})
    for directory in [path.parent] + parents + [parent]:
        sync_directory(directory)


class OperationStore:
    def __init__(self, root, authorize=None):
        self.root = Path(root).resolve()
        self.directory = self.root / "workbench/operations"
        self.authorize = authorize or self._within_root

    def _within_root(self, path):
        if not path.resolve().is_relative_to(self.root):
            raise WorkbenchError("forbidden", "恢复目标不在工作区内")

    def path(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", operation_id):
            raise WorkbenchError("invalid", "operation_id 必须是 1–128 位字母、数字、连字符或下划线")
        for path in (self.root / "workbench", self.directory):
            if path.is_symlink():
                raise WorkbenchError("forbidden", "操作记录目录不能是软链接")
        target = self.directory / (operation_id + ".json")
        if target.is_symlink():
            raise WorkbenchError("forbidden", "操作记录不能是软链接")
        return target

    def get(self, operation_id):
        path = self.path(operation_id)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("id") != operation_id:
            raise WorkbenchError("invalid", "操作记录结构无效", {"operation_id": operation_id})
        return value

    def pending(self):
        if not self.directory.exists():
            return []
        self.path("validate-directory")
        result = []
        for path in sorted(self.directory.glob("*.json")):
            operation = self.get(path.stem)
            if operation.get("status") in {"prepared", "conflict"}:
                result.append({key: operation.get(key) for key in ("id", "method", "status", "created_at", "conflicts")})
        return result

    def existing(self, operation_id, method, params):
        operation = self.get(operation_id)
        if operation is None:
            return None
        if operation.get("fingerprint") != fingerprint(method, params):
            raise WorkbenchError("operation_mismatch", "这个操作 ID 已用于不同的请求")
        if operation.get("status") != "committed":
            raise WorkbenchError("recovery_required", "上次操作尚未完成，请先恢复或处理冲突", {"operation_id": operation_id})
        return operation

    def _write(self, operation):
        durable_write(self.path(operation["id"]), json_bytes(operation))

    def _entry_path(self, entry):
        if entry.get("scope") == "workspace":
            value = Path(entry["path"])
            if value.is_absolute() or ".." in value.parts:
                raise WorkbenchError("invalid", "操作记录的工作区路径无效")
            return self.root / value
        return Path(entry["path"])

    def _move_paths(self, move):
        project = Path(move["project"])
        if (len(project.parts) != 2 or project.parts[0] != "projects"
                or project.name.startswith((".", "_"))):
            raise WorkbenchError("forbidden", "只能移动工作区中的单个项目目录")
        self.path(move["trash_id"])
        active = self.root / project
        deleted = self.root / "workbench/trash" / move["trash_id"]
        if any(path.resolve() != path for path in (active, deleted)):
            raise WorkbenchError("forbidden", "项目或回收站路径不能经过软链接")
        return (deleted, active) if move["restore"] else (active, deleted)

    def project_move(self, project, trash_id, previous=None):
        restore = previous is not None
        move = {"project": project, "trash_id": trash_id, "restore": restore}
        source, target = self._move_paths(move)
        if target.exists():
            raise WorkbenchError("conflict", "目标目录已存在，请先移开同名目录：" + str(target))
        if not source.is_dir():
            raise WorkbenchError("not_found", "待移动的项目目录不存在：" + str(source))
        stat = source.stat()
        move["identity"] = [stat.st_dev, stat.st_ino]
        state = source / "state.md"
        if state.is_symlink() or not state.is_file():
            raise WorkbenchError("conflict", "项目断点文件缺失或已替换，请核对目录")
        move["state_hash"] = digest(state.read_bytes())
        # A normal backup/restore gives directories new inodes. The unique trash
        # slot and the recorded project checkpoint identify such a copied folder.
        if previous and move["identity"] != previous["identity"] and move["state_hash"] != previous.get("state_hash"):
            raise WorkbenchError("conflict", "回收站中的项目目录已被替换，请核对项目断点")
        self._move_applied(move)
        return move

    def _move_applied(self, move):
        source, target = self._move_paths(move)
        for path, other, applied in ((source, target, False), (target, source, True)):
            if path.is_dir() and not other.exists():
                stat = path.stat()
                if [stat.st_dev, stat.st_ino] == move["identity"]:
                    return applied
        raise WorkbenchError("conflict", "项目目录已被移动、替换或占用，请核对原目录与回收站")

    def _apply_move(self, move):
        if self._move_applied(move):
            return
        source, target = self._move_paths(move)
        if not target.parent.exists():
            target.parent.mkdir()
            sync_directory(target.parent.parent)
        rename_directory_exclusive(source, target)
        sync_directory(source.parent)
        sync_directory(target.parent)
        self._move_applied(move)

    def commit(self, operation_id, method, params, updates, result=None, after_replace=None, *, move=None, expected=None):
        """Caller holds library.writer_lock and has checked business revisions."""
        previous = self.existing(operation_id, method, params)
        if previous:
            return previous
        entries = []
        for path, content in updates.items():
            path = Path(path)
            self.authorize(path)
            if path.is_symlink():
                raise WorkbenchError("forbidden", "不能替换软链接本身")
            before = raw_at(path)
            if expected and path in expected and digest(before) != expected[path]:
                raise WorkbenchError("conflict", "准备写入时文件已修改，请刷新后重试")
            after = content.encode("utf-8") if isinstance(content, str) else content
            if not isinstance(after, bytes):
                raise WorkbenchError("invalid", "保存内容必须是文本或字节")
            if before == after:
                continue
            scope = "workspace" if path.resolve().is_relative_to(self.root) else "external"
            location = path.resolve().relative_to(self.root).as_posix() if scope == "workspace" else str(path.resolve())
            entries.append({"scope": scope, "path": location, "before_hash": digest(before), "after_hash": digest(after),
                            "before": base64.b64encode(before).decode() if before is not None else None,
                            "after": base64.b64encode(after).decode()})
        operation = {"schema_version": 1, "id": operation_id, "method": method,
                     "fingerprint": fingerprint(method, params), "status": "prepared", "created_at": stamp(),
                     "changes": entries, "result": result or {}}
        if move:
            operation["project_move"] = move
        self._write(operation)
        if move:
            self._apply_move(move)
        # A process exit here or after any replace deliberately leaves recovery data.
        for index, entry in enumerate(entries):
            path = self._entry_path(entry)
            if digest(raw_at(path)) != entry["before_hash"]:
                operation.update(status="conflict", conflicts=[entry["path"]])
                self._write(operation)
                raise WorkbenchError("conflict", "写入期间文件被外部修改；恢复资料已保留", {"path": entry["path"]})
            durable_write(path, base64.b64decode(entry["after"]))
            if after_replace:
                after_replace(index, operation)
        if ((move and not self._move_applied(move))
                or any(digest(raw_at(self._entry_path(entry))) != entry["after_hash"] for entry in entries)):
            raise WorkbenchError("recovery_required", "写回核对未通过，操作保留为待恢复")
        operation.update(status="committed", completed_at=stamp())
        self._write(operation)
        return operation

    def recover(self, operation_id, action="resume"):
        operation = self.get(operation_id)
        if operation is None:
            raise WorkbenchError("not_found", "未找到操作")
        if operation["status"] in {"committed", "abandoned"}:
            return operation
        move = operation.get("project_move")
        if action == "keep_current" and operation["method"] in {"task.delete", "project.delete", "trash.restore"}:
            applied = [digest(raw_at(self._entry_path(e))) == e["after_hash"] for e in operation["changes"]]
            if move:
                source, _ = self._move_paths(move)
                stat = source.stat() if source.is_dir() else None
                original_in_place = stat and [stat.st_dev, stat.st_ino] == move["identity"]
                applied.append(False if original_in_place else self._move_applied(move))
            if all(applied):
                action = "resume"
            elif any(applied):
                raise WorkbenchError("conflict", "部分删除或恢复步骤已经完成，请核对冲突文件并继续恢复")
        if action == "keep_current":
            operation.update(status="abandoned", resolved_at=stamp(), resolution="用户选择保留当前文件；原恢复资料保留")
            self._write(operation)
            return operation
        if action != "resume":
            raise WorkbenchError("invalid", "恢复方式应为 resume 或 keep_current")
        conflicts, decoded = [], []
        for entry in operation.get("changes", []):
            path = self._entry_path(entry)
            self.authorize(path)
            after = base64.b64decode(entry["after"], validate=True)
            before = base64.b64decode(entry["before"], validate=True) if entry["before"] is not None else None
            if digest(after) != entry["after_hash"] or digest(before) != entry["before_hash"]:
                raise WorkbenchError("invalid", "恢复记录校验不符，未写入任何文件")
            current = digest(raw_at(path))
            if path.is_symlink() or current not in {entry["before_hash"], entry["after_hash"]}:
                conflicts.append(str(path))
            decoded.append((path, current, entry["after_hash"], after))
        if conflicts:
            operation.update(status="conflict", conflicts=conflicts)
            self._write(operation)
            raise WorkbenchError("conflict", "文件已有后来修改，恢复不能覆盖；可保留当前文件并结束该恢复项", {"paths": conflicts})
        if move:
            self._apply_move(move)
        for path, current, expected, after in decoded:
            if current != expected:
                if digest(raw_at(path)) != current:
                    raise WorkbenchError("conflict", "恢复期间文件被外部修改")
                durable_write(path, after)
        if ((move and not self._move_applied(move))
                or any(digest(raw_at(path)) != expected for path, _, expected, _ in decoded)):
            raise WorkbenchError("conflict", "恢复期间文件或项目目录已被外部修改，请核对后继续")
        operation.update(status="committed", completed_at=stamp(), recovered=True)
        operation.pop("conflicts", None)
        self._write(operation)
        return operation


def legacy_board_update(root, revision, key, changes):
    """One shared durable write path for the original HTTP board."""
    from sr_board import BOARD, Board
    import uuid
    root = Path(root).resolve()
    with library.writer_lock(root):
        store = OperationStore(root)
        if store.pending():
            raise WorkbenchError("recovery_required", "工作区有待恢复操作，请先在工作台处理")
        board = Board(root)
        if revision != board.revision:
            raise WorkbenchError("conflict", "看板已被修改，请重新读取后重试")
        updated = board.update(key, changes).encode("utf-8")
        snapshot = Board(root, updated).snapshot()
        store.commit("legacy-" + uuid.uuid4().hex, "legacy.board.update", {"id": key, "changes": changes},
                     {root / BOARD: updated}, {"id": key})
        return snapshot
