import { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowRight, ArrowUpRight, Network, RefreshCw, Search, UserRound, X } from "lucide-react";
import { errorDetail, rpc, type Paper } from "./api";
import { CitationChart, type NetworkEdge, type NetworkNode } from "./CitationChart";
import "./CitationNetwork.css";

type Edge = { source: string; target: string };
type Coverage = "available" | "pending" | "no_references" | "not_indexed" | "no_doi";
interface CitationGraph {
  edges: Edge[];
  coverage: Record<string, Coverage>;
  progress: null | {
    state: "running" | "current" | "complete" | "cancelled" | "failed";
    done: number;
    total: number;
    message?: string;
  };
}
const coverageLabels: Record<Coverage, string> = {
  available: "Crossref 已提供带 DOI 的参考文献",
  pending: "待查询参考文献",
  no_references: "Crossref 缺少带 DOI 的参考文献",
  not_indexed: "Crossref 未收录此 DOI",
  no_doi: "书目缺少可查询的 DOI",
};

function authorNames(author?: string) {
  return (author || "").replace(/[{}]/g, "").split(/\s+and\s+/i).flatMap(raw => {
    const cleaned = raw.trim().replace(/\s+/g, " ").normalize("NFC");
    if (!cleaned || /^(others|et al\.?)$/i.test(cleaned)) return [];
    const parts = cleaned.split(",").map(part => part.trim());
    return [parts.length === 2 ? [parts[1], parts[0]].filter(Boolean).join(" ") : cleaned];
  });
}

function sharedAuthors(papers: Paper[]) {
  const authors = new Map<string, { id: string; name: string; keys: Set<string> }>();
  for (const paper of papers) {
    for (const name of authorNames(paper.author)) {
      const id = "author:" + name.toLowerCase();
      const entry = authors.get(id) ?? { id, name, keys: new Set() };
      entry.keys.add(paper.bibkey);
      authors.set(id, entry);
    }
  }
  return [...authors.values()].filter(author => author.keys.size > 1).sort((a, b) => a.name.localeCompare(b.name));
}

function paperNode(paper: Paper): NetworkNode {
  const name = authorNames(paper.author)[0];
  const surname = name?.split(" ").at(-1);
  const title = paper.title || paper.bibkey;
  return {
    id: paper.bibkey, kind: "paper",
    label: surname ? [surname, paper.year].filter(Boolean).join(", ") : title,
    title: [title, paper.year].filter(Boolean).join(" · "),
  };
}

