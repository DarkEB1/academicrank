import type { ContributingPath, NodeKind, PathNode } from './types';

/**
 * Turning a path through the graph into a sentence a person can read.
 *
 * The API returns `nodes` and `edges` where `edges[i]` joins `nodes[i]` to
 * `nodes[i+1]`. Relation strings come from the backend and are not enumerated in
 * the contract, so unknown relations are humanised rather than dropped.
 */

const RELATION_PHRASES: Record<string, string> = {
  trusts: 'which you trust,',
  trusted_by: 'which you trust,',
  cites: 'cites',
  cited_by: 'is cited by',
  citation: 'cites',
  authored_by: 'was written by',
  authored: 'wrote',
  author: 'was written by',
  wrote: 'wrote',
  in_venue: 'appeared in',
  venue: 'appeared in',
  published_in: 'appeared in',
  about_topic: 'is about',
  topic: 'is about',
  has_topic: 'is about',
  topic_of: 'covers',
  affiliated_with: 'is affiliated with',
  institution: 'is affiliated with',
  affiliation_of: 'is where the authors of',
  coupling: 'shares references with',
  bibliographic_coupling: 'shares references with',
  cocitation: 'is cited alongside',
  cocited_with: 'is cited alongside',
};

export function humaniseRelation(relation: string): string {
  const known = RELATION_PHRASES[relation];
  if (known) return known;
  return relation.replace(/[_-]+/g, ' ').trim() || 'connects to';
}

export const KIND_LABEL: Record<NodeKind, string> = {
  paper: 'paper',
  author: 'author',
  topic: 'topic',
  venue: 'venue',
  institution: 'institution',
  profile: 'you',
};

export type PathStep = {
  from: PathNode;
  to: PathNode;
  relation: string;
  phrase: string;
  weight: number;
};

export function pathSteps(path: Pick<ContributingPath, 'nodes' | 'edges'>): PathStep[] {
  const steps: PathStep[] = [];
  for (let i = 0; i < path.edges.length && i + 1 < path.nodes.length; i += 1) {
    const edge = path.edges[i];
    steps.push({
      from: path.nodes[i],
      to: path.nodes[i + 1],
      relation: edge.relation,
      phrase: humaniseRelation(edge.relation),
      weight: edge.weight,
    });
  }
  return steps;
}

function nodePhrase(node: PathNode, isFirst: boolean): string {
  if (node.kind === 'profile') return isFirst ? 'You' : 'you';
  return node.label;
}

/**
 * "Terence Tao, whose work you trust, cites this."
 *
 * Built left to right from the actual nodes and relations; where the relation is
 * unknown the sentence degrades to "X — relation → Y" rather than inventing a
 * connection that is not in the data.
 */
export function pathSentence(path: Pick<ContributingPath, 'nodes' | 'edges' | 'seed'>): string {
  const steps = pathSteps(path);
  if (steps.length === 0) {
    const only = path.nodes[0];
    return only ? `${only.label} is connected directly.` : 'No path recorded.';
  }

  const parts: string[] = [];
  steps.forEach((step, index) => {
    const subject = nodePhrase(step.from, index === 0);
    const isLast = index === steps.length - 1;
    const object = isLast ? 'this paper' : nodePhrase(step.to, false);

    if (index === 0) {
      const trusted =
        step.from.kind === 'paper'
          ? `${subject}, which is in your trust set,`
          : step.from.kind === 'author'
            ? `${subject}, whose work you trust,`
            : subject;
      parts.push(`${trusted} ${step.phrase} ${object}`);
    } else {
      // People are "who"; papers, venues and topics are "which".
      const pronoun = step.from.kind === 'author' ? 'who' : 'which';
      parts.push(`${pronoun} ${step.phrase} ${object}`);
    }
  });

  return `${parts.join(', ')}.`;
}

/** Total edge-weight product: how the backend ranks paths. */
export function pathStrength(path: Pick<ContributingPath, 'edges'>): number {
  return path.edges.reduce((acc, edge) => acc * edge.weight, 1);
}

export function formatContribution(contribution: number): string {
  if (!Number.isFinite(contribution)) return '—';
  const pct = contribution * 100;
  if (pct > 0 && pct < 0.5) return '<1%';
  return `${Math.round(pct)}%`;
}
