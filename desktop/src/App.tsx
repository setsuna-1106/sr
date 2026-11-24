import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  Archive,
  Atom,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  CircleDot,
  Clock3,
  ExternalLink,
  File,
  FileImage,
  FileText,
  Flag,
  Folder,
  FolderOpen,
  LayoutDashboard,
  Link2,
  ListFilter,
  LoaderCircle,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
  Unplug,
  X,
} from "lucide-react";
import {
  AssetForm,
  Field,
  MilestoneForm,
  Modal,
  ProjectForm,
  RecordForm,
  RecordLinksForm,
  Select,
  TaskForm,
} from "./Forms";
import { ProjectCover, ProjectPreview, TaskDistribution, projectColor, projectNextAction } from "./WorkspaceVisuals";
import { FileInspector, fileViewKind, formatFileSize } from "./FileInspector";
import "./Overview.css";
import { TaskRow } from "./TaskRow";
import { TaskBoard, unassignedScope } from "./TaskBoard";
import { DeleteConfirmation, TrashDialog, type DeleteSelection } from "./Trash";
const Editor = lazy(() => import("./Editor"));
import type { EditorHandle } from "./Editor";
import {
  baseName,
  chooseDirectory,
  dirname,
  errorDetail,
  isDesktop,
  openPath,
  openUrl,
  projectLabels,
  recordLabels,
  rpc,
  taskLabels,
  writeRpc,
} from "./api";
import type {
  Asset,
  FileEntry,
  FileListing,
  Milestone,
  Paper,
  Project,
  ResearchRecord,
  SearchHit,
  Snapshot,
  Task,
  TaskStatus,
  WriteResult,
} from "./api";

type Page = "overview" | "projects" | "board" | "assets" | "records" | "search";
type Dialog =
  | {
      type: "task";
      task?: Task;
      projectId?: string;
      initialStatus?: TaskStatus;
      source?: { id: string; path: string; title: string };
    }
  | { type: "project"; project?: Project }
  | { type: "milestone"; milestone?: Milestone; projectId?: string }
  | { type: "record"; projectId?: string; taskId?: string; kind?: string }
  | { type: "asset"; asset: Asset }
  | { type: "recordLinks"; record: ResearchRecord }
  | { type: "settings" }
  | { type: "trash" }
  | { type: "image"; path: string; dataUrl: string }
  | { type: "paper"; paper: Paper };
const navGroups: Array<{ label: string; title?: string; items: { id: Page; label: string; icon: typeof Folder }[] }> = [
  { label: "工作区", items: [
    { id: "overview", label: "总览", icon: LayoutDashboard },
    { id: "projects", label: "项目", icon: FolderOpen },
  ] },
  { label: "资料库", title: "资料库", items: [
    { id: "assets", label: "资产", icon: BookOpen },
    { id: "records", label: "记录", icon: FileText },
  ] },
];
const pageTitles: Record<Page, string> = {
  overview: "科研总览",
  projects: "研究项目",
  board: "研究项目",
  assets: "资产与文献",
  records: "研究记录",
  search: "搜索工作区",
};
type NewDialog = Extract<Dialog, { type: "task" | "project" | "record" }>;
type Appearance = "system" | "light" | "dark";
const newLabels = { task: "新建任务", project: "新建项目", record: "新建记录" };

