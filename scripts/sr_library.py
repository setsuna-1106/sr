"""Local bibliography, index and integrity helpers (Python standard library only)."""

from contextlib import contextmanager
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


READING_STATUSES = ("未读", "粗读", "精读中", "已讲通")
STATUS_LINE = re.compile(r"^- 状态：[ \t]*(.*)$", re.MULTILINE)
INDEX_ROW = re.compile(r"^\|\s*`([A-Za-z0-9_-]+)`\s*\|")


def valid_key(key):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"invalid bibkey: {key}")
    return key


def parse_bib(text):
    """Parse the literal BibTeX entries written by sr, preserving original entries.

    Braced/quoted strings, nested braces and bare numbers are supported. Macros
    and concatenation are rejected rather than silently misinterpreted.
    """
    entries = {}
    pos = 0

    def skip():
        nonlocal pos
        while pos < len(text):
            if text[pos].isspace():
                pos += 1
            elif text[pos] == "%":
                end = text.find("\n", pos)
                pos = len(text) if end < 0 else end + 1
            else:
                break

    def delimited(opener):
        nonlocal pos
        start = pos = pos + 1
        depth = 0 if opener == '"' else 1
        while pos < len(text):
            char = text[pos]
            if char == "\\":
                pos += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    raise ValueError("unbalanced braces in BibTeX value")
                if opener == "{" and depth == 0:
                    value = text[start:pos]
                    pos += 1
                    return value
            elif opener == '"' and char == '"' and depth == 0:
                value = text[start:pos]
                pos += 1
                return value
            pos += 1
        raise ValueError("unterminated BibTeX value")

    skip()
    while pos < len(text):
        start = pos
        header = re.match(r"@([A-Za-z]+)\s*([({])\s*([A-Za-z0-9_-]+)\s*,", text[pos:])
        if not header or header[1].lower() in {"string", "preamble", "comment"}:
            raise ValueError(f"unsupported or malformed BibTeX near line {text[:pos].count(chr(10)) + 1}")
        kind, opening, key = header.groups()
        if key in entries:
            raise ValueError(f"duplicate bibkey: {key}")
        closing = "}" if opening == "{" else ")"
        pos += header.end()
        fields = {}
        skip()
        while pos < len(text) and text[pos] != closing:
            field = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", text[pos:])
            if not field:
                raise ValueError(f"malformed field in {key}")
            name = field[1].lower()
            if name in fields:
                raise ValueError(f"duplicate field in {key}: {name}")
            pos += field.end()
            skip()
            if pos < len(text) and text[pos] in '{"':
                value = delimited(text[pos])
            else:
                number = re.match(r"[0-9]+", text[pos:])
                if not number:
                    raise ValueError(f"literal BibTeX value required: {key}.{name}")
                value = number[0]
                pos += number.end()
            fields[name] = value
            skip()
            if pos < len(text) and text[pos] == ",":
                pos += 1
                skip()
            elif pos >= len(text) or text[pos] != closing:
                raise ValueError(f"expected comma or closing delimiter: {key}.{name}")
        if pos >= len(text):
            raise ValueError(f"unterminated BibTeX entry: {key}")
        pos += 1
        entries[key] = {"type": kind.lower(), "fields": fields, "raw": text[start:pos]}
        skip()
    return entries


def bibliography(root):
    return parse_bib((root / "library/references.bib").read_text(encoding="utf-8"))


def require_entry(root, key):
    valid_key(key)
    entries = bibliography(root)
    if key not in entries:
        raise ValueError(f"bibkey not found: {key}")
    return entries[key]


def note_status(text):
    matches = STATUS_LINE.findall(text)
    if len(matches) != 1 or matches[0].strip() not in READING_STATUSES:
        raise ValueError("精读卡必须有且只有一行有效的 '- 状态：未读/粗读/精读中/已讲通'")
    return matches[0].strip()


def reading_status(root, key, overrides=None):
    if overrides and key in overrides:
        return note_status(overrides[key])
    path = root / "library/notes" / f"{key}.md"
    return note_status(path.read_text(encoding="utf-8")) if path.is_file() else "未建卡"


@contextmanager
def writer_lock(root):
    """Serialize sr library mutations on this machine; never lock read commands."""
    lock = root / "sync/.sr.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("另一个 sr 写入操作正在运行，请稍后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_files(updates):
    """Stage all bytes before replacing files; roll back ordinary write failures.

    Each replace is atomic. A power loss across multiple replaces still requires
    doctor verification; this is not a cross-file crash-atomic transaction.
    """
    before, staged, replaced = {}, {}, []
    try:
        for path, content in updates.items():
            if path.is_symlink():
                raise ValueError(f"refusing to replace symlink: {path}")
            before[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
                staged[path] = Path(handle.name)
                handle.write(content.encode("utf-8") if isinstance(content, str) else content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staged[path], path.stat().st_mode & 0o777 if path.exists() else 0o644)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                # Restore through a staged file, too; do not truncate old data.
                write_files({path: before[path]})
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def normalized_doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).casefold()


