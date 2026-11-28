import { useEffect, useMemo, useRef, useState } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import { Maximize, Minus, Plus } from "lucide-react";

export type NetworkNode = { id: string; label: string; title: string; kind: "paper" | "author" };
export type NetworkEdge = { source: string; target: string; kind: "citation" | "authorship" };
type LayoutPoint = NetworkNode & { x: number; y: number; degree: number };

function layout(nodes: NetworkNode[], edges: NetworkEdge[]) {
  const links = edges.map(edge => ({ ...edge }));
  const degrees = new Map(nodes.map(node => [node.id, 0]));
  for (const { source, target } of links) {
    degrees.set(source, (degrees.get(source) ?? 0) + 1);
    degrees.set(target, (degrees.get(target) ?? 0) + 1);
  }
  // D3's phyllotaxis initialization gives repeatable positions for the same input.
  const points = nodes.map(node => ({ ...node, x: NaN, y: NaN, degree: degrees.get(node.id) ?? 0 }));
  const simulation = forceSimulation<LayoutPoint>(points)
    .force("links", forceLink<LayoutPoint, NetworkEdge>(links).id(node => node.id).distance(link => link.kind === "citation" ? 94 : 72))
    .force("repulsion", forceManyBody().strength(-105))
    .force("collision", forceCollide<LayoutPoint>(point => point.kind === "paper" ? 31 : 23).iterations(2))
    .force("center", forceCenter())
    .force("x", forceX().strength(0.019))
    .force("y", forceY().strength(0.045))
    .stop();
  const positioned = simulation.tick(210).nodes();
  const xs = positioned.map(point => point.x);
  const ys = positioned.map(point => point.y);
  const bounds = { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
  return { positioned, bounds };
}

const origin = { x: 0, y: 0 };
const defaultView = { zoom: 1, offset: origin };
const minZoom = 0.6;
const maxZoom = 3.4;
type GestureEvent = Event & { clientX: number; clientY: number; scale: number };
type PlotPoint = LayoutPoint & { radius: number };

function zoomAt(view: typeof defaultView, factor: number, anchor = origin) {
  const zoom = Math.min(maxZoom, Math.max(minZoom, view.zoom * factor));
  if (zoom === view.zoom) return view;
  const ratio = zoom / view.zoom;
  return { zoom, offset: {
    x: anchor.x + (view.offset.x - anchor.x) * ratio,
    y: anchor.y + (view.offset.y - anchor.y) * ratio,
  } };
}

function placeLabels(points: PlotPoint[], width: number, height: number, selected?: string) {
  const labels = new Map<string, number>();
  const occupied = points.map(point => ({ x: point.x, y: point.y, halfWidth: point.radius + 1, halfHeight: point.radius + 1 }));
  const priority = (node: PlotPoint) => (node.id === selected ? 1000 : 0) + node.degree * 2 + (node.kind === "paper" ? 1 : 0);
  for (const node of [...points].sort((a, b) => priority(b) - priority(a))) {
    const halfWidth = Math.min(71, [...node.label].reduce((sum, char) => sum + (char.charCodeAt(0) < 128 ? 5.6 : 10.8), 6) / 2);
    const halfHeight = 7;
    for (const offset of [node.radius + 10, -node.radius - 10]) {
      const y = node.y + offset;
      if (y - halfHeight < 5 || y + halfHeight > height - 5 || node.x - halfWidth < 5 || node.x + halfWidth > width - 5) continue;
      if (occupied.some(rect => Math.abs(rect.x - node.x) < rect.halfWidth + halfWidth && Math.abs(rect.y - y) < rect.halfHeight + halfHeight)) continue;
      labels.set(node.id, offset);
      occupied.push({ x: node.x, y, halfWidth, halfHeight });
      break;
    }
  }
  return labels;
}

export function CitationChart({ nodes, edges, selected, onSelect }: {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  selected: string | undefined;
  onSelect: (id: string) => void;
}) {
  const chart = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const [size, setSize] = useState({ width: 640, height: 330 });
  const [{ zoom, offset }, setView] = useState(defaultView);
  const [panning, setPanning] = useState(false);
  useEffect(() => {
    const canvas = chart.current;
    if (!canvas) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(canvas);
    const listeners = new AbortController();
    const options = { passive: false, signal: listeners.signal };
    let pinch: { scale: number; anchor: typeof origin } | undefined;

    const anchorFor = (event: { clientX: number; clientY: number }) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 };
    };

    // WKWebView reports a scale relative to the beginning of the gesture.
    canvas.addEventListener("gesturestart", (event: Event) => {
      event.preventDefault();
      pinch = { scale: 1, anchor: anchorFor(event as GestureEvent) };
    }, options);
    canvas.addEventListener("gesturechange", (event: Event) => {
      if (!pinch) return;
      event.preventDefault();
      const { scale } = event as GestureEvent;
      if (!Number.isFinite(scale) || scale <= 0) return;
      const { anchor } = pinch;
      const factor = scale / pinch.scale;
      pinch.scale = scale;
      setView(view => zoomAt(view, factor, anchor));
    }, options);
    canvas.addEventListener("gestureend", (event: Event) => {
      if (!pinch) return;
      event.preventDefault();
      pinch = undefined;
    }, options);

    // Chromium-style webviews express pinch as a control-modified wheel event.
    canvas.addEventListener("wheel", event => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      if (pinch || !event.deltaY) return;
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 :
        event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? canvas.clientHeight : 1;
      const anchor = anchorFor(event);
      const factor = Math.exp(-event.deltaY * unit / 200);
      setView(view => zoomAt(view, factor, anchor));
    }, options);
    return () => { observer.disconnect(); listeners.abort(); };
  }, []);

  const { positioned, bounds } = useMemo(() => layout(nodes, edges), [nodes, edges]);
  const fitY = Math.max(0.05, Math.min(1.4, (size.height - 64) / Math.max(1, bounds.bottom - bounds.top)));
  const fitX = Math.max(0.05, Math.min(1.75, fitY * 1.65, (size.width - 124) / Math.max(1, bounds.right - bounds.left)));
  const points = new Map(positioned.map(node => [node.id, {
    ...node,
    x: size.width / 2 + (node.x - (bounds.left + bounds.right) / 2) * fitX * zoom + offset.x,
    y: size.height / 2 + (node.y - (bounds.top + bounds.bottom) / 2) * fitY * zoom + offset.y,
    radius: node.kind === "author" ? 4.4 : Math.min(9.5, 5 + node.degree * 0.42),
  }]));
  const labels = placeLabels([...points.values()], size.width, size.height, selected);

  const zoomBy = (step: number) => setView(view => zoomAt(view, step));
  function endPan() {
    drag.current = null;
    setPanning(false);
  }

  return (
    <div ref={chart} className={`citation-viewport${panning ? " is-panning" : ""}`} role="region"
      aria-label="文献与作者关系图"
      onPointerDown={event => {
        if (event.button !== 0 || (event.target as Element).closest("button")) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
        setPanning(true);
      }}
      onPointerMove={event => {
        const previous = drag.current;
        if (previous?.id !== event.pointerId) return;
        const dx = event.clientX - previous.x;
        const dy = event.clientY - previous.y;
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
        setView(view => ({ ...view, offset: { x: view.offset.x + dx, y: view.offset.y + dy } }));
      }}
      onLostPointerCapture={endPan}>
      <svg width={size.width} height={size.height} aria-hidden="true" className="citation-lines">
        <defs>
          {["normal", "outgoing", "incoming"].map(kind => (
            <marker key={kind} id={`citation-${kind}`} viewBox="0 0 8 8" refX="7" refY="4"
              markerWidth="6.5" markerHeight="6.5" orient="auto" markerUnits="userSpaceOnUse">
              <path className={`citation-arrow ${kind}`} d="M0 0 L8 4 L0 8 L2 4 Z" />
            </marker>
          ))}
        </defs>
        {edges.map(({ source, target, kind }) => {
          const from = points.get(source)!;
          const to = points.get(target)!;
          const distance = Math.hypot(to.x - from.x, to.y - from.y) || 1;
          const dx = (to.x - from.x) / distance;
          const dy = (to.y - from.y) / distance;
          const active = source === selected ? "outgoing" : target === selected ? "incoming" : "normal";
          const gap = kind === "citation" ? to.radius + 3 : 0;
          return <line key={JSON.stringify([kind, source, target])} className={`citation-edge ${kind} ${active}`}
            x1={from.x} y1={from.y} x2={to.x - dx * gap} y2={to.y - dy * gap}
            markerEnd={kind === "citation" ? `url(#citation-${active})` : undefined} />;
        })}
      </svg>
      {[...points.values()].map(node => {
        const labelOffset = labels.get(node.id);
        return (
          <button key={node.id} type="button" className={`citation-node ${node.kind}`} aria-pressed={selected === node.id}
            title={node.title} aria-label={node.title} style={{ left: node.x, top: node.y }} onClick={() => onSelect(node.id)}>
            <span className="citation-dot" style={{ width: node.radius * 2, height: node.radius * 2 }} />
            <span className={`citation-node-label${labelOffset !== undefined ? " visible" : ""}`} aria-hidden="true"
              style={labelOffset !== undefined ? { top: `calc(50% + ${labelOffset - 7}px)` } : undefined}>{node.label}</span>
          </button>
        );
      })}
      <div className="citation-zoom" role="group" aria-label="图谱缩放">
        <button className="icon-button" aria-label="缩小图谱" title="缩小图谱" disabled={zoom <= minZoom} onClick={() => zoomBy(0.8)}><Minus size={15} /></button>
        <button className="icon-button" aria-label="适应窗口" title="适应窗口" onClick={() => setView(defaultView)}><Maximize size={13} /></button>
        <button className="icon-button" aria-label="放大图谱" title="放大图谱" disabled={zoom >= maxZoom} onClick={() => zoomBy(1.25)}><Plus size={15} /></button>
      </div>
    </div>
  );
}