export function CitationNetwork({ papers, selectedKey, onSelect, onDetails }: {
  papers: Paper[];
  selectedKey: string;
  onSelect: (key: string) => void;
  onDetails: (key: string) => void;
}) {
  const [graph, setGraph] = useState<CitationGraph | null>(null);
  const [error, setError] = useState("");
  const [actionPending, setActionPending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [query, setQuery] = useState("");
  const [showUnlinked, setShowUnlinked] = useState(true);
  const [showAuthors, setShowAuthors] = useState(true);
  const progress = graph?.progress;
  const running = progress?.state === "running";
  const signature = JSON.stringify(papers.map(({ bibkey, doi }) => [bibkey, doi]));
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await rpc<CitationGraph>("paper.citations");
        if (!active) return;
        setGraph(value);
        setError("");
        if (value.progress?.state === "running") timer = setTimeout(load, 950);
        else setStopping(false);
      } catch (cause) {
        if (active) {
          setError(errorDetail(cause).message);
          if (running) timer = setTimeout(load, 2000);
        }
      }
    }
    if (running) timer = setTimeout(load, 550);
    else void load();
    return () => { active = false; clearTimeout(timer); };
  }, [signature, running]);

  async function networkAction(cancel: boolean) {
    setActionPending(true);
    setError("");
    try {
      const value = await rpc<CitationGraph>(cancel ? "paper.citations_cancel" : "paper.citations_refresh");
      setGraph(value);
      setStopping(cancel && value.progress?.state === "running");
    } catch (cause) {
      setError(errorDetail(cause).message);
    } finally {
      setActionPending(false);
    }
  }

  const orderedPapers = useMemo(() => [...papers].sort((a, b) => a.title.localeCompare(b.title)), [papers]);
  const byKey = useMemo(() => new Map(orderedPapers.map(paper => [paper.bibkey, paper])), [orderedPapers]);
  const authors = useMemo(() => sharedAuthors(orderedPapers), [orderedPapers]);
  const selected = byKey.get(selectedKey);
  const selectedAuthor = showAuthors ? authors.find(author => author.id === selectedKey) : undefined;
  const focused = selected || selectedAuthor;
  const edges = useMemo(() => (graph?.edges ?? []).filter(edge => byKey.has(edge.source) && byKey.has(edge.target)),
    [graph?.edges, byKey]);
  const references = new Set(edges.filter(edge => edge.source === selectedKey).map(edge => edge.target));
  const citing = new Set(edges.filter(edge => edge.target === selectedKey).map(edge => edge.source));
  const coauthors = showAuthors && selected ? authors.filter(author => author.keys.has(selectedKey)) : [];
  const topology = useMemo(() => {
    const citationEdges = edges.map(edge => ({ ...edge, kind: "citation" as const }));
    const authorEdges = showAuthors
      ? authors.flatMap(author => [...author.keys].map(target => ({ source: author.id, target, kind: "authorship" as const })))
      : [];
    const allEdges: NetworkEdge[] = [...citationEdges, ...authorEdges];
    const linked = new Set(allEdges.flatMap(({ source, target }) => [source, target]));
    const focusEdges = allEdges.filter(edge => edge.source === selectedKey || edge.target === selectedKey);
    const neighborhood = new Set([selectedKey, ...focusEdges.flatMap(edge => [edge.source, edge.target])]);
    const allNodes: NetworkNode[] = [
      ...orderedPapers.map(paperNode),
      ...(showAuthors ? authors.map(author => ({ id: author.id, kind: "author" as const, title: `作者 · ${author.name}`, label: author.name })) : []),
    ];
    const nodes = allNodes.filter(node => focused ? neighborhood.has(node.id) : showUnlinked || !linked.size || linked.has(node.id));
    const visible = new Set(nodes.map(node => node.id));
    return { nodes, linked: linked.size, edges: allEdges.filter(edge => visible.has(edge.source) && visible.has(edge.target)) };
  }, [edges, authors, orderedPapers, selectedKey, showAuthors, showUnlinked, focused]);
  const terms = query.trim().toLowerCase();
  const matches = terms ? [
    ...(showAuthors ? authors.filter(author => author.name.toLowerCase().includes(terms)).map(author => ({
      id: author.id, title: author.name, detail: `作者 · ${author.keys.size} 篇入库`,
    })) : []),
    ...orderedPapers.filter(paper => `${paper.title} ${paper.author || ""} ${paper.bibkey}`.toLowerCase().includes(terms)).map(paper => ({
      id: paper.bibkey, title: paper.title || paper.bibkey, detail: [paper.year, paper.bibkey].filter(Boolean).join(" · "),
    })),
  ] : [];
  const available = Object.values(graph?.coverage || {}).filter(value => value === "available").length;

  function select(key: string) {
    setQuery("");
    onSelect(key);
  }

  return (
    <section className="citation-network" aria-label="文献关系网络">
      <div className="citation-tools">
        <div className="citation-search">
          <label className="library-search">
            <Search size={16} aria-hidden="true" />
            <input type="search" aria-label="定位图谱节点" placeholder="按标题、作者或 bibkey 定位"
              value={query} onChange={event => setQuery(event.target.value)}
              onKeyDown={event => {
                if (event.key === "Escape") setQuery("");
                if (event.key === "Enter" && matches.length) select(matches[0].id);
              }} />
          </label>
          {terms && (
            <div className="citation-matches" role="group" aria-label="定位结果">
              {matches.slice(0, 8).map(item => (
                <button key={item.id} onClick={() => select(item.id)}>
                  <strong>{item.title}</strong><small>{item.detail}</small>
                </button>
              ))}
              {!matches.length && <p className="muted">没有匹配的论文或作者</p>}
            </div>
          )}
        </div>
        {focused ? (
          <button className="button small" onClick={() => select("")}><X size={14} />返回全图</button>
        ) : (
          <label className="hidden-toggle"><input type="checkbox" checked={showUnlinked || !topology.linked}
            disabled={!topology.linked} onChange={event => setShowUnlinked(event.target.checked)} />显示未连线论文</label>
        )}
        <button className="button small citation-refresh" disabled={!papers.length || actionPending || stopping}
          onClick={() => void networkAction(running)}>
          {running ? <X size={14} /> : <RefreshCw size={14} />}{running ? (stopping ? "正在停止" : "停止更新") : "更新引文"}
        </button>
      </div>
      <div className="citation-summary">
        <label className="hidden-toggle"><input type="checkbox" checked={showAuthors} onChange={event => {
          setShowAuthors(event.target.checked);
          if (!event.target.checked && selectedAuthor) select("");
        }} />显示作者</label>
        <span><strong>{papers.length}</strong> 篇入库</span>
        <span><strong>{edges.length}</strong> 条库内引用</span>
        <span title="Crossref 提供了至少一条带 DOI 的参考文献"><strong>{available}</strong> 篇可查参考文献</span>
        {running && <span className="citation-progress"><progress aria-label="引文更新进度" max={progress.total} value={progress.done} />{progress.done} / {progress.total}</span>}
      </div>
      {(error || progress?.state === "failed") && <p className="citation-message" role="alert">{error || progress?.message}</p>}
      <div className={`citation-workspace${focused ? " has-paper" : ""}`}>
        <div className="citation-canvas">
          {topology.nodes.length ? <CitationChart key={selectedKey} nodes={topology.nodes} edges={topology.edges} selected={focused ? selectedKey : undefined} onSelect={select} />
            : <div className="citation-empty"><Network size={24} /><p>登记论文后就能在这里查看关系。</p></div>}
          <div className="citation-legend">
            <span><i className="citation-key paper" />论文</span>
            {showAuthors && <><span><i className="citation-key author" />作者</span><span><i className="citation-key authorship" />署名</span></>}
            {selected ? <><span className="outgoing"><ArrowRight size={14} />该文引用</span><span className="incoming"><ArrowRight size={14} />引用该文</span></>
              : <span><ArrowRight size={14} />引用方向</span>}
          </div>
        </div>
        {selected && (
          <aside className="citation-inspector" aria-label="所选文献的引文">
            <div className="citation-paper-meta"><span>{selected.year || "年份未知"}</span><span>{selected.status}</span></div>
            <h3>{selected.title || selected.bibkey}</h3>
            <button className="button small" onClick={() => onDetails(selected.bibkey)}>文献详情<ArrowUpRight size={13} /></button>
            <p className="muted">{coverageLabels[graph?.coverage[selected.bibkey] || "pending"]}</p>
            {coauthors.length > 0 && <div className="citation-related">
              <h4><UserRound size={14} aria-hidden="true" />共同署名作者<span>{coauthors.length}</span></h4>
              <div className="citation-author-buttons">
                {coauthors.map(author => <button key={author.id} onClick={() => select(author.id)}>{author.name}</button>)}
              </div>
            </div>}
            {[{ label: "引用的库内论文", keys: references, Icon: ArrowUpRight },
              { label: "引用它的库内论文", keys: citing, Icon: ArrowDownLeft }].map(({ label, keys, Icon }) => (
              <div key={label} className="citation-related">
                <h4><Icon size={14} aria-hidden="true" />{label}<span>{keys.size}</span></h4>
                {keys.size ? orderedPapers.filter(paper => keys.has(paper.bibkey)).map(paper => (
                  <button key={paper.bibkey} onClick={() => select(paper.bibkey)} title={paper.title}>
                    <span>{paper.title || paper.bibkey}</span><small>{paper.year}</small>
                  </button>
                )) : <p className="muted">暂无已核对的连线</p>}
              </div>
            ))}
          </aside>
        )}
        {selectedAuthor && (
          <aside className="citation-inspector" aria-label="作者署名文献">
            <div className="citation-paper-meta"><UserRound size={13} />作者</div>
            <h3>{selectedAuthor.name}</h3>
            <div className="citation-related">
              <h4>库内署名文献<span>{selectedAuthor.keys.size}</span></h4>
              {orderedPapers.filter(paper => selectedAuthor.keys.has(paper.bibkey)).map(paper => (
                <button key={paper.bibkey} onClick={() => select(paper.bibkey)} title={paper.title}>
                  <span>{paper.title || paper.bibkey}</span><small>{paper.year}</small>
                </button>
              ))}
            </div>
          </aside>
        )}
      </div>
      <p className="citation-source muted">
        {!graph && !error ? "正在读取本地引文缓存。" : progress?.state === "current" ? "引文缓存仍在有效期内。" :
          progress?.state === "cancelled" ? "更新已停止，已获取的引文仍可查看。" : !edges.length && !running && !error && progress?.state !== "failed"
            ? "点击「更新引文」查询库内论文之间的引用。" : "数据来自 Crossref，覆盖范围取决于出版方提供的参考文献。"}
        <span>{showAuthors && " 作者按书目全名匹配，同名可能是不同人。"} 更新时仅查询 DOI，结果保存在本机。</span>
      </p>
    </section>
  );
}
