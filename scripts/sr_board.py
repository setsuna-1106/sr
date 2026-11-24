"""Serve the Markdown research board on the local machine."""

from datetime import date
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import json
import re
import webbrowser

import sr_library as library

BOARD = Path("projects/KANBAN.md")
WEEK = Path("projects/WEEKLY_PLAN.md")
ASSETS = Path(__file__).with_name("board")
COLUMNS = {"ready": "待开工", "doing": "进行中", "waiting": "等待", "done": "已完成"}
EMPTY = {"", "—", "-"}
FIELDS = {"blocker": "等待的输入", "followup": "跟进日期", "evidence": "证据",
          "due_date": "截止日期", "completion": "完成条件"}
ID = r"[A-Za-z0-9][A-Za-z0-9_-]*"
CARD = re.compile(rf"^\[({ID})\]\(#({ID})\)\s*·\s*【([^】]+)】\s*(\S.*)$")
LINK = re.compile(r"\[([^\]\n]+)\]\((?:<([^>\n]+)>|([^\s)]+))\)")


def cells(line):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip()[1:-1])]


def link_parts(match):
    return match[1], match[2] or match[3]


def link_url(root, source, value):
    """Map Markdown destinations to safe links in the local reader."""
    if not value or re.search(r"[\\<>\x00-\x1f\x7f]", value):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme in {"https", "http"} and url.hostname and not url.username:
        return value
    if url.scheme or url.netloc or value.startswith("/") or url.query:
        return None
    path = (root / source.parent / unquote(url.path) if url.path else root / source).resolve()
    if not path.is_relative_to(root):
        return None
    relative = path.relative_to(root)
    if any(part.startswith(".") for part in relative.parts):
        return None
    fragment = "#" + quote(unquote(url.fragment)) if url.fragment else ""
    if relative == BOARD and fragment:
        return "/" + fragment
    return "/file/" + quote(relative.as_posix()) + fragment


def read_fields(lines):
    result = dict.fromkeys(FIELDS, "")
    positions = {}
    for index, line in enumerate(lines):
        for name, label in FIELDS.items():
            prefix = f"- {label}："
            if not line.startswith(prefix):
                continue
            if name in positions:
                raise ValueError(f"卡片详情重复填写了「{label}」")
            positions[name] = index
            value = line[len(prefix):].strip()
            link = LINK.fullmatch(value) if name == "evidence" else None
            result[name] = link_parts(link)[1] if link else value
    return result, positions