const emptySnapshot: Snapshot = {
  root: "",
  revision: "",
  projects: [],
  tasks: [],
  milestones: [],
  assets: [],
  records: [],
  papers: [],
};
function normalizeSnapshot(data: Snapshot): Snapshot {
  return {
    ...emptySnapshot,
    ...data,
    projects: data.projects || [],
    tasks: data.tasks || [],
    assets: data.assets || [],
    milestones: data.milestones || [],
    papers: data.papers || [],
    records: data.records || [],
  };
}
function Empty({
  icon = FolderOpen,
  title,
  description,
  action,
}: {
  icon?: typeof Folder;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  const Icon = icon;
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Icon size={25} strokeWidth={1.3} />
      </div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
function shortDate(value?: string) {
  if (!value) return "";
  return value.slice(0, 10).replace(/-/g, ".");
}
function relationProject(record: ResearchRecord, project: string) {
  return record.project_id === project || record.project_ids?.includes(project);
}

export default function App() {
  const [snapshot, setSnapshotState] = useState<Snapshot | null>(null);
  const [page, setPage] = useState<Page>("overview");
  const [loading, setLoading] = useState(false);
  const [selectedProject, setSelectedProject] = useState("");
  const [boardScope, setBoardScope] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [projectGroup, setProjectGroup] = useState("active");
  const [assetTab, setAssetTab] = useState<"files" | "featured" | "papers">(
    "files",
  );
  const [paperQuery, setPaperQuery] = useState("");
  const [recordType, setRecordType] = useState("all");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [deletion, setDeletion] = useState<DeleteSelection | null>(null);
  const [toast, setToast] = useState("");
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [directory, setDirectory] = useState("");
  const [listing, setListing] = useState<FileListing | null>(null);
  const [inspectedPath, setInspectedPath] = useState<string | null>(null);
  const [filesLoading, setFilesLoading] = useState(false);
  const [fileError, setFileError] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [editorPath, setEditorPath] = useState<string | null>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState<null | (() => void)>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [working, setWorking] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [libraryExpanded, setLibraryExpanded] = useState(true);
  const [projectsExpanded, setProjectsExpanded] = useState(true);
  const [appearance, setAppearance] = useState<Appearance>(() => {
    const stored = localStorage.getItem("sr-appearance");
    return stored === "light" || stored === "dark" ? stored : "system";
  });
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
  const dark = appearance === "system" ? systemDark : appearance === "dark";
  useEffect(() => {
    const preference = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setSystemDark(preference.matches);
    preference.addEventListener("change", update);
    update();
    return () => preference.removeEventListener("change", update);
  }, []);
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  const [lastPath, setLastPath] = useState(
    () => localStorage.getItem("sr-workspace") || "",
  );
  const editorRef = useRef<EditorHandle>(null);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const dirtyRef = useRef(editorDirty);
  dirtyRef.current = editorDirty;
  const searchRef = useRef<HTMLInputElement>(null);
  const projectHeadingRef = useRef<HTMLHeadingElement>(null);
  const initialized = useRef(false);
  const fileListRequest = useRef(0);
  const notify = useCallback((message: string) => setToast(message), []);
  const setSnapshot = useCallback((data: Snapshot) => {
    setSnapshotState(normalizeSnapshot(data));
  }, []);
  const refresh = useCallback(async () => {
    if (!snapshotRef.current) return;
    try {
      setSnapshot(await rpc<Snapshot>("workspace.snapshot"));
    } catch (error) {
      notify(errorDetail(error).message);
    }
  }, [setSnapshot, notify]);
  const guard = useCallback((action: () => void) => {
    if (dirtyRef.current) setConfirmLeave(() => action);
    else action();
  }, []);
  const focusSearch = useCallback(() => {
    guard(() => {
      setEditorPath(null);
      setCollapsed(false);
      requestAnimationFrame(() => searchRef.current?.focus());
    });
  }, [guard]);
  const openEditor = useCallback(
    (path: string) => guard(() => setEditorPath(path)),
    [guard],
  );
  const navigate = (destination: Page) =>
    guard(() => {
      setEditorPath(null);
      if (destination === "projects") setSelectedProject("");
      setPage(destination);
    });
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 6500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    if (lastPath && isDesktop()) void openWorkspace(lastPath);
  }, []);
  useEffect(() => {
    if (!isDesktop()) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<{ quit?: boolean }>("workbench-close-requested", (event) => {
      guard(() => {
        void invoke("close_window", { quit: !!event.payload?.quit }).catch(
          (error) => notify(errorDetail(error).message),
        );
      });
    }).then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [guard, notify]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", unload);
    return () => window.removeEventListener("beforeunload", unload);
  }, []);
  async function openWorkspace(path: string) {
    setLoading(true);
    try {
      const data = await rpc<Snapshot>("workspace.open", { path });
      setSnapshot(data);
      setLastPath(path);
      localStorage.setItem("sr-workspace", path);
      fileListRequest.current++;
      setDirectory("");
      setListing(null);
      setInspectedPath(null);
      setFilesLoading(false);
      setFileError("");
      setSelectedProject("");
      setProjectGroup("active");
      setBoardScope("");
      setProjectFilter("");
      setPage("overview");
      setDeletion(null);
      setEditorPath(null);
      setDialog(null);
    } catch (error) {
      notify(errorDetail(error).message);
    } finally {
      setLoading(false);
    }
  }
  async function chooseWorkspace() {
    try {
      const path = await chooseDirectory(true);
      if (path) guard(() => void openWorkspace(path));
    } catch (error) {
      notify(errorDetail(error).message);
    }
  }
  async function write(
    method: string,
    params: Record<string, unknown>,
    expectedRevision?: string,
  ): Promise<WriteResult | null> {
    const data = snapshotRef.current;
    if (!data) return null;
    setWorking(true);
    try {
      const result = await writeRpc(
        method,
        { ...params, revision: expectedRevision ?? data.revision },
        data.root,
      );
      setSnapshot(result.snapshot);
      return result;
    } catch (error) {
      const detail = errorDetail(error);
      if (
        detail.code === "conflict" ||
        detail.code === "recovery_pending" ||
        detail.code === "recovery_required"
      )
        await refresh();
      notify(detail.message);
      return null;
    } finally {
      setWorking(false);
    }
  }
  async function recover(id: string, action: "resume" | "keep_current") {
    setWorking(true);
    try {
      const result = await rpc<{
        snapshot: Snapshot;
        results: Array<{ status: string; error?: string }>;
      }>("workspace.recover", { operation_id: id, action });
      setSnapshot(result.snapshot);
      const unresolved = result.results.find(
        (item) => item.status === "conflict",
      );
      notify(
        unresolved
          ? "仍有文件冲突。请核对当前内容，或明确保留当前文件。"
          : action === "resume"
            ? "恢复处理已完成。"
            : "已保留当前文件，原恢复资料仍可核查。",
      );
    } catch (error) {
      notify(errorDetail(error).message);
    } finally {
      setWorking(false);
    }
  }
  async function taskChange(task: Task, changes: Record<string, unknown>) {
    const result = await write("task.update", { id: task.id, changes });
    if (!result) return;
    const status = changes.status as TaskStatus | undefined;
    if (status) {
      notify(status === "done" ? `已完成「${task.title}」` : `「${task.title}」已移至${taskLabels[status]}`);
      requestAnimationFrame(() => {
        if (document.activeElement === document.body) {
          document.querySelector<HTMLElement>(".task-featured .task-main, .page h1")?.focus({ preventScroll: true });
        }
      });
    } else if (typeof changes.pinned === "boolean") {
      notify(changes.pinned ? "已置顶任务" : "已取消置顶");
    }
  }
  async function moveBoardTask(task: Task, status: TaskStatus, revision: string) {
    const result = await write(
      "task.update", { id: task.id, changes: { status } }, revision,
    );
    if (result) notify(`${task.id} 已移至「${taskLabels[status]}」`);
    return !!result;
  }
  async function projectChange(
    project: Project,
    changes: Record<string, unknown>,
  ) {
    await write("project.update", { id: project.id, changes });
  }
  const loadDirectory = useCallback(async (path: string, quiet = false) => {
    const request = ++fileListRequest.current;
    if (!quiet) setFilesLoading(true);
    setFileError("");
    try {
      const result = await rpc<FileListing>("files.list", { path, show_hidden: showHidden });
      if (request === fileListRequest.current) setListing(result);
    } catch (error) {
      if (request === fileListRequest.current) setFileError(errorDetail(error).message);
    } finally {
      if (request === fileListRequest.current) setFilesLoading(false);
    }
  }, [showHidden]);
  function browse(path: string) {
    if (path === directory) {
      void loadDirectory(path);
      return;
    }
    fileListRequest.current++;
    setInspectedPath(null);
    setListing(null);
    setFilesLoading(true);
    setDirectory(path);
  }
  const browsing = !!snapshot && page === "assets" && assetTab === "files";
  useEffect(() => {
    if (browsing) void loadDirectory(directory);
  }, [browsing, directory, loadDirectory, snapshot?.root]);
  useEffect(() => {
    const focus = () => {
      void refresh();
      if (browsing) void loadDirectory(directory, true);
    };
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, [browsing, directory, loadDirectory, refresh]);
  async function safeOpen(path: string) {
    try {
      if (/^(https?:|zotero:|mailto:)/i.test(path)) {
        await openUrl(path);
        return;
      }
      const resolved = await rpc<{ path: string }>("files.resolve", { path });
      await openPath(resolved.path);
    } catch (error) {
      notify(errorDetail(error).message);
    }
  }
  async function viewFile(entry: FileEntry | { path: string; kind?: string }) {
    if (entry.kind === "directory") {
      void browse(entry.path);
      return;
    }
    const fileKind = fileViewKind(entry.path);
    if (fileKind === "text") {
      openEditor(entry.path);
      return;
    }
    if (fileKind === "image") {
      try {
        const result = await rpc<{ data_url: string }>("files.preview", {
          path: entry.path,
        });
        setDialog({
          type: "image",
          path: entry.path,
          dataUrl: result.data_url,
        });
      } catch (error) {
        notify(errorDetail(error).message);
      }
      return;
    }
    void safeOpen(entry.path);
  }
  async function addDirectory() {
    try {
      const path = await chooseDirectory();
      if (!path) return;
      const result = await write("asset.link", {
        path,
        kind: "directory",
        project_id: projectFilter || selectedProject || null,
        featured: false,
      });
      if (result) void browse(path);
    } catch (error) {
      notify(errorDetail(error).message);
    }
  }
  async function feature(entry: FileEntry) {
    const existing = snapshot?.assets.find(
      (asset) => asset.path === entry.path,
    );
    if (existing) {
      await write("asset.update", {
        id: existing.id,
        changes: { featured: !existing.featured },
      });
    } else
      await write("asset.link", {
        path: entry.path,
        kind: "file",
        featured: true,
        project_id: projectFilter || null,
      });
  }
  useEffect(() => {
    if (page !== "search" || !snapshot) return;
    const value = query.trim();
    if (!value) {
      setSearchHits([]);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      setSearching(true);
      rpc<{ results: SearchHit[] }>("search", { query: value })
        .then((result) => {
          if (live) setSearchHits(result.results);
        })
        .catch((error) => {
          if (live) notify(errorDetail(error).message);
        })
        .finally(() => {
          if (live) setSearching(false);
        });
    }, 220);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, page, snapshot?.revision, notify]);
  const current = snapshot || emptySnapshot;
  const projectById = (id: string | null | undefined) =>
    current.projects.find((p) => p.id === id);
  const activeProjects = current.projects.filter(
    (p) => p.status === "active" && !p.archived,
  );
  const isActiveTask = (task: Task) =>
    !!task.project_id && activeProjects.some((p) => p.id === task.project_id);
  const sortTasks = (tasks: Task[]) =>
    [...tasks].sort((a, b) =>
      (a.status === "waiting"
        ? a.followup || a.due_date || "9999"
        : a.due_date || "9999"
      ).localeCompare(
        b.status === "waiting"
          ? b.followup || b.due_date || "9999"
          : b.due_date || "9999",
      ),
    );
  const overviewProject = activeProjects.find(p => p.id === projectFilter);
  const activeTasks = current.tasks.filter(isActiveTask);
  const scopedTasks = overviewProject
    ? activeTasks.filter(t => t.project_id === overviewProject.id)
    : activeTasks;
  const openTasks = sortTasks(scopedTasks.filter(t => t.status !== "done"));
  const featuredTask = openTasks.find(t => t.status === "doing" && t.pinned)
    || openTasks.find(t => t.status === "doing")
    || openTasks.find(t => t.status === "ready" && t.pinned)
    || openTasks.find(t => t.status === "ready");
  const remainingTasks = openTasks.filter(task => task.id !== featuredTask?.id);
  const unassigned = current.tasks.filter(
    (t) => !t.project_id && t.status !== "done",
  );
  const tasksForProject = (id: string) =>
    current.tasks.filter((t) => t.project_id === id);
  const createTask = (projectId?: string, initialStatus?: TaskStatus) =>
    setDialog({ type: "task", projectId, initialStatus });
  const boardProject = projectById(boardScope);
  const scope = boardScope === unassignedScope
    ? unassignedScope
    : boardProject?.id || "";
  const visibleProjects = current.projects.filter(p => projectGroup === "all" ||
    (projectGroup === "archived" ? p.archived : !p.archived && p.status === projectGroup));
  const detailProject = page === "projects" ? projectById(selectedProject) : undefined;
  const selectedShortcut = !editorPath && activeProjects.some(project => project.id === detailProject?.id);
  const pageProject = page === "overview" ? overviewProject
    : page === "board" ? boardProject
    : page === "projects" ? detailProject
    : page === "assets" || page === "records" ? projectById(projectFilter) : undefined;

  const primaryNew: NewDialog | null = editorPath || page === "search" || page === "assets" ? null
    : page === "projects" && !detailProject ? { type: "project" }
    : page === "records" ? {
      type: "record", projectId: projectFilter || undefined,
      kind: recordType === "all" ? "note" : recordType,
    } : {
      type: "task",
      projectId: (page === "projects" ? detailProject : page === "board" ? boardProject : overviewProject)?.id,
    };
  const hasWorkspace = !!snapshot;
  const primaryNewRef = useRef(primaryNew);
  primaryNewRef.current = primaryNew;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (!hasWorkspace || deletion || dialog || confirmLeave || e.isComposing || e.defaultPrevented) return;
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
      if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        focusSearch();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        if (!e.repeat) setDialog(primaryNewRef.current || { type: "task" });
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [focusSearch, confirmLeave, deletion, dialog, hasWorkspace]);

  useEffect(() => {
    if ((page === "projects" || page === "board") && !editorPath) {
      projectHeadingRef.current?.focus({ preventScroll: true });
    }
  }, [page, editorPath, detailProject?.id]);

  function openBoard(projectId = "") {
    setBoardScope(projectId);
    navigate("board");
  }

  function projectViews() {
    return <div className="segmented project-views" role="group" aria-label="项目视图">
      <button aria-pressed={page === "projects"} className={page === "projects" ? "active" : ""}
        onClick={() => { if (page !== "projects") navigate("projects"); }}>项目库</button>
      <button aria-pressed={page === "board"} className={page === "board" ? "active" : ""}
        onClick={() => { if (page !== "board") openBoard(); }}>任务看板</button>
    </div>;
  }

  function openActiveProject(project: Project) {
    guard(() => {
      setProjectGroup("active");
      setSelectedProject(project.id);
      setPage("projects");
      setEditorPath(null);
    });
  }

  function requestDelete(items: Task[], project?: Project) {
    setDeletion({
      tasks: items, project, revision: current.revision,
      milestones: project ? current.milestones.filter(m => m.project_id === project.id).length : 0,
    });
  }

  async function confirmDelete() {
    if (!deletion || working) return;
    const { project, tasks: items, revision } = deletion;
    const result = project
      ? await write("project.delete", { id: project.id }, revision)
      : await write("task.delete", { ids: items.map(t => t.id) }, revision);
    setDeletion(null);
    if (!result) return;
    setDialog(null);
    if (project) {
      if (selectedProject === project.id) setSelectedProject("");
      if (boardScope === project.id) setBoardScope("");
      if (projectFilter === project.id) setProjectFilter("");
      const folder = `${current.root}/${project.path}`;
      if (directory === folder || directory.startsWith(folder + "/")) {
        browse("");
      }
    }
    notify(project ? `项目「${project.name}」已移入回收站` : `${items.length} 张任务卡已移入回收站`);
  }

  function taskRow(task: Task, featured = false) {
    return (
      <TaskRow key={task.id} task={task}
        owner={projectById(task.project_id)}
        milestone={current.milestones.find(m => m.id === task.milestone_id)?.title}
        featured={featured} busy={working}
        onOpen={() => setDialog({ type: "task", task })}
        onStatus={status => void taskChange(task, { status })}
        onPin={() => void taskChange(task, { pinned: !task.pinned })}
        onRecord={() => setDialog({
          type: "record", projectId: task.project_id || undefined, taskId: task.id,
        })}
        onDelete={() => requestDelete([task])} />
    );
  }
  function taskSection(title: string, items: Task[], Icon: typeof Folder) {
    if (!items.length) return null;
    return (
      <section className="task-section">
        <h3><Icon size={15} />{title}<span>{items.length}</span></h3>
        <div className="task-list">{items.map(task => taskRow(task))}</div>
      </section>
    );
  }
  function projectPreviews(projects: Project[], onOpen: (project: Project) => void, compact = false) {
    return (
      <div className={`project-previews ${compact ? "compact" : ""}`}>
        {projects.map(project => (
          <ProjectPreview key={project.id} project={project}
            openTasks={tasksForProject(project.id).filter(task => task.status !== "done").length}
            compact={compact}
            onOpen={() => onOpen(project)} />
        ))}
      </div>
    );
  }
  function renderOverview() {
    return (
      <>
        {current.recovery?.length ? (
          <section className="recovery-notice">
            <h3>有未完成的保存需要处理</h3>
            <p>
              资料和恢复记录已保留。继续恢复原操作，或明确保留磁盘上的当前文件。
            </p>
            {current.recovery.map((item) => (
              <div key={item.id}>
                <span>
                  {item.method || "写入操作"} ·{" "}
                  {item.status === "conflict" ? "检测到冲突" : "等待恢复"}
                </span>
                <button
                  className="button small"
                  disabled={working}
                  onClick={() => void recover(item.id, "resume")}
                >
                  继续恢复
                </button>
                <button
                  className="button small"
                  disabled={working}
                  onClick={() => void recover(item.id, "keep_current")}
                >
                  保留当前文件
                </button>
              </div>
            ))}
          </section>
        ) : null}
        <div className="overview-stack">
          <section className="overview-work" aria-labelledby="work-title">
            <header className="panel-heading">
              <h2 id="work-title">继续研究</h2>
              <label className="filter-select" title="筛选总览任务">
                <ListFilter size={15} aria-hidden="true" />
                <span className="sr-only">筛选总览项目</span>
                <Select value={overviewProject?.id || ""} onChange={setProjectFilter}>
                  <option value="">全部活跃项目</option>
                  {activeProjects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </label>
            </header>
            {featuredTask ? taskRow(featuredTask, true) : (
              <p className="overview-empty">
                {openTasks.length ? "当前任务正在等待跟进" : scopedTasks.length ? "当前任务已全部完成" : "暂无待推进的任务"}
              </p>
            )}
          </section>
          <section className="overview-projects" aria-labelledby="active-projects-title">
            <header className="panel-heading">
              <h2 id="active-projects-title">活跃项目</h2>
              <button className="text-button" onClick={() => { setProjectGroup("all"); navigate("projects"); }}>
                全部项目 <ArrowRight size={14} />
              </button>
            </header>
            {activeProjects.length ? projectPreviews(activeProjects, openActiveProject, true) : (
              <p className="overview-empty">
                尚无活跃项目
                <button className="text-button" onClick={() => setDialog({ type: "project" })}>
                  <Plus size={15} /> 新建项目
                </button>
              </p>
            )}
          </section>
          <section className="overview-queue" aria-labelledby="queue-title">
            <header className="panel-heading">
              <h2 id="queue-title">任务清单</h2>
              <button className="text-button" onClick={() => openBoard(overviewProject?.id)}>
                查看任务看板 <ArrowUpRight size={14} />
              </button>
            </header>
            {taskSection("已置顶", remainingTasks.filter(t => t.pinned), Pin)}
            {taskSection("进行中", remainingTasks.filter(t => !t.pinned && t.status === "doing"), CircleDot)}
            {taskSection("待开工", remainingTasks.filter(t => !t.pinned && t.status === "ready"), ArrowRight)}
            {taskSection("等待与跟进", remainingTasks.filter(t => !t.pinned && t.status === "waiting"), Clock3)}
            {!overviewProject && taskSection("候选与待整理", unassigned, Flag)}
            {!remainingTasks.length && (overviewProject || !unassigned.length) && (
              <p className="overview-empty">{featuredTask ? "暂无其他待办" : "暂无待办"}</p>
            )}
          </section>
          <TaskDistribution tasks={scopedTasks} />
        </div>
      </>
    );
  }
  function renderProjects() {
    if (!detailProject) {
      return (
        <>
          <div className="section-toolbar">
            <div className="tabs" role="group" aria-label="项目分组">
              {[
                ["active", "活跃"],
                ["paused", "暂停"],
                ["completed", "已完成"],
                ["archived", "已归档"],
                ["all", "全部"],
              ].map(([value, label]) => (
                <button key={value} className={projectGroup === value ? "active" : ""}
                  aria-pressed={projectGroup === value} onClick={() => setProjectGroup(value)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {visibleProjects.length
            ? projectPreviews(visibleProjects, project => setSelectedProject(project.id))
            : <Empty title={current.projects.length ? "此分组暂无项目" : "尚未建立项目"} />}
        </>
      );
    }

    const project = detailProject;
    return (
      <article className="project-detail" aria-labelledby="project-title">
        <button className="text-button project-back" onClick={() => navigate("projects")}>
          <ChevronLeft size={16} />研究项目
        </button>
        <header className="project-detail-heading">
          <ProjectCover id={project.id} />
          <div className="project-detail-title">
            <h1 id="project-title" ref={projectHeadingRef} tabIndex={-1}>{project.name}</h1>
            <div className="project-meta">
              <span className={`project-status ${project.status}`}>
                {project.archived ? "已归档" : projectLabels[project.status]}
              </span>
              {project.stage && <span className="project-stage">{project.stage}</span>}
            </div>
          </div>
          <button
            className="text-button"
            title="编辑项目"
            onClick={() => setDialog({ type: "project", project })}
          >
            <Pencil size={17} />
            编辑项目
          </button>
        </header>
        {project.goal && <p className="project-goal">{project.goal}</p>}
        <div className="project-actions">
          <button
            className="button small" aria-keyshortcuts="Meta+N" title="⌘N"
            onClick={() => createTask(project.id)}
          >
            <Plus size={14} />
            新任务
          </button>
          <button
            className="text-button"
            onClick={() =>
              setDialog({ type: "milestone", projectId: project.id })
            }
          >
            <Flag size={14} />
            里程碑
          </button>
          <button
            className="text-button"
            onClick={() =>
              setDialog({ type: "record", projectId: project.id })
            }
          >
            <Pencil size={14} />
            写记录
          </button>
          {project.state_path && (
            <button
              className="text-button"
              onClick={() => openEditor(project.state_path!)}
            >
              <FileText size={14} />
              项目断点
            </button>
          )}
          <details className="project-properties" key={project.id}
            onKeyDown={(e) => {
              if (e.key === "Escape" && e.currentTarget.open) {
                e.currentTarget.open = false;
                e.currentTarget.querySelector("summary")?.focus();
              }
            }}>
            <summary className="text-button"><MoreHorizontal size={15} />项目管理</summary>
            <div className="project-state-menu">
              <Select
                label="项目状态"
                value={project.status}
                onChange={(v) => void projectChange(project, { status: v })}
              >
                <option value="active">活跃</option>
                <option value="paused">暂停</option>
                <option value="completed">已完成</option>
              </Select>
              <button
                className="button small"
                title={project.archived ? "恢复归档" : "归档项目"}
                onClick={() =>
                  void projectChange(project, {
                    archived: !project.archived,
                  })
                }
              >
                <Archive size={16} />
                {project.archived ? "恢复归档" : "归档项目"}
              </button>
              <button className="button small delete-text" disabled={working}
                onClick={() => requestDelete(tasksForProject(project.id), project)}>
                <Trash2 size={14} />删除项目
              </button>
              <span className="project-path" title={project.path}><FolderOpen size={13} />{project.path}</span>
            </div>
          </details>
        </div>
        <section className="project-facts" aria-labelledby="project-next-action">
          <h2 id="project-next-action">下一步第一件事</h2>
          <p>{projectNextAction(project) || "尚未填写下一步"}</p>
        </section>
        {current.milestones.filter((m) => m.project_id === project.id)
          .length > 0 && (
          <section className="milestones">
            <h2>里程碑</h2>
            {current.milestones
              .filter((m) => m.project_id === project.id)
              .map((m) => (
                <div className="milestone-row" key={m.id}>
                  <Flag size={16} />
                  <button
                    onClick={() =>
                      setDialog({ type: "milestone", milestone: m })
                    }
                  >
                    <strong>{m.title}</strong>
                    <small>{m.completion || "完成条件待补充"}</small>
                  </button>
                  <select
                    aria-label="里程碑状态"
                    value={m.status}
                    onChange={(e) =>
                      void write("milestone.update", {
                        id: m.id,
                        changes: { status: e.target.value },
                      })
                    }
                  >
                    <option value="todo">未开始</option>
                    <option value="doing">进行中</option>
                    <option value="done">已确认完成</option>
                  </select>
                </div>
              ))}
          </section>
        )}
        <div className="section-heading">
          <h2>
            项目任务<span>{tasksForProject(project.id).length}</span>
          </h2>
          <button
            className="text-button"
            onClick={() => openBoard(project.id)}
          >
            打开项目看板 <ArrowUpRight size={14} />
          </button>
        </div>
        {tasksForProject(project.id).length ? (
          <div className="task-list">
            {tasksForProject(project.id).map(task => taskRow(task))}
          </div>
        ) : (
          <Empty
            icon={Circle}
            title="还没有任务"
            description="可以直接创建任务，不必先设定里程碑。"
          />
        )}
        <div className="project-related">
          <section>
            <div className="section-heading">
              <h2>相关资料</h2>
              <button
                className="text-button"
                onClick={() => {
                  setProjectFilter(project.id);
                  setAssetTab("featured");
                  navigate("assets");
                }}
              >
                全部 <ArrowUpRight size={13} />
              </button>
            </div>
            {current.assets
              .filter(
                (a) =>
                  a.project_ids?.includes(project.id) &&
                  a.kind !== "directory",
              )
              .slice(0, 6)
              .map((a) => (
                <button
                  className="related-link"
                  key={a.id}
                  onClick={() => void viewFile(a)}
                >
                  <File size={15} />
                  <span>{a.title || baseName(a.path)}</span>
                  <ArrowUpRight size={13} />
                </button>
              ))}
            {!current.assets.some(
              (a) =>
                a.project_ids?.includes(project.id) &&
                a.kind !== "directory",
            ) && <p className="muted">尚未关联重点资料</p>}
          </section>
          <section>
            <div className="section-heading">
              <h2>研究记录</h2>
              <button
                className="text-button"
                onClick={() => {
                  setProjectFilter(project.id);
                  navigate("records");
                }}
              >
                全部 <ArrowUpRight size={13} />
              </button>
            </div>
            {current.records
              .filter((r) => relationProject(r, project.id))
              .slice(0, 6)
              .map((r) => (
                <button
                  className="related-link"
                  key={r.id}
                  onClick={() => openEditor(r.path)}
                >
                  <FileText size={15} />
                  <span>{r.title}</span>
                </button>
              ))}
            {!current.records.some((r) =>
              relationProject(r, project.id),
            ) && <p className="muted">尚未关联研究记录</p>}
          </section>
        </div>
      </article>
    );
  }
  function assetRow(asset: Asset) {
    return (
      <div className="asset-row" key={asset.id}>
        <span className={`file-symbol ${asset.kind}`}>
          <FileText size={19} />
        </span>
        <button className="asset-main" onClick={() => void viewFile(asset)}>
          <strong>{asset.title || asset.name || baseName(asset.path)}</strong>
          <small>{asset.purpose || asset.path}</small>
        </button>
        <span className="asset-tags">
          {asset.project_ids?.slice(0, 1).map((id) => (
            <span key={id}>{projectById(id)?.name || id}</span>
          ))}
          {asset.available === false && (
            <span className="unavailable">暂不可访问</span>
          )}
        </span>
        <button
          className="icon-button"
          title="编辑关联"
          onClick={() => setDialog({ type: "asset", asset })}
        >
          <Link2 size={16} />
        </button>
        <button
          className="icon-button pinned"
          title="取消重点标记"
          onClick={() =>
            void write("asset.update", {
              id: asset.id,
              changes: { featured: false },
            })
          }
        >
          <Pin size={15} />
        </button>
      </div>
    );
  }
  function renderAssets() {
    const assets = current.assets.filter(
      (a) => !projectFilter || a.project_ids?.includes(projectFilter),
    );
    const roots = current.assets.filter(
      (a) =>
        a.kind === "directory" &&
        (!projectFilter || a.project_ids?.includes(projectFilter)),
    );
    const papers = current.papers.filter((p) =>
      `${p.title} ${p.bibkey} ${p.author || ""}`
        .toLowerCase()
        .includes(paperQuery.toLowerCase()),
    );
    const inspected = listing?.entries.find(entry => entry.path === inspectedPath);
    const featuredPaths = new Set(current.assets.filter(asset => asset.featured).map(asset => asset.path));
    return (
      <>
        <div className="section-toolbar">
          <div className="tabs" role="group" aria-label="资产视图">
            {(
              [
                ["files", "目录浏览", FolderOpen],
                ["featured", "重点资产", Pin],
                ["papers", "文献库", BookOpen],
              ] as const
            ).map(([value, label, Icon]) => (
              <button
                key={value}
                className={assetTab === value ? "active" : ""}
                aria-pressed={assetTab === value}
                onClick={() => setAssetTab(value)}
              >
                <Icon size={14} aria-hidden="true" />{label}
                {value === "papers" && <span>{current.papers.length}</span>}
              </button>
            ))}
          </div>
          {assetTab !== "papers" && (
            <Select label="筛选资产项目" value={projectFilter} onChange={setProjectFilter}>
              <option value="">全部项目</option>
              {current.projects.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          )}
        </div>
        {assetTab === "files" && (
          <div className="file-browser">
            <aside className="directory-roots">
              <h3>关联位置</h3>
              <button
                className={
                  directory === "" || directory === current.root
                    ? "selected"
                    : ""
                }
                onClick={() => void browse("")}
              >
                <FolderOpen size={16} />
                <span>科研工作区</span>
              </button>
              {roots.map((a) => (
                <div className="directory-root-item" key={a.id}>
                  <button className={directory === a.path ? "selected" : ""}
                    title={a.title || baseName(a.path)} onClick={() => void browse(a.path)}>
                    <Folder size={16} />
                    <span>{a.title || baseName(a.path)}</span>
                  </button>
                  <button className="icon-button" title="编辑目录关联"
                    aria-label={`编辑目录关联 ${a.title || baseName(a.path)}`}
                    onClick={() => setDialog({ type: "asset", asset: a })}>
                    <MoreHorizontal size={15} />
                  </button>
                </div>
              ))}
            </aside>
            <section className="directory-content">
              <div className="directory-toolbar">
                <div className="breadcrumb">
                  <button
                    title="返回上层"
                    className="icon-button"
                    disabled={!directory || directory === current.root}
                    onClick={() => void browse(dirname(directory))}
                  >
                    <ArrowDownLeft size={15} />
                  </button>
                  <Folder size={14} />
                  <span title={listing?.path || current.root}>
                    {listing?.path || current.root}
                  </span>
                </div>
                <label className="hidden-toggle">
                  <input
                    type="checkbox"
                    checked={showHidden}
                    onChange={(e) => setShowHidden(e.target.checked)}
                  />
                  显示隐藏项
                </label>
                <button
                  className="icon-button"
                  onClick={() => void browse(directory)}
                  title="刷新目录"
                >
                  <RefreshCw size={15} className={filesLoading ? "spin" : ""} />
                </button>
              </div>
              {filesLoading ? (
                <div className="loading-line">
                  <LoaderCircle className="spin" size={17} />
                  读取目录…
                </div>
              ) : fileError ? (
                <Empty
                  icon={Unplug}
                  title="目录暂不可访问"
                  description={fileError}
                />
              ) : listing?.entries.length ? (
                <div className={`file-contents${inspected ? " with-inspector" : ""}`}>
                  <div className="file-table">
                    <div className="file-table-head">
                      <span>名称</span>
                      <span>大小</span>
                      <span>重点</span>
                      <span>预览</span>
                    </div>
                    {listing.entries.map((entry) => (
                      <div className={`file-row${entry.path === inspectedPath ? " selected" : ""}`} key={entry.path}>
                        <button
                          className="file-name"
                          title={entry.name}
                          onClick={() => void viewFile(entry)}
                        >
                          {entry.kind === "directory" ? (
                            <span className="file-glyph directory"><Folder size={15} /></span>
                          ) : fileViewKind(entry.path) === "image" ? (
                            <span className="file-glyph image"><FileImage size={15} /></span>
                          ) : (
                            <span className="file-glyph"><FileText size={15} /></span>
                          )}
                          <span className="file-label">{entry.name}</span>
                          {entry.kind === "directory" && (
                            <ChevronRight size={13} />
                          )}
                        </button>
                        <span className="file-size">
                          {formatFileSize(entry.size)}
                        </span>
                        {entry.kind !== "directory" ? (
                          <button
                            className={`icon-button ${featuredPaths.has(entry.path) ? "pinned" : ""}`}
                            aria-label={`标记${entry.name}为重点资产`}
                            onClick={() => void feature(entry)}
                          >
                            <Pin size={15} />
                          </button>
                        ) : (
                          <span />
                        )}
                        {entry.kind !== "directory" && (
                          <button className="icon-button inspect-button"
                            title={entry.path === inspectedPath ? "收起文件预览" : "在右侧预览"}
                            aria-label={`预览 ${entry.name}`}
                            aria-pressed={entry.path === inspectedPath}
                            onClick={() => setInspectedPath(entry.path === inspectedPath ? null : entry.path)}>
                            <PanelRight size={16} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  {inspected && <FileInspector key={inspected.path} entry={inspected}
                    onClose={() => setInspectedPath(null)}
                    onOpen={() => void viewFile(inspected)}
                    onOpenOriginal={() => void safeOpen(inspected.path)} />}
                </div>
              ) : (
                <Empty
                  title="当前目录没有可见文件"
                />
              )}
            </section>
          </div>
        )}
        {assetTab === "featured" &&
          (assets.some((a) => a.featured && a.kind !== "directory") ? (
            <div className="asset-list">
              {assets
                .filter((a) => a.featured && a.kind !== "directory")
                .map(assetRow)}
            </div>
          ) : (
            <Empty
              icon={Pin}
              title={projectFilter ? "此项目暂无重点资产" : "尚未标记重点资产"}
              description="浏览目录时，点文件旁的图钉即可标记。"
              action={
                <button className="button" onClick={() => setAssetTab("files")}>
                  浏览文件 <ArrowRight size={15} />
                </button>
              }
            />
          ))}
        {assetTab === "papers" && (
          <>
            <label className="library-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                aria-label="搜索已登记文献"
                value={paperQuery}
                onChange={(e) => setPaperQuery(e.target.value)}
                placeholder="按标题、作者或 bibkey 查找文献"
              />
              <span>{papers.length} 篇</span>
            </label>
            <div className="paper-list">
              {papers.map((p) => (
                <div className="paper-row" key={p.bibkey}>
                  <span className="paper-year">{p.year || "—"}</span>
                  <button
                    className="paper-main"
                    onClick={() => setDialog({ type: "paper", paper: p })}
                  >
                    <strong>{p.title}</strong>
                    <small>
                      {p.bibkey}
                      {p.author ? ` · ${p.author}` : ""}
                    </small>
                  </button>
                  <span className="reading-status">{p.status || "未记录"}</span>
                  <button
                    className="icon-button"
                    title="打开精读卡"
                    disabled={!p.note_path}
                    onClick={() => p.note_path && openEditor(p.note_path)}
                  >
                    <FileText size={16} />
                  </button>
                  <button
                    className="icon-button"
                    title="打开论文"
                    onClick={() =>
                      void safeOpen(p.zotero_url || p.pdf_path || p.path || "")
                    }
                  >
                    <ArrowUpRight size={17} />
                  </button>
                </div>
              ))}
            </div>
            {!papers.length && (
              <Empty
                icon={BookOpen}
                title="没有匹配的已登记文献"
                description="新论文继续通过现有 sr 登记流程加入，刷新后显示。"
              />
            )}
            <p className="bottom-note">
              阅读状态来自精读卡。打开论文、留下讨论记录不会自动改变阅读等级。
            </p>
          </>
        )}
      </>
    );
  }
  function renderRecords() {
    const records = current.records.filter(
      (r) =>
        (recordType === "all" || r.kind === recordType) &&
        (!projectFilter || relationProject(r, projectFilter)),
    );
    return (
      <>
        <div className="section-toolbar">
          <div className="tabs" role="group" aria-label="记录类型">
            {[
              ["all", "全部记录"],
              ["note", "笔记"],
              ["meeting", "组会"],
              ["experiment", "实验"],
            ].map(([value, label]) => (
              <button
                className={recordType === value ? "active" : ""}
                aria-pressed={recordType === value}
                key={value}
                onClick={() => setRecordType(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <Select label="筛选记录项目" value={projectFilter} onChange={setProjectFilter}>
            <option value="">全部项目</option>
            {current.projects.map((p) => (
              <option value={p.id} key={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
        {records.length ? (
          <div className="record-list">
            {records.map((record) => (
              <article className="record-row" key={record.id}>
                <div className={`record-kind ${record.kind}`}>
                  <FileText size={20} />
                </div>
                <div className="record-body">
                  <div className="record-meta">
                    <span>
                      {recordLabels[record.kind] || record.kind || "工作记录"}
                    </span>
                    <i />
                    {record.date ? (
                      <time>{shortDate(record.date)}</time>
                    ) : (
                      <span>发生日期未记录</span>
                    )}
                  </div>
                  <button
                    className="record-title"
                    onClick={() => openEditor(record.path)}
                  >
                    {record.title}
                  </button>
                  <p>
                    {record.project_ids
                      ?.map((id) => projectById(id)?.name || id)
                      .join(" / ") ||
                      projectById(record.project_id)?.name ||
                      "暂未关联项目"}
                  </p>
                </div>
                <div className="record-actions">
                  <button
                    className="button small"
                    onClick={() => openEditor(record.path)}
                  >
                    <Pencil size={13} />
                    编辑
                  </button>
                  <button
                    className="icon-button"
                    title="关联项目与材料"
                    onClick={() => setDialog({ type: "recordLinks", record })}
                  >
                    <Link2 size={16} />
                  </button>
                  <button
                    className="icon-button"
                    title="从此记录建立任务"
                    onClick={() =>
                      setDialog({
                        type: "task",
                        projectId: record.project_id || record.project_ids?.[0],
                        source: {
                          id: record.id,
                          path: record.path,
                          title: record.title,
                        },
                      })
                    }
                  >
                    <Plus size={17} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            icon={FileText}
            title={projectFilter || recordType !== "all" ? "当前筛选下暂无记录" : "尚无研究记录"}
            action={
              <button
                className="button primary"
                onClick={() =>
                  setDialog({
                    type: "record",
                    projectId: projectFilter || undefined,
                    kind: recordType === "all" ? "note" : recordType,
                  })
                }
              >
                <Plus size={15} />
                新建记录
              </button>
            }
          />
        )}
      </>
    );
  }
  function renderSearch() {
    return (
      <>
        {query.trim() && !searching && searchHits.length > 0 && (
          <p className="search-description">找到 {searchHits.length} 条结果</p>
        )}
        {searching ? (
          <div className="loading-line">
            <LoaderCircle size={18} className="spin" />
            正在查找…
          </div>
        ) : !query.trim() ? (
          <Empty
            icon={Search}
            title="输入搜索关键词"
            description="可搜索项目、任务、已登记文献与关联的文本记录。"
          />
        ) : searchHits.length ? (
          <div className="search-results">
            {searchHits.map((hit, index) => (
              <button
                key={`${hit.type}-${hit.id}-${index}`}
                className="search-hit"
                onClick={() => {
                  if (hit.type === "project") {
                    setSelectedProject(hit.id);
                    setProjectGroup("all");
                    setPage("projects");
                  } else if (hit.type === "task") {
                    const task = current.tasks.find((t) => t.id === hit.id);
                    if (task) setDialog({ type: "task", task });
                  } else if (hit.type === "paper") {
                    const paper = current.papers.find(
                      (p) => p.bibkey === hit.id || p.id === hit.id,
                    );
                    if (paper) setDialog({ type: "paper", paper });
                    else if (hit.path) void viewFile({ path: hit.path });
                  } else if (hit.path) void viewFile({ path: hit.path });
                }}
              >
                <span className="search-type">
                  {(
                    {
                      project: "项目",
                      task: "任务",
                      paper: "文献",
                      record: "记录",
                      asset: "资产",
                      text: "文本",
                    } as Record<string, string>
                  )[hit.type] || hit.type}
                </span>
                <div>
                  <h3>{hit.title}</h3>
                  {hit.excerpt && <p>{hit.excerpt}</p>}
                  <small>
                    {projectById(hit.project_id)?.name || hit.path || ""}
                  </small>
                </div>
                <ArrowUpRight size={16} />
              </button>
            ))}
          </div>
        ) : (
          <Empty
            icon={Search}
            title="没有找到匹配内容"
            description="尝试更短的关键词。PDF 正文不在当前搜索范围内。"
          />
        )}
      </>
    );
  }

  if (!snapshot)
    return (
      <div className="welcome">
        <div className="welcome-rail" data-tauri-drag-region>
          <span className="brand-icon"><Atom size={20} aria-hidden="true" /></span>
          <span>科研工作台</span>
        </div>
        <main className="welcome-main">
          <h1>打开科研工作区</h1>
          <p className="welcome-copy">选择已有的科研目录，继续管理项目、任务和研究记录。</p>
          <button
            className="button primary large"
            onClick={() => void chooseWorkspace()}
            disabled={loading || !isDesktop()}
          >
            {loading ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <FolderOpen size={18} />
            )}
            选择科研工作区 <ArrowRight size={18} />
          </button>
          {lastPath && (
            <button
              className="resume-workspace"
              disabled={loading || !isDesktop()}
              onClick={() => void openWorkspace(lastPath)}
            >
              继续上次工作区 <span>{lastPath}</span>
              <ArrowUpRight size={14} />
            </button>
          )}
          {!isDesktop() && (
            <p className="browser-notice">
              请通过桌面应用打开以连接本地资料。此页面不显示演示数据。
            </p>
          )}
        </main>
        {toast && (
          <div className="toast" role="status">
            {toast}
            <button onClick={() => setToast("")}>
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    );
  return (
    <div className={`app-shell ${collapsed ? "nav-collapsed" : ""}`}
      style={pageProject && projectColor(pageProject.id)}>
      <aside className="sidebar" aria-label="科研工作台侧栏" inert={!!(dialog || confirmLeave || deletion)}>
        <div className="sidebar-titlebar" data-tauri-drag-region aria-hidden="true" />
        {collapsed ? (
          <button className="icon-button collapsed-search" aria-label="展开侧栏并搜索" title="搜索工作区 ⌘K"
            onClick={focusSearch}>
            <Search size={16} />
          </button>
        ) : (
          <label className="sidebar-search" title="搜索工作区 ⌘K">
            <Search size={15} aria-hidden="true" />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (page !== "search" || editorPath) navigate("search");
              }}
              onFocus={() => {
                if (query && !editorPath && page !== "search") navigate("search");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) navigate("search");
              }}
              aria-label="搜索工作区"
              aria-keyshortcuts="Meta+K"
              placeholder="搜索"
            />
            <kbd aria-hidden="true">⌘K</kbd>
          </label>
        )}
        <nav aria-label="工作区导航">
          {navGroups.map(group => (
            <div className="navigation-group" key={group.label} role="group" aria-label={group.label}>
              {group.title && !collapsed && <button className="sidebar-group-toggle"
                aria-expanded={libraryExpanded} aria-controls="sidebar-library"
                onClick={() => setLibraryExpanded(!libraryExpanded)}>
                {group.title}<ChevronDown size={13} aria-hidden="true" />
              </button>}
              <div className="navigation-items" id={group.title ? "sidebar-library" : undefined}
                hidden={!!group.title && !libraryExpanded && !collapsed}>
                {group.items.map(({ id, label, icon: Icon }) => {
                  const selected = !editorPath && (page === id || id === "projects" && page === "board")
                    && !(id === "projects" && selectedShortcut && !collapsed && projectsExpanded);
                  return (
                    <button
                      className={selected ? "active" : ""}
                      key={id}
                      onClick={() => navigate(id)}
                      title={label}
                      aria-label={label}
                      aria-current={selected ? "page" : undefined}
                    >
                      <Icon size={16} strokeWidth={1.7} />
                      <span>{label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        {!collapsed && <div className="sidebar-projects">
          <div className="sidebar-projects-head">
            <button className="sidebar-group-toggle" aria-expanded={projectsExpanded} aria-controls="sidebar-active-projects"
              onClick={() => setProjectsExpanded(!projectsExpanded)}>
              活跃项目<ChevronDown size={13} aria-hidden="true" />
            </button>
            <button
              className="icon-button"
              title="新建项目"
              onClick={() => setDialog({ type: "project" })}
            >
              <Plus size={14} />
            </button>
          </div>
          <div className="sidebar-project-items" id="sidebar-active-projects" hidden={!projectsExpanded}>
            {activeProjects.map((project) => (
              <button
                key={project.id}
                className={selectedShortcut && project.id === selectedProject ? "active" : ""}
                aria-current={selectedShortcut && project.id === selectedProject ? "page" : undefined}
                style={projectColor(project.id)}
                title={project.name}
                onClick={() => openActiveProject(project)}
              >
                <Folder size={15} className="sidebar-project-icon" aria-hidden="true" />
                <span>{project.name}</span>
              </button>
            ))}
          </div>
        </div>}
        <div className="sidebar-bottom">
          <div className="local-status" title={current.root}>
            <span />
            <span>本地工作区</span>
            {working && <LoaderCircle className="spin" size={12} />}
          </div>
          <button title="工作区设置" onClick={() => setDialog({ type: "settings" })}>
            <Settings2 size={17} />
            <span>工作区设置</span>
          </button>
          <button title="回收站" onClick={() => setDialog({ type: "trash" })}>
            <Trash2 size={17} /><span>回收站</span>
          </button>
        </div>
      </aside>
      <main className={editorPath ? "main has-editor" : "main"} inert={!!(dialog || confirmLeave || deletion)}>
        <header className="topbar" data-tauri-drag-region>
          <button className="icon-button" title={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"} aria-expanded={!collapsed}
            onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
          {editorPath && <span className="breadcrumb-label" data-tauri-drag-region>
            {pageTitles[page]} <ChevronRight size={13} /> 编辑记录
          </span>}
          <div className="titlebar-spacer" data-tauri-drag-region aria-hidden="true" />
          <button
            className="icon-button"
            title="刷新工作区"
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} />
          </button>
        </header>
        {editorPath ? (
          <Suspense
            fallback={<div className="loading-line">正在打开编辑器…</div>}
          >
            <Editor
              ref={editorRef}
              path={editorPath}
              snapshot={current}
              dark={dark}
              onClose={() => guard(() => setEditorPath(null))}
              onSnapshot={setSnapshot}
              onDirty={setEditorDirty}
              onOpen={openEditor}
              onError={notify}
            />
          </Suspense>
        ) : (
          <div className={`page page-${page}`} key={detailProject ? `project:${detailProject.id}` : page}>
            {!detailProject && <header className="page-heading">
              <h1 ref={page === "projects" || page === "board" ? projectHeadingRef : undefined}
                tabIndex={-1}>{pageTitles[page]}</h1>
              {primaryNew ? (
                <button className="button primary" aria-keyshortcuts="Meta+N" title="⌘N" onClick={() => setDialog(primaryNew)}>
                  <Plus size={16} />{newLabels[primaryNew.type]}
                </button>
              ) : page === "assets" ? (
                <button className="button primary" onClick={() => void addDirectory()}>
                  <Plus size={16} />关联目录
                </button>
              ) : null}
            </header>}
            {!detailProject && (page === "projects" || page === "board") && projectViews()}
            {page === "board" ? (
              <TaskBoard
                key={`${current.root}/${scope}`}
                snapshot={current}
                scope={scope}
                busy={working}
                onScope={setBoardScope}
                onCreate={(status) => createTask(boardProject?.id, status)}
                onOpen={(task) => setDialog({ type: "task", task })}
                onMove={moveBoardTask}
                onDelete={requestDelete}
              />
            ) : page === "overview"
              ? renderOverview()
              : page === "projects"
                ? renderProjects()
                : page === "assets"
                  ? renderAssets()
                  : page === "records"
                    ? renderRecords()
                    : renderSearch()}
          </div>
        )}
      </main>
      {toast && (
        <div className="toast" role="status">
          <span>{toast}</span>
          <button onClick={() => setToast("")} aria-label="关闭提示">
            <X size={15} />
          </button>
        </div>
      )}
      {dialog?.type === "task" && (
        <TaskForm
          snapshot={current}
          task={dialog.task}
          projectId={dialog.projectId}
          initialStatus={dialog.initialStatus}
          source={dialog.source}
          onDelete={() => {
            const task = current.tasks.find(t => t.id === dialog.task?.id);
            if (task) requestDelete([task]);
          }}
          onOpen={(path) => {
            setDialog(null);
            if (/^(https?:|zotero:|mailto:)/i.test(path)) void safeOpen(path);
            else void viewFile({ path });
          }}
          onClose={() => setDialog(null)}
          onSubmit={async (values) => {
            const result = await write(
              dialog.task ? "task.update" : "task.create",
              dialog.task ? { id: dialog.task.id, changes: values } : values,
            );
            if (result) setDialog(null);
          }}
        />
      )}
      {dialog?.type === "trash" && (
        <TrashDialog items={current.trash || []} busy={working} onClose={() => setDialog(null)}
          onRestore={async item => {
            const result = await write("trash.restore", { id: item.id });
            if (result) notify(`已恢复「${item.title}」`);
          }} />
      )}
      {deletion && <DeleteConfirmation selection={deletion} busy={working}
        onClose={() => setDeletion(null)} onConfirm={() => void confirmDelete()} />}
      {dialog?.type === "project" && (
        <ProjectForm
          project={dialog.project}
          onClose={() => setDialog(null)}
          onSubmit={async (values) => {
            const result = await write(
              dialog.project ? "project.update" : "project.create",
              dialog.project
                ? { id: dialog.project.id, changes: values }
                : values,
            );
            if (result) {
              setDialog(null);
              setPage("projects");
              const saved = result.snapshot.projects.find(p => p.id === result.result?.id);
              if (saved) {
                setSelectedProject(saved.id);
                setProjectGroup(saved.archived ? "archived" : saved.status);
              }
            }
          }}
        />
      )}
      {dialog?.type === "milestone" && (
        <MilestoneForm
          snapshot={current}
          milestone={dialog.milestone}
          projectId={dialog.projectId}
          onClose={() => setDialog(null)}
          onSubmit={async (values) => {
            const result = await write(
              dialog.milestone ? "milestone.update" : "milestone.create",
              dialog.milestone
                ? { id: dialog.milestone.id, changes: values }
                : values,
            );
            if (result) setDialog(null);
          }}
        />
      )}
      {dialog?.type === "record" && (
        <RecordForm
          snapshot={current}
          projectId={dialog.projectId}
          taskId={dialog.taskId}
          kind={dialog.kind}
          onClose={() => setDialog(null)}
          onSubmit={async (values) => {
            const result = await write("record.create", values);
            if (result) {
              setDialog(null);
              if (typeof result.result?.path === "string")
                openEditor(result.result.path);
              else setPage("records");
            }
          }}
        />
      )}
      {dialog?.type === "asset" && (
        <AssetForm
          snapshot={current}
          asset={dialog.asset}
          onClose={() => setDialog(null)}
          onSubmit={async (changes) => {
            const result = await write("asset.update", {
              id: dialog.asset.id,
              changes,
            });
            if (result) setDialog(null);
          }}
        />
      )}
      {dialog?.type === "recordLinks" && (
        <RecordLinksForm
          snapshot={current}
          record={dialog.record}
          onClose={() => setDialog(null)}
          onSubmit={async (changes) => {
            const result = await write("record.update", {
              id: dialog.record.id,
              changes,
            });
            if (result) setDialog(null);
          }}
        />
      )}
      {dialog?.type === "settings" && (
        <Modal
          title="工作区设置"
          subtitle="资料与关联保存在本地"
          onClose={() => setDialog(null)}
        >
          <div className="form-body">
            <Field label="当前工作区">
              <input readOnly value={current.root} />
            </Field>
            <Field label="外观">
              <Select value={appearance} onChange={value => {
                setAppearance(value as Appearance);
                localStorage.setItem("sr-appearance", value);
              }}>
                <option value="system">跟随系统</option>
                <option value="light">浅色</option>
                <option value="dark">深色</option>
              </Select>
            </Field>
            <div className="settings-actions">
              <button
                className="button"
                onClick={() => void safeOpen(current.root)}
              >
                <FolderOpen size={15} />在 Finder 中打开
              </button>
              <button className="button" onClick={() => void chooseWorkspace()}>
                <Folder size={15} />
                切换工作区
              </button>
            </div>
            <div className="settings-info">
              <Check size={16} />
              <div>
                <strong>本地管理，无需登录</strong>
                <p>
                  目录浏览、任务、笔记与检索在本机运行。外部资料保持原位置。
                </p>
              </div>
            </div>
            <div className="settings-info">
              <Archive size={16} />
              <div>
                <strong>完整性与备份是两件事</strong>
                <p>
                  本机文件可访问不等于外部备份完成。备份证据请查看工作区的同步记录。
                </p>
                <button
                  className="text-button"
                  onClick={() => {
                    setDialog(null);
                    openEditor("sync/manifest.md");
                  }}
                >
                  查看备份记录 <ArrowUpRight size={13} />
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}
      {dialog?.type === "image" && (
        <Modal
          title={baseName(dialog.path)}
          subtitle={dialog.path}
          wide
          onClose={() => setDialog(null)}
        >
          <div className="image-viewer">
            <img src={dialog.dataUrl} alt={baseName(dialog.path)} />
          </div>
          <div className="form-actions">
            <button
              className="button"
              onClick={() => void safeOpen(dialog.path)}
            >
              <ExternalLink size={15} />
              打开原文件
            </button>
          </div>
        </Modal>
      )}
      {dialog?.type === "paper" && (
        <Modal
          title="文献详情"
          subtitle={dialog.paper.bibkey}
          onClose={() => setDialog(null)}
        >
          <div className="form-body paper-details">
            <BookOpen size={26} />
            <h2>{dialog.paper.title}</h2>
            <p>{dialog.paper.author}</p>
            <div className="paper-details-meta">
              <span>{dialog.paper.year}</span>
              <span className="reading-status">
                {dialog.paper.status || "未记录"}
              </span>
            </div>
            {dialog.paper.doi && (
              <button
                className="text-button"
                onClick={() =>
                  void safeOpen(`https://doi.org/${dialog.paper.doi}`)
                }
              >
                DOI · {dialog.paper.doi}
                <ArrowUpRight size={14} />
              </button>
            )}
            <div className="project-actions">
              <button
                className="button primary"
                onClick={() =>
                  void safeOpen(
                    dialog.paper.zotero_url ||
                      dialog.paper.pdf_path ||
                      dialog.paper.path ||
                      "",
                  )
                }
              >
                <BookOpen size={15} />
                打开论文
              </button>
              {dialog.paper.pdf_path && (
                <button
                  className="button"
                  onClick={() => void safeOpen(dialog.paper.pdf_path!)}
                >
                  本地 PDF
                </button>
              )}
              {dialog.paper.note_path && (
                <button
                  className="button"
                  onClick={() => {
                    const path = dialog.paper.note_path;
                    setDialog(null);
                    if (path) openEditor(path);
                  }}
                >
                  精读卡
                </button>
              )}
            </div>
            <Field label="关联到项目">
              <Select
                value=""
                onChange={async (id) => {
                  if (id && (dialog.paper.pdf_path || dialog.paper.path)) {
                    const result = await write("asset.link", {
                      path: dialog.paper.pdf_path || dialog.paper.path,
                      kind: "file",
                      title: dialog.paper.title,
                      featured: true,
                      project_id: id,
                    });
                    if (result) notify("已关联论文原件与项目。");
                  }
                }}
              >
                <option value="">选择项目…</option>
                {current.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="section-heading">
              <h2>最近阅读记录</h2>
            </div>
            {current.records.filter((r) => r.path.includes(dialog.paper.bibkey))
              .length ? (
              current.records
                .filter((r) => r.path.includes(dialog.paper.bibkey))
                .map((r) => (
                  <button
                    className="related-link"
                    key={r.id}
                    onClick={() => {
                      setDialog(null);
                      openEditor(r.path);
                    }}
                  >
                    <FileText size={15} />
                    {r.title}
                    <ArrowUpRight size={13} />
                  </button>
                ))
            ) : (
              <p className="muted">当前工作区未找到这篇文献的阅读会话。</p>
            )}
          </div>
        </Modal>
      )}
      {confirmLeave && (
        <Modal
          title="保留这次编辑吗？"
          subtitle="当前记录还有未保存的内容。"
          onClose={() => {
            if (!confirmBusy) setConfirmLeave(null);
          }}
        >
          <div className="form-body">
            <p className="confirm-copy">
              保存会写回原 Markdown 文件；放弃只撤销当前未保存修改。
            </p>
          </div>
          <footer className="form-actions">
            <button
              className="button"
              onClick={() => setConfirmLeave(null)}
              disabled={confirmBusy}
            >
              取消
            </button>
            <button
              className="button"
              disabled={confirmBusy}
              onClick={() => {
                editorRef.current?.discard();
                setEditorDirty(false);
                dirtyRef.current = false;
                const action = confirmLeave;
                setConfirmLeave(null);
                action();
              }}
            >
              放弃修改
            </button>
            <button
              className="button primary"
              disabled={confirmBusy}
              onClick={async () => {
                setConfirmBusy(true);
                try {
                  if (await editorRef.current?.save()) {
                    setEditorDirty(false);
                    dirtyRef.current = false;
                    const action = confirmLeave;
                    setConfirmLeave(null);
                    action();
                  }
                } finally {
                  setConfirmBusy(false);
                }
              }}
            >
              {confirmBusy ? "正在保存…" : "保存并继续"}
            </button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
