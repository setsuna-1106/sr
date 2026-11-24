import { useEffect, useState } from "react";
import { ArrowUpRight, FileImage, FileText, X } from "lucide-react";
import { errorDetail, rpc } from "./api";
import type { FileDocument, FileEntry } from "./api";

export function fileViewKind(path: string) {
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) return "image";
  if (/\.(md|txt|log|csv|tsv|json|ya?ml|bib|py|rs|js|ts|toml|ini)$/i.test(path)) return "text";
  return "external";
}

export function formatFileSize(size?: number) {
  if (size == null) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

type Preview = { type: "text" | "image" | "error"; value: string };
const excerptLength = 1800;

export function FileInspector({ entry, onClose, onOpen, onOpenOriginal }: {
  entry: FileEntry;
  onClose: () => void;
  onOpen: () => void;
  onOpenOriginal: () => void;
}) {
  const kind = fileViewKind(entry.path);
  const available = entry.available !== false;
  const [preview, setPreview] = useState<Preview | null>(null);

  useEffect(() => {
    if (!available || kind === "external") return;
    let live = true;
    const request: Promise<Preview> = kind === "image"
      ? rpc<{ data_url: string }>("files.preview", { path: entry.path })
        .then(result => ({ type: "image", value: result.data_url }))
      : rpc<FileDocument>("files.read", { path: entry.path })
        .then(({ content }) => ({
          type: "text",
          value: content.length > excerptLength ? content.slice(0, excerptLength) + "…" : content,
        }));
    void request
      .then(result => { if (live) setPreview(result); })
      .catch(error => { if (live) setPreview({ type: "error", value: errorDetail(error).message }); });
    return () => { live = false; };
  }, [available, entry.path, kind]);

  const Icon = kind === "image" ? FileImage : FileText;
  const suffix = /\.([^.]+)$/.exec(entry.name)?.[1].toUpperCase();
  return (
    <aside className="file-inspector" aria-labelledby="file-inspector-title">
      <header>
        <h3 id="file-inspector-title">文件预览</h3>
        <button className="icon-button" title="收起文件预览" aria-label="收起文件预览" onClick={onClose}>
          <X size={16} />
        </button>
      </header>
      <div className="inspector-file">
        <span className="file-symbol"><Icon size={19} /></span>
        <strong title={entry.path}>{entry.name}</strong>
      </div>
      <dl className="inspector-fields">
        <div><dt>格式</dt><dd>{suffix || "文件"}</dd></div>
        <div><dt>大小</dt><dd>{formatFileSize(entry.size)}</dd></div>
      </dl>
      {!available ? (
        <p className="muted">文件暂不可访问。</p>
      ) : preview?.type === "error" ? (
        <p className="muted" role="status">{preview.value}</p>
      ) : preview?.type === "image" ? (
        <img className="inspector-image" src={preview.value} alt={entry.name} />
      ) : preview?.type === "text" ? (
        <pre className="inspector-excerpt" tabIndex={0} aria-label="文本预览">{preview.value || "空文件"}</pre>
      ) : kind !== "external" ? (
        <p className="muted" role="status">正在读取预览…</p>
      ) : null}
      {available && (
        <button className="button small inspector-open"
          onClick={preview?.type === "error" ? onOpenOriginal : onOpen}
          disabled={kind === "image" && !preview}>
          <ArrowUpRight size={15} />
          {preview?.type === "error" ? "打开原文件" : kind === "text" ? "在编辑器打开" : kind === "image" ? "查看大图" : "打开文件"}
        </button>
      )}
    </aside>
  );
}
