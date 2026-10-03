#!/usr/bin/env python3
"""Small, repository-local research asset manager."""
from pathlib import Path
import argparse, os, re, shutil, sys, textwrap
from datetime import datetime
import sr_library as library

ROOT = Path(__file__).resolve().parents[1]

CARD_KINDS = ("灵感", "细节", "问题", "决定")
CARD_STATUSES = ("收件", "发展中", "已确认", "已归档")

def rel(p): return p.relative_to(ROOT)

def doctor(args):
    report = library.diagnose(ROOT, args.verify)
    if args.json:
        print(library.json_text(report), end="")
    else:
        for item in report["issues"]:
            print(f"{item['level'].upper()} [{item['code']}] {item['path']}: {item['message']}")
        errors = sum(item["level"] == "error" for item in report["issues"])
        warnings = len(report["issues"]) - errors
        print(f"{'ERROR' if errors else 'WARN' if warnings else 'OK'} · {report['papers']} 篇文献 · "
              f"{report['projects']} 个项目 · {errors} 个错误 · {warnings} 个提醒")
    return int(not report["ok"] or (args.strict and bool(report["issues"])))

def add_file(args):
    source = Path(args.path).expanduser()
    if source.is_symlink():
        raise ValueError("请提供 PDF 原文件路径；登记时不移走软链接的目标")
    source = source.resolve()
    if not source.is_file():
        raise ValueError(f"file not found: {args.path}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("add-file only accepts PDF files")
    if source.parent == (ROOT / "library/papers").resolve():
        raise ValueError("source is already in library/papers; import from inbox or an external path")
    with source.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError("file header is not PDF")
    key = library.valid_key(args.as_key)
    entries = library.bibliography(ROOT)
    if key in entries:
        raise ValueError(f"bibkey exists: {key}")
    doi = library.normalized_doi(args.doi)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ValueError("invalid DOI; supply verified metadata")
    for existing, entry in entries.items():
        if library.normalized_doi(entry["fields"].get("doi", "")) == doi:
            raise ValueError(f"DOI already registered: {existing}")
    if not re.fullmatch(r"\d{4}", args.year):
        raise ValueError("year must contain four digits")
    bib = ROOT / "library/references.bib"
    target = ROOT / "library/papers" / f"{key}.pdf"
    if target.exists():
        raise ValueError(f"exists: {rel(target)}")
    fields = [
        ("title", args.title),
        ("author", args.author),
        ("journal", args.journal),
        ("year", args.year),
        ("doi", doi),
    ]
    for name, value in (("volume", args.volume), ("number", args.number),
                        ("pages", args.pages), ("article-number", args.article_number),
                        ("url", args.url)):
        if value:
            fields.append((name, value))
    entry = "@article{" + key + ",\n" + "".join(
        f"  {name} = {{{value.strip()}}},\n" for name, value in fields
    ) + "}\n"
    parsed = library.parse_bib(entry)
    if list(parsed) != [key] or set(parsed[key]["fields"]) != {name for name, _ in fields}:
        raise ValueError("metadata contains invalid BibTeX delimiters")
    if any(parsed[key]["fields"][name] != value.strip() or not value.strip() for name, value in fields):
        raise ValueError("metadata fields must be nonempty, balanced BibTeX literals")
    entries[key] = parsed[key]
    inventory = library.inventory_data(ROOT)
    if key in inventory["papers"]:
        raise ValueError(f"checksum already exists: {key}; inspect sr doctor")
    # Read once for the staged archive; source stays in place until every write succeeds.
    source_stat = source.stat()
    raw = source.read_bytes()
    import hashlib
    inventory["papers"][key] = {"path": rel(target).as_posix(), "size_bytes": len(raw),
                                 "sha256": hashlib.sha256(raw).hexdigest()}
    library.write_files({target: raw,
        bib: bib.read_text(encoding="utf-8").rstrip() + "\n\n" + entry,
        ROOT / "sync/assets.json": library.json_text(inventory),
        ROOT / "library/index.md": library.index_text(ROOT, entries)})
    try:
        current_stat = source.stat()
        if (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns) != (current_stat.st_ino, current_stat.st_size, current_stat.st_mtime_ns):
            print(f"WARNING: 登记期间源文件发生变化，已保留：{source}", file=sys.stderr)
        else:
            source.unlink()
    except OSError as exc:
        print(f"WARNING: 已归档，但源文件仍保留：{source} ({exc})", file=sys.stderr)
    print(rel(target))

def new(args):
    name = args.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("项目名请使用字母、数字、下划线或连字符，且以字母或数字开头")
    target = ROOT / "projects" / name
    if target.exists(): raise SystemExit(f"exists: {rel(target)}")
    shutil.copytree(ROOT / "projects" / "_template", target)
    readme = target / "README.md"
    template = readme.read_text(encoding="utf-8").replace("# Project", f"# {name}", 1)
    template = template.replace("project-name", name)
    template = template.replace(f"# {name}\n", f"# {name}\n\n{args.desc or '研究项目。'}\n", 1)
    readme.write_text(template, encoding="utf-8")
    print(rel(target))

def note(args):
    key = library.valid_key(args.bibkey)
    entry = library.require_entry(ROOT, key)
    path = ROOT / "library/notes" / f"{key}.md"
    if path.exists(): raise SystemExit(f"exists: {rel(path)}")
    doi = entry["fields"].get("doi", "")
    content = (f"# {key}\n\n- 状态：未读\n- 原文：\n"
               f"  - PDF：`library/papers/{key}.pdf`\n"
               + (f"  - DOI：https://doi.org/{library.normalized_doi(doi)}\n" if doi else "")
               + "\n## 摘要与证据\n\n## 我的话（本人填写）\n\n## 我的推导（本人填写）\n\n## 卡点\n")
    library.write_files({path: content, ROOT / "library/index.md": library.index_text(ROOT, overrides={key: content})})
    print(rel(path))

def status(args):
    library.require_entry(ROOT, args.bibkey)
    path = ROOT / "library/notes" / f"{args.bibkey}.md"
    if not path.exists(): raise SystemExit("note not found")
    text = path.read_text(encoding="utf-8")
    library.note_status(text)
    if args.value not in library.READING_STATUSES:
        raise ValueError("invalid reading status")
    text = library.STATUS_LINE.sub(lambda _: f"- 状态：{args.value}", text, count=1)
    library.write_files({path: text, ROOT / "library/index.md": library.index_text(ROOT, overrides={args.bibkey: text})})
    print(f"{args.bibkey}: {args.value}（索引已同步）")

def find(args):
    excluded = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
    extensions = {".md", ".txt", ".bib", ".json", ".py", ".toml", ".yaml", ".yml"}
    for directory, dirs, files in os.walk(ROOT, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in excluded and not name.startswith(".")
                         and Path(directory, name) not in {ROOT / "library/papers", ROOT / "library/inbox"})
        for name in sorted(files):
            p = Path(directory, name)
            if p.suffix.lower() not in extensions or p.is_symlink() or p.stat().st_size > 2 * 1024 * 1024:
                continue
            try:
                if args.query.lower() in p.read_text(encoding="utf-8").lower(): print(rel(p))
            except (UnicodeDecodeError, OSError):
                print(f"WARNING: 无法读取 {rel(p)}", file=sys.stderr)

def bib_command(args):
    if args.bibkey:
        print(library.require_entry(ROOT, args.bibkey)["raw"])
    else:
        library.bibliography(ROOT)
        print((ROOT / "library/references.bib").read_text(encoding="utf-8"), end="")

def list_command(args):
    rows = library.paper_rows(ROOT)
    if args.status:
        rows = [row for row in rows if row["status"] == args.status]
    if args.json:
        print(library.json_text(rows), end="")
    else:
        for row in rows:
            print(f"{row['bibkey']} | {row['status']} | {'PDF' if row['pdf'] else '缺 PDF'} | {row['title']}")
        print(f"共 {len(rows)} 篇")

def index_command(args):
    path = ROOT / "library/index.md"
    expected = library.index_text(ROOT)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if args.check:
        print("OK: 索引一致" if expected == current else "索引待同步：运行 sr index")
        return int(expected != current)
    if expected != current:
        library.write_files({path: expected})
    print("library/index.md：已同步" if expected != current else "library/index.md：无需变更")

def inventory_command(_):
    entries = library.bibliography(ROOT)
    data = library.inventory_data(ROOT)
    legacy = library.legacy_checksums(ROOT)
    if set(data["papers"]) - set(entries):
        raise ValueError("校验记录含未登记 bibkey；请先运行 sr doctor")
    added = 0
    for key in sorted(entries):
        path = ROOT / "library/papers" / f"{key}.pdf"
        with path.open("rb") as handle:
            if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                raise ValueError(f"file header is not PDF: {key}")
        record = library.file_record(ROOT, path)
        if key in legacy and legacy[key] != record["sha256"]:
            raise ValueError(f"historical checksum mismatch: {key}; 请核对 sync/manifest.md 中的原校验记录")
        previous = data["papers"].get(key)
        if previous is not None and previous != record:
            raise ValueError(f"checksum mismatch: {key}; 保留原校验基线，请核对原件")
        if previous is None:
            data["papers"][key] = record
            added += 1
    if added or not (ROOT / "sync/assets.json").exists():
        library.write_files({ROOT / "sync/assets.json": library.json_text(data)})
    print(f"sync/assets.json：新增 {added} 条，已核对 {len(entries)} 份 PDF；本机校验不代表外部备份")

def overview(args):
    papers = library.paper_rows(ROOT)
    counts = {status: sum(row["status"] == status for row in papers)
              for status in ("未建卡",) + library.READING_STATUSES}
    report = {"papers": len(papers), "reading": counts,
              "inbox": library.inbox_files(ROOT), "projects": library.project_rows(ROOT)}
    if args.json:
        print(library.json_text(report), end="")
        return
    print(f"文献 {len(papers)} 篇 · " + " · ".join(f"{key} {value}" for key, value in counts.items()))
    print(f"收件箱：{len(report['inbox'])} 个待登记文件")
    for row in report["projects"]:
        print(f"\n{row['project']} · {row['stage'] or '阶段未填'} · {row['updated'] or '日期未填'}")
        print(f"  下一步：{row['next_action'] or '未填写'}")
        print(f"  断点：{row['state']}")

def help_command(args):
    topics = {
        "zotero": """Zotero 接入：
  sr zotero status                        检查本机连接与文献对应数量
  sr zotero list [QUERY]                  查找 Zotero 条目 key 和 PDF 附件
  sr zotero open BIBKEY [--page N]         在 Zotero 阅读已有 sr 论文
  sr zotero import ITEMKEY --as BIBKEY     预览登记；加 --apply 才写入
  sr zotero export --output FILE.bib       导出含 PDF 路径的副本，中央书目不变
  sr zotero sync                          按唯一 DOI 刷新对应关系

Zotero 需运行并启用本地 API。导出后在 Zotero 选择“链接到文件的原始位置”。
已有 DOI 沿用 sr bibkey；导入不移走 Zotero PDF、不覆盖精读卡和个人理解。
详细用法：projects/research-system/ZOTERO_GUIDE.md""",
        "card": """闪卡命令：
  sr card add PROJECT                         快速录入一张闪卡
  sr card add PROJECT --title TITLE --front TEXT
                                               使用参数录入
  sr card list PROJECT [--status STATUS]       列出闪卡
  sr card show PROJECT CARD_ID                 查看闪卡
  sr card update PROJECT CARD_ID [OPTIONS]     更新闪卡
  sr tui PROJECT                               打开终端界面

类型：灵感、细节、问题、决定
状态：收件、发展中、已确认、已归档""",
        "tui": """TUI 快捷键：
  ↑↓ / j k   移动
  Enter      查看闪卡
  n          新建闪卡
  e          编辑闪卡
  s          循环修改状态
  /          搜索
  f          按状态过滤
  p          切换项目
  c          清除过滤
  q          退出""",
    }
    if args.topic:
        if args.topic not in topics:
            raise SystemExit(f"未知帮助主题：{args.topic}；可用主题：card、tui、zotero")
        print(topics[args.topic])
        return
    print("""科研资产工具 sr

用法：
  sr overview                       查看文献、收件箱和项目下一步
  sr doctor [--verify] [--strict]    检查资产一致性；verify 复核 SHA-256
  sr list [--status STATUS]          文献列表（可用 --json）
  sr bib [BIBKEY]                    查看原始 BibTeX 条目
  sr index [--check]                 同步索引或只检查漂移
  sr inventory                      为已有 PDF 补记本机校验基线
  sr new PROJECT                    创建研究项目
  sr add-file PATH ...              登记 PDF 文献
  sr note BIBKEY                    创建文献精读卡
  sr status BIBKEY STATUS           更新文献状态
  sr find QUERY                     搜索科研资产
  sr zotero ...                     连接 Zotero（status/list/export/import/sync/open）
  sr read ...                       AI 伴读入口（open/context/records）
  sr agents ...                    运行多智能体科研工作流（init/plan/call/run/validate/status）
  sr card ...                       管理项目闪卡
  sr tui [PROJECT]                  打开闪卡终端界面
  sr help [card|tui|zotero]         查看帮助

快速开始：
  sr new ai4physics --desc "研究目标"
  sr card add ai4physics
  sr tui ai4physics

查看某个命令的参数：
  sr card --help
  sr card add --help""")

def project_path(name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name == "_template":
        raise SystemExit("invalid project name")
    path = ROOT / "projects" / name
    if not path.is_dir():
        raise SystemExit(f"project not found: {name}")
    return path

def card_dir(name):
    path = project_path(name) / "cards"
    path.mkdir(exist_ok=True)
    return path

def card_meta(text, field):
    match = re.search(rf"^{re.escape(field)}:\s*(.*)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""

def card_title(text):
    return card_meta(text, "title") or next(
        (line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "(无标题)"
    )

def card_file(directory, card_id):
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]+)?", card_id):
        raise SystemExit("invalid card id")
    candidates = list(directory.glob(f"{card_id}-*.md")) + list(directory.glob(f"{card_id}.md"))
    matches = [path for path in candidates if card_meta(path.read_text(encoding="utf-8"), "id") == card_id]
    if not matches:
        raise SystemExit(f"card not found: {card_id}")
    if len(matches) != 1:
        raise SystemExit(f"duplicate card id: {card_id}")
    return matches[0]

def create_card(project, title, front, back=None, kind="灵感", status="收件", tags_text=None):
    directory = card_dir(project)
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    card_id = now
    suffix = 2
    while list(directory.glob(f"{card_id}-*.md")) or (directory / f"{card_id}.md").exists():
        card_id = f"{now}-{suffix}"
        suffix += 1
    safe_title = " ".join(title.splitlines()).strip()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", safe_title).strip("-")[:48] or "card"
    tags = [tag.strip() for tag in (tags_text or "").split(",") if tag.strip()]
    tag_text = "[" + ", ".join(tags) + "]"
    timestamp = datetime.now().isoformat(timespec="minutes")
    content = (
        "---\n"
        f"id: {card_id}\n"
        f"title: {safe_title}\n"
        f"type: {kind}\n"
        f"status: {status}\n"
        f"tags: {tag_text}\n"
        f"created: {timestamp}\n"
        f"updated: {timestamp}\n"
        "---\n\n"
        f"# {safe_title}\n\n"
        "## 正面\n\n"
        f"{front.strip()}\n\n"
        "## 展开 / 背面\n\n"
        f"{(back or '待补充').strip()}\n\n"
        "## 关联\n\n"
        "- 文献 bibkey：\n"
        "- 文件 / 实验：\n\n"
        "## 下一步\n\n"
        "- "
    )
    path = directory / f"{card_id}-{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path

def card_add(args):
    title = args.title
    front = args.front
    if title is None or front is None:
        print("快速录入闪卡（直接回车可留空背面）")
        title = title or input("标题：").strip()
        front = front or input("正面 / 提示：").strip()
        if args.back is None:
            args.back = input("展开 / 背面：").strip()
    if not title or not front:
        raise SystemExit("标题和正面不能为空")
    path = create_card(args.project, title, front, args.back, args.kind, args.status, args.tags)
    print(rel(path))

def load_cards(project, status=None, kind=None, query=None):
    directory = card_dir(project)
    cards = []
    for path in directory.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not card_meta(text, "id"):
            continue
        if status and card_meta(text, "status") != status:
            continue
        if kind and card_meta(text, "type") != kind:
            continue
        if query and query.lower() not in text.lower():
            continue
        cards.append((card_meta(text, "created"), path, text))
    return sorted(cards, key=lambda item: item[0], reverse=True)

def card_list(args):
    cards = load_cards(args.project, args.status, args.kind, args.query)
    for _, path, text in cards:
        tags = card_meta(text, "tags")
        print(
            f"{card_meta(text, 'id')} | {card_meta(text, 'type')} | "
            f"{card_meta(text, 'status')} | {card_title(text)}"
            + (f" | {tags}" if tags != "[]" else "")
        )
    if not cards:
        print("暂无闪卡")

def replace_section(text, heading, value):
    pattern = rf"(^## {re.escape(heading)}\n\n)(.*?)(?=^## |\Z)"
    replacement = lambda match: match.group(1) + value.strip() + "\n\n"
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if not count:
        raise SystemExit(f"section not found: {heading}")
    return updated

def apply_card_update(path, args):
    text = path.read_text(encoding="utf-8")
    changed = False
    if args.title is not None:
        title = " ".join(args.title.splitlines()).strip()
        text = re.sub(r"^title:.*$", lambda _: f"title: {title}", text, count=1, flags=re.MULTILINE)
        text = re.sub(r"^# .*$", lambda _: f"# {title}", text, count=1, flags=re.MULTILINE)
        changed = True
    for field, value in (("type", args.kind), ("status", args.status)):
        if value is not None:
            text = re.sub(rf"^{field}:.*$", lambda _: f"{field}: {value}", text, count=1, flags=re.MULTILINE)
            changed = True
    if args.tags is not None:
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        text = re.sub(r"^tags:.*$", lambda _: "tags: [" + ", ".join(tags) + "]", text, count=1, flags=re.MULTILINE)
        changed = True
    if args.front is not None:
        text = replace_section(text, "正面", args.front)
        changed = True
    if args.back is not None:
        text = replace_section(text, "展开 / 背面", args.back)
        changed = True
    if not changed:
        raise SystemExit("至少提供一个要更新的字段")
    timestamp = datetime.now().isoformat(timespec="minutes")
    text = re.sub(r"^updated:.*$", lambda _: f"updated: {timestamp}", text, count=1, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")

def card_update(args):
    directory = card_dir(args.project)
    path = card_file(directory, args.card_id)
    apply_card_update(path, args)
    print(rel(path))

def card_show(args):
    path = card_file(card_dir(args.project), args.card_id)
    print(path.read_text(encoding="utf-8"), end="")

def section_content(text, heading):
    pattern = rf"^## {re.escape(heading)}\n\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""

def tui_add_card(stdscr, project):
    title = tui_prompt(stdscr, "标题")
    if not title:
        return "已取消"
    front = tui_prompt(stdscr, "正面 / 提示")
    if not front:
        return "已取消：正面不能为空"
    back = tui_prompt(stdscr, "展开 / 背面（可留空）")
    kind = tui_prompt(stdscr, "类型：灵感 / 细节 / 问题 / 决定", "灵感")
    if kind not in CARD_KINDS:
        kind = "灵感"
    tags = tui_prompt(stdscr, "标签，用逗号分隔（可留空）")
    path = create_card(project, title, front, back, kind, "收件", tags)
    return f"已创建 {path.stem}"

def tui_edit_card(stdscr, project, card):
    path = card[1]
    text = card[2]
    title = tui_prompt(stdscr, "标题", card_title(text))
    front = tui_prompt(stdscr, "正面 / 提示", section_content(text, "正面").replace("\n", " / "))
    back = tui_prompt(stdscr, "展开 / 背面", section_content(text, "展开 / 背面").replace("\n", " / "))
    kind = tui_prompt(stdscr, "类型：灵感 / 细节 / 问题 / 决定", card_meta(text, "type"))
    if kind not in CARD_KINDS:
        kind = card_meta(text, "type") or "灵感"
    status = tui_prompt(stdscr, "状态：收件 / 发展中 / 已确认 / 已归档", card_meta(text, "status"))
    if status not in CARD_STATUSES:
        status = card_meta(text, "status") or "收件"
    tags = tui_prompt(stdscr, "标签，用逗号分隔", card_meta(text, "tags").strip("[]"))
    update = type("Update", (), {
        "title": title, "front": front, "back": back, "kind": kind,
        "status": status, "tags": tags,
    })()
    apply_card_update(path, update)
    return "已更新"

def tui_prompt(stdscr, label, default=""):
    import curses
    height, width = stdscr.getmaxyx()
    stdscr.move(max(0, height - 2), 0)
    stdscr.clrtoeol()
    stdscr.addnstr(max(0, height - 2), 0, f"{label}" + (f" [{default}]" if default else "") + "：", max(1, width - 1))
    stdscr.move(max(0, height - 1), 0)
    stdscr.clrtoeol()
    try:
        curses.echo()
        raw = stdscr.getstr(max(0, height - 1), 0, max(1, width - 1))
    finally:
        curses.noecho()
    value = raw.decode("utf-8", errors="replace").strip()
    return value or default

def tui_view(stdscr, text):
    import curses
    offset = 0
    while True:
        height, width = stdscr.getmaxyx()
        lines = []
        for line in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(line, max(10, width - 2)) or [""])
        stdscr.erase()
        stdscr.addnstr(0, 0, "闪卡详情 · ↑↓滚动 · q 返回", max(1, width - 1), curses.A_BOLD)
        for row, line in enumerate(lines[offset:offset + max(1, height - 2)], start=1):
            stdscr.addnstr(row, 0, line, max(1, width - 1))
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return
        if key in (curses.KEY_DOWN, ord("j")):
            offset = min(offset + 1, max(0, len(lines) - height + 2))
        elif key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        elif key == curses.KEY_NPAGE:
            offset = min(offset + max(1, height - 2), max(0, len(lines) - height + 2))
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - max(1, height - 2))

def tui_project_picker(stdscr, current=None):
    import curses
    projects = sorted(p.name for p in (ROOT / "projects").iterdir() if p.is_dir() and p.name != "_template")
    if not projects:
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addnstr(1, 2, "暂无项目，请先运行：sr new project-name", max(1, width - 3))
        stdscr.addnstr(3, 2, "按 q 退出", max(1, width - 3))
        stdscr.refresh()
        while stdscr.getch() not in (ord("q"), 27):
            pass
        return None
    selected = projects.index(current) if current in projects else 0
    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addnstr(0, 0, "选择项目 · ↑↓移动 · Enter确认 · q退出", max(1, width - 1), curses.A_BOLD)
        for row, name in enumerate(projects[:max(1, height - 2)], start=1):
            attr = curses.A_REVERSE if row - 1 == selected else curses.A_NORMAL
            stdscr.addnstr(row, 0, ("> " if row - 1 == selected else "  ") + name, max(1, width - 1), attr)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return None
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(selected + 1, len(projects) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            return projects[selected]

def card_tui(stdscr, project=None):
    import curses
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    project = project or tui_project_picker(stdscr)
    if not project:
        return
    query = ""
    status = None
    kind = None
    selected = 0
    message = ""
    while True:
        cards = load_cards(project, status, kind, query)
        selected = min(selected, max(0, len(cards) - 1))
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        title = f"闪卡 · {project} · {len(cards)} 张"
        if query:
            title += f" · 搜索：{query}"
        if status:
            title += f" · 状态：{status}"
        stdscr.addnstr(0, 0, title, max(1, width - 1), curses.A_BOLD)
        if message:
            stdscr.addnstr(1, 0, message, max(1, width - 1), curses.A_DIM)
        visible = max(1, height - 5)
        start = max(0, min(selected - visible + 1, len(cards) - visible))
        for row, (_, path, text) in enumerate(cards[start:start + visible], start=2):
            index = start + row - 2
            tags = card_meta(text, "tags")
            line = f"{'▶' if index == selected else ' '} [{card_meta(text, 'status')}] {card_meta(text, 'type')} · {card_title(text)}"
            if tags != "[]":
                line += f" · {tags}"
            attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
            stdscr.addnstr(row, 0, line, max(1, width - 1), attr)
        if not cards:
            stdscr.addnstr(3, 2, "暂无匹配闪卡，按 n 新建", max(1, width - 3))
        help_text = "↑↓选择 Enter查看 n新建 e编辑 s状态 /搜索 f状态过滤 p切换项目 c清除 q退出"
        stdscr.addnstr(max(0, height - 2), 0, help_text, max(1, width - 1), curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        message = ""
        if key in (ord("q"), 27):
            return
        if key in (curses.KEY_DOWN, ord("j")) and cards:
            selected = min(selected + 1, len(cards) - 1)
        elif key in (curses.KEY_UP, ord("k")) and cards:
            selected = max(0, selected - 1)
        elif key in (curses.KEY_ENTER, 10, 13) and cards:
            tui_view(stdscr, cards[selected][2])
        elif key == ord("n"):
            message = tui_add_card(stdscr, project)
        elif key == ord("e") and cards:
            message = tui_edit_card(stdscr, project, cards[selected])
        elif key == ord("s") and cards:
            current = card_meta(cards[selected][2], "status")
            current_index = CARD_STATUSES.index(current) if current in CARD_STATUSES else 0
            next_status = CARD_STATUSES[(current_index + 1) % len(CARD_STATUSES)]
            update = type("Update", (), {"title": None, "front": None, "back": None, "kind": None, "status": next_status, "tags": None})()
            apply_card_update(cards[selected][1], update)
            message = f"状态 → {next_status}"
        elif key == ord("/"):
            query = tui_prompt(stdscr, "搜索（留空显示全部）", query)
            selected = 0
        elif key == ord("c"):
            query = ""
            status = None
            kind = None
        elif key == ord("f"):
            value = tui_prompt(stdscr, "状态过滤：收件 / 发展中 / 已确认 / 已归档（留空全部）", status or "")
            status = value if value in CARD_STATUSES else None
            selected = 0
        elif key == ord("p"):
            project = tui_project_picker(stdscr, project) or project
            selected = 0

def tui_main(args):
    try:
        import curses
        curses.wrapper(lambda stdscr: card_tui(stdscr, args.project))
    except ImportError as exc:
        raise SystemExit("当前 Python 没有 curses，无法启动 TUI") from exc

def main(argv=None):
    ap = argparse.ArgumentParser(prog="sr"); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="check asset consistency")
    p.add_argument("--verify", action="store_true", help="recompute PDF SHA-256")
    p.add_argument("--strict", action="store_true", help="fail on warnings as well as errors")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=doctor)
    p = sub.add_parser("overview", help="show project checkpoints and library progress")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=overview)
    p = sub.add_parser("list", help="list registered papers")
    p.add_argument("--status", choices=("未建卡",) + library.READING_STATUSES)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=list_command)
    p = sub.add_parser("bib", help="show original BibTeX entries")
    p.add_argument("bibkey", nargs="?"); p.set_defaults(fn=bib_command)
    p = sub.add_parser("index", help="synchronize reading status and asset links")
    p.add_argument("--check", action="store_true"); p.set_defaults(fn=index_command)
    p = sub.add_parser("inventory", help="record missing PDF checksums without replacing baselines")
    p.set_defaults(fn=inventory_command)
    p = sub.add_parser("add-file"); p.add_argument("path"); p.add_argument("--doi", required=True)
    p.add_argument("--as", dest="as_key", required=True); p.add_argument("--title", required=True)
    p.add_argument("--author", required=True); p.add_argument("--journal", required=True)
    p.add_argument("--year", required=True); p.add_argument("--volume"); p.add_argument("--number")
    p.add_argument("--pages"); p.add_argument("--article-number"); p.add_argument("--url")
    p.set_defaults(fn=add_file)
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("--desc"); p.set_defaults(fn=new)
    p = sub.add_parser("note"); p.add_argument("bibkey"); p.set_defaults(fn=note)
    p = sub.add_parser("status"); p.add_argument("bibkey"); p.add_argument("value", choices=["未读", "粗读", "精读中", "已讲通"]); p.set_defaults(fn=status)
    p = sub.add_parser("find"); p.add_argument("query"); p.set_defaults(fn=find)
    p = sub.add_parser("zotero", help="connect the local Zotero reader and library", add_help=False)
    p.add_argument("-h", "--help", action="store_true", dest="zotero_help")
    p.add_argument("zotero_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda args: __import__("sr_zotero").main(ROOT, ["--help"] if args.zotero_help else args.zotero_args))
    p = sub.add_parser("read", help="use the Zotero AI reader and sr reading records", add_help=False)
    p.add_argument("-h", "--help", action="store_true", dest="reading_help")
    p.add_argument("reading_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda args: __import__("sr_reading").main(ROOT, ["--help"] if args.reading_help else args.reading_args))
    p = sub.add_parser("agents", help="run the multi-agent research workflow")
    p.add_argument("agent_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda args: __import__("sr_agents").main(args.agent_args))
    p = sub.add_parser("help", help="show Chinese usage help")
    p.add_argument("topic", nargs="?", choices=["card", "tui", "zotero"]); p.set_defaults(fn=help_command)
    p = sub.add_parser("tui", help="open the flashcard terminal interface")
    p.add_argument("project", nargs="?"); p.set_defaults(fn=tui_main)
    p = sub.add_parser("card", help="manage project flashcards")
    card_sub = p.add_subparsers(dest="card_cmd", required=True)
    p = card_sub.add_parser("add", help="add a flashcard")
    p.add_argument("project"); p.add_argument("--title"); p.add_argument("--front")
    p.add_argument("--back"); p.add_argument("--kind", choices=CARD_KINDS, default="灵感")
    p.add_argument("--status", choices=CARD_STATUSES, default="收件"); p.add_argument("--tags")
    p.set_defaults(fn=card_add)
    p = card_sub.add_parser("list", help="list project flashcards")
    p.add_argument("project"); p.add_argument("--status", choices=CARD_STATUSES); p.add_argument("--kind", choices=CARD_KINDS)
    p.add_argument("--query"); p.set_defaults(fn=card_list)
    p = card_sub.add_parser("show", help="show one flashcard")
    p.add_argument("project"); p.add_argument("card_id"); p.set_defaults(fn=card_show)
    p = card_sub.add_parser("update", help="update one flashcard")
    p.add_argument("project"); p.add_argument("card_id"); p.add_argument("--title"); p.add_argument("--front")
    p.add_argument("--back"); p.add_argument("--kind", choices=CARD_KINDS); p.add_argument("--status", choices=CARD_STATUSES)
    p.add_argument("--tags"); p.set_defaults(fn=card_update)
    p = card_sub.add_parser("tui", help="open the flashcard terminal interface")
    p.add_argument("project", nargs="?"); p.set_defaults(fn=tui_main)
    args = ap.parse_args(argv)
    try:
        if args.cmd in {"add-file", "note", "status", "inventory"} or (args.cmd == "index" and not args.check):
            with library.writer_lock(ROOT):
                return args.fn(args)
        return args.fn(args)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__": sys.exit(main() or 0)
