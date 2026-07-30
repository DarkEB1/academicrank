/**
 * Canvas-2D graph renderer: the no-WebGL fallback for GraphCanvas.
 *
 * Sigma is WebGL-only, and privacy-hardened browsers (LibreWolf ships
 * `webgl.disabled=true`) refuse the context outright. Nothing about a
 * node-budgeted subgraph needs WebGL, so this class implements the exact
 * renderer surface GraphCanvas consumes -- `on('clickNode'|'enterNode'|
 * 'leaveNode')`, `refresh()`, `kill()` -- over a plain 2D context, with wheel
 * zoom, drag pan, hover labels and click hit-testing.
 *
 * It reads the same graphology attributes sigma would (x, y, size, color,
 * label), so the chunked ForceAtlas2 loop drives it unchanged via refresh().
 * Deliberately simpler than sigma: no label collision grid, no edge events,
 * no camera animations. At the default 1,000-node budget a full redraw is
 * ~1-2ms; redraws happen on interaction and refresh, not on a rAF loop.
 */
import type Graph from 'graphology';

type ClickPayload = { node: string };
type EventName = 'clickNode' | 'enterNode' | 'leaveNode';

const LABEL_MIN_SCALE = 0.55; // px per graph unit before any labels draw
const LABEL_BUDGET = 36;      // most-prominent nodes get labels, nothing else
const HIT_SLOP_PX = 4;

export class Canvas2DGraphRenderer {
  private graph: Graph;
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private handlers: Partial<Record<EventName, ((p: ClickPayload) => void)[]>> = {};
  private resizeObserver: ResizeObserver | null = null;

  // camera: graph-space point at the canvas centre, and pixels per graph unit
  private cx = 0;
  private cy = 0;
  private scale = 1;

  private hovered: string | null = null;
  private dragging = false;
  private moved = false;
  private lastX = 0;
  private lastY = 0;
  private killed = false;

