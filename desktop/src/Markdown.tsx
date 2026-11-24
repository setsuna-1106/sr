import { useEffect, useMemo, useRef } from "react";
import MarkdownIt from "markdown-it";
import katex from "katex";
import { dirname, openPath, openUrl, rpc } from "./api";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });
md.inline.ruler.before("escape", "math_inline", (state, silent) => {
  const start = state.pos;
  if (state.src[start] !== "$" || state.src[start + 1] === "$") return false;
  let end = start + 1;
  while (
    (end = state.src.indexOf("$", end)) !== -1 &&
    state.src[end - 1] === "\\"
  )
    end++;
  if (
    end < 0 ||
    state.src.slice(start + 1, end).includes("\n") ||
    end === start + 1
  )
    return false;
  if (!silent) {
    const token = state.push("math_inline", "math", 0);
    token.content = state.src.slice(start + 1, end);
  }
  state.pos = end + 1;
  return true;
});
md.block.ruler.before(
  "fence",
  "math_block",
  (state, startLine, endLine, silent) => {
    const start = state.bMarks[startLine] + state.tShift[startLine];
    if (state.src.slice(start, start + 2) !== "$$") return false;
    let line = startLine;
    const parts: string[] = [];
    const first = state.src.slice(start + 2, state.eMarks[startLine]);
    if (first.trimEnd().endsWith("$$")) {
      parts.push(first.trimEnd().slice(0, -2));
    } else {
      parts.push(first);
      let closed = false;
      while (++line < endLine) {
        const s = state.src.slice(
          state.bMarks[line] + state.tShift[line],
          state.eMarks[line],
        );
        if (s.trimEnd().endsWith("$$")) {
          parts.push(s.trimEnd().slice(0, -2));
          closed = true;
          break;
        }
        parts.push(s);
      }
      if (!closed) return false;
    }
    if (silent) return true;
    const token = state.push("math_block", "math", 0);
    token.block = true;
    token.content = parts.join("\n");
    token.map = [startLine, line + 1];
    state.line = line + 1;
    return true;
  },
);
md.renderer.rules.math_inline = (tokens, index) =>
  katex.renderToString(tokens[index].content, {
    throwOnError: false,
    trust: false,
  });
md.renderer.rules.math_block = (tokens, index) =>
  `<div class="math-block">${katex.renderToString(tokens[index].content, { throwOnError: false, displayMode: true, trust: false })}</div>`;
const renderImage = md.renderer.rules.image!;
md.renderer.rules.image = (tokens, index, options, env, self) => {
  const token = tokens[index];
  const src = token.attrGet("src") || "";
  token.attrSet("data-original-src", src);
  token.attrSet("src", "");
  token.attrSet("loading", "lazy");
  return renderImage(tokens, index, options, env, self);
};
function resolveRelative(base: string, target: string) {
  if (target.startsWith("/")) return target;
  const parts = `${dirname(base)}/${target}`.split("/");
  const result: string[] = [];
  for (const p of parts) {
    if (p === "..") result.pop();
    else if (p !== ".") result.push(p);
  }
  return result.join("/");
}
function safeDecode(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
export default function Markdown({
  content,
  path,
  onOpen,
  onError,
}: {
  content: string;
  path: string;
  onOpen?: (path: string) => void;
  onError?: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const html = useMemo(() => md.render(content), [content]);
  useEffect(() => {
    let canceled = false;
    const root = container.current;
    if (!root) return;
    root
      .querySelectorAll<HTMLImageElement>("img[data-original-src]")
      .forEach((img) => {
        const source = img.dataset.originalSrc || "";
        if (/^https?:/i.test(source)) {
          const holder = document.createElement("span");
          holder.className = "external-image";
          holder.textContent = `外部图片 · ${img.alt || source}`;
          holder.title = "点击在浏览器打开";
          holder.addEventListener("click", () => void openUrl(source));
          img.replaceWith(holder);
          return;
        }
        if (source.startsWith("data:image/")) {
          img.src = source;
          return;
        }
        const target = resolveRelative(path, safeDecode(source));
        rpc<{ data_url: string }>("files.preview", { path: target })
          .then((result) => {
            if (!canceled) img.src = result.data_url;
          })
          .catch(() => {
            if (!canceled) {
              img.alt = `图片暂不可用：${img.alt || source}`;
              img.classList.add("missing-image");
            }
          });
      });
    return () => {
      canceled = true;
    };
  }, [html, path]);
  return (
    <div
      ref={container}
      className="markdown-body"
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={(event) => {
        const link = (event.target as HTMLElement).closest("a");
        if (!link) return;
        event.preventDefault();
        const href = link.getAttribute("href") || "";
        if (href.startsWith("#")) {
          const anchor = safeDecode(href.slice(1));
          const target = Array.from(
            container.current?.querySelectorAll<HTMLElement>(
              "[id],h1,h2,h3,h4,h5,h6",
            ) || [],
          ).find(
            (element) =>
              element.id === anchor ||
              element.textContent?.trim().toLowerCase().replace(/\s+/g, "-") ===
                anchor,
          );
          target?.scrollIntoView({ block: "start" });
          return;
        }
        if (/^(https?:|zotero:|mailto:)/i.test(href)) {
          void openUrl(href).catch(() => onError?.("无法打开此链接"));
          return;
        }
        const target = resolveRelative(path, safeDecode(href.split("#")[0]));
        if (/\.md$/i.test(target) && onOpen) onOpen(target);
        else void openPath(target).catch(() => onError?.("无法打开原文件"));
      }}
    />
  );
}
