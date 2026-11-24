import { useEffect, useRef } from "react";
import { Folder, RotateCcw, SquareCheck, Trash2 } from "lucide-react";
import { Modal } from "./Forms";
import type { Project, Task, TrashItem } from "./api";

export interface DeleteSelection {
  project?: Project;
  tasks: Task[];
  milestones: number;
  revision: string;
}

export function DeleteConfirmation({ selection, busy, onClose, onConfirm }: {
  selection: DeleteSelection;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const element = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = element.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  const { project, tasks, milestones } = selection;
  return (
    <dialog className="modal delete-dialog" ref={element} aria-labelledby="delete-heading"
      onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
      <div className="form-body">
        <h2 id="delete-heading">{project ? `删除项目「${project.name}」？` :
          tasks.length === 1 ? "删除这张任务卡？" : `删除所选的 ${tasks.length} 张任务卡？`}</h2>
        <p>{project
          ? `项目目录、${tasks.length} 张任务卡和 ${milestones} 个里程碑将一起移入回收站。目录外的文献、数据和工作记录保留在原处。`
          : "任务将从总览、项目和看板移除。可以在侧栏的回收站恢复卡片及其关联。"}</p>
        {project && <code>{project.path}</code>}
        {!!tasks.length && <ul className="delete-task-list">{tasks.map(task =>
          <li key={task.id}><code>{task.id}</code><span>{task.title}</span></li>
        )}</ul>}
      </div>
      <footer className="form-actions">
        <button className="button" autoFocus disabled={busy} onClick={onClose}>取消</button>
        <button className="button destructive" disabled={busy} onClick={onConfirm}>
          <Trash2 size={15} />{busy ? "正在移入…" : "移入回收站"}
        </button>
      </footer>
    </dialog>
  );
}

export function TrashDialog({ items, busy, onClose, onRestore }: {
  items: TrashItem[];
  busy: boolean;
  onClose: () => void;
  onRestore: (item: TrashItem) => void;
}) {
  return <Modal title="回收站" onClose={onClose}>
    <div className="form-body trash-list">
      {!items.length && <p className="empty-inline">回收站为空</p>}
      {items.map(item => {
        const project = item.kind === "project";
        const Icon = project ? Folder : SquareCheck;
        const when = new Date(item.deleted_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
        return <div className="trash-item" key={item.id}>
          <Icon size={17} aria-hidden="true" />
          <div>
            <strong>{item.title}</strong>
            <small>{project ? `项目 · ${item.task_ids.length} 张卡 · ${item.milestone_count} 个里程碑` :
              item.task_ids.join("、")} · {when}</small>
          </div>
          <button className="button small" disabled={busy} aria-label={`恢复 ${item.title}`}
            onClick={() => onRestore(item)}><RotateCcw size={14} />恢复</button>
        </div>;
      })}
    </div>
  </Modal>;
}
