#!/usr/bin/env python3
"""Small, auditable paper-to-IR converter. Python 3.10+, no Python packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = "0.1.0"
KINDS = {"problem", "method", "assumption", "parameter", "result", "limitation", "resource"}
STATUSES = {"supported", "inferred", "unresolved"}
MAX_SOURCE_BYTES = 50 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def paragraphs(lines: list[str], page: int | None = None) -> list[dict]:
    blocks = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(lines + [""], 1):
        if line.strip():
            if not current:
                start = number
            current.append(line.strip())
        elif current:
            content = " ".join(current)
            location = {"page": page} if page is not None else {"line_start": start, "line_end": number - 1}
            blocks.append({"text": content, "location": location, "sha256": digest(content.encode("utf-8"))})
            current = []
    return blocks


def extract(source: Path) -> tuple[str, list[dict]]:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        binary = shutil.which("pdftotext")
        if binary is None:
            raise ValueError("PDF input requires pdftotext (Poppler)")
        try:
            run = subprocess.run([binary, "-enc", "UTF-8", "-layout", str(source), "-"],
                                 capture_output=True, check=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"PDF extraction failed: {exc}") from exc
        pages = run.stdout.decode("utf-8", errors="replace").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        blocks = [block for n, page in enumerate(pages, 1)
                  for block in paragraphs(page.splitlines(), page=n)]
        method = "poppler-pdftotext-layout-v1"
    elif suffix in {".md", ".txt"}:
        blocks = paragraphs(source.read_text(encoding="utf-8").splitlines())
        method = "utf8-paragraphs-v1"
    else:
        raise ValueError("Input must be .pdf, .md, or .txt")
    if not blocks:
        raise ValueError("No text blocks found; scanned PDFs need OCR before conversion")
    for n, block in enumerate(blocks, 1):
        block["id"] = f"b{n:04d}"
    return method, blocks


def write_json(path: Path, obj: dict, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise ValueError(f"Output exists: {path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(obj, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Artifact root must be a JSON object")
    return obj


def convert(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise ValueError(f"Source missing: {source}")
    raw = source.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds 50 MiB limit")
    method, blocks = extract(source)
    artifact = {
        "schema_version": "0.1.0",
        "paper": {"id": args.paper_id, "title": args.title},
        "source": {"path": os.path.relpath(source, output.parent), "sha256": digest(raw),
                   "extraction_method": method},
        "blocks": blocks,
        "items": [],
    }
    errors = validate_artifact(artifact, output, check_source=False)
    if errors:
        raise ValueError("Generated artifact is invalid: " + "; ".join(errors))
    write_json(output, artifact, replace=False)
    print(f"Created {output} with {len(blocks)} blocks")


def validate_artifact(artifact: dict, artifact_path: Path, *, check_source: bool = True) -> list[str]:
    errors = []
    if set(artifact) != {"schema_version", "paper", "source", "blocks", "items"}:
        errors.append("top-level fields do not match v0.1 schema")
    if artifact.get("schema_version") != "0.1.0":
        errors.append("unsupported schema_version")
    paper = artifact.get("paper")
    if not isinstance(paper, dict) or not all(isinstance(paper.get(k), str) and paper[k].strip()
                                               for k in ("id", "title")):
        errors.append("paper.id and paper.title are required")
    elif set(paper) != {"id", "title"}:
        errors.append("paper fields do not match v0.1 schema")
    source = artifact.get("source")
    if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and source[k]
                                               for k in ("path", "sha256", "extraction_method")):
        errors.append("source metadata is incomplete")
    elif set(source) != {"path", "sha256", "extraction_method"} or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]) or source["extraction_method"] not in {"utf8-paragraphs-v1", "poppler-pdftotext-layout-v1"}:
        errors.append("source fields do not match v0.1 schema")
    elif check_source:
        source_path = artifact_path.parent / source["path"]
        if not source_path.is_file():
            errors.append(f"source file missing: {source_path}")
        elif digest(source_path.read_bytes()) != source["sha256"]:
            errors.append("source SHA-256 mismatch; reconvert before using this artifact")
        else:
            try:
                method, expected_blocks = extract(source_path)
                if method != source["extraction_method"] or expected_blocks != artifact.get("blocks"):
                    errors.append("blocks differ from a fresh extraction of the source")
            except ValueError as exc:
                errors.append(str(exc))
    blocks = artifact.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("blocks must be a nonempty array")
        blocks = []
    by_id = {}
    for block in blocks:
        if not isinstance(block, dict):
            errors.append("block must be an object")
            continue
        key, content = block.get("id"), block.get("text")
        if not isinstance(key, str) or not isinstance(content, str) or not content:
            errors.append("block id/text missing")
            continue
        if set(block) != {"id", "text", "location", "sha256"} or not re.fullmatch(r"b\d{4,}", key):
            errors.append(f"block fields do not match v0.1 schema: {key}")
        if key in by_id:
            errors.append(f"duplicate block id: {key}")
        by_id[key] = block
        if block.get("sha256") != digest(content.encode("utf-8")):
            errors.append(f"block SHA-256 mismatch: {key}")
        loc = block.get("location")
        valid_page = isinstance(loc, dict) and set(loc) == {"page"} and type(loc["page"]) is int and loc["page"] > 0
        valid_lines = isinstance(loc, dict) and set(loc) == {"line_start", "line_end"} and type(loc["line_start"]) is int and type(loc["line_end"]) is int and 0 < loc["line_start"] <= loc["line_end"]
        if not (valid_page or valid_lines):
            errors.append(f"invalid block location: {key}")
    items = artifact.get("items")
    if not isinstance(items, list):
        errors.append("items must be an array")
        items = []
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            errors.append("item must be an object")
            continue
        key = item.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"i\d{4,}", key):
            errors.append("invalid item id")
        elif key in ids:
            errors.append(f"duplicate item id: {key}")
        ids.add(key)
        if set(item) != {"id", "kind", "status", "text", "evidence"}:
            errors.append(f"item fields do not match v0.1 schema: {key}")
        if item.get("kind") not in KINDS:
            errors.append(f"invalid kind: {key}")
        if item.get("status") not in STATUSES:
            errors.append(f"invalid status: {key}")
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            errors.append(f"item text missing: {key}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"evidence must be an array: {key}")
            continue
        if item.get("status") == "supported" and not evidence:
            errors.append(f"supported item needs evidence: {key}")
        for ref in evidence:
            if not isinstance(ref, dict) or ref.get("block_id") not in by_id:
                errors.append(f"invalid evidence block: {key}")
                continue
            if set(ref) != {"block_id", "quote"}:
                errors.append(f"evidence fields do not match v0.1 schema: {key}")
            quote = ref.get("quote")
            if not isinstance(quote, str) or not quote or quote not in by_id[ref["block_id"]]["text"]:
                errors.append(f"quote not found in block: {key}")
    return errors


def validate(args: argparse.Namespace) -> None:
    artifact_path = args.artifact.resolve()
    errors = validate_artifact(load_json(artifact_path), artifact_path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("Valid artifact: source digest, blocks, items, and evidence references pass")


def add(args: argparse.Namespace) -> None:
    path = args.artifact.resolve()
    artifact = load_json(path)
    errors = validate_artifact(artifact, path)
    if errors:
        raise ValueError("Existing artifact is invalid: " + "; ".join(errors))
    if args.status == "supported" and (not args.block or not args.quote):
        raise ValueError("supported items require --block and --quote")
    if bool(args.block) != bool(args.quote):
        raise ValueError("--block and --quote must be provided together")
    next_id = max((int(item["id"][1:]) for item in artifact["items"]), default=0) + 1
    item = {"id": f"i{next_id:04d}", "kind": args.kind, "status": args.status,
            "text": args.text, "evidence": []}
    if args.block:
        item["evidence"].append({"block_id": args.block, "quote": args.quote})
    artifact["items"].append(item)
    errors = validate_artifact(artifact, path)
    if errors:
        raise ValueError("New item is invalid: " + "; ".join(errors))
    write_json(path, artifact, replace=True)
    print(f"Added {item['id']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="paper-ir", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("convert", help="extract text blocks into a new JSON artifact")
    p.add_argument("source", type=Path)
    p.add_argument("--paper-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.set_defaults(func=convert)
    p = sub.add_parser("add", help="add one human-reviewed semantic item")
    p.add_argument("artifact", type=Path)
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--status", required=True, choices=sorted(STATUSES))
    p.add_argument("--text", required=True)
    p.add_argument("--block")
    p.add_argument("--quote")
    p.set_defaults(func=add)
    p = sub.add_parser("validate", help="check integrity and evidence references")
    p.add_argument("artifact", type=Path)
    p.set_defaults(func=validate)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"paper-ir: {exc}\n")


if __name__ == "__main__":
    main()
