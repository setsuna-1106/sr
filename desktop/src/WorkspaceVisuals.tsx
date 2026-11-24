import type { CSSProperties } from "react";
import { CheckCircle2, ChevronRight, Circle, CircleDot, Clock3, Folder } from "lucide-react";
import { projectLabels, taskLabels } from "./api";
import type { Project, Task, TaskStatus } from "./api";

export const taskStatuses = Object.keys(taskLabels) as TaskStatus[];
const statusIcons = { ready: Circle, doing: CircleDot, waiting: Clock3, done: CheckCircle2 };

export function StatusIcon({ status, size = 16 }: { status: TaskStatus; size?: number }) {
  const Icon = statusIcons[status];
  return <Icon size={size} aria-hidden="true" />;
}

// The same ID keeps its colour across views, renames and filter changes.
export function projectColor(id: string): CSSProperties {
  let hash = 0;
  for (const character of id) hash = (Math.imul(hash, 31) + character.charCodeAt(0)) >>> 0;
  return { "--project-hue": hash % 360 } as CSSProperties;
}

export function projectNextAction(project: Project) {
  // Legacy state files can still contain the instruction from projects/_template/state.md.
  const text = project.next_action?.trim();
  return text === "必须是下一次打开电脑即可执行的单一动作。" ? "" : text || "";
}

export function ProjectCover({ id }: { id: string }) {
  return (
    <span className="project-cover" style={projectColor(id)} aria-hidden="true">
      <Folder size={24} strokeWidth={1.55} />
    </span>
  );
}

export function ProjectLabel({ project }: { project?: Project }) {
  return (
    <span className={project ? "project-label" : "project-label unassigned"} style={project && projectColor(project.id)}>
      <i className="project-dot" aria-hidden="true" />
      <span>{project?.name || "候选 / 待整理"}</span>
    </span>
  );
}

export function TaskDistribution({ tasks }: { tasks: Task[] }) {
  const counts = { ready: 0, doing: 0, waiting: 0, done: 0 };
  for (const task of tasks) counts[task.status]++;
  const statuses = taskStatuses.filter(status => counts[status] > 0);
  if (tasks.length < 6 || statuses.length < 2) return null;
  return (
    <section className="distribution" aria-labelledby="distribution-title">
      <header className="panel-heading">
        <h2 id="distribution-title">任务统计</h2>
      </header>
      <dl className="distribution-legend">
        {statuses.map(status => (
          <div className={status} key={status}>
            <dt><StatusIcon status={status} size={15} />{taskLabels[status]}</dt>
            <dd>{counts[status]}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function ProjectPreview({ project, openTasks, compact, onOpen }: {
  project: Project; openTasks: number; compact: boolean; onOpen: () => void;
}) {
  const excerpt = projectNextAction(project)
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\*\*|`/g, "");
  return (
    <button className="project-preview" onClick={onOpen}>
      <ProjectCover id={project.id} />
      <span className="preview-copy">
        <strong className="preview-title">{project.name}</strong>
        <span className="preview-meta">
          {project.archived ? "已归档" : projectLabels[project.status]} · {openTasks} 项未完成
        </span>
        {excerpt && <span className="preview-next" title={excerpt}><b>下一步</b>{excerpt}</span>}
      </span>
      {compact && <ChevronRight className="preview-arrow" size={15} aria-hidden="true" />}
    </button>
  );
}
