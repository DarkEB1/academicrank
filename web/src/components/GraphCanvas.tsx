import { useEffect, useRef, useState } from 'react';
import Graph from 'graphology';
import { circular } from 'graphology-layout';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Sigma from 'sigma';
import type { SubgraphEdge, SubgraphNode } from '@/lib/types';
import {
  KIND_SIZE,
  normaliseTrust,
  relationColor,
  trustColor,
  trustExtent,
} from '@/lib/graphColors';

export type GraphCanvasProps = {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  focusId?: string;
  dark: boolean;
  onSelect: (node: SubgraphNode) => void;
  className?: string;
};

/**
 * sigma.js over a graphology graph, WebGL. Layout is ForceAtlas2 run in short
 * synchronous bursts between animation frames: a worker would be tidier, but
 * bursts keep the main thread responsive without a second bundle entry point,
 * and let the layout be watched as it settles.
 */
export function GraphCanvas({
  nodes,
  edges,
  focusId,
  dark,
  onSelect,
  className,
}: GraphCanvasProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Sigma | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  const [settling, setSettling] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const graph = new Graph({ multi: true, type: 'directed' });
    const extent = trustExtent(nodes);
    const present = new Set<string>();

    for (const node of nodes) {
      if (graph.hasNode(node.id)) continue;
      present.add(node.id);
      graph.addNode(node.id, {
        label: node.label,
        size:
          KIND_SIZE[node.kind] +
          (node.id === focusId ? 4 : normaliseTrust(node.trust, extent) * 3),
        color:
          node.id === focusId
            ? dark
              ? '#fb923c'
              : '#c2410c'
            : trustColor(normaliseTrust(node.trust, extent), dark),
        kind: node.kind,
        trust: node.trust,
        year: node.year,
      });
    }

    for (const edge of edges) {
      // The API may reference a neighbour it did not include in `nodes`.
      if (!present.has(edge.source) || !present.has(edge.target)) continue;
      graph.addEdge(edge.source, edge.target, {
        size: Math.max(0.4, Math.min(2.5, edge.weight * 2)),
        color: relationColor(edge.relation, dark),
        relation: edge.relation,
        weight: edge.weight,
      });
    }

    if (graph.order === 0) {
      return () => {
        /* nothing rendered */
      };
    }

    circular.assign(graph, { scale: 100 });

    const renderer = new Sigma(graph, container, {
      renderLabels: true,
      // Above a few thousand nodes, labels are the bottleneck and the noise.
      labelRenderedSizeThreshold: graph.order > 2500 ? 12 : 6,
      labelDensity: 0.25,
      labelGridCellSize: 120,
      labelColor: { color: dark ? '#e8e3d9' : '#26231f' },
      labelFont: 'ui-sans-serif, system-ui, sans-serif',
      labelSize: 11,
      defaultEdgeColor: dark ? '#33373d' : '#d8d3ca',
      enableEdgeEvents: false,
      hideEdgesOnMove: graph.order > 2000,
      hideLabelsOnMove: true,
      allowInvalidContainer: true,
      minCameraRatio: 0.05,
      maxCameraRatio: 12,
    });

    renderer.on('clickNode', ({ node }) => {
      const attrs = graph.getNodeAttributes(node);
      selectRef.current({
        id: node,
        label: String(attrs.label ?? node),
        kind: attrs.kind as SubgraphNode['kind'],
        trust: Number(attrs.trust ?? 0),
        year: (attrs.year as number | null) ?? null,
      });
    });

    renderer.on('enterNode', () => {
      container.style.cursor = 'pointer';
    });
    renderer.on('leaveNode', () => {
      container.style.cursor = 'grab';
    });

    rendererRef.current = renderer;

    // ---- chunked ForceAtlas2 -------------------------------------------
    const settings = forceAtlas2.inferSettings(graph);
    // Big graphs need Barnes-Hut or the layout is O(n²) per iteration.
    const fa2Settings = {
      ...settings,
      barnesHutOptimize: graph.order > 800,
      barnesHutTheta: 0.75,
      slowDown: 1 + Math.log(Math.max(2, graph.order)),
    };
    const totalIterations = graph.order > 5000 ? 120 : graph.order > 1500 ? 200 : 320;
    const perFrame = graph.order > 5000 ? 3 : 8;
    let done = 0;
    let frame = 0;
    let cancelled = false;

    setSettling(true);
    const step = () => {
      if (cancelled) return;
      forceAtlas2.assign(graph, { iterations: perFrame, settings: fa2Settings });
      done += perFrame;
      if (done < totalIterations) {
        frame = requestAnimationFrame(step);
      } else {
        setSettling(false);
        renderer.refresh();
      }
    };
    frame = requestAnimationFrame(step);

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      setSettling(false);
      renderer.kill();
      rendererRef.current = null;
      graph.clear();
    };
  }, [nodes, edges, dark, focusId]);

  return (
    <div className={className}>
      <div
        ref={containerRef}
        className="h-full w-full cursor-grab bg-canvas"
        role="application"
        aria-label={`Trust neighbourhood graph: ${nodes.length} nodes, ${edges.length} edges. Use the node list beneath the graph for keyboard access.`}
      />
      {settling ? (
        <p
          role="status"
          className="pointer-events-none absolute bottom-3 left-3 rounded-sm border border-rule bg-surface/90 px-2 py-1 font-mono text-2xs text-ink-muted"
        >
          settling layout…
        </p>
      ) : null}
    </div>
  );
}
