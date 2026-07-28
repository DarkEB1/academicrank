import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Crosshair, Info } from 'lucide-react';
import { useSeedCount, useSubgraph } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useIsDark } from '@/lib/theme';
import { CONTEXTS, type Context, type NodeKind, type SubgraphNode } from '@/lib/types';
import { relationLegend, trustRampStops } from '@/lib/graphColors';
import { GraphCanvas } from '@/components/GraphCanvas';
import { MathText } from '@/components/Math';
import { ErrorState, NoTrustSetYet } from '@/components/States';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Select } from '@/components/ui/Select';
import { Field } from '@/components/ui/Input';
import { Button, buttonClass } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { formatCount, formatYear } from '@/lib/format';
import { cn } from '@/lib/cn';

const KINDS: NodeKind[] = ['paper', 'author', 'topic', 'venue', 'institution', 'profile'];
const SIZES = [500, 1000, 3000];

export function GraphExplorerScreen(): JSX.Element {
  const { profile } = useSession();
  const profileId = profile?.id ?? '';
  const dark = useIsDark();
  const [params, setParams] = useSearchParams();

  const focus = params.get('focus') ?? undefined;
  const context = (params.get('context') as Context | null) ?? 'aggregate';
  const limit = Number(params.get('limit') ?? 1000);

  const [hiddenKinds, setHiddenKinds] = useState<Set<NodeKind>>(new Set());
  const [hiddenRelations, setHiddenRelations] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<SubgraphNode | null>(null);

  const subgraph = useSubgraph(profileId, { focus, limit, context }, Boolean(profileId));

  useEffect(() => {
    setSelected(null);
  }, [focus, context]);

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  const raw = subgraph.data;

  const filtered = useMemo(() => {
    if (!raw) return { nodes: [], edges: [] };
    const nodes = raw.nodes.filter((n) => !hiddenKinds.has(n.kind));
    const ids = new Set(nodes.map((n) => n.id));
    const edges = raw.edges.filter(
      (e) => !hiddenRelations.has(e.relation) && ids.has(e.source) && ids.has(e.target),
    );
    return { nodes, edges };
  }, [raw, hiddenKinds, hiddenRelations]);

  const kindCounts = useMemo(() => {
    const counts = new Map<NodeKind, number>();
    for (const node of raw?.nodes ?? []) counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
    return counts;
  }, [raw]);

  const relations = useMemo(
    () => relationLegend((raw?.edges ?? []).map((e) => e.relation), dark),
    [raw, dark],
  );

  const relationCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const edge of raw?.edges ?? []) counts.set(edge.relation, (counts.get(edge.relation) ?? 0) + 1);
    return counts;
  }, [raw]);

  const toggleKind = (kind: NodeKind) =>
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });

  const toggleRelation = (relation: string) =>
    setHiddenRelations((prev) => {
      const next = new Set(prev);
      if (next.has(relation)) next.delete(relation);
      else next.add(relation);
      return next;
    });

  const seeds = useSeedCount(profileId).count;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl tracking-tight text-ink">Graph explorer</h1>
          <p className="mt-1.5 max-w-measure text-sm leading-relaxed text-ink-muted">
            The structure the scores are computed from. Colour is trust relative to the rest of
            this view, not an absolute scale — a bright node in a sparse neighbourhood may score
            below a dull one elsewhere.
          </p>
        </div>
        {raw ? (
          <p className="font-mono text-2xs tnum text-ink-faint">
            {formatCount(filtered.nodes.length)} / {formatCount(raw.nodes.length)} nodes ·{' '}
            {formatCount(filtered.edges.length)} / {formatCount(raw.edges.length)} edges
          </p>
        ) : null}
      </header>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-4">
          <div className="flex flex-wrap items-end gap-4 rounded-sm border border-rule bg-surface px-4 py-3">
            <Field label="Context" className="w-44">
              {(id) => (
                <Select id={id} value={context} onChange={(e) => update({ context: e.target.value })}>
                  {CONTEXTS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Node budget" className="w-36">
              {(id) => (
                <Select id={id} value={String(limit)} onChange={(e) => update({ limit: e.target.value })}>
                  {SIZES.map((size) => (
                    <option key={size} value={size}>
                      {formatCount(size)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {focus ? (
              <Button size="sm" onClick={() => update({ focus: null })}>
                Clear focus
              </Button>
            ) : (
              <p className="pb-2 text-xs text-ink-muted">
                Centred on your profile. Click any node to re-centre.
              </p>
            )}
          </div>

          {subgraph.isError ? (
            <ErrorState error={subgraph.error} onRetry={() => void subgraph.refetch()} />
          ) : subgraph.isLoading ? (
            <LoadingRegion label="Loading the subgraph" className="space-y-3">
              <Skeleton className="h-[62vh] min-h-[26rem] w-full" />
            </LoadingRegion>
          ) : seeds === 0 && (raw?.nodes.length ?? 0) === 0 ? (
            <NoTrustSetYet what="This graph" />
          ) : filtered.nodes.length === 0 ? (
            <div className="flex h-[62vh] min-h-[26rem] items-center justify-center rounded-sm border border-rule bg-surface">
              <p className="max-w-measure px-6 text-center text-sm text-ink-muted">
                Every node is filtered out. Re-enable a node kind in the legend.
              </p>
            </div>
          ) : (
            <div className="relative h-[62vh] min-h-[26rem] overflow-hidden rounded-sm border border-rule">
              <GraphCanvas
                nodes={filtered.nodes}
                edges={filtered.edges}
                focusId={focus}
                dark={dark}
                onSelect={setSelected}
                className="absolute inset-0"
              />
            </div>
          )}

          {/* Keyboard route to the same interaction as clicking a node. */}
          {filtered.nodes.length > 0 ? (
            <details className="rounded-sm border border-rule bg-surface">
              <summary className="cursor-pointer px-4 py-2.5 text-xs text-ink">
                Node list (keyboard accessible alternative to the canvas)
              </summary>
              <ul className="max-h-64 divide-y divide-rule overflow-y-auto">
                {[...filtered.nodes]
                  .sort((a, b) => b.trust - a.trust)
                  .slice(0, 200)
                  .map((node) => (
                    <li key={node.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(node)}
                        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-raised"
                      >
                        <span className="w-20 shrink-0 text-2xs uppercase tracking-[0.06em] text-ink-faint">
                          {node.kind}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-xs text-ink">{node.label}</span>
                        <span className="shrink-0 font-mono text-2xs tnum text-ink-muted">
                          {node.trust.toExponential(1)}
                        </span>
                      </button>
                    </li>
                  ))}
              </ul>
            </details>
          ) : null}
        </div>

        <aside className="space-y-4">
          {selected ? (
            <Card>
              <CardHeader title="Selected node" />
              <CardBody className="space-y-3">
                <MathText as="p" className="text-sm leading-snug text-ink">
                  {selected.label}
                </MathText>
                <div className="flex flex-wrap gap-2">
                  <Badge>{selected.kind}</Badge>
                  <Badge>{formatYear(selected.year)}</Badge>
                  <Badge title="Raw trust value for this node">
                    {selected.trust.toExponential(2)}
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" onClick={() => update({ focus: selected.id })}>
                    <Crosshair aria-hidden className="h-3.5 w-3.5" />
                    Focus here
                  </Button>
                  {selected.kind === 'paper' ? (
                    <Link to={`/paper/${selected.id}`} className={buttonClass('primary', 'sm')}>
                      Open paper
                    </Link>
                  ) : null}
                </div>
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader title="Legend" />
            <CardBody className="space-y-5">
              <div>
                <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                  Trust (relative to this view)
                </p>
                <div className="mt-2 flex h-3 overflow-hidden rounded-[1px]">
                  {trustRampStops(dark).map((color) => (
                    <span key={color} className="flex-1" style={{ backgroundColor: color }} />
                  ))}
                </div>
                <div className="mt-1 flex justify-between text-2xs text-ink-faint">
                  <span>lowest here</span>
                  <span>highest here</span>
                </div>
              </div>

              <div>
                <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">Node kind</p>
                <ul className="mt-2 space-y-1">
                  {KINDS.filter((kind) => kindCounts.has(kind)).map((kind) => (
                    <li key={kind}>
                      <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
                        <input
                          type="checkbox"
                          checked={!hiddenKinds.has(kind)}
                          onChange={() => toggleKind(kind)}
                          className="h-3.5 w-3.5 rounded-[2px] border-rule-strong accent-[hsl(var(--accent))]"
                        />
                        <span className="flex-1">{kind}</span>
                        <span className="font-mono text-2xs tnum text-ink-faint">
                          {formatCount(kindCounts.get(kind) ?? 0)}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
                  Edge relation
                </p>
                <ul className="mt-2 space-y-1">
                  {relations.map(({ relation, color }) => (
                    <li key={relation}>
                      <label className="flex cursor-pointer items-center gap-2 text-xs text-ink">
                        <input
                          type="checkbox"
                          checked={!hiddenRelations.has(relation)}
                          onChange={() => toggleRelation(relation)}
                          className="h-3.5 w-3.5 rounded-[2px] border-rule-strong accent-[hsl(var(--accent))]"
                        />
                        <span
                          aria-hidden
                          className="h-0.5 w-4 shrink-0 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                        <span className={cn('flex-1 truncate font-mono text-2xs')}>{relation}</span>
                        <span className="font-mono text-2xs tnum text-ink-faint">
                          {formatCount(relationCounts.get(relation) ?? 0)}
                        </span>
                      </label>
                    </li>
                  ))}
                  {relations.length === 0 ? (
                    <li className="text-xs text-ink-muted">No edges in this view.</li>
                  ) : null}
                </ul>
              </div>
            </CardBody>
          </Card>

          <p className="flex gap-2 text-xs leading-relaxed text-ink-muted">
            <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" />
            The subgraph is a sample around the focus, capped at the node budget. Absence of an
            edge here means it was not sampled, not that it does not exist.
          </p>
        </aside>
      </div>
    </div>
  );
}