class Board:
    def __init__(self, root, raw=None):
        self.root = root
        self.raw = (root / BOARD).read_bytes() if raw is None else raw
        self.revision = sha256(self.raw).hexdigest()
        text = self.raw.decode("utf-8")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.trailing_newline = text.endswith("\n")
        self.lines = text.splitlines()
        self.start = next(
            (i for i, line in enumerate(self.lines)
             if line.strip().startswith("|") and cells(line) == list(COLUMNS.values())), -1,
        ) + 2
        if self.start < 2 or self.start > len(self.lines) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in cells(self.lines[self.start - 1])
        ) or len(cells(self.lines[self.start - 1])) != len(COLUMNS):
            raise ValueError("KANBAN.md 缺少四列任务表")
        self.end = self.start
        self.rows = []
        self.card_rows = {}
        self.cards = {}
        while self.end < len(self.lines) and self.lines[self.end].strip().startswith("|"):
            row = cells(self.lines[self.end])
            if len(row) != len(COLUMNS):
                raise ValueError(f"KANBAN.md 第 {self.end + 1} 行应有四列")
            for state, cell in zip(COLUMNS, row):
                if cell in EMPTY:
                    continue
                card = CARD.fullmatch(cell)
                if not card or card[1].lower() != card[2].lower():
                    raise ValueError(f"KANBAN.md 第 {self.end + 1} 行卡片应形如 [DEMO-01](#demo-01) · 【示例项目】任务")
                key = card[1].lower()
                if key in self.cards:
                    raise ValueError(f"看板卡号重复：{card[1]}")
                self.card_rows[key] = len(self.rows)
                self.cards[key] = {
                    "id": card[1], "project": card[3], "title": card[4].replace(r"\|", "|"),
                    "status": state, "cell": cell,
                }
            self.rows.append(row)
            self.end += 1
        self.sections = {}
        for index, line in enumerate(self.lines[self.end:], self.end):
            heading = re.fullmatch(rf"### ({ID})\s*", line)
            if not heading or heading[1].lower() not in self.cards:
                continue
            key = heading[1].lower()
            if key in self.sections:
                raise ValueError(f"卡片详情重复：{heading[1]}")
            end = next((i for i in range(index + 1, len(self.lines))
                        if re.match(r"^#{1,3} ", self.lines[i])), len(self.lines))
            self.sections[key] = (index + 1, end)
        for key, card in self.cards.items():
            if key not in self.sections:
                raise ValueError(f"缺少卡片详情标题：### {card['id']}")
            start, end = self.sections[key]
            lines = self.lines[start:end]
            fields, positions = read_fields(lines)
            notes, links = [], []
            for index, line in enumerate(lines):
                if not line.startswith("- ") or index in positions.values():
                    continue
                match = LINK.fullmatch(line[2:].strip())
                if match:
                    label, destination = link_parts(match)
                    href = link_url(root, BOARD, destination)
                    if href:
                        links.append({"label": label, "href": href})
                else:
                    notes.append(line[2:].strip())
            card.update(fields, notes=notes, links=links,
                        evidenceUrl=link_url(root, BOARD, fields["evidence"]))

    def snapshot(self):
        week = (self.root / WEEK).read_text(encoding="utf-8")
        period = re.search(r"^## (.*?)\s*·\s*(\d{4}-\d{2}-\d{2}) 至 (\d{4}-\d{2}-\d{2})", week, re.M)
        priorities = {}
        for line in week.splitlines():
            if not line.startswith("|"):
                continue
            for match in LINK.finditer(line):
                _, target = link_parts(match)
                if not target.startswith("KANBAN.md#"):
                    continue
                key = target.split("#", 1)[1].lower()
                if key not in self.cards:
                    continue
                fields = cells(line)
                role = fields[0].split(" · ", 1)[0]
                timebox = re.search(r"\d+\s*分钟", fields[-1])
                priorities[key] = role + (f" · {timebox[0]}" if timebox else "")
        return {
            "revision": self.revision, "columns": COLUMNS,
            "cards": [{k: v for k, v in card.items() if k != "cell"} for card in self.cards.values()],
            "week": {"label": period[1] if period else "周计划",
                     "start": period[2] if period else "",
                     "end": period[3] if period else "",
                     "href": "/file/" + WEEK.as_posix(), "priorities": priorities},
        }

    def without_cards(self, keys):
        """Remove only the selected table cells and their Markdown detail sections."""
        lines = self.raw.decode("utf-8").splitlines(keepends=True)
        for start, end in sorted((self.sections[key] for key in keys), reverse=True):
            del lines[start - 1:end]
        for row_index in sorted({self.card_rows[key] for key in keys}, reverse=True):
            index = self.start + row_index
            parts = re.split(r"(?<!\\)\|", lines[index])
            for column, cell in enumerate(self.rows[row_index], 1):
                card = CARD.fullmatch(cell)
                if card and card[1].lower() in keys:
                    parts[column] = parts[column].replace(cell, "—", 1)
            if all(cell.strip() in EMPTY for cell in parts[1:-1]):
                del lines[index]
            else:
                lines[index] = "|".join(parts)
        return "".join(lines).encode("utf-8")

    def restore_cards(self, original, keys):
        """Merge cards into a board that has changed since their removal."""
        lines = self.raw.decode("utf-8").splitlines(keepends=True)
        old_lines = original.raw.decode("utf-8").splitlines(keepends=True)
        rows, details = [], []
        for key in keys:
            card = original.cards[key]
            cells_in_row = ["—"] * len(COLUMNS)
            cells_in_row[list(COLUMNS).index(card["status"])] = card["cell"]
            rows.append("| " + " | ".join(cells_in_row) + " |" + self.newline)
        for start, end in sorted(original.sections[key] for key in keys):
            details.extend(old_lines[start - 1:end])
            if details and not details[-1].endswith("\n"):
                details.append(self.newline)
            details.append(self.newline)
        # Insert beside existing card details, before any unrelated trailing heading.
        if self.sections:
            insertion = max(end for _, end in self.sections.values())
        else:
            heading = next((i for i, line in enumerate(self.lines) if line.strip() == "## 卡片详情"), None)
            insertion = len(lines)
            if heading is None:
                details[:0] = ["## 卡片详情" + self.newline, self.newline]
            else:
                insertion = next((i for i in range(heading + 1, len(lines)) if re.match(r"^#{1,2} ", lines[i])),
                                 len(lines))
        lines[insertion:insertion] = [self.newline, *details]
        if self.end and not lines[self.end - 1].endswith("\n"):
            rows.insert(0, self.newline)
        lines[self.end:self.end] = rows
        return "".join(lines).encode("utf-8")

    def update(self, key, changes):
        if not isinstance(key, str) or key.lower() not in self.cards:
            raise ValueError("这张卡片已移出看板")
        if not isinstance(changes, dict) or not changes or set(changes) - (set(FIELDS) | {"status"}):
            raise ValueError("无效的卡片修改")
        key = key.lower()
        card = self.cards[key]
        for name, value in changes.items():
            if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x1f\x7f]", value):
                raise ValueError("字段应为单行文本")
            if name == "status" and value not in COLUMNS:
                raise ValueError("无效的任务状态")
            if name in {"followup", "due_date"} and value:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("跟进日期须为 YYYY-MM-DD")
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("跟进日期无效") from exc
            if name == "evidence" and value and not link_url(self.root, BOARD, value):
                raise ValueError("请填写 HTTP(S) 链接或相对于 KANBAN.md 的仓库内路径")
        lines = self.lines[:]
        start, end = self.sections[key]
        detail = lines[start:end]
        _, positions = read_fields(detail)
        for name in FIELDS:
            if name not in changes:
                continue
            value = changes[name].strip()
            index = positions.get(name)
            if index is not None:
                detail[index] = field_line(name, value) if value else None
            elif value:
                insertion = next((i + 1 for i in range(len(detail) - 1, -1, -1) if detail[i]), 0)
                detail.insert(insertion, field_line(name, value))
                positions = {field: i + (i >= insertion) for field, i in positions.items()}
        lines[start:end] = [line for line in detail if line is not None]
        target = changes.get("status", card["status"])
        if target != card["status"]:
            source_column = list(COLUMNS).index(card["status"])
            target_column = list(COLUMNS).index(target)
            source_row = self.card_rows[key]
            last_in_target = max(
                (i for i, row in enumerate(self.rows) if row[target_column] not in EMPTY),
                default=-1,
            )
            target_row = last_in_target + 1 if last_in_target >= 0 else source_row
            rows = self.rows[:]
            rows[source_row] = rows[source_row][:]
            rows[source_row][source_column] = "—"
            if target_row == len(rows):
                rows.append(["—"] * len(COLUMNS))
            elif target_row != source_row:
                rows[target_row] = rows[target_row][:]
            rows[target_row][target_column] = card["cell"]
            changed = {source_row, target_row}
            lines[self.start:self.end] = [
                ("| " + " | ".join(row) + " |") if i in changed else self.lines[self.start + i]
                for i, row in enumerate(rows)
                if i != source_row or any(cell not in EMPTY for cell in row)
            ]
        return self.newline.join(lines) + (self.newline if self.trailing_newline else "")