  constructor(graph: Graph, container: HTMLElement) {
    this.graph = graph;
    this.container = container;
    this.canvas = document.createElement('canvas');
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';
    this.canvas.style.display = 'block';
    const ctx = this.canvas.getContext('2d');
    if (!ctx) {
      throw new Error('2D canvas context unavailable');
    }
    this.ctx = ctx;
    container.appendChild(this.canvas);

    this.sizeToContainer();
    this.fitCamera();

    this.canvas.addEventListener('wheel', this.onWheel, { passive: false });
    this.canvas.addEventListener('mousedown', this.onDown);
    window.addEventListener('mousemove', this.onMove);
    window.addEventListener('mouseup', this.onUp);
    this.canvas.addEventListener('mouseleave', this.onLeaveCanvas);

    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => {
        this.sizeToContainer();
        this.draw();
      });
      this.resizeObserver.observe(container);
    }

    this.draw();
  }

  on(event: EventName, handler: (p: ClickPayload) => void): this {
    (this.handlers[event] ??= []).push(handler);
    return this;
  }

  refresh(): void {
    this.draw();
  }

  kill(): void {
    this.killed = true;
    this.resizeObserver?.disconnect();
    this.canvas.removeEventListener('wheel', this.onWheel);
    this.canvas.removeEventListener('mousedown', this.onDown);
    window.removeEventListener('mousemove', this.onMove);
    window.removeEventListener('mouseup', this.onUp);
    this.canvas.removeEventListener('mouseleave', this.onLeaveCanvas);
    this.canvas.remove();
  }

  // ---- camera -----------------------------------------------------------

  private sizeToContainer(): void {
    const dpr = window.devicePixelRatio || 1;
    const w = this.container.clientWidth || 600;
    const h = this.container.clientHeight || 400;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  private fitCamera(): void {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    this.graph.forEachNode((_n, a) => {
      const x = Number(a.x ?? 0);
      const y = Number(a.y ?? 0);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    });
    if (!Number.isFinite(minX)) {
      minX = -100; maxX = 100; minY = -100; maxY = 100;
    }
    this.cx = (minX + maxX) / 2;
    this.cy = (minY + maxY) / 2;
    const w = this.container.clientWidth || 600;
    const h = this.container.clientHeight || 400;
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    this.scale = 0.9 * Math.min(w / spanX, h / spanY);
  }

  private toScreen(x: number, y: number): [number, number] {
    const w = this.container.clientWidth || 600;
    const h = this.container.clientHeight || 400;
    return [(x - this.cx) * this.scale + w / 2, (y - this.cy) * this.scale + h / 2];
  }

  private toGraph(px: number, py: number): [number, number] {
    const w = this.container.clientWidth || 600;
    const h = this.container.clientHeight || 400;
    return [(px - w / 2) / this.scale + this.cx, (py - h / 2) / this.scale + this.cy];
  }

  // ---- events -----------------------------------------------------------

  private local(e: MouseEvent): [number, number] {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    const [px, py] = this.local(e);
    const [gx, gy] = this.toGraph(px, py);
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    this.scale = Math.min(50, Math.max(0.02, this.scale * factor));
    // keep the point under the cursor fixed
    const [nx, ny] = this.toGraph(px, py);
    this.cx += gx - nx;
    this.cy += gy - ny;
    this.draw();
  };

  private onDown = (e: MouseEvent): void => {
    this.dragging = true;
    this.moved = false;
    [this.lastX, this.lastY] = [e.clientX, e.clientY];
  };

  private onMove = (e: MouseEvent): void => {
    if (this.killed) return;
    if (this.dragging) {
      const dx = e.clientX - this.lastX;
      const dy = e.clientY - this.lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) this.moved = true;
      [this.lastX, this.lastY] = [e.clientX, e.clientY];
      this.cx -= dx / this.scale;
      this.cy -= dy / this.scale;
      this.draw();
      return;
    }
    const [px, py] = this.local(e);
    if (px < 0 || py < 0) return;
    const hit = this.hitTest(px, py);
    if (hit !== this.hovered) {
      const was = this.hovered;
      this.hovered = hit;
      if (hit && !was) this.emit('enterNode', hit);
      if (!hit && was) this.emit('leaveNode', was);
      this.draw();
    }
  };

  private onUp = (e: MouseEvent): void => {
    if (this.killed) return;
    const wasDrag = this.dragging && this.moved;
    this.dragging = false;
    if (wasDrag) return;
    const [px, py] = this.local(e);
    if (px < 0 || py < 0 || px > this.canvas.clientWidth || py > this.canvas.clientHeight) return;
    const hit = this.hitTest(px, py);
    if (hit) this.emit('clickNode', hit);
  };

  private onLeaveCanvas = (): void => {
    if (this.hovered) {
      this.emit('leaveNode', this.hovered);
      this.hovered = null;
      this.draw();
    }
  };

  private emit(event: EventName, node: string): void {
    for (const h of this.handlers[event] ?? []) h({ node });
  }

  private hitTest(px: number, py: number): string | null {
    let best: string | null = null;
    let bestD = Infinity;
    this.graph.forEachNode((n, a) => {
      const [sx, sy] = this.toScreen(Number(a.x ?? 0), Number(a.y ?? 0));
      const r = this.nodeRadius(Number(a.size ?? 2)) + HIT_SLOP_PX;
      const d = (sx - px) ** 2 + (sy - py) ** 2;
      if (d <= r * r && d < bestD) {
        best = n;
        bestD = d;
      }
    });
    return best;
  }

  private nodeRadius(size: number): number {
    // sigma sizes are radii in px at ratio 1; keep them modest across zoom
    return Math.max(1.5, size * Math.min(2, Math.max(0.5, this.scale / 3)));
  }

  // ---- drawing ----------------------------------------------------------

  private draw(): void {
    if (this.killed) return;
    const ctx = this.ctx;
    const w = this.container.clientWidth || 600;
    const h = this.container.clientHeight || 400;
    ctx.clearRect(0, 0, w, h);

    ctx.lineWidth = 0.6;
    ctx.globalAlpha = 0.55;
    this.graph.forEachEdge((_e, attrs, _s, _t, sa, ta) => {
      const [x1, y1] = this.toScreen(Number(sa.x ?? 0), Number(sa.y ?? 0));
      const [x2, y2] = this.toScreen(Number(ta.x ?? 0), Number(ta.y ?? 0));
      if ((x1 < 0 && x2 < 0) || (x1 > w && x2 > w) || (y1 < 0 && y2 < 0) || (y1 > h && y2 > h)) {
        return;
      }
      ctx.strokeStyle = String(attrs.color ?? '#888');
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    });
    ctx.globalAlpha = 1;

    const labelled: { x: number; y: number; size: number; label: string }[] = [];
    this.graph.forEachNode((n, a) => {
      const [sx, sy] = this.toScreen(Number(a.x ?? 0), Number(a.y ?? 0));
      const r = this.nodeRadius(Number(a.size ?? 2));
      if (sx < -r || sy < -r || sx > w + r || sy > h + r) return;
      ctx.fillStyle = String(a.color ?? '#4a90d9');
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
      if (n === this.hovered) {
        ctx.strokeStyle = ctx.fillStyle;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(sx, sy, r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (a.label) {
        labelled.push({ x: sx, y: sy, size: Number(a.size ?? 2), label: String(a.label) });
      }
    });

    if (this.scale >= LABEL_MIN_SCALE) {
      labelled.sort((a, b) => b.size - a.size);
      ctx.font = '10px ui-sans-serif, system-ui, sans-serif';
      ctx.textBaseline = 'middle';
      const isDark = this.isDarkCanvas();
      ctx.fillStyle = isDark ? '#e8e3d9' : '#26231f';
      for (const l of labelled.slice(0, LABEL_BUDGET)) {
        ctx.fillText(this.ellipsis(l.label), l.x + this.nodeRadius(l.size) + 3, l.y);
      }
    }

    // hovered label always wins, drawn last with a backing box for legibility
    if (this.hovered && this.graph.hasNode(this.hovered)) {
      const a = this.graph.getNodeAttributes(this.hovered);
      const [sx, sy] = this.toScreen(Number(a.x ?? 0), Number(a.y ?? 0));
      const text = this.ellipsis(String(a.label ?? this.hovered), 60);
      ctx.font = '11px ui-sans-serif, system-ui, sans-serif';
      const tw = ctx.measureText(text).width;
      const isDark = this.isDarkCanvas();
      ctx.fillStyle = isDark ? 'rgba(20,22,25,0.92)' : 'rgba(250,248,244,0.92)';
      ctx.fillRect(sx + 8, sy - 9, tw + 8, 18);
      ctx.fillStyle = isDark ? '#e8e3d9' : '#26231f';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, sx + 12, sy);
    }
  }

  private ellipsis(s: string, n = 34): string {
    return s.length > n ? `${s.slice(0, n - 1)}…` : s;
  }

  private isDarkCanvas(): boolean {
    return document.documentElement.classList.contains('dark');
  }
}
