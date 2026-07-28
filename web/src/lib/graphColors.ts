import type { NodeKind } from './types';

/**
 * WebGL needs concrete colours, so these cannot come from CSS variables. Two
 * hand-picked ramps — one per theme — rather than one ramp with opacity tricks.
 */

type RGB = [number, number, number];

const TRUST_RAMP_LIGHT: RGB[] = [
  [214, 210, 202], // unreached: warm grey
  [154, 174, 190],
  [92, 132, 168],
  [44, 92, 140],
  [20, 54, 96], // strongly reached: deep ink blue
];

const TRUST_RAMP_DARK: RGB[] = [
  [72, 76, 84],
  [92, 124, 152],
  [122, 168, 200],
  [156, 202, 228],
  [198, 228, 246],
];

function lerp(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

function toHex([r, g, b]: RGB): string {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

/** `t` is expected pre-normalised to 0..1 across the visible subgraph. */
export function trustColor(t: number, dark: boolean): string {
  const ramp = dark ? TRUST_RAMP_DARK : TRUST_RAMP_LIGHT;
  const clamped = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  const scaled = clamped * (ramp.length - 1);
  const index = Math.min(ramp.length - 2, Math.floor(scaled));
  return toHex(lerp(ramp[index], ramp[index + 1], scaled - index));
}

export function trustRampStops(dark: boolean): string[] {
  return [0, 0.25, 0.5, 0.75, 1].map((t) => trustColor(t, dark));
}

/** Node shape/size cue by kind — papers are the subject, entities are scaffolding. */
export const KIND_SIZE: Record<NodeKind, number> = {
  paper: 4,
  author: 3,
  topic: 3,
  venue: 3,
  institution: 3,
  profile: 9,
};

export const KIND_BORDER: Record<NodeKind, string> = {
  paper: '#00000000',
  author: '#8a6d3b',
  topic: '#3b6d8a',
  venue: '#6d3b8a',
  institution: '#8a3b3b',
  profile: '#c2410c',
};

const RELATION_COLORS_LIGHT: Record<string, string> = {
  cites: '#b9b3a8',
  citation: '#b9b3a8',
  cited_by: '#b9b3a8',
  trusts: '#c2410c',
  distrusts: '#9f1239',
  authored_by: '#a98a4e',
  author: '#a98a4e',
  in_venue: '#8a6da8',
  venue: '#8a6da8',
  about_topic: '#4e8aa9',
  topic: '#4e8aa9',
  affiliated_with: '#a95e5e',
  institution: '#a95e5e',
  coupling: '#7fa98a',
  cocitation: '#a9a04e',
};

const RELATION_COLORS_DARK: Record<string, string> = {
  cites: '#3f434b',
  citation: '#3f434b',
  cited_by: '#3f434b',
  trusts: '#f97316',
  distrusts: '#fb7185',
  authored_by: '#c2a45f',
  author: '#c2a45f',
  in_venue: '#a98ac2',
  venue: '#a98ac2',
  about_topic: '#5fa8c2',
  topic: '#5fa8c2',
  affiliated_with: '#c27a7a',
  institution: '#c27a7a',
  coupling: '#7ec296',
  cocitation: '#c2bb5f',
};

export function relationColor(relation: string, dark: boolean): string {
  const table = dark ? RELATION_COLORS_DARK : RELATION_COLORS_LIGHT;
  return table[relation] ?? (dark ? '#33373d' : '#cdc8bf');
}

/** Relations actually present in a subgraph, for the legend. */
export function relationLegend(relations: string[], dark: boolean): { relation: string; color: string }[] {
  return Array.from(new Set(relations))
    .sort()
    .map((relation) => ({ relation, color: relationColor(relation, dark) }));
}

/** Min/max trust across nodes, used to normalise the colour ramp. */
export function trustExtent(nodes: { trust: number }[]): { min: number; max: number } {
  if (nodes.length === 0) return { min: 0, max: 1 };
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const node of nodes) {
    if (!Number.isFinite(node.trust)) continue;
    min = Math.min(min, node.trust);
    max = Math.max(max, node.trust);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return { min: 0, max: max || 1 };
  return { min, max };
}

export function normaliseTrust(value: number, extent: { min: number; max: number }): number {
  if (extent.max === extent.min) return 0.5;
  return (value - extent.min) / (extent.max - extent.min);
}