def field_line(name, value):
    if name == "evidence":
        value = f"[查看证据](<{value}>)"
    return f"- {FIELDS[name]}：{value}"


def document_html(root, relative):
    """Give linked Markdown files a lightweight, clickable source view."""
    content = (root / relative).read_text(encoding="utf-8")
    lines = []
    for line in content.splitlines():
        heading = re.match(r"^#{1,6} (.+)$", line)
        if heading:
            anchor = re.sub(r"[^\w -]", "", heading[1].lower()).replace(" ", "-")
            lines.append(f'<span id="{escape(anchor)}">{escape(line)}</span>')
            continue
        pieces, start = [], 0
        for match in LINK.finditer(line):
            label, value = link_parts(match)
            href = link_url(root, relative, value)
            if href:
                pieces.extend((escape(line[start:match.start()]), f'<a href="{escape(href)}">{escape(label)}</a>'))
                start = match.end()
        lines.append("".join(pieces) + escape(line[start:]))
    title = escape(relative.name)
    body = "\n".join(lines)
    return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width"><title>{title}</title>'
            f'<link rel="stylesheet" href="/style.css"><main class="board-document">'
            f'<a href="/">← 科研看板</a><h1>{title}</h1><pre>{body}</pre></main></html>')


class BoardHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_content(self, status, content, media="application/json; charset=utf-8"):
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'")
        if media == "application/octet-stream":
            self.send_header("Content-Disposition", "attachment")
        self.end_headers()
        self.wfile.write(content)

    def allowed(self):
        host = f"127.0.0.1:{self.server.server_port}"
        return self.headers.get("Host") == host and self.headers.get("Origin") in (None, f"http://{host}")

    def do_GET(self):
        if not self.allowed():
            return self.send_content(403, {"error": "请从看板启动地址访问"})
        root = self.server.root
        static = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                  "/app.js": ("app.js", "text/javascript")}
        try:
            path = urlsplit(self.path).path
            if path in static:
                name, media = static[path]
                return self.send_content(200, (ASSETS / name).read_bytes(), media + "; charset=utf-8")
            if path == "/api/board":
                return self.send_content(200, Board(root).snapshot())
            if path.startswith("/file/"):
                relative = Path(unquote(path.removeprefix("/file/")))
                file = (root / relative).resolve()
                if (file.is_relative_to(root) and not any(part.startswith(".") for part in relative.parts)
                        and not any(part.startswith(".") for part in file.relative_to(root).parts) and file.is_file()):
                    relative = file.relative_to(root)
                    if file.suffix.lower() == ".md":
                        return self.send_content(200, document_html(root, relative), "text/html; charset=utf-8")
                    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                             ".webp": "image/webp", ".pdf": "application/pdf"}.get(file.suffix.lower())
                    return self.send_content(200, file.read_bytes(), media or "application/octet-stream")
            self.send_content(404, {"error": "未找到文件"})
        except (OSError, ValueError) as exc:
            self.send_content(400, {"error": str(exc)})

    def do_PATCH(self):
        if not self.allowed():
            return self.send_content(403, {"error": "请从看板启动地址访问"})
        if self.path != "/api/board":
            return self.send_content(404, {"error": "未找到接口"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 16384:
                raise ValueError("无效的更新请求")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"revision", "id", "changes"}:
                raise ValueError("无效的更新请求")
            from sr_workbench_store import legacy_board_update, WorkbenchError
            try:
                snapshot = legacy_board_update(self.server.root, payload["revision"], payload["id"], payload["changes"])
            except WorkbenchError as exc:
                return self.send_content(409 if exc.code in {"conflict", "recovery_required"} else 400, {"error": str(exc)})
            self.send_content(200, snapshot)
        except (OSError, ValueError) as exc:
            self.send_content(400, {"error": str(exc)})


def serve(root, args):
    Board(root).snapshot()
    with ThreadingHTTPServer(("127.0.0.1", args.port), BoardHandler) as server:
        server.root = root
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"科研看板 {url}（Ctrl+C 停止）", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
