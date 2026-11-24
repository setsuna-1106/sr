import { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { Flag, GripVertical, ListChecks, ListFilter, Pin, Plus, Trash2 } from "lucide-react";
import { projectLabels, taskLabels } from "./api";
import type { Snapshot, Task, TaskStatus } from "./api";
import { Select } from "./Forms";
import { ProjectLabel, StatusIcon, taskStatuses } from "./WorkspaceVisuals";
import "./TaskBoard.css";

export const unassignedScope = ":unassigned";

interface Props {
  snapshot: Snapshot;
  scope: string;
  busy: boolean;
  onScope: (scope: string) => void;
  onOpen: (task: Task) => void;
  onCreate: (status: TaskStatus) => void;
  onDelete: (tasks: Task[]) => void;
  onMove: (task: Task, status: TaskStatus, revision: string) => Promise<boolean>;
}

interface CardGesture {
  task: Task;
  revision: string;
  pointerId: number;
  startX: number;
  startY: number;
  moved: boolean;
}

function columnAt(event: PointerEvent): TaskStatus | undefined {
  const column = document.elementFromPoint(event.clientX, event.clientY)
    ?.closest<HTMLElement>("[data-board-status]");
  return column?.dataset.boardStatus as TaskStatus | undefined;
}

export function TaskBoard({
  snapshot, scope, busy, onScope, onOpen, onCreate, onMove, onDelete,
}: Props) {
  const gesture = useRef<CardGesture | null>(null);
  const preview = useRef<HTMLDivElement>(null);
  const suppressClick = useRef(false);
  const [dragged, setDragged] = useState<Task | null>(null);
  const [over, setOver] = useState<TaskStatus>();
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const projects = new Map(snapshot.projects.map((p) => [p.id, p]));
  const milestones = new Map(snapshot.milestones.map((m) => [m.id, m.title]));
  const visible = snapshot.tasks.filter((task) => {
    if (scope === unassignedScope) return !projects.has(task.project_id || "");
    return !scope || task.project_id === scope;
  });
  const checked = visible.filter((task) => selected?.has(task.id));

  useEffect(() => {
    const ids = new Set(snapshot.tasks.map((task) => task.id));
    setSelected(previous => {
      if (!previous || [...previous].every(id => ids.has(id))) return previous;
      const remaining = new Set([...previous].filter(id => ids.has(id)));
      return remaining.size ? remaining : null;
    });
  }, [snapshot.tasks]);

  function toggleSelection(items: Task[], add: boolean) {
    setSelected(previous => {
      const next = new Set(previous);
      items.forEach(task => { if (add) next.add(task.id); else next.delete(task.id); });
      return next;
    });
  }

  function cancelDrag() {
    gesture.current = null;
    setDragged(null);
    setOver(undefined);
  }

  useEffect(() => {
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && gesture.current) cancelDrag();
    };
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", cancelDrag);
    return () => {
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", cancelDrag);
    };
  }, []);

  function startCard(event: PointerEvent, task: Task) {
    const target = event.target as Element;
    if (busy || selected || !event.isPrimary || event.button !== 0 || target.closest("select, input, .card-delete"))
      return;
    gesture.current = {
      task,
      revision: snapshot.revision,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    // Capturing the hit element preserves ordinary clicks on the task title.
    target.setPointerCapture(event.pointerId);
  }

  function trackCard(event: PointerEvent) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    if (!current.moved) {
      const distance = Math.hypot(event.clientX - current.startX, event.clientY - current.startY);
      if (distance < 5) return;
      current.moved = true;
      suppressClick.current = true;
      setDragged(current.task);
    }
    event.preventDefault();
    if (preview.current) {
      preview.current.style.transform = `translate(${event.clientX + 12}px, ${event.clientY + 10}px)`;
    }
    const target = columnAt(event);
    setOver(target === current.task.status ? undefined : target);
  }

  function dropCard(event: PointerEvent) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    // A fast drag may arrive as a down/up pair with coalesced movement.
    trackCard(event);
    const target = columnAt(event);
    cancelDrag();
    if (!busy && current.moved && target && target !== current.task.status)
      void onMove(current.task, target, current.revision);
  }

  async function moveWithMenu(task: Task, status: TaskStatus, control: HTMLSelectElement) {
    const hadFocus = document.activeElement === control;
    const saved = await onMove(task, status, snapshot.revision);
    if (saved && hadFocus) {
      requestAnimationFrame(() => {
        if (document.activeElement === document.body)
          document.getElementById(control.id)?.focus();
      });
    }
  }

  function card(task: Task) {
    const owner = projects.get(task.project_id || "");
    const milestone = milestones.get(task.milestone_id || "");
    const followup = task.status === "waiting" && task.followup;
    const date = followup || task.due_date;
    return (
      <article
        key={task.id}
        className={`board-card${dragged?.id === task.id ? " dragging" : ""}${selected?.has(task.id) ? " selected" : ""}`}
        onPointerDown={(event) => startCard(event, task)}
      >
        <div className="board-card-head">
          {selected && <input type="checkbox" aria-label={`选择任务 ${task.id}`}
            checked={selected.has(task.id)} onChange={e => toggleSelection([task], e.target.checked)} />}
          <span title={task.id}>{task.id}</span>
          {task.pinned && <Pin size={12} aria-label="已置顶" />}
          <GripVertical size={15} aria-hidden="true" />
        </div>
        <button className="board-card-title" onClick={() => onOpen(task)}>
          {task.title}
        </button>
        {!scope && <div className="board-owner"><ProjectLabel project={owner} /></div>}
        {milestone && <p className="board-milestone"><Flag size={12} />{milestone}</p>}
        {task.status === "waiting" && task.blocker && (
          <p className="board-blocker">{task.blocker}</p>
        )}
        <footer>
          {date && <time dateTime={date}>{followup ? "跟进" : "截止"} {date}</time>}
          <select
            id={`board-move-${task.id}`}
            aria-label={`移动任务 ${task.id}`}
            value=""
            disabled={busy}
            onChange={(event) =>
              void moveWithMenu(task, event.target.value as TaskStatus, event.currentTarget)
            }
          >
            <option value="" disabled>移至…</option>
            {taskStatuses.filter((target) => target !== task.status).map((target) => (
              <option key={target} value={target}>{taskLabels[target]}</option>
            ))}
          </select>
          {!selected && <button className="icon-button card-delete delete-icon" disabled={busy}
            title={`删除任务 ${task.id}`} aria-label={`删除任务 ${task.id}`} onClick={() => onDelete([task])}>
            <Trash2 size={14} />
          </button>}
        </footer>
      </article>
    );
  }

  return (
    <>
      <div className="board-toolbar">
        <label className="board-scope">
          <ListFilter size={15} aria-hidden="true" />
          <Select
            label="筛选看板项目"
            value={scope}
            onChange={(value) => {
              cancelDrag();
              onScope(value);
            }}
          >
            <option value="">全部任务</option>
            <option value={unassignedScope}>候选 / 待整理</option>
            {snapshot.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.archived ? "（已归档）" : p.status !== "active" && `（${projectLabels[p.status]}）`}
              </option>
            ))}
          </Select>
        </label>
        {selected ? <div className="board-selection">
          <label><input type="checkbox" checked={!!visible.length && checked.length === visible.length}
            onChange={e => toggleSelection(visible, e.target.checked)} />全选当前结果</label>
          <button className="button small delete-text" disabled={busy || !checked.length}
            onClick={() => onDelete(checked)}>删除所选（{checked.length}）</button>
          <button className="text-button" onClick={() => setSelected(null)}>取消选择</button>
        </div> : <button className="button small" disabled={busy || !visible.length}
          onClick={() => setSelected(new Set())}><ListChecks size={15} />批量选择</button>}
      </div>
      <div className="board-scroll">
        <div
          className="board-columns"
          aria-label="任务看板"
          onPointerDownCapture={() => { suppressClick.current = false; }}
          onPointerMove={trackCard}
          onPointerUp={dropCard}
          onPointerCancel={cancelDrag}
          onLostPointerCapture={cancelDrag}
          onClickCapture={(event) => {
            if (event.detail && suppressClick.current) {
              suppressClick.current = false;
              event.stopPropagation();
              event.preventDefault();
            }
          }}
        >
          {taskStatuses.map((status) => {
            const items = visible.filter((task) => task.status === status)
              .sort((a, b) => Number(b.pinned) - Number(a.pinned));
            const addLabel = `在「${taskLabels[status]}」新建任务`;
            return (
              <section
                key={status}
                data-board-status={status}
                aria-labelledby={`board-column-${status}`}
                className={`board-column board-${status}${over === status ? " drop-target" : ""}`}
              >
                <header className="board-column-head">
                  {selected && <input type="checkbox" aria-label={`选择「${taskLabels[status]}」全部任务`}
                    disabled={!items.length} checked={!!items.length && items.every(t => selected.has(t.id))}
                    onChange={e => toggleSelection(items, e.target.checked)} />}
                  <h2 id={`board-column-${status}`}><StatusIcon status={status} />{taskLabels[status]}</h2>
                  <span aria-label={`${items.length} 项任务`}>{items.length}</span>
                  <button
                    className="icon-button"
                    disabled={busy}
                    aria-label={addLabel}
                    title={addLabel}
                    onClick={() => onCreate(status)}
                  >
                    <Plus size={16} />
                  </button>
                </header>
                {items.length ? items.map(card) : <p className="board-empty">暂无任务</p>}
              </section>
            );
          })}
        </div>
      </div>
      <div className="board-drag-preview" ref={preview} hidden={!dragged} aria-hidden="true">
        {dragged?.title}
      </div>
    </>
  );
}
