"""Local Zotero bridge. Reads use the official API; never writes Zotero SQLite."""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import sr_library as library

BASE = "http://127.0.0.1:23119/api/users/0/"
MAPPING = "sync/zotero.json"
NON_PAPERS = {"attachment", "note", "annotation"}


def item_key(value):
    if not re.fullmatch(r"[A-Z0-9]{8}", value):
        raise ValueError("Zotero item key must contain eight uppercase letters/digits")
    return value


class Client:
    def __init__(self):
        # Loopback only: no environment HTTP proxy and no configurable remote host.
        self.opener = build_opener(ProxyHandler({}))

    def read(self, path, text=False):
        request = Request(BASE + path, headers={"Zotero-API-Version": "3"})
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
            return raw if text else json.loads(raw)
        except HTTPError as exc:
            if exc.code == 403:
                raise ValueError("Zotero 本地接口未启用：设置 → 高级 → 允许本机其他应用与 Zotero 通信") from exc
            raise ValueError(f"Zotero API HTTP {exc.code}: {path}") from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError("无法连接本机 Zotero；请打开 Zotero 并检查本地 API 设置") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Zotero 返回的数据无法解析") from exc

    def items(self):
        result = self.read("items?format=json")
        if not isinstance(result, list):
            raise ValueError("Zotero items response must be a list")
        for item in result:
            if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
                raise ValueError("invalid Zotero item")
            item_key(item.get("key", ""))
        return result

    def pdf_path(self, key):
        url = self.read(f"items/{item_key(key)}/file/view/url", text=True).strip()
        parsed = urlsplit(url)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ValueError("Zotero 附件必须是本机文件；不自动下载远程 PDF")
        path = Path(unquote(parsed.path))
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("Zotero PDF 尚未在本机可用")
        with path.open("rb") as handle:
            if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                raise ValueError("Zotero attachment header is not PDF")
        return path


def papers(items):
    return [item for item in items if item["data"].get("itemType") not in NON_PAPERS
            and not item["data"].get("deleted")]


def pdfs(items, parent):
    return [item for item in items if item["data"].get("parentItem") == parent
            and item["data"].get("contentType") == "application/pdf"
            and not item["data"].get("deleted")]


def choose_pdf(items, parent, attachment=None):
    candidates = pdfs(items, parent)
    if attachment:
        candidates = [item for item in candidates if item["key"] == item_key(attachment)]
    if len(candidates) != 1:
        keys = ", ".join(item["key"] for item in candidates) or "无"
        raise ValueError(f"需要唯一 PDF 附件（候选：{keys}）；多个附件时用 --attachment KEY 指定")
    return candidates[0]


def match(root, items):
    by_doi = {}
    for item in papers(items):
        doi = library.normalized_doi(item["data"].get("DOI", ""))
        if doi:
            by_doi.setdefault(doi, []).append(item)
    linked, missing, conflicts = {}, [], []
    for key, entry in library.bibliography(root).items():
        doi = library.normalized_doi(entry["fields"].get("doi", ""))
        candidates = by_doi.get(doi, []) if doi else []
        if len(candidates) > 1:
            conflicts.append({"bibkey": key, "items": [item["key"] for item in candidates]})
        elif not candidates:
            missing.append(key)
        else:
            item = candidates[0]
            attachments = pdfs(items, item["key"])
            linked[key] = {"doi": doi, "item_key": item["key"],
                           "pdf_keys": [attachment["key"] for attachment in attachments]}
    return {"schema_version": 1, "library": "local-user", "papers": linked}, missing, conflicts


def rows(root, items):
    entries = library.bibliography(root)
    registered = {library.normalized_doi(entry["fields"].get("doi", "")): key
                  for key, entry in entries.items() if entry["fields"].get("doi")}
    return [{"key": item["key"], "title": item["data"].get("title", ""),
             "doi": item["data"].get("DOI", ""),
             "bibkey": registered.get(library.normalized_doi(item["data"].get("DOI", ""))),
             "pdf_keys": [pdf["key"] for pdf in pdfs(items, item["key"])]}
            for item in papers(items)]


def metadata(item):
    data = item["data"]
    creators = [creator for creator in data.get("creators", []) if creator.get("creatorType") == "author"]
    author = " and ".join(creator.get("name") or ", ".join(
        value for value in (creator.get("lastName"), creator.get("firstName")) if value)
        for creator in creators)
    year = re.search(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)", data.get("date", ""))
    result = {"title": data.get("title", ""), "author": author,
              "journal": data.get("publicationTitle") or data.get("proceedingsTitle") or data.get("repository", ""),
              "year": year[1] if year else "", "doi": library.normalized_doi(data.get("DOI", ""))}
    for field in ("volume", "pages", "url"):
        if data.get(field):
            result[field] = data[field]
    if data.get("issue"):
        result["number"] = data["issue"]
    missing = [field for field in ("title", "author", "journal", "year", "doi") if not result[field].strip()]
    if missing:
        raise ValueError("Zotero 元数据缺少 " + ", ".join(missing) + "；先在 Zotero 核对补齐，再登记")
    return result