def file_record(root, path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def inventory_data(root):
    path = root / "sync/assets.json"
    if not path.exists():
        return {"schema_version": 1, "papers": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("papers"), dict):
        raise ValueError("invalid sync/assets.json schema")
    for key, item in data["papers"].items():
        valid_key(key)
        if (not isinstance(item, dict) or item.get("path") != f"library/papers/{key}.pdf"
                or type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0
                or not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError(f"invalid inventory record: {key}")
    return data


def legacy_checksums(root):
    """Keep the older human-maintained manifest as evidence during migration."""
    path = root / "sync/manifest.md"
    records = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            source = re.search(r"`library/papers/([A-Za-z0-9_-]+)\.pdf`", line)
            checksum = re.search(r"SHA-256\s+`([0-9a-f]{64})`", line)
            if source and checksum:
                records[source[1]] = checksum[1]
    return records


def json_text(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def index_text(root, entries=None, overrides=None):
    entries = bibliography(root) if entries is None else entries
    path = root / "library/index.md"
    original = path.read_text(encoding="utf-8") if path.exists() else "# 文献分类索引\n"
    seen, lines = set(), []
    for line in original.splitlines(keepends=True):
        match = INDEX_ROW.match(line)
        if match:
            key = match[1]
            if key not in entries:
                raise ValueError(f"index references unregistered bibkey: {key}")
            # Escaped pipes may appear in titles; keep their original spelling.
            cells = re.split(r"(?<!\\)\|", line.rstrip("\n"))
            if len(cells) != 8:
                raise ValueError(f"invalid index table row: {key}")
            state = reading_status(root, key, overrides)
            cells[4] = f" {state} "
            note_link = f"[精读卡](notes/{key}.md)"
            if state != "未建卡" and note_link not in cells[6]:
                cells[6] = cells[6].rstrip() + " · " + note_link + " "
            elif state == "未建卡" and note_link in cells[6]:
                cells[6] = re.sub(r"\s*·\s*" + re.escape(note_link), "", cells[6])
                cells[6] = cells[6].replace(note_link, "")
            line = "|".join(cells) + ("\n" if line.endswith("\n") else "")
            seen.add(key)
        lines.append(line)
    result = "".join(lines)
    missing = sorted(set(entries) - seen)
    if missing:
        heading = "## 待分类"
        rows = []
        for key in missing:
            title = " ".join(entries[key]["fields"].get("title", key).split()).replace("|", r"\|")
            state = reading_status(root, key, overrides)
            assets = f"[PDF](papers/{key}.pdf)"
            if state != "未建卡":
                assets += f" · [精读卡](notes/{key}.md)"
            rows.append(f"| `{key}` | {title} | 待确认 | {state} | 待分类 | {assets} |\n")
        section = re.search(r"^## 待分类\s*\n.*?(?=^## |\Z)", result, re.M | re.S)
        if section:
            offset = section.end()
            result = result[:offset].rstrip() + "\n" + "".join(rows) + "\n" + result[offset:]
        else:
            result = result.rstrip() + (f"\n\n{heading}\n\n"
                "| bibkey | 论文 | 文献角色 | 卡片状态 | 关键词 | 资产 |\n"
                "|---|---|---|---|---|---|\n") + "".join(rows)
    if result != original:
        result = re.sub(r"^最后更新：.*$", f"最后更新：{date.today().isoformat()}", result, flags=re.M)
    return result


def paper_rows(root):
    return [{"bibkey": key, "title": entry["fields"].get("title", ""),
             "status": reading_status(root, key),
             "pdf": (root / "library/papers" / f"{key}.pdf").is_file()}
            for key, entry in sorted(bibliography(root).items())]


def project_rows(root):
    rows = []
    for path in sorted((root / "projects").glob("*")):
        if not path.is_dir() or path.name.startswith(("_", ".")):
            continue
        state = path / "state.md"
        text = state.read_text(encoding="utf-8") if state.is_file() else ""
        def field(label):
            match = re.search(rf"^- {label}：[ \t]*(.*)$", text, re.M)
            return match[1].strip() if match else ""
        section = re.search(r"^## 下一步第一件事\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        action = " ".join(line.lstrip("> ").strip() for line in section[1].splitlines() if line.strip()) if section else ""
        rows.append({"project": path.name, "stage": field("阶段"), "updated": field("最后更新"),
                     "next_action": action, "state": state.relative_to(root).as_posix()})
    return rows


def inbox_files(root):
    return sorted(path.relative_to(root).as_posix() for path in (root / "library/inbox").rglob("*")
                  if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root / "library/inbox").parts))


def diagnose(root, verify=False):
    issues = []
    def issue(level, code, path, message):
        issues.append({"level": level, "code": code, "path": str(path), "message": message})
    for name in ("PROFILE.md", "lessons.md", "library/references.bib", "library/index.md", "sync/manifest.md"):
        if not (root / name).is_file():
            issue("error", "missing_file", name, "缺少必需文件")
    for name in ("library/papers", "library/notes", "library/inbox", "projects", "projects/_template", "outputs/drafts", "outputs/releases"):
        if not (root / name).is_dir():
            issue("error", "missing_directory", name, "缺少必需目录")
    try:
        entries = bibliography(root)
    except (ValueError, OSError) as exc:
        issue("error", "bibliography", "library/references.bib", str(exc))
        entries = {}
    dois, missing_notes = {}, []
    for key, entry in entries.items():
        for name in ("title", "author", "year"):
            if not entry["fields"].get(name, "").strip():
                issue("error", "metadata", key, f"缺少 {name}")
        doi = normalized_doi(entry["fields"].get("doi", ""))
        if doi and doi in dois:
            issue("error", "duplicate_doi", key, f"与 {dois[doi]} 重复 DOI")
        dois[doi] = key
        pdf = root / "library/papers" / f"{key}.pdf"
        if not pdf.is_file():
            issue("error", "missing_pdf", pdf.relative_to(root), "已登记原文缺失")
        else:
            with pdf.open("rb") as handle:
                if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                    issue("error", "invalid_pdf", pdf.relative_to(root), "文件头不是 PDF")
        try:
            if reading_status(root, key) == "未建卡":
                missing_notes.append(key)
        except ValueError as exc:
            issue("error", "note_status", f"library/notes/{key}.md", str(exc))
    if missing_notes:
        issue("warning", "missing_notes", "library/notes", f"{len(missing_notes)} 篇未建卡：" + ", ".join(missing_notes))
    for directory, pattern in (("library/notes", "*.md"), ("library/papers", "*.pdf")):
        for path in sorted((root / directory).glob(pattern)):
            if path.stem not in entries:
                issue("error", "orphan_asset", path.relative_to(root), "资产没有对应 bibkey")
    try:
        expected = index_text(root, entries)
        actual = (root / "library/index.md").read_text(encoding="utf-8")
        if expected != actual:
            issue("warning", "index_drift", "library/index.md", "状态、条目或精读卡链接不同步；运行 sr index")
    except (ValueError, OSError) as exc:
        issue("error", "index", "library/index.md", str(exc))
    try:
        inventory = inventory_data(root)["papers"]
        legacy = legacy_checksums(root)
        for key in sorted(set(entries) - set(inventory)):
            issue("warning", "missing_checksum", key, "未记录 PDF 校验值；运行 sr inventory")
        for key, record in inventory.items():
            if key not in entries:
                issue("error", "orphan_checksum", key, "校验记录没有对应 bibkey")
            path = root / record["path"]
            if path.is_file() and (path.stat().st_size != record["size_bytes"]
                                  or (verify and file_record(root, path) != record)):
                issue("error", "checksum_mismatch", record["path"], "PDF 与已记录校验值不符；请核对原件，不能覆盖基线")
        for key, checksum in legacy.items():
            path = root / "library/papers" / f"{key}.pdf"
            if ((key in inventory and inventory[key]["sha256"] != checksum)
                    or (verify and path.is_file() and file_record(root, path)["sha256"] != checksum)):
                issue("error", "legacy_checksum_mismatch", path.relative_to(root), "与 sync/manifest.md 中历史校验值不符")
    except (ValueError, OSError) as exc:
        issue("error", "inventory", "sync/assets.json", str(exc))
    for row in project_rows(root):
        path = root / "projects" / row["project"]
        for name in ("README.md", "state.md", "journal.md"):
            if not (path / name).is_file():
                issue("error", "project_file", (path / name).relative_to(root), "项目必需文件缺失")
        if not row["next_action"] or "必须是下一次打开电脑" in row["next_action"]:
            issue("warning", "next_action", row["state"], "下一步尚未填写")
        try:
            date.fromisoformat(row["updated"])
        except ValueError:
            issue("warning", "project_date", row["state"], "最后更新日期缺失或无效")
    pending = inbox_files(root)
    if pending:
        issue("warning", "inbox", "library/inbox", f"{len(pending)} 个待登记文件：" + ", ".join(pending))
    return {"ok": not any(item["level"] == "error" for item in issues),
            "verified_hashes": verify, "papers": len(entries), "projects": len(project_rows(root)),
            "issues": issues}
