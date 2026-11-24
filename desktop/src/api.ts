import { invoke } from "@tauri-apps/api/core";

export type TaskStatus = "ready" | "doing" | "waiting" | "done";
export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  project_id: string | null;
  milestone_id: string | null;
  pinned: boolean;
  due_date?: string;
  followup?: string;
  blocker?: string;
  evidence?: string;
  evidenceUrl?: string;
  notes?: string;
  links?: Array<{
    label?: string;
    title?: string;
    path?: string;
    url?: string;
    href?: string;
  }>;
  completion?: string;
}
export interface Project {
  id: string;
  path: string;
  name: string;
  status: "active" | "paused" | "completed";
  archived: boolean;
  stage?: string;
  next_action?: string;
  state_path?: string;
  goal?: string;
}
export interface Milestone {
  id: string;
  title: string;
  project_id: string;
  status: "todo" | "doing" | "done";
  completion?: string;
  due_date?: string;
}
export interface Asset {
  id: string;
  path: string;
  title?: string;
  name?: string;
  kind: string;
  featured?: boolean;
  purpose?: string;
  tags?: string[];
  project_ids?: string[];
  task_ids?: string[];
  milestone_ids?: string[];
  available?: boolean;
}
export interface ResearchRecord {
  id: string;
  title: string;
  path: string;
  kind: string;
  project_id?: string;
  project_ids?: string[];
  task_id?: string;
  task_ids?: string[];
  asset_ids?: string[];
  status?: string;
  date?: string;
  created_at?: string;
}
export interface Paper {
  id?: string;
  bibkey: string;
  title: string;
  author?: string;
  year?: string | number;
  status?: string;
  pdf_path?: string;
  note_path?: string;
  path?: string;
  doi?: string;
  zotero_url?: string;
  project_ids?: string[];
  reading_records?: ResearchRecord[];
}
export interface RecoveryItem {
  id: string;
  method?: string;
  status: string;
  created_at?: string;
  conflicts?: unknown[];
}
export interface TrashItem {
  id: string;
  kind: "project" | "tasks";
  title: string;
  deleted_at: string;
  task_ids: string[];
  milestone_count: number;
}
export interface Snapshot {
  root: string;
  revision: string;
  metadata_revision?: string;
  projects: Project[];
  tasks: Task[];
  milestones: Milestone[];
  assets: Asset[];
  records: ResearchRecord[];
  papers: Paper[];
  recovery?: RecoveryItem[];
  trash?: TrashItem[];
}
export interface WriteResult {
  snapshot: Snapshot;
  operation_id: string;
  result?: Record<string, unknown>;
}
export interface FileEntry {
  name: string;
  path: string;
  kind: string;
  size?: number;
  available?: boolean;
}
export interface FileListing {
  path: string;
  entries: FileEntry[];
  roots?: Array<string | { path: string; title?: string }>;
}
export interface FileDocument {
  path: string;
  content: string;
  revision: string;
  editable: boolean;
  encoding?: string;
  newline?: string;
}
export interface SearchHit {
  id: string;
  type: string;
  title: string;
  path?: string;
  project_id?: string;
  excerpt?: string;
}
export interface RpcError {
  code: string;
  message: string;
  details?: {
    content?: string;
    current_revision?: string;
    [key: string]: unknown;
  };
}
export const isDesktop = () => "__TAURI_INTERNALS__" in window;
export function errorDetail(error: unknown): RpcError {
  if (typeof error === "string") {
    try {
      return JSON.parse(error) as RpcError;
    } catch {
      return { code: "error", message: error };
    }
  }
  if (error instanceof Error) return { code: "error", message: error.message };
  if (typeof error === "object" && error && "message" in error)
    return error as RpcError;
  return { code: "error", message: "操作未完成，请稍后重试。" };
}
export async function rpc<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  try {
    return await invoke<T>("rpc", { method, params });
  } catch (error) {
    throw errorDetail(error);
  }
}
export async function chooseDirectory(
  workspace = false,
): Promise<string | null> {
  return invoke<string | null>(
    workspace ? "choose_workspace" : "choose_directory",
  );
}
export async function openPath(path: string): Promise<void> {
  await invoke("open_path", { path });
}
export async function openUrl(url: string): Promise<void> {
  await invoke("open_url", { url });
}
export const operationId = () => crypto.randomUUID();
interface OperationRecord {
  found: boolean;
  id?: string;
  status?: string;
  result?: Record<string, unknown>;
}
const pendingWrites = new Map<string, Record<string, unknown>>();
/** Keep the original revision and operation ID until an ambiguous result is resolved. */
export async function writeRpc(
  method: string,
  params: Record<string, unknown>,
  root: string,
): Promise<WriteResult> {
  const business = { ...params };
  delete business.revision;
  delete business.operation_id;
  const key = `sr-operation:${root}:${JSON.stringify({ method, params: business })}`;
  let payload = pendingWrites.get(key);
  if (!payload) {
    try {
      const stored = localStorage.getItem(key);
      if (stored) payload = JSON.parse(stored) as Record<string, unknown>;
    } catch {
      /* Storage is best effort; live retry still keeps its ID. */
    }
  }
  const clear = () => {
    pendingWrites.delete(key);
    try {
      localStorage.removeItem(key);
    } catch {
      /* Ignore unavailable browser storage. */
    }
  };
  const committed = async (op: OperationRecord): Promise<WriteResult> => {
    const snapshot = await rpc<Snapshot>("workspace.snapshot");
    clear();
    return {
      snapshot,
      operation_id: String(payload?.operation_id || op.id),
      result: op.result,
    };
  };
  if (payload) {
    const operation = await rpc<OperationRecord>("operation.get", {
      operation_id: payload.operation_id,
    });
    if (operation.found) {
      if (operation.status === "committed") return committed(operation);
      if (operation.status === "prepared" || operation.status === "conflict") {
        const result = await rpc<{
          snapshot: Snapshot;
          results: Array<{ id: string; status: string }>;
        }>("workspace.recover", {
          operation_id: payload.operation_id,
          action: "resume",
        });
        const resumed = result.results.find(
          (item) => item.id === payload?.operation_id,
        );
        if (resumed?.status === "committed") {
          return committed(
            await rpc<OperationRecord>("operation.get", {
              operation_id: payload.operation_id,
            }),
          );
        }
        throw {
          code: "recovery_pending",
          message: "上次写入仍需要恢复。当前内容已保留，请在总览处理恢复提示。",
        } satisfies RpcError;
      }
      clear();
      payload = undefined;
    }
  }
  if (!payload) payload = { ...params, operation_id: operationId() };
  pendingWrites.set(key, payload);
  try {
    localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    /* Live retries retain the pending operation in memory. */
  }
  try {
    const result = await rpc<WriteResult>(method, payload);
    clear();
    return result;
  } catch (error) {
    const original = errorDetail(error);
    try {
      const operation = await rpc<OperationRecord>("operation.get", {
        operation_id: payload.operation_id,
      });
      if (operation.found && operation.status === "committed")
        return committed(operation);
      if (
        !operation.found &&
        [
          "invalid",
          "conflict",
          "validation",
          "not_found",
          "invalid_params",
          "forbidden",
          "invalid_request",
          "invalid_path",
          "invalid_state",
          "recovery_required",
        ].includes(original.code)
      )
        clear();
    } catch {
      /* Do not replace the original error or retry with a new operation ID. */
    }
    throw original;
  }
}
export const baseName = (path: string) =>
  path.replace(/\/$/, "").split("/").pop() || path;
export const dirname = (path: string) => path.slice(0, path.lastIndexOf("/"));
export const taskLabels: Record<TaskStatus, string> = {
  ready: "待开工",
  doing: "进行中",
  waiting: "等待",
  done: "已完成",
};
export const projectLabels = {
  active: "活跃",
  paused: "暂停",
  completed: "已完成",
};
export const recordLabels: Record<string, string> = {
  note: "工作笔记",
  meeting: "组会纪要",
  experiment: "实验记录",
  simulation: "仿真记录",
  reading: "阅读记录",
  fit: "拟合记录",
  analysis: "数据分析",
};
