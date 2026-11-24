import { useId, useRef } from "react";
import { Check, ChevronDown, MoreHorizontal, Pencil, Pin, Plus, Trash2 } from "lucide-react";
import { ProjectLabel, StatusIcon } from "./WorkspaceVisuals";
import "./TaskRow.css";
import { taskLabels } from "./api";
import type { Project, Task, TaskStatus } from "./api";

export function TaskRow({ task, owner, milestone, featured, busy, onOpen, onStatus, onPin, onRecord, onDelete }: {
  task: Task;
  owner?: Project;
  milestone?: string;
  featured?: boolean;
  busy: boolean;
  onOpen: () => void;
  onStatus: (status: TaskStatus) => void;
  onPin: () => void;
  onRecord: () => void;
  onDelete: () => void;
}) {
  const menuId = useId();
  const menu = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const done = task.status === "done";
  const followup = task.status === "waiting" && task.followup;
  const date = followup || task.due_date;

  function positionMenu(x: number, y: number) {
    menu.current?.style.setProperty("--menu-x", `${x}px`);
    menu.current?.style.setProperty("--menu-y", `${y}px`);
  }

  function openMenu(x: number, y: number) {
    positionMenu(x, y);
    const popover = menu.current;
    if (!popover?.matches(":popover-open")) {
      trigger.current?.focus({ preventScroll: true });
      popover?.showPopover();
    }
    popover?.querySelector("button")?.focus();
  }

  function choose(action: () => void) {
    menu.current?.hidePopover();
    trigger.current?.focus();
    action();
  }

  return (
    <div className={`task-row status-${task.status}${featured ? " task-featured" : ""}`}
      onContextMenu={event => {
        event.preventDefault();
        openMenu(event.clientX, event.clientY);
      }}
      onPointerUp={event => {
        // macOS can dispatch contextmenu on mouse-down. Open again after light dismissal on mouse-up.
        if (event.button === 2) openMenu(event.clientX, event.clientY);
      }}
      onClickCapture={event => {
        if (!event.ctrlKey || event.detail === 0) return;
        event.preventDefault();
        event.stopPropagation();
        openMenu(event.clientX, event.clientY);
      }}
      onKeyDown={event => {
        if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
        event.preventDefault();
        const bounds = trigger.current?.getBoundingClientRect();
        if (bounds) openMenu(bounds.right, bounds.bottom);
      }}
    >
      <button className="task-check" aria-label={`${done ? "重新打开任务" : "标记任务完成"} ${task.title}`}
        aria-disabled={busy || undefined}
        onClick={() => { if (!busy) onStatus(done ? "ready" : "done"); }}>
        {done ? <Check size={16} /> : <StatusIcon status={task.status} size={21} />}
      </button>
      <button className="task-main" onClick={onOpen}>
        <span className="task-title">{task.title}</span>
        <span className="task-meta">
          <ProjectLabel project={owner} />
          {milestone && <span className="task-meta-detail">{milestone}</span>}
          {task.status === "waiting" && task.blocker && <span className="task-meta-detail">{task.blocker}</span>}
        </span>
      </button>
      <div className="task-trailing">
        {date && <time className="task-date" dateTime={date} title={followup ? "跟进日期" : "截止日期"}>
          {followup && "跟进 "}{date.slice(0, 10)}
        </time>}
        <span className={`task-status ${task.status}`}>
          <select aria-label={`${task.title}的状态`} value={task.status} disabled={busy}
            onChange={event => onStatus(event.target.value as TaskStatus)}>
            {Object.entries(taskLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <ChevronDown size={12} aria-hidden="true" />
        </span>
        {task.pinned && <Pin className="task-pin" size={13} aria-label="已置顶" />}
        <button className="icon-button task-actions-toggle" ref={trigger} popoverTarget={menuId}
          title="任务操作" aria-label={`${task.title}的操作`}
          onClick={event => {
            const bounds = event.currentTarget.getBoundingClientRect();
            positionMenu(bounds.right, bounds.bottom);
          }}>
          <MoreHorizontal size={17} />
        </button>
        <div className="task-actions-menu" id={menuId} ref={menu} popover="auto" role="group"
          aria-label="任务操作">
          <button onClick={() => choose(onOpen)}><Pencil size={15} />编辑任务</button>
          <button disabled={busy} onClick={() => choose(onPin)}><Pin size={15} />{task.pinned ? "取消置顶" : "置顶任务"}</button>
          <button onClick={() => choose(onRecord)}><Plus size={15} />补充记录</button>
          <button className="delete-text" disabled={busy} onClick={() => choose(onDelete)}><Trash2 size={15} />移入回收站</button>
        </div>
      </div>
    </div>
  );
}
