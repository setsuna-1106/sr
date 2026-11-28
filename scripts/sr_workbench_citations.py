"""Direct in-library citations from Crossref reference deposits."""

from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import ssl
import sys
from threading import Event, Thread
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import sr_library as library
from sr_workbench_store import WorkbenchError, json_bytes


CACHE_DIR = Path("workbench/citations")
CACHE_AGE = timedelta(days=30)
MAX_RESPONSE = 4 * 1024 * 1024


def paper_doi(value):
    doi = library.normalized_doi(value) if isinstance(value, str) else ""
    return doi if len(doi) <= 512 and re.fullmatch(r"10\.[0-9]{4,}/[^\s\x00-\x1f\x7f]+", doi) else ""


def fetch_references(doi):
    request = Request("https://api.crossref.org/works/" + quote(doi, safe=""),
                      headers={"Accept": "application/json", "User-Agent": "sr-workbench (citation graph)"})
    try:
        context = ssl.create_default_context()
        if sys.platform == "darwin" and not ssl.get_default_verify_paths().cafile and Path("/etc/ssl/cert.pem").is_file():
            context.load_verify_locations("/etc/ssl/cert.pem")
        with urlopen(request, timeout=8, context=context) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            return None
        if exc.code == 429:
            raise ValueError("Crossref 请求过于频繁。请稍后更新，已获取的引文仍可查看。") from exc
        raise ValueError(f"Crossref 暂时不可用（HTTP {exc.code}）。请稍后更新。") from exc
    except (URLError, OSError, HTTPException) as exc:
        raise ValueError("暂时无法连接 Crossref。请检查网络后更新。") from exc
    try:
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Crossref response exceeds size limit")
        work = json.loads(raw)["message"]
        if paper_doi(work["DOI"]) != doi:
            raise ValueError("Crossref response DOI does not match the request")
        return sorted({normalized for ref in work.get("reference", [])
                       if (normalized := paper_doi(ref.get("DOI")))})
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Crossref 返回的引文数据无法核对。请稍后更新。") from exc


def library_dois(root):
    if not (root / "library/references.bib").is_file():
        return {}
    return {key: paper_doi(entry["fields"].get("doi"))
            for key, entry in library.bibliography(root).items()}


def cache_file(root, doi):
    path = root / CACHE_DIR / (hashlib.sha256(doi.encode()).hexdigest() + ".json")
    if any(part.is_symlink() for part in (root / "workbench", path.parent, path)):
        raise WorkbenchError("forbidden", "引文缓存不能位于软链接")
    return path


def cached_record(root, doi):
    path = cache_file(root, doi)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(record["checked_at"])
        refs = record["references"]
        if record["doi"] == doi and checked_at.tzinfo is not None and (
            refs is None or isinstance(refs, list) and all(paper_doi(ref) == ref and ref for ref in refs)
        ):
            return record
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return None


class CitationNetwork:
    def __init__(self):
        self.root = None
        self.thread = None
        self.stop = Event()
        self.progress = None

    def view(self, root):
        progress = self.progress if self.root == root else None
        paper_dois = library_dois(root)
        keys_by_doi = {}
        for key, doi in paper_dois.items():
            if doi:
                keys_by_doi.setdefault(doi, []).append(key)
        records = {doi: cached_record(root, doi) for doi in keys_by_doi}
        edges, coverage = [], {}
        for key, doi in paper_dois.items():
            record = records.get(doi)
            if not record:
                coverage[key] = "pending" if doi else "no_doi"
                continue
            refs = record["references"]
            coverage[key] = "available" if refs else "no_references" if refs == [] else "not_indexed"
            for target in sorted({target for ref in refs or [] if ref != doi for target in keys_by_doi.get(ref, [])}):
                edges.append({"source": key, "target": target})
        return {"edges": edges, "coverage": coverage, "progress": progress}

    def start(self, root):
        if self.thread and self.thread.is_alive():
            if self.root != root:
                raise WorkbenchError("busy", "上一工作区的引文更新正在结束，请稍后重试。")
            return self.view(root)
        threshold = datetime.now(timezone.utc) - CACHE_AGE
        pending = []
        for doi in dict.fromkeys(filter(None, library_dois(root).values())):
            record = cached_record(root, doi)
            if record is None or datetime.fromisoformat(record["checked_at"]) < threshold:
                pending.append(doi)
        self.root = root
        self.stop = Event()
        self.progress = {"state": "running" if pending else "current", "done": 0, "total": len(pending)}
        if pending:
            self.thread = Thread(target=self._refresh, args=(root, pending), daemon=True)
            self.thread.start()
        return self.view(root)

    def cancel(self, root):
        if self.root == root:
            self.stop.set()

    def _refresh(self, root, pending):
        done = 0
        state, message = "complete", ""
        try:
            for doi in pending:
                if self.stop.is_set():
                    state = "cancelled"
                    break
                refs = fetch_references(doi)
                path = cache_file(root, doi)
                record = {"doi": doi, "checked_at": datetime.now(timezone.utc).isoformat(), "references": refs}
                library.write_files({path: json_bytes(record)})
                done += 1
                self.progress = {"state": "running", "done": done, "total": len(pending)}
                # Public Crossref permits one in-flight DOI lookup and five per second.
                self.stop.wait(0.25)
        except ValueError as exc:
            state, message = "failed", str(exc)
        except OSError:
            state, message = "failed", "无法保存本地引文缓存，请检查工作区的写入权限后重试。"
        self.progress = {"state": state, "done": done, "total": len(pending), "message": message}