def export(root, args):
    entries = library.bibliography(root)
    keys = list(entries) if not args.bibkey else args.bibkey
    if len(set(keys)) != len(keys):
        raise ValueError("重复指定 bibkey")
    for key in keys:
        library.require_entry(root, key)
    if args.missing_only:
        _, missing, conflicts = match(root, Client().items())
        if conflicts:
            raise ValueError("Zotero 中存在重复 DOI，请先核对后再导出")
        keys = [key for key in keys if key in missing]
    if not keys:
        print("没有待导出的文献；未生成导入文件")
        return
    output = Path(args.output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("导出文件已存在，请换一个路径；不覆盖已有文件")
    chunks = ["% Generated by sr zotero export. Central bibliography is unchanged.\n"]
    for key in keys:
        path = root / "library/papers" / f"{key}.pdf"
        inventory = library.inventory_data(root)["papers"].get(key)
        if inventory is None or library.file_record(root, path) != inventory:
            raise ValueError(f"{key}: PDF 校验不一致或缺基线，请先运行 sr doctor --verify")
        # Add a standard BibTeX file field to the generated copy only.
        entry = entries[key]
        fields = dict(entry["fields"])
        fields["file"] = str(path.resolve())
        rendered = "@" + entry["type"] + "{" + key + ",\n" + "".join(
            f"  {name} = {{{value}}},\n" for name, value in fields.items()) + "}\n"
        if library.parse_bib(rendered)[key]["fields"] != fields:
            raise ValueError(f"无法安全导出路径/元数据：{key}")
        chunks.append(rendered)
    library.write_files({output: "\n".join(chunks)})
    print(f"已导出 {len(keys)} 篇到 {output}；Zotero 文件 → 导入，选择链接原始附件")


def import_item(root, args):
    import sr
    items = Client().items()
    selected = [item for item in papers(items) if item["key"] == item_key(args.item)]
    if len(selected) != 1:
        raise ValueError("找不到所选 Zotero 文献条目（请选择父条目，不是附件）")
    key = library.valid_key(args.as_key)
    fields = metadata(selected[0])
    existing = library.bibliography(root)
    duplicate = [entry_key for entry_key, entry in existing.items()
                 if library.normalized_doi(entry["fields"].get("doi", "")) == fields["doi"]]
    if duplicate:
        if duplicate != [key]:
            raise ValueError(f"DOI 已登记为 {', '.join(duplicate)}；沿用现有 bibkey")
        if args.apply:
            save_mapping(root, items)
        print(f"已登记：{key}；保留 PDF、精读卡与阅读状态")
        return
    if key in existing:
        raise ValueError(f"bibkey 已被其他 DOI 使用：{key}")
    attachment = choose_pdf(items, args.item, args.attachment)
    source = Client().pdf_path(attachment["key"])
    print(library.json_text({"action": "import" if args.apply else "preview", "bibkey": key,
        "metadata": fields, "source_pdf": str(source), "attachment": attachment["key"],
        "source_preserved": True, "reading_status": "未读"}), end="")
    if not args.apply:
        print("确认元数据后加 --apply 登记；本次未修改 sr 资产")
        return
    # Reuse the existing add-file / note registration path. Snapshot only files
    # this operation can change, under the same sr writer lock.
    paths = [root / "library/references.bib", root / "library/index.md",
             root / "sync/assets.json", root / MAPPING,
             root / "library/papers" / f"{key}.pdf", root / "library/notes" / f"{key}.md"]
    if any(path.is_symlink() for path in paths):
        raise ValueError("登记目标含软链接；先检查资产路径，不替换或清理链接")
    if paths[-1].exists() or paths[-2].exists():
        raise ValueError("目标 PDF/精读卡已存在；请先检查未登记资产")
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        with tempfile.TemporaryDirectory(prefix="sr-zotero-") as temporary:
            copy = Path(temporary) / "paper.pdf"
            shutil.copyfile(source, copy)
            registration = argparse.Namespace(path=str(copy), as_key=key, article_number=None,
                volume=None, number=None, pages=None, url=None, **{name: fields[name] for name in
                ("title", "author", "journal", "year", "doi")})
            for name in ("volume", "number", "pages", "url"):
                setattr(registration, name, fields.get(name))
            with redirect_stdout(io.StringIO()):
                sr.add_file(registration)
                sr.note(argparse.Namespace(bibkey=key))
            save_mapping(root, items)
    except BaseException:
        library.write_files({path: raw for path, raw in before.items() if raw is not None})
        for path, raw in before.items():
            if raw is None:
                path.unlink(missing_ok=True)
        raise
    print(f"已登记 {key} 并创建未读精读卡；Zotero 原文件保留")


def save_mapping(root, items):
    mapping, missing, conflicts = match(root, items)
    if conflicts:
        raise ValueError("重复 DOI 导致对应关系不明确：" + library.json_text(conflicts))
    library.write_files({root / MAPPING: library.json_text(mapping)})
    return mapping, missing


def open_paper(root, args):
    entry = library.require_entry(root, args.bibkey)
    items = Client().items()
    doi = library.normalized_doi(entry["fields"].get("doi", ""))
    candidates = [item for item in papers(items)
                  if doi and library.normalized_doi(item["data"].get("DOI", "")) == doi]
    if len(candidates) != 1:
        raise ValueError("Zotero 无唯一对应条目；先 export 导入，再运行 sr zotero sync")
    pdf = choose_pdf(items, candidates[0]["key"], args.attachment)
    Client().pdf_path(pdf["key"])
    url = f"zotero://open-pdf/library/items/{pdf['key']}"
    if args.page is not None:
        if args.page < 1:
            raise ValueError("PDF 页码必须从 1 开始")
        url += f"?page={args.page}"
    print(url)
    if not args.print_url:
        if sys.platform != "darwin":
            raise ValueError("当前自动打开只支持 macOS；使用 --print-url 获取阅读链接")
        result = subprocess.run(["open", url], check=False)
        if result.returncode:
            raise ValueError("无法打开 Zotero 阅读器")


def main(root, argv):
    parser = argparse.ArgumentParser(prog="sr zotero")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="检查本机 Zotero 与 sr 对应关系")
    status.add_argument("--json", action="store_true")
    listing = sub.add_parser("list", help="查找 Zotero 文献条目与 PDF 附件 key")
    listing.add_argument("query", nargs="?", default="")
    listing.add_argument("--json", action="store_true")
    sub.add_parser("sync", help="按唯一 DOI 更新本机 key 映射；不覆盖精读卡")
    exporting = sub.add_parser("export", help="生成供 Zotero 导入的含 PDF 路径的 BibTeX")
    exporting.add_argument("--output", required=True)
    exporting.add_argument("--bibkey", action="append")
    exporting.add_argument("--missing-only", action="store_true")
    importing = sub.add_parser("import", help="预览或登记一个 Zotero 条目，保留原文件")
    importing.add_argument("item")
    importing.add_argument("--as", dest="as_key", required=True)
    importing.add_argument("--attachment")
    importing.add_argument("--apply", action="store_true")
    opening = sub.add_parser("open", help="在 Zotero 阅读已登记 sr 论文")
    opening.add_argument("bibkey")
    opening.add_argument("--page", type=int)
    opening.add_argument("--attachment")
    opening.add_argument("--print-url", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "export":
        with library.writer_lock(root):
            return export(root, args)
    if args.command == "import":
        if args.apply:
            with library.writer_lock(root):
                return import_item(root, args)
        return import_item(root, args)
    if args.command == "open":
        return open_paper(root, args)
    if args.command == "sync":
        with library.writer_lock(root):
            mapping, missing = save_mapping(root, Client().items())
        print(f"已对应 {len(mapping['papers'])} 篇；{len(missing)} 篇尚未进入 Zotero；精读卡未改写")
        return
    items = Client().items()
    if args.command == "list":
        result = [row for row in rows(root, items) if args.query.casefold() in
                  (row["title"] + " " + row["doi"] + " " + row["key"]).casefold()]
        if args.json:
            print(library.json_text(result), end="")
        else:
            for row in result:
                print(f"{row['key']} | {row['title']} | PDF {','.join(row['pdf_keys']) or '无'} | sr {row['bibkey'] or '未登记'}")
            print(f"共 {len(result)} 条")
        return
    mapping, missing, conflicts = match(root, items)
    report = {"connected": True, "zotero_papers": len(papers(items)),
              "sr_papers": len(mapping["papers"]) + len(missing) + len(conflicts),
              "matched": len(mapping["papers"]), "missing": missing, "conflicts": conflicts}
    if args.json:
        print(library.json_text(report), end="")
    else:
        print(f"Zotero 本地接口已连接 · Zotero {report['zotero_papers']} 条 · sr {report['sr_papers']} 篇 · 对应 {report['matched']} 篇 · 冲突 {len(conflicts)}")
    return int(bool(conflicts))
