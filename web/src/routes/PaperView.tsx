import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ExternalLink, Sparkles } from 'lucide-react';
import { usePaper, useSetTrust, useSubgraph } from '@/lib/queries';
import { useSession } from '@/lib/session';
import { useIsDark } from '@/lib/theme';
import type { SubgraphNode } from '@/lib/types';
import {
  disagreementBand,
  DISAGREEMENT_COPY,
  formatCount,
  formatInterval,
  formatScore,
  formatYear,
  METHOD_COPY,
  UNCERTAINTY_COPY,
  uncertaintyVerdict,
} from '@/lib/format';
import { MathText, PaperTitle } from '@/components/Math';
import { ScoreReadout } from '@/components/ScoreBar';
import { ComparisonStrip } from '@/components/ComparisonStrip';
import { ExplainContent } from '@/components/ExplainPanel';
import { GraphCanvas } from '@/components/GraphCanvas';
import { StrengthPicker, STRENGTH_LABELS } from '@/components/StrengthPicker';
import { CoverageNote, Notice } from '@/components/Honesty';
import { ErrorState } from '@/components/States';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { CardSkeleton, LoadingRegion, Skeleton } from '@/components/ui/Skeleton';
import { cn } from '@/lib/cn';

export function PaperScreen(): JSX.Element {
  const { id = '' } = useParams();
  const { profile } = useSession();
  const profileId = profile?.id ?? '';
  const dark = useIsDark();

  const paper = usePaper(profileId, id);
  const neighbourhood = useSubgraph(
    profileId,
    { focus: id, limit: 500, context: 'aggregate' },
    Boolean(profileId) && Boolean(id),
  );
  const setTrust = useSetTrust(profileId);
  const [pendingStrength, setPendingStrength] = useState(3);
  const [selectedNode, setSelectedNode] = useState<SubgraphNode | null>(null);

  if (paper.isLoading) {
    return (
      <LoadingRegion label="Loading this paper" className="space-y-8">
        <Skeleton className="h-9 w-3/4 max-w-3xl" />
        <Skeleton className="h-4 w-1/3" />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <CardSkeleton lines={6} />
          <Skeleton className="h-64 w-full" />
        </div>
      </LoadingRegion>
    );
  }

  if (paper.isError) {
    return (
      <div className="space-y-4">
        <ErrorState error={paper.error} onRetry={() => void paper.refetch()} />
        <p className="text-center text-xs text-ink-faint">
          <Link to="/" className="link">
            Back to rankings
          </Link>
        </p>
      </div>
    );
  }

  const data = paper.data;
  if (!data) return <ErrorState error={new Error('The server returned no paper.')} />;

  const band = disagreementBand(data.disagreement);
  const contested = band === 'notable' || band === 'stark';
  const verdict = uncertaintyVerdict(data.trust, data.uncertainty);
  const entry = data.in_trust_set;

  return (
    <div className="space-y-8">
      <header className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {data.paper.is_stub ? <Badge tone="caution">stub record</Badge> : null}
          {entry ? (
            <Badge tone={entry.is_distrust ? 'critical' : 'positive'}>
              {entry.is_distrust ? 'distrusted seed' : `seed · strength ${entry.strength}`}
            </Badge>
          ) : null}
          {contested ? (
            <Badge tone="caution">
              <Sparkles aria-hidden className="h-3 w-3" />
              contested
            </Badge>
          ) : null}
        </div>

        <PaperTitle
          as="h1"
          title={data.paper.title}
          className="max-w-4xl text-[1.75rem] leading-[1.25] tracking-tight text-ink"
        />

        <p className="max-w-4xl text-sm leading-relaxed text-ink-muted">
          {data.paper.authors.length > 0
            ? data.paper.authors.map((a) => a.name).join(' · ')
            : 'Unattributed'}
        </p>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 font-mono text-2xs tnum text-ink-faint">
          <span>{formatYear(data.paper.year)}</span>
          {data.paper.venue ? <span className="font-sans">{data.paper.venue.name}</span> : null}
          <span>{formatCount(data.cited_by_count)} citations</span>
          <span>{formatCount(data.paper.in_corpus_cited_by)} inside this corpus</span>
          {data.paper.doi ? (
            <a
              href={data.paper.doi.startsWith('http') ? data.paper.doi : `https://doi.org/${data.paper.doi}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-sans text-accent hover:underline"
            >
              {data.paper.doi.replace(/^https?:\/\/doi\.org\//, '')}
              <ExternalLink aria-hidden className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      </header>

      {contested ? (
        <Notice title="Your trust graph and the field disagree about this paper">
          <p>{DISAGREEMENT_COPY[band]}</p>
          <p className="mt-1.5">
            Disagreement is {data.disagreement.toFixed(2)} on a 0–1 scale. A paper can land here
            because it is genuinely under-recognised, because it sits in a corner of the literature
            your seeds over-weight, or because the corpus records it badly. The system cannot tell
            you which.
          </p>
        </Notice>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="min-w-0 space-y-8">
          {data.paper.abstract ? (
            <Card>
              <CardHeader title="Abstract" />
              <CardBody>
                <MathText
                  as="div"
                  className="max-w-measure font-serif text-[0.95rem] leading-relaxed text-ink"
                >
                  {data.paper.abstract}
                </MathText>
              </CardBody>
            </Card>
          ) : (
            <Card>
              <CardHeader title="Abstract" />
              <CardBody>
                <p className="max-w-measure text-sm leading-relaxed text-ink-muted">
                  No abstract is available for this record. OpenAlex holds inverted-index abstracts
                  for only part of its corpus, and this API does not currently expose one. Nothing
                  has been substituted in its place.
                </p>
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader
              title="Why this score"
              description="Reconstructed from the trust graph. Every path below is one the walk actually took."
            />
            <ExplainContent profileId={profileId} paperId={id} />
          </Card>

          <Card>
            <CardHeader
              title="Local trust neighbourhood"
              description="The sampled subgraph around this paper. The focused node is highlighted."
            />
            <CardBody className="p-0">
              {neighbourhood.isError ? (
                <ErrorState
                  error={neighbourhood.error}
                  onRetry={() => void neighbourhood.refetch()}
                  className="m-4"
                />
              ) : neighbourhood.isLoading ? (
                <LoadingRegion label="Loading the local neighbourhood" className="p-5">
                  <Skeleton className="h-80 w-full" />
                </LoadingRegion>
              ) : (neighbourhood.data?.nodes.length ?? 0) === 0 ? (
                <p className="px-5 py-10 text-center text-sm text-ink-muted">
                  No neighbourhood was returned. This paper may be isolated in the sampled graph —
                  which is itself informative: nothing in your trust set reaches it directly.
                </p>
              ) : (
                <>
                  <div className="relative h-[26rem] overflow-hidden border-b border-rule">
                    <GraphCanvas
                      nodes={neighbourhood.data?.nodes ?? []}
                      edges={neighbourhood.data?.edges ?? []}
                      focusId={id}
                      dark={dark}
                      onSelect={setSelectedNode}
                      className="absolute inset-0"
                    />
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                    <p className="font-mono text-2xs tnum text-ink-faint">
                      {formatCount(neighbourhood.data?.nodes.length ?? 0)} nodes ·{' '}
                      {formatCount(neighbourhood.data?.edges.length ?? 0)} edges
                    </p>
                    {selectedNode ? (
                      <p className="flex items-center gap-2 text-xs text-ink">
                        <span className="text-ink-muted">{selectedNode.kind}:</span>
                        <span className="max-w-sm truncate">{selectedNode.label}</span>
                        {selectedNode.kind === 'paper' && selectedNode.id !== id ? (
                          <Link to={`/paper/${selectedNode.id}`} className="link">
                            open
                          </Link>
                        ) : null}
                      </p>
                    ) : (
                      <Link to={`/graph?focus=${id}`} className="link text-xs">
                        Open in the full graph explorer
                      </Link>
                    )}
                  </div>
                </>
              )}
            </CardBody>
          </Card>
        </div>

        {/* ------------------------- sidebar ------------------------- */}
        <aside className="space-y-5">
          <Card>
            <CardHeader title="Trust score" />
            <CardBody className="space-y-3">
              <p className="font-mono text-2xl tnum leading-none text-ink">
                {formatScore(data.trust, data.uncertainty.stderr)}
              </p>
              <p className="font-mono text-xs tnum text-ink-muted">
                ± {formatScore(data.uncertainty.stderr, data.uncertainty.stderr)} · interval{' '}
                {formatInterval(data.uncertainty)}
              </p>
              <p
                className={cn(
                  'max-w-measure text-xs leading-relaxed',
                  verdict === 'uninformative' ? 'text-caution' : 'text-ink-muted',
                )}
              >
                {UNCERTAINTY_COPY[verdict]}
              </p>
              <p className="max-w-measure text-xs leading-relaxed text-ink-faint">
                {METHOD_COPY[data.uncertainty.method]} Tie group {data.uncertainty.tie_group}.
              </p>
              <dl className="grid grid-cols-2 gap-3 border-t border-rule pt-3 text-xs">
                <div>
                  <dt className="text-ink-faint">Global merit</dt>
                  <dd className="mt-0.5 font-mono tnum text-ink">{formatScore(data.global_merit)}</dd>
                </div>
                <div>
                  <dt className="text-ink-faint">Your trust</dt>
                  <dd className="mt-0.5">
                    <ScoreReadout
                      value={data.trust}
                      uncertainty={data.uncertainty}
                      className="text-xs text-ink"
                    />
                  </dd>
                </div>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardBody>
              <ComparisonStrip percentiles={data.percentiles} disagreement={data.disagreement} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={entry ? 'In your trust set' : 'Add to your trust set'}
              description={
                entry
                  ? 'Changing this changes every score in the system, including this one.'
                  : 'Seeding this paper makes it, and everything it cites, closer to you.'
              }
            />
            <CardBody className="space-y-3">
              <StrengthPicker
                value={entry?.strength ?? pendingStrength}
                onChange={(value) => {
                  setPendingStrength(value);
                  if (entry) setTrust.mutate({ work_id: id, strength: value, is_distrust: entry.is_distrust });
                }}
                disabled={setTrust.isPending}
              />
              <p className="text-xs text-ink-muted">
                {STRENGTH_LABELS[entry?.strength ?? pendingStrength]}
              </p>
              <div className="flex flex-wrap gap-2">
                {entry ? (
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setTrust.mutate({ work_id: id, strength: 0 })}
                    disabled={setTrust.isPending}
                  >
                    Remove seed
                  </Button>
                ) : (
                  <>
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => setTrust.mutate({ work_id: id, strength: pendingStrength })}
                      disabled={setTrust.isPending}
                    >
                      Trust this
                    </Button>
                    <Button
                      size="sm"
                      onClick={() =>
                        setTrust.mutate({
                          work_id: id,
                          strength: pendingStrength,
                          is_distrust: true,
                        })
                      }
                      disabled={setTrust.isPending}
                    >
                      Distrust
                    </Button>
                  </>
                )}
              </div>
              {setTrust.isError ? <ErrorState error={setTrust.error} /> : null}
            </CardBody>
          </Card>

          {data.topics.length > 0 ? (
            <Card>
              <CardHeader title="Topics" description="OpenAlex classification, with its scores." />
              <CardBody>
                <ul className="space-y-1.5">
                  {data.topics.map((topic) => (
                    <li key={topic.id} className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-xs text-ink">{topic.name}</span>
                      <span className="shrink-0 font-mono text-2xs tnum text-ink-faint">
                        {topic.score.toFixed(2)}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ) : null}

          {data.institutions.length > 0 ? (
            <Card>
              <CardHeader
                title="Institutions"
                description="Recorded affiliations. Institutional edges carry a deliberately low weight."
              />
              <CardBody>
                <ul className="space-y-1.5">
                  {data.institutions.map((inst) => (
                    <li key={inst.id} className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-xs text-ink">{inst.name}</span>
                      {inst.country ? (
                        <span className="shrink-0 font-mono text-2xs uppercase text-ink-faint">
                          {inst.country}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ) : null}

          <CoverageNote />
        </aside>
      </div>
    </div>
  );
}
