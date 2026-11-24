"""Recoverable deletion of board cards and project directories."""
import base64
import json
from pathlib import Path
import re

from sr_board import BOARD, ID, Board
from sr_workbench_store import WorkbenchError, digest, stamp


def relation_targets(entry):
    return {"project_ids": {entry["project_id"]} if entry.get("project_id") else set(),
            "milestone_ids": set(entry["milestone_ids"]), "task_ids": set(entry["task_ids"])}


def detach_links(meta, targets):
    for collection in ("assets", "records"):
        for item in meta[collection].values():
            for field, removed in targets.items():
                if field in item:
                    item[field] = [value for value in item[field] if value not in removed]
                if item.get(field[:-1]) in removed:
                    del item[field[:-1]]


def restore_links(meta, original, targets):
    for collection in ("assets", "records"):
        for key, previous in original.get(collection, {}).items():
            item = meta[collection].get(key)
            if item is None:
                continue
            for field, removed in targets.items():
                prior = previous.get(field, [])
                for index, value in enumerate(prior):
                    if value in removed:
                        current = item.setdefault(field, [])
                        if value not in current:
                            following = next((v for v in prior[index + 1:] if v in current), None)
                            current.insert(current.index(following) if following else len(current), value)
                singular = field[:-1]
                if previous.get(singular) in removed and not item.get(singular):
                    item[singular] = previous[singular]


def move_references(root, meta, source, target):
    # Keep references to files in the moved folder attached to the original,
    # even if someone later creates a different project with the same slug.
    for collection in ("assets", "records"):
        for item in meta[collection].values():
            path = root / item["path"]
            if path.is_relative_to(root / source):
                item["path"] = (target / path.relative_to(root / source)).as_posix()


def delete_items(worker, method, params, meta, snapshot, updates):
    trash_id = params["operation_id"]
    project, move = None, None
    if method == "project.delete":
        project = worker.find_project(snapshot, params.get("id"))
        tasks = [task for task in snapshot["tasks"] if task["project_id"] == project["id"]]
        move = worker.store().project_move(project["path"], trash_id)
    else:
        ids = params.get("ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
            raise WorkbenchError("invalid", "请选择要删除的任务")
        keys = {i.lower() for i in ids}
        tasks = [task for task in snapshot["tasks"] if task["id"].lower() in keys]
        if len(tasks) != len(keys):
            raise WorkbenchError("not_found", "所选任务已变化，请重新选择")
    entry = {
        "kind": "project" if project else "tasks", "deleted_at": stamp(),
        "title": project["name"] if project else tasks[0]["title"] if len(tasks) == 1 else f"{len(tasks)} 张任务卡",
        "task_ids": [task["id"] for task in tasks],
        "parent_ids": sorted({task["project_id"] for task in tasks if task["project_id"]}),
        "milestone_ids": [],
    }
    if tasks:
        updates[worker.root / BOARD] = worker.board().without_cards({task["id"].lower() for task in tasks})
    task_keys = {t["id"].lower() for t in tasks}
    if project:
        project_id = project["id"]
        entry.update(project_id=project_id, project_path=project["path"],
                     milestone_ids=[k for k, m in meta["milestones"].items() if m.get("project_id") == project_id])
        # Older boards can retain metadata for a card removed manually from the table.
        task_keys.update(k for k, t in meta["tasks"].items() if t.get("project_id") == project_id)
        meta["projects"].pop(project_id, None)
        for key in entry["milestone_ids"]:
            del meta["milestones"][key]
        move_references(worker.root, meta, Path(project["path"]), Path("workbench/trash") / trash_id)
    for key in task_keys:
        meta["tasks"].pop(key, None)
    entry["task_keys"] = sorted(task_keys)
    detach_links(meta, relation_targets(entry))
    meta["trash"][trash_id] = entry
    return {"id": project["id"] if project else trash_id, "trash_id": trash_id}, move


def previous_file(operation, path):
    entry = next((item for item in operation["changes"] if item["scope"] == "workspace" and item["path"] == path), None)
    if entry is None:
        raise WorkbenchError("invalid", "回收站缺少原文件记录：" + path)
    before = base64.b64decode(entry["before"], validate=True) if entry["before"] is not None else None
    if digest(before) != entry["before_hash"]:
        raise WorkbenchError("invalid", "回收站原文件校验失败：" + path)
    return before, entry["after_hash"]


def restore_items(worker, params, meta, snapshot, updates):
    trash_id = params.get("id")
    entry = meta["trash"].get(trash_id)
    if not entry:
        raise WorkbenchError("not_found", "该项目或任务已移出回收站")
    operation = worker.store().get(trash_id)
    if not operation or operation["status"] != "committed" or operation["method"] not in {"task.delete", "project.delete"}:
        raise WorkbenchError("invalid", "找不到已完成的删除操作")
    original = json.loads(previous_file(operation, "workbench/metadata.json")[0] or b"{}")
    project_id = entry.get("project_id")
    parents = set(entry["parent_ids"]) - {project_id}
    active = {p["id"] for p in snapshot["projects"]}
    deleted = {item.get("project_id") for item in meta["trash"].values()}
    if parents - active or parents & deleted:
        raise WorkbenchError("conflict", "请先恢复这些任务原先所属的项目")
    move = None
    if project_id:
        if project_id in active or project_id in meta["projects"]:
            raise WorkbenchError("conflict", "同标识项目已存在，请先核对当前项目")
        project = entry["project_path"]
        move = worker.store().project_move(project, trash_id, previous=operation["project_move"])
        move_references(worker.root, meta, Path("workbench/trash") / trash_id, Path(project))
        if project_id in original.get("projects", {}):
            meta["projects"][project_id] = original["projects"][project_id]
    keys = [task_id.lower() for task_id in entry["task_ids"]]
    if keys:
        board = worker.board()
        headings = {match[1].lower() for line in board.lines if (match := re.fullmatch(rf"### ({ID})\s*", line))}
        if headings.intersection(keys) or board.cards.keys() & set(keys):
            raise WorkbenchError("conflict", "看板中已有同卡号的任务或详情，请先处理重名卡号")
        before, after_hash = previous_file(operation, BOARD.as_posix())
        updates[worker.root / BOARD] = (before if board.revision == after_hash
                                        else board.restore_cards(Board(worker.root, before), keys))
    for collection, ids in (("tasks", entry["task_keys"]), ("milestones", entry["milestone_ids"])):
        for key in ids:
            if key in meta[collection]:
                raise WorkbenchError("conflict", "同标识的任务或里程碑已存在：" + key)
            if key in original.get(collection, {}):
                meta[collection][key] = original[collection][key]
    restore_links(meta, original, relation_targets(entry))
    del meta["trash"][trash_id]
    return {"id": project_id or trash_id}, move
