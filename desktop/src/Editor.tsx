import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import CodeMirror from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Columns2,
  FileText,
  Save,
  Code2,
  Eye,
  Copy,
} from "lucide-react";
import Markdown from "./Markdown";
import { baseName, errorDetail, rpc, writeRpc } from "./api";
import type { FileDocument, RpcError, Snapshot } from "./api";
import { canSaveRestoredDraft, decodeDraft, encodeDraft } from "./drafts";
import type { StoredDraft } from "./drafts";

export interface EditorHandle {
  dirty: boolean;
  save: () => Promise<boolean>;
  discard: () => void;
}
interface Props {
  path: string;
  snapshot: Snapshot;
  dark: boolean;
  onClose: () => void;
  onSnapshot: (snapshot: Snapshot) => void;
  onDirty: (dirty: boolean) => void;
  onOpen: (path: string) => void;
  onError: (message: string) => void;
}
const editorTheme = EditorView.theme({
  "&": { backgroundColor: "var(--surface)", color: "var(--ink)", height: "100%", fontSize: "13px" },
  ".cm-content": {
    fontFamily: '"SFMono-Regular",Consolas,"Liberation Mono",monospace',
    padding: "17px 0",
    lineHeight: "1.85",
  },
  ".cm-gutters": {
    backgroundColor: "var(--paper)",
    color: "var(--muted)",
    borderRight: "1px solid var(--separator)",
  },
  ".cm-line": { padding: "0 16px" },
  ".cm-activeLine": { backgroundColor: "var(--hover)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--accent-tint)" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-scroller": { overflow: "auto" },
  "&.cm-focused": { outline: "none" },
});

const Editor = forwardRef<EditorHandle, Props>(function Editor(
  { path, snapshot, dark, onClose, onSnapshot, onDirty, onOpen, onError },
  ref,
) {
  const [doc, setDoc] = useState<FileDocument | null>(null);
  const [loadedPath, setLoadedPath] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState<"split" | "source" | "preview">("split");
  const [conflict, setConflict] = useState<RpcError | null>(null);
  const [diskVisible, setDiskVisible] = useState(false);
  const [restore, setRestore] = useState<StoredDraft | null>(null);
  const [draftBaseRevision, setDraftBaseRevision] = useState<string | null>(
    null,
  );
  const [loadError, setLoadError] = useState("");
  const current = useRef(content);
  current.current = content;
  const draftKey = `sr-draft:${snapshot.root}:${path}`;
  const dirty = !!doc && loadedPath === path && content !== doc.content;
  useEffect(() => {
    let live = true;
    setLoading(true);
    setLoadError("");
    setDoc(null);
    setLoadedPath("");
    setDraftBaseRevision(null);
    setRestore(null);
    setConflict(null);
    rpc<FileDocument>("files.read", { path })
      .then((result) => {
        if (!live) return;
        setDoc(result);
        setContent(result.content);
        setLoadedPath(path);
        setDraftBaseRevision(result.revision);
        try {
          const stored = localStorage.getItem(draftKey);
          const draft = stored === null ? null : decodeDraft(stored);
          setRestore(draft && draft.content !== result.content ? draft : null);
        } catch {
          /* Editing remains available without browser storage. */
        }
      })
      .catch((error) => {
        if (live) setLoadError(errorDetail(error).message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [path, draftKey]);
  useEffect(() => {
    onDirty(dirty);
    if (!doc || loadedPath !== path) return;
    try {
      if (dirty)
        localStorage.setItem(draftKey, encodeDraft(content, draftBaseRevision));
      else if (restore === null) localStorage.removeItem(draftKey);
    } catch {
      /* Explicit disk saving remains available. */
    }
  }, [
    content,
    dirty,
    doc,
    loadedPath,
    path,
    draftKey,
    restore,
    draftBaseRevision,
    onDirty,
  ]);
  async function save(): Promise<boolean> {
    if (!doc || saving) return false;
    if (!dirty) return true;
    if (conflict || draftBaseRevision !== doc.revision) {
      onError(
        "草稿与磁盘版本尚未核对，无法覆盖保存。请先另存草稿，再载入磁盘内容。",
      );
      return false;
    }
    setSaving(true);
    const written = current.current;
    try {
      const result = await writeRpc(
        "files.save",
        {
          path: doc.path,
          content: written,
          expected_revision: draftBaseRevision,
          revision: snapshot.revision,
        },
        snapshot.root,
      );
      if (result.snapshot) onSnapshot(result.snapshot);
      const disk = await rpc<FileDocument>("files.read", { path: doc.path });
      setDoc(disk);
      setDraftBaseRevision(disk.revision);
      if (current.current === written) setContent(disk.content);
      setConflict(null);
      setRestore(null);
      try {
        if (current.current === written) localStorage.removeItem(draftKey);
      } catch {
        /* Best effort cache. */
      }
      return current.current === written;
    } catch (error) {
      const detail = errorDetail(error);
      if (detail.code === "conflict") setConflict(detail);
      else onError(detail.message);
      return false;
    } finally {
      setSaving(false);
    }
  }
  function discard() {
    if (doc) {
      setContent(doc.content);
      setDraftBaseRevision(doc.revision);
    }
    setRestore(null);
    setConflict(null);
    try {
      localStorage.removeItem(draftKey);
    } catch {
      /* Ignore unavailable cache. */
    }
  }
  useImperativeHandle(ref, () => ({ dirty, save, discard }));
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "s") {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });
  async function saveCopy(reloadDisk = false) {
    setSaving(true);
    try {
      const result = await writeRpc(
        "record.create",
        {
          title: `${baseName(path).replace(/\.md$/, "")} · 保留草稿`,
          kind: "note",
          content: current.current,
          revision: snapshot.revision,
        },
        snapshot.root,
      );
      onSnapshot(result.snapshot);
      if (reloadDisk && doc) {
        const disk = await rpc<FileDocument>("files.read", { path: doc.path });
        setDoc(disk);
        setContent(disk.content);
        setDraftBaseRevision(disk.revision);
        setRestore(null);
        setConflict(null);
      }
      onError(
        reloadDisk
          ? "当前编辑已另存为工作记录，并载入磁盘最新内容。"
          : "当前编辑已另存为工作记录，原文件未被覆盖。",
      );
    } catch (error) {
      onError(errorDetail(error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="editor-screen" aria-label="Markdown 编辑器">
      <header className="editor-top">
        <button className="icon-button" onClick={onClose} aria-label="关闭编辑器" title="返回">
          <ArrowLeft size={20} />
        </button>
        <div className="editor-heading">
          <span>
            <i className="editor-file-icon" aria-hidden="true"><FileText size={17} /></i>
            <strong title={baseName(path)}>{baseName(path)}</strong>
            {dirty && <i className="unsaved-dot" />}
          </span>
          <small title={path}>{path}</small>
        </div>
        <div className="segmented compact" role="group" aria-label="编辑器视图">
          <button
            className={mode === "source" ? "active" : ""}
            aria-pressed={mode === "source"}
            onClick={() => setMode("source")}
          >
            <Code2 size={13} />
            源码
          </button>
          <button
            className={mode === "split" ? "active" : ""}
            aria-pressed={mode === "split"}
            onClick={() => setMode("split")}
          >
            <Columns2 size={13} />
            分栏
          </button>
          <button
            className={mode === "preview" ? "active" : ""}
            aria-pressed={mode === "preview"}
            onClick={() => setMode("preview")}
          >
            <Eye size={13} />
            预览
          </button>
        </div>
        <span role="status" className={`save-state ${dirty ? "dirty" : ""}`}>
          {saving ? (
            "保存中…"
          ) : dirty ? (
            "未保存"
          ) : doc ? (
            <>
              <Check size={13} />
              已保存
            </>
          ) : (
            ""
          )}
        </span>
        <button
          className="button primary editor-save"
          onClick={() => void save()}
          disabled={!dirty || saving || !doc?.editable || !!conflict}
        >
          <Save size={15} />
          保存 <kbd>⌘S</kbd>
        </button>
      </header>
      {restore !== null && (
        <div className="notice warning">
          <AlertTriangle size={16} />
          <span>找到上次未保存的草稿。恢复时会核对其磁盘版本。</span>
          <button
            onClick={() => {
              setContent(restore.content);
              setDraftBaseRevision(restore.baseRevision);
              if (doc && !canSaveRestoredDraft(restore, doc.revision)) {
                setConflict({
                  code: "draft_conflict",
                  message: restore.baseRevision
                    ? "草稿对应的磁盘版本已变化"
                    : "旧草稿没有原始版本记录，不能直接覆盖磁盘文件",
                  details: {
                    content: doc.content,
                    current_revision: doc.revision,
                  },
                });
              }
              setRestore(null);
            }}
          >
            恢复草稿
          </button>
          <button
            onClick={() => {
              setRestore(null);
              try {
                localStorage.removeItem(draftKey);
              } catch {
                /* Best effort cache. */
              }
            }}
          >
            放弃旧草稿
          </button>
        </div>
      )}
      {conflict && (
        <div className="conflict-panel">
          <div>
            <AlertTriangle size={18} />
            <strong>
              {conflict.code === "draft_conflict"
                ? "草稿版本需要核对"
                : "文件在外部发生了变化"}
            </strong>
            <span>
              {conflict.code === "draft_conflict"
                ? conflict.message
                : "当前草稿仍保留，请先核对差异。"}
            </span>
          </div>
          <div className="toolbar">
            <button
              className="button small"
              onClick={() => setDiskVisible(!diskVisible)}
            >
              {diskVisible ? "收起磁盘内容" : "查看磁盘内容"}
            </button>
            <button
              className="button small"
              disabled={saving}
              onClick={() => void saveCopy()}
            >
              <Copy size={13} />
              另存当前草稿
            </button>
            <button
              className="button small"
              disabled={saving}
              onClick={() => void saveCopy(true)}
            >
              先另存草稿，再载入磁盘内容
            </button>
          </div>
          {diskVisible && (
            <pre>
              {conflict.details?.content ??
                "磁盘内容未随错误返回，请另开原文件核对。"}
            </pre>
          )}
        </div>
      )}
      {loading ? (
        <div className="empty-state">正在读取原文件…</div>
      ) : loadError ? (
        <div className="empty-state">
          <AlertTriangle size={28} />
          <h3>暂时无法打开</h3>
          <p>{loadError}</p>
        </div>
      ) : (
        <div className={`editor-panes ${mode}`}>
          {mode !== "preview" && (
            <section className="source-pane" aria-label="Markdown 源码">
              {mode === "split" && <div className="editor-pane-label"><Code2 size={14} />源码</div>}
              <CodeMirror
                value={content}
                height="100%"
                theme={dark ? "dark" : "light"}
                extensions={[markdown(), EditorView.lineWrapping, editorTheme]}
                onChange={(value) => setContent(value)}
                editable={
                  doc?.editable !== false && restore === null && !saving
                }
                basicSetup={{
                  foldGutter: true,
                  highlightActiveLine: true,
                  autocompletion: false,
                }}
              />
            </section>
          )}
          {mode !== "source" && (
            <section className="preview-pane" aria-label="Markdown 预览">
              {mode === "split" && <div className="editor-pane-label"><Eye size={14} />预览</div>}
              <div className="preview-scroll">
                <Markdown
                  content={content}
                  path={doc?.path || path}
                  onOpen={onOpen}
                  onError={onError}
                />
              </div>
            </section>
          )}
        </div>
      )}
      {doc && <footer className="editor-statusbar">
        <span>{doc.editable ? "Markdown 原文件" : "只读预览"}</span>
        <span>{content.length.toLocaleString()} 字符</span>
      </footer>}
    </section>
  );
});
export default Editor;
