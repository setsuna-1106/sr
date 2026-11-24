import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, Link2, X } from "lucide-react";
import type {
  Asset,
  Milestone,
  Project,
  ResearchRecord,
  Snapshot,
  Task,
} from "./api";
import { recordLabels, taskLabels } from "./api";

export function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const panel = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const previous = document.activeElement;
    const close = panel.current?.querySelector<HTMLButtonElement>(".modal-header button");
    const explicit = panel.current?.querySelector<HTMLElement>("[data-initial-focus]");
    (explicit || close)?.focus({ preventScroll: true });
    return () => {
      requestAnimationFrame(() => {
        if (previous instanceof HTMLElement && previous.isConnected
          && !previous.closest("[inert]") && document.activeElement === document.body) {
          previous.focus({ preventScroll: true });
        }
      });
    };
  }, []);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className={`modal ${wide ? "wide" : ""}`}
        ref={panel}
        onKeyDown={event => {
          if (event.defaultPrevented || event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
            return;
          }
          if (event.key !== "Tab") return;
          const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
            "button, input, select, textarea, a[href], summary, [tabindex]",
          )).filter(item => item.tabIndex >= 0 && !item.matches(":disabled") && item.getClientRects().length > 0);
          const first = controls[0];
          const last = controls.at(-1);
          if ((event.shiftKey && document.activeElement === first)
            || (!event.shiftKey && document.activeElement === last)) {
            event.preventDefault();
            (event.shiftKey ? last : first)?.focus();
          }
        }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal-header">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Select({
  value,
  onChange,
  children,
  disabled = false,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <span className="select-wrap">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {children}
      </select>
      <ChevronDown size={14} />
    </span>
  );
}
export function FormActions({
  onClose,
  busy,
  label = "保存",
  onDelete,
}: {
  onClose: () => void;
  busy: boolean;
  label?: string;
  onDelete?: () => void;
}) {
  return (
    <footer className="form-actions">
      {onDelete && <button type="button" className="text-button delete-text form-delete"
        disabled={busy} onClick={onDelete}>删除任务</button>}
      <button
        type="button"
        className="button"
        onClick={onClose}
        disabled={busy}
      >
        取消
      </button>
      <button className="button primary" disabled={busy}>
        {busy ? "正在保存…" : label}
      </button>
    </footer>
  );
}
type Submit = (values: Record<string, unknown>) => Promise<void>;
function fileLinkTarget(value: string): string {
  if (!value.startsWith("/file/")) return value;
  const path = value.slice("/file/".length);
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}
function evidenceTarget(task: Task): string {
  if (task.evidenceUrl) return fileLinkTarget(task.evidenceUrl);
  const value = task.evidence || "";
  if (/^(https?:|zotero:|mailto:)/i.test(value) || value.startsWith("/"))
    return fileLinkTarget(value);
  // Existing board evidence paths are relative to projects/KANBAN.md.
  return `projects/${value}`;
}
interface FormBase {
  snapshot: Snapshot;
  onSubmit: Submit;
  onClose: () => void;
}
export function TaskForm({
  snapshot,
  task,
  projectId,
  initialStatus,
  source,
  onSubmit,
  onClose,
  onOpen,
  onDelete,
}: {
  task?: Task;
  projectId?: string;
  initialStatus?: Task["status"];
  source?: { id: string; path: string; title: string };
  onOpen?: (path: string) => void;
  onDelete?: () => void;
} & FormBase) {
  const [title, setTitle] = useState(task?.title || "");
  const [project, setProject] = useState(task?.project_id || projectId || "");
  const [milestone, setMilestone] = useState(task?.milestone_id || "");
  const [status, setStatus] = useState(task?.status ?? initialStatus ?? "ready");
  const [due, setDue] = useState(task?.due_date || "");
  const [followup, setFollowup] = useState(task?.followup || "");
  const [blocker, setBlocker] = useState(task?.blocker || "");
  const [evidence, setEvidence] = useState(task?.evidence || "");
  const [notes, setNotes] = useState(task?.notes || "");
  const [quote, setQuote] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={task ? "编辑任务" : "一项可以推进的事"}
      subtitle="先写下具体动作，其余信息可以之后补充。"
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit({
              title: title.trim(),
              project_id: project || null,
              milestone_id: milestone || null,
              status,
              due_date: due,
              followup,
              blocker,
              evidence,
              ...(source ? { source_record_id: source.id } : {}),
              notes: source
                ? `${notes}\n\n来源记录：${source.title}${quote ? `\n\n来源文字：${quote}` : ""}`.trim()
                : notes,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="任务名称">
            <input
              data-initial-focus
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：核对光子源设计的输入条件"
            />
          </Field>
          <div className="form-grid">
            <Field label="归属">
              <Select
                value={project}
                onChange={(v) => {
                  setProject(v);
                  setMilestone("");
                }}
              >
                <option value="">候选 / 待整理</option>
                {snapshot.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="里程碑 · 可选">
              <Select
                value={milestone}
                onChange={setMilestone}
                disabled={!project}
              >
                <option value="">直接归属项目</option>
                {snapshot.milestones
                  .filter((m) => m.project_id === project)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.title}
                    </option>
                  ))}
              </Select>
            </Field>
          </div>
          <div className="form-grid">
            <Field label="状态">
              <Select
                value={status}
                onChange={(v) => setStatus(v as Task["status"])}
              >
                {Object.entries(taskLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="截止日期 · 可选">
              <input
                type="date"
                value={due}
                onChange={(e) => setDue(e.target.value)}
              />
            </Field>
          </div>
          {status === "waiting" && (
            <div className="form-grid">
              <Field label="在等什么">
                <input
                  value={blocker}
                  onChange={(e) => setBlocker(e.target.value)}
                  placeholder="等待的资料、答复或条件"
                />
              </Field>
              <Field label="下次跟进">
                <input
                  type="date"
                  value={followup}
                  onChange={(e) => setFollowup(e.target.value)}
                />
              </Field>
            </div>
          )}
          <Field label="结果或材料链接 · 可选">
            <input
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
              placeholder="文件路径或网页地址"
            />
          </Field>
          <Field label="详情与完成条件 · 可选">
            <textarea
              rows={4}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="怎样算做完？需要哪些材料？"
            />
          </Field>
          {task && (
            <section className="task-materials">
              <h3>材料与相关记录</h3>
              {task.links?.map((link, index) => {
                const path = link.href || link.path || link.url || "";
                return path ? (
                  <button
                    type="button"
                    className="related-link"
                    key={index}
                    onClick={() => onOpen?.(fileLinkTarget(path))}
                  >
                    <Link2 size={13} />
                    {link.label || link.title || path}
                  </button>
                ) : null;
              })}
              {(task.evidenceUrl || task.evidence) && (
                <button
                  type="button"
                  className="related-link"
                  onClick={() => onOpen?.(evidenceTarget(task))}
                >
                  <Link2 size={13} />
                  打开关联结果
                </button>
              )}
              {snapshot.assets
                .filter((asset) => asset.task_ids?.includes(task.id))
                .map((asset) => (
                  <button
                    type="button"
                    className="related-link"
                    key={asset.id}
                    onClick={() => onOpen?.(asset.path)}
                  >
                    <Link2 size={13} />
                    {asset.title || asset.path}
                  </button>
                ))}
              {snapshot.records
                .filter(
                  (record) =>
                    record.task_ids?.includes(task.id) ||
                    record.task_id === task.id,
                )
                .map((record) => (
                  <button
                    type="button"
                    className="related-link"
                    key={record.id}
                    onClick={() => onOpen?.(record.path)}
                  >
                    <Link2 size={13} />
                    {record.title}
                  </button>
                ))}
            </section>
          )}
          {source && (
            <div className="source-field">
              <p>
                <Link2 size={14} />
                来自：{source.title}
              </p>
              <Field label="保留的来源文字 · 可选">
                <textarea
                  rows={2}
                  value={quote}
                  onChange={(e) => setQuote(e.target.value)}
                  placeholder="填写纪要中的原句，保留建议与决定的区别"
                />
              </Field>
            </div>
          )}
        </div>
        <FormActions
          onClose={onClose}
          busy={busy}
          label={task ? "保存修改" : "创建任务"}
          onDelete={task ? onDelete : undefined}
        />
      </form>
    </Modal>
  );
}
export function ProjectForm({
  project,
  onSubmit,
  onClose,
}: {
  project?: Project;
  onSubmit: Submit;
  onClose: () => void;
}) {
  const [name, setName] = useState(project?.name || "");
  const [goal, setGoal] = useState(project?.goal || "");
  const [stage, setStage] = useState(project?.stage || "");
  const [next, setNext] = useState(project?.next_action || "");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={project ? "编辑项目" : "建立研究项目"}
      subtitle="项目承载目标、资料与进度。研究阶段由你判断。"
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit({
              name: name.trim(),
              goal,
              stage,
              next_action: next,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="项目名称">
            <input
              data-initial-focus
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="给研究问题起一个清楚的名字"
            />
          </Field>
          <Field label="研究目标">
            <textarea
              rows={3}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="这个项目要解决什么问题？"
            />
          </Field>
          <Field label="当前研究阶段">
            <input
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              placeholder="例如：界定问题、建立基线、实验验证"
            />
          </Field>
          <Field label="下一步第一件事">
            <textarea
              rows={2}
              value={next}
              onChange={(e) => setNext(e.target.value)}
              placeholder="下次打开项目，可以直接做什么？"
            />
          </Field>
        </div>
        <FormActions
          onClose={onClose}
          busy={busy}
          label={project ? "保存修改" : "创建项目"}
        />
      </form>
    </Modal>
  );
}
export function MilestoneForm({
  snapshot,
  milestone,
  projectId,
  onSubmit,
  onClose,
}: { milestone?: Milestone; projectId?: string } & FormBase) {
  const [title, setTitle] = useState(milestone?.title || "");
  const [project, setProject] = useState(
    milestone?.project_id || projectId || snapshot.projects[0]?.id || "",
  );
  const [completion, setCompletion] = useState(milestone?.completion || "");
  const [due, setDue] = useState(milestone?.due_date || "");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={milestone ? "编辑里程碑" : "设定阶段交付"}
      subtitle="里程碑可选；是否完成由你确认。"
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit({
              title: title.trim(),
              project_id: project,
              completion,
              due_date: due,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="交付名称">
            <input
              required
              data-initial-focus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="这一阶段希望拿出什么？"
            />
          </Field>
          <Field label="所属项目">
            <Select value={project} onChange={setProject}>
              {snapshot.projects.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="完成条件">
            <textarea
              rows={3}
              value={completion}
              onChange={(e) => setCompletion(e.target.value)}
              placeholder="需要哪些可核对的结果或证据？"
            />
          </Field>
          <Field label="目标日期 · 可选">
            <input
              type="date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
          </Field>
        </div>
        <FormActions onClose={onClose} busy={busy} />
      </form>
    </Modal>
  );
}
export function RecordForm({
  snapshot,
  projectId,
  taskId,
  kind = "note",
  onSubmit,
  onClose,
}: { projectId?: string; taskId?: string; kind?: string } & FormBase) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState(kind);
  const [project, setProject] = useState(projectId || "");
  const [task, setTask] = useState(taskId || "");
  const [date, setDate] = useState("");
  const [body, setBody] = useState("");
  const [runStatus, setRunStatus] = useState("planned");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="记录值得留下的内容"
      subtitle="创建后可在 Markdown 编辑器中继续补充。"
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const scaffold =
            type === "experiment"
              ? "\n\n## 输入数据与版本\n\n## 代码与运行命令\n\n## 参数与环境\n\n## 输出与结果边界\n\n## 问题与下一步\n"
              : type === "meeting"
                ? "\n\n## 讨论记录\n\n## 已确认决定\n\n## 建议与待确认\n\n## 后续行动\n"
                : "";
          try {
            await onSubmit({
              title: title.trim(),
              kind: type,
              project_id: project || null,
              task_id: task || null,
              date,
              status: type === "experiment" ? runStatus : undefined,
              content: `${body}${scaffold}`,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="标题">
            <input
              required
              data-initial-focus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="这次发现、讨论或实验的主题"
            />
          </Field>
          <div className="form-grid">
            <Field label="记录类型">
              <Select value={type} onChange={setType}>
                {["note", "meeting", "experiment"].map((k) => (
                  <option value={k} key={k}>
                    {recordLabels[k]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="发生日期 · 可选">
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </Field>
          </div>
          <div className="form-grid">
            <Field label="所属项目 · 可选">
              <Select
                value={project}
                onChange={(v) => {
                  setProject(v);
                  setTask("");
                }}
              >
                <option value="">暂不关联</option>
                {snapshot.projects.map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="关联任务 · 可选">
              <Select value={task} onChange={setTask}>
                <option value="">暂不关联</option>
                {snapshot.tasks
                  .filter((t) => !project || t.project_id === project)
                  .map((t) => (
                    <option value={t.id} key={t.id}>
                      {t.title}
                    </option>
                  ))}
              </Select>
            </Field>
          </div>
          {type === "experiment" && (
            <Field label="本次运行情况">
              <Select value={runStatus} onChange={setRunStatus}>
                <option value="planned">计划中</option>
                <option value="running">进行中（手动登记）</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
                <option value="interrupted">中断</option>
              </Select>
            </Field>
          )}
          <Field label="先记几句 · 可选">
            <textarea
              rows={4}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="只记实际发生的内容，稍后可以继续编辑。"
            />
          </Field>
        </div>
        <FormActions onClose={onClose} busy={busy} label="创建并编辑" />
      </form>
    </Modal>
  );
}
function MultiCheck({
  options,
  values,
  onChange,
}: {
  options: Array<{ id: string; label: string }>;
  values: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="check-list">
      {options.length ? (
        options.map((option) => (
          <label key={option.id}>
            <input
              type="checkbox"
              checked={values.includes(option.id)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...values, option.id]
                    : values.filter((id) => id !== option.id),
                )
              }
            />
            <span>{option.label}</span>
          </label>
        ))
      ) : (
        <span className="muted">暂无可关联项</span>
      )}
    </div>
  );
}
export function AssetForm({
  snapshot,
  asset,
  onSubmit,
  onClose,
}: { asset: Asset } & FormBase) {
  const [title, setTitle] = useState(asset.title || asset.name || "");
  const [path, setPath] = useState(asset.path);
  const [purpose, setPurpose] = useState(asset.purpose || "");
  const [tags, setTags] = useState(asset.tags?.join(", ") || "");
  const [projects, setProjects] = useState(asset.project_ids || []);
  const [tasks, setTasks] = useState(asset.task_ids || []);
  const [milestones, setMilestones] = useState(asset.milestone_ids || []);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="资产详情与关联"
      subtitle="只维护引用和用途，原文件保存在原位置。"
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit({
              title,
              path,
              purpose,
              tags: tags
                .split(/[,，]/)
                .map((t) => t.trim())
                .filter(Boolean),
              project_ids: projects,
              task_ids: tasks,
              milestone_ids: milestones,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="显示名称">
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field
            label="文件位置"
            hint="文件移动后可在此重新定位；不会搬移或覆盖文件。"
          >
            <input
              required
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
          </Field>
          {path !== asset.path && (
            <p className="notice warning">
              请核对新位置指向同一原件；如果是新版本，请新建资产关联。
            </p>
          )}
          <Field label="用途">
            <textarea
              rows={2}
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </Field>
          <Field label="标签">
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="用逗号分隔"
            />
          </Field>
          <Field label="关联项目">
            <MultiCheck
              options={snapshot.projects.map((p) => ({
                id: p.id,
                label: p.name,
              }))}
              values={projects}
              onChange={setProjects}
            />
          </Field>
          <Field label="关联任务">
            <MultiCheck
              options={snapshot.tasks.map((t) => ({
                id: t.id,
                label: t.title,
              }))}
              values={tasks}
              onChange={setTasks}
            />
          </Field>
          <Field label="关联里程碑">
            <MultiCheck
              options={snapshot.milestones.map((m) => ({
                id: m.id,
                label: m.title,
              }))}
              values={milestones}
              onChange={setMilestones}
            />
          </Field>
        </div>
        <FormActions onClose={onClose} busy={busy} />
      </form>
    </Modal>
  );
}
export function RecordLinksForm({
  snapshot,
  record,
  onSubmit,
  onClose,
}: { record: ResearchRecord } & FormBase) {
  const [projects, setProjects] = useState(
    record.project_ids || (record.project_id ? [record.project_id] : []),
  );
  const [tasks, setTasks] = useState(
    record.task_ids || (record.task_id ? [record.task_id] : []),
  );
  const [assets, setAssets] = useState<string[]>(record.asset_ids || []);
  const [status, setStatus] = useState(record.status || "planned");
  const [date, setDate] = useState(record.date || "");
  const [appendJournal, setAppendJournal] = useState(false);
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="关联记录与研究材料" subtitle={record.title} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit({
              project_ids: projects,
              task_ids: tasks,
              asset_ids: assets,
              date,
              ...(record.kind === "experiment" ? { status } : {}),
              append_journal: appendJournal && projects.length > 0,
              summary,
            });
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-body">
          <Field label="发生日期 · 可选">
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </Field>
          {record.kind === "experiment" && (
            <Field label="运行情况（手动登记）">
              <Select value={status} onChange={setStatus}>
                <option value="planned">计划中</option>
                <option value="running">进行中</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
                <option value="interrupted">中断</option>
              </Select>
            </Field>
          )}
          <Field label="项目">
            <MultiCheck
              options={snapshot.projects.map((p) => ({
                id: p.id,
                label: p.name,
              }))}
              values={projects}
              onChange={setProjects}
            />
          </Field>
          <Field label="任务">
            <MultiCheck
              options={snapshot.tasks.map((t) => ({
                id: t.id,
                label: t.title,
              }))}
              values={tasks}
              onChange={setTasks}
            />
          </Field>
          <Field label="材料与结果">
            <MultiCheck
              options={snapshot.assets
                .filter((a) => a.kind !== "directory")
                .map((a) => ({ id: a.id, label: a.title || a.path }))}
              values={assets}
              onChange={setAssets}
            />
          </Field>
          <label className="journal-toggle">
            <input
              type="checkbox"
              checked={appendJournal}
              disabled={!projects.length}
              onChange={(e) => setAppendJournal(e.target.checked)}
            />
            将来源及一句摘要追加到关联项目日志
          </label>
          {appendJournal && projects.length > 0 && (
            <Field label="本人填写的摘要">
              <input
                required
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="仅记录你选定的发现或进展"
              />
            </Field>
          )}
        </div>
        <FormActions onClose={onClose} busy={busy} />
      </form>
    </Modal>
  );
}
