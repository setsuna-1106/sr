"""Thin sr entry point for the installed Zotero AI reader and its local records."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import re

import sr_library as library
import sr_zotero as zotero


def project_state(root, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or name == "_template":
        raise ValueError("无效项目名")
    path = root / "projects" / name / "state.md"
    if not path.is_file() or path.is_symlink():
        raise ValueError("项目 state.md 不存在或为软链接")
    return path


def context(root, args):
    entry = library.require_entry(root, args.bibkey)
    pdf = root / "library/papers" / f"{args.bibkey}.pdf"
    record = library.inventory_data(root)["papers"].get(args.bibkey)
    if not record or library.file_record(root, pdf) != record:
        raise ValueError("PDF 校验不一致；请先运行 sr doctor --verify，不能沿用旧证据")
    note = root / "library/notes" / f"{args.bibkey}.md"
    if note.is_symlink():
        raise ValueError("精读卡路径为软链接，先核对资产")
    result = {"bibkey": args.bibkey, "metadata": entry["fields"],
        "source_pdf": str(pdf.resolve()), "source_sha256": record["sha256"],
        "reading_status": library.reading_status(root, args.bibkey),
        "note_path": str(note) if note.is_file() else None,
        "note_basis": "精读卡是已有资料；不等于本次已核对的原文或本人已读通",
        "record_directory": str(root / "outputs/reading/zotero/sessions"),
        "rules_path": str(root / "projects/research-system/AI_READING_PROMPT.md")}
    if args.project:
        state = project_state(root, args.project)
        result["project"] = args.project
        result["project_state"] = state.read_text(encoding="utf-8")
    if args.page is not None:
        if args.page < 1:
            raise ValueError("PDF 实际页码从 1 开始")
        source = root / "projects/paper-ir/paper_ir.py"
        spec = importlib.util.spec_from_file_location("sr_reading_paper_ir", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        method, blocks = module.extract(pdf)
        selected = [block for block in blocks if block.get("location", {}).get("page") == args.page]
        if not selected:
            raise ValueError("该 PDF 页码不存在或未抽取出文本；请回看 Zotero 原文页面")
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("抽取期间 PDF 发生变化，未输出证据")
        result["pdf_page"] = args.page
        result["extraction_method"] = method
        result["page_blocks"] = selected
        result["evidence_boundary"] = "仅当前 PDF 页文本，用于定位；双栏可能串列，图表/公式可能不完整，未自动判定语义支持。引文须回看 Zotero 原文页面核对"
    print(library.json_text(result), end="")


def records(root, args):
    library.require_entry(root, args.bibkey)
    directory = root / "outputs/reading/zotero/sessions"
    result = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink():
                continue
            # Require a full bibkey token; a partial key must not select another paper.
            text = path.read_text(encoding="utf-8")
            pattern = r"(?<![A-Za-z0-9_-])" + re.escape(args.bibkey) + r"(?![A-Za-z0-9_-])"
            if re.search(pattern, text):
                result.append(str(path))
    if args.json:
        print(library.json_text(result), end="")
    else:
        print("\n".join(result) if result else "尚无该论文的 sr 会话草稿；Zotero 对话历史仍可继续读取")


def main(root, argv):
    parser = argparse.ArgumentParser(prog="sr read")
    sub = parser.add_subparsers(dest="command", required=True)
    opening = sub.add_parser("open", help="在 Zotero 打开已配置 AI 伴读的论文")
    opening.add_argument("bibkey")
    opening.add_argument("--page", type=int)
    opening.add_argument("--attachment")
    opening.add_argument("--print-url", action="store_true")
    context_parser = sub.add_parser("context", help="只读导出已登记论文、项目和可选页码上下文")
    context_parser.add_argument("bibkey")
    context_parser.add_argument("--project")
    context_parser.add_argument("--page", type=int)
    records_parser = sub.add_parser("records", help="查找按需保存到 sr 的会话草稿")
    records_parser.add_argument("bibkey")
    records_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "open":
        return zotero.open_paper(root, args)
    if args.command == "context":
        return context(root, args)
    return records(root, args)
